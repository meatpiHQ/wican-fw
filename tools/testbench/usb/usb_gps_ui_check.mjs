/* The USB GPS receiver on the REAL device's page (issue #978, 2026-10-11):
   Chrome over the tunnel. USB > Status must show the GPS receiver card for the
   receiver on the connector (reading, or the fix), the connector card's USB
   serial row, no LTE & GPS card; USB > Settings the USB GPS receiver switch (on);
   the Dongle console tab the console switch named for the dongle; the Status
   page's System card the GPS row. Saves screenshots next to the run's log.
   Prints `USB GPS UI PASS`.
   node usb_gps_ui_check.mjs <base url, e.g. http://127.0.0.1:18081> [out dir] */
import { mkdirSync } from "node:fs";
import { createRequire } from "node:module";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
const here = dirname(fileURLToPath(import.meta.url));
const { chromium } = createRequire(join(here, "..", "..", "webui_preview", "package.json"))("playwright");

const base = (process.argv[2] || "http://127.0.0.1:18081").replace(/\/$/, "");
const out = process.argv[3] || join(here, "..", "..", "..", "test-reports", "logs", "usb_gps_ui_" + new Date().toISOString().slice(0, 16).replace(/[-:T]/g, ""));
mkdirSync(out, { recursive: true });
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
let fails = 0;
const check = (n, ok, extra) => { console.log((ok ? "PASS " : "FAIL ") + n + (extra !== undefined ? "  [" + String(extra).slice(0, 220) + "]" : "")); if (!ok) fails++; };

const b = await chromium.launch({ channel: "chrome" });
const page = await (await b.newContext({ viewport: { width: 1024, height: 1700 } })).newPage();
const rows = (title) => page.evaluate((t) => {
  const c = [...document.querySelectorAll("#view .section")].find((s) => new RegExp(t).test(((s.querySelector("h3") || {}).textContent || "").trim()));
  if (!c) return null;
  const o = { __shown: c.style.display !== "none", __text: c.textContent };
  for (const dt of c.querySelectorAll(".kv dt")) o[dt.textContent.trim()] = (dt.nextElementSibling || {}).textContent || "";
  return o;
}, title);
const shot = async (name, title) => {
  await page.evaluate(() => { const c = document.querySelector("main.content"); if (c) c.scrollTop = 0; });
  await blankLocation();
  await sleep(300);
  const loc = title ? page.locator(".section", { has: page.locator("h3", { hasText: title }) }).first() : page.locator("#view");
  if (await loc.count()) { await loc.screenshot({ path: `${out}/${name}.png` }); console.log("shot", name); }
};

const acm = await (await fetch(base + "/api/usb/acm")).json().catch(() => null);
const gps = await (await fetch(base + "/api/gps")).json().catch(() => null);
/* the bench's position is nobody's business (the owner's rule, 2026-10-11): the log
   names the fix by its satellites and accuracy, and the shots blank the Location row */
console.log("device:", JSON.stringify(acm), gps ? JSON.stringify({ valid: gps.valid, satellites: gps.satellites, accuracy: gps.accuracy, age_ms: gps.age_ms }) : "null");
const blankLocation = () => page.evaluate(() => {
  for (const dt of document.querySelectorAll(".kv dt")) if (dt.textContent.trim() === "Location") { const dd = dt.nextElementSibling; if (dd) dd.textContent = "(withheld in this capture)"; }
});

await page.goto(base + "/#/usb"); await sleep(7000);
const g = await rows("^GPS receiver$");
check("USB > Status: the GPS receiver card is shown", !!g && g.__shown, g && g.Receiver);
check("the card names the receiver", !!g && /u-blox|GNSS/i.test(g.Receiver || ""), g && g.Receiver);
if (acm && acm.gps !== false) {
  check("the card counts the NMEA sentences", !!g && /NMEA, \d+ sentences/.test(g.Reading || ""), g && g.Reading);
  check(gps && gps.valid ? "the card shows the fix" : "the card says No fix yet", !!g && (gps && gps.valid ? /Fix/.test(g.Fix || "") && /satellites|Satellites/.test(g.__text) : /No fix yet/.test(g.Fix || "")), g && g.Fix);
} else {
  check("the card says the receiver is ignored (switch off)", !!g && /Off/.test(g.Reading || "") && /switched off/.test(g.__text));
}
const c = await rows("USB Connector");
check("the connector card's last row reads USB serial", !!c && c["USB serial"] !== undefined && c["Dongle console"] === undefined, c && c["USB serial"]);
const lg = await rows("LTE & GPS");
check("the LTE & GPS card is hidden for a plain receiver", !lg || !lg.__shown);
await shot("1_usb_status", "GPS receiver");
await shot("1b_usb_status_page");

await page.evaluate(() => { location.hash = "#/usb/settings"; }); await sleep(2500);
const s = await rows("^USB GPS receiver$");
const swOn = await page.evaluate(() => { const i = document.querySelector('#usb-gps-settings-card .frow[data-key="gps"] input[type=checkbox]'); return i ? i.checked : null; });
check("USB > Settings: the USB GPS receiver card with its switch", !!s && swOn !== null, s && s.__text.slice(0, 100));
check("the switch reads what the device applied", swOn === (acm ? acm.gps !== false : true), String(swOn));
await shot("2_usb_settings", "USB GPS receiver");

await page.evaluate(() => { location.hash = "#/usb/console"; }); await sleep(2500);
const cc = await rows("ESPNetLink Console");
/* scoped to the console card: the Settings tab's GPS card stays in the DOM while hidden */
const consoleRow = await page.evaluate(() => {
  const c = [...document.querySelectorAll("#view .section")].find((s) => /ESPNetLink Console/.test(((s.querySelector("h3") || {}).textContent || "").trim()));
  return !!c && !!c.querySelector('.frow[data-key="console"]') && !c.querySelector('.frow[data-key="gps"]');
});
check("Dongle console tab: the console switch named for the dongle, no GPS row", !!cc && /ESPNetLink console over USB/.test(cc.__text) && consoleRow);
await shot("3_usb_console", "ESPNetLink Console");

await page.goto(base + "/#/status"); await sleep(6500);
const sys = await rows("^System$");
if (acm && acm.gps !== false) {
  check("Status > System: the GPS row", !!sys && !!sys.GPS && (gps && gps.valid ? /satellites/.test(sys.GPS) && /USB receiver/.test(sys.GPS) : /Receiver found, no fix yet/.test(sys.GPS)), sys && sys.GPS);
} else {
  check("Status > System: no GPS row with the switch off", !!sys && sys.GPS === undefined);
}
await shot("4_status_system", "System");
await b.close();
console.log(fails ? `USB GPS UI FAIL (${fails})` : "USB GPS UI PASS");
process.exit(fails ? 1 : 0);
