/* Probe (2026-10-16): a socket server switched on with no connection row is
   the slcan-over-TCP trap (a Discord report: "client connected", no data).
   The Connections card must flag such a server and add the CAN bus row in one
   click; the Socket servers card under All Settings must say which connection
   each server carries, flag the orphan, and its shortcut must land on the
   Connections card with the row ready; the mock's /api/sockets carries
   `bridge` like the firmware. Runs over the mock API:
   `python make_preview.py --fresh && node probe_sockets_note.mjs` -> `SOCKETS NOTE PROBE PASS`. */
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
const nav = (hash) => { w.location.hash = hash; w.dispatchEvent(new w.Event("hashchange")); };
const store = () => w.eval("store");
const warns = () => $$("#view .banner.warn").map((b) => b.textContent.trim());
const rows = () => $$("#view .brline");
const rowSelects = (r) => [...r.querySelectorAll("select")];          // [iface, conn]
const rowDetail = (r) => r.parentElement;                              // the row box (port + protocol live below the line)
const rowPort = (r) => rowDetail(r).querySelector('input[type="number"]');
const rowProto = (r) => [...rowDetail(r).querySelectorAll("select")].slice(2)[0];   // the Protocol select (CAN bus rows)

const DEFAULT_BRIDGES = [
  { name: "br_obd", a: "obd", b: "ws_obd", translator: "raw", enabled: true },
  { name: "br_tcp_obd", a: "obd0", b: "obd", translator: "raw", enabled: true },
  { name: "br_usb_obd", a: "usb_obd", b: "obd", translator: "raw", enabled: true },
];
const SERVERS_TRAP = [
  { name: "obd0", proto: "tcp", port: 35000, enabled: true },
  { name: "slcan0", proto: "tcp", port: 3333, enabled: true },   // the trap: on, nothing bridged
  { name: "gvret0", proto: "tcp", port: 23, enabled: false },
  { name: "udp0", proto: "udp", port: 17, enabled: false },
];
const DEFAULT_CHANNELS = [
  { name: "ws_obd", path: "/ws/obd", mode: "text", enabled: true },
  { name: "ws_can", path: "/ws/can", mode: "binary", enabled: true },
  { name: "ws_cli", path: "/ws/cli", mode: "text", enabled: true },
];
const resetMock = (S) => {
  S.bridge_manager.values.bridges = DEFAULT_BRIDGES.map((b) => ({ ...b }));
  S.socket_manager.values.servers = SERVERS_TRAP.map((s) => ({ ...s }));
  S.websocket_manager.values.channels = DEFAULT_CHANNELS.map((c) => ({ ...c }));
  store().pending.clear(); store().dirty.clear(); w.eval("renderSubmit()");
};

(async () => {
  await sleep(1200);
  const S = w.__MOCK_SETTINGS__;
  check("mock settings reachable", !!(S && S.bridge_manager && S.socket_manager && S.websocket_manager));
  resetMock(S);

  /* 0. the mock's /api/sockets and /api/ws carry `bridge` like the firmware */
  const sk = await (await w.fetch("/api/sockets")).json();
  const byName = Object.fromEntries((sk.servers || []).map((s) => [s.name, s.bridge]));
  check("/api/sockets: obd0 is carried by br_tcp_obd, slcan0 by nothing", byName.obd0 === "br_tcp_obd" && byName.slcan0 === null && "bridge" in sk.servers[1], byName);
  const wsl = await (await w.fetch("/api/ws")).json();
  check("/api/ws: ws_obd is carried by br_obd, ws_can by nothing", (wsl.channels || []).some((c) => c.name === "ws_obd" && c.bridge === "br_obd") && (wsl.channels || []).some((c) => c.name === "ws_can" && c.bridge === null));

  /* 1. the Connections card flags the orphan server and nothing else */
  nav("#/settings/connections");
  await sleep(900);
  check("three default connections listed", rows().length === 3, rows().length);
  const orphan = warns().filter((t) => /switched on but no connection uses it/.test(t));
  check("slcan0 (TCP 3333) flagged as switched on with no connection", orphan.length === 1 && /slcan0 \(TCP 3333\)/.test(orphan[0]) && /gets nothing/.test(orphan[0]), orphan);
  check("obd0 (carried by br_tcp_obd) and the switched-off servers are not flagged", !warns().some((t) => /obd0|gvret0|udp0/.test(t)), warns());
  const btn = $('#view button[data-orphan="slcan0"]');
  check("the banner offers the CAN bus row on TCP 3333 as slcan", !!btn && /TCP 3333 \(slcan\)/.test(btn.textContent), btn && btn.textContent.trim());

  /* 2. one click adds the row, stages the bridge, and the flag goes away */
  btn.click(); await sleep(300);
  check("a fourth connection appeared", rows().length === 4, rows().length);
  const r4 = rows()[3];
  check("the new row is CAN bus -> TCP server, port 3333, protocol slcan",
    rowSelects(r4)[0].value === "can" && rowSelects(r4)[1].value === "tcp" && rowPort(r4) && Number(rowPort(r4).value) === 3333 && rowProto(r4) && rowProto(r4).value === "slcan",
    { iface: rowSelects(r4)[0].value, conn: rowSelects(r4)[1].value, port: rowPort(r4) && rowPort(r4).value, proto: rowProto(r4) && rowProto(r4).value });
  check("the flag is gone and no other warning took its place", warns().length === 0, warns());
  const bm = store().pending.get("bridge_manager");
  const staged = bm && (bm.bridges || []).find((b) => b.a === "can" && b.b === "slcan0");
  check("bridge_manager staged with can <-slcan-> slcan0 enabled", !!staged && staged.translator === "slcan" && staged.enabled === true, staged);
  check("the three factory rows survived the staging", bm && bm.bridges.length === 4 && DEFAULT_BRIDGES.every((b) => bm.bridges.some((x) => x.name === b.name && x.enabled)), bm && bm.bridges.map((b) => b.name));

  /* 3. All Settings > Socket servers: carried / orphan per server, the shortcut */
  resetMock(S);
  nav("#/allsettings");
  await sleep(900);
  const head = $$("#view button.legend").find((b) => (b.querySelector("h3") || {}).textContent === "Socket servers");
  check("the Socket servers group is listed under All Settings", !!head);
  head.click(); await sleep(900);
  const carried = $('#view [data-server="obd0"]');
  check("obd0: carries connection br_tcp_obd", !!carried && /carries connection br_tcp_obd/.test(carried.textContent), carried && carried.textContent);
  const off = $('#view [data-server="gvret0"]');
  check("gvret0: switched off, no warning", !!off && /switched off/.test(off.textContent), off && off.textContent);
  const foot = $('#view button[data-orphan="slcan0"]');
  const footWarn = warns().find((t) => /slcan0 \(TCP 3333\) is switched on but no connection uses it/.test(t));
  check("slcan0 flagged under the form with the shortcut", !!foot && !!footWarn, { btn: foot && foot.textContent.trim(), warn: footWarn });

  /* 4. the shortcut lands on the Connections card with the row ready */
  foot.click(); await sleep(1000);
  check("the shortcut opened Settings > Connections", w.location.hash === "#/settings/connections", w.location.hash);
  check("the Connections card shows the pre-filled fourth row", rows().length === 4, rows().length);
  const r4b = rows()[3];
  check("pre-filled row: CAN bus, TCP 3333, slcan, enabled", !!r4b && rowSelects(r4b)[0].value === "can" && Number(rowPort(r4b).value) === 3333 && rowProto(r4b).value === "slcan" && !!r4b.querySelector('input[type="checkbox"]:checked'),
    r4b && { iface: rowSelects(r4b)[0].value, port: rowPort(r4b) && rowPort(r4b).value, proto: rowProto(r4b) && rowProto(r4b).value });
  check("no orphan flag once the row exists", !warns().some((t) => /no connection uses it/.test(t)), warns());
  const bm2 = store().pending.get("bridge_manager");
  check("the pre-filled row is staged as a bridge", !!bm2 && (bm2.bridges || []).some((b) => b.a === "can" && b.b === "slcan0" && b.translator === "slcan" && b.enabled));

  /* 5. a second visit does not add the row twice (one-shot prefill) */
  nav("#/status"); await sleep(400);
  nav("#/settings/connections"); await sleep(900);
  check("re-opening the card keeps the staged four rows, adds nothing", rows().length === 4, rows().length);

  check("no jsdom errors", errs.length === 0, errs.slice(0, 3));
  console.log(fails ? `SOCKETS NOTE PROBE FAIL (${fails})` : "SOCKETS NOTE PROBE PASS");
  process.exit(fails ? 1 : 0);
})();
