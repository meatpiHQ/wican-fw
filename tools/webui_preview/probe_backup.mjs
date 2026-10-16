/* Probe (2026-10-11, issue #979): the configuration backup on System > Maintenance.
   Three cards from the on-demand chunk web/backup.js over the mock's /api/backup:
   the downloadable file (five parts with ticks, the certificates row never
   tickable, Remove sensitive data, Encrypt the file gated on a downloaded key),
   the copies on the device (internal flash and SD card rows with Restore, the
   refresh, the auto_copy setting row, the key row), the restore preview in its
   states (a plain file of this device, a locked file of another device and its
   unlock, a redacted file, a newer file refused, the copy on the flash). Three
   boots: a device with a card, one without and with the copies off, and
   (2026-10-16) one whose copy holds the settings from before a factory reset
   (issue #987: the row, the refresh that asks first) and whose databases part
   leaves a file out (issue #989: the note under the row and in the preview).
   Prints `BACKUP PROBE PASS`. */
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import { JSDOM, VirtualConsole } from "jsdom";

const here = dirname(fileURLToPath(import.meta.url));
const html = readFileSync(join(here, "preview.html"), "utf-8");
let fails = 0;
const check = (n, ok, extra) => { console.log((ok ? "PASS " : "FAIL ") + n + (extra !== undefined ? "  [" + JSON.stringify(extra) + "]" : "")); if (!ok) { fails++; process.exitCode = 1; } };
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
      if (!w.TextEncoder) { w.TextEncoder = TextEncoder; w.TextDecoder = TextDecoder; }
    },
  });
  const w = dom.window, d = () => w.document;
  const $ = (sel) => d().querySelector(sel), $$ = (sel, root) => [...(root || d()).querySelectorAll(sel)];
  const cardByTitle = (t) => $$("#view .section").find((c) => (c.querySelector(".legend, h3") || {}).textContent.trim().startsWith(t));
  const go = async (h) => { w.location.hash = h; await sleep(1200); };
  const modal = () => $("#modal-root .modal");
  const closeModal = () => { const r = $("#modal-root"); if (r) { r.classList.remove("on"); r.replaceChildren(); } };
  const rowOf = (root, part) => $$(".bk-row", root).find((r) => r.dataset.part === part);
  const click = (el) => el && el.dispatchEvent(new w.MouseEvent("click", { bubbles: true, cancelable: true }));
  const flip = (input) => { input.checked = !input.checked; input.dispatchEvent(new w.Event("change", { bubbles: true })); };
  return { w, d, $, $$, cardByTitle, go, modal, closeModal, rowOf, click, flip, errs, close: () => dom.window.close() };
}

(async () => {
  /* ---- boot 1: a device with a card, the copies kept up to date, the key never downloaded ---- */
  {
    const p = boot("#/system/maintenance", { backup: { keyDownloaded: false, sd: true, copies: true } });
    await sleep(2200);
    const bk = p.$("#bk-backup-card"), cp = p.$("#bk-copies-card"), rs = p.$("#bk-restore-card"), maint = p.cardByTitle("Maintenance");
    check("the three cards render", !!bk && !!cp && !!rs);
    check("they sit ahead of Maintenance", !!maint && !!bk && (bk.compareDocumentPosition(maint) & 4) !== 0);
    check("the old Download Config and Upload Config buttons are gone", !p.$$("#view a, #view label").some((e) => /Download Config|Upload Config/.test(e.textContent)));
    check("Maintenance keeps Reboot, Restart History, Quick Setup, Factory Reset", !!maint && ["Reboot", "Restart History", "Run Quick Setup again", "Factory Reset"].every((t) => p.$$("button", maint).some((b) => b.textContent.includes(t))));

    /* the parts list */
    const rows = p.$$(".bk-row", bk);
    check("six parts listed in order", rows.map((r) => r.dataset.part).join(",") === "settings,vehicles,databases,scripts,dashboard,certs", rows.map((r) => r.dataset.part));
    const certs = p.rowOf(bk, "certs");
    check("the certificates row cannot be ticked and says where they are kept", !!certs && certs.querySelector("input").disabled && /Never leave the device/.test(certs.textContent));
    check("the five file parts are ticked", rows.filter((r) => r.dataset.part !== "certs").every((r) => r.querySelector("input").checked));
    check("the settings row shows the groups and a size", /\d+ groups/.test(p.rowOf(bk, "settings").textContent) && /KB|B/.test(p.rowOf(bk, "settings").textContent));
    check("the vehicles row shows cars and PIDs", /\d+ cars? · \d+ PIDs/.test(p.rowOf(bk, "vehicles").textContent));
    check("what is never in the file is listed", /Not in the file: logs on the SD card/.test(bk.textContent));
    const master = p.$(".bk-master input", bk);
    check("Everything that may leave the device reads 5 of 5", !!master && master.checked && /5 of 5/.test(p.$(".bk-master", bk).textContent));

    /* the download link follows the ticks and the switches */
    const dl = p.$(".bk-download", bk);
    const name = () => p.$(".bk-name", bk).textContent;
    const included = () => (((dl.getAttribute("href") || "").match(/include=([a-z,]*)/) || [, ""])[1]).split(",").filter(Boolean).sort().join(",");
    check("the download link carries every file part", !!dl && /^\/api\/backup\?include=/.test(dl.getAttribute("href") || "") && included() === "dashboard,databases,scripts,settings,vehicles", dl && dl.getAttribute("href"));
    check("the file is named by device and date, plain", /^wican-14c19f44e349-\d{4}-\d{2}-\d{2}\.json$/.test(name()), name());
    check("plain text chip and the privacy line", /plain text/.test(p.$(".bk-file", bk).textContent) && /keep it private/.test(p.$(".bk-file", bk).textContent));
    p.flip(p.rowOf(bk, "databases").querySelector("input")); await sleep(100);
    check("unticking a part drops it from the link and the master goes half-set", included() === "dashboard,scripts,settings,vehicles" && master.indeterminate && /4 of 5/.test(p.$(".bk-master", bk).textContent));
    p.flip(master); await sleep(100);
    check("the master tick restores every part", included() === "dashboard,databases,scripts,settings,vehicles" && !master.indeterminate);
    const redact = p.$('.frow[data-key="bk_redact"] .switch input', bk);
    p.flip(redact); await sleep(100);
    check("Remove sensitive data adds redact=1 and the chip", /redact=1/.test(dl.getAttribute("href")) && /passwords removed/.test(p.$(".bk-file", bk).textContent) && /safe to share/.test(p.$(".bk-file", bk).textContent));
    p.flip(redact); await sleep(100);
    const encrypt = p.$('.frow[data-key="bk_encrypt"] .switch input', bk);
    p.flip(encrypt); await sleep(100);
    check("Encrypt without a downloaded key: the nudge shows and the download waits", dl.classList.contains("disabled") && !dl.hasAttribute("href") && p.$$(".banner", bk).some((b) => b.style.display !== "none" && /Download the key before the backup/.test(b.textContent)));
    check("the nudge carries the key link", !!p.$(".banner .bk-key", bk) && /\/api\/backup\/key$/.test(p.$(".banner .bk-key", bk).getAttribute("href")));
    check("the copies card shows the key as never downloaded", /never downloaded/.test(p.$('.frow[data-key="bk_key"]', cp).textContent));
    /* the key goes to the PC: the device marks it downloaded when it serves the file;
       the page re-reads the state 1.5 s after the click */
    p.w.__mockState.backup.keyDownloaded = true; p.w.__mockState.backup.keyDownloadedAt = new Date().toISOString();
    /* a real browser downloads the href; jsdom would try to navigate: keep the page */
    p.d().addEventListener("click", (e) => { if (e.target.closest && e.target.closest("a.bk-key")) e.preventDefault(); }, true);
    p.click(p.$(".banner .bk-key", bk)); await sleep(1900);
    check("after the key download the encrypted download is allowed", !dl.classList.contains("disabled") && /encrypt=1/.test(dl.getAttribute("href") || "") && included() === "dashboard,databases,scripts,settings,vehicles" && /-encrypted\.json$/.test(name()), dl.getAttribute("href"));
    check("the encrypted chip and the wording", /encrypted/.test(p.$(".bk-file", bk).textContent) && /opens on this WiCAN or with the key file/.test(p.$(".bk-file", bk).textContent));
    check("both cards now read downloaded", /downloaded/.test(p.$('.frow[data-key="bk_key"]', cp).textContent) && !/never downloaded/.test(p.$('.frow[data-key="bk_key"]', cp).textContent));

    /* the copies card */
    const places = p.$$(".bk-place", cp);
    check("internal flash and SD card rows", places.map((x) => x.dataset.where).join(",") === "internal,sd");
    check("each copy has its own Restore and the refresh time", places.every((x) => !!x.querySelector(".bk-restore-copy")) && /6 parts/.test(places[0].textContent));
    check("the SD row names the free space and the replacement story", /free/.test(places[1].textContent) && /replacement WiCAN/.test(places[1].textContent));
    const autoRow = p.$('.frow[data-key="auto_copy"]', cp);
    check("the auto_copy setting row with its label", !!autoRow && /Keep the copies up to date/.test(autoRow.querySelector("label").textContent));
    check("the refresh button sits on the right", !!p.$(".bk-refresh", cp) && /flex-end/.test(p.$(".bk-refresh", cp).parentElement.style.justifyContent));
    p.click(p.$(".bk-refresh", cp)); await sleep(600);
    check("Refresh the copies now hits the device", p.w.__mockState.backupRefreshes === 1);
    /* a settings switch stages like any setting */
    const autoSw = autoRow && autoRow.querySelector(".switch input");
    if (autoSw) { p.flip(autoSw); await sleep(100); }
    check("the auto_copy switch stages config_backup", !!autoSw && p.w.eval("store").pending.has("config_backup") && p.w.eval("store").pending.get("config_backup").auto_copy === false);
    if (autoSw) { p.flip(autoSw); await sleep(100); }
    p.w.eval("store").pending.clear(); p.w.eval("store").dirty.clear();

    /* the restore preview from the copy on the flash */
    p.click(places[0].querySelector(".bk-restore-copy")); await sleep(600);
    let m = p.modal();
    check("Restore on the flash copy opens its preview", !!m && /Restore the copy on the internal flash/.test(m.querySelector("h3").textContent));
    check("the flash copy holds six parts, certificates included and ticked", !!m && p.$$(".bk-row", m).length === 6 && p.rowOf(m, "certs").querySelector("input").checked);
    check("no warning on the device's own copy", !!m && p.$$(".banner", m).length === 0);
    check("Restore and restart is enabled", !!m && !p.$(".bk-go", m).disabled);
    p.click(p.$$(".acts .btn", m).find((b) => b.textContent === "Cancel")); await sleep(300);
    check("Cancel drops the staged backup", p.w.__mockState.backupStaged === null && !p.modal());

    /* the preview of files through the chunk's own entry point: the mock answers inspect by scene */
    const preview = async (as) => { p.w.__mockState.backup.inspectAs = as; const r = await p.w.fetch("/api/backup/inspect?name=" + encodeURIComponent("wican-x-" + as + ".json"), { method: "POST", body: "{}" }); const man = await r.json(); p.w.eval("PAGES").__backup.restorePreview(man, async () => {}); await sleep(300); return p.modal(); };
    m = await preview("plain_this");
    check("a plain file of this device: five parts, certificates never in a file", !!m && /a backup of this WiCAN/.test(m.querySelector("h3").textContent) && p.rowOf(m, "certs").querySelector("input").disabled && /Never in a file/.test(p.rowOf(m, "certs").textContent));
    check("the facts name the file, the date and this WiCAN", /wican-x-plain_this\.json/.test(m.textContent) && /this WiCAN/.test(m.textContent));
    check("no names or VPN ticks for the device's own file", !p.$(".bk-keep-names", m) && !p.$(".bk-keep-vpn", m));
    p.flip(p.rowOf(m, "scripts").querySelector("input")); await sleep(100);
    p.click(p.$(".bk-go", m)); await sleep(500);
    const restored = p.w.__mockState.backupRestores[0];
    check("Restore and restart sends the ticked parts and the keep flags", !!restored && restored.include.join(",") === "settings,vehicles,databases,dashboard" && restored.keep_identity === true && restored.keep_vpn_keys === false, restored);
    check("the page waits for the restart", p.w.eval("conn").rebootUntil > Date.now());
    p.closeModal();

    m = await preview("enc_other");
    check("an encrypted file of another device is locked: the key picker, no parts, Restore disabled", !!m && /another WiCAN's backup/.test(m.querySelector("h3").textContent) && !!p.$(".bk-unlock", m) && p.$$(".bk-row", m).length === 0 && p.$(".bk-go", m).disabled && /Encrypted with another WiCAN's key/.test(m.textContent));
    /* the unlock route answers the open manifest; the chunk re-renders from it */
    const un = await (await p.w.fetch("/api/backup/unlock", { method: "POST", body: JSON.stringify({ key: "# k\n" + "ab".repeat(32) }) })).json();
    p.closeModal(); p.w.eval("PAGES").__backup.restorePreview(un, async () => {}); await sleep(300); m = p.modal();
    check("unlocked: the other-device banner, the names tick on, the VPN tick off, the parts listed", !!m && /From another WiCAN/.test(m.textContent) && p.$(".bk-keep-names input", m).checked && !p.$(".bk-keep-vpn input", m).checked && p.$$(".bk-row", m).length === 6 && !p.$(".bk-go", m).disabled);
    check("the VPN tick explains the replacement case", /takes over the other one's tunnel identity/.test(p.$(".bk-keep-vpn", m).textContent));
    p.closeModal();

    m = await preview("redacted");
    check("a redacted file: the banner says the stored secrets are kept", !!m && /were removed from this backup/.test(m.textContent) && /stored on this WiCAN are kept/.test(m.textContent) && !p.$(".bk-go", m).disabled);
    p.closeModal();
    m = await preview("newer");
    check("a newer file is refused: the banner, no parts, Restore disabled", !!m && /Made with newer firmware/.test(m.textContent) && p.$(".bk-go", m).disabled && p.$$(".bk-row", m).every((r) => r.querySelector("input").disabled));
    p.closeModal();
    m = await preview("v1");
    check("today's settings-only file reads as the settings part", !!m && p.rowOf(m, "settings").querySelector("input").checked && p.$$(".bk-row input:not(:disabled)", m).length === 1 && /today's settings-only format/.test(m.textContent));
    p.closeModal();

    check("the restore card offers a file and points at the copies", !!p.$(".bk-pick", rs) && /its own Restore button, above/.test(rs.textContent));
    check("no page errors", p.errs.length === 0, p.errs.slice(0, 2));
    p.close();
  }

  /* ---- boot 2: no card, the copies off, a card from another WiCAN later ---- */
  {
    const p = boot("#/status", { backup: { keyDownloaded: true, sd: false, copies: false } });
    await sleep(1500);
    p.w.__mockSettings.config_backup.values.auto_copy = false;
    await p.go("#/system/maintenance"); await sleep(1500);
    const cp = p.$("#bk-copies-card");
    const places = p.$$(".bk-place", cp);
    check("no card: the SD row says so and offers nothing to restore", !!cp && /No SD card in the slot/.test(places[1].textContent) && !places[1].querySelector(".bk-restore-copy"));
    check("no copy yet on the flash", /No copy yet/.test(places[0].textContent));
    check("the copies switch reads off from the settings", !p.$('.frow[data-key="auto_copy"] .switch input', cp).checked);
    check("the key downloaded: no nudge", p.$$(".banner", cp).every((b) => b.style.display === "none"));
    /* a card from another WiCAN appears: its copy restores through the key file */
    p.w.__mockState.backup.sd = true; p.w.__mockState.backup.sdDeviceId = "68ee8f5a653d";
    await p.go("#/status"); await p.go("#/system/maintenance"); await sleep(1500);
    const cp2 = p.$("#bk-copies-card");
    const sdRow = p.$$(".bk-place", cp2)[1];
    check("a card from another WiCAN is named on its row with Restore", /another WiCAN \(68ee8f5a653d\)/.test(sdRow.textContent) && !!sdRow.querySelector(".bk-restore-copy"));
    p.click(sdRow.querySelector(".bk-restore-copy")); await sleep(600);
    const m = p.modal();
    check("its preview is locked until the key file is given", !!m && /Restore the copy on the SD card/.test(m.querySelector("h3").textContent) && !!p.$(".bk-unlock", m) && p.$(".bk-go", m).disabled);
    p.closeModal();
    check("no page errors (boot 2)", p.errs.length === 0, p.errs.slice(0, 2));
    p.close();
  }

  /* ---- boot 3: a copy held since a factory reset (issue #987), files the backup leaves out (issue #989) ---- */
  {
    const p = boot("#/system/maintenance", { backup: { keyDownloaded: true, sd: true, copies: true, held: true,
      leftOut: [{ name: "big.dbc", why: "over 512 KB" }, { name: "", why: "and 2 more" }] } });
    await sleep(2200);
    const bk = p.$("#bk-backup-card"), cp = p.$("#bk-copies-card");
    const dbRow = p.rowOf(bk, "databases");
    check("a part says what the backup leaves out, by name and reason", !!dbRow && /Not in the backup: big\.dbc \(over 512 KB\), and 2 more\./.test(dbRow.textContent), dbRow && dbRow.textContent);
    check("the other parts carry no such note", !/Not in the backup/.test(p.rowOf(bk, "scripts").textContent));
    let places = p.$$(".bk-place", cp);
    check("the held copy says so, with the date and both ways out", places[0].classList.contains("bk-held") && /Holding the settings from before the factory reset of /.test(places[0].textContent) && /Restore to undo the reset/.test(places[0].textContent) && /refresh the copies to let them follow/.test(places[0].textContent) && !!places[0].querySelector(".bk-held-chip"), places[0].textContent);
    check("the held copy still offers Restore", !!places[0].querySelector(".bk-restore-copy"));
    /* a refresh asks before letting the undo go */
    p.click(p.$(".bk-refresh", cp)); await sleep(400);
    let m = p.modal();
    check("Refresh on a held copy asks first", !!m && /Let the copies follow the device again\?/.test(m.querySelector("h3").textContent) && /the undo is gone/.test(m.textContent) && p.w.__mockState.backupRefreshes === 0);
    p.click(p.$$(".acts .btn", m).find((b) => b.textContent === "Cancel")); await sleep(300);
    check("Cancel keeps the hold", p.w.__mockState.backupRefreshes === 0 && !p.modal() && p.$$(".bk-place", cp)[0].classList.contains("bk-held"));
    p.click(p.$(".bk-refresh", cp)); await sleep(400);
    m = p.modal();
    p.click(p.$$(".acts .btn", m).find((b) => b.textContent === "Continue")); await sleep(1200);
    places = p.$$(".bk-place", cp);
    check("Continue refreshes and the copy follows the device again", p.w.__mockState.backupRefreshes === 1 && !places[0].classList.contains("bk-held") && /Survives a factory reset/.test(places[0].textContent), places[0].textContent);
    /* the restore preview names what the backup left out */
    p.click(places[0].querySelector(".bk-restore-copy")); await sleep(600);
    m = p.modal();
    check("the preview names what the backup left out", !!m && /This backup left out: big\.dbc \(over 512 KB\), and 2 more\./.test(p.rowOf(m, "databases").textContent), m && p.rowOf(m, "databases").textContent);
    p.closeModal();
    check("no page errors (boot 3)", p.errs.length === 0, p.errs.slice(0, 2));
    p.close();
  }

  console.log(fails ? `BACKUP PROBE FAIL (${fails})` : "BACKUP PROBE PASS");
  process.exit(fails ? 1 : 0);
})();
