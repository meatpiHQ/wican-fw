/* Probe (2026-10-11, issue #976): "Pause VPN" per network. Under Settings > WiFi the
   main network carries a Pause VPN switch under its password and every backup network a
   "pause VPN" tick beside "trusted"; a tick stages the WiFi settings. The VPN page sets
   nothing: its badge reads "Paused on <network>" while the station is on a marked
   network, one note at the end of VPN Settings names the marked networks with a link to
   the WiFi settings, the debug block carries a Network row; the Status page's VPN row
   maps the state to words. Runs over the mock API, two boots (connected, paused). */
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import { JSDOM, VirtualConsole } from "jsdom";

const here = dirname(fileURLToPath(import.meta.url));
const html = readFileSync(join(here, "preview.html"), "utf-8");
let fails = 0;
const check = (n, ok, extra) => { console.log((ok ? "PASS " : "FAIL ") + n + (extra !== undefined ? "  [" + JSON.stringify(extra) + "]" : "")); if (!ok) { fails++; process.exitCode = 1; } };
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function boot(hash, preset) {
  const errs = [];
  const vc = new VirtualConsole();
  vc.on("jsdomError", (e) => errs.push(e.message + " " + (e.detail && e.detail.stack || "").slice(0, 300)));
  const dom = new JSDOM(html, {
    runScripts: "dangerously", url: "http://wican.local/" + hash,
    pretendToBeVisual: true, virtualConsole: vc,
    beforeParse(w) {
      w.__mockPreset = preset;
      w.matchMedia = () => ({ matches: false, addListener() {}, addEventListener() {} });
      w.scrollTo = () => {}; w.HTMLCanvasElement.prototype.getContext = () => null;
      if (!w.TextEncoder) { w.TextEncoder = TextEncoder; w.TextDecoder = TextDecoder; }
    },
  });
  const w = dom.window, d = () => w.document;
  const $ = (sel) => d().querySelector(sel), $$ = (sel, root) => [...(root || d()).querySelectorAll(sel)];
  const cardByTitle = (t) => $$("#view .section").find((c) => (c.querySelector(".legend, h3") || {}).textContent.trim().startsWith(t));
  const go = async (h) => { w.location.hash = h; await sleep(1200); };
  return { w, d, $, $$, cardByTitle, go, errs, close: () => dom.window.close() };
}

(async () => {
  /* ---- Settings > WiFi: the controls on the station card ---- */
  {
    const p = boot("#/settings/wifi", { vpn: { state: "connected", network: "HomeWiFi", paused: false } });
    await sleep(1500);
    /* the mock's station card lists the main network HomeWiFi; give it a backup to tick */
    const sta = p.cardByTitle("WiFi Network");
    check("station card present", !!sta);
    const row = p.$('#view .frow[data-key="sta_vpn_pause"]');
    check("Pause VPN row on the station card, labelled exactly Pause VPN", !!row && (row.querySelector("label") || {}).textContent.trim() === "Pause VPN", row && row.querySelector("label").textContent.trim());
    const keys = p.$$("#view .frow[data-key]", sta).map((r) => r.dataset.key);
    const iSsid = keys.indexOf("sta_ssid"), iPw = keys.indexOf("sta_password"), iPause = keys.indexOf("sta_vpn_pause"), iAdv = keys.indexOf("__advtoggle");
    check("the switch sits under the password, above the advanced fold", iSsid >= 0 && iPw > iSsid && iPause === iPw + 1 && (iAdv < 0 || iPause < iAdv), keys.slice(0, 6));
    check("help names the home WiFi and the resume", !!row && /stays down while WiCAN is on this network/.test(row.textContent) && /comes back as soon as it leaves/.test(row.textContent));
    const sw = row && row.querySelector(".switch input");
    check("switch off by default on the mock device", !!sw && !sw.checked);
    /* add a backup network: the block carries the pause VPN tick beside trusted */
    const add = p.$$("button", sta).find((b) => /Add backup network/.test(b.textContent));
    check("Add backup network button", !!add);
    add && add.click(); await sleep(200);
    const block = p.$$("label.rowflex", sta).find((l) => /pause VPN/.test(l.textContent));
    const trusted = p.$$("label.rowflex", sta).find((l) => /trusted/.test(l.textContent));
    check("backup block has a pause VPN tick beside trusted, off by default", !!block && !!trusted && block.parentElement === trusted.parentElement && !block.querySelector("input").checked);
    /* a tick stages the WiFi settings with the flat key */
    const ssidIn = p.$$("input", trusted && trusted.parentElement).find((i) => i.placeholder === "SSID");
    if (ssidIn) { ssidIn.value = "Workshop"; ssidIn.dispatchEvent(new p.w.Event("change", { bubbles: true })); }
    const tick = block && block.querySelector("input");
    if (tick) { tick.checked = true; tick.dispatchEvent(new p.w.Event("change", { bubbles: true })); }
    await sleep(200);
    const staged = p.w.eval("store").pending.get("wifi_manager");
    check("the tick stages fallback1_vpn_pause true and the name", !!staged && staged.fallback1_vpn_pause === true && staged.fallback1_ssid === "Workshop", staged && { ssid: staged.fallback1_ssid, pause: staged.fallback1_vpn_pause });
    const submit = p.$("#submitbtn");
    check("the header's Submit lights up", !!submit && !submit.disabled && /Submit Changes/.test(submit.textContent), submit && submit.textContent.trim());
    /* the main switch stages sta_vpn_pause */
    if (sw) { sw.checked = true; sw.dispatchEvent(new p.w.Event("change", { bubbles: true })); }
    await sleep(200);
    const staged2 = p.w.eval("store").pending.get("wifi_manager");
    check("the switch stages sta_vpn_pause true", !!staged2 && staged2.sta_vpn_pause === true);
    /* the trash button clears the flag with the slot */
    const trash = p.$$("button.danger", trusted && trusted.parentElement.parentElement).find((b) => b);
    trash && trash.click(); await sleep(200);
    const staged3 = p.w.eval("store").pending.get("wifi_manager");
    check("deleting the backup clears its pause flag", !!staged3 && staged3.fallback1_vpn_pause === false && staged3.fallback1_ssid === "");
    check("no page errors on the WiFi tab", p.errs.length === 0, p.errs.slice(0, 2));
    p.close();
  }

  /* ---- the VPN page and the Status page, connected ---- */
  {
    const p = boot("#/vpn", { vpn: { state: "connected", network: "HomeWiFi", paused: false } });
    await sleep(1500);
    const badge = p.$("#view .page .chip");
    check("badge Connected (ok) while the tunnel is up", !!badge && badge.classList.contains("ok") && /^Connected$/.test(badge.textContent.trim()), badge && badge.textContent.trim());
    const settings = p.cardByTitle("VPN Settings");
    const note = settings && settings.querySelector('[data-key="vpn_pause_note"]');
    check("the settings card ends with the pause note, no network marked on the mock", !!note && /Pauses on no network yet/.test(note.textContent) && settings.querySelector(".body").lastElementChild === note, note && note.textContent.trim().slice(0, 60));
    const link = note && note.querySelector('a[href="#/settings/wifi"]');
    check("the note links to Settings > WiFi > Station", !!link && /Settings > WiFi > Station/.test(link.textContent));
    check("no Pause card of its own on the VPN page", !p.cardByTitle("Pause on home networks"));
    const dbg = p.cardByTitle("VPN Debug");
    const dts = p.$$(".kv dt", dbg).map((e) => e.textContent.trim());
    const netDd = p.$$(".kv dt", dbg).find((e) => e.textContent.trim() === "Network");
    check("debug block has a Network row with the station's network", dts.includes("Network") && !!netDd && netDd.nextElementSibling.textContent.trim() === "HomeWiFi", dts);
    /* the VPN switched off folds the note */
    const sel = settings && settings.querySelector("select");
    if (sel) { sel.value = "disabled"; sel.dispatchEvent(new p.w.Event("change", { bubbles: true })); }
    await sleep(200);
    check("the note hides while the VPN is disabled", !!note && note.style.display === "none");
    /* drop the staged change: a navigation with one pending opens the unsaved-changes dialog */
    p.w.eval("store").pending.clear(); p.w.eval("store").dirty.clear(); p.w.eval("renderSubmit()");
    await p.go("#/status");
    const vpnDt = p.$$("#view .kv dt").find((e) => e.textContent.trim() === "VPN");
    check("Status page VPN row reads Connected", !!vpnDt && vpnDt.nextElementSibling.textContent.trim() === "Connected", vpnDt && vpnDt.nextElementSibling.textContent.trim());
    check("no page errors (connected boot)", p.errs.length === 0, p.errs.slice(0, 2));
    p.close();
  }

  /* ---- paused: the station is on a marked network ---- */
  {
    /* the main network marked on the mock device, the station on it */
    const p = boot("#/vpn", { vpn: { state: "paused", network: "HomeWiFi", paused: true, uptime_s: 0, connects: 3 }, wifi: { sta_vpn_pause: true } });
    await sleep(1500);
    const badge = p.$("#view .page .chip");
    check("badge Paused on HomeWiFi (info chip)", !!badge && badge.classList.contains("info") && badge.textContent.trim() === "Paused on HomeWiFi", badge && badge.textContent.trim());
    const settings = p.cardByTitle("VPN Settings");
    const note = settings && settings.querySelector('[data-key="vpn_pause_note"]');
    check("the note names the marked network", !!note && /Pauses on HomeWiFi: the tunnel stays down while WiCAN is on that network/.test(note.textContent), note && note.textContent.trim().slice(0, 80));
    const dbg = p.cardByTitle("VPN Debug");
    const state = p.$$(".kv dt", dbg).find((e) => e.textContent.trim() === "State");
    check("debug State reads paused", !!state && state.nextElementSibling.textContent.trim() === "paused");
    await p.go("#/status");
    const vpnDt = p.$$("#view .kv dt").find((e) => e.textContent.trim() === "VPN");
    check("Status page VPN row reads Paused on HomeWiFi", !!vpnDt && vpnDt.nextElementSibling.textContent.trim() === "Paused on HomeWiFi", vpnDt && vpnDt.nextElementSibling.textContent.trim());
    check("no page errors (paused boot)", p.errs.length === 0, p.errs.slice(0, 2));
    p.close();
  }

  console.log(fails ? `VPN PAUSE PROBE FAIL (${fails})` : "VPN PAUSE PROBE PASS");
  process.exit(fails ? 1 : 0);
})();
