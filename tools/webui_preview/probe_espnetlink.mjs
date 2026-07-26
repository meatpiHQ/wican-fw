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

  check("ESPNetLink Status card present", titles.some((t) => /ESPNetLink Status/.test(t)), titles.join(" | "));
  check("ESPNetLink Console card present", titles.some((t) => /ESPNetLink Console/.test(t)));

  const stat = cards.find((c) => /ESPNetLink Status/.test(title(c)));
  const txt = stat ? stat.textContent : "";

  // LTE panel rendered from lte -j
  check("LTE operator rendered", /ALDI Mobile/.test(txt), txt.slice(0, 120));
  check("LTE signal dBm rendered", /-59 dBm/.test(txt));
  check("LTE stage chip = connected", /connected/i.test(txt));
  check("LTE IP rendered", /100\.88\.65\.162/.test(txt));

  // GPS panel rendered from /api/gps (firmware cache; also HA's autopid gps_*)
  check("GPS fix rendered", /Fix/.test(txt));
  check("GPS location rendered", /-37\.905350/.test(txt));
  check("GPS satellites rendered", /\b7\b/.test(txt));
  check("GPS heading rendered", /27[01]°/.test(txt));

  // full-JSON details populated
  const pres = [...stat.querySelectorAll("pre")].map((p) => p.textContent);
  check("LTE full-JSON details populated", pres.some((t) => /"modem_model": "BG95-M5"/.test(t)));
  check("GPS full-JSON details populated (/api/gps shape)",
    pres.some((t) => /"accuracy"/.test(t) && /"valid": true/.test(t)));

  check("no jsdom errors", errs.length === 0, errs.slice(0, 2).join(" ;; "));
})();
