/* The SD card slot on the REAL device's page (2026-10-25): Chrome over the
   tunnel. Reads GET /api/sdcard first and checks the page against what the
   device says: a mounted card = no bar, the Status row Mounted, the File
   Manager tile with its usage and no Format button; an unreadable card = the
   bar with what the card holds, the Status row's chip and Format link, the
   tile's Format button, the dialog's first step (opened and cancelled: nothing
   is posted); a card that does not answer = the red bar with OK only. Saves
   screenshots next to the run's log. Prints `SD CARD UI PASS`.
   node sdcard_ui_check.mjs <base url, e.g. http://127.0.0.1:18081> [out dir] */
import { mkdirSync } from "node:fs";
import { createRequire } from "node:module";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
const here = dirname(fileURLToPath(import.meta.url));
const { chromium } = createRequire(join(here, "..", "..", "webui_preview", "package.json"))("playwright");

const base = (process.argv[2] || "http://127.0.0.1:18081").replace(/\/$/, "");
const out = process.argv[3] || join(here, "..", "..", "..", "test-reports", "logs", "sdcard_ui_" + new Date().toISOString().slice(0, 16).replace(/[-:T]/g, ""));
mkdirSync(out, { recursive: true });
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
let fails = 0;
const check = (n, ok, extra) => { console.log((ok ? "PASS " : "FAIL ") + n + (extra !== undefined ? "  [" + String(extra).slice(0, 220) + "]" : "")); if (!ok) fails++; };

const sd = await (await fetch(base + "/api/sdcard")).json().catch(() => null);
const st = await (await fetch(base + "/api/status")).json().catch(() => null);
console.log("device:", JSON.stringify(sd), "status.sd:", JSON.stringify(st && st.sd));
check("GET /api/sdcard answers with its keys", !!sd && ["present", "mounted", "state", "reason", "card", "format"].every((k) => k in sd), sd && Object.keys(sd).join(","));
check("the status poll's sd summary agrees with /api/sdcard", !!st && (sd.present ? !!st.sd && st.sd.state === sd.state && st.sd.reason === sd.reason : st.sd === undefined), JSON.stringify(st && st.sd));
check("the status bit agrees", !!st && st.bits.sdcard_mounted === !!sd.mounted, st && st.bits.sdcard_mounted);

const b = await chromium.launch({ channel: "chrome" });
const page = await (await b.newContext({ viewport: { width: 1024, height: 1500 } })).newPage();
page.on("pageerror", (e) => check("no page error: " + e.message, false));
const rows = (title) => page.evaluate((t) => {
  const c = [...document.querySelectorAll("#view .section")].find((s) => new RegExp(t).test(((s.querySelector("h3") || {}).textContent || "").trim()));
  if (!c) return null;
  const o = { __text: c.textContent };
  for (const dt of c.querySelectorAll(".kv dt")) { const dd = dt.nextElementSibling; o[dt.textContent.trim()] = dd ? { text: dd.textContent, link: !!dd.querySelector("a.sd-fmt-link"), chip: (dd.querySelector(".chip") || {}).textContent || "" } : null; }
  return o;
}, title);
const bar = () => page.evaluate(() => { const el = document.querySelector("#sdbar"); return el ? { hidden: el.hidden, text: el.textContent, dead: el.classList.contains("dead"), buttons: [...el.querySelectorAll("button")].map((x) => x.textContent), links: [...el.querySelectorAll("a")].map((x) => x.textContent) } : null; });
const shot = async (name, sel) => {
  await page.evaluate(() => { const c = document.querySelector("main.content"); if (c) c.scrollTop = 0; });
  await sleep(300);
  const loc = page.locator(sel || "#view");
  if (await loc.count()) { await loc.first().screenshot({ path: `${out}/${name}.png` }); console.log("shot", name); }
};

/* the Status page: the bar and the System row */
await page.context().addInitScript(() => { try { localStorage.removeItem("wican-sd-seen"); } catch (_) {} });
await page.goto(base + "/#/status"); await sleep(7500);
const br = await bar();
const sys = await rows("^System$");
const row = sys && sys["SD card"];
check("the page has the SD card bar element", !!br);
if (sd.state === "mounted") {
  check("mounted card: no bar", !!br && br.hidden, br && br.text);
  check("Status > System: the SD card row reads Mounted, no link", !!row && /^Mounted$/.test(row.text.trim()) && !row.link, row && row.text);
} else if (sd.state === "unreadable") {
  check("unreadable card: the bar shows", !!br && !br.hidden, br && br.text);
  check("the bar names what the card holds", !!br && (sd.reason === "blank" ? /no filesystem/.test(br.text) : /cannot read it/.test(br.text)) && (sd.reason !== "exfat" || /exFAT/.test(br.text)), br && br.text);
  check("the bar offers Format the card and Not now", !!br && br.buttons.some((t) => /Format the card/.test(t)) && br.links.some((t) => /Not now/.test(t)));
  check("Status > System: the chip and the Format link", !!row && /Inserted/.test(row.chip) && row.link, row && row.text);
  await shot("0_bar", "#sdbar");
  /* the dialog's first step, cancelled: nothing is posted */
  await page.locator("#sdbar button", { hasText: "Format the card" }).click(); await sleep(1500);
  const m = await page.evaluate(() => { const el = document.querySelector("#modal-root .modal"); return el ? el.textContent : ""; });
  check("the dialog asks first, with the card and what was found", /Format the SD card\?/.test(m) && new RegExp(sd.card.name).test(m) && /Everything on the card is erased/.test(m), m.slice(0, 160));
  await shot("0b_dialog_ask", "#modal-root .modal");
  await page.locator("#modal-root .acts button", { hasText: "Cancel" }).click(); await sleep(400);
  const after = await (await fetch(base + "/api/sdcard")).json();
  check("Cancel posted nothing: the device still reads unreadable, format idle", after.state === "unreadable" && after.format.state === "idle", JSON.stringify(after.format));
} else if (sd.state === "failed") {
  check("a card that does not answer: the red bar with OK only", !!br && !br.hidden && br.dead && /does not answer/.test(br.text) && br.buttons.length === 0, br && br.text);
  check("Status > System: Inserted, not answering", !!row && /not answering/.test(row.chip), row && row.text);
} else {
  check("empty slot: no bar, the row reads Not mounted", !!br && br.hidden && !!row && /Not mounted/.test(row.text), row && row.text);
}
await shot("1_status_system", ".section:has(h3:text-is('System'))");

/* the File Manager landing */
await page.evaluate(() => { location.hash = "#/files"; }); await sleep(4000);
const tile = await page.evaluate(() => { const t = [...document.querySelectorAll(".fm-mount")].find((x) => /\/sd/.test(x.textContent)); return t ? { text: t.textContent, off: t.classList.contains("off"), button: (t.querySelector("button") || {}).textContent || "", chip: (t.querySelector(".chip") || {}).textContent || "" } : null; });
if (sd.state === "mounted") {
  check("File Manager: the SD card tile shows its usage and no Format button", !!tile && / of .* used/.test(tile.text) && !tile.button && !tile.off, tile && tile.text.slice(0, 120));
} else if (sd.state === "unreadable") {
  check("File Manager: the tile reads not readable with a Format button", !!tile && tile.chip === "not readable" && /Format/.test(tile.button) && !tile.off, tile && tile.text.slice(0, 160));
} else if (sd.state === "failed") {
  check("File Manager: the tile reads not answering, greyed out", !!tile && tile.chip === "not answering" && !tile.button && tile.off, tile && tile.text.slice(0, 160));
} else {
  check("File Manager: an empty slot reads not mounted", !!tile && tile.chip === "not mounted", tile && tile.text.slice(0, 120));
}
await shot("2_files_landing", ".fm-mounts");

/* the Logger page */
await page.evaluate(() => { location.hash = "#/logger"; }); await sleep(5000);
const lg = await rows("^Status$");
if (sd.state === "mounted") check("Logger: the storage chip reads the card", !!lg && /SD card/.test(lg.__text) && !/not readable/.test(lg.__text), lg && lg.__text.slice(0, 120));
else if (sd.state === "unreadable") check("Logger: the storage chip reads inserted, not readable with the link", !!lg && /SD card inserted, not readable/.test(lg.__text) && /Format the card/.test(lg.__text), lg && lg.__text.slice(0, 160));
await shot("3_logger_status", ".section:has(h3:text-is('Status'))");

await b.close();
console.log(fails ? `SD CARD UI FAIL (${fails})` : "SD CARD UI PASS");
process.exit(fails ? 1 : 0);
