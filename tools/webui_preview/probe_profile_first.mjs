/* Probe (2026-10-26, discussion #1018, Ali: "ask the user to select the
   profile before we probe an identity"): the Quick Setup's vehicle step asks
   which car it is first, with two choices (a published profile, or no profile
   and detection as before), the detection carries the profile along, a car
   that answers nothing standard (a Nissan Leaf) is confirmed by the profile's
   own request and stored, and a car seen without its VIN can be folded into
   the entry it belongs to ("Same car as...").

   Five page boots over the mock:
     A  the step opens on section 1 with the two choices, detection the default,
        Detect waiting for the two ticks as before
     B  a profile chosen before the probe: the pill at the right edge of the
        row, the name prefilled from the profile, the detect body carries the
        profile, the name, its protocol and its requests; the result card shows
        the profile row (Change, Test, Scan again), the entry is stored under
        the name with the profile attached, Continue is enabled
     C  a car that answers nothing standard: without a profile the scan fails
        as before; with one, the car is confirmed by the profile, no standard
        PID section, the entry keyed by its ECUs with confirmed_by profile
     D  the list: a car without a VIN shows "No VIN" and "Same car as...";
        folding it into the VIN car calls the merge route and drops the entry
     E  no JS errors on any of them
   usage: node probe_profile_first.mjs   (after make_preview.py; node >= 20) */
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
  return { w, d, $, $$, fire, tick, btn, h2, text, errs, modalBtn, modalText, M: () => w.__mockState };
}

const VIN = "1WCAN0FW0P0000001";
const twoCars = () => ({ current: VIN, vehicles: [
  { key: VIN, vin: VIN, fingerprint: "9a3f17c2", name: "Bench car", protocol: "6", chip_protocol: "6", dialect: "obd2", j1939: false, profile: "", specific_init: "", ecus: "7E8:183F8003", std_supported: 16, pending_profile: false, first_seen: 1792900000, last_seen: 1792950000, scan_ts: 1792900000 },
  { key: "fp:0a70ff20", vin: "", fingerprint: "0a70ff20", name: "Car 0a70", protocol: "6", chip_protocol: "6", dialect: "obd2", j1939: false, profile: "", specific_init: "", ecus: "7E8:183F8000", std_supported: 15, pending_profile: true, first_seen: 1792890000, last_seen: 1792890000, scan_ts: 1792890000 },
] });

async function openVehicle(preset) {
  const p = boot("http://wican.local/#/setup/vehicle", { apDefaultPassword: false, vehicles: { current: "", vehicles: [] }, ...preset });
  await sleep(1500);
  check(`${preset.__name}: the vehicle step opens from the URL`, /Your vehicle/.test(p.h2()), p.h2());
  return p;
}
/* the profile from section 1 through the shared picker (the mock's list) */
async function chooseProfile(p, name) {
  p.btn(/Choose profile/).click(); await sleep(900);
  const mk = p.$("#modal-root .vp-make");
  check(`${name}: Choose profile opens the picker with makes`, !!mk);
  if (mk) { mk.click(); await sleep(150); const row = p.$("#modal-root .vp-row"); if (row) row.click(); await sleep(150); const use = p.modalBtn(/Use this profile/); if (use) use.click(); await sleep(400); }
}
async function detect(p, name, wait = 4800) {
  p.tick(p.$("#qs-plugged")); p.tick(p.$("#qs-ignition"));
  const det = p.btn(/Detect my vehicle/);
  check(`${name}: Detect enabled after the two ticks`, det && !det.disabled);
  det.click(); await sleep(wait);
}

(async () => {
  /* A: the step as it opens */
  {
    const p = await openVehicle({ __name: "A" });
    check("A: section 1 asks which car this is, section 2 is the detection", /1\. Which car is this\?/.test(p.text()) && /2\. Detect the vehicle/.test(p.text()), p.text().match(/\d\. [A-Z][a-z]+ [a-z ?]+/g));
    const prof = p.$("#qs-car-profile"), det = p.$("#qs-car-detect");
    check("A: two choices, detection the default", !!prof && !!det && !prof.checked && det.checked);
    check("A: the first choice offers Choose profile, no pill yet", !!p.btn(/Choose profile/) && !p.$(".qs-radios .sel"));
    check("A: the name field is on the step, empty", !!p.$("#qs-veh-name") && p.$("#qs-veh-name").value === "");
    const dbtn = p.btn(/Detect my vehicle/);
    check("A: Detect waits for the plug + ignition acknowledgements", dbtn && dbtn.disabled);
    check("A: no JS errors", p.errs.length === 0, p.errs.slice(0, 2));
  }
  /* B: a profile chosen before the probe */
  {
    const p = await openVehicle({ __name: "B" });
    await chooseProfile(p, "B");
    check("B: the profile choice is selected, the pill sits in the row with Change profile", p.$("#qs-car-profile").checked && !!p.$(".qs-radios .sel") && /AAA: Generic/.test(p.$(".qs-radios .sel").textContent) && !!p.btn(/Change profile/));
    check("B: the name is prefilled from the profile's make and model", p.$("#qs-veh-name").value === "AAA Generic", p.$("#qs-veh-name").value);
    check("B: the line under the pill says what the profile's init pins and how many requests", /CAN 11-bit 500 kbit\/s \(the profile's init\), 10 requests: EngineRPM/.test(p.text()), (p.text().match(/[^.]{0,60}\(the profile's init\)[^.]{0,80}/) || [p.text().slice(0, 200)])[0]);
    await detect(p, "B");
    const probe = p.M().scanProbe || {};
    check("B: the detect body carried the profile, the name and its first three requests", probe.profile === "AAA: Generic" && probe.name === "AAA Generic" && probe.cmds === 3 && probe.answered === "010C1", probe);
    check("B: the result is section 2, the standard PIDs 3, the profile 4", /2\. Vehicle detected/.test(p.text()) && /3\. Standard PIDs/.test(p.text()) && /4\. Vehicle profile/.test(p.text()), p.text().match(/\d\. [A-Z][a-z]+ [a-zP]+/g));
    check("B: the profile row shows the chosen profile with Change, Test and Scan again", /AAA: Generic/.test(p.text()) && !!p.btn(/Change profile/) && !!p.btn(/Test profile/) && !!p.btn(/Scan again/) && !p.$("#qs-prof-none"));
    const e = (p.M().vehicles.vehicles || []).find((v) => v.key === VIN) || {};
    check("B: the entry is stored under the name with the profile attached, not pending", e.name === "AAA Generic" && e.profile === "AAA: Generic" && e.pending_profile === false, { name: e.name, profile: e.profile, pending: e.pending_profile });
    const fin = p.btn(/^Continue$/);
    check("B: Continue is enabled (the profile is the choice)", fin && !fin.disabled);
    const tb = p.btn(/Test profile/);
    check("B: Test profile is enabled", tb && !tb.disabled);
    check("B: no JS errors", p.errs.length === 0, p.errs.slice(0, 2));
  }
  /* C: a car that answers nothing standard */
  {
    const p = await openVehicle({ __name: "C1", detect: "profileonly" });
    await detect(p, "C1", 2600);
    check("C1: without a profile the scan fails as before", /The car did not answer/.test(p.text()) && /no ECU response/.test(p.text()));
    check("C1: section 1 is still offered, so the profile can be chosen now", /1\. Which car is this\?/.test(p.text()) && !!p.btn(/Choose profile/));
    check("C1: no JS errors", p.errs.length === 0, p.errs.slice(0, 2));
  }
  {
    const p = await openVehicle({ __name: "C2", detect: "profileonly" });
    await chooseProfile(p, "C2");
    await detect(p, "C2");
    check("C2: the car is confirmed by the profile's own request", /New vehicle: AAA Generic/.test(p.text()) && /answers no standard request/.test(p.text()) && /010C1/.test(p.text()), (p.text().match(/(New vehicle|Welcome back|Vehicle recognised)[^\n]{0,160}/) || [p.text().slice(0, 240)])[0]);
    check("C2: no standard PID section, the profile is section 3", !/3\. Standard PIDs/.test(p.text()) && !p.btn(/Choose PIDs/) && /3\. Vehicle profile/.test(p.text()), p.text().match(/\d\. [A-Z][a-z]+ [a-zP]+/g));
    check("C2: the VIN row says not available, identified by the ECUs the profile's request answered from", /not available/.test(p.text()) && /identified by the ECUs the profile's request answered from/.test(p.text()));
    const e = (p.M().vehicles.vehicles || []).find((v) => String(v.key).startsWith("fp:")) || {};
    check("C2: the entry is keyed by its ECUs, confirmed by the profile, named, the profile attached", e.key === "fp:3b91c204" && e.confirmed_by === "profile" && e.name === "AAA Generic" && e.profile === "AAA: Generic" && e.std_supported === 0, e);
    check("C2: no JS errors", p.errs.length === 0, p.errs.slice(0, 2));
  }
  /* D: the list with a car seen without its VIN */
  {
    const p = await openVehicle({ __name: "D", vehicles: twoCars() });
    const rows = p.$$(".qs-net");
    check("D: both cars are listed, the VIN-less one marked No VIN with Same car as...", rows.length === 2 && /No VIN/.test(rows[1].textContent) && /Same car as/.test(rows[1].textContent) && !/Same car as/.test(rows[0].textContent), rows.map((r) => r.textContent.slice(0, 60)));
    const same = [...rows[1].querySelectorAll("button")].find((b) => /Same car as/.test(b.textContent));
    same.click(); await sleep(300);
    check("D: the modal offers the other car", /Same car as/.test(p.modalText()) && /Bench car/.test(p.modalText()) && !!p.modalBtn(/Fold it in/));
    p.modalBtn(/Fold it in/).click(); await sleep(600);
    const merges = p.M().vehicleMerges || [];
    check("D: the merge route was called with the VIN car as the target", merges.length === 1 && merges[0].from === "fp:0a70ff20" && merges[0].into === VIN, merges);
    check("D: the list is down to the VIN car, which carries the face", p.$$(".qs-net").length === 1 && /Bench car/.test(p.text()) && (p.M().vehicles.vehicles[0].faces || []).includes("0a70ff20"), p.M().vehicles.vehicles[0].faces);
    check("D: no JS errors", p.errs.length === 0, p.errs.slice(0, 2));
  }

  console.log(fails ? `FAILED ${fails} of ${total}` : `ALL PASS (${total} checks)`);
  process.exit(fails ? 1 : 0);   /* the pages' timers would keep node alive */
})();
