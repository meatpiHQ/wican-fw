"""The field tool chain itself: udsoncan's J2534Connection over the installed WiCAN DLL.

Runs under a 32-bit Python (the DLL is 32-bit) with udsoncan on its path:
  C:\\Users\\Ali\\idf_tmp\\py32\\python.exe (the embeddable 3.12 + udsoncan 1.26.1 site)
The DLL's target comes from the environment, no registry edit needed:
  WICAN_J2534_HOST / WICAN_J2534_PORT   (127.0.0.1 / 18534 through the bench tunnel)
  WICAN_UDS_TXID / WICAN_UDS_RXID       (hex, default 7E0 / 7E8)
Prints `OK: <request> -> <answer>` per request, `Failed: ...` otherwise, exit 1 on any failure.
Driven by j2534_std_tool_bench.py (leg D); runnable by hand.
"""
import logging
import os
import struct
import sys

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
import udsoncan  # noqa: E402
from udsoncan.client import Client  # noqa: E402
from udsoncan.connections import J2534Connection  # noqa: E402
from udsoncan.exceptions import (InvalidResponseException, NegativeResponseException,  # noqa: E402
                                 TimeoutException, UnexpectedResponseException)

DLL = os.environ.get("WICAN_J2534_DLL", r"C:\Program Files (x86)\WiCAN\wican_j2534.dll")
TXID = int(os.environ.get("WICAN_UDS_TXID", "7E0"), 16)
RXID = int(os.environ.get("WICAN_UDS_RXID", "7E8"), 16)
print("udsoncan %s on python %s (%d-bit); target %s:%s; DLL %s" % (
    udsoncan.__version__, sys.version.split()[0], struct.calcsize("P") * 8,
    os.environ.get("WICAN_J2534_HOST"), os.environ.get("WICAN_J2534_PORT"), DLL), flush=True)
print("CAN    bitrate=500000  txid=0x%03X  rxid=0x%03X" % (TXID, RXID), flush=True)

requests = [("DiagnosticSessionControl 10 03", lambda c: c.change_session(3)),
            ("ReadDataByIdentifier 22 F1 90 (VIN)", lambda c: c.read_data_by_identifier_first(0xF190)),
            ("TesterPresent 3E 00", lambda c: c.tester_present())]
failed = 0

# udsoncan 1.26.1 (PyPI): the constructor opens the DLL, connects ISO15765 at 500000
# and arms the FLOW_CONTROL filter; every message carries the id and ISO15765_FRAME_PAD
conn = J2534Connection(DLL, rxid=RXID, txid=TXID)
with Client(conn) as client:
    client.config["data_identifiers"] = {0xF190: udsoncan.AsciiCodec(17)}
    for name, fn in requests:
        try:
            r = fn(client)
            data = r.data.hex(" ").upper() if hasattr(r, "data") and r.data else str(r)
            print("OK: %s -> %s" % (name, data), flush=True)
        except TimeoutException as e:
            failed += 1
            print("Failed: no response (%s)" % e, flush=True)
        except NegativeResponseException as e:
            failed += 1
            print("Failed: negative response (%s)" % e, flush=True)
        except (InvalidResponseException, UnexpectedResponseException) as e:
            failed += 1
            print("Failed: %s: %s" % (type(e).__name__, e), flush=True)
sys.exit(1 if failed else 0)
