/* WiCAN web-UI preview: in-page mock of the device /api + /ws surface.
 * Injected before the app script by make_preview.py; window.fetch and
 * window.WebSocket are replaced. Data shapes mirror the real firmware
 * (settings/schemas auto-extracted from the C field tables into
 * __MOCK_SETTINGS__; the rest hand-written from live captures). */
(function () {
  "use strict";
  const S = window.__MOCK_SETTINGS__ || {};

  /* ---- realistic value overrides on top of the extracted defaults ---- */
  const ov = (c, v) => { if (S[c]) Object.assign(S[c].values, v); };
  ov("wifi_manager", { mode: "apsta", sta_ssid: "HomeWiFi", sta_password: "********", ap_ssid: "WiCAN_E349" });
  ov("ble_manager", { enabled: false });
  ov("mqtt_manager", { enabled: true, broker: "mqtt://10.42.0.1:1883", topic_prefix: "wican" });
  ov("ha_webhooks", { enabled: true, url: "http://homeassistant.local:8123/api/webhook/8f4a21be", interval_s: 15 });
  ov("autopid", { enabled: true, vehicle: "Corolla 2021" });
  ov("data_logger", { enabled: false });
  ov("vpn_manager", { enabled: true, type: "wireguard", address: "10.66.0.2", endpoint: "vpn.example.com", port: 51820 });
  ov("usb_host_manager", { enabled: true, role: "host" });
  ov("event_manager", {
    enabled: true,
    timers: [{ name: "dest1", period_s: 5 }],
    rules: [
      { name: "dest1", on: "timer.tick", match: { timer: "dest1" }, do: "mqtt.publish", with: { topic: "~/autopid", payload: "${autopid.data}" } },
      { name: "bump_event", on: "imu.bump", do: "mqtt.publish", with: { topic: "~/events", payload: "{\"event\":\"bump\"}" } },
      { name: "low_batt", on: "battery.threshold", enabled: false, do: "http.post", with: { url: "http://example.com/alert", body: "{\"v\":${volts}}" } },
    ],
  });

  const now = () => Math.floor(Date.now() / 1000);
  const state = {
    scan: { status: "idle", found: 0 },
    /* this boot's restart record (probes swap it: power_wake / periodic_wake / panic …) */
    lastRestart: { reason: "software", planned: true, planned_reason: "config_apply", source: "config_server" },
    dirs: new Set(),            /* folders created through /api/fs/mkdir */
    loggerRunning: false,       /* true -> /api/logger reports the active file (write-locked) */
    sdMounted: true,            /* false -> /api/status says no card, /sd listings fail */
    autopidCfg: {
      version: 1,
      groups: [{ name: "default", enabled_default: true, period_ms: 1000 }],
      pids: [
        { name: "RPM", cmd: "010C1", group: "default", period_ms: 1000, type: "std", parameters: [{ name: "RPM", expression: "(B2*256+B3)/4", unit: "rpm", min: 0, max: 8000 }] },
        { name: "Speed", cmd: "010D1", group: "default", period_ms: 1000, type: "std", parameters: [{ name: "Speed", expression: "B2", unit: "km/h", class: "speed", min: 0, max: 255 }] },
        { name: "Coolant", cmd: "01051", group: "default", period_ms: 5000, type: "std", parameters: [{ name: "Coolant", expression: "B2-40", unit: "°C", class: "temperature", min: -40, max: 215 }] },
        { name: "OilTemp", cmd: "2101AF1", group: "default", period_ms: 2000, type: "custom", parameters: [{ name: "OilTemp", expression: "B4-40", unit: "°C" }] },
      ],
      filters: [{ id: "0x18DAF110", name: "SOC_BMS", expression: "B5/2", unit: "%", monitor_ms: 1000 }],
    },
    dash: { RPM: 843, Speed: 0, Coolant: 88, SOC_BMS: 71.5 },
    polls: 1200, groupOn: true,
    files: { "/data/scripts/hello.be": "# hello.be\nlog('hello from the mock')\n" },   /* /api/fs upload/download/delete of small text files (dashboard layout, scripts) */
    scriptBusy: false, runs: 0, checks: 0, stops: 0,   /* the Scripts page */
    gateCalls: 0, loggerFile: "",   /* the dashboard history pauses the gate to read the active file */
    logEpoch: Math.floor(Date.now() / 1000) - 7200,   /* the fresh log files' epoch, fixed at load */
  };

  const STD_ROWS = [
    ["0104", "Engine Load", "B3*100/255", "%"], ["0105", "Coolant Temp", "B3-40", "°C"],
    ["010B", "Intake MAP", "B3", "kPa"], ["010C", "Engine RPM", "(B3*256+B4)/4", "rpm"],
    ["010D", "Vehicle Speed", "B3", "km/h"], ["010F", "Intake Air Temp", "B3-40", "°C"],
    ["0111", "Throttle", "B3*100/255", "%"], ["012F", "Fuel Level", "B3*100/255", "%"],
  ].map(([pid, name, expr, unit]) => ({
    pid: parseInt(pid.slice(2), 16), cmd: pid + "1", name,
    parameters: [{ name: name.replaceAll(" ", "_"), expression: expr, unit, class: "" }],
  }));

  const J = (o, status = 200) => ({
    ok: status < 300, status,
    headers: { get: (k) => (k.toLowerCase() === "content-type" ? "application/json" : null) },
    json: async () => JSON.parse(JSON.stringify(o)), text: async () => JSON.stringify(o),
  });
  const T = (s, status = 200) => ({
    ok: status < 300, status,
    headers: { get: (k) => (k.toLowerCase() === "content-type" ? "text/plain" : null) },
    json: async () => { throw new Error("not json"); }, text: async () => s,
  });

  const settingsList = () => ({
    components: Object.keys(S).sort().map((n) => ({ name: n, version: 1, degraded: false, pending_reboot: false })),
  });

  const FIXED = {
    "/api/status": () => J({
      bits: { awake: true, sleep: false, sta_connected: true, mqtt_connected: true, ble_connected: false, sdcard_mounted: state.sdMounted !== false, ble_enabled: false, sta_enabled: true, ap_enabled: true, autopid_enabled: true, home_mode: false, drive_mode: false, smartconnect: false, sta_ap_overlap: false, time_synced: true, vpn_enabled: true, wake_voltage_ok: true, eth_connected: false, autopid_idle: false, motion: false, sta_suspended: false, ap_suspended: false, ble_suspended: false },
      network_connected: true, uptime: "02:14:09", version: "v6.0.0-preview", partition: "ota_0",
      boot_count: 42, unexpected_resets: 1, device_id: "14c19f44e349",
      memory: { internal: { total: 274580, free: 71103, min_free: 63587, largest_block: 45056 }, psram: { total: 8272000, free: 7734508, min_free: 7524288, largest_block: 7274496 } },
      temp_c: 38.4,
      health: { log_errors: 0, log_warnings: 7,
        flash: { writes: 0, write_bytes: 0, erases: 0, erase_bytes: 0 },
        caps: { settings: { used: 34, cap: 40 }, cmdline: { used: 34, cap: 48 }, bridge_ep: { used: 14, cap: 24 } },
        faults: S.__faults ? S.__faults.length : 1 },
    }),
    "/api/faults": () => J({ faults: S.__faults || (S.__faults = [
      { code: "registry_headroom", detail: "bridge_tr at 3/4", count: 1,
        first_time: 1784400000, last_time: 1784400000 }]) }),
    "/api/wifi/status": () => J({ enabled: true, sta_connected: true, ip: "10.42.0.62", ap_started: true, ap_default_password: state.apDefaultPassword === true, clients: 0, ap_ip: "192.168.80.1", dns: ["10.42.0.1", "1.1.1.1"],
      sta_attempt: { ssid: "HomeWiFi", reason: 204, fail_count: 3, deprioritised: true } }),
    "/api/webhook": () => J({ url: S.ha_webhooks.values.url, enabled: true, interval: 15, manual_override: false, data_mode: "changed", gzip: false, status: "ok", last_post: new Date().toISOString(), retries: 0, success_count: 512, fail_count: 3, last_error: "", last_error_time: "" }),
    "/api/vpn": () => J({ state: "connected", type: "wireguard", endpoint: "vpn.example.com:51820", ts_ip: "", ts_peers: 0, connects: 1, failures: 0, uptime_s: 8040 }),
    "/api/usb": () => J({ enabled: true, device_present: true, host_active: true, eth_connected: true, driver: "cdc_ncm", ip: "192.168.7.2", attaches: 1 }),
    /* espnetlink_link: paired steady state by default; state.espnlBlocked
       flips to the fresh-device hold (factory AP password, 2026-09-07) */
    "/api/espnetlink": () => J(state.espnlBlocked
      ? { enabled: true, mode: "wifi_modem", auto_pair: true, paired: false, ssid: "", device_id: "206ef1894a5d", uplink: "none", on_link: false, host: "",
          pair_blocked_factory_pw: true, last_error: "the access point still has the factory password: set a new one (8 to 63 characters)",
          usb: { attached: true, pair_state: "idle", cuts: 0, vbus_cycles: 0, errors: 0 }, gps: { valid: false, age_ms: 0 }, dongle: { valid: false }, polls: 0, failures: 0, link_ups: 0 }
      : { enabled: true, mode: "wifi_modem", auto_pair: true, paired: true, ssid: "ESPNetLink_894A5D", device_id: "206ef1894a5d", uplink: "espnetlink", on_link: true, host: "192.168.80.1",
          pair_blocked_factory_pw: false, last_error: "",
          usb: { attached: false, pair_state: "idle", cuts: 0, vbus_cycles: 0, errors: 0 }, gps: { valid: true, age_ms: 1200, satellites: 7 },
          dongle: { valid: true, lte_connected: true, rssi_dbm: -59, operator: "ALDI Mobile", network_type: "eMTC", gps_fix: true, usb_data: false }, polls: 42, failures: 0, link_ups: 1 }),
    "/api/usb/acm": () => J({ connected: true }),
    "/api/gps": () => J({ valid: true, latitude: -37.905350, longitude: 145.145047, accuracy: 6, altitude: 88.8, speed: 1.0, heading: 270.5, satellites: 7, age_ms: 1200 }),
    "/api/battery": () => J({ voltage: 12.52 }),
    /* the native CAN bus (state.canEnabled=false: a fresh device, bus off) */
    "/api/can": () => J({ enabled: state.canEnabled !== false, running: state.canEnabled !== false, silent: false, baud_kbps: 500, state: state.canEnabled === false ? "stopped" : "running", tx: 1543, rx: 89231, tx_errors: 0, rx_errors: 0, arb_lost: 0, bus_errors: state.busErrors || 0, rx_missed: 0, dispatch_drops: 0, bus_off: 0, recoveries: 0 }),
    "/api/bridges": () => J({ bridges: ((S.bridge_manager && S.bridge_manager.values.bridges) || []).map((b) => ({ ...b, up: b.enabled !== false, stats: { a2b_chunks: 0, b2a_chunks: 0, a2b_bytes: 0, b2a_bytes: 0, send_errors: 0, codec_errors: 0 } })) }),
    "/api/ws": () => J({ channels: ((S.websocket_manager && S.websocket_manager.values.channels) || []).map((c) => ({ ...c, up: c.enabled !== false, stats: { clients: 0, frames_in: 0, frames_out: 0, bytes_in: 0, bytes_out: 0, rx_drops: 0, tx_drops: 0, refused: 0 } })) }),
    "/api/autopid": () => { state.polls += state.groupOn ? 4 : 0; const nowUs = Date.now() * 1000; return J({
      groups: [{ name: "default", enabled: state.groupOn, period_ms: 1000 }],
      params: Object.entries(state.dash).map(([name, value]) => ({
        name, value: value + (Math.random() - 0.5) * (name === "RPM" ? 40 : 2),
        unit: { RPM: "rpm", Speed: "km/h", Coolant: "°C", SOC_BMS: "%" }[name] || "",
        ts_us: nowUs - (name === "SOC_BMS" ? 45e6 : 800e3),   /* SOC_BMS deliberately stale */
      })).concat([{ name: "gps_speed", unit: "km/h", value: 61.6, ts_us: nowUs - 1.2e6, external: true }]),
      stats: { running: state.groupOn, paused_voltage: false, polls_ok: state.polls, polls_failed: 2, pids: 4, filters: 1,
        period_floor_ms: 50, sub_floor_pids: 0, now_us: nowUs },
    }); },
    "/api/events/sources": () => J([
      { event: "timer.tick", description: "A named timer fired" },
      { event: "autopid.param", description: "A parameter value updated" },
      { event: "imu.bump", description: "Bump/impact detected" },
      { event: "imu.motion", description: "Motion state changed" },
      { event: "battery.threshold", description: "Voltage crossed a watch threshold" },
      { event: "status.bit", description: "A device status bit changed" },
      { event: "vpn.state", description: "VPN connection state changed" },
    ]),
    "/api/events/actions": () => J([
      { name: "mqtt.publish" }, { name: "http.post" }, { name: "led.alert" },
      { name: "logger.gate" }, { name: "can.send" },
    ]),
    "/api/events/values": () => J(["autopid.data", "autopid.RPM", "time.iso", "status.uptime", "battery.volts"]),
    "/api/autopid/dtc": () => J({ enabled: false, codes: [], last_scan: null }),
    "/api/autopid/dtc/db": () => J({ dbs: [] }),
    /* DBC files + signals for the CAN Monitor's decode panel (state.dbcs / state.dbcSignals) */
    "/api/autopid/dbc": () => J({ dbcs: state.dbcs || [], max: 4 }),
    "/api/autopid/dbc/signals": (full) => { const q = new URLSearchParams((full || "").split("?")[1] || ""); const db = q.get("db");
      const items = (state.dbcSignals || []).filter((s) => !db || s.db === db); return J({ total: items.length, items }); },
    "/api/logger": () => J({ enabled: !!state.loggerRunning, running: !!state.loggerRunning, paused: false, storage_ok: false, file: state.loggerFile, file_rows: 0, files: 0, queued: 0,
      written: 0, dropped: 0, errors: 0, rotations: 0,
      can: { enabled: false, file: "", file_rows: 0, files: 0, queued: 0, frames_written: 0, frames_dropped: 0, rotations: 0 },
      salvaged: state.loggerSalvaged || 0, corrupt: state.loggerCorrupt || 0, dir: "/sd/logs" }),
    "/api/fs/info": (full) => (/path=\/data/.test(full || "") ? J({ total: 6029312, used: 1060864 })
      : !state.sdMounted ? J({ error: "invalid path" }, 400) : J({ total: 3038806016, used: 327680 })),
    "/api/rtc": () => J({ time: new Date().toISOString(), valid: true, rtc: null, sntp: { enabled: true, server: "pool.ntp.org", last_sync: null } }),
    "/api/logs/status": () => J({ dropped: 0, sinks: { ring: true, uart: true } }),
    "/api/logs/ring": () => T("I (1234) main: WiCAN v6 preview mock\nI (1240) wifi_manager: STA got IP 10.42.0.62\nI (2001) autopid: started (3 pids / default group)\nW (9004) event_manager: rule low_batt disabled\n"),
    "/api/status/tasks": () => J({ cores: 2, total_us: 23785179, tasks: [
      { name: "IDLE1", state: "R", core: 1, prio: 0, stack_hw: 776, runtime_us: 21699114 },
      { name: "IDLE0", state: "R", core: 0, prio: 0, stack_hw: 764, runtime_us: 20988014 },
      { name: "httpd", state: "X", core: -1, prio: 5, stack_hw: 5240, runtime_us: 913245 },
      { name: "autopid", state: "B", core: -1, prio: 5, stack_hw: 1508, runtime_us: 407121 },
      { name: "ha_webhook", state: "B", core: -1, prio: 4, stack_hw: 21012, runtime_us: 88012 },
      { name: "wifi", state: "B", core: 0, prio: 23, stack_hw: 1210, runtime_us: 1893201 },
    ] }),
    "/api/certs": () => J({ sets: [{ name: "homeca", ca: true, cert: false, key: false }] }),
    "/api/restart/history": () => J({ boot_count: 42, unexpected_resets: 1, records: [
      { seq: 42, ...state.lastRestart, flags: 0, boot_time: now() - 8040, time_valid: true, request_time: now() - 8041, request_uptime_ms: 120033 },
      { seq: 41, reason: "poweron", planned: false, planned_reason: "none", source: "unknown", flags: 0, boot_time: now() - 90000, time_valid: true, request_time: 0, request_uptime_ms: 0 },
    ] }),
    "/api/events/log": () => J({
      stats: { published: 812, fired: 640, action_errors: 1 },
      events: [
        { ts: now() - 62, source: "timer", name: "tick", data: { timer: "dest1" }, fired: ["dest1"] },
        { ts: now() - 31, source: "autopid", name: "param", data: { name: "RPM", value: 843 }, fired: [] },
        { ts: now() - 5, source: "timer", name: "tick", data: { timer: "dest1" }, fired: ["dest1"] },
      ],
    }),
    "/api/scripts": () => J({
      scripts: Object.keys(state.files).filter((p) => p.startsWith("/data/scripts/") && p.endsWith(".be")).map((p) => ({ name: p.slice(14), size: state.files[p].length })),
      dir: "/data/scripts", busy: state.scriptBusy, enabled: !!(S.script_engine && S.script_engine.values.enabled), max_runtime_ms: 10000 }),
    /* the engine's self-description (a subset of the firmware's tables; shapes identical) */
    "/api/scripts/reference": () => J({
      language: "Berry 1.1.0", enabled: !!(S.script_engine && S.script_engine.values.enabled), allow_reflash: false,
      limits: { src_max: 8192, file_max: 65536, out_max: 4096, sleep_max_ms: 60000, name_max: 40, resp_max_bytes: 128, max_runtime_ms: 10000 },
      groups: [{ id: "basics", title: "Output & timing" }, { id: "obd", title: "OBD-II / UDS, one request at a time" }, { id: "session", title: "UDS conversation on a claimed bus" }],
      bindings: [
        { name: "log", sig: "log(msg)", group: "basics", ret: "nil", doc: "Print a line to the run output and the device log. Numbers need str().", ex: "log('rpm ' + str(rpm))" },
        { name: "sleep_ms", sig: "sleep_ms(ms)", group: "basics", ret: "nil", doc: "Pause for up to 60 000 ms. The run budget keeps counting while asleep.", ex: "sleep_ms(500)" },
        { name: "uds", sig: "uds(tx, rx, hexreq)", group: "obd", ret: "response hex string, or nil", doc: "One request to an ECU over ISO-TP with 11-bit CAN ids. Sets uds_ok and uds_nrc.", ex: "var r = uds(0x7E0, 0x7E8, '01 0C')" },
        { name: "obd_request", sig: "obd_request(hexreq[, timeout_ms])", group: "session", ret: "response hex string, or nil", doc: "Send a UDS request on the claimed connection and wait for the final answer.", ex: "var r = obd_request('22 F1 90')" },
      ],
      globals: [{ name: "uds_ok", doc: "1 when the last request got an answer." }, { name: "uds_nrc", doc: "The negative response code, or -1." }, { name: "evt_<field>", doc: "One global per field of the trigger event." }],
      primer: [{ title: "Variables", code: "var rpm = 0\nrpm += 1", note: "Declare with var." }, { title: "Loops", code: "for i : 0..4  log(str(i))  end", note: "0..4 is inclusive." }],
      errors: [{ match: "obd_claim() first", hint: "Call obd_claim(tx, rx) before obd_request()." }, { match: "syntax_error", hint: "Every block ends with end. The line number is in the message." }, { match: "my_error", hint: "A raise in the script." }],
      rules: { action: "script.run", with: "{\"name\": \"<script>\"}", event: "script.done" },
    }),
    "/api/scripts/examples": (full) => {
      const id = new URLSearchParams((full || "").split("?")[1] || "").get("id");
      if (id === "hello") return T("# Hello, WiCAN - the basics.\nlog('Hello from WiCAN')\nfor i : 1..3\n  log(str(i))\nend\n");
      if (id === "vin") return T("# Read the VIN.\nvar r = uds(0x7DF, 0x7E8, '09 02')\nlog(str(r))\n");
      if (id) return T("no such example", 404);
      return J({ examples: [
        { id: "hello", title: "Hello, WiCAN", desc: "Output, variables, loops and timing. Runs without a vehicle.", needs: "", level: 1, size: 812 },
        { id: "vin", title: "Read the VIN", desc: "Mode 09 first, UDS data identifier F190 as the fallback, decoded to text.", needs: "vehicle", level: 1, size: 1040 },
      ] });
    },
    "/api/j2534": () => J({ enabled: false, allow_reflash: false, allow_lan: false, sessions: 0 }),
    "/api/sleep": () => J({ state: "awake", voltage: 12.52, sleep_v: 12.2, wake_v: 13.2 }),
    /* fixtures per folder + whatever probes uploaded (state.files) or created (state.dirs) */
    "/api/fs/list": (full) => {
      const p = ((new URLSearchParams((full || "").split("?")[1] || "")).get("path") || "/data").replace(/(.)\/$/, "$1");
      if (!/^\/(data|sd)(\/|$)/.test(p) || (!state.sdMounted && /^\/sd/.test(p))) return J({ error: "invalid path" }, 400);
      const fixed = p === "/sd/logs" ? [
          { name: "dl_" + String(state.logEpoch).padStart(10, "0") + ".jsonl", dir: false, size: 240000 },
          { name: "dl_" + String(state.logEpoch).padStart(10, "0") + ".csv", dir: false, size: 120000 },
          { name: "dl_" + String(state.logEpoch).padStart(10, "0") + ".wdl", dir: false, size: 4096 },
          { name: "dl_" + String(state.logEpoch).padStart(10, "0") + ".db", dir: false, size: 28672 },
          { name: "dl_1784563543.db", dir: false, size: 28672 }, { name: "can_1784563543.wdl", dir: false, size: 0 },
          { name: "dl_1784329639.jsonl", dir: false, size: 42480 }, { name: "can_1783689511.asc", dir: false, size: 1498 },
        ]
        : p === "/sd" ? [{ name: "logs", dir: true, size: 0 }, { name: "fw", dir: true, size: 0 }, { name: "bench_ok.txt", dir: false, size: 33 }]
        : p === "/data" ? [{ name: "autopid", dir: true, size: 0 }, { name: "scripts", dir: true, size: 0 }, { name: "config.json", dir: false, size: 1420 }]
        : [];
      const seen = new Set(fixed.map((e) => e.name)), extra = [];
      const child = (k) => (k.startsWith(p + "/") && !k.slice(p.length + 1).includes("/")) ? k.slice(p.length + 1) : null;
      for (const dname of state.dirs) { const n = child(dname); if (n && !seen.has(n)) { seen.add(n); extra.push({ name: n, dir: true, size: 0 }); } }
      for (const f of Object.keys(state.files)) { const n = child(f); if (n && !seen.has(n)) { seen.add(n); extra.push({ name: n, dir: false, size: state.files[f].length }); } }
      return J({ path: p, entries: [...fixed, ...extra] });
    },
    "/api/autopid/std_scan": () => J(state.scan),
    "/api/autopid/std_scan/result": () => (state.scan.status === "done"
      ? J({ version: 1, protocol: "6", supported: STD_ROWS, found: STD_ROWS.length, ts: now() })
      : J({ error: "no scan stored" }, 404)),
    "/api/autopid/std_table": () => J({ version: 1, supported: STD_ROWS, found: STD_ROWS.length }),
    "/api/autopid/config": () => J(state.autopidCfg),
    "/api/settings": () => J(settingsList()),
  };

  async function handle(path, opts, full) {
    const method = ((opts && opts.method) || "GET").toUpperCase();
    const body = opts && typeof opts.body === "string" ? (() => { try { return JSON.parse(opts.body); } catch { return null; } })() : null;

    /* mutations */
    if (method === "POST" && path === "/api/autopid/std_scan") {
      state.scan = { status: "running", found: 0 };
      setTimeout(() => { state.scan = { status: "done", found: STD_ROWS.length }; }, 2500);
      return J({ ok: true });
    }
    if (method === "PUT" && path === "/api/autopid/config") { state.autopidCfg = body || state.autopidCfg; return J({ ok: true }); }
    const q = new URLSearchParams((full || "").split("?")[1] || "");
    if (method === "POST" && path === "/api/autopid/dbc") { const n = q.get("name") || "dbc"; state.dbcs = [...(state.dbcs || []), { name: n, messages: 1, signals: 1, bytes: String((opts && opts.body) || "").length }]; return J({ ok: true, name: n, messages: 1, signals: 1 }); }
    if (method === "POST" && path === "/api/fs/mkdir") { const p = q.get("path"); if (!p) return J({ error: "invalid path" }, 400); state.dirs.add(p); return J({ ok: true }); }   /* query string, like the firmware */
    if (method === "POST" && path === "/api/fs/upload") {
      let txt = "";
      try {
        if (typeof opts.body === "string") txt = opts.body;
        else if (opts.body && opts.body.get && opts.body.get("file")) txt = await opts.body.get("file").text();   /* FormData (XHR shim or fetch) */
        else txt = new TextDecoder().decode(opts.body);
      } catch (e) { txt = ""; }
      state.files[q.get("path")] = txt; return J({ ok: true, path: q.get("path"), size: txt.length });
    }
    if (method === "DELETE" && path === "/api/fs/file") {
      const p = q.get("path") || "";
      if (state.loggerRunning && p === "/sd/logs/" + state.loggerFile) return J({ error: "file in use" }, 409);
      if (state.dirs.has(p)) {
        const busy = Object.keys(state.files).some((f) => f.startsWith(p + "/")) || [...state.dirs].some((d) => d.startsWith(p + "/"));
        if (busy) return J({ error: "invalid path" }, 400);   /* the firmware refuses non-empty folders the same way */
        state.dirs.delete(p); return J({ ok: true });
      }
      delete state.files[p]; return J({ ok: true });
    }
    if (method === "POST" && path === "/api/scripts/run") {
      state.runs++;
      if (!(S.script_engine && S.script_engine.values.enabled)) return J({ ok: false, error: "busy or script_engine disabled" }, 409);
      const src = (body && body.src) || (body && body.name ? state.files["/data/scripts/" + body.name] : "") || "";
      if (/raise/.test(src)) return J({ ok: false, output: "before the error\n\nERROR: my_error: boom\nstack traceback:\n\tstring:3: in function `main`\n" });
      return J({ ok: true, output: "hello from the mock\nsum 1..10 = 55\n" });
    }
    if (method === "POST" && path === "/api/scripts/check") {
      state.checks++;
      if (!(S.script_engine && S.script_engine.values.enabled)) return J({ ok: false, error: "busy or script_engine disabled" }, 409);
      return /syntax/.test((body && body.src) || "") ? J({ ok: false, error: "syntax_error: string:2: unexpected symbol near 'end'\n" }) : J({ ok: true });
    }
    if (method === "POST" && path === "/api/scripts/stop") { state.stops++; return J({ ok: true }); }
    if (method === "GET" && path === "/api/logger/export") {
      /* the params stream: 120 points, 30 s apart, for the requested name — NDJSON or CSV rows */
      const fmt = S.data_logger && S.data_logger.values.format;
      if (fmt !== "jsonl" && fmt !== "csv") return T("params stream is not jsonl or csv", 400);
      const name = q.get("name") || "RPM", nowMs = Date.now(), base = state.dash[name] != null ? state.dash[name] : 50;
      let out = fmt === "csv" ? "ts_ms,param,value\n" : "";
      for (let i = 120; i >= 1; i--) {
        const ts = nowMs - i * 30000, value = base + Math.sin(i / 7) * (name === "RPM" ? 300 : 5);
        out += fmt === "csv" ? ts + ",autopid." + name + "," + value.toFixed(6) + "\n" : JSON.stringify({ ts, param: "autopid." + name, value }) + "\n";
      }
      return T(out + JSON.stringify({ _cursor: "0:0", more: false }) + "\n");
    }
    if (method === "POST" && path === "/api/logger/gate") { state.gateCalls++; return J({ ok: true }); }
    if (method === "GET" && path === "/api/fs/download") {
      const p = q.get("path");
      /* a binary log fixture the probe injects (window.__WDL_FIXTURE__: Uint8Array) */
      if (/\.wdl$/.test(p || "") && window.__WDL_FIXTURE__) {
        const buf = window.__WDL_FIXTURE__.buffer.slice(window.__WDL_FIXTURE__.byteOffset, window.__WDL_FIXTURE__.byteOffset + window.__WDL_FIXTURE__.byteLength);
        return { ok: true, status: 200, headers: { get: () => "application/octet-stream" }, arrayBuffer: async () => buf, text: async () => "", json: async () => { throw new Error("binary"); } };
      }
      const t = state.files[p]; return t != null ? T(t) : J({ error: "not found" }, 404);
    }
    if (method === "POST" && path === "/api/autopid/group") { if (body && typeof body.enabled === "boolean") state.groupOn = body.enabled; return J({ ok: true }); }
    if (method === "POST" && path === "/api/settings/submit") return J({ reboot: false });
    if (method === "POST" && path === "/api/restart") return J({ ok: true });
    if (method === "POST" && path === "/api/faults/clear") { S.__faults = []; return J({ cleared: true }); }
    if (method === "POST" && path === "/api/vpn/keygen") return J({ public_key: "MockPubKey000000000000000000000000000000000=" });
    if (method === "POST" && path === "/api/autopid/dtc/scan") return J({ ok: true });
    if (method === "POST" && path === "/api/autopid/test") {
      const expr = body && body.expression;
      return J({ ok: true, raw: "7EC 10 27 62 01 01 FF F7 E7\n7EC 21 FF 84 84 84 84 84 84", elapsed_ms: 42,
        value: expr ? Math.round(Math.random() * 900) / 10 : undefined });
    }
    if (method === "POST" && path === "/api/usb/acm/cmd") {
      // canned dongle-console replies captured from a live ESPNetLink
      // (v1.22, BG95-M5) — echo + payload + esp> prompt, as on the wire
      const cmd = (body && body.cmd) || "";
      const wrap = (b) => J({ ok: true, response: cmd + "\r\n\r\r\n" + b + "\r\r\nOK\r\r\nesp> ", connected: true });
      if (cmd.startsWith("lte -j")) return wrap(JSON.stringify({
        stage: "connected", status_valid: true, rssi: 27, rssi_dbm: -59, ber: 99,
        attached: true, ppp_connected: true, ip: "100.88.65.162",
        operator: "ALDI Mobile", operator_act: 8, network_type: "eMTC",
        modem_available: true, imsi: "505015634201328", iccid: "89610140000735834363",
        imei: "862382067194737", modem_model: "BG95-M5",
        modem_fw: "BG95M5LAR02A03_01.006.00.000", sim_status: "READY",
        cereg: "+CEREG: 0,1", creg: "+CREG: 0,1", csq: "+CSQ: 27,99",
        qnwinfo: '+QNWINFO: "eMTC","50501","LTE BAND 28",9410',
        serving_cell: "+QENG: servingcell,NOCONN", pdp_context: "+CGDCONT: 1,IP,telstra.internet",
        cops: "+COPS: 0,0,ALDI Mobile,8", psm_enabled: false, edrx_cycle_s: null,
      }));
      if (cmd.startsWith("gps")) return wrap(JSON.stringify({
        valid: true, lat: -37.905350, lon: 145.145047, satellites: 7, sats_in_view: 11,
        fix_quality: 1, fix_type: 3, altitude_m: 88.8, hdop: 1.2, pdop: 1.9, vdop: 1.4,
        speed_knots: 0.02, speed_kmph: 0.04, course_deg: 0, age_ms: 480,
        agnss_enabled: true, time_injected: true, position_injected: true,
        cached_lat: -37.905350, cached_lon: 145.145047, cached_alt: 88.8,
      }));
      if (cmd.startsWith("ver")) return wrap("ESPNetLink USB CLI (CDC-ACM)\r\nFW v1.22-31 (preview)\r\nESP-IDF v6.0.2\r\nOK");
      return wrap("OK");
    }
    if (path === "/api/wifi/scan") return J({ networks: [
      { ssid: "HomeWiFi", rssi: -48, auth: "wpa2", channel: 6 },
      { ssid: "Neighbor", rssi: -77, auth: "wpa2", channel: 11 },
    ] });
    if (path === "/api/settings/backup") return T("{\"mock\":true}");
    if (path === "/api/settings/factory_reset") return J({ ok: true });

    /* settings values + schema */
    let m = path.match(/^\/api\/settings\/([a-z0-9_]+)\/schema$/);
    if (m) return S[m[1]] ? J(S[m[1]].schema) : J({ error: "unknown component" }, 404);
    m = path.match(/^\/api\/settings\/([a-z0-9_]+)$/);
    if (m) {
      const c = m[1];
      if (!S[c]) return J({ error: "unknown component" }, 404);
      if (method === "PUT") { state.puts = (state.puts || 0) + 1; S[c].values = { ...body }; return J({ changed: true }); }
      return J({ ...S[c].values, degraded: false, pending_reboot: false });
    }

    if (path.endsWith("/vehicle_profiles.json")) return J({"cars": [{"car_model": "AAA: Generic", "init": "ATSP6;", "pids": [{"pid": "010C1", "parameters": [{"name": "EngineRPM", "expression": "[B3:B4]*0.25", "unit": "RPM", "class": "frequency"}]}, {"pid": "010D1", "parameters": [{"name": "VehicleSpeed", "expression": "B3", "unit": "km/h", "class": "speed"}]}, {"pid": "01051", "parameters": [{"name": "Coolant", "expression": "B3-40", "unit": "°C", "class": "temperature"}]}, {"pid": "012F1", "parameters": [{"name": "FuelLevel", "expression": "B3/2.55", "unit": "%", "class": "none"}]}, {"pid": "010F1", "parameters": [{"name": "IntakeAirTemp", "expression": "B3-40", "unit": "°C", "class": "temperature"}]}, {"pid": "01111", "parameters": [{"name": "Throttle", "expression": "B3/2.55", "unit": "%", "class": "none"}]}, {"pid": "01101", "parameters": [{"name": "MAF", "expression": "[B3:B4]*0.01", "unit": "g/s", "class": "none"}]}, {"pid": "010A1", "parameters": [{"name": "FuelPressure", "expression": "B3*3", "unit": "kPa", "class": "pressure"}]}, {"pid": "01061", "parameters": [{"name": "ShortTermFuelTrim", "expression": "(B3/1.28)-100", "unit": "%", "class": "none"}]}, {"pid": "01A61", "parameters": [{"name": "Odometer", "expression": "[B3:B6]", "unit": "km", "class": "distance"}]}]}, {"car_model": "Hyundai: Ioniq2017", "init": "ATSP6;ATSH7E4;ATST96;", "pids": [{"pid": "21057", "parameters": [{"name": "SOC_DISPLAY", "expression": "B39/2", "unit": "%", "class": "battery"}, {"name": "SOH", "expression": "[B33:B34]/10", "unit": "%", "class": ""}]}, {"pid": "2101", "parameters": [{"name": "SOC_BMS", "expression": "B09/2", "unit": "%", "class": "battery"}, {"name": "Charger_Connected", "expression": "B14:5", "unit": "", "class": ""}, {"name": "Charging", "expression": "B14:7", "unit": "", "class": ""}, {"name": "HV_Charger_Connected", "expression": "B14:6", "unit": "", "class": ""}]}]}, {"car_model": "Kia/Hyundai: Niro/Soul/Kona", "init": "ATST96;", "pids": [{"pid_init": "ATSH7E4;", "pid": "2201019", "parameters": [{"name": "SOC_BMS", "expression": "B10/2", "unit": "%", "class": "battery"}, {"name": "Max_REGEN", "expression": "[B11:B12]/100", "unit": "kW", "class": "power"}, {"name": "Max_Power", "expression": "[B13:B14]/100", "unit": "kW", "class": "power"}, {"name": "Batt_Current", "expression": "(65536-([B17:B18]))/10", "unit": "A", "class": "current"}, {"name": "HV_Volts", "expression": "[B19:B20]/10", "unit": "V", "class": "voltage"}, {"name": "HV_Power", "expression": "([B19:B20]/10)*((65536-([B17:B18]))/10)", "unit": "W", "class": "power"}, {"name": "Batt_MaxT", "expression": "B21", "unit": "°C", "class": "temperature"}, {"name": "Batt_MinT", "expression": "B22", "unit": "°C", "class": "temperature"}, {"name": "Batt_Temp_1", "expression": "B23", "unit": "°C", "class": "temperature"}, {"name": "Batt_Temp_2", "expression": "B25", "unit": "°C", "class": "temperature"}, {"name": "Batt_Temp_3", "expression": "B26", "unit": "°C", "class": "temperature"}, {"name": "Batt_Temp_4", "expression": "B27", "unit": "°C", "class": "temperature"}, {"name": "Batt_InletT", "expression": "B30", "unit": "°C", "class": "temperature"}, {"name": "Max_Cell_V", "expression": "B31/50", "unit": "V", "class": "voltage"}, {"name": "Max_Cell_V_No", "expression": "B33", "unit": "none", "class": "none"}, {"name": "Min_Cell_V", "expression": "B34/50", "unit": "V", "class": "voltage"}, {"name": "Min_Cell_V_No", "expression": "B35", "unit": "none", "class": "none"}, {"name": "Aux_Batt_Volts", "expression": "B38*0.1", "unit": "V", "class": "voltage"}]}, {"pid_init": "ATSH7E4;", "pid": "2201057", "parameters": [{"name": "SOH", "expression": "[B34:B35]/10", "unit": "%", "class": "battery"}, {"name": "SOC_D", "expression": "B41/2", "unit": "%", "class": "battery"}, {"name": "Min_Cell_Det_No", "expression": "B39", "unit": "none", "class": "none"}, {"name": "Max_Cell_Det_No", "expression": "B36", "unit": "none", "class": "none"}, {"name": "Min_Cell_Det", "expression": "[B37:B38]/10", "unit": "%", "class": "battery"}]}, {"pid_init": "ATSH7E2;", "pid": "21014", "parameters": [{"name": "GearSelector_Raw", "expression": "B10", "unit": "none", "class": "none"}, {"name": "Speed_Vehicle", "expression": "[B19:B20]/100", "unit": "%", "class": "battery"}, {"name": "Car_Ready", "expression": "B26:3", "unit": "none", "class": "none"}, {"name": "Car_ParkBreak", "expression": "B26:5", "unit": "none", "class": "none"}]}]}]});
    if (FIXED[path]) return FIXED[path](full || path);
    return J({ error: "mock: " + method + " " + path + " not implemented" }, 404);
  }

  /* XMLHttpRequest over the same mock — pages that want upload progress use XHR */
  window.XMLHttpRequest = class {
    constructor() { this.upload = {}; this.status = 0; this.responseText = ""; }
    open(m, u) { this._m = m; this._u = u; }
    setRequestHeader() {}
    send(body) {
      const rel = String(this._u).replace(/^https?:\/\/[^/]+/, "");
      Promise.resolve(handle(rel.split("?")[0], { method: this._m, body }, rel))
        .then(async (r) => {
          if (this.upload.onprogress) this.upload.onprogress({ lengthComputable: true, loaded: 1, total: 1 });
          this.status = r.status || 200; this.responseText = r.text ? await r.text() : "";
          if (this.onload) this.onload();
        })
        .catch((e) => { if (this.onerror) this.onerror(e); });
    }
  };
  window.__mockState = state;   /* probes read counters (gate calls) and set the active log file */
  window.fetch = (url, opts) => {
    const u = String(url);
    const path = u.startsWith("http") ? new URL(u).pathname + (new URL(u).search || "") : u;
    const clean = path.split("?")[0];
    return Promise.resolve(handle(clean, opts, path));
  };

  /* ---- WebSocket mock: /ws/can emits slcan frames; others stay quiet ---- */
  window.WebSocket = class {
    constructor(url) {
      this.url = String(url); this.readyState = 0; this._timers = [];
      if (this.url.includes("/ws/can")) state.wsCanOpened = (state.wsCanOpened || 0) + 1;   /* the monitor must not open one until Connect */
      setTimeout(() => {
        /* state.wsCanRefuse: the channel is disabled on the device (handshake refused) */
        if (state.wsCanRefuse && this.url.includes("/ws/can")) { this.readyState = 3; this.onclose && this.onclose({}); return; }
        this.readyState = 1; this.onopen && this.onopen({}); this._start();
      }, 60);
    }
    _start() {
      if (this.url.includes("/ws/can")) {
        /* state.wsCanPeriod: ms between frames (read when the socket opens); every
           10th frame is a remote frame; the first four bytes are letters so the
           monitor's ASCII column shows something readable */
        const ids = ["123", "2C4", "3E8", "18DAF110"];
        let n = 0;
        this._timers.push(setInterval(() => {
          if (!this.onmessage) return;
          n++;
          if (n % 10 === 0) { this.onmessage({ data: "r7DF0\r" }); return; }
          const id = ids[n % ids.length];
          const dlc = 8; let data = "";
          for (let i = 0; i < dlc; i++) data += (i < 4 ? 0x41 + ((n + i) % 26) : (Math.random() * 256 | 0)).toString(16).padStart(2, "0").toUpperCase();
          const f = (id.length > 3 ? "T" : "t") + id + dlc + data;
          this.onmessage({ data: f + "\r" });
        }, state.wsCanPeriod || 120));
      }
      if (this.url.includes("/ws/cli")) {
        setTimeout(() => this.onmessage && this.onmessage({ data: "WiCAN preview console (mock)\r\nwican> " }), 150);
      }
    }
    send(d) {
      if (this.url.includes("/ws/can")) (state.wsSent = state.wsSent || []).push(String(d));   /* the monitor's transmit */
      if (this.url.includes("/ws/cli") && this.onmessage) {
        setTimeout(() => this.onmessage({ data: "(preview: no device)\r\nwican> " }), 80);
      }
    }
    close() { this._timers.forEach(clearInterval); this.readyState = 3; this.onclose && this.onclose({}); }
  };

  console.log("[wican-preview] mock api installed:", Object.keys(S).length, "components");
})();
