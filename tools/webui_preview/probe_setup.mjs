/* Probe (2026-10-01): the Quick Setup wizard (web/setup.js chunk) over the
   mock API. Two page boots: a FRESH device (factory AP password) that must
   land on the wizard and be walked end to end (safety, use case, MQTT
   details, AP password, WiFi scan with an open network, review, the single
   submit, the deep link into the checks, the vehicle scan phases, finish),
   and a CONFIGURED device that must boot on Status, keep the sidebar entry
   and honour "Skip setup for now".
   usage: node probe_setup.mjs   (npm i jsdom first; node >= 20) */
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
  const errs = [];
  const vc = new VirtualConsole();
  vc.on("jsdomError", (e) => errs.push(e.message + " " + (e.detail && e.detail.stack || "").slice(0, 300)));
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
  const tick = (el, on) => { el.checked = on !== false; fire(el, "change"); };
  const btn = (re) => $$("#view button").find((b) => re.test(b.textContent.trim()));
  const h2 = () => (($("#view h2") || {}).textContent || "").trim();
  const text = () => ($("#view") || {}).textContent || "";
  const nav = (hash) => { w.location.hash = hash; w.dispatchEvent(new w.Event("hashchange")); };
  const modalBtn = (re) => [...d().querySelectorAll("#modal-root .acts button")].find((b) => re.test(b.textContent));
  const modalText = () => ($("#modal-root") || {}).textContent || "";
  return { w, d, $, $$, fire, setText, tick, btn, h2, text, nav, errs, modalBtn, modalText, M: () => w.__mockState, S: () => w.__MOCK_SETTINGS__ };
}

(async () => {
  /* ---------------- fresh device ---------------- */
  {
    const p = boot("http://wican.local/", { apDefaultPassword: true });
    await sleep(1500);
    check("fresh device: the default route opens the wizard", p.w.location.hash === "#/setup" || p.w.location.hash.startsWith("#/setup/") || /Quick Setup/.test((p.$("#view h1") || {}).textContent || ""), p.w.location.hash);
    check("focused layout: #app carries setup-focus", p.$("#app").classList.contains("setup-focus"));
    check("rail lists twelve steps", p.$$(".qs-step").length === 12, p.$$(".qs-step").length);
    check("safety screen first", /Keep your WiCAN private/.test(p.h2()), p.h2());
    let cont = p.btn(/^Continue$/);
    check("Continue disabled until the three rules are ticked", cont && cont.disabled);
    p.$$(".qs-agree input[type=checkbox]").forEach((c) => p.tick(c));
    cont = p.btn(/^Continue$/);
    check("Continue enabled after ticking", cont && !cont.disabled);
    cont.click(); await sleep(250);
    check("use case screen", /How will you use WiCAN/.test(p.h2()), p.h2());
    check("Home Assistant preselected", p.$('.qs-tile[data-use="ha"]').classList.contains("sel"));
    /* WiFi only is a tile (Ali, 2026-10-07): chosen, the use-case screen leaves the rail and the
       steps renumber */
    check("a third tile: Just connect WiCAN to my WiFi", !!p.$('.qs-tile[data-use="wifi"]') && /Just connect WiCAN to my WiFi/.test(p.$('.qs-tile[data-use="wifi"]').textContent) && !p.$("#qs-use-wifi"));
    p.$('.qs-tile[data-use="wifi"]').click(); await sleep(250);
    check("WiFi only chosen: no Home Assistant step on the rail, Access point is step 3", !p.$('.qs-step[data-step="details"]') && /^3/.test(p.$('.qs-step[data-step="ap"] .n').textContent) && p.$$(".qs-step").length === 11, [p.$$(".qs-step").length, (p.$('.qs-step[data-step="ap"] .n') || {}).textContent]);
    p.$('.qs-tile[data-use="mqtt"]').click(); await sleep(250);
    check("MQTT chosen again: the step is back, twelve on the rail", !!p.$('.qs-step[data-step="details"]') && p.$$(".qs-step").length === 12);
    check("MQTT tile selects and the rail label follows", p.$('.qs-tile[data-use="mqtt"]').classList.contains("sel") && /MQTT broker/.test(p.$('.qs-step[data-step="details"]').textContent));
    p.btn(/^Continue$/).click(); await sleep(400);
    check("MQTT details screen", /Your MQTT broker/.test(p.h2()), p.h2());
    cont = p.btn(/^Continue$/);
    check("Continue waits for a broker address", cont && cont.disabled === !p.$("#qs-mq-url").value);
    p.setText(p.$("#qs-mq-url"), "mqtt://10.0.0.5:1883");
    p.setText(p.$("#qs-mq-user"), "wican");
    p.setText(p.$("#qs-mq-pw"), "secret12");
    p.setText(p.$("#qs-mq-period"), "7");
    cont = p.btn(/^Continue$/);
    check("Continue enabled with an address", cont && !cont.disabled);
    cont.click(); await sleep(300);
    check("AP password screen with the factory warning", /Secure the access point/.test(p.h2()) && /factory password/.test(p.text()), p.h2());
    cont = p.btn(/^Continue$/);
    check("Continue disabled with no password", cont && cont.disabled);
    p.setText(p.$("#qs-ap-pw"), "@meatpi#"); p.setText(p.$("#qs-ap-pw2"), "@meatpi#");
    check("the factory password is refused", p.btn(/^Continue$/).disabled && /That is the factory password/.test(p.text()));
    p.setText(p.$("#qs-ap-pw"), "garage-door-2026"); p.setText(p.$("#qs-ap-pw2"), "garage-door-2025");
    check("mismatch keeps Continue disabled", p.btn(/^Continue$/).disabled && /two passwords differ/.test(p.text()));
    p.setText(p.$("#qs-ap-pw2"), "garage-door-2026");
    check("matching password enables Continue", !p.btn(/^Continue$/).disabled);
    p.btn(/^Continue$/).click(); await sleep(600);
    check("WiFi screen with the scan list", /Join your home WiFi/.test(p.h2()) && p.$$(".qs-net").length >= 3, p.$$(".qs-net").length);
    check("duplicate names collapse to one row (strongest)", p.$$('.qs-net[data-ssid="HomeWiFi"]').length === 1);
    check("security comes from auth_mode (Neighbor is WPA2/3, CafeFree open)", /WPA2\/3/.test(p.$('.qs-net[data-ssid="Neighbor"]').textContent) && /Open/.test(p.$('.qs-net[data-ssid="CafeFree"]').textContent));
    p.$('.qs-net[data-ssid="CafeFree"]').click(); await sleep(50);
    check("an open network blocks Continue with a warning", p.btn(/Test and continue|^Continue$/).disabled && /has no password/.test(p.text()));
    p.$('.qs-net[data-ssid="Neighbor"]').click(); await sleep(50);
    check("a protected network needs a password first", p.btn(/Test and continue/).disabled);
    p.setText(p.$("#qs-wifi-pw"), "letmein-please");
    check("password enables Test and continue (2026-10-06: the connection test runs before Review)", !p.btn(/Test and continue/).disabled);
    /* after it joins (Ali, 2026-10-07): Station only is the default on a fresh device; Access
       point + Station reveals the auto-off box, ticked by default */
    check("after it joins: Station only is the default, no auto-off box yet", /After it joins/.test(p.text()) && p.$("#qs-mode-sta") && p.$("#qs-mode-sta").checked && !p.$("#qs-mode-apsta").checked && !p.$("#qs-ap-auto") && /hold its button for 5 seconds/.test(p.text()));
    p.$("#qs-mode-apsta").click(); p.fire(p.$("#qs-mode-apsta"), "change"); await sleep(100);
    check("Access point + Station reveals the auto-off box, ticked by default", p.$("#qs-mode-apsta").checked && !!p.$("#qs-ap-auto") && p.$("#qs-ap-auto").checked && /Turn the access point off while WiCAN is on Neighbor/.test(p.text()));
    p.$("#qs-mode-sta").click(); p.fire(p.$("#qs-mode-sta"), "change"); await sleep(100);
    check("back to Station only: the box is gone", p.$("#qs-mode-sta").checked && !p.$("#qs-ap-auto"));
    const auto0 = p.S().wifi_manager.values.ap_auto_disable;
    p.btn(/Test and continue/).click(); await sleep(400);
    check("the test starts: POST /api/wifi/try, the Trying card, the button says Testing", p.M().tryPosts === 1 && /Trying Neighbor/.test(p.text()) && /nothing is saved/.test(p.text()) && /Testing/.test((p.$(".qs-foot button.pri") || {}).textContent || ""), [p.M().tryPosts, (p.$(".qs-foot button.pri") || {}).textContent]);
    await sleep(2800);
    check("the network accepted the password: straight on to Review with the verdict", /Review, then restart/.test(p.h2()) && /Password accepted/.test(p.text()) && /10\.42\.0\.62/.test(p.text()), p.h2());
    /* Review is a checklist (Ali's sketch, 2026-10-07): a circle per row, the title in its
       column, the value and details beside it; three settled, the vehicle data pending; no pills */
    check("review: a checklist of four rows, three done circles and one pending, no pills", p.$$(".qs-rv li").length === 4 && p.$$(".qs-rv .ci").length === 4 && p.$$(".qs-rv .ci.pend").length === 1 && p.$$(".qs-rv .ci.warn").length === 0 && p.$$(".qs-rv .chip").length === 0, { rows: p.$$(".qs-rv li").length, pend: p.$$(".qs-rv .ci.pend").length });
    check("Review: step 3 is the address itself, Station only on the rows (the access point off after the restart, the button named)", /open http:\/\/10\.42\.0\.62\/#\/setup\/checks/.test(p.text()) && /Station only/.test(p.text()) && /Off after the restart/.test(p.text()) && /hold the button 5 s/.test(p.text()) && /drops off WiCAN_[0-9a-f]+ for good/.test(p.text()) && !/\.local/.test(p.text()), (p.text().match(/Connect your phone[^.]*/) || [])[0]);
    check("review screen summarises everything", /Review, then restart/.test(p.h2()) && /Neighbor/.test(p.text()) && /mqtt:\/\/10\.0\.0\.5:1883/.test(p.text()) && /New password/.test(p.text()), p.h2());
    const puts0 = p.M().puts || 0;
    p.btn(/Save and restart/).click(); await sleep(900);
    const wf = p.S().wifi_manager.values, mq = p.S().mqtt_manager.values, dd = p.S().data_destinations.values;
    check("one submit: wifi_manager PUT carries mode sta (the default) + station + trusted + new AP password, ap_auto_disable untouched", wf.mode === "sta" && wf.ap_auto_disable === auto0 && wf.sta_ssid === "Neighbor" && wf.sta_password === "letmein-please" && wf.sta_trusted === true && wf.ap_password === "garage-door-2026", { mode: wf.mode, auto: [wf.ap_auto_disable, auto0], ssid: wf.sta_ssid, trusted: wf.sta_trusted });
    check("mqtt_manager PUT enables the broker", mq.enabled === true && mq.url === "mqtt://10.0.0.5:1883" && mq.username === "wican" && mq.broker_password === "secret12", { enabled: mq.enabled, url: mq.url });
    const row = (dd.destinations || []).find((x) => x.type === "mqtt" && x.url === "~/autopid");
    check("data_destinations PUT has the ~/autopid mqtt row at the chosen period", dd.enabled === true && row && row.enabled === true && row.period_s === 7, row && { period: row.period_s, enabled: row.enabled });
    check("three PUTs went out", (p.M().puts || 0) - puts0 === 3, (p.M().puts || 0) - puts0);
    check("reconnect screen with the address link carrying the resume hash and the use case (mqtt), no mDNS name", /Now switch networks/.test(p.h2()) && (p.$(".qs-link .url") || {}).textContent === "http://10.42.0.62/#/setup/checks/mqtt" && !/\.local/.test(p.text()), (p.$(".qs-link .url") || {}).textContent);
    check("reconnect screen, Station only: the fallback names the router's device list and the button, with the access point address", /router's device list/.test(p.text()) && /hold WiCAN's button for 5 seconds/.test(p.text()) && /192\.168\.80\.1\/#\/setup\/checks|192\.168\.0\.10\/#\/setup\/checks/.test(p.text()));

    /* the phone comes back on the home network: deep link into the checks */
    p.M().apDefaultPassword = false;
    await p.w.ping();
    p.nav("#/setup/checks/mqtt"); await sleep(700);
    check("deep link (with the use case segment) lands on the checks screen", /Connected through/.test(p.h2()), p.h2());
    const cards = p.$$(".qs-check");
    check("WiFi, access point (off: Station only) and MQTT cards are shown, no mDNS name", cards.length === 3 && /joined/.test(cards[0].textContent) && /router's device list shows it as wican_/.test(cards[0].textContent) && /Access point off: Station only/.test(cards[1].textContent) && /hold its button for 5 seconds/i.test(cards[1].textContent) && /MQTT broker connected/.test(cards[2].textContent) && !/\.local/.test(p.text()), cards.map((c) => c.querySelector("b").textContent));
    check("rail: the six steps before the restart show as done", p.$$(".qs-step.done").length === 7, p.$$(".qs-step.done").length);
    /* a FRESH device: polling off, the firmware's 1 s default group (the mock ships autopid on) */
    p.S().autopid.values.enabled = false;
    p.btn(/Set up my vehicle/).click(); await sleep(600);
    check("vehicle screen", /Your vehicle/.test(p.h2()), p.h2());
    let det = p.btn(/Detect my vehicle/);
    check("detect waits for the plug + ignition acknowledgements", det && det.disabled);
    p.tick(p.$("#qs-plugged")); p.tick(p.$("#qs-ignition"));
    det = p.btn(/Detect my vehicle/);
    check("detect enabled after both ticks", det && !det.disabled);
    det.click(); await sleep(400);
    check("detection shows the protocol phase first", /Detecting the OBD protocol/.test(p.text()) && p.$(".qs-spin") !== null);
    await sleep(1500);
    check("then the VIN phase, protocol done", /Reading the VIN/.test(p.text()) && /Done/.test(p.text()));
    await sleep(2600);
    check("result: new vehicle, VIN, detected protocol, PID count, no pills (2026-10-07)", /New vehicle/.test(p.text()) && /1WCAN0FW0P0000001/.test(p.text()) && /CAN 11-bit 500 kbit\/s/.test(p.text()) && /8 answered/.test(p.text()) && /none chosen yet/.test(p.text()) && p.$$(".qs-kv .chip").length === 0, p.text().slice(0, 200));
    /* 2. Standard PIDs: the scan found what the car answers, the user chooses what WiCAN reads
       (Ali, 2026-10-07): nothing chosen for a new car, the page's picker (the Automate one) */
    check("section 2 is the standard PID choice, the profile section 3, nothing chosen yet", /2\. Standard PIDs/.test(p.text()) && /3\. Vehicle profile/.test(p.text()) && /None chosen yet/.test(p.text()) && !!p.btn(/Choose PIDs/), p.text().match(/\d\. [A-Z][a-z]+ [a-zP]+/g));
    p.btn(/Choose PIDs/).click(); await sleep(300);
    const pickBoxes = () => [...p.$$('#modal-root input[type="checkbox"]')];
    check("the picker lists the 8 PIDs the scan found, none ticked, Add selected", /Standard PIDs this car answers/.test(p.modalText()) && pickBoxes().length === 8 && pickBoxes().every((b) => !b.checked) && !!p.modalBtn(/Add selected/) && /0 ticked/.test(p.modalText()), { boxes: pickBoxes().length });
    for (const b of pickBoxes()) { const row = b.closest("label"); if (/010C1|010D1|01051|012F1/.test(row.textContent)) { b.click(); p.fire(b, "change"); } }
    check("four ticked, the count follows", /4 ticked/.test(p.modalText()));
    p.modalBtn(/Add selected/).click(); await sleep(300);
    check("the choice shows on the step: 4 of 8, named, with Change; the card says 4 chosen", /4 of 8 chosen: /.test(p.text()) && /Fuel Level/.test(p.text()) && !!p.btn(/^Change$/) && /4 chosen/.test(p.text()), p.text().match(/4 of 8 chosen[^.]*/) && p.text().match(/4 of 8 chosen[^.]*/)[0]);
    check("the store lists the car as current, profile pending", /Vehicles this WiCAN knows/.test(p.text()) && /Profile pending|Current/.test(p.text()));
    check("a name field is offered", !!p.$("#qs-veh-name"));
    check("profile radios offer another profile and none", !!p.$("#qs-prof-other") && !!p.$("#qs-prof-none"));
    let fin = p.btn(/^Continue$/);
    check("Continue waits for a profile choice", fin && fin.disabled);
    let tb = p.btn(/Test profile/);
    check("Test profile disabled without a chosen profile", tb && tb.disabled);
    p.$("#qs-prof-none").click(); p.fire(p.$("#qs-prof-none"), "change"); await sleep(200);
    fin = p.btn(/^Continue$/);
    check("standard PIDs only enables Continue", fin && !fin.disabled);
    /* a profile through the shared picker (the mock serves the published list) */
    p.$("#qs-prof-other").click(); p.fire(p.$("#qs-prof-other"), "change"); await sleep(900);
    const mk = p.$("#modal-root .vp-make");
    check("Another profile opens the picker with makes", !!mk);
    if (mk) { mk.click(); await sleep(150); const row = p.$("#modal-root .vp-row"); if (row) row.click(); await sleep(150); const use = p.modalBtn(/Use this profile/); if (use) use.click(); await sleep(400); }
    check("the picked profile shows next to Another profile", !!p.$(".qs-radios .sel"), (p.$(".qs-radios .sel") || {}).textContent);
    tb = p.btn(/Test profile/);
    check("Test profile enabled with a chosen profile", tb && !tb.disabled);
    tb.click(); await sleep(1800);
    check("the test popup lists parameters with values and a summary", /^Test: /.test(((p.$("#modal-root h3") || {}).textContent || "")) && p.$$("#modal-root tbody tr").length > 0 && /parameters answered/.test(p.modalText()), { rows: p.$$("#modal-root tbody tr").length });
    p.modalBtn(/Use this profile/).click(); await sleep(300);
    p.setText(p.$("#qs-veh-name"), "Bench car");
    const pids0 = p.M().autopidCfg.pids.filter((x) => x.type === "specific").length;
    check("vehicle step ends with Continue (Finish moved to the reading rules)", !!p.btn(/^Continue$/) && !p.btn(/Finish and restart/));
    p.btn(/^Continue$/).click(); await sleep(1600);
    /* ---- the Battery and sleep step (2026-10-01): the probe plays the car through the mock battery ---- */
    check("battery step after the vehicle", /When should WiCAN sleep/.test(p.h2()), p.h2());
    check("rail has twelve steps", p.$$(".qs-step").length === 12, p.$$(".qs-step").length);
    check("the live reading shows ONE decimal", /^12\.5V$/.test((p.$("#qs-pwr-big") || {}).textContent || ""), (p.$("#qs-pwr-big") || {}).textContent);
    check("waiting for the engine, Continue locked", /Waiting for the engine/.test(p.text()) && p.$("#qs-pwr-continue") && p.$("#qs-pwr-continue").disabled);
    check("the skip is a real button quoting the device pair with one decimal (Ali, 2026-10-06)", !!p.$("#qs-pwr-keep") && /Skip, keep the defaults \(13\.1 \/ 13\.2 V\)/.test(p.$("#qs-pwr-keep").textContent) && !p.$("#qs-pwr-keep").classList.contains("gh") && !p.$("#qs-pwr-keep").disabled, (p.$("#qs-pwr-keep") || {}).textContent);
    p.M().batteryV = 14.31; await sleep(7600);
    check("charging captured by itself from six stable readings, one decimal", /14\.3 V charging/.test(p.text()) && !/14\.31/.test(p.text()), (p.text().match(/1\d\.\d+ V charging/) || [])[0]);
    check("the engine-off row is now the active one", /Waiting for the engine to stop/.test(p.text()));
    p.M().batteryV = 12.78; await sleep(13500);
    check("the drop was noticed and the rest captured, one decimal", /12\.8 V resting/.test(p.text()), (p.text().match(/1\d\.\d+ V resting/) || [])[0]);
    check("recommended pair from the two readings: sleep 13.1, wake 13.3", (p.$("#qs-pwr-sleep-val") || {}).textContent === "13.1 V" && (p.$("#qs-pwr-wake-val") || {}).textContent === "13.3 V", { s: (p.$("#qs-pwr-sleep-val") || {}).textContent, w: (p.$("#qs-pwr-wake-val") || {}).textContent });
    check("the trace carries both threshold lines", p.$$("#qs-pwr-trace line.t").length === 2 && /sleep 13\.1 V/.test(p.$("#qs-pwr-trace").textContent) && /wake 13\.3 V/.test(p.$("#qs-pwr-trace").textContent));
    check("the summary names the margins with one decimal", /below 13\.1 V for 5 minutes/.test(p.text()) && /above 13\.3 V/.test(p.text()) && /0\.3 V above resting, 1\.0 V below charging/.test(p.text()), (p.text().match(/Margins on your car[^.]*\./) || [])[0]);
    check("Continue unlocked once both readings are in", p.$("#qs-pwr-continue") && !p.$("#qs-pwr-continue").disabled);
    check("the wake row quotes the device's wake-up delay", /Above this voltage for 0\.5 s/.test(p.text()));
    const sl = p.$("#qs-pwr-sleep"); sl.value = "13.3"; p.fire(sl, "input"); await sleep(150);
    check("dragging sleep up pushes wake to keep the 0.1 V band", (p.$("#qs-pwr-wake-val") || {}).textContent === "13.4 V" && !!p.btn(/Back to the recommendation/), (p.$("#qs-pwr-wake-val") || {}).textContent);
    const wk = p.$("#qs-pwr-wake"); wk.value = "14.3"; p.fire(wk, "input"); await sleep(150);
    check("wake above the charging voltage is warned about", /Wake is above your charging voltage/.test(p.text()));
    p.btn(/Back to the recommendation/).click(); await sleep(400);
    check("back to the recommendation restores 13.1 / 13.3", (p.$("#qs-pwr-sleep-val") || {}).textContent === "13.1 V" && (p.$("#qs-pwr-wake-val") || {}).textContent === "13.3 V" && !/Wake is above/.test(p.text()));
    p.$("#qs-pwr-continue").click(); await sleep(600);
    check("reading-the-car step", /How WiCAN reads the car/.test(p.h2()), p.h2());
    check("the pause rule quotes the measured pair", /sleep voltage \(13\.1 V, measured on your car: engine off\)/.test(p.text()) && /wake-up voltage \(13\.3 V\)/.test(p.text()), (p.text().match(/sleep voltage \([^)]*\)[^.]*\./) || [])[0]);
    check("defaults: every 5 s, pause with Power Saving, trouble codes off", p.$("#qs-rate-5").checked && p.$("#qs-pause-sleep").checked && !p.$("#qs-dtc").checked);
    check("the sleep voltage from the device is quoted", /13\.1 V|\d+\.\d V/.test(p.text()));
    check("vehicle-specific switch enabled because a profile was chosen", p.$("#qs-specific") && !p.$("#qs-specific").disabled && p.$("#qs-specific").checked);
    check("the Standard PIDs switch reads the choice; no Custom PIDs switch (Ali, 2026-10-07)", p.$("#qs-std") && p.$("#qs-std").checked && !p.$("#qs-std").disabled && /The 4 of the 8 the scan found that you chose/.test(p.text()) && !p.$("#qs-custom") && !/Custom PIDs/.test(p.text()), p.text().match(/The \d of the \d[^.]*/) && p.text().match(/The \d of the \d[^.]*/)[0]);
    p.$("#qs-pause-never").click(); p.fire(p.$("#qs-pause-never"), "change"); await sleep(150);
    check("Never pause shows the battery warning", /keeps the car's modules awake/.test(p.text()));
    p.$("#qs-rate-10").click(); p.fire(p.$("#qs-rate-10"), "change"); await sleep(150);
    p.tick(p.$("#qs-dtc")); await sleep(150);
    check("trouble codes on reveals the interval", !!p.$("#qs-dtcmin") && p.$("#qs-dtcmin").value === "60");
    p.setText(p.$("#qs-dtcmin"), "120");
    p.setText(p.$("#qs-minevent"), "2");
    /* the device's own file carries a parameter name twice (a car answering both oxygen-sensor
       PID sets under the old table, 2026-10-06): Finish must not send it as it is. Custom rows
       since 2026-10-07: a standard row that was not chosen leaves the config at Finish */
    p.M().autopidCfg.pids.push(
      { name: "OxySensor1_Volt", type: "custom", cmd: "0114", group: "default", parameters: [{ name: "OxySensor1_Volt", expression: "B2*0.005" }, { name: "OxySensor1_STFT", expression: "B3" }] },
      { name: "OxySensor1_FAER", type: "custom", cmd: "0124", group: "default", parameters: [{ name: "OxySensor1_FAER", expression: "[B2:B3]" }, { name: "OxySensor1_Volt", expression: "[B4:B5]" }] });
    /* a standard row the device stored that was NOT chosen (the firmware stores every row the
       scan found): Finish drops it */
    p.M().autopidCfg.pids.push({ name: "Throttle", type: "std", cmd: "01111", group: "default", parameters: [{ name: "Throttle", expression: "B3*100/255", unit: "%" }] });
    const custom0 = p.S().autopid.values.custom_enabled;
    p.btn(/Finish and restart/).click(); await sleep(1000);
    {
      const names = p.M().autopidCfg.pids.flatMap((x) => (x.parameters || []).map((q) => q.name));
      check("finish: no parameter name twice in the config PUT (the later one got _2)", new Set(names).size === names.length && names.includes("OxySensor1_Volt") && names.includes("OxySensor1_Volt_2"), names.filter((n) => /OxySensor1/.test(n)));
    }
    const puts = p.M().vehiclePuts || [];
    check("finish: PUT the car's entry with the name, the profile and its init", puts.length === 1 && puts[0].key === "1WCAN0FW0P0000001" && puts[0].body.name === "Bench car" && typeof puts[0].body.profile === "string" && puts[0].body.profile.length > 0 && "specific_init" in puts[0].body, puts[0] && puts[0].body);
    check("finish: the store cleared pending_profile and holds the profile", !(p.M().vehicles.vehicles[0] || {}).pending_profile && !!(p.M().vehicles.vehicles[0] || {}).profile);
    check("finish: the config PUT carries the profile's vehicle-specific rows", p.M().autopidCfg.pids.filter((x) => x.type === "specific").length > pids0, p.M().autopidCfg.pids.filter((x) => x.type === "specific").length);
    const ap = p.S().autopid.values;
    check("finish: autopid PUT enables polling, protocol follows the store, profile named", ap.enabled === true && ap.std_enabled === true && ap.std_protocol === "0" && ap.specific_enabled === true && ap.vehicle.length > 0, { enabled: ap.enabled, proto: ap.std_protocol, vehicle: ap.vehicle });
    check("finish: the reading rules landed in the autopid PUT; custom_enabled left as the device had it", ap.pause_below_mv === 0 && ap.pause_follow_sleep === false && ap.pause_mode === "requests_only" && ap.dtc_enabled === true && ap.dtc_scan_period_min === 120 && ap.min_event_interval_ms === 2000 && ap.custom_enabled === custom0, { pause_below_mv: ap.pause_below_mv, follow: ap.pause_follow_sleep, mode: ap.pause_mode, dtc: ap.dtc_enabled, dtc_min: ap.dtc_scan_period_min, ev: ap.min_event_interval_ms, custom: [ap.custom_enabled, custom0] });
    const smv = p.S().sleep_manager.values;
    check("finish: the measured pair is staged for Power Saving, switched on", smv.enabled === true && smv.sleep_mv === 13100 && smv.wake_mv === 13300, { enabled: smv.enabled, sleep_mv: smv.sleep_mv, wake_mv: smv.wake_mv });
    const cfgNow = p.M().autopidCfg;
    const byName = (n) => cfgNow.pids.find((x) => x.name === n) || {};
    check("finish: the default group runs at the chosen rate; rows on the old default inherit it, a row with its own rate keeps it", cfgNow.groups[0].period_ms === 10000 && !byName("RPM").period_ms && !byName("Speed").period_ms && byName("Coolant").period_ms === 5000 && cfgNow.pids.filter((x) => x.type === "std" && x.name !== "Coolant").every((x) => !x.period_ms), { period: cfgNow.groups[0].period_ms, own: cfgNow.pids.filter((x) => x.period_ms).map((x) => x.name + ":" + x.period_ms) });
    {
      /* the chosen four are all asked for; a chosen request the picked profile already carries
         (this one asks for 012F itself) is not added as a second row: one row per request, the
         guard the no-store path always had; no standard row that was not chosen stays */
      const cmds = cfgNow.pids.map((x) => String(x.cmd).toUpperCase());
      const std = cfgNow.pids.filter((x) => x.type === "std").map((x) => x.cmd);
      check("finish: every chosen request is asked for, the device's three standard rows kept, Throttle (stored, not chosen) dropped", ["010C1", "010D1", "01051", "012F1"].every((c) => cmds.includes(c)) && std.every((c) => ["010C1", "010D1", "01051", "012F1"].includes(c)) && std.length === 3 && !byName("Throttle").cmd, { std, other: cfgNow.pids.filter((x) => x.type !== "std").map((x) => x.cmd) });
    }
    check("finish: the custom rows (the dedupe fixture, the device's own) stayed", cfgNow.pids.filter((x) => x.type === "custom" && /^OxySensor1_(Volt|FAER)$/.test(x.name)).length === 2 && cfgNow.pids.some((x) => x.type === "custom" && x.name === "OilTemp"), cfgNow.pids.filter((x) => x.type === "custom").map((x) => x.name));
    check("done screen", /WiCAN is set up/.test(p.h2()), p.h2());
    check("done screen shows the VIN, the address and the router's name for WiCAN (no mDNS name)", /1WCAN0FW0P0000001/.test(p.text()) && /http:\/\/10\.42\.0\.62/.test(p.text()) && /shown as wican_[0-9a-f]+ in your router's device list/i.test(p.text()) && !/\.local/.test(p.text()));
    check("done screen summarises the reading rules", /every 10 s, never pauses, trouble codes every 120 min/.test(p.text()), p.text().match(/every [^.]*min/) && p.text().match(/every [^.]*min/)[0]);
    check("done screen lists the measured pair", /sleeps after 5 min below 13\.1 V, wakes above 13\.3 V/.test(p.text()) && /Measured on your car/.test(p.text()) && !/Power saving is off/.test(p.text()), (p.text().match(/sleeps after[^M]*/) || [])[0]);
    check("no JS errors on the fresh-device run", p.errs.length === 0, p.errs.slice(0, 3));
  }

  /* ---------------- configured device ---------------- */
  {
    const p = boot("http://wican.local/", { apDefaultPassword: false });
    await sleep(1300);
    check("configured device: boots on Status, not the wizard", /^#\/?$|^$/.test(p.w.location.hash) && /Status/.test((p.$("#view h1") || {}).textContent || ""), p.w.location.hash);
    check("no focused layout on normal pages", !p.$("#app").classList.contains("setup-focus"));
    const navBtn = p.$$("#menu .nav-btn").find((b) => /Quick Setup/.test(b.textContent));
    check("sidebar: Quick Setup is the first DEVICE entry", navBtn && p.$$("#menu .nav-btn")[0] === navBtn);
    navBtn.click(); await sleep(600);
    check("the wizard opens on demand and the sidebar hides", /Quick Setup/.test((p.$("#view h1") || {}).textContent || "") && p.$("#app").classList.contains("setup-focus"));
    check("re-run: the safety screen offers Exit, not Skip", !!p.btn(/^Exit setup$/) && !p.btn(/Skip setup/));
    p.$$(".qs-agree input[type=checkbox]").forEach((c) => p.tick(c));
    p.btn(/^Continue$/).click(); await sleep(300);
    check("re-run: use case screen", /How will you use WiCAN/.test(p.h2()), p.h2());
    const wifiOnly = p.$('.qs-tile[data-use="wifi"]');
    check("WiFi-only choice is offered as a tile", !!wifiOnly);
    if (wifiOnly) wifiOnly.click();
    await sleep(300);
    p.btn(/^Continue$/).click(); await sleep(300);
    check("WiFi-only skips the details screen, which is not on the rail", /Secure the access point/.test(p.h2()) && !p.$('.qs-step[data-step="details"]'), p.h2());
    check("a device with its own AP password may keep it (Continue enabled with blank fields)", !p.btn(/^Continue$/).disabled && /already has your own password/.test(p.text()));
    p.btn(/^Continue$/).click(); await sleep(700);
    check("re-run WiFi step: the stored network with a blank password needs no test (Continue)", /Join your home WiFi/.test(p.h2()) && !!p.btn(/^Continue$/) && !p.btn(/Test and continue/), [p.h2(), (p.$(".qs-foot button.pri") || {}).textContent]);
    check("re-run on a device running Access point + Station with a network: that stays the default, the auto-off box ticked", p.$("#qs-mode-apsta") && p.$("#qs-mode-apsta").checked && !!p.$("#qs-ap-auto") && p.$("#qs-ap-auto").checked, [(p.$("#qs-mode-apsta") || {}).checked, (p.$("#qs-ap-auto") || {}).checked]);
    p.setText(p.$("#qs-wifi-pw"), "another-password");
    check("a typed password brings the test back", !!p.btn(/Test and continue/), (p.$(".qs-foot button.pri") || {}).textContent);
    p.btn(/^Exit setup$/) ? null : null;
    p.nav("#/system"); await sleep(700);
    check("System > Maintenance has Run Quick Setup again", !!p.btn(/Run Quick Setup again/));
    check("sidebar is back on a normal page", !p.$("#app").classList.contains("setup-focus"));
    /* a fresh-looking device, but the user skipped: stays on Status */
    p.M().apDefaultPassword = true; await p.w.ping();
    p.w.sessionStorage.setItem("wican-setup-skip", "1");
    p.nav(""); await sleep(600);
    check("Skip setup for now: the default route stays on Status for this session", /Status/.test((p.$("#view h1") || {}).textContent || ""), (p.$("#view h1") || {}).textContent);
    check("the header still warns about the factory password", !p.$("#apwarn").hidden);
    p.w.sessionStorage.removeItem("wican-setup-skip");
    p.nav(""); await sleep(600);
    check("without the skip flag the default route opens the wizard again", /Quick Setup/.test((p.$("#view h1") || {}).textContent || ""));
    /* the resume link of a WiFi-only run carries the choice (the page state is lost at the new
       origin): the checks screen must not wait for Home Assistant */
    p.nav("#/setup/checks/wifi"); await sleep(700);
    check("a WiFi-only resume link: the checks screen with no Home Assistant card", /Connected through/.test(p.h2()) && p.$$(".qs-check").length >= 2 && !p.$$(".qs-check").some((c) => /Home Assistant/.test(c.textContent)), p.$$(".qs-check").map((c) => (c.querySelector("b") || {}).textContent));
    check("no JS errors on the configured-device run", p.errs.length === 0, p.errs.slice(0, 3));
  }

  console.log(fails ? ("\n" + fails + " check(s) FAILED") : "\nALL PASS");
  process.exit(fails ? 1 : 0);
})();
