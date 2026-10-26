/* Probe (2026-10-26): the new-car bar. Ali set a device up on the simulator (two
   standard PIDs chosen), took it to his car, and the dashboard was empty with no
   word: the car answered as a new one, became current with its rows stored off,
   and his two rows stayed with the simulator's entry. The identity rule stays
   (two cars of one make can share an ECU set); the page now says so.

   The status poll's `vehicle` summary drives it: shown while the current car's
   setup never finished (`pending`) and the store holds another car.

   Four page boots over the mock:
     A  a pending car current beside a set-up one, on the Dashboard: the bar
        (lead, the car named with "(no VIN)", Set up this car -> #/setup/vehicle,
        Not now), and the banner above the tiles; Not now hides the bar for that
        car in this browser and leaves the banner
     B  the same store, on the Quick Setup: no bar (the wizard is the remedy)
     C  one car only, pending (a fresh device that met its first car): no bar
     D  two cars, the current one set up: no bar
   usage: node probe_newcar.mjs   (after make_preview.py; node >= 20) */
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import { JSDOM, VirtualConsole } from "jsdom";

const here = dirname(fileURLToPath(import.meta.url));
const html = readFileSync(join(here, "preview.html"), "utf-8");
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
let fails = 0, total = 0;
const check = (n, ok, extra) => { total++; console.log((ok ? "PASS " : "FAIL ") + n + (extra !== undefined ? "  [" + JSON.stringify(extra) + "]" : "")); if (!ok) { fails++; process.exitCode = 1; } };

function boot(url, preset) {
  const errs = [];
  const vc = new VirtualConsole();
  vc.on("jsdomError", (e) => errs.push(e.message + " " + (e.detail && e.detail.stack || "").slice(0, 300)));
  const dom = new JSDOM(html, {
    runScripts: "dangerously", url, pretendToBeVisual: true, virtualConsole: vc,
    beforeParse(w) {
      w.__mockPreset = preset || {};
      w.matchMedia = () => ({ matches: false, addListener() {}, addEventListener() {} });
      w.scrollTo = () => {}; w.HTMLCanvasElement.prototype.getContext = () => null;
      if (!w.TextEncoder) { w.TextEncoder = TextEncoder; w.TextDecoder = TextDecoder; }
      w.Element.prototype.scrollIntoView = () => {};
    },
  });
  const w = dom.window, d = () => w.document;
  w.addEventListener("error", (e) => errs.push("window.onerror: " + e.message));
  w.addEventListener("unhandledrejection", (e) => errs.push("unhandledrejection: " + (e.reason && e.reason.message || e.reason)));
  const $ = (sel) => d().querySelector(sel), $$ = (sel) => [...d().querySelectorAll(sel)];
  return { w, d, $, $$, errs, close: () => dom.window.close() };
}

const VIN = { key: "1WCAN0FW0P0000001", vin: "1WCAN0FW0P0000001", name: "1WC 0001", fingerprint: "bf3423ae", protocol: "6", dialect: "obd2",
  ecus: "7E8:183F8003", std_supported: 16, pending_profile: false, profile: "" };
const NEW = { key: "fp:be34221b", vin: "", name: "Car be34", fingerprint: "be34221b", protocol: "6", dialect: "obd2",
  ecus: "7E8:183F8000", std_supported: 9, pending_profile: true, profile: "" };
const BASE = "http://wican.local/";

/* ---- A: the fork, seen from the Dashboard ---- */
{
  const p = boot(BASE + "#/dashboard", { vehicles: { current: NEW.key, vehicles: [VIN, NEW] } });
  await sleep(2500);
  const bar = p.$("#vehbar");
  check("A: the bar is up on the dashboard", bar && !bar.hidden, bar && bar.hidden);
  const txt = (bar && bar.textContent) || "";
  check("A: the lead says WiCAN is reading a car it was not set up for", /WiCAN is reading a car it was not set up for\./.test(txt), txt.slice(0, 60));
  check("A: the car is named, without a VIN", /Car be34 \(no VIN\) answered on the last contact/.test(txt), txt.slice(60, 140));
  check("A: it says what to do (fold in, or set up)", /fold it in under Vehicles/.test(txt) && /set it up/.test(txt));
  const act = p.$$("#vehbar a").find((a) => /Set up this car/.test(a.textContent));
  check("A: Set up this car leads to the vehicle step", act && /#\/setup\/vehicle$/.test(act.getAttribute("href")), act && act.getAttribute("href"));
  const note = p.$("#dash-vehnote");
  check("A: the dashboard carries the banner above the tiles", note && /Showing Car be34 \(no VIN\): not set up yet/.test(note.textContent) && note.querySelector("a[href='#/setup/vehicle']"), note && note.textContent.slice(0, 80));
  const notNow = p.$$("#vehbar a").find((a) => /Not now/.test(a.textContent));
  notNow.click(); await sleep(200);
  let seen = ""; try { seen = p.w.localStorage.getItem("wican-veh-seen"); } catch (e) { }
  check("A: Not now hides the bar for this car in this browser", p.$("#vehbar").hidden && seen === NEW.key, seen);
  check("A: the dashboard banner stays (it is the page's own word)", p.$("#dash-vehnote") && /Showing Car be34/.test(p.$("#dash-vehnote").textContent));
  check("A: no page errors", p.errs.length === 0, p.errs.slice(0, 2));
  p.close();
}

/* ---- B: the same store on the Quick Setup: the wizard is the remedy, no bar ---- */
{
  const p = boot(BASE + "#/setup/vehicle", { vehicles: { current: NEW.key, vehicles: [VIN, NEW] } });
  await sleep(2500);
  check("B: no bar on the Quick Setup", p.$("#vehbar") && p.$("#vehbar").hidden);
  check("B: no page errors", p.errs.length === 0, p.errs.slice(0, 2));
  p.close();
}

/* ---- C: one car, pending: a fresh device that met its first car ---- */
{
  const p = boot(BASE + "#/dashboard", { vehicles: { current: NEW.key, vehicles: [NEW] } });
  await sleep(2500);
  check("C: one car only: no bar, no banner", p.$("#vehbar").hidden && !(p.$("#dash-vehnote") || {}).textContent);
  p.close();
}

/* ---- D: two cars, the current one set up ---- */
{
  const p = boot(BASE + "#/dashboard", { vehicles: { current: VIN.key, vehicles: [VIN, NEW] } });
  await sleep(2500);
  check("D: the set-up car current: no bar, no banner", p.$("#vehbar").hidden && !(p.$("#dash-vehnote") || {}).textContent);
  check("D: no page errors", p.errs.length === 0, p.errs.slice(0, 2));
  p.close();
}

console.log(fails ? `NEWCAR PROBE FAIL (${fails} of ${total})` : `NEWCAR PROBE PASS (${total} checks)`);
process.exit(fails ? 1 : 0);
