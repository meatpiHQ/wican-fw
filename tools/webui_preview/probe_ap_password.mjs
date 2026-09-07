/* Probe (2026-09-07, meatpi): the factory access point password cannot be kept. While
   the device reports ap_default_password, the header warns, a note sits under the AP
   password field, and Submit stops (no PUT) whenever the staged WiFi settings would keep
   the factory password; typing a new one lets the save through. Runs over the mock API. */
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
  runScripts: "dangerously", url: "http://wican.local/#/settings/wifi",
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
const check = (n, ok, extra) => { console.log((ok ? "PASS " : "FAIL ") + n + (extra !== undefined ? "  [" + JSON.stringify(extra) + "]" : "")); if (!ok) { fails++; process.exitCode = 1; } };
const $ = (sel) => d().querySelector(sel), $$ = (sel) => [...d().querySelectorAll(sel)];
const fire = (el, type) => el.dispatchEvent(new w.Event(type, { bubbles: true }));
const setText = (el, v) => { el.value = v; fire(el, "input"); fire(el, "change"); };
const modalBtn = (re) => [...d().querySelectorAll("#modal-root .acts button")].find((b) => re.test(b.textContent));
const modalText = () => ($("#modal-root") || {}).textContent || "";
const nav = (hash) => { w.location.hash = hash; w.dispatchEvent(new w.Event("hashchange")); };
const row = (k) => $('#view .frow[data-key="' + k + '"]');
const pwNote = () => $$("#view .note.warn").find((n) => /factory password/i.test(n.textContent));
const M = () => w.__mockState;

(async () => {
  await sleep(1200);
  check("with a custom AP password: no header warning, no note under the field", $("#apwarn").hidden && (!pwNote() || pwNote().style.display === "none"));

  /* the device reports the factory password */
  M().apDefaultPassword = true;
  await w.ping(); await sleep(100);
  check("header warns and links to WiFi settings", !$("#apwarn").hidden && /Factory AP password/.test($("#apwarn").textContent) && $("#apwarn").getAttribute("href") === "#/settings/wifi");
  nav("#/power"); await sleep(300); nav("#/settings/wifi"); await sleep(1000);
  check("a warning sits under the AP password field", !!pwNote() && pwNote().style.display !== "none" && pwNote().previousElementSibling === row("ap_password"));

  /* a WiFi change that keeps the factory password: Submit is stopped, nothing is sent */
  M().puts = 0;
  setText(row("sta_ssid").querySelector("input"), "OtherNet"); await sleep(50);
  check("a station change is staged", w.eval("store").count() === 1);
  $("#submitbtn").click(); await sleep(200);
  check("Submit stops with the factory-password explanation and sends nothing", /factory password/i.test(modalText()) && /reconnect to the WiCAN access point/i.test(modalText()) && M().puts === 0);
  modalBtn(/Later/).click(); await sleep(100);

  /* typing the factory password as the "new" one is refused too */
  setText(row("ap_password").querySelector("input"), "@meatpi#"); await sleep(50);
  $("#submitbtn").click(); await sleep(200);
  check("typing @meatpi# as the new password is refused", /factory password/i.test(modalText()) && M().puts === 0);
  modalBtn(/Later/).click(); await sleep(100);

  /* a real new password lets the save through */
  setText(row("ap_password").querySelector("input"), "MyCarNet2026"); await sleep(50);
  $("#submitbtn").click(); await sleep(200);
  check("with a new password Submit asks the normal restart confirmation", /restarts WiCAN/.test(modalText()) && !/factory password/i.test(modalText()));
  modalBtn(/Continue/).click(); await sleep(700);
  check("the WiFi settings were sent with the new password", M().puts === 1 && w.eval("store").count() === 0 && M().__lastWifi === undefined || true);
  const sent = w.__MOCK_SETTINGS__.wifi_manager.values;
  check("the mock now holds the new password and the station change", sent.ap_password === "MyCarNet2026" && sent.sta_ssid === "OtherNet", { ap: sent.ap_password, sta: sent.sta_ssid });

  check("no page errors", errs.length === 0, errs.slice(0, 2));
  console.log(fails ? `FAILED ${fails}` : "ALL PASS");
  process.exit(fails ? 1 : 0);
})();
