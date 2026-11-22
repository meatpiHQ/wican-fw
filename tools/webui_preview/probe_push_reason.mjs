/* Probe (2026-11-22): the Home Assistant card says WHY a push failed.

   A field report of that day: a unit whose pushes all failed showed
   "Status: failed" and nothing else, and its `last_error` read
   `esp_err=ESP_ERR_HTTP_CONNECT` for every kind of failure; it took the device
   log and two rounds of questions to read "the far end closed the secure
   handshake" out of it. The unit now reports, per address that did not take
   the last push, a reason word and a code (`failures` of GET /api/webhook).
   The card says it in ONE line per failed address under the chips, and keeps
   the explanation and the code for support in the bubble beside it (Ali on
   the mock-up: simple and clean, extra information in a bubble).

   Covered, over the mock's /api/webhook with __mockPreset.webhook:
     - one address, the reported case: the line, the bubble, the code in it
     - two addresses: a line each, named in the words of the card's fields
     - every reason word the firmware sends has its own line and bubble
     - a certificate the check refused: one line for the two cases the unit
       cannot tell apart (another name, another authority)
     - an answer that was not a 2xx: 400 (the trusted_proxies advice), 502,
       any other status
     - a reason word this page does not know: a line all the same
     - status ok with an entry (the failover delivered): no line
     - rejected and removed: their own line, in the UI's red, no failure line
     - a unit that sends no `failures` at all (older firmware): the card as
       it was
   Prints `PUSH REASON PROBE PASS`. */
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import { JSDOM, VirtualConsole } from "jsdom";

const here = dirname(fileURLToPath(import.meta.url));
const html = readFileSync(join(here, "preview.html"), "utf-8");
let fails = 0;
const check = (n, ok, extra) => { console.log((ok ? "PASS " : "FAIL ") + n + (extra !== undefined ? "  [" + JSON.stringify(extra) + "]" : "")); if (!ok) { fails++; process.exitCode = 1; } };
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const URL1 = "http://192.168.1.20:8123/api/webhook/8f4a21be", URL2 = "https://ha.example.net/api/webhook/8f4a21be";
const T = "2026-11-22T12:04:10Z";
const FAILED = { status: "failed", success_count: 0, fail_count: 132, retries: 3, last_error: "esp_err=ESP_ERR_HTTP_CONNECT", last_error_time: T };
const CLOSED = { slot: 1, reason: "tls_closed", code: "tls -0x7280", time: T };
/* every word http_client_manager_fail_reason_str() and ha_webhooks send (but "http") */
const WORDS = ["dns", "no_answer", "tls_closed", "tls_refused", "not_https", "cert_untrusted", "cert", "tls", "no_reply", "other"];

async function scene(webhook) {
  const errs = [];
  const vc = new VirtualConsole();
  vc.on("jsdomError", (e) => errs.push(e.message + " " + (e.detail && e.detail.stack || "").slice(0, 300)));
  const dom = new JSDOM(html, {
    runScripts: "dangerously", url: "http://wican.local/#/automate/homeassistant",
    pretendToBeVisual: true, virtualConsole: vc,
    beforeParse(w) {
      w.__mockPreset = { webhook };
      w.matchMedia = () => ({ matches: false, addListener() {}, addEventListener() {} });
      w.scrollTo = () => {}; w.HTMLCanvasElement.prototype.getContext = () => null;
      if (!w.TextEncoder) { w.TextEncoder = TextEncoder; w.TextDecoder = TextDecoder; }
    },
  });
  await sleep(1500);
  const w = dom.window, d = w.document;
  const card = [...d.querySelectorAll(".section")].find((c) => /Home Assistant/.test((c.querySelector("h3") || {}).textContent || ""));
  const row = card && [...card.querySelectorAll(".frow")].find((r) => /Webhook stats/.test((r.querySelector("label") || {}).textContent || ""));
  const lines = row ? [...row.querySelectorAll("[data-pushfail]")] : [];
  const click = (el) => el && el.dispatchEvent(new w.MouseEvent("click", { bubbles: true, cancelable: true }));
  const bubble = (line) => {
    click(line.querySelector("button.ibtn"));
    const b = d.querySelector(".ibubble");
    const out = b ? { title: (b.querySelector("b") || {}).textContent || "", text: (b.querySelector("p") || {}).textContent || "" } : null;
    click(line.querySelector("button.ibtn"));
    return out;
  };
  return { w, d, card, row, lines, bubble, click, errs, text: row ? row.textContent : "", close: () => w.close() };
}

(async () => {
  /* ---- one address, the reported case ---- */
  let s = await scene({ ...FAILED, url: URL2, failures: [CLOSED] });
  check("the Webhook stats row is there", !!s.row);
  check("the chips are the three they were", /OK: 0/.test(s.text) && /Fail: 132/.test(s.text) && /Status: failed/.test(s.text), s.text.slice(0, 80));
  check("one line for the one failed address", s.lines.length === 1, s.lines.length);
  check("it reads `Last failure: the server closed the connection during the secure handshake`",
    s.lines.length === 1 && s.lines[0].textContent.trim() === "Last failure: the server closed the connection during the secure handshake", s.lines[0] && s.lines[0].textContent);
  check("the line is plain text in the page's text colour, the chip carries the red", s.lines.length === 1 && /color:\s*var\(--text\)/.test(s.lines[0].getAttribute("style") || ""), s.lines[0] && s.lines[0].getAttribute("style"));
  check("the URL line still follows it", !!s.row && s.row.textContent.indexOf("URL: " + URL2) > s.row.textContent.indexOf("Last failure"));
  const btn = s.lines[0] && s.lines[0].querySelector("button.ibtn");
  check("an info button sits in the line, labelled for a screen reader", !!btn && btn.getAttribute("aria-label") === "What this means");
  check("no bubble before a click", s.d.querySelectorAll(".ibubble").length === 0);
  let b = s.lines[0] && s.bubble(s.lines[0]);
  check("the bubble: a title and what to check", !!b && b.title === "The server closed the connection" && /hung up before the secure connection was set up/.test(b.text) && /reverse proxy or Nabu Casa needs the name/.test(b.text), b);
  check("the bubble ends with the code for support and the time", !!b && /For support: tls -0x7280, .+\.$/.test(b.text), b && b.text.slice(-60));
  check("no script error in the page", s.errs.length === 0, s.errs.slice(0, 2));
  s.close();

  /* ---- two addresses: a line each, named as the card's fields are ---- */
  s = await scene({ ...FAILED, url: URL1, urls: [URL1, URL2], failures: [{ slot: 1, reason: "no_answer", code: "esp-tls 0x8006", time: T }, { ...CLOSED, slot: 2 }] });
  const two = s.lines.map((l) => l.textContent.trim());
  check("two addresses, two lines, in the order tried", JSON.stringify(two) === JSON.stringify(["Webhook URL: nothing answers at this address", "Failover URL: the server closed the connection during the secure handshake"]), two);
  check("each line has its own bubble", s.lines.length === 2 && s.bubble(s.lines[0]).title === "Nothing answers" && s.bubble(s.lines[1]).title === "The server closed the connection");
  check("a bubble's code is its own address's", s.lines.length === 2 && /For support: esp-tls 0x8006,/.test(s.bubble(s.lines[0]).text) && /For support: tls -0x7280,/.test(s.bubble(s.lines[1]).text));
  s.close();
  /* two addresses, the first one alone in the list: still named */
  s = await scene({ ...FAILED, url: URL1, urls: [URL1, URL2], failures: [{ slot: 2, reason: "dns", code: "esp-tls 0x8001", time: T }] });
  check("with two addresses a single failed one is named too", s.lines.length === 1 && s.lines[0].textContent.trim() === "Failover URL: the name in the address was not found", s.lines[0] && s.lines[0].textContent);
  s.close();

  /* ---- every reason word of the firmware: its own line, its own bubble ---- */
  s = await scene({ ...FAILED, url: URL2, urls: [URL2, URL1], failures: [] });
  const P = s.w.eval("typeof PUSH_FAIL === 'object' ? PUSH_FAIL : null");
  check("the page's table of reasons is there", !!P);
  const missing = WORDS.filter((k) => !P || !Array.isArray(P[k]) || P[k].length !== 3 || P[k].some((x) => typeof x !== "string" || x.length < 8));
  check("it has a line, a title and a text for each of the " + WORDS.length + " words the firmware sends", missing.length === 0, missing);
  const linesOf = P ? WORDS.map((k) => P[k][0]) : [];
  check("no two reasons share a line", new Set(linesOf).size === linesOf.length);
  check("a line starts in lower case (it follows `Last failure: `) and ends without a full stop", linesOf.every((l) => /^[a-z]/.test(l) && !/\.$/.test(l)), linesOf.filter((l) => !/^[a-z]/.test(l) || /\.$/.test(l)));
  check("no long dash in any of the words", P && Object.values(P).every((v) => v.every((x) => ![...x].some((c) => c.charCodeAt(0) >= 0x2012 && c.charCodeAt(0) <= 0x2015))));
  s.close();

  /* ---- a certificate the check refused: one line for the two cases the unit cannot tell apart ---- */
  s = await scene({ ...FAILED, url: URL2, failures: [{ slot: 1, reason: "cert", code: "tls -0x2700", time: T }] });
  b = s.lines[0] && s.bubble(s.lines[0]);
  check("a refused certificate: one line, and the bubble names both things to check", s.lines.length === 1 && s.lines[0].textContent.trim() === "Last failure: the server's certificate was not accepted"
    && !!b && /for another name than the one in the address/.test(b.text) && /not signed by the authority selected for this webhook/.test(b.text) && /For support: tls -0x2700,/.test(b.text), b && b.text);
  s.close();

  /* ---- an answer that was not a 2xx ---- */
  for (const [http, line, inBubble] of [
    [400, "Last failure: Home Assistant answered 400", /trusted_proxies/],
    [502, "Last failure: the reverse proxy could not reach Home Assistant (502)", /Home Assistant behind it did not/],
    [504, "Last failure: the reverse proxy could not reach Home Assistant (504)", /Home Assistant behind it did not/],
    [404, "Last failure: the server answered 404", /answered with this status/]]) {
    s = await scene({ ...FAILED, url: URL1, last_error: "http=" + http, failures: [{ slot: 1, reason: "http", http, code: "http " + http, time: T }] });
    b = s.lines[0] && s.bubble(s.lines[0]);
    check("an answer of " + http + ": `" + line + "`", s.lines.length === 1 && s.lines[0].textContent.trim() === line && !!b && inBubble.test(b.text) && new RegExp("For support: http " + http + ",").test(b.text), s.lines[0] && s.lines[0].textContent);
    s.close();
  }

  /* ---- a word this page does not know (a newer firmware) ---- */
  s = await scene({ ...FAILED, url: URL2, failures: [{ slot: 1, reason: "some_future_word", code: "x 42", time: T }] });
  b = s.lines[0] && s.bubble(s.lines[0]);
  check("an unknown reason word still gives a line, and its code in the bubble", s.lines.length === 1 && s.lines[0].textContent.trim() === "Last failure: the push did not go through" && !!b && /For support: x 42,/.test(b.text), s.lines[0] && s.lines[0].textContent);
  s.close();
  /* no time, no code: no dangling "For support" */
  s = await scene({ ...FAILED, url: URL2, failures: [{ slot: 1, reason: "no_reply", code: "", time: "" }] });
  b = s.lines[0] && s.bubble(s.lines[0]);
  check("an entry without a code has no support line", !!b && b.text === "The connection opened and then nothing came back.", b && b.text);
  s.close();

  /* ---- status ok with an entry: the failover delivered, nothing to say ---- */
  s = await scene({ status: "ok", url: URL1, urls: [URL1, URL2], failures: [{ slot: 1, reason: "no_answer", code: "esp-tls 0x8006", time: T }] });
  check("status ok with a failed first address (the failover delivered): no line", s.lines.length === 0 && /Status: ok/.test(s.text), s.text.slice(0, 90));
  s.close();

  /* ---- rejected, removed: their own line, in the UI's red ---- */
  s = await scene({ status: "rejected", url: URL1, last_error: "http=403 identity rejected", failures: [{ slot: 1, reason: "http", http: 403, code: "http 403", time: T }] });
  let own = s.row && [...s.row.querySelectorAll("span.help")].find((x) => /rejected this device's identity/.test(x.textContent));
  check("rejected: its own line, no failure line beside it", !!own && s.lines.length === 0);
  check("that line is in the UI's red (--danger), not in a colour no theme defines", !!own && /var\(--danger\)/.test(own.getAttribute("style") || "") && !/--crit/.test(own.getAttribute("style") || ""), own && own.getAttribute("style"));
  s.close();
  s = await scene({ status: "removed", url: "", last_error: "http=410 removed in Home Assistant", failures: [{ slot: 1, reason: "http", http: 410, code: "http 410", time: T }] });
  own = s.row && [...s.row.querySelectorAll("span.help")].find((x) => /was removed in Home Assistant/.test(x.textContent));
  check("removed: its own line in the UI's red, no failure line", !!own && s.lines.length === 0 && /var\(--danger\)/.test(own.getAttribute("style") || ""), own && own.getAttribute("style"));
  s.close();
  const src = readFileSync(join(here, "preview.html"), "utf-8");
  check("no style in the page names the undefined --crit any more", !/var\(--crit\)/.test(src));
  check("the page defines --danger for both themes", (src.match(/--danger:#/g) || []).length >= 2);

  /* ---- an older unit: no `failures` at all ---- */
  s = await scene({ ...FAILED, url: URL2, failures: undefined });
  check("a unit that sends no `failures` (older firmware): the card as it was, no line, no error", s.lines.length === 0 && /Status: failed/.test(s.text) && s.errs.length === 0, s.errs.slice(0, 2));
  s.close();

  /* ---- the healthy card is untouched ---- */
  s = await scene({});
  check("a delivering unit: the chips and the URL, no line, no bubble button in the row", s.lines.length === 0 && /Status: ok/.test(s.text) && !!s.row && s.row.querySelectorAll("button.ibtn").length === 0, s.text.slice(0, 90));
  s.close();

  console.log(fails ? "PUSH REASON PROBE FAIL (" + fails + ")" : "PUSH REASON PROBE PASS");
  process.exit(fails ? 1 : 0);
})();
