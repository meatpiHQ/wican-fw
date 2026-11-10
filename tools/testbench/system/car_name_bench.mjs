/* The car's name in the header, on the real device (2026-11-10).

   A user's request: the car's name at the top of every page. The page shows
   the current car's name (the vehicle store's `name`, from the status poll's
   `vehicle` summary) as a first line inside the header pill and on the
   browser tab; a rename from the header is staged into Submit Changes and
   restarts the device like a setting (Ali, 2026-11-10: one write of the
   store file per submit, not one per edit). The jsdom probe
   (tools/webui_preview/probe_car_name.mjs) covers the screens over the mock;
   this bench drives them against the device, in Chrome through its own
   tunnel (ssh -L through rpi001).

   Legs:
     show     the header shows the stored name of the current car, the pill
              in its two-line form with the id and the address, the tab
              title follows
     rename   the popover holds the stored name; Set name sends NOTHING (the
              store still has the old name, the boot count has not moved);
              Submit, Continue: the device restarts once, planned; back up,
              the store and the header carry the new name
     restore  the name as found goes back the same way (the second planned
              restart)

   Afterwards: the stored name as found, no unexpected reset, no new E line
   in the ring. Needs a current car in the vehicle store (the bench unit has
   the simulator's), the DUT on the bench AP, Chrome and the Playwright
   devDependency of tools/webui_preview.

   node tools/testbench/system/car_name_bench.mjs [out dir] [--dut 10.42.1.75] [--port 8091]
   Verdict: CAR NAME PASS / CAR NAME FAIL: <names>. */
import { spawn } from "node:child_process";
import { mkdirSync } from "node:fs";
import { createRequire } from "node:module";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const { chromium } = createRequire(join(here, "..", "..", "webui_preview", "package.json"))("playwright");

const arg = (k, d) => { const i = process.argv.indexOf(k); return i > 0 && process.argv[i + 1] ? process.argv[i + 1] : d; };
const DUT = arg("--dut", "10.42.1.75");
const PORT = Number(arg("--port", "8091"));
const out = (process.argv[2] && !process.argv[2].startsWith("--")) ? process.argv[2] : "shots_car_name";
const base = `http://127.0.0.1:${PORT}`;
mkdirSync(out, { recursive: true });

const stamp = () => new Date().toTimeString().slice(0, 8);
const say = (...a) => console.log(stamp(), ...a);
const failed = [];
const check = (n, ok, extra) => { say((ok ? "PASS " : "FAIL ") + n + (extra !== undefined ? "  [" + JSON.stringify(extra).slice(0, 300) + "]" : "")); if (!ok) failed.push(n); return ok; };
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const getJ = async (p, ms = 8000) => { try { const r = await fetch(base + p, { signal: AbortSignal.timeout(ms) }); return r.ok ? await r.json() : null; } catch (e) { return null; } };
const getT = async (p, ms = 8000) => { try { const r = await fetch(base + p, { signal: AbortSignal.timeout(ms) }); return r.ok ? await r.text() : ""; } catch (e) { return ""; } };
const plain = (s) => s.replace(/\x1b\[[0-9;]*m/g, "");
const eLines = async () => plain(await getT("/api/logs/ring")).split("\n").filter((l) => /^\s*E \(/.test(l));
const history = () => getJ("/api/restart/history");
const currentCar = async () => { const v = await getJ("/api/autopid/vehicles"); return v ? (v.vehicles || []).find((e) => e.current) || null : null; };
/* the device is back when its boot count moved past `after` */
const waitBoot = async (after, ms = 150000) => { const t0 = Date.now(); while (Date.now() - t0 < ms) { const h = await history(); if (h && h.boot_count > after) return h; await sleep(1500); } return null; };

const tun = spawn("ssh", ["-N", "-L", `127.0.0.1:${PORT}:${DUT}:80`, "rpi001"], { stdio: "ignore" });
let browser = null;
let found = null;
let planned = 0;
try {
  let info = null;
  for (let i = 0; i < 20 && !info; i++) { info = await getJ("/api/info", 3000); if (!info) await sleep(500); }
  if (!check("the device answers through the tunnel", !!info, info && (info.fw_version || info.version))) throw new Error("no device");
  found = await currentCar();
  if (!check("the vehicle store has a current car", !!found, found && found.name)) throw new Error("no current car: detect one first (Quick Setup's vehicle step)");
  const h0 = await history();
  const unexpected0 = h0.unexpected_resets;
  const e0 = (await eLines()).length;
  say(`as found: "${found.name}" (${found.key}), boot ${h0.boot_count}, unexpected ${unexpected0}, E lines ${e0}`);

  browser = await chromium.launch({ channel: "chrome" });
  const ctx = await browser.newContext({ viewport: { width: 1280, height: 800 }, colorScheme: "dark" });
  const page = await ctx.newPage();
  const pageErrs = [];
  page.on("pageerror", (e) => pageErrs.push(e.message));
  const open = async () => { await page.goto(base + "/#/status", { waitUntil: "domcontentloaded" }); await page.waitForFunction(() => { const b = document.querySelector("#cp-name"); return b && !b.hidden; }, null, { timeout: 30000 }); };
  const header = () => page.evaluate(() => ({ name: document.querySelector("#cp-name-t").textContent, named: document.querySelector("#connpill").classList.contains("named"),
    staged: document.querySelector("#cp-name").classList.contains("staged"), id: document.querySelector("#cp-id").textContent, ip: document.querySelector("#cp-ip").textContent,
    title: document.title, submit: document.querySelector("#submitlabel").textContent, pillH: Math.round(document.querySelector("#connpill").getBoundingClientRect().height),
    hdrH: Math.round(document.querySelector(".app-header").getBoundingClientRect().height), newChip: !document.querySelector("#cp-name-new").hidden }));

  /* one rename through the page: popover, Set name, nothing sent, Submit, restart, the name stored */
  async function rename(label, from, to) {
    const boot0 = (await history()).boot_count;
    await page.click("#cp-name");
    await page.waitForSelector("#cp-name-pop", { timeout: 5000 });
    check(`${label}: the popover holds the stored name`, (await page.inputValue("#cp-name-input")) === from, await page.inputValue("#cp-name-input"));
    await page.screenshot({ path: join(out, `${label}_popover.png`), clip: { x: 0, y: 0, width: 1280, height: 420 } });
    await page.fill("#cp-name-input", to);
    await page.click("#cp-name-pop .acts .btn.pri");
    await sleep(300);
    const s = await header();
    check(`${label}: the header shows the new name as not saved, one change on Submit`, s.name === to && s.staged && s.submit === "Submit Changes (1)", s);
    await sleep(4000);
    const mid = await currentCar();
    const hm = await history();
    check(`${label}: Set name sent nothing (the store keeps "${from}", the boot count stands)`, !!mid && mid.name === from && hm.boot_count === boot0, { stored: mid && mid.name, boot: hm && hm.boot_count });
    await page.screenshot({ path: join(out, `${label}_staged.png`), clip: { x: 0, y: 0, width: 1280, height: 120 } });
    await page.click("#submitbtn");
    await page.waitForSelector("#modal-root .acts button", { timeout: 5000 });
    check(`${label}: Submit says it restarts`, /restarts WiCAN/.test(await page.textContent("#modal-root")));
    await page.locator("#modal-root .acts button", { hasText: "Continue" }).click();
    planned++;
    const hb = await waitBoot(boot0);
    check(`${label}: the device restarted once`, !!hb && hb.boot_count === boot0 + 1, hb && hb.boot_count);
    const rec = hb && (hb.records || []).find((r) => r.seq === hb.boot_count) || (hb && (hb.records || [])[0]);
    check(`${label}: the restart is a planned one`, !!rec && rec.planned === true, rec && { reason: rec.reason, planned: rec.planned, planned_reason: rec.planned_reason, source: rec.source });
    const after = await currentCar();
    check(`${label}: the store carries "${to}"`, !!after && after.name === to && after.key === found.key, after && after.name);
    await open();
    const s2 = await header();
    check(`${label}: the header and the tab carry "${to}"`, s2.name === to && !s2.staged && s2.title === to + " · WiCAN Pro" && s2.submit === "Saved", s2);
  }

  /* ---- show ---- */
  await open();
  const s0 = await header();
  check("show: the header shows the stored name", s0.name === found.name, s0);
  check("show: the pill in its two-line form, inside the 58 px header", s0.named && s0.pillH <= 50 && s0.hdrH === 58, { pillH: s0.pillH, hdrH: s0.hdrH });
  check("show: the id and the address under the name", /^WiCAN_/i.test(s0.id) && s0.ip.length > 6, { id: s0.id, ip: s0.ip });
  check("show: the tab title follows", s0.title === found.name + " · WiCAN Pro", s0.title);
  check("show: the 'not set up' chip matches the store", s0.newChip === (found.pending_profile === true), { chip: s0.newChip, pending: found.pending_profile });
  await page.screenshot({ path: join(out, "show_header.png"), clip: { x: 0, y: 0, width: 1280, height: 120 } });

  /* ---- rename, restore ---- */
  const tmp = "Bench car " + String(Date.now()).slice(-4);
  await rename("rename", found.name, tmp);
  await rename("restore", tmp, found.name);

  /* ---- afterwards ---- */
  const end = await currentCar();
  check("afterwards: the stored name as found", !!end && end.name === found.name, end && end.name);
  const h1 = await history();
  check("afterwards: no unexpected reset", !!h1 && h1.unexpected_resets === unexpected0, h1 && h1.unexpected_resets);
  const e1 = await eLines();
  check("afterwards: no E line in the ring", e1.length === 0, e1.slice(0, 3));
  check("afterwards: no JS error on the page", pageErrs.length === 0, pageErrs.slice(0, 3));
  say(`planned restarts: ${planned}`);
} catch (e) {
  check("the bench ran to its end", false, String(e && e.message || e));
  /* a rename left behind goes back over the API */
  try {
    const cur = await currentCar();
    if (found && cur && cur.name !== found.name) {
      await fetch(base + "/api/autopid/vehicles/" + encodeURIComponent(found.key), { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name: found.name }), signal: AbortSignal.timeout(8000) });
      say("the name put back over the API");
    }
  } catch (_) { say("WARNING: the name could not be put back:", found && found.name); }
} finally {
  if (browser) await browser.close();
  tun.kill();
}
console.log(failed.length ? "CAR NAME FAIL: " + failed.join("; ") : "CAR NAME PASS");
process.exit(failed.length ? 1 : 0);
