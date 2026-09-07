/* Probe for the Scripts page (2026-09-07): editor · examples · reference
   over the mock API. The engine starts disabled (banner, Run off); the
   stored script opens in the textarea editor (CodeMirror is not installed
   under jsdom: the page offers it); edit/save/rename/delete go through
   /api/fs; run/check output is rendered with jump-to-line links and the
   firmware's hints; examples open as new scripts; the reference inserts
   calls. A second pass with a CodeMirror stub checks the upgrade wiring. */
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
  runScripts: "dangerously", url: "http://wican.local/#/scripts/editor",
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
const btn = (txt, root) => [...(root || d()).querySelectorAll("button")].find((b) => b.textContent.trim() === txt);
const fire = (el, type) => el.dispatchEvent(new w.Event(type, { bubbles: true }));
const setText = (el, v) => { el.value = v; fire(el, "input"); };
const modalBtn = (re) => [...d().querySelectorAll("#modal-root .acts button")].find((b) => re.test(b.textContent));
const show = (tab) => { w.eval("subNav.show('" + tab + "')"); };
const ta = () => $(".scr-ed textarea");
const files = () => w.__mockState.files;
const status = () => ($(".scr-status") || {}).textContent || "";
const rows = () => $$("#view tr.scr-row");
const rowBtn = (txt, i) => btn(txt, rows()[i || 0]);
const tb = (txt) => btn(txt, $(".scr-bar"));   /* the editor toolbar (the table rows have Run/Delete buttons too) */
const out = () => ($(".scr-out") || {}).textContent || "";

(async () => {
  await sleep(1800);
  /* 1. engine off: banner, Run disabled, list, editor offer, settings switch only */
  check("engine off: the banner explains scripts save but do not run", /Scripting is off/.test(($(".banner.warn") || {}).textContent || ""));
  const run = tb("Run");
  check("engine off: Run is disabled with an explanatory title", !!run && run.disabled && /Scripting is off/.test(run.title), run && run.title);
  check("stored script listed as a table row with its size and actions", rows().length === 1 && /hello/.test(rows()[0].textContent) && /B/.test(rows()[0].textContent) && ["Open", "Run", "Download", "Delete"].every((t) => !!btn(t, rows()[0])), rows().map((x) => x.textContent));
  check("the code editor is offered for install (210 KB) and a plain textarea works meanwhile", /Install \(210 KB\)/.test(($(".installbox") || {}).textContent || "") && !!ta());
  const enRow = $('.frow[data-key="enabled"]'), rtRow = $('.frow[data-key="max_runtime_ms"]');
  check("settings tab: only the enable switch shows while scripting is off", !!enRow && enRow.style.display !== "none" && !!rtRow && rtRow.style.display === "none" && $$(".subtab").some((a) => a.dataset.id === "settings"));

  /* 2. open the stored script */
  rowBtn("Open").click(); await sleep(300);
  check("Open fills the editor, the name and the card header; the row is highlighted", ta().value === files()["/data/scripts/hello.be"] && $(".scr-name input").value === "hello" && status() === "Saved" && rows()[0].classList.contains("on") && /hello\.be/.test($("#view .section .legend .ldesc").textContent), [ta().value.slice(0, 20), status()]);

  /* 3. edit + save */
  setText(ta(), ta().value + "log('edited')\n"); await sleep(50);
  check("editing marks the script unsaved", status() === "Unsaved changes", status());
  tb("Save").click(); await sleep(400);
  check("Save uploads the file through /api/fs and clears the flag", /log\('edited'\)/.test(files()["/data/scripts/hello.be"] || "") && status() === "Saved", status());

  /* 4. rename = save under the new name, old file removed */
  setText($(".scr-name input"), "hello2"); tb("Save").click(); await sleep(400);
  check("renaming saves under the new name and removes the old file", !!files()["/data/scripts/hello2.be"] && !files()["/data/scripts/hello.be"] && rows().length === 1 && /hello2/.test(rows()[0].textContent), Object.keys(files()));

  /* 5. enable the engine (mock) and reload the page */
  w.__MOCK_SETTINGS__.script_engine.values.enabled = true;
  w.location.hash = "#/status"; await sleep(500); w.location.hash = "#/scripts/editor"; await sleep(1800);
  check("engine on: no banner, Run enabled", !$(".banner.warn") && !tb("Run").disabled);

  /* 6. run the stored script from its row (opens it, then runs) */
  rowBtn("Run").click(); await sleep(600);
  check("a row's Run opens the script and shows the output with the elapsed time", $(".scr-name input").value === "hello2" && /hello from the mock/.test(out()) && /finished in/.test(out()) && w.__mockState.runs === 1, out().replace(/\s+/g, " ").slice(0, 80));

  /* 7. a runtime error: red lines, a jump-to-line link, the firmware's hint */
  setText(ta(), "log('a')\nlog('b')\nraise 'my_error', 'boom'\n"); tb("Run").click(); await sleep(400);
  const link = $(".scr-out a");
  check("error output: ERROR line flagged, traceback line linked", $$(".scr-out .er").length >= 2 && !!link && link.textContent === "string:3:", $$(".scr-out .er").map((x) => x.textContent));
  check("the matching hint from the reference shows under the output", /Hint: A raise in the script/.test(($(".scr-hint") || {}).textContent || ""));
  const t = ta(); t.setSelectionRange(0, 0); link.click(); await sleep(50);
  check("clicking the line link selects that line in the editor", t.selectionStart === "log('a')\nlog('b')\n".length && t.selectionEnd === t.selectionStart + "raise 'my_error', 'boom'".length, [t.selectionStart, t.selectionEnd]);

  /* 8. syntax check without running */
  setText(ta(), "var x = 1\nsyntax here\n"); tb("Check").click(); await sleep(300);
  check("Check reports the syntax error with its line and the hint", /syntax_error: string:2:/.test(out()) && /Every block ends with end/.test(($(".scr-hint") || {}).textContent || "") && w.__mockState.checks === 1, out().slice(0, 60));
  setText(ta(), "var x = 1\nlog(str(x))\n"); tb("Check").click(); await sleep(300);
  check("Check on a clean script says so and runs nothing", /No syntax errors/.test(out()) && w.__mockState.runs === 2, out());

  /* 9. insert a call from the toolbar */
  const sel = $(".scr-bar select"); sel.value = "uds"; fire(sel, "change"); await sleep(50);
  check("Insert puts the binding's example line at the cursor", /var r = uds\(0x7E0, 0x7E8, '01 0C'\)/.test(ta().value) && sel.value === "");

  /* 10. Stop */
  tb("Stop").disabled = false; tb("Stop").click(); await sleep(200);
  check("Stop posts the kill switch", w.__mockState.stops === 1);

  /* 11. examples: cards and "open in the editor" */
  show("examples"); await sleep(100);
  const cards = $$(".xcard");
  check("examples: one card per example with its needs chip", cards.length === 2 && /Runs anywhere/.test(cards[0].textContent) && /Needs a vehicle/.test(cards[1].textContent), cards.map((c) => c.querySelector("h4").textContent));
  btn("Open in the editor", cards[1]).click(); await sleep(200);
  check("opening an example first asks to discard the unsaved editor text", /Unsaved changes/.test(($("#modal-root h3") || {}).textContent || ""));
  modalBtn(/Continue/).click(); await sleep(300);
  check("opening an example asks for a name, prefilled with its id", !!$("#modal-root input") && $("#modal-root input").value === "vin");
  modalBtn(/Create/).click(); await sleep(500);
  check("the example becomes a new, unsaved script in the editor", /Read the VIN/.test(ta().value) && $(".scr-name input").value === "vin" && /not saved/i.test(status()) && $$(".subtab.on")[0].dataset.id === "editor", status());

  /* 12. reference: groups, search, insert */
  show("reference"); await sleep(100);
  check("reference: every binding with signature, doc and example", $$(".ref-item").length === 4 && $$(".ref-sig").some((x) => x.textContent === "obd_request(hexreq[, timeout_ms])") && $$(".prim > div").length === 2);
  const search = $('input[placeholder^="filter"]'); setText(search, "obd_request"); await sleep(50);
  check("reference search filters the list", $$(".ref-item").length === 1);
  btn("Insert", $(".ref-item")).click(); await sleep(100);
  check("reference Insert adds the line and returns to the editor", /obd_request\('22 F1 90'\)/.test(ta().value) && $$(".subtab.on")[0].dataset.id === "editor");
  check("the rule recipe names the action and the done event", /script\.run/.test($("#view").textContent) && /script\.done/.test($("#view").textContent));

  /* 13. delete from the row (the open example stays in the editor) */
  rowBtn("Delete").click(); await sleep(100); modalBtn(/Continue/).click(); await sleep(300);
  check("a row's Delete removes the file and shows the empty state", !files()["/data/scripts/hello2.be"] && rows().length === 0 && /No scripts yet/.test($("#view").textContent), Object.keys(files()));

  /* 14. the CodeMirror path with a stub: upgrade wiring only */
  const calls = { mode: null, from: 0, setValue: [] };
  w.CodeMirror = { defineSimpleMode: (n) => { calls.mode = n; }, fromTextArea: (ta0, opts) => { calls.from++; calls.opts = opts; let v = ta0.value; return {
    getValue: () => v, setValue: (x) => { v = x; calls.setValue.push(x.slice(0, 12)); }, on() {}, focus() {}, replaceSelection: (x) => { v += x; }, setCursor() {}, addLineClass() {}, removeLineClass() {},
    getCursor: () => ({ line: 0, ch: 0 }), getTokenAt: () => ({ string: "", start: 0 }), getRange: () => "", somethingSelected: () => false, state: {} }; } };
  files()["/data/scripts/again.be"] = "log('again')\n";
  w.location.hash = "#/status"; await sleep(400); w.location.hash = "#/scripts/editor"; await sleep(1800);
  check("with CodeMirror present: no install offer, the Berry mode and the wican theme are set, the textarea is upgraded", !$(".installbox") && calls.mode === "berry" && calls.from === 1 && calls.opts.mode === "berry" && calls.opts.theme === "wican" && !!calls.opts.extraKeys["Ctrl-S"]);
  rowBtn("Open").click(); await sleep(300);
  check("opening a script goes through the CodeMirror instance", calls.setValue.includes("log('again')"), calls.setValue);

  check("no jsdom errors", errs.length === 0, errs.slice(0, 2));
  console.log(fails ? "PROBE: " + fails + " failure(s)" : "PROBE: all checks passed");
  w.close();
})().catch((e) => { console.error("PROBE ERROR", e.stack || e.message); process.exit(1); });
