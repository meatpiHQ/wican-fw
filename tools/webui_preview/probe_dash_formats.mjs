/* Probe for the dashboard history across the logger's parameter formats
   (2026-09-06): CSV through the export route, binary .wdl files fetched
   and decoded in the browser (with the logger gate paused for the file
   being written), and the SQLite path offering to install the sql.js
   reader when it is missing. Mock API under jsdom with a stub uPlot; the
   history-info cache is 30 s, so each format switch waits it out. */
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
    if (!w.TextEncoder) { w.TextEncoder = TextEncoder; w.TextDecoder = TextDecoder; }
    w.uPlot = class { constructor(o, d, el) { this.el = el; const c = w.document.createElement("div"); c.className = "u-stub"; el.append(c); this.setData(d); }
      setData(d) { this.el.dataset.points = String(d[0].length); } setSize() {} destroy() { this.el.replaceChildren(); } };
  },
});
const w = dom.window, d = () => w.document;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
let fails = 0;
const check = (n, ok, extra) => { console.log((ok ? "PASS " : "FAIL ") + n + (extra !== undefined ? "  [" + JSON.stringify(extra) + "]" : "")); if (!ok) { fails++; process.exitCode = 1; } };
const tile = (name) => [...d().querySelectorAll("#view .dtile")].find((t) => t.dataset.name === name);
const modalRoot = () => d().querySelector("#modal-root");
const rowEl = (label) => [...modalRoot().querySelectorAll(".frow")].find((x) => x.querySelector("label").textContent === label);
const fire = (el, type) => el.dispatchEvent(new w.Event(type, { bubbles: true }));
const setSel = (el, v) => { el.value = v; fire(el, "change"); };
const makeChart = async (name, win, waitMs) => {
  tile(name).querySelector(".tgear").click(); await sleep(100);
  setSel(rowEl("Widget").querySelector("select"), "chart");
  if (win) setSel(rowEl("History").querySelector("select"), String(win));
  [...modalRoot().querySelectorAll(".acts button")].find((b) => /^Save$/.test(b.textContent)).click(); await sleep(waitMs || 2500);
};
const setFormat = async (fmt, active) => {
  Object.assign(w.__MOCK_SETTINGS__.data_logger.values, { enabled: true, format: fmt, autopid_log: "all" });
  w.__mockState.loggerFile = active || "";
  console.log("   format -> " + fmt + "; waiting 31 s for the history-info cache…");
  await sleep(31000);
};

/* a .wdl fixture exactly as the firmware writes it (tools/wdl_dump.py is the reference):
   "WDL1", a dictionary record per parameter, then 120 param records 30 s apart per parameter */
function wdlFixture(names, nowMs) {
  const parts = [];
  const push = (arr) => parts.push(Uint8Array.from(arr));
  push([0x57, 0x44, 0x4c, 0x31]);
  names.forEach((nm, id) => { const b = new TextEncoder().encode("autopid." + nm); push([0x01, id & 255, id >> 8, b.length]); parts.push(b); });
  for (let i = 120; i >= 1; i--) {
    names.forEach((nm, id) => {
      const rec = new Uint8Array(19), dv = new DataView(rec.buffer);
      rec[0] = 0x02; dv.setBigInt64(1, BigInt(nowMs - i * 30000), true); dv.setUint16(9, id, true); dv.setFloat64(11, 40 + id * 10 + Math.sin(i / 5), true);
      parts.push(rec);
    });
  }
  /* a torn tail (half a record) must simply end the file */
  push([0x02, 1, 2, 3]);
  const total = parts.reduce((n, p) => n + p.length, 0), out = new Uint8Array(total);
  let off = 0; for (const p of parts) { out.set(p, off); off += p.length; }
  return out;
}

(async () => {
  await sleep(1300);
  /* 1. CSV streams through the export route */
  await setFormat("csv");
  await makeChart("RPM", 10800);
  let note = tile("RPM").querySelector(".chart-note").textContent;
  check("CSV: RPM chart backfilled 120 points through the export", /History from the logger \(CSV\): 120 points over 3 hours/.test(note) && Number(tile("RPM").querySelector(".chart").dataset.points) >= 120, note);

  /* 2. binary: the .wdl file is fetched whole and decoded here; it is the file being written -> gate paused + resumed */
  const wdlName = "dl_" + String(w.__mockState.logEpoch).padStart(10, "0") + ".wdl";
  w.__WDL_FIXTURE__ = wdlFixture(["Speed", "Coolant"], Date.now());
  await setFormat("binary", wdlName);
  const gate0 = w.__mockState.gateCalls;
  await makeChart("Speed", 10800, 3500);
  note = tile("Speed").querySelector(".chart-note").textContent;
  check("binary: Speed chart backfilled 120 points from the decoded .wdl file (torn tail ignored)", /History from the logger \(binary, 1 file\): 120 points over 3 hours/.test(note) && Number(tile("Speed").querySelector(".chart").dataset.points) >= 120, note);
  check("binary: the active file was read with the logger gate paused and resumed", w.__mockState.gateCalls - gate0 === 2, w.__mockState.gateCalls - gate0);
  /* the decoded file is cached: a second chart on the same file does not touch the gate again */
  await makeChart("Coolant", 900, 3000);
  note = tile("Coolant").querySelector(".chart-note").textContent;
  const m15 = note.match(/History from the logger \(binary, 1 file\): (\d+) points over 15 min/);
  check("binary: second parameter served from the decoded-file cache (15 min window: ~30 points, no new gate calls)", !!m15 && Number(m15[1]) >= 26 && Number(m15[1]) <= 31 && w.__mockState.gateCalls - gate0 === 2, [note, w.__mockState.gateCalls - gate0]);

  /* 3. SQLite without the reader: the tile offers to install it */
  await setFormat("sqlite");
  await makeChart("gps_speed", 3600, 27000);   /* under jsdom the script never loads: 12 s timeout per store, two stores */
  const n = tile("gps_speed").querySelector(".chart-note");
  check("SQLite without sql.js: 'needs the SQLite reader' with an Install (690 KB) button", /needs the SQLite reader/.test(n.textContent) && !!n.querySelector("button") && /Install \(690 KB\)/.test(n.querySelector("button").textContent), n.textContent);
  check("no jsdom errors", errs.length === 0, errs.slice(0, 2));
  console.log(fails ? "PROBE: " + fails + " failure(s)" : "PROBE: all checks passed");
  w.close();
})().catch((e) => { console.error("PROBE ERROR", e.stack || e.message); process.exit(1); });
