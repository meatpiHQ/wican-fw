#!/usr/bin/env python3
"""A phone app's Bluetooth session, played by the bench Pi (the fake phone).

Runs ON rpi001 (the UB500, bleak, the passkey agent of ble_bench.py),
shipped and started by ble_pi_run.py:

  python tools/testbench/ble/ble_pi_run.py app_session_phone_pi.py
         --name WiC_44bd8d73ca79 --identity 44:BD:8D:73:CA:7A [--passkey 123456]
         [--mode elm|realdash] [--secs 30] [--cycles 1] [--drop-bond]
         [--nm-profile wican-dut-ap]

What a Car Scanner style app does, in order: connect, pair (the device
asks a second in; the agent answers the static passkey), subscribe FFF1,
write its init to FFF2 without response (`ATZ`, `ATE0`, `ATH1`, `ATSP6`,
`ATS0`, `ATAT1`, `ATST96`, `0100`), then the dashboard loop with the
response-count hint (`010C1`, `010D1`, `01051`) for --secs, then
disconnect. --cycles repeats the whole thing (the "four attempts" of a
field report). --mode realdash only subscribes and counts the 20-byte
RealDash frames the device streams (the CAN bridge with the realdash
translator). --drop-bond removes the Pi's bond first, so the session pairs
afresh as a new phone would.

Prints one `FAKE PHONE cycle N: ...` line per cycle (the pair time, the
init time, the poll count, the RTT p50/p95/max, the bad answers, the
notifications and bytes) and `FAKE PHONE DONE` at the end; exit 1 when a
cycle could not connect or got no answer. The device's own story of the
same session is on its console (ble_manager's session lines) and in the
capture (app_session_capture.py).
"""
import argparse
import asyncio
import sys
import time

from ble_link import connect, prep_adapter, sh, uuid16

UUID_FFF1 = uuid16(0xFFF1)
UUID_FFF2 = uuid16(0xFFF2)
INIT = [b"ATZ", b"ATE0", b"ATH1", b"ATSP6", b"ATS0", b"ATAT1", b"ATST96", b"0100"]
POLL = [b"010C1", b"010D1", b"01051"]
BAD = (b"NO DATA", b"STOPPED", b"?", b"UNABLE", b"ERROR", b"BUS")


def pct(vals, p):
    if not vals:
        return None
    s = sorted(vals)
    return round(s[int(round(p / 100 * (len(s) - 1)))], 1)


class Pipe:
    def __init__(self):
        self.buf = bytearray()
        self.ev = asyncio.Event()
        self.notifs = 0
        self.rx = 0

    def on_notify(self, _, data):
        self.buf.extend(bytes(data))
        self.notifs += 1
        self.rx += len(data)
        self.ev.set()

    async def wait_prompt(self, timeout):
        """Everything up to and including the next '>' (None on timeout)."""
        deadline = time.monotonic() + timeout
        while True:
            i = self.buf.find(b">")
            if i >= 0:
                out = bytes(self.buf[:i + 1])
                del self.buf[:i + 1]
                return out
            left = deadline - time.monotonic()
            if left <= 0:
                return None
            self.ev.clear()
            try:
                await asyncio.wait_for(self.ev.wait(), left)
            except asyncio.TimeoutError:
                return None


async def elm_cycle(client, pipe, secs):
    t0 = time.monotonic()
    answered = 0
    for cmd in INIT:
        await client.write_gatt_char(UUID_FFF2, cmd + b"\r", response=False)
        r = await pipe.wait_prompt(8.0 if cmd in (b"ATZ", b"0100") else 3.0)
        if r is not None:
            answered += 1
        print(f"    {cmd.decode():8s} -> {r[:60]!r}" if r else f"    {cmd.decode():8s} -> (no prompt)",
              flush=True)
    t_init = round(time.monotonic() - t0, 2)
    rtts, bad, polls = [], 0, 0
    t1 = time.monotonic()
    i = 0
    while time.monotonic() - t1 < secs:
        cmd = POLL[i % len(POLL)]
        i += 1
        ts = time.monotonic()
        await client.write_gatt_char(UUID_FFF2, cmd + b"\r", response=False)
        r = await pipe.wait_prompt(2.0)
        if r is None:
            bad += 1
            continue
        polls += 1
        rtts.append((time.monotonic() - ts) * 1000)
        if any(b in r for b in BAD):
            bad += 1
    return {"init_answered": f"{answered}/{len(INIT)}", "init_s": t_init, "polls": polls,
            "rtt_p50": pct(rtts, 50), "rtt_p95": pct(rtts, 95), "rtt_max": pct(rtts, 100),
            "rate_hz": round(polls / secs, 1), "bad": bad}


async def realdash_cycle(pipe, secs):
    t1 = time.monotonic()
    frames = 0
    while time.monotonic() - t1 < secs:
        pipe.ev.clear()
        try:
            await asyncio.wait_for(pipe.ev.wait(), 1.0)
        except asyncio.TimeoutError:
            pass
        while len(pipe.buf) >= 20:
            j = pipe.buf.find(b"\x66\x33\x22\x11")
            if j < 0:
                pipe.buf.clear()
                break
            if j > 0:
                del pipe.buf[:j]
            if len(pipe.buf) < 20:
                break
            del pipe.buf[:20]
            frames += 1
    return {"frames": frames, "frames_per_s": round(frames / secs, 1)}


async def run(a):
    bonded = prep_adapter(a.identity.upper(), a.nm_profile, drop_bond=a.drop_bond)
    fails = 0
    for cycle in range(1, a.cycles + 1):
        pipe = Pipe()
        t0 = time.monotonic()
        try:
            client, cap = await connect(a.name, a.identity.upper(), a.passkey, bonded=bonded)
        except Exception as e:  # noqa: BLE001
            print(f"FAKE PHONE cycle {cycle}: connect failed: {e}", flush=True)
            fails += 1
            await asyncio.sleep(3)
            continue
        t_conn = round(time.monotonic() - t0, 2)
        bonded = True
        try:
            try:
                await client.start_notify(UUID_FFF1, pipe.on_notify, use_notify_acquire=True)
            except Exception:  # noqa: BLE001
                await client.start_notify(UUID_FFF1, pipe.on_notify)
            t_sub = round(time.monotonic() - t0, 2)
            await asyncio.sleep(0.3)
            if a.mode == "elm":
                r = await elm_cycle(client, pipe, a.secs)
            else:
                r = await realdash_cycle(pipe, a.secs)
            r.update({"connect_pair_s": t_conn, "subscribed_s": t_sub,
                      "notifications": pipe.notifs, "rx_bytes": pipe.rx})
            print(f"FAKE PHONE cycle {cycle}: " +
                  " ".join(f"{k}={v}" for k, v in r.items()), flush=True)
            if (a.mode == "elm" and r["polls"] == 0) or (a.mode != "elm" and r["frames"] == 0):
                fails += 1
            try:
                await client.stop_notify(UUID_FFF1)
            except Exception:  # noqa: BLE001
                pass
        finally:
            try:
                await client.disconnect()
            except Exception:  # noqa: BLE001
                pass
        if cycle < a.cycles:
            await asyncio.sleep(a.gap)
    print(f"FAKE PHONE DONE cycles={a.cycles} failed={fails}", flush=True)
    return 1 if fails else 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--name", required=True)
    ap.add_argument("--identity", required=True, help="the device's public address")
    ap.add_argument("--passkey", type=int, default=123456)
    ap.add_argument("--mode", choices=("elm", "realdash"), default="elm")
    ap.add_argument("--secs", type=float, default=30)
    ap.add_argument("--cycles", type=int, default=1)
    ap.add_argument("--gap", type=float, default=5, help="seconds between cycles")
    ap.add_argument("--drop-bond", action="store_true")
    ap.add_argument("--nm-profile", default="wican-dut-ap")
    a = ap.parse_args()
    return asyncio.run(run(a))


if __name__ == "__main__":
    sys.exit(main())
