# uds_bindings.be: every UDS binding of the script engine against one ECU
# (the bench ECU simulator on 7E0/7E8 by default; run_be.py --tx/--rx
# retarget the header below). Prints PASS/FAIL lines and a final
# "SCRIPT UDS PASS|FAIL". Sim answers used: 10 02 -> 50 02 .., 3E 00 -> 7E 00,
# 22 F1 90 -> 7F 22 31 on the Ioniq profile (a VIN on the plain profile:
# either is accepted, a nil is not), and the simulator's memory for a long
# answer: 23 14 <addr> <size> -> 63 and the bytes A & 0xFF (23 24 .. above
# 255 bytes). An ECU without it (the PCAN reflash ECU) skips those checks.
import string

var TX = 0x7E0
var RX = 0x7E8
var fails = 0

def check(name, ok, extra)
  if ok
    log("PASS: " + name + (extra != nil ? "  " + str(extra) : ""))
  else
    fails += 1
    log("FAIL: " + name + (extra != nil ? "  " + str(extra) : ""))
  end
end

# --- one-shot uds(): positive, negative (+ uds_nrc / uds_nrc_str) ---------
var s = uds(TX, RX, '10 02')
check("uds 10 02 -> 50 02", s != nil && string.startswith(s, '50 02'), s)
check("uds_ok set", uds_ok == 1, uds_ok)

var v = uds(TX, RX, '22 F1 90')
check("uds 22 F1 90 answered", v != nil, v)
if v != nil && string.startswith(v, '7F')
  check("negative decoded: uds_nrc 0x31", uds_nrc == 0x31, uds_nrc)
  check("uds_nrc_str", uds_nrc_str(uds_nrc) == 'requestOutOfRange', uds_nrc_str(uds_nrc))
else
  check("positive VIN read", v != nil && string.startswith(v, '62 F1 90'), v)
end

var tp = uds(TX, RX, '3E 00')
check("uds 3E 00 -> 7E 00", tp != nil && string.startswith(tp, '7E 00'), tp)

# --- a long answer, whole (2026-11-26: a script got the first 128 bytes of
# an answer and nothing about the rest, and nil for one over 512 bytes) -----
def whole(h, nbytes)
  # nbytes of answer as 'XX XX ..': its length, its start and its last byte
  if h == nil return false end
  var n = size(h)
  return n == nbytes * 3 - 1 && string.startswith(h, '63 00 01 02') &&
         h[n - 2 .. n - 1] == string.format('%02X', (nbytes - 2) & 0xFF)
end

def hexlen(h)
  return h != nil ? str(size(h)) + " chars" : "nil"
end

var mem = uds(TX, RX, '23 14 00 00 00 00 03')
var has_mem = mem != nil && string.startswith(mem, '63 00 01 02')
if has_mem
  var l1 = uds(TX, RX, '23 14 00 00 00 00 C8')
  check("uds: an answer of 201 bytes is whole", whole(l1, 201), hexlen(l1))
  var l2 = uds(TX, RX, '23 24 00 00 00 00 0F FE')
  check("uds: an answer of 4095 bytes is whole", whole(l2, 4095), hexlen(l2))
else
  log("SKIP: this ECU does not serve the simulator's memory, no long answer")
end

# --- the claimed conversation: obd_claim / obd_request / raw ISO-TP ----------
check("obd_claim", obd_claim(TX, RX) == 1)
var r = obd_request('10 02')
check("obd_request 10 02", r != nil && string.startswith(r, '50 02'), r)
check("obd_request sets uds_pending", uds_pending == 0, uds_pending)
var r2 = obd_request('3E 00', 800)
check("obd_request with timeout", r2 != nil && string.startswith(r2, '7E 00'), r2)

check("obd_isotp_tx 10 02", obd_isotp_tx('10 02') == 1)
var p = obd_isotp_rx(800)
check("obd_isotp_rx got the raw reply", p != nil && string.startswith(p, '50 02'), p)

if has_mem
  var l3 = obd_request('23 24 00 00 00 00 0F FE')
  check("obd_request: an answer of 4095 bytes is whole", whole(l3, 4095), hexlen(l3))
  check("obd_isotp_tx a read of 200 bytes", obd_isotp_tx('23 14 00 00 00 00 C8') == 1)
  var l4 = obd_isotp_rx(800)
  check("obd_isotp_rx: an answer of 201 bytes is whole", whole(l4, 201), hexlen(l4))
end
obd_release()

# after the release a fresh one-shot must still work (the claim is gone)
var again = uds(TX, RX, '3E 00')
check("uds after release", again != nil && string.startswith(again, '7E 00'), again)

if fails == 0
  log("SCRIPT UDS PASS")
else
  log("SCRIPT UDS FAIL (" + str(fails) + ")")
end
