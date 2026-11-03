/* Probe (2026-11-03): the Status page's MQTT row. A broker that is enabled
   but unreachable (behind a VPN that carried nothing, the Tailscale subnet
   route report) read "Off", which a user takes for "disabled". The row must
   say "Not connected" while MQTT is enabled and the broker bit is down,
   "Connected" while it is up, and "Off" only when MQTT is disabled. Three
   mock scenes over `window.__mockPreset` ({ mqtt: {enabled}, mqttConnected }).
   Runs over the mock API:
   `python make_preview.py --fresh && node probe_mqtt_status.mjs` -> `MQTT STATUS PROBE PASS`. */
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import { JSDOM, VirtualConsole } from "jsdom";

const here = dirname(fileURLToPath(import.meta.url));
const html = readFileSync(join(here, "preview.html"), "utf-8");
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
let fails = 0;
const check = (n, ok, extra) => { console.log((ok ? "PASS " : "FAIL ") + n + (extra !== undefined ? "  [" + JSON.stringify(extra) + "]" : "")); if (!ok) { fails++; } };

async function scene(name, preset) {
  const errs = [];
  const vc = new VirtualConsole();
  vc.on("jsdomError", (e) => errs.push(e.message));
  const dom = new JSDOM(html, {
    runScripts: "dangerously", url: "http://wican.local/#/status",
    pretendToBeVisual: true, virtualConsole: vc,
    beforeParse(w) {
      w.__mockPreset = preset;
      w.matchMedia = () => ({ matches: false, addListener() {}, addEventListener() {} });
      w.scrollTo = () => {}; w.HTMLCanvasElement.prototype.getContext = () => null;
      if (!w.TextEncoder) { w.TextEncoder = TextEncoder; w.TextDecoder = TextDecoder; }
    },
  });
  const w = dom.window;
  let word = null;
  for (let i = 0; i < 40 && word === null; i++) {
    await sleep(100);
    const rows = [...w.document.querySelectorAll("#view .kv > div, #view .kv dt, #view .kv tr, #view .kv .row, #view .kv")];
    const text = w.document.querySelector("#view") ? w.document.querySelector("#view").textContent : "";
    const m = /MQTT\s*(Connected|Not connected|Off)/.exec(text);
    if (m) word = m[1];
    void rows;
  }
  check(name + ": no page error", errs.length === 0, errs.slice(0, 2));
  dom.window.close();
  return word;
}

const a = await scene("enabled, broker down", { mqtt: { enabled: true }, mqttConnected: false });
check("enabled and unreachable reads Not connected", a === "Not connected", a);
const b = await scene("enabled, broker up", { mqtt: { enabled: true }, mqttConnected: true });
check("enabled and connected reads Connected", b === "Connected", b);
const c = await scene("disabled", { mqtt: { enabled: false }, mqttConnected: false });
check("disabled reads Off", c === "Off", c);

console.log(fails ? `MQTT STATUS PROBE FAIL (${fails})` : "MQTT STATUS PROBE PASS");
process.exit(fails ? 1 : 0);
