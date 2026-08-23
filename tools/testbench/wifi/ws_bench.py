"""websocket_manager benchmark load generator (BENCHMARKS.md scenarios).

Runs ON rpi001 against the composed main firmware with a CROSS-TRANSPORT
bridge configured: a WS channel <-raw-> `obd0` (WebSocket channel to the
TCP server). The Pi holds both ends, so every scenario exercises the real
production path: httpd WS frames -> chunks -> bridge pump -> lwIP TCP (and
back). One pump crossing per direction (the socket_manager echo-bridge
numbers crossed the pump TWICE — compare accordingly; the `latency`
scenario here loops WS->TCP->WS so it also crosses twice + radio twice).

    python3 ws_bench.py --host 10.42.0.62 --scenario \
        ws_to_tcp|tcp_to_ws|bidir|latency|fanout|obd_poll \
        [--secs 20] [--frame 128] [--ws-path /ws/obd]

On the CANONICAL bench config (TESTING.md) the echo scenarios use
`--ws-path /ws/can` (bridge `br_echo = ws_can <-> obd0`), leaving `ws_obd`
bridged to the real chip. `obd_poll` stays on `/ws/obd` (`br_obd = obd <->
ws_obd`) + a live OBD chip: it polls `0100` over WS and reports poll RTT
percentiles (compare with system_bench's TCP polling row).

Copy result lines (plus commit, IDF, RSSI) into
components/websocket_manager/BENCHMARKS.md.
"""
import argparse
import socket
import statistics
import sys
import threading
import time

import websocket

TCP_PORT = 35000  # obd0
WS_PATH = "/ws/obd"  # overridden by --ws-path


def _ws(host):
    w = websocket.WebSocket()
    w.connect(f"ws://{host}{WS_PATH}", timeout=5)
    return w


def _tcp(host):
    return socket.create_connection((host, TCP_PORT), timeout=5)


def _tcp_drain(s, count, done):
    s.settimeout(1)

    while not done[0]:
        try:
            b = s.recv(65536)
        except socket.timeout:
            continue
        except OSError:
            break

        if not b:
            break

        count[0] += len(b)


def _ws_drain(w, count, done):
    w.settimeout(1)

    while not done[0]:
        try:
            _, data = w.recv_data()
        except Exception:
            continue

        count[0] += len(data)


def ws_to_tcp(host, secs, frame):
    w, s = _ws(host), _tcp(host)
    payload = (bytes(range(256)) * ((frame // 256) + 1))[:frame]
    rcvd, done = [0], [False]
    t = threading.Thread(target=_tcp_drain, args=(s, rcvd, done))
    t.start()
    sent = 0
    t0 = time.time()

    while time.time() - t0 < secs:
        w.send_binary(payload)
        sent += frame

    dt = time.time() - t0
    time.sleep(1.5)
    done[0] = True
    t.join()
    loss = 100.0 * (1 - rcvd[0] / sent) if sent else 0
    print(f"ws_to_tcp frame={frame} offered={sent / dt / 1024:.1f} KB/s "
          f"delivered={rcvd[0] / dt / 1024:.1f} KB/s loss={loss:.1f}% "
          f"over {dt:.0f}s")
    w.close(); s.close()


def tcp_to_ws(host, secs, frame):
    w, s = _ws(host), _tcp(host)
    payload = (bytes(range(256)) * ((frame // 256) + 1))[:frame]
    rcvd, done = [0], [False]
    t = threading.Thread(target=_ws_drain, args=(w, rcvd, done))
    t.start()
    sent = 0
    t0 = time.time()

    while time.time() - t0 < secs:
        s.sendall(payload)
        sent += frame

    dt = time.time() - t0
    time.sleep(1.5)
    done[0] = True
    t.join()
    loss = 100.0 * (1 - rcvd[0] / sent) if sent else 0
    print(f"tcp_to_ws frame={frame} offered={sent / dt / 1024:.1f} KB/s "
          f"delivered={rcvd[0] / dt / 1024:.1f} KB/s loss={loss:.1f}% "
          f"over {dt:.0f}s")
    w.close(); s.close()


def bidir(host, secs, frame):
    w, s = _ws(host), _tcp(host)
    payload = (bytes(range(256)) * ((frame // 256) + 1))[:frame]
    tcp_rx, ws_rx, done = [0], [0], [False]
    threads = [threading.Thread(target=_tcp_drain, args=(s, tcp_rx, done)),
               threading.Thread(target=_ws_drain, args=(w, ws_rx, done))]
    sent = {"ws": 0, "tcp": 0}

    def tcp_tx():
        t0 = time.time()

        while time.time() - t0 < secs:
            try:
                s.sendall(payload)
                sent["tcp"] += frame
            except (socket.timeout, TimeoutError):
                continue  # backpressure — keep offering

    threads.append(threading.Thread(target=tcp_tx))

    for t in threads:
        t.start()

    t0 = time.time()

    while time.time() - t0 < secs:
        try:
            w.send_binary(payload)
            sent["ws"] += frame
        except Exception:
            time.sleep(0.005)  # backpressure (drain thread's 1 s timeout
                               # is shared with send) — retry, don't die

    dt = time.time() - t0
    time.sleep(1.5)
    done[0] = True

    for t in threads:
        t.join()

    print(f"bidir frame={frame} ws->tcp={tcp_rx[0] / dt / 1024:.1f} KB/s "
          f"tcp->ws={ws_rx[0] / dt / 1024:.1f} KB/s "
          f"aggregate={(tcp_rx[0] + ws_rx[0]) / dt / 1024:.1f} KB/s "
          f"over {dt:.0f}s")
    w.close(); s.close()


def latency(host, secs, frame):
    """WS sends a small frame; the Pi's TCP end echoes it straight back;
    WS waits for the return. Crosses the pump twice + the radio twice —
    directly comparable to socket_manager's echo-bridge RTT rows."""
    w, s = _ws(host), _tcp(host)
    s.settimeout(2)
    w.settimeout(2)
    rtts = []
    t0 = time.time()

    while time.time() - t0 < secs:
        t = time.perf_counter()
        w.send_binary(b"ping-rtt")

        try:
            got = b""

            while len(got) < 8:
                got += s.recv(64)

            s.sendall(got)
            reply = b""

            while len(reply) < 8:
                _, d = w.recv_data()
                reply += d
        except Exception:
            continue

        rtts.append((time.perf_counter() - t) * 1000)
        time.sleep(0.02)

    if len(rtts) < 2:
        print(f"latency FAILED: no echoes came back "
              f"({WS_PATH} channel bridged to obd0?)")
        sys.exit(1)

    rtts.sort()
    q = statistics.quantiles(rtts, n=100)
    print(f"latency n={len(rtts)} p50={statistics.median(rtts):.1f}ms "
          f"p95={q[94]:.1f}ms p99={q[98]:.1f}ms")
    w.close(); s.close()


def fanout(host, secs, frame):
    """TCP blasts; BOTH WS clients (max_clients=2) must receive the full
    stream — fan-out TX cost is O(clients) on the DUT."""
    w1, w2, s = _ws(host), _ws(host), _tcp(host)
    payload = (bytes(range(256)) * ((frame // 256) + 1))[:frame]
    rx1, rx2, done = [0], [0], [False]
    threads = [threading.Thread(target=_ws_drain, args=(w1, rx1, done)),
               threading.Thread(target=_ws_drain, args=(w2, rx2, done))]

    for t in threads:
        t.start()

    sent = 0
    t0 = time.time()

    while time.time() - t0 < secs:
        s.sendall(payload)
        sent += frame

    dt = time.time() - t0
    time.sleep(1.5)
    done[0] = True

    for t in threads:
        t.join()

    print(f"fanout clients=2 frame={frame} offered={sent / dt / 1024:.1f} "
          f"KB/s per-client={rx1[0] / dt / 1024:.1f}/"
          f"{rx2[0] / dt / 1024:.1f} KB/s "
          f"aggregate_out={(rx1[0] + rx2[0]) / dt / 1024:.1f} KB/s")
    w1.close(); w2.close(); s.close()


def obd_poll(host, secs, frame):
    """Needs br_obd = obd <-> ws_obd + the live OBD bench. Mirrors
    system_bench's TCP polling loop for the apples-to-apples row."""
    w = _ws(host)
    w.settimeout(3)
    rtts = []
    t0 = time.time()

    while time.time() - t0 < secs:
        t = time.perf_counter()
        w.send_binary(b"0100\r")
        buf = b""

        try:
            while b">" not in buf:
                _, d = w.recv_data()
                buf += d
        except Exception:
            continue

        if b"41 00" in buf:
            rtts.append((time.perf_counter() - t) * 1000)

    if len(rtts) < 2:
        print("obd_poll FAILED: no OBD answers "
              "(obd<->ws_obd bridge + live OBD bench configured?)")
        sys.exit(1)

    rtts.sort()
    q = statistics.quantiles(rtts, n=100)
    print(f"obd_poll n={len(rtts)} polls/{secs:.0f}s "
          f"p50={statistics.median(rtts):.1f}ms p95={q[94]:.1f}ms")
    w.close()


SCENARIOS = {f.__name__: f
             for f in (ws_to_tcp, tcp_to_ws, bidir, latency, fanout,
                       obd_poll)}

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", required=True)
    ap.add_argument("--scenario", required=True, choices=SCENARIOS)
    ap.add_argument("--secs", type=int, default=20)
    ap.add_argument("--frame", type=int, default=128)
    ap.add_argument("--ws-path", default="/ws/obd")
    args = ap.parse_args()
    WS_PATH = args.ws_path
    SCENARIOS[args.scenario](args.host, args.secs, args.frame)
