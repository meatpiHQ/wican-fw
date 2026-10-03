#!/usr/bin/env python3
"""J1939 reference codec for the bench (pure python, no CAN import).

The PC-side truth the J1939 benches compare the DUT against: 29-bit id
pack/unpack, Request / ACKM / transport-protocol frames (J1939-21), DM1/DM2
(J1939-73, SPN conversion method version 4) and the encoders of the PGN set
the truck actor broadcasts (J1939-71 layouts, written from public sources;
`--selftest` cross-checks the ones that also exist in
fixtures/dbc/j1939_database.dbc against that file).

Used by actors/pcan_j1939_truck.py and by the J1939 benches (expected
values, decoding what the DUT transmits in active mode).

  python j1939_ref.py --selftest      -> J1939 REF SELFTEST PASS
"""
import os
import re
import sys

GLOBAL = 0xFF
NULL_ADDR = 0xFE

PGN_REQUEST = 0xEA00
PGN_ACKM = 0xE800
PGN_TP_CM = 0xEC00
PGN_TP_DT = 0xEB00
PGN_ADDRESS_CLAIMED = 0xEE00

PGN_EEC1 = 0xF004
PGN_EEC2 = 0xF003
PGN_ETC2 = 0xF005
PGN_CCVS1 = 0xFEF1
PGN_ET1 = 0xFEEE
PGN_EFL_P1 = 0xFEEF
PGN_IC1 = 0xFEF6
PGN_AMB = 0xFEF5
PGN_VEP1 = 0xFEF7
PGN_LFE1 = 0xFEF2
PGN_DD1 = 0xFEFC
PGN_VDHR = 0xFEC1
PGN_AT1T1I = 0xFE56
PGN_VD = 0xFEE0
PGN_HOURS = 0xFEE5
PGN_LFC = 0xFEE9
PGN_VI = 0xFEEC
PGN_CI = 0xFEEB
PGN_SOFT = 0xFEDA
PGN_DM1 = 0xFECA
PGN_DM2 = 0xFECB
PGN_DM3 = 0xFECC
PGN_DM11 = 0xFED3

TP_RTS, TP_CTS, TP_EOMA, TP_BAM, TP_ABORT = 16, 17, 19, 32, 255
TP_MAX = 1785

ACK, NACK, ACCESS_DENIED, CANNOT_RESPOND = 0, 1, 2, 3


# ---- ids ------------------------------------------------------------------

def is_pdu1(pgn):
    """PDU1 (destination-specific): PDU format byte below 240."""
    return ((pgn >> 8) & 0xFF) < 240


def can_id(prio, pgn, sa, da=GLOBAL):
    """29-bit id. For PDU1 PGNs the low PGN byte is the destination."""
    if is_pdu1(pgn):
        pgn = (pgn & 0x3FF00) | (da & 0xFF)
    return ((prio & 7) << 26) | ((pgn & 0x3FFFF) << 8) | (sa & 0xFF)


def parse_id(cid):
    """-> (prio, pgn, sa, da). PDU1: pgn low byte zeroed, da from the id;
    PDU2: da = 0xFF (broadcast by definition)."""
    prio = (cid >> 26) & 7
    sa = cid & 0xFF
    raw = (cid >> 8) & 0x3FFFF
    if is_pdu1(raw):
        return prio, raw & 0x3FF00, sa, raw & 0xFF
    return prio, raw, sa, GLOBAL


def pgn_le(pgn):
    return bytes([pgn & 0xFF, (pgn >> 8) & 0xFF, (pgn >> 16) & 0xFF])


def pgn_from_le(b):
    return b[0] | (b[1] << 8) | (b[2] << 16)


# ---- J1939-21: request, acknowledgment, transport protocol -----------------

def request(pgn):
    """Data of a Request (PGN 59904): the requested PGN, LSB first."""
    return pgn_le(pgn)


def ackm(control, pgn, addr=GLOBAL, group=0xFF):
    """Acknowledgment (PGN 59392) data: control, group function, FF FF,
    the address being acknowledged, the PGN."""
    return bytes([control, group, 0xFF, 0xFF, addr]) + pgn_le(pgn)


def tp_npackets(size):
    return (size + 6) // 7


def tp_rts(size, pgn, max_per_cts=0xFF):
    return bytes([TP_RTS, size & 0xFF, size >> 8, tp_npackets(size),
                  max_per_cts]) + pgn_le(pgn)


def tp_cts(count, next_seq, pgn):
    return bytes([TP_CTS, count, next_seq, 0xFF, 0xFF]) + pgn_le(pgn)


def tp_eoma(size, pgn):
    return bytes([TP_EOMA, size & 0xFF, size >> 8, tp_npackets(size),
                  0xFF]) + pgn_le(pgn)


def tp_bam(size, pgn):
    return bytes([TP_BAM, size & 0xFF, size >> 8, tp_npackets(size),
                  0xFF]) + pgn_le(pgn)


def tp_abort(reason, pgn):
    return bytes([TP_ABORT, reason, 0xFF, 0xFF, 0xFF]) + pgn_le(pgn)


def tp_packets(payload):
    """The TP.DT data fields: sequence number 1.., 7 bytes, FF padded."""
    out = []
    for i in range(0, len(payload), 7):
        chunk = payload[i:i + 7]
        out.append(bytes([i // 7 + 1]) + chunk + b"\xFF" * (7 - len(chunk)))
    return out


class TpReassembler:
    """Reference receiver for one (sa, da) connection. feed() TP.CM and
    TP.DT data; returns (pgn, payload) when a message completes, else None.
    `errors` collects protocol violations (the bench asserts it empty)."""

    def __init__(self):
        self.pgn = None
        self.size = 0
        self.npk = 0
        self.next = 1
        self.buf = b""
        self.errors = []

    def feed_cm(self, d):
        if d[0] in (TP_RTS, TP_BAM):
            self.size = d[1] | (d[2] << 8)
            self.npk = d[3]
            self.pgn = pgn_from_le(d[5:8])
            self.next = 1
            self.buf = b""
            if self.npk != tp_npackets(self.size):
                self.errors.append("packet count does not match the size")
        elif d[0] == TP_ABORT:
            self.pgn = None
        return None

    def feed_dt(self, d):
        if self.pgn is None:
            self.errors.append("TP.DT without a connection")
            return None
        if d[0] != self.next:
            self.errors.append(f"sequence {d[0]} expected {self.next}")
            self.pgn = None
            return None
        self.buf += bytes(d[1:8])
        self.next += 1
        if self.next > self.npk:
            done = (self.pgn, self.buf[:self.size])
            self.pgn = None
            return done
        return None


# ---- J1939-73: DM1 / DM2 ---------------------------------------------------

LAMP_OFF, LAMP_ON, LAMP_NA = 0, 1, 3


def dm_encode(lamps, dtcs):
    """DM1/DM2 payload. lamps = dict(mil, rsl, awl, pl) of 0/1/3;
    dtcs = [(spn, fmi, oc)], conversion method 0 (version 4). No DTC ->
    the 8-byte "no fault" record; one DTC -> 8 bytes; more -> multi-packet."""
    b1 = ((lamps.get("mil", 0) & 3) << 6) | ((lamps.get("rsl", 0) & 3) << 4) \
        | ((lamps.get("awl", 0) & 3) << 2) | (lamps.get("pl", 0) & 3)
    out = bytes([b1, 0xFF])
    if not dtcs:
        return out + bytes([0, 0, 0, 0, 0xFF, 0xFF])
    for spn, fmi, oc in dtcs:
        out += bytes([spn & 0xFF, (spn >> 8) & 0xFF,
                      (((spn >> 16) & 0x07) << 5) | (fmi & 0x1F), oc & 0x7F])
    if len(out) < 8:
        out += b"\xFF" * (8 - len(out))
    return out


def dm_decode(p):
    """-> (lamps, [(spn, fmi, oc)]). The all-zero record means no fault."""
    lamps = {"mil": (p[0] >> 6) & 3, "rsl": (p[0] >> 4) & 3,
             "awl": (p[0] >> 2) & 3, "pl": p[0] & 3}
    dtcs = []
    for i in range(2, len(p) - 3, 4):
        b = p[i:i + 4]
        if b[:2] == b"\xFF\xFF" and i + 4 >= len(p):
            break                       # trailing FF FF padding
        spn = b[0] | (b[1] << 8) | ((b[2] >> 5) << 16)
        fmi = b[2] & 0x1F
        oc = b[3] & 0x7F
        if spn == 0 and fmi == 0 and oc == 0:
            continue
        dtcs.append((spn, fmi, oc))
    return lamps, dtcs


def dtc_text(spn, fmi):
    """The firmware's code string for a J1939 DTC."""
    return f"SPN{spn}-{fmi}"


# ---- J1939-71: the truck's PGN set -----------------------------------------

DEFAULTS = {
    "rpm": 1500.0, "torque_pct": 40.0, "driver_torque_pct": 45.0,
    "pedal_pct": 32.0, "load_pct": 55.0, "speed_kmh": 82.5,
    "coolant_c": 86.0, "fuel_temp_c": 38.0, "oil_temp_c": 97.5,
    "fuel_press_kpa": 400.0, "oil_level_pct": 80.0, "oil_press_kpa": 360.0,
    "coolant_level_pct": 90.0, "boost_kpa": 180.0, "intake_temp_c": 45.0,
    "exhaust_temp_c": 420.0, "baro_kpa": 100.0, "ambient_c": 21.5,
    "inlet_temp_c": 25.0, "batt_v": 27.6, "charge_v": 28.0, "key_v": 27.4,
    "fuel_rate_lph": 23.5, "fuel_econ_kml": 3.5, "fuel_level_pct": 62.0,
    "hr_distance_km": 345678.9, "total_distance_km": 345678.875,
    "trip_distance_km": 412.5, "hours_h": 8123.45, "total_fuel_l": 123456.5,
    "trip_fuel_l": 150.0, "def_level_pct": 70.0, "def_temp_c": 30.0,
    "gear_current": 10, "gear_selected": 10,
}


def _u8(v, scale=1.0, offset=0.0):
    raw = int(round((v - offset) / scale))
    return bytes([max(0, min(0xFA, raw))])


def _u16(v, scale=1.0, offset=0.0):
    raw = max(0, min(0xFAFF, int(round((v - offset) / scale))))
    return bytes([raw & 0xFF, raw >> 8])


def _u32(v, scale=1.0, offset=0.0):
    raw = max(0, min(0xFAFFFFFF, int(round((v - offset) / scale))))
    return raw.to_bytes(4, "little")


NA8, NA16, NA32 = b"\xFF", b"\xFF\xFF", b"\xFF\xFF\xFF\xFF"


def encode_pgn(pgn, v):
    """The data field of @pgn for the values dict @v (unknown PGN -> None)."""
    if pgn == PGN_EEC1:
        return (b"\xF0" + _u8(v["driver_torque_pct"], 1, -125)
                + _u8(v["torque_pct"], 1, -125) + _u16(v["rpm"], 0.125)
                + NA8 + b"\xFF" + NA8)
    if pgn == PGN_EEC2:
        return (b"\xFF" + _u8(v["pedal_pct"], 0.4) + _u8(v["load_pct"])
                + NA8 * 5)
    if pgn == PGN_ETC2:
        return (_u8(v["gear_selected"], 1, -125) + NA16
                + _u8(v["gear_current"], 1, -125) + NA16 + NA16)
    if pgn == PGN_CCVS1:
        return b"\xFF" + _u16(v["speed_kmh"], 1 / 256) + NA8 * 5
    if pgn == PGN_ET1:
        return (_u8(v["coolant_c"], 1, -40) + _u8(v["fuel_temp_c"], 1, -40)
                + _u16(v["oil_temp_c"], 0.03125, -273) + NA16 + NA8 + NA8)
    if pgn == PGN_EFL_P1:
        return (_u8(v["fuel_press_kpa"], 4) + NA8
                + _u8(v["oil_level_pct"], 0.4) + _u8(v["oil_press_kpa"], 4)
                + NA16 + NA8 + _u8(v["coolant_level_pct"], 0.4))
    if pgn == PGN_IC1:
        return (NA8 + _u8(v["boost_kpa"], 2) + _u8(v["intake_temp_c"], 1, -40)
                + NA8 + NA8 + _u16(v["exhaust_temp_c"], 0.03125, -273) + NA8)
    if pgn == PGN_AMB:
        return (_u8(v["baro_kpa"], 0.5) + NA16
                + _u16(v["ambient_c"], 0.03125, -273)
                + _u8(v["inlet_temp_c"], 1, -40) + NA16)
    if pgn == PGN_VEP1:
        return (NA8 + NA8 + _u16(v["charge_v"], 0.05)
                + _u16(v["batt_v"], 0.05) + _u16(v["key_v"], 0.05))
    if pgn == PGN_LFE1:
        return (_u16(v["fuel_rate_lph"], 0.05)
                + _u16(v["fuel_econ_kml"], 1 / 512) + NA16 + NA8 + NA8)
    if pgn == PGN_DD1:
        return NA8 + _u8(v["fuel_level_pct"], 0.4) + NA8 * 6
    if pgn == PGN_VDHR:
        return _u32(v["hr_distance_km"], 0.005) + NA32
    if pgn == PGN_AT1T1I:
        return (_u8(v["def_level_pct"], 0.4) + _u8(v["def_temp_c"], 1, -40)
                + NA8 * 6)
    if pgn == PGN_VD:
        return (_u32(v["trip_distance_km"], 0.125)
                + _u32(v["total_distance_km"], 0.125))
    if pgn == PGN_HOURS:
        return _u32(v["hours_h"], 0.05) + NA32
    if pgn == PGN_LFC:
        return _u32(v["trip_fuel_l"], 0.5) + _u32(v["total_fuel_l"], 0.5)
    return None


# (name, pgn, priority, period ms; 0 = on request only)
PGN_SET = [
    ("EEC1", PGN_EEC1, 3, 20), ("EEC2", PGN_EEC2, 3, 50),
    ("ETC2", PGN_ETC2, 6, 100),
    ("CCVS1", PGN_CCVS1, 6, 100), ("LFE1", PGN_LFE1, 6, 100),
    ("EFL_P1", PGN_EFL_P1, 6, 500), ("IC1", PGN_IC1, 6, 500),
    ("ET1", PGN_ET1, 6, 1000), ("AMB", PGN_AMB, 6, 1000),
    ("VEP1", PGN_VEP1, 6, 1000), ("DD1", PGN_DD1, 6, 1000),
    ("VDHR", PGN_VDHR, 6, 1000), ("AT1T1I", PGN_AT1T1I, 6, 1000),
    ("VD", PGN_VD, 6, 0), ("HOURS", PGN_HOURS, 6, 0), ("LFC", PGN_LFC, 6, 0),
]

# What the firmware must decode from the defaults: (pgn, name, value, tol).
# Tolerance = one raw step of the SPN. The names are the firmware's
# (components/j1939/j1939_spn_core.c): SPN labels, none equal to a name of
# the OBD table.
def expected(v=None):
    v = dict(DEFAULTS, **(v or {}))
    return [
        (PGN_EEC1, "EngineSpeed", v["rpm"], 0.125),
        (PGN_EEC1, "ActualEnginePercentTorque", v["torque_pct"], 1),
        (PGN_EEC2, "AccelPedalPosition1", v["pedal_pct"], 0.4),
        (PGN_EEC2, "EnginePercentLoad", v["load_pct"], 1),
        (PGN_CCVS1, "WheelBasedVehicleSpeed", v["speed_kmh"], 1 / 256),
        (PGN_ETC2, "TransCurrentGear", v["gear_current"], 1),
        (PGN_ET1, "EngineCoolantTemperature", v["coolant_c"], 1),
        (PGN_ET1, "EngineOilTemperature", v["oil_temp_c"], 0.03125),
        (PGN_EFL_P1, "EngineOilPressure", v["oil_press_kpa"], 4),
        (PGN_IC1, "IntakeManifoldPressure", v["boost_kpa"], 2),
        (PGN_IC1, "IntakeManifoldTemperature", v["intake_temp_c"], 1),
        (PGN_AMB, "BarometricPressure", v["baro_kpa"], 0.5),
        (PGN_AMB, "AmbientAirTemperature", v["ambient_c"], 0.03125),
        (PGN_VEP1, "BatteryPotential", v["batt_v"], 0.05),
        (PGN_LFE1, "FuelRate", v["fuel_rate_lph"], 0.05),
        (PGN_DD1, "FuelLevel1", v["fuel_level_pct"], 0.4),
        (PGN_VDHR, "TotalVehicleDistanceHR", v["hr_distance_km"], 0.005),
        (PGN_AT1T1I, "DEFTankLevel", v["def_level_pct"], 0.4),
        (PGN_HOURS, "EngineTotalHours", v["hours_h"], 0.05),
        (PGN_LFC, "EngineTotalFuelUsed", v["total_fuel_l"], 0.5),
        (PGN_VD, "TotalVehicleDistance", v["total_distance_km"], 0.125),
    ]


def vin_payload(vin):
    """PGN 65260 data: the VIN, '*' terminated."""
    return vin.encode("ascii") + b"*"


def name_field(identity, manufacturer=0, function=0, arbitrary=False,
               industry=0, vehicle_system=0):
    """The 8-byte NAME of an address claim (J1939-81), LSB first."""
    n = (identity & 0x1FFFFF) | ((manufacturer & 0x7FF) << 21) \
        | ((function & 0xFF) << 40) | ((vehicle_system & 0x7F) << 49) \
        | ((industry & 0x7) << 60) | ((1 if arbitrary else 0) << 63)
    return n.to_bytes(8, "little")


# ---- selftest ---------------------------------------------------------------

def _dbc_signals(path):
    """{pgn: {signal: (start, length, factor, offset)}} for Intel signals."""
    out, cur = {}, None
    bo = re.compile(r"^BO_ (\d+) (\w+):")
    sg = re.compile(r"^ SG_ (\w+) : (\d+)\|(\d+)@1[+-] \(([-\d.eE]+),"
                    r"([-\d.eE]+)\)")
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            m = bo.match(line)
            if m:
                cur = parse_id(int(m.group(1)) & 0x1FFFFFFF)[1]
                out.setdefault(cur, {})
                continue
            m = sg.match(line)
            if m and cur is not None:
                out[cur][m.group(1)] = (int(m.group(2)), int(m.group(3)),
                                        float(m.group(4)), float(m.group(5)))
    return out


def _dbc_decode(data, start, length, factor, offset):
    raw = (int.from_bytes(data, "little") >> start) & ((1 << length) - 1)
    return raw * factor + offset


def selftest():
    fails = []

    def check(name, cond):
        if not cond:
            fails.append(name)

    # ids: EEC1 from the engine, a request to the engine, the global request
    check("id eec1", can_id(3, PGN_EEC1, 0x00) == 0x0CF00400)
    check("id request", can_id(6, PGN_REQUEST, 0xF9, 0x00) == 0x18EA00F9)
    check("id request global", can_id(6, PGN_REQUEST, 0xF9) == 0x18EAFFF9)
    check("parse pdu2", parse_id(0x18FEF100) == (6, PGN_CCVS1, 0x00, 0xFF))
    check("parse pdu1", parse_id(0x1CECFF00) == (7, PGN_TP_CM, 0x00, 0xFF))
    check("parse pdu1 da", parse_id(0x18EA00F9) == (6, PGN_REQUEST, 0xF9, 0))

    # J1939-21: a request for the VIN is EC FE 00
    check("request", request(PGN_VI) == bytes([0xEC, 0xFE, 0x00]))
    # BAM for an 18-byte VIN message: 3 packets
    check("bam", tp_bam(18, PGN_VI) ==
          bytes([0x20, 0x12, 0x00, 0x03, 0xFF, 0xEC, 0xFE, 0x00]))
    check("rts", tp_rts(18, PGN_VI) ==
          bytes([0x10, 0x12, 0x00, 0x03, 0xFF, 0xEC, 0xFE, 0x00]))
    check("cts", tp_cts(3, 1, PGN_VI) ==
          bytes([0x11, 0x03, 0x01, 0xFF, 0xFF, 0xEC, 0xFE, 0x00]))
    check("eoma", tp_eoma(18, PGN_VI) ==
          bytes([0x13, 0x12, 0x00, 0x03, 0xFF, 0xEC, 0xFE, 0x00]))
    check("nack", ackm(NACK, PGN_HOURS, 0xF9) ==
          bytes([0x01, 0xFF, 0xFF, 0xFF, 0xF9, 0xE5, 0xFE, 0x00]))

    vin = vin_payload("1WCANJ1939TRUCK01")
    pk = tp_packets(vin)
    check("packets", len(pk) == 3 and pk[0][0] == 1 and pk[2][0] == 3
          and pk[2][5:] == b"\xFF\xFF\xFF")
    r = TpReassembler()
    r.feed_cm(tp_bam(len(vin), PGN_VI))
    done = None
    for p in pk:
        done = r.feed_dt(p)
    check("reassembly", done == (PGN_VI, vin) and not r.errors)
    r.feed_cm(tp_bam(len(vin), PGN_VI))
    r.feed_dt(pk[0])
    r.feed_dt(pk[2])
    check("reassembly gap", r.errors and r.pgn is None)

    # J1939-73: SPN 110 FMI 0 OC 5, MIL on -> 40 FF 6E 00 00 05 FF FF
    one = dm_encode({"mil": 1}, [(110, 0, 5)])
    check("dm1 one", one == bytes([0x40, 0xFF, 0x6E, 0x00, 0x00, 0x05,
                                   0xFF, 0xFF]))
    # SPN 520192 (0x7F000) needs the three high bits: byte 3 = 111 fffff
    big = dm_encode({"awl": 1}, [(520192, 31, 126), (3226, 4, 1)])
    check("dm1 two", big == bytes([0x04, 0xFF, 0x00, 0xF0, 0xFF, 0x7E,
                                   0x9A, 0x0C, 0x04, 0x01]))
    check("dm1 decode", dm_decode(big) ==
          ({"mil": 0, "rsl": 0, "awl": 1, "pl": 0},
           [(520192, 31, 126), (3226, 4, 1)]))
    none = dm_encode({}, [])
    check("dm1 none", none == bytes([0, 0xFF, 0, 0, 0, 0, 0xFF, 0xFF])
          and dm_decode(none)[1] == [])
    check("dm1 one decode", dm_decode(one)[1] == [(110, 0, 5)])

    # J1939-71 against the DBC fixture (an independent definition)
    v = dict(DEFAULTS)
    eec1 = encode_pgn(PGN_EEC1, v)
    check("eec1 bytes", eec1[3:5] == bytes([0xE0, 0x2E]))   # 1500 rpm
    dbc_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "..", "fixtures", "dbc", "j1939_database.dbc")
    crossed = 0
    if os.path.exists(dbc_path):
        dbc = _dbc_signals(dbc_path)
        for pgn, sig, key, tol in [
                (PGN_EEC1, "EngineSpeed", "rpm", 0.125),
                (PGN_EEC1, "ActualEnginePercentTorque", "torque_pct", 1),
                (PGN_EEC1, "DriversDemandEngineTorque", "driver_torque_pct", 1),
                (PGN_EEC2, "AccelPedalPosition1", "pedal_pct", 0.4),
                (PGN_CCVS1, "WheelBasedVehicleSpeed", "speed_kmh", 1 / 256),
                (PGN_LFC, "EngineTotalFuelUsed", "total_fuel_l", 0.5),
                (PGN_LFC, "EngineTripFuel", "trip_fuel_l", 0.5),
                (PGN_EFL_P1, "EngineFuelDeliveryPressure", "fuel_press_kpa", 4),
                (PGN_EFL_P1, "EngineOilLevel", "oil_level_pct", 0.4),
                (PGN_EFL_P1, "EngineOilPressure", "oil_press_kpa", 4),
                (PGN_EFL_P1, "EngineCoolantLevel", "coolant_level_pct", 0.4),
                (PGN_AMB, "BarometricPressure", "baro_kpa", 0.5),
                (PGN_AMB, "AmbientAirTemperature", "ambient_c", 0.03125),
                (PGN_AMB, "EngineAirInletTemperature", "inlet_temp_c", 1)]:
            spec = dbc.get(pgn, {}).get(sig)
            if spec is None:
                fails.append(f"dbc has no {sig}")
                continue
            got = _dbc_decode(encode_pgn(pgn, v), *spec)
            check(f"dbc {sig} {got} vs {v[key]}", abs(got - v[key]) <= tol)
            crossed += 1
    else:
        fails.append("fixture DBC not found")

    # not-available stays not-available, every PGN is 8 bytes
    for _name, pgn, _prio, _per in PGN_SET:
        check(f"len {pgn:04X}", len(encode_pgn(pgn, v)) == 8)

    if fails:
        print("J1939 REF SELFTEST FAIL: " + "; ".join(fails))
        return 1
    print(f"J1939 REF SELFTEST PASS ({crossed} signals cross-checked "
          "against the DBC fixture)")
    return 0


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(selftest())
    print(__doc__)
