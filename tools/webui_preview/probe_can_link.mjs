/* Probe (2026-10-03, TASK_j1939_wwh.md phase 2): the native CAN node listens
   before it talks and autopid's bus guard can park the poller. The Status
   tile must say where the link is (not just "Normal · active"), the CAN
   Monitor must explain why nothing is sent, the bit rate select must offer
   Automatic, and the Automate dashboard must print the firmware's sentence
   when polling is paused for the bus. Runs over the mock API, once per
   preset. */
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
    },
  });
  return { w: dom.window, errs };
}

const tileText = (w) => {
  const t = [...w.document.querySelectorAll("#view *")].find((e) => e.children.length === 0 && e.textContent.trim() === "CAN Bus");
  let n = t;
  for (let i = 0; i < 4 && n && !/K|Auto|—/.test(n.textContent.replace("CAN Bus", "")); i++) n = n.parentElement;
  return n ? n.textContent.replace(/\s+/g, " ").trim() : "";
};

(async () => {
  /* ---- the Status tile, one preset per link state ---- */
  const cases = [
    ["healthy bus", null, /500K/, /Normal · active/],
    ["automatic, nothing proven yet", { baud_auto: true, state: "detecting", listen_only: true, verified: false, baud_detected: 0 }, /Auto/, /Listening for the bit rate/],
    ["fixed bit rate, no verdict yet", { state: "listening", listen_only: true, verified: false, baud_detected: 0 }, /500K/, /Listening · no traffic yet/],
    ["the bus runs at another bit rate", { state: "mismatch", listen_only: true, verified: false, baud_detected: 0 }, /500K/, /Bus runs at another bit rate · listen-only/],
    ["automatic, no candidate reads the bus", { baud_auto: true, state: "mismatch", listen_only: true, verified: false, baud_detected: 0, baud_kbps: 33 }, /Auto/, /No bit rate reads this bus · listen-only/],
    ["demoted: listen-only although the setting says normal", { state: "running", listen_only: true, verified: true, baud_kbps: 250, baud_detected: 250 }, /250K/, /Listen-only · active/],
  ];
  for (const [name, can, rate, cap] of cases) {
    const { w, errs } = boot("#/", can ? { can } : {});
    await sleep(1200);
    const t = tileText(w);
    check("Status tile: " + name, rate.test(t) && cap.test(t), t);
    check("  no page errors (" + name + ")", errs.length === 0, errs);
    check("  no long dash in the caption (" + name + ")", !/—/.test(t.replace("CAN Bus", "")), t);
    w.close();
  }

  /* ---- the Dashboard (Automate's live values): the bus guard's sentence ---- */
  {
    const reason = "the vehicle bus runs at 250 kbit/s and the protocol setting 6 transmits at 500: nothing is transmitted (set the protocol to Automatic)";
    const { w, errs } = boot("#/dashboard", { busGuard: reason });
    await sleep(1500);
    const view = w.document.querySelector("#view");
    const banner = [...view.querySelectorAll(".banner")].find((b) => /Polling is paused/.test(b.textContent));
    check("Automate: a banner says polling is paused", !!banner, banner && banner.textContent.slice(0, 80));
    check("Automate: the banner prints the firmware's sentence", !!banner && banner.textContent.includes("The vehicle bus runs at 250 kbit/s") && banner.textContent.includes("Automatic"));
    check("Automate: the banner links to the settings", !!banner && !!banner.querySelector('a[href="#/automate/settings"]'));
    const chip = [...view.querySelectorAll(".chip")].find((c) => /Paused: nothing is sent to this bus/.test(c.textContent));
    check("Automate: the state chip says paused", !!chip);
    check("Automate: no long dash in the new text", !!banner && !/—/.test(banner.textContent));
    check("Automate: no page errors", errs.length === 0, errs);
    w.close();
  }
  {
    const { w, errs } = boot("#/dashboard", {});
    await sleep(1500);
    const banner = [...w.document.querySelectorAll("#view .banner")].find((b) => /Polling is paused/.test(b.textContent) && b.parentElement.style.display !== "none");
    check("Automate: no banner while the guard allows", !banner);
    check("Automate: no page errors (allowed)", errs.length === 0, errs);
    w.close();
  }

  /* ---- rows not sent because their own init sets another bit rate (2026-10-05) ---- */
  {
    const reason = "the init of row Soc sets protocol 6 (500 kbit/s) and the vehicle bus runs at 250 kbit/s: not sent";
    const { w, errs } = boot("#/dashboard", { busRefused: { reason, ms: 1200 } });
    await sleep(1500);
    const view = w.document.querySelector("#view");
    const banner = [...view.querySelectorAll(".banner")].find((b) => /Some rows are not sent/.test(b.textContent) && b.parentElement.style.display !== "none");
    check("Automate: a banner says some rows are not sent", !!banner, banner && banner.textContent.slice(0, 80));
    check("Automate: it prints the firmware's sentence", !!banner && banner.textContent.includes("The init of row Soc sets protocol 6 (500 kbit/s)") && banner.textContent.includes("250 kbit/s"));
    check("Automate: it links to the parameters", !!banner && !!banner.querySelector('a[href="#/automate/parameters"]'));
    check("Automate: polling is not shown as paused", ![...view.querySelectorAll(".chip")].some((c) => /Paused: nothing is sent/.test(c.textContent)));
    check("Automate: no long dash in the refused text", !!banner && !/—/.test(banner.textContent));
    check("Automate: no page errors (refused)", errs.length === 0, errs);
    w.close();
  }
  {
    const { w, errs } = boot("#/dashboard", { busRefused: { reason: "the init of row Soc sets protocol 6 (500 kbit/s) and the vehicle bus runs at 250 kbit/s: not sent", ms: 300000 } });
    await sleep(1500);
    const banner = [...w.document.querySelectorAll("#view .banner")].find((b) => /Some rows are not sent/.test(b.textContent) && b.parentElement.style.display !== "none");
    check("Automate: no banner for a refusal long ago", !banner);
    check("Automate: no page errors (old refusal)", errs.length === 0, errs);
    w.close();
  }

  /* ---- CAN Monitor: Automatic in the bit rate select, the reason nothing is sent ---- */
  {
    const { w, errs } = boot("#/monitor", { can: { state: "mismatch", listen_only: true, verified: false, baud_detected: 0 } });
    await sleep(2500);
    const d = w.document;
    const opts = [...d.querySelectorAll("#view select option, .modal select option")].map((o) => o.value);
    check("Monitor: the bit rate select offers Automatic", opts.includes("auto"), opts.slice(0, 12));
    const status = d.querySelector(".pm-status");
    check("Monitor: the status bar shows the link state", !!status && /other bit rate/.test(status.textContent), status && status.textContent.slice(0, 120));
    check("Monitor: no page errors", errs.length === 0, errs);
    w.close();
  }

  console.log(fails ? "PROBE FAIL (" + fails + ")" : "PROBE PASS");
  process.exit(fails ? 1 : 0);
})();
