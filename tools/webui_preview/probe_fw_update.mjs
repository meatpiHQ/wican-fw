/* Probe (2026-10-18, internal/TASK_fw_update.md): the Updates card of
   System > Firmware update over the mock's /api/fw_update, scene by scene:
   an update available (the row, Release notes rendered from the proxied
   markdown, Update posts the install, the job's bar and Cancel, the
   reboot hand-over), up to date, a stepping stone, a wipe verdict (the
   dialog first, confirm_wipe on the POST), a serial verdict (Needs a USB
   flash, no Update), the mobile uplink (downloads wait for WiFi: no
   dialog, both buttons disabled), the dongle row (an update through the
   relay to verified, a failed last job, the USB flash case, no dongle),
   a rolled-back last result with Try again, a withdrawn installed release,
   no manifest yet, Check now, the settings form with its labels, and the
   About page's build row. Prints `FW UPDATE PROBE PASS`. */
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import { JSDOM, VirtualConsole } from "jsdom";

const here = dirname(fileURLToPath(import.meta.url));
const html = readFileSync(join(here, "preview.html"), "utf-8");
let fails = 0;
let checks = 0;
const check = (n, ok, extra) => { checks++; console.log((ok ? "PASS " : "FAIL ") + n + (extra !== undefined ? "  [" + JSON.stringify(extra) + "]" : "")); if (!ok) { fails++; process.exitCode = 1; } };
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function boot(hash, preset) {
  const errs = [];
  const vc = new VirtualConsole();
  vc.on("jsdomError", (e) => errs.push(e.message + " " + (e.detail && e.detail.stack || "").slice(0, 300)));
  const dom = new JSDOM(html, {
    runScripts: "dangerously", url: "http://wican.local/" + hash,
    pretendToBeVisual: true, virtualConsole: vc,
    beforeParse(w) {
      w.__mockPreset = preset;
      w.matchMedia = () => ({ matches: false, addListener() {}, addEventListener() {} });
      w.scrollTo = () => {}; w.HTMLCanvasElement.prototype.getContext = () => null;
      w.open = () => null;
      if (!w.TextEncoder) { w.TextEncoder = TextEncoder; w.TextDecoder = TextDecoder; }
    },
  });
  const w = dom.window, d = () => w.document;
  const $ = (sel) => d().querySelector(sel), $$ = (sel, root) => [...(root || d()).querySelectorAll(sel)];
  const click = (el) => el && el.dispatchEvent(new w.MouseEvent("click", { bubbles: true, cancelable: true }));
  const modal = () => $("#modal-root .modal");
  const modalBtn = (text) => $$("#modal-root .modal .acts button").find((b) => b.textContent.trim() === text);
  const btn = (root, text) => $$("button", root).find((b) => b.textContent.trim().includes(text));
  const state = () => w.__mockFw();
  return { w, d, $, $$, click, modal, modalBtn, btn, state, errs, close: () => dom.window.close() };
}

async function openCard(preset) {
  const p = boot("#/system/firmware", preset);
  await sleep(1800);
  const card = p.$("#fwu-card");
  return { p, card };
}

(async () => {
  /* ---- an update available ---- */
  {
    const { p, card } = await openCard({ fw: "available" });
    check("available: the Updates card renders ahead of Manual update", !!card && !!p.$$("#view .section").find((c) => (c.querySelector(".legend h3") || {}).textContent.includes("Manual update")));
    const row = card && card.querySelector(".fwu-row");
    check("available: the WiCAN PRO row shows the installed version", !!row && row.textContent.includes("WiCAN PRO") && row.textContent.includes("6.1.0-alfa.1"));
    check("available: the second line names the new version and its summary", !!row && /6\.1\.0-alfa\.2 available, 2026-10-24, The device checks/.test(row.textContent));
    check("available: the ESPNetLink row shows the dongle up to date over USB", /ESPNetLink/.test((card.querySelector(".fwu-dongle") || {}).textContent || "") && /v1\.22-61-gcc81591/.test(card.querySelector(".fwu-dongle").textContent) && /USB/.test(card.querySelector(".fwu-dongle").textContent) && /Up to date/.test(card.querySelector(".fwu-dongle").textContent));
    check("available: the intro says downloads go over WiFi only", /WiFi only/.test(card.textContent));
    check("available: checked N ago in the legend tools", /checked 2 h ago/.test((card.querySelector(".fwu-checked") || {}).textContent || ""));
    check("available: the channel is named", /channel alfa/.test(card.textContent));
    /* the release notes modal */
    p.click(card.querySelector(".fwu-notes-btn"));
    await sleep(600);
    const m = p.modal();
    check("notes: the modal renders the markdown (heading, list, bold, code, link)", !!m && !!m.querySelector("h3") && m.querySelectorAll("li").length === 3 && !!m.querySelector("b") && !!m.querySelector("code") && !!m.querySelector("a[href^='https://github.com/meatpiHQ/meatpi-devices']"));
    check("notes: the Open on GitHub and Close buttons", !!p.modalBtn("Open on GitHub") && !!p.modalBtn("Close"));
    p.click(p.modalBtn("Close"));
    await sleep(100);
    /* Update: a plain install, no dialog */
    p.click(card.querySelector(".fwu-update"));
    await sleep(400);
    const inst = p.state().installs;
    check("update: POST /api/fw_update/install with the target version, no flags", inst.length === 1 && inst[0].target === "self" && inst[0].version === "6.1.0-alfa.2" && !inst[0].confirm_wipe && !("allow_cellular" in inst[0]), inst);
    await sleep(2300);
    const prog = card.querySelector(".fwu-progress");
    check("update: the job's bar and phase show while it runs", !!prog && prog.style.display !== "none" && /Downloading|Verifying|Checking/.test(prog.textContent) && !!prog.querySelector("i"));
    check("update: Cancel is offered while the job runs", !!card.querySelector(".fwu-cancel"));
    check("update: the Update button is disabled meanwhile", (card.querySelector(".fwu-update") || { disabled: true }).disabled === true);
    await sleep(2200);
    check("update: rebooting hands over to the reconnect screen", p.w.eval("conn.state") === "reboot", p.w.eval("conn.state"));
    check("available: no jsdom errors", p.errs.length === 0, p.errs.slice(0, 2));
    p.close();
  }

  /* ---- up to date ---- */
  {
    const { p, card } = await openCard({ fw: "up_to_date" });
    const row = card.querySelector(".fwu-row");
    check("up_to_date: the row says Up to date, no Update button", /Up to date/.test(row.textContent) && !card.querySelector(".fwu-update"));
    check("up_to_date: Release notes still offered for the installed release", !!card.querySelector(".fwu-notes-btn"));
    p.close();
  }

  /* ---- a stepping stone ---- */
  {
    const { p, card } = await openCard({ fw: "step" });
    check("step: the row says more updates follow", /first step: more updates follow/.test(card.querySelector(".fwu-row").textContent));
    check("step: the channel word is beta", /channel beta/.test(card.textContent));
    p.close();
  }

  /* ---- a wipe verdict ---- */
  {
    const { p, card } = await openCard({ fw: "wipe" });
    check("wipe: the row warns what the layout erases", /erases settings, storage/.test(card.querySelector(".fwu-row").textContent));
    p.click(card.querySelector(".fwu-update"));
    await sleep(300);
    const m = p.modal();
    check("wipe: the dialog names the erased partitions and the backup step", !!m && /erases data/.test(m.textContent) && /settings/.test(m.textContent) && /stored files, logs/.test(m.textContent) && /Configuration backup/.test(m.textContent));
    check("wipe: nothing was posted before the confirmation", p.state().installs.length === 0);
    p.click(p.modalBtn("Install and erase"));
    await sleep(400);
    const inst = p.state().installs;
    check("wipe: the POST carries confirm_wipe", inst.length === 1 && inst[0].confirm_wipe === true && inst[0].version === "6.2.0-alfa.1", inst);
    p.close();
  }

  /* ---- a serial verdict ---- */
  {
    const { p, card } = await openCard({ fw: "serial" });
    check("serial: Needs a USB flash instead of Update", !card.querySelector(".fwu-update") && !!card.querySelector(".fwu-usb") && /Needs a USB flash/.test(card.querySelector(".fwu-usb").textContent));
    check("serial: the link points at the docs", /meatpihq\.github\.io/.test(card.querySelector(".fwu-usb").href));
    p.close();
  }

  /* ---- the mobile uplink ---- */
  {
    const { p, card } = await openCard({ fw: "cellular" });
    check("cellular: the banner says downloads wait for WiFi", !!card.querySelector(".banner.warn") && /downloads wait until the device is on a WiFi network/.test(card.querySelector(".banner.warn").textContent));
    check("cellular: both Update buttons are disabled with the reason", (card.querySelector(".fwu-update") || {}).disabled === true && /WiFi/.test((card.querySelector(".fwu-update") || {}).title || "") && (card.querySelector(".fwu-dongle-update") || {}).disabled === true);
    card.querySelector(".fwu-update").click(); /* a browser click: a disabled button fires nothing */
    await sleep(300);
    check("cellular: nothing was posted and no dialog opened", p.state().installs.length === 0 && !p.modal());
    p.close();
  }

  /* ---- the dongle: an update available, the dialog, the job to verified ---- */
  {
    const { p, card } = await openCard({ fw: "dongle_available" });
    const row = card.querySelector(".fwu-dongle");
    check("dongle: the row names the new version and its summary", !!row && /1\.23\.0-alfa\.1 available, 2026-10-24, First build/.test(row.textContent));
    check("dongle: Release notes and Update dongle at the right edge", !!row.querySelector(".fwu-dongle-notes") && !!row.querySelector(".fwu-dongle-update"));
    p.click(row.querySelector(".fwu-dongle-update"));
    await sleep(300);
    const m = p.modal();
    check("dongle: the dialog says how the image travels and what pauses", !!m && /sends it to the dongle over the USB cable/.test(m.textContent) && /internet through it pauses/.test(m.textContent));
    check("dongle: nothing posted before the confirmation", p.state().installs.length === 0);
    p.click(p.modalBtn("Update the dongle"));
    await sleep(2400); /* the card polls every 2 s */
    const inst = p.state().installs;
    check("dongle: POST install target dongle with the version", inst.length === 1 && inst[0].target === "dongle" && inst[0].version === "1.23.0-alfa.1", inst);
    const prog = card.querySelector(".fwu-progress");
    check("dongle: the job words name the dongle", !!prog && /dongle/i.test(prog.textContent) && !!card.querySelector(".fwu-cancel"));
    await sleep(2000);
    check("dongle: the push phase names the transport and a percentage", /Sending to the dongle over USB, \d+ %/.test(card.querySelector(".fwu-progress").textContent));
    check("dongle: no Cancel once the push started", !card.querySelector(".fwu-cancel"));
    await sleep(4200);
    check("dongle: the row reports the verified update", /Dongle updated to 1\.23\.0-alfa\.1/.test(card.textContent) && /sent in 21 s/.test(card.textContent));
    check("dongle: the WiCAN never went to the reconnect screen", p.w.eval("conn.state") !== "reboot", p.w.eval("conn.state"));
    check("dongle: no jsdom errors", p.errs.length === 0, p.errs.slice(0, 2));
    p.close();
  }

  /* ---- the dongle: a failed last job, the USB flash case, no dongle ---- */
  {
    const { p, card } = await openCard({ fw: "dongle_failed" });
    const b = card.querySelector(".fwu-result .banner.crit");
    check("dongle_failed: the danger banner names the cause and offers Try again", !!b && /did not come back after the update/.test(b.textContent) && !!card.querySelector(".fwu-dongle-retry"));
    p.close();
    const u = await openCard({ fw: "dongle_usb_flash" });
    const urow = u.card.querySelector(".fwu-dongle");
    check("dongle_usb_flash: the row explains the one-time USB flash and links the docs", /cannot be updated from the WiCAN PRO/.test(urow.textContent) && !!urow.querySelector(".fwu-dongle-usb") && !urow.querySelector(".fwu-dongle-update"));
    u.p.close();
    const n = await openCard({ fw: "dongle_absent" });
    check("dongle_absent: the row says no dongle is connected, no buttons", /No ESPNetLink dongle is connected/.test(n.card.querySelector(".fwu-dongle").textContent) && !n.card.querySelector(".fwu-dongle-update"));
    n.p.close();
  }

  /* ---- rolled back ---- */
  {
    const { p, card } = await openCard({ fw: "rolled_back" });
    const b = card.querySelector(".fwu-result .banner");
    check("rolled_back: the danger banner names both versions", !!b && b.classList.contains("crit") && /Rolled back to 6\.1\.0-alfa\.1/.test(b.textContent) && /6\.1\.0-alfa\.2 did not run well/.test(b.textContent));
    check("rolled_back: Try again is offered", !!card.querySelector(".fwu-retry"));
    p.close();
  }

  /* ---- a withdrawn installed release ---- */
  {
    const { p, card } = await openCard({ fw: "yanked" });
    const row = card.querySelector(".fwu-row");
    check("yanked: the withdrawn chip and the reason", /withdrawn/.test(row.textContent) && /boot loop on units with a card/.test(row.textContent));
    check("yanked: the lower target is offered as an update", !!card.querySelector(".fwu-update") && /6\.1\.0-alfa\.0 available/.test(row.textContent));
    p.close();
  }

  /* ---- no manifest yet, then Check now ---- */
  {
    const { p, card } = await openCard({ fw: "no_manifest" });
    check("no_manifest: not checked yet, Not checked yet", /not checked yet/.test(card.querySelector(".fwu-checked").textContent) && /Not checked yet/.test(card.querySelector(".fwu-row").textContent));
    p.click(card.querySelector(".fwu-check"));
    await sleep(300);
    check("check now: POST /api/fw_update/check went out", p.state().checks === 1);
    /* the settings form */
    const rows = p.$$("#fwu-card .frow");
    const labels = rows.map((r) => (r.querySelector("label") || {}).textContent || "");
    check("settings: the form carries the fw_update rows with their labels", labels.includes("Check for updates automatically") && labels.includes("Release channel") && labels.includes("Check every (hours)") && !labels.includes("Over a mobile connection"), labels);
    const sel = rows.find((r) => (r.querySelector("label") || {}).textContent === "Release channel");
    const opts = sel ? [...sel.querySelectorAll("option")].map((o) => o.textContent) : [];
    check("settings: the channel enum is worded", opts.includes("Stable") && opts.includes("Beta (release candidates)") && opts.includes("Alfa (every test build)"), opts);
    check("settings: base_url and cert_set are advanced (hidden until Show advanced)", !labels.includes("Release list address") && !!p.btn(p.$("#fwu-card"), "Show advanced"));
    p.close();
  }

  /* ---- the About page ---- */
  {
    const p = boot("#/about", {});
    await sleep(1200);
    const dts = p.$$("#view dt").map((e) => e.textContent);
    check("about: the Build row sits under the version", dts.indexOf("Build") === dts.indexOf("Firmware version") + 1, dts);
    check("about: the Partition row", dts.includes("Partition"));
    p.close();
  }

  console.log(fails ? `FW UPDATE PROBE FAIL (${fails} of ${checks})` : `FW UPDATE PROBE PASS (${checks} checks)`);
  process.exit(fails ? 1 : 0);
})();
