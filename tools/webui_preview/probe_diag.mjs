/* Probe (2026-10-28, issue #1023): the OBD chip and the CAN self-test cards
   on System > Maintenance, from the on-demand chunk web/diag.js over the
   mock's /api/obd_chip, /api/obd_chip/probe, /api/obd_chip/report and
   /api/can/selftest. Four boots: a healthy chip (the cards, the state chips
   with the chip's firmware as its version alone, no update offer and no
   Rewrite button, the configuration report as a copyable block, the wire
   tools' console lines, the self-test run to PASS as the verdict chip and
   the copyable block), a chip that did not come up (the banner with the
   bring-up's wire verdicts, the report refused), a refused self-test, and a
   chip left on V2.3.22 (the banner naming both versions without the chip
   name, the Update button at its right edge, the confirm dialog, the POST
   without force: Ali 2026-10-28 and 2026-11-01), and a chip whose stored
   identity is damaged (issue #1023, 2026-11-12: the red state chip, the
   banner that says so, no update offered). Prints `DIAG PROBE PASS`. */
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
    const row = chip && chip.querySelector(".rowflex");
    check("the state row: ready at 2 Mbaud and the chip's firmware as its version alone", !!row && /ready at 2 000 000 baud/.test(row.textContent) && /firmware V2\.3\.24/.test(row.textContent) && !/MIC3624/.test(row.textContent), row && row.textContent);
    check("the firmware chip is green on a chip at the packaged version, the device's image beside it", !!row && !!p.$$(".chip.ok", row).find((c) => /firmware V2\.3\.24/.test(c.textContent)) && /device carries V2\.3\.24/.test(row.textContent) && !/last update/.test(row.textContent));
    check("no down banner on a healthy chip", !!chip && !/did not come up/.test(chip.textContent));
    check("no update offer on a chip at the packaged version", !!chip && !/newer chip firmware is available/.test(chip.textContent) && !p.btn(chip, "Update"));
    check("the forced rewrite is gone: no Rewrite button, no danger button", !!chip && !p.btn(chip, "Rewrite") && p.$$("button.danger", chip).length === 0);
    const rb = p.btn(chip, "Read the chip configuration");
    check("Read the chip configuration sits at the right edge of its row", !!rb && rb === rb.parentElement.lastElementChild && /grid/.test(rb.parentElement.getAttribute("style") || ""));

    /* the configuration report: the chip's own lines, as it printed them */
    p.click(rb); await sleep(500);
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
    check("the wire tools close the card: nothing below them", !!det && det === chip.querySelector(".body").lastElementChild);

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
    check("a down chip shows the warning chip and no firmware chip", !!chip && /chip not up/.test(chip.querySelector(".rowflex").textContent) && !/firmware V/.test(chip.querySelector(".rowflex").textContent));
    check("the banner carries the bring-up's wire verdicts and points at the wire tools, not at a rewrite", !!chip && /did not come up at this start/.test(chip.textContent) && /at 2 000 000 baud nothing; at 115 200 baud 2 byte\(s\) without a prompt \(FC 00\)/.test(chip.textContent) && /Open the wire tools below/.test(chip.textContent) && !/Rewrite/.test(chip.textContent), chip && chip.textContent.slice(0, 400));
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
    const row = chip && chip.querySelector(".rowflex");
    check("a chip on V2.3.22 is up, its version alone in the state row, flagged", !!row && /ready at 2 000 000 baud/.test(row.textContent) && !/MIC3624/.test(row.textContent) && !!p.$$(".chip.warn", row).find((c) => /firmware V2\.3\.22/.test(c.textContent)), row && row.textContent);
    const ban = chip && chip.querySelector(".banner");
    check("the banner offers the update and names both versions without the chip name", !!ban && /A newer chip firmware is available/.test(ban.textContent) && /The chip runs V2\.3\.22, this device carries V2\.3\.24/.test(ban.textContent) && /20 seconds/.test(ban.textContent) && !/MIC3624/.test(ban.textContent), ban && ban.textContent.slice(0, 300));
    const ub = p.btn(chip, "Update to V2.3.24");
    check("the Update button is a primary button at the right edge of the banner", !!ub && ub.classList.contains("pri") && !!ban && ub === ban.lastElementChild);
    check("no Rewrite button and no danger button here either", !p.btn(chip, "Rewrite") && p.$$("button.danger", chip).length === 0);
    p.click(ub); await sleep(300);
    const m = p.modal();
    check("a confirm dialog names the version and the 20 s", !!m && /Update the chip firmware to V2\.3\.24/.test(m.textContent) && /20 seconds/.test(m.textContent) && !/download mode/.test(m.textContent));
    const ok = m && p.$$("button", m).find((b) => /^Continue$/.test(b.textContent.trim()));
    p.click(ok); await sleep(400);
    const st = p.w.__mockState || null;
    check("confirming posts the update without force", !!(st && st.diagUpdates === 1 && !st.diagForced), st && [st.diagUpdates, st.diagForced]);
    check("no jsdom errors", p.errs.length === 0, p.errs.slice(0, 2));
    p.close();
  }

  /* ---- boot 5: a chip whose stored identity is damaged (issue #1023) ---- */
  {
    const p = boot("#/system/maintenance", { diag: { damaged: true } });
    await sleep(2600);
    const chip = p.cardByTitle("OBD chip");
    const row = chip && chip.querySelector(".rowflex");
    check("a damaged chip is up and says so in the state row, in red, with no version beside it", !!row && /ready at 2 000 000 baud/.test(row.textContent) && !!p.$$(".chip.crit", row).find((c) => /stored identity damaged/.test(c.textContent)) && !/firmware /.test(row.textContent), row && row.textContent);
    const ban = chip && chip.querySelector(".banner");
    check("the banner says the chip still works, is not rewritten and names support", !!ban && ban.classList.contains("crit") && /stored identity is damaged/.test(ban.textContent) && /still works/.test(ban.textContent) && /does not rewrite its firmware/.test(ban.textContent) && /support/.test(ban.textContent), ban && ban.textContent.slice(0, 160));
    check("no update is offered for a damaged chip", !p.btn(chip, "Update to V2.3.24") && !p.btn(chip, "Update the chip firmware") && !/A newer chip firmware/.test(chip.textContent));
    check("no jsdom errors", p.errs.length === 0, p.errs.slice(0, 2));
    p.close();
  }

  console.log(fails ? "DIAG PROBE FAIL (" + fails + ")" : "DIAG PROBE PASS");
})();
