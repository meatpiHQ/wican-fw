/* The ESPNetLink easy setup over the preview's mock device (2026-10-13):
   the no-home-WiFi path through the wizard, the bar and the header segment on
   a configured WiCAN, the dongle screen on its own (the PIN row, the GPS
   nudge), the Set up door on Settings > USB. Prints `ESPNL EASY PROBE PASS`.
   usage: node probe_espnl_easy.mjs [preview.html]  (playwright over the
   installed Chrome, the way the other probes run) */
import { pathToFileURL } from "node:url";
import { createRequire } from "node:module";
import path from "node:path";
const here = path.dirname(new URL(import.meta.url).pathname.replace(/^\/([A-Za-z]:)/, "$1"));
const { chromium } = createRequire(path.join(here, "package.json"))("playwright");

const preview = pathToFileURL(process.argv[2] || path.join(here, "preview.html")).href;
let n = 0, failed = 0;
const ok = (name, cond) => { n++; if (!cond) { failed++; console.log("FAIL " + name); } else console.log("ok   " + name); };
const browser = await chromium.launch({ channel: "chrome" });

async function boot(preset, hash, height = 1400) {
  const ctx = await browser.newContext({ viewport: { width: 1024, height }, deviceScaleFactor: 1 });
  const page = await ctx.newPage();
  /* a fresh browser for every scene: the bar's per-dongle dismissal is wiped
     once per context (sessionStorage survives the reload inside a scene,
     so a reload keeps what the scene stored) */
  await page.addInitScript((p) => { window.__mockPreset = p; try { if (!sessionStorage.getItem("probe-nl-fresh")) { localStorage.removeItem("wican-nl-seen"); sessionStorage.setItem("probe-nl-fresh", "1"); } } catch (_) {} }, preset);
  const errors = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await page.goto(preview + hash);
  await page.waitForTimeout(2400);
  return { ctx, page, errors };
}
const text = (page, sel) => page.evaluate((s) => { const el = document.querySelector(s); return el ? el.textContent : null; }, sel);
const has = (page, sel) => page.evaluate((s) => !!document.querySelector(s), sel);
const visible = (page, sel) => page.evaluate((s) => { const el = document.querySelector(s); return !!el && !el.hidden && el.offsetParent !== null; }, sel);
const h2 = (page) => text(page, "#view h2");
const body = (page) => text(page, "#view");

const fresh = { apDefaultPassword: true, tryResult: "connected", tryDelayMs: 600 };
const nlOk = { kind: "espnetlink", device_id: "206ef1894a5d", sim: "ready", lte: true, operator: "Telstra", gps_fix: false, hint: "" };
const nlNudge = { ...nlOk, hint: "no_fix", no_fix_s: 760 };
const afterRestart = { wifi: { sta_ssid: "", mode: "apsta", ap_auto_disable: false }, staConnected: false, apDefaultPassword: false };

async function toWifi(preset) {
  const b = await boot(preset, "#/setup");
  const { page } = b;
  for (let i = 0; i < 8 && !/Join your home WiFi/.test(await h2(page)); i++) {
    const t = await h2(page);
    if (/Keep your WiCAN private/.test(t)) { for (const cb of await page.$$('.qs-agree input[type="checkbox"]')) await cb.click(); }
    if (/Secure the access point/.test(t)) { await page.fill("#qs-ap-pw", "garage-door-2026"); await page.fill("#qs-ap-pw2", "garage-door-2026"); }
    const btn = await page.$(".qs-foot button.pri:not([disabled])");
    if (!btn) break;
    await btn.click(); await page.waitForTimeout(600);
  }
  await page.waitForTimeout(3400);
  return b;
}

{ /* 1. the Home WiFi step: the offer only with a dongle that has a SIM */
  const b = await toWifi({ ...fresh, usb: "espnetlink_fresh_usb" });
  ok("wifi step reached", /Join your home WiFi/.test(await h2(b.page)));
  ok("offer shown with a dongle", await has(b.page, ".qs-offer"));
  ok("offer names the way", /No home WiFi here\? Use the ESPNetLink/.test(await text(b.page, ".qs-offer")));
  ok("offer names the carrier", /Telstra/.test(await text(b.page, ".qs-offer")));
  /* 2. the dongle step on that path */
  await b.page.click(".qs-offer button");
  await b.page.waitForTimeout(3400);
  ok("dongle step title", /Your ESPNetLink/.test(await h2(b.page)));
  ok("USB Ethernet preselected", await b.page.evaluate(() => document.querySelector("#qs-nl-usb").checked));
  ok("access point note", /keeps its own access point on/.test(await body(b.page)));
  ok("rows read live", /Telstra \(LTE\)/.test(await body(b.page)));
  /* 3. the review */
  await b.page.click(".qs-foot button.pri");
  await b.page.waitForTimeout(800);
  ok("review title", /Review, then restart/.test(await h2(b.page)));
  const rv = await body(b.page);
  ok("review: home WiFi none", /None/.test(rv) && /Internet over the ESPNetLink/.test(rv));
  ok("review: access point stays on", /Stays on/.test(rv));
  ok("review: dongle connected over the cable", /Connected over the cable/.test(rv));
  ok("review: continues at the same address", /Quick Setup continues here/.test(rv) && /192\.168\.0\.10\/#\/setup\/checks/.test(rv));
  ok("no page errors (wizard path)", b.errors.length === 0);
  await b.ctx.close();
}
{ /* no dongle: the step is as it was */
  const b = await toWifi({ ...fresh, usb: "none" });
  ok("no offer without a dongle", !(await has(b.page, ".qs-offer")));
  await b.ctx.close();
}
{ /* no SIM: no offer either */
  const b = await toWifi({ ...fresh, usb: "espnetlink_nosim" });
  ok("no offer with a dongle without a SIM", !(await has(b.page, ".qs-offer")));
  await b.ctx.close();
}
{ /* 4. reconnect over the access point, recognised from the device after a reload */
  const b = await boot({ ...afterRestart, usb: "espnetlink_fresh_usb" }, "#/setup/reconnect");
  ok("reconnect over the AP", /Join WiCAN's network again/.test(await h2(b.page)));
  ok("reconnect shows the one address", /192\.168\.0\.10\/#\/setup\/checks/.test(await body(b.page)));
  await b.page.waitForTimeout(11000);
  ok("reconnect: WiCAN is back", /WiCAN is back/.test(await body(b.page)));
  await b.ctx.close();
}
{ /* 5. checks with the dongle as the internet */
  const b = await boot({ ...afterRestart, usb: "espnetlink_usb_nofix" }, "#/setup/checks");
  await b.page.waitForTimeout(1500);
  ok("checks title", /Connected through the ESPNetLink/.test(await h2(b.page)));
  const ck = await body(b.page);
  ok("checks: internet via the dongle", /Internet via ESPNetLink: connected/.test(ck));
  ok("checks: access point on", /Access point on, secured/.test(ck));
  ok("checks: HA needs the VPN", /Home Assistant needs the VPN away from home/.test(ck));
  ok("no page errors (checks)", b.errors.length === 0);
  await b.ctx.close();
}
{ /* 6. the bar and the header segment on a configured WiCAN */
  const b = await boot({ usb: "espnetlink_usb_nofix", usbStatus: nlOk }, "#/status", 900);
  await b.page.waitForTimeout(1500);
  ok("bar shown once for a new dongle", await visible(b.page, "#nlbar"));
  ok("bar text", /ESPNetLink connected/.test(await text(b.page, "#nlbar")) && /SIM found, Telstra connected, GPS no fix yet/.test(await text(b.page, "#nlbar")));
  ok("header segment", /USB: ESPNetLink/.test(await text(b.page, "#cp-usb")) && /LTE/.test(await text(b.page, "#cp-usb")) && /no fix/.test(await text(b.page, "#cp-usb")));
  await b.page.click("#nlbar a");
  await b.page.waitForTimeout(300);
  ok("OK hides the bar", !(await visible(b.page, "#nlbar")));
  await b.page.reload(); await b.page.waitForTimeout(2400);
  ok("the bar stays away after a reload (per dongle, this browser)", !(await visible(b.page, "#nlbar")));
  ok("the header segment stays", /USB: ESPNetLink/.test(await text(b.page, "#cp-usb")));
  await b.ctx.close();
}
{ /* 7. the dongle screen on its own */
  const b = await boot({ usb: "espnetlink_usb_nofix", usbStatus: nlOk }, "#/espnetlink");
  await b.page.waitForTimeout(1500);
  ok("standalone title", /ESPNetLink/.test(await text(b.page, "#view h1, #view .page h1, #view .ph h1")) || /ESPNetLink/.test(await text(b.page, "#view")));
  ok("standalone screen", /On the cable now/.test(await h2(b.page)));
  ok("standalone: Save and Not now", /Save/.test(await text(b.page, ".qs-foot")) && /Not now/.test(await text(b.page, ".qs-foot")));
  ok("standalone: no tiles", !(await has(b.page, ".qs-tiles")));
  ok("bar hidden on its own screen", !(await visible(b.page, "#nlbar")));
  await b.page.click(".qs-foot button.pri");
  await b.page.waitForTimeout(500);
  ok("nothing to change says so", /Nothing to change/.test(await text(b.page, "#toasts")));
  ok("no page errors (standalone)", b.errors.length === 0);
  await b.ctx.close();
}
{ /* 8. a PIN-locked SIM */
  const b = await boot({ usb: "espnetlink_pin", usbStatus: { ...nlOk, sim: "pin", lte: false } }, "#/espnetlink");
  await b.page.waitForTimeout(1500);
  ok("PIN locked row", /PIN locked/.test(await body(b.page)));
  ok("PIN field offered", await has(b.page, "#qs-pin"));
  ok("PIN row and the APN asked on their own, the verdict waits for the PIN (2026-10-28)", await b.page.evaluate(() => { const pin = document.querySelector("#qs-pin"), apn = document.querySelector("#qs-apn"), v = document.querySelector("#qs-apn-verdict"); return !!pin && !pin.closest("details") && !!apn && !apn.closest("details") && /Your SIM's APN/.test(document.body.textContent) && !!v && /Waiting for the PIN/.test(v.textContent); }));
  ok("button says Send PIN and save", /Send PIN and save/.test(await text(b.page, ".qs-foot button.pri")));
  ok("header says SIM PIN", /SIM PIN/.test(await text(b.page, "#cp-usb")));
  await b.ctx.close();
}
{ /* 9. the GPS nudge: the screen, then the bar */
  const b = await boot({ usb: "espnetlink_nudge", usbStatus: nlNudge }, "#/espnetlink");
  await b.page.waitForTimeout(1500);
  ok("nudge card", /No GPS fix after 13 minutes on USB Ethernet/.test(await body(b.page)));
  ok("GPS row warns", /No fix after 13 min/.test(await body(b.page)));
  await b.page.click("button:has-text('Switch to WiFi modem')");
  await b.page.waitForTimeout(300);
  ok("one press ticks WiFi modem", await b.page.evaluate(() => document.querySelector("#qs-nl-wifi").checked));
  await b.ctx.close();
  const c = await boot({ usb: "espnetlink_nudge", usbStatus: nlNudge }, "#/status", 900);
  await c.page.waitForTimeout(1500);
  ok("nudge bar", await visible(c.page, "#nlbar") && /no GPS fix after 13 minutes/.test(await text(c.page, "#nlbar")));
  ok("nudge bar is the warning kind", await c.page.evaluate(() => document.querySelector("#nlbar").classList.contains("crit")));
  await c.ctx.close();
}
{ /* 10. Settings > USB: the third door */
  const b = await boot({ usb: "espnetlink_usb_nofix", usbStatus: nlOk }, "#/usb");
  await b.page.waitForTimeout(1800);
  ok("Set up button on the ESPNetLink card", await b.page.evaluate(() => [...document.querySelectorAll(".section .legend .tools button")].some((x) => /Set up/.test(x.textContent))));
  await b.ctx.close();
}
{ /* 11. the header segment for the other devices, and none */
  for (const [name, usb, st, re] of [
    ["gps receiver", "gps", { kind: "gps", fix: true, sats: 9 }, /USB: GPS.*fix, 9 sats/],
    ["ethernet adapter", "eth", { kind: "eth", link: true, ip: "10.42.2.37" }, /USB: Ethernet adapter.*10\.42\.2\.37/],
  ]) {
    const b = await boot({ usb, usbStatus: st }, "#/status", 700);
    await b.page.waitForTimeout(1200);
    ok("header: " + name, re.test(await text(b.page, "#cp-usb")));
    ok("no bar for a " + name, !(await visible(b.page, "#nlbar")));
    await b.ctx.close();
  }
  const b = await boot({ usb: "none" }, "#/status", 700);
  await b.page.waitForTimeout(1200);
  ok("no segment with nothing on the connector", !(await visible(b.page, "#cp-usb")));
  await b.ctx.close();
}
await browser.close();
console.log(failed ? `ESPNL EASY PROBE FAIL (${failed} of ${n})` : `ESPNL EASY PROBE PASS (${n} checks)`);
process.exit(failed ? 1 : 0);
