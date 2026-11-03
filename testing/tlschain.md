# HTTPS chain ending in a cross-signed root

Command: `.\test.ps1 tlschain` (= `python tools/testbench/ha/tls_chain_bench.py --dut localhost:18083` through an ssh tunnel to the DUT via rpi001; by hand: `python tools/testbench/ha/tls_chain_bench.py --dut <ip-or-host:port> [--host developers.cloudflare.com]`)

Needs: the DUT on the bench AP with an internet path (the Pi's NAT) + rpi001 (the tunnel to the DUT); nothing on the DUT beyond its factory certificate bundle

Index row and the other stages: [../TESTING.md](../TESTING.md).

**What it covers.** The built-in certificate bundle against a server whose
chain ends in a cross-signed copy of a root the bundle carries: Cloudflare
serves `leaf <- WE1 <- GTS Root R4 (as signed by GlobalSign Root CA)`, and
the IDF 6.1 bundle (145 roots) holds GTS Root R4 but not GlobalSign Root
CA. The IDF bundle callback looks only at the issuer of the last presented
certificate, so the handshake failed with `No matching trusted root
certificate found` (the field report of 2026-11-03: the Home Assistant
webhook behind a Cloudflare tunnel, `ESP_ERR_HTTP_CONNECT` on every push;
the reporter's workaround was uploading GlobalSign Root CA as a certificate
set). `CONFIG_MBEDTLS_CERTIFICATE_BUNDLE_CROSS_SIGNED_VERIFY` (mbedTLS's
trusted-certificate callback) lets the chain validate at the GTS root the
bundle knows. The bench points the HA webhook (`POST /api/webhook`, applies
live, no restart) at `https://developers.cloudflare.com/api/webhook/wican-tlschain`,
waits for the poster's attempts and asserts that the TLS handshake went
through: the stats carry a push (`success_count`) or an HTTP status
(`last_error` = `http=NNN`: the server answered the POST), never a
transport error (`esp_err=...`). The webhook is restored to its found state
(`DELETE /api/webhook` when it was unregistered, the found URL otherwise).
Every HTTPS client of the device (the webhook poster, the destinations,
the firmware update check, the ABRP push) shares the bundle and the
`http_client_manager`, so the one leg stands for all of them.

**Records.**

- 2026-11-03 10:22, the reproduction on the published beta.7 (bundle as it
  was): 5 of 5 pushes `esp_err=ESP_ERR_HTTP_CONNECT`, the console
  `esp-x509-crt-bundle: No matching trusted root certificate found`,
  `Failed to verify certificate`, `mbedtls_ssl_handshake returned -0x3000`.
  REPRODUCED (`test-reports/logs/2026-11-03_1030_tsrelay_repro/console.log`).
- 2026-11-03 12:25, the fixed image (`CONFIG_MBEDTLS_CERTIFICATE_BUNDLE_CROSS_SIGNED_VERIFY=y`,
  `v6.0.0p-beta.7-1-gaff83a6-dirty`): **`TLS CHAIN PASS`**, 13 s; the poster's
  two pushes answered `http=405` (the host refusing a POST on that path, the
  TLS handshake through), the webhook back to unregistered. Report
  `test-reports/2026-11-03_1225_tlschain.md`. The run before it (12:24) had
  the device passing and the bench failing its own t1/t3 on the 201 and 204
  status codes, fixed in the script.

**Traps.** `POST /api/webhook` answers 201 for a new registration (200 for a
re-POST of the same URL) and `DELETE /api/webhook` answers 204: the first run
of the bench expected 200 from both and failed its own t1/t3 while the
device had passed. The host must present the cross-signed chain: on 2026-11-03
`developers.cloudflare.com` did (`openssl s_client -showcerts`:
`GTS Root R4` issued by `GlobalSign Root CA`), while `www.cloudflare.com`
served a Let's Encrypt chain under ISRG Root X1 that the bundle trusts
either way; check the chain before blaming the device when the leg fails.
The bench needs the DUT's internet path (DNS and 443 out); the webhook
poster retries every `interval` seconds, so a second attempt lands within
10 s of the registration.
