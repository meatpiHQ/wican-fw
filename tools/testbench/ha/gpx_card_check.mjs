/* The WiCAN GPS track card in a real browser against the bench Home
   Assistant (2026-11-06): a storage dashboard with the card over a synthetic
   GPX under www/wican, headless Chromium through Playwright, the map's
   Leaflet paths and the card's status line judged, a screenshot kept, the
   dashboard and the file removed again.

   Usage (from the repo root, the bench HA up on rpi001):
     node tools/testbench/ha/gpx_card_check.mjs http://192.168.8.228:8123 <user> <password> \
          test-reports/logs/<dir>/gpx_card.png [rpi001]
   With the Pi host the script writes the synthetic track over ssh and removes
   it at the end; without it, put a GPX at www/wican/test_track.gpx yourself.
   Playwright and its Chromium come from tools/webui_preview (npm ci there,
   npx playwright install chromium). Verdict: GPX CARD PASS / FAIL. */
import { execSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const here = dirname(fileURLToPath(import.meta.url));
const pw = join(here, "..", "..", "webui_preview", "node_modules", "playwright", "index.mjs");
const { chromium } = await import("file:///" + pw.replace(/\\/g, "/"));

const [base, user, password, shot, pi] = process.argv.slice(2);
if (!base || !user || !password || !shot) {
  console.log("usage: gpx_card_check.mjs <ha url> <user> <password> <screenshot.png> [<pi host>]");
  process.exit(2);
}
const clientId = base + "/";
const fails = [];
const check = (name, ok, detail = "") => {
  console.log((ok ? "PASS" : "FAIL") + ": " + name + (detail ? "  (" + detail + ")" : ""));
  if (!ok) fails.push(name);
};

/* a synthetic loop nowhere real (a 1 km circle in the Sahara), 120 fixes 10 s apart */
const GPX_PY = [
  "import math, datetime",
  "t0 = datetime.datetime(2026, 11, 6, 3, 0, 0, tzinfo=datetime.timezone.utc)",
  "pts = []",
  "for i in range(120):",
  "    a = 2 * math.pi * i / 120",
  "    lat = 23.0 + 0.009 * math.sin(a)",
  "    lon = 12.0 + 0.009 * math.cos(a)",
  "    when = (t0 + datetime.timedelta(seconds=10 * i)).strftime('%Y-%m-%dT%H:%M:%SZ')",
  "    pts.append('<trkpt lat=\"%.6f\" lon=\"%.6f\"><ele>%d</ele><time>%s</time></trkpt>' % (lat, lon, 300 + i % 7, when))",
  "doc = '<?xml version=\"1.0\" encoding=\"UTF-8\"?>\\n<gpx version=\"1.1\" creator=\"bench\" xmlns=\"http://www.topografix.com/GPX/1/1\"><trk><name>test</name><trkseg>' + ''.join(pts) + '</trkseg></trk></gpx>\\n'",
  "import os; os.makedirs(os.path.expanduser('~/.homeassistant/www/wican'), exist_ok=True)",
  "open(os.path.expanduser('~/.homeassistant/www/wican/test_track.gpx'), 'w').write(doc)",
  "print('gpx written', len(pts), 'points')",
].join("\n");

/* the remote command stays plain (no quoting through cmd.exe): a script goes
   in through stdin (`python3 -`) */
function ssh(host, cmd, input) {
  return execSync(`ssh -o BatchMode=yes -o ConnectTimeout=10 ${host} ${cmd}`, { encoding: "utf-8", input: input || "" }).trim();
}

async function login() {
  const flow = await (await fetch(base + "/auth/login_flow", { method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ client_id: clientId, handler: ["homeassistant", null], redirect_uri: clientId }) })).json();
  const res = await (await fetch(base + "/auth/login_flow/" + flow.flow_id, { method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username: user, password, client_id: clientId }) })).json();
  if (res.type !== "create_entry") throw new Error("login did not finish: " + JSON.stringify(res).slice(0, 200));
  return (await fetch(base + "/auth/token", { method: "POST", headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams({ grant_type: "authorization_code", code: res.result, client_id: clientId }) })).json();
}

/* Node 22's own WebSocket: the Lovelace storage API */
function ws(token) {
  return new Promise((resolve, reject) => {
    const sock = new WebSocket(base.replace(/^http/, "ws") + "/api/websocket");
    let id = 1;
    const pending = new Map();
    sock.onmessage = (ev) => {
      const m = JSON.parse(String(ev.data));
      if (m.type === "auth_required") sock.send(JSON.stringify({ type: "auth", access_token: token }));
      else if (m.type === "auth_ok") resolve({
        call: (msg) => new Promise((res2, rej2) => { const i = id++; pending.set(i, { res2, rej2 }); sock.send(JSON.stringify({ id: i, ...msg })); }),
        close: () => sock.close(),
      });
      else if (m.type === "auth_invalid") reject(new Error("websocket auth invalid"));
      else if (m.type === "result" && pending.has(m.id)) { const p = pending.get(m.id); pending.delete(m.id); m.success ? p.res2(m.result) : p.rej2(new Error(JSON.stringify(m.error))); }
    };
    sock.onerror = (e) => reject(new Error("websocket error " + (e && e.message ? e.message : "")));
  });
}

if (pi) console.log("  " + ssh(pi, "python3 -", GPX_PY + "\n"));
const tok = await login();
const api = await ws(tok.access_token);
try {
  try { await api.call({ type: "lovelace/dashboards/create", url_path: "wican-test", title: "WiCAN test", mode: "storage", require_admin: false, show_in_sidebar: false }); }
  catch (e) { if (!/already|exists|url_path/.test(String(e.message))) throw e; }
  await api.call({ type: "lovelace/config/save", url_path: "wican-test", config: { views: [{ title: "Track", cards: [
    { type: "custom:wican-gpx-card", url: "/local/wican/test_track.gpx", title: "GPS track test", height: 300 }] }] } });
  check("a storage dashboard with the card saved", true);
} catch (e) { check("a storage dashboard with the card saved", false, e.message); }

const browser = await chromium.launch({ headless: true });
const page = await browser.newPage({ viewport: { width: 1100, height: 760 } });
const errors = [];
page.on("pageerror", (e) => errors.push(String(e.message).slice(0, 200)));
page.on("console", (m) => { if (m.type() === "error") errors.push(m.text().slice(0, 200)); });
/* the tokens before any navigation: the first load is signed in, no auth
   redirect races the probe */
await page.addInitScript(([t, b, c]) => {
  localStorage.setItem("hassTokens", JSON.stringify({ access_token: t.access_token, token_type: "Bearer", expires_in: t.expires_in,
    refresh_token: t.refresh_token, hassUrl: b, clientId: c, expires: Date.now() + t.expires_in * 1000 }));
}, [tok, base, clientId]);
try { await page.goto(base + "/wican-test/0", { waitUntil: "commit" }); } catch (e) { console.log("  goto raced: " + String(e.message).slice(0, 80)); }
try { await page.waitForURL((u) => String(u).includes("wican-test"), { timeout: 30000 }); } catch (e) { console.log("  url wait: " + String(e.message).slice(0, 80)); }
await page.waitForLoadState("load");

const deadline = Date.now() + 60000;
let state = null;
while (Date.now() < deadline) {
  state = await page.evaluate(() => {
    const deep = (root, sel) => {
      if (!root) return null;
      const hit = root.querySelector ? root.querySelector(sel) : null;
      if (hit) return hit;
      const all = root.querySelectorAll ? root.querySelectorAll("*") : [];
      for (const el of all) { if (el.shadowRoot) { const r = deep(el.shadowRoot, sel); if (r) return r; } }
      return null;
    };
    const card = deep(document, "wican-gpx-card");
    if (!card || !card.shadowRoot) {
      const err = deep(document, "hui-error-card");
      return { card: false, defined: !!customElements.get("wican-gpx-card"),
        errText: (err && err.shadowRoot ? err.shadowRoot.textContent : "").replace(/\s+/g, " ").slice(0, 300) };
    }
    const line = card.shadowRoot.querySelector(".line");
    const map = card.shadowRoot.querySelector("ha-map");
    const paths = map && map.shadowRoot ? map.shadowRoot.querySelectorAll("path.leaflet-interactive").length : 0;
    const tiles = map && map.shadowRoot ? map.shadowRoot.querySelectorAll("img.leaflet-tile").length : 0;
    /* the synthetic loop's north and south points inside the map's view */
    const lm = map ? map.leafletMap : null;
    let inView = false;
    try { inView = !!(lm && lm.getBounds().contains([23.009, 12.0]) && lm.getBounds().contains([22.991, 12.0])); } catch (e) { inView = false; }
    return { card: true, line: line ? line.textContent : "", lineClass: line ? line.className : "", map: !!map, paths, tiles, inView };
  });
  if (state.card && ((state.paths > 0 && state.inView) || /err/.test(state.lineClass || ""))) break;
  await new Promise((r) => setTimeout(r, 1000));
}
console.log("  card state: " + JSON.stringify(state));
check("the card element rendered on the dashboard", !!(state && state.card), state && state.errText ? state.errText : "");
check("the card drew the track on Home Assistant's map (Leaflet paths)", !!(state && state.map && state.paths >= 1), "paths " + (state && state.paths));
check("the map's view fits the track", !!(state && state.inView));
check("the status line names the points and the span", !!(state && /points?,? .* to /.test(state.line || "")), state && state.line);
check("no page error", errors.length === 0, errors.slice(0, 3).join(" | "));
await new Promise((r) => setTimeout(r, 1500));
await page.screenshot({ path: shot, fullPage: false });
console.log("  screenshot: " + shot);
await browser.close();
try {
  const dash = (await api.call({ type: "lovelace/dashboards/list" })).find((d) => d.url_path === "wican-test");
  if (dash) await api.call({ type: "lovelace/dashboards/delete", dashboard_id: dash.id });
  check("the test dashboard removed", true);
} catch (e) { check("the test dashboard removed", false, e.message); }
api.close();
if (pi) ssh(pi, "rm -f ~/.homeassistant/www/wican/test_track.gpx");
console.log(fails.length ? "GPX CARD FAIL: " + fails.join("; ") : "GPX CARD PASS");
process.exit(fails.length ? 1 : 0);
