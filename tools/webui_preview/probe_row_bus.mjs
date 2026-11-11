/* Probe (2026-11-11, TASK_vehicle_protocols.md phase 3, mockup board 8): the
   Bus field of a row on Automate > Parameters. A custom or vehicle-specific
   row can be asked on the car's OBD protocol (the default: no field in the
   file) or on MS, single-wire or CH CAN, at the bus's usual speed or at
   another one; the page says how the round goes (the rows of a bus together,
   the changes of bus), the Test button asks on the row's bus, and Apply
   sends `bus` and `bus_kbps` with the row and nothing for the car's own
   protocol. A standard PID and a J1939 group have no such field. Runs over
   the mock API.
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
  console.log(fails ? `PROBE FAILED (${fails})` : "ROW BUS PROBE PASS");
  process.exit(fails ? 1 : 0);
})();
