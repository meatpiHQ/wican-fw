/* The Quick Setup reconnect screen on the REAL device, end to end, in Chrome
   over plain http on a LAN address (the phone's situation, 2026-10-06: Ali's
   phone auto-joined the home WiFi when the AP password changed and the page,
   loaded from the AP address, sat at "Restarting" for good; Copy link copied
   nothing because plain http has no navigator.clipboard).

   Two ssh tunnels to the DUT stand in for the two networks: A, bound to a
   LAN address, is the page's origin ("the access point") and is killed once
   the screen is up; B stays and every request for http://wican_<id>.local is
   carried to it by Playwright's router ("the home WiFi": this PC cannot
   resolve the Pi network's mDNS names). Expected: the join card while A
   answers; Copy link puts the link on the clipboard (execCommand path); after
   the kill the card says Restarting with what to do meanwhile, the pill says
   the AP is out of reach, no probe of the link in the first 8 s, then the page
   finds WiCAN at the link, says so and offers "Continue on <ssid>" as a
   button (no jump on a timer: Ali, 2026-10-06); the press lands on the checks
   screen at the .local URL. Prints RECONNECT DEVICE PASS.

   usage: node tools/testbench/wifi/reconnect_screen_bench.mjs [out dir] [--bind <LAN ip>] [--dut <ip>]
   needs: ssh rpi001, Chrome, and playwright from tools/webui_preview
   (`npm install` there; it drives the installed Chrome, no browser download).
   The bind address must be a real LAN address of this PC (not 127.0.0.1,
   which Chrome treats as secure and gives a clipboard API). */
import { spawn } from "node:child_process";
import { mkdirSync, writeFileSync } from "node:fs";
import { networkInterfaces } from "node:os";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const here = dirname(fileURLToPath(import.meta.url));
const { chromium } = createRequire(join(here, "..", "..", "webui_preview", "package.json"))("playwright");

const args = process.argv.slice(2);
const opt = (k, d) => { const i = args.indexOf(k); return i >= 0 ? args[i + 1] : d; };
const lanIp = () => { const all = Object.values(networkInterfaces()).flat().filter((a) => a && a.family === "IPv4" && !a.internal).map((a) => a.address); return all.find((a) => a.startsWith("192.168.8.")) || all[0]; };
const BIND = opt("--bind", lanIp()), DUT = opt("--dut", "10.42.1.194"), PA = 8078, PB = 8079;
const out = args.find((a) => !a.startsWith("--") && args[args.indexOf(a) - 1] !== "--bind" && args[args.indexOf(a) - 1] !== "--dut") || ".";
const baseA = `http://${BIND}:${PA}`, baseB = `http://127.0.0.1:${PB}`;
mkdirSync(out, { recursive: true });
const log = [];
const stamp = () => new Date().toTimeString().slice(0, 8);
const say = (...a) => { const l = stamp() + " " + a.join(" "); console.log(l); log.push(l); };
let fails = 0;
const check = (n, ok, extra) => { say((ok ? "PASS " : "FAIL ") + n + (extra !== undefined ? "  [" + JSON.stringify(extra) + "]" : "")); if (!ok) fails++; };

const tunA = spawn("ssh", ["-N", "-L", `${BIND}:${PA}:${DUT}:80`, "rpi001"], { stdio: "ignore" });
const tunB = spawn("ssh", ["-N", "-L", `127.0.0.1:${PB}:${DUT}:80`, "rpi001"], { stdio: "ignore" });
const up = async (base) => { for (let i = 0; i < 40; i++) { try { const r = await fetch(base + "/api/info"); if (r.ok) return await r.json(); } catch (e) { } await new Promise((r) => setTimeout(r, 500)); } throw new Error("tunnel never answered: " + base); };
const info = await up(baseA); await up(baseB);
say("both tunnels answer: device", info.device_id, info.fw_version, "| origin", baseA);
const mdns = `http://wican_${info.device_id}.local`;

const b = await chromium.launch({ channel: "chrome" });
const ctx = await b.newContext({ viewport: { width: 1280, height: 900 } });
const page = await ctx.newPage();
let linkHits = 0;
/* the home WiFi: whatever the page asks of the mDNS name reaches the device through B */
await page.route((u) => u.href.toLowerCase().startsWith(mdns + "/") || u.href.toLowerCase() === mdns, async (route) => {
  linkHits++;
  const target = baseB + route.request().url().slice(mdns.length);
  try { const r = await route.fetch({ url: target }); await route.fulfill({ response: r }); }
  catch (e) { say("route error", e.message.slice(0, 100)); await route.abort("connectionrefused"); }
});
page.on("console", (m) => { if (m.type() === "error" && !/ERR_CONNECTION_REFUSED|ERR_CONNECTION_TIMED_OUT/.test(m.text())) say("console error:", m.text().slice(0, 160)); });

const h2 = () => page.evaluate(() => (document.querySelector("#view h2") || {}).textContent || "");
const card = () => page.evaluate(() => { const c = document.querySelector(".qs-check"); return c ? c.textContent.replace(/\s+/g, " ").trim() : "(no card)"; });
const pill = () => page.evaluate(() => ({ tag: document.getElementById("cp-tag").textContent, off: getComputedStyle(document.getElementById("offline")).display !== "none" ? document.getElementById("offtx").textContent : "" }));

await page.goto(baseA + "/#/setup/reconnect");
await page.waitForTimeout(4000);
check("reconnect screen on the device's page", /Now switch networks/.test(await h2()), await h2());
check("own origin online: the join card", /WiCAN joined/.test(await card()), (await card()).slice(0, 60));
check("no link probe while the own origin answers", linkHits === 0, linkHits);
await page.screenshot({ path: `${out}/1_online.png` });

/* Copy link over plain http */
const sec = await page.evaluate(() => ({ secure: window.isSecureContext, clipboard: typeof navigator.clipboard }));
check("plain http: no navigator.clipboard", sec.secure === false && sec.clipboard === "undefined", sec);
await page.evaluate(() => { window.__toasts = []; const t = window.toast; window.toast = (m, k) => { window.__toasts.push([String(m), k]); return t(m, k); }; });
const link = await page.evaluate(() => document.querySelector(".qs-link .url").textContent);
await page.click("text=Copy link");
await page.waitForTimeout(300);
const btnTxt = await page.evaluate(() => (document.querySelector(".qs-link .acts button") || {}).textContent);
await page.evaluate(() => { const ta = document.createElement("textarea"); ta.id = "__paste"; document.body.append(ta); ta.focus(); });
await page.keyboard.press("Control+V");
await page.waitForTimeout(300);
const pasted = await page.evaluate(() => { const v = document.getElementById("__paste").value; document.getElementById("__paste").remove(); return v; });
check("Copy link puts the link on the clipboard", pasted === link, { pasted, link });
check("button says Copied, toast says Link copied", /Copied/.test(btnTxt) && (await page.evaluate(() => window.__toasts)).some((t) => /Link copied/.test(t[0])), [btnTxt, await page.evaluate(() => window.__toasts)]);
await page.screenshot({ path: `${out}/2_copied.png` });

/* the phone leaves the access point. Re-enter the screen first: in the real
   flow it is mounted at the moment of Save, and its 12 s patience counts
   from the mount */
await page.evaluate(() => { location.hash = "#/status"; });
await page.waitForTimeout(1200);
await page.evaluate(() => { location.hash = "#/setup/reconnect"; });
await page.waitForTimeout(1200);
check("screen re-entered", /Now switch networks/.test(await h2()), await h2());
await page.evaluate(() => expectReboot("Quick Setup: saving and restarting"));
tunA.kill();
await new Promise((r) => setTimeout(r, 500));
try { await fetch(baseA + "/api/info", { signal: AbortSignal.timeout(2000) }); say("WARNING: origin A still answers"); } catch (e) { say("origin A gone:", e.name); }
const t0 = Date.now();
let seenRestarting = false, foundAt = -1, hitsAt8 = -1, last = "";
for (let i = 0; i < 30; i++) {
  await page.waitForTimeout(1000);
  const el = Math.round((Date.now() - t0) / 1000);
  const c = await card(), p = await pill(), url = page.url();
  if (el === 8) hitsAt8 = linkHits;
  const line = `card: ${c.slice(0, 120)} | pill: ${p.tag} ${p.off ? "(" + p.off + ")" : ""} | url: ${url} | link hits: ${linkHits}`;
  if (line !== last) { say(`+${el} s`, line); last = line; }
  if (/Restarting/.test(c) && /tells you when it finds WiCAN there/.test(c)) seenRestarting = true;
  if (/WiCAN answers on/.test(c) && foundAt < 0) { foundAt = el; await page.screenshot({ path: `${out}/3_found.png` }); }
  if (foundAt >= 0 && el >= foundAt + 5) break;   /* five more seconds: it must stay here */
}
const stillHere = page.url(), goBtn = await page.evaluate(() => { const a = document.querySelector(".qs-check a.btn.pri"); return a ? { text: a.textContent.trim(), href: a.getAttribute("href") } : null; });
check("the card said Restarting with the instructions first", seenRestarting);
check("no link probe in the first 8 s", hitsAt8 === 0, hitsAt8);
check("the card said WiCAN answers on <ssid>", foundAt >= 0, foundAt);
check("no jump on a timer: the page is still on the reconnect screen 5 s later", stillHere.startsWith(baseA + "/#/setup/reconnect") && /Now switch networks/.test(await h2()), stillHere);
check("the way on is a button to the link, in the card", !!goBtn && /Continue on/.test(goBtn.text) && goBtn.href === mdns + "/#/setup/checks", goBtn);
await page.click(".qs-check a.btn.pri");
await page.waitForTimeout(4000);
const finalUrl = page.url(), finalH2 = await h2();
say("after the press: url", finalUrl, "| h2:", JSON.stringify(finalH2));
await page.screenshot({ path: `${out}/4_checks_at_local.png` });
check("the press opens Quick Setup at the .local link", finalUrl.startsWith(mdns + "/#/setup/checks"), finalUrl);
check("the checks screen is up there", /Connected through/.test(finalH2), finalH2);
check("the pill never said Device offline while waiting", !log.some((l) => /Device offline/.test(l)));
await b.close();
tunB.kill();
say(fails ? `RECONNECT DEVICE FAIL (${fails})` : "RECONNECT DEVICE PASS");
writeFileSync(`${out}/verify.log`, log.join("\n") + "\n");
process.exitCode = fails ? 1 : 0;
