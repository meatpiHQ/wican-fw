/* The page leg of the CAN filters bench (2026-11-14): the unit's own embedded page,
   in Chrome, against the unit. Run by can_filters_bench.py while its generator puts
   the frames on the bus and its table is in place (one standard PID, the vehicle's
   filter "Road speed" on 0x284, the custom filters on 0x3B3, 0x4D0, 0x6F1 and the
   29-bit 0x18DAF110; the speed frame reads 60 km/h, the 0x3B3 frame 7, the 29-bit
   one 34).

     p1  Automate > Parameters > Custom PIDs: the CAN filters list shows the four
         custom filters with their IDs in hex, the live values beside 0x3B3 and
         the 29-bit ID
     p2  Add CAN filter: a row, opened; the ID typed as a plain 644 reads 0x284
         (decimal, as on the 4.x page) and as 0x284 the same; a listen time, an
         expression and a unit typed in
     p3  Test on that row: the dialog shows the whole frame and 60 km/h
     p4  Apply Configuration: the unit's table carries the filter as typed, with
         no tag (a custom one), the unit reads it (60), the row shows it live
     p5  Vehicle Specific: the vehicle's filter under its name, live; a filter
         added there and applied is tagged profile:true on the unit and read (7)
     p6  no page error

   node tools/testbench/obd/can_filters_page.mjs --base http://localhost:18081 [--out DIR]
   Prints PASS / FAIL lines and `PAGE LEG PASS` or `PAGE LEG FAIL`; the tables are
   left as the page made them (the bench restores them). Needs the Playwright
   devDependency of tools/webui_preview (`npm install` there once) and Chrome. */
import { mkdirSync } from "node:fs";
import { createRequire } from "node:module";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const { chromium } = createRequire(join(here, "..", "..", "webui_preview", "package.json"))("playwright");

const arg = (k, d) => { const i = process.argv.indexOf(k); return i > 0 && process.argv[i + 1] ? process.argv[i + 1] : d; };
const base = arg("--base", "http://localhost:18081");
const out = arg("--out", "shots_can_filters");
mkdirSync(out, { recursive: true });

let failed = 0;
const check = (n, ok, extra) => { console.log((ok ? "PASS " : "FAIL ") + n + (extra !== undefined ? "  [" + JSON.stringify(extra).slice(0, 320) + "]" : "")); if (!ok) failed++; return ok; };
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const getJ = async (p) => { try { const r = await fetch(base + p, { signal: AbortSignal.timeout(8000) }); return r.ok ? await r.json() : null; } catch (e) { return null; } };
const value = async (name) => { const a = await getJ("/api/autopid"); const p = ((a && a.params) || []).find((x) => x.name === name); return p ? p.value : undefined; };
const waitValue = async (name, want, secs) => { let v; for (let i = 0; i < secs; i++) { v = await value(name); if (v === want) return v; await sleep(1000); } return v; };

const b = await chromium.launch({ channel: "chrome" });
const page = await (await b.newContext({ viewport: { width: 1280, height: 1000 } })).newPage();
const pageErrors = [];
page.on("pageerror", (e) => pageErrors.push(String(e.message || e)));
page.on("console", (m) => { if (m.type() === "error" && !/ERR_CONNECTION|ERR_EMPTY|ERR_TIMED|ERR_NETWORK|Failed to load resource/.test(m.text())) pageErrors.push(m.text()); });
const shot = async (name) => { try { await page.screenshot({ path: `${out}/${name}.png`, fullPage: true }); } catch (e) { } };
const rows = () => page.evaluate(() => [...document.querySelectorAll(".fltl .pidrow")].map((r) => ({
  name: r.querySelector(".fname").value, id: r.querySelector(".fid").value, mon: r.querySelector(".fmon").value,
  val: r.querySelector(".val").textContent, open: r.classList.contains("open") })));
const count = () => page.evaluate(() => (document.querySelector(".fltl .pidl-tools .help") || {}).textContent || "");
const modalText = () => page.evaluate(() => (document.querySelector("#modal-root") || {}).textContent || "");
const tab = async (name) => { await page.locator(".seg button", { hasText: new RegExp("^" + name + "$") }).first().click(); await sleep(900); };
const type = async (loc, text) => { await loc.fill(text); await loc.dispatchEvent("change"); await sleep(150); };
const apply = async () => {
  await page.locator("button.btn.pri", { hasText: /^Apply Configuration$/ }).last().click();
  for (let i = 0; i < 30; i++) { if (await page.evaluate(() => [...document.querySelectorAll(".toast")].some((t) => /Configuration applied/.test(t.textContent)))) return true; await sleep(300); }
  return false;
};

try {
  /* ---- p1 ---- */
  await page.goto(base + "/#/automate/parameters"); await sleep(4000);
  await tab("Custom PIDs"); await sleep(3200);       /* one look at the live values */
  let r = await rows();
  check("p1: the unit's page has the CAN filters list on Custom PIDs, with the four custom filters",
    r.length === 4 && r.map((x) => x.id).join() === "0x3B3,0x4D0,0x6F1,0x18DAF110" && (await count()) === "4 CAN filters · 5 parameters", { rows: r, count: await count() });
  check("p1: the live values beside 0x3B3 and the 29-bit ID, no data beside the ID nobody sends", r.length === 4 && r[0].val === "7" && r[2].val === "no data" && r[3].val === "34", r.map((x) => x.val));
  await shot("p1_custom");

  /* ---- p2 ---- */
  await page.locator(".flt-add").click(); await sleep(400);
  r = await rows();
  check("p2: Add CAN filter adds a row, opened, with no ID yet, 1000 ms listen", r.length === 5 && r[4].name === "NewFilter" && r[4].id === "" && r[4].mon === "1000" && r[4].open, r[4]);
  const row = () => page.locator(".fltl .pidrow").last();
  const prm = (n) => row().locator(".det .prm:not(.prm-head) input:not([type=checkbox])").nth(n);
  await type(row().locator(".fid"), "644");
  check("p2: a plain 644 is decimal: the field reads 0x284", (await rows())[4].id === "0x284", (await rows())[4].id);
  await type(row().locator(".fid"), "0x284");
  await type(row().locator(".fmon"), "300");
  await type(prm(1), "[B4:B5]/98");
  await type(prm(2), "km/h");
  r = await rows();
  check("p2: the ID typed as 0x284 reads 0x284, the listen time 300", r[4].id === "0x284" && r[4].mon === "300", r[4]);

  /* ---- p3 ---- */
  await row().locator(".main button", { hasText: /^Test$/ }).click();
  let t = "";
  for (let i = 0; i < 20 && !/^Test: /.test(t); i++) { await sleep(300); t = await modalText(); }
  check("p3: Test shows the whole frame, its data and 60 km/h",
    /^Test: NewFilter/.test(t) && /the frame came after \d+ ms/.test(t) && /< 284 02 1E 02 1F 16 F8 4E 20/.test(t) &&
    /\(B0 is the first data byte\): 02 1E 02 1F 16 F8 4E 20/.test(t) && /DecodedNewFilter60km\/h/.test(t), t.slice(0, 400));
  await shot("p3_test");
  await page.locator("#modal-root .acts button", { hasText: /Close/ }).click(); await sleep(300);

  /* ---- p4 ---- */
  check("p4: Apply Configuration is taken by the unit", await apply());
  {
    const cfg = await getJ("/api/autopid/config");
    const f = ((cfg && cfg.filters) || []).find((x) => x.name === "NewFilter");
    check("p4: the unit's table carries the filter as typed, a custom one (no tag)",
      !!f && JSON.stringify(f) === JSON.stringify({ name: "NewFilter", frame_id: 644, monitor_ms: 300, period_ms: 5000, group: "default", parameters: [{ name: "NewFilter", expression: "[B4:B5]/98", unit: "km/h" }] }), f);
    check("p4: the unit reads it", (await waitValue("NewFilter", 60, 12)) === 60, await value("NewFilter"));
    await sleep(3000);
    r = await rows();
    check("p4: the row shows it live", r.length === 5 && r[4].val === "60 km/h", r.map((x) => x.val));
    await shot("p4_applied");
  }

  /* ---- p5 ---- */
  await tab("Vehicle Specific"); await sleep(3200);
  r = await rows();
  check("p5: Vehicle Specific lists the vehicle's filter alone, under its name, live",
    r.length === 1 && r[0].name === "Road speed" && r[0].id === "0x284" && r[0].val === "60 km/h", r);
  await page.locator(".flt-add").click(); await sleep(400);
  await type(row().locator(".fid"), "0x3B3");
  await type(prm(0), "PageDoor");
  check("p5: a filter added on this tab is applied", await apply());
  {
    const cfg = await getJ("/api/autopid/config");
    const f = ((cfg && cfg.filters) || []).find((x) => x.frame_id === 947 && x.profile === true);
    check("p5: on the unit it is one of the vehicle's (profile:true), the custom filter on 0x3B3 beside it untouched",
      !!f && f.parameters.length === 1 && f.parameters[0].name === "PageDoor" &&
      (cfg.filters || []).some((x) => x.frame_id === 947 && !x.profile && x.parameters[0].name === "FLT_DOOR"), f);
    check("p5: the unit reads it", (await waitValue("PageDoor", 7, 12)) === 7, await value("PageDoor"));
    await shot("p5_vehicle");
  }
} catch (e) {
  check("the page leg ran to its end", false, String(e && e.message || e).slice(0, 300));
  await shot("error");
}
check("p6: no page error", pageErrors.length === 0, pageErrors.slice(0, 3));
await b.close();
console.log(failed ? "PAGE LEG FAIL" : "PAGE LEG PASS");
process.exit(failed ? 1 : 0);
