/* Probe (2026-10-11, issue #975): routes through the tunnel. The VPN Settings card
   shows `routes` as one field under the tunnel address (not advanced) with its help
   and placeholder; the wg-quick importer maps every IPv4 AllowedIPs entry into it and
   turns 0.0.0.0/0 into Route all traffic; the debug block carries a Routes row from
   /api/vpn. Runs over the mock API. */
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import { JSDOM, VirtualConsole } from "jsdom";

const here = dirname(fileURLToPath(import.meta.url));
const html = readFileSync(join(here, "preview.html"), "utf-8");
let fails = 0;
const check = (n, ok, extra) => { console.log((ok ? "PASS " : "FAIL ") + n + (extra !== undefined ? "  [" + JSON.stringify(extra) + "]" : "")); if (!ok) { fails++; process.exitCode = 1; } };
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const errs = [];
const vc = new VirtualConsole();
vc.on("jsdomError", (e) => errs.push(e.message + " " + (e.detail && e.detail.stack || "").slice(0, 300)));
const dom = new JSDOM(html, {
  runScripts: "dangerously", url: "http://wican.local/#/vpn",
  pretendToBeVisual: true, virtualConsole: vc,
  beforeParse(w) {
    w.__mockPreset = { vpn: { state: "connected", network: "HomeWiFi", paused: false, routes: ["10.66.0.0/24", "192.168.1.0/24"] } };
    w.matchMedia = () => ({ matches: false, addListener() {}, addEventListener() {} });
    w.scrollTo = () => {}; w.HTMLCanvasElement.prototype.getContext = () => null;
    if (!w.TextEncoder) { w.TextEncoder = TextEncoder; w.TextDecoder = TextDecoder; }
  },
});
const w = dom.window, d = () => w.document;
const $ = (sel) => d().querySelector(sel), $$ = (sel, root) => [...(root || d()).querySelectorAll(sel)];
const cardByTitle = (t) => $$("#view .section").find((c) => (c.querySelector(".legend, h3") || {}).textContent.trim().startsWith(t));

(async () => {
  await sleep(1500);
  const settings = cardByTitle("VPN Settings");
  check("VPN Settings card present", !!settings);
  const keys = $$("#view .frow[data-key]", settings).map((r) => r.dataset.key);
  const iAddr = keys.indexOf("address"), iRoutes = keys.indexOf("routes"), iAdv = keys.indexOf("__advtoggle");
  check("Routes row right under the tunnel address, above the advanced fold", iAddr >= 0 && iRoutes === iAddr + 1 && (iAdv < 0 || iRoutes < iAdv), keys);
  check("no allowed_ip rows any more", !keys.includes("allowed_ip") && !keys.includes("allowed_ip_mask"));
  const row = $('#view .frow[data-key="routes"]');
  check("labelled Routes through the tunnel", !!row && (row.querySelector("label") || {}).textContent.trim() === "Routes through the tunnel", row && row.querySelector("label").textContent.trim());
  check("help names CIDR, the tunnel's own network and the normal connection", !!row && /CIDR/.test(row.textContent) && /tunnel's own network/.test(row.textContent) && /normal connection/.test(row.textContent));
  const input = row && row.querySelector("input");
  check("the field carries the mock's route and the CIDR placeholder", !!input && input.value === "10.66.0.0/24" && /10\.66\.0\.0\/24, 192\.168\.1\.0\/24/.test(input.placeholder || ""), input && { value: input.value, placeholder: input.placeholder });

  /* the importer: every IPv4 AllowedIPs entry, 0.0.0.0/0 = route all */
  const imp = w.eval("importWgConf");
  const vals = {};
  const conf = "[Interface]\nPrivateKey = abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOP8=\nAddress = 10.66.0.2/24\n\n[Peer]\nPublicKey = abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOP8=\nAllowedIPs = 10.66.0.0/24, 192.168.1.0/24, fd00::/64\nEndpoint = vpn.example.com:51820\nPersistentKeepalive = 25\n";
  const n = imp(conf, vals);
  check("importer maps every IPv4 AllowedIPs entry into routes", vals.routes === "10.66.0.0/24, 192.168.1.0/24" && vals.default_route === false && n >= 5, { routes: vals.routes, default_route: vals.default_route, n });
  check("importer leaves no allowed_ip keys", !("allowed_ip" in vals) && !("allowed_ip_mask" in vals));
  const vals2 = {};
  imp(conf.replace("AllowedIPs = 10.66.0.0/24, 192.168.1.0/24, fd00::/64", "AllowedIPs = 0.0.0.0/0, 10.66.0.0/24"), vals2);
  check("0.0.0.0/0 becomes Route all traffic and stays out of the list", vals2.default_route === true && vals2.routes === "10.66.0.0/24", { routes: vals2.routes, default_route: vals2.default_route });

  /* the debug block */
  const dbg = cardByTitle("VPN Debug");
  const dt = $$(".kv dt", dbg).find((e) => e.textContent.trim() === "Routes");
  check("debug block has a Routes row with the device's list", !!dt && dt.nextElementSibling.textContent.trim() === "10.66.0.0/24, 192.168.1.0/24", dt && dt.nextElementSibling.textContent.trim());
  check("no page errors", errs.length === 0, errs.slice(0, 2));
  console.log(fails ? `VPN ROUTES PROBE FAIL (${fails})` : "VPN ROUTES PROBE PASS");
  process.exit(fails ? 1 : 0);
})();
