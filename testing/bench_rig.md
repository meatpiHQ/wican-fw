# Bench rig: control plane, recovery ladder, timeout policy (2026-07-26)

Moved from `TESTING.md` on 2026-10-31 (the section, whole). The index row and the other stages: [../TESTING.md](../TESTING.md).

The bench-Pi standardization plan (`docs/bench_pi_standardization_plan.md`)
is EXECUTED. What it changed:

- **Ethernet control plane.** ssh `rpi001` = **192.168.90.2** over the
  direct PC↔Pi cable (static both ends, PC = 192.168.90.1, NO gateway on
  either side: the link must never carry a default route). A dead radio
  can no longer take the bench offline: `rfkill block all` leaves ssh,
  sync, and `sudo reboot` fully working (verified). `rpi001-wifi`
  (192.168.0.83) is the explicit fallback alias.
- **Radio role names** (udev-pinned by MAC on the Pi): `wint0` = internal
  brcmfmac (DUT hotspot `wican-bench-w0`, pinned **10.42.0.1/24** → DUT =
  10.42.0.62 again, the lease flip-flop is dead), `wtest0` = USB stick #1
  (`wican-bench` twin, pinned 10.42.1.1/24), `wtest1` = USB stick #2
  (home-LAN uplink, NON-load-bearing). Old `dnsmasq-wlan{0,1}.leases`
  paths keep working via boot-created symlinks.
- **Recovery ladder** (all reachable over ethernet, all idempotent):
  `bench-health` (seconds; `RIG OK` / `RIG FAULT <comp>: <reason>`;
  truth-tests each active hotspot by **BSSID** from a second radio:
  nmcli "activated" lies), `bench-radio-reset <role> [--usb]` (profile
  bounce → usbreset re-enumeration), `bench-recover` (probe → targeted
  level 0 → usbreset → level-1 Pi reboot with a marker + 10-min
  reboot-loop guard), `bench-startup.service` (asserts canonical state at
  boot, logs `BENCH READY`), `wican-bench-watchdog` (beacon watchdog v2,
  primary = the internal radio). Sources live in `tools/testbench/pi/`;
  install with `deploy_bench_pi.sh`.
- **One bench library, one timeout policy: `tools/testbench/lib/benchlib.py`.**
  New scripts MUST use it; existing ones migrate opportunistically
  (system_bench is migrated). The constants (single source of truth):

  | Constant | Value | Rationale |
  |---|---|---|
  | `SSH_CONNECT` | 10 s + 1 retry | Windows OpenSSH cold-handshake flake |
  | `HTTP_TRY` | 8 s | one API call on a healthy link |
  | `HTTP_RETRY_BUDGET` | 30 s (3 s apart) | rides a route flap; longer = rig fault |
  | `DUT_REBOOT_WINDOW` | 180 s | healthy ≈ 20-40 s; beyond 180 s = rig fault |
  | `OTA_UPLOAD` | 300 s | 3.4 MB at worst-case RF |
  | `BLE_SCAN_TRIES/WINDOW` | 3 × 8 s | existing ble_bench practice |

  **The classification rule:** when a step exhausts its budget, benchlib
  runs `bench-health` BEFORE failing. `RIG FAULT` → `bench-recover`, retry
  once; still faulty → the run fails as **`RIG FAULT <detail>`** (e.g.
  `SYSTEM BENCH RIG FAULT`, exit 2): never blamed on firmware. `RIG OK` →
  the original DUT-side failure surfaces immediately. HTTP status errors
  (4xx/5xx) always surface immediately: they are answers, not transport
  faults.
