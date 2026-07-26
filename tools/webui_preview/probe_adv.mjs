/* Probe: are advanced settings rows hidden by default? */
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import { JSDOM, VirtualConsole } from "jsdom";

const here = dirname(fileURLToPath(import.meta.url));
const html = readFileSync(join(here, "preview.html"), "utf-8");
const vc = new VirtualConsole();
vc.on("jsdomError", (e) => console.log("ERR " + e.message));

const dom = new JSDOM(html, {
  runScripts: "dangerously", url: "http://wican.local/#/settings",
  pretendToBeVisual: true, virtualConsole: vc,
  beforeParse(w) {
    w.matchMedia = () => ({ matches: false, addListener() {}, addEventListener() {} });
    w.scrollTo = () => {}; w.HTMLCanvasElement.prototype.getContext = () => null;
  },
});
const w = dom.window;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

(async () => {
  await sleep(900);
  const rows = [...w.document.querySelectorAll(".frow[data-key]")];
  console.log("frows with data-key:", rows.length);
  const interesting = ["ap_channel", "ap_hidden", "power_save", "sta_max_retry",
    "hostname", "tx_power_dbm", "silent", "cli", "__advtoggle", "keepalive_s"];
  for (const r of rows) {
    const k = r.dataset.key;
    if (interesting.includes(k))
      console.log(`  ${k}: display='${r.style.display}' visible=${r.style.display !== "none"}`);
  }
  /* count how many cards even HAVE an adv toggle */
  const togglers = rows.filter((r) => r.dataset.key === "__advtoggle");
  console.log("advanced toggles present:", togglers.length,
    "of", w.document.querySelectorAll(".section").length, "cards");
  /* Automate page too */
  w.location.hash = "#/automate";
  w.dispatchEvent(new w.Event("hashchange"));
  await sleep(700);
  const rows2 = [...w.document.querySelectorAll(".frow[data-key]")];
  const t2 = rows2.filter((r) => r.dataset.key === "__advtoggle").length;
  const visKeys = rows2.filter((r) => r.style.display !== "none").map((r) => r.dataset.key);
  console.log("automate frows:", rows2.length, "adv toggles:", t2);
  console.log("automate visible keys:", visKeys.join(","));
  process.exit(0);
})();
