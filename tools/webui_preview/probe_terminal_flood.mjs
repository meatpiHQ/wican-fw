/* Probe (2026-11-22, components pull request 12, GitHub issue #1085): the Terminal page keeps
   up with a monitor on a busy bus. `ATMA` on a 2012 Chevrolet Volt's HS bus (about 2,800 frames
   a second) froze the browser tab: every WebSocket message became a text node of its own and a
   forced layout, and the page kept all of them. Now the output is one text node, written at
   most once a screen frame and capped at the last 200,000 characters behind an
   "[earlier output dropped]" line.

   A freeze is a matter of layout, which jsdom does not do: this probe drives the page in Chrome
   (playwright, as can_filters_page.mjs does). The page is preview.html, its /ws/obd socket fed
   by the probe at a fixed message rate, one frame line a message.

   node probe_terminal_flood.mjs [--page <preview.html>] [--secs 20] [--rate 2800]
   ->  TERMINAL FLOOD PROBE PASS */
import { createRequire } from "node:module";
import { dirname, join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const { chromium } = createRequire(join(here, "package.json"))("playwright");
const arg = (k, d) => { const i = process.argv.indexOf(k); return i > 0 && process.argv[i + 1] ? process.argv[i + 1] : d; };
const PAGE = arg("--page", join(here, "preview.html"));
const SECS = Number(arg("--secs", 20)), RATE = Number(arg("--rate", 2800));
const KEEP = 200000, MARK = "[earlier output dropped]\n";
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const within = (p, ms) => Promise.race([p, sleep(ms).then(() => "TIMEOUT")]);
let fails = 0;
const check = (n, ok, extra) => { console.log((ok ? "PASS " : "FAIL ") + n + (extra !== undefined ? "  [" + JSON.stringify(extra) + "]" : "")); if (!ok) { fails++; process.exitCode = 1; } };

const browser = await chromium.launch({ channel: "chrome" });
const page = await (await browser.newContext({ viewport: { width: 1280, height: 900 } })).newPage();
const errs = [];
page.on("pageerror", (e) => errs.push(String(e).slice(0, 200)));
await page.goto(pathToFileURL(PAGE).href + "#/terminal");
await page.waitForSelector("pre.term-body", { timeout: 15000 });

/* the mock's /ws/obd socket stays quiet: keep a hand on it, choose ELM327 and connect */
await page.evaluate(() => {
  const Base = window.WebSocket;
  window.WebSocket = class extends Base {
    _start() { if (this.url.includes("/ws/obd")) window.__ws = this; else super._start(); }
    send(d) { (window.__sent = window.__sent || []).push(String(d)); }
  };
  const sel = [...document.querySelectorAll("#view select")].find((s) => [...s.options].some((o) => o.value === "elm327"));
  sel.value = "elm327"; sel.dispatchEvent(new Event("change"));
  const btn = [...document.querySelectorAll("#view button")].find((b) => b.textContent === "Connect");
  btn.disabled = false; btn.click();
});
const connected = await within(page.waitForFunction(() => /\[connected \/ws\/obd\]/.test(document.querySelector("pre.term-body").textContent), null, { timeout: 5000 }).then(() => true, () => false), 6000);
check("the ELM327 terminal connects and says so", connected === true);

/* the flood: RATE messages a second, one frame line each, for SECS seconds. A turn of the
   page's timer sends what is due, a second's worth at most, as a socket would deliver it */
await page.evaluate(({ RATE }) => {
  const st = (window.__st = { t0: performance.now(), last: performance.now(), maxGap: 0, sent: 0, n: 0, stop: false });
  const enc = new TextEncoder();
  const hex = (v) => v.toString(16).toUpperCase().padStart(2, "0");
  const frame = () => { st.n++; let s = "7E" + (st.n % 8) + " 08"; for (let i = 0; i < 8; i++) s += " " + hex((st.n * 7 + i * 13) & 255); return enc.encode(s + "\r").buffer; };
  setInterval(() => { const n = performance.now(); if (n - st.last > st.maxGap) st.maxGap = n - st.last; st.last = n; }, 50);
  setInterval(() => {
    const ws = window.__ws; if (st.stop || !ws || !ws.onmessage) return;
    const due = Math.floor((performance.now() - st.t0) * RATE / 1000) - st.sent;
    const n = Math.min(due, RATE);
    for (let i = 0; i < n; i++) ws.onmessage({ data: frame() });
    st.sent += n;
  }, 10);
}, { RATE });

/* once a second, from outside: how long the page takes to answer (what a click would wait) */
const answers = [];
const t0 = Date.now();
let dead = false;
while (Date.now() - t0 < SECS * 1000) {
  await sleep(1000);
  const a = Date.now();
  if (await within(page.evaluate(() => 1), 30000) === "TIMEOUT") { dead = true; answers.push(30000); break; }
  answers.push(Date.now() - a);
}
answers.sort((a, b) => a - b);
const worst = answers[answers.length - 1], median = answers[answers.length >> 1];
check("the page answers while the monitor runs (the worst of " + answers.length + " answers under 500 ms)", !dead && worst < 500, { median_ms: median, worst_ms: worst });

const end = dead ? "TIMEOUT" : await within(page.evaluate(({ RATE }) => {
  const st = window.__st; st.stop = true;
  const out = document.querySelector("pre.term-body");
  const ran = (performance.now() - st.t0) / 1000;
  return { ran_s: +ran.toFixed(1), due: Math.floor(ran * RATE), taken: st.sent, timer_max_gap_ms: Math.round(st.maxGap) };
}, { RATE }), 60000);
if (end === "TIMEOUT") {
  check("the page gave its numbers at the end", false, "no answer in 60 s");
} else {
  check("it took the messages as they came (98 % of those due)", end.taken >= end.due * 0.98, end);
  check("no stall of the page's own timer over 500 ms", end.timer_max_gap_ms < 500, end.timer_max_gap_ms);
  await sleep(300);   /* the last screen frame */
  const held = await page.evaluate(() => { const out = document.querySelector("pre.term-body"); const t = out.textContent; return { nodes: out.childNodes.length, chars: t.length, head: t.slice(0, 60), tail: t.slice(-40) }; });
  check("the output is one text node however much arrived (the element holds 3 nodes at most)", held.nodes <= 3, held.nodes);
  check("it holds the last " + KEEP + " characters at most, behind the line that says output was dropped",
    held.chars <= KEEP + MARK.length + 2 && held.chars > KEEP * 0.9 && held.head.startsWith(MARK), { chars: held.chars, head: held.head.slice(0, 30) });
  const firstLine = held.head.slice(MARK.length).split("\n")[0];
  check("the cut is at a line: the first line kept and the last one are whole frames",
    /^7E[0-7] 08( [0-9A-F]{2}){8}$/.test(firstLine) && /7E[0-7] 08( [0-9A-F]{2}){8}\n.?$/.test(held.tail), { first: firstLine, tail: held.tail });
  /* a hidden tab draws no frames: what waits must be capped too. No frame is given to the
     page while 50,000 lines arrive, then one */
  const hidden = await within(page.evaluate(() => {
    const real = window.requestAnimationFrame; let heldCb = null;
    window.requestAnimationFrame = (cb) => { heldCb = cb; return 1; };
    const enc = new TextEncoder();
    for (let i = 0; i < 50000; i++) window.__ws.onmessage({ data: enc.encode("7E8 08 00 11 22 33 44 55 66 77\r").buffer });
    window.__ws.onmessage({ data: enc.encode("LAST LINE\r").buffer });
    window.requestAnimationFrame = real;
    const out = document.querySelector("pre.term-body");
    const before = out.textContent.length;
    if (heldCb) heldCb(performance.now());
    const t = out.textContent;
    return { held_back: !!heldCb, before, chars: t.length, marked: t.startsWith("[earlier output dropped]\n"), last: /LAST LINE\n.?$/.test(t.slice(-12)) };
  }), 60000);
  check("with no screen frame for 50,000 lines (a hidden tab) the page still holds the cap, and the newest line is there when a frame comes",
    hidden !== "TIMEOUT" && hidden.held_back && hidden.chars <= KEEP + MARK.length + 2 && hidden.marked && hidden.last, hidden);
  /* a typed command still goes out and shows (not asked of a page that no longer answers) */
  const typed = hidden === "TIMEOUT" ? "TIMEOUT" : await within(page.evaluate(async () => {
    const input = document.querySelector("#view .term-in input");
    input.value = "ATRV"; input.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true }));
    await new Promise((r) => setTimeout(r, 200));
    return { sent: (window.__sent || []).slice(-1)[0], shown: /ATRV\n.?$/.test(document.querySelector("pre.term-body").textContent.slice(-8)) };
  }), 30000);
  check("a command typed after it goes to the socket and shows in the output", typed !== "TIMEOUT" && typed.sent === "ATRV\r" && typed.shown, typed);
}
check("no script error on the page", errs.length === 0, errs.slice(0, 2));
/* a page that hangs keeps its browser from closing: do not wait for that */
await within(browser.close().catch(() => {}), 10000);
setTimeout(() => process.exit(process.exitCode || 0), 500).unref();
console.log(fails ? "TERMINAL FLOOD PROBE FAIL (" + fails + ")" : "TERMINAL FLOOD PROBE PASS");
