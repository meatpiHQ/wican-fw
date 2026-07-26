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
    autopidCfg: {
      version: 1,
      groups: [{ name: "default", enabled_default: true, period_ms: 1000 }],
      pids: [
        { name: "RPM", cmd: "010C1", group: "default", period_ms: 1000, type: "std", parameters: [{ name: "RPM", expression: "(B2*256+B3)/4", unit: "rpm" }] },
        { name: "Speed", cmd: "010D1", group: "default", period_ms: 1000, type: "std", parameters: [{ name: "Speed", expression: "B2", unit: "km/h", class: "speed" }] },
        { name: "Coolant", cmd: "01051", group: "default", period_ms: 5000, type: "std", parameters: [{ name: "Coolant", expression: "B2-40", unit: "°C", class: "temperature" }] },
        { name: "OilTemp", cmd: "2101AF1", group: "default", period_ms: 2000, type: "custom", parameters: [{ name: "OilTemp", expression: "B4-40", unit: "°C" }] },
      ],
      filters: [{ id: "0x18DAF110", name: "SOC_BMS", expression: "B5/2", unit: "%", monitor_ms: 1000 }],
    },
    dash: { RPM: 843, Speed: 0, Coolant: 88, SOC_BMS: 71.5 },
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
      bits: { awake: true, sleep: false, sta_connected: true, mqtt_connected: true, ble_connected: false, sdcard_mounted: true, ble_enabled: false, sta_enabled: true, ap_enabled: true, autopid_enabled: true, home_mode: false, drive_mode: false, smartconnect: false, sta_ap_overlap: false, time_synced: true, vpn_enabled: true, wake_voltage_ok: true, eth_connected: false, autopid_idle: false, motion: false, sta_suspended: false, ap_suspended: false, ble_suspended: false },
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
    "/api/wifi/status": () => J({ enabled: true, sta_connected: true, ip: "10.42.0.62", ap_started: true, clients: 0, ap_ip: "192.168.80.1", dns: ["10.42.0.1", "1.1.1.1"] }),
    "/api/webhook": () => J({ url: S.ha_webhooks.values.url, enabled: true, interval: 15, manual_override: false, data_mode: "changed", gzip: false, status: "ok", last_post: new Date().toISOString(), retries: 0, success_count: 512, fail_count: 3, last_error: "", last_error_time: "" }),
    "/api/vpn": () => J({ state: "connected", type: "wireguard", endpoint: "vpn.example.com:51820", ts_ip: "", ts_peers: 0, connects: 1, failures: 0, uptime_s: 8040 }),
    "/api/usb": () => J({ enabled: true, device_present: true, host_active: true, eth_connected: true, driver: "cdc_ncm", ip: "192.168.7.2", attaches: 1 }),
    "/api/usb/acm": () => J({ connected: true }),
    "/api/gps": () => J({ valid: true, latitude: -37.905350, longitude: 145.145047, accuracy: 6, altitude: 88.8, speed: 1.0, heading: 270.5, satellites: 7, age_ms: 1200 }),
    "/api/battery": () => J({ voltage: 12.52 }),
    "/api/can": () => J({ enabled: true, state: "running", running: true, baud_kbps: 500, silent: false, bitrate: 500000, mode: "normal", tx: 1543, rx: 89231, tx_err: 0, rx_err: 0, bus_off: 0, recoveries: 0, rx_missed: 0, dispatch_drops: 0 }),
    "/api/autopid": () => J({
      groups: [{ name: "default", enabled: true, period_ms: 1000 }],
      params: Object.entries(state.dash).map(([name, value]) => ({
        name, value: value + (Math.random() - 0.5) * (name === "RPM" ? 40 : 2),
        unit: { RPM: "rpm", Speed: "km/h", Coolant: "°C", SOC_BMS: "%" }[name] || "",
      })),
      stats: { running: true },
    }),
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
    "/api/autopid/dbc": () => J({ dbcs: [] }),
    "/api/logger": () => J({ enabled: false, gate: "open", stream: "params", format: "jsonl", file: "", bytes: 0, rows: 0, drops: 0 }),
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
      { seq: 42, reason: "software", planned: true, planned_reason: "config_apply", source: "config_server", flags: 0, boot_time: now() - 8040, time_valid: true, request_time: now() - 8041, request_uptime_ms: 120033 },
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
    "/api/scripts": () => J({ scripts: [{ name: "hello.be", size: 120 }] }),
    "/api/j2534": () => J({ enabled: false, allow_reflash: false, allow_lan: false, sessions: 0 }),
    "/api/sleep": () => J({ state: "awake", voltage: 12.52, sleep_v: 12.2, wake_v: 13.2 }),
    "/api/fs/list": () => J({ path: "/data", entries: [
      { name: "autopid", dir: true, size: 0 }, { name: "config.json", dir: false, size: 1420 },
    ] }),
    "/api/autopid/std_scan": () => J(state.scan),
    "/api/autopid/std_scan/result": () => (state.scan.status === "done"
      ? J({ version: 1, protocol: "6", supported: STD_ROWS, found: STD_ROWS.length, ts: now() })
      : J({ error: "no scan stored" }, 404)),
    "/api/autopid/std_table": () => J({ version: 1, supported: STD_ROWS, found: STD_ROWS.length }),
    "/api/autopid/config": () => J(state.autopidCfg),
    "/api/settings": () => J(settingsList()),
  };

  async function handle(path, opts) {
    const method = ((opts && opts.method) || "GET").toUpperCase();
    const body = opts && typeof opts.body === "string" ? (() => { try { return JSON.parse(opts.body); } catch { return null; } })() : null;

    /* mutations */
    if (method === "POST" && path === "/api/autopid/std_scan") {
      state.scan = { status: "running", found: 0 };
      setTimeout(() => { state.scan = { status: "done", found: STD_ROWS.length }; }, 2500);
      return J({ ok: true });
    }
    if (method === "PUT" && path === "/api/autopid/config") { state.autopidCfg = body || state.autopidCfg; return J({ ok: true }); }
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
      if (method === "PUT") { S[c].values = { ...body }; return J({ changed: true }); }
      return J({ ...S[c].values, degraded: false, pending_reboot: false });
    }

    if (path.endsWith("/vehicle_profiles.json")) return J({"cars": [{"car_model": "AAA: Generic", "init": "ATSP6;", "pids": [{"pid": "010C1", "parameters": [{"name": "EngineRPM", "expression": "[B3:B4]*0.25", "unit": "RPM", "class": "frequency"}]}, {"pid": "010D1", "parameters": [{"name": "VehicleSpeed", "expression": "B3", "unit": "km/h", "class": "speed"}]}, {"pid": "01051", "parameters": [{"name": "Coolant", "expression": "B3-40", "unit": "°C", "class": "temperature"}]}, {"pid": "012F1", "parameters": [{"name": "FuelLevel", "expression": "B3/2.55", "unit": "%", "class": "none"}]}, {"pid": "010F1", "parameters": [{"name": "IntakeAirTemp", "expression": "B3-40", "unit": "°C", "class": "temperature"}]}, {"pid": "01111", "parameters": [{"name": "Throttle", "expression": "B3/2.55", "unit": "%", "class": "none"}]}, {"pid": "01101", "parameters": [{"name": "MAF", "expression": "[B3:B4]*0.01", "unit": "g/s", "class": "none"}]}, {"pid": "010A1", "parameters": [{"name": "FuelPressure", "expression": "B3*3", "unit": "kPa", "class": "pressure"}]}, {"pid": "01061", "parameters": [{"name": "ShortTermFuelTrim", "expression": "(B3/1.28)-100", "unit": "%", "class": "none"}]}, {"pid": "01A61", "parameters": [{"name": "Odometer", "expression": "[B3:B6]", "unit": "km", "class": "distance"}]}]}, {"car_model": "Hyundai: Ioniq2017", "init": "ATSP6;ATSH7E4;ATST96;", "pids": [{"pid": "21057", "parameters": [{"name": "SOC_DISPLAY", "expression": "B39/2", "unit": "%", "class": "battery"}, {"name": "SOH", "expression": "[B33:B34]/10", "unit": "%", "class": ""}]}, {"pid": "2101", "parameters": [{"name": "SOC_BMS", "expression": "B09/2", "unit": "%", "class": "battery"}, {"name": "Charger_Connected", "expression": "B14:5", "unit": "", "class": ""}, {"name": "Charging", "expression": "B14:7", "unit": "", "class": ""}, {"name": "HV_Charger_Connected", "expression": "B14:6", "unit": "", "class": ""}]}]}, {"car_model": "Kia/Hyundai: Niro/Soul/Kona", "init": "ATST96;", "pids": [{"pid_init": "ATSH7E4;", "pid": "2201019", "parameters": [{"name": "SOC_BMS", "expression": "B10/2", "unit": "%", "class": "battery"}, {"name": "Max_REGEN", "expression": "[B11:B12]/100", "unit": "kW", "class": "power"}, {"name": "Max_Power", "expression": "[B13:B14]/100", "unit": "kW", "class": "power"}, {"name": "Batt_Current", "expression": "(65536-([B17:B18]))/10", "unit": "A", "class": "current"}, {"name": "HV_Volts", "expression": "[B19:B20]/10", "unit": "V", "class": "voltage"}, {"name": "HV_Power", "expression": "([B19:B20]/10)*((65536-([B17:B18]))/10)", "unit": "W", "class": "power"}, {"name": "Batt_MaxT", "expression": "B21", "unit": "°C", "class": "temperature"}, {"name": "Batt_MinT", "expression": "B22", "unit": "°C", "class": "temperature"}, {"name": "Batt_Temp_1", "expression": "B23", "unit": "°C", "class": "temperature"}, {"name": "Batt_Temp_2", "expression": "B25", "unit": "°C", "class": "temperature"}, {"name": "Batt_Temp_3", "expression": "B26", "unit": "°C", "class": "temperature"}, {"name": "Batt_Temp_4", "expression": "B27", "unit": "°C", "class": "temperature"}, {"name": "Batt_InletT", "expression": "B30", "unit": "°C", "class": "temperature"}, {"name": "Max_Cell_V", "expression": "B31/50", "unit": "V", "class": "voltage"}, {"name": "Max_Cell_V_No", "expression": "B33", "unit": "none", "class": "none"}, {"name": "Min_Cell_V", "expression": "B34/50", "unit": "V", "class": "voltage"}, {"name": "Min_Cell_V_No", "expression": "B35", "unit": "none", "class": "none"}, {"name": "Aux_Batt_Volts", "expression": "B38*0.1", "unit": "V", "class": "voltage"}]}, {"pid_init": "ATSH7E4;", "pid": "2201057", "parameters": [{"name": "SOH", "expression": "[B34:B35]/10", "unit": "%", "class": "battery"}, {"name": "SOC_D", "expression": "B41/2", "unit": "%", "class": "battery"}, {"name": "Min_Cell_Det_No", "expression": "B39", "unit": "none", "class": "none"}, {"name": "Max_Cell_Det_No", "expression": "B36", "unit": "none", "class": "none"}, {"name": "Min_Cell_Det", "expression": "[B37:B38]/10", "unit": "%", "class": "battery"}]}, {"pid_init": "ATSH7E2;", "pid": "21014", "parameters": [{"name": "GearSelector_Raw", "expression": "B10", "unit": "none", "class": "none"}, {"name": "Speed_Vehicle", "expression": "[B19:B20]/100", "unit": "%", "class": "battery"}, {"name": "Car_Ready", "expression": "B26:3", "unit": "none", "class": "none"}, {"name": "Car_ParkBreak", "expression": "B26:5", "unit": "none", "class": "none"}]}]}]});
    if (FIXED[path]) return FIXED[path]();
    return J({ error: "mock: " + method + " " + path + " not implemented" }, 404);
  }

  window.fetch = (url, opts) => {
    const u = String(url);
    const path = u.startsWith("http") ? new URL(u).pathname + (new URL(u).search || "") : u;
    const clean = path.split("?")[0];
    return Promise.resolve(handle(clean, opts));
  };

  /* ---- WebSocket mock: /ws/can emits slcan frames; others stay quiet ---- */
  window.WebSocket = class {
    constructor(url) {
      this.url = String(url); this.readyState = 0; this._timers = [];
      setTimeout(() => { this.readyState = 1; this.onopen && this.onopen({}); this._start(); }, 60);
    }
    _start() {
      if (this.url.includes("/ws/can")) {
        const ids = ["123", "2C4", "3E8", "18DAF110"];
        this._timers.push(setInterval(() => {
          if (!this.onmessage) return;
          const id = ids[Math.random() * ids.length | 0];
          const dlc = 8; let data = "";
          for (let i = 0; i < dlc; i++) data += (Math.random() * 256 | 0).toString(16).padStart(2, "0").toUpperCase();
          const f = (id.length > 3 ? "T" : "t") + id + dlc + data;
          this.onmessage({ data: f + "\r" });
        }, 120));
      }
      if (this.url.includes("/ws/cli")) {
        setTimeout(() => this.onmessage && this.onmessage({ data: "WiCAN preview console (mock)\r\nwican> " }), 150);
      }
    }
    send(d) {
      if (this.url.includes("/ws/cli") && this.onmessage) {
        setTimeout(() => this.onmessage({ data: "(preview: no device)\r\nwican> " }), 80);
      }
    }
    close() { this._timers.forEach(clearInterval); this.readyState = 3; this.onclose && this.onclose({}); }
  };

  console.log("[wican-preview] mock api installed:", Object.keys(S).length, "components");
})();
