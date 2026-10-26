#!/usr/bin/env python3
"""AutoPID profile-first bench  ->  AUTOPID PROFILE FIRST PASS / FAIL   (discussion #1018)

Ali, 2026-10-26, after the fp-key Forget fix: "change the quick setup to ask the
user to select the profile before we probe an identity". The wizard's vehicle
step now asks which car it is first; the detection carries the profile along:
  1. a new car is stored under the profile's name with the profile attached
     (POST /api/autopid/vehicles/detect {profile, name, protocol, init, cmds});
  2. a car that answers nothing standard (no 0100, no 22F400, no VIN: a Nissan
     Leaf) is confirmed by the profile's own request instead and stored, keyed
     by the ECUs that answered it (`confirmed_by: "profile"`);
  3. a car seen without its VIN can be folded into the entry it belongs to
     (POST .../<key>/merge {"into"}): its ECU-set hash becomes a face of that
     entry, so the next contact that answers the same way lands on it;
  4. the identification asks 0100 three times and unites the replies (host-
     tested; here the simulator answers the same way every time).

The bench plays it on the ECU simulator (its default profile, VIN
1WCAN0FW0P0000001): L0 preflight and saves; L1 the simulator's car forgotten
and detected again WITH a profile hint: the new entry carries the name and the
profile from the start, pending_profile false, confirmed_by obd; L2 the
simulator silenced on 0100, 22F400, 0902 and 22F190 (its error injection) and
detected with the hint whose request is one the simulator answers physically
(22F405 on 7E4, its HV battery ECU, the request's own init addressing it):
a second entry keyed fp:<hash> over that ECU, confirmed_by profile, no
standard rows, the result's probe_answered; L3 the fp: entry folded into the
VIN car: the VIN car carries the face, the entry is gone, and the same
silenced contact now lands on the VIN car (known); L4 the refusals (merge
into itself 400, an unknown target 404, a bad request in the body 400); L5
restore (the simulator answering again, the store as found, the tables
compared with the save).

usage: autopid_profile_first_bench.py [--dut localhost:18081] [--out DIR]
  Runs on the PC (the simulator at 192.168.8.1 through sim_net); the DUT
  through an ssh tunnel (`ssh -N -L 18081:10.42.1.194:80 rpi001`) or by
  address.
Needs: the main firmware on the DUT on the bench AP, the ECU simulator
answering 7DF with its default profile (its car in the vehicle store or
learned by the bench), the PSU above the sleep voltage. ~3 min, no restart.
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
SIM_VIN = "1WCAN0FW0P0000001"
PROFILE = "Bench: Generic Petrol Sedan"
NAME = "Bench profile car"
# nothing standard: the chip's 0100 / 22F400 walk and the VIN requests
SILENCE = (("01", "00"), ("22", "F400"), ("09", "02"), ("22", "F190"))
# the simulator's HV battery DIDs live on 7E4 (reply 7EC): a request with its
# own init addressing another ECU than the engine's, as a Leaf profile does
HINT = {"profile": PROFILE, "name": NAME, "protocol": "6", "init": "ATSP6",
        "cmds": [{"cmd": "22F4051", "init": "ATSH7E4"}, "010C1"]}


def http(base, method, path, body=None, timeout=30):
    data = None if body is None else json.dumps(body).encode()
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


def sim_errors():
    st, d = get_json(SIM, "/api/ecu/errors")
    return d if st == 200 and isinstance(d, list) else None


def sim_silence(mode, pid, on):
    if on:
        st, body = http(SIM, "POST", "/api/ecu/errors/%s/%s" % (mode, pid),
                        {"enabled": True, "response_type": "no_response",
                         "trigger": "continuous"})
        return st == 200 and '"ok":true' in body.replace(" ", "")
    st, _ = http(SIM, "DELETE", "/api/ecu/errors/%s/%s" % (mode, pid))
    return st == 200


def silenced(errors, mode, pid):
    key = "%s_%s" % (mode, pid.lstrip("0") or "0")
    return any(str(e.get("pid_key", "")).upper() == key.upper() and e.get("enabled")
               for e in errors or [])


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


def detect(base, c, label, body=None, window=150):
    st, text = http(base, "POST", "/api/autopid/vehicles/detect", body if body is not None else {})
    if not c.add("%s: detection started" % label, st == 202 and '"started":true' in text.replace(" ", ""),
                 "%s %s" % (st, text.strip()[:100])):
        return None, None
    t0 = time.time()
    while time.time() - t0 < window:
        time.sleep(2)
        st, s = get_json(base, "/api/autopid/std_scan")
        if st == 200 and (s or {}).get("status") in ("done", "failed"):
            c.add("%s: detection %s in %.0f s" % (label, s["status"], time.time() - t0),
                  True, "found %s%s" % (s.get("found"), (" error " + str(s.get("error")))
                                        if s.get("error") else ""))
            st, r = get_json(base, "/api/autopid/std_scan/result")
            return s, (r if st == 200 else None)
    c.add("%s: detection finished within %d s" % (label, window), False)
    return None, None


def forget(base, key):
    st, _ = http(base, "DELETE", "/api/autopid/vehicles/" + urllib.parse.quote(key, safe=""))
    return st in (204, 404)


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
                                    "autopid_profile_first_" + ts))
    os.makedirs(out, exist_ok=True)
    print("AUTOPID PROFILE FIRST bench: dut=%s out=%s" % (dut, out), flush=True)
    c = Checks()
    saved_cfg = saved_store = None
    silenced_now = []
    fp_key = ""

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
        errs = sim_errors()
        c.add("the simulator answers, nothing of it silenced", errs is not None and
              not any(silenced(errs, m, p) for m, p in SILENCE), json.dumps(errs)[:100])
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
        if not c.add("the store knows the simulator's car by its VIN",
                     entry(saved_store, SIM_VIN) is not None):
            raise SystemExit(1)
        c.add("no fp: car in the store before the bench", not fp_entries(saved_store),
              ", ".join(v["key"] for v in fp_entries(saved_store)))
        if c.failed():
            raise SystemExit(1)

        print("L1 a new car detected with the profile named first")
        c.add("the simulator's car forgotten for the leg", forget(base, SIM_VIN) and
              entry(store(base), SIM_VIN) is None)
        s, r = detect(base, c, "L1", HINT)
        st1 = store(base)
        snap("store_l1.json", st1)
        e = entry(st1, SIM_VIN) or {}
        c.add("the entry is stored under the name with the profile attached, not pending",
              e.get("name") == NAME and e.get("profile") == PROFILE and
              e.get("pending_profile") is False and e.get("specific_init") == "ATSP6",
              "name %r profile %r pending %r init %r" % (e.get("name"), e.get("profile"),
                                                         e.get("pending_profile"), e.get("specific_init")))
        c.add("a standard request answered: confirmed by obd, the rows found, the VIN read",
              e.get("confirmed_by") == "obd" and (e.get("std_supported") or 0) > 0 and
              (r or {}).get("confirmed_by") == "obd" and (r or {}).get("probe_answered") is None and
              (r or {}).get("vin") == SIM_VIN and (r or {}).get("name") == NAME,
              "confirmed_by %r std %s result %s/%s name %r" % (
                  e.get("confirmed_by"), e.get("std_supported"), (r or {}).get("confirmed_by"),
                  (r or {}).get("probe_answered"), (r or {}).get("name")))
        c.add("the entry carries no face and is current", e.get("faces") == [] and e.get("current") is True)

        print("L2 nothing standard answers: the profile's own request confirms the car")
        for m, p in SILENCE:
            if sim_silence(m, p, True):
                silenced_now.append((m, p))
        errs = sim_errors()
        c.add("0100, 22F400, 0902 and 22F190 silenced on the simulator",
              all(silenced(errs, m, p) for m, p in SILENCE), json.dumps(errs)[:160])
        s, r = detect(base, c, "L2", HINT)
        st2 = store(base)
        snap("store_l2.json", st2)
        fps = fp_entries(st2)
        dup = fps[0] if fps else {}
        fp_key = dup.get("key", "")
        c.add("a second entry appeared, keyed by the ECUs the profile's request answered from",
              len(fps) == 1 and fp_key.startswith("fp:") and dup.get("vin") == "",
              ", ".join(v["key"] for v in (st2 or {}).get("vehicles", [])))
        c.add("it is confirmed by the profile: no standard rows, named, the profile attached",
              dup.get("confirmed_by") == "profile" and dup.get("std_supported") == 0 and
              dup.get("name") == NAME and dup.get("profile") == PROFILE and dup.get("current") is True,
              "confirmed_by %r std %s name %r profile %r" % (dup.get("confirmed_by"), dup.get("std_supported"),
                                                              dup.get("name"), dup.get("profile")))
        c.add("the result says which request answered (the physical 22F405 with its own init)",
              (r or {}).get("confirmed_by") == "profile" and (r or {}).get("probe_answered") == "22F4051" and
              (r or {}).get("found") == 0 and (r or {}).get("known") is False,
              "result confirmed_by %r probe_answered %r found %s known %s" % (
                  (r or {}).get("confirmed_by"), (r or {}).get("probe_answered"), (r or {}).get("found"),
                  (r or {}).get("known")))
        c.add("its ECU set is the responder of that request (7EC, the HV battery ECU, not the engine's 7E8)",
              "7EC:" in (dup.get("ecus") or "") and "7E8:" not in (dup.get("ecus") or ""),
              "ecus %r" % dup.get("ecus"))
        st, cfg2 = get_json(base, "/api/autopid/config")
        c.add("the live tables hold no standard row for it",
              st == 200 and not [p for p in (cfg2 or {}).get("pids", []) if p.get("type") == "std"],
              "%d row(s)" % len((cfg2 or {}).get("pids", [])))

        print("L3 the fp: entry folded into the VIN car")
        enc = urllib.parse.quote(fp_key, safe="")
        st, body = http(base, "POST", "/api/autopid/vehicles/" + enc + "/merge", {"into": SIM_VIN})
        c.add("merge answers ok", st == 200 and '"ok":true' in body.replace(" ", ""),
              "%s %s" % (st, body.strip()[:80]))
        st3 = store(base)
        snap("store_l3.json", st3)
        vin_car = entry(st3, SIM_VIN) or {}
        c.add("the VIN car carries the face, the fp: entry is gone, the VIN car is current",
              fp_key[3:] in (vin_car.get("faces") or []) and entry(st3, fp_key) is None and
              vin_car.get("current") is True and len(st3.get("vehicles", [])) == 1,
              "faces %s, %d car(s), current %r" % (vin_car.get("faces"), len((st3 or {}).get("vehicles", [])),
                                                   (st3 or {}).get("current")))
        s, r = detect(base, c, "L3 again", HINT)
        st3b = store(base)
        c.add("the same silenced contact now lands on the VIN car (a face matched): known, no new entry",
              (r or {}).get("key") == SIM_VIN and (r or {}).get("known") is True and
              len(st3b.get("vehicles", [])) == 1 and not fp_entries(st3b),
              "result key %r known %s, %d car(s)" % ((r or {}).get("key"), (r or {}).get("known"),
                                                     len((st3b or {}).get("vehicles", []))))
        c.add("the VIN car keeps confirmed_by obd (a standard request answered once)",
              (entry(st3b, SIM_VIN) or {}).get("confirmed_by") == "obd")

        print("L4 the refusals")
        st, body = http(base, "POST", "/api/autopid/vehicles/" + SIM_VIN + "/merge", {"into": SIM_VIN})
        c.add("merge into itself is 400", st == 400, "%s %s" % (st, body.strip()[:80]))
        st, body = http(base, "POST", "/api/autopid/vehicles/" + SIM_VIN + "/merge", {"into": "fp:00000000"})
        c.add("merge into an unknown car is 404", st == 404, "%s %s" % (st, body.strip()[:80]))
        st, body = http(base, "POST", "/api/autopid/vehicles/fp%3A00000000/merge", {"into": SIM_VIN})
        c.add("merge of an unknown car is 404", st == 404, "%s %s" % (st, body.strip()[:80]))
        st, body = http(base, "POST", "/api/autopid/vehicles/detect", {"profile": PROFILE, "cmds": ["not hex"]})
        c.add("a detect body with a bad request is 400 and starts nothing", st == 400,
              "%s %s" % (st, body.strip()[:80]))
        st, body = http(base, "POST", "/api/autopid/vehicles/detect", {"profile": PROFILE, "init": "ATPP0C SV 68"})
        c.add("a detect body whose init would write the chip's EEPROM is 400", st == 400,
              "%s %s" % (st, body.strip()[:80]))
    finally:
        print("L5 restore")
        for m, p in silenced_now:
            c.add("the simulator answers %s%s again" % (m, p), sim_silence(m, p, False))
        if saved_store is not None:
            # the bench's entries go; the simulator's car is learned again as it was
            # (a plain detection: default name, no profile, pending), then the tables
            cur = store(base) or {"vehicles": []}
            for v in cur.get("vehicles", []):
                forget(base, v["key"])
            detect(base, c, "L5")
            st5 = store(base)
            snap("store_restored.json", st5)
            e5 = entry(st5, SIM_VIN) or {}
            c.add("the store is as found: the simulator's car alone, current, no face, pending",
                  len(st5.get("vehicles", [])) == 1 and st5.get("current") == SIM_VIN and
                  e5.get("faces") == [] and e5.get("pending_profile") is True and e5.get("profile") == "",
                  "%d car(s), current %r, faces %s, pending %s" % (
                      len((st5 or {}).get("vehicles", [])), (st5 or {}).get("current"), e5.get("faces"),
                      e5.get("pending_profile")))
            saved_e = entry(saved_store, SIM_VIN) or {}
            if saved_e.get("profile") or saved_e.get("pending_profile") is False:
                st, body = http(base, "PUT", "/api/autopid/vehicles/" + SIM_VIN,
                                {"name": saved_e.get("name", ""), "profile": saved_e.get("profile", ""),
                                 "specific_init": saved_e.get("specific_init", "")})
                c.add("the entry's name/profile as saved", st == 200, "%s %s" % (st, body.strip()[:60]))
        if saved_cfg is not None:
            st, cfg = get_json(base, "/api/autopid/config")
            if cfg != saved_cfg:
                st, body = http(base, "PUT", "/api/autopid/config", saved_cfg)
                c.add("tables restored (they had moved)", st == 200, "%s %s" % (st, body.strip()[:80]))
            else:
                c.add("tables as saved", True)

    failed = c.failed()
    print("checks: %d, failed: %d" % (len(c.rows), len(failed)))
    if failed:
        print("AUTOPID PROFILE FIRST FAIL: " + "; ".join(failed))
        return 1
    print("AUTOPID PROFILE FIRST PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
