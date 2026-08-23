# reflash.be — script-driven ECU reflash from an SD firmware file.
#
# Reads /sd/fw/ecu.bin and flashes it to the ECU at 0x7E0/0x7E8 using the
# ISO 14229 programming sequence the WiCAN ECU simulator implements:
#   prog session -> security access -> fingerprint -> erase ->
#   RequestDownload -> TransferData(stream) -> TransferExit ->
#   checkMemory(CRC) -> confirm version bump -> reset.
#
# Requires script_engine.allow_reflash = true. Emits script.done with a
# result string. Tuned for the sim (maxNumberOfBlockLength 0x0102 = 258,
# flash window base 0x08000000, key = seed[0:4] XOR 0xFF, fingerprint DID
# F15A, checkMemory routine 0202, SW-version DID F195).

import string

var PATH = "/sd/fw/ecu.bin"
var BLOCK = 128          # <= the ECU's advertised max block (258 for the sim)

def fail(msg)
  log("REFLASH FAIL: " + msg)
  emit("reflash", "done", "result", "fail:" + msg)
  return false
end

# response hex is space-separated ("67 01 AA BB .."); return token i as int
def tok(resp, i)
  var parts = string.split(resp, " ")
  if i >= size(parts)  return -1 end
  return int("0x" + parts[i])
end

# --- preflight: file present, claim the bus ---
var fwlen = obd_file_size(PATH)
if fwlen == nil || fwlen == 0  return fail("no firmware at " + PATH) end
log("firmware " + str(fwlen) + " bytes, " + str(BLOCK) + "-byte blocks")

if obd_claim(0x7E0, 0x7E8) == 0  return fail("cannot claim the bus") end

# --- programming session + security access (sim: key = seed XOR 0xFF) ---
obd_request("1002")
var seed = obd_request("2701")           # -> "67 01 SS SS SS SS ..."
if seed == nil  return fail("no seed") end
var key = "2702"
for i: 2..5                              # first 4 seed bytes (skip 67 01)
  var b = tok(seed, i)
  if b < 0  return fail("short seed") end
  key += string.format("%02X", (b ^ 0xFF) & 0xFF)
end
obd_request(key)
if uds_nrc != -1  return fail("security denied nrc=" + uds_nrc_str(uds_nrc)) end

# --- fingerprint (required before download) + erase ---
obd_request("2EF15A00112233445566")
obd_request("3101FF00", 4000)            # erase routine (0x78-heavy)

# --- RequestDownload: 34 00 44 <addr32> <size32> ---
var rq = string.format("340044%08X%08X", 0x08000000, fwlen)
if obd_request(rq) == nil || uds_nrc != -1
  return fail("RequestDownload nrc=" + uds_nrc_str(uds_nrc))
end

# --- stream the firmware as TransferData (36 <bsc> <block>) ---
var sent = obd_transfer_file(PATH, 0, fwlen, BLOCK, 1)
if sent == nil  return fail("TransferData failed") end
log("transferred " + str(sent) + " bytes in " + str(uds_xfer_blocks) +
    " blocks, crc=" + string.format("0x%08X", uds_xfer_crc))

# --- TransferExit (size-enforced) + checkMemory (real CRC compare) ---
obd_request("37")
if uds_nrc != -1  return fail("TransferExit nrc=" + uds_nrc_str(uds_nrc)) end
var cm = obd_request("31010202" + string.format("%08X", uds_xfer_crc))
if cm == nil || uds_nrc != -1  return fail("checkMemory nrc=" + uds_nrc_str(uds_nrc)) end
log("checkMemory result: " + cm)          # sim: last routineInfo byte 00 = pass
var parts = string.split(cm, " ")
if tok(cm, size(parts) - 1) != 0  return fail("checkMemory VERIFY FAILED: " + cm) end

# --- confirm the bumped software version, then activate ---
log("SW version now: " + str(obd_request("22F195")))
obd_request("1101")                       # ECU reset = activate

obd_release()
log("REFLASH OK")
emit("reflash", "done", "result", string.format("ok:%d bytes crc=0x%08X", sent, uds_xfer_crc))
return true
