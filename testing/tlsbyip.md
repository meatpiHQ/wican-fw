# HTTPS to an address by IP: delivered, the log quiet

Command: `.\test.ps1 tlsbyip` = `python tools/testbench/ha/tls_by_ip_bench.py --dut localhost:18084 --pi rpi001 --pi-ip 10.42.1.1 [--port 8444]` through the runner's tunnel

Needs: the DUT on the bench AP, rpi001 over ssh (`openssl` and `python3` there; the bench makes a CA and a certificate under `/tmp/wican_tlsbyip`, runs its own HTTPS receiver on port 8444 and removes both at the end)

The index row and the other stages: [../TESTING.md](../TESTING.md).

## What it covers

A webhook (the same holds for a data destination: both go through
`http_client_manager`) at `https://<ip>:<port>` whose certificate names a
host. That is what the Home Assistant integration registers for a Home
Assistant whose own web server has SSL on and no Local network URL (ha-wican
issue 110, 2026-11-14): `https://<Home Assistant's address>:8123`, with a
Let's Encrypt certificate for a DuckDNS name behind it. The device verifies
the chain and does not compare the name (`skip_common_name` for an address
by IP).

esp-tls prints a W line on every connection that skips the name check
(`esp-tls-mbedtls: skip_common_name=true: hostname matching and SNI
disabled. ...`). With a push every 15 s that was one warning per push: the
16 KB log ring held little else and `health.log_warnings` climbed by 4 a
minute. `http_client_manager` now registers the tag `esp-tls-mbedtls` at
ERROR and logs one I line a boot itself. Two things in `log_manager` had to
be put right for that to hold, and the bench stands on both:

- IDF drops every per-tag level when a level is set for `*`, and the `level`
  setting does that at boot after every `_init()`: a quiet descriptor held
  only until `settings_manager_start()` (true for can_isotp_esp's `esp_isotp`
  at WARN since it was written). The manager puts the descriptors below INFO
  back after each wildcard set.
- the level table had 32 slots for 56 registrants and dropped the ones past
  it without a line; it holds 96 now, an overflow logs E, and the occupancy is
  on the boot line `WICAN CAPS log_levels=`.

The legs:

- **t0** as found: the webhook's settings, the certificate sets.
- **t1** a CA and a certificate naming `tlsbyip.bench.example` only are made
  on the Pi, the HTTPS receiver started, the CA uploaded as the set
  `tlsbyip` and selected for the webhook (restart 1: `cert_set` is a
  setting), the webhook registered at `https://<pi ip>:8444/...`, interval 5 s.
- **t2** four pushes delivered, none failed, status `ok`: the chain verified
  through the set, the name not compared.
- **t3** no `esp-tls-mbedtls` W line among this run's ring lines;
  `http_client_manager: https to an address by IP: ...` is there once.
- **t4** `PUT /api/logs/level {"tag":"*","level":"info"}`: three more pushes,
  still no such W line (the wildcard does not undo the quieting; it is also
  what the settings do at boot, which restart 1 has already exercised).
- **t5** `PUT /api/logs/level {"tag":"esp-tls-mbedtls","level":"warn"}`: the W
  line comes with the next pushes. The knob works, and this leg is the bench's
  own proof that it would see the line if it were there.
- **t6** a wildcard again: quiet again.
- **t7** everything as found (restart 2): the webhook's settings read back
  equal, the certificate sets as found, no reset nobody asked for, no E line
  of `ha_webhooks`, `http_client_manager` or `log_manager`, the internal
  heap's floor.

## Records

- 2026-11-14 20:20, **the public-certificate leg**, by hand (not a stage: it
  needs a server of ours on the internet; the kit is kept outside the
  repository). An HTTPS receiver on that server with a Let's Encrypt
  certificate for its DNS name, issued that minute by the intermediate YR1,
  the name only and no address in it: the shape of a Home Assistant on SSL
  behind a DuckDNS name. The DUT on the image `09e67a3f6bf10e00` with NO
  certificate set, so its built-in bundle alone, over the internet:
  by IP 4 of 4 pushes delivered (the chain verified, the name not compared)
  and no `esp-tls-mbedtls` W line; by name 4 of 4 (the name compared); both
  URLs registered the way the integration registers them, by IP first, 3 of
  3; the control, by IP at the Pi's private-CA receiver without a certificate
  set: none delivered, two failed, `esp_err=ESP_ERR_HTTP_CONNECT` (skipping
  the name is not skipping the chain). **TLS PUBLIC PASS**, 19 checks, 14
  pushes at the receiver, no restart, the internal heap's floor 51,056 B. A
  curl with the public roots agreed with the device's view of the server:
  verified by name, refused by IP. The certificate was revoked afterwards.
- 2026-11-14 19:56, the same unit on the image `09e67a3f6bf10e00` (the one
  below plus data_logger's export answer without a log line, the
  uncommitted trees of that evening): **TLS BY IP PASS**, 19 checks, two
  planned restarts, 15 pushes at the receiver, the internal heap's floor
  49,700 B. Report `test-reports/2026-11-14_1956_tlsbyip.md`.
- 2026-11-14 19:33, unit 14c19f457fd9 on the changed image
  `a35960129339afea` (`v6.0.0p-beta.10-3-gadf7463-dirty`, components
  `03b2c8f-dirty`): **TLS BY IP PASS**, 19 checks, 104 s, two planned
  restarts; 15 pushes at the receiver, none failed; no `esp-tls-mbedtls` W
  line with the pushes or after the wildcard, two with the tag raised by
  name, none after the next wildcard; the manager's line once
  (`I (8898) http_client_manager: https to an address by IP: ...`); the
  internal heap's floor 51,280 B. The console of the run, both boots:
  `WICAN CAPS log_levels=57/96`, `WICAN HEALTH errors=0 warnings=4`. Report
  `test-reports/2026-11-14_1933_tlsbyip.md`, console
  `test-reports/logs/2026-11-14_1933_tlsbyip/console_tls_by_ip.log`.
- 2026-11-14 19:31, the same image against a real Home Assistant 2026.2.3 on
  SSL (the bench instance on rpi001, the ha-wican tree with the issue 110
  fix, the webhook at `https://192.168.8.228:8123`, the certificate naming
  `rpi001.local` only): the pushes by IP delivered, no `esp-tls-mbedtls` W
  line since the boot (the boot's own wildcard included), the manager's line
  once at 15.9 s, `log_errors` 0, no fault.
- 2026-11-14 18:22 to 19:26, the reproduction on the image before the change
  (`6.0.0-beta.10`, `0d7ef83a954e77ce`), the same Home Assistant as the
  receiver: 60 min, 240 pushes delivered, none failed, no E line, the
  internal heap steady (free 87 to 91 KB, the floor 50,760 to 49,940 B), and
  `health.log_warnings` 49 to 295: one W line per push. Then
  `wildcard_repro.py` on the same image, 40 s a step: 3 pushes and 3 W lines
  as it is; 3 pushes and 0 W lines with the tag set to error by name; 3
  pushes and 3 W lines again after `PUT /api/logs/level` for `*`:
  **REPRODUCED**, the wildcard drops the per-tag level
  (`rpi001:~/issue110/logs/soak.csv`, `wildcard_repro_old_fw.txt`).
- 2026-11-14 19:35, `.\test.ps1 tlschain` on the changed image: `TLS CHAIN
  PASS` (https by name through the built-in bundle, unchanged).

## Traps

- `cert_set` is a setting (reboot-to-apply), the URL is not: the set has to
  be selected and the DUT restarted before the webhook is registered, or the
  first pushes go out against the built-in bundle and fail
  (`esp_err=ESP_ERR_HTTP_CONNECT`, `mbedtls_ssl_handshake returned -0x2700`).
  A registration push keeps the stored `cert_set`.
- The firmware does not check a certificate's dates
  (`CONFIG_MBEDTLS_HAVE_TIME_DATE` is off), so the bench's 30 day certificate
  needs no care about the Pi's clock.
- The W count (`health.log_warnings`) is not asserted: other components may
  warn in the same minute. The bench reads the ring and counts the lines of
  the one tag.
- A leg that is refused on purpose (a chain the bundle does not know) costs
  IDF's own E lines, four per attempt (`mbedtls_ssl_handshake returned
  -0x2700` and the three that follow it), which the DUT's
  `health.log_errors` keeps until its next restart; and a push that is under
  way when the webhook is deleted finishes after it and leaves `failed` and a
  count of 1 in the stats of an unregistered webhook. A second `DELETE` and a
  planned restart put both back (done after the public-certificate leg).
- A real Home Assistant on SSL as the receiver (the run of 2026-11-14 that
  found this): `rpi001:~/issue110`, `bench.sh` and `leg.sh` there; Home
  Assistant's `http: ssl_certificate` and the same certificate set on the DUT.
