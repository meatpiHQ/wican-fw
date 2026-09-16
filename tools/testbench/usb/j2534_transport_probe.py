"""J2534 transport probe  ->  J2534 TRANSPORT: raw CAN PASS|FAIL, ISO15765 bind PASS|FAIL

What the J2534 server can actually do on THIS build, proven on the bus:
  raw CAN   CONNECT(CAN), WRITE_MSGS a properly framed 7DF [02 01 00 ..],
            witness = the native `/api/can` tx counter stepping + the ECU's
            7E8 `06 41 00 ..` coming back as RX_MSG (collected from the
            instant of the send: the bench Client.call() drops RX_MSG that
            land before the ACK, and an ECU answers in milliseconds).
  ISO15765  CONNECT without ids (always accepted: nothing binds yet), then a
            FLOW_CONTROL filter (pattern 7E8, fc 7E0) = the moment the
            server has to open an ISO-TP session. Public builds have no
            `can_isotp()` provider: ERR_FAILED here and
            `E j2534_server: ISO15765 not available in this build`; with the
            add-on pack it binds and 10 02 answers 50 02.

usage (ON rpi001, next to j2534_bench.py):
  python3 j2534_transport_probe.py <dut_ip>
needs j2534_server.enabled=true and, from the Pi's STA side, allow_lan=true
(both reboot-to-apply; restore afterwards). Bench 2026-09-16, public build:
raw CAN PASS (tx 6 -> 7, `06 41 00 00 00 00 00 00`), ISO15765 bind FAIL.
"""
import json, struct, sys, time, urllib.request
DUT = sys.argv[1] if len(sys.argv) > 1 else "10.42.1.194"
START_FILTER = int(sys.argv[2], 0) if len(sys.argv) > 2 else 0x11  # J2534_MT_START_FILTER
sys.argv = [sys.argv[0], DUT]
import j2534_bench as b


def can_stats():
    with urllib.request.urlopen("http://%s/api/can" % DUT, timeout=5) as r:
        d = json.load(r)
    return d.get("tx"), d.get("tx_errors"), d.get("rx")


def write_and_collect(c, channel, payload, secs):
    """Send WRITE_MSGS and keep every RX_MSG from that instant: the bench
    Client's call() drops RX messages that land before the ACK, and the
    simulator answers within milliseconds."""
    c.seq = (c.seq + 1) & 0xFFFF
    c.s.sendall(b.HDR.pack(b.MAGIC, 1, b.WRITE_MSGS, c.seq, channel, len(payload)) + payload)
    c.s.settimeout(0.5)
    got, ack = [], None
    t0 = time.time()
    while time.time() - t0 < secs:
        try:
            rt, rseq, rch, body = c._read_frame()
        except Exception:
            break
        if rt == b.ACK:
            ack = struct.unpack_from("<I", body, 0)[0]
        elif rt == b.RX_MSG:
            size = b.MSG.unpack_from(body, 0)[5]
            d = body[b.MSG.size:b.MSG.size + size]
            got.append((struct.unpack(">I", d[:4])[0], d[4:].hex(" ").upper()))
    c.s.settimeout(5)
    return ack, got


c = b.Client(DUT, 6809)
print("HELLO", hex(c.call(b.HELLO)[0]), "OPEN", hex(c.call(b.OPEN)[0]))
st, ch, _ = c.call(b.CONNECT, payload=struct.pack("<III", b.PROT_CAN, 0, 500000))
print("CONNECT CAN ->", hex(st), "channel", ch)
raw_ok = False
for req in (bytes([0x02, 0x01, 0x00, 0, 0, 0, 0, 0]),):
    tx0 = can_stats()
    data = struct.pack(">I", 0x7DF) + req
    msg = b.MSG.pack(b.PROT_CAN, 0, 0, 0, 0, len(data)) + data
    st, got = write_and_collect(c, ch, struct.pack("<I", 1) + msg, 1.5)
    st = st if st is not None else -1
    tx1 = can_stats()
    on_7e8 = [d for cid, d in got if cid == 0x7E8]
    want = "06 41 00"
    hit = [d for d in on_7e8 if d.startswith(want)]
    raw_ok = bool(hit) and tx1[0] == tx0[0] + 1
    print("WRITE raw 7DF %s -> ack %s | TWAI tx %s -> %s (errors %s) | %d frames rx, 7E8: %s | reply %s"
          % (req.hex(" ").upper(), hex(st), tx0[0], tx1[0], tx1[1], len(got), on_7e8[:3], ("YES " + hit[0]) if hit else "NO"))
c.call(b.DISCONNECT, channel=ch)

# ---- ISO15765: CONNECT without ids, then a FLOW_CONTROL filter carrying them
st, ch2, _ = c.call(b.CONNECT, payload=struct.pack("<III", b.PROT_ISO15765, 0, 500000))
print("CONNECT ISO15765 (no ids) ->", hex(st), "channel", ch2)
mask = b.MSG.pack(b.PROT_ISO15765, 0, 0, 0, 0, 4) + struct.pack(">I", 0xFFFFFFFF)
patt = b.MSG.pack(b.PROT_ISO15765, 0, 0, 0, 0, 4) + struct.pack(">I", 0x7E8)
flow = b.MSG.pack(b.PROT_ISO15765, 0, 0, 0, 0, 4) + struct.pack(">I", 0x7E0)
st, fid, _ = c.call(START_FILTER, ch2, struct.pack("<I", 3) + mask + patt + flow)
print("START_FILTER 0x%02X FLOW_CONTROL (pattern 7E8, fc 7E0) -> %s filter id %s" % (START_FILTER, hex(st), fid))
st, rx = c.write_uds(ch2, bytes([0x10, 0x02]))
print("WRITE ISO15765 10 02 ->", hex(st), "rx:", [m.hex(" ").upper() for m in rx][:3])
c.call(b.DISCONNECT, channel=ch2)
c.call(b.CLOSE)
iso_ok = st == 0 and any(m[:2] == b"P" for m in rx)
print("J2534 TRANSPORT: raw CAN %s, ISO15765 bind %s" % ("PASS" if raw_ok else "FAIL", "PASS" if iso_ok else "FAIL"))
sys.exit(0 if raw_ok else 1)
