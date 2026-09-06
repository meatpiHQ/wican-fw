/* Probe for the WiFi Network card's "why the station is not connected" line
   (2026-09-06): fed by /api/wifi/status sta_attempt, shown only while the
   station is down, worded from the disconnect reason and the failure streak. */
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
  runScripts: "dangerously", url: "http://wican.local/#/settings/wifi",
  pretendToBeVisual: true, virtualConsole: vc,
  beforeParse(w) {
    w.matchMedia = () => ({ matches: false, addListener() {}, addEventListener() {} });
    w.scrollTo = () => {}; w.HTMLCanvasElement.prototype.getContext = () => null;
  },
});
const w = dom.window, d = () => w.document;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
let fails = 0;
const check = (n, ok, extra) => { console.log((ok ? "PASS " : "FAIL ") + n + (extra !== undefined ? "  [" + JSON.stringify(extra) + "]" : "")); if (!ok) { fails++; process.exitCode = 1; } };
const note = () => [...d().querySelectorAll("#view .note.warn")].find((n) => /Joining|Not connected to/.test(n.textContent));

(async () => {
  await sleep(1500);
  check("station up: no 'why' line", !note() || note().style.display === "none");
  /* the page refreshes /api/status every 3 s, so flip the bit at the source: wrap the mock's fetch */
  const orig = w.fetch; let down = true;
  w.fetch = (u, o) => orig(u, o).then((r) => (String(u).endsWith("/api/status") && down)
    ? Object.assign({}, r, { json: async () => { const j = await r.json(); j.bits.sta_connected = false; return j; } }) : r);
  await sleep(6600);
  const n = note();
  check("station down after rejections: plain-language line", !!n && n.style.display !== "none" && /Joining “HomeWiFi” failed \(the handshake timed out\), 3 attempts in a row/.test(n.textContent) && /Other networks on the list are tried first/.test(n.textContent) && /If the password is wrong/.test(n.textContent), n && n.textContent);
  down = false;
  await sleep(6600);
  check("station back: line hidden", note().style.display === "none");
  check("no jsdom errors", errs.length === 0, errs.slice(0, 2));
  console.log(fails ? "PROBE: " + fails + " failure(s)" : "PROBE: all checks passed");
  w.close();
})().catch((e) => { console.error("PROBE ERROR", e.stack || e.message); process.exit(1); });
