#!/usr/bin/env python3
"""Bluedroid-vs-NimBLE A/B against the COMPOSED main (run ON rpi001,
sudo). Needs: BLE enabled in settings and a `br_ble = ble <-raw-> obd0`
bridge (obd0 TCP :35000 is the wired far end for throughput).

Measures the same contract on either stack:
  - scan by name + pair (static passkey via D-Bus agent) + Device Info
  - CLI RTT p50/p95 ("version\\n" -> "wican> ")
  - BLE TX throughput: TCP push -> bridge -> FFF1 notifies (delivered rate)
  - BLE RX throughput: FFF2 writes -> bridge -> TCP receive

Usage: sudo python3 ble_ab.py [--passkey 123456] [--dut 10.42.0.62]
Ends with: BLE AB DONE <json>
"""
import argparse
import asyncio
import json
import socket
import statistics
import sys
import time

from bleak import BleakClient, BleakScanner

UUID_FFF1 = "0000fff1-0000-1000-8000-00805f9b34fb"
UUID_FFF2 = "0000fff2-0000-1000-8000-00805f9b34fb"
UUID_CLI_OUT = "0200dec0-01ef-bc9a-5678-1234deadf0be"
UUID_CLI_IN = "0300dec0-01ef-bc9a-5678-1234deadf0be"
UUID_MANUFACTURER = "00002a29-0000-1000-8000-00805f9b34fb"
UUID_MODEL = "00002a24-0000-1000-8000-00805f9b34fb"
UUID_SERIAL = "00002a25-0000-1000-8000-00805f9b34fb"


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

    path = "/wican/abagent"
    Agent(bus, path)
    manager = dbus.Interface(bus.get_object("org.bluez", "/org/bluez"),
                             "org.bluez.AgentManager1")
    manager.RegisterAgent(path, "KeyboardOnly")
    manager.RequestDefaultAgent(path)
    loop = GLib.MainLoop()
    threading.Thread(target=loop.run, daemon=True).start()


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--passkey", type=int, default=123456)
    ap.add_argument("--dut", default="10.42.0.62")
    ap.add_argument("--name", default="WiC_")
    args = ap.parse_args()

    start_passkey_agent(args.passkey)

    dev = None
    for _ in range(3):
        devs = await BleakScanner.discover(timeout=6.0)
        for d in devs:
            if d.name and d.name.startswith(args.name):
                dev = d
                break
        if dev:
            break
    if not dev:
        print("SCAN FAILED")
        return 1
    print(f"found {dev.name} {dev.address}")

    result = {}
    async with BleakClient(dev.address, timeout=30.0) as client:
        try:
            paired = await client.pair()
            print(f"pair: {paired}")
        except Exception as e:
            print(f"pair: {e} (may already be bonded)")

        # legacy contract: values are sizeof()-sized so they INCLUDE the
        # NUL terminator; serial = adv name + 7 (drops "WiC_" plus the
        # first three hex chars -> last 9 hex), space-padded to 32
        manuf = (await client.read_gatt_char(UUID_MANUFACTURER)).decode()
        model = (await client.read_gatt_char(UUID_MODEL)).decode()
        serial = (await client.read_gatt_char(UUID_SERIAL)).decode()
        print(f"device info: {manuf} / {model} / serial={serial}")
        result["device_info_ok"] = (manuf.strip(" \x00") == "MEATPI.COM"
                                    and model.strip(" \x00") == "WiCAN-PRO"
                                    and serial.strip(" \x00") == dev.name[7:])

        # ---- CLI RTT --------------------------------------------------
        cli_buf = bytearray()
        cli_evt = asyncio.Event()

        def on_cli(_, data):
            cli_buf.extend(data)
            if cli_buf.endswith(b"wican> "):
                cli_evt.set()

        await client.start_notify(UUID_CLI_OUT, on_cli)
        rtts = []
        for i in range(20):
            cli_buf.clear()
            cli_evt.clear()
            t0 = time.time()
            await client.write_gatt_char(UUID_CLI_IN, b"version\n",
                                         response=True)
            try:
                await asyncio.wait_for(cli_evt.wait(), 5.0)
                rtts.append((time.time() - t0) * 1000)
            except asyncio.TimeoutError:
                print(f"cli rtt {i}: timeout")
        if rtts:
            result["cli_rtt_p50_ms"] = round(statistics.median(rtts), 1)
            result["cli_rtt_p95_ms"] = round(
                sorted(rtts)[int(len(rtts) * 0.95) - 1], 1)
            print(f"CLI RTT p50={result['cli_rtt_p50_ms']}ms "
                  f"p95={result['cli_rtt_p95_ms']}ms n={len(rtts)}")

        # ---- throughput via the ble<->obd0 bridge ----------------------
        rx_count = {"n": 0}

        def on_data(_, data):
            rx_count["n"] += len(data)

        await client.start_notify(UUID_FFF1, on_data)
        try:
            tcp = socket.create_connection((args.dut, 35000), timeout=5)
        except OSError as e:
            print(f"TCP unreachable ({e}) - skipping throughput legs "
                  f"(WiFi off?)")
            await client.stop_notify(UUID_FFF1)
            await client.stop_notify(UUID_CLI_OUT)
            print("BLE AB DONE " + json.dumps(result))
            return 0
        tcp.settimeout(0.2)

        # TX (device->BLE): push TCP for 10 s, count notified bytes
        blob = bytes(range(256)) * 16  # 4 KB
        t0 = time.time()
        sent = 0
        while time.time() - t0 < 10.0:
            tcp.sendall(blob)
            sent += len(blob)
            await asyncio.sleep(0.02)  # ~200 KB/s offered
        await asyncio.sleep(1.0)
        dur = time.time() - t0
        result["ble_tx_kbps"] = round(rx_count["n"] / dur / 1024, 1)
        print(f"BLE TX (notify): {rx_count['n']} bytes in {dur:.1f}s = "
              f"{result['ble_tx_kbps']} KB/s (offered {sent} via TCP)")

        # RX (BLE->device): write FFF2 for 5 s, count TCP-received bytes
        rx_tcp = 0
        chunk = bytes(200) * 1  # 200 B write-without-response
        t0 = time.time()
        writes = 0
        while time.time() - t0 < 5.0:
            await client.write_gatt_char(UUID_FFF2, chunk, response=False)
            writes += 1
            while True:
                try:
                    got = tcp.recv(4096)
                    if not got:
                        break
                    rx_tcp += len(got)
                except socket.timeout:
                    break
        # drain the tail
        t_end = time.time() + 1.5
        while time.time() < t_end:
            try:
                got = tcp.recv(4096)
                if not got:
                    break
                rx_tcp += len(got)
            except socket.timeout:
                break
        dur = time.time() - t0
        result["ble_rx_kbps"] = round(rx_tcp / dur / 1024, 1)
        print(f"BLE RX (write): {rx_tcp} bytes in {dur:.1f}s = "
              f"{result['ble_rx_kbps']} KB/s ({writes} writes)")

        tcp.close()
        await client.stop_notify(UUID_FFF1)
        await client.stop_notify(UUID_CLI_OUT)

    print("BLE AB DONE " + json.dumps(result))
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
