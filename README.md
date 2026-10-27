<picture>
  <source media="(prefers-color-scheme: dark)" srcset=".github/images/meatpi-logo-dark.svg">
  <img src=".github/images/meatpi-logo.svg" alt="MeatPi Electronics" width="300">
</picture>

# WiCAN firmware

Open-source firmware for the MeatPi WiCAN family: OBD-II and CAN adapters with WiFi, Bluetooth LE and USB that bring vehicle data to Home Assistant, MQTT and the diagnostic apps you already use.

### [Documentation](https://docs.meatpi.com/) | [Supported vehicles](https://docs.meatpi.com/wican-pro/supported-vehicles/) | [Firmware releases](https://github.com/meatpiHQ/wican-fw/releases/) | [Discord](https://discord.com/invite/2hpHVDmyfw) | [Reddit](https://www.reddit.com/r/wican/)

### Order on [**meatpi.com**](https://www.meatpi.com), [**Mouser**](https://www.mouser.com/c/?m=MeatPi) or [**Crowd Supply**](https://www.crowdsupply.com/meatpi-electronics)

---

## WiCAN PRO

![WiCAN PRO](https://github.com/user-attachments/assets/ed94e7b2-3d8a-4b1c-a759-c1e7a28d4a67)

WiCAN PRO plugs into the OBD-II port and reads your car without an app: pick a vehicle profile, or add your own PIDs, and the values arrive in Home Assistant (auto-discovered over MQTT), on any MQTT broker, or at an HTTP, HTTPS or ABRP endpoint. Setup is a web page from any phone or laptop, firmware updates are over the air.

- **Every OBD-II protocol**, thanks to a dedicated OBD chip: CAN, MS-CAN, single-wire CAN, J1850 PWM and VPW, ISO 9141-2, ISO 14230, plus J1939 and ISO 27145 (WWH-OBD) for trucks.
- **Works with the apps you know**: ELM327 v2.3 over WiFi and Bluetooth LE for Car Scanner, RealDash and friends, CAN over TCP for SavvyCAN, slcan, and a J2534 PassThru driver for Windows diagnostic tools.
- **A USB port for accessories**: the [ESPNetLink](#espnetlink) LTE and GPS dongle, a USB GPS receiver or an Ethernet adapter.
- **Built for the long run**: WireGuard VPN, HTTPS and MQTTS, SD card logging, a CAN monitor, a UDS tool, scripts and automation rules, settings backup and restore.
- **Kind to the battery**: sleep mode under 1 mA with a configurable voltage threshold, wake on voltage or motion, and a low-battery alert.

We recommend WiCAN PRO over the original WiCAN: it reads more vehicles, and it is where new features land. The original WiCAN's memory limits what its firmware can grow into.

[Read the WiCAN PRO manual](https://docs.meatpi.com/wican-pro/)

## ESPNetLink

![ESPNetLink](https://github.com/user-attachments/assets/5314bd88-768f-4e8c-8eea-dd2ed4649283)

ESPNetLink is the LTE and GPS dongle for WiCAN PRO: internet over Cat M1 or NB-IoT wherever the car is, and a GPS position, with nothing to set up. On a computer it is a USB modem with its own web interface. Follow the project on [Crowd Supply](https://www.crowdsupply.com/meatpi-electronics/espnetlink) or [read the manual](https://docs.meatpi.com/espnetlink/).

## WiCAN OBD and WiCAN USB

![WiCAN OBD and WiCAN USB](https://github.com/meatpiHQ/wican-fw/assets/94690098/537b5062-cb8a-485f-9354-6c351d08aa49)

The original ESP32-C3 WiCAN, in two shapes:

- **WiCAN OBD** sits in the OBD-II port: an ELM327 adapter for Car Scanner and RealDash, a CAN interface for SavvyCAN and SocketCAN, and vehicle data for Home Assistant and MQTT, with a sleep mode and a battery alert.
- **WiCAN USB** has screw terminals for any CAN bus from 7.5 V to 36 V and a USB serial port: SocketCAN, SavvyCAN, BUSMASTER and RealDash over WiFi, Bluetooth or the cable.

Manuals: [WiCAN OBD](https://docs.meatpi.com/wican-obd/) | [WiCAN USB](https://docs.meatpi.com/wican-usb/)

## WiCAN PRO compared

| Feature | WiCAN PRO | WiCAN | OBDLink MX+ |
|---|:---:|:---:|:---:|
| **Hardware** | | | |
| USB host port for accessories | ✅ | ❌ | ❌ |
| Dual USB to serial | ✅ | ❌ | ❌ |
| Button | ✅ | ❌ | ✅ |
| SD card logging | ✅ | ❌ | ❌ |
| **Connectivity and software** | | | |
| Interface | WiFi / BLE / USB | WiFi / BLE | Bluetooth |
| WiCAN firmware | ✅ | ✅ | ❌ |
| WireGuard VPN | ✅ | ❌ | ❌ |
| HTTPS | ✅ | HTTP only | - |
| MQTTS (secure MQTT) | ✅ | MQTT only | - |
| Home Assistant through Nabu Casa | ✅ | ❌ | ❌ |
| Firmware updates with new features | Full | Limited | - |
| Improved web interface | ✅ | ❌ | - |
| Reverse engineering tools | ✅ | ❌ | - |
| RealDash CAN protocol (pins 6/14) | ✅ | ✅ | ❌ |
| slcan / SocketCAN (pins 6/14) | ✅ | ✅ | ❌ |
| **Protocols** | | | |
| Medium speed CAN, MS-CAN (pins 3/11) | ✅ | ❌ | ✅ |
| GM high speed J1962 (pins 12/13) | ✅ | ❌ | ✅ |
| Single wire CAN, GMLAN (pin 1) | ✅ | ❌ | ✅ |
| SAE J1850 PWM | ✅ | ❌ | ✅ |
| SAE J1850 VPW | ✅ | ❌ | ✅ |
| ISO 9141-2 | ✅ | ❌ | ✅ |
| ISO 14230-4 slow and fast init | ✅ | ❌ | ✅ |
| **Instruction sets** | | | |
| ELM327 | v2.3 | Emulation v1.5 | v2.3 |
| ELM329 | v2.2 | ❌ | v2.2 |
| STN | ✅ | ❌ | ✅ |
| VT | ✅ | ❌ | ❌ |

---

## Firmware updates

Every release is on the [Releases](https://github.com/meatpiHQ/wican-fw/releases/) page. Install it from the device's web interface, or over USB if the device no longer boots. The manual for your device has the steps: [WiCAN PRO](https://docs.meatpi.com/wican-pro/firmware/update/), [WiCAN OBD](https://docs.meatpi.com/wican-obd/firmware/update/), [WiCAN USB](https://docs.meatpi.com/wican-usb/firmware/update/).

## Vehicle profiles

A vehicle profile tells WiCAN which PIDs to read for a given car and how to decode them. The profiles live in [vehicle_profiles/](vehicle_profiles/) and are merged into [vehicle_profiles.json](vehicle_profiles.json), which the device downloads. The [Supported vehicles](https://docs.meatpi.com/wican-pro/supported-vehicles/) page is generated from them.

To add or fix a car, open a pull request that changes the files under vehicle_profiles/. The format is described in [vehicle_profiles/README.md](vehicle_profiles/README.md), and a workflow validates the profile and attaches a test build of the merged file to the pull request.

## For developers

- HTTP API reference: [WiCAN PRO](https://docs.meatpi.com/wican-pro/reference/http-api/status/), [WiCAN OBD](https://docs.meatpi.com/wican-obd/reference/http-endpoints/), [WiCAN USB](https://docs.meatpi.com/wican-usb/reference/http-endpoints/)
- [MQTT messages](https://docs.meatpi.com/wican-obd/reference/mqtt-messages/) and [ELM327 commands](https://docs.meatpi.com/wican-obd/reference/elm327-commands/)
- [Programming examples](https://github.com/meatpiHQ/programming_examples/tree/master/CAN) in Python, C#, LabVIEW and more
- Pinouts: [WiCAN PRO](https://docs.meatpi.com/wican-pro/hardware/pinout/), [WiCAN OBD](https://docs.meatpi.com/wican-obd/hardware/pinout/), [WiCAN USB](https://docs.meatpi.com/wican-usb/hardware/pinout/)

## Support

Questions and show-and-tell go to [Discord](https://discord.com/invite/2hpHVDmyfw) and [Reddit](https://www.reddit.com/r/wican/). Bugs and feature requests go to [GitHub issues](https://github.com/meatpiHQ/wican-fw/issues), with the firmware version and the device model.

## License

The firmware is released under the [GNU GPL v3](LICENSE).

---

© 2026 MeatPi Electronics | www.meatpi.com | PO Box 5005 Clayton, VIC 3168, Australia
