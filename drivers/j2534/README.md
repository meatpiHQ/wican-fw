# WiCAN J2534 PassThru driver (Windows)

`wican_j2534_setup_1.5.0.exe` installs the WiCAN SAE J2534-1 (04.04) PassThru
driver: a 32-bit `wican_j2534.dll` that lets Windows diagnostic and reflash
tools drive a vehicle's bus through a WiCAN PRO, over USB or WiFi.

| | |
|---|---|
| Version | 1.5.0 (the DLL reports itself as `WiCAN J2534 1.5`; 2026-10-27: `PassThruGetLastError` explains every device refusal in words; 1.4, the same day: `PassThruReadMsgs` waits on an event instead of a `Sleep(1)` poll, and the driver log is on by default at `C:\Users\Public\Documents\WiCAN\wican_j2534.log` with every message in full both ways, see below; 1.3: accepts the J1850 VPW protocol; 1.2: event-driven ACK wait, GET_CONFIG answers, requests serialized across threads) |
| SHA-256 | `61bdadbd9aad79be8a4779a8695592034203d57b6a39b393c04785ade435d3f4` |
| Requires | WiCAN PRO on firmware v6 (a build of 2026-10-20 or later for ISO15765 and J1850 VPW: see below), Windows 10 or 11 |
| Protocols | CAN, ISO15765 (ISO-TP) on the CAN controller, and J1850 VPW (GM Class 2) on the device's OBD chip. J1850 PWM, ISO9141 and ISO14230 return `ERR_NOT_SUPPORTED`. |

ISO15765 messages are passed to and from the device exactly as SAE J2534-1
lays them out: the 4-byte CAN id first, then the UDS payload, the pad flag
in each message's TxFlags. Firmware before 2026-10-20 expected the bare
payload, so every standard tool (udsoncan, the OEM suites) timed out on
its first request; update the firmware (the message layout is the
device's business, every driver version passes messages through
verbatim). Driver 1.2 adds the faster ACK wait and the debug log on top.

J1850 VPW (protocol id 1, GM Class 2) runs on the WiCAN's OBD chip, not the
CAN controller. A J1850 message is its 3-byte header (priority, target,
source) followed by the payload, placed whole in `PASSTHRU_MSG.Data` with
no CAN-id prefix; the DLL passes it through verbatim like every message.
WiCAN segments large transfers itself, so SPS/DPS-style programming works
as is. Limits: request/response only (no unsolicited
bus capture), and GM 4x programming speed (service `$A1`) is not
implemented, so programming runs at 10.4 kbit/s. Driver 1.3 only adds the
protocol id to the accept list.

Driver 1.4 (2026-10-27) changes two things a flashing tool feels. The read
side no longer polls: `PassThruReadMsgs` wakes on the message itself, where
the 1.2 and 1.3 poll slept one Windows timer period per wait (15.6 ms on a
PC where no application raised the timer resolution), which a tool that
acknowledges every block of a transfer paid at every block end. And the
driver log is on by default: `C:\Users\Public\Documents\WiCAN\wican_j2534.log`
(the Start Menu entry "WiCAN J2534 driver log" opens the folder) holds one
line per call with every byte sent to the ECU and received from it, the
device's timestamp on reads and every status, 16 MB then one rotation to
`.1`; send it with a problem report. `WICAN_J2534_LOG` or the registry
`LogFile` move it, the value `off` disables it. Driver 1.5 (the same day)
makes `PassThruGetLastError` say why the WiCAN refused a call ("the WiCAN
refused: ERR_INVALID_BAUDRATE (0x18): the CAN bus runs at another bit
rate ..."); until then a device status left "no error" behind and a tool's
dialog said nothing useful.

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
enable the server. It listens on TCP port 6809 and is off by default.
Enabling it is the consent: the WiCAN then forwards whatever the tool
sends, reads and reprogramming alike, like a cable (the J2534 page counts
the reprogramming services it saw). One exposure gate defaults to off:

- "Expose on LAN" (`allow_lan`): until enabled, connections are accepted only
  on the WiCAN's own hotspot and the USB network link, because the transport
  is unauthenticated.

Firmware before 2026-10-27 also had an "Allow ECU flashing" gate that
refused UDS 0x34 to 0x37 and, on raw CAN, ordinary frames of tools with
their own framing (`NotSupported (0x01)`, "no error"); on such firmware turn
that switch on for flashing work, or update.

Status: `GET /api/j2534` (its `transport` field says which link the
attached tool came in on). Details in `components/HTTP_API.md`.

J1850 VPW also needs the WiCAN's OBD chip ready; CONNECT returns
`ERR_FAILED` if it is not.

## The wire protocol and the other transports

The DLL, the bench scripts and phone apps all speak the same versioned
wire protocol, specified in one place:
`components/j2534_server/J2534_WIRE_PROTOCOL.md` (framing, every
message, status codes, byte-exact examples, transport bindings). Besides
TCP the WiCAN serves it over the USB COM port (CDC-ACM) and, since
2026-09-21, over **BLE** for mobile apps (stream channel FFF5/FFF6, see
`components/ble_manager/BLE_API.md`); one tool at a time across all of
them.

## Source

The driver source and the installer script live in the `wican-j2534-driver`
repository (MeatPi internal). Build a new installer there with
`installer\build_installer.ps1 -Version x.y.z`, then replace the exe in this
folder and update the version and SHA-256 above. The repo's `.gitignore`
excludes `*.exe` everywhere except under `drivers/`.
