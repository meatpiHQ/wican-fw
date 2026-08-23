#!/usr/bin/env python3
"""ESPNetLink (LTE dongle) target bench (run ON rpi001).

Physical setup: the espnetlink cellular dongle plugged into the WiCAN
USB connector (host mode), its LTE modem provisioned with a SIM. It is a
COMPOSITE device — RNDIS (the LTE data path, handled by usb_eth_host)
plus a CDC-ACM management console (usb_acm_cli). This bench exercises
both.

Enable on the DUT first (reboot-to-apply):
  /api/settings/usb_host_manager  {enabled:true}   (+ prefer_usb_route to
                                                     use LTE as the uplink)
  /api/settings/usb_acm_cli       {enabled:true}

Flow: RNDIS enumerated + IP -> ACM console up -> `ver`/`lte -s`/`lte -i`
show a provisioned+attached modem. Optional --prove-lte isolates the
Pi's WiFi internet and confirms a fresh SNTP sync goes over the modem.

Expected final line: ESPNETLINK TARGET PASS
"""
import json
import subprocess
import sys
import time
import urllib.request

DUT = "10.42.0.62"
PROVE_LTE = "--prove-lte" in sys.argv

fails = []


def check(name, ok, detail=""):
    print(("PASS" if ok else "FAIL") + f": {name}" +
          (f" ({detail})" if detail else ""))
    if not ok:
        fails.append(name)


def api(path, method="GET", body=None, timeout=15):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(f"http://{DUT}{path}", data=data,
                                 method=method)
    if data:
        req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode() or "{}")


def acm(cmd, timeout_ms=4000):
    r = api("/api/usb/acm/cmd", "POST", {"cmd": cmd,
                                         "timeout_ms": timeout_ms})
    return r.get("response", "") if r.get("ok") else ""


def sh(cmd):
    return subprocess.run(cmd, shell=True, capture_output=True, text=True)


def main():
    # 1. RNDIS data path
    usb = api("/api/usb")
    print("usb:", json.dumps(usb))
    check("usb host active", usb.get("host_active") and
          usb.get("device_present"))
    check("RNDIS driver", usb.get("driver") == "rndis", usb.get("driver"))
    check("data-path IP", usb.get("ip", "").count(".") == 3 and
          usb.get("eth_connected"), usb.get("ip"))

    # 2. CDC-ACM management console
    st = api("/api/usb/acm")
    check("ACM console connected", st.get("connected") is True)
    if not st.get("connected"):
        print("ESPNETLINK TARGET FAIL: no ACM console")
        sys.exit(1)

    ver = acm("ver")
    check("ver = ESPNetLink", "ESPNetLink" in ver,
          ver.replace("\r", " ").strip()[:40])

    lte = acm("lte -s")
    print("--- lte -s ---\n" + lte.strip())
    check("modem network attached", "attached" in lte)
    check("PPP connected", "PPP" in lte and "connected" in lte)
    check("signal reported", "RSSI" in lte or "dBm" in lte)

    ip = acm("lte -i")
    check("carrier IP assigned", "IP:" in ip and "." in ip,
          ip.replace("\r", " ").strip()[:40])

    # 3. optional: prove internet actually flows over LTE (isolate WiFi)
    if PROVE_LTE:
        print("--- proving internet-over-LTE (blocking WiFi at the Pi) ---")
        # requires prefer_usb_route=true so LTE is the default route
        blocked = False
        try:
            sh("sudo /usr/sbin/nft insert rule ip nm-shared-wlan1 "
               "filter_forward ip saddr 10.42.0.62 ip daddr != "
               "10.42.0.0/24 drop")
            blocked = True
            time.sleep(2)
            r = api("/api/rtc/sync", "POST", {}, timeout=25)
            ok = r.get("valid") is True
            check("SNTP sync over LTE (WiFi blocked)", ok,
                  r.get("sntp", {}).get("last_sync", ""))
        finally:
            if blocked:
                h = sh("sudo /usr/sbin/nft -a list chain ip "
                       "nm-shared-wlan1 filter_forward | grep '10.42.0.62' "
                       "| grep -o 'handle [0-9]*' | awk '{print $2}'"
                       ).stdout.strip()
                if h:
                    sh(f"sudo /usr/sbin/nft delete rule ip nm-shared-wlan1 "
                       f"filter_forward handle {h}")
                print("WiFi block removed")
    else:
        print("SKIP: internet-over-LTE isolation (pass --prove-lte + "
              "prefer_usb_route=true; needs sudo nft on the Pi)")

    if fails:
        print("ESPNETLINK TARGET FAIL:", ", ".join(fails))
        sys.exit(1)
    print("ESPNETLINK TARGET PASS")


if __name__ == "__main__":
    main()
