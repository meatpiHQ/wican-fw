/* jsdom smoke: load preview.html, visit every route, report JS errors.
 * usage: node smoke.mjs   (npm i jsdom first; node >= 20) */
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import { JSDOM, VirtualConsole } from "jsdom";

const here = dirname(fileURLToPath(import.meta.url));
const html = readFileSync(join(here, "preview.html"), "utf-8");

const ROUTES = ["status", "settings", "automate", "power", "logger",
  "dashboard", "monitor", "terminal", "advanced", "system", "vpn", "usb",
  "about", "dtc", "dbc", "events", "scripts", "uds", "j2534", "files",
  "logs", "sysmon", "allsettings"];

const errors = [];
const vc = new VirtualConsole();
vc.on("jsdomError", (e) => errors.push("jsdomError: " + e.message));
vc.on("error", (...a) => errors.push("console.error: " + a.join(" ")));

const dom = new JSDOM(html, {
  runScripts: "dangerously",
  url: "http://wican.local/",
  pretendToBeVisual: true,
  virtualConsole: vc,
  beforeParse(win) {
    win.matchMedia = () => ({ matches: false, media: "", addListener() {}, removeListener() {}, addEventListener() {}, removeEventListener() {} });
    win.scrollTo = () => {};
    win.HTMLCanvasElement.prototype.getContext = () => null;
  },
});
const w = dom.window;
w.addEventListener("error", (e) => errors.push("window.onerror: " + e.message));
w.addEventListener("unhandledrejection", (e) =>
  errors.push("unhandledrejection: " + (e.reason && e.reason.message || e.reason)));

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

(async () => {
  await sleep(700); /* boot + first route */
  if (errors.length) {
    console.log("BOOT errors:");
    errors.forEach((e) => console.log("   " + e.slice(0, 400)));
  }
  let fails = 0;
  for (const r of ROUTES) {
    const before = errors.length;
    w.location.hash = "#/" + r;
    w.dispatchEvent(new w.Event("hashchange"));
    await sleep(450);
    const delta = errors.slice(before);
    const view = w.document.querySelector("#view");
    const rendered = view && view.textContent.trim().length > 0;
    if (delta.length || !rendered) {
      fails++;
      console.log(`FAIL ${r}${rendered ? "" : " (empty view)"}`);
      delta.forEach((e) => console.log("   " + e.slice(0, 300)));
    } else {
      console.log(`ok   ${r}`);
    }
  }
  console.log(fails ? `SMOKE FAIL: ${fails}/${ROUTES.length} routes` :
    `SMOKE PASS: ${ROUTES.length} routes, 0 errors`);
  process.exit(fails ? 1 : 0);
})();
