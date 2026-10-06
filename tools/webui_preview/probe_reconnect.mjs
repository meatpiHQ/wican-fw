/* Probe (2026-10-06): the Quick Setup reconnect screen after Ali's phone
   report. The page is loaded from the access point; when the AP password
   changes the phone drops off and joins the home WiFi by itself, so the
   page's own origin is out of reach for good. The screen must then look for
   WiCAN at the mDNS link and, when it answers, say so and offer the way on
   as a button (never a jump on a timer: Ali, 2026-10-06), say honestly what
   it is waiting for meanwhile, and the Copy link button must copy without
   navigator.clipboard (plain http). Over the mock (`__mockState`):
   `offline` = the page's origin is gone, `linkAnswers` = WiCAN answers at
   wican_<id>.local, `linkProbes` counts the no-CORS probes.
   usage: node probe_reconnect.mjs   (about 70 s: the screen is patient on purpose) */
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import { JSDOM, VirtualConsole } from "jsdom";

const here = dirname(fileURLToPath(import.meta.url));
const html = readFileSync(join(here, "preview.html"), "utf-8");
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
let fails = 0;
const check = (n, ok, extra) => { console.log((ok ? "PASS " : "FAIL ") + n + (extra !== undefined ? "  [" + JSON.stringify(extra) + "]" : "")); if (!ok) { fails++; process.exitCode = 1; } };

function boot(url, preset) {
  const errs = [], navs = [];
  const vc = new VirtualConsole();
  vc.on("jsdomError", (e) => { const m = e.message + " " + (e.detail && e.detail.stack || "").slice(0, 300); if (/navigation/i.test(e.message)) navs.push(m); else errs.push(m); });
  const dom = new JSDOM(html, {
    runScripts: "dangerously", url, pretendToBeVisual: true, virtualConsole: vc,
    beforeParse(w) {
      w.__mockPreset = preset || {};
      w.matchMedia = () => ({ matches: false, addListener() {}, addEventListener() {} });
      w.scrollTo = () => {}; w.HTMLCanvasElement.prototype.getContext = () => null;
      if (!w.TextEncoder) { w.TextEncoder = TextEncoder; w.TextDecoder = TextDecoder; }
      w.Element.prototype.scrollIntoView = () => {};
      /* jsdom has no clipboard at all: stand in for execCommand("copy") and keep what was selected */
      w.document.execCommand = (cmd) => { if (cmd !== "copy") return false; const a = w.document.activeElement; w.__copied = a && a.value !== undefined ? a.value.slice(a.selectionStart || 0, a.selectionEnd == null ? undefined : a.selectionEnd) : ""; return true; };
    },
  });
  const w = dom.window, d = () => w.document;
  w.addEventListener("error", (e) => errs.push("window.onerror: " + e.message));
  w.addEventListener("unhandledrejection", (e) => errs.push("unhandledrejection: " + (e.reason && e.reason.message || e.reason)));
  const $ = (sel) => d().querySelector(sel), $$ = (sel) => [...d().querySelectorAll(sel)];
  const card = () => { const c = $(".qs-check"); return c ? c.textContent.replace(/\s+/g, " ").trim() : "(no card)"; };
  const pill = () => ({ tag: ($("#cp-tag") || {}).textContent, shown: $("#offline") && $("#offline").style.display !== "none", text: ($("#offtx") || {}).textContent });
  const toasts = () => $$(".toast, #toasts > *").map((t) => t.textContent.trim());
  return { w, d, $, $$, card, pill, toasts, errs, navs, M: () => w.__mockState };
}

(async () => {
  /* ---------------- the phone that moved ---------------- */
  {
    const p = boot("http://wican.local/#/setup/reconnect", { apDefaultPassword: true });
    await sleep(1800);
    check("reconnect screen opens from its URL", /Now switch networks/.test(($("h2", p) || {}).textContent || ""), ($("h2", p) || {}).textContent);
    const link = (p.$(".qs-link .url") || {}).textContent || "";
    check("the link is the mDNS address with the resume hash", /^http:\/\/wican_[0-9a-f]+\.local\/#\/setup\/checks$/i.test(link), link);
    check("the link is selectable in one go (user-select: all)", /user-select:\s*all/.test((p.$("#qs-css") || {}).textContent || ""));
    check("own origin online: the card reports the join, not Restarting", /WiCAN joined/.test(p.card()) && /10\.42\.0\.62/.test(p.card()), p.card());
    check("no probe of the link while the own origin answers", !(p.M().linkProbes), p.M().linkProbes || 0);

    /* Copy link over plain http: the page's execCommand path, not navigator.clipboard */
    const copyBtn = p.$$(".qs-link .acts button").find((b) => /Copy link/.test(b.textContent));
    check("Copy link button present", !!copyBtn);
    p.w.navigator.clipboard = undefined;
    copyBtn.click(); await sleep(150);
    check("Copy link copies the link without navigator.clipboard", p.w.__copied === link, p.w.__copied);
    check("the button says Copied and the toast says so", /Copied/.test(copyBtn.textContent) && p.toasts().some((t) => /Link copied/.test(t)), [copyBtn.textContent, p.toasts()]);
    await sleep(1600);
    check("the button reads Copy link again", /Copy link/.test(copyBtn.textContent), copyBtn.textContent);

    /* the phone leaves the access point: the page's origin is gone, the device is fine */
    const t0 = Date.now();
    p.M().offline = true;
    await sleep(6500);
    check("origin gone: the card says Restarting and what to do meanwhile", /Restarting/.test(p.card()) && /Join .* on this device meanwhile/.test(p.card()) && /tells you when it finds WiCAN there/.test(p.card()), p.card());
    check("no 'keeps trying' promise about the old address", !/keeps trying/.test(p.card()));
    check("pill: the access point is out of reach, not 'Device offline'", p.pill().shown && /access point is out of reach/.test(p.pill().text), p.pill());
    check("no probe of the link in the first 12 s (WiCAN is still restarting)", !(p.M().linkProbes), p.M().linkProbes || 0);
    await sleep(9000);
    check("after 12 s the link is probed every tick", (p.M().linkProbes || 0) >= 1, p.M().linkProbes);
    check("the link does not answer: still Restarting", /Restarting/.test(p.card()), p.card());
    const waited = Date.now() - t0;
    await sleep(Math.max(0, 43000 - waited));
    check("after 40 s: Still looking, with the access point fallback link", /Still looking for WiCAN/.test(p.card()) && /192\.168\.(80\.1|0\.10)\/#\/setup\/checks/.test(p.card()) && /wrong password/.test(p.card()), p.card());
    check("the fallback link is a real anchor", !!p.$(".qs-check a[href*='/#/setup/checks']"));

    /* WiCAN appears on the home WiFi */
    const probes = p.M().linkProbes || 0;
    p.M().linkAnswers = true;
    await sleep(3500);
    check("the link answers: the card says found and where Quick Setup continues", /WiCAN answers on/.test(p.card()) && /continues at wican_[0-9a-f]+\.local/i.test(p.card()), p.card());
    check("probes kept running until then", (p.M().linkProbes || 0) > probes, [probes, p.M().linkProbes]);
    const go = p.$(".qs-check a.btn.pri");
    check("the way on is a button to the link, in the card", !!go && /Continue on HomeWiFi/.test(go.textContent) && go.getAttribute("href") === link, go && [go.textContent, go.getAttribute("href")]);
    await sleep(4500);
    check("no jump on a timer: the page stays here", p.navs.length === 0, p.navs);
    check("found stays found (no repaint, no flicker)", /WiCAN answers on/.test(p.card()) && !!p.$(".qs-check a.btn.pri"), p.card());
    const pr = p.M().linkProbes;
    await sleep(3500);
    check("no more probes once found", p.M().linkProbes === pr, [pr, p.M().linkProbes]);
    go.click();
    await sleep(300);
    check("the button opens the link (jsdom reports the navigation)", p.navs.length >= 1, p.navs[0]);
    check("no page errors", p.errs.length === 0, p.errs.slice(0, 3));
    p.w.close();
  }

  /* ---------------- a PC that stays on the access point ---------------- */
  {
    const p = boot("http://wican.local/#/setup/reconnect", { apDefaultPassword: true });
    p.M().linkAnswers = true;   /* the name resolves over the AP too */
    await sleep(15500);
    check("own origin online and the link answering: no probe, no found card, the join card", /WiCAN joined/.test(p.card()) && p.navs.length === 0 && !(p.M().linkProbes), [p.card().slice(0, 40), p.navs.length, p.M().linkProbes || 0]);
    check("no page errors", p.errs.length === 0, p.errs.slice(0, 3));
    p.w.close();
  }

  console.log(fails ? `RECONNECT PROBE FAIL (${fails})` : "RECONNECT PROBE PASS");
})();

function $(sel, p) { return p.$("#view " + sel) || p.$(sel); }
