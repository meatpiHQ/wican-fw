
import { readFileSync } from "node:fs";
import { JSDOM, VirtualConsole } from "jsdom";
const html = readFileSync("preview.html", "utf-8");
const vc = new VirtualConsole(); vc.on("jsdomError", e=>console.log("ERR", e.message));
const dom = new JSDOM(html, { runScripts: "dangerously", url: "http://wican.local/#/automate",
  pretendToBeVisual: true, virtualConsole: vc,
  beforeParse(w){ w.matchMedia=()=>({matches:false,addListener(){},addEventListener(){}});
    w.scrollTo=()=>{}; w.HTMLCanvasElement.prototype.getContext=()=>null; } });
const w = dom.window, d = () => w.document;
const sleep = ms => new Promise(r=>setTimeout(r,ms));
(async()=>{
  await sleep(900);
  [...d().querySelectorAll(".seg button")].find(b=>b.textContent==="Vehicle Specific").click();
  await sleep(400);
  const sel=[...d().querySelectorAll("select")].find(s=>[...s.options].some(o=>/Ioniq2017/.test(o.textContent)));
  sel.value="Hyundai: Ioniq2017"; sel.dispatchEvent(new w.Event("change")); await sleep(100);
  [...d().querySelectorAll("button")].find(b=>b.textContent==="Load Profile").click();
  await sleep(500);
  console.log([...d().querySelectorAll("input")].map(i=>i.value).filter(Boolean).join(" | "));
  process.exit(0);
})();
