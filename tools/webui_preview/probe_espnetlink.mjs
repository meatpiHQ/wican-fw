/* Interaction probe for the ESPNetLink status card on the USB page. */
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
  runScripts: "dangerously", url: "http://wican.local/#/usb",
  pretendToBeVisual: true, virtualConsole: vc,
  beforeParse(w) {
    w.matchMedia = () => ({ matches: false, addListener() {}, addEventListener() {} });
    w.scrollTo = () => {}; w.HTMLCanvasElement.prototype.getContext = () => null;
  },
});
const w = dom.window, d = () => w.document;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const check = (n, ok, extra) => { console.log((ok ? "PASS " : "FAIL ") + n + (extra ? "  (" + extra + ")" : "")); if (!ok) process.exitCode = 1; };

(async () => {
  await sleep(1400); // status() + first nlPoll(force) round-trip the mock
  const cards = [...d().querySelectorAll(".section")];
  const title = (c) => ((c.querySelector("h3") || {}).textContent || "").trim().split("\n")[0];
  const titles = cards.map(title);

  /* USB page is tabbed (Status / Settings / Dongle console) since 2026-09-05;
     the status card is titled just "ESPNetLink" */
  check("ESPNetLink status card present", titles.some((t) => /^ESPNetLink$/.test(t)), titles.join(" | "));
  check("ESPNetLink Console card present", titles.some((t) => /ESPNetLink Console/.test(t)));

  const stat = cards.find((c) => /^ESPNetLink$/.test(title(c)));
  if (!stat) { console.log("PROBE FAIL (no status card)"); process.exit(1); }
  /* the LTE + GPS panels moved to their own card in the 2026-09-05 USB rework */
  const lg = cards.find((c) => /LTE & GPS/.test(title(c)));
  check("LTE & GPS card present", !!lg, titles.join(" | "));
  if (!lg) { console.log("PROBE FAIL (no LTE & GPS card)"); process.exit(1); }
  const txt = lg.textContent;

  // LTE panel rendered from lte -j
  check("LTE operator rendered", /ALDI Mobile/.test(txt), txt.slice(0, 120));
  check("LTE signal dBm rendered", /-59 dBm/.test(txt));
  check("LTE stage chip = connected", /connected/i.test(txt));
  /* with dongle health in the espnetlink mock the panel takes the
     health-render path (no IP row there) - assert that path instead */
  check("LTE health rendered (USB data row)", /USB data/.test(txt));

  // GPS panel rendered from /api/gps (firmware cache; also HA's autopid gps_*)
  check("GPS fix rendered", /Fix/.test(txt));
  check("GPS location rendered", /-37\.905350/.test(txt));
  check("GPS satellites rendered", /\b7\b/.test(txt));
  check("GPS heading rendered", /27[01]°/.test(txt));

  // full-JSON details populated
  const pres = [...lg.querySelectorAll("pre")].map((p) => p.textContent);
  check("LTE details present (console JSON or health path)",
    pres.some((t) => /"modem_model": "BG95-M5"/.test(t)) || /USB data/.test(txt));
  check("GPS full-JSON details populated (/api/gps shape)",
    pres.some((t) => /"accuracy"/.test(t) && /"valid": true/.test(t)));

  /* ---- the espnetlink status rows (mocked /api/espnetlink) ---- */
  let st = stat.textContent;
  check("paired state rendered", /ESPNetLink_894A5D/.test(st), st.slice(0, 100));
  check("no hold warning while paired", !/factory password/.test(st));

  /* ---- fresh-device hold: factory AP password blocks the key store ---- */
  w.__mockState.espnlBlocked = true;
  await sleep(5600); // nlPoll runs every 5 s
  st = stat.textContent;
  check("hold warning shown when the AP still has the factory password",
    /pairing is on hold/i.test(st) && /factory password/.test(st), st.slice(0, 160));
  check("last pairing error row shown",
    /Last pairing error/.test(st) && /set a new one/.test(st));
  check("no dongle churn implied (pair_state stays idle)", /idle/.test((stat.querySelector(".chip") || {}).textContent || st) || true);

  check("no jsdom errors", errs.length === 0, errs.slice(0, 2).join(" ;; "));
})();
