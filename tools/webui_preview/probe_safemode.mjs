/* Probe (2026-10-05, TASK_crash_loop_brake.md section 6): the safe mode page
   (main/web/safemode.html, embedded into the firmware as it is) is the last way
   into a device that no longer starts, and since that day the place where its
   user gets the stored crash report. No bench can reach it (safe mode needs the
   button held at power-on and serves its own access point), so the page is run
   here against the three routes it calls: GET /crash_report, POST
   /factory_reset, POST /upload_firmware. What must hold: the report shows, as
   the firmware's text, with Copy and Download; a device with no report says
   so; and the two things the page always did still work. */
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import { JSDOM, VirtualConsole } from "jsdom";

const here = dirname(fileURLToPath(import.meta.url));
const html = readFileSync(join(here, "..", "..", "main", "web", "safemode.html"), "utf-8");
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
let fails = 0;
const check = (n, ok, extra) => { console.log((ok ? "PASS " : "FAIL ") + n + (extra !== undefined ? "  [" + JSON.stringify(extra) + "]" : "")); if (!ok) { fails++; process.exitCode = 1; } };

const REPORT = "WiCAN crash report\nDevice:    68ee8f5a653d\nFirmware:  v6.00p_alfa-01\nImage:     ab67e6d34a77b139\n"
  + "Stored:    2026-10-05 03:37:24 UTC\nLoop:      3 crashes in a row; the device parked itself\n"
  + "Crash:     exception StoreProhibited at 0x42087fdc (address 0x0000bad0), core 0, task \"cli_console\", up 11 s, image ab67e6d34a77b139\n"
  + "Backtrace: 0x42087fd9 0x42088462 0x421281bf 0x421278d3 0x42127e1e 0x4038c53d\n";

/* one page load against a device that answers `report` (a string, null = 404,
   undefined = the request fails) */
async function load(report) {
  const errs = [], calls = [];
  const vc = new VirtualConsole();
  vc.on("jsdomError", (e) => errs.push(e.message));
  const dom = new JSDOM(html, {
    runScripts: "dangerously", url: "http://192.168.0.10/", pretendToBeVisual: true, virtualConsole: vc,
    beforeParse(w) {
      w.console.error = () => {};
      w.fetch = async (url, opts) => {
        calls.push((opts && opts.method || "GET") + " " + url);
        if (url === "/crash_report") {
          if (report === undefined) throw new Error("network");
          return report === null
            ? { ok: false, status: 404, text: async () => "No crash report is stored on this device.\n" }
            : { ok: true, status: 200, text: async () => report };
        }
        if (url === "/factory_reset") return { ok: true, status: 200, text: async () => "OK" };
        return { ok: false, status: 404, text: async () => "" };
      };
      w.confirm = () => true;
      w.XMLHttpRequest = class {
        constructor() { this.upload = {}; }
        open(m, u) { calls.push(m + " " + u); }
        send() { this.status = 200; setTimeout(() => { if (this.onload) this.onload(); if (this.onloadend) this.onloadend(); }, 10); }
      };
      w.document.execCommand = (cmd) => { calls.push("execCommand " + cmd); return true; };
    },
  });
  await sleep(120);
  return { w: dom.window, d: dom.window.document, errs, calls };
}

(async () => {
  /* a device with a stored report */
  let p = await load(REPORT);
  const titles = () => [...p.d.querySelectorAll(".section-title")].map((e) => e.textContent);
  check("the page still parses and runs", p.errs.length === 0, p.errs.slice(0, 2));
  check("the sections: Crash Report first, then the two the page always had", titles().join("|") === "Crash Report|Factory Reset|Firmware Update", titles());
  check("it asks the device for the report", p.calls.includes("GET /crash_report"), p.calls);
  check("the report is shown as the firmware's own text", p.d.getElementById("crash-text").textContent === REPORT);
  check("the box with the buttons is visible", p.d.getElementById("crash-box").style.display === "block");
  const dl = p.d.querySelector("#crash-box a");
  check("Download is a plain link to the text route", !!dl && dl.getAttribute("href") === "/crash_report.txt"
    && dl.getAttribute("download") === "wican_crash_report.txt" && dl.textContent === "Download Report");
  p.d.querySelector("#crash-box button").click();
  await sleep(30);
  const st = p.d.getElementById("status-message");
  check("Copy copies without the clipboard API (plain http)", p.calls.includes("execCommand copy") && st.textContent === "Crash report copied."
    && st.style.display === "block", [st.textContent, p.calls]);
  check("the copy helper leaves no textarea behind", p.d.querySelectorAll("textarea").length === 0);
  check("the warning line names what the page offers", p.d.querySelector(".warning-text").textContent.includes("crash report, firmware update and factory reset"));
  check("no long dash on the page", !/—/.test(p.d.body.textContent));

  /* the two things the page always did */
  p.w.confirmFactoryReset();
  await sleep(40);
  check("Factory Reset still posts", p.calls.includes("POST /factory_reset") && st.textContent.startsWith("Factory reset successful"), st.textContent);
  const file = new p.w.File(["x"], "fw.bin");
  Object.defineProperty(p.d.getElementById("firmware-file"), "files", { value: [file] });
  p.d.getElementById("firmware-form").dispatchEvent(new p.w.Event("submit", { cancelable: true }));
  await sleep(60);
  check("Firmware Update still uploads", p.calls.includes("POST /upload_firmware") && st.textContent.startsWith("Firmware update successful"), st.textContent);

  /* a device with no report */
  p = await load(null);
  check("no report: the page says so and shows no box", p.d.getElementById("crash-status").textContent === "No crash report is stored on this device."
    && p.d.getElementById("crash-box").style.display === "none" && p.errs.length === 0, p.d.getElementById("crash-status").textContent);

  /* the request fails: the rest of the page must not care */
  p = await load(undefined);
  check("a failed request is said, and the page stays usable", p.d.getElementById("crash-status").textContent.startsWith("The crash report could not be read")
    && p.errs.length === 0 && typeof p.w.updateFirmware === "function" && typeof p.w.confirmFactoryReset === "function");

  console.log(fails ? `FAILED ${fails}` : "SAFE MODE PAGE PASS");
  process.exit(fails ? 1 : 0);
})();
