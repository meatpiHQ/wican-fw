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
