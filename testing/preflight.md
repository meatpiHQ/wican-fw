# The bench preflight (runs first, every kind)

Moved from `TESTING.md` on 2026-10-31 (the section, whole). The index row and the other stages: [../TESTING.md](../TESTING.md).

Every run (except `list`) starts with a ~10 s **bench preflight** that
verifies the PHYSICAL bench matches what the selected kind needs, fails fast
with a specific message when it doesn't, and records the findings in the
report's conditions. `-SkipBenchCheck` bypasses it.

What it detects: available COM ports (auto-falls-back `-Port` → COM10 → the
CH344 quad-UART's channel B → CH342 A; every fixture is matched by its PnP
NAME because COM numbers move on each re-plug - on 2026-09-19 the CH344 went
from COM12-15 to COM252-255 and the FTDI PSU port from COM2016/17 to COM50 -
and nothing is opened, since opening the WiCAN console resets the DUT),
whether a CH342 is enumerated (= the DUT's USB connector is cabled to the
PC), the DUT's address (hotspot lease / mDNS by device id), Pi reachability, DUT
online + firmware version, USB-Ethernet adapter presence (`/api/usb`),
mosquitto on :1883. It also prints the FTDI `USB Serial Port` (the OWON PSU
on this rig) and `MeatPi USB-CAN Device` (the ECU simulator's CDC console)
ports it sees.

**The port detector** behind it is `tools/testbench/detect_ports.py`. Run
it by hand after re-plugging anything: it prints a role table
(`wican_console`, `dongle_uart`, `psu`, `ecu_sim_cdc`, `ch342_console`,
`ch342_obd`, spares), `--json` for machines, `--role psu` for shell
substitution, `--probe-psu` to confirm the OWON by `*IDN?` (the only mode
that opens anything, and only FTDI ports), `--dut-ip` to add the WiCAN's
hotspot address via the Pi; every run refreshes the gitignored
`tools/testbench/bench_ports.json`. The manual benches default their
`--psu-port` / `--bridge` / `--elm-port` to `auto`, which asks the same
detector through `lib/bench_ports.py` (`resolve(value, role, default)`),
and the BenchBoard PSU instrument's `port` is `auto` too. No bench script
carries a COM number any more; a new fixture gets a role in the detector.

**The physical-setup matrix**: the DUT's USB-C connector can serve exactly
ONE role at a time, so some legs are mutually exclusive by wiring:

| Connector wiring | COM ports | Enables | Excludes |
|---|---|---|---|
| → PC cable (CH342 device role) | COM7 (A=console) + COM6 (B=`usb_obd` port) | usb_obd/port-B passthrough legs; flash via CH342 | `usbeth` (auto-SKIPs), espnetlink |
| → USB-Ethernet adapter (host) | CH342 gone | `usbeth` bench, eth-uplink testing | usb_obd/port-B legs |
| → espnetlink dongle (host) | CH342 gone | `espnetlink` bench (RNDIS LTE data + CDC-ACM `acm` console; `--prove-lte` isolation leg) | usbeth, usb_obd/port-B legs |

**The connector's DEVICE-side role is SOFTWARE-switched** (found
2026-07-21): with the PC cable attached, `usb_host_manager` settings
pick the mux -- `enabled:false` (default) = CH342 (COM6/7);
`enabled:true, role:"device", device_class:"ncm"` = the ESP's native
USB presents CDC-NCM to the PC (adapter at 192.168.82.2, DUT at
192.168.82.1; COM6/7 VANISH -- console-reset recovery is gone, WiFi +
PSU-cycle remain). Switching BACK to CH342 needs the settings change
**plus a real power cycle** (the mux GPIO only re-defaults at POR).
`usb_ncm_conservation_test.py` uses this flip.

The **UART0 serial path (the external adapter on COM10 when fitted, else the
CH344 quad-UART's channel B, PnP name `USB-Enhanced-SERIAL-B CH344`: 2 Mbaud
console, esptool at 460800 only; channel A is the dongle's now-unwired UART,
C/D spare)** works in EVERY wiring: target/hil/all always run through it.
Opening it resets the DUT, whatever DTR/RTS you preset. Legs whose
fixture is absent report **SKIP** (yellow), not FAIL: the run stays green and
the report says exactly why a leg didn't run.

**The bench CAN bus** (the vehicle side; written down 2026-10-03 for
`TASK_j1939_wwh.md` phase 7). Three nodes share one classic CAN bus: the DUT
(two transceivers on the same wires: the MIC3624 OBD chip and the ESP32-S3's
native TWAI controller, `can_manager`), the ECU simulator (a WiCAN-PRO at
USB-NCM 192.168.8.1: the OBD-II car, and the ACK source of every leg where
the DUT listens only), and the PCAN adapter (`PCAN_USBBUS2`, the PC's
python-can: the J1939 truck, the WWH-OBD ECUs, the reflash ECU, the flood
and watch instruments). Rules the benches live by: the simulator's CAN
controller cannot leave the bus (it survives a software restart and keeps
acknowledging at the bit rate it LAST ran at, `enabled` or not), so a leg
at another bit rate turns it on at that rate first and then off; a
listen-only DUT acknowledges nothing, so the simulator must be on the bus at
the test bit rate or nothing leaves the adapter; any node transmitting at
the wrong bit rate destroys the traffic (an ELM app pinned to 500k on a 250k
bus, the chip's pending 500k frames when a silent bus wakes up at 250k): the
actors are started BEFORE the DUT boots and talk `ATSP9` on a 250k bus. The
bus is terminated with 120 ohm at both ends; 250 kbit/s is
termination-sensitive on this hardware (the simulator's README), and the
250k legs of `autobaud`, `wwh` and `j1939` (line rate 1786 frames/s, exactly-N
conservation, zero bus errors at the adapter) are the evidence that this
rig's termination is right. A fourth node joins for some legs: the bench
AP (rpi001's) is NOT on the CAN bus; `obd0` / the ELM bridge reach the chip
over TCP, not CAN. **The simulator's REST on a LAN that shares its subnet**
(2026-10-04: the bench PC and rpi001 moved behind a router whose LAN is
192.168.8.0/24 with the router at 192.168.8.1, the simulator's own
address): Windows sends `http://192.168.8.1/` to the router (the wired
interface has the better metric) and a bench reads an admin page where it
expects JSON. `tools/testbench/lib/sim_net.py` binds every request to the
simulator to the PC's address on the simulator's USB link (192.168.8.2,
found by asking: the one behind which `/api/ecu/status` answers JSON);
`bench_bus.py` and `benchlib.py` install it, the stand-alone benches import
it, and it does nothing when no second interface shares the subnet. By
hand: `curl.exe --interface 192.168.8.2 http://192.168.8.1/api/ecu/status`
or `python tools/testbench/lib/sim_net.py`. No setting of the PC is
touched; the simulator's web UI is not reachable from a browser on that PC
while the LAN is plugged in.

A failing stage no longer aborts the run: every stage is recorded in the
report and the runner exits non-zero at the end if anything failed.

**A reset nobody asked for must explain itself** (2026-10-04, after the EU
truck bench's first run lost a device to one and the reason with it: the
panic handler prints on the console only, core dumps are off, and the
restart tracker keeps eight boots). Two nets: the PC benches read
`/api/restart/history` the moment `unexpected_resets` moves (`pcbench.Dut`:
every leg end and before every restart) and their `no_unexpected_reset`
check prints the reset reason and, since 2026-10-05, the tracker's **crash
note** of that boot: what crashed, the task, the backtrace, and the
backtrace as function names when the ELF of that very image is in `build/`
(`lib/crash_note.py`; by hand: `python tools/crash_decode.py --url
http://localhost:18081` through a tunnel, or `--file` on a saved reply). A
reset that went through the panic handler no longer needs a console capture
to be placed; one that did not (brown-out, power, a watchdog outside the
handler) still does. Since the evening of 2026-10-05 the newest distinct
crash is also kept in flash (`report` in the history, `GET
/api/restart/report`): after a power cycle it is all that is left of a
crash, and `python tools/crash_decode.py --report <file>` decodes the text.
**And three quick crashes in a row PARK the DUT**: it stops answering and
stays that way. How to recognise that, get the DUT back and read what
crashed is the next section, "When the DUT disappears in the middle of a
test". And `test.ps1` tails the DUT's console
beside every PC bench stage (`autobaud`, `eutruck`, `wwh`, the four `j1939`
stages; `tools/testbench/lib/console_tail.py`, the port opened with DTR and
RTS off, so the DUT is not reset) into the run's log folder as
`console_<stage>.log` and prints the crash lines at the stage's end when
there are any, and `THE DUT PARKED ITSELF` with the crash behind it when the
console said `WICAN PARK`. `-NoConsole` leaves the port alone. By hand: `python
tools/testbench/lib/console_tail.py COM254 out.log --secs 600`, and decode
a backtrace with `xtensa-esp32s3-elf-addr2line -pfiaC -e build\<the
newest>.elf <addresses>` (the build folder keeps older ELFs). It paid off
on 2026-10-05: the tail caught that reset's panic (`LoadProhibited` in
`twai_ll_get_tec`: a status read on a CAN node the link policy was
deleting) and the decoded backtrace named the race in one look (TASK phase
7 addendum, finding 28). After probing the OBD chip by hand on another bus
(test-a-PID one-shots), restart the DUT before a bench: the chip keeps its
session open and answers a bus at another bit rate with error frames.

**When the DUT disappears in the middle of a test** (2026-10-05). A bench
that loses the DUT says `the DUT did not come back within 150 s` or `GET
...: (0, ...)`. Since the crash-loop brake (`TASK_crash_loop_brake.md`) the
first suspect is a **park**: three runs in a row that crash (panic or any
watchdog) within ten minutes of their start, and the firmware goes to sleep
instead of starting a fourth time. Nothing answers on the network, waiting
does not help, and a reset over the console only costs the evidence. Two
readings tell the states apart, and neither touches the DUT:

- the supply: `python tools\testbench\lib\owon_psu.py` (setpoint, output,
  measured volts and milliamps; `... owon_psu.py cycle` is a power cycle);
- the console of the stage: `test-reports\logs\<run>\console_<stage>.log`
  (`test.ps1` tails it beside `autobaud`, `eutruck`, `wwh` and the `j1939`
  stages, and prints `THE DUT PARKED ITSELF` at the stage's end, with the
  crash behind it). A bench that holds the console itself (`crashnote`, the
  sleep bench and matrix, Quick Setup, the fresh-device and failover
  benches) prints the same notice when it closes the port, and the benches
  built on `lib/pcbench.py` name the park as the first suspect in their
  `did not come back` message (the two older ones with their own helpers,
  `autobaud` and `wwh`, run under the console tail).

| Supply at 13.5 V | Console | LED | The DUT is | Back with |
|---|---|---|---|---|
| 46 to 48 mA | last line `WICAN PARK streak=N parks=N retry_s=N bare=N`; before it `crash-loop brake: N runs in a row crashed ...`, and the crashes with their panic text | breathing red | **parked** | `owon_psu.py cycle`, or its button held until the LED turns sky blue |
| 43 to 48 mA | `sleep_manager: components down; napping (wake >= 13.20 V)` | dark | asleep (sleep mode, the battery guard, the critical floor). At the bench's 13.5 V it wakes by itself; if it stays down, the supply is under its wake voltage | the supply above the wake voltage |
| about 73 mA, steady | silent | as it was | in the ROM's download mode (a console reset that latched the boot strap) | `owon_psu.py cycle` |
| 150 to 170 mA | alive | normal | awake, off the bench AP (an association stall): `ap` on the bench_ap console | `ap kick <mac>`, `ap off` / `ap on`, or a restart over the console |
| 0 mA | silent | dark | without power: the output is off (or a cold OWON's first answer was wrong: ask twice) | the PSU's output |

**A parked DUT, step by step.**

1. **Read the console log before anything else.** It holds what a power
   cycle takes away: each crash with IDF's panic text, and after each one
   the tracker's lines of the next boot (`previous run crashed: ...`,
   `backtrace: ...`, `crash report stored`, `crash-loop brake: 2 of 3 quick
   crashes in a row`), then the park.
2. **Bring it back.** `python tools\testbench\lib\owon_psu.py cycle` empties
   the RAM (the history, the notes, the count): three new tries, and what is
   left of the crashes is the stored report. The button (hold until the LED
   turns sky blue, about 3 s, release) is a warm start: the history then
   still has the boot that parked (`mode: park`) and the note of every crash
   before it, but it is ONE try: a crash before the run has been up ten
   minutes parks it again at once.
3. **Read what crashed.** `GET /api/restart/report` is the crash that
   parked it, kept in flash (`Loop: N crashes in a row; the device parked
   itself`). `python tools\crash_decode.py --url http://localhost:18081`
   (through `ssh -N -L 18081:<dut>:80 rpi001`) turns its addresses into
   function names, and takes the ELF only when its id is the report's
   `Image:` line; `--report file.txt` does the same on the saved text.
   `/api/restart/history` shows the count as it stands (`brake`) and, after
   a warm start, every note.
4. **Whose crash was it?** A bench that crashes the DUT on purpose types
   `restart_tracker --settle` before each crash: that marks the run healthy,
   so the crash is a first crash and not one of a streak (`crashnote` stage
   1 and the sleep matrix's `bootloop_guard` do; `crashnote` stage 2 leaves
   it out on purpose). Three `restart_tracker --panic` in a row without it
   ARE a crash loop. If no bench asked for a crash, the firmware has one,
   and the report is the bug report.
5. **Leave the rig clean.** `DELETE /api/restart/report` (or
   `restart_tracker --clear-report`) once the report is saved; a power
   cycle if the unexpected-reset counter should read 0 for the next bench.

Nothing here can be asked of a parked DUT itself: it runs no network and no
console command. `restart_tracker --park-retry <s>` (typed BEFORE the
crashes) makes the next park end by itself after 10 to 3600 s, for a bench
that wants to watch one from the outside.

**Parking one on purpose** (to try this section, or a fix): restart the DUT
first, then type `restart_tracker --panic=fault` three times, each as soon
as the web page answers again. The restart matters: a run that has been up
ten minutes is healthy, its crash is one crash and does not count, so three
crashes typed on such a DUT stop one short of a park and leave it running
(the first rehearsal of this section, on a DUT 82 minutes up, did exactly
that).

**Rehearsed on the bench 2026-10-05**, every step with the tools named
here: the HTTP bench's message named the park (`GET /api/status: (0, 'timed
out') [no answer: if the supply reads about 47 mA ...]`), closing the
console printed `THE DUT PARKED ITSELF and is still parked` with `WICAN
PARK streak=3 parks=1 retry_s=0 bare=0` and the three `previous run
crashed` lines, `owon_psu.py` read 48 mA (46 to 52), `owon_psu.py cycle`
brought it back (136 mA two seconds later is the boot, not the steady
160), `GET /api/restart/report` had `Loop: 3 crashes in a row; the device
parked itself`, and `crash_decode.py --url` named `panic_fault <-
cmd_restart_tracker`. The crash note gate's own net was rehearsed too: a DUT
that parks under it is reported with its console lines, power-cycled, and
the gate waits for its boot.

**Canonical bench settings** (what the bench DUT persists so `live` + `perf`
pass end to end; verified green 2026-07-05): websocket channels `ws_obd` +
`ws_can` + `ws_cli` all enabled; bridges `br_obd = obd<->ws_obd` (real chip
over WS: ws_live + obd_poll), `br_cli = cli<->ws_cli`, `br_echo =
ws_can<->obd0` (the Pi-held echo path for the WS latency numbers); socket
`obd0` enabled; mqtt enabled against the Pi broker. The single-consumer rule
is why this exact split exists: `obd` can feed only one bridge, so USB
passthrough (`obd<->usb_obd`) or a BLE bridge (`ble<->obd0`) require
swapping a bridge out; put this set back afterwards. 2026-07-07: the
canonical set GREW, socket `slcan0:3333` + can_manager enabled.
autopid stays `backend=obd_chip` canonically.

Components with a `target` app today: `settings_manager`, `filesystem`,
`http_server_manager`, `wifi_manager`, `log_manager`, `dev_status_manager`,
`restart_tracker`, `obd_chip`, `api_http`, `socket_manager`,
`bridge_manager`, `ble_manager`. (`restart_tracker`, `log_manager` and
`api_http` are two-phase: they reboot the DUT mid-test (PSRAM `.noinit`
survival / the real settings submit-then-reboot cycle); `obd_chip` requires
the live OBD bench: chip + ECU simulator, see
`components/obd_chip/test_apps/README.md`; `socket_manager` and
`bridge_manager` are lwIP-loopback self-contained.)
