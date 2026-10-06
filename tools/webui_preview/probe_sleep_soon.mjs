/* Sleep countdown probe (2026-10-06): the bar under the header that counts
   down to the device's sleep entry on every page, Quick Setup included.
   /api/sleep names the rule (`pending`: the sleep delay, or the critical
   floor that cuts a longer delay short) and the seconds left; the bar ticks
   by itself between reads, clears when the battery recovers, and when the
   device drops off at the end of the countdown the offline pill says it went
   to sleep. Born from a default device that slept after 2 min on an 11.6 V
   supply in the middle of a setup, with "Sleep after 5 min" on the page.
   The hold (same day): a Keep awake 10 min button on the bar, the page's own
   dialog one minute before the entry (once per deadline, visible tab only),
   a press posts /api/sleep/hold and the bar says so; three per boot.
   Prints SLEEP SOON PROBE PASS. Runs over the mock API (~60 s: the page
   reads the device every 3 s). */
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import { JSDOM, VirtualConsole } from "jsdom";

const here = dirname(fileURLToPath(import.meta.url));
const html = readFileSync(join(here, "preview.html"), "utf-8");
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
let fails = 0;
const check = (n, ok, extra) => { console.log((ok ? "PASS " : "FAIL ") + n + (extra !== undefined ? "  [" + JSON.stringify(extra) + "]" : "")); if (!ok) { fails++; process.exitCode = 1; } };
const errs = [];
const vc = new VirtualConsole();
vc.on("jsdomError", (e) => errs.push(e.message));
const dom = new JSDOM(html, { runScripts: "dangerously", url: "http://wican.local/#/status", pretendToBeVisual: true, virtualConsole: vc,
  beforeParse(w) { w.__mockPreset = { apDefaultPassword: false }; w.matchMedia = () => ({ matches: false, addListener() {}, addEventListener() {} }); w.scrollTo = () => {}; w.HTMLCanvasElement.prototype.getContext = () => null; w.Element.prototype.scrollIntoView = () => {}; } });
const w = dom.window, d = () => w.document;
w.addEventListener("error", (e) => errs.push("window.onerror: " + e.message));
const $ = (s) => d().querySelector(s), $$ = (s) => [...d().querySelectorAll(s)];
const M = () => w.__mockState;
const bar = () => $("#sleepbar");
const shown = () => !!bar() && !bar().hidden;
const text = () => (bar() ? bar().textContent : "");
const left = () => { const m = text().match(/Going to sleep in (\d+):(\d\d)\./); return m ? Number(m[1]) * 60 + Number(m[2]) : null; };
const until = async (fn, ms) => { const t = Date.now(); while (Date.now() - t < ms) { if (fn()) return true; await sleep(150); } return !!fn(); };

await sleep(1500);
check("the bar sits in the content area, above the page", !!bar() && bar().parentElement === $("main.content") && bar().nextElementSibling === $("#view"));
check("nothing counts on a healthy battery: no bar", !shown());

/* the report: 11.6 V, a default device, the floor's 2 min under way */
M().batteryV = 11.6; M().sleepPending = { cause: "critical", in_s: 100 };
check("under the floor the bar appears at the next read of the device", await until(shown, 4500));
check("it counts down in minutes and seconds", left() !== null && left() <= 100 && left() >= 90, text().slice(0, 40));
check("it names the reading, the floor and its 5 minutes", /The battery reads 11\.6 V, under the 11\.9 V critical floor: WiCAN sleeps after 5 minutes there, whatever the sleep delay and the sleep switch say\./.test(text()), text());
check("it says when the device wakes", /It wakes above 13\.2 V\./.test(text()));
check("the floor's bar is the warning kind and links to Power Saving", bar().classList.contains("crit") && (bar().querySelector("a") || {}).hash === "#/power");
const a0 = left(); await sleep(2300); const a1 = left();
check("it ticks by itself between reads", a0 !== null && a1 !== null && a0 - a1 >= 2 && a0 - a1 <= 4, { a0, a1 });

/* Quick Setup hides the sidebar, not the bar */
w.location.hash = "#/setup"; await sleep(900);
check("the bar stays on in Quick Setup's focused layout", $("#app").classList.contains("setup-focus") && shown() && left() !== null, text().slice(0, 30));
w.location.hash = "#/status"; await sleep(600);

/* the everyday case: engine off, under the sleep voltage, the 5 min delay */
M().batteryV = 12.4; M().sleepPending = { cause: "delay", in_s: 290 };
check("under the sleep voltage the bar counts the sleep delay", await until(() => /below the sleep voltage/.test(text()), 4500) && left() !== null && left() <= 290 && left() >= 280, text().slice(0, 40));
check("it names the reading and the sleep voltage", /The battery reads 12\.4 V, below the sleep voltage \(13\.1 V\)\. It wakes above 13\.2 V\./.test(text()), text());
check("the delay's bar is the plain kind", !bar().classList.contains("crit"));
M().batteryV = 13.08;
check("a reading that rounds to the threshold shows two decimals", await until(() => /reads 13\.08 V, below the sleep voltage \(13\.1 V\)/.test(text()), 4500), text());

/* the battery recovers: nothing left to count */
M().batteryV = 14.1; M().sleepPending = null;
check("the bar clears when the battery recovers", await until(() => !shown(), 4500));

/* the countdown runs out: the device stops answering, the pill says why */
M().batteryV = 11.6; M().sleepPending = { cause: "critical", in_s: 5 };
check("a short countdown shows", await until(shown, 4500), text().slice(0, 30));
const off = () => { const o = $("#offline"); return o && o.style.display === "flex" ? $("#offtx").textContent : ""; };
check("when the device drops off at the end, the pill says it went to sleep", await until(() => /went to sleep/.test(off()), 12000), off());
check("with the floor and the wake voltage", /WiCAN went to sleep \(battery under 11\.9 V\)\. It wakes above 13\.2 V\./.test(off()), off());
check("and the bar is gone while the device is away", !shown());

/* the engine starts: the device is back, nothing counts */
M().batteryV = 14.1; M().sleepPending = null; M().asleep = false;
check("back online: no pill, no bar", await until(() => off() === "" && !shown() && w.eval("conn").state === "online", 6000), { off: off(), shown: shown() });

/* a device that walked out of range mid-countdown is not "asleep" */
M().batteryV = 12.4; M().sleepPending = { cause: "delay", in_s: 290 };
await until(shown, 4500);
M().asleep = true;
check("an early drop-off keeps the plain reconnecting text", await until(() => off() !== "", 6000) && !/went to sleep/.test(off()), off());
M().asleep = false; M().batteryV = 14.1; M().sleepPending = null;
await until(() => off() === "" && !shown(), 6000);

/* the hold: the button, the press, the prompt, the cap */
M().batteryV = 12.4; M().sleepPending = { cause: "delay", in_s: 200 }; M().holds = 0; M().holdUntil = 0;
check("a countdown brings the Keep awake button", await until(() => shown() && bar().querySelector(".sb-hold") && !bar().querySelector(".sb-hold").hidden, 4500));
const holdBtn = () => bar().querySelector(".sb-hold");
check("the button offers ten minutes", holdBtn() && holdBtn().textContent === "Keep awake 10 min", holdBtn() && holdBtn().textContent);
holdBtn().click();
check("a press posts the hold and the bar counts the new time", await until(() => M().holds === 1 && left() !== null && left() >= 590 && left() <= 600, 4000), { holds: M().holds, left: left() });
check("the bar says it was kept awake and how many holds are left", /Kept awake on your request \(2 of 3 holds left\)\./.test(text()), text().slice(0, 120));
check("the button now offers ten more", holdBtn().textContent === "10 min more", holdBtn().textContent);
const modalOn = () => !!$("#modal-root") && $("#modal-root").classList.contains("on");
check("no dialog while minutes remain", !modalOn());
/* time passes: the device says 58 s are left */
M().sleepPending = { cause: "delay", in_s: 58 };
check("one minute before the entry the page asks", await until(modalOn, 5000));
const mt = () => ($("#modal-root h3") || {}).textContent || "";
check("the dialog counts the seconds and offers the choice", /WiCAN goes to sleep in 0:[45]\d/.test(mt()) && $$("#modal-root .acts button").map((b) => b.textContent).join("|") === "Let it sleep|Keep awake 10 minutes", { title: mt(), buttons: $$("#modal-root .acts button").map((b) => b.textContent) });
check("it names the reading and the holds left", /below the sleep voltage \(13\.1 V\)/.test($("#modal-root").textContent) && /2 of 3 holds left/.test($("#modal-root").textContent));
$$("#modal-root .acts button").find((b) => b.textContent === "Let it sleep").click();
await sleep(2500);
check("Let it sleep closes the dialog, posts nothing, and it does not come back for the same deadline", !modalOn() && M().holds === 1, { modal: modalOn(), holds: M().holds });
/* a new countdown (a hold) earns a new prompt; the second press from the dialog */
M().sleepPending = { cause: "delay", in_s: 400 };
await until(() => left() !== null && left() > 300, 4500);
M().sleepPending = { cause: "delay", in_s: 55 };
check("the next deadline is asked about again", await until(modalOn, 5000));
$$("#modal-root .acts button").find((b) => b.textContent === "Keep awake 10 minutes").click();
check("Keep awake from the dialog posts the hold and closes it", await until(() => M().holds === 2 && !modalOn(), 4000), { holds: M().holds, modal: modalOn() });
/* the third and last, then no button and no dialog */
await sleep(800);   /* the press before is still being answered */
holdBtn().click();
check("the third press is the last", await until(() => M().holds === 3, 4000), M().holds);
check("with the three used the button is gone and the bar says so", await until(() => holdBtn().hidden && /Kept awake on your request \(0 of 3 holds left\)\./.test(text()), 4500), text().slice(0, 140));
M().sleepPending = { cause: "delay", in_s: 50 };
await until(() => left() !== null && left() <= 50, 4500);
await sleep(1500);
check("and the page does not ask any more", !modalOn());
/* a hidden tab is not asked */
M().batteryV = 14.1; M().sleepPending = null; M().holds = 0; M().holdUntil = 0;
await until(() => !shown(), 4500);
Object.defineProperty(d(), "visibilityState", { value: "hidden", configurable: true });
M().batteryV = 12.4; M().sleepPending = { cause: "delay", in_s: 45 };
await until(shown, 4500); await sleep(1500);
check("a hidden tab gets the bar but not the dialog", shown() && !modalOn(), { shown: shown(), modal: modalOn() });
Object.defineProperty(d(), "visibilityState", { value: "visible", configurable: true });
check("made visible, it is asked", await until(modalOn, 3000));
$$("#modal-root .acts button").find((b) => b.textContent === "Let it sleep").click();
M().batteryV = 14.1; M().sleepPending = null;
await until(() => !shown(), 4500);

/* Power Saving names the floor as a rule of its own */
w.location.hash = "#/power"; await sleep(1500);
const view = () => ($("#view") || {}).textContent || "";
check("Power Saving names the floor with the sleep switch on", /Critical battery floor: under 11\.9 V for 5 minutes WiCAN goes to sleep without waiting for a longer sleep delay, and with sleep mode off too\./.test(view()));
const dRow = $$("#view .frow").find((r) => /^Sleep after \(min\)/.test((r.querySelector("label") || {}).textContent || ""));
check("the Sleep after help says the floor is the latest", !!dRow && /Under 11\.9 V it sleeps after 5 minutes at the latest, whatever this says\./.test(dRow.textContent), dRow && dRow.textContent.slice(0, 160));
check("Power Saving explains the hold", /Keep awake: while a countdown runs, the bar at the top offers ten more minutes/.test(view()) && /three times per start/.test(view()));
check("no page errors", errs.length === 0, errs.slice(0, 2));
console.log(fails ? fails + " check(s) FAILED" : "SLEEP SOON PROBE PASS");
process.exit(fails ? 1 : 0);
