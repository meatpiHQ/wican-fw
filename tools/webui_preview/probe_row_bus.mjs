/* Probe (2026-11-11, TASK_vehicle_protocols.md phase 3, mockup board 8): the
   Bus field of a row on Automate > Parameters. A custom or vehicle-specific
   row can be asked on the car's OBD protocol (the default: no field in the
   file) or on MS, single-wire or CH CAN, at the bus's usual speed or at
   another one; the page says how the round goes (the rows of a bus together,
   the changes of bus), the Test button asks on the row's bus, and Apply
   sends `bus` and `bus_kbps` with the row and nothing for the car's own
   protocol. A standard PID and a J1939 group have no such field. A PID with
   a protocol preset in its init is shown switched off with the reason, a
   resting bus is marked on the round strip, and a preset in a TYPE's init
   (the setting) is kept as typed with a note under the field, the count of
   the PIDs that wait under the table and a banner on the dashboard. Runs
   over the mock API.
   usage: node probe_row_bus.mjs   (python make_preview.py first; npm i jsdom; node >= 20) */
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import { JSDOM, VirtualConsole } from "jsdom";

const here = dirname(fileURLToPath(import.meta.url));
const html = readFileSync(join(here, "preview.html"), "utf-8");
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
let fails = 0;
const check = (n, ok, extra) => { console.log((ok ? "PASS " : "FAIL ") + n + (extra !== undefined ? "  [" + JSON.stringify(extra) + "]" : "")); if (!ok) fails++; };

function boot(hash, preset) {
  const errs = [];
  const vc = new VirtualConsole();
  vc.on("jsdomError", (e) => errs.push(e.message + " " + ((e.detail && e.detail.stack) || "").slice(0, 300)));
  const dom = new JSDOM(html, {
    runScripts: "dangerously", url: "http://wican.local/" + hash,
    pretendToBeVisual: true, virtualConsole: vc,
    beforeParse(w) {
      w.__mockPreset = preset;
      w.matchMedia = () => ({ matches: false, addListener() {}, addEventListener() {} });
      w.scrollTo = () => {}; w.HTMLCanvasElement.prototype.getContext = () => null;
      if (!w.TextEncoder) { w.TextEncoder = TextEncoder; w.TextDecoder = TextDecoder; }
      w.Element.prototype.scrollIntoView = () => {};
    },
  });
  const w = dom.window;
  w.addEventListener("error", (e) => errs.push("window.onerror: " + e.message));
  w.addEventListener("unhandledrejection", (e) => errs.push("unhandledrejection: " + ((e.reason && e.reason.message) || e.reason)));
  const $ = (sel) => w.document.querySelector(sel), $$ = (sel) => [...w.document.querySelectorAll(sel)];
  const text = () => (($("#view") || {}).textContent || "").replace(/\s+/g, " ");
  const btn = (re, root) => $$((root || "#view") + " button").find((b) => re.test(b.textContent.trim()));
  const fire = (el, type) => el.dispatchEvent(new w.Event(type, { bubbles: true }));
  return { w, errs, $, $$, text, btn, fire, M: () => w.__mockState, S: () => w.__mockSettings };
}

(async () => {
  const cfg = { groups: [{ name: "default", enabled_default: true, period_ms: 1000 }],
    pids: [{ name: "EngineRPM", type: "std", cmd: "010C1", group: "default", period_ms: 0, parameters: [{ name: "EngineRPM", expression: "[B3:B4]/4", unit: "rpm" }] },
           { name: "Speed", type: "std", cmd: "010D1", group: "default", period_ms: 0, parameters: [{ name: "Speed", expression: "B3", unit: "km/h" }] },
           { name: "DoorAjar", type: "custom", cmd: "224101", init: "ATSH726", rxheader: "72E", group: "default", period_ms: 0, parameters: [{ name: "DoorAjar", expression: "B4", unit: "" }] },
           { name: "SeatHeat", type: "custom", cmd: "224102", group: "default", period_ms: 0, parameters: [{ name: "SeatHeat", expression: "B4", unit: "" }] },
           { name: "Heard", type: "custom", cmd: "PGN:FEEE", group: "default", period_ms: 1000, parameters: [{ name: "Coolant", expression: "B0-40", unit: "degC" }] }],
    filters: [] };
  const p = boot("#/automate/parameters", { autopidCfg: cfg });
  await sleep(1800);
  if (!/EngineRPM/.test(p.text())) { p.w.location.hash = "#/automate"; p.w.dispatchEvent(new p.w.Event("hashchange")); await sleep(1500); }

  const tab = async (re) => { const b = p.$$("#view .seg button").find((x) => re.test(x.textContent)); if (b) { b.click(); await sleep(600); } return !!b; };
  const rowOf = (cmd) => p.$$("#view .pidrow").find((r) => [...r.querySelectorAll("input.mono")].some((i) => i.value === cmd) || r.textContent.includes(cmd));
  const openRow = async (cmd) => { const r = rowOf(cmd); const c = r && !r.classList.contains("open") && r.querySelector(".caret"); if (c) { c.click(); await sleep(400); } return rowOf(cmd); };
  const field = (row, label) => { const d = row && [...row.querySelectorAll(".fields > div")].find((x) => (x.querySelector("label") || {}).textContent === label); return d ? d.querySelector("select, input") : null; };
  const strip = () => p.$$("#view .chip").map((c) => c.textContent.trim());
  const pick = async (sel, value) => { sel.value = value; p.fire(sel, "change"); await sleep(500); };

  /* ---- a standard PID: the car's OBD protocol by definition ---- */
  let row = await openRow("010C1");
  check("std: the row opens", !!row && row.classList.contains("open"));
  check("std: a standard PID has no Bus field", !!row && !field(row, "Bus"));

  /* ---- the custom tab ---- */
  check("custom: the tab is there", await tab(/Custom PIDs/));
  row = await openRow("224101");
  let bus = field(row, "Bus");
  check("custom: a row has a Bus field", !!bus && bus.tagName === "SELECT");
  const opts = bus ? [...bus.options].map((o) => o.value + "=" + o.textContent) : [];
  check("custom: the car's OBD protocol, MS CAN, single-wire CAN, CH CAN, with their pins",
    opts.length === 4 && /^obd=The car's OBD protocol \(pins 6 and 14\)$/.test(opts[0]) && /^ms=MS CAN \(125 kbit\/s, pins 3 and 11\)$/.test(opts[1]) &&
    /^sw=Single-wire CAN \(33\.3 kbit\/s, pin 1\)$/.test(opts[2]) && /^ch=CH CAN \(500 kbit\/s, pins 12 and 13\)$/.test(opts[3]), opts);
  check("custom: a row without the field is on the car's OBD protocol", !!bus && bus.value === "obd");
  check("custom: no speed and no note on the car's OBD protocol", !field(row, "Bus speed") && !/Rows on another bus are asked together/.test(row.textContent));
  check("custom: no round strip while every row is on the car's protocol", !strip().includes("this round") || p.$$("#view .rowflex").every((e) => e.style.display === "none" || !/this round/.test(e.textContent)));
  row = await openRow("PGN:FEEE");
  check("custom: a J1939 group has no Bus field (it is heard, not asked)", !!row && !field(row, "Bus"));

  /* ---- MS CAN ---- */
  row = await openRow("224101");
  bus = field(row, "Bus");
  if (bus) { await pick(bus, "ms"); }
  row = rowOf("224101");
  bus = field(row, "Bus");
  let speed = field(row, "Bus speed");
  check("ms: the row is on MS CAN", !!bus && bus.value === "ms");
  const sp = speed ? [...speed.options].map((o) => o.textContent) : [];
  check("ms: the speed is the usual one, with the others to choose", !!speed && speed.value === "0" && sp[0] === "Usual (125 kbit/s)" &&
    sp.includes("250 kbit/s") && sp.includes("500 kbit/s") && sp.includes("83.3 kbit/s") && sp.includes("95.2 kbit/s") && !sp.includes("125 kbit/s"), sp);
  check("ms: the note of mockup board 8", /Rows on another bus are asked together, one bus after the other, so a round changes bus once per bus and not once per row\. The choice is kept with the row on this device: nothing is stored in the OBD chip\./.test(row.textContent));
  let s = strip();
  check("ms: the round: 3 rows on the OBD protocol, 1 on MS CAN, 2 bus changes", s.includes("this round") && s.includes("OBD protocol: 3 rows") && s.includes("MS CAN: 1 row") && s.includes("2 bus changes"), s);
  check("ms: the edit is unsaved", s.some((x) => /Unsaved edits/.test(x)) || /Unsaved edits/.test(p.text()));

  /* ---- its speed, and a second bus ---- */
  if (speed) { await pick(speed, "250"); }
  row = await openRow("224102");
  bus = field(row, "Bus");
  if (bus) { await pick(bus, "sw"); }
  s = strip();
  check("sw: a second bus: 2 rows on the OBD protocol, MS CAN and single-wire CAN 1 each, 3 bus changes",
    s.includes("OBD protocol: 2 rows") && s.includes("MS CAN: 1 row") && s.includes("single-wire CAN: 1 row") && s.includes("3 bus changes"), s);
  check("sw: its usual speed is 33.3 kbit/s", ((field(rowOf("224102"), "Bus speed") || {}).options || [{}])[0].textContent === "Usual (33.3 kbit/s)");

  /* ---- Test asks on the row's bus ---- */
  row = rowOf("224101");
  const test = row && [...row.querySelectorAll("button")].find((b) => /^Test$/.test(b.textContent.trim()));
  if (test) { test.click(); await sleep(900); }
  const last = p.M().lastTest || {};
  check("test: the Test call carries the row's bus and speed", last.cmd === "224101" && last.bus === "ms" && last.bus_kbps === 250, { cmd: last.cmd, bus: last.bus, bus_kbps: last.bus_kbps });

  /* ---- Apply: the file ---- */
  const apply = p.btn(/^Apply Configuration$/) || p.btn(/Apply/);
  check("apply: the button is there", !!apply);
  if (apply) { apply.click(); await sleep(1200); }
  let saved = (p.M().autopidCfg || {}).pids || [];
  const by = (cmd) => saved.find((x) => x.cmd === cmd) || {};
  check("apply: the MS row is saved with its bus and speed", by("224101").bus === "ms" && by("224101").bus_kbps === 250, by("224101"));
  check("apply: the single-wire row with its bus alone", by("224102").bus === "sw" && !("bus_kbps" in by("224102")), by("224102"));
  check("apply: a standard row carries no bus", !("bus" in by("010C1")) && !("bus_kbps" in by("010C1")));

  /* ---- back to the car's protocol: nothing in the file ---- */
  row = await openRow("224101");
  bus = field(row, "Bus");
  if (bus) { await pick(bus, "obd"); }
  row = rowOf("224101");
  check("obd: the speed field is gone with the bus", !!row && !field(row, "Bus speed"));
  const apply2 = p.btn(/^Apply Configuration$/) || p.btn(/Apply/);
  if (apply2) { apply2.click(); await sleep(1200); }
  saved = (p.M().autopidCfg || {}).pids || [];
  check("obd: the row is saved without bus and speed", !("bus" in by("224101")) && !("bus_kbps" in by("224101")), by("224101"));
  if (test) { const t2 = [...rowOf("224101").querySelectorAll("button")].find((b) => /^Test$/.test(b.textContent.trim())); if (t2) { t2.click(); await sleep(900); } }
  check("obd: its Test call names no bus", !("bus" in (p.M().lastTest || {})) && (p.M().lastTest || {}).cmd === "224101", p.M().lastTest && { bus: p.M().lastTest.bus });

  /* ---- a profile that names the bus ---- */
  const conv = p.w.profileToPids && p.w.profileToPids({ car_model: "Test", bus: "ms", pids: [
    { pid: "224101", pid_init: "ATSH726", parameters: [{ name: "A", expression: "B4" }] },
    { pid: "224102", bus: "ch", bus_kbps: 250, parameters: [{ name: "B", expression: "B4" }] },
    { pid: "22F190", bus: "obd", parameters: [{ name: "C", expression: "B4" }] }] }, [], []);
  if (conv) {
    check("profile: the car's bus goes to its rows, a row's own wins, obd is no field",
      conv.pids[0].bus === "ms" && !("bus_kbps" in conv.pids[0]) && conv.pids[1].bus === "ch" && conv.pids[1].bus_kbps === 250 && !("bus" in conv.pids[2]),
      conv.pids.map((x) => [x.bus, x.bus_kbps]));
  } else {
    console.log("NOTE profileToPids is not reachable from the page scope: the profile import is not probed here");
  }

  check("no script error", p.errs.length === 0, p.errs.slice(0, 2));

  /* ---- a PID with a protocol preset in its init, as the firmware leaves a
     file an earlier build stored: switched off, and the page says why. A
     bus that rests after CAN ERRORs shows on the round strip. ---- */
  const cfg2 = { groups: [{ name: "default", enabled_default: true, period_ms: 1000 }],
    pids: [{ name: "MsDoor", type: "custom", cmd: "224101", init: "STP53;ATSH726", enabled: false, group: "default", period_ms: 0, parameters: [{ name: "MsDoor", expression: "B4", unit: "" }] },
           { name: "MsSeat", type: "custom", cmd: "224102", bus: "ms", group: "default", period_ms: 0, parameters: [{ name: "MsSeat", expression: "B4", unit: "" }] },
           { name: "Plain", type: "custom", cmd: "22F190", init: "STPBR 250000;ATSH7E0", group: "default", period_ms: 0, parameters: [{ name: "Plain", expression: "B4", unit: "" }] }],
    filters: [] };
  const q = boot("#/automate/parameters", { autopidCfg: cfg2 });
  await sleep(1800);
  if (!/MsDoor|Custom PIDs/.test(q.text())) { q.w.location.hash = "#/automate"; q.w.dispatchEvent(new q.w.Event("hashchange")); await sleep(1500); }
  const qtab = q.$$("#view .seg button").find((x) => /Custom PIDs/.test(x.textContent));
  if (qtab) { qtab.click(); await sleep(600); }
  const qrow = (cmd) => q.$$("#view .pidrow").find((r) => [...r.querySelectorAll("input.mono")].some((i) => i.value === cmd));
  const qopen = async (cmd) => { const r = qrow(cmd); const c = r && !r.classList.contains("open") && r.querySelector(".caret"); if (c) { c.click(); await sleep(400); } return qrow(cmd); };
  const qfield = (row, label) => { const d = row && [...row.querySelectorAll(".fields > div")].find((x) => (x.querySelector("label") || {}).textContent === label); return d ? d.querySelector("select, input") : null; };
  const qstrip = () => q.$$("#view .chip").map((c) => c.textContent.trim());
  const qapply = async () => { const b = q.btn(/^Apply Configuration$/) || q.btn(/Apply/); if (b) { b.click(); await sleep(1200); } };
  const qsaved = (cmd) => ((q.M().autopidCfg || {}).pids || []).find((x) => x.cmd === cmd) || {};

  let r2 = qrow("224101");
  check("preset: the PID is shown switched off, with the word on its row", !!r2 && r2.classList.contains("off") && /STP53: not sent/.test(r2.textContent), r2 && r2.textContent.slice(0, 80));
  check("preset: a bit rate word (STPBR) is no preset: no mark on that PID", !!qrow("22F190") && !/not sent/.test(qrow("22F190").textContent));
  r2 = await qopen("224101");
  check("preset: opened, the page says why and what to do",
    !!r2 && /STP53 is stored by the OBD chip every time it is sent/.test(r2.textContent) && /Take STP53 out of the PID init, choose the Bus this PID is on/.test(r2.textContent), r2 && r2.textContent.slice(-260));
  /* switched on with the word still there: Apply is refused with the reason */
  let sw2 = r2 && r2.querySelector(".main .switch input");
  if (sw2) { sw2.checked = true; q.fire(sw2, "change"); await sleep(300); }
  await qapply();
  check("preset: switched on with the word, Apply is refused and says why",
    /Rejected: pid MsDoor: STP53 is stored by the OBD chip; use the row's Bus/.test(q.text()) && qsaved("224101").enabled === false, q.text().match(/Rejected:[^.]{0,90}/));
  /* the way out: the word out of the init, the Bus chosen */
  r2 = await qopen("224101");
  const init2 = qfield(r2, "PID init (optional AT chain)");
  if (init2) { init2.value = "ATSH726"; q.fire(init2, "change"); await sleep(500); }
  r2 = await qopen("224101");
  check("preset: with the word out of the init the mark and the note are gone", !!r2 && !/not sent/.test(r2.textContent) && !/is stored by the OBD chip every time/.test(r2.textContent));
  const bus2 = qfield(r2, "Bus");
  if (bus2) { bus2.value = "ms"; q.fire(bus2, "change"); await sleep(500); }
  await qapply();
  check("preset: saved switched on, on MS CAN, without the preset",
    qsaved("224101").enabled === true && qsaved("224101").bus === "ms" && qsaved("224101").init === "ATSH726" && !/Rejected:/.test(q.text()), qsaved("224101"));

  /* a bus that rests: the mock's status says MS CAN is parked */
  check("rest: the strip counts the rows while the bus answers", qstrip().includes("MS CAN: 2 rows"), qstrip());
  q.M().buses = { now: "obd", changes: 12, obd: { ok: 40, failed: 0 }, ms: { ok: 0, failed: 3, parks: 1, parked: true, retry_s: 24 },
    sw: { ok: 0, failed: 0, parks: 0, parked: false, retry_s: 0 }, ch: { ok: 0, failed: 0, parks: 0, parked: false, retry_s: 0 } };
  await sleep(3200);
  check("rest: a resting bus is marked on the round strip", qstrip().some((c) => /^MS CAN: resting, no answer$/.test(c)), qstrip());
  check("rest: with the reason, what to check and when the next try comes",
    /MS CAN is resting: three requests in a row were not acknowledged\. The Bus speed of its PIDs is not the car's, or the car has no such bus\./.test(q.text()) && /The next try comes in 24 s\./.test(q.text()));
  q.M().buses.ms.retry_s = 9;
  await sleep(3000);
  check("rest: the countdown follows the unit", /The next try comes in 9 s\./.test(q.text()));
  q.M().buses.ms.parked = false; q.M().buses.ms.retry_s = 0;
  await sleep(3000);
  check("rest: over, the strip counts the rows again", qstrip().includes("MS CAN: 2 rows") && !/is resting/.test(q.text()), qstrip());
  check("no script error on the second page", q.errs.length === 0, q.errs.slice(0, 2));

  /* ---- a preset in a TYPE's init (the setting, as an earlier build stored
     it or as it is typed): kept as typed, never sent, and the PIDs of that
     type on the car's OBD protocol wait. The field says so, the table says
     what the device does now, a PID with its Bus chosen is not concerned. ---- */
  const cfg3 = { groups: [{ name: "default", enabled_default: true, period_ms: 1000 }],
    pids: [{ name: "EngineRPM", type: "std", cmd: "010C1", group: "default", period_ms: 0, parameters: [{ name: "EngineRPM", expression: "[B3:B4]/4", unit: "rpm" }] },
           { name: "DoorAjar", type: "custom", cmd: "224101", group: "default", period_ms: 0, parameters: [{ name: "DoorAjar", expression: "B4", unit: "" }] },
           { name: "SeatHeat", type: "custom", cmd: "224102", group: "default", period_ms: 0, parameters: [{ name: "SeatHeat", expression: "B4", unit: "" }] },
           { name: "Mirror", type: "custom", cmd: "224103", bus: "ms", group: "default", period_ms: 0, parameters: [{ name: "Mirror", expression: "B4", unit: "" }] },
           { name: "Heard", type: "custom", cmd: "PGN:FEEE", group: "default", period_ms: 1000, parameters: [{ name: "Coolant", expression: "B0-40", unit: "degC" }] }],
    filters: [] };
  const t = boot("#/automate/parameters", { autopidCfg: cfg3, autopid: { custom_init: "STP53;ATSH726" } });
  await sleep(1800);
  if (!/EngineRPM|Custom PIDs/.test(t.text())) { t.w.location.hash = "#/automate"; t.w.dispatchEvent(new t.w.Event("hashchange")); await sleep(1500); }
  const ttab = async (re) => { const b = t.$$("#view .seg button").find((x) => re.test(x.textContent)); if (b) { b.click(); await sleep(700); } return !!b; };
  const trow = (cmd) => t.$$("#view .pidrow").find((r) => [...r.querySelectorAll("input.mono")].some((i) => i.value === cmd));
  const topen = async (cmd) => { const r = trow(cmd); const c = r && !r.classList.contains("open") && r.querySelector(".caret"); if (c) { c.click(); await sleep(400); } return trow(cmd); };
  const tfield = (row, label) => { const d = row && [...row.querySelectorAll(".fields > div")].find((x) => (x.querySelector("label") || {}).textContent === label); return d ? d.querySelector("select, input") : null; };
  const shown = (sel) => t.$$("#view " + sel).filter((e) => e.style.display !== "none" && e.isConnected);
  check("held: no note on the standard table (its init is clean)", shown(".init-held").length === 0 && shown(".held-note").length === 0, shown(".held-note").map((e) => e.textContent.slice(0, 60)));
  await ttab(/Custom PIDs/);
  await sleep(2800);   /* one look at the device's state */
  const initRow = t.$$("#view .frow").find((r) => r.dataset && r.dataset.key === "custom_init");
  const initInp = initRow && initRow.querySelector(".ctl input");
  check("held: the init field keeps what was typed", !!initInp && initInp.value === "STP53;ATSH726", initInp && initInp.value);
  const fnote = initRow && initRow.querySelector(".init-held");
  check("held: the field says the word is not sent, what waits and the way out",
    !!fnote && fnote.style.display !== "none" && /^STP53 is stored by the OBD chip every time it is sent, so WiCAN does not send it, and does not ask the custom PIDs on the car's OBD protocol while it is here\. Take STP53 out of this field, and choose the Bus of the PIDs that are on MS CAN, single-wire CAN or CH CAN\.$/.test(fnote.textContent),
    fnote && fnote.textContent);
  let tnote = shown(".held-note")[0];
  check("held: the table counts the PIDs that wait: the two on the car's OBD protocol, not the one on MS CAN, not the J1939 group",
    !!tnote && /^2 PIDs of this table on the car's OBD protocol are not asked: the Custom PID init commands hold STP53, which the OBD chip stores every time it is sent, so WiCAN does not send it\./.test(tnote.textContent), tnote && tnote.textContent.slice(0, 200));
  /* a Test of a PID that waits is refused with the reason; one on MS CAN is asked */
  const tbtn = (cmd) => { const r = trow(cmd); return r && [...r.querySelectorAll("button")].find((b) => /^Test$/.test(b.textContent.trim())); };
  if (tbtn("224101")) { tbtn("224101").click(); await sleep(900); }
  check("held: a Test of a PID that waits is refused, and the page shows why",
    /Test failed: custom_init holds STP53, which the OBD chip stores every time it is sent/.test(t.w.document.body.textContent), (t.w.document.querySelector("#toasts") || {}).textContent);
  if (tbtn("224103")) { tbtn("224103").click(); await sleep(900); }
  check("held: a Test of the PID on MS CAN is asked", (t.M().lastTest || {}).cmd === "224103" && (t.M().lastTest || {}).bus === "ms" && /Test: Mirror/.test(t.w.document.body.textContent), t.M().lastTest && { cmd: t.M().lastTest.cmd, bus: t.M().lastTest.bus });
  const closeBtn = [...t.w.document.querySelectorAll("button")].find((b) => /^Close$/.test(b.textContent.trim()));
  if (closeBtn) { closeBtn.click(); await sleep(300); }
  /* the way out, one PID at a time: its Bus chosen, it no longer waits */
  let tr3 = await topen("224101");
  const tbus = tfield(tr3, "Bus");
  if (tbus) { tbus.value = "ms"; t.fire(tbus, "change"); await sleep(600); }
  tnote = shown(".held-note")[0];
  check("held: with the Bus of one chosen, one PID is left waiting", !!tnote && /^1 PID of this table on the car's OBD protocol is not asked: /.test(tnote.textContent), tnote && tnote.textContent.slice(0, 80));
  /* the word out of the field: the field's note goes at once; the table follows the device, which applies a setting at its restart */
  if (initInp) { initInp.value = "ATSH726"; t.fire(initInp, "input"); t.fire(initInp, "change"); await sleep(500); }
  check("held: with the word out of the field its note is gone", !!fnote && fnote.style.display === "none");
  check("held: the table still says what the device does until it restarts", shown(".held-note").length === 1);
  if (initInp) { initInp.value = "ATSH726;vtp 2c3"; t.fire(initInp, "input"); await sleep(300); }
  check("held: a vendor preset typed in is named as the chip reads it", !!fnote && fnote.style.display !== "none" && /^VTP2C3 is stored by the OBD chip/.test(fnote.textContent), fnote && fnote.textContent.slice(0, 60));
  if (initInp) { initInp.value = "ATSH726"; t.fire(initInp, "input"); t.fire(initInp, "change"); await sleep(300); }
  t.M().held = {};   /* the device after the restart with the saved setting */
  await sleep(3000);
  check("held: after the restart the table's note is gone", shown(".held-note").length === 0, shown(".held-note").map((e) => e.textContent.slice(0, 60)));
  check("no script error on the third page", t.errs.length === 0, t.errs.slice(0, 2));

  /* the dashboard says it too, while it happens */
  const d = boot("#/dashboard", { autopid: { custom_init: "STP53;ATSH726" } });
  await sleep(2500);
  check("held: the dashboard says which init holds which word, with the way to the page",
    /Some PIDs are not asked\. The Custom PID init commands hold STP53: the OBD chip stores such a word every time it is sent, so WiCAN does not send it, and does not ask the PIDs behind it on the car's OBD protocol\./.test(d.text()) &&
    !!d.$$("#view a").find((a) => a.getAttribute("href") === "#/automate/parameters"), d.text().match(/Some PIDs[^.]{0,80}/));
  d.M().held = {};
  await sleep(3500);
  check("held: nothing held, no banner", !/Some PIDs are not asked/.test(d.text()));
  check("no script error on the dashboard", d.errs.length === 0, d.errs.slice(0, 2));

  console.log(fails ? `PROBE FAILED (${fails})` : "ROW BUS PROBE PASS");
  process.exit(fails ? 1 : 0);
})();
