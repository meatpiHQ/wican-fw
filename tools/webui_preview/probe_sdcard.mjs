/* The SD card format on the web UI (2026-10-25), over the mock: a card in the
   slot WiCAN cannot read (exFAT, the factory format of cards over 32 GB; blank;
   damaged) shows the bar under the header on every page, the Status row, the
   File Manager tile, the Logger chips and the backup copies row say so with a
   Format link, and the dialog asks, has the words typed, shows the progress
   and the outcome; a card that does not answer is named without a format
   offer; a mounted card shows nothing new; Not now is per card.
   Prints `SD CARD PROBE PASS`. Run after make_preview.py (plain and --min). */
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import { JSDOM, VirtualConsole } from "jsdom";

const here = dirname(fileURLToPath(import.meta.url));
const html = readFileSync(join(here, "preview.html"), "utf-8");
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
let fails = 0;
const check = (n, ok, extra) => { console.log((ok ? "PASS " : "FAIL ") + n + (extra ? "  (" + String(extra).slice(0, 200) + ")" : "")); if (!ok) fails++; };

function boot(hash, preset) {
  const errs = [];
  const vc = new VirtualConsole();
  vc.on("jsdomError", (e) => errs.push(e.message));
  const dom = new JSDOM(html, {
    runScripts: "dangerously", url: "http://wican.local/" + hash,
    pretendToBeVisual: true, virtualConsole: vc,
    beforeParse(w) {
      w.__mockPreset = preset;
      w.matchMedia = () => ({ matches: false, addListener() {}, addEventListener() {} });
      w.scrollTo = () => {}; w.HTMLCanvasElement.prototype.getContext = () => null;
    },
  });
  const w = dom.window, d = () => w.document;
  return {
    w, errs,
    $: (s) => d().querySelector(s), $$: (s) => [...d().querySelectorAll(s)],
    card: (t) => [...d().querySelectorAll("#view .section")].find((c) => new RegExp(t).test(((c.querySelector("h3") || {}).textContent || "").trim())),
    nav: (h) => { w.location.hash = h; },
    rows: (c) => { const o = {}; if (!c) return o; for (const dt of c.querySelectorAll(".kv dt")) o[dt.textContent.trim()] = dt.nextElementSibling || null; return o; },
    bar: () => d().querySelector("#sdbar"),
    modal: () => d().querySelector("#modal-root .modal"),
    modalBtn: (t) => [...d().querySelectorAll("#modal-root .acts button")].find((b) => b.textContent.trim() === t),
    click: (el) => el && el.dispatchEvent(new w.MouseEvent("click", { bubbles: true, cancelable: true })),
    type: (el, v) => { el.value = v; el.dispatchEvent(new w.Event("input", { bubbles: true })); },
    close: () => { const r = d().querySelector("#modal-root"); r.classList.remove("on"); r.replaceChildren(); },
  };
}

(async () => {
  { /* the 64 GB exFAT card: the bar on Status, the row, the dialog through to the mounted card */
    const p = boot("#/status", { sd: "exfat", sdFormatMs: 900 });
    await sleep(1800);
    const bar = p.bar();
    check("the bar shows under the header for an unreadable card", !!bar && !bar.hidden, bar && bar.hidden);
    check("the bar names what the card holds and the size", /cannot read it/.test(bar.textContent) && /exFAT/.test(bar.textContent) && /59\.48 GB/.test(bar.textContent) && /everything on the card is erased/.test(bar.textContent), bar.textContent.slice(0, 160));
    const barBtn = [...bar.querySelectorAll("button")].find((b) => /Format the card/.test(b.textContent));
    check("the bar carries Format the card and Not now", !!barBtn && !!bar.querySelector("a") && /Not now/.test(bar.textContent));
    const sdRow = p.rows(p.card("^System$"))["SD card"];
    check("Status > System: the SD card row reads Inserted, exFAT: not readable with a Format link", !!sdRow && /Inserted, exFAT: not readable/.test(sdRow.textContent) && !!sdRow.querySelector("a.sd-fmt-link"), sdRow && sdRow.textContent);
    check("no JS errors", p.errs.length === 0, p.errs.slice(0, 2));
    /* the dialog */
    p.click(barBtn); await sleep(300);
    let m = p.modal();
    check("the dialog asks first: Format the SD card? with the card, what was found and the result", !!m && /Format the SD card\?/.test(m.textContent) && /SD64G/.test(m.textContent) && /exFAT, the factory format/.test(m.textContent) && /FAT32, one partition, 32 KB clusters/.test(m.textContent) && /Everything on the card is erased/.test(m.textContent), m && m.textContent.slice(0, 200));
    check("Yes, format it is the danger action", !!p.modalBtn("Yes, format it") && p.modalBtn("Yes, format it").classList.contains("danger"));
    p.click(p.modalBtn("Yes, format it")); await sleep(200);
    m = p.modal();
    const inp = p.$("#sd-phrase"), cont = p.modalBtn("Continue");
    check("then the words: an input, Continue off", !!m && /Type the words to continue/.test(m.textContent) && !!inp && !!cont && cont.disabled);
    p.type(inp, "format sd"); await sleep(50);
    check("a partial phrase keeps Continue off", cont.disabled);
    p.type(inp, "  Format   SDCARD "); await sleep(50);
    check("the words in any case and spacing switch Continue on", !cont.disabled);
    const posts0 = p.w.__mockSd().posts.length;
    p.click(cont); await sleep(300);
    const posts = p.w.__mockSd().posts;
    check("Continue posts the canonical phrase once", posts.length === posts0 + 1 && posts[posts.length - 1].confirm === "format sdcard", JSON.stringify(posts));
    m = p.modal();
    check("the progress dialog: Formatting, the card named, do not take it out", !!m && /Formatting the SD card/.test(m.textContent) && /SD64G/.test(m.textContent) && /Do not take the card out/.test(m.textContent) && !p.modalBtn("Cancel"), m && m.textContent.slice(0, 160));
    check("the device reports formatting meanwhile", p.w.__mockSd().sd.state === "formatting");
    await sleep(2200);
    m = p.modal();
    check("done: SD card ready, mounted at /sd, FAT32, the free space, the time it took", !!m && /SD card ready/.test(m.textContent) && /Mounted at \/sd/.test(m.textContent) && /FAT32/.test(m.textContent) && /GB free/.test(m.textContent) && /formatted in \d/.test(m.textContent), m && m.textContent.slice(0, 200));
    check("Open File Manager and Close", !!p.modalBtn("Open File Manager") && !!p.modalBtn("Close"));
    p.click(p.modalBtn("Close")); await sleep(600);
    check("after the format the bar is gone", p.bar().hidden);
    const sdRow2 = p.rows(p.card("^System$"))["SD card"];
    await sleep(4200); /* the Status page's own refresh */
    const sdRow3 = p.rows(p.card("^System$"))["SD card"];
    check("the Status row reads Mounted again", /Mounted/.test((sdRow3 || sdRow2 || {}).textContent || ""), (sdRow3 || sdRow2 || {}).textContent);
    check("no JS errors through the dialog", p.errs.length === 0, p.errs.slice(0, 2));
  }
  { /* a refused phrase from a page that bypasses the dialog: the mock answers 400 like the device */
    const p = boot("#/status", { sd: "exfat" });
    await sleep(1200);
    const r = await p.w.fetch("/api/sdcard/format", { method: "POST", body: JSON.stringify({ confirm: "yes" }) });
    check("POST without the phrase is refused with 400", r.status === 400 && /confirm with/.test((await r.json()).error));
  }
  { /* the format fails */
    const p = boot("#/status", { sd: "exfat_failfmt", sdFormatMs: 600 });
    await sleep(1800);
    p.click([...p.bar().querySelectorAll("button")].find((b) => /Format the card/.test(b.textContent))); await sleep(300);
    p.click(p.modalBtn("Yes, format it")); await sleep(200);
    p.type(p.$("#sd-phrase"), "format sdcard"); await sleep(50);
    p.click(p.modalBtn("Continue")); await sleep(2000);
    const m = p.modal();
    check("a failed format: the error named, Try again offered", !!m && /The format failed/.test(m.textContent) && /write error at sector 2048/.test(m.textContent) && !!p.modalBtn("Try again") && !!p.modalBtn("Close"), m && m.textContent.slice(0, 200));
    check("the bar stays for the still unreadable card", !p.bar().hidden);
  }
  { /* the File Manager tile */
    const p = boot("#/files", { sd: "exfat" });
    await sleep(1800);
    const tile = p.$$(".fm-mount").find((t) => /\/sd/.test(t.textContent));
    check("File Manager: the SD card tile reads not readable, names the card and what it holds", !!tile && /not readable/.test(tile.textContent) && /SD64G/.test(tile.textContent) && /exFAT/.test(tile.textContent) && !/Insert an SD card/.test(tile.textContent), tile && tile.textContent.slice(0, 200));
    check("the tile carries a Format button at the right and is not greyed out", !!tile && !!tile.querySelector("button") && /Format/.test(tile.querySelector("button").textContent) && !tile.classList.contains("off"));
    p.click(tile.querySelector("button")); await sleep(300);
    check("the tile's button opens the same dialog", !!p.modal() && /Format the SD card\?/.test(p.modal().textContent));
    check("no JS errors", p.errs.length === 0, p.errs.slice(0, 2));
  }
  { /* the Logger page */
    const p = boot("#/logger", { sd: "blank" });
    await sleep(1800);
    const st = p.card("^Status$");
    check("Logger: the storage chip reads SD card inserted, not readable with Format the card", !!st && /SD card inserted, not readable/.test(st.textContent) && !!st.querySelector("a.sd-fmt-link") && !/No SD card/.test(st.textContent), st && st.textContent.slice(0, 200));
    const files = p.card("^Log files$");
    check("the empty file list says the card is in but unreadable, with Format it", !!files && /in the slot, but WiCAN cannot read it/.test(files.textContent) && !!files.querySelector("a.sd-fmt-link"), files && files.textContent.slice(0, 200));
    check("the bar for a blank card says it has no filesystem and that nothing is lost", /has no filesystem/.test(p.bar().textContent) && /card is blank/.test(p.bar().textContent) && !/erased/.test(p.bar().textContent), p.bar().textContent);
    check("no JS errors", p.errs.length === 0, p.errs.slice(0, 2));
  }
  { /* the backup copies row */
    const p = boot("#/system/maintenance", { sd: "exfat" });
    await sleep(2200);
    const row = p.$('.bk-place[data-where="sd"]');
    check("System > Maintenance: the copies row says the card is not readable yet, with a chip and a Format link", !!row && /not readable yet/.test(row.textContent) && /not readable/.test((row.querySelector(".chip") || {}).textContent || "") && !!row.querySelector("a.sd-fmt-link") && !/No SD card in the slot/.test(row.textContent), row && row.textContent.slice(0, 200));
    check("no JS errors", p.errs.length === 0, p.errs.slice(0, 2));
  }
  { /* a card that does not answer: a fault, no format */
    const p = boot("#/status", { sd: "failed" });
    await sleep(1800);
    const bar = p.bar();
    check("a card that does not answer: the bar says so with the error, OK only, no Format", !!bar && !bar.hidden && /does not answer/.test(bar.textContent) && /ESP_ERR_TIMEOUT/.test(bar.textContent) && !bar.querySelector("button") && bar.classList.contains("dead"), bar && bar.textContent.slice(0, 160));
    const sdRow = p.rows(p.card("^System$"))["SD card"];
    check("the Status row reads Inserted, not answering without a link", !!sdRow && /Inserted, not answering/.test(sdRow.textContent) && !sdRow.querySelector("a"), sdRow && sdRow.textContent);
    p.nav("#/files"); await sleep(1200);
    const tile = p.$$(".fm-mount").find((t) => /\/sd/.test(t.textContent));
    check("the File Manager tile reads not answering, greyed out, no button", !!tile && /not answering/.test(tile.textContent) && !tile.querySelector("button") && tile.classList.contains("off"), tile && tile.textContent.slice(0, 160));
  }
  { /* Not now: per card and state, in this browser */
    const p = boot("#/status", { sd: "exfat" });
    await sleep(1800);
    p.click(p.bar().querySelector("a")); await sleep(100);
    check("Not now hides the bar", p.bar().hidden);
    check("and remembers the card", /6b2f91a4:unreadable/.test(p.w.localStorage.getItem("wican-sd-seen") || ""), p.w.localStorage.getItem("wican-sd-seen"));
    await sleep(3200); /* the next poll must not bring it back */
    check("the next status poll keeps it hidden", p.bar().hidden);
    const sdRow = p.rows(p.card("^System$"))["SD card"];
    check("the Status row keeps its Format link after Not now", !!sdRow && !!sdRow.querySelector("a.sd-fmt-link"));
  }
  { /* a mounted card: nothing new; an empty slot: nothing new */
    const p = boot("#/status", { sd: "mounted" });
    await sleep(1800);
    check("a mounted card: no bar", p.bar().hidden);
    const sdRow = p.rows(p.card("^System$"))["SD card"];
    check("the Status row reads Mounted as before", !!sdRow && sdRow.textContent.trim() === "Mounted", sdRow && sdRow.textContent);
    const q = boot("#/status", { sd: "absent" });
    await sleep(1800);
    check("an empty slot: no bar, the row reads Not mounted", q.bar().hidden && /Not mounted/.test((q.rows(q.card("^System$"))["SD card"] || {}).textContent || ""));
    q.nav("#/files"); await sleep(1200);
    const tile = q.$$(".fm-mount").find((t) => /\/sd/.test(t.textContent));
    check("the File Manager tile still says Insert an SD card for an empty slot", !!tile && /Insert an SD card/.test(tile.textContent) && /not mounted/.test(tile.textContent), tile && tile.textContent.slice(0, 120));
    check("no JS errors", p.errs.length === 0 && q.errs.length === 0, p.errs.concat(q.errs).slice(0, 2));
  }
  { /* the wizard: the bar stays out of Quick Setup */
    const p = boot("#/setup", { sd: "exfat", apDefaultPassword: true });
    await sleep(1800);
    check("no bar on the Quick Setup screens", !!p.bar() && p.bar().hidden);
  }
  console.log(fails ? `SD CARD PROBE FAIL (${fails})` : "SD CARD PROBE PASS");
  process.exit(fails ? 1 : 0);
})();
