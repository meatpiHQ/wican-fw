/* Probe (2026-10-09): the standard PIDs stay the user's choice on every path of
   the Quick Setup (Ali: "when I don't select any standard PIDs and later enable
   the standard PIDs, they are all enabled by default; I want the user to enable
   the PIDs they want"). Reproduced on the bench DUT the same day on three paths
   (test-reports/logs/std_none_20261009/): a page reload between the detection and
   Finish, then detecting again, pre-ticked every row the first detection had
   stored; "Skip for now" left the device to store every row enabled at its own
   detection; a reload then Skip for now left the stored rows in place.

   Five page boots over the mock (which stores a new car's rows at the detection
   like the firmware: every one off, whatever the standard-PID switch says, since
   Ali's rule of the same day: nothing is read until the user enables the standard
   PIDs and ticks the ones they want):
     A  a new car, none chosen, Finish: no standard row, std_enabled off
     B  the same car detected again after a reload (its setup never finished):
        none ticked, not "8 of 8 chosen"; Finish none cuts the stored rows
     C  a car set up before: its current rows ticked, kept at Finish
     D  the standard PIDs off at the detection: two ticked rows read (the Reading
        step switches the standard set on), the rest go
     E  Automate > Parameters shows stored-off rows with their switches off
   usage: node probe_std_choice.mjs   (after make_preview.py; node >= 20) */
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import { JSDOM, VirtualConsole } from "jsdom";

const here = dirname(fileURLToPath(import.meta.url));
const html = readFileSync(join(here, "preview.html"), "utf-8");
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
let fails = 0, total = 0;
const check = (n, ok, extra) => { total++; console.log((ok ? "PASS " : "FAIL ") + n + (extra !== undefined ? "  [" + JSON.stringify(extra) + "]" : "")); if (!ok) { fails++; process.exitCode = 1; } };

function boot(url, preset) {
  const errs = [];
  const vc = new VirtualConsole();
  vc.on("jsdomError", (e) => errs.push(e.message + " " + (e.detail && e.detail.stack || "").slice(0, 300)));
  const dom = new JSDOM(html, {
    runScripts: "dangerously", url, pretendToBeVisual: true, virtualConsole: vc,
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
  const $ = (sel) => d().querySelector(sel), $$ = (sel) => [...d().querySelectorAll(sel)];
  const fire = (el, type) => el.dispatchEvent(new w.Event(type, { bubbles: true }));
  const tick = (el, on) => { el.checked = on !== false; fire(el, "change"); };
  const btn = (re) => $$("#view button").find((b) => re.test(b.textContent.trim()));
  const h2 = () => (($("#view h2") || {}).textContent || "").trim();
  const text = () => ($("#view") || {}).textContent || "";
  const modalBtn = (re) => [...d().querySelectorAll("#modal-root .acts button")].find((b) => re.test(b.textContent));
  const modalText = () => ($("#modal-root") || {}).textContent || "";
  return { w, d, $, $$, fire, tick, btn, h2, text, errs, modalBtn, modalText, M: () => w.__mockState, S: () => w.__MOCK_SETTINGS__ };
}
const stdRows = (cfg) => (cfg.pids || []).filter((x) => x.type === "std");
const clone = (x) => JSON.parse(JSON.stringify(x));

/* the wizard's vehicle half on a configured device, from the URL */
async function openVehicle(preset) {
  const p = boot("http://wican.local/#/setup/vehicle", { apDefaultPassword: false, ...preset });
  await sleep(1500);
  check(`${preset.__name}: the vehicle step opens from the URL`, /Your vehicle/.test(p.h2()), p.h2());
  return p;
}
async function detect(p, name) {
  p.tick(p.$("#qs-plugged")); p.tick(p.$("#qs-ignition"));
  const det = p.btn(/Detect my vehicle/);
  check(`${name}: Detect enabled`, det && !det.disabled);
  det.click(); await sleep(4800);
  check(`${name}: the car was detected`, /1\. Vehicle detected/.test(p.text()), (p.text().match(/1\. Vehicle [a-z]+/) || [])[0]);
}
async function chooseNone(p, name) {
  p.btn(/Choose PIDs|^Change$/).click(); await sleep(300);
  const clear = p.modalBtn(/^Clear$/) || [...p.d().querySelectorAll("#modal-root button")].find((b) => /^Clear$/.test(b.textContent.trim()));
  clear.click(); await sleep(100);
  check(`${name}: the picker cleared, 0 ticked`, /0 ticked/.test(p.modalText()));
  p.modalBtn(/Add selected/).click(); await sleep(300);
  check(`${name}: none chosen on the step`, /None chosen yet/.test(p.text()) && /none chosen yet/.test(p.text()));
}
async function toReading(p, name) {
  const none = p.$("#qs-prof-none");
  if (none) { none.click(); p.fire(none, "change"); await sleep(200); }
  const cont = p.btn(/^Continue$/);
  check(`${name}: Continue enabled`, cont && !cont.disabled);
  cont.click(); await sleep(1500);
  check(`${name}: the battery step`, /When should WiCAN sleep/.test(p.h2()), p.h2());
  p.$("#qs-pwr-keep").click(); await sleep(1200);
  check(`${name}: the Reading the car step`, /How WiCAN reads the car/.test(p.h2()), p.h2());
}
async function finish(p, name) {
  p.btn(/Finish and restart/).click(); await sleep(1200);
  check(`${name}: the done screen`, /WiCAN is set up/.test(p.h2()), p.h2());
}

(async () => {
  /* ---- A: a new car, none chosen ---- */
  let snapshot = null;
  {
    const p = await openVehicle({ __name: "A" });
    await detect(p, "A");
    check("A: a new car, none chosen yet", /New vehicle/.test(p.text()) && /none chosen yet/.test(p.text()));
    const stored = stdRows(p.M().autopidCfg);
    check("A: the detection stored the 8 rows the scan found, every one OFF although the standard PIDs are on", stored.length === 8 && stored.every((r) => r.enabled === false), stored.map((r) => r.cmd + (r.enabled === false ? ":off" : "")));
    snapshot = { autopidCfg: clone(p.M().autopidCfg), vehicles: clone(p.M().vehicles) };
    check("A: the store holds the car with its setup pending", snapshot.vehicles.vehicles.length === 1 && snapshot.vehicles.vehicles[0].pending_profile === true && snapshot.vehicles.current === snapshot.vehicles.vehicles[0].key, snapshot.vehicles);
    await chooseNone(p, "A");
    await toReading(p, "A");
    check("A: the Standard PIDs switch off and greyed, the text says none chosen", p.$("#qs-std") && !p.$("#qs-std").checked && p.$("#qs-std").disabled && /None chosen on the vehicle step \(the scan found 8\)/.test(p.text()), (p.text().match(/None chosen on the vehicle step[^.]*\./) || [])[0]);
    await finish(p, "A");
    check("A: Finish left no standard row in the config", stdRows(p.M().autopidCfg).length === 0, stdRows(p.M().autopidCfg).map((r) => r.cmd));
    check("A: Finish switched the standard PIDs off", p.S().autopid.values.std_enabled === false && p.S().autopid.values.enabled === true);
    check("A: no JS errors", p.errs.length === 0, p.errs.slice(0, 2));
  }

  /* ---- B: the page reloaded after the detection, the car detected again ---- */
  {
    const p = await openVehicle({ __name: "B", autopidCfg: clone(snapshot.autopidCfg), vehicles: clone(snapshot.vehicles) });
    check("B: after the reload the step is back at Detect, the device still holds the 8 stored rows", /1\. Detect the vehicle/.test(p.text()) && stdRows(p.M().autopidCfg).length === 8);
    await detect(p, "B");
    check("B: the second detection ticks nothing (the car's setup never finished), not 8 of 8", /none chosen yet/.test(p.text()) && !/8 of 8 chosen/.test(p.text()), (p.text().match(/\d+ of \d+ chosen|none chosen yet/) || [])[0]);
    check("B: the store still says pending", (p.M().vehicles.vehicles[0] || {}).pending_profile === true);
    await toReading(p, "B");
    check("B: the Standard PIDs switch off and greyed", p.$("#qs-std") && !p.$("#qs-std").checked && p.$("#qs-std").disabled);
    await finish(p, "B");
    check("B: Finish cut the 8 stored rows: no standard row left", stdRows(p.M().autopidCfg).length === 0, stdRows(p.M().autopidCfg).map((r) => r.cmd));
    check("B: the standard PIDs off", p.S().autopid.values.std_enabled === false);
    check("B: no JS errors", p.errs.length === 0, p.errs.slice(0, 2));
  }

  /* ---- B2: the page reloaded, the wizard resumed at Reading the car without a detection ---- */
  {
    const p = boot("http://wican.local/#/setup/polling", { apDefaultPassword: false, autopidCfg: clone(snapshot.autopidCfg), vehicles: clone(snapshot.vehicles) });
    await sleep(1500);
    check("B2: the Reading the car step from the URL", /How WiCAN reads the car/.test(p.h2()), p.h2());
    check("B2: the Standard PIDs switch off and greyed (no choice this session)", p.$("#qs-std") && !p.$("#qs-std").checked && p.$("#qs-std").disabled);
    await finish(p, "B2");
    check("B2: Finish without a detection cut the rows the device stored (setup pending)", stdRows(p.M().autopidCfg).length === 0, stdRows(p.M().autopidCfg).map((r) => r.cmd));
    check("B2: no JS errors", p.errs.length === 0, p.errs.slice(0, 2));
  }

  /* ---- C: a car set up before keeps its rows ---- */
  {
    const veh = clone(snapshot.vehicles);
    veh.vehicles[0].pending_profile = false; veh.vehicles[0].name = "Bench car";
    const cfg = clone(snapshot.autopidCfg);
    /* a car set up before: its two chosen rows, on (the wizard turned them on at its Finish) */
    cfg.pids = cfg.pids.filter((x) => x.type !== "std" || /^(010C1|010D1)$/.test(x.cmd)).map((x) => { const y = { ...x }; delete y.enabled; return y; });
    const p = await openVehicle({ __name: "C", autopidCfg: cfg, vehicles: veh });
    await detect(p, "C");
    check("C: a car set up before comes with its two rows ticked", /2 of 8 chosen/.test(p.text()), (p.text().match(/\d+ of \d+ chosen[^.]*/) || [])[0]);
    await toReading(p, "C");
    check("C: the Standard PIDs switch on (the device's setting) and enabled, the text counts the choice", p.$("#qs-std") && p.$("#qs-std").checked && !p.$("#qs-std").disabled && /The 2 of the 8 the scan found/.test(p.text()));
    await finish(p, "C");
    const rows = stdRows(p.M().autopidCfg).map((r) => r.cmd).sort();
    check("C: Finish kept exactly those two rows", rows.join(",") === "010C1,010D1", rows);
    check("C: the standard PIDs stay on", p.S().autopid.values.std_enabled === true);
    check("C: no JS errors", p.errs.length === 0, p.errs.slice(0, 2));
  }

  /* ---- C2: a car set up before, Finish without a detection keeps its rows ---- */
  {
    const veh = clone(snapshot.vehicles);
    veh.vehicles[0].pending_profile = false;
    const cfg = clone(snapshot.autopidCfg);
    cfg.pids = cfg.pids.filter((x) => x.type !== "std" || /^(010C1|010D1)$/.test(x.cmd)).map((x) => { const y = { ...x }; delete y.enabled; return y; });
    const p = boot("http://wican.local/#/setup/polling", { apDefaultPassword: false, autopidCfg: cfg, vehicles: veh });
    await sleep(1500);
    await finish(p, "C2");
    const rows = stdRows(p.M().autopidCfg).map((r) => r.cmd).sort();
    check("C2: a set-up car's rows survive a Finish without a detection (an earlier choice)", rows.join(",") === "010C1,010D1", rows);
  }

  /* ---- D: the standard PIDs off at the detection ---- */
  {
    const p = await openVehicle({ __name: "D" });
    p.S().autopid.values.std_enabled = false;
    await detect(p, "D");
    const stored = stdRows(p.M().autopidCfg);
    check("D: the detection stored the 8 rows OFF (the standard PIDs off as well)", stored.length === 8 && stored.every((r) => r.enabled === false), stored.map((r) => r.cmd + (r.enabled === false ? ":off" : "")));
    p.btn(/Choose PIDs/).click(); await sleep(300);
    for (const b of p.$$('#modal-root input[type="checkbox"]')) { const row = b.closest("label"); if (/010C1|01051/.test(row.textContent)) { b.click(); p.fire(b, "change"); } }
    check("D: two ticked", /2 ticked/.test(p.modalText()));
    p.modalBtn(/Add selected/).click(); await sleep(300);
    check("D: 2 of 8 chosen", /2 of 8 chosen/.test(p.text()));
    await toReading(p, "D");
    check("D: a pick made this session switches the Standard PIDs on, although the device has them off", p.$("#qs-std") && p.$("#qs-std").checked && !p.$("#qs-std").disabled && /The 2 of the 8 the scan found that you chose/.test(p.text()));
    await finish(p, "D");
    const rows = stdRows(p.M().autopidCfg);
    check("D: Finish kept the two chosen rows, switched on, and dropped the other six", rows.length === 2 && rows.every((r) => r.enabled !== false) && rows.map((r) => r.cmd).sort().join(",") === "01051,010C1", rows.map((r) => r.cmd + (r.enabled === false ? ":off" : "")));
    check("D: the standard PIDs on in the autopid PUT", p.S().autopid.values.std_enabled === true);
    check("D: no JS errors", p.errs.length === 0, p.errs.slice(0, 2));
  }

  /* ---- E: Automate > Parameters shows stored-off rows off ---- */
  {
    const cfg = clone(snapshot.autopidCfg);
    cfg.pids = cfg.pids.map((x) => (x.type === "std" ? { ...x, enabled: false } : x));
    const p = boot("http://wican.local/#/automate/parameters", { apDefaultPassword: false, autopidCfg: cfg, vehicles: clone(snapshot.vehicles) });
    await sleep(1800);
    const rows = p.$$("#view .pidrow");
    check("E: the Standard PIDs table lists the 8 stored rows", rows.length === 8, rows.length);
    check("E: every row's switch is off (stored off, waiting for the user's tick)", rows.length === 8 && rows.every((r) => r.classList.contains("off") && r.querySelector('input[type="checkbox"]') && !r.querySelector('input[type="checkbox"]').checked));
    check("E: no JS errors", p.errs.length === 0, p.errs.slice(0, 2));
  }

  console.log(fails ? `FAILED ${fails} of ${total}` : `ALL PASS (${total} checks)`);
  process.exit(fails ? 1 : 0);   /* the pages' timers would keep node alive */
})();
