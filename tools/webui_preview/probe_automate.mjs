/* Interaction probe for the reworked Automate + Rules pages. */
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import { JSDOM, VirtualConsole } from "jsdom";

const here = dirname(fileURLToPath(import.meta.url));
const html = readFileSync(join(here, "preview.html"), "utf-8");
const errs = [];
const vc = new VirtualConsole();
vc.on("jsdomError", (e) => errs.push(e.message + " " + (e.detail && e.detail.stack || "").slice(0,300)));

const dom = new JSDOM(html, {
  runScripts: "dangerously", url: "http://wican.local/#/automate",
  pretendToBeVisual: true, virtualConsole: vc,
  beforeParse(w) {
    w.matchMedia = () => ({ matches: false, addListener() {}, addEventListener() {} });
    w.scrollTo = () => {}; w.HTMLCanvasElement.prototype.getContext = () => null;
  },
});
const w = dom.window, d = () => w.document;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const check = (n, ok) => { console.log((ok ? "PASS " : "FAIL ") + n); if (!ok) process.exitCode = 1; };

(async () => {
  await sleep(900);
  const cards = [...d().querySelectorAll(".section")];
  const titles = cards.map((c) => (c.querySelector("h3") || {}).textContent || "");
  /* tabbed page (2026-09-05/06): Settings -> Parameters -> Data destinations -> Home Assistant */
  const tabs = [...d().querySelectorAll(".subtabs .subtab")].map((a) => a.textContent.trim());
  check("tab order Settings->Parameters->Data destinations->Home Assistant (" + tabs.join(",") + ")",
    JSON.stringify(tabs) === JSON.stringify(["Settings", "Parameters", "Data destinations", "Home Assistant"]));
  check("first card is Automate settings", /Automate settings/.test(titles[0] || ""));
  /* advanced fields must appear BELOW the essentials, not interleaved */
  const haCard = cards.find((c) => /Home Assistant/.test((c.querySelector("h3") || {}).textContent));
  const haKeys = [...haCard.querySelectorAll(".frow[data-key]")].map((r) => r.dataset.key);
  check("advanced rows sorted below essentials (HA card)",
    haKeys.indexOf("url") > haKeys.indexOf("data_mode") && haKeys.indexOf("manual_override") > haKeys.indexOf("interval_s"));
  check("no stray null in webhook stats", !/\bnull\b/.test(haCard.textContent));
  const pollingRows = [...cards[0].querySelectorAll(".frow[data-key]")];
  const visible = pollingRows.filter((r) => r.style.display !== "none").map((r) => r.dataset.key);
  check("polling essentials only (" + visible.join(",") + ")",
    visible.length <= 6 && visible.includes("enabled") && !visible.includes("std_init"));
  const allPollingKeys = pollingRows.map((r) => r.dataset.key);
  check("PID-group / vehicle keys are NOT on the Polling card",
    !["std_enabled", "std_init", "std_protocol", "custom_init", "specific_init", "vehicle"].some((k) => allPollingKeys.includes(k)));
  check("pause threshold is the 3-way choice (sleep voltage / custom / never)",
    [...cards[0].querySelectorAll("select option")].some((o) => /sleep voltage/.test(o.textContent)) &&
    !!cards[0].querySelector('input[type="range"][min="12"][max="14.5"][step="0.1"]'));
  const stdProto = d().querySelector('.frow[data-key="std_protocol"]');
  check("std protocol/init rows head the Standard PIDs pane", !!stdProto && !cards[0].contains(stdProto) &&
    !!d().querySelector('.frow[data-key="std_init"]') && !!d().querySelector('.frow[data-key="std_enabled"]'));
  check("no per-card 'Submit to apply' badges", ![...d().querySelectorAll(".chip")].some((c) => /Submit to apply/.test(c.textContent)));
  check("live badge present", d().body.textContent.includes("Applies live"));
  /* parameters are collapsible: hidden by default, caret expands */
  const expandAll = async () => {
    for (const b of [...d().querySelectorAll("button")].filter((x) => x.textContent.trim().startsWith("▸"))) b.click();
    await sleep(120);
  };
  check("parameters collapsed by default", !d().querySelector('input[placeholder="(B2*256+B3)/4"]') &&
    [...d().querySelectorAll("button")].some((b) => b.textContent.trim().startsWith("▸")));
  await expandAll();
  check("expression column present after expand", !!d().querySelector('input[placeholder="(B2*256+B3)/4"]'));
  check("unit column present after expand", !!d().querySelector('input[placeholder="rpm"]'));
  check("destinations cycle in seconds", d().body.textContent.includes("Every (s)"));
  check("destinations enable switch + type select", (() => {
    const dest = cards.find((c) => /destinations/i.test((c.querySelector("h3") || {}).textContent));
    return dest && dest.querySelectorAll(".switch").length >= 1 && dest.querySelectorAll("select").length >= 1;
  })());
  /* scan flow: click Scan PIDs -> wait past mock 2.5 s -> results modal */
  const scanBtn = [...d().querySelectorAll("button")].find((b) => b.textContent.trim() === "Scan PIDs");
  check("scan button exists", !!scanBtn);
  scanBtn.click();
  await sleep(4500);
  const modalTxt = (d().querySelector("#modal-root") || {}).textContent || "";
  check("scan results modal with PIDs", /supported PIDs reported/.test(modalTxt) && /Engine RPM/.test(modalTxt));
  const addSel = [...d().querySelectorAll("#modal-root button")].find((b) => b.textContent === "Add selected");
  check("add-selected action", !!addSel);
  if (addSel) addSel.click();
  await sleep(200);
  const stdSpans = () => [...d().querySelectorAll("span")].map((s) => s.textContent);
  check("rows merged into table (dedup respected)",
    stdSpans().includes("Intake MAP") && stdSpans().filter((t) => t === "010C1").length === 1);
  check("std names/commands are read-only text",
    ![...d().querySelectorAll("input")].some((i) => i.value === "Intake MAP" || i.value === "010C1"));
  /* dedup regression: re-open Last Scan Results and re-add — no dup rows */
  const lastBtn = [...d().querySelectorAll("button")].find((b) => b.textContent === "Last Scan Results");
  if (lastBtn) {
    lastBtn.click(); await sleep(300);
    const boxes = [...d().querySelectorAll('#modal-root input[type="checkbox"]')];
    boxes.forEach((b) => { b.checked = true; });
    const addAgain = [...d().querySelectorAll("#modal-root button")].find((b) => b.textContent === "Add selected");
    if (addAgain) addAgain.click();
    await sleep(200);
  }
  check("re-adding scan results does not duplicate", stdSpans().filter((t) => t === "010C1").length === 1);
  /* vehicle-specific tab: dropdown -> load profile -> B-shift check */
  const segBtns = [...d().querySelectorAll(".seg button")];
  check("four parameter tabs", segBtns.length >= 4 && segBtns.some((b) => b.textContent === "Custom PIDs"));
  check("tab order Standard->Vehicle->Custom->Raw",
    JSON.stringify(segBtns.slice(0, 4).map((b) => b.textContent)) ===
    JSON.stringify(["Standard PIDs", "Vehicle Specific", "Custom PIDs", "Raw Config"]));
  const vehBtn = segBtns.find((b) => b.textContent === "Vehicle Specific");
  vehBtn.click();
  await sleep(400);
  const carSel = [...d().querySelectorAll("select")].find((s) => [...s.options].some((o) => /Ioniq2017/.test(o.textContent)));
  check("car dropdown populated from vehicle_profiles.json", !!carSel);
  if (carSel) {
    const btns = () => [...d().querySelectorAll("button")].map((b) => b.textContent.trim());
    check("Fetch latest button, no Load Profile button",
      btns().some((t) => t.includes("Fetch latest")) && !btns().includes("Load Profile"));
    carSel.value = "Hyundai: Ioniq2017";
    carSel.dispatchEvent(new w.Event("change"));
    await sleep(500);   /* selecting IS the load: config PUT + staging */
    check("vehicle name + init rows head the Vehicle Specific pane",
      !!d().querySelector('.frow[data-key="vehicle"]') && !!d().querySelector('.frow[data-key="specific_init"]'));
    check("no RX Hdr column on vehicle tab", !d().querySelector('input[placeholder="7E8"]'));
    check("drag-drop profile zone on vehicle tab", !!d().querySelector("#view .drop") &&
      [...d().querySelectorAll("label")].some((l) => l.textContent.includes("Choose File")));
    check("selecting a profile stages the car name + init into the group rows",
      (d().querySelector('.frow[data-key="vehicle"] input') || {}).value === "Hyundai: Ioniq2017" &&
      ((d().querySelector('.frow[data-key="specific_init"] input') || {}).value || "").length > 0);
    await expandAll();
    const exprs = [...d().querySelectorAll("input")].map((i) => i.value);
    check("profile PIDs imported with B-index shift (B39->B38)", exprs.some((v) => v === "B38/2"));
    const vals = [...d().querySelectorAll("input")].map((i) => i.value);
    check("multi-parameter PID grouped: ONE 2101 row, 4 params under it",
      vals.filter((v) => v === "2101").length === 2 && /* cmd + pid-name inputs */
      ["SOC_BMS","Charger_Connected","Charging","HV_Charger_Connected"].every((n) => vals.includes(n)));
    /* PID init editing + one-shot Test button + Add PID on vehicle tab */
    check("PID init editable on PID row", [...d().querySelectorAll("input")].some((i) => (i.placeholder || "") === "ATSP6;ATSH7E4;"));
    const testBtn = [...d().querySelectorAll("button")].find((b) => b.textContent === "Test");
    check("Test button present", !!testBtn);
    if (testBtn) {
      testBtn.click(); await sleep(500);
      const tm = (d().querySelector("#modal-root") || {}).textContent || "";
      check("test modal shows raw reply + value", /Raw reply/.test(tm) && /7EC 10 27/.test(tm));
      const closeBtn = [...d().querySelectorAll("#modal-root button")].find((b) => b.textContent === "Close");
      if (closeBtn) { closeBtn.click(); await sleep(150); }
    }
    const addPid = [...d().querySelectorAll("button")].find((b) => b.textContent.includes("Add PID"));
    check("Add PID on vehicle tab", !!addPid);
    if (addPid) {
      addPid.click(); await sleep(150);
      check("new specific PID row appended", [...d().querySelectorAll("input")].some((i) => i.value === "NewPID"));
    }
    /* std/custom sets survive: check the Custom tab still has OilTemp */
    segBtns.find((b) => b.textContent === "Custom PIDs").click();
    await sleep(250);
    const cust = [...d().querySelectorAll("input")].map((i) => i.value);
    check("custom PIDs survived the import", cust.includes("OilTemp"));
    check("RX Hdr column present on custom tab", !!d().querySelector('input[placeholder="7E8"]'));
  }
  if (errs.length) { console.log('errors so far:'); errs.forEach(e=>console.log('  '+e)); }
  /* rules page — the veh flow staged settings, so the unsaved-changes
     guard modal appears on navigation: choose Discard (authentic UX) */
  w.location.hash = "#/events";
  w.dispatchEvent(new w.Event("hashchange"));
  await sleep(400);
  const guard = [...d().querySelectorAll("#modal-root button")].find((b) => b.textContent === "Discard");
  check("unsaved-changes guard appeared on navigation", !!guard);
  if (guard) { guard.click(); await sleep(150);
    w.location.hash = "#/events"; w.dispatchEvent(new w.Event("hashchange")); }
  await sleep(800);
  const evTxt = d().body.textContent;
  check("rules table lists rules", evTxt.includes("bump_event") && evTxt.includes("dest1"));
  check("auto tag on dest rules", evTxt.includes("·auto"));
  const addRule = [...d().querySelectorAll("button")].find((b) => b.textContent.includes("Add Rule"));
  check("Add Rule button", !!addRule);
  if (addRule) { addRule.click(); await sleep(150); }
  const m2 = (d().querySelector("#modal-root") || {}).textContent || "";
  check("rule editor modal (When/Do/With)", /When/.test(m2) && /Do/.test(m2) && /With/.test(m2));
  if (errs.length) { console.log("JS errors:"); errs.forEach((e) => console.log("  " + e)); process.exitCode = 1; }
  console.log(process.exitCode ? "PROBE FAIL" : "PROBE PASS");
  process.exit(process.exitCode || 0);
})();
