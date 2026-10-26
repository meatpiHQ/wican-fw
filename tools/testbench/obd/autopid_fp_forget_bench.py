#!/usr/bin/env python3
"""AutoPID fp-key forget bench  ->  AUTOPID FP FORGET PASS / FAIL   (discussion #1018)

The report (2026-10-25, a 2021 Toyota Sienna on a 6.0.0 beta): the Vehicles
list showed the Sienna by its VIN and a second car, "Car 0a70" (no VIN, ECU
set 0a70ff20, 15 std PIDs, Profile pending), and Forget on the second one did
nothing. Two things behind it:
  1. a car that answers no VIN request is keyed `fp:<8 hex>` (a hash of its
     responder set); the browser sends that key through encodeURIComponent,
     `fp%3A...`, and the device did not decode it: every per-key route
     answered 404 "no such route", so such a car could be neither forgotten
     (the page toasted "no such route") nor given a profile (it stayed
     Profile pending). A VIN key has nothing to encode and never saw it.
  2. the same physical car becomes two entries when one contact answered no
     VIN and a different main-ECU support bitmap than another (fewer ECUs
     awake: ACC against ON on a hybrid). The adoption rule keeps them apart
     on purpose (two cars of one make can share an ECU set), so the user
     needs Forget to work on the extra entry.

The bench plays both on the ECU simulator: a variant of its default profile
with seven standard PIDs disabled (a different 0100 bitmap, so no adoption)
and the VIN replies silenced (0902 and 22F190 through the simulator's error
injection), then the detection: a second car, `fp:<hash>`, beside the
simulator's VIN car. Then the per-key routes as the browser sends them (the
key percent-encoded): PUT renames the car, POST activate answers ok, DELETE
forgets it and the list is back to one car. Then the raw-colon form (a second
detection re-creates the car, DELETE with the colon) and the refusals (a torn
escape, a non-hex escape: 404 "no such route"; a decoded but unknown key: 404
"no such vehicle"). Restore: the errors cleared, the simulator's profile
reloaded and the fixture deleted from it, the VIN answering again, the VIN
car detected again and current, the tables compared with the save.

Found on the way (2026-10-26, fixed with it): with no current car (the one
just forgotten was current) the chip gets the no-car prelude, the chip's own
protocol search; it read `ATS1;ATH0;ATST96;ATTP0` and the sender's
print-format strip (issue #1014, meant for the user's inits) blanked its
ATH0 into an empty command the chip answered with `?`; the same strip hit
the K-line and J1850 preludes, whose headers-off is deliberate. The sender
now strips the tables' chains only and never sends an empty element, and
the no-car prelude is `ATS1;ATH1;ATST96;ATTP0` (headers on, the frame of
reference of the profiles: a car that answers no standard request, such as
a Nissan Leaf, never gets an entry and runs its profile on this prelude);
the bench reads the prelude off the test route's transcript right after
the forget.

Legs: L0 preflight (DUT, simulator, saves), L1 stage (the fixture profile
uploaded + reloaded, the VIN silenced, the DUT sees NO DATA on 0902 and a
different bitmap on 0100), L2 the duplicate (detection -> a fp: entry beside
the VIN car), L3 the routes with the encoded key, L4 the raw key and the
refusals, L5 restore.

usage: autopid_fp_forget_bench.py [--dut localhost:18081] [--out DIR]
  Runs on the PC (the simulator at 192.168.8.1 through sim_net); the DUT
  through an ssh tunnel (`ssh -N -L 18081:10.42.1.194:80 rpi001`) or by
  address.
Needs: the main firmware on the DUT on the bench AP, the ECU simulator
answering 7DF with its default profile (VIN 1WCAN0FW0P0000001, its car in
the vehicle store or learned by the bench), the PSU above the sleep voltage.
~2 min, no restart (the detection pauses the polling and resumes it).
"""
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "lib"))
import sim_net  # noqa: E402

REPO = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
SIM = "http://192.168.8.1"
SIM_VIN = "1WCAN0FW0P0000001"           # the default profile's VIN
FIXTURE = os.path.join(HERE, "fixtures", "ecu_sim_fp_dup_profile.json")
FIXTURE_NAME = "fp_dup_bench.json"      # its name on the simulator
VIN_SILENCE = (("09", "02"), ("22", "F190"))   # 0902, then the 22F190 fallback


def http(base, method, path, body=None, timeout=30, raw=None):
    data = raw if raw is not None else (
        None if body is None else json.dumps(body).encode())
    req = urllib.request.Request(base + path, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode(errors="replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode(errors="replace")
    except (urllib.error.URLError, OSError, ValueError) as e:
        return 0, str(e)


def get_json(base, path):
    st, body = http(base, "GET", path)
    try:
        return st, json.loads(body)
    except ValueError:
        return st, None


def post_json(base, path, body=None, timeout=30):
    st, text = http(base, "POST", path, body, timeout=timeout)
    try:
        return st, json.loads(text)
    except ValueError:
        return st, None


class Checks:
    def __init__(self):
        self.rows = []

    def add(self, name, ok, detail=""):
        self.rows.append((name, bool(ok), detail))
        print("  [%s] %s%s" % ("ok" if ok else "FAIL", name,
                               (": " + detail) if detail else ""), flush=True)
        return bool(ok)

    def failed(self):
        return [n for n, ok, _ in self.rows if not ok]


# ---- the simulator -------------------------------------------------------------

def sim_profiles():
    st, d = get_json(SIM, "/api/ecu/profiles")
    return d if st == 200 and isinstance(d, dict) else None


def sim_profile_name():
    st, d = get_json(SIM, "/api/ecu/status")
    return (d or {}).get("profile") if st == 200 else None


def sim_upload(name, path):
    with open(path, "rb") as f:
        st, body = http(SIM, "POST", "/api/ecu/profiles/" + name, raw=f.read(),
                        timeout=60)
    return st == 200 and '"ok":true' in body.replace(" ", ""), body.strip()[:120]


def sim_reload(name):
    st, body = http(SIM, "POST", "/api/ecu/profiles/" + name + "/reload",
                    timeout=40)
    time.sleep(1.0)
    return st == 200 and '"ok":true' in body.replace(" ", ""), body.strip()[:120]


def sim_profile_delete(name):
    st, body = http(SIM, "DELETE", "/api/ecu/profiles/" + name)
    return st == 200, body.strip()[:120]


def sim_errors():
    st, d = get_json(SIM, "/api/ecu/errors")
    return d if st == 200 and isinstance(d, list) else None


def sim_silence(mode, pid):
    st, body = http(SIM, "POST", "/api/ecu/errors/%s/%s" % (mode, pid),
                    {"enabled": True, "response_type": "no_response",
                     "trigger": "continuous"})
    return st == 200 and '"ok":true' in body.replace(" ", "")


def sim_unsilence(mode, pid):
    st, body = http(SIM, "DELETE", "/api/ecu/errors/%s/%s" % (mode, pid))
    return st == 200


def silenced(errors, mode, pid):
    """The simulator lists an injection as `pid_key` "<mode>_<pid>" with the
    mode as two digits and the PID without leading zeros: "09_2", "22_F190"."""
    key = "%s_%s" % (mode, pid.lstrip("0") or "0")
    for e in errors or []:
        if str(e.get("pid_key", "")).upper() == key.upper():
            return bool(e.get("enabled"))
    return False


# ---- the DUT -------------------------------------------------------------------

def store(base):
    st, d = get_json(base, "/api/autopid/vehicles")
    return d if st == 200 and isinstance(d, dict) else None


def entry(st, key):
    for v in (st or {}).get("vehicles", []):
        if v.get("key") == key:
            return v
    return None


def fp_entries(st):
    return [v for v in (st or {}).get("vehicles", [])
            if str(v.get("key", "")).startswith("fp:")]


def detect(base, c, label, window=120):
    """Run the detection job and wait for it: the result status."""
    st, d = post_json(base, "/api/autopid/vehicles/detect", {})
    if not c.add("%s: detection started" % label, st == 202 and (d or {}).get("started"),
                 "%s %s" % (st, d)):
        return None
    t0 = time.time()
    while time.time() - t0 < window:
        time.sleep(2)
        st, s = get_json(base, "/api/autopid/std_scan")
        if st == 200 and (s or {}).get("status") in ("done", "failed"):
            c.add("%s: detection %s in %.0f s" % (label, s["status"], time.time() - t0),
                  s["status"] == "done",
                  "found %s%s" % (s.get("found"), (" error " + str(s.get("error")))
                                  if s.get("error") else ""))
            return s
    c.add("%s: detection finished within %d s" % (label, window), False)
    return None


def test_cmd(base, cmd, until=None, tries=5):
    """One shot through the test route, repeated 2 s apart until `until(d)`
    holds (at most `tries`). Right after the simulator's warm reload the bus
    is briefly down: the route can answer 409 (the bus guard), the chip can
    answer `STOPPED`, a reply can be empty. On a failure the dict carries
    `_status` and `_body` so the check can say what came back last."""
    last = {}
    for _ in range(tries):
        st, text = http(base, "POST", "/api/autopid/test", {"cmd": cmd},
                        timeout=40)
        try:
            d = json.loads(text)
        except ValueError:
            d = None
        if st == 200 and isinstance(d, dict) and d.get("raw") and \
                (until is None or until(d)):
            return d
        last = dict(d) if isinstance(d, dict) else {}
        last.update({"_status": st, "_body": text.strip()[:120]})
        time.sleep(2)
    return last


def main_bitmap(ecus):
    """`7E8:183F8003,7E9:...` -> the lowest id's bitmap as upper-case hex."""
    best = None
    for part in str(ecus or "").split(","):
        if ":" not in part:
            continue
        cid, bm = part.split(":", 1)
        try:
            n = int(cid, 16)
        except ValueError:
            continue
        if best is None or n < best[0]:
            best = (n, bm.upper())
    return best[1] if best else ""


def payload_bitmap(payload):
    """`06 41 00 18 3F 80 00` -> `183F8000`."""
    b = str(payload or "").split()
    return "".join(b[3:7]).upper() if len(b) >= 7 else ""


def main():
    args = sys.argv[1:]
    opt = lambda k, d=None: args[args.index(k) + 1] if k in args else d
    if "-h" in args or "--help" in args:
        print(__doc__)
        return 2
    dut = opt("--dut", "localhost:18081")
    base = "http://" + dut.rstrip("/")
    ts = time.strftime("%Y%m%d_%H%M%S")
    out = opt("--out", os.path.join(REPO, "test-reports", "logs",
                                    "autopid_fp_forget_" + ts))
    os.makedirs(out, exist_ok=True)
    print("AUTOPID FP FORGET bench: dut=%s out=%s" % (dut, out), flush=True)
    c = Checks()
    saved_cfg = saved_store = None
    profile_before = None
    uploaded = reloaded = False
    silenced_now = []

    def snap(name, data):
        with open(os.path.join(out, name), "w") as f:
            json.dump(data, f, indent=1)

    try:
        print("L0 preflight")
        st, info = get_json(base, "/api/info")
        if not c.add("dut answers", st == 200 and info,
                     "%s %s" % (st, (info or {}).get("fw_build"))):
            raise SystemExit(1)
        sim_net.install(SIM.split("//")[1])
        profs = sim_profiles()
        profile_before = (profs or {}).get("active")
        c.add("the simulator answers with its profile list", profs is not None,
              "active %s, playing %r" % (profile_before, sim_profile_name()))
        errs = sim_errors()
        c.add("the simulator's VIN is not silenced already", errs is not None and
              not any(silenced(errs, m, p) for m, p in VIN_SILENCE),
              "errors %s" % json.dumps(errs)[:100])
        c.add("the fixture profile is on disk", os.path.exists(FIXTURE), FIXTURE)
        saved_store = store(base)
        c.add("vehicle store saved", saved_store is not None,
              "%d car(s), current %r" % (len((saved_store or {}).get("vehicles", [])),
                                         (saved_store or {}).get("current")))
        st, saved_cfg = get_json(base, "/api/autopid/config")
        c.add("tables saved", st == 200 and saved_cfg is not None)
        snap("saved_store.json", saved_store)
        snap("saved_config.json", saved_cfg)
        if c.failed():
            raise SystemExit(1)
        if entry(saved_store, SIM_VIN) is None:
            print("  the store does not know the simulator's car: learning it")
            detect(base, c, "L0 learn")
            saved_store = store(base)
            snap("saved_store.json", saved_store)
        vin_car = entry(saved_store, SIM_VIN)
        if not c.add("the store knows the simulator's car by its VIN",
                     vin_car is not None, "ecus %r" % (vin_car or {}).get("ecus")):
            raise SystemExit(1)
        cars_before = len(saved_store.get("vehicles", []))
        current_before = saved_store.get("current")
        stale = fp_entries(saved_store)
        c.add("no fp: car in the store before the bench", not stale,
              ", ".join(v["key"] for v in stale))
        if c.failed():
            raise SystemExit(1)

        print("L1 stage")
        ok, detail = sim_upload(FIXTURE_NAME, FIXTURE)
        uploaded = c.add("the fixture profile uploaded to the simulator", ok, detail)
        ok, detail = sim_reload(FIXTURE_NAME)
        reloaded = c.add("the simulator plays it (warm reload)",
                         ok and sim_profile_name() == "Fp Dup Test",
                         "%s, playing %r" % (detail, sim_profile_name()))
        for m, p in VIN_SILENCE:
            if sim_silence(m, p):
                silenced_now.append((m, p))
        errs = sim_errors()
        c.add("the VIN replies are silenced (0902 and 22F190)",
              all(silenced(errs, m, p) for m, p in VIN_SILENCE),
              json.dumps(errs)[:160])
        t = test_cmd(base, "0902",
                     until=lambda d: str(d.get("raw", "")).strip() == "NO DATA")
        c.add("the DUT gets NO DATA on 0902", str(t.get("raw", "")).strip() == "NO DATA",
              "raw %r%s" % (t.get("raw"), " (%s %s)" % (t.get("_status"), t.get("_body"))
                            if "_status" in t else ""))
        t = test_cmd(base, "0100", until=lambda d: bool(d.get("payload")))
        bm_now, bm_stored = payload_bitmap(t.get("payload")), main_bitmap(vin_car.get("ecus"))
        c.add("the main ECU's support bitmap differs from the stored car's "
              "(no adoption possible)", bm_now and bm_stored and bm_now != bm_stored,
              "now %s, stored %s%s" % (bm_now, bm_stored,
                                       " (%s %s)" % (t.get("_status"), t.get("_body"))
                                       if "_status" in t else ""))
        if c.failed():
            raise SystemExit(1)

        print("L2 the duplicate")
        detect(base, c, "L2")
        st2 = store(base)
        snap("store_duplicate.json", st2)
        fps = fp_entries(st2)
        dup = fps[0] if fps else None
        c.add("a second car appeared beside the VIN car",
              st2 is not None and len(st2.get("vehicles", [])) == cars_before + 1 and
              len(fps) == 1 and entry(st2, SIM_VIN) is not None,
              "%d car(s): %s" % (len((st2 or {}).get("vehicles", [])),
                                 ", ".join(v["key"] for v in (st2 or {}).get("vehicles", []))))
        if dup is None:
            raise SystemExit(1)
        key = dup["key"]
        fp = key[3:]
        c.add("it is keyed fp:<8 hex>, has no VIN, is current and pending a profile",
              len(fp) == 8 and dup.get("vin") == "" and dup.get("current") is True and
              dup.get("pending_profile") is True and st2.get("current") == key,
              "%s vin %r current %r pending %r" % (key, dup.get("vin"), dup.get("current"),
                                                   dup.get("pending_profile")))
        c.add("it is named like the report ('Car <4 hex>') with fewer standard PIDs",
              dup.get("name") == "Car " + fp[:4] and
              0 < dup.get("std_supported", 0) < vin_car.get("std_supported", 0),
              "name %r, %s std PIDs against %s" % (dup.get("name"), dup.get("std_supported"),
                                                   vin_car.get("std_supported")))
        c.add("the VIN car is no longer current", entry(st2, SIM_VIN).get("current") is False)

        print("L3 the routes with the key as the browser sends it")
        enc = urllib.parse.quote(key, safe="")
        c.add("encodeURIComponent turns the colon into %3A", enc == "fp%3A" + fp, enc)
        st, body = http(base, "PUT", "/api/autopid/vehicles/" + enc, {"name": "Dup bench"})
        try:
            put = json.loads(body)
        except ValueError:
            put = {}
        c.add("PUT with the encoded key renames the car", st == 200 and
              put.get("name") == "Dup bench" and put.get("key") == key,
              "%s %s" % (st, body.strip()[:100]))
        st, body = http(base, "POST", "/api/autopid/vehicles/" + enc + "/activate", {})
        c.add("POST activate with the encoded key answers ok", st == 200 and
              '"ok":true' in body.replace(" ", ""), "%s %s" % (st, body.strip()[:80]))
        c.add("the store shows the new name",
              (entry(store(base), key) or {}).get("name") == "Dup bench")
        st, body = http(base, "DELETE", "/api/autopid/vehicles/" + enc)
        c.add("DELETE with the encoded key forgets the car (204)", st == 204,
              "%s %s (the report: 404 no such route)" % (st, body.strip()[:80]))
        st3 = store(base)
        snap("store_after_forget.json", st3)
        c.add("the list is back to the car(s) it had, the fp: car gone",
              st3 is not None and entry(st3, key) is None and
              len(st3.get("vehicles", [])) == cars_before,
              ", ".join(v["key"] for v in (st3 or {}).get("vehicles", [])))
        c.add("forgetting the current car cleared `current`", st3.get("current") == "")
        # with no current car the chip gets the no-car prelude (headers on,
        # the chip's protocol search): before 2026-10-26 it read ATH0 and the
        # sender's print strip blanked that into an empty command (`> ` /
        # `< ?`); the request itself may time out under the search, the
        # shape of the prelude is the check
        t = test_cmd(base, "0100", tries=1)
        tr = str(t.get("transcript", ""))
        c.add("the no-car prelude that follows carries ATH1 and ATTP0 with no empty "
              "command", "> ATH1\n" in tr and "> ATTP0\n" in tr and "> \n" not in tr,
              repr(tr)[:150])

        print("L4 the raw key and the refusals")
        detect(base, c, "L4")
        st4 = store(base)
        c.add("the detection re-created the same fp: car",
              entry(st4, key) is not None and (entry(st4, key) or {}).get("name") == "Car " + fp[:4],
              ", ".join(v["key"] for v in (st4 or {}).get("vehicles", [])))
        st, body = http(base, "DELETE", "/api/autopid/vehicles/" + key)
        c.add("DELETE with the raw colon (curl, python) forgets it too (204)", st == 204,
              "%s %s" % (st, body.strip()[:80]))
        c.add("the fp: car is gone again", entry(store(base), key) is None)
        st, body = http(base, "DELETE", "/api/autopid/vehicles/fp%3")
        c.add("a torn escape is 404 no such route", st == 404 and "no such route" in body,
              "%s %s" % (st, body.strip()[:80]))
        st, body = http(base, "DELETE", "/api/autopid/vehicles/fp%ZZ" + fp)
        c.add("a non-hex escape is 404 no such route", st == 404 and "no such route" in body,
              "%s %s" % (st, body.strip()[:80]))
        st, body = http(base, "PUT", "/api/autopid/vehicles/" + enc, {"name": "x"})
        c.add("the decoded key of a forgotten car is 404 no such vehicle (the decode "
              "happened, the lookup failed)", st == 404 and "no such vehicle" in body,
              "%s %s" % (st, body.strip()[:80]))
        vin_name = (entry(store(base), SIM_VIN) or {}).get("name", "")
        st, body = http(base, "PUT", "/api/autopid/vehicles/" +
                        urllib.parse.quote(SIM_VIN, safe=""), {"name": vin_name})
        try:
            put = json.loads(body)
        except ValueError:
            put = {}
        c.add("a VIN key passes the decoder unchanged (a no-op PUT on the VIN car "
              "answers its entry)", st == 200 and put.get("key") == SIM_VIN,
              "%s %s" % (st, body.strip()[:80]))
    finally:
        print("L5 restore")
        for m, p in silenced_now:
            c.add("the simulator answers %s%s again" % (m, p), sim_unsilence(m, p))
        if reloaded and profile_before:
            ok, detail = sim_reload(profile_before)
            c.add("the simulator plays its profile again (%s)" % profile_before,
                  ok and sim_profile_name() not in (None, "Fp Dup Test"),
                  "%s, playing %r" % (detail, sim_profile_name()))
        if uploaded:
            ok, detail = sim_profile_delete(FIXTURE_NAME)
            c.add("the fixture profile deleted from the simulator", ok, detail)
        if saved_store is not None:
            # the detection first: with no current car (the forget above
            # cleared it) the chip sits on the no-car prelude, the chip's own
            # protocol search, and a 1.5 s test shot times out under it; the
            # detection makes the VIN car current and pins its protocol
            detect(base, c, "L5")
            st5 = store(base)
            snap("store_restored.json", st5)
            c.add("the VIN car is current again and the store is as found",
                  st5 is not None and st5.get("current") == SIM_VIN and
                  len(st5.get("vehicles", [])) == len(saved_store.get("vehicles", [])) and
                  not fp_entries(st5),
                  "current %r, %d car(s)" % ((st5 or {}).get("current"),
                                             len((st5 or {}).get("vehicles", []))))
            t = test_cmd(base, "0902", until=lambda d: "49 02" in str(d.get("raw", "")))
            c.add("the DUT reads the VIN again", "49 02" in str(t.get("raw", "")),
                  "raw %r%s" % (str(t.get("raw", ""))[:60],
                                " (%s %s)" % (t.get("_status"), t.get("_body"))
                                if "_status" in t else ""))
        if saved_cfg is not None and saved_store is not None:
            # config.json is a copy of the CURRENT car's tables and a PUT
            # lands in that car's file: put the save back only when the VIN
            # car was current at L0 (then the save is its tables); with no
            # current car at L0 the save was a leftover of a forgotten car
            # and the detection above already gave the VIN car its own file
            st, cfg = get_json(base, "/api/autopid/config")
            if saved_store.get("current") != SIM_VIN:
                c.add("tables left to the VIN car's own file (no current car "
                      "at L0, the save was a leftover)", cfg is not None,
                      "%d row(s) now" % len((cfg or {}).get("pids", [])))
            elif cfg != saved_cfg:
                st, body = http(base, "PUT", "/api/autopid/config", saved_cfg)
                c.add("tables restored (they had moved)", st == 200,
                      "%s %s" % (st, body.strip()[:80]))
            else:
                c.add("tables as saved", True)

    failed = c.failed()
    print("checks: %d, failed: %d" % (len(c.rows), len(failed)))
    if failed:
        print("AUTOPID FP FORGET FAIL: " + "; ".join(failed))
        return 1
    print("AUTOPID FP FORGET PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
