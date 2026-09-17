/* Probe: the Rules & Events page's rule builder (2026-09-17): sentence rows
 * with runtime badges, Templates, the Trigger / Only if / Then form with live
 * value conditions and undo, the preview sentence, Edit as JSON round trip,
 * Add staging the rule (JSON shape: when[].value + undo), duplicate, edit.
 * Prints RULES PROBE PASS|FAIL. */
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
  runScripts: "dangerously", url: "http://wican.local/#/events",
  pretendToBeVisual: true, virtualConsole: vc,
  beforeParse(w) {
    w.matchMedia = () => ({ matches: false, addListener() {}, addEventListener() {} });
    w.scrollTo = () => {}; w.HTMLCanvasElement.prototype.getContext = () => null;
  },
});
const w = dom.window, d = () => w.document;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const fails = [];
const check = (name, ok, extra = "") => { console.log((ok ? "PASS: " : "FAIL: ") + name + (extra ? "  " + extra : "")); if (!ok) fails.push(name); };
const modalEl = () => d().querySelector("#modal-root");
const mtext = () => (modalEl() || {}).textContent || "";
const rows = () => [...d().querySelectorAll(".rl-row")];
const sel = (label) => { const lab = [...modalEl().querySelectorAll(".rb-grid > label")].find((l) => l.textContent.trim() === label); return lab && lab.nextElementSibling.querySelector("select, input, textarea"); };
const setSel = (el, v) => { el.value = v; el.dispatchEvent(new w.Event("change")); };
const btn = (re, root) => [...(root || d()).querySelectorAll("button")].find((b) => re.test(b.textContent));
const preview = () => (modalEl().querySelector(".preview") || {}).textContent || "";
let staged = 0;
const origFetch = w.fetch;
w.fetch = (u, o) => origFetch(u, o);

(async () => {
  await sleep(1200);
  const n0 = rows().length;
  check("rules render as sentence rows", n0 >= 2 && rows().every((r) => r.querySelector(".rl-s") && r.querySelector(".rl-t")), "rows=" + n0);
  check("technical line keeps name and auto tag", rows().some((r) => /dest1/.test(r.querySelector(".rl-t").textContent) && /·auto/.test(r.querySelector(".rl-t").textContent)));
  check("runtime badge from /api/events/rules", rows().some((r) => /fired 3×/.test((r.querySelector(".badge") || {}).textContent || "")) &&
    rows().some((r) => /never fired/.test((r.querySelector(".badge") || {}).textContent || "")));
  check("each row has edit, duplicate, delete", rows().every((r) => r.querySelectorAll(".rl-acts button").length === 3));
  /* templates */
  btn(/Templates/).click(); await sleep(50);
  const menu = d().querySelector(".menu");
  check("Templates menu opens with the starters + blank", !!menu && menu.querySelectorAll(".mi").length >= 4 && /Blank rule/.test(menu.textContent));
  [...menu.querySelectorAll(".mi")].find((b) => /while charging/.test(b.textContent)).click(); await sleep(150);
  check("charging template opens the builder prefilled", /Add rule/.test(mtext()) && sel("Name").value === "charge_poll" &&
    /Trigger/.test(mtext()) && /Only if/.test(mtext()) && /Then/.test(mtext()));
  const trig = sel("Trigger"), act = sel("Action");
  check("trigger + action read back into the vocabulary", trig.value === "param" && act.value === "enable" && !!sel("Parameter") && !!sel("Group"));
  check("one condition row: it is 1", modalEl().querySelectorAll(".cond select").length === 2 && /it is 1/.test(preview()));
  check("undo on: preview says disable it again", /disable it again when that stops being true/.test(preview()) && /Undo when it stops being true/.test(mtext()));
  /* add a live-value condition */
  btn(/Add condition/, modalEl()).click(); await sleep(80);
  const fieldSels = [...modalEl().querySelectorAll(".cond .fld select")];
  check("second condition row added", fieldSels.length === 2);
  const liveOpt = [...fieldSels[1].options].find((o) => /\(live\)/.test(o.textContent) && /^v:autopid\./.test(o.value));
  check("live values listed (autopid parameters expanded)", !!liveOpt, liveOpt && liveOpt.textContent);
  setSel(fieldSels[1], liveOpt.value); await sleep(80);
  const ops = [...modalEl().querySelectorAll(".cond > select")];
  setSel(ops[1], ">"); await sleep(80);
  const vals = [...modalEl().querySelectorAll(".cond > input")];
  vals[1].value = "20"; vals[1].dispatchEvent(new w.Event("change")); await sleep(50);
  const liveName = liveOpt.value.slice(2).replace(/^autopid\./, "");
  check("live condition shows the LIVE badge and reads in the preview", !!modalEl().querySelector(".cond .fld .live") && new RegExp(liveName + " is above 20").test(preview()), preview());
  /* JSON round trip */
  btn(/Edit as JSON/, modalEl()).click(); await sleep(80);
  const ta = modalEl().querySelector("textarea");
  check("Edit as JSON shows the engine shape (when[].value + undo)", !!ta && /"value": "\$\{autopid\./.test(ta.value) && /"undo": true/.test(ta.value) && /"match"/.test(ta.value));
  btn(/Back to the form/, modalEl()).click(); await sleep(80);
  check("back to the form keeps both conditions", modalEl().querySelectorAll(".cond .fld select").length === 2);
  /* add it */
  btn(/^Add rule$/, modalEl()).click(); await sleep(150);
  check("Add closes the modal and stages the rule", !modalEl().querySelector(".modal") && rows().length === n0 + 1 && /Submit Changes \(1\)/.test(d().querySelector("#submitlabel").textContent));
  const newRow = rows().find((r) => /charge_poll/.test(r.querySelector(".rl-t").textContent));
  check("new rule reads as a sentence with the undo tail", !!newRow && /enable the/.test(newRow.textContent) && /disable it again/.test(newRow.textContent) && /· undo/.test(newRow.querySelector(".rl-t").textContent));
  /* WiFi template */
  btn(/Templates/).click(); await sleep(50);
  [...d().querySelectorAll(".menu .mi")].find((b) => /at home/.test(b.textContent)).click(); await sleep(150);
  check("WiFi template: trigger, network picker, rate action, undo", sel("Trigger").value === "wifi_up" && !!sel("Network") && sel("Action").value === "rate" &&
    /poll the default group every 10 s/.test(preview()) && /back to the configured rate/.test(preview()));
  const perNum = sel("Poll every");
  perNum.value = "30"; perNum.dispatchEvent(new w.Event("change")); await sleep(50);
  check("rate edits reach the preview", /every 30 s/.test(preview()));
  btn(/^Add rule$/, modalEl()).click(); await sleep(150);
  check("second rule staged", rows().length === n0 + 2 && rows().some((r) => /home_slow/.test(r.querySelector(".rl-t").textContent) && /wifi\.sta/.test(r.querySelector(".rl-t").textContent)));
  /* edit + duplicate */
  rows()[0].querySelector(".rl-acts button[title='Edit']").click(); await sleep(120);
  check("edit opens the builder with the name locked", /Edit rule:/.test(mtext()) && sel("Name").disabled);
  btn(/^Cancel$/, modalEl()).click(); await sleep(80);
  rows()[0].querySelector(".rl-acts button[title='Duplicate']").click(); await sleep(120);
  check("duplicate opens a copy with a new name", /Add rule/.test(mtext()) && /_2$/.test(sel("Name").value));
  btn(/^Cancel$/, modalEl()).click(); await sleep(80);
  /* validation: a bad name is refused */
  btn(/Add Rule/).click(); await sleep(120);
  const nm = sel("Name"); nm.value = "bad name!"; nm.dispatchEvent(new w.Event("change")); await sleep(30);
  btn(/^Add rule$/, modalEl()).click(); await sleep(80);
  check("bad name refused, modal stays", !!modalEl().querySelector(".modal") && /Name: letters/.test(d().body.textContent));
  btn(/^Cancel$/, modalEl()).click(); await sleep(50);
  check("no jsdom errors", errors.length === 0, errors.join(" | "));
  console.log("RULES PROBE " + (fails.length ? "FAIL: " + fails.join(", ") : "PASS"));
  process.exit(fails.length ? 1 : 0);
})();
