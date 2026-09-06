/* Interaction probe for the reworked Dashboard (2026-09-06): state chip,
   group switches + poll stats, sparkline tiles with the parameter's own
   range / PID / age, stale fading, external values and the empty states.
   Runs against preview.html (mock API) under jsdom — no device needed. */
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
  runScripts: "dangerously", url: "http://wican.local/#/dashboard",
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
const shown = (el) => { if (!el) return false; for (let e = el; e && e !== d().body; e = e.parentElement) { if (e.hidden || (e.style && e.style.display === "none")) return false; } return true; };
const tiles = () => [...d().querySelectorAll("#view .dtile")];
const tile = (name) => tiles().find((t) => t.querySelector(".tk").textContent === name);
const stateChip = () => (d().querySelector("#view .phead .tools .chip") || {}).textContent || "";

(async () => {
  await sleep(1200);
  check("state chip: Polling", stateChip().trim() === "Polling", stateChip());
  const pill = d().querySelector("#view .gpill");
  check("group pill: switch on + name + period", !!pill && pill.querySelector("label.switch input").checked && /default/.test(pill.textContent) && /every 1 s/.test(pill.textContent), pill && pill.textContent);
  const stats = [...d().querySelectorAll("#view .dstats .chip")].map((c) => c.textContent.trim());
  check("stats: PID/filter counts + failures", stats.includes("4 PIDs · 1 filter") && stats.includes("2 failed"), stats);
  await sleep(1700);
  const stats2 = [...d().querySelectorAll("#view .dstats .chip")].map((c) => c.textContent.trim());
  check("poll rate measured between refreshes", stats2.some((s) => /^\d+(\.\d)? polls\/s$/.test(s)), stats2);
  check("five tiles: four parameters + the external GPS value", tiles().length === 5 && !!tile("gps_speed"), tiles().map((t) => t.querySelector(".tk").textContent));
  const rpm = tile("RPM");
  check("RPM: value + unit, sparkline, PID footer, fresh age", /rpm/.test(rpm.querySelector(".tv small").textContent) && !!rpm.querySelector("svg.spark polyline") && /PID 010C1/.test(rpm.textContent) && /\bs ago$/.test(rpm.querySelector(".dfoot span:last-child").textContent), rpm.querySelector(".dfoot").textContent);
  const rr = [...rpm.querySelectorAll(".drange span")].map((x) => x.textContent);
  check("RPM range from the config (0 … 8,000), not session autoscale", rr[0] === "0" && rr[2] === "8,000" && rr[1] === "", rr);
  const cool = tile("Coolant");
  check("Coolant: temperature icon title + -40 … 215 range", cool.querySelector(".ti").title === "temperature" && [...cool.querySelectorAll(".drange span")].map((x) => x.textContent).join("|") === "-40||215", [...cool.querySelectorAll(".drange span")].map((x) => x.textContent));
  const soc = tile("SOC_BMS");
  check("SOC_BMS (filter, no range): session autoscale labelled, stale after 45 s, faded", /seen this session/.test(soc.textContent) && soc.classList.contains("stale") && /stale · 45 s/.test(soc.textContent) && /Filter 0x18DAF110/.test(soc.textContent), soc.querySelector(".dfoot").textContent);
  const gps = tile("gps_speed");
  check("external value: GPS source + navigation icon title", /External \(GPS\)/.test(gps.textContent) && gps.querySelector(".ti").title === "external value");
  check("fresh tiles are not faded", !rpm.classList.contains("stale") && !cool.classList.contains("stale"));

  /* group switch -> runtime toggle -> state follows */
  pill.querySelector("label.switch input").click(); await sleep(2000);
  check("switching the group off: chip 'Not polling', switch reflects it", stateChip().trim() === "Not polling" && !d().querySelector("#view .gpill label.switch input").checked, stateChip());
  d().querySelector("#view .gpill label.switch input").click(); await sleep(2000);
  check("switching it back on: Polling again", stateChip().trim() === "Polling", stateChip());

  /* empty states */
  const conn = w.eval("conn");
  conn.status.bits.autopid_enabled = false; await sleep(1800);
  const empty = d().querySelector("#view .empty");
  check("Automate off: chip + empty state linking to Automate → Settings, strip hidden, no tiles", stateChip().trim() === "Automate off" && shown(empty) && /Automate is off/.test(empty.textContent) && !!empty.querySelector('a[href="#/automate/settings"]') && !shown(d().querySelector("#view .dash-strip")) && !shown(d().querySelector("#view .tiles")), empty.textContent);
  conn.status.bits.autopid_enabled = true; await sleep(1800);
  check("back to tiles once Automate is on", tiles().length === 5 && shown(d().querySelector("#view .dash-strip")));
  check("no jsdom errors", errs.length === 0, errs.slice(0, 2));
  console.log(fails ? "PROBE: " + fails + " failure(s)" : "PROBE: all checks passed");
  w.close();
})().catch((e) => { console.error("PROBE ERROR", e.stack || e.message); process.exit(1); });
