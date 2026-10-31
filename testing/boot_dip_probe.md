# The post-boot dip: where the internal heap's floor is set after the boot sequence

Command: `python tools/testbench/system/boot_dip_probe.py --dut localhost:18081 [--seconds 60] [--period 2] [--only found,usb_off] [--logdir DIR]`

Needs: DUT through rpi001 (the tunnel)

Index row and the other stages: [../TESTING.md](../TESTING.md).

**What it is.** A measurement, not a pass/fail bench (prints `BOOT DIP PROBE
DONE`, three planned restarts). The boot map's `WICAN RAMMIN <step>=<min>`
lines name the lowest internal-RAM point of the boot SEQUENCE, but the heap
probe's `min_free` (read 150 s after a boot) is set later, by whatever comes
up after `WICAN RAMMIN end`. The probe restarts the DUT and samples
`/api/status` memory.internal (free, min_free, largest block) and the task
list every 2 s over the first 60 to 70 s of the boot, prints a line whenever
`min_free` moved (with the tasks that came and went since the previous
sample), then the log ring's lines around the floor's moment, for two
configurations: the DUT as found (the dongle enumerating when it is on) and
with the USB host and the ESPNetLink link off; the found values are put
back by a third restart. A run that dies midway leaves the DUT on the
configuration under test: the first line of the output names the found
values.

**2026-10-31 23:23, on the committed image `a73e9ef` (the HTTP server on a
PSRAM stack, the flash worker at 6 KB), bench unit 68ee8f5a653d, station
mode, Bluetooth off, VPN off:** the floor is set at 27 s in both rows, by
the backup copies' first sweep (`config_backup: copies refreshed (flash +
card)` at 27.7 s and 27.4 s), the first PSRAM-stack caller of a boot, which
creates the flash worker (its 6 KB stack and TCB) and runs 31 jobs through
it. With the dongle: min_free 47,368 B at 10 s (the sequence's floor plus the
enumeration) to 41,704 B at 27 s (-5,664), free 77.6 KB at the floor, 84.2 KB
at 80 s, 53 tasks. Without the dongle: 55,040 B to 48,564 B (-6,476), free
84.4 KB at the floor, 90.9 KB at 79 s, 48 tasks. So the dongle's enumeration
costs about 7.7 KB of the settled floor before 10 s but is not the dip; the
dip is the worker's creation plus the sweep's own buffers, and it is the same
moment and size whether the dongle is on or off. Decisions taken from it
(`internal/TASK_httpd_psram_followups.md`): the worker's stack went from
6 KB to 4 KB (the battery of 2026-10-31 used 2.5 KB of it at most), which
takes 2 KB off the dip; the sweep's timing needs no change (it would dip the
same at any moment, and it is the device's first PSRAM-stack flash work
either way). Rows: `test-reports/logs/boot_dip_20261031_2323/` (one JSON per
configuration: every sample, the log lines of the first 75 s).

**2026-11-01 00:23, the same probe on the follow-ups image (the worker at 4 KB, the vehicle
writer's 6 KB stack on PSRAM, the Tailscale stacks on PSRAM but the VPN off here):** the
floor is still set at 25 s by the first sweep, and the probe now names the worker itself
(`came: flash_jobs` in the sample that set the floor). With the dongle: min_free 52,876 B
(the enumeration's own 116 B step at 10 s, `obd_chip_rx` arriving) to 49,712 B (-3,164),
free 84.5 KB at the floor, 89.7 KB at 78 s, 52 tasks; without the dongle: 60,764 to
56,556 B (-4,208), free 96.5 KB at 78 s, 48 tasks; the restored boot 90.1 KB free, min
57.3 KB. Against the night before: the dip shrank from 5.7 to 3.2 KB (dongle on) and from
6.5 to 4.2 KB (off), the floor rose 8.0 KB in both rows (the 6 KB `.bss` of the vehicle
writer and the worker's 2 KB). Rows: `test-reports/logs/boot_dip_20261101_0023/`.
