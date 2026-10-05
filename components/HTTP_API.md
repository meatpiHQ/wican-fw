# WiCAN Device HTTP API: Requirements

> Status: **IMPLEMENTED** (2026-07-03; route map last reconciled
> 2026-07-04): the contract the web UI is built against. The `api_http`
> glue component (Architecture §10) implements the core-component routes
> (on-target suite green, incl. the submit-then-reboot cycle); feature
> components (`wifi_manager`, `battery_monitor`) register their own
> `/api/<domain>` routes via `<comp>_register_http()` (called by main in
> HTTP-bearing compositions); `websocket_manager` owns the `/ws/*`
> namespace.
>
> This file owns the **conventions and the route map**. The **full
> per-endpoint reference** (request/response examples, status codes, errors)
> lives next to each component and is the authoritative detail:
>
> | Component | Endpoint reference |
> |---|---|
> | settings (all components) | `settings_manager/HTTP_API.md` |
> | device status | `dev_status_manager/HTTP_API.md` |
> | reboot history + reboot | `restart_tracker/HTTP_API.md` |
> | logs / crash ring / levels | `log_manager/HTTP_API.md` |
> | file manager (list/info/down/up/delete/mkdir) | `filesystem/HTTP_API.md` |
> | wifi status + scan | `wifi_manager/HTTP_API.md` |
> | firmware update (upload + progress) | `ota_manager/HTTP_API.md` |
> | battery voltage | `battery_monitor/README.md` (single route) |
> | RTC time (read/set) | `rtc_manager/README.md` |
> | certificate sets (list/upload/delete) | `cert_manager/README.md` |
> | IMU activity/accel/temp | `imu_manager/README.md` (single route) |
> | WebSocket channels (`/ws/*`) | `websocket_manager/README.md` |
>
> Change protocol (**mandatory**: Standard rev 2.3 §6): a change to any
> route updates the component's endpoint reference **and** the map here in
> the same commit; the UI is built against these files. An undocumented
> route does not exist as far as the product is concerned.

## 1. Conventions

- **Everything lives under `/api/`**, except WebSocket channels, which
  live under **`/ws/`** (websocket_manager; paths are settings-defined and
  must start `/ws/`). These are the TWO reserved namespaces; the
  `http_server_manager` catch-all serves assets for every other path.
  Specific routes always win over the catch-all (§9.3). The catch-all
  also serves two on-demand asset folders registered by `web_ui_v2`
  (2026-09-06): `/cache/*` → `/data/cache` and `/sdcache/*` →
  `/sd/cache` (MIME by extension, ETag/304, `Cache-Control: max-age=3600`).
  The UI fills them through `/api/fs/upload` (today the dashboard's
  uPlot chart library under `cache/www/`) and nothing there is needed
  for the firmware to work; a missing file is a plain 404.
- **JSON in, JSON out** (`application/json`), except the log-ring dump
  (`text/plain`). Errors: `{"error":"<human message>"}` with 4xx/5xx.
- **GET never mutates. Mutations are PUT/POST/DELETE.** Reboot-affecting
  writes follow the settings model (§4.2): persist → respond → reboot via
  `restart_tracker_restart()`, never a raw `esp_restart()`.
- **Network-trust lockdown (2026-07-08):** `http_server_manager` runs an
  optional per-request admission gate in front of EVERY route (API, the
  UI catch-all, and `/ws/*` handshakes). When the WiCAN is joined to a
  WiFi network its user marked untrusted (`wifi_manager` `*_trusted`
  flags), any request arriving via the STA address gets **403
  `{"error":"configuration disabled on this network"}`** (WS: handshake
  refused). The device's own AP + the USB link are non-STA and keep full
  admin: the intended management path. Outbound clients (autopid
  http.post, MQTT) don't go through httpd and are unaffected. The whole
  surface is swept by an automated live test (every route × every
  method + WS). New routes are covered automatically:
  the gate wraps at registration, nothing per-handler to remember.
- **Never expose secrets**: settings GETs must redact password fields
  (`sta_password`, `ap_password`, `fallbackN_password` → `""` plus
  `"secret":true` in the schema is a later refinement; v1 redacts to `""`).
  A PUT with `""` for a redacted field means "keep the stored value".
- **Layering**: core components never depend on the HTTP server. Their routes
  are implemented by the `api_http` glue (depends down on the cores +
  `http_server_manager`). Feature components (`wifi_manager`, CAN, …)
  register their own `httpd_uri_t` tables at init (§9.1).
- Auth/session handling is out of scope for v1 (AP-mode setup UI); the
  namespace choice keeps a later auth middleware to one prefix.
- **Every `/api/*` route is also reachable over BLE (2026-09-21):** the
  `ble_http` component carries framed HTTP requests on a BLE stream
  channel (FFF3/FFF4) and replays them against this server over loopback,
  so a paired phone app gets this whole document without WiFi and a new
  route needs no BLE work. The loopback request passes the network-trust
  gate (it never arrives via the STA address) and carries
  `X-WiCAN-Transport: ble`. Contract: `ble_http/BLE_HTTP_PROTOCOL.md`;
  GATT surface: `ble_manager/BLE_API.md`. Nothing outside `/api/` (UI
  assets, `/ws/*`) is tunnelled.

## 2. Settings (all components), via `api_http`

The generic surface every component's config UI uses (standard §6):

| Route | Method | Behavior |
|---|---|---|
| `/api/settings` | GET | list registered components, enriched (2026-07-05): `{"components":[{"name","version","degraded","pending_reboot"},…]}`, one call for the UI's overview page |
| `/api/settings/<name>` | GET | current (pending) object + `"degraded":true` (§4.3 fallback) + `"pending_reboot":true` (persisted ≠ boot-applied: "restart to apply"); PUT strips both if a UI echoes them back |
| `/api/settings/<name>` | PUT | full-object replace; validate + persist; 400 with the `on_validate` message on reject |
| `/api/settings/<name>/schema` | GET | the JSON Schema: UIs self-describe, no per-component UI code for forms |
| `/api/settings/submit` | POST | end-of-batch: responds `{"reboot":true|false}`, then reboots iff any PUT reported `changed=true` (no-op submits never reboot), via `restart_tracker_restart(CONFIG_APPLY, CONFIG_SERVER)` |
| `/api/settings/backup` | GET | the whole configuration as ONE document (offline backup / device transfer, 2026-07-05): per-component `{version, data}` + device metadata; **passwords NOT redacted** (the one settings GET returning secrets; UI treats the file as sensitive) |
| `/api/settings/backup` | POST | restore a backup: **all-or-nothing** (every component dry-run-validated first, incl. `on_migrate` for older backups: before anything persists; 400 + per-component errors otherwise), unknown components skipped+reported, reboots iff changed |
| `/api/settings/factory_reset` | POST | wipe the settings partition to factory defaults (2026-07-05; settings ONLY: /data, SD, NVS untouched); requires `{"confirm":"factory-reset"}` else 400 and nothing wiped; reboots via `restart_tracker(FACTORY_RESET, WEB_UI)` |

## 3. Device status: `dev_status_manager` via `api_http`

| Route | Method | Behavior |
|---|---|---|
| `/api/status` | GET | `{"bits":{"sta_connected":true,"ap_enabled":false,…}` (every named `DEV_STATUS_BIT_*` incl. `motion`, `time_synced`, and the interface_manager arbitration bits `sta_suspended`/`ap_suspended`/`ble_suspended`: WHY an interface is down while its config is untouched), `"network_connected":bool` (the STA\|ETH mask), `"uptime":"1d 02:03:04"`, `"version":"…"`, `"partition":"ota_0"`, `"boot_count":N, "unexpected_resets":N,` **`"memory":{internal/psram × total/free/min_free/largest_block}`** (§12b: largest_block is the fragmentation signal), `"temp_c":33.1` (die temperature, 2026-07-08; omitted on sensor error), **`"health":{log_errors,log_warnings,flash:{writes,write_bytes,erases,erase_bytes},caps:{settings/cmdline/bridge_ep × used/cap},faults:N}`** (2026-07-19 silent-failure net: the bench asserts zero errors, flash budgets, registry headroom, zero faults; boot prints the same as `WICAN HEALTH/FLASH/CAPS/FAULTS`)} |
| `/api/info` | GET | `{"device_type","model","hw_version","fw_version","device_id","mac","api_level"}`: cheap, dependency-free identity probe (same keys/casing as the webhook push `status` section; the HA integration verifies identity with this BEFORE control commands; `mac` matches the mDNS TXT record). Owner: `api_http` glue |
| `/api/status/tasks` | GET | Task monitor (JSON since 2026-07-08: was text/plain): `{"cores":2,"total_us":N,"tasks":[{"name","state"(X/R/B/S/D),"core"(-1=unpinned),"prio","stack_hw"(never-used bytes; small = near overflow),"runtime_us"}]}` busiest first. CPU% = client-side delta between two polls: `Δruntime_us/(Δtotal_us×cores)`; IDLE deltas → system load. Full spec: `dev_status_manager/HTTP_API.md`. Consumed by web-UI `#/monitor` + `system -t` |

One poll answers the dashboard header. No mutations: bits are owned by the
components that publish them.

**Bit ↔ publisher map** (audited 2026-07-07; a bit not listed under a live
publisher is reserved and always `false`):

| Bit (JSON name) | Publisher |
|---|---|
| `awake`, `sleep`, `wake_voltage_ok` | sleep_manager (main sets `awake` at boot) |
| `sta_connected`, `sta_enabled`, `ap_enabled`, `sta_ap_overlap` | wifi_manager |
| `mqtt_connected` | mqtt_manager |
| `ble_connected`, `ble_enabled` | ble_manager |
| `sdcard_mounted` | external_storage |
| `autopid_enabled`, `autopid_idle` | autopid (added 2026-07-07: sleep_manager gates its teardown on `autopid_idle`) |
| `time_synced` | rtc_manager |
| `vpn_enabled` | vpn_manager (both types) |
| `eth_connected` | usb_host_manager (USB-Ethernet uplink; part of the `network_connected` mask) |
| `motion` | imu_manager |
| `sta_suspended`, `ap_suspended`, `ble_suspended` | interface_manager (arbitration: WHY an interface is down) |
| `home_mode`, `drive_mode`, `smartconnect` | **RESERVED**: legacy concepts, no v6 publisher yet (future interface_manager/connection rules); always `false` |

## 4. Reboot forensics + reboot: `restart_tracker` via `api_http`

| Route | Method | Behavior |
|---|---|---|
| `/api/faults` | GET | Latched device fault codes (2026-07-19: the automotive-DTC idea for the firmware): `{"faults":[{"code":"boot_errors","detail":"…","count":N,"first_time":unix,"last_time":unix},…]}`. Raised on structural problems (boot errors, registry headroom, flash budget/churn), persisted to NVS, survive reboot+power-cycle until MANUALLY cleared. CLI: `faults` / `faults -c` |
| `/api/faults/clear` | POST | `{"cleared":true}`: the manual "mode 04". |
| `/api/restart/history` | GET | `{"boot_count":N,"unexpected_resets":N,"elf_sha":"<16 hex>","records":[{"seq":…,"reason":"software","planned":true,"planned_reason":"config_apply","source":"config_server","flags":0,"boot_time":…,"time_valid":true,"request_time":…,"request_uptime_ms":…,"crash":{…}},…]}` (newest first, to-str names not raw enums). `elf_sha` is the running image: the first 16 hex characters of its ELF file's SHA-256. `crash` (2026-10-05, the crash note) is only on a record whose boot followed a crash that went through the panic handler, and says where the run BEFORE that boot crashed: `summary` (one line, as the boot log prints it), `kind` (`exception` / `abort` / `int_wdt` / `task_wdt` / `debug`), `reason`, `cause`, `pc`, `excvaddr`, `core`, `task`, `in_isr`, `uptime_s`, `text` (the abort / assert / stack overflow message), `backtrace` (up to 16 hex PCs, those of the console's `Backtrace:` line), `backtrace_more`, `backtrace_corrupt`, `other_core`, `nested`, `elf_sha` (the image that crashed), `same_image`, `complete`. Field reference: `restart_tracker/HTTP_API.md`. The PCs become function names with `python tools/crash_decode.py --url http://<device>`, which takes the ELF only when its id is the note's. **2026-10-05, the crash-loop brake and the stored crash report**: every record also carries `mode` (`normal` / `park` / `park_bare` / `safe`: how that boot ran) and `settled` (that run stayed up 600 s: a crash of it is not part of a loop); top level `brake` = `{"verdict":"normal","streak":N,"limit":3,"parks":N,"settled":bool,"settle_s":600,"report_budget":0-4}` (three runs in a row that crash before they settle park the device asleep, LED breathing red; the count is RAM and a power cycle starts it again); top level `report`, present only while a crash report is stored in flash (NVS: it outlives a power cycle, the notes in `records` do not) = `{"stored_time":…,"time_valid":bool,"firmware":"<version of the image that crashed>","streak":N,"parked":bool,"crash":{…as above…}}`. `planned_reason` gains `park_retry` (a parked device started again; `source` `park` = its timer, `button`) |
| `/api/restart/report` | GET | the stored crash report as `text/plain`, the lines a user sends on (`WiCAN crash report`, `Device:`, `Firmware:`, `Image:`, `Stored:`, `Loop:` when it was a loop, `Crash:`, `Backtrace:`, `Core N:`; 1024 bytes at most). The console (`restart_tracker --report`) and safe mode's page give the same text; `python tools/crash_decode.py --report <file>` decodes it. `404 {"error":"no crash report is stored"}` when there is none |
| `/api/restart/report` | DELETE | forgets the stored report (one NVS erase): `{"cleared":true}`. The notes in RAM stay |
| `/api/restart` | POST | respond `{"ok":true}`, wait ≈1 s (flush), then `restart_tracker_restart(USER_REQUEST, WEB_UI, 0)` |

## 5. Logs: `log_manager` via `api_http`

The remote-debugging surface (runtime knobs, §9.4: ephemeral, never persisted
here; persisted defaults go through `/api/settings/log_manager`):

| Route | Method | Behavior |
|---|---|---|
| `/api/logs/ring` | GET | `text/plain` dump of the PSRAM crash ring (chronological, spans reboots: the first thing support asks for) |
| `/api/logs/ring` | DELETE | clear the ring |
| `/api/logs/status` | GET | `{"dropped":N,"sinks":[{"name":"console","enabled":true},…]}` |
| `/api/logs/level` | PUT | `{"tag":"wifi_manager","level":"debug"}` → `log_manager_set_level()`; `"tag":"*"` allowed |
| `/api/logs/sink` | PUT | `{"name":"ring","enabled":false}` → `log_manager_sink_set_enabled()` |

Ring dumps can be ~16 KiB: stream in chunks (internal-RAM chunk buffer,
§9.4 serving rules).

## 6. Filesystem: `filesystem` via `api_http`

The UI file manager (write surface un-deferred by meatpi 2026-07-04:
upload any file type, download logs/configs, delete, mkdir; AP-mode trust
model until the auth story). Also serves the settings UI's `format:"file"`
picker (REVIEW §6.1):

| Route | Method | Behavior |
|---|---|---|
| `/api/fs/list?path=/data/web` | GET | `{"path":"/data/web","entries":[{"name":"x.svg","dir":false,"size":123},…]}`; 400 on invalid path (component's own validation) |
| `/api/fs/info?path=/data` | GET | `{"total":N,"used":N}` |
| `/api/fs/download?path=…` | GET | streamed file, `Content-Disposition: attachment` (logs/configs to the browser); 409 `file in use` for a file a writer holds open (the logger's active file: pause `/api/logger/gate` first, 2026-09-07) |
| `/api/fs/upload?path=…` | POST | multipart (HTML form) or raw body, **STREAMED: no size cap** beyond free space (8 MB live-verified to /sd); ATOMIC (temp+rename at commit); 503 while another transfer runs |
| `/api/fs/file?path=…` | DELETE | delete a file / empty dir |
| `/api/fs/mkdir?path=…` | POST | create dir + parents |

The write surface (upload/delete/mkdir) was un-deferred by meatpi
2026-07-04 and is LIVE: AP-mode trust model until the auth story; the
settings partition is unreachable by construction, traversal refused.

## 6b. Firmware update: `ota_manager` via `api_http`

(Full reference: `ota_manager/HTTP_API.md`.)

| Route | Method | Behavior |
|---|---|---|
| `/api/ota/upload` | POST | firmware image, multipart (`firmware=@…`, the HTML form path) **or** raw `application/octet-stream` body; streams into the OTA session; on success responds `{"ok":true,"received":N,"partition":"ota_1","reboot":true}` then self-reboots via `restart_tracker_restart(OTA_APPLY, WEB_UI)` |
| `/api/ota/status` | GET | `{"state":"idle|receiving|ready|failed","received":N,"total":N,"error":"…","target":"ota_1"}`: poll for progress |

During a session the LED shows the CRITICAL indication (fast-blink red,
main's glue): do not power off.

## 6c. Battery: `battery_monitor` registers its own route

| Route | Method | Behavior |
|---|---|---|
| `/api/battery` | GET | `{"voltage":11.51}` (latest averaged reading, volts; `null` before the first sample) |

Thresholds/events are an in-firmware surface (watch registration,
`battery_monitor/README.md`): no HTTP mutation.

## 6d. RTC / time: `rtc_manager` registers its own routes

All times are **UTC** (the firmware pins `TZ=UTC0`; timezone is a
presentation concern of the client).

| Route | Method | Behavior |
|---|---|---|
| `/api/rtc` | GET | `{"time":"…Z","valid":true,"rtc":"…Z","sntp":{"enabled":true,"server":"pool.ntp.org","last_sync":"…Z"}}`: `time` = system clock, `valid` = trusted this boot (RTC restore / SNTP / manual), `rtc` = the chip's own reading (`null` when implausible: fresh board / drained caps), `last_sync` = last successful SNTP (`null` = none this boot) |
| `/api/rtc` | POST | `{"epoch":1751600000}` (UTC seconds, 2020..2099) → sets the system clock AND the RTC, marks TIME_SYNCED; responds with the GET payload. The manual path for AP-mode/no-NTP: the browser knows the time. 400 outside range |
| `/api/rtc/sync` | POST | on-demand SNTP sync (primary then fallback server; blocking, ≤~32 s worst case: UI shows a spinner). 400 when offline, 500 when no server answered, else the fresh GET payload |

SNTP behavior (config via `/api/settings/rtc_manager`): syncs on every
internet (re)connect + every `sync_interval_h`; `ntp_server` +
optional `ntp_server2` fallback are user-configurable.

## 6e. IMU: `imu_manager` registers its own route

| Route | Method | Behavior |
|---|---|---|
| `/api/imu` | GET | `{"activity":"stationary\|active\|unknown","accel":{"x":0.01,"y":0.02,"z":1.00},"temp":34.5}`: accel in g (expect ≈1 g on one axis at rest: gravity), die temp °C; `null` fields when the chip is unreadable |

Motion events are an in-firmware surface (`imu_manager_subscribe`, see
`imu_manager/README.md`): no HTTP push; poll `activity` or watch the
`motion` bit in `/api/status`.

## 6e2. LED: `led_manager` registers its own routes (2026-07-05)

| Route | Method | Behavior |
|---|---|---|
| `/api/led` | GET | what the LED shows right now (the arbiter's winner): `{"priority":"idle\|status\|alert\|critical","mode":"off\|solid\|blink_slow\|blink_fast\|breathe","r":0,"g":0,"b":60}`; `{"priority":null}` when the LED is unavailable |
| `/api/led` | PUT | `{"r":0-255,"g":..,"b":..[,"mode":"solid\|off\|blink_slow\|blink_fast\|breathe"]}` (`breathe`, 2026-10-05: about 1 s up, 1 s down, 2 s dark, timed by the LED chip itself), sets the **ALERT** indication (the user slot, same as the `led -c` CLI). 400 on bad values |
| `/api/led` | DELETE | releases the ALERT indication; the arbiter falls back (STATUS/IDLE) |

REST clients drive ONLY the ALERT priority: firmware-internal
indications (STATUS activity, OTA's CRITICAL do-not-power-off) are never
writable from outside, so the priority ladder stays honest. Idle
color/mode config strictly via `/api/settings/led_manager`.

## 6e3. Data-path stats: `api_http` glue over bridge/socket/ws managers (2026-07-05)

Read-only observability for user dashboards (API-first §1b): the
configured entries come from each component's settings, the live
counters from its stats API. `up:false` (no `stats`) = configured but
not running (disabled, or its endpoint isn't registered).

| Route | Method | Behavior |
|---|---|---|
| `/api/bridges` | GET | `{"bridges":[{"name","a","b","translator","enabled","up","stats":{"a2b_chunks","b2a_chunks","a2b_bytes","b2a_bytes","send_errors","codec_errors"}}]}` |
| `/api/sockets` | GET | `{"servers":[{"name","proto","port","enabled","up","stats":{"clients","bytes_in","bytes_out","rx_drops","tx_drops","reconnects","refused"}}]}` |
| `/api/obd_chip` | GET | (2026-09-08, `obd_chip` own route) `{"ready","claim":"none\|command\|monitor\|exclusive","client_idle_ms","uart":{"rx_bytes","rx_chunks","rx_max_chunk","rx_overflows","rx_buffered","tx_bytes"},"subscribers":[{"idx","name","dropped","queued","depth"}]}`: the MIC chip's wire + fan-out counters (several subscribers share the name `bridge`; `idx` tells them apart): read before/after a stream to place a loss (chip → UART ring `rx_overflows` → fan-out `dropped` per subscriber → `/api/bridges` → `/api/sockets`). `client_idle_ms` = ms since a bridged app last wrote (autopid yields below 10 s); `eeprom_guard`: `{"rewrites","blocked","protocol_saves"}` (2026-09-16: the driver rewrites ATSP->ATTP / ATM1->ATM0 on every TX path and refuses ATPP/ATSD/ATCV/STWBR; a refused raw command gets the ELM `?` back, a refused `obd_chip_request()` returns ESP_ERR_NOT_SUPPORTED; `protocol_saves` (2026-10-01) = real `ATSP` writes this boot through `obd_chip_protocol_save()`, 0 or 1: the vehicle detection job teaches the chip its base protocol once) |
| `/api/ws` | GET | `{"channels":[{"name","path","mode","max_clients","enabled","up","stats":{"clients","frames_in","frames_out","bytes_in","bytes_out","rx_drops","tx_drops","refused"}}]}` |

## 6e4. AutoPID: `autopid` registers its own routes (2026-07-06)

| Route | Method | Behavior |
|---|---|---|
| `/api/autopid` | GET | dashboard view: `{"groups":[{"name","enabled","period_ms"}],"params":[{"name","unit","value"\|null,"ts_us"}],"stats":{"running","paused_voltage","paused_client","paused_diag"(a diagnostic tool holds the bus: the UDS Tool / J2534 Exclusive bus option, 2026-09-16),"polls_ok","polls_failed","pids","filters","period_floor_ms","sub_floor_pids","now_us"}}`: value age = `now_us - ts_us`; `sub_floor_pids > 0` = periods configured below the measured ~53 ms/request chip floor (accepted, can't be honored: UI should warn; see `autopid/BENCHMARKS.md`); `paused_client` (2026-09-08) = the poller is yielding the chip to an external ELM app (a bridged TCP/BLE/USB/WS client wrote within the last 10 s: legacy `DEV_AUTOPID_ELM327_APP_BIT` parity), `running` is false meanwhile. **Bus guard (2026-10-03):** `stats.paused_bus` = the poller is parked because the vehicle bus runs at another bit rate than the OBD protocol in use (nothing is written to the chip); `"bus_guard":{"bus":"unknown\|silent\|live\|unreadable","bus_kbps":N,"verdict":"allow\|search\|park","parked":bool,"reason":"<one sentence>"}` = what the native controller heard before the chip was allowed to talk and what was decided (`search` = the chip's own protocol search is used instead of the stored protocol). The same sentence is the `error` of the HTTP 409 the chip jobs answer while parked: `POST /api/autopid/std_scan`, `/vehicles/detect`, `/test`, `/dtc/scan`, `/dtc/clear`. **The tables' own protocols (2026-10-05):** `bus_guard.refused` = rows and jobs NOT sent since boot because a chain of the tables (a row's `init`, `std_init` / `custom_init` / `specific_init` or the vehicle's own init, an AT `cmd`, `dtc_init`) sets a CAN protocol at another bit rate than the bus runs at; with `refused` > 0 also `refused_reason` (the last one's sentence: `the init of row Soc sets protocol 6 (500 kbit/s) and the vehicle bus runs at 250 kbit/s: not sent`) and `refused_ms` (milliseconds since it). Such a row counts in `polls_failed`; `POST /api/autopid/test`, `/dtc/scan` and `/dtc/clear` answer 409 with the sentence. A silent bus refuses nothing. **J1939 rows (2026-10-03, TASK_j1939_wwh.md phase 5):** `stats.passive_ok` / `passive_failed` = looks at the J1939 listener's store for the PGN rows (the group was there / nobody sent it), `passive_published` = looks that published a message the row had not published before, `j1939_listening` = the listener is up; the PGN rows run through every pause that is about the chip (`paused_client`, `paused_diag`, `paused_bus`, a chip job) and stop under the voltage pause only with `pause_mode` = `all`. **Phase 6 (2026-10-03):** `passive_requested` = requests the `?` rows sent on the J1939 network (`j1939` `mode` active with an address held: `j1939_active`), `passive_refused` = looks a `?` row failed because the controller had answered its request negatively (the row is held for a minute); a `?` row asks at its period, once a second at most, and not while `paused_diag` (the reading goes on) |
| `/api/autopid/data` | GET | the LEGACY-shape flat snapshot `{"Name":value,…}` (only parameters that have polled at least once) |
| `/api/autopid/config` | GET | the PID/filter tables file verbatim (`{"groups":[],"pids":[],"filters":[]}` when none saved) |
| `/api/autopid/config` | PUT | full tables JSON ≤256 KB: validated (expressions dry-run, group refs, bounds), atomically saved to `/data/autopid/config.json`, then reloaded **LIVE** (no reboot). 400 + `{"error":…}` on any invalid entry; the old tables keep running |
| `/api/autopid/std_scan` | POST | the vehicle DETECTION job (the async standard-PID support scan; `POST /api/autopid/vehicles/detect` is the same job): one-shot task that pauses polling and runs three phases (status `phase`): `protocol` (which protocol and which OBD dialect answer: with `std_protocol` "0" a walk over the ISO 15765-4 protocols the bus guard allows for this bus, `0100` = OBD-II then `22F400` = ISO 27145 / SAE J1979-2 on each; a pinned protocol is asked in both dialects; since 2026-10-03, before that the chip's own `ATTP0` search, which never finds a UDS-dialect vehicle), `vin` (the responder set behind the fingerprint, then `0902` with fallback `22F190` on the engine ECU, or `22F802` ECU by ECU on a UDS-dialect vehicle), `pids` (the support bitmap walk: 0100/0120/…/01A0 multi-ECU OR-merged, or 22F400…22F4E0 per ECU), `network` (2026-10-03, phase 5: is the vehicle network J1939, from the listener's store when it runs, else a one second listen-only sample of a live bus through can_manager; the built-in SPN rows of the groups heard join the result; a vehicle that answered no OBD request but broadcasts J1939 gets the dialect `j1939` and its source addresses as the responder set). The result goes to the vehicle store first (a known car comes back with its own tables; an unknown car becomes a new entry polled on the standard rows found, `pending_profile:true`; the chip learns the base protocol once), then to `std_scan.json`, then polling resumes. 202 `{"started":true}`; 409 while one is running, or with the bus guard's sentence when nothing may be transmitted. A failed job says why in `error`: `no ECU response (ignition on?)`, or `the vehicle bus runs at N kbit/s; OBD is asked at 250 or 500, so nothing was sent`. UI shows "ignition ON" before calling. The poller starts this job by itself once per boot when first contact meets an unknown car (second pass, 2026-10-01) |
| `/api/autopid/std_scan` | GET | job status: `{"status":"idle\|running\|done\|failed","phase":"idle\|protocol\|vin\|pids","found":N[,"error"],"ts":epoch,"stored":bool}` (`phase` names the running step, `idle` otherwise; 2026-10-01) |
| `/api/autopid/config` | PUT (note, 2026-10-03) | **J1939 rows:** a `pids[]` row whose `cmd` reads `PGN:<hex>[@<source>][?]` is a J1939 parameter group read from the listener's store (`PGN:F004` engine controller 1 from the lowest source heard, `PGN:FEEE@0` pinned to source 0, decimal or `0x..`, `PGN:FEE5?` a group sent on request); any `type`, the same `parameters` and expressions, B0 = the first data byte, J1939 words little-endian (`B3+B4*256`); `init` and `rxheader` on such a row are refused (400 `pid N: init and rxheader do not apply to a PGN row`), a malformed one is 400 `bad PGN command`. Such rows never touch the OBD chip; period 0 means "looked at every 20 ms, published when newer". **`?` rows since phase 6:** in `j1939` `mode` active the row sends a Request for its group (to the pinned source, else to everyone) at its period, floored to 1 s, and publishes the answer at the next look; a negative acknowledgment fails the row (`passive_refused`) and holds the asking for 60 s; a diagnostics hold (`paused_diag`) stops the asking, not the reading; in listen mode the row reads what another node asked for |
| `/api/autopid/std_scan/result` | GET | the stored `/data/autopid/std_scan.json` verbatim: `{"version","protocol","protocol_detected","dialect","uds_protocol_id"?,"vin","fingerprint","supported":[{"pid","cmd","name","init"?,"parameters":[{"name","expression","unit","class"[,"min","max"]}]}],"found","ts","key","known","name"}`: entries are ready-made config rows (expressions generated from the SAE table); scan once, store, never auto-rescan. `protocol_detected` = the protocol that answered (or the pinned setting), `dialect` = `obd2` or `uds` (2026-10-03: ISO 27145 / SAE J1979-2; its rows read `"cmd":"22F40C"`, expressions one byte further in (`[B3:B4]*0.25`), and `"init":"ATSH18DA58F1"` = the ECU that owns the PID, asked alone), `uds_protocol_id` = the `22F810` byte when the vehicle answers it, a PID the table cannot decode gets no entry, `vin` / `fingerprint` = "" when none; `key` = the vehicle store entry the result landed on (`""` when nothing identifiable answered), `known` = that entry existed before this run, `name` = its name (second pass, 2026-10-01). **Since 2026-10-03 (phase 5):** `j1939` = a J1939 network was heard (alone, dialect `j1939`, or beside the OBD dialect of an EU truck), `j1939_listening` = the listener was up for it (false = the result came from a bus sample and the native bus + the listener need enabling, `can_manager {enabled, baud, silent:true}` + `j1939 {enabled}`, one restart: the Quick Setup stages that), `bus_kbps` = the live bus's bitrate (0 = none read); J1939 rows read `{"pgn":"F004","spn":190,"cmd":"PGN:F004","name":"EngineSpeed","parameters":[{"name","expression":"(B3+B4*256)*0.125","unit","class","min","max"}]}` (one row per SPN of the built-in table, `min`/`max` = the operational range so the clamp drops not-available and error values) |
| `/api/autopid/vehicles` | GET | the vehicle store (second pass, 2026-10-01, TASK_quick_setup.md): `{"version":1,"current":"<key>"("" = none),"max":8,"vehicles":[{"key"(the VIN, or "fp:<8 hex>" for a car without a readable VIN; fixed at first contact),"vin","fingerprint"(8 hex over the responder set, "" = none),"name","protocol"("6".."9"/"1".."5"/"A".."C", "" = none learned),"chip_protocol"(the last ATSP saved to the chip for this car, "" = never),"dialect"("obd2" \| "uds" \| "j1939": how the car's legislated data is asked, 2026-10-03; a store written before has none = "obd2"; `j1939` = no OBD dialect at all, the chip stays parked for this car),"j1939"(bool, 2026-10-03: the vehicle network is J1939, alone or beside the OBD dialect; the J1939 rows and the DM1 codes apply),"profile"("" = none),"specific_init","ecus"("7E8:BE7FB813,7E9:80000001" or "18DAF158:9818A013,…": the responder ids + their range-00 support bitmaps behind the fingerprint),"std_supported":N,"pending_profile":bool,"first_seen","last_seen","scan_ts"(epoch s, 0 = clock unset),"current":bool}]}`. At most 8 cars, the least recently seen evicted (one W line, event `autopid.vehicle_evicted {vin, name}`). `config.json` is a copy of the current car's tables; `PUT /api/autopid/config` also lands in the current car's file. Always 200 |
| `/api/autopid/vehicles/detect` | POST | start the detection job (= `POST /api/autopid/std_scan`): 202 `{"started":true}`; 409 `{"error":"detection already running"}`; 500 on a start failure. Progress on `GET /api/autopid/std_scan` (`phase`), the result on `/std_scan/result` (`key`, `known`, `name`) and in `GET /api/autopid/vehicles`. First contact per boot does the same work without the PID walk: same car = `last_seen` touched (daily); another stored car = live switch (tables, init, protocol; event `autopid.vehicle_changed {vin, name, known:true}`); unknown car = this job (event `{known:false}`); no answer = no change |
| `/api/autopid/vehicles/<key>` | PUT | edit one car: `{"name"?,"profile"?,"specific_init"?}` (any subset; strings, 31/63/95 chars max) -> the entry as in GET (with `"current"`). `profile` present (a name or `""`) clears `pending_profile`; `profile:""` = "keep it without a profile, standard PIDs only" and also clears the car's `specific_init`; a body without `profile` leaves `pending_profile` alone. `specific_init` applies live to the runner's SPECIFIC init when the car is current (the `specific_init` setting is the fallback for a car without one); an init that would write the chip's EEPROM (ATPP/ATSD/ATCV/STWBR) is 400. 404 unknown key, 400 bad body. Nothing is written when nothing changed |
| `/api/autopid/vehicles/<key>/activate` | POST | switch to that car by hand, the same path as the automatic switch: snapshot the live `config.json` into the previous current car's file, copy this car's file over `config.json` (empty tables when it has none), live reload (the `PUT /api/autopid/config` path), its `specific_init`, prelude re-armed for its protocol, `current` saved, event `autopid.vehicle_changed {vin, name, known:true}` -> `{"ok":true}` (no body; already current = no-op). 404 unknown key |
| `/api/autopid/vehicles/<key>` | DELETE | forget the car: entry + its tables file -> 204. Deleting the current car keeps `config.json` as it is and clears `current` (the SPECIFIC init goes back to the setting; the next first contact learns the car again). 404 unknown key |
| `/api/autopid/std_table` | GET (note, 2026-10-03) | for a vehicle whose `j1939` flag is set the built-in J1939 rows (`cmd` `PGN:…`, `pgn`, `spn`) are appended; a J1939-only vehicle (dialect `j1939`) gets those alone |
| `/api/autopid/std_table` | GET | the full built-in SAE table in the same entry shape: the UI's "all possible standard PIDs" list. In the current car's dialect (`22F4xx` rows on a `uds` car, no `init`: such a row is asked functionally). PIDs the table cannot decode are left out |
| `/api/autopid/test` | POST | test-a-PID one-shot through the REAL runner path: `{"cmd" (required), "init"?, "rxheader"?, "expression"?, "type"? ("std"|"custom"|"specific": prepend the type init chain the poller sends, 2026-09-16), "expressions"? (array, up to 256: decode the one reply with every expression)}` → `{"ok","raw","elapsed_ms","payload"? (hex),"value"? (for "expression"),"values"? (array, number or null per entry),"transcript" (one line per exchange: `> cmd` then `< reply`, in the order sent: type init, PID init, ATCRA, the request, ATCRA off),"error"?}`. Pauses polling around the shot (state restored); 409 while another test runs; 400 on bad expression/lengths. The `/ws/obd` console can't reproduce init/rxheader/expression handling, this can (bench: the full MEB 29-bit UDS init chain in one call). **A `PGN:` command (2026-10-03)** decodes the newest message of that group in the J1939 listener's store instead: nothing is sent, no chip job, `transcript` reads `> store: PGN F004` / `< from source 0, 8 bytes, 17 ms old, message 264 of this key`, `payload` the first 128 bytes; `ok:false` with `error` "the group is not in the listener's store (…)" when nobody sent it 409 with the bus guard's sentence and nothing sent when the poller is parked, or (2026-10-05) when `init`, the type's init or an AT `cmd` sets a CAN protocol at another bit rate than the vehicle bus runs at; since 2026-10-05 the transcript starts with the baseline prelude (`ATS1`, `ATH0`, `ATST96`, `ATTP<p>` ...) when none went out since the chip was last reset (a test as the first chip traffic of a boot, Automate off), and a reset inside `init` (`ATZ`, `ATD`, `ATWS`) is followed by it |

| `/api/autopid/group` | POST | runtime (non-persisted) group toggle: the HTTP twin of the `autopid.group` event action (API-first §1b): `{"name":"…","enabled":bool,"period_ms"?:N}` → `{"ok":true}`; 404 unknown group. A reboot restores the configured state. Added 2026-07-07; bench scripts use it to claim OBD exclusivity (ws_live_test.py pauses/restores all groups) |

The tables live in a FILE, not settings (profiles exceed the settings
16-item array cap); the `autopid` settings component holds only the
knobs (enable, per-type enables, init strings, pause voltage,
`min_event_interval_ms`) and stays reboot-to-apply. (Schema v2's
`backend` field was removed 2026-09-06: the OBD chip is the only
transport; a stored value is ignored.)

### 6e4b. DTC check / report / clear

DTC scanning (OBD modes 01-01/03/07/0A) + conditional clearing (mode 04) as
an autopid sub-module. **Disabled by default**: settings v3 adds
`dtc_enabled=false` + a second `dtc_allow_clear=false` gate for mode 04
(both must be flipped for clears; scans need only `dtc_enabled`).
Reporting/scheduling ride event_manager (sources `autopid.dtc` [per NEW
code], `autopid.dtc_scan`, `autopid.dtc_clear`; actions `autopid.dtc_scan`,
`autopid.dtc_clear` [queued to the job task, never the dispatcher]; pull
values `${autopid.dtc_json}`, `${autopid.dtc_codes}`, `${autopid.dtc_count}`,
`${autopid.dtc_mil}`); Berry gets `dtc_scan()` / `dtc_clear(codes?, mode?)`.
Scheduled scan = the `dtc_scan_period_min` setting OR a `timer.tick` rule.

| Route | Method | Behavior |
|---|---|---|
| `/api/autopid/dtc` | GET | `{"enabled","allow_clear","scanning","path","report":{"valid","ts","mil","mil_count","ecus","protocol","stored":["P0420",…],"pending":[…],"permanent":[…],"new":[…],"sources":[{"ecu","mil","count"}],"items":[{"code","kind","ecu"?,"status"?,"severity"?}]["error"]["desc"]}}`: always available; `report.valid=false` until the first scan. **Since 2026-10-03:** `path` = what a scan or clear does NOW: `obd` (03/07/0A, clear 04), `wwh` (the current car's dialect is `uds`: `19 42 33 08/04 1E`, `19 55 33`, clear `14 FF FF 33`, all functional with every ECU parsed apart) or `uds` (`dtc_protocol=uds`); `report.protocol` = the path the last scan took. `sources` = every ECU that answered the lamp request (`0101` / `22F401`) with its lamp bit and confirmed count; `mil` and `mil_count` are their OR and sum. `items` = who reported what, one per (code, `kind` = `stored` \| `pending` \| `permanent`, ECU), with the ISO 14229 `status` byte and the WWH-OBD `severity` + class byte where the service carries them; the three arrays stay the merged view (one code once). A vehicle that reports in the SAE J1939 DTC format reads `"SPN3226-4"`. **J1939 (2026-10-03, phase 5):** on a vehicle with the `j1939` flag the scan folds in every controller's DM1 from the listener's store (nothing asked): `path` = `j1939` on a J1939-only vehicle (the chip is not used; `report.protocol` `j1939`), the chip's path otherwise with `report.j1939:true` beside it; `report.lamps = {"mil","rsl","awl","pl"}` (any controller's malfunction indicator, red stop, amber warning, protect lamp), a `sources[]` entry of a J1939 controller carries `sa` (its source address) and its own `lamps` instead of `ecu`, an `items[]` entry of a DM1 code carries `sa` and `oc` (occurrence count, absent when the controller said not available) with `kind` `stored` (active). **J1939 active mode (phase 6):** the scan first asks everyone for DM2 and waits 1.5 s; the previously active codes join `pending` with items of `kind` `pending` + `sa` + `oc`. The clear on a J1939-only vehicle: 403 `J1939: clearing needs mode active (j1939)` in listen mode; 409 `J1939: no address on the bus yet (<claim state>)` while the node holds none; otherwise DM11 (active codes) then DM3 (previously active) are requested of every controller heard and their acknowledgments awaited (1.5 s each): `cleared` when at least one controller acknowledged DM11, `before` / `after` = active codes on the network before and 1.5 s after (the next DM1 broadcast), `ok:false` + `error` `DM11 not acknowledged by N controller(s)` otherwise; the condition (`mode` / `codes`) is evaluated against the active codes heard now; a fresh scan is queued after a clear so the report follows the network. Every legislated scan and clear starts from the chip baseline (a polled row's own header or filter no longer decides which ECU is asked), and a scan on a 29-bit OBD-II car reads its ECUs (it found none before). Since 2026-07-22 a functional OBD scan MERGES every responding ECU (union lists, `mil` OR, `mil_count` sum, `ecus` count) and `dtc_protocol` settings (`obd`/`uds`/`auto`) add ISO 14229 0x19 scans + 0x14 clears: `protocol` names the path that answered, UDS codes may carry a failure-type suffix (`"P0420-08"`; FTB 0 suffixless), and under `uds` a clear with a `codes` list is a TRUE per-code clear. Since 2026-07-26 (`dtc_freeze`, default true) an OBD scan that finds stored codes also captures **freeze frame 0** (mode 02): `report.freeze = {"dtc":"P0301","ecu":"7E8","params":{"EngineRPM":{"value":3000,"unit":"rpm"},…}}`, `dtc` = DTCFRZF (which code froze the frame), `ecu` = the responder that holds it (first responder wins, multi-ECU freeze = v2), `params` = the curated PID set decoded via the standard table, bitmap-pruned. Absent when nothing was captured (no codes, no frame, UDS scans, or the knob off) |
| `/api/autopid/dtc/scan` | POST | start the async DTC scan (std_scan job pattern; one chip job at a time incl. std scan/test); 202 `{"started":true}`; 409 busy; 403 when `dtc_enabled=false`. Right after boot the chip is provisioning ~15 s: a scan then fails with `"no ECU response"`, just re-scan |
| `/api/autopid/dtc/clear` | POST | `{"confirm":true,"codes"?,"mode"?:"always\|if_any\|if_only"}` → `{"ok","cleared","before","after"}`; 400 missing confirm; 403 unless `dtc_allow_clear`; 409 busy. Mode 04 clears ALL codes + readiness monitors + MIL: the conditional modes gate the wipe on the currently-present set (`if_only` = refuse when an unlisted code would be wiped); permanent (0A) codes survive by design. On the `wwh` path the clear is `14 FF FF 33` (the emissions group, every ECU, all or nothing like mode 04): `cleared` needs every answering ECU to confirm, a refusal (`7F 14 xx`) fails it |

**DTC databases**: user-
uploaded code→description files. Formats auto-detected + normalized at
upload: CSV/TSV/semicolon (headers, quoted fields), JSON map
(`{"P0420":"…"}`), JSON array (`[{"code","description"}]` with key
aliases code/dtc/id + description/desc/text/meaning/title), plain text.
≤8 databases, ≤1 MB / 20000 entries each; descriptions kept to 95 chars;
`P0420-00`-style suffixes truncate to the base code. Lookup priority =
db name order (name an override db `0_…` to win). The report GET above
gains `"report":{…,"desc":{"P0420":"…"}}` for every code with a hit.
Berry: `dtc_desc(code)`. Databases are independent of the
`dtc_enabled` gate (upload/browse works anytime).

| Route | Method | Behavior |
|---|---|---|
| `/api/autopid/dtc/db` | GET | `{"dbs":[{"name","entries","bytes"}],"max":8}` |
| `/api/autopid/dtc/db?name=<n>` | POST | raw file body (any format above) → sniff/normalize/store/cache → `{"ok","name","entries","format"}`; 400 parse/name/size (`{"error"}` names the first rejected line); 409 slots full |
| `/api/autopid/dtc/db?name=<n>` | DELETE | remove db; 404 unknown |
| `/api/autopid/dtc/db/search?q=&db=&offset=&limit=` | GET | the picker: `{"total","items":[{"code","desc","db"}]}`, `q` = code prefix OR description substring (case-insensitive), empty = browse; limit ≤100 |
| `/api/autopid/dtc/lookup?codes=P0420,P0171` | GET | batch enrich: `{"P0420":"…","P0171":null}` |

### 6e4c. DBC files → filter parameters

Upload `.dbc` files (≤4, ≤1 MB / 400 messages / 3000 signals each),
browse/search the signals, and add selected ones to autopid **filters**:
each signal compiles into an `expression_parser` expression (Intel/
Motorola, signed/unsigned, sub-byte, all host-cross-checked against a
reference decoder), merged into `/data/autopid/config.json` (one filter
per frame id), dry-run validated, atomically saved, and applied LIVE.
Unsupported signals (multiplexed, float, >95-char expressions) are
listed with a reason, never silently dropped. UI: Vehicle › DBC Signals.

| Route | Method | Behavior |
|---|---|---|
| `/api/autopid/dbc` | GET | `{"dbcs":[{"name","messages","signals","bytes"}],"max":4}` |
| `/api/autopid/dbc?name=<n>` | POST | raw .dbc body → parse/store/cache → `{"ok","name","messages","signals"}`; 400 (`"no signals found (N SG_ rejected, first: …)"` / name / size), 409 slots full |
| `/api/autopid/dbc?name=<n>` | DELETE | remove file + cache; 404 unknown |
| `/api/autopid/dbc/signals?db=&q=&offset=&limit=` | GET | `{"total","items":[{"db","msg","id","name","unit","start","len","order","signed","factor","offset"[,"min","max"],"supported",("expression"\|"reason")}]}`: q matches signal or message name; limit ≤100 |
| `/api/autopid/dbc/add` | POST | `{"db","signals":["Name"…]\|[{"id","name"}…],"group"?,"monitor_ms"?,"period_ms"?}` → merge into autopid filters → `{"ok","added","filters"?,"skipped":[{"name","reason"}]}`; 400 = merged config failed validation (nothing saved); added=0 → ok:false + skips, nothing written |

## 6e5. Events: `event_manager` registers its own routes (2026-07-06)

The rule editor's vocabulary comes entirely from discovery (API-first
§1b: zero hardcoded dropdowns). Rules/timers themselves are settings
(`/api/settings/event_manager`, strict validation at PUT).

| Route | Method | Behavior |
|---|---|---|
| `/api/events/sources` | GET | declared events: `[{"event":"autopid.param","description",…,"keys":[{"key","type"}]}]` |
| `/api/events/actions` | GET | registered actions: `[{"name":"mqtt.publish","params_schema":{…}}]`, the schema drives the `with` form |
| `/api/events/values` | GET | pull-value names (`"autopid."` trailing dot = prefix family) |
| `/api/events/log` | GET | `{"stats":{running,published,dropped,fired,action_errors,suppressed,blocking_dropped},"events":[{source,name,ts_us,data{},fired:[rule names]}]}`: last 32 events, oldest first; THE rule-debugging view. `blocking_dropped` = slow (network/bus) action jobs dropped-oldest when the worker-pool queue was full |
| `/api/status` (`health.caps`, 2026-09-17) | GET | `events_src` and `events_act` joined `settings` / `cmdline` / `bridge_ep`: the rules engine's source and action registries (`{used, cap}`), also on the `WICAN CAPS` boot line, headroom < 2 latches `registry_headroom` (standard §12) |
| `/api/status` (`memory`, 2026-10-03) | GET | poll-friendly: `memory.psram.largest_block` is NOT in the routine answer (measuring it walks the 6000-block PSRAM heap with interrupts off for 3 to 4 ms, and a busy CAN bus paid for every poll in frames); `memory.internal.largest_block` stays (0.14 ms). `?deep=1` asks for the PSRAM figure: something a person wants once, never a poll. Likewise `/api/status/tasks` no longer counts stack bytes inside the kernel's critical section (6 ms with interrupts off before) |
| `/api/status` (`health.caps`, 2026-10-03) | GET | `http_routes` joined: the HTTP route table itself (`{used, cap}`, 113 of 144 with every route and WebSocket channel of the stock build), also `WICAN CAPS http_routes` on the boot line. It was found at 112 of 112 the day it became visible: one more route and the last WebSocket channel was refused |
| `/api/events/rules` | GET | per-rule runtime for the Rules page's badges (2026-09-17): `[{name, enabled, undo, active, fired, last_fired_age_s}]`, `active` = a while-rule (`undo:true`) whose action is in effect, `last_fired_age_s` -1 = never. Rules themselves live in the `event_manager` settings (`when[].value` = a live-value condition, `undo` = reverse when the conditions stop holding; `GET /api/events/actions` carries `undoable` per action) |

## 6e6. Data logger: `data_logger` registers its own route (2026-07-07)

| Route | Method | Behavior |
|---|---|---|
| `/api/logger` | GET | `{"enabled","running","paused","storage_ok","file":"dl_<epoch>.db","file_rows","files","queued","written","dropped","errors","rotations","can":{"enabled","file":"can_<epoch>.wdl","file_rows","files","queued","frames_written","frames_dropped","rotations"},"salvaged":N,"corrupt":N,"dir":"/sd/logs"}`, `salvaged` = records carried over the last warm reset from PSRAM, `corrupt` = `*.corrupt` files set aside on the card (data_logger/ROBUSTNESS.md, 2026-09-07), top level = the params stream, `can{}` = the CAN-frames stream (2026-07-09 two-stream addendum) |
| `/api/logger/gate` | POST | runtime (non-persisted) logging gate for BOTH streams: the HTTP twin of the `logger.enable`/`logger.disable` event actions (API-first §1b): `{"enabled":bool}` → `{"ok":true}`; observable as `paused` in the GET. A reboot restores the configured state. Added 2026-07-07 |
| `/api/logger/export` | GET | incremental pull of the PARAMS stream, `format=jsonl` or `csv` (400 otherwise; csv rows stream as-is, the header row is skipped by the name filter, the trailing meta line stays JSON, 2026-09-06): `?stream=params&since=<epoch>:<off>&limit=N[&name=<param>]` (2026-09-06: `name` keeps only that parameter's records, full `source.name` or the part after the last `.`; the cursor still walks every line, at most 512 KB read and 64 KB emitted per request: the dashboard's chart history uses it) streams raw jsonl lines from the rotated files starting at the cursor, briefly gating the logger if it must read the ACTIVE file; final chunk is `{"_cursor":"epoch:off","more":bool}`: feed `_cursor` back as `since` to tail. Poll-based exporter for integrations that can't mount the SD |

Config = `/api/settings/data_logger` (v2): per-stream engine choice
(`format` = params: sqlite|csv|binary|jsonl; `can_format` = CAN:
binary|csv|sqlite|**mf4** (MDF 4.10 ASAM: asammdf/CANoe/MATLAB)|
**blf** (Vector, python-can readable)|candump (can-utils/SavvyCAN)|
asc (CANalyzer text)|jsonl: mf4/blf/asc are single-use per boot/
rotation, addendum-2 2026-07-09),
`autopid_log` off|changed|all (the autopid→logger sink),
`can_log` + `can_filter`/`can_mask`/`can_ext` (hex id filter, "" =
all frames; needs can_manager enabled), per-stream rotation/retention
(`max_file_mb`/`max_files`, `can_max_file_mb`/`can_max_files`),
`ring_len`. The log FILES are ordinary `/sd/logs` entries
(`dl_<epoch>.*` params, `can_<epoch>.*` frames): browse / download /
delete through the §6 filesystem routes; no duplicate file surface
here. The ACTIVE file of each stream is write-locked
(`CONFIG_FATFS_FS_LOCK`): `/api/fs` download/delete of it FAILS
instead of corrupting the card: pause via `/api/logger/gate` (closes
+ flushes) or wait for rotation. `.wdl` files decode offline with
`tools/wdl_dump.py` (`--to csv|candump|text`). Event actions (§6e5
discovery lists them):
`logger.enable` / `logger.disable` (rule-driven gating) and
`logger.write {source?, name, value}`, the SELECTIVE producer path: a
rule like `autopid.param -> logger.write {"source":"autopid",
"name":"${param}","value":"${value}"}` records exactly the parameters
the user picks (the `autopid_log` setting is the log-everything twin).

## 6e7. VPN: `vpn_manager` registers its own routes (2026-07-07)

| Route | Method | Behavior |
|---|---|---|
| `/api/vpn` | GET | `{"state":"disabled\|waiting\|connecting\|connected","type":"wireguard\|tailscale","endpoint":"host:port","ts_ip":"100.64.x.y","ts_peers":N,"connects":N,"failures":N,"uptime_s":N}`: `ts_*` fields are only meaningful for type=tailscale |
| `/api/vpn/keygen` | POST | generate a Curve25519 pair ON the device; private key goes straight into pending settings (never over HTTP); responds `{"public_key":"…","pending_reboot":true}` (wireguard type) |

Config = `/api/settings/vpn_manager`. `type` selects the stack:
`wireguard` (fields `private_key`*, `peer_public_key`, `preshared_key`*,
`address`, `allowed_ip`+mask, `endpoint`, `port`, `keepalive_s`,
`default_route`, `dns`) or `tailscale` (fields `ts_auth_key`*,
`ts_device_name`, `ts_control_url`: empty control_url = official
Tailscale, `host[:port]` = Headscale/Ionscale). Secret fields
(`*` above: the api_http redaction suffixes `private_key`,
`preshared_key`, `auth_key`, extended 2026-07-07) come back `""` in
GETs; sending `""` on PUT keeps the stored value.

## 6e8. Sleep: `sleep_manager` registers its own route (2026-07-07)

| Route | Method | Behavior |
|---|---|---|
| `/api/sleep` | GET | `{"enabled","state":"normal\|low_voltage\|sleeping\|wake_pending","voltage","sleep_v","wake_v","naps"}` |

Config = `/api/settings/sleep_manager` (v3 since 2026-10-01: `wake_mv`
12100..15000, default 13200, is the user's own wake voltage; a v2 document
migrates to `sleep_mv + 100`; the applied `wake_v` is never under
`sleep_v + 0.1`; v4 the same day: `wake_delay_ms` 100..5000, default 500,
how long the battery must stay above `wake_v` before the wake reboot, older
documents get the default). No HTTP mutation: forcing a sleep is a bench affair
(`sleep test <secs>` CLI). The wizard's Battery and sleep step polls
`GET /api/battery` once a second and stages this document with Finish.

## 6e9. USB host: `usb_host_manager` registers its own route (2026-07-07)

| Route | Method | Behavior |
|---|---|---|
| `/api/usb` | GET | `{"enabled":bool,"device_present":bool,"host_active":bool,"eth_connected":bool,"driver":"asix\|rtl8152\|cdc_ecm\|cdc_ncm\|rndis\|","ip":"10.42.1.x\|","attaches":N,"vid":"303a","pid":"4007","vbus":bool}`: USB-connector role + USB-Ethernet uplink status. `device_present` = the ID pin sees a device; `host_active` = the mux is on the ESP OTG host; `attaches` counts enumerations since boot; `vid`/`pid` (hex, 2026-08-24) = the enumerated device behind the active driver (`0000` when none: the ESPNetLink is `303a:4007`); `vbus` = the connector rail as last driven (`usb_host_manager_set_vbus()`, the dongle re-pair lever) |
| `/api/usb/acm` | GET | `{"connected":bool}`: is a CDC-ACM console (the espnetlink's management interface) bound |
| `/api/usb/acm/cmd` | POST | `{"cmd":"lte -s","timeout_ms"?:N (default 8000, a CAP not a latency)}` → `{"ok","response","connected"}`: send one line to the espnetlink's management console; the reply is collected until the dongle's `esp>` PROMPT (2026-07-13 fix: the old 150 ms quiet window raced the echo→payload gap and returned "no reply" for the modem-querying commands, the web-UI ESPNetLink card bug; 2 s quiet remains as the prompt-less fallback, since the modem legs go silent mid-response). 409 if no device/busy. The console is the dongle's OWN CLI (`esp>` prompt: `ver`, `lte -s/-r/-o/-i` signal/operator/IP, **`lte -j`** = machine-readable JSON the web UI's ESPNetLink Status card polls, `gps -p -j`, `speedtest`, `ping`, `config`, `agnss`), NOT raw AT. Owner: `usb_acm_cli` (config `/api/settings/usb_acm_cli`: `enabled` default false, `cli`); also a bridge_manager endpoint `acm` for raw passthrough |
| `/api/gps` | GET | `{"valid":bool}` or, with a live fix, `{"valid":true,"latitude","longitude","accuracy"(m, HDOP-derived),"altitude"(m),"speed"(m/s),"heading"(deg),"satellites","age_ms"}`: the ESPNetLink dongle's last fix (device-contract `gps` field names). Owner `usb_acm_cli`: a poll task runs the dongle's `gps -p -j` every 5 s and caches the parsed fix (read here, no ACM I/O); when the console has no live fix the route serves `espnetlink_link`'s HTTP-polled fix instead (the WiFi-modem topology has no console: main wires `usb_acm_cli_set_gps_fallback()`, 2026-08-24). **The same fixes are ALSO published as first-class autopid parameters** `gps_latitude`/`gps_longitude`/`gps_altitude`(m)/`gps_speed`(km/h)/`gps_heading`/`gps_satellites`, so they appear in `autopid_data` (→ the HA push, the dashboard, `${autopid.data}`), the `data_logger`, and `autopid.param` event rules with no GPS-specific code in those consumers. Only a LIVE fix is reported (a cached AGNSS position is never presented as current) |

Config = `/api/settings/usb_host_manager`. New additive setting `role` (`host`|`device`, default `host`, reboot-to-apply): when `enabled`, picks whether the ESP-OTG runs as a CherryUSB **host** (USB-Ethernet/espnetlink, today) or a CherryUSB **device**, with `device_class` {`ncm`|`rndis`|`cdc`}: `ncm`/`rndis` make WiCAN a USB-Ethernet DEVICE (a NIC to the PC → IP-over-USB carries the J2534 TCP server + web UI + bridges over one cable), `cdc` a serial COM port (J2534 USB transport: Phase 3). `enabled=false` keeps the connector on the CH342 (serial). The connector serves ONE
role at a time: host mode (this surface) or device mode (the CH342,
serial console/`usb_obd` to a PC); the `eth_connected` dev-status bit
feeds the §3 `network_connected` mask. The `usb` CLI mirrors this GET
(its `mux`/`vbus` subcommands are temporary bench debug, not API
surface).

## 6e9b. ESPNetLink dongle: `espnetlink_link` registers its own routes (2026-08-24)

| Route | Method | Behavior |
|---|---|---|
| `/api/espnetlink` | GET | `{"enabled","mode":"wifi_modem\|usb_ncm\|usb_rndis","auto_pair","paired","ssid","device_id","uplink":"none\|wifi\|espnetlink\|espnetlink_usb","on_link","host","pair_blocked_factory_pw" (true = zero-touch pairing is held because the AP still uses the factory password; the store would be refused by wifi_manager's gate. Set a new AP password; the restart completes pairing),"last_error" (last pairing failure text, "" = none),"dongle_fw","dongle_api","dongle_api_min" (the dongle's `/api/info` fw_version + api_level and the level this firmware expects, 7 = the WiFi-modem surface; 2026-09-08),"health_unsupported" (true = the dongle's `GET /api/wifi_modem` answers 404: a dongle build without the WiFi-modem API),"usb":{"attached","pair_state":"idle\|identify\|read_key\|cut\|wait_drop\|done\|tether\|foreign\|ncm_share\|ncm_up\|hold\|unsupported" (`hold` = the key was read but cannot be stored yet, e.g. the factory AP password; `unsupported` = the dongle firmware lacks the API, update it; both park without retries or VBUS cycles),"cuts","vbus_cycles","errors"},"gps":{"valid",…fix fields…},"dongle":{"valid","lte_connected","rssi_dbm","operator","network_type","gps_fix","usb_data"},"polls","failures","link_ups"}`: the dongle as the internet uplink. `uplink=espnetlink` = the STA is joined to the dongle's AP (WiFi-modem topology, USB = power only); `espnetlink_usb` = the dongle is a USB-Ethernet uplink (`mode=usb_ncm`/`usb_rndis`). `usb.pair_state` is the zero-touch pairing machine (USB attach 303A:4007 → `/api/info` → `/api/wifi_modem/credentials` over USB → store → `usb_data` cut → drop); `dongle` mirrors the dongle's `GET /api/wifi_modem` health |
| `/api/espnetlink/pair` | POST | `{"ssid","password"}` → `{"ok":true,"slot":N,"reboot_required":true}`: MANUAL pairing (no USB): stores the dongle's AP as a trusted wifi_manager fallback network (first free `fallbackN`, or the slot already holding that SSID; `slot` 0 = it is already the primary) and sets `espnetlink.ssid/enabled`; flips a `mode=ap`/`off` wifi_manager to `apsta`/`sta`. Persists immediately, reboot-to-apply (the next `/api/settings/submit` reboots). 400 `no free fallback slot` |
| `/api/espnetlink/repair` | POST | `{}` → `{"ok":true}`: re-pair: cycle the dongle's VBUS (`usb_host_manager_set_vbus`) so it re-enumerates and the key is read again. 409 when the USB host is not up |

Config = `/api/settings/espnetlink` (v2): `enabled` (true), `mode` (`wifi_modem` default, the cable carries 5 V only, the dongle is reached over its WiFi AP, its GPS is not desensed by the USB host; `usb_ncm`/`usb_rndis`: the data lines stay on, the dongle is a USB-Ethernet uplink presenting that class and the WiCAN switches the dongle's `usb_dev_ethernet.class` + `lte_upstream_pppos.ncm_share` to match, which reboots the dongle once), `auto_pair` (true), `ssid`, `device_id`, `host` ("" = the STA gateway / 192.168.7.1), `gps_poll_s` (2), `health_poll_s` (10), `cut_retries` (2), `cli`. The `espnetlink` CLI mirrors the GET (+ `pair`, `repair`).

## 6e10. Native CAN: `can_manager` registers its own route (2026-07-07)

| Route | Method | Behavior |
|---|---|---|
| `/api/can` | GET | `{"enabled":bool,"running":bool,"silent":bool,"baud_kbps":N,"state":"running"/"bus_off"/"recovering"/"stopped","tx":N,"rx":N,"tx_errors":N,"rx_errors":N,"arb_lost":N,"bus_errors":N,"rx_missed":N,"dispatch_drops":N,"bus_off":N,"recoveries":N}`: the native TWAI bus (WiCAN Pro TX=GPIO2/RX=GPIO1/STDBY=GPIO38), shared by every CAN consumer. `tx_errors`/`rx_errors` are the live TEC/REC; `rx_missed` = wire frames lost to a full TWAI RX queue (RX task starved), `dispatch_drops` = drop-oldest evictions across subscriber queues (a consumer's drain starved): both 0 in healthy operation, they localize any frame-loss report. Bus-off recovery is AUTOMATIC (2026-07-10): re-enter as soon as hardware recovery completes; rapid re-offense backs the restart off 1→2→4…30 s (escalation resets after 60 s of stability). `bus_off`/`recoveries` count episodes; `state` reads `recovering` from bus-off until the restart lands. **Listen before talk (2026-10-03):** the node always starts listen-only (its TX pin is not routed) and the reply carries where its link is: `"state"` reads `"detecting"` (automatic bit rate, none proven yet), `"listening"` (fixed bit rate, no verdict yet) or `"mismatch"` (the bus carries traffic this bit rate cannot read; with `auto`: that no candidate reads) until the verdict, then the bus state as before; `"listen_only":bool` = what the node may do right now whatever `silent` asks for; `"verified":bool` = frames were read at `baud_kbps`; `"baud_auto":bool`; `"baud_detected":N` = the last bit rate proven by frames (0 = none); `"rx_bad":N` receive errors (what a wrong bit rate produces), `"rx_deaf":N` looks at the RX line that found the bus busy while the controller reported nothing (a saturated bus at a higher bit rate), `"tx_refused":N` transmits asked while the node may not talk, `"link_switches":N` / `"link_demotions":N`; `"err":{"stuff","form","bit","ack","other"}` = `bus_errors` by kind; `"probe":{"result":"none\|silent\|live\|unreadable","baud_kbps","frames","age_ms"}` = the last look a bus guard took (it works while can_manager is disabled); `"subscribers":[{"idx","name","drops"}]` of `"subscribers_max"` (16) = who reads the bus and what each one's queue lost (`dispatch_drops` is their sum). **2026-10-03:** `"rx_overrun":N` = wire frames the controller's own receive FIFO lost (it holds 64 bytes, four to five frames: the receive interrupt was served too late). Until this counter such a frame vanished without a trace; 0 in healthy operation, like `rx_missed` and `dispatch_drops`. `"rx_storms":N` = receive-error storms the driver throttled: more than 400 bus errors in 20 ms (a controller listening far above the bus's bit rate, e.g. an `auto` candidate of 1000 kbit/s on an 83 kbit/s bus, raises an error interrupt every few bit times and starved the tick until the interrupt watchdog reset the chip); the error ISR masks the controller's interrupts, the link policy reads the deaf node as a mismatch and starts a new one. One `W` line per storm; counts up on a wrong bit rate, 0 on a bus the node can read |

Config = `/api/settings/can_manager` (v2: `enabled` default false, `baud`
enum `auto` or 33…1000 kbit/s (default `500`; `auto` takes the bit rate
from the traffic and never talks on a silent bus), `silent`, `cli`). The bus is SHARED: the
slcan/gvret/internal-CAN consumers, firmware ISO-TP (UDS / J2534) and
any add-on jacks all multiplex it, unlike the single-master MIC3624,
multiple clients run CONCURRENTLY.

`/api/settings/obd_gate` (`enabled` default TRUE, 2026-07-11): the
bus-conversation gate. The MIC chip and any ESP-side requester share
ONE physical CAN bus; overlapping request/response conversations
mis-attribute responses (a BLE app polling the chip + a second poller
= bad data). The gate serializes them: one
conversation at a time, fair turn-taking, fail-open (a wedged holder
can't block the other side; holds self-expire at 2 s). Disable only
for test setups that WANT concurrent conversations.

## 6e10b. J1939 listener: `j1939` registers its own route (2026-10-03)

The component listens on the native CAN bus and never transmits; it needs
`can_manager` enabled (for a vehicle network: `baud` `auto`, `silent` on).
Per-endpoint reference with a full example: `j1939/README.md`.

| Route | Method | Behavior |
|---|---|---|
| `/api/j1939` | GET | `{"enabled":bool,"state":"off"/"no_bus"/"listening","bus":"unknown"/"j1939"/"other","mode":"listen"/"active","claim":{...},"tx":{...},"vin":"..." or null,"vin_sa":N,"can":{"running","baud_kbps","link","listen_only"},"stats":{...},"tp":{...},"values":[...],"sources":[...],"dm1":[...]}`. `state` `no_bus` = enabled while the native CAN bus is off (`listening` in both modes). **Active mode (phase 6, 2026-10-03):** `claim` = `{"state":"idle"/"claiming"/"claimed"/"cannot_claim","address":N (254 while none is held),"preferred":N,"tx_ready":bool (the bus lets the node transmit: normal mode, bit rate proven),"name":"<16 hex, most significant byte first>","claims_sent","contests","won","lost","held" (contests won but not answered again within 250 ms: a node contesting every claim it hears is not fed a storm),"requests_answered","cannot"}`; `tx` = `{"frames","failed","requests","acks","nacks" (ours answered),"nacks_sent" (requests to us for a group we do not have),"tp_to_me","tp_cts","tp_eoma","tp_aborts","tp_reply_lost"}` (the transport protocol as a destination). In listen mode `claim.state` is `idle` and every `tx` counter 0. `bus` turns `j1939` once two distinct well-known groups were seen, `other` after 50 frames without one. `stats`: `rx_frames` = `rx_data` + `rx_tp_cm` + `rx_tp_dt` + `rx_diag` (ISO 15765 on J1939 ids, not stored) + `rx_foreign`; `queue_drops` (frames lost before the listener, its queue was full), `messages` stored, `not_kept` / `evicted` / `long_evicted` (the store's bounds), `entries` of `entries_max` (384), `seq` (moves whenever a message was stored). `tp` (transport protocol): `open` of `max` (8), and `started` = `completed` + `seq_errors` + `timeouts` + `aborted` + `replaced` + `open`; `no_session`, `orphan_dt`, `bad_cm`. `values[]`: `{"name","spn","pgn","sa","state","value","unit","age_ms","period_ms"}` for every entry of the built-in table whose group somebody sends; `state` is `valid`, `na`, `error`, `specific`, `reserved` or `short` and `value` is present only with `valid`; `sa` = the source picked (the lowest address heard in the last 5 s, else the lowest of all: since 2026-10-03 a bus that falls silent does not move the pick to whoever spoke last). `sources[]`: `{"sa","frames","age_ms","name"}` (`name` = the NAME of its address claim in hex, or null). `dm1[]`: `{"sa","age_ms","mil","rsl","awl","pl","count","dtcs":[{"code":"SPN110-0","spn","fmi","oc","cm"}]}` per controller that sent DM1 (lamps 0 off, 1 on, 2 error, 3 not available; at most 32 codes listed, `count` is the message's) |
| `/api/j1939?pgns=1` | GET | everything stored: `{"seq":N,"pgns":[{"pgn":"F004","sa":0,"da":255,"len":8,"count":N,"period_ms":N,"age_ms":N,"data":"F0AAA5E02EFFFFFF"}]}`. `pgn` hex, `sa` / `da` decimal (`da` 255 = broadcast), `data` the whole payload in hex (a transport-protocol message may be 1785 bytes), `len` 0 = a long payload that had to make room |
| `/api/j1939?pgn=FEEC[&sa=N][&da=N]` | GET | one group's newest message, in the shape of a `pgns` element; `sa` / `da` decimal or `0x..`, without them the pick applies. `404 {"error":"nobody sent this group"}` |
| `/api/j1939?request=FECB[&da=N]` | GET (2026-10-03, phase 6) | active mode: send a Request (PGN 59904) for that group to controller `da` (default everyone) from the node's address and answer at once: `{"sent":true,"pgn":"FECB","da":N,"from":N,"outcome":"pending"}`. The data arrives in the store a moment later (`?pgn=`), an acknowledgment is read by asking the same (group, destination) again (`outcome` `acked` / `nacked`). `409 {"error":"listen mode: the node never transmits (j1939.mode)"}` / `{"error":"no address on the bus yet (claim pending, lost, or the bus is listen-only)"}`, `503` when the bus did not take the frame |

Config = `/api/settings/j1939` (v1: `enabled` default false, `cli`).

## 6e11. UDS terminal: `uds_manager` registers its own route (2026-07-07)

| Route | Method | Behavior |
|---|---|---|
| `/api/uds` | GET | the live path without sending anything: `{"backend_setting","backend_active":"isotp"\|"obd_chip","provider":"esp_isotp"\|"add-on"\|"none","can_running","exclusive"(runtime switch),"exclusive_default"(the setting),"holding","autopid_paused","session_active","exclusive_idle_ms","max_request":64,"max_response":4096,"last":{"age_ms","ok","error"?,"tx_id","rx_id","req_sid","sid","positive","nrc"?,"nrc_name"?,"pending","elapsed_ms","backend"}\|null,"provider_stats"?:{"sessions_open","pdus_tx","pdus_rx","frames_fed","tx_timeouts","rx_dropped"}}` (2026-09-16) |
| `/api/uds` | POST | `{"exclusive":bool}`: the runtime **Exclusive bus** switch (boot default = the `exclusive` setting): while the tool is in use (a request, then `exclusive_idle_ms` = 10 s of idle, or an open session) AutoPID polling and DTC scans stay off the bus (obd_gate's diagnostics hold; `/api/autopid` shows `stats.paused_diag`). Answers with the GET status (2026-09-16) |
| `/api/uds/session` | POST | `{"action":"begin","tx_id","rx_id","ext"?}` opens a tester-present session (3E 80 every `tester_present_ms`; requests ride the held claim; the Exclusive hold, when on, lasts for the session); `{"action":"end"}` closes it. Answers with the status + `ok`; 409 while another transaction/session owns the bus (2026-09-16) |
| `/api/uds/request` | POST | one UDS (ISO 14229) request→final response over the selected transport. Body `{"tx_id","rx_id"(hex str or int),"ext"?:bool,"data":"22 F1 90"(hex, ≤64 bytes),"p2_ms"?,"p2star_ms"?(alias `timeout_ms`),"session"?:bool}` → `{"ok",response":"62 F1 90 …"(hex),"length","positive":bool,"sid","nrc"?,"nrc_name"?,"pending","elapsed_ms","backend"}`. `response` carries the WHOLE PDU (up to 4096 bytes, 2026-09-16). Handles the 0x78 responsePending loop + NRC decode; 409 while another UDS transaction is in flight. 2026-09-16: the obd_chip transport holds the chip for its whole AT transaction (`obd_chip_txn_begin`, autopid's poll waits instead of interleaving) and a reply whose SID is not the request's is refused with `ESP_ERR_INVALID_RESPONSE` (a stray from another requester), on either transport |

Config = `/api/settings/uds_manager` (`backend` =
`auto`|`obd_chip`|`isotp`, default **auto** = isotp when
can_manager is running else obd_chip; `p2_ms`, `p2star_ms`,
`tester_present_ms`, `exclusive` (boot default of the Exclusive bus switch,
default **true**, 2026-09-16), `cli`; schema v2, 2026-09-06: the former
`elm327` value migrates to `auto`). Backends: **isotp** (firmware
ISO-TP over native CAN: the proven, recommended path, ≤8 KB
multi-frame PDUs; the public build carries its own provider,
`can_isotp_esp` (esp_isotp over can_manager, since 2026-09-16), an add-on
pack's provider takes precedence; without CAN running it falls back to
obd_chip); **obd_chip** (the MIC via AT: reaches the
OBD-connector bus; 2026-09-16: one AT line per request in a steady
conversation, the ELM response-count digit so the chip answers in ~3 ms
instead of waiting out ATST, `7F xx 78` lines counted as `pending`; single-
frame requests ≤64 bytes only; 2026-10-02: the digit counts printed lines, so
a multi-frame answer came back as its first frame: the transport reads the
ISO-TP length line, asks again with the digit the answer needs, trims the
last frame's padding and never returns a part of an answer:
`ESP_ERR_INVALID_SIZE` when the chip keeps cutting; SecurityAccess,
Authentication, RoutineControl, RequestFileTransfer and
SecuredDataTransmission go out without the digit because they must not be
sent twice). `uds` CLI: `uds -t 7E0 -r 7E8 22 F1 90`.

## 6e12. Scripting: `script_engine` registers its own route (2026-07-07)

| Route | Method | Behavior |
|---|---|---|
| `/api/scripts` | GET | `{"scripts":[{"name","size"}…],"dir":"/data/scripts","busy":bool,"enabled":bool,"max_runtime_ms":N}`, stored scripts for the UI. File CRUD rides the generic `/api/fs` surface (`upload?path=/data/scripts/x.be`, `download`, DELETE `file`) |
| `/api/scripts/run` | POST | `{"src":"berry …"}` (inline, ≤ 8 KB) or `{"name":"uds_diag"}` (stored `/data/scripts/<name>.be`) → `{"ok","output"}` (≤ 4 KB; on failure `ERROR: <type>: <message>` + Berry's stack traceback, `string:<line>:` names the inline line); 404 unknown name, 409 busy/disabled |
| `/api/scripts/check` | POST | `{"src"}` → compile only, nothing runs: `{"ok":true}` or `{"ok":false,"error":"syntax_error: string:3: …"}`; 409 busy/disabled (2026-09-07) |
| `/api/scripts/stop` | POST | kill switch for the running script → `{"ok":true}` |
| `/api/scripts/reference` | GET | the engine describes itself for the Scripts page (2026-09-07): `{language, enabled, allow_reflash, limits{src_max,file_max,out_max,sleep_max_ms,name_max,resp_max_bytes,max_runtime_ms}, groups[{id,title}], bindings[{name,sig,group,ret,doc,ex}], globals[{name,doc}], primer[{title,code,note}], errors[{match,hint}], rules{action,with,event}}`: the tables live in `script_engine_doc.c`, cross-checked against the binding table at boot and host-tested |
| `/api/scripts/examples` | GET | `{"examples":[{id,title,desc,needs,level,size}]}`: the built-in example gallery (`script_engine_examples.c`; `needs` = `""` \| `vehicle` \| `dtc` \| `can`) |
| `/api/scripts/examples?id=` | GET | one example's Berry source, `text/plain`; 404 unknown (the list handler with a query: one URI-table slot) |


Event wiring (2026-07-07 pm): rules may use the sugar body `{"on":"source.event","script":"name"}` (parser rewrites to the `script.run {name}` action), the trigger event reaches the script as `evt_source`/`evt_name`/`evt_<key>` globals; scripts emit `script.done {value}` via `emit()` (a declared source rules can chain on). `uds.request {tx,rx,req,ext?}` is an action too, publishing `uds.response {ok,nrc,len,data,req}` (data truncated to the event kv limit: full payloads belong in a script's `uds()` binding). Actions run on the event dispatcher and BLOCK it for the transaction, same contract as `http.post`; keep event-triggered scripts short.

Config = `/api/settings/script_engine` (`enabled` default false,
`max_runtime_ms`, `allow_reflash` default false, `exclusive` default **true**:
from a script's first ECU access to the end of the run AutoPID polling
and DTC scans stay off the bus (obd_gate's diagnostics hold, independent of
the UDS Tool's switch; `GET /api/scripts/reference` echoes it), `cli`). The Berry bindings ARE the scripting API
(SCRIPTING.md): `uds(tx,rx,hexreq)` / `uds_ext(...)` → response hex (or
nil), with `uds_ok`/`uds_nrc` globals; `can_tx(id,ext,hex)`;
`emit(source,name,key,value)`; `log`, `sleep_ms`, `millis`. `script`
CLI: `script test` | `script stop`. Stored-script CRUD on
`/data/scripts` + event-triggered `script` rules are the documented
follow-up (v1 runs inline source, enough for the UDS terminal +
scripting).

## 6f. Certificates: `cert_manager` registers its own routes

| Route | Method | Behavior |
|---|---|---|
| `/api/certs` | GET | `{"sets":[{"name":"bench","ca":true,"cert":false,"key":false},…]}` |
| `/api/certs/upload?set=<a-z0-9_->` | POST multipart | fields `ca`/`client_cert`/`client_key` (the legacy form names), any subset in one request; each part PEM-validated ≤8 KB |
| `/api/certs/upload?set=<name>&type=ca\|cert\|key` | POST raw | one PEM body ≤8 KB (wrong PEM kind → 400) |
| `/api/certs?set=<name>` | DELETE | delete the whole set |

**No read-back**: key material never leaves the device through this
surface. Consumers reference sets by name (`mqtt_manager`'s `cert_set`
setting → mqtts server-auth or mutual TLS).

## 6g. WebSocket channels: `websocket_manager` owns `/ws/*`

Not HTTP request/response, but the same server and a reserved namespace:
settings-defined channels (defaults: `ws_obd` `/ws/obd` binary **ships
ENABLED**, paired with `bridge_manager`'s default `obd<->ws_obd` bridge,
so OBD over WebSocket works out of the box on a factory-fresh device;
`ws_can` `/ws/can` binary and `ws_cli` `/ws/cli` text stay parked until
enabled) upgrade at their path, gate `max_clients` before the 101, and
bridge to whatever endpoint the `bridge_manager` settings pair them with
(OBD-over-WS live-verified 2026-07-04; shipped-default flip 2026-07-05;
details + benchmarks in `websocket_manager/`).

## 6e14. Data destinations: `data_destinations` registers its own routes (2026-09-19)

The Automate → Data destinations page: cyclic pushes of the live autopid
snapshot to MQTT topics, HTTP/HTTPS endpoints (bearer / API-key header or
query / basic auth, extra query parameters, a cert_manager set for
private CAs or mutual TLS) and the ABRP (Iternio) telemetry API: the v6
home of the legacy autopid `destinations[]`. The table itself is a
settings component (`/api/settings/data_destinations`, reboot-to-apply,
≤ 8 rows; `auth_token` / `api_key` / `basic_password` are redacted on
GET and kept-when-empty on PUT: api_http matches array rows by `name`,
then by index). See `data_destinations/README.md`.

| Route | Method | Behavior |
|---|---|---|
| `/api/destinations` | GET | `{"enabled","running","network","mqtt","destinations":[{"name","type","enabled","url","period_s","auth","has_token","has_api_key","cert_set","success","fail","skipped_offline","consecutive_failures","backoff_s","next_in_s","last_status","last_error","last_error_time","last_ok_time","full_sent"}]}`, the APPLIED table with live counters: `skipped_offline` = due laps skipped because the link the type needs was down (no network / broker not connected, never a failure, never a backoff); `consecutive_failures` ≥ 3 engages the backoff ladder (`backoff_s`: 10 → 20 → 40 → max(8 × period, 60) s, 10 min cap, cleared by one success); `full_sent` = the HTTP(S) config+status first push landed. 503 before the settings apply |
| `/api/destinations/test` | POST | `{"name":"dest1"}` → deliver that destination ONCE, now, on the poster task (ignores cycle/backoff, books the counters like a scheduled push): `{"ok","status" (HTTP, 0 for MQTT/transport),"elapsed_ms","error"}`. 404 unknown name (a row not yet applied), 409 while another test runs or the poster is off, 504 when the poster did not answer within 20 s |

## 6e13. J2534 PassThru: `j2534_server` (groundwork, 2026-07-07)

| Route | Method | Behavior |
|---|---|---|
| `/api/j2534` | GET | `{"enabled","port","listening","client_connected","device_open","channels","frames_rx","frames_tx","allow_reflash","allow_lan","exclusive","autopid_paused","transport","phase"}`: SAE J2534 PassThru server status; `exclusive` = the runtime Exclusive bus switch (AutoPID off the bus while a tester is attached), `autopid_paused` = the pollers acknowledged it (2026-09-16); `transport` = which link the attached tester came in on: `tcp`, `serial` (USB CDC-ACM), `ble`, or `none` (2026-09-21) |
| `/api/j2534` | POST | `{"exclusive":bool}`: the runtime Exclusive bus switch (boot default = the `exclusive` setting, default **true**); applies at once, also to an attached tester. Answers with the status (2026-09-16) |

Config = `/api/settings/j2534_server` (`enabled` default false, `port` default 6809, **`allow_reflash` default false** = reject UDS memory-transfer services (34/35/36/37) so a tool can diagnose but not write ECU firmware, **`allow_lan` default false** = accept only on WiCAN's SoftAP + USB-device (NCM) netifs, refusing STA / USB-Ethernet uplinks since the transport is unauthenticated, `cli`). Status `/api/j2534` echoes `allow_reflash`/`allow_lan`. WiCAN as a SAE J2534-1 PassThru device: a PC diagnostic/reflash tool drives WiCAN's CAN bus via the companion Windows DLL over a versioned wire protocol, the driver ships as an **installer attached to firmware releases** and in this repo under `drivers/j2534/` (real-tool validated with the DrewTech J2534-1 tool, incl. a real reflash via the PassThru* API). Reachable over TCP on WiFi / USB-Ethernet / **USB-device (CDC-NCM)**: with `usb_host_manager.role=device` WiCAN is a USB NIC to the PC, DHCP at 192.168.82.1/24, so the DLL target is `WICAN_J2534_HOST=192.168.82.1` over the cable. Transports (all carry the SAME protocol, all TCP): USB-device CDC-NCM (IP-over-USB), WiFi, USB-Ethernet. ISO15765 channels use the ISO-TP provider slot: the public build's native `can_isotp_esp` (esp_isotp over can_manager, 2026-09-16) or an add-on pack's; they need `can_manager.enabled`. Raw CAN channels work everywhere. One session per rx id: a UDS request to the same ECU holds it for 5 s after the last request. `j2534` CLI mirrors the status. **2026-09-21: a fourth transport, BLE** (`ble_j2534`): while `enabled` is true the device also exposes the wire protocol on the BLE stream channel FFF5 (notify) / FFF6 (write) for phone apps; the paired MITM link stands in for `allow_lan`, which gates only the TCP listener. The single-tester rule spans all transports and a second tester is now answered `ERR_DEVICE_IN_USE` (0x1A) instead of a silent close. The wire protocol is specified in `j2534_server/J2534_WIRE_PROTOCOL.md` (one document for the DLL, the benches and BLE apps).

## 6e15. BLE: `ble_manager` registers its own route (2026-09-21)

| Route | Method | Behavior |
|---|---|---|
| `/api/ble` | GET | `{"enabled","connected","secured","pairing_enabled","max_payload","phy","advertising","phy_tx","phy_rx","channels_cap","channels":[{"name","uuid_out","uuid_in","rx_size","rx_bytes","tx_bytes","rx_overflow","tx_timeouts","tx_link_down","rx_pending","out","out_modes","tx_notifications","tx_indications"}]}`: the BLE link (`secured` = paired, MITM-authenticated; `max_payload` = `min(490, MTU-3)` of the current link) and the stream-channel registry (`http` = FFF3/FFF4 from `ble_http`, `j2534` = FFF5/FFF6 from `ble_j2534`) with per-channel byte counters and the loss indicators a bench compares against the client's view: `rx_overflow` (write-without-response bursts the receive buffer could not hold), `tx_timeouts` (notify credit waits that expired), `tx_link_down` (writes refused or failed with no secured link); `out` = the OUT mode the connected central selected with its CCCD (`indicate` | `notify` | `none` = not subscribed), `out_modes` what the channel offers (`indicate` for j2534, `both` for http), `tx_notifications` / `tx_indications` PDU counts per mode (2026-09-22, protocol v2 of `ble_http`). `phy` / `advertising` echo the v4 settings (`1m|2m|coded|auto`, `legacy|extended|both`); `phy_tx` / `phy_rx` are the live link's PHYs (1 = 1M, 2 = 2M, 3 = coded, 0 = not connected), the witness the `blephy` bench reads |

| `/api/ble/blast` | POST | `{"bytes":N,"chunk":490}` -> `{"started":true}`: pumps N bytes of a counting pattern as notifications on the data pipe (FFF1) from a PSRAM-stack task, retrying a full queue every 2 ms like the July `blast` console command (the bench's device->app throughput source, 2026-09-21; reachable through the BLE tunnel while WiFi is handed over). 400 bounds (1..8 MB, chunk 1..490), 409 `no BLE link` / `blast running`. Progress in `GET /api/ble` `blast:{running,bytes,target,ms}` |

Config = `/api/settings/ble_manager` (see `ble_manager/BLE_API.md` 7). The tunnel's own request counters are on the `blehttp` console command.

## 7. WiFi: `wifi_manager` registers its own routes

| Route | Method | Behavior |
|---|---|---|
| `/api/wifi/status` | GET | `{"enabled":…,"sta_connected":…,"ip":"10.0.0.5","ap_started":…,"ap_default_password":bool,"clients":N,"ap_ip":"192.168.0.10","dns":["…","…"],"sta_attempt":{"ssid","reason","fail_count","deprioritised"}}` (`sta_attempt` since 2026-09-06: the last station attempt, its entry, the `WIFI_REASON_*` of the last disconnect (0 = none), recent consecutive failed connection attempts (any reason but "not found"), and whether that entry is now tried only after the other networks on the list, the memory fades after 2 minutes; no time-based ban exists any more) (wraps the status getters; `ap_ip` = live AP gateway, reflects the configurable `ap_ip` setting, omitted when AP is down). **AP config (v4): `ap_ip`/`ap_hidden`/`ap_bandwidth`/`ap_auth`; WiFi RAM profile (v5): `wifi_ram_profile` full/lean/custom, lean frees ~14 KB internal at no throughput cost, runtime via `esp_wifi_init`. Via `/api/settings/wifi_manager`, see wifi_manager/HTTP_API.md** |
| `/api/wifi/scan` | GET | the `wifi_manager_scan_networks()` JSON verbatim (`{"networks":[…]}`); blocking ≈2 s: the UI shows a spinner |

Config strictly via `/api/settings/wifi_manager`: no bespoke config routes.

## 8. No HTTP surface (by design)

- `http_server_manager`: content-agnostic owner; things register into it.
- `external_storage`: surfaces through `DEV_STATUS_BIT_SDCARD_MOUNTED` and
  `/api/fs/info`.
- `download`: internal service; failures surface in logs.
- `mqtt_manager`: config via `/api/settings/mqtt_manager` (the
  `broker_password` field is redacted in GETs); live state via the
  `mqtt_connected` bit in `/api/status`. Its own surface is MQTT itself
  (`<prefix>/status` online/offline contract: `mqtt_manager/README.md`).
- `http_client_manager`: an HTTP *client*; consumers call its API
  in-firmware. No routes.
- `mdns_manager`: its surface is mDNS itself (`_wican._tcp`, the HA
  discovery contract: `mdns_manager/README.md`); config via
  `/api/settings/mdns_manager`.
- `led_manager`: an output; driven by indications, not HTTP.
- `cmdline_manager`: its surface is the CLI itself, reachable over
  bridged transports (e.g. a `cli <-> ws_cli` bridge exposes it at
  `/ws/cli`: a *WebSocket* route owned by `websocket_manager`, §6g),
  BLE, or UART0; config via `/api/settings/cmdline_manager`. No `/api`
  routes.
- `interface_manager`: pure policy; its state surfaces as the
  `sta_suspended`/`ap_suspended`/`ble_suspended` bits in `/api/status`,
  its rules via `/api/settings/interface_manager`. No routes.
- `mqtt_can`: its surface is MQTT itself (legacy-JSON CAN frames on
  `~/can/rx|tx`: `mqtt_can/README.md`); config via
  `/api/settings/mqtt_can` (both `allow_rx`/`allow_tx` gates default
  false), live counters via the bridge stats in `/api/bridges`.
- `iperf_manager`: bench-only; its surface is the `iperf` CLI
  (reachable over `/ws/cli`); config via `/api/settings/iperf_manager`.
- `button_manager`: GPIO input; a 5 s hold surfaces as the config-mode
  LED pattern + AP, not HTTP; config via `/api/settings/button_manager`.

## 9. Registration ownership summary

| Routes | Implemented by | Depends on |
|---|---|---|
| `/api/settings/*`, `/api/status`, `/api/status/tasks`, `/api/info`, `/api/faults`, `/api/faults/clear`, `/api/restart*`, `/api/logs/*`, `/api/fs/*`, `/api/ota/*` | **`api_http` glue** (Phase 7; supersedes the `settings_http` plan) | settings_manager, dev_status_manager, restart_tracker, log_manager, filesystem, ota_manager, multipart_upload, http_server_manager |
| `/api/wifi/*` | `wifi_manager` (feature layer) | http_server_manager (private) |
| `/api/battery` | `battery_monitor` (feature layer) | http_server_manager (private) |
| `/api/rtc` (GET/POST) | `rtc_manager` (service layer) | http_server_manager (private) |
| `/api/imu` | `imu_manager` (feature layer) | http_server_manager (private) |
| `/api/certs*` | `cert_manager` (service layer) | http_server_manager (private) |
| `/api/logger*` | `data_logger` (feature layer) | http_server_manager (private) |
| `/api/vpn*` | `vpn_manager` (feature layer) | http_server_manager (private) |
| `/api/sleep` | `sleep_manager` (feature layer) | http_server_manager (private) |
| `/api/usb` | `usb_host_manager` (feature layer) | http_server_manager (private) |
| `/api/usb/acm*` | `usb_acm_cli` (feature layer) | http_server_manager (private) |
| `/api/espnetlink*` | `espnetlink_link` (feature layer) | http_server_manager (private) |
| `/api/j2534` | `j2534_server` (feature layer) | http_server_manager (private) |
| `/api/can` | `can_manager` (service layer) | http_server_manager (private) |
| `/api/j1939` | `j1939` (service layer, §6e10b) | http_server_manager (private) |
| `/api/uds/request` | `uds_manager` (service layer) | http_server_manager (private) |
| `/api/scripts*` | `script_engine` (feature layer) | http_server_manager (private) |
| `/ws/*` (WebSocket channels) | `websocket_manager` (service) | http_server_manager (private) |
| `/api/autopid/dtc*` | `autopid` (feature layer, §6e4b) | http_server_manager (private) |
| `/api/autopid/dbc*` | `autopid` (feature layer, §6e4c) | http_server_manager (private) |
| `/api/destinations`, `/api/destinations/test` | `data_destinations` (feature layer, §6e14): MQTT / HTTP(S) / ABRP cyclic pushes of the autopid snapshot; the table is the `data_destinations` settings component | http_server_manager (private) |
| `/api/webhook` | `ha_webhooks` (feature layer): HA integration discovery push (GET/POST/DELETE); outbound telemetry `{status,autopid_data,config,gps}` poster (`gps` = the contract §5.4 block for HA's Location tracker, 2026-09-09). See `ha_webhooks/HTTP_API.md` | http_server_manager (private) |
| future: `/api/can/*`, `/api/obd/*`, … | their feature components | http_server_manager (private) |
