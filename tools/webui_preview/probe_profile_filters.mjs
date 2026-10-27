/* Probe (2026-10-27): a vehicle profile's passive CAN filters, over the mock.

   The Leaf ZE1 (2018-) profile is the first published one with `filters`
   (SPEED, the signed DM_F_RPM and ODOMETER read from frames the car broadcasts
   on 0x284, 0x1DA and 0x5C5). The autopid README documented the import on
   2026-10-21 and the e2e bench proved the frames decode on the device, but the
   web UI's half of the edit had been made in a stale clone of the components:
   the live importer carried a profile's PIDs alone, so picking the Leaf in
   Automate > Parameters > Vehicle Specific dropped its three filters, and the
   Quick Setup's Finish did the same (builds up to 6.0.0-beta.4). Ported
   2026-10-27 into both; this probe pins the behaviour.

   The mock serves the probe's own published list (window.__mockPreset.profiles):
     Nissan: Leaf ZE1 (2018-)   2 of its PIDs, its 3 filters as published
     Bench: Twin                1 PID, 1 filter whose parameter is named Speed
                                (the mock's standard PID already has a Speed)
     Bench: Plain               1 PID, no filters
   and its config starts with one hand-made filter (SOC_BMS, legacy shape).

   On the Vehicle Specific pane:
     A  pick the Leaf: the toast counts 2 PIDs and 3 CAN filters, the banner
        says 3 passive CAN filters replace the last profile's filters
     B  Apply: the PUT carries the hand-made filter untouched plus the three
        profile filters (frame ids as numbers 644/474/1477, monitor_ms 500 as
        published, period_ms 0, group default, profile true, the expressions
        unshifted, the parameters' names/units/classes/min/max as published)
     C  pick Twin, then Discard: Apply sends the Leaf's filters again (Discard
        put the previous profile filters back, the hand-made one still there)
     D  pick Twin and Apply: the Leaf's three are gone, Twin's one is there with
        its parameter renamed Speed_2 (the rename toast says so), still beside
        the hand-made filter
     E  pick Plain and Apply: no profile filter left, the hand-made one stays,
        the toast and banner mention no filter
     F  no page error in the whole run
   In the Quick Setup's vehicle step (a fresh device, the car named first):
     G  the Leaf chosen, the detection, Test profile names the three broadcast
        frames (nothing to request for them), Continue, the battery step
        skipped, Finish: the table PUT carries the hand-made filter plus the
        Leaf's three and its two PIDs, the car's entry carries the profile
   usage: node probe_profile_filters.mjs   (after make_preview.py; node >= 20) */
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import { JSDOM, VirtualConsole } from "jsdom";

const here = dirname(fileURLToPath(import.meta.url));
const html = readFileSync(join(here, "preview.html"), "utf-8");
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
let fails = 0, total = 0;
const check = (n, ok, extra) => { total++; console.log((ok ? "PASS " : "FAIL ") + n + (extra !== undefined ? "  [" + JSON.stringify(extra).slice(0, 500) + "]" : "")); if (!ok) { fails++; process.exitCode = 1; } };

/* the Leaf ZE1 (2018-) entry of the published list, trimmed to two PIDs; the
   filters are byte for byte the published ones (fetched 2026-10-27) */
const LEAF = {
  car_model: "Nissan: Leaf ZE1 (2018-)",
  init: "ATSP6;ATST96;ATFCSH79B;ATFCSD300000;ATFCSM1;",
  pids: [
    { pid: "21018", parameters: [
      { name: "SOC", expression: "[B39:B41]/10000", unit: "%", class: "battery" },
      { name: "HV_V", expression: "[B22:B23]/100", unit: "V", class: "voltage" },
      { name: "HV_A", expression: "[S4:S7]/1024", unit: "A", class: "current" },
      { name: "LV_V", expression: "B11*0.08", unit: "V", class: "voltage" },
      { name: "BATT_CAPACITY", expression: "[B36:B38]/10000", unit: "Ah", class: "none" } ] },
    { pid: "2161", parameters: [ { name: "SOH", expression: "[B6:B7]/100", unit: "%", class: "none" } ] },
  ],
  filters: [
    { frame_id: "0x284", monitor_ms: 500, parameters: [{ name: "SPEED", expression: "[B4:B5]/98", unit: "km/h", class: "speed" }] },
    { frame_id: "0x1DA", monitor_ms: 500, parameters: [{ name: "DM_F_RPM", expression: "[S4:S5]/2", unit: "rpm", class: "none", min: "-10100", max: "10100" }] },
    { frame_id: "0x5C5", monitor_ms: 500, parameters: [{ name: "ODOMETER", expression: "[B1:B3]", class: "distance", unit: "km", min: "0" }] },
  ],
};
const TWIN = {
  car_model: "Bench: Twin", init: "ATSP6;",
  pids: [{ pid: "221234", parameters: [{ name: "TwinTemp", expression: "B3-40", unit: "°C", class: "temperature" }] }],
  filters: [{ frame_id: "0x3A0", monitor_ms: 250, period_ms: 2000, parameters: [{ name: "Speed", expression: "B0", unit: "km/h", class: "speed" }] }],
};
const PLAIN = {
  car_model: "Bench: Plain", init: "ATSP6;",
  pids: [{ pid: "2199", parameters: [{ name: "PlainOne", expression: "B3", unit: "" }] }],
};
const PROFILES = [LEAF, TWIN, PLAIN];
const HAND = { id: "0x18DAF110", name: "SOC_BMS", expression: "B5/2", unit: "%", monitor_ms: 1000 };
const profileFilters = (cfg) => ((cfg && cfg.filters) || []).filter((f) => f.profile);
const handFilters = (cfg) => ((cfg && cfg.filters) || []).filter((f) => !f.profile);

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
  const btn = (re, root) => [...(root || d()).querySelectorAll("button")].find((b) => re.test(b.textContent.trim()));
  const h2 = () => (($("#view h2") || {}).textContent || "").trim();
  const text = () => ($("#view") || {}).textContent || "";
  const modalEl = () => $("#modal-root");
  const modalBtn = (re) => [...d().querySelectorAll("#modal-root .acts button")].find((b) => re.test(b.textContent));
  const modalText = () => (modalEl() || {}).textContent || "";
  const toasts = () => $$(".toast").map((t) => t.textContent);
  const clearToasts = () => { for (const t of $$(".toast")) t.remove(); };
  return { w, d, $, $$, fire, tick, btn, h2, text, errs, modalEl, modalBtn, modalText, toasts, clearToasts, M: () => w.__mockState, close: () => dom.window.close() };
}
/* the shared picker: search, the single match is selected by itself, Use */
async function pickInPicker(p, query) {
  const choose = p.btn(/Choose profile|Change profile/);
  if (!choose) return false;
  choose.click(); await sleep(700);
  const input = p.$("#modal-root .vp-search input");
  if (!input) return false;
  input.value = query; input.dispatchEvent(new p.w.Event("input")); await sleep(100);
  const ok = p.modalBtn(/Use this profile/);
  if (!ok || ok.disabled) return false;
  ok.click(); await sleep(400);
  return true;
}

/* ================= the Vehicle Specific pane ================= */
{
  const p = boot("http://wican.local/#/automate", { profiles: PROFILES });
  const { w, d, $$, errs } = p;
  const btn = p.btn;
  const bannerEl = () => $$(".banner").find((b) => /staged|applied/.test(b.textContent) && b.style.display !== "none");
  const vehInput = () => d().querySelector('.frow[data-key="vehicle"] input');
  await sleep(2500);
  const segBtns = $$(".seg button");
  const vehBtn = segBtns.find((b) => b.textContent === "Vehicle Specific");
  check("the Parameters tab offers Vehicle Specific", !!vehBtn);
  vehBtn.click(); await sleep(500);

  /* record every PUT of the table */
  const puts = [];
  const origFetch = w.fetch;
  w.fetch = (u, o) => { if (o && o.method === "PUT" && /\/api\/autopid\/config/.test(String(u))) puts.push(JSON.parse(o.body)); return origFetch(u, o); };
  const lastPut = () => puts[puts.length - 1];
  const apply = async () => {
    const before = puts.length;
    const applyBtns = $$("button.btn.pri").filter((b) => b.textContent === "Apply Configuration");
    const applyBtn = applyBtns[applyBtns.length - 1];
    if (!applyBtn) return false;
    applyBtn.click(); await sleep(600);
    /* the settings-save modal (name + init staged): save, restart later */
    const later = p.modalEl() && [...p.modalEl().querySelectorAll("button")].find((b) => /restart later/.test(b.textContent));
    if (later) { later.click(); await sleep(400); }
    return puts.length === before + 1;
  };

  /* ---- A: the Leaf staged ---- */
  p.clearToasts();
  check("A: the Leaf is picked from the probe's list", await pickInPicker(p, "Leaf ZE1 (2018"));
  check("A: the toast counts the PIDs and the filters", p.toasts().some((t) => /Nissan: Leaf ZE1 \(2018-\) staged: 2 PIDs and 3 CAN filters\. Apply Configuration sends them/.test(t)), p.toasts());
  check("A: the banner says 3 passive CAN filters replace the last profile's filters and hand-made ones are kept",
    !!bannerEl() && /2 PIDs replace the vehicle-specific table, 3 passive CAN filters \(frames the car broadcasts\) replace the last profile's filters/.test(bannerEl().textContent) && /hand-made filters are kept/.test(bannerEl().textContent),
    bannerEl() && bannerEl().textContent.slice(0, 300));
  check("A: the vehicle field carries the profile name", (vehInput() || {}).value === LEAF.car_model);

  /* ---- B: Apply sends them ---- */
  check("B: Apply PUTs the table once", await apply());
  {
    const cfg = lastPut();
    const pf = profileFilters(cfg), hf = handFilters(cfg);
    check("B: the hand-made filter is untouched, first", hf.length === 1 && JSON.stringify(hf[0]) === JSON.stringify(HAND) && JSON.stringify(cfg.filters[0]) === JSON.stringify(HAND), hf);
    check("B: three profile filters follow it", pf.length === 3 && cfg.filters.length === 4);
    check("B: frame ids read like Number(): 0x284 / 0x1DA / 0x5C5 are 644 / 474 / 1477", JSON.stringify(pf.map((f) => f.frame_id)) === "[644,474,1477]", pf.map((f) => f.frame_id));
    check("B: monitor_ms as published (500), period_ms 0, group default, tagged profile",
      pf.every((f) => f.monitor_ms === 500 && f.period_ms === 0 && f.group === "default" && f.profile === true), pf.map((f) => [f.monitor_ms, f.period_ms, f.group, f.profile]));
    const want = LEAF.filters.map((f) => f.parameters[0]);
    check("B: the parameters go as published (names, expressions unshifted, units, classes, min/max)",
      JSON.stringify(pf.map((f) => f.parameters)) === JSON.stringify(want.map((q) => [{ ...q, expression: String(q.expression) }])), pf.map((f) => f.parameters));
    check("B: the specific PIDs went too (2), the standard and custom rows stayed",
      cfg.pids.filter((x) => x.type === "specific").length === 2 && cfg.pids.filter((x) => x.type === "std").length === 3 && cfg.pids.filter((x) => x.type === "custom").length === 1,
      cfg.pids.map((x) => x.type + ":" + x.cmd));
  }

  /* ---- C: pick another, Discard, Apply: the Leaf's filters are back ---- */
  p.clearToasts();
  check("C: Twin picked over the applied Leaf", await pickInPicker(p, "Twin"));
  check("C: the toast counts Twin's 1 PID and 1 CAN filter", p.toasts().some((t) => /Bench: Twin staged: 1 PID and 1 CAN filter\. Apply Configuration sends them/.test(t)), p.toasts());
  {
    const discard = bannerEl() && btn(/^Discard$/, bannerEl());
    check("C: Discard offered", !!discard);
    if (discard) { discard.click(); await sleep(300); }
    check("C: Discard hides the banner and puts the Leaf's name back", !bannerEl() && (vehInput() || {}).value === LEAF.car_model, (vehInput() || {}).value);
    check("C: Apply after Discard PUTs the Leaf's three filters again beside the hand-made one", await apply() && JSON.stringify(profileFilters(lastPut()).map((f) => f.frame_id)) === "[644,474,1477]" && handFilters(lastPut()).length === 1 && lastPut().filters.length === 4,
      (lastPut().filters || []).map((f) => f.frame_id || f.id));
  }

  /* ---- D: Twin applied: the Leaf's gone, the colliding name renamed ---- */
  p.clearToasts();
  check("D: Twin picked again", await pickInPicker(p, "Twin"));
  check("D: the rename toast names Speed to Speed_2", p.toasts().some((t) => /1 duplicate parameter name in this profile renamed: Speed to Speed_2/.test(t)), p.toasts());
  check("D: Apply PUTs Twin's one filter in place of the Leaf's three", await apply() && lastPut().filters.length === 2 && profileFilters(lastPut()).length === 1 && handFilters(lastPut()).length === 1,
    (lastPut().filters || []).map((f) => f.frame_id || f.id));
  {
    const f = profileFilters(lastPut())[0] || {};
    check("D: Twin's filter as published: 0x3A0 is 928, monitor_ms 250, period_ms 2000, the parameter Speed_2",
      f.frame_id === 928 && f.monitor_ms === 250 && f.period_ms === 2000 && f.group === "default" && f.profile === true && f.parameters && f.parameters.length === 1 && f.parameters[0].name === "Speed_2" && f.parameters[0].expression === "B0", f);
  }

  /* ---- E: a profile without filters clears the profile's filters ---- */
  p.clearToasts();
  check("E: Plain picked", await pickInPicker(p, "Plain"));
  check("E: the toast counts 1 PID and no filter", p.toasts().some((t) => /Bench: Plain staged: 1 PID\. Apply Configuration sends them/.test(t)), p.toasts());
  check("E: the banner mentions no filter", !!bannerEl() && /1 PID replaces the vehicle-specific table, and the profile name/.test(bannerEl().textContent) && !/CAN filter/.test(bannerEl().textContent), bannerEl() && bannerEl().textContent.slice(0, 200));
  check("E: Apply PUTs the hand-made filter alone", await apply() && lastPut().filters.length === 1 && JSON.stringify(lastPut().filters[0]) === JSON.stringify(HAND), lastPut().filters);

  /* ---- F ---- */
  check("F: no page error on the Automate page", errs.length === 0, errs.slice(0, 3));
  p.close();
}

/* ================= the Quick Setup's vehicle step ================= */
{
  const p = boot("http://wican.local/#/setup/vehicle", { profiles: PROFILES, apDefaultPassword: false, vehicles: { current: "", vehicles: [] } });
  await sleep(1500);
  check("G: the vehicle step opens from the URL", /Your vehicle/.test(p.h2()), p.h2());
  check("G: the Leaf chosen first, in the wizard's picker", await pickInPicker(p, "Leaf ZE1 (2018"));
  check("G: the profile pill names the Leaf", /Nissan: Leaf ZE1 \(2018-\)/.test((p.$(".qs-radios .sel") || {}).textContent || ""), (p.$(".qs-radios .sel") || {}).textContent);
  p.tick(p.$("#qs-plugged")); p.tick(p.$("#qs-ignition"));
  const det = p.btn(/Detect my vehicle/);
  check("G: Detect enabled after the two ticks", det && !det.disabled);
  det.click(); await sleep(4800);
  check("G: the car detected, the profile section shown", /Vehicle detected/.test(p.text()) && /4\. Vehicle profile/.test(p.text()), p.text().match(/\d\. [A-Z][a-z]+ [a-zP]+/g));
  const tb = p.btn(/Test profile/);
  check("G: Test profile offered", tb && !tb.disabled);
  if (tb) {
    tb.click(); await sleep(1800);
    check("G: Test profile names the three broadcast frames, nothing to request for them",
      /The profile also reads 3 frames the car broadcasts \(passive CAN filters: SPEED, DM_F_RPM, ODOMETER\)\. Nothing to request for those: they are set up with the profile\./.test(p.modalText()), p.modalText().slice(-260));
    const use = p.modalBtn(/Use this profile/); if (use) { use.click(); await sleep(300); }
  }
  const cont = p.btn(/^Continue$/);
  check("G: Continue enabled with the profile chosen", cont && !cont.disabled);
  cont.click(); await sleep(1600);
  const keep = p.$("#qs-pwr-keep");
  check("G: the battery step offers to keep the defaults", !!keep);
  if (keep) { keep.click(); await sleep(1200); }
  check("G: the reading-the-car step", /How WiCAN reads the car/.test(p.h2()), p.h2());
  const fin = p.btn(/Finish and restart/);
  check("G: Finish and restart offered", !!fin);
  if (fin) { fin.click(); await sleep(1200); }
  const cfg = p.M().autopidCfg;
  const pf = profileFilters(cfg), hf = handFilters(cfg);
  check("G: Finish PUT the hand-made filter untouched plus the Leaf's three, as published, tagged profile",
    hf.length === 1 && JSON.stringify(hf[0]) === JSON.stringify(HAND) && JSON.stringify(pf.map((f) => [f.frame_id, f.monitor_ms, f.period_ms, f.group, f.profile, f.parameters.map((q) => q.name + "=" + q.expression)])) ===
      JSON.stringify([[644, 500, 0, "default", true, ["SPEED=[B4:B5]/98"]], [474, 500, 0, "default", true, ["DM_F_RPM=[S4:S5]/2"]], [1477, 500, 0, "default", true, ["ODOMETER=[B1:B3]"]]]),
    (cfg.filters || []).map((f) => f.frame_id || f.id));
  check("G: Finish PUT the Leaf's two PIDs as the vehicle-specific rows", (cfg.pids || []).filter((x) => x.type === "specific").map((x) => x.cmd).join(",") === "21018,2161", (cfg.pids || []).map((x) => x.type + ":" + x.cmd));
  const puts = p.M().vehiclePuts || [];
  check("G: the car's entry carries the profile and its init", puts.length >= 1 && puts[puts.length - 1].body.profile === LEAF.car_model && puts[puts.length - 1].body.specific_init === LEAF.init, puts.map((x) => x.body));
  check("G: no page error in the wizard", p.errs.length === 0, p.errs.slice(0, 3));
  p.close();
}

console.log(`${fails ? "PROFILE FILTERS PROBE FAIL" : "PROFILE FILTERS PROBE PASS"}: ${total - fails}/${total}`);
