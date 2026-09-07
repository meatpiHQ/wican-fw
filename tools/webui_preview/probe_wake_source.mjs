/* Probe (2026-09-07, meatpi): the Status page names this boot's cause — "Last wake-up":
   battery-voltage recovery vs the periodic check-in vs a plain power-on, a requested
   restart or a crash. The mock's last restart record is __mockState.lastRestart. */
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
  runScripts: "dangerously", url: "http://wican.local/#/status",
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
const $$ = (sel) => [...d().querySelectorAll(sel)];
const nav = (hash) => { w.location.hash = hash; w.dispatchEvent(new w.Event("hashchange")); };
const rowVal = (label) => {
  const dt = $$("#view dl.kv dt").find((e) => e.textContent.trim() === label);
  return dt && dt.nextElementSibling ? dt.nextElementSibling.textContent.trim() : null;
};

(async () => {
  await sleep(900);
  check("Status shows the mock's planned settings-apply restart as this boot's cause", rowVal("Last wake-up") === "Settings applied", rowVal("Last wake-up"));
  check("the row sits in the System card next to the boot count", !!rowVal("Boot count") && !!rowVal("Unexpected resets"));

  const cases = [
    [{ reason: "software", planned: true, planned_reason: "power_wake", source: "sleep_mode" }, "Battery voltage recovered, woke from sleep"],
    [{ reason: "software", planned: true, planned_reason: "periodic_wake", source: "sleep_mode" }, "Periodic check-in, woke from sleep"],
    [{ reason: "software", planned: true, planned_reason: "user_request", source: "web_ui" }, "Restart requested · web UI"],
    [{ reason: "software", planned: true, planned_reason: "internal_recovery", source: "sleep_mode" }, "Internal recovery restart · power saving"],
    [{ reason: "software", planned: true, planned_reason: "ota_apply", source: "ota" }, "Firmware update · OTA"],
    [{ reason: "panic", planned: false, planned_reason: "none", source: "unknown" }, "Crash (panic), unexpected"],
    [{ reason: "task_wdt", planned: false, planned_reason: "none", source: "unknown" }, "Task watchdog, unexpected"],
    [{ reason: "poweron", planned: false, planned_reason: "none", source: "unknown" }, "Power-on"],
    [{ reason: "software", planned: true, planned_reason: "something_new", source: "unknown" }, "Planned restart (something_new)"],
  ];
  for (const [rec, want] of cases) {
    w.__mockState.lastRestart = rec;
    nav("#/power"); await sleep(300); nav("#/status"); await sleep(900);
    check("Last wake-up for " + (rec.planned ? rec.planned_reason : rec.reason), rowVal("Last wake-up") === want, rowVal("Last wake-up"));
  }
  check("no page errors", errs.length === 0, errs.slice(0, 2));
  console.log(fails ? `FAILED ${fails}` : "ALL PASS");
  process.exit(fails ? 1 : 0);
})();
