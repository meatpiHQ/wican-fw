#!/usr/bin/env python3
"""BLE bond/security probe — runs ON rpi001 (BlueZ + bleak + UB500).

One probe per invocation; the PC-side orchestrator (ble_bonding_test.py)
sequences them with a DUT reboot in between. Modes:

  wrongkey   connect and read Device Info while the agent supplies a WRONG
             passkey — MUST be rejected (authenticated pairing fails =>
             every char is gated behind MITM; on un-gated firmware the DI
             read would succeed regardless of the passkey)
  pair       pair with the static passkey, then read Device Info — proves
             pairing works with the NVS store and a paired read succeeds
  reconnect  connect and read an ENC+AUTHEN char WITHOUT calling pair()
             and WITHOUT the passkey agent firing — proves the DUT kept
             its bond across a reboot (no re-pair / no re-passkey)

Prints one machine-readable RESULT: line. Needs python3-dbus + bleak.

  sudo python3 ble_bond_probe.py --mac <AA:BB..> --passkey N --mode MODE
"""
import argparse
import asyncio
import sys

from bleak import BleakClient, BleakScanner

UUID_SERIAL = "00002a25-0000-1000-8000-00805f9b34fb"  # DI serial (ENC+AUTHEN)

_agent_prompted = False


def start_passkey_agent(passkey):
    """BlueZ agent answering RequestPasskey; flips a global when asked so
    the reconnect mode can detect a re-pair."""
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
            global _agent_prompted
            _agent_prompted = True
            return dbus.UInt32(passkey)

        @dbus.service.method("org.bluez.Agent1", in_signature="ou")
        def RequestConfirmation(self, device, pk):
            global _agent_prompted
            _agent_prompted = True

        @dbus.service.method("org.bluez.Agent1", in_signature="o",
                             out_signature="s")
        def RequestPinCode(self, device):
            global _agent_prompted
            _agent_prompted = True
            return str(passkey)

        @dbus.service.method("org.bluez.Agent1", in_signature="")
        def Release(self):
            pass

    Agent(bus, "/wican/agent")
    mgr = dbus.Interface(bus.get_object("org.bluez", "/org/bluez"),
                         "org.bluez.AgentManager1")
    mgr.RegisterAgent("/wican/agent", "KeyboardOnly")
    mgr.RequestDefaultAgent("/wican/agent")
    loop = GLib.MainLoop()
    threading.Thread(target=loop.run, daemon=True).start()
    return loop


async def find(mac, name):
    for _ in range(4):
        for d in await BleakScanner.discover(timeout=8.0):
            if (mac and d.address.upper() == mac.upper()) or \
               (name and d.name and d.name.startswith(name)):
                return d
    return None


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mac", default="")
    ap.add_argument("--name", default="WiC_")
    ap.add_argument("--passkey", type=int, default=123456)
    ap.add_argument("--mode", required=True,
                    choices=["wrongkey", "pair", "reconnect"])
    args = ap.parse_args()

    start_passkey_agent(args.passkey)

    device = await find(args.mac, args.name)
    if device is None:
        print("RESULT: error=scan_failed")
        return 1

    if args.mode == "wrongkey":
        # the agent supplies a WRONG passkey (caller passes it): MITM
        # pairing fails, so the encrypted DI read MUST be refused
        try:
            async with BleakClient(device, timeout=30.0) as c:
                try:
                    val = await c.read_gatt_char(UUID_SERIAL)
                    print(f"  read returned {len(val)} bytes: {bytes(val)!r}")
                    print("RESULT: wrongkey_read=LEAKED")  # bad — not gated
                    return 1
                except Exception as e:
                    print(f"RESULT: wrongkey_read=blocked ({type(e).__name__})")
                    return 0
        except Exception as e:
            # rejected at the encrypted-read step by dropping the link —
            # still "not readable without the right passkey"
            print(f"RESULT: wrongkey_read=blocked ({type(e).__name__})")
            return 0

    if args.mode == "pair":
        async with BleakClient(device, timeout=30.0) as c:
            try:
                await c.pair()  # BlueZ often auto-pairs on the enc read;
            except Exception:   # its return is unreliable, so judge by the
                pass            # gated read below, not by pair()'s result
            serial = (await c.read_gatt_char(UUID_SERIAL)).decode(
                errors="replace").rstrip("\x00")
            # the ENC+AUTHEN read only succeeds if MITM pairing completed
            # with the correct passkey
            ok = serial == device.name[7:]
            print(f"RESULT: paired_read={serial!r} ok={ok}")
            return 0 if ok else 1

    if args.mode == "reconnect":
        # do NOT pair; rely on the bond both sides already hold
        async with BleakClient(device, timeout=30.0) as c:
            serial = (await c.read_gatt_char(UUID_SERIAL)).decode(
                errors="replace").rstrip("\x00")
            persisted = (serial == device.name[7:]) and not _agent_prompted
            print(f"RESULT: reconnect_read={serial!r} "
                  f"agent_prompted={_agent_prompted} "
                  f"bond_persisted={persisted}")
            return 0 if persisted else 1

    return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
