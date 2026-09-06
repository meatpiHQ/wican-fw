/* Interaction probe for the reworked Logger page (2026-09-06): status
   chips from the real card state, per-stream settings cards with
   progressive disclosure, the guided CAN filter, retention totals, the
   log-files list and the Files deep link. Runs against preview.html
   (mock API) under jsdom — no device needed. */
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
  runScripts: "dangerously", url: "http://wican.local/#/logger",
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
/* jsdom has no layout: visibility = no display:none / hidden up the tree */
const shown = (el) => { if (!el) return false; for (let e = el; e && e !== d().body; e = e.parentElement) { if (e.hidden || (e.style && e.style.display === "none")) return false; } return true; };
const cards = () => [...d().querySelectorAll("#view .section")];
const cardByTitle = (t) => cards().find((c) => ((c.querySelector("h3") || {}).textContent || "").trim() === t);
const row = (key) => d().querySelector('#view .frow[data-key="' + key + '"]');
const fire = (el, type) => el.dispatchEvent(new w.Event(type, { bubbles: true }));
const setSel = (el, v) => { el.value = v; fire(el, "change"); };
/* page-script consts (store, renderSubmit) are not window properties under jsdom */

(async () => {
  await sleep(1200);
  const titles = cards().map((c) => ((c.querySelector("h3") || {}).textContent || "").trim());
  check("cards: Status · Logger settings · Vehicle parameters · CAN frames · Log files (" + titles.join(" · ") + ")",
    JSON.stringify(titles) === JSON.stringify(["Status", "Logger settings", "Vehicle parameters", "CAN frames", "Log files"]));
  const status = cardByTitle("Status");
  const chips = [...status.querySelectorAll(".lg-chips .chip")].map((c) => c.textContent.trim());
  check("status chips: Off + SD card with GB capacity", chips[0] === "Off" && /^SD card · 320\.0 KB of 2\.83 GB used$/.test(chips[1] || ""), chips);
  check("status says logging is off, no dash-filled table", /Logging is off/.test(status.textContent) && !status.querySelector(".kv"));
  check("no Pause button while the logger is off", !/Pause/.test(status.textContent) && /Open folder/.test(status.textContent));
  check("stream cards hidden while the logger is off", !shown(cardByTitle("Vehicle parameters")) && !shown(cardByTitle("CAN frames")));
  const files = cardByTitle("Log files");
  const names = [...files.querySelectorAll("tbody tr td:first-child .mono")].map((e) => e.textContent);
  /* the mock's newest file is a fresh JSON-lines params log (its name carries the current epoch) */
  const legacy = ["dl_1784563543.db", "can_1784563543.wdl", "dl_1784329639.jsonl", "can_1783689511.asc"];
  check("log files listed newest first: the four fresh ones, then the legacy set", names.length === 8 && names.slice(0, 4).every((n) => /^dl_\d{10}\.(jsonl|csv|wdl|db)$/.test(n)) && JSON.stringify(names.slice(4)) === JSON.stringify(legacy), names);
  const dbRow = [...files.querySelectorAll("tbody tr")].find((tr) => tr.querySelector(".mono").textContent === "dl_1784563543.db");
  const cells = [...dbRow.querySelectorAll("td")].map((e) => e.textContent.trim());
  check("file row: stream + format + date + size", cells[1] === "Parameters" && cells[2] === "SQLite" && /\d{4}/.test(cells[3]) && cells[4] === "28.0 KB", cells);
  check("download link per file, none marked in use", files.querySelectorAll('a[download]').length === 8 && !/in use/.test(files.textContent));

  /* enable: the stream cards appear, nothing selected yet */
  row("enabled").querySelector("label.switch input").click(); await sleep(50);
  check("enabling shows the stream cards", shown(cardByTitle("Vehicle parameters")) && shown(cardByTitle("CAN frames")));
  const nothing = cardByTitle("Logger settings").querySelector(".banner.warn");
  check("'nothing selected' nudge while both streams are off", shown(nothing) && /Nothing is selected/.test(nothing.textContent));
  check("advanced writer knobs hidden behind the toggle", !shown(row("batch_rows")) && !shown(row("flush_ms")) && /Show advanced/.test(cardByTitle("Logger settings").textContent));

  /* parameters */
  setSel(row("autopid_log").querySelector("select"), "changed"); await sleep(30);
  check("picking a parameter mode clears the nudge", !shown(nothing));
  check("mode help paints live", /changes/.test(row("autopid_log").querySelector(".help").textContent));
  setSel(row("format").querySelector("select"), "binary"); await sleep(30);
  check("format help follows the selection", /wdl_dump/.test(row("format").querySelector(".help").textContent));
  const parRet = cardByTitle("Vehicle parameters").querySelector('.frow[data-key="x_ret"] .help');
  check("retention total under the size rows", /^Up to 400 MB \(100 × 4 MB\)/.test(parRet.textContent), parRet.textContent);
  setSel(row("autopid_log").querySelector("select"), "off"); await sleep(30);

  /* CAN frames: switch, formats, the guided filter */
  const canCard = cardByTitle("CAN frames");
  check("CAN rows hidden until the stream is on", !shown(row("can_format")) && !shown(row("x_mode")));
  row("can_log").querySelector("label.switch input").click(); await sleep(50);
  check("CAN stream on: format + 'Frames to record' shown, ID rows hidden", shown(row("can_format")) && shown(row("x_mode")) && !shown(row("x_id")) && !shown(row("x_mask")) && !shown(row("x_ext")));
  check("nudge cleared by the CAN stream too", !shown(nothing));
  const canFmtHelp = row("can_format").querySelector(".help");
  setSel(row("can_format").querySelector("select"), "sqlite"); await sleep(30);
  check("sqlite for CAN warns (amber help)", /Slow/.test(canFmtHelp.textContent) && canFmtHelp.style.color !== "");
  setSel(row("can_format").querySelector("select"), "mf4"); await sleep(30);
  check("mf4 help mentions the tooling + fresh file per boot", /asammdf/.test(canFmtHelp.textContent) && canFmtHelp.style.color === "");
  check("CAN size rows relabelled without the CAN prefix", row("can_max_file_mb").querySelector("label").textContent === "Max file size (MB)" && row("can_max_files").querySelector("label").textContent === "Files kept");
  const canRet = canCard.querySelector('.frow[data-key="x_ret"] .help');
  check("CAN retention total", /^Up to 400 MB \(50 × 8 MB\)/.test(canRet.textContent), canRet.textContent);
  const modeSel = row("x_mode").querySelector("select"), extSel = row("x_ext").querySelector("select");
  const idIn = row("x_id").querySelector("input"), maskIn = row("x_mask").querySelector("input"), matchHelp = row("x_mask").querySelector(".help");
  check("filter mode defaults to all frames", modeSel.value === "all" && w.eval("store").pending.get("data_logger").can_filter === "");
  setSel(modeSel, "filter"); await sleep(30);
  check("matching-IDs mode reveals ID length / ID / mask", shown(row("x_ext")) && shown(row("x_id")) && shown(row("x_mask")));
  check("ID prefilled, mask default, single-ID explanation", idIn.value === "7E8" && maskIn.value === "7FF" && matchHelp.textContent === "Records ID 0x7E8 only.", [idIn.value, maskIn.value, matchHelp.textContent]);
  setSel(extSel, "29"); await sleep(30);
  check("29-bit swaps the default mask + example", maskIn.value === "1FFFFFFF" && /18DAF110/.test(idIn.placeholder) && w.eval("store").pending.get("data_logger").can_ext === true, [maskIn.value, idIn.placeholder]);
  setSel(extSel, "11"); await sleep(30);
  check("back to 11-bit restores the 7FF mask", maskIn.value === "7FF" && w.eval("store").pending.get("data_logger").can_ext === false);
  maskIn.value = "7F8"; fire(maskIn, "change"); await sleep(30);
  check("mask 7F8 spells out the 8-ID range", matchHelp.textContent === "Records the 8 IDs where (ID AND mask) = 0x7E8: 0x7E8 to 0x7EF.", matchHelp.textContent);
  idIn.value = "7e0zz9"; fire(idIn, "input"); await sleep(10);
  check("typed ID is sanitised to 3 hex digits, upper case", idIn.value === "7E0", idIn.value);
  fire(idIn, "change"); await sleep(30);
  check("staged filter follows", w.eval("store").pending.get("data_logger").can_filter === "7E0" && /0x7E0 to 0x7E7/.test(matchHelp.textContent), matchHelp.textContent);
  idIn.value = ""; fire(idIn, "change"); await sleep(30);
  check("blank ID explained (all frames), rows stay put", /Blank ID/.test(matchHelp.textContent) && shown(row("x_id")));
  setSel(modeSel, "all"); await sleep(30);
  check("all frames clears the filter and hides the ID rows", w.eval("store").pending.get("data_logger").can_filter === "" && !shown(row("x_id")));
  setSel(modeSel, "filter"); await sleep(30);
  check("switching back remembers the last ID", idIn.value === "7E0");
  /* the advanced toggle must not resurrect hidden filter rows */
  setSel(modeSel, "all"); await sleep(30);
  [...canCard.querySelectorAll("button")].find((b) => /Show advanced/.test(b.textContent)).click(); await sleep(30);
  check("Show advanced reveals the frame buffer but not the filter rows", shown(row("ring_len")) && !shown(row("x_id")));
  /* CAN bus off warning from a staged can_manager change */
  const canOff = canCard.querySelector(".banner.warn");
  /* the warning follows the staged can_manager switch when there is one, else the device's setting */
  w.eval("store").pending.set("can_manager", { enabled: true }); setSel(modeSel, "all"); await sleep(30);
  check("no CAN-bus-off warning while the bus is (staged) on", !shown(canOff));
  w.eval("store").pending.set("can_manager", { enabled: false }); setSel(modeSel, "all"); await sleep(30);
  check("CAN-bus-off warning with a link to Settings → CAN bus", shown(canOff) && canOff.querySelector('a[href="#/settings/can"]'));
  w.eval("store").pending.delete("can_manager"); setSel(modeSel, "all"); await sleep(30);
  const busOnMock = !!(w.__MOCK_SETTINGS__ && w.__MOCK_SETTINGS__.can_manager && w.__MOCK_SETTINGS__.can_manager.values.enabled);
  check("without a staged change the warning mirrors the device setting (bus " + (busOnMock ? "on" : "off") + ")", shown(canOff) === !busOnMock);
  /* disabling hides everything but the switch again */
  row("enabled").querySelector("label.switch input").click(); await sleep(50);
  check("disabling hides the stream cards and the nudge", !shown(cardByTitle("Vehicle parameters")) && !shown(cardByTitle("CAN frames")) && !shown(nothing));
  check("one component staged for submit", w.eval("store").count() === 1 && /Submit/.test(d().querySelector("#submitbtn").textContent));

  /* the Files deep link from "Open folder" */
  w.eval("store").pending.clear(); w.eval("store").dirty.clear(); w.eval("renderSubmit()");
  w.location.hash = "#/files/sd/logs"; await sleep(900);
  const crumb = d().querySelector("#view .rowflex") ? d().querySelector("#view .rowflex").textContent : "";
  check("#/files/sd/logs opens the log folder", /Storage.*sd.*logs/.test(crumb) && /dl_1784563543\.db/.test(d().querySelector("#view").textContent), crumb.slice(0, 40));
  check("no jsdom errors", errs.length === 0, errs.slice(0, 2));
  console.log(fails ? "PROBE: " + fails + " failure(s)" : "PROBE: all checks passed");
  w.close();
})().catch((e) => { console.error("PROBE ERROR", e.stack || e.message); process.exit(1); });
