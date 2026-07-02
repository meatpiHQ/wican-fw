# WiCAN → Two-Mode OpenPort Replacement — Trim-Down Plan

> **Scope of THIS branch:** the *subtractive* refactor only — strip WiCAN down to a
> focused two-mode datalogger/flasher. New features (cloud auto-upload, live UDP
> telemetry) are explicitly **out of scope** and get their own feature branches
> later (see §7). Do not build them here.
>
> **Intended executor:** an agent with physical access to **both the WiCAN device
> and a test ECU**. Every phase ends in a firmware you flash and verify on real
> hardware. This document is self-contained — you should not need any prior chat
> context to execute it.

---

## 0. Context & Goal

WiCAN today is a general-purpose CAN gateway with a large surface (MQTT, VPN,
Home Assistant, dashboards, CAN monitor, terminal, vehicle profiles, cloud
telemetry destinations, …). We are collapsing it into a **Tactrix OpenPort 2.0
replacement** with exactly **two user-facing modes**:

1. **DATALOGGING (default mode)** — poll a user-defined **custom PID list** over
   OBD and/or raw CAN, log to the **SD card as CSV**, and surface the **latest
   logs to download first**. A file viewer stays.
2. **FLASHING TOOL** — read & write ECU ROM, read & clear DTCs. This is driven
   **entirely by the external "NC Flash" desktop tool** over the dedicated SLCAN
   socket (port **35001**). The device side already exists and **must keep
   working** — we do not build new flashing UI in this branch.

Everything that does not serve those two modes (or the un-brick guardrails) is
removed.

---

## 1. Hard Invariants — never violate these

These hold at **every commit**, in **every phase**:

1. **The un-brick path is untouchable.** Never remove, hide, gate, or refactor:
   - `safe_mode_check()` boot recovery + the whole `main/safemode.c` surface
     (safe-mode AP root `/`, `POST /upload_firmware`, `POST /factory_reset`)
   - `POST /upload/ota.bin` (the OTA lifeline) + the System-tab OTA form
   - the Recovery AP (5-second button hold → safe mode)
   - dual OTA slots (`ota_0` / `ota_1` / `otadata`) in **both** partition CSVs
   - `main/multipart_upload.c` (the parser OTA depends on)
2. **The NC Flash device-side contract is untouchable.** Keep `slcan_port.c`
   (port **35001** — must match the desktop tool's `constants.py`),
   `ncflash_fastread.c`, `ncflash_fastwrite.c`, `/upload/sd/*`, the
   `POST/GET /datalog` pause/resume lease, `datalog_lease_task.c`, and the
   `can.c` `FLASH_ACTIVE_BIT` single-owner interlock.
   - ⚠️ **`NCFW_ALLOW_LIVE` is currently `1`** in `main/ncflash_fastwrite.c` —
     this build can perform **real ECU writes**. For routine regression use NC
     Flash **dry-run (mode 'D')** only. Do not trigger a live flash ('L') unless
     you are deliberately testing flashing on a recoverable ECU.
3. **Build stays green at every commit.** `main/CMakeLists.txt` is the **master
   cut-list**: its `requires` list hard-links every component. Delete a component
   directory and remove its `requires` entry **in the same commit**, or the build
   breaks. **One atomic commit per component.**
4. **Subtractive before additive.** Only deletions/prunes in this branch.
5. **Recovery is verified before and after anything risky** (see §3).

---

## 2. Hardware Test Setup

You have the WiCAN + a test ECU. Establish, once:

- A reliable **flash path** (USB `idf.py flash`, and confirm OTA via
  `/upload/ota.bin` works too — OTA is your in-field update + rollback path).
- A **known-good Wi-Fi** the device can join in STA, plus the device's own AP.
- The **NC Flash desktop tool** pointed at the device (port 35001), able to do a
  **dry-run** read/identify against the ECU.
- An SD card inserted and mountable.
- A way to drive/observe **battery voltage** (the ignition/sleep signal is
  derived from voltage in `main/vehicle.c` / `main/sleep_mode.c`).

Targets: develop on **ESP32-S3 (WiCAN Pro)** primarily. Do a final regression on
**ESP32-C3** too (single-core, no PSRAM) — it is the tighter constraint.

---

## 3. Device Regression Checklist — run after EVERY phase

This is your suite. A phase is not "done" until every line passes on hardware:

- [ ] Boots cleanly; serial log shows `safe_mode_check passed`.
- [ ] Device AP comes up **and** STA joins the known Wi-Fi.
- [ ] Web UI loads at `/`; only the expected tabs are present for that phase.
- [ ] Configure the **custom PID list**, start a **≥60 s datalog trip**.
- [ ] CSV file lands on the SD card; **download it** from the UI; it parses.
- [ ] **NC Flash dry-run** (port 35001) connects and identifies the ECU.
- [ ] `POST /upload/ota.bin` accepts a build; device reboots into it; **flash the
      previous build back** to prove rollback.
- [ ] **5-second button hold → Recovery AP** appears and serves the OTA form.

Tag the baseline before Phase 1 so you can always bisect/rollback.

---

## 4. KEEP / DITCH / DEFER Reference

### KEEP — datalogging engine
`components/autopid` (strip its cloud-destination flavors — see Phase 2),
`components/csv_logger`, `components/fast_log` (+ `poll_log.c`),
`components/event_log`, `components/sd_filemgr`,
`main/elm327.c`, `main/obd.c`, `main/obd2_standard_pids.c`,
`main/expression_parser.c` (load-bearing for custom-PID decode despite its
"MQTT filter" label), `main/vehicle.c`, `main/imu.c`, `main/icm42670.c`,
`main/wc_uart.c`.

### KEEP — NC Flash (flashing mode, all of it)
`main/ncflash_fastread.c`, `main/ncflash_fastwrite.c`, `main/slcan_port.c`,
`main/slcan.c`, `main/datalog_lease_task.c`, `main/can.c` (incl. the full
11-entry `twai_timing_config[]` table — see landmine #5).

### KEEP — settings + connectivity + infra
`main/wifi_mgr.c`, `main/wifi_network.c`, `main/smartconnect.c`,
`main/config_mode.c`, `main/wc_mdns.c`, `main/ble.c`, `main/sleep_mode.c`
(power saving), `main/config_server.c`, `main/filesystem.c`, `main/sdcard.c`,
`main/comm_server.c` (prune RealDash/SavvyCAN arms), `main/hw_config.c`,
`main/console.c` (serial console — NOT the web Terminal), `main/rtcm.c`,
`main/sync_sys_time.c`, `components/cmdline`, `components/wican_common`,
`components/restart_tracker`, `main/dev_status.c`, plus `led.c` / `wc_timer.c` /
`types.h` / `ver.h`.

### KEEP — guardrails
`main/safemode.c`, `main/multipart_upload.c`, partition tables, OTA endpoints
(see §1).

### KEEP — web tabs (trimmed)
Status, Settings (Wi-Fi + **single** CAN config), Automate (→ becomes the
custom-PID editor — it already exists), Power Saving, Logger Settings, Advanced
(IMU motion threshold only), System (OTA), About, File Viewer / Logs.
JS: `main.js`, `lucide_icons.js`.

### DITCH — components (delete dir + remove from `main/CMakeLists.txt` requires)
`mqtt`, `ws_server`, `vpn_manager`, `esp_wireguard`, `cert_manager`,
`ha_webhooks`, `https_client_mgr` (see Phase 2 decision), `debug_logs`,
`obd_logger` (+ its `sqlite3` dependency).

### DITCH — source files
`main/realdash.c` / `.h`, `main/gvret.c` / `.h`, `main/ftp.c` / `.h`,
and the orphan `main/ws_router.c` / `.h` (dead — **not** in `main/CMakeLists.txt`
srcs; the live router is `components/ws_server/ws_router.c`).

### DITCH — web tabs / sub-blocks (excise by **element id**, not by tab)
CAN Monitor, Terminal, MQTT Settings, VPN Settings, Dashboard; and the
Vehicle-Specific, Home Assistant, and Destinations **sub-sections** of the
Automate tab; the TLS Certificate Manager section of the System tab; the ELM327
UDP-debug toggle row of the Advanced tab.
JS: `ws_client.js`, `terminal.js`, `dashboard.js`, `dashboard_live.js`.

### DITCH — endpoints
`/store_canflt`, `/load_canflt` (MQTT filter rules — **NOT**
`/autopid/test_can_filter`, which is raw-CAN datalog authoring and stays),
`/api/webhook`, `/cert_manager/*`, `/api/destinations_stats`, `/vpn/*`,
`/load_car_config`, `/store_car_data`, `/upload/car_data.json`,
`/load_auto_pid_car_data`, `/obd_logs`, `/download_db`, `/obd_logger_ws`,
`/autopid_data`, `/ws`.

### DEFER — NOT in this branch (see §7)
Cloud auto-upload on sleep, live UDP telemetry, on-device-stored-ROM flashing
from the web UI. **DTC read/clear is NOT a gap** — NC Flash issues and manages it
over SLCAN; no on-device endpoint/UI is needed. (`SERVICE_03`/`SERVICE_04` in
`elm327.c` are dead constants, fine to leave.)

---

## 5. Dependency Landmines

1. **Master cut-list.** `main/CMakeLists.txt` `requires` currently lists
   `mqtt, obd_logger, https_client_mgr, debug_logs, cert_manager, vpn_manager,
   ha_webhooks, ws_server`. Remove each entry in the same commit as the dir, and
   drop `mqtt.c` from the explicit srcs list at the top.
2. **`autopid` (KEEP) depends on `mqtt.c` (DITCH).** `autopid.c` includes
   `mqtt.h` and calls `mqtt_publish()` at ~8 sites (~1167, 1172, 1494, 1512,
   3358, 3363) plus `config_server_get_mqtt_rx_topic()` / `mqtt_connected()` /
   `config_server_mqtt_en_config()`. Excise the `DEST_MQTT` **and** HTTPS/ABRP
   destination flavors from autopid **first**; the PID poll loop and the
   `csv_logger` column path are independent and stay.
3. **Transitive include.** `mqtt.h` `#include`s `elm327.h` (KEEP). After deleting
   `mqtt.h`, add a direct `#include "elm327.h"` to any KEEP unit that was getting
   `elm327` symbols transitively.
4. **`dev_status` event bits.** `DEV_MQTT_CONNECTED_BIT` shares the event group
   with `FLASH_ACTIVE_BIT` and `DEV_SDCARD_MOUNTED_BIT`. Leave the MQTT bit
   **defined-but-dead** — do **NOT** renumber the bitfield, or you silently shift
   `FLASH_ACTIVE_BIT` and break the flash↔datalog interlock. Grep all MQTT-bit
   sites; confirm `FLASH_ACTIVE_BIT`'s value is unchanged.
5. **CAN timing table stays.** `can.c` holds an 11-entry
   `twai_timing_config[CAN_5K..CAN_1000K]` array. "Single custom CAN config"
   applies to the **UI/preset layer only** — collapse the Settings dropdown to one
   row, but do **NOT** delete this driver table or you break bitrate selection for
   datalogging, raw-CAN, AND NC Flash.
6. **`cert_manager` removal order.** It is referenced by `mqtt.c`, `autopid.c`,
   and `config_server.c` (`cert_manager_init()` ~3976, `cert_manager_register_handlers()`
   ~4070/4098). Remove the MQTT + autopid consumers first, then delete the
   `config_server.c` init/registration lines, then the component. (`main.c` has
   only a commented log line — harmless.)
7. **`https_client_mgr` has TWO consumers.** `autopid.c` (cloud destinations —
   DITCH) **and** `config_server.c:790` `https_client_mgr_download_file()`, a
   "fetch a missing web asset from a URL" fallback. **Decision (recommended):**
   remove that fallback too — a car datalogger should serve only embedded/on-SD
   assets, never fetch UI from the internet — which lets the component be deleted
   cleanly. (A future feature branch will reintroduce a streaming-capable HTTP
   client; mine this component from git history then.)
8. **Two `ws_router` files.** Edit/delete `components/ws_server/ws_router.c` (the
   live one `config_server.c` calls). Separately delete `main/ws_router.c` as a
   zero-risk dead-file cleanup — it is not compiled. Don't waste a pass on it.
9. **Double-sourced HTML.** `/` serves the **minified** `main/web/src/homepage.html`,
   not the readable `main/web/homepage_full.html`. Editing the readable file alone
   has **zero runtime effect**. The minify/regen step is not yet owned — **Phase 0
   must establish it** before any UI edit.
10. **DOM co-mingling.** KEEP and DITCH share parent nodes: `wifi_settings` holds
    KEEP CAN rows + the DITCH `mqtt_en_div` + the Protocol dropdown; Automate holds
    KEEP custom-PID rows + DITCH vehicle/HA/destinations. **Excise by element id**,
    and prune the matching `main.js` handlers as each DOM block goes.
11. **Shared endpoints, partial prune.** `/load_auto_pid` + `/store_auto_data`
    carry BOTH KEEP custom-PID data AND DITCH vehicle/destination keys — prune the
    keys, keep the endpoints. `/scan_available_pids` surfaces KEEP PID discovery +
    DITCH vehicle scan — keep the PID-discovery core.
12. **Protocol-enum cleanup spans 4 files.** `main.c`, `comm_server.c`,
    the `config_server` protocol selector, and `smartconnect` auto-select. Keep
    SLCAN / OBD / ELM327 / AUTO_PID / FAST_LOG / POLL_LOG; ditch REALDASH /
    SAVVYCAN. **Also** remove the `realdash.h` include + RealDash branch inside
    KEEP `sleep_mode.c`. Assign one owner to this enum cleanup.

---

## 6. Execution Phases

### Phase 0 — Foundation & Safety Net (no feature change)

**Goal:** make UI edits real and make regressions one-phase-wide.

1. **Own the web build pipeline.** Locate or create the
   `homepage_full.html → src/homepage.html` minify/regen step; make it one
   command. Verify by making a trivial visible edit, regenerating, flashing, and
   seeing it at `/`. *(Landmine #9.)*
2. **Codify the §3 regression checklist** as a written runbook.
3. **Tag the baseline** (`git tag pre-trim-baseline`) and confirm OTA rollback
   works (flash old → flash new → flash old).

**Acceptance:** §3 checklist green on S3 PRO; a UI edit provably reaches `/`;
rollback proven.

---

### Phase 1 — Cut the Independent Leaves (lowest risk)

No KEEP dependents. **One commit per item**, each: delete dir/files + edit
`main/CMakeLists.txt` requires + remove endpoint registrations + remove the
matching UI tab/JS. Run §3 after each.

1. `ha_webhooks` (+ `/api/webhook`, the autopid HA hook, the Automate HA block).
2. `vpn_manager` + `esp_wireguard` (+ `/vpn/*`, the VPN tab).
3. `ftp.c` / `.h`.
4. **Dashboard + `obd_logger`** — `dashboard.js`, `dashboard_live.js`, the
   Dashboard tab, the `sqlite3` dependency, and `/obd_logs` `/download_db`
   `/obd_logger_ws` `/autopid_data`.
5. `realdash.c` + `gvret.c` + the protocol-enum arms across the 4 files,
   including the `sleep_mode.c` RealDash branch. *(Landmine #12.)*
6. `debug_logs` (+ the Advanced-tab ELM327 UDP-debug toggle row).
7. Delete the orphan `main/ws_router.c` / `.h` (dead file). *(Landmine #8.)*

**Acceptance:** ~6 components + 3 source files gone; §3 fully green; recovery
intact; firmware noticeably smaller.

---

### Phase 2 — The Coupled Cut: MQTT and Dependents (strict order)

Do the sub-steps **in order**; build green + §3 datalog/CAN checks after each.

1. **Strip cloud destinations out of `autopid.c`** — remove the `DEST_MQTT`
   **and** HTTPS/ABRP flavors (the ~8 `mqtt_publish` sites + the
   `https_client_mgr` destination calls ~1633–1770). Keep the poll loop +
   csv_logger path. *(Landmines #2, #7.)*
2. **Remove MQTT from boot** (`main.c` mqtt init) and leave
   `DEV_MQTT_CONNECTED_BIT` defined-but-dead — **do not renumber**; verify
   `FLASH_ACTIVE_BIT` unchanged. *(Landmine #4.)*
3. **Fix transitive includes** — add direct `#include "elm327.h"` where needed.
   *(Landmine #3.)*
4. **Delete `mqtt.c` / `mqtt.h`** + remove `mqtt` from requires and `mqtt.c` from
   srcs. Grep: zero remaining `mqtt_` / `config_server_*mqtt*` refs.
5. **Remove the `config_server.c:790` lazy-asset-download fallback**, then delete
   `https_client_mgr` (dir + requires). *(Landmine #7.)*
6. **Delete `cert_manager`** — remove `cert_manager_init()` / `register_handlers`
   from `config_server.c` (~3976/4070/4098), then the component + requires.
   *(Landmine #6.)*
7. **Web:** remove the MQTT Settings tab (by element id); prune MQTT keys from
   `/store_config`, `/load_config`, `/load_auto_pid`, `/store_auto_data`; delete
   `/store_canflt` + `/load_canflt` (keep `/autopid/test_can_filter`).
   *(Landmines #10, #11.)*

**Acceptance:** datalogging still polls and writes CSV; CAN bitrate still applies;
NC Flash dry-run still works; no MQTT/cert/https_client refs remain; §3 green.

---

### Phase 3 — The Datalogger-First Console (UI consolidation)

Now that Phase 0 made UI edits real:

1. **Excise by element id** the Vehicle-Specific + Destinations sub-blocks from
   Automate; prune dead `main.js` handlers (`loadCarModels`,
   `fetchVehicleProfiles`, `addDestinationEntry`, `renderDestinations`, cert,
   vpn). Drop `/load_car_config`, `/store_car_data`, `/upload/car_data.json`,
   `/load_auto_pid_car_data`, `/api/destinations_stats`. *(Landmines #10, #11.)*
2. **Confirm the existing custom-PID editor survives** the payload-key pruning
   (the "Custom PIDs" / "New PID" / Store UI already exists — this is a verify,
   not a build).
3. **Remove CAN Monitor + Terminal tabs**, then delete `ws_server` +
   `ws_client.js` + `terminal.js` + `/ws` + the 3 `config_server` ws hooks
   (coupled cut — after the tabs are gone). *(Landmine #8.)*
4. **Trim the protocol dropdown** to slcan / elm327 / auto_pid.
5. **Remove the TLS Cert Manager section** from the System tab.
6. **Latest-logs-first:** in `csv_list_handler`
   (`components/csv_logger/csv_logger.c` ~848) add `st_mtime` to the output and
   **sort newest-first**; make Logger/Files the **default landing** surface.
   Optionally reshape toward the Field Console reference
   (`main/web/mockups/mockup-2-field-console.html`,
   `docs/mockups/field-console.png`).

**Acceptance:** only the intended tabs render; datalog console is the landing
page; logs sorted newest-first; downloads work; OTA + Recovery untouched; §3
green on **both** S3 PRO and C3.

---

## 7. Deferred — future feature branches (DO NOT build here)

These were discussed and intentionally postponed; each gets its own branch once
the trim above is merged and stable:

- **`feature/cloud-upload-on-sleep`** — on engine-off + known Wi-Fi, stream
  un-uploaded CSVs to S3 (presigned PUT preferred; on-device SigV4 fallback),
  then deep-sleep. Requires a new **streaming** upload path (the current
  `https_client_mgr` upload buffers the whole body in RAM — unusable on C3) and
  guardrails (timeout, voltage floor, delete-only-after-200, abort on restart).
- **`feature/live-udp-telemetry`** — a dedicated UDP sender (mined from the
  `debug_logs` pattern in git history) emitting decimated 10–20 Hz PID lines to
  native consumers, while SD stays the full-rate record. (Not browser-reachable.)
- **On-device-stored-ROM flashing from the web UI** — ROMs already stage on the
  **SD card** (`/sdcard/roms/`); the deferred piece is a web-UI trigger + moving
  host-side packaging/secrets on-device. High brick-risk; design carefully.

---

## 8. Working Agreement

- Branch: `feature/datalogger-trim`. Develop only here.
- One atomic commit per component/concern; **build green at every commit**.
- Run the §3 hardware checklist after every phase; do not advance on a red suite.
- Never touch the §1 guardrails or the NC Flash contract.
- Do **not** start §7 work in this branch.
