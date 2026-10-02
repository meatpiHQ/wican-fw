/* Power Saving page probe (2026-10-01, sleep_manager v3/v4): the wake-up
   voltage renders as a volt slider next to the sleep voltage, "Wake up after"
   renders in SECONDS (0.1 to 5, default 0.5) and stages MILLISECONDS, the
   sleep delay is labelled "Sleep after", the status chip shows one decimal.
   Prints POWER PROBE PASS. Runs over the mock API. */
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import { JSDOM, VirtualConsole } from "jsdom";

const here = dirname(fileURLToPath(import.meta.url));
const html = readFileSync(join(here, "preview.html"), "utf-8");
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
let fails = 0;
const check = (n, ok, extra) => { console.log((ok ? "PASS " : "FAIL ") + n + (extra !== undefined ? "  [" + JSON.stringify(extra) + "]" : "")); if (!ok) { fails++; process.exitCode = 1; } };
const errs = [];
const vc = new VirtualConsole();
vc.on("jsdomError", (e) => errs.push(e.message));
const dom = new JSDOM(html, { runScripts: "dangerously", url: "http://wican.local/#/power", pretendToBeVisual: true, virtualConsole: vc,
  beforeParse(w) { w.__mockPreset = { apDefaultPassword: false }; w.matchMedia = () => ({ matches: false, addListener() {}, addEventListener() {} }); w.scrollTo = () => {}; w.HTMLCanvasElement.prototype.getContext = () => null; w.Element.prototype.scrollIntoView = () => {}; } });
const w = dom.window, d = () => w.document;
w.addEventListener("error", (e) => errs.push("window.onerror: " + e.message));
const $ = (s) => d().querySelector(s), $$ = (s) => [...d().querySelectorAll(s)];
const rowByLabel = (re) => $$("#view .frow").find((r) => re.test((r.querySelector("label") || {}).textContent || ""));
const fire = (el, type) => el.dispatchEvent(new w.Event(type, { bubbles: true }));
await sleep(1500);
check("Power Saving page", /Power Saving/.test(($("#view h1") || {}).textContent || ""), ($("#view h1") || {}).textContent);
const vRow = rowByLabel(/^Wake-up voltage \(V\)/);
check("the wake-up voltage row renders next to the sleep voltage", !!vRow && !!rowByLabel(/^Sleep voltage \(V\)/));
if (vRow) {
  const num = vRow.querySelector('input[type="number"]'), rng = vRow.querySelector('input[type="range"]');
  check("wake-up voltage as a volt slider: 13.2 V, range 12.1 to 15", num && num.value === "13.2" && rng && rng.min === "12.1" && rng.max === "15" && /\bV\b/.test(vRow.textContent), num && { v: num.value, min: rng && rng.min, max: rng && rng.max });
}
const aRow = rowByLabel(/^Wake up after \(s\)/);
check("the wake-up-after row renders in seconds", !!aRow);
if (aRow) {
  const num = aRow.querySelector('input[type="number"]'), rng = aRow.querySelector('input[type="range"]');
  check("0.5 s default, 0.1 to 5 s range, unit s", num && num.value === "0.5" && rng && rng.min === "0.1" && rng.max === "5" && /\bs\b/.test(aRow.textContent), num && { v: num.value, min: rng && rng.min, max: rng && rng.max });
  check("help explains spikes vs faster wake", /spikes/.test(aRow.textContent) && /faster/.test(aRow.textContent));
  num.value = "2"; fire(num, "change"); await sleep(200);
  const staged = w.eval("store").pending.get("sleep_manager");
  check("changing it to 2 s stages 2000 ms for sleep_manager (nothing PUT yet)", staged && staged.wake_delay_ms === 2000 && !(w.__mockState.puts > 0), staged && { wake_delay_ms: staged.wake_delay_ms, puts: w.__mockState.puts });
  check("header shows one unsaved change", /Submit Changes \(1\)/.test(($("#submitlabel") || {}).textContent || ""), ($("#submitlabel") || {}).textContent);
}
check("the sleep delay row is labelled Sleep after", !!rowByLabel(/^Sleep after \(min\)/));
check("the status chip shows the battery with one decimal", /(^|\D)1\d\.\d V/.test(($("#view") || {}).textContent) && !/1\d\.\d\d V/.test(($("#view") || {}).textContent), (($("#view") || {}).textContent.match(/1\d\.\d+ V/) || [])[0]);
check("no page errors", errs.length === 0, errs.slice(0, 2));
console.log(fails ? fails + " check(s) FAILED" : "POWER PROBE PASS");
process.exit(fails ? 1 : 0);
