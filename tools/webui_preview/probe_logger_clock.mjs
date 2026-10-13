/* The clock's history on the pages (2026-10-13, issue #984): the Logger
   card's clock line from /api/logger.clock + /api/rtc.clock, the Files
   table labelling files from the clock journal (clock not set / clock
   moved), the Clock journal button, the Status page's Clock row, the words
   for a boot without a clock (mock preset clockUnset), and the per-file
   re-base the dashboard history applies (journalShift / journalFromText
   on the mock's journal). Runs against preview.html (mock API) under
   jsdom: no device needed. Prints `LOGGER CLOCK PROBE PASS`. */
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import { JSDOM, VirtualConsole } from "jsdom";

const here = dirname(fileURLToPath(import.meta.url));
const html = readFileSync(join(here, "preview.html"), "utf-8");
let fails = 0;
const check = (n, ok, extra) => { console.log((ok ? "PASS " : "FAIL ") + n + (extra !== undefined ? "  [" + JSON.stringify(extra) + "]" : "")); if (!ok) { fails++; process.exitCode = 1; } };
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function boot(hash, preset) {
  const errs = [];
  const vc = new VirtualConsole();
  vc.on("jsdomError", (e) => errs.push(e.message + " " + (e.detail && e.detail.stack || "").slice(0, 300)));
  const dom = new JSDOM(html, {
    runScripts: "dangerously", url: "http://wican.local/" + hash,
    pretendToBeVisual: true, virtualConsole: vc,
    beforeParse(w) {
      w.matchMedia = () => ({ matches: false, addListener() {}, addEventListener() {} });
      w.scrollTo = () => {}; w.HTMLCanvasElement.prototype.getContext = () => null;
      if (preset) w.__mockPreset = preset;
    },
  });
  return { dom, w: dom.window, d: () => dom.window.document, errs };
}
const cards = (d) => [...d().querySelectorAll("#view .section")];
const cardByTitle = (d, t) => cards(d).find((c) => ((c.querySelector("h3") || {}).textContent || "").trim() === t);
const rowCells = (card, name) => { const tr = [...card.querySelectorAll("tbody tr")].find((r) => (r.querySelector(".mono") || {}).textContent === name); return tr ? [...tr.querySelectorAll("td")].map((e) => e.textContent.trim()) : null; };

(async () => {
  /* ---- the Logger page, a device with a clock --------------------------------- */
  let { w, d, errs } = boot("#/logger");
  await sleep(1200);
  const files = cardByTitle(d, "Log files");
  check("Log files card present", !!files);
  const unsynced = rowCells(files, "dl_1784329639.jsonl");
  check("a file the clock journal lists as unsynced says so in Started", !!unsynced && /^clock not set until \d/.test(unsynced[3]), unsynced && unsynced[3]);
  const asc = rowCells(files, "can_1783689511.asc");
  check("the ASC file open at the same time says so too", !!asc && /^clock not set until/.test(asc[3]), asc && asc[3]);
  const stepped = rowCells(files, "dl_1784563543.db");
  check("a file that saw a +2 h step shows its date and the step", !!stepped && /\d{4}/.test(stepped[3]) && /clock moved \+2 h$/.test(stepped[3]), stepped && stepped[3]);
  const plain = rowCells(files, "can_1784563543.wdl");
  check("the CAN file of that step is labelled the same way (journal, not the format)", !!plain && /clock moved \+2 h$/.test(plain[3]), plain && plain[3]);
  const fresh = [...files.querySelectorAll("tbody tr")].map((r) => [...r.querySelectorAll("td")].map((e) => e.textContent.trim())).find((c) => /^dl_\d{10}\.jsonl$/.test(c[0]) && !c[0].startsWith("dl_1784329639"));
  check("a file the journal does not mention keeps its plain date", !!fresh && /\d{4}/.test(fresh[3]) && !/clock/.test(fresh[3]), fresh && fresh[3]);
  const jbtn = files.closest(".section").querySelector('a[download="clock.jsonl"]');
  check("Clock journal download button on the Log files card", !!jbtn && /clock\.jsonl/.test(jbtn.getAttribute("href")) && /Clock journal/.test(jbtn.textContent));
  const status = cardByTitle(d, "Status");
  check("no clock line while logging is off", !status.querySelector(".lg-clock"));

  /* the logger running: the clock line */
  w.__mockState.loggerRunning = true;
  await sleep(4300);
  const line = cardByTitle(d, "Status").querySelector(".lg-clock");
  check("clock line: set by SNTP at the step's time, a step of +2 h, 2 files rotated",
    !!line && line.textContent === "Clock: set by SNTP at 2026-10-13 09:14:02 UTC, a step of +2 h this boot (2 files rotated by the clock).", line && line.textContent);

  /* the pure re-base rules the dashboard history uses, on the mock's journal
     (page-scope functions and constants are not window properties: evaluate
     inside the page) */
  const r = await w.eval(`(async()=>{const j=await loadClockJournal();return {
    n:j.size,
    un: journalShift(j,"dl_1784329639.jsonl",135000),
    unAfter: journalShift(j,"dl_1784329639.jsonl",1784329700500),
    db: journalShift(j,"dl_1784563543.db",1784563590000),
    dbAfter: journalShift(j,"dl_1784563543.db",1784563605000),
    dbOld: journalShift(j,"dl_1784563543.db",1784563500000),
    other: journalShift(j,"dl_1784570801.db",1784563590000),
    later: journalShift(j,"dl_1784570800.db",1784574000000),
    laterOld: journalShift(j,"dl_1784570800.db",1784570700000),
    words:[clockDelta(7200000),clockDelta(-2500),clockDelta(1784329560000),clockDelta(45000),clockDelta(90*60000)]};})()`);
  check("journal: the five files the mock's journal lists", r.n === 5, r.n);
  check("a later step of the same boot: its origin followed the +2 h step, the row moves -1 h", r.later === 1784574000000 - 3600000, r.later);
  check("a row older than that moved origin keeps its stamp", r.laterOld === 1784570700000, r.laterOld);
  check("an unsynced row is moved to the clock that was set (1970 + 135 s -> the sync)", r.un === 135000 + 1784329560000, r.un);
  check("a row after the step keeps its stamp", r.unAfter === 1784329700500, r.unAfter);
  check("a row before a +2 h step moves by +2 h", r.db === 1784563590000 + 7200000, r.db);
  check("a row after the step's ts_before keeps its stamp", r.dbAfter === 1784563605000, r.dbAfter);
  check("a row from before the boot (a resumed file) keeps its stamp", r.dbOld === 1784563500000, r.dbOld);
  check("a file the step does not list is untouched", r.other === 1784563590000, r.other);
  check("delta words: +2 h, -3 s, +57 years, +45 s, +90 min", JSON.stringify(r.words) === JSON.stringify(["+2 h", "-3 s", "+57 years", "+45 s", "+90 min"]), r.words);

  /* the Status page's Clock row */
  w.location.hash = "#/status"; await sleep(1400);
  const kvs = [...d().querySelectorAll("#view dl.kv dt")].map((dt) => [dt.textContent.trim(), (dt.nextElementSibling || {}).textContent || ""]);
  const clockRow = kvs.find((x) => x[0] === "Clock");
  check("Status page: the Clock row names the source and the last step", !!clockRow && /Z · set by SNTP, last moved \+2 h by SNTP$/.test(clockRow[1]), clockRow && clockRow[1]);
  check("no jsdom errors (device with a clock)", errs.length === 0, errs.slice(0, 2));
  w.close();

  /* ---- a boot without a clock ----------------------------------------------------- */
  ({ w, d, errs } = boot("#/status", { clockUnset: true }));
  await sleep(1400);
  const kv2 = [...d().querySelectorAll("#view dl.kv dt")].map((dt) => [dt.textContent.trim(), (dt.nextElementSibling || {}).textContent || ""]);
  const row2 = kv2.find((x) => x[0] === "Clock");
  check("Status page without a clock: not set, rows carry the uptime", !!row2 && row2[1] === "not set, waiting for an internet time sync (log rows carry the uptime since boot)", row2 && row2[1]);
  w.__mockState.loggerRunning = true;
  w.location.hash = "#/logger"; await sleep(1500);
  const line2 = cardByTitle(d, "Status").querySelector(".lg-clock");
  check("Logger card without a clock: the plain sentence", !!line2 && /^Clock: not set\. Rows carry the uptime since boot/.test(line2.textContent), line2 && line2.textContent);
  check("no jsdom errors (no clock)", errs.length === 0, errs.slice(0, 2));
  w.close();

  console.log(fails ? "LOGGER CLOCK PROBE FAIL: " + fails + " failure(s)" : "LOGGER CLOCK PROBE PASS");
})().catch((e) => { console.error("PROBE ERROR", e.stack || e.message); process.exit(1); });
