/* Probe (2026-11-20, meatpi): the web page says what J2534 over the USB cable needs. A
   contributor could not get it to work: the connector ships in the Host role, where the PC
   sees only the board's console ports, and nothing on the J2534 page named the two USB
   settings; the role's help spoke of a network adapter alone. Now an info bubble on the
   J2534 page names them, the role's help names the serial port, and the USB device class
   has a help text. Runs over the mock API.  ->  J2534 USB PROBE PASS */
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import { JSDOM, VirtualConsole } from "jsdom";

const here = dirname(fileURLToPath(import.meta.url));
const html = readFileSync(join(here, "preview.html"), "utf-8");
const errs = [];
const vc = new VirtualConsole();
vc.on("jsdomError", (e) => errs.push(e.message + " " + (e.detail && e.detail.stack || "").slice(0, 300)));

const dom = new JSDOM(html, {
  runScripts: "dangerously", url: "http://wican.local/#/j2534",
  pretendToBeVisual: true, virtualConsole: vc,
  beforeParse(w) {
    w.matchMedia = () => ({ matches: false, addListener() {}, addEventListener() {} });
    w.scrollTo = () => {}; w.HTMLCanvasElement.prototype.getContext = () => null;
    if (!w.TextEncoder) { w.TextEncoder = TextEncoder; w.TextDecoder = TextDecoder; }
  },
});
const w = dom.window, d = () => w.document;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
let fails = 0;
const check = (n, ok, extra) => { console.log((ok ? "PASS " : "FAIL ") + n + (extra !== undefined ? "  [" + JSON.stringify(extra) + "]" : "")); if (!ok) { fails++; process.exitCode = 1; } };
const $ = (sel) => d().querySelector(sel), $$ = (sel) => [...d().querySelectorAll(sel)];
const nav = (hash) => { w.location.hash = hash; w.dispatchEvent(new w.Event("hashchange")); };
const row = (k) => $('#view .frow[data-key="' + k + '"]');

(async () => {
  await sleep(1500);

  /* the J2534 page: one small button in the header, the text behind it */
  const btn = $('#view button.ibtn[aria-label="J2534 over the USB cable"]');
  const chips = $$("#view .chip").find((c) => /listening/.test(c.textContent));
  check("the J2534 page has ONE info button for the USB cable, outside its status block",
    !!btn && $$("#view button.ibtn").length === 1 && !!chips && !chips.parentElement.contains(btn));
  check("the page itself gained no text: the two settings are not named outside the bubble", !/Connector role/.test($("#view").textContent));
  if (btn) btn.click();
  await sleep(50);
  const bubble = $(".ibubble");
  const text = bubble ? bubble.textContent : "";
  check("its bubble names the page and the two settings by the words on the USB page",
    /USB page/.test(text) && /Connector role to Device/.test(text) && /USB device class to CDC serial/.test(text), text.slice(0, 120));
  check("and says what the PC then shows, and that the Host role's ports are not it", /USB serial port/.test(text) && /Host role/.test(text) && /do not speak J2534/.test(text));
  /* the status block repaints every 4 s: the open bubble must keep its button through that */
  const chipsBefore = chips;
  await sleep(4600);
  const chipsNow = $$("#view .chip").find((c) => /listening/.test(c.textContent));
  check("the status block repainted meanwhile, and the open bubble still has its button",
    !!chipsNow && chipsNow !== chipsBefore && btn.isConnected && btn.getAttribute("aria-expanded") === "true" && !!$(".ibubble"));
  d().dispatchEvent(new w.KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
  await sleep(50);
  check("Escape closes the bubble", !$(".ibubble"));

  /* the USB page's settings: the words the bubble uses are the labels there */
  let where = null;
  for (const hash of ["#/usb/settings", "#/usb"]) {
    nav(hash); await sleep(1200);
    if (!row("role")) {
      const tab = $$("#view button, #view a").find((b) => /^Settings$/.test(b.textContent.trim()));
      if (tab) { tab.click(); await sleep(800); }
    }
    if (row("role")) { where = hash; break; }
  }
  check("the USB page shows the connector's settings", !!where && !!row("device_class"), where);
  const roleRow = row("role"), classRow = row("device_class");
  const label = (r) => r ? (r.querySelector("label") || r).textContent : "";
  check("their labels are Connector role and USB device class", /Connector role/.test(label(roleRow)) && /USB device class/.test(label(classRow)), [label(roleRow).slice(0, 40), label(classRow).slice(0, 40)]);
  const opts = classRow ? [...classRow.querySelectorAll("option")].map((o) => o.textContent) : [];
  const roleOpts = roleRow ? [...roleRow.querySelectorAll("option")].map((o) => o.textContent) : [];
  check("the choices are Device (network or serial port, no longer network alone) and CDC serial",
    roleOpts.some((o) => /^Device: network or serial port to a PC$/.test(o.trim())) && opts.some((o) => /^CDC serial$/.test(o.trim())), [roleOpts, opts]);
  check("the role's help names the serial port beside the network adapter", !!roleRow && /network adapter or a serial port/.test(roleRow.textContent));
  check("the device class has a help text: CDC serial is the COM port for a J2534 tool on the cable", !!classRow && /COM port for a J2534 tool on the cable/.test(classRow.textContent));

  check("no script error on either page", errs.length === 0, errs.slice(0, 2));
  console.log(fails ? "J2534 USB PROBE FAIL" : "J2534 USB PROBE PASS");
  process.exit(fails ? 1 : 0);
})();
