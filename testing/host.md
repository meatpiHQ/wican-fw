# Host unit suites (components/*/host_test, 526 tests across 44 components as of 2026-07-26

Command: `.\test.ps1 host [component]`

Needs: `ssh rpi001` reachable (the WSL form: gcc in WSL, the IDF tree on C:)

Moved from the `TESTING.md` test-kinds table on 2026-10-31 (the row's detail cell, whole, as paragraphs). The index row and the other stages: [../TESTING.md](../TESTING.md).

Host **unit** suites (`components/*/host_test`, 526 tests across 44 components as of 2026-07-26; the summary prints a per-suite and TOTAL count) on the bench Pi's IDF linux target, syncs sources automatically (the tree it syncs is printed as `components: <path>`: since 2026-10-29 the dev layout's live tree by default, `MEATPI_COMPONENTS_PATH` to override, and nothing found stops the run; before that an unset variable silently synced the stale `components/meatpi` clone, which is gone). Optional component name runs just that suite.

**Pi offline:** `bash tools/testbench/host_gcc_wsl.sh <vpn_manager|config_backup>` from WSL compiles that PURE suite with the system gcc + Unity under ASan/UBSan (no IDF build; 2026-10-16, issues #985 to #989: `vpn_manager` 11, `config_backup` 24; add a case per IDF-free suite)

**2026-11-10:** `.\test.ps1 host obd_chip` compiles the production request
engine, claim model and fan-out against a fake chip since this date
(`test_request.c`: the words on the wire). The reproduction on the driver
before the fail-closed `ATM0`: 26 tests, 2 failures, the two expected (the
switch went out behind an `ATM0` answered `?`, and behind one that timed
out; report `test-reports/2026-11-10_1222_host_obd_chip.md`). With the
change: 33 tests PASS (`test-reports/2026-11-10_1228_host_obd_chip.md`), and
the whole host run 1176 tests PASS in 554 s
(`test-reports/2026-11-10_1229_host.md`).

**2026-11-10, later:** the protocol save's read-back (`obd_chip`). The
reproduction on the save before the change: 34 tests, 1 failure, the expected
one (`test_save_lost_answer_is_not_written_twice`: the fake chip took `ATSP6`,
its answer was lost, the second call of the boot wrote again, `Expected 1 Was
2`; report `test-reports/2026-11-10_1348_host_obd_chip.md`). With the change:
40 tests PASS (`test-reports/2026-11-10_1353_host_obd_chip.md`), autopid 148
PASS after it (`2026-11-10_1413_host_autopid.md`).

**2026-11-11:** the claim model's EXCLUSIVE holder may claim again (the
bring-up task takes the claim before it opens its gate; `testing/obdbringup.md`,
the check-first leg). `test_claim_exclusive_holder_claims_again`: the holder is
let in a second time, another task and a request are refused meanwhile, one
release frees the chip and no holder is remembered. `obd_chip` 41 tests PASS
(`test-reports/2026-11-11_0906_host_obd_chip.md`).

**2026-11-11, afternoon:** the bus of a row (`autopid`, `test_bus.c`, 11 cases;
the device half is [autopid_bus_bench.md](autopid_bus_bench.md)). The config
words and the refused ones, the speed of a bus as a divisor of 500 kbit/s (and
the 8/7 form for 95.2), the chain to MS, single-wire and CH CAN with no preset,
link word or stored protocol in it and the slot defined before it is chosen,
what a row of another bus may carry (a bit rate word yes, a protocol, a slot
definition or a reset no), the config parse, the scheduler asking the rows of
a bus together (two changes of bus where the plain order makes five), an
excursion asking every row of the other buses once, early by a quarter period
at most, and coming home with two buses at max rate, the rest after three
`CAN ERROR` replies (30 s, doubled to ten minutes, ended by one frame that
gets through), and the class that keeps such rows out of the HS bus guard's
hands. `autopid` 163 tests PASS (`test-reports/2026-11-11_1427_host_autopid.md`).

**2026-11-11, later:** the words the OBD chip stores every time it takes them
(`autopid`, `test_store_words.c`, 5 cases). Which words they are (`STP 53`,
`vtp 2c3`, `ATLNKMS PB`, anywhere in a chain) and which RAM words only look
like them (`STPBR 250000`, `STPTO`, `STPO`, `STPC`); no chain keeps one behind
the sanitizing every chain of the tables goes through; the parser refuses a row
that is switched on, names the row, the word and the Bus field, and lets a
switched-off row keep the word; a file as an earlier build stored it is
repaired once (the two rows that were on switched off, the clean row left on,
the word still in the file, a second pass a no-op); the settings migration
takes the word out of the three type inits and of nothing else. Written with
the change, so there is no red run before it: the parser accepted such a row
until then (the device half shows that, `testing/autopid_bus_bench.md`).
`autopid` 168 tests PASS (`test-reports/2026-11-11_1523_host_autopid.md`).

**2026-11-11, evening:** the settings migration is gone again and its case
with it (Ali: "keep STP53 and simply not send it": a type's init keeps the
word, it is never sent, and the rows behind it wait). In its place
`test_store_a_type_init_with_one_holds_its_rows`: with the hold on the custom
type the custom row of the car's protocol is not enabled and the scheduler
never picks it in twelve picks, while the custom row on MS CAN, a J1939 group
and a standard row are picked; the hold on the standard type holds the
standard row and not the custom one; released, the row is back; a row that is
switched off stays off whatever the hold says. `autopid` 168 tests PASS
(`test-reports/2026-11-11_2215_host_autopid.md`).

**2026-11-12:** the pre-CAN protocols (GitHub issue #1045; the device half is
[kline_accept_bench.md](kline_accept_bench.md)). `obd_chip`, `test_kline.c`,
7 cases: a K-line ECU behind the fake chip, its late answers sent from a
task. A request that starts the bus (`BUS INIT: ` in the answer so far)
outlives the caller's 1.5 s and comes back with the reply; a request that
starts none is still cut at the caller's time; `BUS INIT: ...ERROR` opens no
session; the same protocol chosen again keeps a session and another one ends
it; a bus request behind more than 4.5 s of silence gets `ATPC` first (the
words on the wire: `ATPC`, the request) and one in time does not; `NO DATA`
in a live session closes nothing; the rule's two parsers on the chip's own
texts (`FB ERROR` and `FC RX TIMEOUT` are no data lines). `obd_chip` 48 tests
PASS (`test-reports/2026-11-12_0203_host_obd_chip.md`). `autopid`,
`test_precan.c`, 6 cases: the protocol table, the request header of each
protocol (at the end of its prelude, and behind an init that takes the chip
there from another protocol: the reproduction of that one is the device
bench's, where the first build read `NO DATA` under the CAN header), the
report's own replies and the bench's on all five protocols decoded by the
expression that reads them on CAN (the first assertions read one the CAN way
and get a wrong engine speed: the defect), two ECUs by address, the VIN from
five messages and from one, and what the protocol hint must leave alone.
`autopid` 174 tests PASS (`test-reports/2026-11-12_0142_host_autopid.md`).

**2026-11-10, evening:** what the chip may learn (`autopid`,
`ap_veh_chip_learns`: only a protocol a standard request answered on, never
the protocol of a car confirmed by its profile). 149 tests PASS
(`test-reports/2026-11-10_1702_host_autopid.md`); the reproduction is the
device bench's (`testing/profile_protocol_bench.md`, the rule has no host
harness around the detection job).

**2026-11-03:** `.\test.ps1 host microlink` PASS, 8 tests. Compiles the
production DISCO pong module with transport, RNG and NaCl test doubles:
same-transport replies, exact UDP source, frame encoding, invalid addresses,
crypto and send failures, and no cross-transport fallback. Report:
`test-reports/2026-11-03_1549_host_microlink.md`.

**2026-11-10, night:** two rules of that evening's autopid changes (`ap_veh_dpn_is_search`: the chip's current protocol is its search's result only while no row of the tables pinned one; `ap_chain_reply_hint`: what a chain tells the reply parser, carried from chain to chain). 151 tests PASS (`test-reports/2026-11-10_2214_host_autopid.md`); the reproductions are the device bench's (`testing/profile_protocol_bench.md`).

**2026-11-10, late:** the profile probe's chains before the bus guard (`autopid`, `test_guard_the_profile_probes_chains`: the prelude of the profile's protocol is judged like a chain, refused on a live bus at another bit rate and on an unreadable one, sent on the bus's own protocols, on a silent bus and for K-line / J1850; the sentence the job hands on). 152 tests PASS (`test-reports/2026-11-10_2308_host_autopid.md`); the reproduction is the device bench's (`testing/autobaud.md`, leg `c5`).

**2026-11-12 15:13, `.\test.ps1 host obd_chip`: PASS, 58 tests (10 new).** Issue 1023, the device killer: `test_settle.c`, the READY settle logic against the traces measured on two units (a wake, a wake with the chip's store to tidy, a reset's boot, a chip that was awake, a stuck one, a restless one), with the fault itself as its first test (300 ms behind the sleep pin, and anywhere inside the chip's own boot, the verdict must not be READY), and the verdict on a `VTVERS` answer (`ERR82` behind stray bytes, a NUL among them, is a damaged store and not a chip to recover by a rewrite). Report `test-reports/2026-11-12_1513_host_obd_chip.md`.

**2026-11-12 15:57, `.\test.ps1 host obd_chip`: PASS, 61 tests (3 new).** The rule of the chip's reset line (`obd_settle_pulse_allowed`, issue 1023): `test_pulse_only_for_a_chip_that_does_not_answer` (READY not ready for a full second, or a chip found silent with READY at rest; never a chip that reads ready and was not found silent, never a READY that moves), `test_pulse_never_allowed_inside_a_measured_boot` (every measured boot, a wake, a wake with the store to tidy, a reset's boot with and without the tidying, sampled every 2 ms and asked for both reasons: no pulse before the boot is over and READY has rested) and `test_pulse_a_chip_asleep_gets_it_after_a_full_second`. Report `test-reports/2026-11-12_1557_host_obd_chip.md`.

**2026-11-12 16:19, `.\test.ps1 host obd_chip`: PASS, 62 tests.** The reset line as the last resort (Ali: "should not be allowed unless as a last resort", "remove it from API"): the rule takes one condition more, the chip found silent at both UART speeds, so `test_pulse_only_as_the_last_resort` and `test_pulse_a_silent_chip_asleep_waits_a_full_second` replace the two tests of 15:57 that allowed a pulse on READY alone, and `test_reset_line_has_one_driver_and_two_last_resort_calls` is new: the suite's CMakeLists counts, in the sources the component builds, who names the reset pin (one file), where it is pulled low (one place), who names the pulse (the pins file, the bring-up, its own header), who includes that header (two files), how often the bring-up calls it (twice) and whether `obd_chip.h` offers a hardware reset (no). The run of 16:17 failed on that count because the Pi's copy of the tree still held `obd_chip_sweep.c` of an old branch; the count went from the folder's files to the component's source list. Report `test-reports/2026-11-12_1619_host_obd_chip.md`.

**2026-11-12 16:43 and 16:44, `.\test.ps1 host obd_chip`: PASS, 64 tests (2 new); `.\test.ps1 host sleep_manager`: PASS, 32 tests (6 new).** The OBD chip's device fault codes. obd_chip: `test_fault_a_silent_chip_is_latched_however_it_ended` (no fault for a chip that answered; `obd_chip_silent` with one of three details for a chip that took the last resort, also when it came back; a short buffer is cut, not overrun) and `test_fault_codes_and_details_fit_the_fault_store` (the store's 24 and 48 characters). sleep_manager, a second test file `test_chip_watch.c` for the pure `sleep_manager_chip.c`: asleep through 20000 naps flags nothing; awake at the first nap, before it ever slept, is put back without a fault; a wake after sleeping is `obd_chip_woke` once per wake; six re-sleeps in a row end in the give-up (`obd_chip_no_sleep`), with or without a sleep before; a nap asleep in between starts the count again; the codes fit the store. Reports `test-reports/2026-11-12_1643_host_obd_chip.md` and `test-reports/2026-11-12_1644_host_sleep_manager.md`.

**2026-11-12 21:38, `.\test.ps1 host dev_status_manager`: PASS, 14 tests (8 new).** The cap of a fault code's count (Ali: "cap recurring faults at a certain number, there is no point to keep incrementing"; `dev_status_fault_count.c`, pure, `DEV_STATUS_FAULT_COUNT_MAX` 10): `test_fault_first_occurrence_is_counted_and_written`, `test_fault_recurrence_is_written_once_per_boot` (more of it in the same boot counts in RAM and is not written), `test_fault_count_stops_at_the_cap`, `test_fault_that_recurs_every_boot_stops_writing_at_the_cap` (ten boots, ten writes, then a thousand boots and not one), `test_fault_one_short_of_the_cap_is_still_written` (the store reads the cap, not one short of it), `test_fault_count_of_an_earlier_build_is_kept` (a stored 137 is neither cut down nor written), `test_fault_capped_says_this_many_or_more`, `test_fault_record_layout_is_the_stored_one` (84 bytes: the store's format is untouched). Report `test-reports/2026-11-12_2138_host_dev_status_manager.md`.

**2026-11-12 22:16, `.\test.ps1 host obd_chip`: PASS, 65 tests (1 new).** `test_question_alone_is_the_loaders_answer`: the reset word answered with `?` and nothing else (line ends, blanks, the prompt and a stray NUL aside) is the mark of a chip in its download mode, which the bring-up now names in one W line; a banner, an `OK`, the damaged chip's error text behind a `?`, `STOPPED` and no answer at all are not. Report `test-reports/2026-11-12_2216_host_obd_chip.md`.

**2026-11-12 23:27 and 23:41, `.\test.ps1 host autopid`: PASS, 175 tests (1 new).** `test_precan_trouble_codes_in_several_messages` (`test_precan.c`, 7 cases now): the trouble codes of a pre-CAN car come three a message with no count, and the reply reader kept the first message only, so a car with more than three codes showed three (the bench, 2026-11-12). The bench's own lines: five and seven stored codes and four pending ones joined into what a CAN ECU sends (`43 05` and the codes) and read by the DTC codec; one message with an unused place; no code at all (`43 00`); behind `BUS INIT: ...OK` and with headers off; two ECUs with their messages mixed, each with its own list; a refusal behind a list left out; forty-five messages, of which a payload holds 63 codes and says so; J1850 VPW under its CRC; ISO 14230 with seven data bytes a message, and with ECU sim v2's one long message that carries a count. Without the fix the case is red (`Expected 12 Was 7`, 23:28). `test_precan_vin_in_five_messages` gained the ISO 14230 VIN in five messages of seven data bytes (no such ECU on the bench).

**2026-11-13 00:14, `.\test.ps1 host autopid`: PASS, 175 tests.** `test_precan.c` took the real cars' lines from GitHub issue #1045's thread: the 2005 Porsche Cayman S answers from address 0x11 (`48 6B 11 41 0C 0A 96 B1`, 677.5 rpm; `48 6B 11 41 05 7E 88`, 86 degrees), and with headers off its chip prints no checksum either (`41 0C 0A 9A`, `41 05 7E`). The ISO 14230 lines of the trouble code and VIN cases are what ECU sim v2 sends since its firmware of that night (seven data bytes a message); the one long message it sent before stays in the cases as the CAN way of answering.

**2026-11-13 14:41, `.\test.ps1 host autopid`: PASS, 181 tests (1 new).** `test_guard_a_fixed_nodes_unreadable_names_its_setting`: the sentence of a park on traffic that a CAN node of the unit's own, fixed at one bit rate, could not read (its bit rate and "set the CAN bit rate to Automatic", whole in the 176 bytes that keep it at 1000 kbit/s too; the listener's "not 250 or 500 kbit/s" unchanged; a chain's sentence; the same verdict). The first run of that build did not compile: the test files' `{ AP_BUS_LIVE, 500 }` initializers left the new field of `ap_bus_t` out, and the host build takes that warning for an error; they name their fields now.

**2026-11-13 13:30, `.\test.ps1 host autopid`: PASS, 180 tests (2 new).** `test_guard_a_picture_held_against_the_quiet` and `test_guard_who_was_heard_stays_heard_for_a_while`: what the quiet makes of a picture that may be the OBD chip's own doing (nothing heard = silent, frames the node read = "live" stands, noise = "live" turns into "unreadable" and "unreadable" stands; a picture that named the bus by itself is left alone), and how long noise heard with the chip quiet counts (ten seconds, started again by every hearing). The bench behind it: the stage `autobaud`, leg `c6`, and its record of that day.

**2026-11-13 10:04, `.\test.ps1 host autopid`: PASS, 178 tests (2 new).** `test_guard_sent_marks` and `test_guard_a_running_nodes_picture_may_be_the_chips_own`: where the OBD chip was let talk since the boot, and which pictures of a CAN node of the unit's own that runs may be the chip's own doing (frames at a bit rate the chip talked at, unreadable traffic once it talked on CAN; not frames at the other bit rate, which name the bus, and nothing before the chip talked). The bench behind it: the stage `precan`, legs `test` and `startup`.

**2026-11-13 01:13, `.\test.ps1 host autopid`: PASS, 176 tests (1 new).** `test_dialect_precan_candidates`: what first contact tries by itself behind the CAN candidates, SAE J1850 and the K-line in the detection's order when nobody else talks on the CAN pins, nothing when somebody does. `test_precan_protocol_table` holds the walk's new order (`21534`, SAE J1850 first: no start of the bus, 0.7 s to say nobody is there) and that every entry is a pre-CAN protocol. The rule that asks the bus (`ap_guard_others_on_can()`) is target code: `.\test.ps1 precan`.
