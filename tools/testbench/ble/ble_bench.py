"""BLE bench client for ble_manager — runs ON rpi001 (BlueZ + bleak).

Scenarios against the ble_manager test app (ble<->echo bridge + stand-in
CLI handler):

  1. scan      — find "WiC_<id>", verify FFF0 advertised
  2. pair      — bond with the static passkey (BlueZ agent), verify the
                 Device Information strings (MEATPI.COM / WiCAN-PRO / serial)
  3. echo      — subscribe FFF1, write FFF2, assert byte-exact echo
  4. cli       — subscribe CLI OUT, write a line to CLI IN, expect the
                 stand-in handler's UPPER-CASED reply
  5. perf      — RTT (p50/p95) and notify throughput through the echo bridge

Pairing note: the device is IO_CAP_OUT with a STATIC passkey (settings,
default 123456). BlueZ needs a KeyboardOnly-ish agent to enter it; this
script registers a D-Bus agent that answers RequestPasskey automatically.
Remove a stale bond first: bluetoothctl remove <mac>.

Usage (on rpi001):
    sudo python3 ble_bench.py --passkey 123456 [--name WiC_] [--scenario all]

Needs: python3-dbus + bleak (pip install bleak dbus-python or apt
python3-dbus). Expected output ends with: BLE BENCH PASS
"""
import argparse
import asyncio
import statistics
import sys
import time

from bleak import BleakClient, BleakScanner

# on-air contract (ble_manager_gatt.c — legacy-preserved)
UUID_FFF1 = "0000fff1-0000-1000-8000-00805f9b34fb"   # notify: data OUT
UUID_FFF2 = "0000fff2-0000-1000-8000-00805f9b34fb"   # write: data IN
UUID_CLI_OUT = "0200dec0-01ef-bc9a-5678-1234deadf0be"
UUID_CLI_IN = "0300dec0-01ef-bc9a-5678-1234deadf0be"
UUID_MANUFACTURER = "00002a29-0000-1000-8000-00805f9b34fb"
UUID_MODEL = "00002a24-0000-1000-8000-00805f9b34fb"
UUID_SERIAL = "00002a25-0000-1000-8000-00805f9b34fb"


def start_passkey_agent(passkey):
    """Register a BlueZ agent that answers RequestPasskey with our static
    passkey (the device displays it; we 'type' it)."""
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
            print(f"agent: RequestPasskey -> {passkey}")
            return dbus.UInt32(passkey)

        @dbus.service.method("org.bluez.Agent1", in_signature="ou")
        def RequestConfirmation(self, device, pk):
            print(f"agent: RequestConfirmation {pk} -> yes")

        @dbus.service.method("org.bluez.Agent1", in_signature="o",
                             out_signature="s")
        def RequestPinCode(self, device):
            return str(passkey)

        @dbus.service.method("org.bluez.Agent1", in_signature="")
        def Release(self):
            pass

    path = "/wican/agent"
    Agent(bus, path)
    manager = dbus.Interface(bus.get_object("org.bluez", "/org/bluez"),
                             "org.bluez.AgentManager1")
    manager.RegisterAgent(path, "KeyboardOnly")
    manager.RequestDefaultAgent(path)

    loop = GLib.MainLoop()
    threading.Thread(target=loop.run, daemon=True).start()
    return loop


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", default="WiC_")
    ap.add_argument("--passkey", type=int, default=123456)
    ap.add_argument("--secs", type=int, default=10)
    args = ap.parse_args()

    start_passkey_agent(args.passkey)

    print("scanning...")
    device = None

    for _ in range(3):
        devices = await BleakScanner.discover(timeout=8.0)

        for d in devices:
            if d.name and d.name.startswith(args.name):
                device = d
                break

        if device:
            break

    if device is None:
        print("SCAN FAILED: no WiC_* advertiser")
        return 1

    print(f"found {device.name} @ {device.address}")

    async with BleakClient(device, timeout=30.0) as client:
        print("connected; pairing...")
        paired = await client.pair()
        print(f"paired: {paired}")

        # device information strings (byte-identical to legacy)
        manu = (await client.read_gatt_char(UUID_MANUFACTURER)).decode()
        model = (await client.read_gatt_char(UUID_MODEL)).decode()
        serial = (await client.read_gatt_char(UUID_SERIAL)).decode(
            errors="replace").rstrip("\x00")
        expect_serial = device.name[7:]
        print(f"device-info: manu={manu!r} model={model!r} serial={serial!r}")
        info_ok = ("MEATPI.COM" in manu and "WiCAN-PRO" in model and
                   serial == expect_serial)

        # echo through the bridge (FFF2 -> pump -> echo -> pump -> FFF1)
        rx = asyncio.Queue()

        def on_fff1(_, data):
            rx.put_nowait(bytes(data))

        await client.start_notify(UUID_FFF1, on_fff1)
        await asyncio.sleep(0.3)

        payload = b"ping-over-ble-bridge"
        await client.write_gatt_char(UUID_FFF2, payload, response=False)
        got = b""

        try:
            while len(got) < len(payload):
                got += await asyncio.wait_for(rx.get(), timeout=5.0)
        except asyncio.TimeoutError:
            pass

        echo_ok = got == payload
        print(f"echo: sent={payload!r} got={got!r} ok={echo_ok}")

        # CLI characteristics (stand-in handler upper-cases the line)
        cli_rx = asyncio.Queue()

        def on_cli(_, data):
            cli_rx.put_nowait(bytes(data))

        await client.start_notify(UUID_CLI_OUT, on_cli)
        await asyncio.sleep(0.3)
        await client.write_gatt_char(UUID_CLI_IN, b"hello cli\n",
                                     response=False)
        cli_got = b""

        try:
            cli_got = await asyncio.wait_for(cli_rx.get(), timeout=5.0)
        except asyncio.TimeoutError:
            pass

        cli_ok = cli_got.strip() == b"HELLO CLI"
        print(f"cli: got={cli_got!r} ok={cli_ok}")

        # perf: RTT p50/p95 + echoed throughput
        rtts = []

        for _ in range(50):
            while not rx.empty():
                rx.get_nowait()

            t0 = time.perf_counter()
            await client.write_gatt_char(UUID_FFF2, b"rtt-probe",
                                         response=False)

            try:
                await asyncio.wait_for(rx.get(), timeout=2.0)
                rtts.append((time.perf_counter() - t0) * 1000)
            except asyncio.TimeoutError:
                pass

        blob = bytes(range(256)) * 4  # 1 KB per write burst
        rcvd = 0
        t0 = time.perf_counter()

        while time.perf_counter() - t0 < args.secs:
            await client.write_gatt_char(UUID_FFF2, blob[:244],
                                         response=False)

            while not rx.empty():
                rcvd += len(rx.get_nowait())

        dt = time.perf_counter() - t0
        await asyncio.sleep(1.0)

        while not rx.empty():
            rcvd += len(rx.get_nowait())

        rtts.sort()
        perf_ok = len(rtts) >= 40 and rcvd > 0
        print(f"perf: rtt n={len(rtts)} "
              f"p50={statistics.median(rtts):.1f}ms "
              f"p95={rtts[int(len(rtts) * 0.95) - 1]:.1f}ms "
              f"echo_throughput={rcvd / dt / 1024:.1f} KB/s")

        await client.stop_notify(UUID_FFF1)
        await client.stop_notify(UUID_CLI_OUT)

        if info_ok and echo_ok and cli_ok and perf_ok:
            print("BLE BENCH PASS")
            return 0

    print("BLE BENCH FAIL")
    return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
