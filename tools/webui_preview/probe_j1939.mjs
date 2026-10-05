/* Probe (2026-10-03, TASK_j1939_wwh.md phase 5): a J1939 vehicle (a truck)
   in the web UI. Quick Setup must say what the detection heard (a J1939
   vehicle, its controllers, no profile step) and, with the listener off,
   stage can_manager (listen-only) + j1939 for the one restart its Finish
   makes; the Trouble Codes page must show the four lamps, the source address
   and occurrence count of every DM1 code and refuse to clear on a listener;
   Automate must render a PGN row without RX ID or init, offer Add PGN and
   decode the test through the store. Runs over the mock API, once per
   preset.
   usage: node probe_j1939.mjs   (npm i jsdom first; node >= 20) */
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
  return { w, errs, $, $$, text, btn, fire, M: () => w.__mockState, S: () => w.__mockSettings };
}

const rows = (p) => p.$$("#view table tbody tr").map((tr) => [...tr.querySelectorAll("td")].map((td) => td.textContent.trim()));
const heads = (p) => p.$$("#view table thead th").map((th) => th.textContent.trim());

(async () => {
  /* ---- Trouble Codes: a J1939 truck's DM1 ---- */
  {
    const p = boot("#/dtc", { dtc: "j1939" });
    await sleep(1500);
    const t = p.text();
    check("dtc: the path is named", /J1939, heard \(DM1\)/.test(t));
    check("dtc: the four lamps are shown, two on", /MIL ON/.test(t) && /Warning ON/.test(t) && /Stop off/.test(t) && /Protect off/.test(t), t.slice(0, 0));
    check("dtc: active codes counted", /3 active code\(s\)/.test(t));
    const hd = heads(p);
    check("dtc: ECU and Count columns", hd.includes("ECU") && hd.includes("Count"), hd);
    const r = rows(p);
    check("dtc: three DM1 rows, active, with the source address", r.length === 3 && r.every((x) => x[1] === "active") && r.some((x) => x[0] === "SPN110-0" && x.includes("SA 0") && x.includes("5")) && r.some((x) => x[0] === "SPN520192-31" && x.includes("SA 11")), r);
    check("dtc: SPN-FMI explained", /SPN-FMI/.test(t) && /heard from each controller/.test(t));
    check("dtc: the lamp sentence names the source", /The lamp is requested by SA 0\./.test(t));
    check("dtc: no clear on a listener", /WiCAN only listens on this vehicle/.test(t) && !p.btn(/^Clear$/), t.slice(0, 0));
    check("dtc: no word of mode 04", !/[Mm]ode 04/.test(t));
    check("dtc: no long dash in the page", !/\u2014/.test(t.replace(/Last scan:.*$/, "")));
    check("dtc: no script error", p.errs.length === 0, p.errs.slice(0, 2));
  }

  /* ---- Trouble Codes, J1939 active mode (phase 6): DM2 asked, the clear offered ---- */
  {
    const p = boot("#/dtc", { dtc: "j1939", j1939Active: true });
    await sleep(1500);
    const t = p.text();
    check("active dtc: the path says DM2 is asked", /J1939 \(DM1 heard, DM2 asked\)/.test(t));
    const r = rows(p);
    check("active dtc: a previously active code from DM2, with its source and count", r.length === 4 && r.some((x) => x[0] === "SPN100-1" && x[1] === "previously active" && x.includes("SA 0") && x.includes("2")), r);
    check("active dtc: the sentence says DM2 is asked at every scan", /previously active ones are asked for \(DM2\) at every scan/.test(t) && !/asks nothing/.test(t));
    check("active dtc: the clear is offered as DM11 and DM3", /DM11 clears the active codes of every controller/.test(t) && /DM3 the previously active ones/.test(t) && !!p.btn(/^Clear$/), t.slice(0, 0));
    check("active dtc: no word of listening only", !/WiCAN only listens on this vehicle/.test(t));
    check("active dtc: no long dash in the page", !/\u2014/.test(t.replace(/Last scan:.*$/, "")));
    check("active dtc: no script error", p.errs.length === 0, p.errs.slice(0, 2));
  }

  /* ---- Settings > CAN: the J1939 card (phase 6) ---- */
  for (const [preset, want] of [[{}, /^Off\.$/], [{ j1939Listening: true }, /Listening to a J1939 network, never transmits\./], [{ j1939Active: true }, /Active: address 249 is WiCAN's; 4 requests sent\./]]) {
    const p = boot("#/settings/can", preset);
    await sleep(2200);
    const t = p.text();
    const tag = JSON.stringify(preset);
    check("settings " + tag + ": the J1939 card", /J1939 \(trucks, buses, machines\)/.test(t) && /Mode/.test(t), t.slice(0, 0));
    const sel = p.$$("#view select").find((s) => [...s.options].some((o) => /never transmits/.test(o.textContent)));
    check("settings " + tag + ": the mode choices are spelled out", !!sel && [...sel.options].map((o) => o.textContent).join("|") === "Listen only (never transmits)|Active (claims an address, can ask and clear)", sel ? [...sel.options].map((o) => o.textContent) : null);
    const live = p.$$("#view .help").map((e) => e.textContent.trim()).find((x) => want.test(x));
    check("settings " + tag + ": the live line on the claim", !!live, p.$$("#view .help").map((e) => e.textContent.trim()).filter((x) => /Active|Listening|Off\./.test(x)).slice(0, 4));
    check("settings " + tag + ": the card explains active mode", /Active mode lets WiCAN ask/.test(t));
    check("settings " + tag + ": no script error", p.errs.length === 0, p.errs.slice(0, 2));
  }

  /* ---- Trouble Codes, an OBD-II car: nothing of J1939 shows ---- */
  {
    const p = boot("#/dtc", { dtc: "obd" });
    await sleep(1500);
    const t = p.text();
    check("obd: no lamp chips, no J1939 words", !/Warning off/.test(t) && !/J1939/.test(t) && /Mode 04 clears ALL codes/.test(t));
    check("obd: no Count column", !heads(p).includes("Count"), heads(p));
    check("obd: no script error", p.errs.length === 0, p.errs.slice(0, 2));
  }

  /* ---- Quick Setup: a J1939-only truck, listener off (the first run) ---- */
  {
    const p = boot("#/setup/vehicle", { detect: "j1939" });
    await sleep(1500);
    const tick = (id) => { const el = p.$(id); if (el) { el.checked = true; p.fire(el, "change"); } };
    tick("#qs-plugged"); tick("#qs-ignition");
    const det = p.btn(/Detect my vehicle/);
    check("setup: Detect my vehicle is offered", !!det && !det.disabled);
    if (det) {
      det.click();
      await sleep(2300);
      check("setup: the network phase is shown while detecting", /Listening to the vehicle network/.test(p.text()));
      await sleep(3600);
      const t = p.text();
      check("setup: a J1939 vehicle", /J1939 vehicle\./.test(t) && /WiCAN listens and never asks/.test(t), t.slice(0, 0));
      check("setup: the network row", !!p.$("#qs-veh-dialect") && /SAE J1939, 250 kbit\/s/.test(p.$("#qs-veh-dialect").textContent), (p.$("#qs-veh-dialect") || {}).textContent);
      check("setup: no VIN, identified by its controllers", /identified by its controllers \(9be17165\)/.test(t));
      check("setup: standard parameters heard", /7 heard/.test(t));
      check("setup: the restart for the listener is announced", /One more restart for the J1939 listener/.test(t));
      check("setup: no profile step for a J1939 vehicle", /Profiles carry OBD requests/.test(t) && !p.btn(/^Test profile$/));
      check("setup: the store line says J1939", /no VIN, controller set 9be17165 · SAE J1939/.test(t));
      const cont = p.btn(/^Continue$/);
      check("setup: Continue is enabled without a profile choice", !!cont && !cont.disabled);
      if (cont) {
        cont.click(); await sleep(1200);
        const keep = p.btn(/Keep the defaults|Keep/);
        if (keep) { keep.click(); await sleep(1200); }
        const fin = p.btn(/Finish and restart/);
        check("setup: the Reading the car step offers Finish", !!fin);
        if (fin) {
          fin.click(); await sleep(1500);
          const S = p.S() || {};
          const cm = (S.can_manager || {}).values || {};
          const jl = (S.j1939 || {}).values || {};
          check("setup: Finish staged the native bus listen-only at the measured bitrate", cm.enabled === true && cm.silent === true && cm.baud === "250", cm);
          check("setup: Finish staged the J1939 listener", jl.enabled === true, jl);
          check("setup: Done names the vehicle network", /SAE J1939, listening/.test(p.text()) || /J1939/.test(p.text()));
        }
      }
    }
    check("setup: no script error", p.errs.length === 0, p.errs.slice(0, 2));
  }

  /* ---- Quick Setup: the listener already up (the second run): VIN by BAM, no restart notice ---- */
  {
    const p = boot("#/setup/vehicle", { detect: "j1939", j1939Listening: true });
    await sleep(1500);
    const tick = (id) => { const el = p.$(id); if (el) { el.checked = true; p.fire(el, "change"); } };
    tick("#qs-plugged"); tick("#qs-ignition");
    const det = p.btn(/Detect my vehicle/);
    if (det) { det.click(); await sleep(5900); }
    const t = p.text();
    check("setup2: the VIN the truck broadcast", /1WCANJ1939TRUCK01/.test(t) && /Broadcast by the vehicle/.test(t));
    check("setup2: no restart notice", !/One more restart for the J1939 listener/.test(t));
    check("setup2: no script error", p.errs.length === 0, p.errs.slice(0, 2));
  }

  /* ---- Automate: a PGN row, Add PGN, the listener line, the test ---- */
  {
    const cfg = { groups: [{ name: "default", enabled_default: true, period_ms: 1000 }],
      pids: [{ name: "EngineSpeed", type: "std", cmd: "PGN:F004", group: "default", period_ms: 0, parameters: [{ name: "EngineSpeed", expression: "(B3+B4*256)*0.125", unit: "rpm" }] },
             { name: "Coolant", type: "custom", cmd: "PGN:FEEE@0", group: "default", period_ms: 1000, parameters: [{ name: "EngineCoolantTemperature", expression: "B0-40", unit: "degC" }] },
             { name: "Hours", type: "custom", cmd: "PGN:FEE5?", group: "default", period_ms: 2000, parameters: [{ name: "EngineTotalHours", expression: "(B0+B1*256+B2*65536+B3*16777216)*0.05", unit: "h" }] }],
      filters: [] };
    const p = boot("#/automate/parameters", { detect: "j1939", j1939Listening: true, autopidCfg: cfg });
    await sleep(1800);
    let t = p.text();
    if (!/EngineSpeed/.test(t)) { p.w.location.hash = "#/automate"; p.w.dispatchEvent(new p.w.Event("hashchange")); await sleep(1500); t = p.text(); }
    check("automate: the listener line over the standard table", /J1939 listener/.test(t) && /listening at 250 kbit\/s, bus j1939: 3489 messages, 19 groups from 2 sources, VIN 1WCANJ1939TRUCK01/.test(t), t.slice(0, 0));
    check("automate: the PGN row is tagged J1939 and has no RX ID input", /PGN:F004 · J1939/.test(t) && !p.$$("#view .pidrow input[placeholder='7E8']").length, t.slice(0, 0));
    const caret = p.$$("#view .pidrow .caret")[0];
    if (caret) { caret.click(); await sleep(400); }
    t = p.text();
    check("automate: the expanded row explains the group instead of an init field", /J1939 group F004, broadcast\. No init, no reply header/.test(t) && !p.$$("#view .pidrow input[placeholder='ATSP6;ATSH7E4;']").length, t.slice(0, 0));
    /* the custom tab: Add PGN */
    const custom = p.$$("#view .seg button").find((b) => /Custom PIDs/.test(b.textContent));
    if (custom) { custom.click(); await sleep(600); }
    const addPgn = p.btn(/^Add PGN$/);
    check("automate: Add PGN is offered on the custom tab", !!addPgn);
    if (addPgn) {
      addPgn.click(); await sleep(500);
      t = p.text();
      /* custom rows keep their request in an input: read the values */
      const cmds = p.$$("#view .pidrow input.mono").map((i) => i.value).filter((v) => /^PGN:/.test(v));
      check("automate: the new PGN row is in the table beside the pinned one", cmds.includes("PGN:F004") && cmds.includes("PGN:FEEE@0"), cmds);
      check("automate: a custom PGN row shows the J1939 tag where the RX ID would be", p.$$("#view .pidrow span.dim").some((s) => s.textContent.trim() === "J1939"));
      check("automate: the note explains the grammar and the ? mark", /PGN:<hex>, with @<source>/.test(t) && /little-endian/.test(t) && /in J1939 active mode \(Settings > CAN\) WiCAN asks for it/.test(t));
      /* the `?` row: expanded, it says the group is asked for */
      const hoursRow = p.$$("#view .pidrow").find((r) => [...r.querySelectorAll("input.mono")].some((i) => i.value === "PGN:FEE5?"));
      const hc = hoursRow && hoursRow.querySelector(".caret");
      if (hc) { hc.click(); await sleep(400); }
      check("automate: a ? row is explained as asked for", /J1939 group FEE5, asked for \(J1939 active mode\)/.test(p.text()), p.text().slice(0, 0));
    }
    check("automate: no script error", p.errs.length === 0, p.errs.slice(0, 2));
  }

  console.log(fails ? `PROBE FAILED (${fails})` : "J1939 PROBE PASS");
  process.exit(fails ? 1 : 0);
})();
