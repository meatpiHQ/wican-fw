/* Probe (2026-11-08): the OBD chip firmware prompt on Quick Setup's vehicle
   step. A chip on V2.3.22 loses its automatic protocol search after the warm
   start the bring-up sends (the finding of 2026-11-08), so the step asks for
   the update to the device's V2.3.24 before the detection. Four boots over
   the mock (`diag.chip22`, `diag.updateMs`, `diag.updateFail`):
     A  the popup (versions, one button), Detect disabled with its chip, the
        banner behind; Update and continue -> the progress popup that a tap
        beside it does not close (records written, the steps) -> Done with
        Continue -> the banner gone, Detect free once the acknowledgements
        are ticked
     B  a tap beside the popup closes it: the banner stays with Update now,
        Detect stays disabled with both acknowledgements ticked
     C  the job fails (ESP_ERR_TIMEOUT at record 1248): the banner turns red
        with the error and Try again; a second failure adds the unplug advice
     D  a chip on V2.3.24: no popup, no banner, Detect as before
   Prints `CHIP PROMPT PROBE PASS`. Run after `python make_preview.py`
   (plain and `--min`). */
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import { JSDOM, VirtualConsole } from "jsdom";

const here = dirname(fileURLToPath(import.meta.url));
const html = readFileSync(join(here, "preview.html"), "utf-8");
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
let fails = 0;
const check = (n, ok, extra) => { console.log((ok ? "PASS " : "FAIL ") + n + (extra !== undefined ? "  [" + JSON.stringify(extra).slice(0, 300) + "]" : "")); if (!ok) { fails++; process.exitCode = 1; } };

function boot(preset, url = "http://wican.local/#/setup/vehicle") {
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
  const $ = (sel) => d().querySelector(sel), $$ = (sel) => [...d().querySelectorAll(sel)];
  const fire = (el, type) => el.dispatchEvent(new w.Event(type, { bubbles: true }));
  const tick = (el, on) => { el.checked = on !== false; fire(el, "change"); };
  const btn = (re) => $$("#view button").find((b) => re.test(b.textContent.trim()));
  const h2 = () => (($("#view h2") || {}).textContent || "").trim();
  const text = () => ($("#view") || {}).textContent || "";
  const modalBtn = (re) => [...d().querySelectorAll("#modal-root .acts button")].find((b) => re.test(b.textContent));
  const modalText = () => ($("#modal-root") || {}).textContent || "";
  const modalOn = () => !!$("#modal-root .modal");
  const detect = () => btn(/Detect my vehicle/);
  const waitFor = async (fn, ms) => { const t0 = Date.now(); while (Date.now() - t0 < ms) { if (fn()) return true; await sleep(150); } return fn(); };
  return { w, d, $, $$, fire, tick, btn, h2, text, errs, modalBtn, modalText, modalOn, detect, waitFor, M: () => w.__mockState };
}

(async () => {
  /* ---- A: the prompt, the update, done ---- */
  {
    const p = boot({ diag: { chip22: true, updateMs: 1500 } });
    await sleep(1500);
    check("A: the vehicle step", /Your vehicle/.test(p.h2()), p.h2());
    check("A: the popup names the fault and both versions", p.modalOn() && /The OBD chip needs a firmware update/.test(p.modalText()) && /V2\.3\.22/.test(p.modalText()) && /V2\.3\.24/.test(p.modalText()) && /automatic protocol search/.test(p.modalText()), p.modalText().slice(0, 160));
    check("A: one button, Update and continue", [...p.d().querySelectorAll("#modal-root .acts button")].length === 1 && !!p.modalBtn(/Update and continue/));
    check("A: the banner behind carries the same offer", !!p.$("#qs-chip-banner") && /needs a firmware update/.test(p.$("#qs-chip-banner").textContent) && !!p.btn(/Update now/));
    check("A: Detect my vehicle disabled with the chip beside it", p.detect() && p.detect().disabled && /Update the chip first/.test(p.text()));
    p.modalBtn(/Update and continue/).click(); await sleep(1200);
    check("A: the progress popup with the records written", p.modalOn() && /Updating the OBD chip firmware/.test(p.modalText()) && /records/.test(p.modalText()) && /Writing V2\.3\.24/.test(p.modalText()), p.modalText().slice(0, 160));
    check("A: the mock's update route was posted once", (p.M().diagUpdates || 0) === 1, p.M().diagUpdates);
    check("A: the banner reads Updating while it runs", /Updating/.test((p.$("#qs-chip-banner") || {}).textContent || ""));
    /* a tap beside the popup does not close it while the chip is written */
    const root = p.$("#modal-root"); root.dispatchEvent(new p.w.MouseEvent("click", { bubbles: true }));
    check("A: a tap beside the popup does not close it", p.modalOn() && /Updating the OBD chip firmware/.test(p.modalText()));
    const done = await p.waitFor(() => /OBD chip updated to V2\.3\.24/.test(p.modalText()), 9000);
    check("A: Done, the chip on V2.3.24", done && !!p.modalBtn(/^Continue$/), p.modalText().slice(0, 120));
    p.modalBtn(/^Continue$/).click(); await sleep(300);
    check("A: the popup gone, the banner gone, no chip beside Detect", !p.modalOn() && !p.$("#qs-chip-banner") && !/Update the chip first/.test(p.text()));
    p.tick(p.$("#qs-plugged")); p.tick(p.$("#qs-ignition")); await sleep(50);
    check("A: Detect my vehicle free once the acknowledgements are ticked", p.detect() && !p.detect().disabled);
    check("A: no JS errors", p.errs.length === 0, p.errs);
  }
  /* ---- B: a tap beside the popup closes it, the banner stays ---- */
  {
    const p = boot({ diag: { chip22: true, updateMs: 1500 } });
    await sleep(1500);
    check("B: the popup", p.modalOn() && /needs a firmware update/.test(p.modalText()));
    const root = p.$("#modal-root"); root.dispatchEvent(new p.w.MouseEvent("click", { bubbles: true })); await sleep(100);
    check("B: closed by the tap, nothing posted", !p.modalOn() && !(p.M().diagUpdates), p.M().diagUpdates);
    check("B: the banner stays with Update now", !!p.$("#qs-chip-banner") && !!p.btn(/Update now/));
    p.tick(p.$("#qs-plugged")); p.tick(p.$("#qs-ignition")); await sleep(50);
    check("B: Detect stays disabled with both acknowledgements ticked", p.detect() && p.detect().disabled && /Update the chip first/.test(p.text()));
    check("B: no JS errors", p.errs.length === 0, p.errs);
  }
  /* ---- C: the job fails ---- */
  {
    const p = boot({ diag: { chip22: true, updateMs: 1500, updateFail: "ESP_ERR_TIMEOUT" } });
    await sleep(1500);
    p.modalBtn(/Update and continue/).click();
    const failed = await p.waitFor(() => !p.modalOn() && /did not finish/.test((p.$("#qs-chip-banner") || {}).textContent || ""), 9000);
    const b = (p.$("#qs-chip-banner") || {}).textContent || "";
    check("C: the banner turns red with the error and the record", failed && p.$("#qs-chip-banner").classList.contains("crit") && /ESP_ERR_TIMEOUT/.test(b) && /at record 1[ ,.]?248/.test(b), b.slice(0, 200));
    check("C: Try again offered, no unplug advice yet", !!p.btn(/Try again/) && !/unplug WiCAN/.test(b));
    check("C: Detect still disabled", p.detect() && p.detect().disabled);
    p.btn(/Try again/).click(); await sleep(1200);
    check("C: the second attempt runs the popup again", p.modalOn() && /Updating the OBD chip firmware/.test(p.modalText()) && (p.M().diagUpdates || 0) === 2, p.M().diagUpdates);
    const failed2 = await p.waitFor(() => !p.modalOn() && /unplug WiCAN for ten seconds/.test((p.$("#qs-chip-banner") || {}).textContent || ""), 9000);
    check("C: a second failure adds the unplug advice", failed2, ((p.$("#qs-chip-banner") || {}).textContent || "").slice(0, 240));
    check("C: no JS errors", p.errs.length === 0, p.errs);
  }
  /* ---- D: a chip on the packaged version ---- */
  {
    const p = boot({});
    await sleep(1500);
    check("D: no popup, no banner on a current chip", !p.modalOn() && !p.$("#qs-chip-banner") && !/Update the chip first/.test(p.text()));
    p.tick(p.$("#qs-plugged")); p.tick(p.$("#qs-ignition")); await sleep(50);
    check("D: Detect my vehicle as before", p.detect() && !p.detect().disabled);
    check("D: no JS errors", p.errs.length === 0, p.errs);
  }
  /* ---- E: the Status page (a device set up before never re-runs the wizard; Ali, 2026-11-08) ---- */
  {
    const p = boot({ diag: { chip22: true, updateMs: 1500 } }, "http://wican.local/#/status");
    await sleep(1800);
    check("E: the Status page", /Live overview/.test(p.text()), p.text().slice(0, 80));
    check("E: the popup opens on the Status page too", p.modalOn() && /needs a firmware update/.test(p.modalText()) && /V2\.3\.22/.test(p.modalText()));
    check("E: the banner on the Status page with Update now", !!p.$("#chip-fw-banner") && !!p.btn(/Update now/));
    const root = p.$("#modal-root"); root.dispatchEvent(new p.w.MouseEvent("click", { bubbles: true })); await sleep(100);
    check("E: a tap beside the popup closes it, the banner stays", !p.modalOn() && !!p.$("#chip-fw-banner"));
    await sleep(4600);   /* the page's next refresh: the popup is once per browser session */
    check("E: the next refresh does not open the popup again", !p.modalOn() && !!p.$("#chip-fw-banner"));
    p.btn(/Update now/).click(); await sleep(1200);
    check("E: Update now runs the progress popup", p.modalOn() && /Updating the OBD chip firmware/.test(p.modalText()) && (p.M().diagUpdates || 0) === 1, p.M().diagUpdates);
    check("E: the banner reads Updating meanwhile", /Updating/.test((p.$("#chip-fw-banner") || {}).textContent || ""));
    const done = await p.waitFor(() => /OBD chip updated to V2\.3\.24/.test(p.modalText()), 9000);
    check("E: Done", done && !!p.modalBtn(/^Continue$/));
    p.modalBtn(/^Continue$/).click(); await sleep(300);
    check("E: the popup and the banner are gone", !p.modalOn() && !p.$("#chip-fw-banner"));
    await sleep(4600);
    check("E: the banner stays away on the next refresh (the chip reads current)", !p.$("#chip-fw-banner") && !p.modalOn());
    check("E: no JS errors", p.errs.length === 0, p.errs);
  }
  /* ---- F: the Status page with a current chip ---- */
  {
    const p = boot({}, "http://wican.local/#/status");
    await sleep(1800);
    check("F: no popup, no banner on the Status page with a current chip", !p.modalOn() && !p.$("#chip-fw-banner") && /Live overview/.test(p.text()));
    check("F: no JS errors", p.errs.length === 0, p.errs);
  }
  console.log(fails ? "CHIP PROMPT PROBE FAIL (" + fails + ")" : "CHIP PROMPT PROBE PASS");
  process.exit(fails ? 1 : 0);
})();
