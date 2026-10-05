/* Probe (2026-10-05, TASK_crash_note.md): a boot that follows a crash carries the
   restart tracker's crash note in its history record. The Status page must say where
   the device last crashed in one row ("Last crash"), only when there is a note, and
   open the details (the firmware's own summary line and the note as JSON, to copy
   into a bug report) on request. Runs over the mock API: the mock's newest record is
   __mockState.lastRestart.
   Since the same evening the newest distinct crash is also kept in flash
   (TASK_crash_note.md section 19): __mockState.crashReport is that report. After a
   power cut it is what the row shows; `Report` opens the firmware's own text with
   Copy, Download and Clear; and a start after a crash park names itself. */
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
  runScripts: "dangerously", url: "http://wican.local/#/status",
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
const $$ = (sel) => [...d().querySelectorAll(sel)];
const nav = (hash) => { w.location.hash = hash; w.dispatchEvent(new w.Event("hashchange")); };
const row = (label) => {
  const dt = $$("#view dl.kv dt").find((e) => e.textContent.trim() === label);
  return dt && dt.nextElementSibling ? dt.nextElementSibling : null;
};
const rowVal = (label) => { const e = row(label); return e ? e.textContent.trim() : null; };
const reload = async () => { nav("#/power"); await sleep(300); nav("#/status"); await sleep(900); };

/* the panic of 2026-10-05 02:06:56, as the firmware reports it */
const NOTE = {
  summary: "exception LoadProhibited at 0x42209640 (address 0x0000003c), core 1, task \"apid_scan\", up 18 s, image 76a961dd5f08aabb",
  kind: "exception", reason: "LoadProhibited", cause: 28, pc: "0x42209640", excvaddr: "0x0000003c", core: 1,
  task: "apid_scan", in_isr: false, uptime_s: 18, text: "",
  backtrace: ["0x4220963d", "0x4220685d", "0x42206805", "0x420ed8f5", "0x420ec6a5", "0x420ec945", "0x4204e1c1"],
  backtrace_more: false, backtrace_corrupt: false, other_core: [], nested: false,
  elf_sha: "76a961dd5f08aabb", same_image: true, complete: true,
};
const PANIC = { reason: "panic", planned: false, planned_reason: "none", source: "unknown" };

(async () => {
  await sleep(900);
  check("no Last crash row while no record carries a note", row("Last crash") === null, rowVal("Last crash"));

  w.__mockState.lastRestart = { ...PANIC, crash: NOTE };
  await reload();
  check("the row says what crashed and where, in words", rowVal("Last crash") === "LoadProhibited in task apid_scan · boot 42 Details", rowVal("Last crash"));
  check("the row sits under Unexpected resets", (() => {
    const dts = $$("#view dl.kv dt").map((e) => e.textContent.trim());
    return dts.indexOf("Last crash") === dts.indexOf("Unexpected resets") + 1;
  })());
  check("the wake-up row still names the reset", rowVal("Last wake-up") === "Crash (panic), unexpected", rowVal("Last wake-up"));

  row("Last crash").querySelector("a").click();
  await sleep(150);
  const modal = d().querySelector("#modal-root .modal");
  check("Details opens a modal", !!modal && modal.querySelector("h3").textContent === "Last crash");
  const text = modal ? modal.textContent : "";
  check("the modal shows the firmware's own summary line", text.includes(NOTE.summary));
  check("the modal says the firmware is the one running", text.includes("This firmware is the one running now."));
  const pre = modal && modal.querySelector("pre");
  let parsed = null;
  try { parsed = JSON.parse(pre.textContent); } catch { /* checked below */ }
  check("the details are the note as JSON, with what a report needs",
    !!parsed && parsed.boot === 42 && parsed.reset === "panic" && parsed.running_elf_sha === "76a961dd5f08aabb"
    && typeof parsed.firmware === "string" && parsed.firmware.length > 0
    && parsed.crash.backtrace.length === 7 && parsed.crash.pc === "0x42209640", parsed && Object.keys(parsed));
  const labels = modal ? [...modal.querySelectorAll(".acts button")].map((b) => b.textContent) : [];
  check("the modal offers Copy details and Close", labels.join("|") === "Copy details|Close", labels);
  check("no long dash in the row or the modal", !/—/.test(rowVal("Last crash") + text.replace(pre ? pre.textContent : "", "")));
  if (modal) modal.querySelectorAll(".acts button")[1].click();
  await sleep(100);
  check("Close closes it", !d().querySelector("#modal-root .modal"));

  const cases = [
    [{ ...NOTE, kind: "abort", reason: "", text: "assert failed: twai_node_get_info twai.c:412 (node)", task: "httpd" },
      "assert failed: twai_node_get_info twai.c:412 (node) in task httpd · boot 42 Details"],
    [{ ...NOTE, kind: "int_wdt", reason: "Interrupt wdt timeout on CPU0", task: "can_rx" }, "Interrupt watchdog in task can_rx · boot 42 Details"],
    [{ ...NOTE, task: "" }, "LoadProhibited · boot 42 Details"],
    [{ ...NOTE, complete: false, reason: "", task: "", backtrace: [] }, "Crash at 0x42209640, details not recorded · boot 42 Details"],
  ];
  for (const [note, want] of cases) {
    w.__mockState.lastRestart = { ...PANIC, crash: note };
    await reload();
    check("row for " + (note.complete ? note.kind + (note.task ? "" : " without a task") : "an unfinished note"), rowVal("Last crash") === want, rowVal("Last crash"));
  }

  w.__mockState.lastRestart = { ...PANIC, crash: { ...NOTE, same_image: false, elf_sha: "0011223344556677" } };
  await reload();
  row("Last crash").querySelector("a").click();
  await sleep(150);
  const m2 = d().querySelector("#modal-root .modal");
  check("a note of another firmware says so", !!m2 && m2.textContent.includes("This was another firmware than the one running now."));
  if (m2) m2.querySelectorAll(".acts button")[1].click();

  /* ---- the crash report kept in flash (2026-10-05): what is left after a power
     cut, and the text a user sends on (GET /api/restart/report) ---- */
  const POWERON = { reason: "poweron", planned: false, planned_reason: "none", source: "unknown" };
  const REPORT = { stored_time: 1767229261, time_valid: true, firmware: "v6.00p_alfa-01", streak: 3, parked: true, crash: NOTE };
  const day = new w.Date(REPORT.stored_time * 1000).toLocaleDateString();
  w.__mockState.lastRestart = POWERON;
  w.__mockState.crashReport = { ...REPORT };
  await reload();
  check("after a power cut the row is the stored report, with its date", rowVal("Last crash") === "LoadProhibited in task apid_scan · " + day + " Report", rowVal("Last crash"));
  check("one row only when the report is all there is", row("Stored crash report") === null);
  row("Last crash").querySelector("a").click();
  await sleep(200);
  const m3 = d().querySelector("#modal-root .modal");
  check("Report opens the stored report", !!m3 && m3.querySelector("h3").textContent === "Crash report");
  const pre3 = m3 && m3.querySelector("pre");
  const t3 = pre3 ? pre3.textContent : "";
  check("it is the firmware's own text", t3.startsWith("WiCAN crash report\n") && t3.includes("Image:     76a961dd5f08aabb")
    && t3.includes("Backtrace: 0x4220963d 0x4220685d"), t3.slice(0, 80));
  check("the text and the help say the device parked itself", t3.includes("Loop:      3 crashes in a row; the device parked itself")
    && !!m3 && m3.querySelector("p.help").textContent.includes("After 3 crashes in a row the device parked itself"));
  const l3 = m3 ? [...m3.querySelectorAll(".acts button")].map((b) => b.textContent) : [];
  check("the modal offers Copy report, Download, Clear and Close", l3.join("|") === "Copy report|Download|Clear|Close", l3);
  check("no long dash in the report row or its modal", !/—/.test(rowVal("Last crash") + (m3 ? m3.textContent : "")));

  /* Clear asks first, then removes the report from the device and from the page */
  if (m3) m3.querySelectorAll(".acts button")[2].click();
  await sleep(150);
  const ask = d().querySelector("#modal-root .modal");
  check("Clear asks before it removes anything", !!ask && ask.textContent.includes("Remove the stored crash report from the device?")
    && !w.__mockState.crashReportClears);
  if (ask) [...ask.querySelectorAll(".acts button")].find((b) => b.textContent === "Continue").click();
  await sleep(900);
  check("Clear sends one DELETE and the row goes", w.__mockState.crashReportClears === 1 && row("Last crash") === null, rowVal("Last crash"));

  /* with the note of the same crash still in RAM: one row, both links */
  w.__mockState.lastRestart = { ...PANIC, crash: NOTE };
  w.__mockState.crashReport = { ...REPORT, parked: false, streak: 1 };
  await reload();
  check("the same crash in RAM and in flash is one row with both links",
    rowVal("Last crash") === "LoadProhibited in task apid_scan · boot 42 Details · Report" && row("Stored crash report") === null, rowVal("Last crash"));

  /* another crash in flash than the newest one in RAM: each has its row */
  w.__mockState.crashReport = { ...REPORT, time_valid: false, stored_time: 0, parked: false, streak: 1,
    crash: { ...NOTE, kind: "abort", reason: "", pc: "0x42001234", text: "abort() was called at PC 0x42001234 on core 0", task: "main" } };
  await reload();
  check("a different stored crash gets its own row", rowVal("Last crash") === "LoadProhibited in task apid_scan · boot 42 Details"
    && rowVal("Stored crash report") === "abort() was called at PC 0x42001234 on core 0 in task main · date not known Report",
    [rowVal("Last crash"), rowVal("Stored crash report")]);
  w.__mockState.crashReport = null;

  /* a start after a crash park names itself */
  w.__mockState.lastRestart = { reason: "software", planned: true, planned_reason: "park_retry", source: "button" };
  await reload();
  check("a start after a crash park says so, and how", rowVal("Last wake-up") === "Start after a crash park · button", rowVal("Last wake-up"));

  /* the restart history marks a boot that parked (System page) */
  w.__mockState.lastRestart = { ...PANIC, mode: "park" };
  nav("#/system"); await sleep(900);
  const histBtn = $$("#view button").find((b) => b.textContent.trim() === "Restart History");
  if (histBtn) histBtn.click();
  await sleep(300);
  const hm = d().querySelector("#modal-root .modal");
  const firstRow = hm ? hm.querySelector("tbody tr") : null;
  check("the restart history marks a parked boot", !!firstRow && /unexpected/.test(firstRow.textContent) && /parked/.test(firstRow.textContent)
    && !/parked/.test(hm.querySelectorAll("tbody tr")[1].textContent), firstRow && firstRow.textContent);
  if (hm) hm.querySelector(".acts button").click();

  w.__mockState.lastRestart = { reason: "software", planned: true, planned_reason: "config_apply", source: "config_server" };
  await reload();
  check("the row is gone again without a note", row("Last crash") === null, rowVal("Last crash"));
  check("no page errors", errs.length === 0, errs.slice(0, 2));
  console.log(fails ? `FAILED ${fails}` : "ALL PASS");
  process.exit(fails ? 1 : 0);
})();
