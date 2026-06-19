#!/usr/bin/env python3
"""Generate logcfg_auto_pid.json -- a curated NC tuning log for the WiCAN.

This is the "real" logging config (NOT the Phase-0 discovery dump). It mirrors
the EcuTek/OpenPort logcfg.txt channel set (logs/opconfig/logcfg.txt), with one
deliberate upgrade: any signal that the NC broadcasts live on the bus is read by
CAN-bus LISTENING (a can_filter / ATMA monitor) instead of being request/response
POLLED. Listening is passive, faster, and doesn't add bus traffic.

Two sources of data, both land in the SD wide-CSV as one column each:

  BROADCAST (can_filters, source "CANFLT", is_vehicle_specific=false so they run
  regardless of the car_specific toggle). Byte layout + scaling are the proven
  ones from vehicle_profiles/mazda/mx5_nc.json -- B0..B7 index the 8 raw CAN data
  bytes of the frame, [Bx:By] is big-endian.

  POLLED (pids, PID_CUSTOM, source "PID"). The "PID" string is the ELM request
  <mode><pid><nframes>; the trailing "1" tells the ELM to return after 1 frame.
  The Expression indexes the RESPONSE buffer, which begins with the ISO-TP PCI
  length byte -- so for a mode 01 reply [PCI,0x41,pidecho,A,B,...] the first data
  byte A is B3, B is B4; for a mode 22 reply [PCI,0x62,pidHi,pidLo,A,B,...] A is
  B4, B is B5. (Verified against mx5_nc.json's AAT: "01461" -> "B3-40".)

Channels covered by BROADCAST: RPM, VSS, APP, ECT, IAT (all in logcfg) plus TPS
(free bonus on the same 0x240 frame). car_specific is "disable" so the mx5_nc
profile's own 0x201/0x240 filters don't double up; ecu_protocol "6" + ATSP6 init
force ISO15765 11bit/500k.

NOTE (poll_log validation): those same six channels are ALSO listed under POLLED,
as standard mode-01 PIDs. The broadcast set still feeds the hybrid/fast_log modes,
but in poll_log (poll-only) mode the can_filters never fire, so the polled copies
keep RPM/VSS/ECT/IAT/TPS/APP populated -- and being ground-truthable (tach, temp
gauge) they're the cleanest way to confirm polled values are correct. The header
disambiguates the two as e.g. "RPM [PID]" vs "RPM [CANFLT]".

Run: python gen_logcfg_pid.py > logcfg_auto_pid.json
"""

import json

# ---- BROADCAST channels: (frame_id, name, expression, unit, class) ----
# Period in ms applied uniformly below. Expressions/scaling proven in mx5_nc.json.
BROADCAST_PERIOD_MS = 100
BROADCAST = [
    ("0x201", "RPM", "[B0:B1]/4",        "rpm",  "frequency"),
    ("0x201", "VSS", "([B4:B5]/100)-100", "km/h", "speed"),
    ("0x201", "APP", "B6/2",             "%",    "none"),
    ("0x240", "ECT", "B1-40",            "C",    "temperature"),
    ("0x240", "IAT", "B4-40",            "C",    "temperature"),
    ("0x240", "TPS", "B3/2.55",          "%",    "none"),
]

# ---- POLLED channels: (name, request, expression, unit, class) ----
# request = <mode><pid><nframes>.  mode 01 single byte -> B3 ; 16-bit -> [B3:B4]
#                                  mode 22 single byte -> B4 ; 16-bit -> [B4:B5]
POLLED_PERIOD_MS = 200
POLLED = [
    # name          request      expression                  unit       class
    ("MAP",        "010B1",     "B3",                       "kPa",     "none"),
    ("MAF",        "01101",     "[B3:B4]*0.01",             "g/s",     "none"),
    ("SPARKADV",   "010E1",     "(B3*0.5)-64",              "deg",     "none"),
    ("STFT",       "01061",     "(B3*0.78125)-100",         "%",       "none"),
    ("LTFT",       "01071",     "(B3*0.78125)-100",         "%",       "none"),
    ("EQ_RATIO",   "01341",     "[B3:B4]*0.0000305175",     "lambda",  "none"),
    ("FUELSYS",    "01031",     "[B3:B4]*0.00390625",       "",        "none"),
    ("BARO",       "01331",     "B3",                       "kPa",     "none"),
    ("LOAD",       "01431",     "[B3:B4]*0.0039215684",     "g/rev",   "none"),
    ("KNOCKR",     "2217461",   "B4*0.3521126761",          "deg",     "none"),
    ("FUEL_PW",    "2214101",   "[B4:B5]*0.008",            "ms",      "none"),
    ("VCT_ACT",    "2216CD1",   "[B4:B5]*0.0625",           "deg",     "none"),
    ("HIDET_SW",   "2217061",   "B4&1",                     "on/off",  "none"),
    # Polled mode-01 equivalents of the BROADCAST channels above. In poll_log (poll-only)
    # mode the can_filters never fire, so without these the RPM/VSS/ECT/IAT/TPS/APP columns
    # are empty. Adding them lets the poll-only run be validated against ground truth (tach,
    # temp gauge, etc.) and cross-checked against the broadcast values from a fast_log run.
    # These are standard OBD PIDs (0C/0D/05/0F/11) -- universally supported -- except APP:
    # 0x49 (accelerator pedal position D) may NRC/time out on the NC; if so, drop it.
    ("RPM",        "010C1",     "[B3:B4]/4",                "rpm",     "none"),
    ("VSS",        "010D1",     "B3",                       "km/h",    "none"),
    ("ECT",        "01051",     "B3-40",                    "C",       "none"),
    ("IAT",        "010F1",     "B3-40",                    "C",       "none"),
    ("TPS",        "01111",     "B3*0.39215686",            "%",       "none"),
    ("APP",        "01491",     "B3*0.39215686",            "%",       "none"),
]


def build_can_filters():
    by_frame = {}
    order = []
    for frame_id, name, expr, unit, cls in BROADCAST:
        if frame_id not in by_frame:
            by_frame[frame_id] = []
            order.append(frame_id)
        by_frame[frame_id].append({
            "name": name,
            "expression": expr,
            "unit": unit,
            "class": cls,
            "period": BROADCAST_PERIOD_MS,
        })
    return [{"frame_id": fid, "parameters": by_frame[fid]} for fid in order]


def build_pids():
    pids = []
    for name, request, expr, unit, cls in POLLED:
        entry = {
            "PID": request,
            "Period": str(POLLED_PERIOD_MS),
            "enabled": True,
            "Name": name,
            "Expression": expr,
            "unit": unit,
            "class": cls,
        }
        # Mazda enhanced PIDs (mode 0x22) must be addressed PHYSICALLY to the PCM
        # (ATSH7E0); functional 0x7DF does not elicit a mode-22 response on the NC.
        # Mode 01 PIDs use the default functional header, so they need no init.
        if request.startswith("22"):
            entry["Init"] = "ATSH7E0;"
        pids.append(entry)
    return pids


def main():
    config = {
        "car_model": "NC logcfg (broadcast + polled)",
        "ecu_protocol": "6",          # ISO15765 11-bit 500k (ATTP6 early)
        "initialisation": "ATSP6",    # force/persist protocol for custom polling
        "car_specific": "disable",    # don't double up mx5_nc's own 0x201/0x240 filters
        # Low-Voltage Behavior = "Pause Automate on low voltage (Sleep Voltage)":
        # pause ALL AutoPID (polling + CAN monitoring) below sleep_volt to protect
        # the battery when parked. (UI: Automate -> Low-Voltage Behavior.)
        "disable_on_sleep_voltage": "enable",
        "can_filters": build_can_filters(),
        "pids": build_pids(),
    }
    print(json.dumps(config, indent=4))


if __name__ == "__main__":
    main()
