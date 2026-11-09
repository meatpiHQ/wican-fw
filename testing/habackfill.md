# Store and forward: the SD card log into Home Assistant

Command: `.\test.ps1 habackfill` (= `python tools/testbench/ha/ha_history_backfill_bench.py --dut localhost:18086 --dut-ip <DutIp> --pi rpi001` through an ssh tunnel to the DUT via rpi001; by hand through your own tunnel with `--dut localhost:<port>`; `--only b2,b3` picks legs, b0 and b6 always run; `--psu none` skips the supply part of b5)

Needs: the bench HA on rpi001 (`ha-bench.service`, started by the bench when it is down and stopped at the end then; `--keep-ha` leaves it) with the integration copied from `rpi002:~/workspace/ha-wican` (the `meatpi-multi-device` branch at or after the 2026-11-06 alignment) and `homeassistant.internal_url: http://10.42.1.1:8123` in its configuration.yaml; the DUT on the bench AP running a build with the export counters (2026-11-06); the ECU simulator on the bus answering `010C` at 500 kbit/s; the ECU sim v2 supply on its port; `internal/bench_ha_credentials.json` (the bench HA's owner login) or `--ha-user`/`--ha-pass`/`--ha-token`.

Index row and the other stages: [../TESTING.md](../TESTING.md).

**What it covers.** Ali's scenario of 2026-11-06: a car on the road records
its vehicle parameters, comes home, joins the home WiFi, and the Home
Assistant integration pulls what was recorded into each sensor's long-term
statistics before the device goes to sleep. The bench stands the drive in
with `logger test <rows> autopid EngineRPM <age_s> <step_ms>` (explicit
stamps in the past, the values `i * 0.5` one second apart: a series whose
hourly mean, min and max are known exactly), the absence with the device's
pushes switched off (`POST /api/webhook` with `enabled:false`, applied live)
for longer than the integration's staleness window, the return with the
pushes switched on again: the first push ends the gap at the integration,
which pulls `GET /api/logger/export` from its saved cursor and imports the
hours through the recorder's statistics API. The bench reads the
integration's diagnostics (`history_sync`: the source, the pages, the rows
fetched, used, deferred, the hours and PIDs imported, the cursor, the
follow-up, the device's logger state) and the recorder itself
(`recorder/statistics_during_period` over the websocket API) and holds the
seeded hours' mean/min/max to the series. Then the Catch up history button
(nothing imported twice; only the deferred rows of the hour in progress are
read again), and the sleep leg: the OWON at 12.5 V starts the 5 min
countdown, with less than 70 s left a three-page series is seeded in the
hour before this one and the button pressed: `GET /api/sleep` must report
`activity_s` and the entry moved out past the reads, the hour imported, the
countdown over at 13.5 V. Restore: the settings as found (one planned
restart), the custom AutoPID row gone, the log files of the run deleted,
the webhook registration as found (deleted when there was none), no
unexpected reset, no E line from ha_webhooks, data_logger, sleep_manager or
autopid. The HA entry for the bench unit stays (reloaded at the end).

**Why a custom row.** The fresh bench unit has no AutoPID rows (an empty
vehicle store), so the bench adds one custom row (EngineRPM, `010C`,
`(B3*256+B4)/4` in the 2026-10-24 frame of reference) in its own group for
the run: the push then carries `EngineRPM`, Home Assistant creates the
sensor, and the seeded series lands on it. The row is removed in the
restore (the table PUT back as found), and the car the device's own
detection learned at the first start with autopid on is forgotten again
when the store was empty before (`DELETE /api/autopid/vehicles/<key>`).

**The GPS track (b4g, 2026-11-06).** Two series under the fix's own names
(`gps_latitude`, `gps_longitude`, 100 rows each on the same stamps
through `logger test ... @<epoch_s>`, the values `i * 0.5`) recorded in the hour before last; `GET /api/logger/gpx`
must answer a GPX document with 100 `<trkpt>` whose latitude and
longitude follow the series in order, times one second apart, and a
trailer naming 100 points and no more; then the integration's
`wican.save_gpx` service (the entry's HA device id from the device
registry over the websocket API, 6 hours back) must save a file under
`www/wican/` with at least those points (read and removed on the Pi).

**The GPS track card (2026-11-06 evening).** The integration ships a
Lovelace card (`custom:wican-gpx-card`) that draws a track on Home
Assistant's own map. `node tools/testbench/ha/gpx_card_check.mjs <ha url>
<user> <password> <screenshot.png> rpi001` (the bench HA up; Playwright
from `tools/webui_preview`) writes a synthetic 120-point loop under
`www/wican/`, makes a storage dashboard with the card over it, opens it in
headless Chromium signed in as the owner, and holds: the card element
rendered, Leaflet paths drawn on `ha-map`, the map's view fits the track,
the status line names the points and the span, no page error; then removes
the dashboard and the file. Verdict `GPX CARD PASS`. By hand, not a
`test.ps1` stage: it needs the bench HA started (`habackfill` starts and
stops it itself).

**Records.**

- 2026-11-06, runs 1 and 2 (`test-reports/logs/habackfill_20261106/run1.log`,
  `run2.log`): rig and bench faults only, no firmware or integration
  defect in the feature's path. Run 1: the custom row was written before
  the device's vehicle detection (which rewrote the table), and `/ws/cli`
  had no bridge to the console. Run 2 (34 of 41 checks): the gap-end pull
  was dropped by the integration's 5 min rate limit (the run at the restart
  minutes earlier), and the later legs cascaded; the b5 series was seeded
  into an hour the watermark had passed because the hour rolled over, and
  its written target did not count the live rows. The integration now
  books a rate-limited gap end for the end of its window instead of
  dropping it; the bench waits the window out, counts written rows
  relative to the seed and seeds b5 only into an hour the watermark has
  not passed. What did hold in run 2: the pull through the export route
  (2 pages, 1537 rows, 2 hours imported, the cursor kept), the live rows
  of the hour in progress deferred with a follow-up booked, and the sleep
  entry pushed out to 60 s after the read at 12.5 V.
- 2026-11-06, run 3 (34 of 38 checks): the rate-limited gap end was
  booked and ran 90 s after the pushes returned (the fix of run 2 holds),
  the 1450 rows came in 2 pages, the live rows were deferred with a
  follow-up booked; but the seeded hour was rejected as older than the
  integration's TIME WATERMARK, which the previous run had left at the
  end of a later hour (the marks persist per entry in HA's `.storage`):
  the bench now clears the entry's marks file and reloads the entry at
  its start. The GPS track leg died on an `IncompleteRead`: a firmware
  defect in the new route (the `Content-Disposition` value was a stack
  buffer the route reused for the XML prolog before httpd sent the
  headers; httpd keeps a pointer, not a copy), fixed, rebuilt, flashed.
- 2026-11-06, run 4 (48 of 54): b3 green end to end (the main scenario);
  the GPS track paired a 1 Hz track into 50 points (the two series landed
  a second apart and the pairing tolerance of 1.5 s merged neighbours:
  the tolerance is 0.2 s and the series share stamps through `@<epoch_s>`
  now); b4 took b3's result for its own (the PC's clock behind the Pi's:
  the waits compare with the previous run's `finished_at` now); the sleep
  leg imported its hour from 1274 of 3000 rows (3 pages, no more).
- 2026-11-06, run 5 (52 of 53): the GPS track and the button legs green;
  the sleep leg read 2 pages and stopped although the device had more
  (the device's cursor at 197 KB of a 306 KB file). Home Assistant's log
  named it: `skipped 1 undecodable line(s)`. The export route forwarded a
  read window raw when the page's byte budget ran out exactly at the
  window's start (the walk took "nothing consumed" for "no line
  boundary"): the cut tail row and the cursor line went out as one line,
  the integration saw no cursor line, took the page for the last and, in
  run 4, imported the hour from the rows before the cut. About one page
  in sixteen. Fixed in the firmware (the walk is the pure
  `dl_export_walk()`, host-tested) and guarded in the integration (a
  page without a cursor line voids the run, the stored cursor stays, a
  run a minute later reads again).
- 2026-11-06, run 6 (`run6.log`): **HA BACKFILL PASS, 54 checks**, on the
  export-walk build and the integration with the cut-page guard. The gap
  end pulled 2 pages (1451 rows, the seeded hour imported exactly, the
  live rows deferred with the follow-up booked), the button read the
  file again and imported nothing twice, the GPS track came back as 100
  paired points and the service saved it, the sleep leg's pull read 5
  pages to the end of the file (the device's cursor at the file size) and
  imported the hour from all 3000 rows while the countdown was pushed out
  to 60 s; the restore left the settings, the table and the card as
  found, no unexpected reset, no E line, ha-bench stopped again.

**Traps.** The integration runs an automatic pull at most every 5 min
(`HISTORY_SYNC_MIN_INTERVAL`): a gap that ends inside that window is
booked for the window's end, so a bench that restarts the device (the
probe at the entry reload runs a pull) and comes home 4 min later waits
up to 5 min for its pull (b3 waits 6). The writer lands 256 rows per 5 s
lap: a seed of N rows is on the card about N / 50 s later; count the
written rows relative to the count before the seed, never against an
absolute number (the live rows move it). The hour can roll over during a
12 min run: an hour that was "the one still running" at b3 is imported
as a complete hour by the next pull, and the watermark then passes it;
a later series must go into a newer hour or it is read and rejected
(b5 reads the watermark first). The marks (the time watermark, the
device cursor) persist per entry in HA's `.storage/wican.history_<entry_id>`:
a second bench run on the same day would seed hours older than the
watermark the first left; the bench deletes that file and reloads the
entry at its start (a real device never meets this: its drives are
newer than what was imported). `httpd_resp_set_hdr` keeps a pointer to
the value, not a copy: a header built in a buffer that is reused before
the first chunk goes out corrupts the response (the GPS route's
`Content-Disposition`, run 3). The PC's clock runs seconds behind the
Pi's: a wait that compares HA's `finished_at` with the PC's clock takes
the previous run for the new one (run 4, b4); the bench compares with
the previous run's `finished_at`. The stamps of `logger test` are taken
from the device's now at each command: two series meant to share stamps
use the absolute start `@<epoch_s>` (run 4, b4g: the series landed 1 s
apart, and the GPS route's pairing tolerance of 1.5 s then merged the
neighbours of a 1 Hz track into 50 points; the tolerance is 0.2 s now).
The integration imports nothing from the hour still running
(live wins there until the hour has ended): the seeded series must sit in
a complete hour, which is why the bench starts it 5 min into the hour
before last (b2) and 1 min into the hour before this one (b5), and why
the live rows of the current hour show up as `rows_deferred` with a
follow-up booked past the top of the next hour, which the bench does not
wait for. Rows older than the integration's time watermark are never
re-imported: a second series must be in a newer hour than the first
(b5 is). The integration's rate limit (5 min between automatic runs) does
not apply to the button; the gap trigger needs a push after a silence
longer than `max(post_interval * 5, 120 s)`. The HA websocket wants the
`auth` message before anything else; the bench's minimal client in
`lib/wsmin.py` serves. `POST /api/webhook` answers 201 for a first
registration and 200 after. Through the tunnel the `/ws/cli` channel must
be on (`websocket_manager.channels[ws_cli].enabled`, off on the fresh unit;
the bench switches it on for the run and back). The bench HA's
`internal_url` was the WireGuard address 10.9.0.3 from the 2026-09-09 LTE
test until this day; the integration registers that URL on the device, so
it has to be the bench AP's address for a station-side run.
