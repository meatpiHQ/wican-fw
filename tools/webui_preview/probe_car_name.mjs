/* Probe (2026-11-10): the car's name in the header pill and on the browser
   tab, and the rename staged into Submit Changes. Boots over the mock
   (`__mockPreset.vehicles`):
     A  a named current car: the pill has two lines (the name above the id and
        the address), the tab reads "<name> · WiCAN Pro"; a click opens the
        popover (the name, the count, the VIN, the stored cars); an empty name
        cannot be set; Escape and a click beside it close it; Set name stages
        the rename (the header shows it in italics, Submit Changes (1), NO
        request yet); the stored name typed back unstages it; Submit sends
        ONE `PUT /api/autopid/vehicles/<key>` and ONE restart
     B  no current car: the pill as before, one line, the tab "WiCAN Pro"
     C  a current car whose setup never finished: the "not set up" chip
     D  a staged rename thrown away by Discard on the unsaved-changes dialog
   Prints `CAR NAME PROBE PASS`. Run after `python make_preview.py` (plain
   and `--min`). */
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import { JSDOM, VirtualConsole } from "jsdom";

const here = dirname(fileURLToPath(import.meta.url));
const html = readFileSync(join(here, "preview.html"), "utf-8");
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
let fails = 0;
const check = (n, ok, extra) => { console.log((ok ? "PASS " : "FAIL ") + n + (extra !== undefined ? "  [" + JSON.stringify(extra).slice(0, 300) + "]" : "")); if (!ok) { fails++; process.exitCode = 1; } };

const VIN = "WVGZZZ5NZJW123456";
const car = (over) => ({ key: VIN, vin: VIN, fingerprint: "be7fb813", name: "Tiguan", protocol: 6, profile: "", std_supported: 42, pending_profile: false, ...over });
const other = { key: "KMHKN81AFNU000001", vin: "KMHKN81AFNU000001", fingerprint: "11aa22bb", name: "Ioniq 5", protocol: 6, profile: "", std_supported: 30, pending_profile: false };

function boot(preset, url = "http://wican.local/#/status") {
  const errs = [];
  const vc = new VirtualConsole();
  vc.on("jsdomError", (e) => errs.push(e.message + " " + (e.detail && e.detail.stack || "").slice(0, 300)));
  const dom = new JSDOM(html, {
    runScripts: "dangerously", url, pretendToBeVisual: true, virtualConsole: vc,
    beforeParse(w) {
      w.__mockPreset = { apDefaultPassword: false, ...preset };
      w.matchMedia = () => ({ matches: false, addListener() {}, addEventListener() {} });
      w.scrollTo = () => {}; w.HTMLCanvasElement.prototype.getContext = () => null;
      if (!w.TextEncoder) { w.TextEncoder = TextEncoder; w.TextDecoder = TextDecoder; }
      w.Element.prototype.scrollIntoView = () => {};
    },
  });
  const w = dom.window, d = () => w.document;
  w.addEventListener("error", (e) => errs.push("window.onerror: " + e.message));
  w.addEventListener("unhandledrejection", (e) => errs.push("unhandledrejection: " + (e.reason && e.reason.message || e.reason)));
  const $ = (sel) => d().querySelector(sel);
  const fire = (el, type) => el.dispatchEvent(new w.Event(type, { bubbles: true }));
  const type = (el, v) => { el.value = v; fire(el, "input"); };
  const key = (el, k) => el.dispatchEvent(new w.KeyboardEvent("keydown", { key: k, bubbles: true, cancelable: true }));
  const modalBtn = (re) => [...d().querySelectorAll("#modal-root .acts button")].find((b) => re.test(b.textContent));
  const popBtn = (re) => [...d().querySelectorAll("#cp-name-pop button")].find((b) => re.test(b.textContent));
  const waitFor = async (fn, ms) => { const t0 = Date.now(); while (Date.now() - t0 < ms) { if (fn()) return true; await sleep(100); } return fn(); };
  return { w, d, $, fire, type, key, errs, modalBtn, popBtn, waitFor, M: () => w.__mockState,
    name: () => $("#cp-name"), nameText: () => $("#cp-name-t").textContent, pop: () => $("#cp-name-pop"), input: () => $("#cp-name-input"),
    submit: () => $("#submitlabel").textContent, puts: () => (w.__mockState.vehiclePuts || []) };
}

(async () => {
  /* ---- A: a named car ---- */
  {
    const p = boot({ vehicles: { current: VIN, vehicles: [car(), other] } });
    await p.waitFor(() => !p.name().hidden, 4000);
    check("A: the name is in the pill", !p.name().hidden && p.nameText() === "Tiguan", p.nameText());
    check("A: the pill has its two-line form", p.$("#connpill").classList.contains("named"));
    check("A: the id and the address stay in the pill", /^WiCAN_/.test(p.$("#cp-id").textContent) && p.$("#cp-ip").textContent.length > 3 && p.$("#cp-id").closest(".cline") !== null);
    check("A: the name sits above the id line", p.name().nextElementSibling === p.$("#cp-id").parentElement);
    check("A: the tab title follows the name", p.d().title === "Tiguan · WiCAN Pro", p.d().title);
    check("A: no 'not set up' chip on a finished car", p.$("#cp-name-new").hidden);
    check("A: nothing pending", p.submit() === "Saved", p.submit());

    p.name().click(); await sleep(50);
    check("A: a click opens the popover", !!p.pop() && p.name().getAttribute("aria-expanded") === "true");
    check("A: the field holds the name, 31 characters at most", p.input().value === "Tiguan" && p.input().getAttribute("maxlength") === "31");
    check("A: the count", /6 \/ 31/.test(p.pop().textContent));
    check("A: the VIN and the stored cars", p.pop().textContent.includes(VIN) && /Stored cars\s*2/.test(p.pop().textContent), p.pop().textContent.slice(-120));
    check("A: the popover says Submit Changes and a restart", /Submit Changes/.test(p.pop().textContent) && /restarts/.test(p.pop().textContent));
    const acts = [...p.pop().querySelectorAll(".acts button")].map((b) => b.textContent);
    check("A: Cancel, then Set name at the right edge", acts.join("|") === "Cancel|Set name", acts);
    p.type(p.input(), "   ");
    check("A: an empty name cannot be set", p.popBtn(/^Set name$/).disabled);
    p.key(p.input(), "Escape"); await sleep(30);
    check("A: Escape closes the popover, nothing staged", !p.pop() && p.submit() === "Saved" && p.name().getAttribute("aria-expanded") === "false");

    p.name().click(); await sleep(30);
    p.$("#view").dispatchEvent(new p.w.MouseEvent("mousedown", { bubbles: true })); await sleep(30);
    check("A: a click beside the popover closes it", !p.pop());

    p.name().click(); await sleep(30);
    p.type(p.input(), "Family car");
    check("A: the count follows the typing", /10 \/ 31/.test(p.pop().textContent));
    p.popBtn(/^Set name$/).click(); await sleep(50);
    check("A: Set name closes the popover", !p.pop());
    check("A: the header shows the new name as not saved yet", p.nameText() === "Family car" && p.name().classList.contains("staged") && /Submit Changes/.test(p.name().title), p.name().title);
    check("A: one pending change on the Submit button", p.submit() === "Submit Changes (1)" && !p.$("#submitbtn").disabled && /Car name/.test(p.$("#submitbtn").title), p.submit());
    check("A: NOTHING was sent to the device yet", p.puts().length === 0 && !(p.M().restarts > 0));
    check("A: the tab keeps the stored name until the submit", p.d().title === "Tiguan · WiCAN Pro", p.d().title);
    await sleep(3500);   /* a status poll must not paint the stored name back over the staged one */
    check("A: the staged name survives the status poll", p.nameText() === "Family car" && p.name().classList.contains("staged"));

    p.name().click(); await sleep(30);
    check("A: the popover reopens on the staged name", p.input().value === "Family car");
    p.type(p.input(), "Tiguan"); p.key(p.input(), "Enter"); await sleep(50);
    check("A: the stored name typed back unstages the rename", p.submit() === "Saved" && p.nameText() === "Tiguan" && !p.name().classList.contains("staged"), p.submit());

    p.name().click(); await sleep(30);
    p.type(p.input(), "  Family car  "); p.key(p.input(), "Enter"); await sleep(50);
    check("A: Enter sets the name, trimmed", p.nameText() === "Family car" && p.submit() === "Submit Changes (1)");
    p.$("#submitbtn").click(); await sleep(100);
    check("A: Submit asks first, and says it restarts", !!p.modalBtn(/^Continue$/) && /restarts WiCAN/.test(p.$("#modal-root").textContent));
    check("A: still nothing sent before Continue", p.puts().length === 0);
    p.modalBtn(/^Continue$/).click();
    const sent = await p.waitFor(() => (p.M().restarts || 0) > 0, 4000);
    check("A: Continue sends ONE rename", p.puts().length === 1 && p.puts()[0].key === VIN && JSON.stringify(p.puts()[0].body) === JSON.stringify({ name: "Family car" }), p.puts());
    check("A: and ONE restart", sent && p.M().restarts === 1, p.M().restarts);
    check("A: the page waits for the device", p.$("#connpill").classList.contains("reboot"));
    check("A: nothing pending after the submit", p.submit() === "Saved");
    check("A: no JS errors", p.errs.length === 0, p.errs);
  }
  /* ---- B: no current car ---- */
  {
    const p = boot({});
    await sleep(1800);
    check("B: no name button without a current car", p.name().hidden);
    check("B: the pill keeps its one-line form", !p.$("#connpill").classList.contains("named") && /^WiCAN_/.test(p.$("#cp-id").textContent));
    check("B: the tab title is plain", p.d().title === "WiCAN Pro", p.d().title);
    check("B: no JS errors", p.errs.length === 0, p.errs);
  }
  /* ---- C: a car whose setup never finished ---- */
  {
    const p = boot({ vehicles: { current: VIN, vehicles: [car({ name: "Volkswagen (3456)", pending_profile: true }), other] } });
    await p.waitFor(() => !p.name().hidden, 4000);
    check("C: the name with its 'not set up' chip", p.nameText() === "Volkswagen (3456)" && !p.$("#cp-name-new").hidden);
    check("C: no JS errors", p.errs.length === 0, p.errs);
  }
  /* ---- D: Discard on the unsaved-changes dialog drops the rename ---- */
  {
    const p = boot({ vehicles: { current: VIN, vehicles: [car()] } });
    await p.waitFor(() => !p.name().hidden, 4000);
    p.name().click(); await sleep(30);
    p.type(p.input(), "Thrown away"); p.popBtn(/^Set name$/).click(); await sleep(50);
    check("D: staged", p.submit() === "Submit Changes (1)");
    p.w.location.hash = "#/about";
    await p.waitFor(() => !!p.modalBtn(/^Discard$/), 3000);
    check("D: leaving the section asks", !!p.modalBtn(/^Discard$/));
    p.modalBtn(/^Discard$/).click(); await sleep(300);
    check("D: Discard drops the rename, the stored name is back", p.submit() === "Saved" && p.nameText() === "Tiguan" && !p.name().classList.contains("staged") && p.puts().length === 0, p.nameText());
    check("D: no JS errors", p.errs.length === 0, p.errs);
  }
  console.log(fails ? "CAR NAME PROBE FAIL (" + fails + ")" : "CAR NAME PROBE PASS");
  process.exit(fails ? 1 : 0);
})();
