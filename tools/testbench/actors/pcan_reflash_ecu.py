#!/usr/bin/env python3
"""UDS reprogramming (ECU-flash) simulator on PCAN — for exercising the
J2534 PassThru device against realistic reflash flows AND their failure
modes.

Models the ISO 14229 reprogramming sequence a real OEM tool drives:

  10 02/03  diagnostic/programming session
  85 02     control DTC setting off            (optional)
  28 03 01  communication control (disable)    (optional)
  27 01/02  security access: seed then key
  34        request download   (fmt, addr, size)
  36 <bsc>  transfer data      (block sequence counter + payload) × N
  37        request transfer exit
  31 01 rid routine control    (erase / check-deps / verify)
  11 01     ECU reset
  3E 80     tester present (suppressed) throughout

Runs on PCAN_USBBUS2 @ 500k, 11-bit, req 0x7E0 / resp 0x7E8 (matches the
other bench ECUs). Pick a failure to inject with --scenario; the UDS
state machine is a pure class so `--selftest` validates every scenario
with an in-process loopback (no PCAN needed).

  pcan_reflash_ecu.py [duration_s] --scenario happy
  pcan_reflash_ecu.py --selftest            # runs all scenarios, no HW

Expected self-test line: REFLASH ECU SELFTEST PASS
"""
import argparse
import sys
import time

# ---- UDS constants --------------------------------------------------------
SID_SESSION      = 0x10
SID_RESET        = 0x11
SID_SECURITY     = 0x27
SID_COMM_CTRL    = 0x28
SID_TESTER_PRES  = 0x3E
SID_DTC_SETTING  = 0x85
SID_REQ_DOWNLOAD = 0x34
SID_TRANSFER     = 0x36
SID_XFER_EXIT    = 0x37
SID_ROUTINE      = 0x31

NRC_GENERAL_REJECT           = 0x10
NRC_SERVICE_NOT_SUPPORTED    = 0x11
NRC_SUBFUNC_NOT_SUPPORTED    = 0x12
NRC_INCORRECT_LEN            = 0x13
NRC_CONDITIONS_NOT_CORRECT   = 0x22
NRC_REQUEST_SEQUENCE_ERROR   = 0x24
NRC_REQUEST_OUT_OF_RANGE     = 0x31
NRC_SECURITY_ACCESS_DENIED   = 0x33
NRC_INVALID_KEY              = 0x35
NRC_EXCEED_ATTEMPTS          = 0x36
NRC_TIME_DELAY_NOT_EXPIRED   = 0x37
NRC_UPLOAD_DOWNLOAD_DENIED   = 0x70
NRC_TRANSFER_DATA_SUSPENDED  = 0x71
NRC_GENERAL_PROGRAMMING_FAIL = 0x72
NRC_WRONG_BLOCK_SEQ_COUNTER  = 0x73
NRC_RESPONSE_PENDING         = 0x78
NRC_SERVICE_NOT_IN_SESSION   = 0x7F

SESSION_PROGRAMMING = 0x02
SESSION_EXTENDED    = 0x03

# Sentinel a handler yields to mean "send this negative, then keep working"
PENDING = NRC_RESPONSE_PENDING

SCENARIOS = {
    "happy":            "clean reflash, no failures",
    "invalid_key":      "27 02 -> NRC 0x35 invalidKey",
    "security_lockout": "3 bad keys -> 0x36 exceedAttempts, then 0x37 delay",
    "seq_before_dl":    "36 before 34 -> 0x24 requestSequenceError",
    "wrong_bsc":        "36 with wrong block counter -> 0x73",
    "download_oor":     "34 bad addr/size -> 0x31 requestOutOfRange",
    "prog_fail":        "36 mid-transfer -> 0x72 generalProgrammingFailure",
    "conditions":       "34 in wrong session/voltage -> 0x22 conditionsNotCorrect",
    "erase_pending":    "erase routine floods 0x78 pending (P2* stress)",
    "verify_fail":      "final checkMemory routine returns FAIL status",
    "silent_mid_xfer":  "ECU goes silent after 2 blocks (client timeout)",
}


class ReflashEcu:
    """Pure UDS reprogramming state machine. handle() returns a list of
    response byte-strings (usually one; several when emitting 0x78
    pending frames before the final answer); an empty list means the ECU
    stays silent (timeout injection)."""

    SEED = bytes([0x11, 0x22, 0x33, 0x44])
    GOOD_KEY = bytes([0xEE, 0xDD, 0xCC, 0xBB])  # seed XOR 0xFF, toy KDF
    TOTAL_BYTES = 0x400                          # pretend 1 KiB image

    def __init__(self, scenario="happy", erase_pending_count=8):
        self.scn = scenario
        self.erase_pending_count = erase_pending_count
        self.reset()

    def reset(self):
        self.session = 0x01
        self.security_unlocked = False
        self.seed_issued = False
        self.bad_keys = 0
        self.locked = False
        self.download_active = False
        self.next_bsc = 0x01
        self.received = 0
        self.blocks = 0

    # -- helpers
    @staticmethod
    def _pos(sid, *rest):
        return bytes([sid + 0x40, *rest])

    @staticmethod
    def _neg(sid, nrc):
        return bytes([0x7F, sid, nrc])

    def handle(self, req):
        if not req:
            return []
        sid = req[0]

        if sid == SID_TESTER_PRES:
            # 3E 80 suppressed positive response
            return [] if len(req) > 1 and req[1] & 0x80 else \
                [self._pos(sid, 0x00)]

        if sid == SID_SESSION:
            return self._session(req)
        if sid == SID_DTC_SETTING:
            return [self._pos(sid, req[1] if len(req) > 1 else 0)]
        if sid == SID_COMM_CTRL:
            return [self._pos(sid, req[1] if len(req) > 1 else 0)]
        if sid == SID_SECURITY:
            return self._security(req)
        if sid == SID_REQ_DOWNLOAD:
            return self._req_download(req)
        if sid == SID_TRANSFER:
            return self._transfer(req)
        if sid == SID_XFER_EXIT:
            return self._xfer_exit(req)
        if sid == SID_ROUTINE:
            return self._routine(req)
        if sid == SID_RESET:
            self.reset()
            return [self._pos(sid, req[1] if len(req) > 1 else 0x01)]
        return [self._neg(sid, NRC_SERVICE_NOT_SUPPORTED)]

    def _session(self, req):
        sub = req[1] if len(req) > 1 else 0
        self.session = sub
        # 50 <sub> P2(2) P2*(2)
        return [self._pos(SID_SESSION, sub, 0x00, 0x32, 0x01, 0xF4)]

    def _security(self, req):
        sub = req[1] if len(req) > 1 else 0
        if self.locked:
            return [self._neg(SID_SECURITY, NRC_TIME_DELAY_NOT_EXPIRED)]
        if sub == 0x01:            # request seed
            self.seed_issued = True
            return [self._pos(SID_SECURITY, 0x01, *self.SEED)]
        if sub == 0x02:            # send key
            if not self.seed_issued:
                return [self._neg(SID_SECURITY, NRC_REQUEST_SEQUENCE_ERROR)]
            key = bytes(req[2:])
            good = (key == self.GOOD_KEY) and self.scn != "invalid_key"
            if good and self.scn != "security_lockout":
                self.security_unlocked = True
                self.bad_keys = 0
                return [self._pos(SID_SECURITY, 0x02)]
            # wrong key path
            self.bad_keys += 1
            if self.scn == "security_lockout" and self.bad_keys >= 3:
                self.locked = True
                return [self._neg(SID_SECURITY, NRC_EXCEED_ATTEMPTS)]
            return [self._neg(SID_SECURITY, NRC_INVALID_KEY)]
        return [self._neg(SID_SECURITY, NRC_SUBFUNC_NOT_SUPPORTED)]

    def _req_download(self, req):
        if self.scn == "conditions":
            return [self._neg(SID_REQ_DOWNLOAD, NRC_CONDITIONS_NOT_CORRECT)]
        if not self.security_unlocked:
            return [self._neg(SID_REQ_DOWNLOAD, NRC_SECURITY_ACCESS_DENIED)]
        if self.scn == "download_oor":
            return [self._neg(SID_REQ_DOWNLOAD, NRC_REQUEST_OUT_OF_RANGE)]
        # 34 dataFormatId ALFID addr... size...  (we don't validate deeply)
        if len(req) < 4:
            return [self._neg(SID_REQ_DOWNLOAD, NRC_INCORRECT_LEN)]
        self.download_active = True
        self.next_bsc = 0x01
        self.received = 0
        self.blocks = 0
        # 74 lengthFormatId maxBlockLength(2) -> 0x0102 = 258 bytes/block
        return [self._pos(SID_REQ_DOWNLOAD, 0x20, 0x01, 0x02)]

    def _transfer(self, req):
        if not self.download_active:
            return [self._neg(SID_TRANSFER, NRC_REQUEST_SEQUENCE_ERROR)]
        bsc = req[1] if len(req) > 1 else 0
        if self.scn == "wrong_bsc" and self.blocks == 1:
            return [self._neg(SID_TRANSFER, NRC_WRONG_BLOCK_SEQ_COUNTER)]
        if bsc != self.next_bsc:
            return [self._neg(SID_TRANSFER, NRC_WRONG_BLOCK_SEQ_COUNTER)]
        if self.scn == "prog_fail" and self.blocks == 1:
            return [self._neg(SID_TRANSFER, NRC_GENERAL_PROGRAMMING_FAIL)]
        if self.scn == "silent_mid_xfer" and self.blocks >= 2:
            return []   # ECU goes dark; client must time out
        self.received += len(req) - 2
        self.blocks += 1
        self.next_bsc = (self.next_bsc + 1) & 0xFF
        if self.next_bsc == 0x00:
            self.next_bsc = 0x01
        return [self._pos(SID_TRANSFER, bsc)]

    def _xfer_exit(self, req):
        if not self.download_active:
            return [self._neg(SID_XFER_EXIT, NRC_REQUEST_SEQUENCE_ERROR)]
        self.download_active = False
        return [self._pos(SID_XFER_EXIT)]

    def _routine(self, req):
        sub = req[1] if len(req) > 1 else 0
        rid = (req[2] << 8 | req[3]) if len(req) >= 4 else 0
        # 0xFF00 = eraseMemory, 0x0202 = checkProgrammingDependencies/verify
        if rid == 0xFF00 and self.scn == "erase_pending":
            pend = [self._neg(SID_ROUTINE, PENDING)] * self.erase_pending_count
            return pend + [self._pos(SID_ROUTINE, sub, 0xFF, 0x00, 0x00)]
        if rid == 0x0202 and self.scn == "verify_fail":
            # routineStatusRecord byte != 0 -> verification failed
            return [self._pos(SID_ROUTINE, sub, 0x02, 0x02, 0x01)]
        return [self._pos(SID_ROUTINE, sub, (rid >> 8) & 0xFF, rid & 0xFF,
                          0x00)]


# ---- self-test (no hardware) ---------------------------------------------

def _run_sequence(ecu, reqs):
    """Feed a list of requests; return the flat list of (req, responses)."""
    out = []
    for r in reqs:
        out.append((bytes(r), ecu.handle(bytes(r))))
    return out


def selftest():
    fails = []

    def expect(name, cond, detail=""):
        print(("PASS" if cond else "FAIL") + f": {name}" +
              (f" ({detail})" if detail else ""))
        if not cond:
            fails.append(name)

    # happy path: session -> seed/key -> download -> 3 blocks -> exit -> verify
    e = ReflashEcu("happy")
    seq = _run_sequence(e, [
        [0x10, 0x02], [0x27, 0x01], [0x27, 0x02, *ReflashEcu.GOOD_KEY],
        [0x34, 0x00, 0x44, 0x00, 0x00, 0x04, 0x00],
        [0x36, 0x01, 0xAA], [0x36, 0x02, 0xBB], [0x36, 0x03, 0xCC],
        [0x37], [0x31, 0x01, 0x02, 0x02],
    ])
    ok = all(r[0] != 0x7F for _, resps in seq for r in resps)
    expect("happy path all-positive", ok)
    expect("happy security unlocked", e.security_unlocked)
    expect("happy 3 blocks received", e.blocks == 3, f"blocks={e.blocks}")

    # invalid key
    e = ReflashEcu("invalid_key")
    e.handle(b"\x27\x01")
    r = e.handle(bytes([0x27, 0x02, *ReflashEcu.GOOD_KEY]))
    expect("invalid_key -> 0x35",
           r == [bytes([0x7F, 0x27, NRC_INVALID_KEY])])

    # security lockout after 3 bad keys
    e = ReflashEcu("security_lockout")
    e.handle(b"\x27\x01")
    last = None
    for _ in range(3):
        last = e.handle(b"\x27\x02\x00\x00\x00\x00")
    expect("lockout -> 0x36 exceedAttempts",
           last == [bytes([0x7F, 0x27, NRC_EXCEED_ATTEMPTS])])
    after = e.handle(b"\x27\x01")
    expect("locked -> 0x37 timeDelay",
           after == [bytes([0x7F, 0x27, NRC_TIME_DELAY_NOT_EXPIRED])])

    # transfer before download
    e = ReflashEcu("seq_before_dl")
    r = e.handle(b"\x36\x01\xAA")
    expect("36 before 34 -> 0x24",
           r == [bytes([0x7F, 0x36, NRC_REQUEST_SEQUENCE_ERROR])])

    # download out of range
    e = ReflashEcu("download_oor")
    e.handle(b"\x10\x02"); e.handle(b"\x27\x01")
    e.handle(bytes([0x27, 0x02, *ReflashEcu.GOOD_KEY]))
    r = e.handle(b"\x34\x00\x44\x00\x00\x04\x00")
    expect("download_oor -> 0x31",
           r == [bytes([0x7F, 0x34, NRC_REQUEST_OUT_OF_RANGE])])

    # conditions not correct (no security needed to reach it)
    e = ReflashEcu("conditions")
    r = e.handle(b"\x34\x00\x44\x00\x00\x04\x00")
    expect("conditions -> 0x22",
           r == [bytes([0x7F, 0x34, NRC_CONDITIONS_NOT_CORRECT])])

    # wrong block sequence counter mid-transfer
    e = ReflashEcu("wrong_bsc")
    e.handle(b"\x10\x02"); e.handle(b"\x27\x01")
    e.handle(bytes([0x27, 0x02, *ReflashEcu.GOOD_KEY]))
    e.handle(b"\x34\x00\x44\x00\x00\x04\x00")
    e.handle(b"\x36\x01\xAA")
    r = e.handle(b"\x36\x02\xBB")
    expect("wrong_bsc -> 0x73",
           r == [bytes([0x7F, 0x36, NRC_WRONG_BLOCK_SEQ_COUNTER])])

    # programming failure mid-transfer
    e = ReflashEcu("prog_fail")
    e.handle(b"\x10\x02"); e.handle(b"\x27\x01")
    e.handle(bytes([0x27, 0x02, *ReflashEcu.GOOD_KEY]))
    e.handle(b"\x34\x00\x44\x00\x00\x04\x00")
    e.handle(b"\x36\x01\xAA")
    r = e.handle(b"\x36\x02\xBB")
    expect("prog_fail -> 0x72",
           r == [bytes([0x7F, 0x36, NRC_GENERAL_PROGRAMMING_FAIL])])

    # erase pending storm
    e = ReflashEcu("erase_pending", erase_pending_count=8)
    r = e.handle(b"\x31\x01\xFF\x00")
    pend = sum(1 for x in r if x[:1] == b"\x7F" and x[2] == PENDING)
    expect("erase_pending 8×0x78 then positive",
           pend == 8 and r[-1][0] == 0x71, f"pending={pend}")

    # verify fail
    e = ReflashEcu("verify_fail")
    r = e.handle(b"\x31\x01\x02\x02")
    expect("verify_fail routine status != 0",
           r[0][0] == 0x71 and r[0][-1] == 0x01)

    # silent mid-transfer
    e = ReflashEcu("silent_mid_xfer")
    e.handle(b"\x10\x02"); e.handle(b"\x27\x01")
    e.handle(bytes([0x27, 0x02, *ReflashEcu.GOOD_KEY]))
    e.handle(b"\x34\x00\x44\x00\x00\x04\x00")
    e.handle(b"\x36\x01\xAA"); e.handle(b"\x36\x02\xBB")
    r = e.handle(b"\x36\x03\xCC")
    expect("silent_mid_xfer -> no response", r == [])

    if fails:
        print("REFLASH ECU SELFTEST FAIL:", ", ".join(fails))
        sys.exit(1)
    print("REFLASH ECU SELFTEST PASS")


# ---- PCAN runner (ISO-TP over the bus) -----------------------------------

def run_pcan(duration, scenario):
    import can

    REQ, RESP = 0x7E0, 0x7E8
    bus = can.Bus(interface="pcan", channel="PCAN_USBBUS2", bitrate=500000)
    ecu = ReflashEcu(scenario)
    print(f"Reflash ECU on PCAN_USBBUS2: req 0x{REQ:03X} -> 0x{RESP:03X}, "
          f"scenario '{scenario}'")

    def send_isotp(payload):
        n = len(payload)
        if n <= 7:
            bus.send(can.Message(arbitration_id=RESP, is_extended_id=False,
                                 data=bytes([n]) + payload))
            return
        ff = bytes([0x10 | ((n >> 8) & 0x0F), n & 0xFF]) + payload[:6]
        bus.send(can.Message(arbitration_id=RESP, is_extended_id=False,
                             data=ff))
        # wait for flow control
        t = time.time() + 1.0
        while time.time() < t:
            m = bus.recv(timeout=0.5)
            if m and m.arbitration_id == REQ and (m.data[0] & 0xF0) == 0x30:
                break
        rest, sn = payload[6:], 1
        while rest:
            cf = bytes([0x20 | (sn & 0x0F)]) + rest[:7]
            cf += bytes(8 - len(cf))
            bus.send(can.Message(arbitration_id=RESP, is_extended_id=False,
                                 data=cf))
            rest, sn = rest[7:], (sn + 1) & 0x0F
            time.sleep(0.001)

    def recv_isotp():
        m = bus.recv(timeout=0.5)
        if not m or m.arbitration_id != REQ:
            return None
        d = m.data
        pci = d[0] & 0xF0
        if pci == 0x00:
            return bytes(d[1:1 + (d[0] & 0x0F)])
        if pci == 0x10:
            left = ((d[0] & 0x0F) << 8) | d[1]
            buf = bytes(d[2:8]); left -= len(buf)
            bus.send(can.Message(arbitration_id=RESP, is_extended_id=False,
                                 data=bytes([0x30, 0x00, 0x00])))
            while left > 0:
                m = bus.recv(timeout=0.5)
                if not m or m.arbitration_id != REQ or (m.data[0] & 0xF0) != 0x20:
                    break
                take = min(7, left)
                buf += bytes(m.data[1:1 + take]); left -= take
            return buf
        return None

    end = time.time() + duration
    try:
        while time.time() < end:
            req = recv_isotp()
            if not req:
                continue
            for i, resp in enumerate(ecu.handle(req)):
                if resp[:1] == b"\x7F" and resp[2] == PENDING:
                    print(f"  {req.hex()} -> 0x78 pending")
                    send_isotp(resp)
                    time.sleep(0.05)
                    continue
                print(f"  {req.hex()} -> {resp.hex()}")
                send_isotp(resp)
    finally:
        bus.shutdown()


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("duration", nargs="?", type=float, default=600.0,
                    help="seconds to run on PCAN (default 600)")
    ap.add_argument("--scenario", default="happy", choices=list(SCENARIOS),
                    help="failure to inject; see --list")
    ap.add_argument("--selftest", action="store_true",
                    help="run the state machine against every scenario (no HW)")
    ap.add_argument("--list", action="store_true", help="list scenarios")
    args = ap.parse_args()

    if args.list:
        for k, v in SCENARIOS.items():
            print(f"  {k:18} {v}")
        return
    if args.selftest:
        selftest()
        return
    run_pcan(args.duration, args.scenario)


if __name__ == "__main__":
    main()
