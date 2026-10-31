/* Probe (2026-10-31): the Bluetooth memory profile on Settings > Bluetooth
   (ble_manager v5): the Memory profile row with its three words, the four
   controller knobs only under Custom and behind Show advanced, the
   advertising and PHY selects greyed under Lean with the note that says why
   (and brought back to legacy / 1m when Lean is picked over a BLE 5 word),
   and the VPN page's hold note from /api/vpn `reason` (issue #1024) with the
   Relay row. Runs over the mock API. Prints BLE RAM PROBE PASS. */
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import { JSDOM, VirtualConsole } from "jsdom";

const here = dirname(fileURLToPath(import.meta.url));
const html = readFileSync(join(here, "preview.html"), "utf-8");
let fails = 0;
const check = (n, ok, extra) => { console.log((ok ? "PASS " : "FAIL ") + n + (extra !== undefined ? "  [" + JSON.stringify(extra) + "]" : "")); if (!ok) { fails++; process.exitCode = 1; } };
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function load(hash, preset) {
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
  return { w: dom.window, d: () => dom.window.document, errs };
}

/* visible through its ancestors too: cards fold by hiding a wrapper, not each row */
const shown = (el) => { if (!el) return false; for (let e = el; e && e.id !== "view"; e = e.parentElement) { if (e.style && e.style.display === "none") return false; } return true; };

(async () => {
  /* ---- the Bluetooth card, Lean as the device has it ---- */
  let { w, d, errs } = load("#/settings/bluetooth", { ble: { enabled: true, ble_ram_profile: "lean", advertising: "legacy", phy: "1m" } });
  await sleep(1400);
  const $ = (sel) => d().querySelector(sel), $$ = (sel, root) => [...(root || d()).querySelectorAll(sel)];
  const row = (k) => $('#view .frow[data-key="' + k + '"]');
  const sel = (k) => row(k) && row(k).querySelector("select");
  const pick = (k, v) => { const s = sel(k); s.value = v; s.dispatchEvent(new w.Event("change", { bubbles: true })); };
  const leanNote = () => $('#view [data-key="ble_lean_note"]');
  const bleCard = () => row("ble_ram_profile") && row("ble_ram_profile").closest(".section");

  check("Bluetooth page renders the memory profile row with its three words",
    !!sel("ble_ram_profile") && [...sel("ble_ram_profile").options].map((o) => o.value).join(",") === "full,lean,custom",
    sel("ble_ram_profile") && [...sel("ble_ram_profile").options].map((o) => o.value));
  check("the words read Full (BLE 5 options) / Lean (recommended) / Custom",
    !!sel("ble_ram_profile") && /Full \(BLE 5 options\)/.test(sel("ble_ram_profile").options[0].textContent) &&
    /Lean \(recommended\)/.test(sel("ble_ram_profile").options[1].textContent) && /^Custom$/.test(sel("ble_ram_profile").options[2].textContent.trim()));
  check("Lean is selected and the profile row is not behind Show advanced",
    sel("ble_ram_profile").value === "lean" && shown(row("ble_ram_profile")));
  check("help text names what Lean frees and what Full keeps",
    /Lean frees about \d+ KB/.test(row("ble_ram_profile").textContent) && /Full keeps the BLE 5 options/.test(row("ble_ram_profile").textContent));
  check("under Lean the advertising and PHY selects are greyed",
    !!sel("advertising") && sel("advertising").disabled && !!sel("phy") && sel("phy").disabled);
  check("the Lean note shows under the advertising row and says legacy on 1M, one phone",
    shown(leanNote()) && /legacy advertising on the 1M PHY/.test(leanNote().textContent) && /one phone/.test(leanNote().textContent) &&
    leanNote().previousElementSibling === row("advertising"));
  check("the three controller knobs stay hidden under Lean",
    ["ble_max_act", "ble_acl_tx_bufs", "ble_scan_cache"].every((k) => row(k) && !shown(row(k))));

  /* Full: the BLE 5 selects come back */
  pick("ble_ram_profile", "full"); await sleep(120);
  check("picking Full stages one change, frees the two selects and hides the note",
    w.eval("store").count() === 1 && !sel("advertising").disabled && !sel("phy").disabled && !shown(leanNote()));
  pick("advertising", "both"); pick("phy", "2m"); await sleep(120);
  check("a BLE 5 choice (advertising both, PHY 2m) is accepted under Full",
    sel("advertising").value === "both" && sel("phy").value === "2m" && w.eval("store").count() === 1);

  /* back to Lean: the BLE 5 words are brought back to the ones Lean accepts */
  pick("ble_ram_profile", "lean"); await sleep(120);
  check("picking Lean over a BLE 5 choice resets advertising to legacy and PHY to 1m, greys them and shows the note",
    sel("advertising").value === "legacy" && sel("phy").value === "1m" && sel("advertising").disabled && sel("phy").disabled && shown(leanNote()));

  /* Custom: the knobs, behind Show advanced */
  pick("ble_ram_profile", "custom"); await sleep(120);
  check("under Custom the selects are free and the knobs wait behind Show advanced",
    !sel("advertising").disabled && !shown(leanNote()) && !shown(row("ble_max_act")) && !!row("ble_max_act"));
  const adv = $$("button", bleCard()).find((b) => /Show advanced/.test(b.textContent));
  check("the Bluetooth card has a Show advanced toggle", !!adv);
  if (adv) { adv.click(); await sleep(120); }
  check("Show advanced reveals the three knobs with their labels",
    ["ble_max_act", "ble_acl_tx_bufs", "ble_scan_cache"].every((k) => shown(row(k))) &&
    /Controller activities/.test(row("ble_max_act").textContent) && /Static ACL TX buffers/.test(row("ble_acl_tx_bufs").textContent) &&
    /Scan duplicate cache/.test(row("ble_scan_cache").textContent));
  check("the knob help says 0 turns scanning off and warns about fewer than 12 buffers",
    /0 turns the scan feature off/.test(row("ble_scan_cache").textContent) && /Fewer than 12/.test(row("ble_acl_tx_bufs").textContent));
  pick("ble_ram_profile", "lean"); await sleep(120);
  check("back on Lean the knobs fold away again", !shown(row("ble_max_act")) && shown(leanNote()));
  check("no page errors on the Bluetooth tab", errs.length === 0, errs.slice(0, 2));

  /* ---- Bluetooth off: only the switch ---- */
  w.close();
  ({ w, d, errs } = load("#/settings/bluetooth", { ble: { enabled: false } }));
  await sleep(2000);
  /* the Bluetooth card's own rows: `enabled` exists on other cards of the page too */
  const bleCard2 = [...d().querySelectorAll("#view .section")].find((c) => /^Bluetooth/.test(((c.querySelector(".legend, h3") || {}).textContent || "").trim()));
  const row2 = (k) => bleCard2 && bleCard2.querySelector('.frow[data-key="' + k + '"]');
  check("with Bluetooth off the profile row and the note stay folded, the switch stays",
    !!bleCard2 && !shown(row2("ble_ram_profile")) && !shown(bleCard2.querySelector('[data-key="ble_lean_note"]')) && shown(row2("enabled")),
    [!!bleCard2, !!row2("ble_ram_profile"), shown(row2("ble_ram_profile")), !!row2("enabled"), shown(row2("enabled"))]);

  /* ---- the VPN page: a Tailscale tunnel held for internal RAM ---- */
  const reason = "not started: 13 KB of internal RAM free, Tailscale needs 30 KB: set the Bluetooth memory profile to Lean (about 15 KB), turn Bluetooth off (about 40 KB), or use WireGuard";
  w.close();
  ({ w, d, errs } = load("#/vpn", { vpn: { state: "waiting", type: "tailscale", derp: false, reason } }));
  await sleep(2000);
  const hold = () => d().querySelector('[data-key="vpn_hold_note"]');
  check("VPN page shows the hold note with the firmware's reason", shown(hold()) && /^Tailscale not started: 13 KB/.test(hold().textContent.trim()) && /Bluetooth memory profile to Lean/.test(hold().textContent));
  check("the hold note is a warning placed above the settings card", hold().classList.contains("warn") && hold().nextElementSibling && /VPN Settings/.test(hold().nextElementSibling.textContent));
  const relay = [...d().querySelectorAll("#view *")].find((e) => e.children.length === 0 && e.textContent.trim() === "Relay");
  check("the debug block carries a Relay row reading down", !!relay && /down/.test((relay.parentElement || {}).textContent || ""));
  check("the badge says waiting", /waiting/i.test(d().querySelector("#view .chip").textContent));

  w.close();
  ({ w, d, errs } = load("#/vpn", {}));
  await sleep(2000);
  check("a connected WireGuard tunnel shows no hold note and no relay state", !shown(hold()) &&
    (() => { const r = [...d().querySelectorAll("#view *")].find((e) => e.children.length === 0 && e.textContent.trim() === "Relay"); return !!r && /-/.test((r.parentElement || {}).textContent || ""); })());
  check("no page errors on the VPN page", errs.length === 0, errs.slice(0, 2));

  console.log(fails ? `BLE RAM PROBE FAIL (${fails})` : "BLE RAM PROBE PASS");
  process.exit(fails ? 1 : 0);
})();
