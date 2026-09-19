/* Interaction probe for Automate > Data destinations (2026-09-19): the
   data_destinations settings table + live counters. Run after
   `python make_preview.py`. */
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
    w.matchMedia = () => ({ matches: false, addListener() {}, addEventListener() {} });
    w.scrollTo = () => {}; w.HTMLCanvasElement.prototype.getContext = () => null;
  },
});
const w = dom.window, d = () => w.document;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const check = (n, ok) => { console.log((ok ? "PASS " : "FAIL ") + n); if (!ok) process.exitCode = 1; };
const card = () => [...d().querySelectorAll(".section")].find((c) => /Data Destinations/.test((c.querySelector("h3") || {}).textContent || ""));
const rowsOf = () => [...card().querySelectorAll("[data-dest-url]")];
const btn = (re, root = card()) => [...root.querySelectorAll("button")].find((b) => re.test(b.textContent.trim()));

(async () => {
  await sleep(1000);
  const c = card();
  check("Data Destinations card on the destinations tab", !!c);
  if (!c) { console.log(errs); process.exit(1); }
  const txt = c.textContent;
  check("three mock rows rendered (mqtt / https / abrp)", rowsOf().length === 3);
  check("type select offers MQTT, HTTP, HTTPS, ABRP",
    [...c.querySelectorAll("select")].some((s) => ["MQTT", "HTTP", "HTTPS", "ABRP"].every((t) => [...s.options].some((o) => o.textContent === t))));
  check("header says Every (s) + Status", txt.includes("Every (s)") && txt.includes("Status"));
  check("live counters: OK chip on dest1, Fail + backoff on cloud",
    /OK 412/.test(txt) && /Fail 3/.test(txt) && /wait 10s/.test(txt));
  check("link chips (network / MQTT)", /Network up/.test(txt) && /MQTT connected/.test(txt));
  check("master switch row", /Master switch/.test(txt));
  check("legacy timer-rule import banner (mock still has dest1 rule)", /timer rules/.test(txt) && !!btn(/Import and remove/));
  check("no long dash in the card", !txt.includes("—"));

  /* expand the HTTPS row: auth, cert set dropdown with the mock set, first-push switch */
  const carets = [...c.querySelectorAll("button")].filter((b) => b.textContent.trim() === "▸");
  check("one options caret per row", carets.length === 3);
  carets[1].click(); await sleep(150);
  const t2 = card().textContent;
  check("https options: Authentication + Certificate set + First push", /Authentication/.test(t2) && /Certificate set/.test(t2) && /First push/.test(t2));
  const certSel = [...card().querySelectorAll("select")].find((s) => [...s.options].some((o) => /Built-in CA bundle/.test(o.textContent)));
  check("cert set dropdown lists the built-in bundle + the mock set 'homeca' selected", !!certSel && certSel.value === "homeca");
  check("bearer token field shows 'stored' placeholder for a stored secret",
    [...card().querySelectorAll("input[type=password]")].some((i) => /stored/.test(i.placeholder)));
  check("counters line on the expanded row", /last error: http=503/.test(t2) && /backing off 10 s/.test(t2));

  /* expand the ABRP row: token + api key + car model, no Authentication dropdown */
  [...card().querySelectorAll("button")].filter((b) => b.textContent.trim() === "▸")[1].click(); await sleep(150);
  const t3 = card().textContent;
  check("abrp options: user token, API key, placement, car model", /ABRP user token/.test(t3) && /ABRP API key/.test(t3) && /API key placement/.test(t3) && /Car model/.test(t3));
  check("abrp car_model prefilled from the mock", [...card().querySelectorAll("input")].some((i) => i.value === "hyundai:ioniq5:22:77"));

  /* add a destination: a new mqtt row appears expanded, staged for Apply */
  btn(/Add Destination/).click(); await sleep(150);
  check("Add Destination adds a 4th row named dest2 with ~/autopid", rowsOf().length === 4 && rowsOf().some((i) => i.value === "~/autopid") && /Retained/.test(card().textContent));
  const unsaved = [...d().querySelectorAll(".chip,button")].some((x) => /Apply Configuration|Unsaved/.test(x.textContent));
  check("staged edit shows the apply affordance", unsaved);
  /* switching the new row to ABRP pre-selects the api_key query placement and clears the ~/ topic */
  const newRowSel = [...card().querySelectorAll("select")].filter((s) => [...s.options].some((o) => o.textContent === "ABRP")).pop();
  newRowSel.value = "abrp"; newRowSel.dispatchEvent(new w.Event("change")); await sleep(120);
  check("type -> ABRP clears the ~/ topic and shows the token fields", !rowsOf().some((i) => i.value === "~/autopid" && i.placeholder.includes("iternio")) && (card().textContent.match(/ABRP user token/g) || []).length >= 1);

  /* Test on a saved destination hits the mock and toasts the result */
  const tests = [...card().querySelectorAll("button")].filter((b) => b.textContent.trim() === "Test");
  check("Test button per row", tests.length === 4);
  tests[0].click(); await sleep(300);
  const toastTxt = [...d().querySelectorAll(".toast")].map((t) => t.textContent).join(" | ");
  check("Test dest1 toasts 'delivered'", /dest1: delivered/.test(toastTxt));
  tests[3].click(); await sleep(300);
  const toast2 = [...d().querySelectorAll(".toast")].map((t) => t.textContent).join(" | ");
  check("Test on an unsaved row explains apply+restart", /Apply Configuration and restart first/.test(toast2));

  /* delete a row */
  const trash = [...card().querySelectorAll("button.danger")];
  trash[trash.length - 1].click(); await sleep(120);
  check("delete removes the row", rowsOf().length === 3);

  /* the legacy import converts dest1 rule into a row and removes the banner */
  btn(/Import and remove/).click(); await sleep(150);
  check("import adds the timer-rule destination and drops the banner", rowsOf().length === 4 && !/timer rules/.test(card().textContent));

  if (errs.length) { console.log("JS errors:"); errs.forEach((e) => console.log("  " + e)); process.exitCode = 1; }
  else console.log("no JS errors");
  process.exit(process.exitCode || 0); /* the page keeps a stats poller alive */
})();
