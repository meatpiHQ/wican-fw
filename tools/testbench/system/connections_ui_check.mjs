/* The Connections page on the REAL device (2026-10-29): Chrome through the
   tunnel, with a connection stranded by its server's removal planted by
   connections_bench.py --ui (br_bench -> tcp3300, no such server on the
   device). The field report of 2026-10-28: such rows locked a device out of
   every settings page and could not be removed.

     u1  Settings > Connections opened: nothing staged (the Submit button reads
         Saved), no page error
     u2  the stranded row shows as CAN bus ⇄ TCP server, port 3300, with the
         missing server named and a trash button
     u3  its trash button stages Bridges alone ("Submit Changes (1)")
     u4  Submit and the confirm: the device takes the list, restarts, the row is
         gone from the device, no E line since the restart
     u5  Add connection, CAN bus ⇄ TCP server port 3300, raw: Bridges and Socket
         servers staged together, Submit, the restart: the new bridge is up on
         /api/bridges, tcp3300 up on /api/sockets and carried by it

   Prints `restarts=N` (the python adds them up) and CONNECTIONS UI PASS / FAIL.
   Saves screenshots next to the run's log.
   node connections_ui_check.mjs <base url, e.g. http://127.0.0.1:18081>
        [--port 3300] [--bridge br_bench] [--out dir] */
import { mkdirSync } from "node:fs";
import { createRequire } from "node:module";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
const here = dirname(fileURLToPath(import.meta.url));
const { chromium } = createRequire(join(here, "..", "..", "webui_preview", "package.json"))("playwright");

const arg = (k, d) => { const i = process.argv.indexOf(k); return i > 0 && process.argv[i + 1] ? process.argv[i + 1] : d; };
const base = (process.argv[2] && !process.argv[2].startsWith("--") ? process.argv[2] : "http://127.0.0.1:18081").replace(/\/$/, "");
const PORT = Number(arg("--port", "3300"));
const BRIDGE = arg("--bridge", "br_bench");
const JACK = "tcp" + PORT;
const out = arg("--out", join(here, "..", "..", "..", "test-reports", "logs", "connections_ui_" + new Date().toISOString().slice(0, 16).replace(/[-:T]/g, "")));
mkdirSync(out, { recursive: true });
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const stamp = () => new Date().toTimeString().slice(0, 8);
const say = (...a) => console.log(stamp(), ...a);
let fails = 0, restarts = 0;
const check = (n, ok, extra) => { console.log((ok ? "PASS " : "FAIL ") + n + (extra !== undefined ? "  [" + String(typeof extra === "string" ? extra : JSON.stringify(extra)).slice(0, 300) + "]" : "")); if (!ok) fails++; return !!ok; };
const getJ = async (p, ms = 8000) => { try { const r = await fetch(base + p, { signal: AbortSignal.timeout(ms) }); return r.ok ? await r.json() : null; } catch (e) { return null; } };
const getT = async (p, ms = 8000) => { try { const r = await fetch(base + p, { signal: AbortSignal.timeout(ms) }); return r.ok ? await r.text() : ""; } catch (e) { return ""; } };
const NOISE = ["data_logger", "esp-tls", "HTTP_CLIENT", "esp-x509-crt-bundle"];
const eSinceRestart = async () => { const t = await getT("/api/logs/ring", 15000); if (!t) return null; const ls = t.split("\n").map((l) => l.replace(/\x1b\[[0-9;]*m/g, "")); let at = -1; ls.forEach((l, i) => { if (l.includes("restart_tracker: restarting:")) at = i; }); return ls.slice(at < 0 ? 0 : at).filter((l) => l.includes("E (") && !NOISE.some((n) => l.includes(n))); };
const bootCount = async () => { const s = await getJ("/api/status", 3000); return s ? s.boot_count : null; };
const waitRestart = async (label, b0, limit = 120) => {
  const t0 = Date.now();
  await sleep(6000);
  while (Date.now() - t0 < limit * 1000) { const b = await bootCount(); if (b !== null && b !== b0) { await sleep(4000); say(label + ": the DUT is back (boot " + b + ")"); restarts++; return b; } await sleep(2000); }
  check(label + ": the DUT came back", false, "boot count still " + b0); return null;
};

const bm0 = await getJ("/api/settings/bridge_manager");
const stranded = bm0 && (bm0.bridges || []).find((b) => b.name === BRIDGE);
if (!check("u0 the stranded bridge " + BRIDGE + " -> " + JACK + " is on the device", !!stranded && stranded.b === JACK && stranded.enabled !== false, stranded)) { console.log("restarts=0"); console.log("CONNECTIONS UI FAIL"); process.exit(1); }
const live0 = await getJ("/api/bridges");
const liveRow = live0 && (live0.bridges || []).find((b) => b.name === BRIDGE);
check("u0 /api/bridges reports it down with the reason naming " + JACK, !!liveRow && liveRow.up === false && String(liveRow.reason || "").includes(JACK), liveRow);

const b = await chromium.launch({ channel: "chrome" });
const page = await (await b.newContext({ viewport: { width: 1280, height: 1000 } })).newPage();
const pageErrors = [];
page.on("pageerror", (e) => pageErrors.push(String(e.message || e)));
const shot = async (name, sel) => { try { const loc = page.locator(sel || "#view"); if (await loc.count()) { await loc.first().screenshot({ path: `${out}/${name}.png` }); say("shot", name); } } catch (e) { say("no shot", name, String(e.message || e).slice(0, 80)); } };
const openCard = async () => {
  await page.goto(base + "/#/settings/connections");
  try { await page.waitForSelector("[data-row], [data-custom]", { timeout: 25000 }); } catch (e) { return false; }
  await sleep(1500);
  return true;
};
const submitState = () => page.evaluate(() => ({ label: (document.querySelector("#submitlabel") || {}).textContent, disabled: !!(document.querySelector("#submitbtn") || {}).disabled, title: (document.querySelector("#submitbtn") || {}).title }));
const confirmSubmit = async () => {
  await page.click("#submitbtn");
  await page.waitForSelector("#modal-root.on button.pri", { timeout: 10000 });
  const cb = page.locator("#modal-root.on input[type=checkbox]");
  if (await cb.count()) await cb.first().check();
  await sleep(200);
  await page.click("#modal-root.on button.pri");
};

/* ---- u1: open, nothing staged ---- */
const opened = await openCard();
check("u1 the Connections card rendered its rows", opened);
if (!opened) { await shot("u1_no_card"); await b.close(); console.log("restarts=" + restarts); console.log("CONNECTIONS UI FAIL"); process.exit(1); }
const s1 = await submitState();
check("u1 nothing staged on open: the Submit button reads Saved", s1.label === "Saved" && s1.disabled, s1);
check("u1 no page error", pageErrors.length === 0, pageErrors);
await shot("u1_card");

/* ---- u2: the stranded row ---- */
const rowSel = `[data-row="${BRIDGE}"]`;
const row = await page.evaluate((sel) => {
  const r = document.querySelector(sel); if (!r) return null;
  const v = (q) => { const e = r.querySelector(q); return e ? e.value : null; };
  return { missing: r.dataset.missing, iface: v("select[data-sel=iface]"), conn: v("select[data-sel=conn]"), port: v("input[data-port]"), translator: v("select[data-sel=translator]"), text: r.textContent, trash: !!r.querySelector("[data-remove]") };
}, rowSel);
check("u2 the stranded row shows as CAN bus ⇄ TCP server " + PORT, !!row && row.iface === "can" && row.conn === "tcp" && String(row.port) === String(PORT), row);
check("u2 the row names the missing server and offers the trash button", !!row && row.missing === JACK && /does not exist on this device/.test(row.text) && row.trash, row && row.text.slice(0, 200));
await shot("u2_row", rowSel);

/* ---- u3: its trash button stages Bridges alone ---- */
await page.click(`${rowSel} [data-remove]`);
await sleep(500);
const s3 = await submitState();
check("u3 the removal stages Bridges alone: Submit Changes (1)", s3.label === "Submit Changes (1)" && /Bridges/.test(s3.title || "") && !s3.disabled, s3);
check("u3 the row is gone from the card", (await page.locator(rowSel).count()) === 0);

/* ---- u4: Submit, the restart, the device ---- */
{
  const b0 = await bootCount();
  await confirmSubmit();
  const toast = await page.evaluate(() => [...document.querySelectorAll(".toast")].map((t) => t.textContent).join(" | "));
  check("u4 no 'Apply failed' toast after the Submit", !/Apply failed/.test(toast), toast);
  const back = await waitRestart("u4", b0);
  if (back !== null) {
    const bm4 = await getJ("/api/settings/bridge_manager");
    check("u4 the stranded bridge is gone from the device", !!bm4 && !(bm4.bridges || []).some((x) => x.name === BRIDGE), bm4 && (bm4.bridges || []).map((x) => x.name));
    const e4 = await eSinceRestart();
    check("u4 no E line since the restart", Array.isArray(e4) && e4.length === 0, e4);
  }
}

/* ---- u5: a new connection on a new TCP port from the card, one Submit ---- */
{
  const opened5 = await openCard();
  check("u5 the card rendered again after the restart", opened5);
  const s5a = await submitState();
  check("u5 nothing staged on open", s5a.label === "Saved", s5a);
  await page.click("[data-add]");
  await sleep(300);
  const sel = '[data-row="new"]';
  await page.selectOption(`${sel} select[data-sel=iface]`, "can");
  await sleep(300);
  await page.selectOption(`${sel} select[data-sel=conn]`, "tcp");
  await sleep(300);
  await page.fill(`${sel} input[data-port]`, String(PORT));
  await page.dispatchEvent(`${sel} input[data-port]`, "change");
  await sleep(300);
  await page.selectOption(`${sel} select[data-sel=translator]`, "raw");
  await sleep(500);
  const newRow = await page.evaluate((q) => { const r = document.querySelector(q); if (!r) return null; const v = (x) => { const e = r.querySelector(x); return e ? e.value : null; }; return { iface: v("select[data-sel=iface]"), conn: v("select[data-sel=conn]"), port: v("input[data-port]"), translator: v("select[data-sel=translator]") }; }, sel);
  check("u5 the new row reads CAN bus ⇄ TCP server " + PORT + ", raw", !!newRow && newRow.iface === "can" && newRow.conn === "tcp" && String(newRow.port) === String(PORT) && newRow.translator === "raw", newRow);
  const warn = await page.evaluate(() => [...document.querySelectorAll("#view .banner.warn")].map((x) => x.textContent).join(" | "));
  check("u5 the card raised no error for the row", !/no free server slot|enter a port|already used/.test(warn), warn);
  const s5 = await submitState();
  check("u5 Bridges and Socket servers staged together: Submit Changes (2)", s5.label === "Submit Changes (2)" && /Bridges/.test(s5.title || "") && /Socket servers/.test(s5.title || ""), s5);
  await shot("u5_new_row", sel);
  const b0 = await bootCount();
  await confirmSubmit();
  await sleep(800);
  const toast = await page.evaluate(() => [...document.querySelectorAll(".toast")].map((t) => t.textContent).join(" | "));
  check("u5 no 'Apply failed' toast after the Submit", !/Apply failed/.test(toast), toast);
  const back = await waitRestart("u5", b0);
  if (back !== null) {
    const live5 = await getJ("/api/bridges");
    const br5 = live5 && (live5.bridges || []).find((x) => x.b === JACK || x.a === JACK);
    check("u5 the new bridge to " + JACK + " is up on /api/bridges", !!br5 && br5.up === true, br5);
    const sk5 = await getJ("/api/sockets");
    const sv5 = sk5 && (sk5.servers || []).find((x) => x.name === JACK);
    check("u5 " + JACK + " is up on /api/sockets and carried by the new bridge", !!sv5 && sv5.up === true && !!sv5.bridge && (!br5 || sv5.bridge === br5.name), sv5);
    const e5 = await eSinceRestart();
    check("u5 no E line since the restart", Array.isArray(e5) && e5.length === 0, e5);
  }
}
check("end: no page error", pageErrors.length === 0, pageErrors);
await b.close();
console.log("restarts=" + restarts);
console.log("CONNECTIONS UI " + (fails ? "FAIL (" + fails + " failed)" : "PASS"));
process.exit(fails ? 1 : 0);
