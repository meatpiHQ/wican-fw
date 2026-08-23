#!/usr/bin/env python3
"""ha_webhooks follow-up legs (run ON rpi001; DUT over the hotspot):

  403 leg   — a mock HA answering HTTP 403: after HW_REJECT_LIMIT (3)
              consecutive rejected cycles the poster must PAUSE (post
              count plateaus, last_error says identity rejected), and a
              re-registration must clear the pause (attempts resume).
  vpnip leg — a normal 200 receiver: with a live WG tunnel up, every
              status section must carry vpn_ip == the expected tunnel
              address (HA's away-from-home control endpoint).

  python3 ha_webhook_403_vpn_bench.py 403   [dut_ip]
  python3 ha_webhook_403_vpn_bench.py vpnip <expected_vpn_ip> [dut_ip]

Restores the DUT's prior webhook config on exit. Final line: PASS/FAIL.
"""
import json
import sys
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer

MODE = sys.argv[1] if len(sys.argv) > 1 else "403"
EXPECT_VPN = sys.argv[2] if MODE == "vpnip" and len(sys.argv) > 2 else ""
DUT = sys.argv[-1] if sys.argv[-1].count(".") == 3 else "10.42.0.62"
ME = "10.42.0.1"
PORT = 8232

fails = []
bodies = []
hits = [0]


def check(name, ok, detail=""):
    print(("PASS" if ok else "FAIL") + f": {name}" +
          (f" ({detail})" if detail else ""))
    if not ok:
        fails.append(name)


def api(path, method="GET", body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(f"http://{DUT}{path}", data=data,
                                 method=method)
    if data:
        req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=10) as r:
        t = r.read().decode()
        return json.loads(t) if t.strip().startswith("{") else t


class H(BaseHTTPRequestHandler):
    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(n)
        hits[0] += 1

        if MODE == "403":
            self.send_response(403)
            self.end_headers()
            return

        try:
            if self.headers.get("Content-Encoding") == "gzip":
                import gzip
                raw = gzip.decompress(raw)
            bodies.append(json.loads(raw.decode()))
        except Exception:
            bodies.append({})

        self.send_response(200)
        self.end_headers()

    def log_message(self, *a):
        pass


def main():
    prev = api("/api/webhook")
    srv = HTTPServer(("0.0.0.0", PORT), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    url = f"http://{ME}:{PORT}/api/webhook/wican"

    try:
        api("/api/webhook", "POST",
            {"url": url, "enabled": True, "interval": 2})
        time.sleep(1)

        if MODE == "403":
            # let >= 3 cycles get rejected, then watch for the plateau
            time.sleep(10)
            mid = hits[0]
            st = api("/api/webhook")
            check("rejection recorded",
                  "reject" in str(st.get("last_error", "")).lower() or
                  "403" in str(st.get("last_error", "")),
                  st.get("last_error", ""))
            check("saw >= 3 rejected posts", mid >= 3, f"hits={mid}")
            time.sleep(8)
            check("posting PAUSED after reject streak", hits[0] <= mid + 1,
                  f"during-pause hits {mid} -> {hits[0]}")
            # re-registration must clear the pause
            api("/api/webhook", "POST",
                {"url": url, "enabled": True, "interval": 2})
            base = hits[0]
            time.sleep(6)
            check("re-registration resumes attempts", hits[0] > base,
                  f"hits {base} -> {hits[0]}")
        else:
            time.sleep(7)
            check("pushes arriving", len(bodies) >= 2, f"n={len(bodies)}")
            # diff mode: vpn_ip appears in the FULL (first) push and then
            # only on change — assert the full push, and that no later
            # diff ever contradicts it.
            vpn_ips = [b.get("status", {}).get("vpn_ip") for b in bodies]
            present = [v for v in vpn_ips if v is not None]
            check("vpn_ip in the full push", bool(present) and
                  present[0] == EXPECT_VPN,
                  f"expect {EXPECT_VPN} got {vpn_ips}")
            check("no diff contradicts vpn_ip",
                  all(v == EXPECT_VPN for v in present), str(present))
            vss = [b.get("status", {}).get("vpn_status") for b in bodies]
            vs = [v for v in vss if v is not None]
            check("vpn_status connected (full push)",
                  bool(vs) and vs[0] == "connected", str(vss))
    finally:
        # restore the prior webhook config
        try:
            if prev.get("url"):
                api("/api/webhook", "POST", {
                    "url": prev.get("url"),
                    "enabled": bool(prev.get("enabled")),
                    "interval": int(prev.get("interval", 60)),
                })
            else:
                api("/api/webhook", "DELETE")
        except Exception as e:
            print(f"(restore: {e})")
        srv.shutdown()

    print(("HA " + MODE.upper() + " LEG ") +
          ("FAIL: " + ", ".join(fails) if fails else "PASS"))
    sys.exit(1 if fails else 0)


main()
