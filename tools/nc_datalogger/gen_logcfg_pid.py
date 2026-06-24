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

NOTE (poll_log validation): those same six channels are listed ONLY under POLLED,
as standard mode-01 PIDs. The broadcast can_filters are NOT emitted by default
(EMIT_BROADCAST_FILTERS = False) because poll_log (the live path) does request/
response only and never decodes broadcast frames -- emitting them would just add
empty "RPM [CANFLT]" duplicate columns beside the polled "RPM [PID]" ones. The
polled copies keep RPM/VSS/ECT/IAT/TPS/APP populated and are ground-truthable
(tach, temp gauge), so they're the cleanest way to confirm polled values. Flip
EMIT_BROADCAST_FILTERS back on to re-stage the broadcast set for hybrid/fast_log
(passive, ~100 Hz, no bus traffic) once polled values are confirmed.

Run: python gen_logcfg_pid.py > logcfg_auto_pid.json
"""

import json
import sys

# ---- "Best grid rate without clipping" model (Tactrix-style guesstimate) ----------------
# poll_log polls one PID at a time, round-robin. The aggregate request rate is RTT-bound
# (~1000/avg_rtt req/s), so each of the N polled channels is refreshed aggregate/N times per
# second. The wide-CSV fixed grid should sit AT that per-channel rate: set it higher and you
# only log held repeats (oversampling -> the stair-step "clipping"); set it lower and you throw
# real samples away. AVG_RTT_MS is measured live on the NC PCM (GET /poll_status showed
# rtt_avg ~2.5 ms, 396 req/s, 0 timeouts). recommend_grid_hz() prints the suggested
# device_config.csv_grid_hz for the current POLLED set every time this file is generated.
AVG_RTT_MS  = 2.5     # measured per-request round-trip on the NC PCM via poll_log
GRID_SAFETY = 0.95    # sit just under the per-channel rate so each grid row lands a fresh sample
GRID_HZ_MAX = 50      # firmware clamps csv_grid_hz to 1..50


def recommend_grid_hz(num_pids):
    """Best fixed-grid Hz for num_pids polled channels = per-channel poll rate, floored to 1..50."""
    budget_req_s = 1000.0 / AVG_RTT_MS                 # achievable aggregate poll rate
    per_channel_hz = budget_req_s / max(1, num_pids)
    return max(1, min(GRID_HZ_MAX, int(per_channel_hz * GRID_SAFETY)))


# ---- BROADCAST channels: (frame_id, name, expression, unit, class) ----
# Period in ms applied uniformly below. Expressions/scaling proven in mx5_nc.json.
#
# EMIT_BROADCAST_FILTERS: OFF for poll-only validation. poll_log (the live path)
# does request/response only and never decodes broadcast frames, so emitting these
# filters would only add empty duplicate columns (RPM [CANFLT] etc.) next to the
# polled copies below. The six channels are covered as POLLED PIDs instead. Flip to
# True to re-stage the broadcast set for the hybrid/fast_log path (passive ~100 Hz,
# no bus traffic) once polled values are confirmed valid.
EMIT_BROADCAST_FILTERS = False
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


# ---- CALCULATED channels (Task #17): (name, expression, unit) ----
# Math over OTHER decoded channel NAMES (source "CALC"), evaluated once per poll sweep on-device.
# Operators: + - * / and parens, plus unary minus. Operands are channel names (bare identifiers,
# e.g. EQ_RATIO, MAP, BARO) or decimal numbers. A calc whose inputs aren't decoded yet stays empty
# (LOCF) until they are. These reference the POLLED channels above so they populate in poll_log.
CALCULATED = [
    # name      expression            unit    note
    ("AFR",    "EQ_RATIO * 14.64",   "AFR"),   # lambda -> gasoline air/fuel ratio (~14.6 stoich)
    ("BOOST",  "MAP - BARO",         "kPa"),   # manifold vs atmospheric (NA NC: <=0 == vacuum)
]


def build_calculated():
    return [
        {"name": name, "expression": expr, "unit": unit, "enabled": True}
        for name, expr, unit in CALCULATED
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
        "can_filters": build_can_filters() if EMIT_BROADCAST_FILTERS else [],
        "pids": build_pids(),
        "calculated": build_calculated(),
    }
    print(json.dumps(config, indent=4))

    # Grid-rate guesstimate -> stderr (keeps stdout pure JSON for redirection).
    n = len(POLLED)
    budget = 1000.0 / AVG_RTT_MS
    hz = recommend_grid_hz(n)
    print(
        f"[grid] {n} polled PIDs | ~{budget:.0f} req/s budget (@{AVG_RTT_MS} ms RTT) "
        f"| ~{budget / n:.1f} Hz/channel -> recommended csv_grid_hz = {hz}\n"
        f"[grid] set it in Logger Settings -> Grid Rate (device_config.csv_grid_hz, 1..{GRID_HZ_MAX}); "
        f"use grid_mode=fixed.",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()
