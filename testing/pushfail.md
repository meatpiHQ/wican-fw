# A push to Home Assistant that fails says why, per address

Command: `.\test.ps1 pushfail` = `python tools/testbench/ha/push_fail_reason_bench.py --dut localhost:18086 --pi rpi001 --pi-ip 10.42.1.1 [--name rpi001.local] [--nobody 10.42.1.250] [--port 8470] [--no-page]` through the runner's tunnel

Needs: the DUT on the bench AP; rpi001 over ssh with `python3` and its `cryptography` (the bench makes a root and five certificates under `/tmp/wican_pushfail`, runs nine small far ends on ports 8471 to 8479 and removes all of it at the end); Chrome on the PC and the Playwright devDependency of `tools/webui_preview` for the page leg (`--no-page` leaves it out)

The index row and the other stages: [../TESTING.md](../TESTING.md). The page's words over the mock: [probe_push_reason.md](probe_push_reason.md).

## What it covers

When the webhook's address does not take a push, the unit said `status: failed` and `last_error: esp_err=ESP_ERR_HTTP_CONNECT`, whatever the cause: esp_http_client answers that one error for every connection that does not open. A field report of 2026-11-22 (Discord: a reverse proxy at home, Nabu Casa outside, "both don't work") showed `mbedtls_ssl_handshake returned -0x7280` in the device log, which means the far end closed the connection during the TLS handshake without an alert, and it took the log and two rounds of questions to get that far.

Since that day:

- `http_client_manager` hands back what esp-tls kept about a connection that failed (`http_client_response_t.fail`) and works a reason out of it (`http_client_manager_fail_reason()`, pure, host tested);
- `ha_webhooks` keeps, per address that did not take the last push, the reason word, a code for support and the time; `GET /api/webhook` carries them as `failures`, the `webhook` command prints them, the cycle's W line names them;
- the web interface's Home Assistant card says each in one line of plain words, with the explanation and the code in a bubble.

The far ends are the bench's own, on the Pi, one per way a push can be turned away. The unit meets them by the Pi's name (`rpi001.local`), so its hello carries a host name as it does for a real Home Assistant by name. The legs, each a registration at one address (or two) followed by two push cycles:

| Leg | The far end | `reason` | `code` | `last_error` |
|---|---|---|---|---|
| a1 | a name that does not exist (`wican-pushfail-nohost.invalid`) | `dns` | `esp-tls 0x8001` | `esp_err=ESP_ERR_HTTP_CONNECT` |
| a2 | a port nothing listens on | `no_answer` | `esp-tls 0x8004 errno 104` | the same |
| a3 | an address nobody has | `no_answer` | `esp-tls 0x8006` | the same |
| a4 | closes the connection after the hello (the field report) | `tls_closed` | `tls -0x7280` | the same |
| a5 | answers the hello with an alert (a TLS server that shares no cipher suite with the unit) | `tls_refused` | `tls -0x7780` | the same |
| a6 | an https address on a port that speaks plain http | `not_https` | `tls -0x7200` | the same |
| a7 | a certificate under a root the built-in bundle does not hold | `cert_untrusted` | `tls -0x3000` | the same |
| a8 | takes the request and never answers | `no_reply` | `no status` | `http=-1` |
| a9, a10, a11 | answers 400, 404, 502 | `http` (with `"http": <status>`) | `http 400` ... | `http=400` ... |
| b1 | with the run's root as the certificate set: a certificate for another name | `cert` | `tls -0x2700` | `esp_err=ESP_ERR_HTTP_CONNECT` |
| b2 | the same set: a certificate under another root | `cert` | `tls -0x2700` | the same |
| b3 | a certificate that ran out yesterday, and one that starts tomorrow | recorded, see below | | |
| b4 | the good far end | none: `failures` empty, status `ok` | | `""` |
| c1 | two addresses, the first refuses, the second is the good one | one entry, slot 1, `no_answer`; status `ok`, the pushes arrive at the second | | `""` |
| c2 | two addresses, the first refuses, the second closes after the hello | two entries in the order tried, slots 1 and 2 | | `esp_err=ESP_ERR_HTTP_CONNECT` |
| c3 | the unit's own page in Chrome, in the state of c2 | a line per address (`Webhook URL: nothing answers at this address`, `Failover URL: the server closed the connection during the secure handshake`), the second line's bubble with its title, what to check and the code the unit reported | | |

Also asserted: every entry has a time in the unit's UTC clock; an entry carries `http` only for an answer; the W line of a4 reads `webhook POST failed: esp_err=ESP_ERR_HTTP_CONNECT (1 tls_closed [tls -0x7280])` and the one of c2 names both addresses; at the end the webhook's settings and the certificate sets are as found, no reset nobody asked for, no E line of `ha_webhooks` or `http_client_manager` (the IDF's own E lines of a failed connection are not theirs; see the traps).

**The certificate legs, and two things they showed.**

- A certificate for another name (b1) and one under another root (b2) both end the handshake with `-0x2700`, and the unit cannot tell them apart: the certificate check's flags would, and esp-tls of IDF 6.1 does not hand them over for a handshake that failed (`esp_mbedtls_handshake()` asks for the peer certificate first, and there is none before the handshake is done). So they are one reason, `cert`, and the page's bubble names both things to check. With the built-in bundle an unknown root has its own code (`-0x3000`, a7).
- **The firmware does not check a certificate's dates** (`CONFIG_MBEDTLS_HAVE_TIME_DATE` is off, IDF's default): a certificate that ran out yesterday and one that starts tomorrow are both taken, the pushes delivered. Leg b3 records this (`cert_dates_checked 0`) and judges only that the unit treats the two alike and that a refusal, on a build that checks, carries the reason `cert`. Whether to switch the check on is a product decision (a unit without a set clock would then take no certificate at all).

## Records

- 2026-11-22 16:07, the first run on the bench unit (the image of that hour, by hand through a tunnel): 28 of 32 checks. Every leg of the built-in bundle, the two-address legs and the page in Chrome were right at the first go. The four reds were the certificate legs as first written, and each was the unit telling something true: a certificate for another name came back as `tls -0x2700` with no flags (the bench expected `flags 0x4`), so did one under another root (`flags 0x8` expected), and the expired and the not-yet-valid certificate were TAKEN (`status: ok`). Read in the IDF: esp-tls does not hand the flags over after a failed handshake, and `CONFIG_MBEDTLS_HAVE_TIME_DATE` is off. The three reasons that hung on the flags (`cert_name`, `cert_expired`, `cert_future`) were folded into `cert`, the dates leg turned into a record.
- 2026-11-22 16:14 to 16:23, `.	est.ps1 pushfail` on the image with that change (bench unit 14c19f457fd9): **`PUSH FAIL REASON PASS`**, every check green. a1 `dns` / `esp-tls 0x8001`; a2 `no_answer` / `esp-tls 0x8004 errno 104`; a3 `no_answer` / `esp-tls 0x8006`; a4 `tls_closed` / `tls -0x7280` and its W line `webhook POST failed: esp_err=ESP_ERR_HTTP_CONNECT (1 tls_closed [tls -0x7280])`; a5 `tls_refused` / `tls -0x7780`; a6 `not_https` / `tls -0x7200`; a7 `cert_untrusted` / `tls -0x3000`; a8 `no_reply` / `no status` (`last_error` `http=-1`); a9 to a11 `http` 400, 404, 502; b1 and b2 `cert` / `tls -0x2700`; b3 both certificates taken, `cert_dates_checked 0`; b4 delivered, `failures` empty; c1 status `ok` with the one entry of slot 1, the pushes at the second address; c2 two entries, slots 1 and 2, the W line `(1 no_answer [esp-tls 0x8004 errno 104], 2 tls_closed [tls -0x7280])`; c3 `PUSH FAIL PAGE PASS` (the two lines, the bubble "The server closed the connection" with `For support: tls -0x7280`). Two planned restarts, `unexpected_resets` 0 -> 0, no E line of the own tags, internal heap floor 40,600 B, the webhook's settings and the sets as found.
- 2026-11-22, beside it: `.	est.ps1 host http_client_manager` 9 tests (the two new ones: the reason, the code), `.	est.ps1 host ha_webhooks` 11 tests, `.	est.ps1 tlsbyip` `TLS BY IP PASS` (the certificate set path still delivers), `PUSH REASON PROBE PASS` (35 checks), `probe_automate.mjs` 81 of 81.

## Traps

- **Two cycles per leg.** A registration does not clear the last push's `failures`; the bench waits for two finished cycles after it, so that what it reads belongs to the new address. A leg takes 12 to 25 s (a connection that times out costs the push's 6 s).
- **The IDF logs E lines for a connection that fails** (`esp-tls-mbedtls: mbedtls_ssl_handshake returned ...`, `esp-tls: Failed to open new connection`, `transport_base: ...`, `HTTP_CLIENT: Connection failed`), four per failed push. They are not the components' own and the bench does not count them; they do raise `health.log_errors` on the unit while the stage runs.
- **The unit has to resolve the Pi's name.** `rpi001.local` goes over mDNS on the bench AP's network. A unit that cannot resolve it fails every by-name leg with `dns`: check with `--name` and an address the unit knows.
- **`--nobody`** has to be an address on the unit's own subnet that nothing has (10.42.1.250 on the bench AP), so that the connection times out instead of being answered by a router.
- **Port 8470 itself stays free**: it is the "nothing listens" port of a2, c1 and c2. A stale run's servers are stopped at the start (`pkill -f wican_pushfail/server.py`).
- **The page leg needs the image of this build on the unit**: an older page shows no line (the device serves the embedded page; flash it).
- The far ends answer 204 with an empty body: no reply settings, so nothing of the unit's webhook settings changes but what the bench registers and puts back.
