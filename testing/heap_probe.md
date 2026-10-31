# Internal-heap probe

Command: `python tools/testbench/system/heap_probe.py --dut localhost:18081 [--settle 150] [--only found,link_off] [--logdir DIR]`

Needs: DUT through rpi001 (the tunnel)

Moved from the `TESTING.md` test-kinds table on 2026-10-31 (the row's detail cell, whole, as paragraphs). The index row and the other stages: [../TESTING.md](../TESTING.md).

**Internal-heap probe** (a measurement, not a pass/fail bench; prints `HEAP PROBE DONE`, ~15 min, 5 planned restarts; 2026-10-13, `internal/TASK_internal_ram.md`): restarts the DUT as found (twice: the reading repeats to within 0.1 KB), with the ESPNetLink link off, with the USB host off on top, and restored, and reads `memory.internal` (free, min_free since boot, largest block) plus the task list 150 s after each boot, so a placement change can be measured on the same protocol before and after. Born when the log clock bench's floor read 35.8 KB: the bench unit had lost about 19 KB of idle headroom since 2026-10-05 (11.6 KB static, 8.8 KB of it config_backup's internal worker stack, the rest the dongle path), and the clock bench's two open log files take 10 KB more. A run that dies midway leaves the DUT on the configuration under test: put `espnetlink.enabled` and `usb_host_manager.enabled` back by hand (the first line of the output names the found values).

**The fix, measured the same evening** (`heap_probe_20261013_2146` before, `_2202` after): config_backup's copies worker on a PSRAM stack (the filesystem now runs every `/data` operation of a PSRAM-stack caller on its own internal-stack writer task, reads included) and eight small state structs moved: idle free 49.2 to 59.0 KB, floor 47.8 to 58.4 KB, largest block 28.7 to 30.7 KB; the enumerated dongle's 7.5 KB stays (TCBs, FreeRTOS objects, DMA buffers: nothing movable). The benches on the new build: `CONFIG BACKUP PASS` (floor 53.2 KB), `LOG CLOCK PASS` (46.4 KB), `VPN ROUTES PASS` (50.2 KB); the USB GPS bench not run (the receiver must replace the dongle on the connector).
