# WiCAN Device HTTP API — Requirements

> Status: **IMPLEMENTED** (2026-07-03; route map last reconciled
> 2026-07-04) — the contract the web UI is built against. The `api_http`
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
> Change protocol (**mandatory** — Standard rev 2.3 §6): a change to any
> route updates the component's endpoint reference **and** the map here in
> the same commit; the UI is built against these files. An undocumented
> route does not exist as far as the product is concerned.

## 1. Conventions

- **Everything lives under `/api/`** — except WebSocket channels, which
  live under **`/ws/`** (websocket_manager; paths are settings-defined and
  must start `/ws/`). These are the TWO reserved namespaces; the
  `http_server_manager` catch-all serves assets for every other path.
  Specific routes always win over the catch-all (§9.3).
- **JSON in, JSON out** (`application/json`), except the log-ring dump
  (`text/plain`). Errors: `{"error":"<human message>"}` with 4xx/5xx.
- **GET never mutates. Mutations are PUT/POST/DELETE.** Reboot-affecting
  writes follow the settings model (§4.2): persist → respond → reboot via
  `restart_tracker_restart()` — never a raw `esp_restart()`.
- **Network-trust lockdown (2026-07-08):** `http_server_manager` runs an
  optional per-request admission gate in front of EVERY route (API, the
  UI catch-all, and `/ws/*` handshakes). When the WiCAN is joined to a
  WiFi network its user marked untrusted (`wifi_manager` `*_trusted`
  flags), any request arriving via the STA address gets **403
  `{"error":"configuration disabled on this network"}`** (WS: handshake
  refused). The device's own AP + the USB link are non-STA and keep full
  admin — the intended management path. Outbound clients (autopid
  http.post, MQTT) don't go through httpd and are unaffected. The whole
  surface is swept by an automated live test (every route × every
  method + WS). New routes are covered automatically —
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

## 2. Settings (all components) — via `api_http`

The generic surface every component's config UI uses (standard §6):

| Route | Method | Behavior |
|---|---|---|
| `/api/settings` | GET | list registered components, enriched (2026-07-05): `{"components":[{"name","version","degraded","pending_reboot"},…]}` — one call for the UI's overview page |
| `/api/settings/<name>` | GET | current (pending) object + `"degraded":true` (§4.3 fallback) + `"pending_reboot":true` (persisted ≠ boot-applied — "restart to apply"); PUT strips both if a UI echoes them back |
| `/api/settings/<name>` | PUT | full-object replace; validate + persist; 400 with the `on_validate` message on reject |
| `/api/settings/<name>/schema` | GET | the JSON Schema — UIs self-describe, no per-component UI code for forms |
| `/api/settings/submit` | POST | end-of-batch: responds `{"reboot":true|false}`, then reboots iff any PUT reported `changed=true` (no-op submits never reboot), via `restart_tracker_restart(CONFIG_APPLY, CONFIG_SERVER)` |
| `/api/settings/backup` | GET | the whole configuration as ONE document (offline backup / device transfer, 2026-07-05) — per-component `{version, data}` + device metadata; **passwords NOT redacted** (the one settings GET returning secrets; UI treats the file as sensitive) |
| `/api/settings/backup` | POST | restore a backup: **all-or-nothing** (every component dry-run-validated first — incl. `on_migrate` for older backups — before anything persists; 400 + per-component errors otherwise), unknown components skipped+reported, reboots iff changed |
| `/api/settings/factory_reset` | POST | wipe the settings partition to factory defaults (2026-07-05; settings ONLY — /data, SD, NVS untouched); requires `{"confirm":"factory-reset"}` else 400 and nothing wiped; reboots via `restart_tracker(FACTORY_RESET, WEB_UI)` |

## 3. Device status — `dev_status_manager` via `api_http`

| Route | Method | Behavior |
|---|---|---|
| `/api/status` | GET | `{"bits":{"sta_connected":true,"ap_enabled":false,…}` (every named `DEV_STATUS_BIT_*` incl. `motion`, `time_synced`, and the interface_manager arbitration bits `sta_suspended`/`ap_suspended`/`ble_suspended` — WHY an interface is down while its config is untouched), `"network_connected":bool` (the STA\|ETH mask), `"uptime":"1d 02:03:04"`, `"version":"…"`, `"partition":"ota_0"`, `"boot_count":N, "unexpected_resets":N,` **`"memory":{internal/psram × total/free/min_free/largest_block}`** (§12b — largest_block is the fragmentation signal), `"temp_c":33.1` (die temperature, 2026-07-08; omitted on sensor error), **`"health":{log_errors,log_warnings,flash:{writes,write_bytes,erases,erase_bytes},caps:{settings/cmdline/bridge_ep × used/cap},faults:N}`** (2026-07-19 silent-failure net — the bench asserts zero errors, flash budgets, registry headroom, zero faults; boot prints the same as `WICAN HEALTH/FLASH/CAPS/FAULTS`)} |
| `/api/info` | GET | `{"device_type","model","hw_version","fw_version","device_id","mac","api_level"}` — cheap, dependency-free identity probe (same keys/casing as the webhook push `status` section; the HA integration verifies identity with this BEFORE control commands; `mac` matches the mDNS TXT record). Owner: `api_http` glue |
| `/api/status/tasks` | GET | Task monitor (JSON since 2026-07-08 — was text/plain): `{"cores":2,"total_us":N,"tasks":[{"name","state"(X/R/B/S/D),"core"(-1=unpinned),"prio","stack_hw"(never-used bytes; small = near overflow),"runtime_us"}]}` busiest first. CPU% = client-side delta between two polls: `Δruntime_us/(Δtotal_us×cores)`; IDLE deltas → system load. Full spec: `dev_status_manager/HTTP_API.md`. Consumed by web-UI `#/monitor` + `system -t` |

One poll answers the dashboard header. No mutations — bits are owned by the
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
| `autopid_enabled`, `autopid_idle` | autopid (added 2026-07-07 — sleep_manager gates its teardown on `autopid_idle`) |
| `time_synced` | rtc_manager |
| `vpn_enabled` | vpn_manager (both types) |
| `eth_connected` | usb_host_manager (USB-Ethernet uplink; part of the `network_connected` mask) |
| `motion` | imu_manager |
| `sta_suspended`, `ap_suspended`, `ble_suspended` | interface_manager (arbitration — WHY an interface is down) |
| `home_mode`, `drive_mode`, `smartconnect` | **RESERVED** — legacy concepts, no v6 publisher yet (future interface_manager/connection rules); always `false` |

## 4. Reboot forensics + reboot — `restart_tracker` via `api_http`

| Route | Method | Behavior |
|---|---|---|
| `/api/faults` | GET | Latched device fault codes (2026-07-19 — the automotive-DTC idea for the firmware): `{"faults":[{"code":"boot_errors","detail":"…","count":N,"first_time":unix,"last_time":unix},…]}`. Raised on structural problems (boot errors, registry headroom, flash budget/churn), persisted to NVS, survive reboot+power-cycle until MANUALLY cleared. CLI: `faults` / `faults -c` |
| `/api/faults/clear` | POST | `{"cleared":true}` — the manual "mode 04". |
| `/api/restart/history` | GET | `{"boot_count":N,"unexpected":N,"records":[{"seq":…,"reason":"software","planned":true,"planned_reason":"config_apply","source":"config_server","boot_time":…,"uptime_at_request_ms":…},…]}` (newest first, to-str names not raw enums) |
| `/api/restart` | POST | respond `{"ok":true}`, wait ≈1 s (flush), then `restart_tracker_restart(USER_REQUEST, WEB_UI, 0)` |

## 5. Logs — `log_manager` via `api_http`

The remote-debugging surface (runtime knobs, §9.4 — ephemeral, never persisted
here; persisted defaults go through `/api/settings/log_manager`):

| Route | Method | Behavior |
|---|---|---|
| `/api/logs/ring` | GET | `text/plain` dump of the PSRAM crash ring (chronological, spans reboots — the first thing support asks for) |
| `/api/logs/ring` | DELETE | clear the ring |
| `/api/logs/status` | GET | `{"dropped":N,"sinks":[{"name":"console","enabled":true},…]}` |
| `/api/logs/level` | PUT | `{"tag":"wifi_manager","level":"debug"}` → `log_manager_set_level()`; `"tag":"*"` allowed |
| `/api/logs/sink` | PUT | `{"name":"ring","enabled":false}` → `log_manager_sink_set_enabled()` |

Ring dumps can be ~16 KiB — stream in chunks (internal-RAM chunk buffer,
§9.4 serving rules).

## 6. Filesystem — `filesystem` via `api_http`

The UI file manager (write surface un-deferred by meatpi 2026-07-04 —
upload any file type, download logs/configs, delete, mkdir; AP-mode trust
model until the auth story). Also serves the settings UI's `format:"file"`
picker (REVIEW §6.1):

| Route | Method | Behavior |
|---|---|---|
| `/api/fs/list?path=/data/web` | GET | `{"path":"/data/web","entries":[{"name":"x.svg","dir":false,"size":123},…]}`; 400 on invalid path (component's own validation) |
| `/api/fs/info?path=/data` | GET | `{"total":N,"used":N}` |
| `/api/fs/download?path=…` | GET | streamed file, `Content-Disposition: attachment` (logs/configs to the browser) |
| `/api/fs/upload?path=…` | POST | multipart (HTML form) or raw body, **STREAMED — no size cap** beyond free space (8 MB live-verified to /sd); ATOMIC (temp+rename at commit); 503 while another transfer runs |
| `/api/fs/file?path=…` | DELETE | delete a file / empty dir |
| `/api/fs/mkdir?path=…` | POST | create dir + parents |

The write surface (upload/delete/mkdir) was un-deferred by meatpi
2026-07-04 and is LIVE — AP-mode trust model until the auth story; the
settings partition is unreachable by construction, traversal refused.

## 6b. Firmware update — `ota_manager` via `api_http`

(Full reference: `ota_manager/HTTP_API.md`.)

| Route | Method | Behavior |
|---|---|---|
| `/api/ota/upload` | POST | firmware image, multipart (`firmware=@…`, the HTML form path) **or** raw `application/octet-stream` body; streams into the OTA session; on success responds `{"ok":true,"received":N,"partition":"ota_1","reboot":true}` then self-reboots via `restart_tracker_restart(OTA_APPLY, WEB_UI)` |
| `/api/ota/status` | GET | `{"state":"idle|receiving|ready|failed","received":N,"total":N,"error":"…","target":"ota_1"}` — poll for progress |

During a session the LED shows the CRITICAL indication (fast-blink red,
main's glue) — do not power off.

## 6c. Battery — `battery_monitor` registers its own route

| Route | Method | Behavior |
|---|---|---|
| `/api/battery` | GET | `{"voltage":11.51}` (latest averaged reading, volts; `null` before the first sample) |

Thresholds/events are an in-firmware surface (watch registration,
`battery_monitor/README.md`) — no HTTP mutation.

## 6d. RTC / time — `rtc_manager` registers its own routes

All times are **UTC** (the firmware pins `TZ=UTC0`; timezone is a
presentation concern of the client).

| Route | Method | Behavior |
|---|---|---|
| `/api/rtc` | GET | `{"time":"…Z","valid":true,"rtc":"…Z","sntp":{"enabled":true,"server":"pool.ntp.org","last_sync":"…Z"}}` — `time` = system clock, `valid` = trusted this boot (RTC restore / SNTP / manual), `rtc` = the chip's own reading (`null` when implausible: fresh board / drained caps), `last_sync` = last successful SNTP (`null` = none this boot) |
| `/api/rtc` | POST | `{"epoch":1751600000}` (UTC seconds, 2020..2099) → sets the system clock AND the RTC, marks TIME_SYNCED; responds with the GET payload. The manual path for AP-mode/no-NTP — the browser knows the time. 400 outside range |
| `/api/rtc/sync` | POST | on-demand SNTP sync (primary then fallback server; blocking, ≤~32 s worst case — UI shows a spinner). 400 when offline, 500 when no server answered, else the fresh GET payload |

SNTP behavior (config via `/api/settings/rtc_manager`): syncs on every
internet (re)connect + every `sync_interval_h`; `ntp_server` +
optional `ntp_server2` fallback are user-configurable.

## 6e. IMU — `imu_manager` registers its own route

| Route | Method | Behavior |
|---|---|---|
| `/api/imu` | GET | `{"activity":"stationary\|active\|unknown","accel":{"x":0.01,"y":0.02,"z":1.00},"temp":34.5}` — accel in g (expect ≈1 g on one axis at rest: gravity), die temp °C; `null` fields when the chip is unreadable |

Motion events are an in-firmware surface (`imu_manager_subscribe`, see
`imu_manager/README.md`) — no HTTP push; poll `activity` or watch the
`motion` bit in `/api/status`.

## 6e2. LED — `led_manager` registers its own routes (2026-07-05)

| Route | Method | Behavior |
|---|---|---|
| `/api/led` | GET | what the LED shows right now (the arbiter's winner): `{"priority":"idle\|status\|alert\|critical","mode":"off\|solid\|blink_slow\|blink_fast","r":0,"g":0,"b":60}`; `{"priority":null}` when the LED is unavailable |
| `/api/led` | PUT | `{"r":0-255,"g":..,"b":..[,"mode":"solid\|off\|blink_slow\|blink_fast"]}` — sets the **ALERT** indication (the user slot, same as the `led -c` CLI). 400 on bad values |
| `/api/led` | DELETE | releases the ALERT indication; the arbiter falls back (STATUS/IDLE) |

REST clients drive ONLY the ALERT priority — firmware-internal
indications (STATUS activity, OTA's CRITICAL do-not-power-off) are never
writable from outside, so the priority ladder stays honest. Idle
color/mode config strictly via `/api/settings/led_manager`.

## 6e3. Data-path stats — `api_http` glue over bridge/socket/ws managers (2026-07-05)

Read-only observability for user dashboards (API-first §1b): the
configured entries come from each component's settings, the live
counters from its stats API. `up:false` (no `stats`) = configured but
not running (disabled, or its endpoint isn't registered).

| Route | Method | Behavior |
|---|---|---|
| `/api/bridges` | GET | `{"bridges":[{"name","a","b","translator","enabled","up","stats":{"a2b_chunks","b2a_chunks","a2b_bytes","b2a_bytes","send_errors","codec_errors"}}]}` |
| `/api/sockets` | GET | `{"servers":[{"name","proto","port","enabled","up","stats":{"clients","bytes_in","bytes_out","rx_drops","tx_drops","reconnects","refused"}}]}` |
| `/api/ws` | GET | `{"channels":[{"name","path","mode","max_clients","enabled","up","stats":{"clients","frames_in","frames_out","bytes_in","bytes_out","rx_drops","tx_drops","refused"}}]}` |

## 6e4. AutoPID — `autopid` registers its own routes (2026-07-06)

| Route | Method | Behavior |
|---|---|---|
| `/api/autopid` | GET | dashboard view: `{"groups":[{"name","enabled","period_ms"}],"params":[{"name","unit","value"\|null,"ts_us"}],"stats":{"running","paused_voltage","polls_ok","polls_failed","pids","filters","period_floor_ms","sub_floor_pids","now_us"}}` — value age = `now_us - ts_us`; `sub_floor_pids > 0` = periods configured below the measured ~53 ms/request chip floor (accepted, can't be honored — UI should warn; see `autopid/BENCHMARKS.md`) |
| `/api/autopid/data` | GET | the LEGACY-shape flat snapshot `{"Name":value,…}` (only parameters that have polled at least once) |
| `/api/autopid/config` | GET | the PID/filter tables file verbatim (`{"groups":[],"pids":[],"filters":[]}` when none saved) |
| `/api/autopid/config` | PUT | full tables JSON ≤256 KB — validated (expressions dry-run, group refs, bounds), atomically saved to `/data/autopid/config.json`, then reloaded **LIVE** (no reboot). 400 + `{"error":…}` on any invalid entry; the old tables keep running |
| `/api/autopid/std_scan` | POST | start the async standard-PID support scan (one-shot task: pauses polling, sets `std_protocol`, walks the 0100/0120/…/01A0 bitmaps multi-ECU OR-merged, stores the result, resumes polling). 202 `{"started":true}`; 409 while one is running. UI shows "ignition ON" before calling |
| `/api/autopid/std_scan` | GET | job status: `{"status":"idle\|running\|done\|failed","found":N[,"error"],"ts":epoch,"stored":bool}` |
| `/api/autopid/std_scan/result` | GET | the stored `/data/autopid/std_scan.json` verbatim: `{"version","protocol","supported":[{"pid","cmd","name","parameters":[{"name","expression","unit","class"[,"min","max"]}]}],"found","ts"}` — entries are ready-made config rows (expressions generated from the SAE table); scan once, store, never auto-rescan |
| `/api/autopid/std_table` | GET | the full built-in SAE mode-01 table (~166 PIDs) in the same entry shape — the UI's "all possible standard PIDs" list |
| `/api/autopid/test` | POST | test-a-PID one-shot through the REAL runner path: `{"cmd" (required), "init"?, "rxheader"?, "expression"?}` → `{"ok","raw","elapsed_ms","payload"? (hex),"value"?,"error"?}`. Pauses polling around the shot (state restored); 409 while another test runs; 400 on bad expression/lengths. The `/ws/obd` console can't reproduce init/rxheader/expression handling — this can (bench: the full MEB 29-bit UDS init chain in one call) |

| `/api/autopid/group` | POST | runtime (non-persisted) group toggle — the HTTP twin of the `autopid.group` event action (API-first §1b): `{"name":"…","enabled":bool,"period_ms"?:N}` → `{"ok":true}`; 404 unknown group. A reboot restores the configured state. Added 2026-07-07; bench scripts use it to claim OBD exclusivity (ws_live_test.py pauses/restores all groups) |

The tables live in a FILE, not settings (profiles exceed the settings
16-item array cap); the `autopid` settings component holds only the
knobs (enable, per-type enables, init strings, pause voltage,
`min_event_interval_ms`, and — schema v2, 2026-07-07 — `backend` =
`obd_chip`|`elm327`, see §6e10) and stays reboot-to-apply.

### 6e4b. DTC check / report / clear

DTC scanning (OBD modes 01-01/03/07/0A) + conditional clearing (mode 04) as
an autopid sub-module. **Disabled by default** — settings v3 adds
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
| `/api/autopid/dtc` | GET | `{"enabled","allow_clear","scanning","report":{"valid","ts","mil","mil_count","ecus","protocol","stored":["P0420",…],"pending":[…],"permanent":[…],"new":[…]["error"]}}` — always available; `report.valid=false` until the first scan. Since 2026-07-22 a functional OBD scan MERGES every responding ECU (union lists, `mil` OR, `mil_count` sum, `ecus` count) and `dtc_protocol` settings (`obd`/`uds`/`auto`) add ISO 14229 0x19 scans + 0x14 clears: `protocol` names the path that answered, UDS codes may carry a failure-type suffix (`"P0420-08"`; FTB 0 suffixless), and under `uds` a clear with a `codes` list is a TRUE per-code clear. Since 2026-07-26 (`dtc_freeze`, default true) an OBD scan that finds stored codes also captures **freeze frame 0** (mode 02): `report.freeze = {"dtc":"P0301","ecu":"7E8","params":{"EngineRPM":{"value":3000,"unit":"rpm"},…}}` — `dtc` = DTCFRZF (which code froze the frame), `ecu` = the responder that holds it (first responder wins, multi-ECU freeze = v2), `params` = the curated PID set decoded via the standard table, bitmap-pruned. Absent when nothing was captured (no codes, no frame, UDS scans, or the knob off) |
| `/api/autopid/dtc/scan` | POST | start the async DTC scan (std_scan job pattern; one chip job at a time incl. std scan/test); 202 `{"started":true}`; 409 busy; 403 when `dtc_enabled=false`. Right after boot the chip is provisioning ~15 s — a scan then fails with `"no ECU response"`, just re-scan |
| `/api/autopid/dtc/clear` | POST | `{"confirm":true,"codes"?,"mode"?:"always\|if_any\|if_only"}` → `{"ok","cleared","before","after"}`; 400 missing confirm; 403 unless `dtc_allow_clear`; 409 busy. Mode 04 clears ALL codes + readiness monitors + MIL — the conditional modes gate the wipe on the currently-present set (`if_only` = refuse when an unlisted code would be wiped); permanent (0A) codes survive by design |

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
| `/api/autopid/dtc/db/search?q=&db=&offset=&limit=` | GET | the picker: `{"total","items":[{"code","desc","db"}]}` — `q` = code prefix OR description substring (case-insensitive), empty = browse; limit ≤100 |
| `/api/autopid/dtc/lookup?codes=P0420,P0171` | GET | batch enrich: `{"P0420":"…","P0171":null}` |

### 6e4c. DBC files → filter parameters

Upload `.dbc` files (≤4, ≤1 MB / 400 messages / 3000 signals each),
browse/search the signals, and add selected ones to autopid **filters**:
each signal compiles into an `expression_parser` expression (Intel/
Motorola, signed/unsigned, sub-byte — all host-cross-checked against a
reference decoder), merged into `/data/autopid/config.json` (one filter
per frame id), dry-run validated, atomically saved, and applied LIVE.
Unsupported signals (multiplexed, float, >95-char expressions) are
listed with a reason, never silently dropped. UI: Vehicle › DBC Signals.

| Route | Method | Behavior |
|---|---|---|
| `/api/autopid/dbc` | GET | `{"dbcs":[{"name","messages","signals","bytes"}],"max":4}` |
| `/api/autopid/dbc?name=<n>` | POST | raw .dbc body → parse/store/cache → `{"ok","name","messages","signals"}`; 400 (`"no signals found (N SG_ rejected, first: …)"` / name / size), 409 slots full |
| `/api/autopid/dbc?name=<n>` | DELETE | remove file + cache; 404 unknown |
| `/api/autopid/dbc/signals?db=&q=&offset=&limit=` | GET | `{"total","items":[{"db","msg","id","name","unit","start","len","order","signed","factor","offset"[,"min","max"],"supported",("expression"\|"reason")}]}` — q matches signal or message name; limit ≤100 |
| `/api/autopid/dbc/add` | POST | `{"db","signals":["Name"…]\|[{"id","name"}…],"group"?,"monitor_ms"?,"period_ms"?}` → merge into autopid filters → `{"ok","added","filters"?,"skipped":[{"name","reason"}]}`; 400 = merged config failed validation (nothing saved); added=0 → ok:false + skips, nothing written |

## 6e5. Events — `event_manager` registers its own routes (2026-07-06)

The rule editor's vocabulary comes entirely from discovery (API-first
§1b — zero hardcoded dropdowns). Rules/timers themselves are settings
(`/api/settings/event_manager`, strict validation at PUT).

| Route | Method | Behavior |
|---|---|---|
| `/api/events/sources` | GET | declared events: `[{"event":"autopid.param","description",…,"keys":[{"key","type"}]}]` |
| `/api/events/actions` | GET | registered actions: `[{"name":"mqtt.publish","params_schema":{…}}]` — the schema drives the `with` form |
| `/api/events/values` | GET | pull-value names (`"autopid."` trailing dot = prefix family) |
| `/api/events/log` | GET | `{"stats":{running,published,dropped,fired,action_errors,suppressed,blocking_dropped},"events":[{source,name,ts_us,data{},fired:[rule names]}]}` — last 32 events, oldest first; THE rule-debugging view. `blocking_dropped` = slow (network/bus) action jobs dropped-oldest when the worker-pool queue was full |

## 6e6. Data logger — `data_logger` registers its own route (2026-07-07)

| Route | Method | Behavior |
|---|---|---|
| `/api/logger` | GET | `{"enabled","running","paused","storage_ok","file":"dl_<epoch>.db","file_rows","files","queued","written","dropped","errors","rotations","can":{"enabled","file":"can_<epoch>.wdl","file_rows","files","queued","frames_written","frames_dropped","rotations"},"dir":"/sd/logs"}` — top level = the params stream, `can{}` = the CAN-frames stream (2026-07-09 two-stream addendum) |
| `/api/logger/gate` | POST | runtime (non-persisted) logging gate for BOTH streams — the HTTP twin of the `logger.enable`/`logger.disable` event actions (API-first §1b): `{"enabled":bool}` → `{"ok":true}`; observable as `paused` in the GET. A reboot restores the configured state. Added 2026-07-07 |
| `/api/logger/export` | GET | incremental pull of the PARAMS stream, `format=jsonl` only (400 otherwise): `?stream=params&since=<epoch>:<off>&limit=N` streams raw jsonl lines from the rotated files starting at the cursor, briefly gating the logger if it must read the ACTIVE file; final chunk is `{"_cursor":"epoch:off","more":bool}` — feed `_cursor` back as `since` to tail. Poll-based exporter for integrations that can't mount the SD |

Config = `/api/settings/data_logger` (v2): per-stream engine choice
(`format` = params: sqlite|csv|binary|jsonl; `can_format` = CAN:
binary|csv|sqlite|**mf4** (MDF 4.10 ASAM — asammdf/CANoe/MATLAB)|
**blf** (Vector, python-can readable)|candump (can-utils/SavvyCAN)|
asc (CANalyzer text)|jsonl — mf4/blf/asc are single-use per boot/
rotation, addendum-2 2026-07-09),
`autopid_log` off|changed|all (the autopid→logger sink),
`can_log` + `can_filter`/`can_mask`/`can_ext` (hex id filter, "" =
all frames; needs can_manager enabled), per-stream rotation/retention
(`max_file_mb`/`max_files`, `can_max_file_mb`/`can_max_files`),
`ring_len`. The log FILES are ordinary `/sd/logs` entries
(`dl_<epoch>.*` params, `can_<epoch>.*` frames) — browse / download /
delete through the §6 filesystem routes; no duplicate file surface
here. The ACTIVE file of each stream is write-locked
(`CONFIG_FATFS_FS_LOCK`): `/api/fs` download/delete of it FAILS
instead of corrupting the card — pause via `/api/logger/gate` (closes
+ flushes) or wait for rotation. `.wdl` files decode offline with
`tools/wdl_dump.py` (`--to csv|candump|text`). Event actions (§6e5
discovery lists them):
`logger.enable` / `logger.disable` (rule-driven gating) and
`logger.write {source?, name, value}` — the SELECTIVE producer path: a
rule like `autopid.param -> logger.write {"source":"autopid",
"name":"${param}","value":"${value}"}` records exactly the parameters
the user picks (the `autopid_log` setting is the log-everything twin).

## 6e7. VPN — `vpn_manager` registers its own routes (2026-07-07)

| Route | Method | Behavior |
|---|---|---|
| `/api/vpn` | GET | `{"state":"disabled\|waiting\|connecting\|connected","type":"wireguard\|tailscale","endpoint":"host:port","ts_ip":"100.64.x.y","ts_peers":N,"connects":N,"failures":N,"uptime_s":N}` — `ts_*` fields are only meaningful for type=tailscale |
| `/api/vpn/keygen` | POST | generate a Curve25519 pair ON the device; private key goes straight into pending settings (never over HTTP); responds `{"public_key":"…","pending_reboot":true}` (wireguard type) |

Config = `/api/settings/vpn_manager`. `type` selects the stack:
`wireguard` (fields `private_key`*, `peer_public_key`, `preshared_key`*,
`address`, `allowed_ip`+mask, `endpoint`, `port`, `keepalive_s`,
`default_route`, `dns`) or `tailscale` (fields `ts_auth_key`*,
`ts_device_name`, `ts_control_url` — empty control_url = official
Tailscale, `host[:port]` = Headscale/Ionscale). Secret fields
(`*` above — the api_http redaction suffixes `private_key`,
`preshared_key`, `auth_key`, extended 2026-07-07) come back `""` in
GETs; sending `""` on PUT keeps the stored value.

## 6e8. Sleep — `sleep_manager` registers its own route (2026-07-07)

| Route | Method | Behavior |
|---|---|---|
| `/api/sleep` | GET | `{"enabled","state":"normal\|low_voltage\|sleeping\|wake_pending","voltage","sleep_v","wake_v","naps"}` |

Config = `/api/settings/sleep_manager`. No HTTP mutation — forcing a
sleep is a bench affair (`sleep test <secs>` CLI).

## 6e9. USB host — `usb_host_manager` registers its own route (2026-07-07)

| Route | Method | Behavior |
|---|---|---|
| `/api/usb` | GET | `{"enabled":bool,"device_present":bool,"host_active":bool,"eth_connected":bool,"driver":"asix\|rtl8152\|cdc_ecm\|cdc_ncm\|rndis\|","ip":"10.42.1.x\|","attaches":N}` — USB-connector role + USB-Ethernet uplink status. `device_present` = the ID pin sees a device; `host_active` = the mux is on the ESP OTG host; `attaches` counts enumerations since boot |
| `/api/usb/acm` | GET | `{"connected":bool}` — is a CDC-ACM console (the espnetlink's management interface) bound |
| `/api/usb/acm/cmd` | POST | `{"cmd":"lte -s","timeout_ms"?:N (default 8000, a CAP not a latency)}` → `{"ok","response","connected"}` — send one line to the espnetlink's management console; the reply is collected until the dongle's `esp>` PROMPT (2026-07-13 fix — the old 150 ms quiet window raced the echo→payload gap and returned "no reply" for the modem-querying commands, the web-UI ESPNetLink card bug; 2 s quiet remains as the prompt-less fallback, since the modem legs go silent mid-response). 409 if no device/busy. The console is the dongle's OWN CLI (`esp>` prompt: `ver`, `lte -s/-r/-o/-i` signal/operator/IP, **`lte -j`** = machine-readable JSON the web UI's ESPNetLink Status card polls, `gps -p -j`, `speedtest`, `ping`, `config`, `agnss`), NOT raw AT. Owner: `usb_acm_cli` (config `/api/settings/usb_acm_cli`: `enabled` default false, `cli`); also a bridge_manager endpoint `acm` for raw passthrough |
| `/api/gps` | GET | `{"valid":bool}` or, with a live fix, `{"valid":true,"latitude","longitude","accuracy"(m, HDOP-derived),"altitude"(m),"speed"(m/s),"heading"(deg),"satellites","age_ms"}` — the ESPNetLink dongle's last fix (device-contract `gps` field names). Owner `usb_acm_cli`: a poll task runs the dongle's `gps -p -j` every 5 s and caches the parsed fix (read here — no ACM I/O). **The same fixes are ALSO published as first-class autopid parameters** `gps_latitude`/`gps_longitude`/`gps_altitude`(m)/`gps_speed`(km/h)/`gps_heading`/`gps_satellites`, so they appear in `autopid_data` (→ the HA push, the dashboard, `${autopid.data}`), the `data_logger`, and `autopid.param` event rules with no GPS-specific code in those consumers. Only a LIVE fix is reported (a cached AGNSS position is never presented as current) |

Config = `/api/settings/usb_host_manager`. New additive setting `role` (`host`|`device`, default `host`, reboot-to-apply): when `enabled`, picks whether the ESP-OTG runs as a CherryUSB **host** (USB-Ethernet/espnetlink — today) or a CherryUSB **device** — with `device_class` {`ncm`|`rndis`|`cdc`}: `ncm`/`rndis` make WiCAN a USB-Ethernet DEVICE (a NIC to the PC → IP-over-USB carries the J2534 TCP server + web UI + bridges over one cable), `cdc` a serial COM port (J2534 USB transport — Phase 3). `enabled=false` keeps the connector on the CH342 (serial). The connector serves ONE
role at a time: host mode (this surface) or device mode (the CH342 —
serial console/`usb_obd` to a PC); the `eth_connected` dev-status bit
feeds the §3 `network_connected` mask. The `usb` CLI mirrors this GET
(its `mux`/`vbus` subcommands are temporary bench debug, not API
surface).

## 6e10. Native CAN — `can_manager` registers its own route (2026-07-07)

| Route | Method | Behavior |
|---|---|---|
| `/api/can` | GET | `{"enabled":bool,"running":bool,"silent":bool,"baud_kbps":N,"state":"running"/"bus_off"/"recovering"/"stopped","tx":N,"rx":N,"tx_errors":N,"rx_errors":N,"arb_lost":N,"bus_errors":N,"rx_missed":N,"dispatch_drops":N,"bus_off":N,"recoveries":N}` — the native TWAI bus (WiCAN Pro TX=GPIO2/RX=GPIO1/STDBY=GPIO38), shared by every CAN consumer. `tx_errors`/`rx_errors` are the live TEC/REC; `rx_missed` = wire frames lost to a full TWAI RX queue (RX task starved), `dispatch_drops` = drop-oldest evictions across subscriber queues (a consumer's drain starved) — both 0 in healthy operation, they localize any frame-loss report. Bus-off recovery is AUTOMATIC (2026-07-10): re-enter as soon as hardware recovery completes; rapid re-offense backs the restart off 1→2→4…30 s (escalation resets after 60 s of stability). `bus_off`/`recoveries` count episodes; `state` reads `recovering` from bus-off until the restart lands |

Config = `/api/settings/can_manager` (`enabled` default false, `baud`
enum 33…1000 kbit/s, `silent`, `cli`). The bus is SHARED: software AT
engines (add-on packs), autopid's `elm327` backend, and the
slcan/gvret/internal-CAN consumers all multiplex it — unlike the
single-master MIC3624, multiple clients run CONCURRENTLY.

`/api/settings/obd_gate` (`enabled` default TRUE, 2026-07-11): the
bus-conversation gate. The MIC chip and the ESP-side ELM engines share
ONE physical CAN bus; overlapping request/response conversations
mis-attribute responses (a BLE app polling the chip + autopid's
`elm327` backend = bad data). The gate serializes them — one
conversation at a time, fair turn-taking, fail-open (a wedged holder
can't block the other side; holds self-expire at 2 s). Disable only
for test setups that WANT concurrent conversations.

## 6e11. UDS terminal — `uds_manager` registers its own route (2026-07-07)

| Route | Method | Behavior |
|---|---|---|
| `/api/uds/request` | POST | one UDS (ISO 14229) request→final response over the selected transport. Body `{"tx_id","rx_id"(hex str or int),"ext"?:bool,"data":"22 F1 90"(hex),"p2_ms"?,"p2star_ms"?,"session"?:bool}` → `{"ok",response":"62 F1 90 …"(hex),"length","positive":bool,"sid","nrc"?,"nrc_name"?,"pending","elapsed_ms","backend"}`. Handles the 0x78 responsePending loop + NRC decode; 409 while another UDS transaction is in flight |

Config = `/api/settings/uds_manager` (`backend` =
`auto`|`obd_chip`|`elm327`|`isotp`, default **auto** = isotp when
can_manager is running else obd_chip; `p2_ms`, `p2star_ms`,
`tester_present_ms`, `cli`). Backends: **isotp** (firmware ISO-TP over
native CAN — the proven, recommended path, ≤8 KB multi-frame PDUs;
needs the ISO-TP provider from the add-on pack, else falls back to
obd_chip); **obd_chip** (the MIC via AT — reaches the OBD-connector
bus); **elm327** (a dedicated software AT engine — add-on pack,
experimental). `uds` CLI: `uds -t 7E0 -r 7E8 22 F1 90`.

## 6e12. Scripting — `script_engine` registers its own route (2026-07-07)

| Route | Method | Behavior |
|---|---|---|
| `/api/scripts` | GET | `{"scripts":[{"name","size"}…],"dir":"/data/scripts","busy":bool}` — stored scripts for the UI. File CRUD rides the generic `/api/fs` surface (`upload?path=/data/scripts/x.be`, `download`, DELETE `file`) |
| `/api/scripts/run` | POST | `{"src":"berry …"}` (inline) or `{"name":"uds_diag"}` (stored `/data/scripts/<name>.be`) → `{"ok","output"}`; 404 unknown name, 409 busy/disabled |
| `/api/scripts/stop` | POST | kill switch for the running script → `{"ok":true}` |


Event wiring (2026-07-07 pm): rules may use the sugar body `{"on":"source.event","script":"name"}` (parser rewrites to the `script.run {name}` action) — the trigger event reaches the script as `evt_source`/`evt_name`/`evt_<key>` globals; scripts emit `script.done {value}` via `emit()` (a declared source rules can chain on). `uds.request {tx,rx,req,ext?}` is an action too, publishing `uds.response {ok,nrc,len,data,req}` (data truncated to the event kv limit — full payloads belong in a script's `uds()` binding). Actions run on the event dispatcher and BLOCK it for the transaction — same contract as `http.post`; keep event-triggered scripts short.

Config = `/api/settings/script_engine` (`enabled` default false,
`max_runtime_ms`, `cli`). The Berry bindings ARE the scripting API
(SCRIPTING.md): `uds(tx,rx,hexreq)` / `uds_ext(...)` → response hex (or
nil), with `uds_ok`/`uds_nrc` globals; `can_tx(id,ext,hex)`;
`emit(source,name,key,value)`; `log`, `sleep_ms`, `millis`. `script`
CLI: `script test` | `script stop`. Stored-script CRUD on
`/data/scripts` + event-triggered `script` rules are the documented
follow-up (v1 runs inline source, enough for the UDS terminal +
scripting).

## 6f. Certificates — `cert_manager` registers its own routes

| Route | Method | Behavior |
|---|---|---|
| `/api/certs` | GET | `{"sets":[{"name":"bench","ca":true,"cert":false,"key":false},…]}` |
| `/api/certs/upload?set=<a-z0-9_->` | POST multipart | fields `ca`/`client_cert`/`client_key` (the legacy form names), any subset in one request; each part PEM-validated ≤8 KB |
| `/api/certs/upload?set=<name>&type=ca\|cert\|key` | POST raw | one PEM body ≤8 KB (wrong PEM kind → 400) |
| `/api/certs?set=<name>` | DELETE | delete the whole set |

**No read-back** — key material never leaves the device through this
surface. Consumers reference sets by name (`mqtt_manager`'s `cert_set`
setting → mqtts server-auth or mutual TLS).

## 6g. WebSocket channels — `websocket_manager` owns `/ws/*`

Not HTTP request/response, but the same server and a reserved namespace:
settings-defined channels (defaults: `ws_obd` `/ws/obd` binary **ships
ENABLED** — paired with `bridge_manager`'s default `obd<->ws_obd` bridge,
so OBD over WebSocket works out of the box on a factory-fresh device;
`ws_can` `/ws/can` binary and `ws_cli` `/ws/cli` text stay parked until
enabled) upgrade at their path, gate `max_clients` before the 101, and
bridge to whatever endpoint the `bridge_manager` settings pair them with
(OBD-over-WS live-verified 2026-07-04; shipped-default flip 2026-07-05;
details + benchmarks in `websocket_manager/`).

## 6e13. J2534 PassThru — `j2534_server` (groundwork, 2026-07-07)

| Route | Method | Behavior |
|---|---|---|
| `/api/j2534` | GET | `{"enabled","port","listening","client_connected","device_open","channels","frames_rx","frames_tx","phase"}` — SAE J2534 PassThru server status |

Config = `/api/settings/j2534_server` (`enabled` default false, `port` default 6809, **`allow_reflash` default false** = reject UDS memory-transfer services (34/35/36/37) so a tool can diagnose but not write ECU firmware, **`allow_lan` default false** = accept only on WiCAN's SoftAP + USB-device (NCM) netifs, refusing STA / USB-Ethernet uplinks since the transport is unauthenticated, `cli`). Status `/api/j2534` echoes `allow_reflash`/`allow_lan`. WiCAN as a SAE J2534-1 PassThru device: a PC diagnostic/reflash tool drives WiCAN's CAN bus via the companion Windows DLL over a versioned wire protocol — the driver ships as an **installer attached to firmware releases** (real-tool validated with the DrewTech J2534-1 tool, incl. a real reflash via the PassThru* API). Reachable over TCP on WiFi / USB-Ethernet / **USB-device (CDC-NCM)**: with `usb_host_manager.role=device` WiCAN is a USB NIC to the PC — DHCP at 192.168.82.1/24, so the DLL target is `WICAN_J2534_HOST=192.168.82.1` over the cable. Transports (all carry the SAME protocol, all TCP): USB-device CDC-NCM (IP-over-USB), WiFi, USB-Ethernet. ISO15765 channels require the ISO-TP provider (add-on pack — present in official builds); raw CAN channels work everywhere. `j2534` CLI mirrors the status.

## 7. WiFi — `wifi_manager` registers its own routes

| Route | Method | Behavior |
|---|---|---|
| `/api/wifi/status` | GET | `{"enabled":…,"sta_connected":…,"ip":"10.0.0.5","ap_started":…,"clients":N,"ap_ip":"192.168.0.10","dns":["…","…"]}` (wraps the status getters; `ap_ip` = live AP gateway, reflects the configurable `ap_ip` setting, omitted when AP is down). **AP config (v4): `ap_ip`/`ap_hidden`/`ap_bandwidth`/`ap_auth`; WiFi RAM profile (v5): `wifi_ram_profile` full/lean/custom — lean frees ~14 KB internal at no throughput cost, runtime via `esp_wifi_init`. Via `/api/settings/wifi_manager` — see wifi_manager/HTTP_API.md** |
| `/api/wifi/scan` | GET | the `wifi_manager_scan_networks()` JSON verbatim (`{"networks":[…]}`); blocking ≈2 s — the UI shows a spinner |

Config strictly via `/api/settings/wifi_manager` — no bespoke config routes.

## 8. No HTTP surface (by design)

- `http_server_manager` — content-agnostic owner; things register into it.
- `external_storage` — surfaces through `DEV_STATUS_BIT_SDCARD_MOUNTED` and
  `/api/fs/info`.
- `download` — internal service; failures surface in logs.
- `mqtt_manager` — config via `/api/settings/mqtt_manager` (the
  `broker_password` field is redacted in GETs); live state via the
  `mqtt_connected` bit in `/api/status`. Its own surface is MQTT itself
  (`<prefix>/status` online/offline contract — `mqtt_manager/README.md`).
- `http_client_manager` — an HTTP *client*; consumers call its API
  in-firmware. No routes.
- `mdns_manager` — its surface is mDNS itself (`_wican._tcp`, the HA
  discovery contract — `mdns_manager/README.md`); config via
  `/api/settings/mdns_manager`.
- `led_manager` — an output; driven by indications, not HTTP.
- `cmdline_manager` — its surface is the CLI itself, reachable over
  bridged transports (e.g. a `cli <-> ws_cli` bridge exposes it at
  `/ws/cli` — a *WebSocket* route owned by `websocket_manager`, §6g),
  BLE, or UART0; config via `/api/settings/cmdline_manager`. No `/api`
  routes.
- `interface_manager` — pure policy; its state surfaces as the
  `sta_suspended`/`ap_suspended`/`ble_suspended` bits in `/api/status`,
  its rules via `/api/settings/interface_manager`. No routes.
- `mqtt_can` — its surface is MQTT itself (legacy-JSON CAN frames on
  `~/can/rx|tx` — `mqtt_can/README.md`); config via
  `/api/settings/mqtt_can` (both `allow_rx`/`allow_tx` gates default
  false), live counters via the bridge stats in `/api/bridges`.
- `iperf_manager` — bench-only; its surface is the `iperf` CLI
  (reachable over `/ws/cli`); config via `/api/settings/iperf_manager`.
- `button_manager` — GPIO input; a 5 s hold surfaces as the config-mode
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
| `/api/j2534` | `j2534_server` (feature layer) | http_server_manager (private) |
| `/api/can` | `can_manager` (service layer) | http_server_manager (private) |
| `/api/uds/request` | `uds_manager` (service layer) | http_server_manager (private) |
| `/api/scripts*` | `script_engine` (feature layer) | http_server_manager (private) |
| `/ws/*` (WebSocket channels) | `websocket_manager` (service) | http_server_manager (private) |
| `/api/autopid/dtc*` | `autopid` (feature layer, §6e4b) | http_server_manager (private) |
| `/api/autopid/dbc*` | `autopid` (feature layer, §6e4c) | http_server_manager (private) |
| `/api/webhook` | `ha_webhooks` (feature layer) — HA integration discovery push (GET/POST/DELETE); outbound telemetry `{status,autopid_data,config}` poster. See `ha_webhooks/HTTP_API.md` | http_server_manager (private) |
| future: `/api/can/*`, `/api/obd/*`, … | their feature components | http_server_manager (private) |
