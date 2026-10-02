/* Debug helper for the wizard's Battery and sleep step: boots the preview as a
   fresh device, jumps to #/setup/power, moves the mock battery and prints the
   step's state after each phase. usage: node debug_power.mjs */
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { JSDOM, VirtualConsole } from "jsdom";
const here = dirname(fileURLToPath(import.meta.url));
const html = readFileSync(join(here, "preview.html"), "utf-8");
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const vc = new VirtualConsole();
vc.on("jsdomError", (e) => console.log("jsdomError", e.message, (e.detail && e.detail.stack || "").slice(0, 300)));
const dom = new JSDOM(html, { runScripts: "dangerously", url: "http://wican.local/#/setup/power", pretendToBeVisual: true, virtualConsole: vc,
  beforeParse(w) { w.__mockPreset = { apDefaultPassword: false }; w.matchMedia = () => ({ matches: false, addListener() {}, addEventListener() {} }); w.scrollTo = () => {}; w.HTMLCanvasElement.prototype.getContext = () => null; w.Element.prototype.scrollIntoView = () => {}; } });
const w = dom.window;
w.addEventListener("error", (e) => console.log("window.onerror", e.message));
const st = () => { const d = w.document; const t = (sel) => (d.querySelector(sel) || {}).textContent; const tr = d.querySelector("#qs-pwr-trace"); return JSON.stringify({ big: t("#qs-pwr-big"), state: t("#qs-pwr-state"), chips: [...d.querySelectorAll("#view .qs-check .chip")].map((c) => c.textContent), sleepVal: t("#qs-pwr-sleep-val"), wakeVal: t("#qs-pwr-wake-val"), labels: tr ? [...tr.querySelectorAll("text")].map((x) => x.textContent).slice(-2) : null, wakeIn: d.querySelector("#qs-pwr-wake") ? { v: d.querySelector("#qs-pwr-wake").value, min: d.querySelector("#qs-pwr-wake").min, max: d.querySelector("#qs-pwr-wake").max, dis: d.querySelector("#qs-pwr-wake").disabled } : null, sum: (t("#qs-pwr-sum") || "").slice(0, 160) }); };
await sleep(2500);
console.log("h2:", (w.document.querySelector("#view h2") || {}).textContent, st());
w.__mockState.batteryV = 14.31; await sleep(7600);
console.log("after charging:", st());
w.__mockState.batteryV = 12.78; await sleep(13500);
console.log("after rest:", st());
console.log("vals:", (w.document.querySelector("#qs-pwr-sleep-val") || {}).textContent, (w.document.querySelector("#qs-pwr-wake-val") || {}).textContent, "lines:", w.document.querySelectorAll("#qs-pwr-trace line.t").length);
process.exit(0);
