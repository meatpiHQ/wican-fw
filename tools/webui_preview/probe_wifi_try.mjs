/* Probe (2026-10-06): the connection test on the Quick Setup WiFi step
   ("test the station connect before storing and rebooting", Ali). Over the
   mock: `__mockState.tryResult` decides the verdict (connected | password |
   not_found | refused | no_ip | timeout), `tryDelayMs` the time it takes,
   `tryMode` "ap" refuses the start (no station interface), `offline` is the
   access point blinking while the radio changes channel. Checks every card,
   the marked password field, Test again, Continue anyway and its chip on
   Review, the pass remembered for the same credentials, the blink ridden
   out, the AP-only skip, and the tested address as the second way in on
   Reconnect, preferred by the probe when it answers.
   usage: node probe_wifi_try.mjs   (about 80 s) */
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
    },
  });
  const w = dom.window, d = () => w.document;
  w.addEventListener("error", (e) => errs.push("window.onerror: " + e.message));
  w.addEventListener("unhandledrejection", (e) => errs.push("unhandledrejection: " + (e.reason && e.reason.message || e.reason)));
  const $ = (sel) => d().querySelector(sel), $$ = (sel) => [...d().querySelectorAll(sel)];
  const fire = (el, type) => el.dispatchEvent(new w.Event(type, { bubbles: true }));
  const setText = (el, v) => { el.value = v; fire(el, "input"); fire(el, "change"); };
  const btn = (re) => $$("#view button").find((b) => re.test(b.textContent.trim()));
  const h2 = () => (($("#view h2") || {}).textContent || "").trim();
  const text = () => ($("#view") || {}).textContent || "";
  const card = () => { const c = $("#view .qs-check"); return c ? c.textContent.replace(/\s+/g, " ").trim() : "(no card)"; };
  const pri = () => (($("#view .qs-foot button.pri") || {}).textContent || "").trim();
  return { w, d, $, $$, fire, setText, btn, h2, text, card, pri, errs, navs, M: () => w.__mockState };
}

/* safety, use case (Home Assistant), the HACS steps, the AP password: to the WiFi step */
async function toWifi(p) {
  for (let i = 0; i < 8 && !/Join your home WiFi/.test(p.h2()); i++) {
    if (/Keep your WiCAN private/.test(p.h2())) p.$$(".qs-agree input[type=checkbox]").forEach((c) => { c.checked = true; p.fire(c, "change"); });
    if (/Secure the access point/.test(p.h2())) { p.setText(p.$("#qs-ap-pw"), "garage-door-2026"); p.setText(p.$("#qs-ap-pw2"), "garage-door-2026"); }
    const b = p.$("#view .qs-foot button.pri:not([disabled])");
    if (!b) break;
    b.click(); await sleep(500);
  }
  await sleep(1200); /* the scan */
  p.$('.qs-net[data-ssid="Neighbor"]').click(); await sleep(60);
  p.setText(p.$("#qs-wifi-pw"), "letmein-please");
}
const runTest = async (p, result, wait = 2600) => { p.M().tryResult = result; p.btn(/Test and continue|Test again/).click(); await sleep(wait); };

(async () => {
  /* ---------------- the verdicts ---------------- */
  {
    const p = boot("http://wican.local/", { apDefaultPassword: true, tryResult: "password" });
    await sleep(1500);
    await toWifi(p);
    check("WiFi step, Neighbor picked, the button says Test and continue", /Join your home WiFi/.test(p.h2()) && p.pri() === "Test and continue", [p.h2(), p.pri()]);

    await runTest(p, "password");
    check("wrong password: the card says so with the reason", /Neighbor did not accept the password/.test(p.card()) && /reason 204/.test(p.card()) && /typo/.test(p.card()), p.card());
    check("the password field is marked and the button says Test again", p.$("#qs-wifi-pw").classList.contains("qs-bad") && p.pri() === "Test again", p.pri());
    check("Continue anyway is offered in the card", !!p.$("#view .qs-check button") && /Continue anyway/.test(p.$("#view .qs-check button").textContent));
    check("one POST so far", p.M().tryPosts === 1, p.M().tryPosts);

    await runTest(p, "not_found");
    check("not found: the card names range, 5 GHz and hidden names", /Neighbor was not found/.test(p.card()) && /reason 201/.test(p.card()) && /5 GHz/.test(p.card()) && /hidden name/.test(p.card()), p.card());
    check("the field is no longer marked for a not-found verdict", !p.$("#qs-wifi-pw").classList.contains("qs-bad"));

    await runTest(p, "no_ip");
    check("no address: the card blames DHCP or a MAC filter", /Joined Neighbor, but got no address/.test(p.card()) && /DHCP/.test(p.card()), p.card());

    await runTest(p, "timeout");
    check("timeout: the card says 20 s and closer to the router", /No answer from Neighbor in 20 s/.test(p.card()) && /closer/.test(p.card()), p.card());

    await runTest(p, "refused");
    check("refused: the card gives the reason and the router causes", /Neighbor refused the connection/.test(p.card()) && /reason 203/.test(p.card()) && /MAC filter/.test(p.card()), p.card());
    check("five POSTs, one per press", p.M().tryPosts === 5, p.M().tryPosts);

    /* Continue anyway: Review carries the warning, Back returns to a fresh test */
    p.$("#view .qs-check button").click(); await sleep(400);
    check("Continue anyway: Review with the warning chip", /Review, then restart/.test(p.h2()) && /Test failed, continuing anyway/.test(p.text()), p.h2());
    check("no tested address promised in that case", !/10\.42\.0\.62/.test(p.text()));
    p.btn(/^Back$/).click(); await sleep(700);
    check("Back to the WiFi step: the test is asked again", /Join your home WiFi/.test(p.h2()) && p.pri() === "Test and continue", [p.h2(), p.pri()]);

    /* the blink: the AP pauses for two seconds in the middle of a longer test */
    p.M().trial = null; p.M().tryDelayMs = 4500;
    p.M().tryResult = "connected";
    p.btn(/Test and continue/).click(); await sleep(600);
    check("the Trying card is up and the button says Testing", /Trying Neighbor/.test(p.card()) && /stay on this screen|loses it for a moment/.test(p.card()) && p.pri() === "Testing", [p.card().slice(0, 40), p.pri()]);
    p.M().offline = true; await sleep(2000); p.M().offline = false;
    await sleep(1200);
    check("after the blink the page re-routed and the rebuilt step carries the test on (Trying card again)", /Trying Neighbor/.test(p.card()) && p.pri() === "Testing", [p.card().slice(0, 40), p.pri()]);
    await sleep(3200);
    check("the blink is ridden out: connected, straight to Review with the verdict and address", /Review, then restart/.test(p.h2()) && /Tested: password accepted, 10\.42\.0\.62/.test(p.text()), [p.h2(), p.card().slice(0, 60)]);
    check("six POSTs: the blink did not start a second test", p.M().tryPosts === 6, p.M().tryPosts);

    /* the pass is remembered for these credentials, a new password is not */
    p.btn(/^Back$/).click(); await sleep(700);
    check("Back: the same credentials need no new test (Continue)", p.pri() === "Continue", p.pri());
    p.setText(p.$("#qs-wifi-pw"), "letmein-pleas");
    check("an edited password asks for the test again", p.pri() === "Test and continue", p.pri());
    p.setText(p.$("#qs-wifi-pw"), "letmein-please");
    check("the tested password back: Continue again", p.pri() === "Continue", p.pri());
    p.btn(/^Continue$/).click(); await sleep(400);
    check("Review again without a new POST", /Review, then restart/.test(p.h2()) && p.M().tryPosts === 6, p.M().tryPosts);

    /* Reconnect: the tested address is the second way in and the preferred door */
    p.w.location.hash = "#/setup/reconnect"; p.w.dispatchEvent(new p.w.Event("hashchange")); await sleep(1500);
    check("Reconnect shows the tested address as a second link", /Now switch networks/.test(p.h2()) && /or http:\/\/10\.42\.0\.62\/#\/setup\/checks/.test(p.text()) && /usually the same after the restart/.test(p.text()), p.h2());
    p.M().offline = true;
    await sleep(13500);
    check("after 12 s offline: the name and the address are probed (two probes per tick)", (p.M().linkProbes || 0) >= 2, p.M().linkProbes);
    p.M().linkAnswers = true;
    await sleep(3500);
    const go = p.$("#view .qs-check a.btn.pri");
    check("found: the Continue button goes to the tested address, not the .local name", !!go && go.getAttribute("href") === "http://10.42.0.62/#/setup/checks" && /continues at 10\.42\.0\.62/.test(p.card()), go && go.getAttribute("href"));
    check("no page errors", p.errs.length === 0, p.errs.slice(0, 3));
    p.w.close();
  }

  /* ---------------- a device in access point only mode ---------------- */
  {
    const p = boot("http://wican.local/", { apDefaultPassword: true, tryMode: "ap" });
    await sleep(1500);
    await toWifi(p);
    p.btn(/Test and continue/).click(); await sleep(900);
    check("AP only: the test is skipped with its note on Review", /Review, then restart/.test(p.h2()) && /Not tested: WiCAN is access point only until the restart/.test(p.text()), p.h2());
    check("no POST counted (the 409 came before any trial)", !(p.M().tryPosts), p.M().tryPosts || 0);
    check("no page errors", p.errs.length === 0, p.errs.slice(0, 3));
    p.w.close();
  }

  /* ---------------- lost contact for the whole test ---------------- */
  {
    const p = boot("http://wican.local/", { apDefaultPassword: true, tryResult: "connected", tryDelayMs: 1500 });
    await sleep(1500);
    await toWifi(p);
    p.btn(/Test and continue/).click(); await sleep(300);
    p.M().offline = true;
    await sleep(31500);
    check("30 s without the device: the card says so and offers Test again", /Lost contact with WiCAN during the test/.test(p.card()) && /WiCAN_/.test(p.card()) && p.pri() === "Test again", [p.card().slice(0, 60), p.pri()]);
    p.M().offline = false;
    check("no page errors", p.errs.length === 0, p.errs.slice(0, 3));
    p.w.close();
  }

  console.log(fails ? `WIFI TRY PROBE FAIL (${fails})` : "WIFI TRY PROBE PASS");
  process.exit(fails ? 1 : 0);
})();
