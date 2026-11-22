/* The Home Assistant card on the REAL device while its pushes fail
   (2026-11-22): Chrome through the tunnel, the unit registered by
   push_fail_reason_bench.py at two addresses that both fail (nothing
   listens at the first, the second closes the TLS handshake).

     p1  Automate > Home Assistant opened, no page error
     p2  the Webhook stats row: Status failed, and one line per address in
         the words of the card's own fields
     p3  the second line's info button opens one bubble: the title, what to
         check, and the code for support the unit reported
     p4  what the page shows is what /api/webhook reports (the reasons and the
         code are the unit's, not the page's guess)

   Prints PUSH FAIL PAGE PASS / FAIL. Saves a screenshot next to the run's log.
   node push_fail_page.mjs <base url, e.g. http://localhost:18086> [--out dir] */
import { mkdirSync } from "node:fs";
import { createRequire } from "node:module";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
const here = dirname(fileURLToPath(import.meta.url));
const { chromium } = createRequire(join(here, "..", "..", "webui_preview", "package.json"))("playwright");

const arg = (k, d) => { const i = process.argv.indexOf(k); return i > 0 && process.argv[i + 1] ? process.argv[i + 1] : d; };
const base = (process.argv[2] && !process.argv[2].startsWith("--") ? process.argv[2] : "http://localhost:18086").replace(/\/$/, "");
const out = arg("--out", join(here, "..", "..", "..", "test-reports", "logs", "push_fail_page_" + new Date().toISOString().slice(0, 16).replace(/[-:T]/g, "")));
mkdirSync(out, { recursive: true });
let fails = 0;
const check = (n, ok, extra) => { console.log((ok ? "PASS " : "FAIL ") + n + (extra !== undefined ? "  [" + String(typeof extra === "string" ? extra : JSON.stringify(extra)).slice(0, 300) + "]" : "")); if (!ok) fails++; return ok; };
const getJ = async (p, ms = 8000) => { try { const r = await fetch(base + p, { signal: AbortSignal.timeout(ms) }); return r.ok ? await r.json() : null; } catch (e) { return null; } };

const wh = await getJ("/api/webhook");
const f = (wh && wh.failures) || [];
if (!check("p0 the unit reports a failed push at two addresses", !!wh && wh.status === "failed" && f.length === 2 && Array.isArray(wh.urls) && wh.urls.length === 2, wh && { status: wh.status, failures: f })) {
  console.log("PUSH FAIL PAGE FAIL");
  process.exit(1);
}

const browser = await chromium.launch({ channel: "chrome" });
const page = await browser.newPage({ viewport: { width: 1180, height: 1500 } });
const errors = [];
page.on("pageerror", (e) => errors.push(String(e.message).slice(0, 160)));
try {
  await page.goto(base + "/#/automate/homeassistant", { waitUntil: "load", timeout: 60000 });
  const row = page.locator(".section .frow", { hasText: "Webhook stats" }).first();
  await row.waitFor({ state: "visible", timeout: 45000 });
  await page.waitForTimeout(1500);
  check("p1 Automate > Home Assistant opened, no page error", errors.length === 0, errors.slice(0, 2));
  const text = await row.innerText();
  check("p2 the row says Status: failed", /Status: failed/.test(text), text.slice(0, 120));
  const lines = await row.locator("[data-pushfail]").evaluateAll((els) => els.map((e) => ({ reason: e.dataset.pushfail, text: e.textContent.trim() })));
  check("p2 one line per address, named Webhook URL and Failover URL", lines.length === 2 && /^Webhook URL: /.test(lines[0].text) && /^Failover URL: /.test(lines[1].text), lines);
  check("p4 the lines are the unit's own reasons, in its order", lines.length === 2 && lines[0].reason === f[0].reason && lines[1].reason === f[1].reason, { page: lines.map((l) => l.reason), unit: f.map((x) => x.reason) });
  check("p2 the second line reads `the server closed the connection during the secure handshake`",
    lines.length === 2 && lines[1].text === "Failover URL: the server closed the connection during the secure handshake", lines[1] && lines[1].text);
  await row.locator("[data-pushfail] button.ibtn").nth(1).click();
  await page.waitForTimeout(500);
  const bubbles = await page.locator(".ibubble").evaluateAll((els) => els.map((e) => ({ title: (e.querySelector("b") || {}).textContent || "", text: (e.querySelector("p") || {}).textContent || "" })));
  check("p3 its button opens one bubble: the title and what to check", bubbles.length === 1 && bubbles[0].title === "The server closed the connection" && /reverse proxy or Nabu Casa needs the name/.test(bubbles[0].text), bubbles[0]);
  check("p4 the bubble carries the code the unit reported", bubbles.length === 1 && bubbles[0].text.includes("For support: " + f[1].code), bubbles[0] && bubbles[0].text.slice(-70));
  await page.screenshot({ path: join(out, "push_fail_card.png") });
  console.log("screenshot: " + join(out, "push_fail_card.png"));
  await page.keyboard.press("Escape");
} catch (e) {
  check("the page leg ran through", false, String(e.message).slice(0, 200));
} finally {
  await browser.close();
}
console.log(fails ? "PUSH FAIL PAGE FAIL (" + fails + ")" : "PUSH FAIL PAGE PASS");
process.exit(fails ? 1 : 0);
