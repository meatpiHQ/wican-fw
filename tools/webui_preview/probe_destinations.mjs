/* Interaction probe for Automate > Data destinations (2026-09-19; the table
   reworked 2026-11-08 after Ali's review: a status chip per row in a fixed
   column with a fixed vocabulary, the row's facts as a muted line under its
   topic or URL, Options as a named button, the options panel starting with
   Active on (a dropdown with check boxes over the station networks) and
   ending with the delivery counters, the Links row with its banner). Run
   after `python make_preview.py`. Prints `PROBE PASS`. */
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import { JSDOM, VirtualConsole } from "jsdom";

const here = dirname(fileURLToPath(import.meta.url));
const html = readFileSync(join(here, "preview.html"), "utf-8");
const errs = [];
const vc = new VirtualConsole();
vc.on("jsdomError", (e) => errs.push(e.message + " " + (e.detail && e.detail.stack || "").slice(0, 300)));

const dom = new JSDOM(html, {
  runScripts: "dangerously", url: "http://wican.local/#/automate/destinations",
  pretendToBeVisual: true, virtualConsole: vc,
  beforeParse(w) {
    /* two backup networks beside the mock's HomeWiFi: the dropdown lists three */
    w.__mockPreset = { wifi: { fallback1_ssid: "Garage", fallback2_ssid: "Office" } };
    w.matchMedia = () => ({ matches: false, addListener() {}, addEventListener() {} });
    w.scrollTo = () => {}; w.HTMLCanvasElement.prototype.getContext = () => null;
  },
});
const w = dom.window, d = () => w.document;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const check = (n, ok, extra) => { console.log((ok ? "PASS " : "FAIL ") + n + (extra !== undefined ? "  [" + JSON.stringify(extra).slice(0, 300) + "]" : "")); if (!ok) process.exitCode = 1; };
const card = () => [...d().querySelectorAll(".section")].find((c) => /Data Destinations/.test((c.querySelector("h3") || {}).textContent || ""));
const rowsOf = () => [...card().querySelectorAll("[data-dest-url]")];
const btn = (re, root = card()) => [...root.querySelectorAll("button")].find((b) => re.test(b.textContent.trim()));
const statusOf = (name) => ((card().querySelector('[data-dest-status="' + name + '"]') || {}).textContent || "").trim();
const subOf = (name) => { const i = card().querySelector('[data-dest-url="' + name + '"]'); return i ? ((i.parentNode.querySelector(".dest-sub") || {}).textContent || "") : ""; };
const options = () => [...card().querySelectorAll("button")].filter((b) => /^Options/.test(b.textContent.trim()));
const panel = () => card().querySelector(".dest-panel");
const fire = (el, type) => el.dispatchEvent(new w.Event(type, { bubbles: true }));
const tick = (inp, on) => { inp.checked = on; fire(inp, "change"); };

(async () => {
  await sleep(1200);
  const c = card();
  check("Data Destinations card on the destinations tab", !!c);
  if (!c) { console.log(errs); process.exit(1); }
  const txt = c.textContent;
  check("three mock rows rendered (mqtt / https / abrp)", rowsOf().length === 3);
  check("type select offers MQTT, HTTP, HTTPS, ABRP",
    [...c.querySelectorAll("select")].some((s) => ["MQTT", "HTTP", "HTTPS", "ABRP"].every((t) => [...s.options].some((o) => o.textContent === t))));
  const hdr = (c.querySelector(".dest-hdr") || {}).textContent || "";
  check("header: On, Type, Topic or URL, Every, Status", /On/.test(hdr) && /Type/.test(hdr) && /Topic or URL/.test(hdr) && /Every/.test(hdr) && /Status/.test(hdr), hdr);
  /* the status column (2026-11-08): one chip per row, the fixed words */
  check("status: dest1 is Sending with its count", /^Sending · 412$/.test(statusOf("dest1")), statusOf("dest1"));
  check("status: cloud is Failing with the retry time", /^Failing · retry in 10 s$/.test(statusOf("cloud")), statusOf("cloud"));
  check("status: abrp waits for its network (Active on Garage, the station on HomeWiFi)", /^Waiting for Garage$/.test(statusOf("abrp")), statusOf("abrp"));
  check("status chips carry the dot (the page's chip)", c.querySelector('[data-dest-status="dest1"] .chip.ok .d') !== null && c.querySelector('[data-dest-status="cloud"] .chip.crit') !== null && c.querySelector('[data-dest-status="abrp"] .chip.warn') !== null);
  /* the row's facts under its topic or URL */
  check("facts: dest1 reads All networks · retained", /All networks/.test(subOf("dest1")) && /retained/.test(subOf("dest1")), subOf("dest1"));
  check("facts: cloud names its networks, the bearer token, the cert set and the first push", /Only on HomeWiFi, Garage/.test(subOf("cloud")) && /bearer token/.test(subOf("cloud")) && /certificate set homeca/.test(subOf("cloud")) && /first push/.test(subOf("cloud")), subOf("cloud"));
  check("facts: abrp names Garage and the car model", /Only on Garage/.test(subOf("abrp")) && /car hyundai:ioniq5:22:77/.test(subOf("abrp")), subOf("abrp"));
  check("link chips: the network with its name, MQTT connected, no banner", /Network up: HomeWiFi/.test(txt) && /MQTT connected/.test(txt) && !/every destination waits/.test(txt));
  check("master switch row", /Master switch/.test(txt));
  check("legacy timer-rule import banner (mock still has dest1 rule)", /timer rules/.test(txt) && !!btn(/Import and remove/));
  check("no long dash in the card", !txt.includes("—"));
  check("Options is a named button on every row (no bare caret)", options().length === 3 && ![...c.querySelectorAll("button")].some((b) => /^[▸▾]$/.test(b.textContent.trim())));

  /* the HTTPS row's options: Active on first, the type's rows, Delivery last */
  options()[1].click(); await sleep(150);
  check("the options open as a panel under the row", !!panel());
  const t2 = panel().textContent;
  check("https options: Active on, Authentication, Certificate set, First push, Delivery", /Active on/.test(t2) && /Authentication/.test(t2) && /Certificate set/.test(t2) && /First push/.test(t2) && /Delivery/.test(t2));
  const certSel = [...panel().querySelectorAll("select")].find((s) => [...s.options].some((o) => /Built-in CA bundle/.test(o.textContent)));
  check("cert set dropdown lists the built-in bundle + the mock set 'homeca' selected", !!certSel && certSel.value === "homeca");
  check("bearer token field shows 'stored' placeholder for a stored secret", [...panel().querySelectorAll("input[type=password]")].some((i) => /stored/.test(i.placeholder)));
  check("Delivery row: failed 3 in a row, backing off 10 s, the last error", /3 in a row/.test(t2) && /backing off 10 s/.test(t2) && /http=503/.test(t2) && /Skipped, other network/.test(t2));
  /* Active on: the dropdown with check boxes (Ali, 2026-11-08) */
  const dd = panel().querySelector(".netdd");
  check("Active on is a dropdown that reads the ticked names", !!dd && /^HomeWiFi, Garage$/.test(dd.querySelector("button > span").textContent.trim()), dd && dd.querySelector("button > span").textContent);
  check("the list is closed until the button is pressed", dd && !dd.classList.contains("open"));
  dd.querySelector("button").click(); await sleep(50);
  check("the button opens the list", dd.classList.contains("open") && dd.querySelector("button").getAttribute("aria-expanded") === "true");
  const labels = () => [...dd.querySelectorAll(".pop label")].map((l) => l.textContent.trim());
  check("the list: All networks, then HomeWiFi (main, connected now), Garage (backup 1), Office (backup 2)",
    labels().length === 4 && /^All networks$/.test(labels()[0]) && /HomeWiFi.*main network.*connected now/.test(labels()[1]) && /Garage.*backup 1/.test(labels()[2]) && /Office.*backup 2/.test(labels()[3]), labels());
  const boxes = () => [...dd.querySelectorAll(".pop input")];
  check("HomeWiFi and Garage ticked, All and Office not", !boxes()[0].checked && boxes()[1].checked && boxes()[2].checked && !boxes()[3].checked);
  tick(boxes()[3], true); await sleep(30);
  check("ticking Office adds it: the button and the row's facts follow", /^HomeWiFi, Garage, Office$/.test(dd.querySelector("button > span").textContent.trim()) && /Only on HomeWiFi, Garage, Office/.test(subOf("cloud")), subOf("cloud"));
  const unsaved = [...d().querySelectorAll(".chip,button")].some((x) => /Apply Configuration|Unsaved/.test(x.textContent));
  check("the change is staged for Apply", unsaved);
  tick(boxes()[0], true); await sleep(30);
  check("ticking All networks clears the names", /^All networks$/.test(dd.querySelector("button > span").textContent.trim()) && boxes()[0].checked && !boxes()[1].checked && /All networks/.test(subOf("cloud")));
  tick(boxes()[2], true); await sleep(30);
  check("ticking a network again drops All", !boxes()[0].checked && /^Garage$/.test(dd.querySelector("button > span").textContent.trim()));
  tick(boxes()[2], false); await sleep(30);
  check("unticking the last network puts All networks back", boxes()[0].checked && /^All networks$/.test(dd.querySelector("button > span").textContent.trim()));
  d().body.click(); await sleep(30);
  check("a click elsewhere closes the list", !dd.classList.contains("open"));

  /* the ABRP row's options: token + api key + car model, no Authentication dropdown */
  options()[1].click(); await sleep(100);   /* close cloud */
  options()[2].click(); await sleep(150);
  const t3 = panel().textContent;
  check("abrp options: user token, API key, placement, car model, Active on", /ABRP user token/.test(t3) && /ABRP API key/.test(t3) && /API key placement/.test(t3) && /Car model/.test(t3) && /Active on/.test(t3) && !/Authentication/.test(t3));
  check("abrp car_model prefilled from the mock", [...panel().querySelectorAll("input")].some((i) => i.value === "hyundai:ioniq5:22:77"));
  await sleep(250);
  const t3b = panel().textContent;
  check("abrp options: the ABRP fields line names the missing planning fields and the parameter to name",
    /ABRP receives now: elevation, heading, lat, lon, speed/.test(t3b) && /Missing for route planning/.test(t3b) && /soc \(name a parameter SOC\)/.test(t3b) && /power \(name a parameter HV_W\)/.test(t3b));
  check("abrp key hint: your own Iternio key, 2 requests per second", /Your own key from Iternio/.test(t3b) && /2 requests per second/.test(t3b));
  check("abrp Delivery row names the laps skipped for the network", /Skipped, other network\s*9/.test(t3b.replace(/\s+/g, " ")), t3b.slice(t3b.indexOf("Skipped, other"), t3b.indexOf("Skipped, other") + 40));
  /* the 5 s floor on an ABRP row's period (the first two rows keep the minimum of 1) */
  const periodInputs = [...card().querySelectorAll("input[type=number]")];
  const abrpPeriod = periodInputs[2];
  check("abrp row's period input has the 5 s floor, the mqtt row keeps 1", !!abrpPeriod && abrpPeriod.min === "5" && periodInputs[0].min === "1");
  abrpPeriod.value = "2"; fire(abrpPeriod, "change"); await sleep(50);
  check("a period under 5 s typed on an ABRP row is raised to 5", abrpPeriod.value === "5");

  /* add a destination: a new mqtt row appears expanded, Not applied yet, its Test waits for the restart */
  options()[2].click(); await sleep(100);   /* close abrp's panel: the new row's is the one to read */
  btn(/Add destination/).click(); await sleep(150);
  check("Add destination adds a 4th row named dest2 with ~/autopid, expanded", rowsOf().length === 4 && rowsOf().some((i) => i.value === "~/autopid" && i.dataset.destUrl === "dest2") && /Retained/.test(panel().textContent));
  check("the new row reads Not applied yet", /^Not applied yet$/.test(statusOf("dest2")), statusOf("dest2"));
  const newTest = card().querySelector('button[data-dest-test="dest2"]');
  check("the new row's Test is disabled until the restart", newTest && newTest.disabled && /after the restart/.test(newTest.title));
  /* switching the new row to ABRP pre-selects the api_key query placement and clears the ~/ topic */
  const newRowSel = [...card().querySelectorAll("select")].filter((s) => [...s.options].some((o) => o.textContent === "ABRP")).pop();
  newRowSel.value = "abrp"; fire(newRowSel, "change"); await sleep(120);
  check("type -> ABRP clears the ~/ topic and shows the token fields", !rowsOf().some((i) => i.dataset.destUrl === "dest2" && i.value === "~/autopid") && /ABRP user token/.test(panel().textContent));

  /* Test on a saved destination hits the mock and toasts the result */
  const tests = [...card().querySelectorAll("button[data-dest-test]")];
  check("Test button per row", tests.length === 4);
  tests[0].click(); await sleep(300);
  const toastTxt = [...d().querySelectorAll(".toast")].map((t) => t.textContent).join(" | ");
  check("Test dest1 toasts 'delivered'", /dest1: delivered/.test(toastTxt));

  /* delete a row */
  const trash = [...card().querySelectorAll("button.danger")];
  trash[trash.length - 1].click(); await sleep(120);
  check("delete removes the row", rowsOf().length === 3);

  /* the legacy import converts dest1 rule into a row and removes the banner */
  btn(/Import and remove/).click(); await sleep(150);
  check("import adds the timer-rule destination and drops the banner", rowsOf().length === 4 && !/timer rules/.test(card().textContent));

  if (errs.length) { console.log("JS errors:"); errs.forEach((e) => console.log("  " + e)); process.exitCode = 1; }
  else console.log("no JS errors");
  console.log(process.exitCode ? "PROBE FAIL" : "PROBE PASS");
  process.exit(process.exitCode || 0); /* the page keeps a stats poller alive */
})();
