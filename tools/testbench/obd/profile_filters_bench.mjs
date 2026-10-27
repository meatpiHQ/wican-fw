/* A vehicle profile's passive CAN filters, imported from the web UI on the real
   device (2026-10-27).

   The Leaf ZE1 (2018-) profile is the first published one with `filters`
   (SPEED, the signed DM_F_RPM and ODOMETER from frames the car broadcasts on
   0x284, 0x1DA and 0x5C5). The e2e bench proved on 2026-10-21 that the device
   decodes them, converting the profile itself; the web UI's import of the same
   filters had been written into a stale clone and never reached the live tree,
   so a user picking the Leaf in Automate > Parameters > Vehicle Specific got its
   PIDs and none of its filters (builds up to 6.0.0-beta.4). Ported 2026-10-27.

   This bench drives the real page in Chrome through an ssh tunnel to the DUT and
   reads the device over its API after every Apply:

     seed    the saved table plus one hand-made filter (0x5A0 BENCH_HAND, off)
     s1      Choose profile, the published list fetched from GitHub by the
             browser, the Leaf ZE1 (2018-) picked: the banner counts 6 PIDs and
             3 passive CAN filters
     s2      Apply Configuration, "Save, restart later": the device's table has
             the hand-made filter untouched plus the three profile filters as
             published (644/474/1477, monitor_ms 500, tagged profile), its
             runtime counts four filters and lists their parameters, the
             vehicle setting carries the profile's name and init
     s3      the same profile picked and applied again: still four, no duplicate
     s4      a profile without filters (Leaf ZE0/AZE0) picked and applied: the
             profile filters gone, the hand-made one still there
     s5      the Leaf ZE1 picked then Discard, Apply: nothing changed
     w1      the Quick Setup's vehicle step: the Leaf named first, the car
             detected, the battery step skipped, Finish and restart: the device's
             table carries the hand-made filter plus the profile's three and its
             PIDs, the car's entry the profile (the wizard's Finish took the PIDs
             alone until 2026-10-27)
     end     no new E line, no fault; the saved table, the autopid settings and
             the car's entry go back (planned restarts, counted as such)

   Needs the DUT on the bench AP (reachable from rpi001), internet on this PC (the
   browser fetches vehicle_profiles.json from GitHub) and the Playwright
   devDependency of tools/webui_preview (`npm install` there once).

   node tools/testbench/obd/profile_filters_bench.mjs [out dir] [--dut 10.42.1.194]
        [--profile "Nissan: Leaf ZE1 (2018-)"] [--other "Nissan: Leaf ZE0/AZE0 (2011-2017)"]
   Verdict: PROFILE FILTERS PASS / PROFILE FILTERS FAIL: <names>. */
import { spawn } from "node:child_process";
import { mkdirSync, writeFileSync } from "node:fs";
import { createRequire } from "node:module";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const { chromium } = createRequire(join(here, "..", "..", "webui_preview", "package.json"))("playwright");

const arg = (k, d) => { const i = process.argv.indexOf(k); return i > 0 && process.argv[i + 1] ? process.argv[i + 1] : d; };
const DUT = arg("--dut", "10.42.1.194");
const PROFILE = arg("--profile", "Nissan: Leaf ZE1 (2018-)");
const OTHER = arg("--other", "Nissan: Leaf ZE0/AZE0 (2011-2017)");
const out = (process.argv[2] && !process.argv[2].startsWith("--")) ? process.argv[2] : "shots_profile_filters";
const BIND = "127.0.0.1", PORT = 8078;
const base = `http://${BIND}:${PORT}`;
const PROFILES_URL = "https://raw.githubusercontent.com/meatpiHQ/wican-fw/main/vehicle_profiles.json";
mkdirSync(out, { recursive: true });

const stamp = () => new Date().toTimeString().slice(0, 8);
const say = (...a) => console.log(stamp(), ...a);
const failed = [];
const check = (n, ok, extra) => { say((ok ? "PASS " : "FAIL ") + n + (extra !== undefined ? "  [" + JSON.stringify(extra).slice(0, 400) + "]" : "")); if (!ok) failed.push(n); return ok; };
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const getJ = async (p, ms = 8000) => { try { const r = await fetch(base + p, { signal: AbortSignal.timeout(ms) }); return r.ok ? await r.json() : null; } catch (e) { return null; } };
const getT = async (p, ms = 8000) => { try { const r = await fetch(base + p, { signal: AbortSignal.timeout(ms) }); return r.ok ? await r.text() : ""; } catch (e) { return ""; } };
const send = async (method, p, body) => { const r = await fetch(base + p, { method, headers: { "Content-Type": "application/json" }, body: body === undefined ? undefined : JSON.stringify(body), signal: AbortSignal.timeout(15000) }); let j = null; try { j = await r.json(); } catch (e) { } return { status: r.status, body: j }; };
const unwrap = (s) => (s && s.values) ? s.values : (s || {});
const same = (a, b) => JSON.stringify(a) === JSON.stringify(b);
const copy = (o) => JSON.parse(JSON.stringify(o));
const profileFilters = (cfg) => ((cfg && cfg.filters) || []).filter((f) => f.profile);
const handFilters = (cfg) => ((cfg && cfg.filters) || []).filter((f) => !f.profile);
const specific = (cfg) => ((cfg && cfg.pids) || []).filter((p) => p.type === "specific");
const std = (cfg) => ((cfg && cfg.pids) || []).filter((p) => p.type === "std");
/* E lines in the ring, the e2e bench's noise list minus its transport entry (the bench
   AP has no internet). The wizard's Finish restarts the device inside this run with the
   broker connected: since 2026-10-27 mqtt_manager stops its client before a planned
   restart, so esp-mqtt's errno-113 lines must not appear (they did, twice, before). */
const NOISE = ["data_logger", "esp-tls", "HTTP_CLIENT"];
const ringLines = async () => { const t = await getT("/api/logs/ring"); return t ? t.split("\n").map((l) => l.replace(/\x1b\[[0-9;]*m/g, "")) : null; };
const eCount = async () => { const ls = await ringLines(); if (!ls) return null; return ls.filter((l) => l.includes("E (") && !NOISE.some((n) => l.includes(n))).length; };
/* the lines from the last planned-restart marker on: the old boot's last ms and the new
   boot, however far the ring has rotated since the baseline was taken */
const eSinceRestart = async () => { const ls = await ringLines(); if (!ls) return null; let at = -1; ls.forEach((l, i) => { if (l.includes("restart_tracker: restarting:")) at = i; }); if (at < 0) return null; return ls.slice(at).filter((l) => l.includes("E (") && !NOISE.some((n) => l.includes(n))); };
const waitBack = async (label, tries = 45) => { for (let i = 0; i < tries; i++) { const j = await getJ("/api/info", 3000); if (j) { say(label + ": the DUT is back"); return true; } await sleep(2000); } check(label + ": the DUT came back", false); return false; };

/* what the UI must produce from the published profile */
const frameId = (v) => Number(v) || 0;
const expectedFilters = (car) => (car.filters || []).map((pf) => ({ frame_id: frameId(pf.frame_id), monitor_ms: pf.monitor_ms || 1000, period_ms: pf.period_ms || 0, group: "default", profile: true,
  parameters: (pf.parameters || []).map((x) => ({ ...x, unit: typeof x.unit === "string" ? x.unit.replace(/�/g, "°") : x.unit, expression: String(x.expression || "") })) })).filter((f) => f.frame_id);
const HAND = { frame_id: 1440, monitor_ms: 1000, period_ms: 0, group: "default", enabled: false, parameters: [{ name: "BENCH_HAND", expression: "B0", unit: "", class: "none" }] };

/* ---- the tunnel ---- */
const tun = spawn("ssh", ["-N", "-L", `${BIND}:${PORT}:${DUT}:80`, "rpi001"], { stdio: "ignore" });
let info0 = null;
for (let i = 0; i < 15 && !info0; i++) { await sleep(1000); info0 = await getJ("/api/info", 3000); }
if (!info0) { say("PROFILE FILTERS FAIL: no tunnel to " + DUT + " through rpi001"); tun.kill(); process.exit(2); }
say("DUT", DUT, "reached through rpi001; firmware", info0.firmware_version || info0.version || "?");

/* ---- the published list, for the expectations ---- */
let pub = null;
try { pub = await (await fetch(PROFILES_URL, { signal: AbortSignal.timeout(20000) })).json(); } catch (e) { }
const car = pub && (pub.cars || []).find((c) => c.car_model === PROFILE);
const otherCar = pub && (pub.cars || []).find((c) => c.car_model === OTHER);
if (!check("the published list carries the profile and the other one", !!car && !!otherCar, { cars: pub ? (pub.cars || []).length : 0 })) { tun.kill(); process.exit(2); }
const WANT = expectedFilters(car);
check("the profile carries passive filters, the other does not", WANT.length >= 1 && !(otherCar.filters || []).length, { filters: WANT.length, other: (otherCar.filters || []).length });

/* ---- the state to put back ---- */
const cfg0 = await getJ("/api/autopid/config");
const set0raw = await getJ("/api/settings/autopid");
const set0 = unwrap(set0raw);
const veh0 = await getJ("/api/autopid/vehicles");
const hist0 = await getJ("/api/restart/history");
const status0 = await getJ("/api/status");
const e0 = await eCount();
writeFileSync(`${out}/config_before.json`, JSON.stringify(cfg0, null, 1));
writeFileSync(`${out}/settings_before.json`, JSON.stringify(set0raw, null, 1));
writeFileSync(`${out}/vehicles_before.json`, JSON.stringify(veh0, null, 1));
check("the saved table and the autopid settings were read", !!cfg0 && !!set0raw, { pids: cfg0 ? (cfg0.pids || []).length : null, filters: cfg0 ? (cfg0.filters || []).length : null });
say("before:", (cfg0.pids || []).length, "pids,", (cfg0.filters || []).length, "filters, vehicle", JSON.stringify(set0.vehicle), "E lines", e0, "faults", status0 && status0.health ? status0.health.faults : "?");

/* ---- seed: one hand-made filter beside whatever is there ---- */
const seed = copy(cfg0);
seed.groups = seed.groups && seed.groups.length ? seed.groups : [{ name: "default", enabled_default: true, period_ms: 1000 }];
seed.filters = handFilters(seed).filter((f) => f.frame_id !== HAND.frame_id).concat([HAND]);
{
  const r = await send("PUT", "/api/autopid/config", seed);
  const got = await getJ("/api/autopid/config");
  check("seed: the device took the table with one hand-made filter", r.status === 200 && !!got && handFilters(got).some((f) => f.frame_id === HAND.frame_id) && profileFilters(got).length === 0, { status: r.status, body: r.body, filters: got ? (got.filters || []).length : null });
}

/* ---- Chrome ---- */
const b = await chromium.launch({ channel: "chrome" });
const ctx = await b.newContext({ viewport: { width: 1280, height: 920 } });
const page = await ctx.newPage();
const pageErrors = [];
page.on("pageerror", (e) => pageErrors.push(String(e.message || e)));
page.on("console", (m) => { if (m.type() === "error" && !/ERR_CONNECTION|ERR_EMPTY|ERR_TIMED|ERR_NETWORK|Failed to load resource/.test(m.text())) pageErrors.push(m.text()); });
const shot = async (name) => { try { await page.screenshot({ path: `${out}/${name}.png`, fullPage: false }); } catch (e) { } };
const modalText = async () => page.evaluate(() => { const m = document.querySelector("#modal-root"); return m ? m.textContent : ""; });
const bannerText = async () => page.evaluate(() => { const b = [...document.querySelectorAll(".banner")].find((x) => /staged|applied/.test(x.textContent) && x.style.display !== "none"); return b ? b.textContent : ""; });
const vehValue = async () => page.evaluate(() => { const i = document.querySelector('.frow[data-key="vehicle"] input'); return i ? i.value : null; });

/* a dialog left open (a failed pick) would swallow the next click */
const closeModal = async () => { if (await page.evaluate(() => !!document.querySelector("#modal-root .modal"))) { await page.keyboard.press("Escape"); await sleep(400); } };
const openVehiclePane = async () => {
  await page.goto(base + "/#/automate/parameters"); await sleep(4000);
  await page.locator(".seg button", { hasText: /^Vehicle Specific$/ }).first().click(); await sleep(900);
};
const pick = async (model, label) => {
  /* the picker's search matches the make or the model on its own, never the "Make: Model" name whole */
  const q = model.includes(":") ? model.slice(model.indexOf(":") + 1).trim() : model;
  await closeModal();
  await page.locator("button", { hasText: /Choose profile/ }).first().click();
  try { await page.waitForSelector(".vp-search input", { timeout: 15000 }); } catch (e) { return check(label + ": the picker opened", false); }
  /* the published list arrives from GitHub through the browser */
  let listed = false;
  for (let i = 0; i < 40 && !listed; i++) { listed = /profiles, fetched/.test(await modalText()); if (!listed) await sleep(500); }
  if (!check(label + ": the published list was fetched by the browser", listed, (await modalText()).slice(0, 200))) return false;
  await page.fill(".vp-search input", q); await sleep(700);
  const sel = await page.evaluate(() => !!document.querySelector("#modal-root .vp-row.sel"));
  if (!sel) { const row = page.locator("#modal-root .vp-row").first(); if (await row.count()) { await row.click(); await sleep(300); } }
  const ok = page.locator("#modal-root .acts button", { hasText: /Use this profile/ }).first();
  const enabled = await ok.isEnabled().catch(() => false);
  if (!check(label + ": one model matched and Use this profile is enabled", enabled, await modalText().then((t) => t.slice(0, 200)))) return false;
  await ok.click(); await sleep(900);
  return true;
};
const apply = async (label) => {
  await closeModal();
  const btn = page.locator("button.btn.pri", { hasText: /^Apply Configuration$/ }).last();
  await btn.click();
  let seen = false;
  for (let i = 0; i < 30 && !seen; i++) { seen = /Configuration applied/.test(await modalText()); if (!seen) await sleep(300); }
  if (seen) { await page.locator("#modal-root button", { hasText: /Save, restart later/ }).first().click(); await sleep(1200); }
  else await sleep(1200);
  return check(label + ": Apply ran and offered the settings save (restart later)", seen, (await modalText()).slice(0, 160));
};

try {
/* ---- s1: the Leaf staged ---- */
await openVehiclePane();
const stdBefore = std(await getJ("/api/autopid/config")).length;
if (await pick(PROFILE, "s1")) {
  const bt = await bannerText();
  const nPid = (car.pids || []).length, nF = WANT.length;
  check("s1: the banner counts the profile's PIDs and passive CAN filters", new RegExp(nPid + " PIDs? replaces? the vehicle-specific table, " + nF + " passive CAN filters? \\(frames the car broadcasts\\) replaces? the last profile's filters").test(bt) && /hand-made filters are kept/.test(bt), bt.slice(0, 300));
  check("s1: the vehicle field carries the profile name", (await vehValue()) === PROFILE, await vehValue());
  await shot("s1_staged");
}

/* ---- s2: applied, read back from the device ---- */
if (await apply("s2")) {
  await shot("s2_applied");
  const cfg1 = await getJ("/api/autopid/config");
  writeFileSync(`${out}/config_after_apply.json`, JSON.stringify(cfg1, null, 1));
  const hf = handFilters(cfg1), pf = profileFilters(cfg1);
  check("s2: the hand-made filter is untouched", hf.length === 1 && same(hf[0], HAND), hf);
  check("s2: the profile's filters are on the device, as published, tagged profile", same(pf, WANT), { got: pf.map((f) => [f.frame_id, f.monitor_ms, f.profile, (f.parameters || []).map((p) => p.name + "=" + p.expression)]) });
  check("s2: frame ids as numbers 644 / 474 / 1477", same(pf.map((f) => f.frame_id), WANT.map((f) => f.frame_id)) && pf.every((f) => typeof f.frame_id === "number"), pf.map((f) => f.frame_id));
  check("s2: the specific PIDs went with them, the standard rows stayed", specific(cfg1).length === (car.pids || []).length && std(cfg1).length === stdBefore, { specific: specific(cfg1).length, std: std(cfg1).length });
  const ap = await getJ("/api/autopid");
  const st = (ap && ap.stats) || {};
  const names = ((ap && ap.params) || []).map((p) => p.name);
  check("s2: the runtime counts the four filters after the live reload", st.filters === hf.length + pf.length, { filters: st.filters, pids: st.pids });
  check("s2: the runtime lists the filters' parameters", WANT.every((f) => f.parameters.every((p) => names.includes(p.name))), names.filter((n) => /SPEED|RPM|ODOMETER|BENCH/.test(n)));
  const s1 = await getJ("/api/settings/autopid");
  const v1 = unwrap(s1);
  check("s2: the vehicle setting carries the profile's name and init (saved, restart pending)", v1.vehicle === PROFILE && v1.specific_init === (car.init || "") && v1.specific_enabled !== false, { vehicle: v1.vehicle, init: v1.specific_init, pending: s1 && s1.pending_reboot });
}

/* ---- s3: picked and applied again: no duplicate ---- */
if (await pick(PROFILE, "s3") && await apply("s3")) {
  const cfg2 = await getJ("/api/autopid/config");
  check("s3: still one hand-made and the profile's filters once", handFilters(cfg2).length === 1 && same(profileFilters(cfg2), WANT) && (cfg2.filters || []).length === 1 + WANT.length, (cfg2.filters || []).map((f) => f.frame_id));
  check("s3: the specific PIDs once too", specific(cfg2).length === (car.pids || []).length, specific(cfg2).length);
}

/* ---- s4: a profile without filters replaces them with none ---- */
if (await pick(OTHER, "s4")) {
  const bt = await bannerText();
  check("s4: the banner mentions no filter for a profile without them", bt.length > 0 && !/CAN filter/.test(bt), bt.slice(0, 200));
  if (await apply("s4")) {
    const cfg3 = await getJ("/api/autopid/config");
    writeFileSync(`${out}/config_after_other.json`, JSON.stringify(cfg3, null, 1));
    check("s4: the profile filters are gone, the hand-made one stays", profileFilters(cfg3).length === 0 && handFilters(cfg3).length === 1 && same(handFilters(cfg3)[0], HAND), (cfg3.filters || []).map((f) => f.frame_id));
    check("s4: the other profile's PIDs replaced the Leaf's", specific(cfg3).length === (otherCar.pids || []).length && std(cfg3).length === stdBefore, specific(cfg3).length);
    check("s4: the vehicle setting names the other profile", unwrap(await getJ("/api/settings/autopid")).vehicle === OTHER);
  }
}

/* ---- s5: picked, then Discard: nothing changes ---- */
if (await pick(PROFILE, "s5")) {
  check("s5: the Leaf staged over the other profile", (await vehValue()) === PROFILE && /passive CAN filter/.test(await bannerText()));
  const discard = page.locator(".banner button", { hasText: /^Discard$/ }).first();
  if (check("s5: Discard offered on the banner", (await discard.count()) > 0)) {
    await discard.click(); await sleep(600);
    check("s5: Discard hides the banner and puts the other profile's name back", (await bannerText()) === "" && (await vehValue()) === OTHER, await vehValue());
    await shot("s5_discarded");
    const before = await getJ("/api/autopid/config");
    const btn = page.locator("button.btn.pri", { hasText: /^Apply Configuration$/ }).last();
    await btn.click(); await sleep(1500);
    if (/Configuration applied/.test(await modalText())) { await page.locator("#modal-root button", { hasText: /Save, restart later/ }).first().click(); await sleep(800); }
    const after = await getJ("/api/autopid/config");
    check("s5: Apply after Discard leaves the device's table as it was", same(before.filters, after.filters) && same(specific(before).map((p) => p.cmd), specific(after).map((p) => p.cmd)), { filters: (after.filters || []).map((f) => f.frame_id) });
  }
}

/* ---- w1: the Quick Setup's Finish carries the filters too ---- */
{
  const h2 = async () => page.evaluate(() => (document.querySelector("#view h2") || {}).textContent || "");
  const text = async () => page.evaluate(() => (document.querySelector("#view") || {}).textContent || "");
  await page.goto(base + "/#/setup/vehicle"); await sleep(3000);
  check("w1: the vehicle step", /Your vehicle/.test(await h2()), await h2());
  if (await pick(PROFILE, "w1")) {
    const pill = await page.evaluate(() => (document.querySelector(".qs-radios .sel") || {}).textContent || "");
    check("w1: the profile pill names the Leaf (the car named before the probe)", pill.includes(PROFILE), pill);
    await page.click("#qs-plugged"); await page.click("#qs-ignition");
    await page.click('button:has-text("Detect my vehicle")');
    const t0 = Date.now(); let tv = "";
    for (let i = 0; i < 90; i++) { tv = await text(); if (/Vehicle detected/.test(tv) || /did not answer/.test(tv)) break; await sleep(1000); }
    check("w1: the simulator was detected with the profile named first (" + Math.round((Date.now() - t0) / 1000) + " s)", /Vehicle detected/.test(tv), (tv.match(/[0-9]+ answered[^N]*/) || [])[0]);
    await shot("w1_detected");
    const cont = await page.$(".qs-foot button.pri:not([disabled])");
    check("w1: Continue enabled", !!cont);
    if (cont) {
      await cont.click(); await sleep(2500);
      const keep = await page.$("#qs-pwr-keep"); if (keep) { await keep.click(); await sleep(1500); }
      check("w1: the reading-the-car step", /How WiCAN reads the car/.test(await h2()), await h2());
      await shot("w1_reading");
      await page.click('button:has-text("Finish and restart")'); await sleep(4000);
      check("w1: the done screen", /WiCAN is set up/.test(await h2()), await h2());
      await sleep(12000); await waitBack("w1: after Finish");
      const eAfter = await eSinceRestart();
      check("w1: no E line from the restart on (the broker session ended cleanly first)", Array.isArray(eAfter) && eAfter.length === 0, eAfter === null ? "no restart marker in the ring" : eAfter.slice(0, 3));
      const cfgW = await getJ("/api/autopid/config");
      writeFileSync(`${out}/config_after_wizard.json`, JSON.stringify(cfgW, null, 1));
      check("w1: after the wizard's Finish the device's table carries the profile's filters beside the hand-made one", !!cfgW && same(profileFilters(cfgW), WANT) && handFilters(cfgW).length === 1 && same(handFilters(cfgW)[0], HAND), cfgW ? (cfgW.filters || []).map((f) => f.frame_id) : null);
      check("w1: and the profile's PIDs", !!cfgW && specific(cfgW).length === (car.pids || []).length, cfgW ? specific(cfgW).length : null);
      const vehW = await getJ("/api/autopid/vehicles");
      const curW = ((vehW && vehW.vehicles) || []).find((v) => v.current) || {};
      check("w1: the car's entry carries the profile", curW.profile === PROFILE, { profile: curW.profile, name: curW.name });
      const apW = await getJ("/api/autopid");
      const namesW = ((apW && apW.params) || []).map((x) => x.name);
      check("w1: the runtime lists the filters' parameters after the restart", WANT.every((f) => f.parameters.every((q) => namesW.includes(q.name))), namesW.filter((n) => /SPEED|RPM|ODOMETER/.test(n)));
    }
  }
}
} catch (e) { check("the run threw no exception", false, String((e && e.message) || e).slice(0, 300)); }

/* ---- end: health ---- */
const e1 = await eCount();
const status1 = await getJ("/api/status");
check("no new E line in the ring during the run", e0 !== null && e1 !== null && e1 === e0, { before: e0, after: e1 });
check("no fault raised", !!status1 && !!status1.health && status1.health.faults === status0.health.faults, { before: status0 && status0.health && status0.health.faults, after: status1 && status1.health && status1.health.faults });
check("no page error in Chrome", pageErrors.length === 0, pageErrors.slice(0, 3));
await b.close();

/* ---- restore ---- */
say("restoring the saved table and the autopid settings");
{
  const r = await send("PUT", "/api/autopid/config", cfg0);
  const got = await getJ("/api/autopid/config");
  check("restore: the saved table is back, byte for byte", r.status === 200 && same(got, cfg0), { status: r.status, filters: got ? (got.filters || []).length : null });
}
{
  const s = unwrap(await getJ("/api/settings/autopid"));
  const want = { ...set0 }; delete want.degraded; delete want.pending_reboot;
  const have = { ...s }; delete have.degraded; delete have.pending_reboot;
  if (!same(want, have)) {
    const r = await send("PUT", "/api/settings/autopid", want);
    check("restore: the autopid settings are back (one planned restart)", r.status === 200, r.status);
    await send("POST", "/api/settings/submit", {}).catch(() => { });
    await sleep(12000); await waitBack("restore");
    const s2 = unwrap(await getJ("/api/settings/autopid"));
    const have2 = { ...s2 }; delete have2.degraded; delete have2.pending_reboot;
    check("restore: the settings read back as saved at the start", same(want, have2), { vehicle: s2.vehicle });
    const cfgEnd = await getJ("/api/autopid/config");
    check("restore: the saved table survived the restart", same(cfgEnd, cfg0), cfgEnd ? (cfgEnd.filters || []).length : null);
    const hist1 = await getJ("/api/restart/history");
    check("restore: the restart was planned (no unexpected reset added)", !!hist0 && !!hist1 && hist1.unexpected_resets === hist0.unexpected_resets, { before: hist0 && hist0.unexpected_resets, after: hist1 && hist1.unexpected_resets });
  } else say("the autopid settings never moved: no restart needed");
}
{
  const veh1 = await getJ("/api/autopid/vehicles");
  writeFileSync(`${out}/vehicles_after.json`, JSON.stringify(veh1, null, 1));
  const strip = (v) => ((v && v.vehicles) || []).map((e) => ({ key: e.key, name: e.name, profile: e.profile, init: e.specific_init }));
  if (!same(strip(veh0), strip(veh1)) || (veh0 && veh1 && veh0.current !== veh1.current)) {
    say("the vehicle store moved during the run; putting the entries' name/profile/init back");
    for (const e of strip(veh0)) { const now = strip(veh1).find((x) => x.key === e.key); if (now && !same(now, e)) await send("PUT", "/api/autopid/vehicles/" + encodeURIComponent(e.key), { name: e.name || "", profile: e.profile || "", specific_init: e.init || "" }); }
    const veh2 = await getJ("/api/autopid/vehicles");
    check("restore: the vehicle store is back", same(strip(veh0), strip(veh2)), strip(veh2));
  } else say("the vehicle store did not move");
}
tun.kill();
say(failed.length ? `PROFILE FILTERS FAIL: ${failed.join("; ")}` : "PROFILE FILTERS PASS");
process.exit(failed.length ? 1 : 0);
