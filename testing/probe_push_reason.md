# The Home Assistant card says why a push failed: probe

Command: `cd tools/webui_preview && MEATPI_COMPONENTS_PATH=<live components tree> python make_preview.py --fresh && node probe_push_reason.mjs`

Needs: nothing but Node and the preview (no device). The index row and the other stages: [../TESTING.md](../TESTING.md). The device side of the same change: [pushfail.md](pushfail.md).

**What it covers** (prints `PUSH REASON PROBE PASS`, 35 checks). The "Webhook stats" row of the Home Assistant card (Automate > Home Assistant) over the mock's `/api/webhook`, with a failing unit laid over it by the probe (`__mockPreset.webhook`).

The story: a unit whose pushes all failed showed `Status: failed` and nothing else, and its `last_error` read `esp_err=ESP_ERR_HTTP_CONNECT` for every kind of failure. A field report of 2026-11-22 (a reverse proxy at home, Nabu Casa outside) took the device log and two rounds of questions to read "the far end closed the secure handshake" out of it. The unit now reports a reason word and a code per address that did not take the last push (`failures`), and the card says it. Ali asked for a mock-up first and then for the build (both 2026-11-22); the card follows his rule of 2026-11-12: simple and clean, extra information in a bubble.

- One address, the reported case: the three chips as they were, then one line, `Last failure: the server closed the connection during the secure handshake`, in the page's text colour (the chip carries the red), an info button in the line labelled for a screen reader, the URL line after it. The bubble: a title, what to check (an https address by IP where a reverse proxy or Nabu Casa needs the name; a proxy that does not let the device in), and at the end `For support: tls -0x7280, <time>.`.
- Two addresses: a line each, in the order tried, named in the words of the card's own fields (`Webhook URL`, `Failover URL`); each with its own bubble and its own code. A single failed address of two is named too.
- Every reason word the firmware sends (`dns`, `no_answer`, `tls_closed`, `tls_refused`, `not_https`, `cert_untrusted`, `cert`, `tls`, `no_reply`, `other`) has a line, a title and a text in the page's `PUSH_FAIL`; no two share a line; a line starts in lower case and has no full stop; no long dash anywhere in the words.
- A certificate the check refused (`cert`): one line, `the server's certificate was not accepted`, and the bubble names both things to check (another name, another authority): the unit cannot tell them apart.
- An answer that was not a 2xx: 400 (`Home Assistant answered 400`, the trusted_proxies advice), 502 and 504 (`the reverse proxy could not reach Home Assistant (502)`), any other (`the server answered 404`).
- A reason word this page does not know (a newer firmware): a line all the same (`the push did not go through`) and the unit's code in the bubble. An entry without a code has no dangling "For support".
- Status `ok` with an entry (the failover delivered, the normal state on the road): no line.
- `rejected` and `removed`: their own line as before, no failure line beside it, and that line in the UI's red. Until this change both named `var(--crit)`, a colour no theme defines, and came out in the plain text colour; the probe asserts `--danger` on both and that no style in the page names `--crit`.
- A unit that sends no `failures` at all (older firmware): the card as it was, no line, no script error. A delivering unit: the chips and the URL, no info button in the row.

## Records

- 2026-11-22, the first run with the change: `PUSH REASON PROBE PASS` (35 checks); `probe_automate.mjs` beside it 81 of 81.
- 2026-11-22, after the certificate reasons the unit cannot tell apart were folded into `cert` (the device bench had shown a certificate for another name and one under another root both as `-0x2700` without flags): `PUSH REASON PROBE PASS` (35 checks).
- 2026-11-22, on a device: the page leg of `.\test.ps1 pushfail` (Chrome through the tunnel, the bench unit registered at two failing addresses): the two lines, the bubble and the unit's own code in it. [pushfail.md](pushfail.md) has the record.

## Traps

- The card paints its stats once, when the page opens: a probe scene is a page load with its own preset, not a change of the mock under an open page.
- The bubble is appended to the body. Look for `.ibubble` in the document, not in the card. The probe opens a bubble, reads it and closes it with the same button before the next one.
- The times in the bubble are in the browser's locale: the probe matches the code and the comma after it, not the time.
- `__mockPreset.webhook` lies over the WHOLE answer of the mock's `/api/webhook`: `failures: undefined` in it takes the key away (the older unit).
