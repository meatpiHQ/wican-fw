#!/usr/bin/env python3
"""Network-trust lockdown — EXHAUSTIVE attack-surface sweep (wifi_manager
v3 `*_trusted` flags + http_server_manager request gate; meatpi
2026-07-08: shared networks must not expose configuration, and the test
must cover the WHOLE surface — one un-gated route is the entire hole).

Enumerates every HTTP route + WebSocket channel from the firmware SOURCE
(so the list can never drift from what's registered), marks the DUT's
current STA network untrusted (managed over the USB-NCM link — the path
the gate must NEVER close), then asserts, over the STA address:
  * EVERY /api route -> 403, across GET/POST/PUT/DELETE (the gate wraps
    per-registered-handler; a method that reaches its handler is a leak)
  * the UI catch-all (/, /index.html, arbitrary deep paths) -> 403
  * EVERY /ws/* channel handshake -> refused before the 101
  * NOTHING on the STA side returns a non-403 (the security invariant)
and, over USB:
  * a representative allowed set -> 200 (admin path intact)
  * mqtt_connected stays TRUE (outbound over the untrusted STA works)
Restores trusted=true and re-asserts STA 200.

  python wifi_gate_live_test.py [usb_ip] [sta_ip] [bench_host] [repo]
"""
import json
import os
import re
import subprocess
import sys
import time
import urllib.request

USB = sys.argv[1] if len(sys.argv) > 1 else "192.168.82.1"
STA = sys.argv[2] if len(sys.argv) > 2 else "10.42.0.62"
PI = sys.argv[3] if len(sys.argv) > 3 else "rpi001"
REPO = sys.argv[4] if len(sys.argv) > 4 else os.path.normpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)),
                 "..", "..", ".."))

fails = []


def check(name, ok, detail=""):
    print(("PASS" if ok else "FAIL") + ": " + name
          + ("  ({})".format(detail) if detail else ""))
    if not ok:
        fails.append(name)


def api(path, method="GET", body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request("http://" + USB + path, data=data,
                                 method=method,
                                 headers={"Content-Type":
                                          "application/json"})
    with urllib.request.urlopen(req, timeout=10) as r:
        t = r.read().decode()
        return json.loads(t) if t.strip().startswith(("{", "[")) else t


def pi(cmd, timeout=60, stdin=None):
    p = subprocess.run(["ssh", "-o", "BatchMode=yes", PI, cmd],
                       capture_output=True, text=True, timeout=timeout,
                       input=stdin)
    return (p.stdout + p.stderr).strip()


def enumerate_surface():
    """Every /api route from the component/main SOURCE (can't drift from
    what's registered), and every /ws channel path LIVE from the device
    (the enabled channel set is settings-driven, not a source literal)."""
    api_routes = set()
    roots = [os.path.join(REPO, "components"), os.path.join(REPO, "main")]
    # matches plain "/api/..." and escaped \"/api/...\" (JSON-literal
    # default-config strings)
    rx_api = re.compile(r'\\?"(/api/[A-Za-z0-9_/*-]+)\\?"')
    for root in roots:
        for dirpath, _, files in os.walk(root):
            if "build" in dirpath or "managed_components" in dirpath:
                continue
            for fn in files:
                if not fn.endswith(".c"):
                    continue
                txt = open(os.path.join(dirpath, fn), encoding="utf-8",
                           errors="replace").read()
                for m in rx_api.findall(txt):
                    api_routes.add(m.replace("/*", "/x"))  # concretize

    ws_paths = set()
    try:
        for ch in api("/api/ws").get("channels", []):
            if ch.get("enabled") and ch.get("path"):
                ws_paths.add(ch["path"])
    except Exception:
        ws_paths = {"/ws/obd", "/ws/cli", "/ws/can"}  # shipped defaults
    return sorted(api_routes), sorted(ws_paths)


def set_trusted(value):
    w = api("/api/settings/wifi_manager")
    w.pop("degraded", None)
    w.pop("pending_reboot", None)
    w["sta_trusted"] = value
    api("/api/settings/wifi_manager", "PUT", w)
    try:
        api("/api/settings/submit", "POST")
    except Exception:
        pass  # reboot can cut the response
    time.sleep(10)
    for _ in range(40):
        try:
            api("/api/status")
            break
        except Exception:
            time.sleep(1.5)
    time.sleep(8)  # STA rejoin


def sweep_http_codes(host, paths, methods):
    """Probe every (method, path) over the Pi via a remote read-loop fed
    on stdin (keeps the ssh command line short — 300+ probes overflow
    Windows CreateProcess otherwise). Emits 'CODE METHOD PATH' per line."""
    probes = [f"{m} {p}" for p in paths for m in methods]
    # tr -d '\r': Windows text-mode stdin adds CR, which would land in $p
    # and make every URL curl to '<path>\r' (000). Strip it stream-wide.
    loop = (f"tr -d '\\r' | while read m p; do "
            f"printf '%s %s %s\\n' "
            f"$(curl -s -o /dev/null -w '%{{http_code}}' -m 5 -X \"$m\" "
            f"\"http://{host}$p\") \"$m\" \"$p\"; done")
    out = pi(loop, timeout=max(90, len(probes) * 3),
             stdin="\n".join(probes) + "\n")
    codes = {}
    for line in out.splitlines():
        parts = line.split()
        if len(parts) == 3:
            codes[(parts[1], parts[2])] = parts[0]
    return codes


def ws_refused(path):
    out = pi(
        "python3 -c \"import websocket\n"
        "try:\n"
        f"    websocket.create_connection('ws://{STA}{path}', timeout=4)\n"
        "    print('CONNECTED')\n"
        "except Exception: print('REFUSED')\"")
    return "REFUSED" in out


def main():
    api_routes, ws_paths = enumerate_surface()
    print(f"surface: {len(api_routes)} API routes, {len(ws_paths)} WS "
          f"channels")
    assert len(api_routes) >= 50 and ws_paths, "enumeration too small"

    ui_paths = ["/", "/index.html", "/anything/deep/path", "/config"]

    # baseline (trusted): STA admin reachable
    base = sweep_http_codes(STA, ["/api/status"], ["GET"])
    check("baseline: Pi->STA /api/status 200 (trusted)",
          base.get(("GET", "/api/status")) == "200",
          base.get(("GET", "/api/status")))

    set_trusted(False)
    try:
        # 1) every API route x every method: the gate must return 403 for
        #    the route's REGISTERED methods; httpd itself returns 405 for
        #    an unregistered method (rejected before any handler). Both
        #    are "closed" — a LEAK is any response a handler produced
        #    (2xx, or a handler-level 4xx/5xx). Safe set = {403, 405}.
        SAFE = {"403", "405"}
        methods = ["GET", "POST", "PUT", "DELETE"]
        codes = sweep_http_codes(STA, api_routes, methods)
        leaks = [f"{m} {p}={c}" for (m, p), c in sorted(codes.items())
                 if c not in SAFE]
        check(f"untrusted: all {len(api_routes)} API routes x "
              f"{len(methods)} methods closed (403/405, no handler leak)",
              not leaks, "; ".join(leaks[:8]))
        # and every route is reachable-but-403 on at least one method
        # (proves the gate fires, not just that nothing is registered)
        gated = {p for (m, p), c in codes.items() if c == "403"}
        ungated = [p for p in api_routes if p not in gated]
        check("untrusted: every API route hit the 403 gate "
              "(no route silently 405-only)",
              not ungated, ",".join(ungated[:8]))

        # 2) UI catch-all + arbitrary paths -> 403
        ui = sweep_http_codes(STA, ui_paths, ["GET"])
        ui_leaks = [f"{p}={c}" for (_, p), c in ui.items() if c != "403"]
        check("untrusted: UI catch-all + arbitrary paths -> 403",
              not ui_leaks, "; ".join(ui_leaks))

        # 3) every WS channel handshake refused
        ws_open = [p for p in ws_paths if not ws_refused(p)]
        check(f"untrusted: all {len(ws_paths)} WS channels refused",
              not ws_open, "open: " + ",".join(ws_open))

        # 4) the invariant: NOTHING on the STA surface leaked a handler
        all_codes = list(codes.values()) + list(ui.values())
        leak_codes = sorted(set(c for c in all_codes
                                if c not in {"403", "405"}))
        check("untrusted: STA surface exposes ZERO handler responses",
              leak_codes == [], "saw: " + ",".join(leak_codes))

        # 5) USB admin path intact + outbound alive (LOCAL — the Pi
        #    can't reach the PC-local USB-NCM address)
        s = api("/api/status")
        check("untrusted: USB admin 200 + STA connected",
              s["bits"]["sta_connected"] is True)
        check("untrusted: outbound MQTT connected over untrusted STA",
              s["bits"]["mqtt_connected"] is True)
        check("untrusted: USB config route reachable (gate never closes "
              "USB) — settings GET works",
              isinstance(api("/api/settings/wifi_manager"), dict))
    finally:
        set_trusted(True)

    restored = sweep_http_codes(STA, ["/api/status"], ["GET"])
    check("restored: Pi->STA 200 again",
          restored.get(("GET", "/api/status")) == "200",
          restored.get(("GET", "/api/status")))

    if fails:
        print("WIFI GATE FAIL:", ", ".join(fails))
        sys.exit(1)

    print("WIFI GATE PASS")


if __name__ == "__main__":
    main()
