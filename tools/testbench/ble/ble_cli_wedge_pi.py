"""BLE CLI wedge probe — runs ON rpi001 (BlueZ + bleak), MAIN firmware.

Verifies the 2026-07-10 fix: BLE CLI lines are queued to the
cmdline_manager dispatcher instead of executing INLINE in the NimBLE
host task (where a long command wedged the stack — GATT "Unlikely
Error" / stalled ATT).

  1. quick    — `help` returns output ending in the `wican> ` prompt
  2. wedge    — queue 4x `system -t` (~1 s each: the dispatcher stays
                busy ~4 s) and DURING that window do Device-Info GATT
                reads: with the fix they return at normal BLE RTT;
                inline exec serializes them behind the commands
                (median >= ~1 s = FAIL) — and all 4 prompts still arrive
  3. flood    — 8 back-to-back lines (queue depth 4): expect prompts
                and/or `busy` feedback, NO disconnect, CLI usable after

Usage: sudo python3 ble_cli_wedge_pi.py --passkey 421337 [--name WiC_]
Ends with: BLE CLI WEDGE PASS

Connect by DISCOVERED DEVICE, never by a bare address: the DUT
advertises RPAs; when a bond exists BlueZ reports the identity address,
and removing the bond (the wrong-LTK hygiene step) deletes the IRK —
a subsequent connect-by-identity then fails "device not found".
"""
import argparse
import asyncio
import statistics
import sys
import time

from bleak import BleakClient, BleakScanner

UUID_CLI_OUT = "0200dec0-01ef-bc9a-5678-1234deadf0be"
UUID_CLI_IN = "0300dec0-01ef-bc9a-5678-1234deadf0be"
UUID_MANUFACTURER = "00002a29-0000-1000-8000-00805f9b34fb"

PROMPT = b"wican> "
fails = []


def check(name, ok, detail=""):
    print(("PASS" if ok else "FAIL") + ": " + name
          + (f"  ({detail})" if detail else ""))
    if not ok:
        fails.append(name)


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

    path = "/wican/wedgeagent"
    Agent(bus, path)
    mgr = dbus.Interface(bus.get_object("org.bluez", "/org/bluez"),
                         "org.bluez.AgentManager1")
    mgr.RegisterAgent(path, "KeyboardOnly")
    mgr.RequestDefaultAgent(path)
    threading.Thread(target=GLib.MainLoop().run, daemon=True).start()


async def drain_until(buf_q, out: bytearray, want_prompts, timeout):
    """Accumulate CLI OUT until `want_prompts` prompts are seen."""
    end = time.time() + timeout
    while time.time() < end:
        if out.count(PROMPT) >= want_prompts:
            return True
        try:
            out += await asyncio.wait_for(
                buf_q.get(), timeout=max(0.1, end - time.time()))
        except asyncio.TimeoutError:
            break
    return out.count(PROMPT) >= want_prompts


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", default="WiC_")
    ap.add_argument("--passkey", type=int, required=True)
    args = ap.parse_args()

    start_passkey_agent(args.passkey)

    device = None
    for _ in range(3):
        for d in await BleakScanner.discover(timeout=10.0):
            if d.name and d.name.startswith(args.name):
                device = d
                break
        if device:
            break

    if device is None:
        print("BLE CLI WEDGE FAIL: no WiC_* advertiser")
        return 1

    print(f"found {device.name} @ {device.address}")

    async with BleakClient(device, timeout=30.0) as client:
        print("connected; pairing...")
        await client.pair()

        cli_q = asyncio.Queue()

        def on_cli(_, data):
            cli_q.put_nowait(bytes(data))

        await client.start_notify(UUID_CLI_OUT, on_cli)
        await asyncio.sleep(0.3)

        # 1) quick command
        out = bytearray()
        await client.write_gatt_char(UUID_CLI_IN, b"help\n",
                                     response=False)
        ok = await drain_until(cli_q, out, 1, 12)
        check("quick: help returns + prompt", ok and b"help" in out,
              f"{len(out)}B")

        # 2) the wedge: 4 queued 1 s commands + concurrent GATT reads
        out = bytearray()
        for _ in range(4):
            await client.write_gatt_char(UUID_CLI_IN, b"system -t\n",
                                         response=False)

        await asyncio.sleep(0.3)  # let the first command start
        lat = []
        t_busy = time.time()
        while time.time() - t_busy < 3.0:
            t0 = time.perf_counter()
            data = await client.read_gatt_char(UUID_MANUFACTURER)
            lat.append((time.perf_counter() - t0) * 1000)
            await asyncio.sleep(0.1)

        med = statistics.median(lat)
        check("wedge: GATT reads live during 4 s of queued commands",
              len(lat) >= 5 and med < 800 and b"MEATPI" in data,
              f"n={len(lat)} median={med:.0f}ms max={max(lat):.0f}ms")

        ok = await drain_until(cli_q, out, 4, 25)
        check("wedge: all 4 command responses arrived", ok,
              f"prompts={out.count(PROMPT)}")

        # 3) flood past the queue depth — feedback, no wedge
        out = bytearray()
        for _ in range(8):
            await client.write_gatt_char(UUID_CLI_IN, b"help\n",
                                         response=False)

        # every line answers with a prompt — the busy fallback ends in
        # one too — so 8 writes must yield 8 prompts if nothing wedged
        await drain_until(cli_q, out, 8, 25)
        prompts = out.count(PROMPT)
        busies = out.count(b"busy")
        check("flood: every line answered (response or busy)",
              prompts >= 8 and client.is_connected,
              f"prompts={prompts} busy={busies}")

        out = bytearray()
        await client.write_gatt_char(UUID_CLI_IN, b"help\n",
                                     response=False)
        ok = await drain_until(cli_q, out, 1, 12)
        check("flood: CLI usable after", ok, "")

        await client.stop_notify(UUID_CLI_OUT)

    if fails:
        print("BLE CLI WEDGE FAIL: " + ", ".join(fails))
        return 1
    print("BLE CLI WEDGE PASS")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
