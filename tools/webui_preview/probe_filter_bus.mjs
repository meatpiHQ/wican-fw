/* Probe (2026-11-26, components pull request 15, GitHub issue #1078): the Bus
   field of a CAN filter on Automate > Parameters. A filter listens on the
   car's OBD protocol (the default: no field in the file) or on MS, single-wire
   or CH CAN, at the bus's usual speed or at another one, as a PID row asks
   there (probe_row_bus.mjs). Runs over the mock API.
     A  a filter without a bus, opened: the Bus field reads the car's OBD
        protocol, four choices, no Bus speed field
     B  single-wire CAN chosen: the Bus speed field comes with the bus's usual
        speed first, the table has unsaved edits
     C  Apply sends `bus` with the filter and no `bus_kbps`, everything else the
        row carried as stored (a multiplexed parameter's mux_expr / mux_val)
     D  Test listens on the filter's bus: the request carries `bus`
     E  another speed: Apply and Test carry `bus_kbps`
     F  another bus: the speed of the bus before it goes
     G  back on the car's OBD protocol: neither word is sent, the speed field
        is gone
     H  a stored filter on CH CAN at 250 kbit/s shows both
     I  a stored word that is no bus (`hs`, the filter switched off) reads as the
        car's OBD protocol and is sent back as stored while nobody touches it
     J  a filter of the vehicle's set has the field too
     K  a profile's filter brings its own `bus` and `bus_kbps`; the car's `bus`,
        which is where its PIDs are asked, is not taken for a filter
     L  the note on Pause mode names these filters
     M  no page error in the whole run
   usage: node probe_filter_bus.mjs   (after make_preview.py; node >= 20) */
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import { JSDOM, VirtualConsole } from "jsdom";

const here = dirname(fileURLToPath(import.meta.url));
const html = readFileSync(join(here, "preview.html"), "utf-8");
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
let fails = 0, total = 0;
const check = (n, ok, extra) => { total++; console.log((ok ? "PASS " : "FAIL ") + n + (extra !== undefined ? "  [" + JSON.stringify(extra).slice(0, 400) + "]" : "")); if (!ok) { fails++; process.exitCode = 1; } };

const table = () => JSON.parse(JSON.stringify({
  version: 1,
  groups: [{ name: "default", enabled_default: true, period_ms: 1000 }],
  pids: [{ name: "RPM", cmd: "010C1", group: "default", period_ms: 1000, type: "std", parameters: [{ name: "RPM", expression: "[B3:B4]/4", unit: "rpm" }] }],
  filters: [
    { name: "Climate", frame_id: 0x1026C0CB, monitor_ms: 400, period_ms: 1000, group: "default", parameters: [
      { name: "ClimatePower", expression: "[B1:B2]", unit: "W" },
      { name: "ClimateMode", expression: "B1", unit: "", mux_expr: "B0&15", mux_val: 2 }] },
    { name: "Gateway", frame_id: 0x321, monitor_ms: 300, period_ms: 1000, group: "default", bus: "ch", bus_kbps: 250,
      parameters: [{ name: "GatewayState", expression: "B0", unit: "" }] },
    { name: "Stored", frame_id: 0x120, monitor_ms: 300, period_ms: 1000, group: "default", bus: "hs", enabled: false,
      parameters: [{ name: "StoredOdo", expression: "B0", unit: "" }] },
    { name: "Road speed", frame_id: 644, monitor_ms: 500, period_ms: 0, group: "default", profile: true,
      parameters: [{ name: "SPEED", expression: "[B4:B5]/98", unit: "km/h" }] },
  ],
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
  const closeModal = () => { const b = $$("#modal-root .acts button").find((x) => /Close/.test(x.textContent)); if (b) b.click(); };
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
  const rowOf = (re) => $$(".fltl .pidrow").find((r) => re.test(($(".fname", r) || {}).value || ""));
  /* the filter's row with its detail shown */
  const open = async (re) => { let r = rowOf(re); if (r && !r.classList.contains("open")) { $(".caret", r).click(); await sleep(200); r = rowOf(re); } return r; };
  const bus = (re) => { const r = rowOf(re); return r ? $("select.fbus", r) : null; };
  const kbps = (re) => { const r = rowOf(re); return r ? $("select.fkbps", r) : null; };
  const dirty = () => $$(".chip.warn").some((c) => /Unsaved edits/.test(c.textContent) && c.style.display !== "none");
  const sent = (name) => ((puts[puts.length - 1] || {}).filters || []).find((f) => f.name === name) || {};
  return { w, $, $$, set, errs, closeModal, puts, tests, tab, apply, rowOf, open, bus, kbps, dirty, sent };
}

(async () => {
  const p = boot({ autopidCfg: table() });
  await sleep(1800);
  check("the Custom PIDs tab opens", await p.tab("Custom PIDs"));

  /* A */
  await p.open(/^Climate$/);
  let b = p.bus(/^Climate$/);
  check("A: a filter without a bus, opened, has a Bus field on the car's OBD protocol with four choices", !!b && b.value === "obd" && b.options.length === 4,
    b && { value: b.value, options: [...b.options].map((o) => o.textContent) });
  check("A: and no Bus speed field", !p.kbps(/^Climate$/));

  /* B */
  p.set(b, "sw"); await sleep(200);
  let k = p.kbps(/^Climate$/);
  check("B: single-wire CAN chosen: the Bus speed field comes, the usual speed first", !!k && k.value === "0" && /Usual \(33\.3 kbit\/s\)/.test(k.options[0].textContent),
    k && [...k.options].map((o) => o.textContent));
  check("B: the Bus field reads single-wire CAN, the table has unsaved edits", (p.bus(/^Climate$/) || {}).value === "sw" && p.dirty());

  /* C */
  check("C: Apply sends the table", await p.apply());
  let f = p.sent("Climate");
  check("C: the filter goes with bus sw and no bus_kbps", f.bus === "sw" && !("bus_kbps" in f), { bus: f.bus, bus_kbps: f.bus_kbps });
  check("C: and with what it carried (the ID, the listen time, the multiplexed parameter)",
    f.frame_id === 0x1026C0CB && f.monitor_ms === 400 && (f.parameters || []).length === 2 && f.parameters[1].mux_expr === "B0&15" && f.parameters[1].mux_val === 2, f);

  /* D */
  let r = p.rowOf(/^Climate$/);
  p.$$("button", r).find((x) => x.textContent.trim() === "Test").click(); await sleep(600);
  let t = (p.tests[p.tests.length - 1] || {}).filter || {};
  check("D: Test listens on the filter's bus: the request carries bus sw", t.bus === "sw" && !("bus_kbps" in t) && t.frame_id === 0x1026C0CB, t);
  p.closeModal(); await sleep(200);

  /* E */
  await p.open(/^Climate$/);
  p.set(p.kbps(/^Climate$/), "50"); await sleep(200);
  check("E: another speed: Apply sends it", await p.apply());
  f = p.sent("Climate");
  check("E: the filter goes with bus sw and bus_kbps 50", f.bus === "sw" && f.bus_kbps === 50, { bus: f.bus, bus_kbps: f.bus_kbps });
  r = p.rowOf(/^Climate$/);
  p.$$("button", r).find((x) => x.textContent.trim() === "Test").click(); await sleep(600);
  t = (p.tests[p.tests.length - 1] || {}).filter || {};
  check("E: and Test carries both", t.bus === "sw" && t.bus_kbps === 50, t);
  p.closeModal(); await sleep(200);

  /* F */
  await p.open(/^Climate$/);
  p.set(p.bus(/^Climate$/), "ms"); await sleep(200);
  k = p.kbps(/^Climate$/);
  check("F: another bus: the speed of the bus before it is gone, the field shows this bus's usual speed",
    !!k && k.value === "0" && /Usual \(125 kbit\/s\)/.test(k.options[0].textContent), k && k.options[0].textContent);
  await p.apply();
  f = p.sent("Climate");
  check("F: Apply sends bus ms and no bus_kbps", f.bus === "ms" && !("bus_kbps" in f), { bus: f.bus, bus_kbps: f.bus_kbps });

  /* G */
  await p.open(/^Climate$/);
  p.set(p.bus(/^Climate$/), "obd"); await sleep(200);
  check("G: back on the car's OBD protocol the Bus speed field is gone", !p.kbps(/^Climate$/));
  await p.apply();
  f = p.sent("Climate");
  check("G: and neither word is sent", f.name === "Climate" && !("bus" in f) && !("bus_kbps" in f), Object.keys(f));

  /* H */
  await p.open(/^Gateway$/);
  check("H: a stored filter on CH CAN at 250 kbit/s shows both", (p.bus(/^Gateway$/) || {}).value === "ch" && (p.kbps(/^Gateway$/) || {}).value === "250",
    { bus: (p.bus(/^Gateway$/) || {}).value, kbps: (p.kbps(/^Gateway$/) || {}).value });
  f = p.sent("Gateway");
  check("H: and went with them in every Apply", f.bus === "ch" && f.bus_kbps === 250, { bus: f.bus, bus_kbps: f.bus_kbps });

  /* I */
  await p.open(/^Stored$/);
  check("I: a stored word that is no bus reads as the car's OBD protocol", (p.bus(/^Stored$/) || {}).value === "obd" && !p.kbps(/^Stored$/));
  f = p.sent("Stored");
  check("I: and is sent back as stored while nobody touches the field", f.bus === "hs" && f.enabled === false, { bus: f.bus, enabled: f.enabled });

  /* J */
  check("J: the Vehicle Specific tab opens", await p.tab("Vehicle Specific"));
  await p.open(/^Road speed$/);
  b = p.bus(/^Road speed$/);
  check("J: a filter of the vehicle's set has the Bus field too", !!b && b.value === "obd" && b.options.length === 4);
  p.set(b, "ms"); await sleep(200);
  await p.apply();
  f = p.sent("Road speed");
  check("J: and goes with its bus, still one of the vehicle's", f.bus === "ms" && f.profile === true, { bus: f.bus, profile: f.profile });

  /* K */
  const car = { car_model: "Probe car", bus: "ms", pids: [{ pid: "224101", parameters: [{ name: "DoorAjar", expression: "B4", unit: "" }] }],
    filters: [{ frame_id: "0x1026C0CB", bus: "sw", parameters: [{ name: "HvacPower", expression: "[B1:B2]", unit: "W" }] },
              { frame_id: "0x120", parameters: [{ name: "Odo", expression: "[B0:B3]/64", unit: "km" }] },
              { frame_id: 801, bus: "ch", bus_kbps: 250, monitor_ms: 300, parameters: [{ name: "Gw", expression: "B0", unit: "" }] }] };
  const got = p.w.profileToPids(car, [], []);
  check("K: a profile's filter brings its own bus", got.filters.length === 3 && got.filters[0].bus === "sw" && got.filters[0].frame_id === 0x1026C0CB && got.filters[0].profile === true, got.filters[0]);
  check("K: a filter without one stays on the car's OBD protocol: the car's bus is where its PIDs are asked", !("bus" in got.filters[1]) && got.pids[0].bus === "ms",
    { filter: Object.keys(got.filters[1]), pid_bus: got.pids[0].bus });
  check("K: and a speed comes with its bus", got.filters[2].bus === "ch" && got.filters[2].bus_kbps === 250, got.filters[2]);

  /* L */
  check("L: the note on Pause mode names these filters", /CAN filters on MS, single-wire or CH CAN/.test(html));

  /* M */
  check("M: no page error in the whole run", p.errs.length === 0, p.errs.slice(0, 3));
  console.log((fails ? "FILTER BUS PROBE FAIL" : "FILTER BUS PROBE PASS") + " (" + total + " checks, " + fails + " failed)");
  p.w.close();
})();
