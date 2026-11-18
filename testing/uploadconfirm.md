# A firmware file installed by upload is confirmed once it has settled

Command: `.\test.ps1 uploadconfirm` (= `python tools\testbench\system\upload_confirm_bench.py --dut localhost:18081 --dut-ip <the DUT's address on the Pi's network> [--image build\wican-fw_obd_pro.bin] [--pi rpi001]`; the stage opens the tunnel through rpi001 and uploads the build of this checkout).

Needs: rpi001 (the tunnel, and the upload runs from there), a DUT whose bootloader can fall back (`rollback.bootloader_rollback` true on `/api/fw_update`: every unit flashed over USB with version 6; a unit that came from 4.x over the air keeps its old bootloader and the bench says `UPLOAD CONFIRM SKIP`). Prints `UPLOAD CONFIRM PASS`, 5 checks, about 11 minutes, one restart (the unit's own, into the uploaded image). It leaves the unit on the uploaded image, confirmed.

The index row and the other stages: [../TESTING.md](../TESTING.md). Updates from the release list: [fwupdate.md](fwupdate.md).

## What it covers

The web page's manual install sends the file to `POST /api/ota/upload`; the unit writes the other slot and restarts into it. On a bootloader that can fall back, the new image starts unconfirmed (`pending_verify`), and a reset before it is confirmed makes the bootloader start the previous image again. An update from the release list is confirmed when its run has settled, 10 minutes without a crash. Until 2026-11-18 an uploaded file was not: it has no update record, and the milestone only looked at a record. It stayed unconfirmed until the next planned restart.

| Check | What is asserted |
|---|---|
| U1 | the unit takes the file and says it restarts into it |
| U2 | the new image runs from the other slot and starts unconfirmed (`ota_state` `pending_verify`) |
| U3 | the run settles (`brake.settled` on `/api/restart/history`), with nothing but one status read every 30 s meanwhile |
| U4 | 15 s after that the image is confirmed (`ota_state` `valid`, `pending_verify` false), still on the same slot, with no restart in between |
| U5 | no unexpected reset, no new E line |

The stage starts no console capture: opening the console port resets the unit, which is the very thing an unconfirmed image must not meet during the wait.

## Records

**2026-11-18 11:55, the build before the fix (image `3e569870...`, run by hand with `--image`): `UPLOAD CONFIRM FAIL`, 4 of 5, 676 s.** U1 to U3 and U5 green; U4 red: `ota_state` still `pending_verify` after the run had settled, the same slot, no restart. A planned restart afterwards made it `valid`. The same had been seen by hand on 6.0.0-beta.11 on 2026-11-16 (unconfirmed at an uptime of 11:42).

**2026-11-18 11:57, the build with the fix (image `95b79c95...`, the stage as it is): `UPLOAD CONFIRM PASS`, 5 checks, 671 s.** U2: the upload went to `ota_0`, which started `pending_verify`. U4: `valid` on the same slot 15 s after the run had settled, the boot count unchanged. U5: no unexpected reset, no new E line.

**2026-11-18 19:00, the final build of that day (image `f444c3eb...`, ELF `eb50e90bb22b268f`): `UPLOAD CONFIRM PASS`, 5 checks, 676 s.** The upload went to `ota_0`, which started `pending_verify`; `valid` on the same slot once the run had settled, the boot count unchanged (9), no unexpected reset, no new E line.

## Traps

- The bench must find the running image confirmed at its start, or the slot it uploads to is the only good one: it stops with a message that says to restart the unit once.
- Uploading the image the unit already runs is fine: the upload always goes to the other slot, so the slot changes and the new copy starts unconfirmed all the same.
- Nothing else may restart the unit during the ten minutes: no stage beside it, no settings submit, no console port opened.
