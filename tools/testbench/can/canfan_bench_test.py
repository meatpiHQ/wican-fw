#!/usr/bin/env python3
"""can-jack fan-out (multi_consumer, 2026-07-26): slcan + MQTT
SIMULTANEOUSLY on the one `can` jack — the mqtt_can v2 backlog item.
Before this, the jack was single-consumer and the two features were
mutually exclusive.

Topology: PCAN on the bus (the generator), mosquitto on rpi001, DUT on
the bench hotspot. Runs on the PC (IDF venv python: python-can; Pi legs
over ssh) — the mqtt_can_bench_test.py pattern.

Leg: configure BOTH bridges on the can jack —
       br_slcan:    can <-> slcan0 (tcp:3333, slcan codec)
       br_mqtt_can: can <-> mqtt0  (canmqtt codec, allow_rx)
     storm N frames -> BOTH consumers must see (>=95% each, same storm):
       - TCP slcan client: t-prefixed lines
       - mosquitto_sub: legacy-JSON batches
     conservation cross-check via the device counters; restore.

    python canfan_bench_test.py [pcan]
Expected final line: CANFAN BENCH PASS
"""
import json
import re
import socket
import subprocess
import sys
import threading
import time

import can

PCAN = sys.argv[1] if len(sys.argv) > 1 else "PCAN_USBBUS2"

fails = []


def check(name, ok, detail=""):
    print(("PASS" if ok else "FAIL") + f": {name}" +
          (f"  ({detail})" if detail else ""))
    if not ok:
        fails.append(name)


def pi(cmd, stdin=None, timeout=180):
    p = subprocess.run(["ssh", "-o", "BatchMode=yes", "rpi001", cmd],
                       input=stdin, capture_output=True, text=True,
                       encoding="utf-8", timeout=timeout)
    return p.stdout


def dut_ip():
    out = pi("sudo sh -c 'cat /var/lib/NetworkManager/dnsmasq-wint0.leases "
             "/var/lib/NetworkManager/dnsmasq-wtest0.leases 2>/dev/null'")
    leases = sorted(l.split() for l in out.splitlines() if l.split())
    return leases[-1][2] if leases else None


def api(ip, path, method="GET", body=None):
    if body is None:
        out = pi(f"curl -s -m 8 -X {method} http://{ip}{path}")
    else:
        out = pi(f"curl -s -m 8 -X {method} -H 'Content-Type: "
                 f"application/json' -d @- http://{ip}{path}",
                 stdin=json.dumps(body))
    try:
        return json.loads(out)
    except ValueError:
        return {}


def submit_and_wait(ip):
    pi(f"curl -s -m 8 -X POST http://{ip}/api/settings/submit")
    time.sleep(8)
    for i in range(40):
        nip = dut_ip()
        if nip and api(nip, "/api/status").get("version"):
            time.sleep(4)
            return nip
        if i % 4 == 3:                    # liveness during the reboot wait
            print(f"  waiting for the DUT to come back… "
                  f"(~{8 + (i + 1) * 3} s)", flush=True)
        time.sleep(3)
    return None


def storm(n_frames, rate_hz, id_=0x123):
    bus = can.Bus(interface="pcan", channel=PCAN, bitrate=500000)
    sent = 0
    gap = 1.0 / rate_hz
    try:
        for i in range(n_frames):
            msg = can.Message(arbitration_id=id_, is_extended_id=False,
                              data=[(i >> 8) & 255, i & 255, 0, 0, 0, 0,
                                    0, 0])
            try:
                bus.send(msg, timeout=0.05)
                sent += 1
            except can.CanError:
                pass
            time.sleep(gap)
    finally:
        bus.shutdown()
    return sent


def main():
    ip = dut_ip()
    print("DUT", ip)
    if not api(ip, "/api/status").get("version"):
        print("FATAL: DUT unreachable")
        return 1

    befores = {}
    for comp in ("bridge_manager", "mqtt_can", "mqtt_manager",
                 "socket_manager"):
        befores[comp] = api(ip, f"/api/settings/{comp}")
        for k in ("degraded", "pending_reboot", "broker_password"):
            befores[comp].pop(k, None)

    gw = ip.rsplit(".", 1)[0] + ".1"
    prefix = "canfan"

    mq = dict(befores["mqtt_manager"])
    mq.update({"enabled": True, "url": f"mqtt://{gw}:1883",
               "topic_prefix": prefix})
    api(ip, "/api/settings/mqtt_manager", "PUT", mq)

    mc = dict(befores["mqtt_can"])
    mc.update({"enabled": True, "allow_rx": True, "allow_tx": False,
               "pub_topic": "~/can/rx", "sub_topic": "~/can/tx",
               "batch_ms": 50, "batch_frames": 24})
    api(ip, "/api/settings/mqtt_can", "PUT", mc)

    # make sure slcan0 (tcp:3333) is enabled
    sock = json.loads(json.dumps(befores["socket_manager"]))
    for s in sock.get("servers", []):
        if s["name"] == "slcan0":
            s["enabled"] = True
    api(ip, "/api/settings/socket_manager", "PUT", sock)

    # BOTH bridges on the can jack — the fan-out under test. Park ANY
    # standing bridge that uses one of our endpoints, not just can-jack
    # ones: a standing bridge holding slcan0 collided with
    # br_slcan and the validator rejected the whole PUT — unnoticed,
    # because the result wasn't checked (first live run 2026-07-26).
    ours = {"can", "slcan0", "mqtt0"}
    bm = json.loads(json.dumps(befores["bridge_manager"]))
    bridges = [b for b in bm.get("bridges", [])
               if b.get("name") not in ("br_slcan", "br_mqtt_can") and
               not ({b.get("a"), b.get("b")} & ours)]
    parked = [b["name"] for b in bm.get("bridges", [])
              if b not in bridges and
              b.get("name") not in ("br_slcan", "br_mqtt_can")]
    if parked:
        print(f"  (parked conflicting bridges for the run: {parked})")
    bridges += [
        {"name": "br_slcan", "a": "can", "b": "slcan0",
         "translator": "slcan", "enabled": True},
        {"name": "br_mqtt_can", "a": "can", "b": "mqtt0",
         "translator": "canmqtt", "enabled": True},
    ]
    bm["bridges"] = bridges[:6]
    r = api(ip, "/api/settings/bridge_manager", "PUT", bm)
    check("bridge config accepted", not r.get("error"), str(r))
    print("configured both can-jack consumers; rebooting…")
    ip = submit_and_wait(ip)
    if ip is None:
        print("FATAL: DUT did not come back")
        return 1
    print("PROGRESS 1/5", flush=True)

    # wait for the DUT's broker session (reconnect backoff can exceed a
    # flat sleep after the apply reboot — 2026-07-26 first-run lesson)
    print("waiting for the DUT's MQTT broker session…", flush=True)
    for _ in range(15):
        if api(ip, "/api/status").get("bits", {}).get("mqtt_connected"):
            break
        time.sleep(3)
    else:
        print("  (mqtt_connected bit never rose — storming anyway)")
    time.sleep(2)
    print("PROGRESS 2/5", flush=True)

    # ---- the simultaneous leg ----
    N = 600
    mqtt_msgs = []
    slcan_buf = [b""]
    slcan_ready = threading.Event()

    def collect_mqtt():
        out = pi(f"timeout 30 mosquitto_sub -h localhost -t "
                 f"'{prefix}/can/rx' -v 2>/dev/null || true", timeout=45)
        for line in out.splitlines():
            i = line.find("{")
            if i >= 0:
                try:
                    mqtt_msgs.append(json.loads(line[i:]))
                except ValueError:
                    pass

    def collect_slcan():
        # the PC has no route to the DUT's hotspot subnet since the bench
        # ethernet link went gateway-less (2026-07-26) — tunnel the TCP
        # leg through the control plane when the direct connect fails
        tunnel = None
        try:
            try:
                s = socket.create_connection((ip, 3333), timeout=5)
            except OSError:
                tunnel = subprocess.Popen(
                    ["ssh", "-o", "BatchMode=yes", "-N",
                     "-L", f"13333:{ip}:3333", "rpi001"])
                time.sleep(3)
                s = socket.create_connection(("127.0.0.1", 13333),
                                             timeout=8)
            _collect_slcan(s)
        finally:
            if tunnel is not None:
                tunnel.terminate()

    def _collect_slcan(s):
        s.settimeout(2)
        # slcan handshake a real client would do
        s.sendall(b"O\r")
        slcan_ready.set()   # storm only starts once this client is on
        t0 = time.time()
        while time.time() - t0 < 13:
            try:
                b = s.recv(4096)
                if not b:
                    break
                slcan_buf[0] += b
            except socket.timeout:
                pass
        s.close()

    t1 = threading.Thread(target=collect_mqtt)
    t2 = threading.Thread(target=collect_slcan)
    t1.start()
    t2.start()
    # gate the storm on the slcan client being CONNECTED — slcan only
    # delivers to attached clients, and the ssh tunnel adds seconds of
    # connect latency (71/600 tail-catch on the fixed-sleep version)
    if not slcan_ready.wait(timeout=25):
        print("  (slcan client never connected — storming anyway)")
    time.sleep(1.5)
    print(f"both consumers listening; storming {N} frames @ 100 Hz "
          f"(~{N // 100} s)…", flush=True)
    sent = storm(N, 100)
    print(f"  storm done ({sent} sent); waiting for the collector "
          "windows to close (mqtt 30 s, slcan 13 s)…", flush=True)
    t1.join()
    t2.join()
    print("PROGRESS 3/5", flush=True)

    slcan_frames = len(re.findall(rb"t123", slcan_buf[0]))
    mqtt_frames = sum(len(m.get("frame", [])) for m in mqtt_msgs)

    check("slcan consumer saw the storm", slcan_frames >= 0.95 * sent,
          f"{slcan_frames}/{sent} t-lines")
    check("mqtt consumer saw the SAME storm", mqtt_frames >= 0.95 * sent,
          f"{mqtt_frames}/{sent} in {len(mqtt_msgs)} batches")
    check("both simultaneously (the fan-out claim)",
          slcan_frames >= 0.95 * sent and mqtt_frames >= 0.95 * sent, "")

    f = api(ip, "/api/faults")
    check("no faults under fan-out", f.get("faults") == [], f)
    print("PROGRESS 4/5", flush=True)

    # ---- restore ----
    for comp in ("bridge_manager", "mqtt_can", "mqtt_manager",
                 "socket_manager"):
        api(ip, f"/api/settings/{comp}", "PUT", befores[comp])
    print("restoring; rebooting…")
    ip = submit_and_wait(ip)
    check("restore ok", ip is not None, "")
    print("PROGRESS 5/5", flush=True)

    if fails:
        print("CANFAN BENCH FAIL: " + ", ".join(fails))
        return 1
    print("CANFAN BENCH PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
