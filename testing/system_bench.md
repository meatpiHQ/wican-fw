# The system bench (the composed main firmware, end to end)

Moved from `TESTING.md` on 2026-10-31 (the section, whole). The index row and the other stages: [../TESTING.md](../TESTING.md).

`tools/testbench/system/system_bench.py` (run ON rpi001, after `erase-flash` +
flashing the root `build/`): the real product journey, factory-AP
onboarding at `192.168.0.10` → configure wifi + a bridge over `/api` →
submit-reboot → STA join → OBD chip + live ECU through the configured TCP
bridge → OBD-polling perf → memory/fragmentation stability asserts.
Expected final line: `SYSTEM BENCH PASS` (first verified 2026-07-04).
Manual-only (it erases and reconfigures the DUT), not wired into test.ps1.

Other manual benches (run ON rpi001 unless noted): `ble_blast.py` (clean-radio
BLE TX ceiling, needs the `blast` CLI fired over UART from the PC side, see
its docstring for the choreography), `socket_bench.py`, `ble_perf.py`,
`obd_bench_check.py` / `obd_vt_isotp_check.py` /
`obd_monitor_injection_check.py` (PCAN + ECU sim bench, run from the PC).
`sleep_bench_test.py` (→ `SLEEP BENCH PASS`: run from the **PC**, the
OWON P4305 bench PSU (the FTDI `USB Serial Port`; its COM number drifts, COM50
since 2026-09-19, `*IDN?` answers `OWON,P4305`) walks the DUT's 12 V input across the
sleep/wake thresholds, proves light-sleep entry by supply current and
voltage wake via `/api/restart/history`; needs sleep_manager enabled +
the DUT on a bench hotspot; temporarily sets `sleep_delay_min=1` and
restores it; leaves the bench at 13.5 V, see `components/TESTBENCH.md`
§1 for the PSU and its 15 V ceiling).
`can_conservation_test.py` (→ `CAN CONSERVATION PASS`: run from the
**PC**, IDF venv python + PCAN on the DUT bus, exactly-N frame
accounting at three load tiers; the §7 performance-truth regression
net; first run verified 2026-07-21).
**Counter-delta rule (2026-07-22, from the ISR drain-loop postmortem):
a deterministic, unexplained counter delta FAILS the bench, never
park it as noise.** The 30x rx_count drain-loop bug had a stable ×432
fingerprint that was seen and parked; every conservation bench asserts
exact accounting (sent == rx + counted losses) for this reason. A
delta that repeats across runs has a cause; find it or fail.
`wifi_conservation_test.py` (→ `WIFI CONSERVATION PASS`: Pi iperf2
UDP client vs the DUT's fw iperf server over ws_cli, byte accounting
both sides, delta < 5%; verified 2026-07-21).
`usb_ncm_conservation_test.py` (→ `USB NCM CONSERVATION PASS`: PC
sends exactly-N UDP bytes over the CDC-NCM link to the DUT's fw iperf
server, byte accounting via `iperf -r` over ws_cli THROUGH the same
link; needs the software role-flip above; 0.001% delta verified
2026-07-21).
`dwc2_hammer_test.py` (→ `DWC2 HAMMER PASS`, `.\test.ps1 dwc2`: run
from the **PC** with the espnetlink dongle on the USB host port +
`usb_host_manager`/`usb_acm_cli` enabled, targeted stress of the
2026-07-17 `usbh_kill_urb`-vs-channel-IRQ crash class: ACM
mixed-timeout kill floods, NCM UDP OUT blasts, mid-flight `iperf -a`
teardown churn; asserts no re-enumeration/reboot/faults; first PASS
2026-07-22, 2 rounds 30/30).
`sleep_matrix_test.py` (→ `SLEEP MATRIX PASS`: run from the **PC**,
~1 h, sleep entry under load, one scenario per subsystem that can be
mid-flight when the voltage drops, incl. the 10-min WireGuard asleep
soak; scenario map + not-yet-automated rows in
`tools/testbench/sleep/SLEEP_MATRIX.md`; `--only key1,key2` runs a subset).
`ts_bench_target.py` (→ `TS TARGET PASS`: brings the LOCAL-ONLY headscale
up on the Pi, disposable preauth key kept Pi-side, DUT to
`type=tailscale`, asserts CONNECTED + tailnet IP + registration, and
(when the static tailscale build is present in /tmp) a real peer with
ICMP + HTTP through the tunnel; full teardown incl. headscale down.
Run with the DUT ip; needs sudo on the Pi).

**VPN bench ruling (meatpi 2026-07-21): the betty VPS rigs are the
CANONICAL VPN benches**, `wg_public_soak.sh` (self-verifying since
2026-07-21: handshake + ICMP-through-tunnel after `up`; split mode
keeps the AllowedIPs NETWORK address = the BUG_WG_NETIF_ADDR
regression gate) and `ts_public_soak.sh` (fresh headscale state each
run = the phantom-peers gate; full register/tunnel/peer legs; since
2026-10-31 also the DERP relay up over TLS, `/api/vpn` `derp`, and the
internal RAM flat over 60 s or over the soak: the two gates of issue
#1024, `.\test.ps1 tspublic` runs it as `run 3`). They
exercise the real internet path (NAT, public endpoint, keepalives;
set `WG_BENCH_DNS=<name>` to run the firmware DNS-endpoint path too).
The rpi001-local rigs above (`vpn_bench_target.py`,
`ts_bench_target.py`) are the OFFLINE FALLBACK when betty is
unreachable. The sleep matrix's `wg_betty_soak`/`ts_betty_soak` legs
build on the betty rigs.

Last run 2026-10-11 12:42, after the split-tunnel change (issue #975,
`routes` replacing `allowed_ip` + mask, the `ip4_routes` component): from
the PC `DUT_IP=10.42.1.194 bash tools/testbench/vpn/wg_public_soak.sh run
split 30` then `run full 30`, the DUT on the P4 bench AP: **`VPN SOAK
PASS`** in both modes (handshake + ICMP at the first check, 30 min each
with the server's handshake never older than 148 s and the counters
growing, four planned restarts and no unexpected one, no E line of the
tunnel components; under full mode the DUT's HTTP management over the LAN
stayed reachable, a directly connected network is never pulled into the
tunnel). The per-check lines and both run logs: `test-reports/
2026-10-11_1242_vpnsoak.md`. Two rig facts from that day: the Pi's
`eth-bench` profile is `never-default` on purpose, so the bench AP has NO
internet path by itself (the first attempt showed the DUT retrying `no
handshake within 20 s` with nothing arriving at the server); give the Pi a
host route to the server only for the run (`sudo ip route add <server
ip>/32 via 192.168.8.1 dev eth0`, deleted afterwards). And the loop's
sampler reads the server over one ssh per sample: a missed ssh prints a
nonsense age (`handshake 1791680966s ago  transfer ?`), which is the
sampler, not the tunnel (13 of 87 samples in the split run, 0 in the
full run). The MQTT `tcp_read error` lines a second before every planned
restart are the shutdown order (WiFi stops before the MQTT client), on
every bench console since September.

**2026-10-31, issue #1024 (Tailscale: the crash loop, the relay, the leak;
`test-reports/2026-10-31_1145_tailscale_1024_repro.md`).** The public rig
reproduced both field reports on the beta.6 dev build: `TS PUBLIC PASS` while
every DERP relay handshake failed (`TLS handshake failed: X509 - Requested OID
is unknown`, the relay's Ed25519 meta certificate on mbedTLS 4's TLS 1.3
parser) and every failed attempt leaked 21 KB PSRAM + 230 B internal; with
BLE on the device panicked at a task's first name lookup (the field backtrace
line for line, `ha_webhook`), three times, and parked. The fix (microlink's
X.509 wrap and session-ticket handling, the DERP cleanup and backoff, the
`lwip_guard` component, vpn_manager's 30 KB internal-RAM floor with its
`reason`) was proven on the same rig: **`.\test.ps1 tspublic` PASS** (302 s)
on the final image, with the two new legs `DERP relay connected (derp:true)`
and `internal RAM flat over the soak (31123 -> 31327 B)`; the floor held
Tailscale with BLE on (`not started: 13 KB of internal RAM free, Tailscale
needs 30 KB: turn Bluetooth off (about 40 KB), or use WireGuard`) and the
device stayed reachable; host suites `lwip_guard` 5/5 and `vpn_manager` 12/12.
Measured on the way: Tailscale takes about 20 KB of internal RAM (51.9 KB
settled with the VPN off, 33.1 KB at the connect, 31.5 KB steady), BLE about
40 KB; the two do not share this unit's budget. Rig lessons: the stage calls
Git for Windows' bash by path (a bare `bash` is WSL's, and the IDF portable
git on the runner's PATH has no bash); the stage needs betty and an internet
path for the DUT (the Pi NATs the bench AP, or the dongle's LTE).

**2026-10-31 evening, three more runs on the same rig.** On the instrumented
image (per-step internal-RAM lines in `vpn_ts_up()` and `microlink_start()`,
the free bytes on the relay's and the coordinator's milestone lines): PASS,
and the map of Tailscale's 22 KB (vpn_manager README "Where Tailscale's
internal RAM goes"). On build 1 (`CONFIG_BT_CTRL_RUN_IN_FLASH_ONLY`, the BLE
controller's code in flash, +19.4 KB of internal heap): PASS with Bluetooth
off (73.0 KB before the start, 51.5 KB steady) and **PASS with Bluetooth on
the Lean profile** (40.9 KB before the start, 20.7 KB at connected, 19.4 KB
with the relay, 18.6 KB steady with the peer, 15.2 KB at the lowest point,
0 E lines): the first time Bluetooth and Tailscale ran together on a WiCAN
PRO. The stage needs nothing new for that: Bluetooth on beforehand (lean,
legacy, 1m) and back off after.
