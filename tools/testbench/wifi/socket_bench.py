"""socket_manager benchmark load generator (BENCHMARKS.md scenarios).

Runs from the PC or rpi001 against a DUT running a socket_manager
composition. Plain sockets — no DUT-side cooperation needed beyond an
echoing/absorbing bridge on the target port.

    python tools/testbench/socket_bench.py --host 10.42.0.123 --port 35000 \
        --scenario tcp_throughput|udp_loss|latency|multiclient [--secs 60]

Prints one result line per run; copy it (plus firmware commit, IDF version,
RSSI/channel for Wi-Fi runs) into components/socket_manager/BENCHMARKS.md.
"""
import argparse
import socket
import statistics
import threading
import time

CHUNK = 128  # matches the firmware chunk convention


def tcp_throughput(host, port, secs):
    """Against an echo bridge this measures BIDIRECTIONAL throughput: the
    reader thread drains the echoed bytes so backpressure stays honest."""
    s = socket.create_connection((host, port), timeout=5)
    payload = (bytes(range(256)) * (CHUNK // 2))[:CHUNK]
    sent = 0
    rcvd = [0]
    done = [False]

    def reader():
        s.settimeout(1)

        while not done[0]:
            try:
                b = s.recv(4096)
            except socket.timeout:
                continue
            except OSError:
                break

            if not b:
                break

            rcvd[0] += len(b)

    t = threading.Thread(target=reader)
    t.start()
    t0 = time.time()

    while time.time() - t0 < secs:
        s.sendall(payload)
        sent += len(payload)

    dt = time.time() - t0
    time.sleep(1)
    done[0] = True
    t.join()
    s.close()
    print(f"tcp_throughput up={sent / dt / 1024:.1f} KB/s "
          f"echoed_down={rcvd[0] / dt / 1024:.1f} KB/s over {dt:.0f}s")


def latency(host, port, secs):
    s = socket.create_connection((host, port), timeout=5)
    s.settimeout(2)
    rtts = []
    t0 = time.time()

    while time.time() - t0 < secs:
        t = time.perf_counter()
        s.sendall(b"ping-rtt")

        try:
            s.recv(64)
        except socket.timeout:
            continue

        rtts.append((time.perf_counter() - t) * 1000)
        time.sleep(0.02)

    s.close()
    rtts.sort()
    print(f"latency n={len(rtts)} p50={statistics.median(rtts):.1f}ms "
          f"p95={rtts[int(len(rtts) * 0.95) - 1]:.1f}ms "
          f"p99={rtts[int(len(rtts) * 0.99) - 1]:.1f}ms")


def udp_loss(host, port, secs):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.settimeout(1)
    sent = 0
    t0 = time.time()

    while time.time() - t0 < secs:
        s.sendto(b"u" * CHUNK, (host, port))
        sent += 1
        time.sleep(0.002)

    print(f"udp offered {sent} datagrams "
          f"({sent * CHUNK / secs / 1024:.1f} KB/s) — read delivered count "
          f"from DUT stats (bytes_in/{CHUNK})")


def multiclient(host, port, secs, n=4):
    results = [0] * n

    def worker(i):
        s = socket.create_connection((host, port), timeout=5)
        payload = b"m" * CHUNK
        t0 = time.time()

        while time.time() - t0 < secs:
            try:
                s.sendall(payload)
                results[i] += CHUNK
            except OSError:
                break

        s.close()

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(n)]

    for t in threads:
        t.start()

    for t in threads:
        t.join()

    per = [r / secs / 1024 for r in results]
    print(f"multiclient n={n} per-client KB/s={[f'{p:.1f}' for p in per]} "
          f"aggregate={sum(per):.1f} KB/s")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", required=True)
    ap.add_argument("--port", type=int, default=35000)
    ap.add_argument("--scenario", required=True,
                    choices=["tcp_throughput", "latency", "udp_loss",
                             "multiclient"])
    ap.add_argument("--secs", type=int, default=60)
    args = ap.parse_args()

    {"tcp_throughput": tcp_throughput, "latency": latency,
     "udp_loss": udp_loss, "multiclient": multiclient}[args.scenario](
        args.host, args.port, args.secs)


if __name__ == "__main__":
    main()
