/* Probe (2026-09-07, meatpi): "Wake on motion" is sleep-related in users' eyes, so the
   Power Saving page must flag it while it is on — and say what it really does (bump
   events, no wake from sleep). The Motion card shows each detector's knobs only while
   that detector is on, and the threshold help no longer claims a knock wakes the device.
   Runs over the mock API (the mock device has WoM on). */
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
  runScripts: "dangerously", url: "http://wican.local/#/power",
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
const fire = (el, type) => el.dispatchEvent(new w.Event(type, { bubbles: true }));
const nav = (hash) => { w.location.hash = hash; w.dispatchEvent(new w.Event("hashchange")); };
const flag = () => $$("#view .banner").find((b) => /Wake on motion is on/.test(b.textContent));
const row = (k) => $('#view .frow[data-key="' + k + '"]');
const shown = (el) => !!el && el.style.display !== "none";
const toggle = (k) => { const i = row(k).querySelector('input[type="checkbox"]'); i.checked = !i.checked; fire(i, "change"); fire(i, "input"); };

(async () => {
  await sleep(900);
  /* the mock ships wake on motion OFF (the 2026-09-07 default): switch it on for the check */
  const v0 = await (await w.fetch("/api/settings/imu_manager")).json();
  await w.fetch("/api/settings/imu_manager", { method: "PUT", body: JSON.stringify({ ...v0, wom: true }) });
  nav("#/advanced"); await sleep(300); nav("#/power"); await sleep(900);
  /* 1. the flag while the (mock) device has WoM on */
  const f = flag();
  check("Power Saving flags wake on motion while it is on, says it does not wake the device, links to Advanced",
    !!f && /does not wake/.test(f.textContent) && !!f.querySelector('a[href="#/advanced"]'), f && f.textContent.slice(0, 80));

  /* 2. turn it off through the API the page uses, look at both pages */
  const vals = await (await w.fetch("/api/settings/imu_manager")).json();
  await w.fetch("/api/settings/imu_manager", { method: "PUT", body: JSON.stringify({ ...vals, wom: false }) });
  nav("#/advanced"); await sleep(700);
  check("Motion card: the threshold row is hidden while wake on motion is off", shown(row("wom")) && !shown(row("wom_threshold")) && shown(row("smd")));
  nav("#/power"); await sleep(700);
  check("no flag once wake on motion is off", !flag());

  /* 3. the disclosure follows the switches (staged edits only, no navigation after) */
  nav("#/advanced"); await sleep(700);
  const adv = $$("#view button").find((b) => /Show advanced/.test(b.textContent));
  if (adv) adv.click(); await sleep(60);
  check("advanced revealed: SMD knobs visible, threshold still hidden", shown(row("smd_sensitivity")) && shown(row("stationary_s")) && !shown(row("wom_threshold")));
  toggle("wom"); await sleep(120);
  check("switching wake on motion on reveals the threshold", shown(row("wom_threshold")));
  check("threshold help talks about bump events, not waking the device",
    /bump event/.test(row("wom_threshold").textContent) && !/wake the device/.test(row("wom_threshold").textContent));
  check("wake on motion help says it does not wake the WiCAN and is off by default",
    /does not wake/.test(row("wom").textContent) && /Off by default/.test(row("wom").textContent));
  toggle("smd"); await sleep(120);
  check("switching SMD off hides its sensitivity and stationary time", !shown(row("smd_sensitivity")) && !shown(row("stationary_s")) && shown(row("wom_threshold")));

  check("no page errors", errs.length === 0, errs.slice(0, 2));
  console.log(fails ? `FAILED ${fails}` : "ALL PASS");
  process.exit(fails ? 1 : 0);
})();
