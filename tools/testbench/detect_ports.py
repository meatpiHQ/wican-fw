#!/usr/bin/env python3
"""Find the bench's serial ports by WHAT they are, not by their COM number.

COM numbers move whenever the USB tree changes (2026-08-26: PSU COM2016 -> 17,
WiCAN COM1175 -> 12; 2026-09-19: the CH344 quad-UART COM12-15 -> COM252-255,
PSU -> COM50). Every bench script that needs a port asks this module through
`lib/bench_ports.py` instead of carrying a number, and test.ps1's preflight
prints what it found.

    python tools/testbench/detect_ports.py              # table + writes bench_ports.json
    python tools/testbench/detect_ports.py --json       # machine-readable, stdout only
    python tools/testbench/detect_ports.py --role psu   # just the port name (shell substitution)
    python tools/testbench/detect_ports.py --probe-psu  # confirm a legacy OWON by *IDN? (opens FTDI ports ONLY)
    python tools/testbench/detect_ports.py --probe-sim  # confirm ECU sim v2's console port by its device id (HTTP, no port opened)
    python tools/testbench/detect_ports.py --dut-ip     # + the WiCAN's hotspot address via the Pi

Enumeration only by default: NOTHING is opened, because opening the WiCAN
console resets the DUT (whatever DTR/RTS you preset, re-confirmed 2026-09-19
and again 2026-11-09 on the quad UART's channel B).
`--probe-psu` opens only ports that enumerate as an FTDI 'USB Serial Port';
`--probe-sim` asks the simulator's REST over its USB network link.

Roles (the bench fixtures; TESTING.md "physical-setup matrix"):
  wican_console    the quad UART's channel B = the WiCAN's UART0 (2 Mbaud console, esptool 460800);
                   the adapter enumerates as 'USB-Enhanced-SERIAL-x CH344' or, since 2026-11-09,
                   'MeatPi USB-UART A/B' + 'MeatPi USB-RS232' (C) + 'MeatPi USB-RS485' (D)
  dongle_uart      channel A = the ESPNetLink dongle's UART0 (as wired on 2026-10-12)
  spare            channels C / D
  ch342_console    CH342 channel A: the DUT's own USB-C cabled to the PC (device role), console
  ch342_obd        CH342 channel B: the usb_obd ELM port on that same cable
  ecu_sim_console  ECU sim v2's debug USB-C console ('USB Serial Device'; its serial number is
                   the simulator's device id, `--probe-sim` confirms it). The DUT's supply is the
                   simulator's OBD pin 16, reached over the USB network link, not a COM port
                   (lib/bench_psu.py)
  sim_v1_cdc       the first-generation simulator box's CDC console ('MeatPi USB-CAN Device'),
                   not part of the ECU sim v2 bench
  psu              a legacy OWON P4305 (FTDI 'USB Serial Port'; `--probe-psu` confirms it by *IDN?);
                   absent on the ECU sim v2 bench

Exit 0 when the WiCAN console was found, 1 otherwise (usable as a health check).
"""
from __future__ import annotations

import argparse
import json
import platform
import re
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
RECORD = HERE / "bench_ports.json"

ROLE_ORDER = ["wican_console", "dongle_uart", "ecu_sim_console", "ch342_console", "ch342_obd",
              "sim_v1_cdc", "psu"]
ROLE_NOTE = {
    "wican_console": "2,000,000 baud console, esptool 460800; OPENING IT RESETS THE DUT",
    "dongle_uart": "dongle UART0 (115200), as wired on 2026-10-12",
    "ecu_sim_console": "ECU sim v2's debug console; the DUT's supply is its /api/power (bench_psu.py)",
    "sim_v1_cdc": "the first-generation simulator box's CDC console (not the ECU sim v2 bench)",
    "psu": "a legacy OWON P4305, SCPI 115200",
    "ch342_console": "DUT USB-C cabled to the PC: console (opening it resets the DUT)",
    "ch342_obd": "DUT USB-C cabled to the PC: usb_obd ELM port",
}
ABSENT_NOTE = {
    "ch342_console": "the DUT's USB-C is not cabled to the PC (host role: dongle / USB-Ethernet)",
    "ch342_obd": "same cable as ch342_console",
    "psu": "no FTDI 'USB Serial Port': the supply is ECU sim v2's (python tools/testbench/lib/bench_psu.py)",
    "ecu_sim_console": "no 'USB Serial Device' of ECU sim v2 enumerated (its debug USB-C unplugged?)",
    "sim_v1_cdc": "no 'MeatPi USB-CAN Device' enumerated (the first-generation box is off the PC)",
    "wican_console": "no quad-UART channel B enumerated: is the adapter plugged in?",
    "dongle_uart": "no quad-UART channel A enumerated",
}
# quad-UART channel letter from the USB interface number in pyserial's LOCATION (…:x.<iface>)
CH344_IFACE = {"0": "A", "2": "B", "4": "C", "6": "D"}
# the MeatPi-branded quad UART (2026-11-09) names its channels by function
MEATPI_UART_NAMES = {"USB-RS232": "C", "USB-RS485": "D"}

VID_WCH, PID_CH344 = 0x1A86, 0x55D5
VID_FTDI = 0x0403
VID_MEATPI = 0x16D0
# ECU sim v2's debug USB-C: a plain USB CDC console whose serial number is the
# simulator's device id (what `/api/info` calls device_id)
VID_SIM_V2_CONSOLE, PID_SIM_V2_CONSOLE = 0x303A, 0x1001


# ---------------------------------------------------------------- enumeration
def enumerate_ports() -> list[dict]:
    """[{port, desc, vid, pid, location, hwid}] via pyserial, else Windows PnP names."""
    try:
        from serial.tools import list_ports  # type: ignore
    except ImportError:
        list_ports = None
    if list_ports is not None:
        out = []
        for p in list_ports.comports():
            desc = (p.description or "").replace(f" ({p.device})", "").strip()
            out.append({"port": p.device, "desc": desc, "vid": p.vid, "pid": p.pid,
                        "location": p.location or "", "hwid": p.hwid or "",
                        "serial": p.serial_number or ""})
        return out
    if sys.platform != "win32":
        raise SystemExit("detect_ports: pyserial is required here (pip install pyserial)")
    # no pyserial: Windows PnP names carry the description and the VID/PID
    cmd = ("Get-CimInstance Win32_PnPEntity | Where-Object { $_.Name -match '\\(COM\\d+\\)' } "
           "| ForEach-Object { $_.Name + '|' + $_.PNPDeviceID }")
    txt = subprocess.run(["powershell", "-NoProfile", "-Command", cmd],
                         capture_output=True, text=True, timeout=60).stdout
    out = []
    for line in txt.splitlines():
        if "|" not in line:
            continue
        name, pnp = line.rsplit("|", 1)
        m = re.search(r"\((COM\d+)\)", name)
        if not m:
            continue
        v = re.search(r"VID_([0-9A-Fa-f]{4})&PID_([0-9A-Fa-f]{4})", pnp)
        out.append({"port": m.group(1), "desc": name.replace(f" ({m.group(1)})", "").strip(),
                    "vid": int(v.group(1), 16) if v else None, "pid": int(v.group(2), 16) if v else None,
                    "location": "", "hwid": pnp, "serial": ""})
    return out


def present_ports() -> set[str]:
    return {p["port"] for p in enumerate_ports()}


# ---------------------------------------------------------------- classification
def _rec(p: dict, how: str) -> dict:
    return {"port": p["port"], "desc": p["desc"], "how": how}


def _channel(p: dict) -> str | None:
    """The quad UART's channel letter: from the PnP name ('USB-Enhanced-SERIAL-B
    CH344', 'MeatPi USB-UART B', 'MeatPi USB-RS232' = C, 'MeatPi USB-RS485' = D),
    else from the USB interface number in pyserial's location."""
    m = re.search(r"SERIAL-([A-D])\b|USB-UART ([A-D])\b", p["desc"])
    if m:
        return m.group(1) or m.group(2)
    for name, ch in MEATPI_UART_NAMES.items():
        if name in p["desc"]:
            return ch
    m = re.search(r":x\.(\d)", p["location"])
    return CH344_IFACE.get(m.group(1)) if m else None


def classify(ports: list[dict]) -> dict:
    roles: dict[str, dict] = {}
    candidates: dict[str, list] = {"psu": [], "ecu_sim_console": []}
    spare, unclassified, ignored = [], [], []
    for p in sorted(ports, key=lambda x: (len(x["port"]), x["port"])):
        d, vid, pid = p["desc"], p["vid"], p["pid"]
        if "Bluetooth" in d or "BTHENUM" in p["hwid"].upper():
            ignored.append(p["port"])
            continue
        if "CH344" in d or "MeatPi USB-" in d and "USB-CAN" not in d or \
                (vid == VID_WCH and pid == PID_CH344):
            ch = _channel(p)
            role = {"B": "wican_console", "A": "dongle_uart"}.get(ch or "")
            if role:
                roles[role] = _rec(p, f"quad UART channel {ch}")
            else:
                spare.append(_rec(p, f"quad UART channel {ch or '?'}"))
            continue
        if "CH342" in d:
            ch = _channel(p)
            role = {"A": "ch342_console", "B": "ch342_obd"}.get(ch or "")
            (roles.__setitem__(role, _rec(p, f"CH342 channel {ch}")) if role
             else unclassified.append(_rec(p, d)))
            continue
        if vid == VID_MEATPI or "MeatPi USB-CAN" in d:
            roles["sim_v1_cdc"] = _rec(p, "MeatPi USB-CAN Device")
            continue
        if vid == VID_SIM_V2_CONSOLE and pid == PID_SIM_V2_CONSOLE:
            rec = _rec(p, "ECU sim v2 debug console")
            rec["serial"] = p.get("serial", "")
            candidates["ecu_sim_console"].append(rec)
            continue
        if vid == VID_FTDI or d.startswith("USB Serial Port"):
            candidates["psu"].append(_rec(p, "FTDI USB Serial Port"))
            continue
        unclassified.append(_rec(p, d or p["hwid"]))
    if len(candidates["psu"]) == 1:
        roles["psu"] = dict(candidates["psu"][0], confirmed=False)
    if len(candidates["ecu_sim_console"]) == 1:
        roles["ecu_sim_console"] = dict(candidates["ecu_sim_console"][0], confirmed=False)
    return {"roles": roles, "candidates": candidates, "spare": spare,
            "unclassified": unclassified, "ignored": ignored}


def probe_sim(result: dict) -> None:
    """Confirm ECU sim v2's console port: the port whose USB serial number is
    the device id its REST reports (`/api/info` over the USB network link; no
    COM port is opened). Also records the simulator's URL."""
    sys.path.insert(0, str(HERE / "lib"))
    import sim_net  # noqa: E402
    base = sim_net.sim_url()
    info = sim_net._info(base)
    result["ecu_sim"] = {"url": base, "answers": sim_net.is_simulator(info),
                         "device_id": (info or {}).get("device_id", ""),
                         "fw": (info or {}).get("fw_version", "")}
    want = (info or {}).get("device_id", "").replace(":", "").upper()
    for cand in result["candidates"]["ecu_sim_console"]:
        have = cand.get("serial", "").replace(":", "").upper()
        if want and have == want:
            result["roles"]["ecu_sim_console"] = dict(cand, confirmed=True)
        elif want and "ecu_sim_console" in result["roles"] and \
                result["roles"]["ecu_sim_console"]["port"] == cand["port"]:
            result["roles"]["ecu_sim_console"]["confirmed"] = False
            result["roles"]["ecu_sim_console"]["note"] = f"serial {have} is not the simulator's {want}"


def probe_psu(result: dict) -> None:
    """Confirm the OWON among the FTDI ports by *IDN? (opens FTDI ports only)."""
    import serial  # type: ignore
    for cand in result["candidates"]["psu"]:
        idn = ""
        for _attempt in range(2):          # a cold first query can come back empty
            s = serial.Serial()
            s.port, s.baudrate, s.timeout = cand["port"], 115200, 0.4
            s.dtr = s.rts = False
            try:
                s.open()
                s.reset_input_buffer()
                s.write(b"*IDN?\n")
                t0 = time.time()
                buf = b""
                while time.time() - t0 < 1.5 and b"\n" not in buf:
                    buf += s.read(64)
                idn = buf.decode(errors="replace").strip()
            except Exception as e:  # busy port, unplugged mid-way
                idn = f"(open failed: {e})"
            finally:
                try:
                    s.close()
                except Exception:
                    pass
            if idn and "OWON" in idn:
                break
        cand["idn"] = idn
        if "OWON" in idn:
            result["roles"]["psu"] = dict(cand, confirmed=True)
    if "psu" in result["roles"] and not result["roles"]["psu"].get("confirmed"):
        result["roles"]["psu"]["idn"] = result["candidates"]["psu"][0].get("idn", "")


# ---------------------------------------------------------------- the DUT's address
def resolve_dut_ip(pi: str, device_id: str, timeout: int = 25) -> str:
    """The WiCAN's hotspot address via the Pi: its DHCP lease first (the STA side,
    what every bench leg wants), then the avahi _wican._tcp record minus the
    DUT's own 192.168.0.x AP address, which avahi also advertises."""
    remote = (
        f"ip=$(sudo sh -c 'cat /var/lib/NetworkManager/dnsmasq-*.leases 2>/dev/null' "
        f"| awk -v h=wican_{device_id} '$4==h{{print $3}}' | tail -1); "
        f"if [ x$ip = x ]; then ip=$(timeout 8 avahi-browse -rtp _wican._tcp 2>/dev/null "
        f"| awk -F';' -v h=wican_{device_id}.local 'length($1)==1 && $7==h && $8 !~ /^192.168.0./ {{print $8}}' "
        f"| head -1); fi; echo DUT_IP=$ip")
    try:
        out = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8", pi, remote],
                             capture_output=True, text=True, timeout=timeout).stdout
    except Exception as e:
        return f"(ssh {pi} failed: {e})"
    m = re.search(r"DUT_IP=(\d+\.\d+\.\d+\.\d+)", out)
    return m.group(1) if m else ""


# ---------------------------------------------------------------- the record
def detect(probe: bool = False, probe_simulator: bool = False) -> dict:
    res = classify(enumerate_ports())
    if probe:
        probe_psu(res)
    if probe_simulator:
        probe_sim(res)
    res["generated"] = time.strftime("%Y-%m-%d %H:%M:%S")
    res["host"] = platform.node()
    return res


def load_record() -> dict | None:
    try:
        return json.loads(RECORD.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def write_record(res: dict) -> None:
    RECORD.write_text(json.dumps(res, indent=2) + "\n", encoding="utf-8")


def table(res: dict) -> str:
    lines = [f"bench serial ports ({res['generated']}, {res['host']})"]
    for role in ROLE_ORDER:
        r = res["roles"].get(role)
        if r:
            extra = ROLE_NOTE.get(role, "")
            if role == "psu":
                extra += ("; confirmed by *IDN?: " + r["idn"][:32]) if r.get("confirmed") \
                    else ("; by name only, --probe-psu confirms" if not r.get("idn") else f"; *IDN? said {r['idn'][:40]!r}")
            if role == "ecu_sim_console":
                extra += "; confirmed by device id" if r.get("confirmed") \
                    else ("; " + r["note"] if r.get("note") else "; by name only, --probe-sim confirms")
            lines.append(f"  {role:<16} {r['port']:<7} {r['how']:<24} {extra}")
        else:
            why = ABSENT_NOTE.get(role, "")
            if role == "psu" and len(res["candidates"]["psu"]) > 1:
                why = "ambiguous: " + ", ".join(c["port"] for c in res["candidates"]["psu"]) + " are FTDI; run --probe-psu"
            if role == "ecu_sim_console" and len(res["candidates"]["ecu_sim_console"]) > 1:
                why = "ambiguous: " + ", ".join(c["port"] for c in res["candidates"]["ecu_sim_console"]) + "; run --probe-sim"
            lines.append(f"  {role:<16} {'-':<7} absent: {why}")
    if res["spare"]:
        lines.append("  spare            " + ", ".join(f"{s['port']} ({s['how']})" for s in res["spare"]))
    for u in res["unclassified"]:
        lines.append(f"  unclassified     {u['port']:<7} {u['desc']}")
    if res["ignored"]:
        lines.append(f"  ignored          {len(res['ignored'])} Bluetooth port(s)")
    if res.get("ecu_sim"):
        s = res["ecu_sim"]
        lines.append(f"  ecu_sim          {s['url']}  " + (f"answers: device {s['device_id']} fw {s['fw']}"
                                                            if s["answers"] else "NO ANSWER (USB network link down?)"))
    if res.get("dut_ip") is not None:
        lines.append(f"  dut_ip           {res['dut_ip'] or '(not resolved)'}")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--json", action="store_true", help="print the record as JSON (nothing else)")
    ap.add_argument("--role", help="print only this role's port name; exit 1 when absent")
    ap.add_argument("--probe-psu", action="store_true", help="confirm a legacy OWON by *IDN? (opens FTDI ports only)")
    ap.add_argument("--probe-sim", action="store_true",
                    help="confirm ECU sim v2's console port by its device id over the simulator's REST (no port opened)")
    ap.add_argument("--dut-ip", action="store_true", help="also resolve the WiCAN's hotspot address via the Pi")
    ap.add_argument("--pi", default="rpi001", help="bench Pi ssh alias for --dut-ip")
    ap.add_argument("--device-id", default="68ee8f5a653d", help="WiCAN device id for --dut-ip")
    ap.add_argument("--no-write", action="store_true", help="do not refresh bench_ports.json")
    args = ap.parse_args()

    res = detect(probe=args.probe_psu, probe_simulator=args.probe_sim)
    if not args.probe_psu:
        # 2026-10-31: a refresh without the probe (the runner's preflight does
        # one before every stage) used to drop the PSU pin a --probe-psu run
        # had confirmed, and with two FTDI ports on the PC (COM50, COM407)
        # the role went ambiguous again: keep a confirmed pin whose port is
        # still among the candidates
        rec = load_record()
        pinned = (rec or {}).get("roles", {}).get("psu")
        if pinned and pinned.get("confirmed") and "psu" not in res["roles"] and                 any(c["port"] == pinned["port"] for c in res["candidates"].get("psu", [])):
            res["roles"]["psu"] = pinned
    if args.dut_ip:
        res["dut_ip"] = resolve_dut_ip(args.pi, args.device_id)
    if not args.no_write:
        try:
            write_record(res)
        except OSError as e:
            print(f"detect_ports: could not write {RECORD}: {e}", file=sys.stderr)

    if args.role:
        r = res["roles"].get(args.role)
        if not r:
            print(f"detect_ports: no port for role {args.role!r}", file=sys.stderr)
            return 1
        print(r["port"])
        return 0
    if args.json:
        print(json.dumps(res, indent=2))
    else:
        print(table(res))
        if not args.no_write:
            print(f"  (record: {RECORD})")
    return 0 if "wican_console" in res["roles"] else 1


if __name__ == "__main__":
    sys.exit(main())
