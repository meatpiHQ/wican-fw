"""Reset the DUT, capture serial until a marker (or timeout), and verify.

Used by test.ps1 `target` for the self-contained on-target test apps
(pytest_<comp>.py marker suites). The marker defaults to 'TEST DONE';
apps that idle for an external driver pass their ready line instead
(e.g. 'BLE READY'). Exit code: 0 = marker seen and no crash signatures;
1 otherwise.

    python serial_capture.py COM7 115200 60 out.log ["TEST DONE"]

The verdict line reports E/W log-line counts (2026-07-19 health pass).
Not fatal by default — negative-path tests provoke errors on purpose —
but visible in every log and report; grep `errors=` to audit a run.
"""
import re
import sys
import time

import serial

CRASH = (b"abort()", b"Guru Meditation", b"assert failed", b"stack overflow")

port, baud, seconds = sys.argv[1], int(sys.argv[2]), float(sys.argv[3])
outfile = sys.argv[4] if len(sys.argv) > 4 else None
marker = sys.argv[5] if len(sys.argv) > 5 else "TEST DONE"

sys.stdout.reconfigure(errors="replace")

s = serial.Serial(port, baud, timeout=0.2)
s.dtr = False
s.rts = True
time.sleep(0.1)
s.rts = False

buf = bytearray()
deadline = time.time() + seconds
while time.time() < deadline:
    chunk = s.read(4096)
    if chunk:
        buf.extend(chunk)
        if marker.encode() in buf or any(c in buf for c in CRASH):
            time.sleep(0.5)
            buf.extend(s.read(65536))
            break
s.close()

text = buf.decode("utf-8", errors="replace")
if outfile:
    with open(outfile, "w", encoding="utf-8") as f:
        f.write(text)

done = marker in text
crashed = any(c.decode() in text for c in CRASH)
plain = re.sub(r"\x1b\[[0-9;]*m", "", text)  # strip ANSI colors
errors = len(re.findall(r"^E \(\d+\)", plain, re.M))
warnings = len(re.findall(r"^W \(\d+\)", plain, re.M))
print(text)
print(f"--- verdict: done={done} ({marker!r}) crashed={crashed} "
      f"errors={errors} warnings={warnings} ---")
sys.exit(0 if (done and not crashed) else 1)
