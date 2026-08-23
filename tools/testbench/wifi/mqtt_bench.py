"""mqtt_manager benchmark driver (BENCHMARKS.md scenarios).

Runs ON rpi001 (broker host). Drives the device's MQTT bench surface
(main_bench.c): `<prefix>/bench/cmd` triggers an on-device publish_async
burst to `<prefix>/bench/out`; `<prefix>/bench/echo` is bounced to
`<prefix>/bench/echo_re` from the esp-mqtt event task.

    python3 mqtt_bench.py --device-id 14c19f44e349 --scenario \
        throughput|sustained|rtt [--n 2000] [--size 512] [--gap-ms 0]

throughput: count/time messages at the broker between first and last,
compare with the device's result message (ok vs dropped_full = offered
vs sustainable). sustained: paced run — expect zero drops. rtt: echo
round trip percentiles.
"""
import argparse
import json
import statistics
import time

import paho.mqtt.client as mqtt

BROKER = "localhost"


def run(args):
    prefix = f"wican/{args.device_id}"
    state = {
        "count": 0, "bytes": 0, "first": None, "last": None,
        "result": None, "rtts": [], "pending": None,
    }

    def on_message(cli, _u, msg):
        now = time.perf_counter()

        if msg.topic.endswith("/bench/out"):
            state["count"] += 1
            state["bytes"] += len(msg.payload)
            state["first"] = state["first"] or now
            state["last"] = now
        elif msg.topic.endswith("/bench/result"):
            state["result"] = json.loads(msg.payload)
        elif msg.topic.endswith("/bench/echo_re"):
            if state["pending"] is not None:
                state["rtts"].append((now - state["pending"]) * 1000)
                state["pending"] = None

    cli = mqtt.Client()
    cli.on_message = on_message
    cli.connect(BROKER)
    cli.subscribe([(f"{prefix}/bench/out", 0),
                   (f"{prefix}/bench/result", 0),
                   (f"{prefix}/bench/echo_re", 0)])
    cli.loop_start()
    time.sleep(0.5)

    if args.scenario in ("throughput", "sustained"):
        cmd = {"n": args.n, "size": args.size}

        if args.gap_ms:
            cmd["gap_ms"] = args.gap_ms

        cli.publish(f"{prefix}/bench/cmd", json.dumps(cmd))

        deadline = time.time() + 120

        while state["result"] is None and time.time() < deadline:
            time.sleep(0.2)

        time.sleep(1)  # trailing messages

        r = state["result"] or {}
        span = ((state["last"] or 0) - (state["first"] or 0)) or 1e-9
        print(f"{args.scenario} size={args.size} n={args.n} "
              f"gap_ms={args.gap_ms}: device ok={r.get('ok')} "
              f"dropped_full={r.get('dropped_full')} "
              f"enqueue_ms={r.get('enqueue_ms')}; broker rx "
              f"{state['count']} msgs "
              f"({state['count'] / span:.0f} msg/s, "
              f"{state['bytes'] / span / 1024:.1f} KB/s)")
    elif args.scenario == "rtt":
        payload = b"r" * args.size

        for _ in range(args.n):
            state["pending"] = time.perf_counter()
            cli.publish(f"{prefix}/bench/echo", payload)

            t0 = time.time()

            while state["pending"] is not None and time.time() - t0 < 2:
                time.sleep(0.001)

            time.sleep(0.02)

        rtts = sorted(state["rtts"])
        q = statistics.quantiles(rtts, n=100)
        print(f"rtt size={args.size} n={len(rtts)} "
              f"p50={statistics.median(rtts):.1f}ms p95={q[94]:.1f}ms "
              f"p99={q[98]:.1f}ms")

    cli.loop_stop()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--device-id", required=True)
    ap.add_argument("--scenario", required=True,
                    choices=["throughput", "sustained", "rtt"])
    ap.add_argument("--n", type=int, default=2000)
    ap.add_argument("--size", type=int, default=512)
    ap.add_argument("--gap-ms", type=int, default=0)
    args = ap.parse_args()
    run(args)
