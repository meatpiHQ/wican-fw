import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import { JSDOM, VirtualConsole } from "jsdom";

const here = dirname(fileURLToPath(import.meta.url));
const html = readFileSync(join(here, "preview.html"), "utf-8");
const vc = new VirtualConsole();
vc.on("jsdomError", (e) => console.log("JSERR:", e.message, (e.detail && e.detail.stack || "").slice(0, 400)));
vc.on("error", (...a) => console.log("CONSOLE.ERR:", a.join(" ").slice(0, 300)));

const dom = new JSDOM(html, {
  runScripts: "dangerously", url: "http://wican.local/#/automate",
  pretendToBeVisual: true, virtualConsole: vc,
  beforeParse(w) {
    w.matchMedia = () => ({ matches: false, addListener() {}, addEventListener() {} });
    w.scrollTo = () => {}; w.HTMLCanvasElement.prototype.getContext = () => null;
  },
});
const w = dom.window, d = () => w.document;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

(async () => {
  await sleep(900);
  const segBtns = [...d().querySelectorAll(".seg button")];
  console.log("tabs:", segBtns.map((b) => b.textContent).join(" | "));
  const vehBtn = segBtns.find((b) => b.textContent === "Vehicle Specific");
  vehBtn.click();
  await sleep(500);
  const carSel = [...d().querySelectorAll("select")].find((s) => [...s.options].some((o) => /Ioniq2017/.test(o.textContent)));
  console.log("carSel found:", !!carSel, carSel && [...carSel.options].map((o) => o.value).join(";").slice(0, 120));
  carSel.value = "Hyundai: Ioniq2017";
  carSel.dispatchEvent(new w.Event("change"));
  await sleep(120);
  const loadBtn = [...d().querySelectorAll("button")].find((b) => b.textContent === "Load Profile");
  console.log("loadBtn disabled:", loadBtn.disabled);
  loadBtn.click();
  await sleep(600);
  console.log("toasts:", [...d().querySelectorAll(".toast")].map((t) => t.textContent).join(" || "));
  const vals = [...d().querySelectorAll("input")].map((i) => i.value).filter((v) => v && /B\d|21\d/.test(v));
  console.log("inputs with B/cmd:", vals.slice(0, 20).join(", "));
  /* events page */
  w.location.hash = "#/events";
  w.dispatchEvent(new w.Event("hashchange"));
  await sleep(900);
  console.log("events body head:", d().querySelector("#view").textContent.slice(0, 400).replace(/\s+/g, " "));
  process.exit(0);
})();
