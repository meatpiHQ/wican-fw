/* The OBD chip firmware prompt of Quick Setup's vehicle step, on the real
   device (2026-11-08).

   The finding of 2026-11-08: on chip firmware V2.3.22 the warm start the
   bring-up sends kills the chip's automatic protocol search until a power
   cycle, so the vehicle detection and every app's ATSP0 fail to find the car;
   V2.3.24 does not have the fault. Ali's fix: the vehicle step asks for the
   update first (a popup with one button, the progress in it, Done), the
   same offer as a banner when the popup is closed, Detect waiting for the
   chip. The jsdom probe (tools/webui_preview/probe_chip_prompt.mjs) covers
   the screens over the mock; this bench drives them against the chip.

   Legs (the chip is put on V2.3.22 before each: the V2.3.22 image uploaded as
   /data/V2.3.99.txt, the firmware's "the name is the version" contract, the
   obdfw bench's way onto an older chip firmware):

     prompt   the step opens with the popup (both versions, one button),
              Detect disabled with its chip; Update and continue; the
              progress popup with the records written (the API seen running
              meanwhile); Done with Continue; the banner gone; Detect free
              once the acknowledgements are ticked; the chip on V2.3.24,
              ready, not outdated
     banner   (--legs prompt,banner) a tap beside the popup closes it: the
              banner stays with Update now and Detect stays disabled; Update
              now runs the same job to Done

   Afterwards: no new E line in the ring, no unexpected reset, the uploaded
   file deleted, the chip on the packaged version (what the bench found, when
   it found V2.3.24). Needs the DUT on the bench AP (through rpi001), Chrome,
   the Playwright devDependency of tools/webui_preview, and the components
   tree (MEATPI_COMPONENTS_PATH, else the dev layout) for the V2.3.22 image.

   node tools/testbench/obd/chip_prompt_bench.mjs [out dir] [--dut 10.42.1.75] [--port 8089] [--legs prompt,banner] [--fw22 <path>]
   Verdict: CHIP PROMPT PASS / CHIP PROMPT FAIL: <names>. */
import { spawn, spawnSync } from "node:child_process";
import { existsSync, mkdirSync, writeFileSync } from "node:fs";
import { createRequire } from "node:module";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const { chromium } = createRequire(join(here, "..", "..", "webui_preview", "package.json"))("playwright");

const arg = (k, d) => { const i = process.argv.indexOf(k); return i > 0 && process.argv[i + 1] ? process.argv[i + 1] : d; };
const DUT = arg("--dut", "10.42.1.75");
const PORT = Number(arg("--port", "8089"));
const LEGS = arg("--legs", "prompt").split(",").map((s) => s.trim()).filter(Boolean);
const compRoot = process.env.MEATPI_COMPONENTS_PATH || join(here, "..", "..", "..", "..", "..", "components", "meatpi-components", "components");
const FW22 = arg("--fw22", join(compRoot, "obd_chip_manager", "obd_fw", "V2.3.22.txt"));
const out = (process.argv[2] && !process.argv[2].startsWith("--")) ? process.argv[2] : "shots_chip_prompt";
const base = `http://127.0.0.1:${PORT}`;
mkdirSync(out, { recursive: true });

const stamp = () => new Date().toTimeString().slice(0, 8);
const say = (...a) => console.log(stamp(), ...a);
const failed = [];
const check = (n, ok, extra) => { say((ok ? "PASS " : "FAIL ") + n + (extra !== undefined ? "  [" + JSON.stringify(extra).slice(0, 300) + "]" : "")); if (!ok) failed.push(n); return ok; };
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const getJ = async (p, ms = 8000) => { try { const r = await fetch(base + p, { signal: AbortSignal.timeout(ms) }); return r.ok ? await r.json() : null; } catch (e) { return null; } };
const getT = async (p, ms = 8000) => { try { const r = await fetch(base + p, { signal: AbortSignal.timeout(ms) }); return r.ok ? await r.text() : ""; } catch (e) { return ""; } };
const send = async (method, p, body) => { const r = await fetch(base + p, { method, headers: { "Content-Type": "application/json" }, body: body === undefined ? undefined : JSON.stringify(body), signal: AbortSignal.timeout(15000) }); let j = null; try { j = await r.json(); } catch (e) { } return { status: r.status, body: j }; };
/* the image goes up from the Pi (scp, then curl on rpi001 to the DUT's own
   address): the same route from this PC through the tunnel answered 500 on
   the 490 KB body (2026-11-08), the obdfw bench's upload from the Pi has
   always worked */
const upload = (path, localFile) => {
  const name = path.split("/").pop();
  const scp = spawnSync("scp", ["-q", localFile, `rpi001:/tmp/${name}`], { encoding: "utf8" });
  if (scp.status !== 0) { say("scp to rpi001 failed:", (scp.stderr || "").slice(0, 160)); return 0; }
  const r = spawnSync("ssh", ["-o", "BatchMode=yes", "rpi001", `curl -s -m 180 -o /dev/null -w '%{http_code}' -X POST -H 'Content-Type: application/octet-stream' --data-binary @/tmp/${name} 'http://${DUT}/api/fs/upload?path=${path}'; rm -f /tmp/${name}`], { encoding: "utf8" });
  return Number((r.stdout || "").trim()) || 0;
};
const plain = (s) => s.replace(/\x1b\[[0-9;]*m/g, "");
const verOf = (s) => (/V\d+\.\d+\.\d+/.exec(s || "") || [""])[0];
const chipState = () => getJ("/api/obd_chip");
const eLines = async () => plain(await getT("/api/logs/ring")).split("\n").filter((l) => /^\s*E \(/.test(l));
const unexpected = async () => { const h = await getJ("/api/restart/history"); if (!h) return null; const e = Object.entries(h).find(([k]) => /unexpected/.test(k)); return e ? e[1] : JSON.stringify(h).length; };
const waitBack = async (label, tries = 150) => { let inf = null; for (let i = 0; i < tries && !inf; i++) { inf = await getJ("/api/info"); if (!inf) await sleep(1000); } check(`${label}: the device answers`, !!inf, inf && inf.fw_build); return inf; };
const waitChip = async (label, pred, ms) => { const t0 = Date.now(); let s = null; while (Date.now() - t0 < ms) { s = await chipState(); if (s && pred(s)) return s; await sleep(1000); } check(`${label}: the chip reached the expected state within ${ms / 1000} s`, false, s && s.fw); return s; };

/* the chip onto V2.3.22: the obdfw bench's way (the image under a higher name) */
async function putChipOn22(label) {
  const s0 = await chipState();
  if (s0 && verOf(s0.fw && s0.fw.chip) === "V2.3.22") { say(label, "the chip is already on V2.3.22"); return true; }
  const st = upload("/data/V2.3.99.txt", FW22);
  check(`${label}: the V2.3.22 image uploaded as /data/V2.3.99.txt (${st})`, st === 200 || st === 201);
  const r = await send("POST", "/api/obd_chip/update", { path: "/data/V2.3.99.txt" });
  check(`${label}: the write to V2.3.22 started (${r.status})`, r.status === 202, r.body);
  await sleep(3000);
  const s1 = await waitChip(`${label}: the stream to V2.3.22 ends`, (s) => !(s.fw && s.fw.update && s.fw.update.running), 120000);
  const s2 = await waitChip(`${label}: the chip back on V2.3.22, outdated`, (s) => s.ready && verOf(s.fw && s.fw.chip) === "V2.3.22" && s.fw.outdated === true, 60000);
  const del = await send("DELETE", "/api/fs/file?path=/data/V2.3.99.txt");
  check(`${label}: the uploaded file deleted (${del.status})`, del.status === 200);
  return !!(s1 && s2 && s2.fw && s2.fw.outdated);
}

const tun = spawn("ssh", ["-N", "-L", `127.0.0.1:${PORT}:${DUT}:80`, "rpi001"], { stdio: "ignore" });
let browser = null;
try {
  check("the V2.3.22 image is at hand", existsSync(FW22), FW22);
  const inf0 = await waitBack("preflight");
  say("legs:", LEGS.join(", "), "fw:", inf0 && inf0.fw_build);
  const st0 = await chipState();
  check("preflight: the chip is up and the device carries a packaged image", !!(st0 && st0.ready && st0.fw && st0.fw.builtin), st0 && st0.fw);
  const found = st0 && verOf(st0.fw.chip);
  const builtin = st0 && st0.fw.builtin;
  say("as found: chip", found, "device carries", builtin);
  const e0 = (await eLines()).length;
  const u0 = await unexpected();
  writeFileSync(`${out}/as_found_chip.json`, JSON.stringify(st0, null, 1));

  browser = await chromium.launch({ channel: "chrome" });
  const ctx = await browser.newContext({ viewport: { width: 1024, height: 1400 } });
  let page = null;
  const newPage = async () => { if (page) { try { await page.close(); } catch (e) { } } page = await ctx.newPage(); page.on("console", (m) => { if (m.type() === "error" && !/ERR_CONNECTION|ERR_EMPTY|ERR_TIMED|ERR_NETWORK|ERR_ABORTED/.test(m.text())) say("console error:", m.text().slice(0, 160)); }); return page; };
  const modalText = () => page.evaluate(() => ((document.querySelector("#modal-root") || {}).textContent || "")).catch(() => "");
  const viewText = () => page.evaluate(() => ((document.querySelector("#view") || {}).textContent || "")).catch(() => "");
  const h2 = () => page.evaluate(() => ((document.querySelector("#view h2") || {}).textContent || "")).catch(() => "");
  const hasBanner = (sel = "#qs-chip-banner") => page.evaluate((s) => !!document.querySelector(s), sel).catch(() => false);
  const detectDisabled = () => page.evaluate(() => { const b = [...document.querySelectorAll("#view button")].find((x) => /Detect my vehicle/.test(x.textContent)); return b ? b.disabled : null; }).catch(() => null);
  const shot = async (name) => { try { await page.screenshot({ path: `${out}/${name}.png`, fullPage: false }); } catch (e) { say("shot failed", name, e.message.slice(0, 80)); } };
  const waitModal = async (re, ms) => { const t0 = Date.now(); let t = ""; while (Date.now() - t0 < ms) { t = await modalText(); if (re.test(t)) return t; await sleep(400); } return t; };
  const openStep = async (label) => {
    await newPage();
    await page.goto(base + "/#/setup/vehicle"); await sleep(3500);
    check(`${label}: the vehicle step`, /Your vehicle/.test(await h2()), await h2());
  };
  /* the update from the popup or the banner, watched from both sides */
  const runUpdate = async (label, pressed, opts = {}) => {
    const bannerSel = opts.banner || "#qs-chip-banner";
    const seen = { running: 0, lines: 0, total: 0, why: new Set() };
    const watch = (async () => { for (let i = 0; i < 240; i++) { const s = await chipState(); if (s && s.fw && s.fw.update && s.fw.update.running) { seen.running++; seen.lines = s.fw.update.lines; seen.total = s.fw.update.total; } const sl = await getJ("/api/sleep"); if (sl && sl.activity_why) seen.why.add(sl.activity_why); if (s && s.ready && s.fw && !s.fw.outdated && seen.running) break; await sleep(500); } })();
    const prog = await waitModal(/Updating the OBD chip firmware/, 6000);
    check(`${label}: the progress popup after ${pressed}`, /Updating the OBD chip firmware/.test(prog), prog.slice(0, 120));
    const recs = await waitModal(/records/, 15000);
    check(`${label}: the records written are shown`, /records/.test(recs), recs.slice(0, 160));
    await shot(`${label}_progress`);
    const done = await waitModal(/OBD chip updated to/, 150000);
    await watch;
    check(`${label}: Done names the new version (${builtin})`, new RegExp("OBD chip updated to " + builtin.replace(/\./g, "\\.")).test(done), done.slice(0, 120));
    check(`${label}: the API saw the stream running (${seen.running} polls, ${seen.lines} of ${seen.total} records at the last one)`, seen.running > 0 && seen.total > 3000, seen);
    say(label, "sleep hold reasons seen during the job:", [...seen.why].join(", ") || "none (no countdown was running)");
    await shot(`${label}_done`);
    await page.click('#modal-root .acts button:has-text("Continue")'); await sleep(800);
    check(`${label}: the popup closed and the banner is gone`, !/OBD chip updated/.test(await modalText()) && !(await hasBanner(bannerSel)));
    if (opts.detect !== false) {
      /* tick what is not ticked yet: the banner leg ticked both before the
         update, and the re-render after Done keeps them (a second click
         would untick them: the first run's only red, 2026-11-08) */
      for (const id of ["#qs-plugged", "#qs-ignition"]) { if (!(await page.isChecked(id))) await page.click(id); }
      await sleep(200);
      check(`${label}: Detect my vehicle free once the acknowledgements are ticked`, (await detectDisabled()) === false);
    }
    await shot(`${label}_after`);
    const s = await chipState();
    check(`${label}: the chip on ${builtin}, ready, not outdated`, !!(s && s.ready && verOf(s.fw.chip) === builtin && s.fw.outdated === false), s && s.fw);
  };

  if (LEGS.includes("prompt")) {
    const L = "prompt";
    if (await putChipOn22(L)) {
      await openStep(L);
      const m = await modalText();
      check(`${L}: the popup names the fault and both versions`, /needs a firmware update/.test(m) && /V2\.3\.22/.test(m) && new RegExp(builtin.replace(/\./g, "\\.")).test(m), m.slice(0, 160));
      const nBtn = await page.evaluate(() => document.querySelectorAll("#modal-root .acts button").length);
      check(`${L}: one button, Update and continue`, nBtn === 1 && /Update and continue/.test(m));
      check(`${L}: Detect my vehicle disabled with the chip beside it`, (await detectDisabled()) === true && /Update the chip first/.test(await viewText()));
      check(`${L}: the banner behind carries the same offer`, await hasBanner());
      await shot(`${L}_popup`);
      await page.click('#modal-root .acts button:has-text("Update and continue")');
      await runUpdate(L, "Update and continue");
    }
  }
  if (LEGS.includes("banner")) {
    const L = "banner";
    if (await putChipOn22(L)) {
      await openStep(L);
      check(`${L}: the popup`, /needs a firmware update/.test(await modalText()));
      await page.evaluate(() => { const r = document.getElementById("modal-root"); r.dispatchEvent(new MouseEvent("click", { bubbles: true })); }); await sleep(300);
      check(`${L}: a tap beside the popup closes it`, !/needs a firmware update/.test(await modalText()));
      check(`${L}: the banner stays with Update now`, (await hasBanner()) && /Update now/.test(await viewText()));
      await page.click("#qs-plugged"); await page.click("#qs-ignition"); await sleep(200);
      check(`${L}: Detect stays disabled with both acknowledgements ticked`, (await detectDisabled()) === true);
      await shot(`${L}_banner`);
      await page.click('#qs-chip-banner button:has-text("Update now")');
      await runUpdate(L, "Update now");
    }
  }

  if (LEGS.includes("status")) {
    /* the Status page (Ali, 2026-11-08: a device set up before never re-runs
       the wizard): the same popup and banner, the same job */
    const L = "status";
    if (await putChipOn22(L)) {
      await newPage();
      await page.goto(base + "/#/status"); await sleep(4000);
      check(`${L}: the Status page`, /Live overview/.test(await viewText()));
      const m = await modalText();
      check(`${L}: the popup on the Status page names the fault and both versions`, /needs a firmware update/.test(m) && /V2\.3\.22/.test(m) && new RegExp(builtin.replace(/\./g, "\\.")).test(m), m.slice(0, 160));
      check(`${L}: the banner on the Status page with Update now`, (await hasBanner("#chip-fw-banner")) && /Update now/.test(await viewText()));
      await shot(`${L}_popup`);
      await page.click('#modal-root .acts button:has-text("Update and continue")');
      await runUpdate(L, "Update and continue", { banner: "#chip-fw-banner", detect: false });
      await sleep(4500);   /* the page's next refresh keeps the banner away */
      check(`${L}: the banner stays away on the next refresh`, !(await hasBanner("#chip-fw-banner")));
    }
  }

  /* the end: the ring, the resets, the file, the chip as found */
  const e1 = await eLines();
  check(`no new E line in the ring (${e1.length - e0 > 0 ? e1.length - e0 : 0} new)`, e1.length <= e0, e1.slice(e0).map((l) => l.trim().slice(0, 140)));
  const u1 = await unexpected();
  check("no unexpected reset", u1 === u0, { before: u0, after: u1 });
  const files = await getJ("/api/fs/list?path=/data");
  const left = files && JSON.stringify(files).includes("V2.3.99.txt");
  check("no uploaded image left under /data", !left);
  const sEnd = await chipState();
  if (found && found !== builtin) say("NOTE: the chip was found on", found, "and is left on", builtin, "(the packaged version; the bench does not downgrade a chip)");
  check(`the chip ends on ${builtin}, ready`, !!(sEnd && sEnd.ready && verOf(sEnd.fw.chip) === builtin), sEnd && sEnd.fw);
} catch (e) {
  check("the bench ran to its end", false, e.message);
} finally {
  if (browser) { try { await browser.close(); } catch (e) { } }
  tun.kill();
}
console.log(failed.length ? "CHIP PROMPT FAIL: " + failed.join("; ") : "CHIP PROMPT PASS");
process.exit(failed.length ? 1 : 0);
