# NC Flash — WiCAN PRO Firmware

Custom firmware for the meatPi **WiCAN PRO** OBD-II adapter that turns it into the
wireless CAN endpoint for **NC Flash**, a ROM editor and ECU flasher for the
**Mazda MX-5 Miata (NC)**.

This is a fork of the upstream [meatpiHQ/wican-fw](https://github.com/meatpiHQ/wican-fw)
project. It keeps WiCAN's Wi-Fi/BLE CAN gateway, power-saving, and battery-alert
features, and adds an NC-specific wireless flash + datalog path that the NC Flash
desktop app drives over Wi-Fi.

> **Platform scope: Mazda MX-5 Miata NC only.** This firmware speaks the NC ECU's
> flash/diagnostic protocol and is not a general-purpose OBD tool. For general
> CAN/OBD use, run the upstream WiCAN firmware instead.

## Relation to NC Flash

```
NC Flash (desktop app)  ⇄  Wi-Fi  ⇄  WiCAN PRO (this firmware)  ⇄  OBD-II  ⇄  NC ECU
```

- The NC Flash desktop app connects to the adapter over your Wi-Fi network and uses
  it to **read, flash, and datalog** the NC ECU wirelessly — no wired flashing cable
  needed.
- The adapter also serves a **built-in web UI** (this firmware) for configuration,
  a live console, datalogging, and SD-card file management.

## Features

- **Wireless ECU flashing — SD-staged and brick-safe.** The ROM image is uploaded to
  the adapter's microSD (`/sdcard/roms`) and the firmware drives the flash locally, so
  a Wi-Fi drop mid-flash can't leave the ECU half-written.
- **No-reboot coexistence.** Flashing and datalogging share the single CAN bus safely:
  a dedicated **SLCAN** port lets NC Flash run UDS while the datalogger is active,
  without a protocol-switch reboot.
- **CSV datalogger + Field Console.** One-tap trip logging from a mobile-friendly web
  console; logs are written to microSD and downloadable from the Files tab.
- **Five selectable CAN modes:** Datalogger (`poll_log`), Passive Logger (`fast_log`),
  OBD App (`elm327`), `auto_pid`, and Bench SLCAN (`slcan`).
- **Built-in web UI** — status, settings, automation, power saving, logger, file
  manager, and OTA — responsive down to phones (hamburger navigation).
- **OTA firmware updates** from the web UI.
- **Battery-alert MQTT and deep-sleep power saving** (inherited from upstream WiCAN):
  sleeps below a configurable voltage threshold at under ~1 mA.

## Hardware

- **WiCAN PRO** OBD-II adapter — Espressif **ESP32-S3**, with a microSD slot.
- Plugs into the vehicle's OBD-II port. Also USB-powerable for bench work (see Notes).

## Usage

1. Power the adapter (OBD-II port in the car, or USB on the bench).
2. Join its Wi-Fi access point — or, if you've configured it for your home/shop Wi-Fi,
   reach it at its **station IP**.
3. Open the adapter's web UI in a browser to set Wi-Fi/CAN options, watch the console,
   datalog, or manage SD files.
4. In the **NC Flash** desktop app, select the WiCAN transport and connect over Wi-Fi
   to read / flash / datalog the ECU. (Detailed flash and datalog steps live in the
   NC Flash desktop app's documentation.)

## Notes

- The OBD-II adapter is **not** designed to be powered from the USB connector for
  normal use. USB can power the adapter to **flash custom firmware** or **hard-reset**
  the device, and is also useful for **debugging**.
- It is strongly recommended to **turn BLE off when it isn't in use** — leaving it on
  can degrade performance.
- **When BLE is connected, the configuration access point is disabled.** You won't be
  able to configure the device until you disconnect BLE (turn BLE off on your phone or
  other device).
- In **AP+Station** mode, use the **station IP** to communicate with the device; the
  **access point is for configuration only**.

## Build

Built with **ESP-IDF v5.x**, target `esp32s3`:

```sh
idf.py set-target esp32s3
idf.py build
```

Web UI changes: edit `main/web/homepage_full.html`, then regenerate the embedded bundle
with `python tools/build_web.py` (never hand-edit `main/web/src/homepage.html`).
Firmware version is derived from `git describe`, so only `v*` tags are valid.

## Credits & license

This project is a fork of **[meatpiHQ/wican-fw](https://github.com/meatpiHQ/wican-fw)**
by [meatPi Electronics](https://www.meatpi.com/) and remains licensed under the
**GNU GPL v3** (see [`LICENSE`](LICENSE)). The WiCAN hardware, upstream firmware,
pinouts, and original API documentation are meatPi's — refer to the
[upstream repository](https://github.com/meatpiHQ/wican-fw) for hardware details and
general CAN/OBD usage.
