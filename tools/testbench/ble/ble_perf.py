"""BLE performance benchmark for ble_manager — runs ON rpi001.

Three measured scenarios against the ble_manager test app (results go into
components/ble_manager/BENCHMARKS.md):

  1. TX (notify) throughput  — CLI "blast <N>": the DUT pushes N bytes
     through ble_manager_send -> FFF1; we time first->last byte and verify
     the byte count (the pattern is 0..127 cycling in 128-byte chunks).
  2. Echo (bidirectional)    — write-without-response flat out for --secs,
     draining the echoed FFF1 notifications: every byte crosses the bridge
     pump twice.
  3. RTT                     — 100 write->notify round trips, p50/p95/p99.

Usage: python3 ble_perf.py [--passkey 123456] [--secs 20] [--blast 262144]
Prints one RESULT line per scenario. Expected final line: BLE PERF DONE
"""
import argparse
import asyncio
import statistics
import sys
import time

from bleak import BleakClient, BleakScanner

UUID_FFF1 = "0000fff1-0000-1000-8000-00805f9b34fb"
UUID_FFF2 = "0000fff2-0000-1000-8000-00805f9b34fb"
UUID_CLI_OUT = "0200dec0-01ef-bc9a-5678-1234deadf0be"
UUID_CLI_IN = "0300dec0-01ef-bc9a-5678-1234deadf0be"


def start_passkey_agent(passkey):
    import dbus
    import dbus.mainloop.glib
    import dbus.service
    from gi.repository import GLib
    import threading

    dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
    bus = dbus.SystemBus()

    class Agent(dbus.service.Object):
        @dbus.service.method("org.bluez.Agent1", in_signature="o",
                             out_signature="u")
        def RequestPasskey(self, device):
            return dbus.UInt32(passkey)

        @dbus.service.method("org.bluez.Agent1", in_signature="ou")
        def RequestConfirmation(self, device, pk):
            pass

        @dbus.service.method("org.bluez.Agent1", in_signature="")
        def Release(self):
            pass

    path = "/wican/perfagent"
    Agent(bus, path)
    manager = dbus.Interface(bus.get_object("org.bluez", "/org/bluez"),
                             "org.bluez.AgentManager1")
    manager.RegisterAgent(path, "KeyboardOnly")
    manager.RequestDefaultAgent(path)
    threading.Thread(target=GLib.MainLoop().run, daemon=True).start()


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--passkey", type=int, default=123456)
    ap.add_argument("--secs", type=int, default=20)
    ap.add_argument("--blast", type=int, default=262144)  # 256 KB
    args = ap.parse_args()

    start_passkey_agent(args.passkey)

    device = None

    for _ in range(3):
        for d in await BleakScanner.discover(timeout=8.0):
            if d.name and d.name.startswith("WiC_"):
                device = d
                break

        if device:
            break

    if device is None:
        print("no WiC_* advertiser")
        return 1

    print(f"device: {device.name} @ {device.address}")

    async with BleakClient(device, timeout=30.0) as client:
        await client.pair()
        print(f"mtu: {client.mtu_size}")

        rx_count = [0]
        rx_first = [None]
        rx_last = [0.0]
        rx_q = asyncio.Queue()

        def on_fff1(_, data):
            now = time.perf_counter()

            if rx_first[0] is None:
                rx_first[0] = now

            rx_last[0] = now
            rx_count[0] += len(data)
            rx_q.put_nowait(len(data))

        cli_q = asyncio.Queue()

        def on_cli(_, data):
            cli_q.put_nowait(bytes(data))

        await client.start_notify(UUID_FFF1, on_fff1)
        await client.start_notify(UUID_CLI_OUT, on_cli)
        await asyncio.sleep(0.5)

        # ---- 1. TX (notify) throughput via blast -------------------------
        rx_count[0] = 0
        rx_first[0] = None
        await client.write_gatt_char(UUID_CLI_IN,
                                     f"blast {args.blast}\n".encode(),
                                     response=False)
        await asyncio.wait_for(cli_q.get(), timeout=5.0)  # "BLASTING"

        deadline = time.time() + 120

        while rx_count[0] < args.blast and time.time() < deadline:
            await asyncio.sleep(0.2)

        got = rx_count[0]
        dt = (rx_last[0] - rx_first[0]) if rx_first[0] else 1
        print(f"RESULT tx_notify: {got}/{args.blast} bytes in {dt:.1f}s "
              f"= {got / dt / 1024:.1f} KB/s")

        # ---- 2. echo (bidirectional) throughput --------------------------
        rx_count[0] = 0
        payload = bytes(range(244))
        t0 = time.perf_counter()

        while time.perf_counter() - t0 < args.secs:
            await client.write_gatt_char(UUID_FFF2, payload, response=False)

        sent_dt = time.perf_counter() - t0
        await asyncio.sleep(2.0)
        print(f"RESULT echo_bidir: up={args.secs}s "
              f"echoed {rx_count[0] / sent_dt / 1024:.1f} KB/s each way")

        # ---- 3. RTT -------------------------------------------------------
        rtts = []

        for _ in range(100):
            while not rx_q.empty():
                rx_q.get_nowait()

            t = time.perf_counter()
            await client.write_gatt_char(UUID_FFF2, b"rtt", response=False)

            try:
                await asyncio.wait_for(rx_q.get(), timeout=2.0)
                rtts.append((time.perf_counter() - t) * 1000)
            except asyncio.TimeoutError:
                pass

        rtts.sort()
        print(f"RESULT rtt: n={len(rtts)} p50={statistics.median(rtts):.1f} "
              f"p95={rtts[int(len(rtts) * 0.95) - 1]:.1f} "
              f"p99={rtts[int(len(rtts) * 0.99) - 1]:.1f} ms")

        await client.stop_notify(UUID_FFF1)
        await client.stop_notify(UUID_CLI_OUT)

    print("BLE PERF DONE")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
