# The admin password, on the web server and on a WebSocket

Command: `.\test.ps1 httpauth` (= `python tools\testbench\wifi\http_auth_bench.py --dut localhost:18081 [--restore-only]`; the stage opens the tunnel through rpi001).

Needs: rpi001 (the tunnel). Prints `HTTP AUTH PASS`, 10 checks, about half a minute, two planned restarts of the DUT (the password on, the password off).

The index row and the other stages: [../TESTING.md](../TESTING.md). The feature: `http_server_manager`'s README in the components repo, "Admin password".

## What it covers

`http_server_manager.auth_enabled` ships off. The bench switches it on with a password of its own (`bench-auth-2026`, the constant `BENCH_PW` of the script), checks both sides, and switches it off again in its `finally`.

| Check | What is asserted |
|---|---|
| A1 | a request without the password is answered 401 with `WWW-Authenticate: Basic` (the browser's own prompt) |
| A2 | the same request with `Authorization: Basic`, any user name, is 200 |
| A3 | with the `wican_auth` cookie it is 200 |
| A4 | a wrong password is 401 |
| W1 | a WebSocket handshake on `/ws/obd` without the password is refused |
| W2 | a handshake with the password is accepted, and the session then stays a WebSocket: a carriage return and `ATI` sent as frames are answered with frames (the OBD chip's prompt and identification), everything the unit sends parses as frames, no `HTTP/1.` text anywhere in the stream |
| R1 to R3 | the password off again and the settings as found, only the two planned restarts, no new E line |

W2 is why the bench exists. The password check sits at one choke point, a trampoline in front of every handler, and the web server calls a WebSocket route's handler again for every data frame of an open session. A frame has no HTTP headers, so the check found no password and answered the frame with a complete HTTP 401, written into the WebSocket stream. With the password on, no WebSocket channel worked: the terminal, the CAN monitor, the live values and the log tail of the web page all run on one. Since 2026-11-18 the frames of a session that passed its handshake are not checked again.

## Records

**2026-11-18 11:17, the firmware before the fix (image `c7da4e55...`): `HTTP AUTH FAIL`, 9 of 10.** A1 to A4, W1 and the handshake of W2 green; W2's frames red: 3,610 bytes came back, none of them a frame, starting `HTTP/1.1 401 Unauthorized\r\nContent-Type: application/json`. Report `test-reports/2026-11-18_1117_httpauth.md`.

**2026-11-18 11:22, with the fix (image `b4037fdd...`, uncommitted): `HTTP AUTH PASS`, 10 checks, 27 s.** W2: two frames, 28 bytes, `STOPPED` with the prompt (AutoPID had the chip) and `ELM327 v2.3` with the prompt. Report `test-reports/2026-11-18_1122_httpauth.md`.

## Traps

- A run that dies between the two restarts leaves the password ON: the unit then answers 401 to every bench. The password is `bench-auth-2026` with any user name, and `python tools\testbench\wifi\http_auth_bench.py --dut <host:port> --restore-only` switches it off. The bench also does that by itself when it finds the unit locked at its start.
- The bench refuses to start when a password is already on and it is not its own: it cannot restore what it cannot read (`GET` returns `auth_password` as an empty string, and an empty string on `PUT` keeps the stored one).
- After a run the unit keeps the bench's password as its stored secret, switched off. That is the settings contract (an empty string keeps the secret); nothing asks for it while `auth_enabled` is false.
- W2 reads the OBD chip through `/ws/obd`, so it needs the factory bridge `br_obd` (OBD chip to `ws_obd`). With AutoPID on the first answer is `STOPPED`: the check only asks for frames, not for a particular answer.
