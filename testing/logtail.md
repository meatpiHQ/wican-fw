# The TCP log tail and a client whose link stalls

Command: `.\test.ps1 logtail` (= `python tools\testbench\system\log_tail_stall_bench.py --dut localhost:18081 --tail localhost:18515 --dut-ip <the unit as the Pi sees it> --pi rpi001 [--gap-ms 2] [--short-ms 2000] [--long-ms 8000]`; the stage opens the tunnel through rpi001 for the web API and for TCP 5515).

Needs: rpi001 with `sudo -n`, `tc` (netem) and `nft` (the stage SKIPs without them); nothing on the CAN bus. Prints `LOG TAIL STALL PASS`, 11 checks, about 3 min, two planned restarts of the DUT (the log tail on, and the settings as found).

The index row and the other stages: [../TESTING.md](../TESTING.md). The four log sinks and their conservation: [logsinks.md](logsinks.md). The same policy on the TCP servers and the WebSocket channels: [slcan.md](slcan.md), legs S and W.

## What it answers

Whether a client of the log tail (`nc <unit> 5515`) keeps its connection and its lines when its link stalls for a moment, whether what it does lose is counted, and whether one client that is behind costs another client anything.

Until 2026-11-18 the sink wrote to its clients with a blocking send and closed a client whose send did not finish. lwIP holds 20 KB unacknowledged. Behind that the flusher stood in the send: the 200 ms limit it had set is only looked at when an ACK arrives or once a second, so it stood there for up to a second; the sink's ring of 8 KB ran over meanwhile; a second client and the UDP and WebSocket log sinks got nothing; and a stall of a few seconds closed the client, with a D line only. The sink now gives each client a backlog of 64 KB (the `tx_backlog` component, as the TCP servers and the WebSocket channels), drops the lines that no longer fit for that client alone and counts them, and closes only a client that took nothing for 10 s.

## What it covers

The generator is the unit's own `logsinks emit <n> <gap_ms>`: n lines, each ending in `LSBENCH i/n`. The client is `log_tail_stall_client.py` on the Pi; it holds its ACKs back (`tc netem`, mode `delay`) or drops them (`nft`, mode `ack`), for its own connection only (64 local ports of its own).

| Check | What is asserted |
|---|---|
| `G1 the tail port is closed while the sink is off` | as found, the sink ships off |
| `L1 no stall: the client holds and gets every line` | the control: no close, no line missing, `dropped` unchanged |
| `L2 ACKs late by 2000 ms: no close, no line lost, nothing dropped` | four stalls in 26 s; the last line number is the generator's last; the sink's `dropped` and the log pipeline's own `dropped` (`/api/logs/status`) did not move. The stall was in force: the client saw the unit silent for at least a sixth of it (`unit_silent_ms`; a unit whose ACKs do not come stops sending once its own 20 KB are full) |
| `L3 ACKs late by 8.0 s: the client stays` | two stalls longer than the backlog and lwIP's buffer hold at this rate; no close; the unit was silent for at least half of one |
| `L3 the lines it lost are counted (dropped) and said in the log` | the lines missing at the client are at most the sink's `dropped` plus the pipeline's, and not fewer by more than a fifth; the client read the unit's line `the tcp tail client caught up, N line(s) were dropped meanwhile` on the tail itself |
| `L4 a client that keeps up loses nothing beside one that is behind` | a second client on the PC, through the tunnel, gets every line while the first is stalled as in L3 |
| `L5 a client that takes nothing for 10 s is closed, with the reason in the log` | ACKs dropped for 15 s: the client sees the close, the unit logged `tcp tail client dropped (took no data for 10 s, ...)` once, and the console's `logsinks` reads `clients` 1 at second 13 of the run and 0 at second 18 |
| `L5 the next client receives again` | a new client gets every line |
| `R1` to `R3` | settings back as found (the tail port closed again), only the planned restarts, no new E line |

## What it does not cover

The UDP, WebSocket and file sinks (their bench is [logsinks.md](logsinks.md)). A generator faster than a line every 2 ms: at a line a millisecond the log pipeline itself, 64 lines deep, loses lines whenever a task of its priority is busy, with or without a client (it counts them: `dropped` on `/api/logs/status`).

## Records

**2026-11-18 13:34, bench unit 14c19f457fd9, the firmware before the fix (image elf `7bb648422357a1e2`), a line every 2 ms, the stalls at 1.5 s and 6 s as the bench had them that hour: `LOG TAIL STALL FAIL` (L2, L3, L4, L5).**

| Leg | Result |
|---|---|
| L1 no stall | 2,772 lines from the connect to line 3,000, none lost |
| L2 ACKs late by 1.5 s, four times | no close; 755 of 9,000 lines lost, the sink's `dropped` +755 (its ring ran over while the flusher stood in the send) |
| L3 ACKs late by 6 s, twice | the client was closed both times (`eof`); 1,700 lines lost, `dropped` +990 |
| L4 a client that keeps up beside it | lost 1,052 of 9,000 lines; the stalled client was closed twice |
| L5 ACKs dropped for 15 s | the client was closed, with no line in the log (the close is a D line) |

An earlier run that morning (12:12, a line every millisecond, stalls of 1 s and 4 s) showed the same: 2,837 lines lost in L2 with `dropped` +1,884, both 4 s stalls closed the client, the client that keeps up lost 3,508 of 18,000 lines.

**2026-11-18 14:26, the same unit, the final build of the day (image elf `2d790852dd98d8ae`), a line every 2 ms, stalls of 2 s and 8 s: `LOG TAIL STALL PASS`, 11 checks, 178 s.**

| Leg | Result |
|---|---|
| L1 no stall | 2,767 lines from the connect to line 3,000, none lost |
| L2 ACKs late by 2 s, four times | no close, none of 9,000 lines lost, `dropped` +0; the unit was silent for 1,378 ms of a stall |
| L3 ACKs late by 8 s, twice | no close; the unit was silent for 7,489 ms; 2,290 lines lost, `dropped` +2,291; the client read `caught up` once |
| L4 a client that keeps up beside it | 9,000 of 9,000 lines, none lost, while the stalled client lost 2,255 |
| L5 ACKs dropped for 15 s | `clients` 1 at second 13, 0 at second 18; the W line once; the client saw the close 22 s after its ACKs stopped (the unit's retransmissions had backed off) |

Two runs on the same build before that, at 14:00 and 14:14, gave the same numbers within a few lines (lost 2,286 with `dropped` +2,287 both times) and failed one check of the bench itself: it looked for the unit's `caught up` line in the log ring, which had rolled over, and then for the `is behind` line on the tail, which is one of the lines a client that is behind does not get.

**2026-11-18 18:22, the same unit, the merged build of that evening (image elf `29c018a9067b831d`): `LOG TAIL STALL PASS`, 11 checks, 244 s.**

| Leg | Result |
|---|---|
| L1 no stall | 3,000 of 3,000 lines, from line 1 (the client is connected before the generator starts now), the unit up 30 s at the start |
| L2 ACKs late by 2 s, four times | no close, none of 9,000 lines lost, `dropped` +0; the unit was silent for 1,383 ms of a stall |
| L3 ACKs late by 8 s, twice | no close; silent for 7,470 ms; 2,296 lines lost, `dropped` +2,298; `caught up` read once |
| L4 a client that keeps up beside it | 9,000 of 9,000 lines, none lost, while the stalled client lost 2,320 |
| L5 ACKs dropped for 15 s | `clients` 1 at second 13, 0 at second 18; the W line once; one close; the next client got its 2,000 lines |

Four runs before it that evening were red, each for a reason outside the sink. They are why the bench reads as it does now:

- 17:09 and 17:24 (image elf `df9dcea581095b8c`): L1 counted 0 lines, L5 found nothing waiting for its client. An ssh to the Pi took 7.4 s to begin (the PC's lookup of the Pi's name: 7.2 s, then 2.7 s, then nothing for ten seconds, in a cycle), and the bench started the generator first and the client 0.3 s later: the six seconds of lines were over before the client was there. The client starts first now and the generator when the client's `EVENT connect` line has arrived.
- 17:44: L1 lost 7 of 3,000 lines with the sink's `dropped` unchanged. The leg ran 20 s after the boot, where the unit refreshes its configuration copies (a flash write, `copies refreshed` in the log, 28 writes and 4 erases); put across that moment on purpose, an emit lost 25 of 3,000 lines, all of them in the log pipeline's own counter (`dropped` on `/api/logs/status` +25, the sink's +0), and none a minute later. L1 waits until the unit has been up 30 s.
- 17:44, L5: three connections ended 0.1 s after they were made. Both places of the tail were still held by the clients of the leg before (see the last trap); with the generator first, its lines had freed them before the client came. The bench frees the places before each leg.
- 17:59: L4's client that keeps up missed 25 of 9,000 lines. The console capture of that run misses the same 25 (lines 8,179 to 8,264 of the emit, 144.9 s after the boot, within 0.2 s): the log pipeline dropped them before any sink, at the moment the configuration copies are checked (20 s after the boot and every two minutes; no write that time). "No line lost" in L1, L2 and L4 is now: none beyond what the pipeline's counter moved by.

With a line every millisecond (13:10, an earlier build of the fix, stalls of 1 s and 4 s) the sink dropped nothing in L2 and 1,175 of 18,000 lines were lost all the same, every one of them in the log pipeline's own counter (`/api/logs/status` `dropped` +1,175): the pipeline is 64 lines deep and the flusher, which has the log task's priority, asked the stalled client again on every line. It now asks a stalled client every 5 ms and the listener every 100 ms; the generator of the bench is at a line every 2 ms, where the pipeline loses nothing with or without a stall.

## Traps

- `logsinks emit` holds the console for as long as it prints: the bench runs it on a `/ws/cli` connection of its own and reads the counters only between two legs.
- The stall is the Pi's: `tc netem` delays, `nft` drops, both only for the client's own source ports (40384 to 40447). A run that dies in the middle can leave the qdisc or the table behind; the bench removes both at its end whatever happened (`stall_clean`).
- With the ACKs dropped the unit's retransmissions back off, so the client sees the close several seconds after the 15 s are over. The run is 30 s for that reason. A client that sees the close connects again, so `clients` is read during the run, not after it.
- The unit says `a tcp tail client is behind` in its log at the moment it starts to drop lines for that client: that line is one of the dropped ones, the client never reads it. `caught up`, with the count, is the line it gets.
- Through an ssh tunnel a closed port still accepts the connection and closes it at once: `G1` reads to tell.
- The sink only keeps lines while a client is connected, and it learns that a client left when the next line cannot be sent: `clients` on the console can read one too many while the log is quiet. There are two places, so on a unit that logs nothing two clients that left keep the next one out (it is accepted and closed at once) until two lines have been logged. The bench emits three lines before a leg when `clients` is not 0 (`free_places`).
- An ssh to the Pi does not begin at once (7.4 s on the evening of 2026-11-18). Nothing in the bench is timed from the start of an ssh: the generator starts at the client's `EVENT connect`, L5's two looks at the sink are timed from it, and the first request waits up to 40 s for the tunnel.
- The log pipeline is 64 lines deep and loses lines of a generator at 500 lines a second whenever the unit is held for a tenth of a second: a flash write, and the check of the configuration copies every two minutes. Those lines reach no sink (the console misses them too) and are counted in `dropped` on `/api/logs/status`; the bench prints that number beside every leg.
