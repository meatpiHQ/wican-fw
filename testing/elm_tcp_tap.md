# Recording what an OBD app really sends: the ELM TCP tap (2026-09-23)

Moved from `TESTING.md` on 2026-10-31 (the section, whole). The index row and the other stages: [../TESTING.md](../TESTING.md).

`python tools\testbench\obd\elm_tcp_tap.py <wican-ip> [--port 35000] [--log tap.log] [--quiet] [--once]`
on any machine that sits on the same network as the phone and the WiCAN
(a laptop on the WiCAN's AP in the car; on the bench rpi001's `wtest1`
parked on the DUT AP, 192.168.0.11). Point the app (Car Scanner, Torque,
...) at THAT machine's address, same port. The tap relays every byte
unchanged (nothing is buffered or re-framed, so the app sees the WiCAN's
own framing) and logs one line per socket read in each direction with a
timestamp; when the app disconnects it prints `INIT` (the app's AT
sequence), `FIRST POLLS` and a `SUMMARY`: poll rate, RTT command-to-`>`
(p50/p95/max), first-read time, the data-line-to-prompt gap, how many
responses needed more than one read, **`pipelined_requests`** (a request
sent before the previous prompt arrived), `bad_responses` (`NO DATA`,
`STOPPED`, `?`), `stalls_gt150` and the app's own idle gaps > 1 s (the
autopid yield window is 10 s). `--analyse tap.log` re-prints the summary.
The recorded command sequence replays against any adapter with
`elm_latency_probe.py --replay tap.log` (AT commands included, played
once; `--n` repeats), so the same Car Scanner session can be run against a
WiCAN, a USB adapter (`--serial`) or a BLE dongle (`--ble ADDR`, bleak,
written 2026-09-23 and NOT yet verified on hardware). `--cmds
010C,010D,0105,0111` plays a dashboard cycle round robin and adds
`per_cmd` p50/p95 and `cycle_hz` (how often the first PID comes round =
the gauge refresh rate) to the summary.

**First recording, Car Scanner on Ali's phone, 2026-09-23** (phone on
the bench DUT's AP, tap on rpi001 `wtest1` 192.168.0.11, autopid + BLE
off, ECU simulator): `tools/testbench/obd/fixtures/carscanner_tap_2026-09-23.log`
(`--analyse` it, or `--replay` it). What the app does: `ATZ` then a
burst of bare CRs, `ATE0` x3, `STI` + `VTI` (probes for STN / vLinker
chips, the MIC answers `STN2120 v5.8.1` / `MIC3624 v2.3.22`), `ATD`,
`ATD0`, `ATH1`, **`ATSP0`** (auto protocol: the first `0100` spent 6.2 s
in `SEARCHING...`), `ATM0`, `ATS0`, `ATAT1`, `ATAL`, **`ATST96`** (600 ms
timeout), `ATDPN`, then hint-less `0100`/`0120`/`0140`/`22F500`/`0902`
/`0904`/`090A` (each hint-less request waits the full 600 ms for the
prompt, e.g. `0100` data at 7 ms, prompt at 625 ms), then the dashboard
loop **with the response-count hint**: `010C1`, `010D1`, `010C1`,
`010D1`, `01051`, ... Steady state over 123 s / 1878 polls: WiCAN round
trip (request → prompt) **p50 8.8 ms, p90 20, p95 29 ms**, 5 stalls
> 150 ms (one is the protocol search); **the app's own gap from prompt
to its next request p50 46.5 ms, p90 69, p95 78 ms**; 0 bad responses,
1 pipelined request (the CR burst), 0 idle gaps > 1 s. Net 15.3 req/s,
RPM refreshed ~5×/s on that 3-PID cycle. So the WiCAN spends ~9 ms per
request and the phone side ~47 ms; whether that is Car Scanner's
scheduling or the phone's WiFi power save needs the same recording
against a WiFi reference adapter (or the legacy firmware). The Pi's
`wtest1` dongle cannot emulate a dozing client (`iw set power_save`:
operation not supported); pings to the AP from it stayed p50 2.7 ms
while the phone polled, so the AP itself was not congested.

**Replay comparison, same recording, Pi as the client, same simulator
bus (2026-09-23; `elm_replay_compare.py` prints this table from the
fixtures `carscanner_replay_wican_tcp_2026-09-23.json` and
`carscanner_replay_vlinker_mc_ble_2026-09-23.json`):**

| command | recorded (phone → WiCAN) n / p50 / p95 | WiCAN TCP replay (Pi) | vLinker MC over BLE replay (Pi) |
|---|---|---|---|
| `010C1` | 742 / 8.5 / 21.3 ms | 742 / 5.4 / 11.8 ms | 742 / 31.7 / 59.6 ms |
| `010D1` | 741 / 8.8 / 22.3 ms | 741 / 5.3 / 12.4 ms | 741 / 31.9 / 60.1 ms |
| `01051` | 370 / 8.7 / 24.3 ms | 370 / 5.4 / 11.4 ms | 370 / 31.8 / 60.0 ms |
| `0100` (hint-less, ATST96) | 13 / 51.8 / 631 ms | 13 / 50.3 / 617 ms | 13 / 76.0 / 637 ms |

Whole sequence: WiCAN 80.8 req/s, vLinker 22.5 req/s, both 1 bad
(`22F500`, the simulator does not serve it) and the same 5 stalls (the
`ATSP0` search 6.8 s / 5.3 s and the 600 ms hint-less probes). Ali's
verdict on the phone the same day: Car Scanner "feels smoother" on the
vLinker over Bluetooth even though every request is ~6× slower there,
so the choppiness is not the adapter's response time; it is the
phone-side pacing/jitter on the WiFi path (the ~47 ms app gap and its
tail) versus Bluetooth. Next: record the same session via the tap on a
WiFi reference (legacy firmware or a vLinker WiFi model), and try the
AP-side knobs (beacon interval / DTIM) against a phone. BLE-leg lessons:
the vLinker MC is service `18F0`, write `2AF1`, notify `2AF0`, MTU 23
(20 B writes); a killed bleak session leaves BlueZ "Connected: yes" and
the next scan cannot find the dongle, `bluetoothctl disconnect <addr>`
first; a protocol search must not be abandoned early (the probe now
stretches its timeout to 20 s when it sees `SEARCHING`).

**BLE to BLE, same replay, the WiCAN's own ELM pipe (FFF2 write / FFF1
notify, bridge `br_ble_obd` = `ble` <-> `obd`, added to the bench DUT
2026-09-23; autopid off) vs the vLinker MC, both from rpi001's UB500:**

| adapter | hinted poll p50 / p95 | whole sequence | negotiated interval |
|---|---|---|---|
| WiCAN BLE, `conn_profile=ios` (default) | 111 / 151 ms (plain `010C 1` loop: 75 / 79 ms) | 8.4 req/s, 288 stalls > 150 ms | 37.5 ms (the WiCAN REQUESTS 20-40 ms after connect: btmon `Connection Parameter Update Request min 16 max 32`) |
| vLinker MC BLE | 31.8 / 60 ms | 22.5 req/s | 15 ms (it requests nothing; BlueZ's stored 7.5-15 ms) |
| WiCAN TCP (same replay) | 5.4 / 12 ms | 80.8 req/s | - |

Every BLE exchange costs whole connection events (write in one, the
notification in the next), so the round trip is quantised to the
interval: at 37.5 ms the WiCAN needs 2 events for a short answer and
3-4 with headers on, i.e. 2-3.5× the vLinker per request. Fixture
`carscanner_replay_wican_ble_ios_2026-09-23.json`. The knob is
`ble_manager.conn_profile` (`ios` = 20-40 ms, `android_fast` = 7.5-15 ms).

| WiCAN BLE, `conn_profile=android_fast` | **30.7 / 41.6 ms** (plain loop 31 / 43 ms) | 26.3 req/s, the same 5 stalls | 15 ms (requests 6-12 units, btmon confirmed) |

With `android_fast` the WiCAN's BLE pipe matches the vLinker at p50 and
beats it at p95 (41.6 vs 60 ms): the whole BLE gap was the connection
window the device asks for. Fixture
`carscanner_replay_wican_ble_android_fast_2026-09-23.json`;
`elm_replay_compare.py` with all four JSONs prints the side-by-side
table. Product question for Ali: make `android_fast` the default (or
request it when the central is not iOS), a 15 ms window costs battery
on the phone, which is why the iOS-friendly window was the default.
Bench note: after every BLE session the DUT's STA did NOT rejoin the
hotspot within 1-3 min (`sta_ble_handover` suspend/resume); use the DUT
AP path (`nmcli con up wican-bench-ap`) to reach it for settings.

**Born from round 2 of the "choppy RPM dial in Car Scanner over TCP"
report (2026-09-23, Ali's run had autopid AND BLE off).** Bench findings,
DUT on the ECU simulator, rpi001 as the client:

- The TCP bridge does not pack or buffer responses. Hinted `010C 1` is
  ONE read at p50 5.4 ms / p95 10.2 ms over the DUT's own AP (166 req/s);
  plain `010C` is TWO reads, the data line at ~6 ms and the `>` prompt
  ~30 ms later (p50 36.9 ms, stdev 1.2): the chip's ATAT1 multi-ECU
  wait, the same shape the vLinker FS showed on 2026-09-08 (16 / 48 ms).
- **BLE enabled + no station on the WiCAN AP makes the STA leg deaf for
  ~60 ms of every 100 ms** (`power_save: none` notwithstanding, WiFi/BLE
  coexistence). Witness: `ping -i 0.01 -c 400 <sta-ip>` from the Pi
  hotspot gives 4 fast replies then a 58/44/28/12 ms sawtooth; ELM
  requests alternate 38 / 63 ms. Clean with `ble_manager.enabled=false`
  (377/400 < 10 ms) or while any station is on the DUT AP (BLE stops);
  deaf again with BLE back on. Affects Car Scanner users who run the
  WiCAN as a station on a phone hotspot or home WiFi. Not fixed.
- **autopid's resume prelude clobbers a connected app**: after 10 s of
  app silence autopid resumes and re-sends its baseline plus the
  profile's `specific_init` (`ATSH7E4;ATST96;` on the Ioniq 5 profile);
  the app's next `010C 1` answers `NO DATA` after 617 ms (299/300) until
  the app re-sends `ATSH`/`ATST`: Car Scanner initialises once per
  connection. Deterministic: init + loop clean → sleep 13 s → 8/8 bad →
  `--init ATSH7E0,ATST32` → clean. Worse form: an app connecting while a
  poll cycle is in flight gets `STOPPED` for its `ATZ` (the reset is
  discarded, 5 ms), the yield lands later, and every request afterwards
  runs with autopid's header: 300/300 `NO DATA` after a "successful"
  init; a real `ATZ` (1.25 s, `ELM327 v2.3` banner) recovers. Not fixed.
  Set `autopid.enabled=false` (+ submit) before any app latency session
  that must not see this.
- Ruled out for Ali's conditions: the car topology (phone on the WiCAN
  AP, no home SSID, STA retrying/scanning on a fresh boot), 6000 hinted
  requests p50 5.9 / p95 12.7 / max 213 ms, 2 stalls > 150 ms in 240 s;
  task priorities (chip RX 12, bridge pump + socket 10, lwIP 18).
- Still open until a phone recording exists: what Car Scanner sends (hint
  or not, PIDs per cycle, pipelining, its timeouts) and the phone's own
  WiFi hop. Compare against a WiFi adapter or the legacy firmware on the
  same hardware; a BLE dongle only compares the app's command pattern.
