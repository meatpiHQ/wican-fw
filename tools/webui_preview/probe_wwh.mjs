/* Probe (2026-10-03, TASK_j1939_wwh.md phase 3): a vehicle that speaks OBD
   over UDS (ISO 27145 / SAE J1979-2). The Trouble Codes page must say who
   reported a code, name the lamp's askers and word the clear for the path
   in use (mode 04 on an OBD-II car, service 14 on the emissions group
   here); a scan row must keep the ECU it is addressed to when it is added
   to the table; Quick Setup must name the dialect of the detected car.
   Runs over the mock API, once per preset.
   usage: node probe_wwh.mjs   (npm i jsdom first; node >= 20) */
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import { JSDOM, VirtualConsole } from "jsdom";

const here = dirname(fileURLToPath(import.meta.url));
const html = readFileSync(join(here, "preview.html"), "utf-8");
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
let fails = 0;
const check = (n, ok, extra) => { console.log((ok ? "PASS " : "FAIL ") + n + (extra !== undefined ? "  [" + JSON.stringify(extra) + "]" : "")); if (!ok) fails++; };

function boot(hash, preset) {
  const errs = [];
  const vc = new VirtualConsole();
  vc.on("jsdomError", (e) => errs.push(e.message + " " + ((e.detail && e.detail.stack) || "").slice(0, 300)));
  const dom = new JSDOM(html, {
    runScripts: "dangerously", url: "http://wican.local/" + hash,
    pretendToBeVisual: true, virtualConsole: vc,
    beforeParse(w) {
      w.__mockPreset = preset;
      w.matchMedia = () => ({ matches: false, addListener() {}, addEventListener() {} });
      w.scrollTo = () => {}; w.HTMLCanvasElement.prototype.getContext = () => null;
      if (!w.TextEncoder) { w.TextEncoder = TextEncoder; w.TextDecoder = TextDecoder; }
      w.Element.prototype.scrollIntoView = () => {};
    },
  });
  const w = dom.window;
  w.addEventListener("error", (e) => errs.push("window.onerror: " + e.message));
  w.addEventListener("unhandledrejection", (e) => errs.push("unhandledrejection: " + ((e.reason && e.reason.message) || e.reason)));
  const $ = (sel) => w.document.querySelector(sel), $$ = (sel) => [...w.document.querySelectorAll(sel)];
  const text = () => (($("#view") || {}).textContent || "").replace(/\s+/g, " ");
  const btn = (re, root) => $$((root || "#view") + " button").find((b) => re.test(b.textContent.trim()));
  const fire = (el, type) => el.dispatchEvent(new w.Event(type, { bubbles: true }));
  return { w, errs, $, $$, text, btn, fire, M: () => w.__mockState };
}

/* the cells of the trouble-code table, row by row */
const rows = (p) => p.$$("#view table tbody tr").map((tr) => [...tr.querySelectorAll("td")].map((td) => td.textContent.trim()));
const heads = (p) => p.$$("#view table thead th").map((th) => th.textContent.trim());

(async () => {
  /* ---- Trouble Codes, an ISO 27145 vehicle with two ECUs ---- */
  {
    const p = boot("#/dtc", { dtc: "wwh" });
    await sleep(1500);
    const t = p.text();
    check("wwh: the page names the path", /OBD on UDS \(ISO 27145\)/.test(t));
    check("wwh: the table has an ECU column", heads(p).includes("ECU"), heads(p));
    const r = rows(p);
    check("wwh: one row per ECU's word (4 items)", r.length === 4, r.length);
    check("wwh: the SCR unit's code is its own row", r.some((x) => x[0] === "P20EE" && x.includes("18DAF13D")), r);
    check("wwh: a code with a failure type reads whole", r.some((x) => x[0] === "P2463-1F" && x[1] === "pending"), r);
    check("wwh: the permanent code is listed as such", r.some((x) => x[0] === "P0420" && x[1] === "permanent"));
    check("wwh: who asks for the lamp", /The lamp is requested by 18DAF100, 18DAF13D\./.test(t), t.slice(0, 0));
    check("wwh: the clear is worded for the emissions group", /This clears ALL emission codes of every ECU/.test(t) && /group FFFF33/.test(t));
    check("wwh: no word of mode 04", !/[Mm]ode 04/.test(t));
    check("wwh: no long dash in the page", !/—/.test(t.replace(/Last scan:.*$/, "")));

    /* the clear: the firmware's confirm, then what is left */
    const clear = p.btn(/^Clear$/);
    check("wwh: Clear is offered (clearing allowed)", !!clear && !clear.disabled);
    clear.click();
    await sleep(300);
    const modal = (p.$("#modal-root") || {}).textContent || "";
    check("wwh: the confirm asks about every ECU", /Clear the emission codes of every ECU\?/.test(modal), modal.slice(0, 120));
    const yes = [...p.w.document.querySelectorAll("#modal-root .acts button")].find((b) => !/cancel/i.test(b.textContent));
    yes.click();
    await sleep(900);
    const sent = p.M().lastDtcClear;
    check("wwh: the clear went out with confirm:true", !!sent && sent.confirm === true, sent);
    const r2 = rows(p);
    check("wwh: after the clear only the permanent code is left", r2.length === 1 && r2[0][0] === "P0420" && r2[0][1] === "permanent", r2);
    check("wwh: the lamp is off", /MIL off/.test(p.text()));
    check("wwh: no script error", p.errs.length === 0, p.errs.slice(0, 2));
  }

  /* ---- Trouble Codes, an OBD-II car: the old wording, the ECU still shown ---- */
  {
    const p = boot("#/dtc", { dtc: "obd" });
    await sleep(1500);
    const t = p.text();
    check("obd: mode 04 wording kept", /Mode 04 clears ALL codes/.test(t));
    check("obd: no path chip", !/OBD on UDS/.test(t));
    const r = rows(p);
    check("obd: three codes, each with its ECU", r.length === 3 && r.every((x) => x.includes("7E8")), r);
    check("obd: one ECU, no lamp sentence", !/The lamp is requested by/.test(t));
    check("obd: the description of a code is shown", /Catalyst System Efficiency/.test(t));
    check("obd: no script error", p.errs.length === 0, p.errs.slice(0, 2));
  }

  /* ---- Trouble Codes on a device with DTC off (no scan yet) ---- */
  {
    const p = boot("#/dtc", {});
    await sleep(1500);
    const t = p.text();
    check("off: says DTC is disabled", /DTC scanning is disabled/.test(t));
    check("off: no table, no error", rows(p).length === 0 && p.errs.length === 0, p.errs.slice(0, 2));
  }

  /* ---- Automate: a scan row keeps the ECU it is addressed to ---- */
  {
    const p = boot("#/automate/parameters", { detect: "wwh" });
    await sleep(1500);
    let scan = p.btn(/^Scan PIDs$/);
    if (!scan) { p.w.location.hash = "#/automate"; p.w.dispatchEvent(new p.w.Event("hashchange")); await sleep(1200); scan = p.btn(/^Scan PIDs$/); }
    check("automate: Scan PIDs is there", !!scan);
    if (scan) {
      scan.click();
      await sleep(4600);
      const modal = () => (p.$("#modal-root") || {}).textContent || "";
      check("automate: the results name each row's ECU", /EngineRPM · ECU 00/.test(modal()) && /CatTempBank1Sens1 · ECU 3D/.test(modal()), modal().slice(0, 200));
      p.$$("#modal-root input[type=checkbox]").forEach((cb) => { if (!cb.disabled) { cb.checked = true; p.fire(cb, "change"); } });
      const add = [...p.w.document.querySelectorAll("#modal-root button")].find((b) => b.textContent === "Add selected");
      add.click();
      await sleep(500);
      /* Apply sends the table: the rows must carry their init */
      const apply = p.btn(/^Apply Configuration$/);
      check("automate: Apply Configuration is offered", !!apply);
      if (apply) { apply.click(); await sleep(900); }
      const cfg = p.M().autopidCfg || {};
      const rpm = (cfg.pids || []).find((x) => x.cmd === "22F40C");
      const cat = (cfg.pids || []).find((x) => x.cmd === "22F43C");
      check("automate: the added row kept its ECU address", !!rpm && rpm.init === "ATSH18DA00F1" && !!cat && cat.init === "ATSH18DA3DF1", { rpm, cat });
      check("automate: the standard table shows the ECU beside the request", /22F40C · ECU 00/.test(p.text()), p.text().slice(0, 0));
    }
    check("automate: no script error", p.errs.length === 0, p.errs.slice(0, 2));
  }

  /* ---- Quick Setup: the detected car's dialect ---- */
  {
    const p = boot("#/setup/vehicle", { detect: "wwh" });
    await sleep(1500);
    const tick = (id) => { const el = p.$(id); if (el) { el.checked = true; p.fire(el, "change"); } };
    tick("#qs-plugged"); tick("#qs-ignition");
    const det = p.btn(/Detect my vehicle/);
    check("setup: Detect my vehicle is offered", !!det && !det.disabled);
    if (det) {
      det.click();
      await sleep(600);
      check("setup: the detection text speaks of both ways of asking", /0100/.test(p.text()) && /ISO 27145/.test(p.text()));
      await sleep(5200);
      const t = p.text();
      check("setup: the protocol and the dialect of the car", /CAN 29-bit 500 kbit\/s/.test(t) && !!p.$("#qs-veh-dialect") && /OBD on UDS \(ISO 27145\)/.test(p.$("#qs-veh-dialect").textContent), t.slice(0, 0));
      check("setup: the VIN the van gave", /1WCANWWH0TRUCK001/.test(t));
      check("setup: the store's line carries the dialect", /CAN 29-bit 500 kbit\/s, OBD on UDS \(ISO 27145\)/.test(t));
    }
    check("setup: no script error", p.errs.length === 0, p.errs.slice(0, 2));
  }

  /* ---- Quick Setup on an OBD-II car: nothing new to read ---- */
  {
    const p = boot("#/setup/vehicle", {});
    await sleep(1500);
    const tick = (id) => { const el = p.$(id); if (el) { el.checked = true; p.fire(el, "change"); } };
    tick("#qs-plugged"); tick("#qs-ignition");
    const det = p.btn(/Detect my vehicle/);
    if (det) { det.click(); await sleep(5800); }
    const t = p.text();
    check("setup obd2: the protocol without a dialect word", /CAN 11-bit 500 kbit\/s/.test(t) && !p.$("#qs-veh-dialect") && !/OBD on UDS/.test(t.replace(/Detecting.*$/, "")));
    check("setup obd2: no script error", p.errs.length === 0, p.errs.slice(0, 2));
  }

  console.log(fails ? `PROBE FAILED (${fails})` : "PROBE PASS");
  process.exit(fails ? 1 : 0);
})();
