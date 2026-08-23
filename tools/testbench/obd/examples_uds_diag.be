# Example WiCAN UDS diagnostic script (Berry).
# Run via: POST /api/scripts/run {"src": "<this file>"}
# Bindings: uds(tx,rx,hexreq)->resp hex|nil; uds_ext(...) for 29-bit;
#           globals uds_ok / uds_nrc set by the last uds() call;
#           can_tx(id,ext,hex), emit(src,name,key,val), log, sleep_ms, millis.
log('=== UDS diagnostic ===')

var sess = uds('7E0', '7E8', '10 03')       # DiagnosticSessionControl
log('session -> ' + str(sess))

var vin = uds('7E0', '7E8', '22 F1 90')     # ReadDataByIdentifier F190
if vin != nil && !string.startswith(vin, '7F')
  log('VIN DID = ' + vin)
else
  log('VIN read failed (nrc=' + str(uds_nrc) + ')')
end

# a slow ECU that answers 0x78 responsePending is handled transparently
var slow = uds('7E0', '7E8', '22 F1 A0')
log('slow DID = ' + str(slow))

log('=== done ===')
