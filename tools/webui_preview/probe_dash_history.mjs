/* Interaction probe for the dashboard's logger history (2026-09-06): a
   chart tile backfills from /api/logger/export when the logger records
   parameters as JSON lines, explains itself otherwise, and the dialog
   offers the history window for charts. Mock API under jsdom with a stub
   uPlot that records the points it was given. */
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
      setData(d) { this.el.dataset.points = String(d[0].length); this.el.dataset.first = String(d[0][0] || ""); } setSize() {} destroy() { this.el.replaceChildren(); } };
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
const shown = (el) => !!el && el.style.display !== "none";
const makeChart = async (name, win) => {
  tile(name).querySelector(".tgear").click(); await sleep(100);
  setSel(rowEl("Widget").querySelector("select"), "chart");
  if (win) setSel(rowEl("History").querySelector("select"), String(win));
  [...modalRoot().querySelectorAll(".acts button")].find((b) => /^Save$/.test(b.textContent)).click(); await sleep(2200);
};

(async () => {
  await sleep(1300);
  /* the mock logger starts off -> a chart says why there is no history */
  [...d().querySelectorAll("#view .phead button")].find((b) => /Customise/.test(b.textContent)).click(); await sleep(300);
  tile("Speed").querySelector(".tgear").click(); await sleep(100);
  check("dialog: History row appears for charts only", !shown(rowEl("History")) && (setSel(rowEl("Widget").querySelector("select"), "chart"), shown(rowEl("History"))) && /1 hour/.test(rowEl("History").querySelector("select").selectedOptions[0].textContent));
  [...modalRoot().querySelectorAll(".acts button")].find((b) => /^Save$/.test(b.textContent)).click(); await sleep(2200);
  let note = tile("Speed").querySelector(".chart-note");
  check("logger off: the chart explains there is no history, with a link to Logger", /No history: the logger is off/.test(note.textContent) && !!note.querySelector('a[href="#/logger"]'), note.textContent);
  check("the chart still runs live (stub got the live points)", Number(tile("Speed").querySelector(".chart").dataset.points) >= 1);

  /* turn the mock logger on as JSON lines: the info is cached 30 s, so wait it out */
  Object.assign(w.__MOCK_SETTINGS__.data_logger.values, { enabled: true, format: "jsonl", autopid_log: "all" });
  console.log("   waiting 31 s for the history info cache to expire…");
  await sleep(31000);
  await makeChart("RPM", 10800);
  note = tile("RPM").querySelector(".chart-note");
  const pts = Number(tile("RPM").querySelector(".chart").dataset.points);
  check("logger on: RPM chart backfilled 120 points over 3 hours from the export", /History from the logger \(JSON lines\): 120 points over 3 hours/.test(note.textContent) && pts >= 120, [note.textContent, pts]);
  const first = Number(tile("RPM").querySelector(".chart").dataset.first);
  check("backfilled points are on the browser's clock (about an hour ago)", first > Date.now() / 1000 - 3700 && first < Date.now() / 1000 - 3500, [first, Date.now() / 1000]);
  const lf = await (async () => { const r = await w.fetch("/api/fs/download?path=/data/dashboard.json"); return r.ok ? JSON.parse(await r.text()) : null; })();
  check("history window saved with the tile", lf && lf.tiles.RPM && lf.tiles.RPM.w === "chart" && lf.tiles.RPM.win === 10800, lf && lf.tiles.RPM);
  /* a 15-minute window keeps only the recent part */
  await makeChart("Coolant", 900); await sleep(300);
  const cpts = Number(tile("Coolant").querySelector(".chart").dataset.points);
  check("15-minute window keeps 30 of the 120 logged points plus the live ones", cpts >= 30 && cpts <= 90, cpts);
  check("no jsdom errors", errs.length === 0, errs.slice(0, 2));
  console.log(fails ? "PROBE: " + fails + " failure(s)" : "PROBE: all checks passed");
  w.close();
})().catch((e) => { console.error("PROBE ERROR", e.stack || e.message); process.exit(1); });
