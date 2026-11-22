/* What a user sees after a drive: Home Assistant's History panel for some
   sensors over a window, as a screenshot (headless Chromium through
   Playwright, signed in as the owner). The drive bench's report says how many
   states and statistics rows Home Assistant holds; this shows the picture.

   Usage (from the repo root, the Home Assistant up):
     node tools/testbench/ha/ha_history_shot.mjs <ha url> <user> <password> <out.png> \
          <start ISO> <end ISO> <entity_id>[,<entity_id>...]
   Playwright and its Chromium come from tools/webui_preview (npm ci there,
   npx playwright install chromium). Prints HISTORY SHOT DONE. */
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const here = dirname(fileURLToPath(import.meta.url));
const pw = join(here, "..", "..", "webui_preview", "node_modules", "playwright", "index.mjs");
const { chromium } = await import("file:///" + pw.replace(/\\/g, "/"));

const [base, user, password, shot, start, end, entities] = process.argv.slice(2);
if (!base || !user || !password || !shot || !start || !end || !entities) {
  console.log("usage: ha_history_shot.mjs <ha url> <user> <password> <out.png> <start ISO> <end ISO> <entity,entity>");
  process.exit(2);
}
const clientId = base + "/";

async function login() {
  const flow = await (await fetch(base + "/auth/login_flow", { method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ client_id: clientId, handler: ["homeassistant", null], redirect_uri: clientId }) })).json();
  const res = await (await fetch(base + "/auth/login_flow/" + flow.flow_id, { method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username: user, password, client_id: clientId }) })).json();
  if (res.type !== "create_entry") throw new Error("login did not finish: " + JSON.stringify(res).slice(0, 200));
  return (await fetch(base + "/auth/token", { method: "POST", headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams({ grant_type: "authorization_code", code: res.result, client_id: clientId }) })).json();
}

const tok = await login();
const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1400, height: 900 }, timezoneId: "UTC" });
await page.addInitScript(([t, b, c]) => {
  localStorage.setItem("hassTokens", JSON.stringify({ access_token: t.access_token, token_type: "Bearer", expires_in: t.expires_in,
    refresh_token: t.refresh_token, hassUrl: b, clientId: c, expires: Date.now() + t.expires_in * 1000 }));
}, [tok, base, clientId]);
const url = base + "/history?entity_id=" + encodeURIComponent(entities) + "&start_date=" + encodeURIComponent(start)
  + "&end_date=" + encodeURIComponent(end);
try { await page.goto(url, { waitUntil: "commit" }); } catch (e) { console.log("  goto raced: " + String(e.message).slice(0, 80)); }
await page.waitForLoadState("load");
await page.waitForTimeout(9000);          // the panel fetches the history and draws
await page.screenshot({ path: shot });
console.log("  " + url);
await browser.close();
console.log("HISTORY SHOT DONE");
