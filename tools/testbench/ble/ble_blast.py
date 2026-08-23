#!/usr/bin/env python3
"""Clean-radio BLE TX ceiling: pair (static passkey agent), subscribe
FFF1, print SUBSCRIBED, then count notified bytes while the operator
fires `blast N` over UART. Do NOT send blast over the BLE CLI: CLI
lines execute inline in the NimBLE host task, so a long-running
command wedges the stack (GATT Unlikely Error). With interface_manager
active the BLE connection suspends WiFi, so this measures the
coex-free ceiling (the esp-idf ble_throughput demo's conditions).
Run ON rpi001 with sudo.

Usage: sudo python3 ble_blast.py [--bytes 262144] [--passkey 123456]
"""
import argparse
import asyncio
import sys
import time

from bleak import BleakClient, BleakScanner

UUID_FFF1 = "0000fff1-0000-1000-8000-00805f9b34fb"


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

        @dbus.service.method("org.bluez.Agent1", in_signature="o",
                             out_signature="s")
        def RequestPinCode(self, device):
            return str(passkey)

        @dbus.service.method("org.bluez.Agent1", in_signature="")
        def Release(self):
            pass

    path = "/wican/blastagent"
    Agent(bus, path)
    manager = dbus.Interface(bus.get_object("org.bluez", "/org/bluez"),
                             "org.bluez.AgentManager1")
    manager.RegisterAgent(path, "KeyboardOnly")
    manager.RequestDefaultAgent(path)
    loop = GLib.MainLoop()
    import threading as _t
    _t.Thread(target=loop.run, daemon=True).start()


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bytes", type=int, default=262144)
    ap.add_argument("--passkey", type=int, default=123456)
    args = ap.parse_args()

    start_passkey_agent(args.passkey)

    devs = await BleakScanner.discover(timeout=8.0)
    dev = next((d for d in devs if d.name and d.name.startswith("WiC_")),
               None)
    if not dev:
        print("SCAN FAILED")
        return 1
    print(f"found {dev.name} {dev.address}")

    async with BleakClient(dev.address, timeout=30.0) as c:
        try:
            await c.pair()
        except Exception as e:
            print(f"pair: {e} (may already be bonded)")

        rx = {"n": 0, "first": None, "last": None}

        def on_data(_, data):
            now = time.time()
            if rx["first"] is None:
                rx["first"] = now
            rx["last"] = now
            rx["n"] += len(data)

        await c.start_notify(UUID_FFF1, on_data)
        await asyncio.sleep(3)  # interface_manager suspends WiFi here
        print("SUBSCRIBED - fire `blast` over UART now", flush=True)
        t0 = time.time()
        while time.time() - t0 < 90:
            await asyncio.sleep(1)
            if rx["last"] and time.time() - rx["last"] > 3 and rx["n"] > 0:
                break
        dur = (rx["last"] - rx["first"]) if rx["first"] else 1
        print(f"BLE TX (clean radio): {rx['n']}/{args.bytes} bytes in "
              f"{dur:.2f}s = {rx['n'] / dur / 1024:.1f} KB/s")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
