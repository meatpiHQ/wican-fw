/* Interaction probe for the Dashboard customisation (2026-09-06): edit
   mode, the per-tile dialog (widget, range, warnings, decimals, width,
   hide), the dial / bar / number / chart widgets, drag reordering, the
   layout file round trip through the fs API and Reset layout. Runs
   against preview.html (mock API) under jsdom with a stub uPlot — the
   real library and its install flow are covered by the device flow. */
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
    /* stub of the chart library: records the points it was given */
    w.uPlot = class { constructor(o, d, el) { this.el = el; const c = w.document.createElement("div"); c.className = "u-stub"; el.append(c); this.setData(d); }
      setData(d) { this.el.dataset.points = String(d[0].length); } setSize() {} destroy() { this.el.replaceChildren(); } };
  },
});
const w = dom.window, d = () => w.document;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
let fails = 0;
const check = (n, ok, extra) => { console.log((ok ? "PASS " : "FAIL ") + n + (extra !== undefined ? "  [" + JSON.stringify(extra) + "]" : "")); if (!ok) { fails++; process.exitCode = 1; } };
const shown = (el) => { if (!el) return false; for (let e = el; e && e !== d().body; e = e.parentElement) { if (e.hidden || (e.style && e.style.display === "none")) return false; } return true; };
const tiles = () => [...d().querySelectorAll("#view .dtile")];
const tile = (name) => tiles().find((t) => t.querySelector(".tk").textContent.replace(/hidden$/, "") === name);
const names = () => tiles().map((t) => t.dataset.name);
const fire = (el, type) => el.dispatchEvent(new w.Event(type, { bubbles: true }));
const modalRoot = () => d().querySelector("#modal-root");
const modalBtn = (re) => [...modalRoot().querySelectorAll(".acts button")].find((b) => re.test(b.textContent));
const layoutFile = async () => { const r = await w.fetch("/api/fs/download?path=/data/dashboard.json"); return r.ok ? JSON.parse(await r.text()) : null; };
const openGear = (name) => { tile(name).querySelector(".tgear").click(); };
const rowEl = (label) => [...modalRoot().querySelectorAll(".frow")].find((x) => x.querySelector("label").textContent === label);
const rowCtl = (label) => { const r = [...modalRoot().querySelectorAll(".frow")].find((x) => x.querySelector("label").textContent === label); return r && r.querySelector(".ctl"); };
const setSel = (el, v) => { el.value = v; fire(el, "change"); };

(async () => {
  await sleep(1300);
  check("five tiles, no layout file yet", tiles().length === 5 && (await layoutFile()) === null, names());
  const editBtn = [...d().querySelectorAll("#view .phead button")].find((b) => /Customise/.test(b.textContent));
  check("Customise button in the header", !!editBtn);
  editBtn.click(); await sleep(400);
  check("edit mode: grid flagged, edit bar with Reset layout + Done, a gear per tile", d().querySelector("#view .tiles").classList.contains("editing") && shown(d().querySelector("#view .dash-edit")) && /Reset layout/.test(d().querySelector("#view .dash-edit").textContent) && tiles().every((t) => t.querySelector(".tgear")) && tiles().every((t) => t.draggable));

  /* Coolant -> dial with a warning above 80 (value 88 -> warn) */
  openGear("Coolant"); await sleep(100);
  check("dialog: title + widget list + Automate range offered", /Coolant/.test(modalRoot().querySelector("h3").textContent) && rowCtl("Widget").querySelector("select").options.length === 6 && /From Automate \(-40 to 215\)/.test(rowCtl("Range").querySelector("select").options[0].textContent));
  setSel(rowCtl("Widget").querySelector("select"), "dial");
  rowCtl("Warn above").querySelector("input").value = "80";
  modalBtn(/^Save$/).click(); await sleep(900);
  let cool = tile("Coolant");
  check("Coolant is a dial: arc drawn, range labels, warn class since 88 > 80, amber zone arc", !!cool.querySelector("svg.dial") && cool.querySelector(".arc-v").getAttribute("d").startsWith("M") && cool.querySelector(".arc-w").getAttribute("d").startsWith("M") && cool.classList.contains("warn") && [...cool.querySelectorAll(".drange span")].map((x) => x.textContent).join("|") === "-40||215" && Math.abs(parseFloat(cool.querySelector(".dial text").textContent) - 88) < 1.5, cool.querySelector(".dial text").textContent);
  let lf = await layoutFile();
  check("layout saved on the device: Coolant dial + warnHi 80", lf && lf.tiles.Coolant && lf.tiles.Coolant.w === "dial" && lf.tiles.Coolant.warnHi === 80, lf && lf.tiles);

  /* Speed -> number only, two columns, 1 decimal */
  openGear("Speed"); await sleep(100);
  setSel(rowCtl("Widget").querySelector("select"), "number"); setSel(rowCtl("Width").querySelector("select"), "2"); setSel(rowCtl("Decimals").querySelector("select"), "1");
  modalBtn(/^Save$/).click(); await sleep(900);
  const sp = tile("Speed");
  check("Speed: big number, two columns, one decimal, no bar / trend", sp.classList.contains("span2") && !!sp.querySelector(".tv.xl") && /^-?\d+\.\d\s*km\/h$/.test(sp.querySelector(".tv").textContent.trim()) && !sp.querySelector(".meter") && !sp.querySelector("svg.spark"), sp.querySelector(".tv").textContent);

  /* RPM -> bar with a custom range */
  openGear("RPM"); await sleep(100);
  setSel(rowCtl("Widget").querySelector("select"), "bar"); setSel(rowCtl("Range").querySelector("select"), "custom");
  rowCtl("Range min").querySelector("input").value = "500"; rowCtl("Range max").querySelector("input").value = "3000";
  modalBtn(/^Save$/).click(); await sleep(900);
  const rpm = tile("RPM");
  check("RPM: bar over the custom range 500 … 3,000, no trend", !!rpm.querySelector(".meter") && !rpm.querySelector("svg.spark") && [...rpm.querySelectorAll(".drange span")].map((x) => x.textContent).join("|") === "500||3,000" && parseFloat(rpm.querySelector(".meter i").style.width) > 5, [...rpm.querySelectorAll(".drange span")].map((x) => x.textContent));

  /* gps_speed -> chart (stub library) */
  openGear("gps_speed"); await sleep(100);
  check("chart option explains itself when the library is ready", (setSel(rowCtl("Widget").querySelector("select"), "chart"), /time-axis chart/i.test(rowEl("Widget").querySelector(".help").textContent)));
  modalBtn(/^Save$/).click(); await sleep(1800);
  const gps = tile("gps_speed");
  check("chart tile created through the library with the history so far", !!gps.querySelector(".chart .u-stub") && Number(gps.querySelector(".chart").dataset.points) >= 1, gps.querySelector(".chart").dataset.points);

  /* SOC_BMS -> hidden: faded with a badge in edit mode, gone once done */
  openGear("SOC_BMS"); await sleep(100);
  rowCtl("Visibility").querySelector("label.switch input").click();
  modalBtn(/^Save$/).click(); await sleep(900);
  check("hidden tile stays in edit mode, faded, with a 'hidden' badge", tile("SOC_BMS") && tile("SOC_BMS").classList.contains("hidden") && !!tile("SOC_BMS").querySelector(".hbadge"));

  /* drag RPM after Coolant (synthetic events: jsdom has no DnD) */
  fire(tile("RPM"), "dragstart"); fire(tile("Coolant"), "dragover"); fire(tile("RPM"), "dragend"); await sleep(1000);
  lf = await layoutFile();
  check("reorder saved: RPM now after Coolant", names().indexOf("RPM") === names().indexOf("Coolant") + 1 && lf && lf.order.indexOf("RPM") === lf.order.indexOf("Coolant") + 1, lf && lf.order);

  /* Done -> hidden tile disappears; Customise again -> badge unhides */
  [...d().querySelectorAll("#view .dash-edit button")].find((b) => /^Done$/.test(b.textContent)).click(); await sleep(900);
  check("Done: SOC_BMS no longer shown, order kept", !tile("SOC_BMS") && names().indexOf("RPM") === names().indexOf("Coolant") + 1 && !d().querySelector("#view .tiles").classList.contains("editing"), names());

  /* reload the page: the layout comes back from the device file */
  w.location.hash = "#/status"; await sleep(700); w.location.hash = "#/dashboard"; await sleep(1800);
  cool = tile("Coolant");
  check("after a reload the layout persists (dial, order, hidden tile)", !!cool && !!cool.querySelector("svg.dial") && !tile("SOC_BMS") && names().indexOf("RPM") === names().indexOf("Coolant") + 1 && tile("Speed").classList.contains("span2"), names());

  /* reset */
  [...d().querySelectorAll("#view .phead button")].find((b) => /Customise/.test(b.textContent)).click(); await sleep(400);
  tile("SOC_BMS").querySelector(".hbadge").click(); await sleep(900);
  check("badge unhides the tile", tile("SOC_BMS") && !tile("SOC_BMS").classList.contains("hidden"));
  [...d().querySelectorAll("#view .dash-edit button")].find((b) => /Reset layout/.test(b.textContent)).click(); await sleep(150);
  modalBtn(/Continue|OK|Yes|Reset/).click(); await sleep(1200);
  check("Reset layout: file deleted, tiles back to automatic", (await layoutFile()) === null && !tile("Coolant").querySelector("svg.dial") && !!tile("Coolant").querySelector("svg.spark") && tiles().length === 5, names());
  check("no jsdom errors", errs.length === 0, errs.slice(0, 2));
  console.log(fails ? "PROBE: " + fails + " failure(s)" : "PROBE: all checks passed");
  w.close();
})().catch((e) => { console.error("PROBE ERROR", e.stack || e.message); process.exit(1); });
