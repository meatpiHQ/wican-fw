import atexit, json, sys, time, threading, urllib.request, websocket

DUT = sys.argv[1] if len(sys.argv) > 1 else "10.42.0.62"
URL = f"ws://{DUT}/ws/obd"


def api(path, method="GET", body=None, timeout=5):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(f"http://{DUT}{path}", data=data,
                                 method=method)
    if data:
        req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode() or "{}")


# OBD exclusivity: autopid polling co-masters the chip and its traffic
# fans out to /ws/obd clients too, breaking this test's request/response
# pairing. Pause every autopid group for the run (runtime-only toggle -
# a reboot restores the configured state).
paused_groups = []
try:
    for g in api("/api/autopid/config").get("groups", []):
        if g.get("enabled"):
            api("/api/autopid/group", "POST",
                {"name": g["name"], "enabled": False})
            paused_groups.append(g["name"])
    if paused_groups:
        print(f"autopid groups paused for the run: {paused_groups}")
        time.sleep(1.5)  # let any in-flight poll finish
except Exception as e:
    print(f"(autopid pause skipped: {e})")


def restore_groups():
    for name in paused_groups:
        try:
            api("/api/autopid/group", "POST", {"name": name, "enabled": True})
        except Exception:
            pass
    if paused_groups:
        print(f"autopid groups restored: {paused_groups}")


atexit.register(restore_groups)

def rx_all(ws, deadline):
    buf = b""
    while time.time() < deadline:
        try:
            ws.settimeout(deadline - time.time())
            op, data = ws.recv_data()
            buf += data
            if b">" in buf:
                break
        except Exception:
            break
    return buf

# --- test 1: single client, OBD commands over WS ---
ws1 = websocket.WebSocket()
ws1.connect(URL, timeout=5)
results = {}
for cmd in ("ATI\r", "VTVERS\r", "0100\r"):
    ws1.send_binary(cmd.encode())
    resp = rx_all(ws1, time.time() + 3)
    results[cmd.strip()] = resp
    print(f"CMD {cmd.strip()!r} -> {resp!r}")

ok1 = (b"ELM327" in results["ATI"] or b"WiCAN" in results["ATI"]) and results["ATI"] != b""
ok2 = results["VTVERS"] != b""
ok3 = b"41 00" in results["0100"] or b"4100" in results["0100"].replace(b" ", b"")
print(f"single-client: ATI={'OK' if ok1 else 'FAIL'} VTVERS={'OK' if ok2 else 'FAIL'} 0100={'OK' if ok3 else 'FAIL'}")

# --- test 2: fan-out — second client connects, one sends, both receive ---
ws2 = websocket.WebSocket()
ws2.connect(URL, timeout=5)
time.sleep(0.3)
got2 = {}
def listener(name, ws):
    got2[name] = rx_all(ws, time.time() + 3)
t = threading.Thread(target=listener, args=("ws2", ws2))
t.start()
ws1.send_binary(b"ATI\r")
got2["ws1"] = rx_all(ws1, time.time() + 3)
t.join()
print(f"fanout ws1={got2['ws1']!r}")
print(f"fanout ws2={got2['ws2']!r}")
fan_ok = got2["ws1"] != b"" and got2["ws2"] != b"" and got2["ws1"] == got2["ws2"]
print(f"fan-out: {'OK' if fan_ok else 'FAIL'}")

# --- test 3: third client must be refused (max_clients=2) ---
ws3 = websocket.WebSocket()
refused = False
try:
    ws3.connect(URL, timeout=5)
    ws3.close()
except Exception as e:
    refused = True
    print(f"third client refused: {type(e).__name__}: {e}")
print(f"max_clients gate: {'OK' if refused else 'FAIL (third client accepted)'}")

ws1.close(); ws2.close()
overall = ok1 and ok2 and ok3 and fan_ok and refused
print("WS LIVE " + ("PASS" if overall else "FAIL"))
sys.exit(0 if overall else 1)
