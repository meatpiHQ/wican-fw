# USB Host Manager Plan

## Goals

- Replace the temporary ESPNetLink USB host test block in `main.c` with a reusable `usb_host_manager` component.
- Keep USB host orchestration out of `main.c` and isolate device-specific behavior behind a manager-owned state machine.
- Use PSRAM-backed task stacks, queue storage, JSON caches, and other larger allocations when practical.
- Follow Allman style for all new component code.
- Keep files reasonably sized by splitting configuration, runtime logic, and HTTP handlers.
- Start with ESPNetLink support while leaving clear extension points for GPS, generic USB Ethernet, and future device classes.
- Keep LTE/USB Ethernet usable as a fallback uplink without overriding Wi-Fi STA when Wi-Fi is connected.
- Move uplink arbitration into a narrow `connection_manager` once both Wi-Fi and USB need shared policy.
- Treat the ESPNetLink CDC-ACM console as a first-class management sideband alongside RNDIS, not as a debug-only path.
- Support structured command/response CLI transactions so the host can query and configure LTE, GPS, AGNSS, and persistent modem settings.
- Add a dedicated web UI tab for USB Host management instead of leaving the feature HTTP-only.
- Keep the USB Host web UI device-aware so ESPNetLink ships first without hardcoding the page around one device class.

## Initial Scope

- Supported device type in this iteration: `espnetlink`
- Detection mechanism: monitor `USB_ID_PIN`
- ESPNetLink behavior:
  - enable USB ESP mode GPIO when a device is detected
  - start RNDIS via `usb_eth_host`
  - use DHCP by default and only prefer the USB route when Wi-Fi STA is unavailable
  - optionally start CDC-ACM CLI via `usb_acm_cli`
  - assert CDC DTR during session bootstrap so command output is visible and parseable
  - expose runtime status and configuration over HTTP
  - persist device configuration in LittleFS JSON

## Web UI Scope

- Add a new top-level `USB Host` tab to the main web UI.
- The tab should be backed by the existing `/usb_host/...` HTTP surface instead of duplicating transport logic in the browser.
- The page structure should separate:
  - manager-level status and enablement
  - attached device type and link state
  - device-specific configuration and telemetry panels
- The first shipped device panel should target `espnetlink` and expose:
  - manager/runtime status such as detection, CLI readiness, Ethernet link, IP info, and last CLI error
  - cached LTE JSON
  - cached GPS JSON
  - cached ESPNetLink config JSON
  - simple config set actions for keys like `APN`
- The UI contract must stay extensible for future device types, at minimum:
  - `gps`
  - `usb_ethernet`
  - additional composite devices later
- Device-specific UI should be selected by `active_device_type` and `configured_device_type` rather than by hardcoded tab duplication.
- If a device type is configured but not yet implemented in the UI, the tab should show manager status plus a clear placeholder panel instead of failing silently.
- The initial UI may use read-only panels for future device types, but the data model and layout should already reserve space for per-device settings and live status.

## ESPNetLink CLI Scope

- Treat the CDC-ACM function as a plain-text request/response console with prompt `esp> `.
- After CDC attach, bootstrap the session in this order:
  - wait for the prompt or a short settle delay
  - assert DTR
  - require prompt sync before issuing higher-level commands
- Command execution should recognize these response patterns:
  - prompt-prefixed command echo, for example `esp> config -l -j`
  - payload lines, which may be plain text or single-line JSON
  - successful completion terminator `OK`
  - prompt reappearance `esp> ` immediately after `OK`, signaling the CLI is ready for the next command
  - unsuccessful completion terminator `ERROR` followed by the prompt
- The host-side parser must tolerate commands that return:
  - human-readable status lines such as `gps -p`
  - single-line JSON such as `config -l -j`, `gps -p -j`, and `lte -j`
  - multi-line text blocks for future commands like `system -i`
- Initial command coverage should include:
  - `config -l -j`
  - `config get <key>`
  - `config set <key> <value>`
  - `gps -p`
  - `gps -p -j`
  - `lte -j`
- Command handling should be generic enough to add later support for:
  - `ver`
  - `system -v`
  - `system -i`
  - `agnss -s`
  - `ping <host>`

## Planned Files

- `components/usb_host_manager/include/usb_host_manager.h`
- `components/usb_host_manager/include/usb_host_manager_http.h`
- `components/usb_host_manager/usb_host_manager.c`
- `components/usb_host_manager/usb_host_manager_config.c`
- `components/usb_host_manager/usb_host_manager_http.c`
- `components/connection_manager/include/connection_manager.h`
- `components/connection_manager/connection_manager.c`
- `main/web/homepage_full.html`

## Checklist

- [x] Inspect existing USB host, VPN manager, config server, filesystem, and cmdline patterns
- [x] Add `usb_host_manager` component skeleton and public API
- [x] Add PSRAM-aware runtime state, task, and queue handling
- [x] Add LittleFS JSON load/save cache for USB host configuration
- [x] Implement ESPNetLink lifecycle management on top of `usb_eth_host` and `usb_acm_cli`
- [x] Implement USB ID pin monitoring task and state transitions
- [x] Add HTTP endpoints for USB host config and runtime status
- [x] Register USB host HTTP handlers from `config_server`
- [x] Replace the temporary ESPNetLink test block in `main.c`
- [x] Update related helpers such as filesystem config cleanup where needed
- [x] Build the firmware and fix compile issues
- [x] Add route arbitration so Wi-Fi STA remains the primary uplink and ESPNetLink LTE is fallback-only
- [x] Extract uplink arbitration into `connection_manager`
- [x] Add ESPNetLink CLI session bootstrap (`DTR` + prompt sync) on CDC attach
- [x] Add a prompt-aware CLI transaction helper with timeout, payload capture, and `OK`/`ERROR` termination
- [x] Add typed helpers for `config`, `gps`, and `lte` command families
- [x] Cache CLI-derived ESPNetLink status/config snapshots for HTTP and debug surfaces
- [x] Expose HTTP endpoints for CLI-backed queries and config updates where appropriate
- [x] Add a `USB Host` tab to the main web UI
- [x] Add manager-level USB Host status rendering in the web UI
- [x] Add an ESPNetLink panel for cached config, GPS, LTE, and config-set actions
- [x] Structure the UI so future `gps` and `usb_ethernet` device panels can be added without redesigning the page
- [x] Add placeholder/fallback UI for configured-but-not-yet-implemented device types
- [ ] Re-test CLI attach/detach behavior while Wi-Fi and LTE fallback are both exercised
- [ ] Re-test hot-plug and fallback behavior with Wi-Fi enabled and disabled

## Design Notes

- The manager owns policy and state. Existing components keep protocol-specific work:
  - `usb_eth_host` handles CherryUSB Ethernet drivers and netif glue.
  - `usb_acm_cli` handles CDC-ACM transport.
- The manager should be the only place that decides when to start or stop those subcomponents.
- `usb_acm_cli` should remain transport-focused. Higher-level ESPNetLink command orchestration, prompt parsing, retries, and response caching belong in `usb_host_manager`.
- Wi-Fi connection truth should continue to come from `wifi_mgr` via `dev_status` bits.
- USB host policy should consume `DEV_STA_CONNECTED_BIT` / `DEV_ETH_CONNECTED_BIT` rather than duplicating Wi-Fi state.
- `connection_manager` now owns uplink/default-route arbitration.
- `wifi_mgr` and `usb_host_manager` keep lifecycle ownership and only publish link state plus route preference inputs.
- HTTP surface should mirror the VPN manager pattern:
  - wildcard route registration from the central config server
  - GET config
  - POST config
  - GET status
- The web UI should mirror that separation and avoid one oversized device-specific view:
  - manager summary first
  - selected device panel second
  - reusable request helpers for `/usb_host/status`, `/usb_host/config`, and `/usb_host/espnetlink/...`
- Device-specific JSON must be shaped for future expansion instead of hardcoding a one-off ESPNetLink blob.
- The `USB Host` tab should treat device type as data:
  - shared container and navigation stay stable
  - device panels are swapped/rendered based on type
  - future GPS or USB Ethernet support should be an additive panel, not a rewrite of the entire page
- CLI integration should model a strict request/response state machine:
  - one in-flight command at a time
  - bounded line and payload buffers
  - explicit timeout/error mapping for missing prompt, missing `OK` or `ERROR` terminator, or malformed JSON
  - no dependence on shell-style interactivity beyond prompt detection and echoed commands
- CLI-derived state should be split into:
  - session state, such as prompt sync and CDC readiness
  - cached device configuration, such as APN and GPS passthrough
  - cached live telemetry, such as LTE JSON and GPS fix JSON

## Follow-Up After This Iteration

- Add runtime command endpoints for ESPNetLink CLI passthrough if the web UI needs interactive diagnostics.
- Add a richer USB Host web workflow once the base tab lands, such as refresh controls, config-set confirmations, and device-specific troubleshooting hints.
- Add descriptor-aware device identification so status can report actual VID, PID, and interface presence from the attached device.
- Add additional device profiles for GPS and generic USB Ethernet devices.
- Extend `connection_manager` only if more uplink candidates or policy rules appear; keep lifecycle ownership in the existing Wi-Fi and USB managers.
- Add command batching or coalescing only if polling pressure becomes visible; keep the first implementation strictly serialized.