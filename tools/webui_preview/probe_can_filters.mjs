/* Probe (2026-11-14): the CAN filters list of Automate > Parameters, over the mock.

   Ali: "allow users to add filters in vehicle specific and in Custom pids", then
   from the mock-up: a separate CAN filters list under the PIDs of both tabs, the
   vehicle's filters editable and more can be added, the tab's switch covers its
   filters, Test for a filter, the search box says Search.

   A filter of the vehicle's set carries profile:true (what a profile brought, what
   is added on Vehicle Specific); every other one is a custom filter. The page's
   own key on a row is `name`; everything else a row carries is kept as stored.

   The probe's table: one standard, one custom and one vehicle-specific PID, the
   vehicle's filter SPEED (0x284) and a custom filter on a 29-bit ID with two
   parameters, one of them a multiplexed signal with a class and a range.

   On Custom PIDs:
     A  the search box says Search; the CAN filters list shows the custom filter
        alone, with its count, ID in hex, listen time; the note no longer sends
        the user to Raw Config; the info bubble explains a filter and names the
        tab's switch
     B  Add CAN filter: a row, opened, NewFilter, no ID yet (marked), 1000 ms,
        cycle 5000 (the 4.x firmware's figures); Unsaved edits; Test refuses
        without an ID and asks nothing
     C  the ID field as the 4.x page read it: 3b3 is hex (0x3B3), a plain 644
        is decimal (0x284), junk is marked and not kept; the listen
        time is held to 50..60000; a parameter is edited, added, removed
     D  Test: the request carries the filter (ID, listen time, the expressions,
        mux fields only where a parameter has them), the dialog shows the
        exchange, the frame data and the value
     E  the search box and Expand all / Collapse all drive both lists
     F  Apply: the new filter as edited and untagged, the stored custom filter
        byte for byte (its mux fields, class and range), the vehicle's untouched;
        a filter switched off goes as enabled:false; a removed one is gone
   On Vehicle Specific:
     G  the vehicle's filter alone, under its own name; its bubble names the
        vehicle-specific switch and the profile; an edit, a new name and a filter
        added there are sent tagged profile:true, the custom one untouched
   Elsewhere:
     H  a car that does not send the frame: the dialog says so, no value
     I  Standard PIDs has no CAN filters list
     J  a row in a shape the page does not know is shown and never rewritten
     K  no page error in the whole run
   usage: node probe_can_filters.mjs   (after make_preview.py; node >= 20) */
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import { JSDOM, VirtualConsole } from "jsdom";

const here = dirname(fileURLToPath(import.meta.url));
const html = readFileSync(join(here, "preview.html"), "utf-8");
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
let fails = 0, total = 0;
const check = (n, ok, extra) => { total++; console.log((ok ? "PASS " : "FAIL ") + n + (extra !== undefined ? "  [" + JSON.stringify(extra).slice(0, 500) + "]" : "")); if (!ok) { fails++; process.exitCode = 1; } };

const VEH = { frame_id: 644, monitor_ms: 500, period_ms: 0, group: "default", profile: true,
  parameters: [{ name: "SPEED", expression: "[B4:B5]/98", unit: "km/h", class: "speed" }] };
const CUS = { frame_id: 0x18DAF110, monitor_ms: 800, period_ms: 3000, group: "default", parameters: [
  { name: "DoorDriver", expression: "B0:0", unit: "" },
  { name: "MuxTemp", expression: "B2-40", unit: "°C", mux_expr: "B0&15", mux_val: 2, class: "temperature", min: -40, max: 215 }] };
const table = () => JSON.parse(JSON.stringify({
  version: 1,
  groups: [{ name: "default", enabled_default: true, period_ms: 1000 }],
  pids: [
    { name: "RPM", cmd: "010C1", group: "default", period_ms: 1000, type: "std", parameters: [{ name: "RPM", expression: "[B3:B4]/4", unit: "rpm" }] },
    { name: "OilTemp", cmd: "2101AF1", group: "default", period_ms: 2000, type: "custom", parameters: [{ name: "OilTemp", expression: "B4-40", unit: "°C" }] },
    { name: "SOH", cmd: "2161", group: "default", period_ms: 0, type: "specific", init: "", parameters: [{ name: "SOH", expression: "[B6:B7]/100", unit: "%" }] },
  ],
  filters: [VEH, CUS],
}));

function boot(preset) {
  const errs = [];
  const vc = new VirtualConsole();
  vc.on("jsdomError", (e) => errs.push(e.message + " " + (e.detail && e.detail.stack || "").slice(0, 300)));
  const dom = new JSDOM(html, {
    runScripts: "dangerously", url: "http://wican.local/#/automate", pretendToBeVisual: true, virtualConsole: vc,
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
  const $ = (sel, root) => (root || d()).querySelector(sel), $$ = (sel, root) => [...(root || d()).querySelectorAll(sel)];
  const fire = (el, type) => el.dispatchEvent(new w.Event(type, { bubbles: true }));
  const set = (el, v, type) => { el.value = v; fire(el, type || "change"); };
  const btn = (re, root) => $$("button", root).find((b) => re.test(b.textContent.trim()));
  const modalText = () => ($("#modal-root") || {}).textContent || "";
  const closeModal = () => { const b = $$("#modal-root .acts button").find((x) => /Close/.test(x.textContent)); if (b) b.click(); };
  const toasts = () => $$(".toast").map((t) => t.textContent);
  const clearToasts = () => { for (const t of $$(".toast")) t.remove(); };
  /* every PUT of the table, every test the page asked for */
  const puts = [], tests = [];
  const origFetch = w.fetch;
  w.fetch = (u, o) => {
    if (o && o.method === "PUT" && /\/api\/autopid\/config/.test(String(u))) puts.push(JSON.parse(o.body));
    if (o && o.method === "POST" && /\/api\/autopid\/test/.test(String(u))) tests.push(JSON.parse(o.body));
    return origFetch(u, o);
  };
  const tab = async (name) => { const b = $$(".seg button").find((x) => x.textContent === name); if (!b) return false; b.click(); await sleep(400); return true; };
  const apply = async () => {
    const before = puts.length;
    const all = $$("button.btn.pri").filter((b) => b.textContent === "Apply Configuration");
    if (!all.length) return false;
    all[all.length - 1].click(); await sleep(500);
    return puts.length === before + 1;
  };
  const fl = () => $(".fltl");                                   /* the tab's CAN filters list */
  const rows = () => $$(".fltl .pidrow");
  const rowOf = (re) => rows().find((r) => re.test(($(".fname", r) || {}).value || ""));
  const count = () => (($(".fltl .pidl-tools .help") || {}).textContent || "");
  const dirty = () => $$(".chip.warn").some((c) => /Unsaved edits/.test(c.textContent) && c.style.display !== "none");
  return { w, d, $, $$, fire, set, btn, errs, modalText, closeModal, toasts, clearToasts, puts, tests, tab, apply, fl, rows, rowOf, count, dirty,
    lastPut: () => puts[puts.length - 1], M: () => w.__mockState, close: () => dom.window.close() };
}
const same = (a, b) => JSON.stringify(a) === JSON.stringify(b);

/* ================= Custom PIDs ================= */
{
  const p = boot({ autopidCfg: table() });
  const { $, $$ } = p;
  await sleep(2500);
  check("the Parameters tab offers Custom PIDs", await p.tab("Custom PIDs"));

  /* ---- A ---- */
  check("A: the search box says Search, not Filter", ($(".pidl-tools .flt") || {}).placeholder === "Search by name, request or parameter", ($(".pidl-tools .flt") || {}).placeholder);
  check("A: a CAN filters list under the PIDs, with its title", !!p.fl() && /^CAN filters/.test(($(".fltl .pidl-tools") || {}).textContent || ""), ($(".fltl .pidl-tools") || {}).textContent);
  check("A: it shows the custom filter alone, and counts it", p.rows().length === 1 && p.count() === "1 CAN filter · 2 parameters", [p.rows().length, p.count()]);
  {
    const r = p.rows()[0];
    check("A: the row: the ID as its name (two parameters, no name given), the ID in hex, the listen time, the parameters",
      r && $(".fname", r).value === "0x18DAF110" && $(".fid", r).value === "0x18DAF110" && $(".fmon", r).value === "800" && /▸ 2 parameters/.test(r.textContent),
      r && [$(".fname", r).value, $(".fid", r).value, $(".fmon", r).value]);
    check("A: the column heads name what a filter has", /Name\s*CAN ID\s*Listen \(ms\)\s*Parameters/.test(($(".fltl .pidl-head") || {}).textContent || ""), ($(".fltl .pidl-head") || {}).textContent);
  }
  check("A: the note no longer sends the user to Raw Config for a filter", !/Passive CAN filters: Raw Config/.test(($("#view") || {}).textContent || ""));
  {
    const ib = $(".fltl .ibtn");
    check("A: an info button beside the title", !!ib && ib.getAttribute("aria-label") === "About CAN filters");
    if (ib) ib.click(); await sleep(100);
    const bub = ($(".ibubble") || {}).textContent || "";
    check("A: the bubble explains a filter and names this tab's switch", /^CAN filter/.test(bub) && /a frame the car sends by itself: nothing is asked/.test(bub) && /B0 is the first data byte/.test(bub) && /Poll custom PIDs switch/.test(bub), bub.slice(0, 120));
    if (ib) ib.click(); await sleep(50);
    check("A: the button again closes it", !$(".ibubble"));
  }

  /* ---- B: a new filter ---- */
  const add = $(".flt-add");
  check("B: Add CAN filter offered beside Apply Configuration", !!add && /Add CAN filter/.test(add.textContent) && !!p.btn(/^Apply Configuration$/, add.parentNode));
  check("B: nothing unsaved before", !p.dirty());
  add.click(); await sleep(150);
  let nf = p.rowOf(/^NewFilter$/);
  check("B: a row NewFilter, opened", !!nf && nf.classList.contains("open") && !!$(".det", nf));
  check("B: no ID yet and the field marked, 1000 ms listen, cycle 5000 ms",
    nf && $(".fid", nf).value === "" && $(".fid", nf).classList.contains("bad") && $(".fmon", nf).value === "1000" && $$(".det .fields input", nf)[1].value === "5000",
    nf && [$(".fid", nf).value, $(".fmon", nf).value, $$(".det .fields input", nf).map((i) => i.value)]);
  check("B: one parameter NewFilter reading B0", nf && same($$(".det .prm:not(.prm-head) input:not([type=checkbox])", nf).map((i) => i.value), ["NewFilter", "B0", ""]),
    nf && $$(".det .prm:not(.prm-head) input:not([type=checkbox])", nf).map((i) => i.value));
  check("B: Unsaved edits shows, the count follows", p.dirty() && p.count() === "2 CAN filters · 3 parameters", p.count());
  p.clearToasts();
  p.btn(/^Test$/, nf).click(); await sleep(200);
  check("B: Test without an ID asks nothing and says why", p.tests.length === 0 && p.toasts().some((t) => /Enter the CAN ID first/.test(t)), p.toasts());

  /* ---- C: the fields ---- */
  p.set($(".fid", nf), "3b3"); await sleep(50);
  check("C: 3b3 becomes 0x3B3 and the mark goes", $(".fid", nf).value === "0x3B3" && !$(".fid", nf).classList.contains("bad"), $(".fid", nf).value);
  p.set($(".fid", nf), "644"); await sleep(50);
  check("C: a plain number is decimal, as on the 4.x page: 644 reads 0x284", $(".fid", nf).value === "0x284", $(".fid", nf).value);
  p.set($(".fid", nf), "xyz"); await sleep(50);
  check("C: junk is marked", $(".fid", nf).classList.contains("bad"));
  p.set($(".fid", nf), "0x3B3"); await sleep(50);
  p.set($(".fmon", nf), "20"); await sleep(50);
  const lo = $(".fmon", nf).value;
  p.set($(".fmon", nf), "99999"); await sleep(50);
  const hi = $(".fmon", nf).value;
  p.set($(".fmon", nf), "250"); await sleep(50);
  check("C: the listen time is held to 50..60000", lo === "50" && hi === "60000" && $(".fmon", nf).value === "250", [lo, hi]);
  {
    const inp = () => $$(".det .prm:not(.prm-head)", p.rowOf(/^NewFilter$/));
    p.set($$("input:not([type=checkbox])", inp()[0])[1], "B1*2");
    p.set($$("input:not([type=checkbox])", inp()[0])[2], "V");
    p.btn(/Add parameter/, p.rowOf(/^NewFilter$/)).click(); await sleep(150);
    check("C: Add parameter adds a row", inp().length === 2);
    p.set($$("input:not([type=checkbox])", inp()[1])[0], "Second");
    p.set($$("input:not([type=checkbox])", inp()[1])[1], "B2");
    $$("button.danger", inp()[1])[0].click(); await sleep(150);
    check("C: its remove button takes it out again", inp().length === 1 && $$("input:not([type=checkbox])", inp()[0])[1].value === "B1*2");
  }
  nf = p.rowOf(/^NewFilter$/);

  /* ---- D: Test ---- */
  p.btn(/^Test$/, nf).click(); await sleep(400);
  check("D: the test carries the filter: the ID as a number, the listen time, the expression",
    p.tests.length === 1 && same(p.tests[0], { filter: { frame_id: 947, monitor_ms: 250, parameters: [{ expression: "B1*2" }] } }), p.tests[0]);
  {
    const t = p.modalText();
    check("D: the dialog: the title, the exchange, the frame, its data and the value",
      /^Test: NewFilter/.test(t) && /the frame came after 38 ms/.test(t) && /> ATCRA3B3/.test(t) && /> ATMA/.test(t) && /< 3B3 02 1E 02 1F 0B 7C 4E 20/.test(t) &&
      /Frame data as the expressions count it \(B0 is the first data byte\): 02 1E 02 1F 0B 7C 4E 20/.test(t) && /DecodedNewFilter30V/.test(t), t.slice(0, 400));
    p.closeModal(); await sleep(100);
  }
  p.btn(/^Test$/, p.rowOf(/^0x18DAF110$/)).click(); await sleep(400);
  check("D: a multiplexed parameter takes its mux fields along, a plain one does not",
    p.tests.length === 2 && same(p.tests[1], { filter: { frame_id: 0x18DAF110, monitor_ms: 800, parameters: [{ expression: "B0:0" }, { expression: "B2-40", mux_expr: "B0&15", mux_val: 2 }] } }), p.tests[1]);
  check("D: its frame is shown with the 29-bit ID", /< 18DAF110 02 1E/.test(p.modalText()), p.modalText().slice(0, 200));
  p.closeModal(); await sleep(100);

  /* ---- E: the search box and the two buttons drive both lists ---- */
  {
    const box = $(".pidl-tools .flt");
    p.set(box, "door", "input"); await sleep(150);
    check("E: a search for a parameter's name leaves its filter, and no PID", p.rows().length === 1 && $(".fid", p.rows()[0]).value === "0x18DAF110" && / · 1 shown/.test(p.count()) && $$(".pidl:not(.fltl) .pidrow").length === 0,
      [p.rows().length, p.count(), $$(".pidl:not(.fltl) .pidrow").length]);
    p.set(box, "3b3", "input"); await sleep(150);
    check("E: a search for an ID finds the filter with it", p.rows().length === 1 && $(".fname", p.rows()[0]).value === "NewFilter");
    p.set(box, "zzz", "input"); await sleep(150);
    check("E: nothing found is said", /No CAN filter matches the search/.test(p.fl().textContent) && /No PID matches the search/.test($("#view").textContent));
    p.set(box, "", "input"); await sleep(150);
    p.btn(/^Collapse all$/).click(); await sleep(150);
    const shut = $$(".fltl .pidrow.open").length;
    p.btn(/^Expand all$/).click(); await sleep(150);
    check("E: Collapse all and Expand all take the filters along", shut === 0 && $$(".fltl .pidrow.open").length === 2 && $$(".pidl:not(.fltl) .pidrow.open").length === 1,
      [shut, $$(".fltl .pidrow.open").length]);
    p.btn(/^Collapse all$/).click(); await sleep(150);
  }

  /* ---- F: Apply ---- */
  check("F: Apply PUTs the table once", await p.apply());
  {
    const f = p.lastPut().filters || [];
    check("F: the vehicle's filter and the stored custom filter are byte for byte as they were (mux fields, class, range, no name added)", same(f[0], VEH) && same(f[1], CUS), f.slice(0, 2));
    check("F: the new filter as edited, a custom one (no profile tag)",
      f.length === 3 && same(f[2], { name: "NewFilter", frame_id: 947, monitor_ms: 250, period_ms: 5000, group: "default", parameters: [{ name: "NewFilter", expression: "B1*2", unit: "V" }] }), f[2]);
    check("F: the PIDs went as they were", same(p.lastPut().pids, table().pids));
    check("F: Unsaved edits is gone", !p.dirty());
  }
  $("input[type=checkbox]", p.rowOf(/^NewFilter$/)).click(); await sleep(100);
  check("F: a filter switched off greys its row", p.rowOf(/^NewFilter$/).classList.contains("off") && p.dirty());
  check("F: and goes as enabled:false", await p.apply() && p.lastPut().filters[2].enabled === false, p.lastPut().filters[2]);
  $$("button.danger", $(".main", p.rowOf(/^NewFilter$/)))[0].click(); await sleep(150);
  check("F: the remove button takes the filter out, the count follows", p.rows().length === 1 && p.count() === "1 CAN filter · 2 parameters", p.count());
  check("F: and Apply sends the two that are left", await p.apply() && same(p.lastPut().filters, [VEH, CUS]), p.lastPut().filters);
  /* a second new filter gets a parameter name no row has */
  $(".flt-add").click(); await sleep(100); $(".flt-add").click(); await sleep(150);
  check("F: two new filters do not share a parameter name", !!p.rowOf(/^NewFilter$/) && !!p.rowOf(/^NewFilter_2$/));

  /* ---- I ---- */
  await p.tab("Standard PIDs");
  check("I: Standard PIDs has no CAN filters list and no Add CAN filter", !p.fl() && !$(".flt-add"));

  check("K: no page error on Custom PIDs", p.errs.length === 0, p.errs.slice(0, 3));
  p.close();
}

/* ================= Vehicle Specific ================= */
{
  const p = boot({ autopidCfg: table() });
  const { $, $$ } = p;
  p.M().dash.SPEED = 30;      /* the poller's value for the vehicle's filter */
  await sleep(2500);
  check("the Parameters tab offers Vehicle Specific", await p.tab("Vehicle Specific"));
  await sleep(2700);          /* one look at the live values */

  /* ---- G ---- */
  check("G: the vehicle's filter alone", p.rows().length === 1 && p.count() === "1 CAN filter · 1 parameter", [p.rows().length, p.count()]);
  let r = p.rows()[0];
  check("G: under its parameter's name, ID 0x284, 500 ms", r && $(".fname", r).value === "SPEED" && $(".fid", r).value === "0x284" && $(".fmon", r).value === "500",
    r && [$(".fname", r).value, $(".fid", r).value, $(".fmon", r).value]);
  check("G: its live value beside the parameter count", r && /km\/h/.test(($(".val", r) || {}).textContent || "") && !$(".val", r).classList.contains("none"), r && $(".val", r).textContent);
  {
    $(".fltl .ibtn").click(); await sleep(100);
    const bub = ($(".ibubble") || {}).textContent || "";
    check("G: the bubble names the vehicle-specific switch and the profile", /Poll vehicle-specific PIDs switch, and choosing another profile replaces them/.test(bub), bub.slice(-160));
    $(".fltl .ibtn").click(); await sleep(50);
  }
  $(".caret", r).click(); await sleep(150);
  r = p.rows()[0];
  p.set($$(".det .prm:not(.prm-head) input:not([type=checkbox])", r)[1], "[B4:B5]/100");
  p.set($(".fname", r), "Road speed");
  $(".flt-add").click(); await sleep(150);
  const nf = p.rowOf(/^NewFilter$/);
  check("G: a filter can be added here too", !!nf && p.count() === "2 CAN filters · 2 parameters", p.count());
  p.set($(".fid", nf), "1DA"); await sleep(50);
  check("G: Apply PUTs the table once", await p.apply());
  {
    const f = p.lastPut().filters || [];
    check("G: the vehicle's filter goes with the edit and its new name, still tagged",
      same(f[0], { ...VEH, parameters: [{ ...VEH.parameters[0], expression: "[B4:B5]/100" }], name: "Road speed" }), f[0]);
    check("G: the custom filter is untouched", same(f[1], CUS), f[1]);
    check("G: the filter added here is the vehicle's: tagged profile:true",
      f.length === 3 && same(f[2], { name: "NewFilter", frame_id: 474, monitor_ms: 1000, period_ms: 5000, group: "default", parameters: [{ name: "NewFilter", expression: "B0", unit: "" }], profile: true }), f[2]);
  }
  /* the name emptied: the row goes back to its parameter's name, the key goes */
  p.set($(".fname", p.rowOf(/^Road speed$/)), ""); await sleep(50);
  check("G: an emptied name drops the key", await p.apply() && !("name" in p.lastPut().filters[0]), p.lastPut().filters[0]);
  check("K: no page error on Vehicle Specific", p.errs.length === 0, p.errs.slice(0, 3));
  p.close();
}

/* ================= a car that does not send the frame ================= */
{
  const p = boot({ autopidCfg: table(), filterTest: "silent" });
  await sleep(2500);
  await p.tab("Vehicle Specific");
  p.btn(/^Test$/, p.rows()[0]).click(); await sleep(400);
  const t = p.modalText();
  check("H: the dialog says no frame came, in the exchange and beside it", /! no frame with CAN ID 284 in 500 ms/.test(t) && /no frame with this CAN ID in the listen time/.test(t), t.slice(0, 300));
  check("H: no arrival time, no frame data, no value", !/the frame came after/.test(t) && !/Frame data as/.test(t) && /DecodedSPEED-km\/h/.test(t), t.slice(-80));
  check("K: no page error with a silent car", p.errs.length === 0, p.errs.slice(0, 3));
  p.close();
}

/* ================= a row in a shape the page does not know ================= */
{
  /* the mock's own table: a filter in a flat shape (id as text, no parameters list) */
  const p = boot({});
  const { $ } = p;
  await sleep(2500);
  await p.tab("Custom PIDs");
  const before = JSON.parse(JSON.stringify(p.M().autopidCfg.filters));
  const r = p.rows()[0];
  check("J: it is shown: its name, no ID the page can read (marked), no parameters", r && $(".fname", r).value === "SOC_BMS" && $(".fid", r).value === "" && $(".fid", r).classList.contains("bad") && /▸ 0 parameters/.test(r.textContent),
    r && [$(".fname", r).value, $(".fid", r).value]);
  $(".caret", r).click(); await sleep(150);
  $(".flt-add").click(); await sleep(150);
  check("J: Apply sends it as it was stored, beside the new one", await p.apply() && same(p.lastPut().filters[0], before[0]) && p.lastPut().filters.length === 2, p.lastPut().filters[0]);
  check("K: no page error with that row", p.errs.length === 0, p.errs.slice(0, 3));
  p.close();
}

console.log(`${fails ? "CAN FILTERS PROBE FAIL" : "CAN FILTERS PROBE PASS"}: ${total - fails}/${total}`);
