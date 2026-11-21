# A firmware file installed by upload is confirmed at its first start

Command: `.\test.ps1 uploadconfirm` (= `python tools\testbench\system\upload_confirm_bench.py --dut localhost:18081 --dut-ip <the DUT's address on the Pi's network> [--image build\wican-fw_obd_pro.bin] [--pi rpi001] [--psu auto] [--no-cut] [--no-sleep] [--sleep-lead-s 22]`; the stage opens the tunnel through rpi001 and uploads the build of this checkout).

Needs: rpi001 (the tunnel, and the upload runs from there), the simulator's supply (the power cut of U4, the parked voltage of U5), a DUT whose bootloader can fall back (`rollback.bootloader_rollback` true on `/api/fw_update`: every unit flashed over USB with version 6; a unit that came from 4.x over the air keeps its old bootloader and the bench says `UPLOAD CONFIRM SKIP`). Prints `UPLOAD CONFIRM PASS`, 8 checks, about 7 minutes with a 5 minute sleep delay, two restarts (the unit's own, into the uploaded image) and one power cut. It leaves the unit on the uploaded image, confirmed, the supply at the voltage it found.

The index row and the other stages: [../TESTING.md](../TESTING.md). Updates from the release list: [fwupdate.md](fwupdate.md). The OBD chip's own update: [chipguard.md](chipguard.md).

## What it covers

The web page's manual install sends the file to `POST /api/ota/upload`; the unit writes the other slot and restarts into it. On a bootloader that can fall back, the new image starts unconfirmed (`pending_verify`), and a reset before it is confirmed makes the bootloader start the previous image again.

The rule changed twice. Until 2026-11-18 an uploaded image stayed unconfirmed until the next planned restart. From then until 2026-11-21 it was confirmed once its run had settled (10 minutes). GitHub issue #1075 (Ali, 2026-11-21: "remove the rollback feature or confirm directly after boot before chip bring up", "disable device sleep while the esp32 or the OBD chip update is ongoing"): a power cut inside those minutes brought the previous firmware back, and one from 6.0.0-beta.5 or older then writes its older chip firmware over the OBD chip's and pulses the chip's reset line. Every new image is now confirmed at its first start, before the chip hears a word from it (`fw_update_confirm_start()`, called by the composition root right ahead of `obd_chip_start()`), and the sleep manager stays awake while an image is being received (`ota_manager_busy()`).

| Check | What is asserted |
|---|---|
| U1 | the unit takes the file and says it restarts into it |
| U2 | the new image runs from the other slot and reads `valid` (`pending_verify` false) at the first status read behind the upload |
| U3 | the log ring holds `the running image is new: confirmed before the OBD chip is brought up`, ahead of the chip's first word (`chip awake`, `chip not ready` or `started`) |
| U4 | the supply cut inside the image's first minute (`--no-cut` skips it): the unit comes back on the same slot, `valid`, with a power-on reset |
| U5 (three checks) | no sleep while an image is received (`--no-sleep` skips them): the supply goes 0.6 V under the unit's sleep voltage, the bench waits until the sleep countdown is 22 s from its end, then uploads again (40 to 50 s): the upload ends well, and the unit restarts into the image (the restart record reads `ota_apply`, the other slot runs, `valid`) |
| U6 | no unexpected reset, no E line |

U5 changes no setting: it runs on the unit's own sleep voltage and delay, and with the default 5 minutes it waits about that long. On a unit with sleep off it is skipped with a line.

## Records

**2026-11-21 15:08, the firmware before the change (`v6.0.0p-beta.12-2-g6f0c3e4-dirty`, image `c02b4ece...`, run by hand with `--image`): `UPLOAD CONFIRM FAIL`.** U1 green. U2 red: the new slot read `pending_verify` 8 s after the upload. U3 red: no confirm in the ring. U4 red: a power cut 15 s into the image's first start and the unit came back on the slot it had run before (`ota_1` to `ota_0`): the bootloader's fallback, as on 6.0.0-beta.12 the same day (`internal/issue_1075/README.md`, round 2). (U5 in that run was typed on the console over `/ws/cli`, which cannot work: the HTTP server is busy with the upload. The leg was rebuilt around the countdown.)

**2026-11-21 15:12 to 15:22, the same firmware, the sleep leg (`--no-cut`): `UPLOAD CONFIRM FAIL`, 4 of 7.** The countdown at 12.50 V read 22 s when the upload began. The console: `15:18:13.810 ota_manager: update session -> ota_1`, `15:18:27.400 sleep_manager: entering sleep (12.58 V)`, `15:18:28.203 W ota_manager: update session aborted`, `components down; napping`. The upload's `curl` hung for 247 s and returned nothing; with the supply back at 13.8 V the unit woke on the old slot (`reset=software planned=power_wake/sleep_mode`). The two U5 checks red: the device went to sleep 14 s into an upload and lost it.

**2026-11-21 15:30, the build with the change (`v6.0.0p-beta.12-3-g883d1d4-dirty`, image `691a4d86...`, the stage as it is): `UPLOAD CONFIRM PASS`, 8 checks, 426 s** (`test-reports/2026-11-21_1530_uploadconfirm.md`). U2: `ota_1` read `valid` at the first status read, 8 s after the upload. U3: the confirm in the ring ahead of the chip's first word. U4: a power cut 14 s into the first start, back on `ota_1`, `valid`, `poweron`. U5: the countdown read 21 s when the second upload began; the upload took 50 s and answered `ok`, the unit restarted into `ota_0` with `ota_apply`. U6: 0 unexpected resets, 0 E lines. On the console of a run by hand 30 minutes earlier (`internal/issue_1075/logs/console_guard1.log`): `(2835) ota_manager: running image ota_1 confirmed (was pending_verify)`, `(2836) fw_update: the running image is new: confirmed before the OBD chip is brought up, a power cut no longer brings the previous firmware back`, `(3138) obd_chip: chip awake; warm start (ATWS) instead of hardware reset`: the confirm 2.8 s into the first start and 300 ms ahead of the chip's first word.

### Before 2026-11-21 (the rule "confirmed once its run has settled", five checks, about 11 minutes)

**2026-11-18 11:55, the build before that fix (image `3e569870...`): `UPLOAD CONFIRM FAIL`, 4 of 5, 676 s.** The image still `pending_verify` after the run had settled; a planned restart afterwards made it `valid`. The same had been seen by hand on 6.0.0-beta.11 on 2026-11-16.

**2026-11-18 11:57 and 19:00, the builds with that fix (images `95b79c95...`, `f444c3eb...`): `UPLOAD CONFIRM PASS`, 5 checks, 671 s and 676 s.** `valid` on the same slot 15 s after the run had settled, the boot count unchanged.

## Traps

- The bench must find the running image confirmed at its start, or the slot it uploads to is the only good one: it stops with a message that says to restart the unit once.
- Uploading the image the unit already runs is fine: the upload always goes to the other slot.
- **Nothing can be asked of the unit over HTTP while it receives an upload** (the server is one task): no status read, no console line over `/ws/cli`. A leg that needs something to happen in the middle of an upload sets it up before (U5's countdown).
- **The simulator is the supply and the car.** U4's power cut is output off, 3 s, output on. (On the simulator firmware from before 2026-11-21 a program on this PC reset the simulator at every cut and its boot switched the output on again, which is what the record of 15:30 ran on; [chipguard.md](chipguard.md), Traps.) A simulator restart puts the set point back to its boot default; the bench sets the parked voltage for U5 itself, and sets the voltage it found again when U5 ends, whatever happened.
- U5 needs sleep on and a delay the stage can wait for (7 minutes at most). With a longer delay, shorten it for the run or pass `--no-sleep`.
- Only one stage on the tunnel's port (`localhost:18081`): a tunnel left open by hand makes the stage stop.
