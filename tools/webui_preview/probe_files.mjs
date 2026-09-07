/* Probe (2026-09-07, meatpi): the File Manager — storage landing, navigation with the
   URL following the folder, filter/sort, in-use marking of the logger's active file,
   New folder, upload (XHR progress) via drop, text preview, single + bulk delete, the
   SD-not-mounted state, and the sidebar rename. Runs over the mock API. */
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
  runScripts: "dangerously", url: "http://wican.local/#/files",
  pretendToBeVisual: true, virtualConsole: vc,
  beforeParse(w) {
    w.matchMedia = () => ({ matches: false, addListener() {}, addEventListener() {} });
    w.scrollTo = () => {}; w.HTMLCanvasElement.prototype.getContext = () => null;
    if (!w.TextEncoder) { w.TextEncoder = TextEncoder; w.TextDecoder = TextDecoder; }
  },
});
const w = dom.window, d = () => w.document;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
let fails = 0;
const check = (n, ok, extra) => { console.log((ok ? "PASS " : "FAIL ") + n + (extra !== undefined ? "  [" + JSON.stringify(extra) + "]" : "")); if (!ok) { fails++; process.exitCode = 1; } };
const $ = (sel) => d().querySelector(sel), $$ = (sel) => [...d().querySelectorAll(sel)];
const btn = (txt, root) => [...(root || d()).querySelectorAll("button")].find((b) => b.textContent.trim() === txt);
const fire = (el, type) => el.dispatchEvent(new w.Event(type, { bubbles: true }));
const modalBtn = (re) => [...d().querySelectorAll("#modal-root .acts button")].find((b) => re.test(b.textContent));
const nav = (hash) => { w.location.hash = hash; w.dispatchEvent(new w.Event("hashchange")); };
const rows = () => $$("#view tr.fm-row");
const row = (name) => rows().find((r) => r.dataset.name === name);
const names = () => rows().map((r) => r.dataset.name);
const crumb = () => ($("#view .fm-crumb") || {}).textContent || "";
const M = () => w.__mockState;
const hid = (el) => !!el && el.style.display === "none";
const drawn = (svg) => !!svg && svg.innerHTML.trim().length > 0;   /* an icon name ic() knows renders paths; an unknown one is an empty svg */

(async () => {
  await sleep(1000);
  /* 1. sidebar + landing */
  const navLabels = $$("#menu .nav-btn .nl").map((e) => e.textContent.trim());
  check("the sidebar tab is called File Manager (no plain Files entry left)", navLabels.includes("File Manager") && !navLabels.includes("Files"), navLabels.filter((l) => /File/.test(l)));
  const mounts = $$("#view .fm-mount");
  check("landing: one card per storage area with its usage", mounts.length === 2 && /Internal flash/.test(mounts[0].textContent) && /1\.01 MB of 5\.75 MB used/.test(mounts[0].textContent) && /SD card/.test(mounts[1].textContent) && /2\.83 GB/.test(mounts[1].textContent), mounts.map((m) => m.textContent.slice(0, 60)));
  check("landing: only Refresh in the toolbar (Up / filter / sort / New folder / Upload / Delete selected hidden)",
    hid($("#view .fm-tools button[title='Up one level']")) && hid(btn("New folder")) && hid($("#view .fm-tools label.btn")) && hid($("#view input[type=search]")) && hid($("#view .fm-tools select")) && hid($("#view .fm-tools button[title='Delete every selected item']")) && !hid($("#view .fm-tools button[title='Refresh']")));
  check("landing: both storage icons really render (known icon names, sized tile)", drawn(mounts[0].querySelector(".mi svg")) && drawn(mounts[1].querySelector(".mi svg")) && /Open/.test(mounts[0].querySelector(".mo").textContent));

  /* 2. open the SD card, then a folder: the URL follows */
  mounts[1].click(); await sleep(500);
  check("SD card opens at /sd with a breadcrumb and the URL", /Storage.*sd/.test(crumb()) && w.location.hash === "#/files/sd" && names().join(",") === "fw,logs,bench_ok.txt", names());
  check("known folders carry a one-line description", /logger output/.test(row("logs").textContent) && /firmware files/.test(row("fw").textContent));
  check("inside a folder the tools show, Delete selected stays hidden until something is selected", !hid(btn("New folder")) && !hid($("#view .fm-tools label.btn")) && !hid($("#view input[type=search]")) && hid($("#view .fm-tools button[title='Delete every selected item']")));
  check("file and folder rows draw a real icon", drawn(row("bench_ok.txt").querySelector(".fm-name svg")) && drawn(row("logs").querySelector(".fm-name svg")));
  check("the usage line names the mount", /SD card · .* of 2\.83 GB used/.test($("#view .fm-use").textContent));
  row("logs").querySelector("a.fn").click(); await sleep(500);
  check("/sd/logs lists the eight fixture files with a Download link each", rows().length === 8 && $$("#view a[download]").length === 8 && w.location.hash === "#/files/sd/logs");
  check("Preview only on text files small enough (asc + the 41 KB jsonl)", $$("#view button").filter((b) => b.textContent.trim() === "Preview").length === 2);
  check("a note points to the Logger page for its files", /Logger page/.test($("#view .fm-body").textContent));

  /* 3. filter + sort */
  const f = $("#view input[type=search]"); f.value = "can_"; fire(f, "input"); await sleep(50);
  check("filter narrows the list", names().length === 2 && names().every((n) => n.startsWith("can_")), names());
  f.value = ""; fire(f, "input"); await sleep(50);
  const sortSel = $("#view .fm-tools select"); sortSel.value = "size"; fire(sortSel, "change"); await sleep(50);
  check("sort by size puts the biggest file first", /\.jsonl$/.test(names()[0]) && rows().length === 8, names()[0]);
  sortSel.value = "name"; fire(sortSel, "change"); await sleep(50);

  /* 4. the logger's active file is marked in use */
  M().loggerRunning = true; M().loggerFile = "dl_1784563543.db";
  btn("Up") && $("#view .fm-tools button[title='Refresh']").click(); await sleep(500);
  const act = row("dl_1784563543.db");
  check("the active log file shows an in-use chip, no Download link, Delete disabled", !!act && /in use/.test(act.textContent) && !act.querySelector("a[download]") && act.querySelector("button.danger").disabled && $$("#view a[download]").length === 7);
  M().loggerRunning = false; $("#view .fm-tools button[title='Refresh']").click(); await sleep(500);
  check("with the logger stopped the marker is gone", !/in use/.test(row("dl_1784563543.db").textContent) && $$("#view a[download]").length === 8);

  /* 5. Up, New folder, upload by drop, preview */
  btn("Up").click(); await sleep(500);
  check("Up returns to /sd", w.location.hash === "#/files/sd" && names().includes("bench_ok.txt"));
  btn("New folder").click(); await sleep(100);
  const inp = $("#modal-root input"); inp.value = "test"; fire(inp, "input"); modalBtn(/OK|Create|Continue/).click(); await sleep(500);
  check("New folder creates /sd/test and lists it", M().dirs.has("/sd/test") && !!row("test") && !!btn("Open", row("test")));
  const file = new w.File(["hello from the probe"], "note.txt", { type: "text/plain" });
  const drop = new w.Event("drop", { bubbles: true }); drop.dataTransfer = { files: [file] };
  $("#view .fm-body").dispatchEvent(drop); await sleep(700);
  check("dropping a file uploads it into the folder (XHR path) and lists it", M().files["/sd/note.txt"] === "hello from the probe" && !!row("note.txt") && /20 B/.test(row("note.txt").textContent), M().files["/sd/note.txt"]);
  btn("Preview", row("note.txt")).click(); await sleep(400);
  check("Preview shows the text in a modal with its path and size", /note\.txt/.test($("#modal-root").textContent) && /\/sd\/note\.txt · 20 B/.test($("#modal-root").textContent) && ($("#modal-root .fm-pre") || {}).textContent === "hello from the probe");
  modalBtn(/Close/).click(); await sleep(100);

  /* 6. single delete, then bulk delete */
  const drop2 = new w.Event("drop", { bubbles: true }); drop2.dataTransfer = { files: [new w.File(["x"], "gone.txt")] };
  $("#view .fm-body").dispatchEvent(drop2); await sleep(700);
  row("gone.txt").querySelector("button.danger").click(); await sleep(100); modalBtn(/Continue/).click(); await sleep(500);
  check("row Delete removes the file after confirmation", !M().files["/sd/gone.txt"] && !row("gone.txt"));
  row("note.txt").querySelector("input[type=checkbox]").click(); row("test").querySelector("input[type=checkbox]").click(); await sleep(100);
  const ds = btn("Delete selected (2)");
  check("selecting two items shows Delete selected (2) and highlights the rows", !!ds && !hid(ds) && row("note.txt").classList.contains("sel"));
  ds.click(); await sleep(100); modalBtn(/Continue/).click(); await sleep(700);
  check("bulk delete removes the file and the empty folder", !M().files["/sd/note.txt"] && !M().dirs.has("/sd/test") && !row("note.txt") && !row("test"));

  /* 7. a folder that is not empty is refused in plain words */
  M().dirs.add("/sd/full"); M().files["/sd/full/a.txt"] = "a";
  $("#view .fm-tools button[title='Refresh']").click(); await sleep(500);
  row("full").querySelector("button.danger").click(); await sleep(100); modalBtn(/Continue/).click(); await sleep(400);
  check("deleting a non-empty folder explains why", M().dirs.has("/sd/full") && /only empty folders/i.test($("#toasts").textContent));
  delete M().files["/sd/full/a.txt"]; M().dirs.delete("/sd/full");

  /* 8. no SD card */
  M().sdMounted = false;
  nav("#/power"); await sleep(300); nav("#/files"); await sleep(900);
  const sd = $$("#view .fm-mount")[1];
  check("landing shows the SD card as not mounted", sd.classList.contains("off") && /not mounted/.test(sd.textContent) && /Insert an SD card/.test(sd.textContent));
  nav("#/files/sd"); await sleep(900);
  check("opening /sd without a card shows the plain-language banner", /No SD card is mounted/.test($("#view").textContent));
  M().sdMounted = true;

  check("no page errors", errs.length === 0, errs.slice(0, 2));
  console.log(fails ? `FAILED ${fails}` : "ALL PASS");
  process.exit(fails ? 1 : 0);
})();
