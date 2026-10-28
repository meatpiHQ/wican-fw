/* Probe (2026-10-28, issue #1023): the OBD chip and the CAN self-test cards
   on System > Maintenance, from the on-demand chunk web/diag.js over the
   mock's /api/obd_chip, /api/obd_chip/probe, /api/obd_chip/report and
   /api/can/selftest. Three boots: a healthy chip (the cards, the state chips,
   the rewrite's confirm dialog and its POST, the configuration report as a
   copyable block, the wire tools' console lines, the self-test run to PASS
   as the verdict chip and the copyable block), a chip that did not come up (the banner with
   the bring-up's wire verdicts, the report refused), a refused self-test, and
   a chip left on V2.3.22 (the outdated banner naming both versions, Ali
   2026-10-28). Prints `DIAG PROBE PASS`. */
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import { JSDOM, VirtualConsole } from "jsdom";

const here = dirname(fileURLToPath(import.meta.url));
const html = readFileSync(join(here, "preview.html"), "utf-8");
let fails = 0;
const check = (n, ok, extra) => { console.log((ok ? "PASS " : "FAIL ") + n + (extra !== undefined ? "  [" + JSON.stringify(extra) + "]" : "")); if (!ok) { fails++; process.exitCode = 1; } };
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function boot(hash, preset) {
  const errs = [];
  const vc = new VirtualConsole();
  vc.on("jsdomError", (e) => errs.push(e.message + " " + (e.detail && e.detail.stack || "").slice(0, 300)));
  const dom = new JSDOM(html, {
    runScripts: "dangerously", url: "http://wican.local/" + hash,
    pretendToBeVisual: true, virtualConsole: vc,
    beforeParse(w) {
      w.__mockPreset = preset;
      w.matchMedia = () => ({ matches: false, addListener() {}, addEventListener() {} });
      w.scrollTo = () => {}; w.HTMLCanvasElement.prototype.getContext = () => null;
      if (!w.TextEncoder) { w.TextEncoder = TextEncoder; w.TextDecoder = TextDecoder; }
    },
  });
  const w = dom.window, d = () => w.document;
  const $ = (sel) => d().querySelector(sel), $$ = (sel, root) => [...(root || d()).querySelectorAll(sel)];
  const cardByTitle = (t) => $$("#view .section").find((c) => (c.querySelector(".legend, h3") || {}).textContent.trim().startsWith(t));
  const modal = () => $("#modal-root .modal");
  const click = (el) => el && el.dispatchEvent(new w.MouseEvent("click", { bubbles: true, cancelable: true }));
  const btn = (root, text) => $$("button", root).find((b) => b.textContent.trim().startsWith(text));
  return { w, d, $, $$, cardByTitle, modal, click, btn, errs, close: () => dom.window.close() };
}

(async () => {
  /* ---- boot 1: a healthy chip ---- */
  {
    const p = boot("#/system/maintenance", { diag: {} });
    await sleep(2600);
    const chip = p.cardByTitle("OBD chip"), self = p.cardByTitle("CAN self-test"), maint = p.cardByTitle("Maintenance");
    check("the two cards render", !!chip && !!self);
    check("they sit after the Maintenance card", !!maint && !!chip && (maint.compareDocumentPosition(chip) & 4) !== 0);
    const bk = p.$("#bk-cards");
    check("the Maintenance card leads the page, the backup cards follow the diagnostics", !!bk && (maint.compareDocumentPosition(bk) & 4) !== 0 && (self.compareDocumentPosition(bk) & 4) !== 0);
    check("the chip card shows ready at 2 Mbaud and the chip's version", !!chip && /ready at 2 000 000 baud/.test(chip.textContent) && /MIC3624 V2\.3\.24/.test(chip.textContent));
    check("the packaged version and the last rewrite", !!chip && /device carries V2\.3\.24/.test(chip.textContent) && /last rewrite: ESP_OK/.test(chip.textContent));
    check("no down banner on a healthy chip", !!chip && !/did not come up/.test(chip.textContent));
    check("no outdated banner on a chip at the packaged version", !!chip && !/older than the one this device carries/.test(chip.textContent));

    /* the rewrite: a confirm dialog, then the POST */
    const rw = p.btn(chip, "Rewrite the chip firmware");
    check("the Rewrite button is the card's action", !!rw && rw.classList.contains("danger"));
    p.click(rw); await sleep(300);
    const m = p.modal();
    check("a confirm dialog explains the 20 s and the recovery", !!m && /20 seconds/.test(m.textContent) && /download mode/.test(m.textContent));
    const ok = m && p.$$("button", m).find((b) => /^Continue$/.test(b.textContent.trim()));
    p.click(ok); await sleep(400);
    const st = p.w.__mockState || null;
    check("confirming posts the forced rewrite", !!(st && st.diagRewrites === 1), st && st.diagRewrites);

    /* the configuration report */
    p.click(p.btn(chip, "Read the chip configuration")); await sleep(500);
    const ta = chip.querySelector("textarea");
    check("the report is one copyable block with the chip's lines", !!ta && /VTVERS  firmware: MIC3624 V2\.3\.24/.test(ta.value) && /STSLCS  sleep and wake configuration:/.test(ta.value) && /VL_SLEEP: OFF, <13\.20V FOR 150 s/.test(ta.value) && /ATPPS/.test(ta.value));
    check("the block names its moment and has a Select all button", /OBD chip configuration \(\d\d:\d\d:\d\d\)/.test(ta.value) && !!p.btn(chip, "Select all"));

    /* the wire tools */
    const det = p.$$("details", chip).find((x) => /Wire tools/.test(x.textContent));
    check("the wire tools are folded with the four buttons", !!det && ["Probe", "Scan all speeds", "Detect speed", "Listen"].every((t) => !!p.btn(det, t)));
    p.click(p.btn(det, "Scan all speeds")); await sleep(1400);
    const con = det.querySelector(".console");
    check("a scan writes its lines into the console", !!con && /scan 115 200 ATI  prompt=1  8 B  'ATI\.\?\.\.>'/.test(con.textContent) && /prompt at 115 200 baud/.test(con.textContent), con && con.textContent.slice(0, 200));
    check("the last start's chip lines come from the log ring", /no obd_chip lines in the log ring|obd_chip:/.test(det.textContent));

    /* the self-test */
    check("not run yet before the first run", !!self && /not run yet/.test(self.textContent) && !self.querySelector("textarea"));
    p.click(p.btn(self, "Run the self-test")); await sleep(1500);
    check("the run ends in PASS as the verdict chip", /PASS: both transceivers see each other/.test(self.textContent) && p.$$("li", self).length === 0 && p.$$("details", self).length === 0);
    const sta = self.querySelector("textarea");
    check("the result is one copyable block in the console's text", !!sta && /^--- CAN self-test \(\d\d:\d\d:\d\d\) ---\n/.test(sta.value) && /\n\[ok\] The chip asked, the ESP32 heard \(6 ms\)\n     the chip sent 7DF 02 01 0C/.test(sta.value) && /\nexchange:\n/.test(sta.value) && /\n  chip   printed: 7EF 04 41 0C 1A F8\n/.test(sta.value) && /\ntook 1880 ms\n--- end ---$/.test(sta.value), sta && sta.value.slice(0, 160));
    check("the block has a Select all button", !!p.btn(self, "Select all"));
    check("no jsdom errors", p.errs.length === 0, p.errs.slice(0, 2));
    p.close();
  }

  /* ---- boot 2: a chip that did not come up ---- */
  {
    const p = boot("#/system/maintenance", { diag: { down: true } });
    await sleep(2600);
    const chip = p.cardByTitle("OBD chip");
    check("a down chip shows the warning chip", !!chip && /chip not up/.test(chip.textContent));
    check("the banner carries the bring-up's wire verdicts", !!chip && /did not come up at this start/.test(chip.textContent) && /at 2 000 000 baud nothing; at 115 200 baud 2 byte\(s\) without a prompt \(FC 00\)/.test(chip.textContent), chip && chip.textContent.slice(0, 400));
    p.click(p.btn(chip, "Read the chip configuration")); await sleep(500);
    check("the report is refused while the chip is down", /Could not read the chip/.test(chip.textContent));
    check("no jsdom errors", p.errs.length === 0, p.errs.slice(0, 2));
    p.close();
  }

  /* ---- boot 3: a refused self-test ---- */
  {
    const p = boot("#/system/maintenance", { diag: { selftestRefusal: "the CAN bus is off: enable it in CAN bus settings" } });
    await sleep(2600);
    const self = p.cardByTitle("CAN self-test");
    p.click(p.btn(self, "Run the self-test")); await sleep(1500);
    check("a refusal is shown as not run with its reason and no block", /not run: the CAN bus is off/.test(self.textContent) && !self.querySelector("textarea"));
    check("no jsdom errors", p.errs.length === 0, p.errs.slice(0, 2));
    p.close();
  }

  /* ---- boot 4: a chip left on V2.3.22 ---- */
  {
    const p = boot("#/system/maintenance", { diag: { chip22: true } });
    await sleep(2600);
    const chip = p.cardByTitle("OBD chip");
    check("a chip on V2.3.22 is up and named in the state row", !!chip && /ready at 2 000 000 baud/.test(chip.textContent) && /MIC3624 V2\.3\.22/.test(chip.textContent));
    check("the banner says the chip is older than the device's image and names both", !!chip && /older than the one this device carries/.test(chip.textContent) && /runs MIC3624 V2\.3\.22, the device has V2\.3\.24/.test(chip.textContent), chip && chip.textContent.slice(0, 300));
    check("the Rewrite button is there to act on it", !!p.btn(chip, "Rewrite the chip firmware"));
    check("no jsdom errors", p.errs.length === 0, p.errs.slice(0, 2));
    p.close();
  }

  console.log(fails ? "DIAG PROBE FAIL (" + fails + ")" : "DIAG PROBE PASS");
})();
