/* The standard PIDs stay the user's choice, on the real device (2026-10-09).

   Born from Ali's report "when I don't select any standard PIDs in Quick Setup and
   later enable the standard PIDs, they are all enabled by default; I want the user
   to enable the PIDs they want", and his rule later that day: "it should not start
   polling unless the user enables the standard PIDs and enables the PIDs they want".
   Reproduced on the bench DUT with the ECU simulator as the car
   (test-reports/logs/std_none_20261009/): the plain path was clean, three others
   were not: a page reload between the detection and Finish then Detect again
   pre-ticked every row the first detection had stored; "Skip for now" let the device
   store every row ENABLED at its own detection (16 polled once the standard set was
   switched on); a reload then Skip for now left the stored rows polling.

   The rule now: the rows a detection stores are OFF, always; nothing standard is
   read until the standard-PID switch is on AND the user ticked rows (the wizard's
   picker, or Automate > Parameters with Apply Configuration).

   Four legs in one run, each starting with the simulator's car forgotten
   (DELETE /api/autopid/vehicles/<key>), the wizard's vehicle half driven in Chrome
   through an ssh tunnel to the DUT, the device state read over the API afterwards:

     plain     detect (the rows are stored OFF whatever the switch says), choose
               none, Finish: no standard row after the restart, the standard set
               off, the first contact adds nothing
     redetect  detect, choose none, reload the page, detect again: "none chosen
               yet" (not "16 of 16 chosen"); Finish leaves no standard row
     skip      the standard set ON (a fresh device), Skip for now, restart: the
               device meets the car by itself and stores its 16 rows OFF (the I
               line says so), polls nothing; the Standard PIDs table shows 16
               switches off; two rows ticked there and Apply Configuration: exactly
               those two read, live, no restart
     reload    detect (rows stored off), choose none, reload, Skip for now: the 16
               rows stay, all off

   At the end the saved config and the autopid settings go back (a restart when the
   settings moved). Needs the ECU simulator answering on the bus, the DUT on the Pi's
   hotspot, the Playwright devDependency of tools/webui_preview (`npm install` there
   once).

   node tools/testbench/obd/std_choice_bench.mjs [out dir] [--dut 10.42.1.194] [--legs plain,redetect,skip,reload]
   Verdict: STD CHOICE PASS / STD CHOICE FAIL: <names>. */
import { spawn } from "node:child_process";
import { mkdirSync, writeFileSync } from "node:fs";
import { createRequire } from "node:module";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const { chromium } = createRequire(join(here, "..", "..", "webui_preview", "package.json"))("playwright");

const arg = (k, d) => { const i = process.argv.indexOf(k); return i > 0 && process.argv[i + 1] ? process.argv[i + 1] : d; };
const DUT = arg("--dut", "10.42.1.194");
const LEGS = arg("--legs", "plain,redetect,skip,reload").split(",").map((s) => s.trim()).filter(Boolean);
const out = (process.argv[2] && !process.argv[2].startsWith("--")) ? process.argv[2] : "shots_std_choice";
const BIND = "127.0.0.1", PORT = 8079;
const base = `http://${BIND}:${PORT}`;
mkdirSync(out, { recursive: true });

const stamp = () => new Date().toTimeString().slice(0, 8);
const say = (...a) => console.log(stamp(), ...a);
const failed = [];
const check = (n, ok, extra) => { say((ok ? "PASS " : "FAIL ") + n + (extra !== undefined ? "  [" + JSON.stringify(extra).slice(0, 400) + "]" : "")); if (!ok) failed.push(n); return ok; };
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const getJ = async (p, ms = 8000) => { try { const r = await fetch(base + p, { signal: AbortSignal.timeout(ms) }); return r.ok ? await r.json() : null; } catch (e) { return null; } };
const getT = async (p, ms = 8000) => { try { const r = await fetch(base + p, { signal: AbortSignal.timeout(ms) }); return r.ok ? await r.text() : ""; } catch (e) { return ""; } };
const send = async (method, p, body) => { const r = await fetch(base + p, { method, headers: { "Content-Type": "application/json" }, body: body === undefined ? undefined : JSON.stringify(body), signal: AbortSignal.timeout(15000) }); let j = null; try { j = await r.json(); } catch (e) { } return { status: r.status, body: j }; };
const info = () => getJ("/api/info");
const stdRows = (cfg) => ((cfg && cfg.pids) || []).filter((x) => x.type === "std").map((x) => ({ cmd: x.cmd, on: x.enabled !== false }));
const brief = (rows) => rows.length + " rows, " + rows.filter((r) => r.on).length + " on";
const unwrap = (s) => (s && s.values) ? s.values : (s || {});
const plain = (s) => s.replace(/\x1b\[[0-9;]*m/g, "");
/* the cache lists every configured parameter; only a polled one carries a value */
const polledNames = (live) => ((live && live.params) || []).filter((p) => !p.external && p.value != null).map((p) => p.name);
const waitBack = async (label, tries = 150) => { let inf = null; for (let i = 0; i < tries && !inf; i++) { inf = await info(); if (!inf) await sleep(1000); } check(`${label}: the device answers`, !!inf, inf && inf.fw_version); return inf; };
const restartAndWait = async (label) => { await send("POST", "/api/restart", {}).catch(() => { }); await sleep(12000); return waitBack(label); };
/* the first contact of a boot: wait for the identity line, then for a detection to end */
const firstContact = async (label) => {
  let ring = "";
  for (let i = 0; i < 24; i++) {
    await sleep(5000); ring = plain(await getT("/api/logs/ring"));
    if (/detection done/.test(ring)) break;
    if (/vehicle identity/.test(ring) && !/detecting/.test(ring)) break;   /* the same car: no job follows */
  }
  /* the ring survives a warm restart: only this boot's lines count (from the
     store's boot line on) */
  const all = ring.split("\n").filter((l) => /autopid: (vehicle|detection|new vehicle|config)/.test(l)).map((l) => l.trim());
  let bootAt = -1;
  all.forEach((l, i) => { if (/autopid: vehicle store: \d+ cars/.test(l)) bootAt = i; });
  const lines = bootAt >= 0 ? all.slice(bootAt) : all;
  writeFileSync(`${out}/${label}_ring.log`, ring);
  return lines;
};
/* the standard set as a leg needs it; a change restarts */
const ensureStd = async (on, label) => {
  const s = unwrap(await getJ("/api/settings/autopid"));
  if (s.std_enabled === on) { say(label, "the standard set is already", on ? "on" : "off"); return; }
  delete s.degraded; delete s.pending_reboot; s.std_enabled = on;
  const r = await send("PUT", "/api/settings/autopid", s);
  await send("POST", "/api/settings/submit", {}); await sleep(12000);
  await waitBack(`${label}: the standard set switched ${on ? "on" : "off"} (PUT ${r.status})`);
};

const tun = spawn("ssh", ["-N", "-L", `${BIND}:${PORT}:${DUT}:80`, "rpi001"], { stdio: "ignore" });
const inf0 = await waitBack("preflight");
say("legs:", LEGS.join(", "), "fw:", inf0 && inf0.fw_version);
const cfg0 = await getJ("/api/autopid/config");
const veh0 = await getJ("/api/autopid/vehicles");
const set0 = unwrap(await getJ("/api/settings/autopid"));
writeFileSync(`${out}/before_config.json`, JSON.stringify(cfg0, null, 1));
writeFileSync(`${out}/before_vehicles.json`, JSON.stringify(veh0, null, 1));
writeFileSync(`${out}/before_settings_autopid.json`, JSON.stringify(set0, null, 1));
say("before:", brief(stdRows(cfg0)), "std_enabled", set0.std_enabled, "cars", ((veh0 && veh0.vehicles) || []).map((v) => v.key + (v.current ? "*" : "")).join(" "));

const b = await chromium.launch({ channel: "chrome" });
const ctx = await b.newContext({ viewport: { width: 1024, height: 1500 } });
let page = null;
const newPage = async () => { if (page) { try { await page.close(); } catch (e) { } } page = await ctx.newPage(); page.on("console", (m) => { if (m.type() === "error" && !/ERR_CONNECTION|ERR_EMPTY|ERR_TIMED|ERR_NETWORK|ERR_ABORTED/.test(m.text())) say("console error:", m.text().slice(0, 160)); }); return page; };
const h2 = () => page.evaluate(() => (document.querySelector("#view h2") || {}).textContent || "").catch(() => "");
const text = () => page.evaluate(() => (document.querySelector("#view") || {}).textContent || "").catch(() => "");
const shot = async (name) => { try { await page.evaluate(() => { const c = document.querySelector("main.content"); if (c) c.scrollTop = 0; }); await sleep(300); await page.locator("#view").screenshot({ path: `${out}/${name}.png` }); } catch (e) { say("shot failed", name, e.message.slice(0, 80)); } };

const forgetCar = async (label) => {
  const v = await getJ("/api/autopid/vehicles");
  for (const e of (v && v.vehicles) || []) { const r = await send("DELETE", "/api/autopid/vehicles/" + encodeURIComponent(e.key)); say(label, "forget", e.key, "->", r.status); }
  const v2 = await getJ("/api/autopid/vehicles");
  check(`${label}: the store has no car (a first setup)`, v2 && (v2.vehicles || []).length === 0);
};
const openVehicle = async (label) => {
  await newPage();
  await page.goto(base + "/#/setup/vehicle"); await sleep(3000);
  check(`${label}: the vehicle step`, /Your vehicle/.test(await h2()), await h2());
};
const detect = async (label) => {
  await page.click("#qs-plugged"); await page.click("#qs-ignition");
  await page.click('button:has-text("Detect my vehicle")');
  const t0 = Date.now();
  for (let i = 0; i < 90 && !/1\. Vehicle detected/.test(await text()) && !/did not answer/.test(await text()); i++) await sleep(1000);
  const tv = await text();
  check(`${label}: the simulator was detected (${Math.round((Date.now() - t0) / 1000)} s)`, /1\. Vehicle detected/.test(tv), (tv.match(/\d+ answered[^N]*/) || [])[0]);
  return tv;
};
const chooseNone = async (label) => {
  const openBtn = (await page.$('button:has-text("Choose PIDs")')) || (await page.$('button:has-text("Change")'));
  await openBtn.click(); await sleep(500);
  await page.click('#modal-root button:has-text("Clear")'); await sleep(100);
  await page.click('#modal-root button:has-text("Add selected")'); await sleep(500);
  check(`${label}: none chosen on the step`, /none chosen yet/.test(await text()));
};
const finishNone = async (label) => {
  const none = await page.$("#qs-prof-none"); if (none) { await none.click(); await sleep(300); }
  const cont = await page.$(".qs-foot button.pri:not([disabled])");
  check(`${label}: Continue enabled`, !!cont);
  await cont.click(); await sleep(2500);
  const keep = await page.$("#qs-pwr-keep"); if (keep) { await keep.click(); await sleep(1500); }
  check(`${label}: the Reading the car step`, /How WiCAN reads the car/.test(await h2()), await h2());
  const sw = await page.$("#qs-std");
  check(`${label}: the Standard PIDs switch off and greyed (none chosen)`, sw && !(await sw.isChecked()) && (await sw.isDisabled()));
  await shot(`${label}_reading`);
  await page.click('button:has-text("Finish and restart")'); await sleep(4000);
  check(`${label}: the done screen`, /WiCAN is set up/.test(await h2()), await h2());
  await sleep(12000);
  await waitBack(`${label}: after Finish`);
};
const skipForNow = async (label) => {
  const skipBtn = await page.$('button:has-text("Skip for now")');
  check(`${label}: Skip for now offered`, !!skipBtn);
  await skipBtn.click(); await sleep(2000);
  check(`${label}: the done screen`, /WiCAN is set up/.test(await h2()), await h2());
};
/* Automate > Parameters, the Standard PIDs tab */
const openStandardTab = async () => {
  await newPage();
  await page.goto(base + "/#/automate/parameters"); await sleep(4000);
  const seg = await page.$('button:has-text("Standard PIDs")'); if (seg) { await seg.click(); await sleep(1500); }
  return page.evaluate(() => [...document.querySelectorAll("#view .pidrow")].map((r) => ({ cmd: ((r.querySelector(".req") || {}).textContent || "").trim().split(" ")[0], off: r.classList.contains("off"), on: !!(r.querySelector('input[type="checkbox"]') || {}).checked })));
};

/* ---- plain ---- */
if (LEGS.includes("plain")) {
  const L = "plain";
  await forgetCar(L);
  await openVehicle(L);
  await detect(L);
  const after = stdRows(await getJ("/api/autopid/config"));
  check(`${L}: the detection stored the rows the car answers, every one OFF (the standard set is ${set0.std_enabled ? "on" : "off"})`, after.length >= 10 && after.every((r) => !r.on), brief(after));
  await chooseNone(L);
  await finishNone(L);
  const cfg1 = stdRows(await getJ("/api/autopid/config"));
  const set1 = unwrap(await getJ("/api/settings/autopid"));
  check(`${L}: no standard row after the restart, the standard set off`, cfg1.length === 0 && set1.std_enabled === false, { rows: brief(cfg1), std_enabled: set1.std_enabled });
  const lines = await firstContact(L);
  say(L, "first contact:\n   " + lines.slice(-6).join("\n   "));
  const cfg2 = stdRows(await getJ("/api/autopid/config"));
  check(`${L}: the first contact (same car) adds no row`, cfg2.length === 0 && lines.some((l) => /vehicle identity: vin/.test(l)) && !lines.some((l) => /new vehicle/.test(l)), brief(cfg2));
}

/* ---- redetect ---- */
if (LEGS.includes("redetect")) {
  const L = "redetect";
  await forgetCar(L);
  await openVehicle(L);
  await detect(L);
  await chooseNone(L);
  say(L, "reloading the page (a phone discarding the tab)");
  await page.reload(); await sleep(3500);
  check(`${L}: after the reload the step is back at Detect`, /1\. Detect the vehicle/.test(await text()));
  const tv = await detect(L + " again");
  const m = tv.match(/(\d+) of (\d+) chosen/);
  check(`${L}: the second detection ticks nothing (the car's setup never finished)`, /none chosen yet/.test(tv) && !m, m ? m[0] : "none chosen yet");
  await shot(`${L}_second_detection`);
  await finishNone(L);
  const cfg1 = stdRows(await getJ("/api/autopid/config"));
  const set1 = unwrap(await getJ("/api/settings/autopid"));
  check(`${L}: no standard row after the restart, the standard set off`, cfg1.length === 0 && set1.std_enabled === false, { rows: brief(cfg1), std_enabled: set1.std_enabled });
}

/* ---- skip ---- */
if (LEGS.includes("skip")) {
  const L = "skip";
  await ensureStd(true, L);               /* a fresh device: the switch is on by default */
  await forgetCar(L);
  await openVehicle(L);
  await skipForNow(L);
  await restartAndWait(`${L}: after the restart`);
  const lines = await firstContact(L);
  say(L, "first contact:\n   " + lines.slice(-8).join("\n   "));
  const cfg1 = stdRows(await getJ("/api/autopid/config"));
  check(`${L}: the device met the car by itself and stored its rows OFF, saying so`, cfg1.length >= 10 && cfg1.every((r) => !r.on) && lines.some((l) => /new vehicle/.test(l)) && lines.some((l) => /stored off/.test(l)), brief(cfg1));
  await sleep(15000);
  const live0 = await getJ("/api/autopid");
  writeFileSync(`${out}/${L}_live_before_tick.json`, JSON.stringify(live0, null, 1));
  check(`${L}: with the standard set on, nothing is read until a row is ticked`, polledNames(live0).length === 0 && ((live0 || {}).stats || {}).polls_ok === 0, { polled: polledNames(live0).slice(0, 5), polls_ok: ((live0 || {}).stats || {}).polls_ok });
  /* the user ticks two rows under Automate > Parameters and applies (live, no restart) */
  const rowsBefore = await openStandardTab();
  check(`${L}: the Standard PIDs table lists the stored rows with every switch off`, rowsBefore.length === cfg1.length && rowsBefore.every((r) => r.off && !r.on), { rows: rowsBefore.length, on: rowsBefore.filter((r) => r.on).length });
  await shot(`${L}_table_off`);
  const ticked = await page.evaluate(() => {
    const want = ["010C", "010D"], done = [];
    for (const r of document.querySelectorAll("#view .pidrow")) {
      const cmd = ((r.querySelector(".req") || {}).textContent || "").trim().split(" ")[0];
      if (want.includes(cmd)) { const cb = r.querySelector('input[type="checkbox"]'); if (cb && !cb.checked) { cb.click(); done.push(cmd); } }
    }
    return done;
  });
  check(`${L}: two rows ticked (engine speed, vehicle speed)`, ticked.length === 2, ticked);
  await sleep(400);
  await page.click('button:has-text("Apply Configuration")'); await sleep(2500);
  const modalTxt = await page.evaluate(() => (document.querySelector("#modal-root") || {}).textContent || "");
  check(`${L}: Apply is live (no settings changed: no restart popup)`, !/Save and restart now/.test(modalTxt), modalTxt.slice(0, 80));
  await shot(`${L}_table_two_on`);
  const cfg2 = stdRows(await getJ("/api/autopid/config"));
  check(`${L}: the config carries exactly the two ticked rows on`, cfg2.filter((r) => r.on).map((r) => r.cmd).sort().join(",") === "010C,010D" && cfg2.length === cfg1.length, brief(cfg2));
  await sleep(15000);
  const live1 = await getJ("/api/autopid");
  writeFileSync(`${out}/${L}_live_after_tick.json`, JSON.stringify(live1, null, 1));
  const polled = polledNames(live1).sort();
  check(`${L}: exactly the two ticked rows read, live, without a restart`, polled.join(",") === "EngineRPM,VehicleSpeed" && ((live1 || {}).stats || {}).polls_ok > 0, { polled, polls_ok: ((live1 || {}).stats || {}).polls_ok });
}

/* ---- reload ---- */
if (LEGS.includes("reload")) {
  const L = "reload";
  await forgetCar(L);
  await openVehicle(L);
  await detect(L);
  const stored = stdRows(await getJ("/api/autopid/config"));
  check(`${L}: the wizard's detection stored the rows OFF`, stored.length >= 10 && stored.every((r) => !r.on), brief(stored));
  await chooseNone(L);
  await page.reload(); await sleep(3500);
  await skipForNow(L);
  const cfg1 = stdRows(await getJ("/api/autopid/config"));
  check(`${L}: the rows the device stored stay, all off: nothing reads until the user ticks`, cfg1.length === stored.length && cfg1.every((r) => !r.on), brief(cfg1));
}

/* ---- restore ---- */
await b.close();
say("restoring the config and the autopid settings saved at the start");
if (cfg0) { const r = await send("PUT", "/api/autopid/config", cfg0); check("restore: the saved config is back", r.status === 200, r.status); }
{
  const s = unwrap(await getJ("/api/settings/autopid"));
  const want = { ...set0 }; delete want.degraded; delete want.pending_reboot;
  const have = { ...s }; delete have.degraded; delete have.pending_reboot;
  if (JSON.stringify(want) !== JSON.stringify(have)) {
    const r = await send("PUT", "/api/settings/autopid", want);
    check("restore: the autopid settings are back (one restart)", r.status === 200, r.status);
    await send("POST", "/api/settings/submit", {}); await sleep(12000); await waitBack("restore");
  }
}
const cfgEnd = stdRows(await getJ("/api/autopid/config"));
say("end:", brief(cfgEnd), "std_enabled", unwrap(await getJ("/api/settings/autopid")).std_enabled);
tun.kill();
say(failed.length ? `STD CHOICE FAIL: ${failed.join("; ")}` : "STD CHOICE PASS");
process.exit(failed.length ? 1 : 0);
