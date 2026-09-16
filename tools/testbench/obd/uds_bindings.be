# uds_bindings.be — every UDS binding of the script engine against one ECU
# (the bench ECU simulator on 7E0/7E8 by default; run_be.py --tx/--rx
# retarget the header below). Prints PASS/FAIL lines and a final
# "SCRIPT UDS PASS|FAIL". Sim answers used: 10 02 -> 50 02 .., 3E 00 -> 7E 00,
# 22 F1 90 -> 7F 22 31 on the Ioniq profile (a VIN on the plain profile:
# either is accepted, a nil is not).
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
obd_release()

# after the release a fresh one-shot must still work (the claim is gone)
var again = uds(TX, RX, '3E 00')
check("uds after release", again != nil && string.startswith(again, '7E 00'), again)

if fails == 0
  log("SCRIPT UDS PASS")
else
  log("SCRIPT UDS FAIL (" + str(fails) + ")")
end
