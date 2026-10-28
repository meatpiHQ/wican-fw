"""A car WiCAN was not set up for: the bar, and the fold-in that brings the
chosen rows back (2026-10-26).

Ali set a device up on the ECU simulator in the Quick Setup (RPM and vehicle
speed chosen), took it to his car, and after the reboot the Dashboard was empty
with no word: the car answered as a new one (no VIN, another supported-PID
bitmap), became current with its rows stored off, and his two rows stayed with
the simulator's entry. The same-car reboot is clean (std_choice_bench.mjs, leg
reboot). The identity rule stays, by his decision ("keep the same behaviour but
show a banner to warn about it"): the status poll carries `vehicle` and the
page says so.

Legs:
  n0  as found: the VIN car current, the simulator on its default profile
  n1  Ali's state: exactly 010C and 010D on, polling on, both read
  n2  the fork: the simulator plays the fp-dup fixture (the same 7E8 ECU with
      seven PIDs disabled) with 0902 and 22F190 silenced, the DUT restarts (his
      reboot): a second car fp:... current, the live table its rows all off,
      nothing polled, `vehicle` pending with 2 cars, the VIN car's own file still
      holding the two rows; the Dashboard in Chrome shows the bar and the banner
  n3  the fold-in: POST .../vehicles/<fp>/merge {"into": <VIN>}: the VIN car
      current again with the two rows, read; `vehicle` not pending, 1 car; the
      bar gone
  close: the simulator back (its profile, the silences lifted, the fixture
      deleted), any extra car forgotten, the VIN car current, the as-found config
      and settings back (a restart when they moved); no unexpected reset

  python tools/testbench/obd/autopid_newcar_bench.py [--dut localhost:18081]
Prints AUTOPID NEWCAR PASS or AUTOPID NEWCAR FAIL: <checks>.
"""
import json
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "lib"))
import autopid_fp_forget_bench as fp  # noqa: E402  (the simulator helpers, the Checks)
import sim_net  # noqa: E402

TWO = {"010C", "010D"}
BAR_CHECK = os.path.join(HERE, "newcar_bar_check.mjs")


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


def main():
    dut = "localhost:18081"
    if "--dut" in sys.argv:
        dut = sys.argv[sys.argv.index("--dut") + 1]
    base = "http://" + dut
    sim_net.install(fp.SIM.split("//")[1])
    c = fp.Checks()

    def get(path):
        st, d = fp.get_json(base, path)
        return d if st == 200 else None

    def put(path, body):
        return fp.http(base, "PUT", path, body, timeout=60)

    def post(path, body=None):
        return fp.http(base, "POST", path, body, timeout=60)

    def upload(path, obj):
        # a file of the device put back as it was read (the fs upload route)
        import urllib.request
        data = json.dumps(obj).encode()
        req = urllib.request.Request(base + "/api/fs/upload?path=" + path,
                                     data=data, method="POST")
        req.add_header("Content-Type", "application/octet-stream")
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.status
        except Exception as e:
            log("upload", path, str(e)[:100])
            return 0

    def std_rows(cfg):
        return [(p["cmd"].upper(), p.get("enabled") is not False)
                for p in (cfg or {}).get("pids", []) if p.get("type") == "std"]

    def on_rows(cfg):
        return sorted(cmd for cmd, on in std_rows(cfg) if on)

    def polled(live):
        return sorted(p["name"] for p in (live or {}).get("params", [])
                      if not p.get("external") and p.get("value") is not None)

    def cars():
        v = get("/api/autopid/vehicles") or {}
        return [(e["key"], bool(e.get("current")), bool(e.get("pending_profile")))
                for e in v.get("vehicles", [])]

    def vehicle():
        return (get("/api/status") or {}).get("vehicle")

    def wait_back(label, tries=150):
        for _ in range(tries):
            if get("/api/info"):
                return True
            time.sleep(1)
        log(label, "the device did not come back")
        return False

    def restart(label):
        b0 = (get("/api/status") or {}).get("boot_count")
        post("/api/restart", {})
        time.sleep(8)
        for _ in range(150):
            st = get("/api/status")
            if st and st.get("boot_count") != b0:
                return True
            time.sleep(1)
        log(label, "no new boot within 150 s")
        return False

    def bar(expect, shot):
        r = subprocess.run(["node", BAR_CHECK, base, expect, shot], capture_output=True,
                           text=True, timeout=120)
        line = (r.stdout.strip().splitlines() or ["{}"])[-1]
        try:
            return json.loads(line)
        except ValueError:
            return {"ok": False, "error": (r.stderr or r.stdout)[-300:]}

    outdir = os.path.join(HERE, "..", "..", "..", "test-reports", "logs",
                          time.strftime("newcar_%Y%m%d_%H%M"))
    os.makedirs(outdir, exist_ok=True)

    # ---- n0: as found ----------------------------------------------------------------
    cfg0 = get("/api/autopid/config")
    set0 = get("/api/settings/autopid")
    cars0 = cars()
    st0 = get("/api/status") or {}
    # the store files byte for byte (2026-10-28): the fold-in keeps the other
    # car's fingerprint as a face on the VIN car, and a later detection with
    # the same fixture then recognises the car instead of forking (the
    # fingerprint bench's L2 failed on a face this bench had left)
    index0 = get("/api/fs/download?path=/data/autopid/vehicles.json")
    vinfile0 = get("/api/fs/download?path=/data/autopid/vehicles/" + fp.SIM_VIN + ".json")
    sim_before = (fp.sim_profiles() or {}).get("active")
    log("as found: std rows", len(std_rows(cfg0)), "autopid",
        {k: (set0 or {}).get(k) for k in ("enabled", "std_enabled")}, "cars", cars0,
        "sim", sim_before, fp.sim_profile_name())
    c.add("n0 the DUT answers with its config, settings and store",
          cfg0 is not None and set0 is not None and cars0 != [])
    c.add("n0 the VIN car is in the store", any(k == fp.SIM_VIN for k, _, _ in cars0), str(cars0))
    c.add("n0 the simulator plays its own profile", bool(sim_before) and not any(
        fp.silenced(e, m, p) for e in [fp.sim_errors() or []] for m, p in fp.VIN_SILENCE),
        "%s, playing %r" % (sim_before, fp.sim_profile_name()))
    v0 = vehicle()
    c.add("n0 GET /api/status carries the vehicle summary (the current car, not pending)",
          isinstance(v0, dict) and v0.get("key") == fp.SIM_VIN and v0.get("pending") is False
          and v0.get("cars") == len(cars0), str(v0))
    if c.failed():
        print("AUTOPID NEWCAR FAIL: " + "; ".join(c.failed()))
        return 2

    staged = silenced = False
    try:
        # ---- n1: Ali's state ---------------------------------------------------------
        cfg = json.loads(json.dumps(cfg0))
        cfg["pids"] = [p for p in cfg["pids"] if p.get("type") != "std" or p["cmd"].upper() in TWO]
        for p in cfg["pids"]:
            if p.get("type") == "std":
                p.pop("enabled", None)
        st, body = put("/api/autopid/config", cfg)
        c.add("n1 the config with exactly RPM and speed on", st == 200 and on_rows(get("/api/autopid/config")) == ["010C", "010D"], body[:80])
        s = {k: v for k, v in set0.items() if k not in ("degraded", "pending_reboot")}
        if not (s.get("enabled") and s.get("std_enabled")):
            s["enabled"] = True
            s["std_enabled"] = True
            st, body = put("/api/settings/autopid", s)
            post("/api/settings/submit", {})
            time.sleep(8)
            wait_back("n1")
        time.sleep(20)
        c.add("n1 both read", polled(get("/api/autopid")) == ["EngineRPM", "VehicleSpeed"], str(polled(get("/api/autopid"))))

        # ---- n2: the fork ------------------------------------------------------------
        ok, detail = fp.sim_upload(fp.FIXTURE_NAME, fp.FIXTURE)
        staged = c.add("n2 the fixture profile uploaded to the simulator", ok, detail)
        ok, detail = fp.sim_reload(fp.FIXTURE_NAME)
        c.add("n2 the simulator plays it (warm reload)", ok, detail)
        for m, p in fp.VIN_SILENCE:
            c.add("n2 %s %s silenced" % (m, p), fp.sim_silence(m, p))
        silenced = True
        time.sleep(3)
        c.add("n2 the DUT restarted (his reboot)", restart("n2"))
        deadline = time.time() + 120
        while time.time() < deadline:
            cs = cars()
            if any(cur and k != fp.SIM_VIN for k, cur, _ in cs):
                break
            time.sleep(3)
        time.sleep(20)
        cs = cars()
        v2 = vehicle()
        cfg2 = get("/api/autopid/config")
        live2 = get("/api/autopid")
        fork = [k for k, cur, _ in cs if cur and k != fp.SIM_VIN]
        with open(os.path.join(outdir, "n2_after_fork.json"), "w", encoding="utf-8") as f:
            json.dump({"cars": cs, "vehicle": v2, "std_rows": std_rows(cfg2), "polled": polled(live2)}, f, indent=1)
        c.add("n2 a second car is current (no VIN, another bitmap)", len(fork) == 1 and fork[0].startswith("fp:") and len(cs) == 2, str(cs))
        c.add("n2 the live table is the new car's rows, all off",
              std_rows(cfg2) != [] and all(not on for _, on in std_rows(cfg2)) and "010C" in [r for r, _ in std_rows(cfg2)], str(std_rows(cfg2)))
        c.add("n2 nothing is polled: the dashboard is empty", polled(live2) == [], str(polled(live2)))
        c.add("n2 the vehicle summary says pending, 2 cars, no VIN",
              isinstance(v2, dict) and v2.get("pending") is True and v2.get("cars") == 2 and v2.get("vin") == ""
              and v2.get("key") == (fork[0] if fork else ""), str(v2))
        vin_file = get("/api/fs/download?path=/data/autopid/vehicles/" + fp.SIM_VIN + ".json")
        c.add("n2 the VIN car's own file still holds the two rows", on_rows(vin_file) == ["010C", "010D"], str(std_rows(vin_file)))
        r = bar("shown", os.path.join(outdir, "n2_dashboard.png"))
        c.add("n2 the Dashboard in Chrome shows the bar and the banner", r.get("ok") is True, str(r)[:300])

        # ---- n3: the fold-in ---------------------------------------------------------
        if fork:
            st, body = post("/api/autopid/vehicles/" + fork[0] + "/merge", {"into": fp.SIM_VIN})
            c.add("n3 the merge accepted", st == 200, "%s %s" % (st, body[:80]))
            time.sleep(4)
            v3 = vehicle()
            cs3 = cars()
            c.add("n3 the VIN car is current again, alone, not pending",
                  isinstance(v3, dict) and v3.get("key") == fp.SIM_VIN and v3.get("pending") is False and v3.get("cars") == 1
                  and [k for k, cur, _ in cs3 if cur] == [fp.SIM_VIN], "%s %s" % (v3, cs3))
            c.add("n3 the two rows are back in the live table", on_rows(get("/api/autopid/config")) == ["010C", "010D"], str(std_rows(get("/api/autopid/config"))))
            time.sleep(20)
            c.add("n3 both read again", polled(get("/api/autopid")) == ["EngineRPM", "VehicleSpeed"], str(polled(get("/api/autopid"))))
            r = bar("hidden", os.path.join(outdir, "n3_dashboard.png"))
            c.add("n3 the bar and the banner are gone", r.get("ok") is True, str(r)[:300])
    finally:
        # ---- close: everything back ------------------------------------------------------
        log("restoring")
        if silenced:
            for m, p in fp.VIN_SILENCE:
                fp.sim_unsilence(m, p)
        if staged:
            if sim_before:
                fp.sim_reload(sim_before)
            fp.sim_profile_delete(fp.FIXTURE_NAME)
        for k, cur, _ in cars():
            if k != fp.SIM_VIN and not any(k == k0 for k0, _, _ in cars0):
                fp.http(base, "DELETE", "/api/autopid/vehicles/" + k)
        if not any(cur for k, cur, _ in cars() if k == fp.SIM_VIN):
            post("/api/autopid/vehicles/" + fp.SIM_VIN + "/activate", {})
            time.sleep(3)
        put("/api/autopid/config", cfg0)
        files_back = False
        if index0 is not None and vinfile0 is not None:
            files_back = (upload("/data/autopid/vehicles.json", index0) == 200 and
                          upload("/data/autopid/vehicles/" + fp.SIM_VIN + ".json", vinfile0) == 200)
            log("close: the store files put back:", files_back)
        want = {k: v for k, v in set0.items() if k not in ("degraded", "pending_reboot")}
        have = {k: v for k, v in (get("/api/settings/autopid") or {}).items() if k not in ("degraded", "pending_reboot")}
        if want != have:
            put("/api/settings/autopid", want)
            post("/api/settings/submit", {})
            time.sleep(8)
            wait_back("close")
        elif files_back:
            restart("close: the store reloaded from its files")
        st1 = get("/api/status") or {}
        c.add("close the simulator plays its own profile again, nothing silenced",
              fp.sim_profile_name() not in (None, "Fp Dup Test") and not (fp.sim_errors() or []),
              "%r %s" % (fp.sim_profile_name(), fp.sim_errors()))
        c.add("close the store, the config and the settings as found",
              cars() == cars0 and std_rows(get("/api/autopid/config")) == std_rows(cfg0)
              and {k: v for k, v in (get("/api/settings/autopid") or {}).items() if k not in ("degraded", "pending_reboot")} == want,
              str(cars()))
        def faces_of(store):
            return [v.get("faces") or [] for v in (store or {}).get("vehicles", []) if v.get("key") == fp.SIM_VIN]
        c.add("close the VIN car carries the faces it had (none left by the fold-in)",
              faces_of(get("/api/autopid/vehicles")) == faces_of(index0), str(faces_of(get("/api/autopid/vehicles"))))
        c.add("close no unexpected reset", st1.get("unexpected_resets") == st0.get("unexpected_resets"),
              "%s -> %s" % (st0.get("unexpected_resets"), st1.get("unexpected_resets")))
    failed = c.failed()
    print(("AUTOPID NEWCAR FAIL: " + "; ".join(failed)) if failed else
          "AUTOPID NEWCAR PASS (%d checks)" % len(c.rows))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
