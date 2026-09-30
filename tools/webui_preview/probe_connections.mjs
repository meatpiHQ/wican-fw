/* Probe (2026-10-01, meatpi): the Connections card (Settings > Connections) must
   accept the FACTORY bridge defaults - the OBD chip on WebSocket + TCP + USB at
   once - because the obd jack is multi-consumer in the firmware
   (bridge_endpoints.c). Before the fix the card claimed both ends of every
   connection and refused the defaults with "obd is already used by br_obd: one
   connection per interface", so Submit never got the change. The single-consumer
   rule must still hold for servers/channels/ble/usb, and the connection cap must
   follow BRIDGE_MANAGER_MAX_BRIDGES (6), not the old 4. Runs over the mock API:
   `python make_preview.py --fresh && node probe_connections.mjs` -> `CONNECTIONS PROBE PASS`. */
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import { JSDOM, VirtualConsole } from "jsdom";

const here = dirname(fileURLToPath(import.meta.url));
const html = readFileSync(join(here, "preview.html"), "utf-8");
const errs = [];
const vc = new VirtualConsole();
vc.on("jsdomError", (e) => errs.push(e.message + " " + (e.detail && e.detail.stack || "").slice(0, 300)));

const dom = new JSDOM(html, {
  runScripts: "dangerously", url: "http://wican.local/#/",
  pretendToBeVisual: true, virtualConsole: vc,
  beforeParse(w) {
    w.matchMedia = () => ({ matches: false, addListener() {}, addEventListener() {} });
    w.scrollTo = () => {}; w.HTMLCanvasElement.prototype.getContext = () => null;
    if (!w.TextEncoder) { w.TextEncoder = TextEncoder; w.TextDecoder = TextDecoder; }
  },
});
const w = dom.window, d = () => w.document;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
let fails = 0;
const check = (n, ok, extra) => { console.log((ok ? "PASS " : "FAIL ") + n + (extra !== undefined ? "  [" + JSON.stringify(extra) + "]" : "")); if (!ok) { fails++; } };
const $ = (sel) => d().querySelector(sel), $$ = (sel) => [...d().querySelectorAll(sel)];
const fire = (el, type) => el.dispatchEvent(new w.Event(type, { bubbles: true }));
const setText = (el, v) => { el.value = v; fire(el, "input"); fire(el, "change"); };
const setSel = (el, v) => { el.value = v; fire(el, "change"); };
const nav = (hash) => { w.location.hash = hash; w.dispatchEvent(new w.Event("hashchange")); };
const warns = () => $$("#view .banner.warn").map((b) => b.textContent.trim());
const rows = () => $$("#view .brline");
const addBtn = () => $$("#view button").find((b) => /Add connection/.test(b.textContent));
const rowSelects = (r) => [...r.querySelectorAll("select")];         // [iface, conn]
const rowPort = (r) => r.parentElement.querySelector('input[type="number"]');   // the Port field (the enable switch is a checkbox)

/* the factory defaults (bridge_manager_settings.c / socket_manager_settings.c /
   websocket_manager: ws_obd is the built-in terminal channel) */
const DEFAULT_BRIDGES = [
  { name: "br_obd", a: "obd", b: "ws_obd", translator: "raw", enabled: true },
  { name: "br_tcp_obd", a: "obd0", b: "obd", translator: "raw", enabled: true },
  { name: "br_usb_obd", a: "usb_obd", b: "obd", translator: "raw", enabled: true },
];
const DEFAULT_SERVERS = [
  { name: "obd0", proto: "tcp", port: 35000, enabled: true },
  { name: "slcan0", proto: "tcp", port: 3333, enabled: false },
  { name: "gvret0", proto: "tcp", port: 23, enabled: false },
  { name: "udp0", proto: "udp", port: 17, enabled: false },
];
const DEFAULT_CHANNELS = [
  { name: "ws_obd", path: "/ws/obd", mode: "text", enabled: true },
  { name: "ws_can", path: "/ws/can", mode: "binary", enabled: true },
  { name: "ws_cli", path: "/ws/cli", mode: "text", enabled: true },
];

(async () => {
  await sleep(1200);
  const S = w.__MOCK_SETTINGS__;
  check("mock settings reachable", !!(S && S.bridge_manager && S.socket_manager && S.websocket_manager));
  S.bridge_manager.values.bridges = DEFAULT_BRIDGES.map((b) => ({ ...b }));
  S.socket_manager.values.servers = DEFAULT_SERVERS.map((s) => ({ ...s }));
  S.websocket_manager.values.channels = DEFAULT_CHANNELS.map((c) => ({ ...c }));

  nav("#/settings/connections");
  await sleep(900);

  /* 1. the factory defaults render as three connections with no complaint */
  check("three default connections listed", rows().length === 3, rows().length);
  check("no 'already used' warning for the shared OBD chip", !warns().some((t) => /already used/.test(t)), warns());
  check("no warning at all on the defaults", warns().length === 0, warns());

  /* 2. single-consumer jacks are still policed: a new row defaults to
        OBD chip -> TCP 35000, which is the obd0 server already used by br_tcp_obd */
  addBtn().click(); await sleep(200);
  check("fourth connection added", rows().length === 4, rows().length);
  check("a second connection on the SAME TCP server is refused", warns().some((t) => /obd0 is already used/.test(t)), warns());

  /* 3. a different port makes it a new server: allowed (obd shared 4 ways) */
  const r4 = rows()[3];
  const port4 = rowPort(r4);
  check("port input found on the new row", !!port4);
  if (port4) { setText(port4, "35001"); await sleep(200); }
  check("OBD chip shared by four connections is accepted", warns().length === 0, warns());

  /* 4. the cap follows the firmware (6): rows 5 and 6 as WebSocket paths, a 7th refused */
  for (let i = 5; i <= 6; i++) {
    addBtn().click(); await sleep(150);
    const r = rows()[i - 1];
    setSel(rowSelects(r)[1], "ws"); await sleep(150);       // path defaults to /ws/br<i>
  }
  check("six connections accepted", rows().length === 6 && warns().length === 0, { rows: rows().length, warns: warns() });
  addBtn().click(); await sleep(150);
  const toasts = () => $$(".toast").map((t) => t.textContent.trim());
  check("a seventh connection is refused by the Add button (max 6)", rows().length === 6 && toasts().some((t) => /Max 6 connections/.test(t)), { rows: rows().length, toasts: toasts() });
  check("no old 'max 4' rule left", !toasts().some((t) => /Max 4/.test(t)) && !warns().some((t) => /max 4/.test(t)), toasts());

  check("no jsdom errors", errs.length === 0, errs.slice(0, 3));
  console.log(fails ? `CONNECTIONS PROBE FAIL (${fails})` : "CONNECTIONS PROBE PASS");
  process.exit(fails ? 1 : 0);
})();
