#!/usr/bin/env python3
"""An HTTPS chain that ends in a cross-signed root, accepted by the built-in bundle.

The claim: a server whose certificate chain ends in a cross-signed copy of a root
the device's bundle carries is trusted. Cloudflare serves `leaf <- WE1 <- GTS Root
R4 (as signed by GlobalSign Root CA)`; the IDF 6.1 bundle holds GTS Root R4 but
not GlobalSign Root CA, and the bundle's own callback looks only at the issuer of
the last presented certificate, so every handshake failed with "No matching
trusted root certificate found" (the field report of 2026-11-03: the Home
Assistant webhook behind a Cloudflare tunnel, ESP_ERR_HTTP_CONNECT on every push).
CONFIG_MBEDTLS_CERTIFICATE_BUNDLE_CROSS_SIGNED_VERIFY lets the chain validate at
the GTS root the bundle knows. Every HTTPS client of the device shares the bundle
and the http_client_manager, so this one leg stands for all of them.

Legs:
  t0  as found: the webhook's registration (kept to restore), the DUT online
  t1  the webhook pointed at https://<host>/api/webhook/wican-tlschain (applies
      live, no restart), interval 5 s
  t2  within 30 s the poster has tried, and the TLS handshake went through:
      a push counted (success_count) or an HTTP answer (last_error = http=NNN:
      the server took the POST), never a transport error (esp_err=...)
  t3  the webhook restored to its found state; no E line of the bundle's tags

  python tools/testbench/ha/tls_chain_bench.py --dut localhost:18083
      [--host developers.cloudflare.com]

Prints `TLS CHAIN PASS`. Written 2026-11-03.
"""
import argparse
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "lib"))
import pcbench  # noqa: E402

PATH = "/api/webhook/wican-tlschain"


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--dut", default="localhost:18083",
                    help="host:port of the DUT's API (a tunnel)")
    ap.add_argument("--host", default="developers.cloudflare.com",
                    help="a host that serves the cross-signed GTS Root R4 chain")
    ap.add_argument("--wait", type=int, default=30)
    args = ap.parse_args()

    run = pcbench.Run("TLS CHAIN")
    dut = pcbench.Dut(args.dut, own_tags=("ha_webhooks",))
    url = f"https://{args.host}{PATH}"

    # t0: as found
    found = dut.get("/api/webhook")
    run.check("t0 the DUT answers, the webhook read",
              isinstance(found, dict) and "url" in found,
              f"url={found.get('url', '?')!r} enabled={found.get('enabled')}")
    found_url = found.get("url") or ""

    try:
        # t1: the webhook at the cross-signed host (applies live)
        code, r = dut.api("/api/webhook", "POST",
                          {"url": url, "enabled": True, "interval": 5})
        # a new registration answers 201, a re-POST of the same URL 200
        run.check("t1 webhook pointed at the cross-signed host",
                  code in (200, 201) and isinstance(r, dict) and r.get("url") == url,
                  f"http {code} {str(r)[:120]}")

        # t2: the poster's attempts and their outcome
        t0 = time.time()
        st = {}
        while time.time() - t0 < args.wait:
            time.sleep(3)
            st = dut.get("/api/webhook")
            if (st.get("success_count", 0) + st.get("fail_count", 0)) >= 2:
                break
        tried = st.get("success_count", 0) + st.get("fail_count", 0)
        err = st.get("last_error", "")
        run.check("t2 the poster tried", tried >= 1,
                  f"ok={st.get('success_count')} fail={st.get('fail_count')} "
                  f"after {time.time() - t0:.0f} s")
        through = st.get("success_count", 0) > 0 or err.startswith("http=")
        run.check("t2 the TLS handshake went through (an HTTP answer, no "
                  "transport error)", tried >= 1 and through,
                  f"last_error={err!r} status={st.get('status')!r}")
        run.metric("tls_chain_last_error", err or "none")
    finally:
        # t3: as found
        if found_url:
            body = {k: found.get(k) for k in ("url", "enabled", "interval",
                                               "manual_override")
                    if found.get(k) is not None}
            if found.get("urls"):
                body["urls"] = found["urls"]
            code, r = dut.api("/api/webhook", "POST", body)
        else:
            code, r = dut.api("/api/webhook", "DELETE")
        after = dut.get("/api/webhook")
        # the DELETE answers 204, a re-registration 200 or 201
        run.check("t3 webhook restored to its found state",
                  code in (200, 201, 204) and after.get("url", "") == found_url
                  and bool(after.get("enabled")) == bool(found.get("enabled")),
                  f"http {code} url={after.get('url')!r} "
                  f"enabled={after.get('enabled')}")

    return run.verdict()


if __name__ == "__main__":
    try:
        sys.exit(main())
    except pcbench.Bench as e:
        print(f"BENCH ERROR: {e}")
        print("TLS CHAIN FAIL: bench error")
        sys.exit(2)
