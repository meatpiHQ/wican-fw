# WiCAN J2534 PassThru driver (Windows)

`wican_j2534_setup_1.1.0.exe` installs the WiCAN SAE J2534-1 (04.04) PassThru
driver: a 32-bit `wican_j2534.dll` that lets Windows diagnostic and reflash
tools drive a vehicle's CAN bus through a WiCAN PRO, over USB or WiFi.

| | |
|---|---|
| Version | 1.1.0 (the DLL reports itself as `WiCAN J2534 1.1`) |
| SHA-256 | `908c2bff5a050ce12c3f30aba7d74499064ff145ab29d093a3bfebdc9c635ad4` |
| Requires | WiCAN PRO on firmware v6, Windows 10 or 11 |
| Protocols | CAN and ISO15765 (ISO-TP). Other J2534 protocols return `ERR_NOT_SUPPORTED`. |

The installer is not code-signed, so Windows SmartScreen may ask you to
confirm before it runs. The same file is attached to each firmware release.

## Install on the PC

1. Run the setup and accept the UAC prompt.
2. Pick how the PC reaches the WiCAN: USB cable in network mode
   (recommended, the WiCAN is `192.168.82.1`), USB cable in COM-port mode,
   the WiCAN's own WiFi hotspot (`192.168.80.1`), or a custom IP for a WiCAN
   on your network.
3. The DLL is copied to `C:\Program Files (x86)\WiCAN\` and the device is
   registered in both the 32-bit and the 64-bit registry view, so any J2534
   tool lists a device named "WiCAN".

Re-run the installer to change the IP later. Uninstall from Windows
"Apps > Installed apps"; that removes the DLL and the registry keys. The full
user manual (PDF) is installed next to the DLL.

## On the WiCAN

Open the J2534 page of the web UI (or `PUT /api/settings/j2534_server`) and
enable the server. It listens on TCP port 6809 and is off by default. Two
safety gates also default to off:

- "Allow ECU flashing" (`allow_reflash`): until enabled, the UDS
  memory-transfer services 0x34 to 0x37 are rejected, so a tool can diagnose
  but not write ECU firmware.
- "Expose on LAN" (`allow_lan`): until enabled, connections are accepted only
  on the WiCAN's own hotspot and the USB network link, because the transport
  is unauthenticated.

Status: `GET /api/j2534`. Details in `components/HTTP_API.md`.

## Source

The driver source and the installer script live in the `wican-j2534-driver`
repository (MeatPi internal). Build a new installer there with
`installer\build_installer.ps1 -Version x.y.z`, then replace the exe in this
folder and update the version and SHA-256 above. The repo's `.gitignore`
excludes `*.exe` everywhere except under `drivers/`.
