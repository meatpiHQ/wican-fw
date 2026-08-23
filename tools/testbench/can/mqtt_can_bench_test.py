#!/usr/bin/env python3
"""CAN ⇄ MQTT bridge end-to-end bench (TASK_mqtt_can.md §7).

Topology: PCAN on the bus (the "car"), mosquitto on rpi001 (the
broker), DUT on the bench hotspot. Runs on the PC (IDF venv python:
python-can; Pi legs over ssh).

Legs (gates FIRST — nothing may move before explicit consent):
  0. mqtt_can enabled, BOTH gates false, bridge up: PCAN storm →
     broker sees ZERO rx messages; tx publish → ZERO bus frames;
     both refusals COUNTED (mqtt_can CLI).
  1. allow_rx: storm N frames → mosquitto_sub collects legacy-JSON
     batches → conservation: frames received + counted drops
     accounts for the storm; legacy shape asserted on the wire.
  2. allow_tx + NON-DEFAULT topics (configurability proof): publish
     the two documented legacy tx examples → PCAN sees the frames
     byte-exact; rx keeps flowing on the custom topic.
  3. restore settings verbatim.

    python mqtt_can_bench_test.py [pcan]
Expected final line: MQTT CAN BENCH PASS
"""
import json
import re
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
    return pi("sudo cat /var/lib/NetworkManager/dnsmasq-wint0.leases "
              "/var/lib/NetworkManager/dnsmasq-wtest0.leases 2>/dev/null"
              " | sort -n | tail -1 | awk '{print $3}'").strip()


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
    for _ in range(40):
        nip = dut_ip()
        if nip and api(nip, "/api/status").get("version"):
            time.sleep(4)
            return nip
        time.sleep(3)
    return None


def mc_stats(ip):
    driver = (
        "import websocket,time\n"
        "ws=websocket.create_connection('ws://%s/ws/cli',timeout=5)\n"
        "ws.settimeout(2)\n"
        "ws.send('mqtt_can\\n')\n"
        "out=''\n"
        "t0=time.time()\n"
        "while time.time()-t0<3:\n"
        "    try: out+=ws.recv()\n"
        "    except Exception: break\n"
        "print(out)\n") % ip
    out = pi("python3 -", stdin=driver)
    stats = {}
    m = re.search(r"frames rx (\d+) \(batches (\d+)\)\s+tx (\d+)", out)
    if m:
        stats.update(rx=int(m.group(1)), batches=int(m.group(2)),
                     tx=int(m.group(3)))
    m = re.search(r"rx-gate (\d+)\s+tx-gate (\d+)\s+q-full (\d+)\s+"
                  r"parse-err (\d+)", out)
    if m:
        stats.update(rx_gate=int(m.group(1)), tx_gate=int(m.group(2)),
                     q_full=int(m.group(3)), parse_err=int(m.group(4)))
    return stats


def storm(n_frames, rate_hz, id_=0x123):
    bus = can.Bus(interface="pcan", channel=PCAN, bitrate=500000)
    gap = 1.0 / rate_hz
    sent = 0
    try:
        for i in range(n_frames):
            msg = can.Message(arbitration_id=id_, is_extended_id=False,
                              data=[(i >> 8) & 255, i & 255, 3, 4, 5, 6,
                                    7, 8])
            try:
                bus.send(msg, timeout=0.05)
                sent += 1
            except can.CanError:
                pass
            time.sleep(gap)
    finally:
        bus.shutdown()
    return sent


def collect(topic, secs):
    out = pi(f"timeout {secs} mosquitto_sub -h localhost -t '{topic}' "
             f"-v 2>/dev/null || true", timeout=secs + 20)
    msgs = []
    for line in out.splitlines():
        i = line.find("{")
        if i < 0:
            continue
        try:
            msgs.append(json.loads(line[i:]))
        except ValueError:
            pass
    return msgs


def main():
    ip = dut_ip()
    print("DUT", ip)
    dev = api(ip, "/api/status")
    if not dev.get("version"):
        print("FATAL: DUT unreachable")
        return 1

    mc_before = api(ip, "/api/settings/mqtt_can")
    bm_before = api(ip, "/api/settings/bridge_manager")
    mq_before = api(ip, "/api/settings/mqtt_manager")
    for c in (mc_before, bm_before, mq_before):
        c.pop("degraded", None)
        c.pop("pending_reboot", None)
        c.pop("broker_password", None)  # GET redacts it — never PUT back

    # explicit prefix: deterministic topics without needing device_id;
    # "~" expansion (the legacy wican/<id> default) is the same code path
    prefix = "benchmqttcan"

    # ---- configure: mqtt on (Pi broker), mqtt_can on, gates CLOSED ----
    mq = dict(mq_before)
    gw = ip.rsplit(".", 1)[0] + ".1"
    mq.update({"enabled": True, "url": f"mqtt://{gw}:1883",
               "topic_prefix": prefix})
    api(ip, "/api/settings/mqtt_manager", "PUT", mq)

    mc = dict(mc_before)
    mc.update({"enabled": True, "allow_rx": False, "allow_tx": False,
               "pub_topic": "~/can/rx", "sub_topic": "~/can/tx",
               "batch_ms": 50, "batch_frames": 24})
    api(ip, "/api/settings/mqtt_can", "PUT", mc)

    bm = dict(bm_before)
    bridges = [b for b in bm.get("bridges", [])
               if b.get("name") != "br_mqtt_can"]
    # the can jack is single-consumer: park any bridge that owns it
    parked = [b["name"] for b in bridges
              if "can" in (b.get("a"), b.get("b")) and b.get("enabled")]
    for b in bridges:
        if b["name"] in parked:
            b["enabled"] = False
    bridges.append({"name": "br_mqtt_can", "a": "can", "b": "mqtt0",
                    "translator": "canmqtt", "enabled": True})
    bm["bridges"] = bridges
    api(ip, "/api/settings/bridge_manager", "PUT", bm)
    if parked:
        print(f"  (parked can-jack bridges for the run: {parked})")
    print("configured (gates CLOSED); rebooting…")
    ip = submit_and_wait(ip)
    if ip is None:
        print("FATAL: DUT did not come back")
        return 1

    rx_topic = f"{prefix}/can/rx"
    tx_topic = f"{prefix}/can/tx"
    print("topics:", rx_topic, "/", tx_topic)
    time.sleep(5)  # broker connect

    # ---- leg 0: both gates closed ----
    s0 = mc_stats(ip)
    got = {"msgs": None}

    def bg_collect():
        got["msgs"] = collect(rx_topic, 8)

    t = threading.Thread(target=bg_collect)
    t.start()
    time.sleep(1)
    sent = storm(300, 200)
    pi(f"mosquitto_pub -h localhost -t '{tx_topic}' -m "
       "'{\"bus\":0,\"type\":\"tx\",\"frame\":[{\"id\":257,\"dlc\":2,"
       "\"data\":[1,2]}]}'")
    t.join()
    s1 = mc_stats(ip)
    check("leg0 NOTHING published while rx gated",
          got["msgs"] == [], f"{len(got['msgs'] or [])} msgs")
    check("leg0 rx-gate drops counted",
          s1.get("rx_gate", 0) > s0.get("rx_gate", 0),
          f"{s0.get('rx_gate')}->{s1.get('rx_gate')}")
    check("leg0 tx-gate drop counted",
          s1.get("tx_gate", 0) > s0.get("tx_gate", 0),
          f"{s0.get('tx_gate')}->{s1.get('tx_gate')}")
    check("leg0 nothing reached the bus side", s1.get("tx", 0) == 0,
          s1.get("tx"))

    # ---- leg 1: allow_rx — conservation at rate ----
    mc.update({"allow_rx": True})
    api(ip, "/api/settings/mqtt_can", "PUT", mc)
    print("opening the rx gate; rebooting…")
    ip = submit_and_wait(ip)
    time.sleep(5)

    s0 = mc_stats(ip)
    N = 1000
    t = threading.Thread(target=bg_collect)
    t.start()
    time.sleep(1.5)
    sent = storm(N, 400)
    time.sleep(2)
    t.join()
    s1 = mc_stats(ip)
    msgs = got["msgs"] or []
    frames = sum(len(m.get("frame", [])) for m in msgs)
    counted = (s1.get("rx", 0) - s0.get("rx", 0))
    check("leg1 legacy shape on the wire",
          bool(msgs) and msgs[0].get("type") == "rx"
          and msgs[0].get("bus") == "0" and "ts" in msgs[0]
          and {"id", "dlc", "rtr", "extd", "data"}
              <= set(msgs[0]["frame"][0].keys()),
          json.dumps(msgs[0])[:120] if msgs else "no msgs")
    check("leg1 batching active (avg > 1 frame/publish)",
          len(msgs) > 0 and frames / max(len(msgs), 1) > 1.0,
          f"{frames} frames in {len(msgs)} publishes")
    # conservation: what the device SAYS it batched matches the storm,
    # and what the broker got matches minus counted mqtt drops
    check("leg1 device batched the storm",
          abs(counted - sent) <= 0.02 * sent,
          f"storm {sent}, device rx {counted}")
    check("leg1 broker received the batches (>= 95%)",
          frames >= 0.95 * sent, f"broker {frames} / storm {sent}")

    # ---- leg 2: allow_tx + NON-DEFAULT topics ----
    mc.update({"allow_tx": True,
               "pub_topic": "bench/custom/up",
               "sub_topic": "bench/custom/down"})
    api(ip, "/api/settings/mqtt_can", "PUT", mc)
    print("opening the tx gate + custom topics; rebooting…")
    ip = submit_and_wait(ip)
    time.sleep(5)

    # tx round-trip: publish the documented legacy examples, catch on PCAN
    rxbus = can.Bus(interface="pcan", channel=PCAN, bitrate=500000)
    try:
        pi("mosquitto_pub -h localhost -t 'bench/custom/down' -m "
           "'{\"bus\":0,\"type\":\"tx\",\"frame\":[{\"id\":123,"
           "\"dlc\":8,\"rtr\":false,\"extd\":true,"
           "\"data\":[1,2,3,4,5,6,7,8]},{\"id\":124,\"dlc\":8,"
           "\"rtr\":false,\"extd\":true,"
           "\"data\":[1,2,3,4,5,6,7,8]}]}'")
        pi("mosquitto_pub -h localhost -t 'bench/custom/down' -m "
           "'{\"bus\":0,\"type\":\"tx\",\"ts\":35519,\"frame\":"
           "[{\"id\":2016,\"dlc\":8,\"rtr\":false,\"extd\":false,"
           "\"data\":[2,1,47,170,170,170,170,170]}]}'")
        seen = {}
        end = time.time() + 8
        while time.time() < end and len(seen) < 3:
            m = rxbus.recv(timeout=1)
            if m is not None and m.arbitration_id in (123, 124, 2016):
                seen[m.arbitration_id] = (m.is_extended_id,
                                          bytes(m.data))
    finally:
        rxbus.shutdown()

    check("leg2 tx frames on the bus (custom sub topic)",
          set(seen) == {123, 124, 2016}, sorted(seen))
    if 123 in seen:
        check("leg2 tx byte-exact",
              seen[123] == (True, bytes([1, 2, 3, 4, 5, 6, 7, 8]))
              and seen[2016][0] is False
              and seen[2016][1][2] == 47, seen)

    # rx still flows, now on the custom pub topic. The broker-side
    # STRICT conservation was leg1's job — here the device stats carry
    # the exact count (an ssh-started mosquitto_sub can attach late and
    # miss early batches), the broker check is liveness-on-this-topic.
    s_rx0 = mc_stats(ip)
    got["msgs"] = None
    t = threading.Thread(
        target=lambda: got.update(msgs=collect("bench/custom/up", 10)))
    t.start()
    time.sleep(3)               # let the subscriber attach
    storm(100, 100)
    time.sleep(1)
    s_rx1 = mc_stats(ip)
    t.join()
    check("leg2 device captured the storm on the custom topic",
          s_rx1.get("rx", 0) - s_rx0.get("rx", 0) >= 95,
          f"device rx +{s_rx1.get('rx', 0) - s_rx0.get('rx', 0)}")
    check("leg2 rx flowing on the custom pub topic",
          got["msgs"] and sum(len(m.get("frame", []))
                              for m in got["msgs"]) >= 50,
          f"{len(got['msgs'] or [])} msgs, "
          f"{sum(len(m.get('frame', [])) for m in (got['msgs'] or []))} "
          "frames")

    s2 = mc_stats(ip)
    check("leg2 no parse errors / q-full drops",
          s2.get("parse_err", 0) == 0 and s2.get("q_full", 0) == 0, s2)

    # ---- leg 3: restore ----
    api(ip, "/api/settings/mqtt_can", "PUT", mc_before)
    api(ip, "/api/settings/bridge_manager", "PUT", bm_before)
    api(ip, "/api/settings/mqtt_manager", "PUT", mq_before)
    print("restoring; rebooting…")
    ip = submit_and_wait(ip)
    f = api(ip, "/api/faults")
    check("restore + faults clean", f.get("faults") == [], f)

    if fails:
        print("MQTT CAN BENCH FAIL: " + ", ".join(fails))
        return 1
    print("MQTT CAN BENCH PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
