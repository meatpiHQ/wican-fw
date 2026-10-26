/* The new-car bar on the real page (2026-10-26): open the Dashboard of the
   device at <base> in Chrome and say whether the bar and the banner are up.
   Run by autopid_newcar_bench.py; prints one JSON line and exits 0 when the
   expectation holds.

   node tools/testbench/obd/newcar_bar_check.mjs http://localhost:18081 shown|hidden [shot.png] */
import { createRequire } from "node:module";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const { chromium } = createRequire(join(here, "..", "..", "webui_preview", "package.json"))("playwright");
const base = process.argv[2] || "http://localhost:18081";
const expect = process.argv[3] || "shown";
const shot = process.argv[4] || "";
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const b = await chromium.launch({ channel: "chrome" });
const page = await b.newPage({ viewport: { width: 1024, height: 900 } });
await page.goto(base + "/#/dashboard");
let out = null;
for (let i = 0; i < 12; i++) {           /* the bar paints on the status poll */
  await sleep(1000);
  out = await page.evaluate(() => {
    const bar = document.querySelector("#vehbar"), note = document.querySelector("#dash-vehnote");
    return { bar: !!bar && !bar.hidden, barText: bar ? bar.textContent.trim().slice(0, 160) : null,
      note: !!note && note.textContent.trim().length > 0, noteText: note ? note.textContent.trim().slice(0, 120) : null };
  });
  if ((expect === "shown" && out.bar && out.note) || (expect === "hidden" && !out.bar && !out.note && i >= 4)) break;
}
if (shot) { try { await page.screenshot({ path: shot, fullPage: false }); } catch (e) { } }
await b.close();
const ok = expect === "shown" ? (out.bar && out.note && /not set up for/.test(out.barText || "")) : (!out.bar && !out.note);
console.log(JSON.stringify({ ok, expect, ...out }));
process.exit(ok ? 0 : 1);
