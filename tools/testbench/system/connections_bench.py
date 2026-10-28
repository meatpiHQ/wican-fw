#!/usr/bin/env python3
"""Connections (bridges) bench  ->  CONNECTIONS PASS / FAIL  (2026-10-29)

The field report of 2026-10-28 (internal/unable-to-save-config): a WiCAN PRO
whose bridge list carried two rows pointing at socket servers that no longer
existed (br4 -> udp3333, br5 -> udp23) could not save ANY settings page any
more ("Apply failed: br4: unknown endpoint 'udp3333'"), could not remove the
rows, and latched a boot_errors fault at every start. The bridge validator
checked a runtime PUT against the jacks registered at boot, so a bridge to a
server created in the same submit was refused too (the Connections card's own
flow), and a bridge left behind by a server rename or removal poisoned every
later save.

Proves, over the DUT's API through test.ps1's tunnel:

  c0  baseline     the three sections (bridge_manager, socket_manager,
                   websocket_manager), /api/bridges, status, faults; a
                   disabled server to borrow (the card reuses one when the
                   four slots are taken), udp0 as shipped
  c1  one submit   the card's order: PUT bridge_manager with br_bench
                   (can <-raw-> tcp3300) BEFORE the server exists, then PUT
                   socket_manager with the borrowed slot as tcp3300 (TCP 3300
                   on), submit, a planned restart: br_bench up on /api/bridges,
                   tcp3300 up and carried by br_bench on /api/sockets, 0 E
                   lines since the restart, no new fault. A firmware that
                   refuses the first PUT ("unknown endpoint") is recorded as
                   FAIL and the state is planted the long way (the server
                   first, a restart, then the bridge, a restart), so the trap
                   leg runs all the same.
  c2  the trap     the server taken away under the bridge (PUT socket_manager
                   with the slot back as found, submit, a restart): br_bench
                   not built, /api/bridges says why (up false, reason naming
                   tcp3300), ONE W line and no E line from bridge_manager since
                   the restart, no boot_errors fault; a PUT of the bridge list
                   that still carries br_bench (switched off) is accepted; a
                   PUT without it is accepted too
  c3  the page     (--ui) connections_ui_check.mjs in Chrome through the same
                   tunnel while the trap is planted: nothing staged on open,
                   the stranded row shown with the missing server named and
                   removable, its removal and the Submit accepted (a restart),
                   a new connection on a new TCP port made from the card in
                   ONE submit (a restart)
  c4  clean-up     the bridge list and the servers as found (a restart when
                   needed), the boot clean; a boot_errors fault the OLD
                   firmware latched during the run is cleared and reported

usage: connections_bench.py [--dut localhost:18081] [--ui] [--port 3300]
                            [--out <dir for the UI leg's shots>]
  run on the PC with the DUT through test.ps1's ssh tunnel (the connections
  stage). 3 to 4 planned restarts (5 to 6 with --ui), about 3 to 5 min.
Needs: the DUT reachable, a disabled server slot (udp0 as shipped).
"""
import json
import os
import subprocess
import sys
import time
import urllib.request

ARGS = sys.argv[1:]


def arg(name, default):
    return ARGS[ARGS.index(name) + 1] if name in ARGS else default


DUT = "http://" + arg("--dut", "localhost:18081")
UI = "--ui" in ARGS
PORT = int(arg("--port", "3300"))
OUT = arg("--out", "")
HERE = os.path.dirname(os.path.abspath(__file__))
BRIDGE = "br_bench"
JACK = "tcp%d" % PORT  # the name the Connections card gives a new TCP server
# E lines that are not this bench's business (the bench AP has no internet:
# fw_update's manifest fetch and friends)
NOISE = ("data_logger", "esp-tls", "HTTP_CLIENT", "esp-x509-crt-bundle")
CHECKS = []


def now():
    return time.strftime("%H:%M:%S")


def check(name, ok, detail=""):
    CHECKS.append((name, bool(ok)))
    print("%s %s: %s%s" % (now(), "PASS" if ok else "FAIL", name,
                           (" (" + str(detail)[:300] + ")") if detail != "" else ""))
    return bool(ok)


def http(path, method="GET", body=None, timeout=8):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(DUT + path, data=data, method=method)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode(errors="replace")
            try:
                return r.status, json.loads(raw)
            except ValueError:
                return r.status, raw
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, json.loads(exc.read().decode())
        except Exception:  # noqa: BLE001
            return exc.code, None
    except Exception as exc:  # noqa: BLE001
        return 0, str(exc)


def settings(comp):
    st, s = http("/api/settings/" + comp)
    if st != 200 or not isinstance(s, dict):
        return None
    s.pop("degraded", None)
    s.pop("pending_reboot", None)
    return s


def put(comp, body):
    """PUT a settings section: (status, the firmware's error text or '')"""
    st, r = http("/api/settings/" + comp, "PUT", body, timeout=15)
    err = r.get("error", "") if isinstance(r, dict) else str(r)
    return st, err


def boot_count():
    st, s = http("/api/status")
    return s.get("boot_count") if st == 200 and isinstance(s, dict) else None


def faults():
    st, f = http("/api/faults")
    return f.get("faults", []) if st == 200 and isinstance(f, dict) else None


def submit(label, limit=120):
    """POST /api/settings/submit; waits for the planned restart; the new
    boot count, or None when the DUT did not come back as expected"""
    b0 = boot_count()
    st, r = http("/api/settings/submit", "POST", timeout=15)
    if st != 200 or not isinstance(r, dict):
        check(label + ": submit answered", False, (st, r))
        return None
    if not r.get("reboot"):
        check(label + ": submit restarts the device", False, r)
        return None
    print("%s %s: submitted, the DUT restarts (boot %s)" % (now(), label, b0))
    t0 = time.time()
    time.sleep(6)
    while time.time() - t0 < limit:
        b1 = boot_count()
        if b1 is not None and (b0 is None or b1 != b0):
            # let the boot settle: the ring, the bridges, the sockets
            time.sleep(4)
            print("%s %s: the DUT is back (boot %s)" % (now(), label, b1))
            return b1
        time.sleep(2)
    check(label + ": the DUT came back", False, "boot count still %s" % b0)
    return None


def ring_since_restart():
    """the log ring's lines from the last planned-restart marker on
    (the old boot's last ms and the new boot), ANSI stripped"""
    st, txt = http("/api/logs/ring", timeout=15)
    if st != 200 or not isinstance(txt, str):
        return None
    import re
    lines = [re.sub(r"\x1b\[[0-9;]*m", "", ln) for ln in txt.split("\n")]
    at = -1
    for i, ln in enumerate(lines):
        if "restart_tracker: restarting:" in ln:
            at = i
    return lines[at:] if at >= 0 else lines


def e_lines(lines):
    return [ln for ln in (lines or []) if "E (" in ln and not any(n in ln for n in NOISE)]


def bridge_row(name):
    st, b = http("/api/bridges")
    if st != 200 or not isinstance(b, dict):
        return None
    for row in b.get("bridges", []):
        if row.get("name") == name:
            return row
    return None


def socket_row(name):
    st, s = http("/api/sockets")
    if st != 200 or not isinstance(s, dict):
        return None
    for row in s.get("servers", []):
        if row.get("name") == name:
            return row
    return None


def with_bridge(bridges, enabled=True):
    return [b for b in bridges if b.get("name") != BRIDGE] + [
        {"name": BRIDGE, "a": "can", "b": JACK, "translator": "raw", "enabled": enabled}]


def without_bridge(bridges):
    return [b for b in bridges if b.get("name") != BRIDGE]


def servers_with_jack(servers, slot):
    out = []
    for s in servers:
        s = dict(s)
        if s.get("name") == slot:
            s.update({"name": JACK, "proto": "tcp", "port": PORT, "enabled": True})
        out.append(s)
    return out


def main():
    print("== connections bench: DUT %s, jack %s on TCP %d%s ==" % (DUT, JACK, PORT, ", with the page" if UI else ""))
    st, info = http("/api/info")
    if st != 200:
        print("CONNECTIONS FAIL: no DUT at %s (%s %s)" % (DUT, st, info))
        sys.exit(2)
    print("%s firmware %s (%s)" % (now(), (info or {}).get("fw_version"), (info or {}).get("fw_build")))

    # ---- c0 baseline ----
    bm0, sm0, wm0 = settings("bridge_manager"), settings("socket_manager"), settings("websocket_manager")
    f0 = faults()
    if not check("c0 the three sections were read", bm0 and sm0 and wm0 is not None, (bool(bm0), bool(sm0), bool(wm0))):
        sys.exit(2)
    bridges0, servers0 = bm0["bridges"], sm0["servers"]
    names0 = {b["name"] for b in bridges0}
    if BRIDGE in names0 or any(s["name"] == JACK for s in servers0):
        print("CONNECTIONS FAIL: %s or %s already on the DUT: a previous run left them (restore by hand)" % (BRIDGE, JACK))
        sys.exit(2)
    if any(Number(s.get("port")) == PORT and s.get("enabled") for s in servers0):
        print("CONNECTIONS FAIL: TCP %d is already served on the DUT (pass --port)" % PORT)
        sys.exit(2)
    slot = next((s["name"] for s in servers0 if not s.get("enabled")), None)
    if not check("c0 a disabled server to borrow (the card reuses one when the four slots are taken)", slot, [(s["name"], s.get("enabled")) for s in servers0]):
        sys.exit(2)
    check("c0 no fault latched before the run", f0 == [], f0)
    print("%s baseline: %d bridges %s, servers %s, borrowing %s" % (now(), len(bridges0), sorted(names0), [(s["name"], s["proto"], s["port"], s["enabled"]) for s in servers0], slot))
    restarts = 0
    old_firmware = False

    try:
        # ---- c1 one submit, the card's order ----
        st, err = put("bridge_manager", {"bridges": with_bridge(bridges0)})
        accepted = check("c1 PUT bridge_manager with %s -> %s BEFORE the server exists is accepted (the Connections card's one-submit flow)" % (BRIDGE, JACK), st == 200, (st, err))
        if accepted:
            st, err = put("socket_manager", {"servers": servers_with_jack(servers0, slot)})
            check("c1 PUT socket_manager with %s as %s TCP %d on" % (slot, JACK, PORT), st == 200, (st, err))
            if submit("c1") is None:
                raise SystemExit(2)
            restarts += 1
        else:
            old_firmware = True
            print("%s c1: the firmware refuses a bridge to a jack it has not registered: '%s'. Planting the state the long way (two restarts)." % (now(), err))
            st, err = put("socket_manager", {"servers": servers_with_jack(servers0, slot)})
            check("c1 (plant) PUT socket_manager with %s as %s" % (slot, JACK), st == 200, (st, err))
            if submit("c1 (plant, the server)") is None:
                raise SystemExit(2)
            restarts += 1
            st, err = put("bridge_manager", {"bridges": with_bridge(bridges0)})
            check("c1 (plant) PUT bridge_manager with %s now that %s is registered" % (BRIDGE, JACK), st == 200, (st, err))
            if submit("c1 (plant, the bridge)") is None:
                raise SystemExit(2)
            restarts += 1
        row = bridge_row(BRIDGE)
        check("c1 after the restart %s is up on /api/bridges" % BRIDGE, row and row.get("up") is True and "stats" in row, row)
        srow = socket_row(JACK)
        check("c1 %s is up on /api/sockets and carried by %s" % (JACK, BRIDGE), srow and srow.get("up") is True and srow.get("bridge") == BRIDGE, srow)
        el = e_lines(ring_since_restart())
        check("c1 no E line since the restart", el == [], el[:5])
        check("c1 no new fault", faults() == f0, faults())

        # ---- c2 the trap: the server taken away under the bridge ----
        st, err = put("socket_manager", {"servers": servers0})
        check("c2 PUT socket_manager with the slot back as found (the validator cannot see the bridges)", st == 200, (st, err))
        if submit("c2") is None:
            raise SystemExit(2)
        restarts += 1
        row = bridge_row(BRIDGE)
        check("c2 %s is not up on /api/bridges" % BRIDGE, row and row.get("up") is False, row)
        check("c2 /api/bridges says why: reason naming %s" % JACK, row and JACK in str(row.get("reason", "")), row and row.get("reason"))
        lines = ring_since_restart()
        bm_lines = [ln for ln in (lines or []) if "bridge_manager: %s" % BRIDGE in ln]
        w = [ln for ln in bm_lines if "W (" in ln]
        e = [ln for ln in bm_lines if "E (" in ln]
        check("c2 the boot logs ONE W line for %s naming %s" % (BRIDGE, JACK), len(w) == 1 and JACK in w[0], bm_lines)
        check("c2 no E line from bridge_manager at that boot (no boot_errors fault to latch)", e == [], e)
        el = e_lines(lines)
        check("c2 no E line at all since the restart", el == [], el[:5])
        f2 = faults()
        check("c2 no boot_errors fault latched", f2 == f0, f2)
        st, err = put("bridge_manager", {"bridges": with_bridge(bridges0, enabled=False)})
        check("c2 a PUT of the bridge list that still carries %s (switched off) is accepted (the reporter's trap)" % BRIDGE, st == 200, (st, err))
        st, err = put("bridge_manager", {"bridges": with_bridge(bridges0, enabled=True)})
        check("c2 the stranded bridge put back on for the page", st == 200, (st, err))

        # ---- c3 the page ----
        if UI:
            mjs = os.path.join(HERE, "connections_ui_check.mjs")
            cmd = ["node", mjs, DUT, "--port", str(PORT), "--bridge", BRIDGE]
            if OUT:
                cmd += ["--out", OUT]
            print("%s c3: %s" % (now(), " ".join(cmd)))
            p = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
            for ln in (p.stdout + p.stderr).splitlines():
                print("   ui | " + ln)
            ok = "CONNECTIONS UI PASS" in p.stdout
            m = [ln for ln in p.stdout.splitlines() if "restarts" in ln and ln.startswith("restarts=")]
            if m:
                try:
                    restarts += int(m[-1].split("=")[1])
                except ValueError:
                    pass
            check("c3 the page in Chrome: CONNECTIONS UI PASS", ok, p.stdout.strip().splitlines()[-1:] if p.stdout.strip() else p.stderr[-300:])
        else:
            print("(no --ui: the page leg is not exercised)")
    finally:
        # ---- c4 clean-up ----
        print("== c4 clean-up ==")
        bm = settings("bridge_manager") or {"bridges": []}
        sm = settings("socket_manager") or {"servers": []}
        changed = False
        if bm["bridges"] != bridges0:
            st, err = put("bridge_manager", {"bridges": bridges0})
            check("c4 the bridge list as found", st == 200, (st, err))
            changed = changed or st == 200
        if sm["servers"] != servers0:
            st, err = put("socket_manager", {"servers": servers0})
            check("c4 the servers as found", st == 200, (st, err))
            changed = changed or st == 200
        if changed:
            if submit("c4") is not None:
                restarts += 1
                el = e_lines(ring_since_restart())
                check("c4 the boot after the clean-up has no E line", el == [], el[:5])
        check("c4 the bridge list is as found", (settings("bridge_manager") or {}).get("bridges") == bridges0)
        check("c4 the servers are as found", (settings("socket_manager") or {}).get("servers") == servers0)
        f4 = faults()
        if f4 != f0:
            st, r = http("/api/faults/clear", "POST")
            print("%s c4: the run latched %s: cleared (%s %s)" % (now(), [f.get("code") for f in (f4 or [])], st, r))
            check("c4 the fault the run latched is cleared", (faults() or []) == [] or faults() == f0, faults())

    fails = [n for n, ok in CHECKS if not ok]
    for f in fails:
        print("FAIL:", f)
    print("restarts=%d" % restarts)
    print("CONNECTIONS %s (%d checks, %d failed, %d planned restarts%s)" % (
        "PASS" if not fails else "FAIL", len(CHECKS), len(fails), restarts,
        "; the firmware under test refuses a bridge to a jack not yet registered" if old_firmware else ""))
    sys.exit(1 if fails else 0)


def Number(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return -1


if __name__ == "__main__":
    main()
