/* Probe: the UDS Tool page (2026-09-16): path badge + Exclusive bus switch
 * from GET /api/uds, and the TERMINAL view — every request (›) and reply
 * (‹) as lines with the decode inline (VIN as text, NRC name), the raw JSON
 * toggle, Clear, persistence in localStorage, a sent line clicking back into
 * the form, the exclusive round-trip (POST /api/uds -> "AutoPID paused"
 * after a request), the settings card with the exclusive boot default.
 * Prints UDS PROBE PASS|FAIL. */
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import { JSDOM, VirtualConsole } from "jsdom";

const here = dirname(fileURLToPath(import.meta.url));
const html = readFileSync(join(here, "preview.html"), "utf-8");
const vc = new VirtualConsole();
const errors = [];
vc.on("jsdomError", (e) => errors.push("jsdomError: " + e.message));

const dom = new JSDOM(html, {
  runScripts: "dangerously", url: "http://wican.local/#/uds",
  pretendToBeVisual: true, virtualConsole: vc,
  beforeParse(w) {
    w.matchMedia = () => ({ matches: false, addListener() {}, addEventListener() {} });
    w.scrollTo = () => {}; w.HTMLCanvasElement.prototype.getContext = () => null;
  },
});
const w = dom.window;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const fails = [];
const check = (name, ok, extra = "") => { console.log((ok ? "PASS: " : "FAIL: ") + name + (extra ? "  " + extra : "")); if (!ok) fails.push(name); };
const text = () => w.document.body.textContent;
const chips = () => [...w.document.querySelectorAll(".chip")].map((c) => c.textContent.trim());
const term = () => w.document.querySelector(".console");
const lines = () => (term() ? [...term().children].map((c) => c.textContent) : []);
const sendBtn = () => [...w.document.querySelectorAll("button")].find((b) => /Send/.test(b.textContent) && b.classList.contains("pri"));
const dataInput = () => [...w.document.querySelectorAll("input.mono")].find((i) => i.placeholder && /hex bytes/.test(i.placeholder));

(async () => {
  await sleep(1200);
  check("page title", /UDS Tool/.test(text()));
  check("path badge from /api/uds", chips().some((c) => /over native CAN/.test(c)), JSON.stringify(chips().slice(0, 4)));
  check("provider chip", chips().some((c) => /provider: native CAN ISO-TP/.test(c)));
  const excl = [...w.document.querySelectorAll("label.switch")].find((l) => /Exclusive bus/.test(l.textContent));
  check("exclusive switch present", !!excl);
  check("timeout labelled P2*", /Final response timeout \(P2\*\)/.test(text()));
  check("29-bit control", /29-bit/.test(text()));
  check("terminal present, empty hint", !!term() && /No requests yet/.test(term().textContent));
  /* send the default request (22 F1 90) */
  check("send button", !!sendBtn());
  sendBtn() && sendBtn().click();
  await sleep(700);
  const L = lines();
  check("terminal: sent line ›", L.some((l) => /›\s+7E0→7E8\s+22 F1 90/.test(l)), JSON.stringify(L.slice(0, 3)));
  check("terminal: reply line ‹ with the VIN as text", L.some((l) => /‹ 62 F1 90/.test(l) && /1WCAN0FW0P0000001/.test(l)));
  check("terminal: decode meta (positive · bytes · ms · path)", L.some((l) => /positive · \d+ B · \d+ ms · isotp/.test(l)));
  check("terminal: no raw JSON by default", !term().querySelector("pre"));
  check("persisted in localStorage", (() => { try { return JSON.parse(w.localStorage.getItem("wican-uds-term") || "[]").length === 1; } catch { return false; } })());
  /* a negative response decodes with its NRC name, in the amber class */
  dataInput().value = "2E F1 90 00";
  sendBtn().click();
  await sleep(700);
  const neg = [...term().children].find((c) => /‹ 7F 2E 31/.test(c.textContent));
  check("terminal: negative reply decoded", !!neg && /NEGATIVE 0x31 requestOutOfRange/.test(neg.textContent) && neg.classList.contains("wn"));
  check("terminal keeps the earlier exchange (scrollable history)", lines().filter((l) => /›/.test(l)).length === 2);
  /* the raw JSON toggle */
  const rawBtn = [...w.document.querySelectorAll("button")].find((b) => /Raw JSON/.test(b.textContent));
  rawBtn && rawBtn.click();
  await sleep(50);
  check("raw JSON toggles on (one pre per reply)", term().querySelectorAll("pre").length === 2 && /"response"/.test(term().querySelector("pre").textContent));
  rawBtn && rawBtn.click();
  await sleep(50);
  check("raw JSON toggles off", !term().querySelector("pre"));
  /* a sent line clicks back into the form */
  const firstSent = [...term().children].find((c) => c.classList.contains("in") && /22 F1 90/.test(c.textContent));
  firstSent && firstSent.click();
  check("sent line loads the request back into the form", dataInput().value === "22 F1 90");
  /* exclusive is ON by default (2026-09-16): the requests above paused AutoPID */
  check("exclusive switch reads on by default", /Exclusive bus: on/.test(excl.textContent));
  check("AutoPID paused chip after a request in exclusive mode", chips().some((c) => /AutoPID paused/.test(c)), JSON.stringify(chips()));
  /* the round-trip: off -> POST -> reads off, AutoPID no longer paused */
  const inp = excl && excl.querySelector("input");
  if (inp) { inp.checked = false; inp.dispatchEvent(new w.Event("change")); }
  await sleep(400);
  const sw2 = [...w.document.querySelectorAll("label.switch")].find((l) => /Exclusive bus/.test(l.textContent));
  check("exclusive switch reads off after POST", !!sw2 && /Exclusive bus: off/.test(sw2.textContent));
  check("AutoPID paused chip gone once exclusive is off", !chips().some((c) => /AutoPID paused/.test(c)));
  /* Clear empties the terminal + the store */
  const clearBtn = [...w.document.querySelectorAll("button")].find((b) => b.textContent.trim() === "Clear");
  clearBtn && clearBtn.click();
  await sleep(50);
  check("Clear empties the terminal", /No requests yet/.test(term().textContent) && w.localStorage.getItem("wican-uds-term") === "[]");
  /* the settings card carries the exclusive boot default */
  const rows = [...w.document.querySelectorAll(".frow[data-key]")].map((r) => r.dataset.key);
  check("settings card: exclusive + backend rows", rows.includes("exclusive") && rows.includes("backend"), JSON.stringify(rows));
  check("no jsdom errors", errors.length === 0, errors.join(" | "));
  console.log("UDS PROBE " + (fails.length ? "FAIL: " + fails.join(", ") : "PASS"));
  process.exit(fails.length ? 1 : 0);
})();
