/* Probe (2026-09-07, meatpi): WiFi settings UX — the mode is a tile selector above the
   cards (not a row in the Access Point card), each card folds to a note when the mode
   does not use it, live chips show the radio state, and "open" is not an AP security
   option. Runs over the mock API (mock WiFi mode: apsta, station connected, AP up). */
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
const $ = (sel) => d().querySelector(sel), $$ = (sel, root) => [...(root || d()).querySelectorAll(sel)];
const cardByTitle = (t) => $$("#view .section").find((c) => (c.querySelector(".legend, h3") || {}).textContent.trim().startsWith(t));
/* visible through its ancestors too: the cards fold by hiding a wrapper, not each row */
const shown = (el) => { if (!el) return false; for (let e = el; e && e.id !== "view"; e = e.parentElement) { if (e.style && e.style.display === "none") return false; } return true; };
const noteIn = (card, re) => $$(".empty", card).find((n) => re.test(n.textContent));
const tile = (v) => $('#view .wm-mode[data-mode="' + v + '"]');
const row = (k) => $('#view .frow[data-key="' + k + '"]');

(async () => {
  await sleep(1200);
  const cards = $$("#view .section").map((c) => (c.querySelector(".legend, h3") || {}).textContent.trim());
  check("WiFi tab order: mode selector, then Access Point, then Station", /^WiFi mode/.test(cards[0] || "") && /Access Point/.test(cards[1] || "") && /Station/.test(cards[2] || ""), cards.slice(0, 3));
  check("four mode tiles, the mock's apsta marked, none called recommended", $$("#view .wm-mode").length === 4 && tile("apsta").classList.contains("on") && !/recommended/i.test($("#view .wm-modes").textContent));
  check("no WiFi mode row inside the Access Point card", !row("mode"));
  const auth = row("ap_auth") && row("ap_auth").querySelector("select");
  check("AP security offers no open option and names automatic as WPA2", !!auth && ![...auth.options].some((o) => o.value === "open") && [...auth.options].some((o) => /Automatic \(WPA2\)/.test(o.textContent)), auth && [...auth.options].map((o) => o.value));
  const apCard = cardByTitle("WiFi Access Point"), staCard = cardByTitle("WiFi Network");
  check("live chips: AP shows clients + address, station shows connected + IP", /client/.test(apCard.textContent) && /Connected/.test(staCard.textContent));

  /* station only: the AP card folds to a note, the station stays */
  tile("sta").click(); await sleep(150);
  check("picking Station only stages one change and marks the tile", w.eval("store").count() === 1 && tile("sta").classList.contains("on") && !tile("apsta").classList.contains("on"));
  check("Access Point card folds to its note; station fields stay", !shown(row("ap_ssid")) && /access point is off in this mode/i.test(apCard.textContent) && shown(row("sta_ssid")));
  /* access point only: the station card folds */
  tile("ap").click(); await sleep(150);
  check("picking Access point only folds the station card and shows the AP fields", shown(row("ap_ssid")) && !shown(row("sta_ssid")) && /station is off in this mode/i.test(staCard.textContent));
  /* off: a warning */
  tile("off").click(); await sleep(150);
  check("WiFi off shows the USB/Bluetooth warning and folds both cards", /USB or Bluetooth/.test($("#view").textContent) && !shown(row("ap_ssid")) && !shown(row("sta_ssid")));
  tile("apsta").click(); await sleep(150);
  check("back to Access point + Station: both cards open, warning gone", shown(row("ap_ssid")) && shown(row("sta_ssid")) && $$("#view .note.warn").every((n) => !/USB or Bluetooth/.test(n.textContent) || n.style.display === "none"));
  /* radio arbitration lives in the WiFi tab (2026-09-07) */
  const im = cardByTitle("Radio Arbitration");
  check("Radio Arbitration card sits in the WiFi tab with its three switches and the Bluetooth note", !!im && ["enabled", "sta_ble_handover", "ap_ble_exclusive"].every((k) => !!row(k) && im.contains(row(k))) && /share one radio/.test(im.textContent));
  check("the switches carry plain-words labels", /Pause the station while a phone is connected over Bluetooth/.test(row("sta_ble_handover").textContent) && /Pause the access point while a phone is connected over Bluetooth/.test(row("ap_ble_exclusive").textContent));
  /* the AP auto-off recommendation (mock: apsta, ap_auto_disable off) */
  const nudge = () => $$("#view .banner.info").find((b) => /turn the access point off while the station is connected/i.test(b.textContent));
  check("with Access point + Station and auto-off unset, the recommendation shows and the switch is outside the advanced fold", !!nudge() && nudge().style.display !== "none" && shown(row("ap_auto_disable")) && /Turn the access point off while the station is connected/.test(row("ap_auto_disable").textContent));
  tile("sta").click(); await sleep(150);
  check("Station only hides the recommendation", nudge().style.display === "none");
  tile("apsta").click(); await sleep(150);
  [...nudge().querySelectorAll("button")].find((b) => /Turn it on/.test(b.textContent)).click(); await sleep(150);
  const pend = w.eval("store").pending.get("wifi_manager");
  check("Turn it on stages ap_auto_disable, ticks the switch and hides the recommendation", pend && pend.ap_auto_disable === true && row("ap_auto_disable").querySelector('input[type="checkbox"]').checked && nudge().style.display === "none");
  w.eval("store").pending.clear(); w.eval("store").dirty.clear(); w.eval("renderSubmit()");
  check("no page errors", errs.length === 0, errs.slice(0, 2));
  console.log(fails ? `FAILED ${fails}` : "ALL PASS");
  process.exit(fails ? 1 : 0);
})();
