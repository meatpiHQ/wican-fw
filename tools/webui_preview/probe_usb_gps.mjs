/* The USB GPS receiver on the web UI (issue #978, 2026-10-11), over the mock:
   USB > Status gets a GPS receiver card while a receiver is on the connector
   (reading / fix / switched off), the connector card's last row reads "USB
   serial", the LTE & GPS card is the dongle's only, USB > Settings carries the
   USB GPS receiver switch (on by default) and the Dongle console tab the
   console switch only, and the Status page's System card gets a GPS row.
   Prints `USB GPS PROBE PASS`. Run after make_preview.py (plain and --min). */
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import { JSDOM, VirtualConsole } from "jsdom";

const here = dirname(fileURLToPath(import.meta.url));
const html = readFileSync(join(here, "preview.html"), "utf-8");
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
let fails = 0;
const check = (n, ok, extra) => { console.log((ok ? "PASS " : "FAIL ") + n + (extra ? "  (" + String(extra).slice(0, 200) + ")" : "")); if (!ok) fails++; };

function boot(hash, preset) {
  const errs = [];
  const vc = new VirtualConsole();
  vc.on("jsdomError", (e) => errs.push(e.message));
  const dom = new JSDOM(html, {
    runScripts: "dangerously", url: "http://wican.local/" + hash,
    pretendToBeVisual: true, virtualConsole: vc,
    beforeParse(w) {
      w.__mockPreset = preset;
      w.matchMedia = () => ({ matches: false, addListener() {}, addEventListener() {} });
      w.scrollTo = () => {}; w.HTMLCanvasElement.prototype.getContext = () => null;
    },
  });
  const w = dom.window, d = () => w.document;
  return {
    w, errs,
    text: () => (d().querySelector("#view") || {}).textContent || "",
    $: (s) => d().querySelector(s), $$: (s) => [...d().querySelectorAll(s)],
    card: (t) => [...d().querySelectorAll("#view .section")].find((c) => new RegExp(t).test(((c.querySelector("h3") || {}).textContent || "").trim())),
    nav: (h) => { w.location.hash = h; },
    rows: (c) => { const o = {}; if (!c) return o; const dts = [...c.querySelectorAll(".kv dt")]; for (const dt of dts) o[dt.textContent.trim()] = (dt.nextElementSibling || {}).textContent || ""; return o; },
    shown: (el) => !!el && el.style.display !== "none",
  };
}

(async () => {
  { /* the receiver read, no fix yet */
    const p = boot("#/usb", { usb: "gps_nofix" });
    await sleep(1500);
    const card = p.card("^GPS receiver$");
    check("USB > Status: a GPS receiver card while the receiver is on the connector", p.shown(card));
    const r = p.rows(card);
    check("the card names the receiver with its ids", /u-blox/.test(r["Receiver"] || "") && /1546:01a7/.test(r["Receiver"] || ""), r["Receiver"]);
    check("the card counts the NMEA sentences read", /NMEA, 23 sentences/.test(r["Reading"] || ""), r["Reading"]);
    check("the card says No fix yet, and why", /No fix yet/.test(r["Fix"] || "") && /view of the sky/.test(card.textContent));
    const cr = p.rows(p.card("USB Connector"));
    check("the connector card's last row is USB serial, naming the receiver", cr["USB serial"] !== undefined && /GNSS receiver, NMEA \(23 sentences\)/.test(cr["USB serial"]) && cr["Dongle console"] === undefined, cr["USB serial"]);
    check("the LTE & GPS card stays the dongle's: hidden for a plain receiver", !p.shown(p.card("LTE & GPS")));
    check("no JS errors", p.errs.length === 0, p.errs.slice(0, 2));
    /* the settings tab: the GPS switch, on */
    p.nav("#/usb/settings"); await sleep(600);
    const sc = p.card("^USB GPS receiver$");
    check("USB > Settings: a USB GPS receiver card with the switch, on by default", !!sc && !!sc.querySelector('.frow[data-key="gps"] input[type=checkbox]') && sc.querySelector('.frow[data-key="gps"] input[type=checkbox]').checked, sc && sc.textContent.slice(0, 120));
    check("the switch's help names the default and the off meaning", /On by default/.test(sc.textContent) && /ignored/.test(sc.textContent));
    /* the console tab: the console switch only, with the new name */
    p.nav("#/usb/console"); await sleep(600);
    const cc = p.card("ESPNetLink Console");
    check("Dongle console tab: the console switch, named for the dongle, and no GPS row", !!cc && !!cc.querySelector('.frow[data-key="console"]') && /ESPNetLink console over USB/.test(cc.textContent) && !cc.querySelector('.frow[data-key="gps"]') && !/Dongle console$/.test(cc.textContent));
    /* a tick on the GPS switch stages ONE document with both keys */
    p.nav("#/usb/settings"); await sleep(400);
    const sw = p.$('#usb-gps-settings-card .frow[data-key="gps"] input[type=checkbox]');
    if (sw) { sw.checked = false; sw.dispatchEvent(new p.w.Event("change", { bubbles: true })); }
    await sleep(200);
    const staged = p.w.eval("store").pending.get("usb_acm_cli");
    check("unticking stages the component's whole document: gps false, console kept", !!staged && staged.gps === false && staged.console === false, JSON.stringify(staged));
    check("the header's Submit lights", /Submit Changes \(1\)/.test((p.$("#submitbtn") || {}).textContent || ""), (p.$("#submitbtn") || {}).textContent);
  }
  { /* the fix */
    const p = boot("#/usb", { usb: "gps" });
    await sleep(1500);
    const r = p.rows(p.card("^GPS receiver$"));
    check("with a fix: the Fix chip, satellites, location, speed, heading, altitude, accuracy, age", /Fix/.test(r["Fix"] || "") && r["Satellites"] === "9" && /-25\.344400, 131\.036900/.test(r["Location"] || "") && /1\.1 km\/h/.test(r["Speed"] || "") && /271°/.test(r["Heading"] || "") && /89 m/.test(r["Altitude"] || "") && /±4 m/.test(r["Accuracy"] || "") && /0\.8 s/.test(r["Age"] || ""), JSON.stringify(r));
    check("the note says where the fix goes", /gps_latitude/.test(p.card("^GPS receiver$").textContent) && /Home Assistant/.test(p.card("^GPS receiver$").textContent));
  }
  { /* the switch off: ignored */
    const p = boot("#/usb", { usb: "gps_off" });
    await sleep(1500);
    const card = p.card("^GPS receiver$");
    const r = p.rows(card);
    check("switched off: the card stays, Reading reads Off, the note points at USB > Settings", p.shown(card) && /Off/.test(r["Reading"] || "") && r["Fix"] === undefined && /switched off under/.test(card.textContent) && !!card.querySelector('a[href="#/usb/settings"]'), JSON.stringify(r));
    const cr = p.rows(p.card("USB Connector"));
    check("the connector row says ignored", /GNSS receiver, ignored \(USB GPS receiver is off\)/.test(cr["USB serial"] || ""), cr["USB serial"]);
  }
  { /* nothing on the connector */
    const p = boot("#/usb", { usb: "none" });
    await sleep(1500);
    check("nothing on the connector: no GPS card, the row reads not bound", !p.shown(p.card("^GPS receiver$")) && /not bound/.test(p.rows(p.card("USB Connector"))["USB serial"] || ""));
  }
  { /* the dongle over USB: the page as before */
    const p = boot("#/usb", { usb: "espnetlink" });
    await sleep(1500);
    check("the dongle on the cable: no GPS card, the LTE & GPS card shown", !p.shown(p.card("^GPS receiver$")) && p.shown(p.card("LTE & GPS")));
  }
  { /* the Status page's row */
    const p = boot("#/status", { usb: "gps" });
    await sleep(1500);
    const r = p.rows(p.card("^System$"));
    check("Status > System: a GPS row with the satellites, the accuracy and the source", /9 satellites/.test(r["GPS"] || "") && /±4 m/.test(r["GPS"] || "") && /USB receiver/.test(r["GPS"] || ""), r["GPS"]);
    const q = boot("#/status", { usb: "gps_nofix" });
    await sleep(1500);
    check("Status > System: Receiver found, no fix yet while it waits", /Receiver found, no fix yet/.test(q.rows(q.card("^System$"))["GPS"] || ""), q.rows(q.card("^System$"))["GPS"]);
    const n = boot("#/status", { usb: "gps_off" });
    await sleep(1500);
    check("Status > System: no GPS row with the switch off", n.rows(n.card("^System$"))["GPS"] === undefined);
    const z = boot("#/status", { usb: "none" });
    await sleep(1500);
    check("Status > System: no GPS row with nothing on the connector", z.rows(z.card("^System$"))["GPS"] === undefined);
  }
  console.log(fails ? `USB GPS PROBE FAIL (${fails})` : "USB GPS PROBE PASS");
  process.exit(fails ? 1 : 0);
})();
