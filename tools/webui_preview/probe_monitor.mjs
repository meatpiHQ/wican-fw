/* Probe (2026-09-07, meatpi): the CAN Monitor built after the "WiCAN PRO Monitor"
   Claude Design. Sub-tabs Monitor / Trace / Settings; the wiring check with the
   one-click enable; the RECEIVE list (chips, ASCII, sort, filter, resizable
   columns) and the decode panel (DBC signals decoded with the firmware's bit
   rules, raw frame otherwise); the TRANSMIT list (send, Space, new/edit dialog,
   cyclic, paused, trigger, on/off, context menu with formats, cut/copy/paste,
   keyboard); the Trace tab (newest first, record/stop, size, CSV); the Settings
   cards (device, CAN interface staging, decoding, settings file save/load); the
   status bar; connect/disconnect; teardown. Runs over the mock API. */
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import { JSDOM, VirtualConsole } from "jsdom";

const here = dirname(fileURLToPath(import.meta.url));
const html = readFileSync(join(here, "preview.html"), "utf-8");
const errs = [], downloads = [];
const vc = new VirtualConsole();
vc.on("jsdomError", (e) => errs.push(e.message + " " + (e.detail && e.detail.stack || "").slice(0, 300)));

const dom = new JSDOM(html, {
  runScripts: "dangerously", url: "http://wican.local/#/monitor",
  pretendToBeVisual: true, virtualConsole: vc,
  beforeParse(w) {
    w.matchMedia = () => ({ matches: false, addListener() {}, addEventListener() {} });
    w.scrollTo = () => {}; w.HTMLCanvasElement.prototype.getContext = () => null;
    if (!w.TextEncoder) { w.TextEncoder = TextEncoder; w.TextDecoder = TextDecoder; }
    w.URL.createObjectURL = () => "blob:preview"; w.URL.revokeObjectURL = () => {};
    w.HTMLAnchorElement.prototype.click = function () { if (this.download) { downloads.push(this.download); return; } this.dispatchEvent(new w.MouseEvent("click", { bubbles: true, cancelable: true })); };
  },
});
const w = dom.window, d = () => w.document;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
let fails = 0;
const check = (n, ok, extra) => { console.log((ok ? "PASS " : "FAIL ") + n + (extra !== undefined ? "  [" + JSON.stringify(extra) + "]" : "")); if (!ok) { fails++; process.exitCode = 1; } };
const $ = (sel, root) => (root || d()).querySelector(sel), $$ = (sel, root) => [...(root || d()).querySelectorAll(sel)];
const fire = (el, type) => el.dispatchEvent(new w.Event(type, { bubbles: true }));
const setText = (el, v) => { el.value = v; fire(el, "input"); fire(el, "change"); };
const key = (init) => w.dispatchEvent(new w.KeyboardEvent("keydown", Object.assign({ bubbles: true, cancelable: true }, init)));
const nav = (hash) => { w.location.hash = hash; w.dispatchEvent(new w.Event("hashchange")); };
const M = () => w.__mockState;
const tbl = (kind) => $('#view .pm-tbl[data-kind="' + kind + '"]');
const rows = (kind) => $$(".pm-r", tbl(kind));
const cells = (row) => [...row.children];
const txt = (el) => (el ? el.textContent.trim() : "");
const status = () => txt($("#view .pm-status")).replace(/\s+/g, " ");
const subtab = (id) => $$("#view .subtab").find((a) => a.dataset.id === id);
const btn = (root, re) => [...(root || d()).querySelectorAll("button")].find((b) => re.test(b.textContent.trim()));
const dlg = () => $("body > .pm-ov .pm-dlg");
const ctx = () => $("body > .pm-ctxov .pm-ctx");
const rxRowById = (id) => rows("rx").find((r) => txt(cells(r)[0]).startsWith(id));
const bytesOf = (row) => $$(".pm-b", row).map((b) => parseInt(b.textContent, 16));
const store = () => w.eval("store");
const last = (a) => a[a.length - 1];

(async () => {
  await sleep(1200);
  /* DBC data for the decode panel: a 16-bit Intel RPM at bit 0 and a Motorola byte at byte 2 */
  M().dbcs = [{ name: "demo", messages: 1, signals: 2, bytes: 200 }];
  M().dbcSignals = [
    { db: "demo", msg: "ENGINE_DATA", id: 0x123, name: "EngineRPM", unit: "rpm", start: 0, len: 16, order: "intel", signed: false, factor: 0.25, offset: 0, min: 0, max: 16384 },
    { db: "demo", msg: "ENGINE_DATA", id: 0x123, name: "CoolantTemp", unit: "C", start: 23, len: 8, order: "motorola", signed: false, factor: 1, offset: -40, min: -40, max: 215 }];
  nav("#/power"); await sleep(300); nav("#/monitor"); await sleep(1500);

  /* ---- opens disconnected ---- */
  const hdr = () => $("#view .pm-chip").parentElement;
  check("the page opens disconnected: Connect… offered, status offline, chip Not connected, no LIVE badge, the list says so", !!btn(hdr(), /Connect…/) && /offline/.test(status()) && /Not connected/.test(txt($("#view .pm-chip"))) && !$(".nav-badge") && /Not connected: press Connect/.test(txt(tbl("rx"))), status());
  check("no socket was opened", M().wsCanOpened === undefined || M().wsCanOpened === 0);
  btn(hdr(), /Connect…/).click(); await sleep(50);
  check("Connect… opens the connect dialog", !!dlg() && /Connect to CAN bus/.test(txt(dlg())));
  btn(dlg(), /^Connect$/).click(); await sleep(300);

  /* ---- layout ---- */
  check("page title and three sub-tabs", /CAN Monitor/.test(txt($("#view h1, #view .page h2, #view .ph h1")) || txt($("#view"))) && ["monitor", "trace", "settings"].every((id) => !!subtab(id)));
  check("page header carries the connection chip and a Disconnect button", !!$("#view .pm-chip") && /Disconnect/.test(txt($("#view .pm-chip").parentElement)));
  const rxHd = $$(".pm-hd button", tbl("rx")).map(txt), txHd = $$(".pm-hd button", tbl("tx")).map(txt);
  check("RECEIVE columns", rxHd.length === 7 && /^CAN-ID/.test(rxHd[0]) && rxHd.slice(1).join("|") === "Type|DLC|Data|ASCII|Cycle|Count", rxHd);
  check("TRANSMIT columns", txHd.length === 10 && txHd.slice(0, 9).join("|") === "On|CAN-ID|Type|DLC|Data|Cycle|Count|Trigger|Comment", txHd);
  check("status bar: connected, bus, load, rx/s, frames, err, TEC/REC", /connected/.test(status()) && /CAN 500k/.test(status()) && /load/.test(status()) && /rx \d+\/s/.test(status()) && /frames \d+/.test(status()) && /err 0/.test(status()) && /TEC 0 · REC 0/.test(status()), status());

  /* ---- wiring (the mock is a fresh device: bus off in settings, no channel, no bridge) ---- */
  const wire = $("#view .pm-wiring .banner.warn");
  check("Monitor tab warns that the monitor is not wired up, three crosses, an Enable button", !!wire && $$(".pm-checks .no", wire).length === 3 && !!btn(wire, /Enable CAN monitor/));
  btn(wire, /Enable CAN monitor/).click(); await sleep(100);
  const st = store(), cm = st.pending.get("can_manager"), wm = st.pending.get("websocket_manager"), bm = st.pending.get("bridge_manager");
  const ch = wm && (wm.channels || []).find((c) => c.path === "/ws/can"), br = bm && (bm.bridges || []).find((b) => b.a === "can");
  check("Enable stages the CAN bus, the ws_can channel and a slcan bridge", st.count() === 3 && cm && cm.enabled === true && ch && ch.enabled && br && br.b === ch.name && br.translator === "slcan" && br.enabled, { cm, ch, br });
  check("the banner turns into the not-active-yet note", !!$("#view .pm-wiring .banner.info") && !$("#view .pm-wiring .banner.warn"));
  st.revert(); await sleep(1600);   /* revert re-routes: a fresh page instance (offline again) */
  btn(hdr(), /Connect…/).click(); await sleep(50); btn(dlg(), /^Connect$/).click(); await sleep(1700);

  /* ---- receive list ---- */
  const rx = rows("rx");
  check("frames arrive: one row per ID with chips, ASCII, cycle and count", rx.length >= 4 && rx.every((r) => cells(r).length === 7) && !!rxRowById("18DAF110") && /EXT/.test(txt(cells(rxRowById("18DAF110"))[1])) && /[A-Z]{4}/.test(txt(cells(rxRowById("123"))[4])) && /ms/.test(txt(cells(rxRowById("123"))[5])) && Number(txt(cells(rxRowById("123"))[6])) >= 1, rx.map((r) => txt(cells(r)[0])));
  const rtr = rxRowById("7DF");
  check("the remote frame shows an RTR chip and 'remote request'", !!rtr && /RTR/.test(txt(cells(rtr)[1])) && /remote request/.test(txt(cells(rtr)[3])));
  check("bytes that changed since the previous frame are lit", $$(".pm-b.on", tbl("rx")).length > 0);
  check("the RECEIVE header counts the IDs and the sidebar shows LIVE", /\d IDs/.test(txt($("#view .pm-pcount"))) && !!$(".nav-badge"));
  /* sort */
  $$(".pm-hd button", tbl("rx"))[6].click(); await sleep(250);
  const counts = rows("rx").map((r) => Number(txt(cells(r)[6])));
  check("Count header sorts by count, descending", counts.length > 2 && counts.every((c, i) => i === 0 || c <= counts[i - 1]) && /▼/.test(txt($$(".pm-hd button", tbl("rx"))[6])), counts);
  $$(".pm-hd button", tbl("rx"))[0].click(); await sleep(250);
  const ids = rows("rx").map((r) => parseInt(txt(cells(r)[0]), 16));
  check("CAN-ID header sorts by ID, ascending", ids.every((v, i) => i === 0 || v >= ids[i - 1]) && /▲/.test(txt($$(".pm-hd button", tbl("rx"))[0])), ids);
  /* filter */
  setText($("#view .pm-toolbar input"), "3"); await sleep(250);
  check("the ID filter keeps only IDs containing the text", rows("rx").length === 2 && rows("rx").every((r) => /3/.test(txt(cells(r)[0]))), rows("rx").map((r) => txt(cells(r)[0])));
  setText($("#view .pm-toolbar input"), ""); await sleep(250);
  /* column resize */
  const tin = $(".pm-tin", tbl("rx")), before = tin.style.getPropertyValue("--c");
  const handle = $(".pm-rs", tbl("rx"));
  handle.dispatchEvent(new w.MouseEvent("mousedown", { bubbles: true, clientX: 100 }));
  w.dispatchEvent(new w.MouseEvent("mousemove", { clientX: 140 })); w.dispatchEvent(new w.MouseEvent("mouseup", {}));
  check("dragging a header handle resizes the column", before.startsWith("96px") && tin.style.getPropertyValue("--c").startsWith("136px"), tin.style.getPropertyValue("--c"));

  /* ---- decode panel ---- */
  rxRowById("123").click(); await sleep(300);
  const dec = $("#view .pm-decode");
  const b = bytesOf(rxRowById("123"));
  const rpm = (b[0] | (b[1] << 8)) * 0.25, temp = b[2] - 40;
  const sigVals = $$(".pm-sig", dec).map((s) => [txt(s.children[0]), txt(s.children[1])]);
  check("clicking a row opens the decode panel with the DBC message and its signals", dec.style.display !== "none" && /ENGINE_DATA/.test(txt(dec)) && sigVals.length === 2 && sigVals[0][0] === "EngineRPM" && sigVals[1][0] === "CoolantTemp", sigVals);
  const shown0 = Number(sigVals[0][1]), shown1 = Number(sigVals[1][1]);
  check("signals decode with the firmware's bit rules (Intel LSB start, Motorola MSB start)", Math.abs(shown0 - rpm) < 0.3 && shown1 === temp, { shown: [shown0, shown1], expect: [rpm, temp] });
  rxRowById("2C4").click(); await sleep(300);
  check("an ID without DBC shows RAW FRAME with the byte grid", /RAW FRAME/.test(txt(dec)) && /No DBC match/.test(txt(dec)) && $$(".pm-raw span", dec).length > 8);
  btn(dec, /×/).click(); await sleep(100);
  check("the × closes the panel", dec.style.display === "none");

  /* ---- transmit list ---- */
  let tx = rows("tx");
  check("three shipped transmit rows, all manual, the 29-bit one chipped EXT", tx.length === 3 && tx.every((r) => /manual/.test(txt(cells(r)[5])) && txt(cells(r)[6]) === "0") && /EXT/.test(txt(cells(tx[2])[2])), tx.map((r) => txt(cells(r)[1])));
  M().wsSent = [];
  btn(tx[0], /^Send$/).click(); await sleep(100);
  check("Send writes the slcan frame and counts it", last(M().wsSent) === "t7DF802010C0000000000\r" && txt(cells(rows("tx")[0])[6]) === "1", M().wsSent);
  rows("tx")[2].click(); await sleep(50);
  key({ code: "Space", key: " " }); await sleep(100);
  check("Space sends the selected row (29-bit id as an extended frame)", rows("tx")[2].classList.contains("sel") && last(M().wsSent) === "T18DB33F180201000000000000\r", last(M().wsSent));
  /* new message via the dialog */
  btn($("#view .pm-txpanel"), /New Message/).click(); await sleep(100);
  check("+ New Message opens the dialog", !!dlg() && /New Transmit Message/.test(txt(dlg())));
  const idIn = $$("input", dlg())[0]; setText(idIn, "321");
  const dlcSel = $("select", dlg()); dlcSel.value = "2"; fire(dlcSel, "change"); await sleep(50);
  const byteIns = $$(".pm-bytesed input", dlg());
  check("DLC 2 leaves two byte boxes", byteIns.length === 2);
  setText(byteIns[0], "AA"); setText(byteIns[1], "55");
  const cmt = $$(".pm-frow input", dlg()).pop(); setText(cmt, "probe row");
  btn(dlg(), /^OK$/).click(); await sleep(100);
  tx = rows("tx");
  check("OK adds the row with its data and comment", !dlg() && tx.length === 4 && txt(cells(tx[3])[1]).startsWith("321") && txt(cells(tx[3])[4]) === "AA 55" && txt(cells(tx[3])[8]) === "probe row");
  btn(tx[3], /✎/).click(); await sleep(100);
  setText($$("input", dlg())[0], ""); btn(dlg(), /^OK$/).click(); await sleep(50);
  check("an empty ID keeps the dialog open and flags the field", !!dlg() && $$("input", dlg())[0].classList.contains("bad"));
  /* cyclic + paused */
  setText($$("input", dlg())[0], "321"); setText($$(".pm-frow input", dlg())[1] === undefined ? $$("input", dlg())[1] : $$(".pm-f input", dlg()).find((i) => i.style.width === "80px"), "40");
  btn(dlg(), /^OK$/).click(); await sleep(100);
  const n0 = M().wsSent.length; await sleep(300);
  check("a 40 ms cycle sends repeatedly", M().wsSent.length - n0 >= 4 && last(M().wsSent) === "t3212AA55\r" && /40 ms/.test(txt(cells(rows("tx")[3])[5])), M().wsSent.length - n0);
  btn(rows("tx")[3], /✎/).click(); await sleep(50);
  const paused = $$('input[type="checkbox"]', dlg())[0]; paused.checked = true; fire(paused, "change");
  btn(dlg(), /^OK$/).click(); await sleep(60);
  const n1 = M().wsSent.length; await sleep(250);
  check("Paused stops the cycle and the row shows the pause mark", M().wsSent.length === n1 && /⏸ 40 ms/.test(txt(cells(rows("tx")[3])[5])));
  /* trigger */
  btn(rows("tx")[3], /✎/).click(); await sleep(50);
  const pz = $$('input[type="checkbox"]', dlg())[0]; pz.checked = false; fire(pz, "change");
  setText($$(".pm-f input", dlg()).find((i) => i.style.width === "80px"), "0");
  setText($$(".pm-frow input", dlg()).find((i) => i.placeholder === "e.g. 7E8"), "123");
  btn(dlg(), /^OK$/).click(); await sleep(50);
  const n2 = M().wsSent.length; await sleep(700);
  check("a trigger on RX ID 123 sends the row whenever 123 arrives", M().wsSent.length > n2 && M().wsSent.slice(n2).every((s) => s === "t3212AA55\r") && txt(cells(rows("tx")[3])[7]) === "123", M().wsSent.length - n2);
  const chk = $('input[type="checkbox"]', rows("tx")[3]); chk.checked = false; fire(chk, "change"); await sleep(50);
  const n3 = M().wsSent.length; await sleep(700);
  check("switching the row off stops the trigger and dims the row", M().wsSent.length === n3 && rows("tx")[3].classList.contains("off"));
  /* context menu + formats */
  rows("tx")[0].dispatchEvent(new w.MouseEvent("contextmenu", { bubbles: true, cancelable: true, clientX: 200, clientY: 300 })); await sleep(50);
  const items = $$(":scope > button", ctx()).map((b) => txt(b.children[0]));
  check("right-click opens the context menu with the PCAN-View items", !!ctx() && items.join("|") === "New Message…|Edit Message…|Cut|Copy|Paste|Delete|Clear All" && $$(".pm-sub", ctx()).length === 2, items);
  const subData = $$(".pm-sub", ctx())[1]; subData.dispatchEvent(new w.MouseEvent("mouseenter")); await sleep(30);
  btn(subData, /Decimal/).click(); await sleep(100);
  check("Data Bytes Format: Decimal shows the transmit data in decimal", !ctx() && txt(cells(rows("tx")[0])[4]) === "2 1 12 0 0 0 0 0", txt(cells(rows("tx")[0])[4]));
  rows("tx")[0].dispatchEvent(new w.MouseEvent("contextmenu", { bubbles: true, cancelable: true, clientX: 200, clientY: 300 })); await sleep(50);
  const subId = $$(".pm-sub", ctx())[0]; subId.dispatchEvent(new w.MouseEvent("mouseenter")); btn(subId, /Decimal/).click(); await sleep(100);
  check("CAN ID Format: Decimal shows 7DF as 2015 in both lists", txt(cells(rows("tx")[0])[1]) === "2015" && txt(cells(rxRowById("291") || rows("rx")[0])[0]) === "291");
  rows("tx")[0].dispatchEvent(new w.MouseEvent("contextmenu", { bubbles: true, cancelable: true, clientX: 200, clientY: 300 })); await sleep(50);
  $$(".pm-sub", ctx())[0].dispatchEvent(new w.MouseEvent("mouseenter")); btn($$(".pm-sub", ctx())[0], /Hexadecimal/).click(); await sleep(50);
  rows("tx")[0].dispatchEvent(new w.MouseEvent("contextmenu", { bubbles: true, cancelable: true, clientX: 200, clientY: 300 })); await sleep(50);
  $$(".pm-sub", ctx())[1].dispatchEvent(new w.MouseEvent("mouseenter")); btn($$(".pm-sub", ctx())[1], /Hexadecimal/).click(); await sleep(50);
  check("back to hex", txt(cells(rows("tx")[0])[1]).startsWith("7DF") && txt(cells(rows("tx")[0])[4]) === "02 01 0C 00 00 00 00 00");
  rows("tx")[1].dispatchEvent(new w.MouseEvent("contextmenu", { bubbles: true, cancelable: true, clientX: 200, clientY: 300 })); await sleep(50);
  btn(ctx(), /^Copy/).click(); await sleep(30);
  rows("tx")[1].dispatchEvent(new w.MouseEvent("contextmenu", { bubbles: true, cancelable: true, clientX: 200, clientY: 300 })); await sleep(50);
  btn(ctx(), /^Paste/).click(); await sleep(60);
  check("Copy then Paste appends a copy of the row", rows("tx").length === 5 && txt(cells(rows("tx")[4])[4]) === txt(cells(rows("tx")[1])[4]));
  rows("tx")[4].dispatchEvent(new w.MouseEvent("contextmenu", { bubbles: true, cancelable: true, clientX: 200, clientY: 300 })); await sleep(50);
  btn(ctx(), /^Delete/).click(); await sleep(60);
  check("Delete removes the row", rows("tx").length === 4);
  rows("tx")[0].dispatchEvent(new w.MouseEvent("contextmenu", { bubbles: true, cancelable: true, clientX: 200, clientY: 300 })); await sleep(30);
  key({ key: "Escape" }); await sleep(30);
  check("Escape closes the menu", !ctx());
  /* keyboard */
  rows("tx")[0].click(); key({ key: "c", ctrlKey: true }); key({ key: "v", ctrlKey: true }); await sleep(60);
  check("Ctrl+C / Ctrl+V duplicate the selected row", rows("tx").length === 5);
  rows("tx")[4].click(); key({ key: "Delete" }); await sleep(60);
  check("Delete removes the selected row", rows("tx").length === 4);
  key({ key: "Insert" }); await sleep(60);
  check("Insert opens the new-message dialog", !!dlg() && /New Transmit Message/.test(txt(dlg())));
  key({ key: "Escape" }); await sleep(30);
  check("Escape closes the dialog", !dlg());
  rows("tx")[0].click(); key({ key: "Enter" }); await sleep(60);
  check("Enter opens the edit dialog for the selected row", !!dlg() && /Edit Transmit Message/.test(txt(dlg())) && $$("input", dlg())[0].value === "7DF");
  key({ key: "Escape" }); await sleep(30);
  /* save / load the settings file */
  btn($("#view .pm-txpanel"), /^Save$/).click(); await sleep(50);
  check("Save downloads wican-can-monitor.json", last(downloads) === "wican-can-monitor.json", downloads);
  key({ key: "Escape", shiftKey: true }); await sleep(60);
  check("Shift+Esc clears the transmit list", rows("tx").length === 0 && /No transmit messages/.test(txt(tbl("tx"))));
  const fileIn = $('#view input[type="file"][accept*="json"]');
  const file = new w.File([JSON.stringify({ version: 1, tx: [{ id: "1AB", bytes: [1, 2, 3], cycle: 0, comment: "loaded" }, { id: "1FFFFFFF", ext: true, bytes: [] }], dataFormat: "hex", idFormat: "hex" })], "s.json", { type: "application/json" });
  Object.defineProperty(fileIn, "files", { value: [file], configurable: true }); fire(fileIn, "change"); await sleep(200);
  check("Load restores the transmit list from the file", rows("tx").length === 2 && txt(cells(rows("tx")[0])[8]) === "loaded" && /EXT/.test(txt(cells(rows("tx")[1])[2])), rows("tx").map((r) => txt(cells(r)[1])));

  /* ---- pause ---- */
  btn($("#view .pm-toolbar"), /Pause/).click(); await sleep(50);
  const fr = status().match(/frames (\d+)/)[1]; await sleep(400);
  check("Pause freezes the counters and says paused", status().match(/frames (\d+)/)[1] === fr && /paused/.test(status()) && /paused/.test(txt($("#view .pm-pcount"))));
  btn($("#view .pm-toolbar"), /Resume/).click(); await sleep(50);

  /* ---- trace tab ---- */
  subtab("trace").click(); await sleep(400);
  let tr = rows("tr");
  check("Trace lists frames newest first with Tx rows", tr.length > 5 && Number(txt(cells(tr[0])[0])) >= Number(txt(cells(tr[1])[0])) && tr.some((r) => txt(cells(r)[1]) === "Tx") && /\d+ \/ 1000 frames/.test(txt($(".pm-pcount", paneOf("trace")))),
    { n: tr.length, t01: tr.slice(0, 2).map((r) => txt(cells(r)[0])), tx: tr.filter((r) => txt(cells(r)[1]) === "Tx").length, label: txt($(".pm-pcount", paneOf("trace"))) });
  btn(paneOf("trace"), /^Stop$/).click(); await sleep(300); const trN = rows("tr").length; await sleep(400);
  check("Stop halts recording", rows("tr").length === trN && !!btn(paneOf("trace"), /^Record$/));
  btn(paneOf("trace"), /^Record$/).click(); await sleep(600);
  check("Record resumes", rows("tr").length > trN, { before: trN, after: rows("tr").length, label: txt($(".pm-pcount", paneOf("trace"))), status: status() });
  btn(paneOf("trace"), /Save CSV/).click(); await sleep(50);
  check("Save CSV downloads a trace file", /^can-trace-.*\.csv$/.test(last(downloads)), last(downloads));
  const lim = $("select", paneOf("trace")); lim.value = "300"; fire(lim, "change"); await sleep(50);
  btn(paneOf("trace"), /^Clear$/).click(); await sleep(50);
  check("Clear empties the trace and the size select is honoured", rows("tr").length === 0 && /0 \/ 300 frames/.test(txt(paneOf("trace"))));

  /* ---- settings tab ---- */
  subtab("settings").click(); await sleep(300);
  const set = paneOf("settings");
  check("DEVICE card: model, firmware, IP, WiFi mode, uptime", /Model/.test(txt(set)) && /Firmware/.test(txt(set)) && /IP address/.test(txt(set)) && /WiFi mode/.test(txt(set)) && /Uptime/.test(txt(set)));
  check("DECODING card lists the demo DBC with its messages", /demo/.test(txt(set)) && /1 mapped/.test(txt(set)) && !!btn(set, /Upload \.dbc/));
  check("CAN FD is shown as unavailable and disabled", /Not available on this CAN controller/.test(txt(set)) && $$('input[type="checkbox"]', set).some((c) => c.disabled));
  const bsel = $$("select", set).find((s) => [...s.options].some((o) => o.value === "500"));
  check("the bit rate select shows the bus setting", bsel && bsel.value === "500");
  bsel.value = "250"; fire(bsel, "change"); await sleep(50);
  const lr = $$('input[type="radio"]', set)[1]; lr.checked = true; fire(lr, "change"); await sleep(50);
  const pc = store().pending.get("can_manager");
  check("bit rate and listen-only stage can_manager", pc && pc.baud === "250" && pc.silent === true && store().count() === 1, pc);
  check("the wiring check is repeated in the CAN INTERFACE card", !!$(".banner", set) && !!btn(set, /Enable CAN monitor/));
  store().pending.clear(); store().dirty.clear(); w.eval("renderSubmit()");

  /* ---- connect / disconnect ---- */
  subtab("monitor").click(); await sleep(200);
  btn($("#view .pm-chip").parentElement, /Disconnect/).click(); await sleep(100);
  check("Disconnect: chip says Not connected, status offline, button Connect", /Not connected/.test(txt($("#view .pm-chip"))) && /offline/.test(status()) && !!btn($("#view .pm-chip").parentElement, /Connect…/));
  btn($("#view .pm-chip").parentElement, /Connect…/).click(); await sleep(50);
  check("Connect… opens the connect dialog with bit rate and mode", !!dlg() && /Connect to CAN bus/.test(txt(dlg())) && $$("select", dlg()).length === 1 && $$('input[type="radio"]', dlg()).length === 2);
  btn(dlg(), /^Connect$/).click(); await sleep(250);
  check("Connect reconnects", !dlg() && /connected/.test(status()) && /Disconnect/.test(txt($("#view .pm-chip").parentElement)));

  /* ---- teardown ---- */
  const socketsBefore = M().wsSent.length;
  nav("#/power"); await sleep(400);
  key({ code: "Space", key: " " }); await sleep(50);
  check("leaving drops the LIVE badge, the key handler and the overlays", w.eval("monLive") === false && !$(".nav-badge") && M().wsSent.length === socketsBefore && !$("body > .pm-ov") && !$("body > .pm-ctxov"));

  check("no page errors", errs.length === 0, errs.slice(0, 2));
  console.log(fails ? `FAILED ${fails}` : "ALL PASS");
  process.exit(fails ? 1 : 0);

  function paneOf(id) { const a = subtab(id); if (!a) return null; const panes = $$("#view .pm-body > .pane > .pm-tab"); const idx = ["monitor", "trace", "settings"].indexOf(id); return panes[idx]; }
})();
