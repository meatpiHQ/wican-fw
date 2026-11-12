/* Probe (2026-11-12): the Fault codes card on System Monitor and its info
   bubbles. Ali on the mock-up of that day: the table stays as it is (simple
   and clean), "if we want to add additional info add a bubble". So: the
   table's five columns unchanged; a small button beside a code the UI has
   words for, none beside a code it does not know; a click opens ONE bubble
   with a title and a paragraph; the page's 2 s refresh does not take it away
   (the card is painted again only when the list changed); the button again,
   Escape or a click elsewhere closes it; Clear fault codes empties the card.
   Over the mock's /api/faults with __mockPreset.faults. Prints
   `FAULTS PROBE PASS`. */
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import { JSDOM, VirtualConsole } from "jsdom";

const here = dirname(fileURLToPath(import.meta.url));
const html = readFileSync(join(here, "preview.html"), "utf-8");
let fails = 0;
const check = (n, ok, extra) => { console.log((ok ? "PASS " : "FAIL ") + n + (extra !== undefined ? "  [" + JSON.stringify(extra) + "]" : "")); if (!ok) { fails++; process.exitCode = 1; } };
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const FAULTS = [
  { code: "obd_chip_silent", detail: "silent; still silent after the reset pulse", count: 3, first_time: 1794462461, last_time: 1794462637 },
  { code: "obd_chip_woke", detail: "awake at nap 412 after it had slept", count: 2, first_time: 1794200000, last_time: 1794300000 },
  { code: "some_future_code", detail: "a code this UI has no words for", count: 1, first_time: 1794462461, last_time: 1794462461 },
];

function boot(hash, preset) {
  const errs = [];
  const vc = new VirtualConsole();
  vc.on("jsdomError", (e) => errs.push(e.message + " " + (e.detail && e.detail.stack || "").slice(0, 300)));
  const dom = new JSDOM(html, {
    runScripts: "dangerously", url: "http://wican.local/" + hash,
    pretendToBeVisual: true, virtualConsole: vc,
    beforeParse(w) {
      w.__mockPreset = preset;
      w.matchMedia = () => ({ matches: false, addListener() {}, addEventListener() {} });
      w.scrollTo = () => {}; w.HTMLCanvasElement.prototype.getContext = () => null;
      if (!w.TextEncoder) { w.TextEncoder = TextEncoder; w.TextDecoder = TextDecoder; }
    },
  });
  const w = dom.window, d = () => w.document;
  const $ = (sel) => d().querySelector(sel), $$ = (sel, root) => [...(root || d()).querySelectorAll(sel)];
  const cardByTitle = (t) => $$("#view .section").find((c) => (c.querySelector(".legend, h3") || {}).textContent.trim().startsWith(t));
  const click = (el) => el && el.dispatchEvent(new w.MouseEvent("click", { bubbles: true, cancelable: true }));
  const key = (k) => d().dispatchEvent(new w.KeyboardEvent("keydown", { key: k, bubbles: true }));
  return { w, d, $, $$, cardByTitle, click, key, errs, close: () => dom.window.close() };
}

(async () => {
  const p = boot("#/sysmon", { faults: FAULTS });
  await sleep(2600);
  const card = () => p.cardByTitle("Fault codes");
  const bubbles = () => p.$$(".ibubble");

  /* ---- the table is the one it was ---- */
  check("the Fault codes card renders", !!card());
  const heads = p.$$("th", card()).map((t) => t.textContent.trim());
  check("its table has the five columns it had, no sixth", JSON.stringify(heads) === JSON.stringify(["Code", "Detail", "Count", "First seen", "Last seen"]), heads);
  const rows = p.$$("tbody tr", card());
  check("three rows, one per fault code", rows.length === 3, rows.length);
  check("a row reads the code and the detail as the device sent them", rows.length === 3 && /obd_chip_silent/.test(rows[0].textContent) && /silent; still silent after the reset pulse/.test(rows[0].textContent), rows[0] && rows[0].textContent.slice(0, 90));
  check("the header's chip counts them", /3 faults/.test((p.$("#cp-faults") || {}).textContent || ""), (p.$("#cp-faults") || {}).textContent);

  /* ---- the bubble's button: beside a code the UI has words for ---- */
  const btns = rows.map((r) => r.querySelector("button.ibtn"));
  check("a code with words has an info button, labelled for a screen reader", !!btns[0] && !!btns[1] && /What obd_chip_silent means/.test(btns[0].getAttribute("aria-label") || ""), btns[0] && btns[0].getAttribute("aria-label"));
  check("a code the UI does not know has none", btns[2] === null);
  check("the button sits in the Code cell, beside the code", !!btns[0] && btns[0].closest("td") === rows[0].querySelector("td"));
  check("no bubble before a click", bubbles().length === 0);

  /* ---- one bubble, opened by the button ---- */
  p.click(btns[0]);
  let b = bubbles();
  check("a click opens one bubble with a title and a paragraph", b.length === 1 && /The OBD chip did not answer/.test((b[0].querySelector("b") || {}).textContent || "") && ((b[0].querySelector("p") || {}).textContent || "").length > 60, b[0] && b[0].textContent.slice(0, 80));
  check("the button says it is open", btns[0].getAttribute("aria-expanded") === "true");

  /* ---- the page's 2 s refresh leaves it alone ---- */
  await sleep(4600);
  check("two refreshes later the bubble is still there", bubbles().length === 1);
  check("and the card was not painted again (the same button)", p.$$("tbody tr", card())[0].querySelector("button.ibtn") === btns[0]);

  /* ---- another row's button swaps it ---- */
  p.click(btns[1]);
  b = bubbles();
  check("the next row's button swaps the bubble: still one, the other code's words", b.length === 1 && /woke up during sleep/.test(b[0].textContent) && btns[0].getAttribute("aria-expanded") === "false" && btns[1].getAttribute("aria-expanded") === "true", b[0] && b[0].textContent.slice(0, 60));

  /* ---- three ways to close ---- */
  p.click(btns[1]);
  check("the same button again closes it", bubbles().length === 0 && btns[1].getAttribute("aria-expanded") === "false");
  p.click(btns[0]);
  p.key("Escape");
  check("Escape closes it", bubbles().length === 0 && btns[0].getAttribute("aria-expanded") === "false");
  p.click(btns[0]);
  p.click(p.$("#view h1") || p.d().body);
  check("a click elsewhere closes it", bubbles().length === 0);
  p.click(btns[0]);
  p.click(bubbles()[0] && bubbles()[0].querySelector("p"));
  check("a click inside the bubble does not (its text can be selected)", bubbles().length === 1);

  /* ---- Clear fault codes: as before, and the bubble goes with the list ---- */
  const clear = p.$$("button", card()).find((x) => x.textContent.trim() === "Clear fault codes");
  check("Clear fault codes is there", !!clear);
  p.click(clear);
  await sleep(300);
  const modal = p.$("#modal-root .modal");
  check("it asks first", !!modal && /3 latched fault code/.test(modal.textContent), modal && modal.textContent.slice(0, 80));
  p.click(p.$$("button", modal).find((x) => x.textContent.trim() === "Continue"));
  await sleep(1200);
  check("the card then reads no fault codes", /No fault codes, device healthy/.test((card() || {}).textContent || ""), (card() || {}).textContent);
  check("and no bubble is left behind", bubbles().length === 0);

  check("no jsdom errors", p.errs.length === 0, p.errs);
  p.close();
  console.log(fails === 0 ? "FAULTS PROBE PASS" : "FAULTS PROBE FAIL (" + fails + ")");
})();
