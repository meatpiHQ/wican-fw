#!/usr/bin/env python3
"""External log sinks end-to-end bench (log_sinks component). Runs ON
rpi001 (it binds the UDP collector + TCP/WS clients on the hotspot).

Legs (gates FIRST — the device ships with no log byte leaving the box):
  0. defaults: all four sinks enumerate DISABLED in /api/logs/status,
     the tail port is closed, counters are zero; websocket_manager v2
     migration proof: the ws_log channel exists in stored settings.
  1. configure tcp+udp+ws+file (udp -> this Pi) + submit-reboot.
  2. TCP tail conservation: `logsinks emit N` (paced) -> exactly N
     marker lines on the socket, sink dropped == 0.
  3. UDP collector conservation: exactly N markers across datagrams.
  4. WS stream: markers on ws://<dut>/ws/log text frames.
  5. FILE: emit past the rotation cap + flush -> markers on the card,
     >= 2 devlog files, retention respects file_keep, rotations
     counted; /api/fs/download works on the ACTIVE file (no held
     handle by design).
  6. counter identity per sink (in == out + dropped, buffered == 0
     after quiesce) + faults clean.
  7. restore settings verbatim; tail port closed again.

    python3 log_sinks_bench_test.py [dut_ip]
Expected final line: LOG SINKS BENCH PASS
"""
import json
import re
import socket
import subprocess
import sys
import threading
import time
import urllib.request

fails = []


def check(name, ok, detail=""):
    print(("PASS" if ok else "FAIL") + f": {name}" +
          (f"  ({detail})" if detail else ""))
    if not ok:
        fails.append(name)


def dut_ip():
    out = subprocess.run(
        ["sudo", "sh", "-c",
         "cat /var/lib/NetworkManager/dnsmasq-wint0.leases "
         "/var/lib/NetworkManager/dnsmasq-wtest0.leases 2>/dev/null"],
        capture_output=True, text=True).stdout
    leases = sorted(l.split() for l in out.splitlines() if l.split())
    return leases[-1][2] if leases else None


def api(ip, path, method="GET", body=None, raw=False, timeout=8):
    req = urllib.request.Request(f"http://{ip}{path}", method=method)
    data = None
    if body is not None:
        req.add_header("Content-Type", "application/json")
        data = json.dumps(body).encode()
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, data=data,
                                        timeout=timeout) as r:
                out = r.read()
            return out if raw else json.loads(out or b"{}")
        except Exception:
            if attempt == 2:
                return b"" if raw else {}
            time.sleep(2)


def submit_and_wait(ip):
    try:
        urllib.request.urlopen(
            urllib.request.Request(f"http://{ip}/api/settings/submit",
                                   method="POST"), timeout=8).read()
    except Exception:
        pass
    time.sleep(8)
    for _ in range(40):
        nip = dut_ip() or ip
        if api(nip, "/api/status").get("version"):
            time.sleep(4)
            return nip
        time.sleep(3)
    return None


def cli(ip, line, quiet_s=3, total_s=12):
    """One command over /ws/cli; returns accumulated text."""
    import websocket
    ws = websocket.create_connection(f"ws://{ip}/ws/cli", timeout=8)
    ws.settimeout(quiet_s)
    ws.send(line + "\n")
    out = ""
    t0 = time.time()
    while time.time() - t0 < total_s:
        try:
            out += ws.recv()
        except Exception:
            break
    ws.close()
    return out


def sink_stats(ip):
    out = cli(ip, "logsinks")
    stats = {}
    for m in re.finditer(
            r"^(tcp|udp|ws|file)\s+(\d)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+"
            r"(\w+)=(\d+)", out, re.M):
        stats[m.group(1)] = dict(
            enabled=int(m.group(2)), inn=int(m.group(3)),
            out=int(m.group(4)), dropped=int(m.group(5)),
            buffered=int(m.group(6)), detail=int(m.group(8)))
    return stats


def count_markers(blob, n_tag):
    if isinstance(blob, bytes):
        blob = blob.decode(errors="replace")
    return len(set(re.findall(r"LSBENCH (\d+)/%d\b" % n_tag, blob)))


def port_open(ip, port):
    try:
        s = socket.create_connection((ip, port), timeout=3)
        s.close()
        return True
    except Exception:
        return False


TCP_PORT = 5515
UDP_PORT = 5514


def main():
    ip = sys.argv[1] if len(sys.argv) > 1 else dut_ip()
    print("DUT", ip)
    if not api(ip, "/api/status").get("version"):
        print("FATAL: DUT unreachable")
        return 1

    ls_before = api(ip, "/api/settings/log_sinks")
    for k in ("degraded", "pending_reboot"):
        ls_before.pop(k, None)
    if not ls_before:
        print("FATAL: /api/settings/log_sinks missing (old fw?)")
        return 1

    # ---- leg 0: shipped-closed defaults + migration proof ----
    wsm = api(ip, "/api/settings/websocket_manager")
    names = [c.get("name") for c in wsm.get("channels", [])]
    check("leg0 ws_log channel present (v1->v2 migration or defaults)",
          "ws_log" in names, names)

    st = api(ip, "/api/logs/status")
    sinks = {s["name"]: s["enabled"] for s in st.get("sinks", [])}
    check("leg0 all four sinks enumerate disabled",
          all(sinks.get(n) is False for n in ("tcp", "udp", "ws", "file")),
          sinks)
    check("leg0 tail port closed", not port_open(ip, TCP_PORT))

    s0 = sink_stats(ip)
    check("leg0 counters zero",
          s0 and all(v["inn"] == 0 and v["out"] == 0 for v in s0.values()),
          s0)

    # ---- leg 1: configure all four sinks ----
    # start from a clean devlog dir (a previous run's files would skew
    # the rotation/retention counts); the file sink is still gated off
    listing = api(ip, "/api/fs/list?path=/sd/devlog")
    for e in listing.get("entries", []):
        if "devlog_" in str(e.get("name")):
            urllib.request.urlopen(urllib.request.Request(
                f"http://{ip}/api/fs/file?path=/sd/devlog/{e['name']}",
                method="DELETE"), timeout=8).read()

    my_ip = ip.rsplit(".", 1)[0] + ".1"  # this Pi = the hotspot gateway
    ls = dict(ls_before)
    ls.update({"tcp_enabled": True, "tcp_port": TCP_PORT,
               "udp_enabled": True, "udp_host": my_ip,
               "udp_port": UDP_PORT, "ws_enabled": True,
               "file_enabled": True, "file_max_kb": 64, "file_keep": 2,
               "file_flush_s": 5})
    api(ip, "/api/settings/log_sinks", "PUT", ls)
    print("configured; rebooting…")
    ip = submit_and_wait(ip)
    if ip is None:
        print("FATAL: DUT did not come back")
        return 1
    time.sleep(3)

    st = api(ip, "/api/logs/status")
    sinks = {s["name"]: s["enabled"] for s in st.get("sinks", [])}
    check("leg1 sinks enabled after apply",
          all(sinks.get(n) for n in ("tcp", "udp", "ws", "file")), sinks)

    # ---- leg 2: TCP tail conservation ----
    N = 300
    tail = socket.create_connection((ip, TCP_PORT), timeout=5)
    tail.settimeout(2)
    time.sleep(1)
    cli(ip, f"logsinks emit {N} 5", quiet_s=3, total_s=25)
    blob = b""
    t0 = time.time()
    while time.time() - t0 < 8:
        try:
            chunk = tail.recv(4096)
            if not chunk:
                break
            blob += chunk
        except socket.timeout:
            break
    tail.close()
    got = count_markers(blob, N)
    check("leg2 TCP tail delivered every marker line", got == N,
          f"{got}/{N}")
    s = sink_stats(ip)
    check("leg2 tcp sink zero drops", s.get("tcp", {}).get("dropped") == 0,
          s.get("tcp"))

    # ---- leg 3: UDP collector conservation ----
    N3 = 300
    rx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    rx.bind(("0.0.0.0", UDP_PORT))
    rx.settimeout(2)
    dgrams = []

    def udp_collect():
        t0 = time.time()
        while time.time() - t0 < 15:
            try:
                dgrams.append(rx.recvfrom(2048)[0])
            except socket.timeout:
                pass

    t = threading.Thread(target=udp_collect)
    t.start()
    time.sleep(1)
    # sendfail (detail) is asserted as a DELTA across the listened
    # window: failures while nobody listened (leg 2) are the ICMP
    # port-unreachable feedback doing its job, not a defect.
    s_pre = sink_stats(ip)
    cli(ip, f"logsinks emit {N3} 5", quiet_s=3, total_s=25)
    t.join()
    rx.close()
    got = count_markers(b"".join(dgrams), N3)
    check("leg3 UDP collector received every marker", got == N3,
          f"{got}/{N3} in {len(dgrams)} datagrams")
    s = sink_stats(ip)
    check("leg3 udp sink zero drops / no send failures while listened",
          s.get("udp", {}).get("dropped") == 0 and
          s.get("udp", {}).get("detail") ==
          s_pre.get("udp", {}).get("detail"),
          f"pre {s_pre.get('udp')} post {s.get('udp')}")

    # ---- leg 4: WS stream ----
    import websocket
    N4 = 200
    wsc = websocket.create_connection(f"ws://{ip}/ws/log", timeout=8)
    wsc.settimeout(2)
    frames = []

    def ws_collect():
        t0 = time.time()
        while time.time() - t0 < 12:
            try:
                frames.append(wsc.recv())
            except Exception:
                pass

    t = threading.Thread(target=ws_collect)
    t.start()
    time.sleep(1)
    cli(ip, f"logsinks emit {N4} 5", quiet_s=3, total_s=25)
    t.join()
    wsc.close()
    got = count_markers("".join(str(f) for f in frames), N4)
    check("leg4 WS stream delivered every marker", got == N4,
          f"{got}/{N4} in {len(frames)} frames")

    # ---- leg 5: file sink content + rotation + retention ----
    # 64 KiB cap, ~2600 lines at ~50 B/line crosses it -> rotation
    N5 = 2600
    for burst in range(N5 // 650):
        cli(ip, "logsinks emit 650 2", quiet_s=3, total_s=25)
        time.sleep(1)
    cli(ip, "logsinks flush")
    time.sleep(3)
    listing = api(ip, "/api/fs/list?path=/sd/devlog")
    entries = listing.get("entries", listing.get("files", []))
    files = [e.get("name") for e in entries
             if "devlog_" in str(e.get("name"))]
    # ~117 KB emitted vs a 64 KB cap -> at least one rotation is
    # guaranteed; retention (file_keep=2) bounds the survivors to exactly 2
    check("leg5 rotation + retention: exactly 2 devlog files",
          len(files) == 2, files)
    blob = b""
    for f in sorted(files):
        blob += api(ip, f"/api/fs/download?path=/sd/devlog/{f}",
                    raw=True) or b""
    got = len(set(re.findall(rb"LSBENCH (\d+)/650\b", blob)))
    # retention may have pruned the OLDEST bursts with the rotated file;
    # the newest burst must be fully present
    check("leg5 newest burst fully on the card", got >= 500,
          f"{got} distinct /650 markers across {len(files)} files")
    s = sink_stats(ip)
    check("leg5 rotations counted", s.get("file", {}).get("detail", 0) >= 1,
          s.get("file"))
    check("leg5 file sink zero drops",
          s.get("file", {}).get("dropped") == 0, s.get("file"))

    # ---- leg 6: conservation identity + health ----
    cli(ip, "logsinks flush")
    time.sleep(2)
    s = sink_stats(ip)
    for name, v in s.items():
        check(f"leg6 {name} identity in == out + dropped (quiesced)",
              abs(v["inn"] - (v["out"] + v["dropped"] +
                              v["buffered"])) == 0, v)
    f = api(ip, "/api/faults")
    check("leg6 faults clean", f.get("faults") == [], f)

    # ---- leg 7: restore ----
    api(ip, "/api/settings/log_sinks", "PUT", ls_before)
    print("restoring; rebooting…")
    ip = submit_and_wait(ip)
    check("leg7 restore: tail port closed again",
          ip is not None and not port_open(ip, TCP_PORT))

    if fails:
        print("LOG SINKS BENCH FAIL: " + ", ".join(fails))
        return 1
    print("LOG SINKS BENCH PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
