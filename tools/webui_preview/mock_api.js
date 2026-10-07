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
  ov("data_destinations", { enabled: true, destinations: [
    { name: "dest1", type: "mqtt", enabled: true, url: "~/autopid", period_s: 5, auth: "none", auth_token: "", auth_name: "", basic_username: "", basic_password: "", api_key: "", query: "", cert_set: "", car_model: "", retain: true, full_first: true },
    { name: "cloud", type: "https", enabled: true, url: "https://telemetry.example.com/wican", period_s: 30, auth: "bearer", auth_token: "", auth_name: "", basic_username: "", basic_password: "", api_key: "", query: "src=wican", cert_set: "homeca", car_model: "", retain: true, full_first: true },
    { name: "abrp", type: "abrp", enabled: false, url: "", period_s: 10, auth: "api_key_query", auth_token: "", auth_name: "", basic_username: "", basic_password: "", api_key: "", query: "", cert_set: "", car_model: "hyundai:ioniq5:22:77", retain: true, full_first: true },
  ] });
  ov("autopid", { enabled: true, vehicle: "Corolla 2021" });
  if ((window.__mockPreset || {}).j1939Active === true) ov("j1939", { enabled: true, mode: "active" });
  else if ((window.__mockPreset || {}).j1939Listening === true) ov("j1939", { enabled: true });
  ov("data_logger", { enabled: false });
  ov("vpn_manager", { enabled: true, type: "wireguard", address: "10.66.0.2", endpoint: "vpn.example.com", port: 51820 });
  ov("usb_host_manager", { enabled: true, role: "host" });
  /* the "gps" USB scene is a receiver already read by the console (usbScene below) */
  if ((window.__mockPreset || {}).usb === "gps") ov("usb_acm_cli", { enabled: true });
  /* the carrier settings the wizard relays to the dongle (Quick Setup's USB step) */
  ov("espnetlink", { apn: "", apn_user: "", apn_password: "" });
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

  /* what GET /api/sleep (and a hold) answer: the countdown counted down from the first read */
  const sleepStatus = () => {
    const sm = (S.sleep_manager && S.sleep_manager.values) || {};
    const sp = state.sleepPending;
    if (sp && !sp.t0) sp.t0 = Date.now();
    const left = sp ? Math.max(0, Math.ceil(sp.in_s - (Date.now() - sp.t0) / 1000)) : 0;
    const hold = sp && state.holdUntil > Date.now() ? Math.ceil((state.holdUntil - Date.now()) / 1000) : 0;
    return { enabled: sm.enabled !== false, state: sp && sm.enabled !== false ? "low_voltage" : "normal", voltage: state.batteryV,
      sleep_v: (sm.sleep_mv || 13100) / 1000, wake_v: (sm.wake_mv || 13200) / 1000, naps: 0,
      pending: sp ? sp.cause : "none", sleep_in_s: left, critical_v: 11.9, critical_s: 300,
      hold_s: hold, holds_left: 3 - state.holds, holds_max: 3 };
  };
  /* what /api/usb, /api/espnetlink, /api/usb/acm and /api/gps answer for the USB
     scene state.usb names (Quick Setup's USB step, 2026-10-07): "none",
     "espnetlink_fresh" (on the cable, pairing on hold behind the factory AP
     password), "espnetlink" (paired, WiCAN on the dongle's WiFi: LTE and GPS live),
     "espnetlink_home" (paired, WiCAN on the home WiFi: the dongle standing by),
     "eth" (an ASIX adapter with an address), "eth_nolink" (the adapter, no cable),
     "gps" (a u-blox receiver read by the console, with a fix), "gps_nofix" (the
     receiver identified, the console off: read after the restart), "unknown" (a
     memory stick). `device` on /api/usb is the enumerated device whatever its class
     (the shape the GPS part needs from the firmware) */
  const usbScene = () => {
    const k = state.usb;
    if (!k) return null;
    const usb0 = { enabled: true, device_present: false, host_active: true, eth_connected: false, driver: "", ip: "", attaches: 0, vid: "0000", pid: "0000", vbus: true };
    const nl0 = { enabled: true, mode: "wifi_modem", auto_pair: true, paired: false, ssid: "", device_id: "", uplink: "wifi", on_link: false, host: "",
      pair_blocked_factory_pw: false, last_error: "", dongle_fw: "", dongle_api: 0, dongle_api_min: 7, health_unsupported: false,
      usb: { attached: false, pair_state: "idle", cuts: 0, vbus_cycles: 0, errors: 0 }, gps: { valid: false }, dongle: { valid: false }, polls: 0, failures: 0, link_ups: 0 };
    const nlv = (S.espnetlink && S.espnetlink.values) || {};
    /* dongle api 8 + the carrier relay (2026-10-07): `apn` is the WiCAN's setting,
       `carrier_synced` whether the pairing pass found the dongle holding it */
    const fw = { dongle_fw: "v1.22-41-gf2f6aa2", dongle_api: 8, dongle_api_min: 7, device_id: "206ef1894a5d", apn: nlv.apn || "", carrier_synced: true, carrier_note: "" };
    /* the dongle's health as WiCAN relays it (`sim`, `stage`, `ip` and `age_s` are
       dongle api 8; read over the cable during pairing too, so a fresh device sees
       them before its restart) */
    const health = { valid: true, lte_connected: true, attached: true, rssi_dbm: -67, operator: "ALDI Mobile", network_type: "eMTC", ip: "10.86.12.44", sim: "ready", stage: "connected", gps_fix: false, usb_data: true, age_s: 2 };
    const paired = { ...nl0, ...fw, paired: true, ssid: "ESPNetLink_894A5D", usb: { attached: false, pair_state: "done", cuts: 1, vbus_cycles: 0, errors: 0 } };
    const fix = { valid: true, latitude: -37.905350, longitude: 145.145047, accuracy: 4, altitude: 88.8, speed: 0.3, heading: 270.5, satellites: 9, age_ms: 800 };
    const nofix = { valid: false };
    const ublox = { vid: "1546", pid: "01a7", class: "cdc", product: "u-blox 7 - GPS/GNSS Receiver" };
    /* /api/usb/acm since 2026-10-07: `reading` (the console enabled and its RX task on the
       device) and `mode` ("nmea" once a receiver's sentences arrived, else "console") */
    const off = { connected: false, reading: false, mode: "console", nmea_sentences: 0 };
    switch (k) {
      case "none": return { usb: usb0, nl: nl0, acm: off, gps: nofix };
      case "espnetlink_fresh":
      case "espnetlink_nosim": {
        const hold = state.apDefaultPassword === true;
        const d = k === "espnetlink_nosim" ? { ...health, lte_connected: false, attached: false, rssi_dbm: 0, operator: "", network_type: "", ip: "", sim: "missing", stage: "not_started" } : health;
        return { usb: { ...usb0, device_present: true, eth_connected: true, driver: "cdc_ncm", ip: "192.168.7.2", attaches: 1, vid: "303a", pid: "4007", device: { vid: "303a", pid: "4007", class: "cdc_ncm", product: "ESPNetLink" } },
          nl: { ...nl0, ...fw, pair_blocked_factory_pw: hold, last_error: hold ? "the access point still has the factory password: set a new one (8 to 63 characters)" : "",
            usb: { attached: true, pair_state: hold ? "hold" : "read_key", cuts: 0, vbus_cycles: 0, errors: 0 }, dongle: d },
          acm: off, gps: nofix };
      }
      case "espnetlink": return { usb: { ...usb0, device_present: true, eth_connected: true, driver: "rndis", ip: "192.168.7.2", attaches: 1, vid: "303a", pid: "4007", device: { vid: "303a", pid: "4007", class: "rndis", product: "ESPNetLink" } },
        nl: { ...paired, mode: "usb_rndis", uplink: "espnetlink_usb", on_link: true, host: "192.168.7.1", gps: { valid: true, age_ms: 800, satellites: 9 },
          usb: { attached: true, pair_state: "ncm_up", cuts: 0, vbus_cycles: 0, errors: 0 },
          dongle: { ...health, rssi_dbm: -59, gps_fix: true }, polls: 42, link_ups: 1 },
        acm: off, gps: fix };
      case "espnetlink_home": return { usb: { ...usb0, device_present: true, attaches: 1 }, nl: paired, acm: off, gps: nofix };
      case "eth": return { usb: { ...usb0, device_present: true, eth_connected: true, driver: "asix", ip: "10.42.2.37", attaches: 1, vid: "0b95", pid: "772b", device: { vid: "0b95", pid: "772b", class: "vendor", product: "AX88772B" } },
        nl: nl0, acm: off, gps: nofix };
      case "eth_nolink": return { usb: { ...usb0, device_present: true, driver: "asix", attaches: 1, device: { vid: "0b95", pid: "772b", class: "vendor", product: "AX88772B" } }, nl: nl0, acm: off, gps: nofix };
      case "gps": return { usb: { ...usb0, device_present: true, attaches: 1, device: ublox }, nl: nl0, acm: { connected: true, reading: true, mode: "nmea", nmea_sentences: 412 }, gps: fix };
      /* the receiver bound but nobody reads it: the console is off in settings */
      case "gps_nofix": return { usb: { ...usb0, device_present: true, attaches: 1, device: ublox }, nl: nl0, acm: { connected: true, reading: false, mode: "console", nmea_sentences: 0 }, gps: nofix };
      case "unknown": return { usb: { ...usb0, device_present: true, attaches: 1, device: { vid: "0781", pid: "5581", class: "mass_storage", product: "SanDisk Ultra" } }, nl: nl0, acm: off, gps: nofix };
    }
    return null;
  };
  const state = {
    scan: { status: "idle", found: 0 },
    /* Quick Setup (2026-10-01): a probe presets window.__mockPreset before
       the page boots (fresh device = factory AP password); the vehicle
       identity file, the broker bit and HA's registration are toggles */
    apDefaultPassword: (window.__mockPreset || {}).apDefaultPassword === true,
    staConnected: (window.__mockPreset || {}).staConnected !== false,
    mqttConnected: true,
    webhookUrl: undefined,            /* undefined = the ha_webhooks setting */
    vehicle: (window.__mockPreset || {}).vehicle || null,   /* null = no vehicle.json yet */
    /* the vehicle STORE (second pass): entries keyed by VIN or fp:<hash> */
    vehicles: (window.__mockPreset || {}).vehicles || { current: "", vehicles: [] },
    /* the battery the wizard's Battery and sleep step watches: a probe moves it (14.3 = charging, 12.8 = resting) */
    batteryV: typeof (window.__mockPreset || {}).batteryV === "number" ? window.__mockPreset.batteryV : 12.52,
    /* the sleep countdown (2026-10-06): null = nothing counts, whatever batteryV says (the mock's
       ladder does not run by itself); a probe sets {cause: "delay" | "critical", in_s: N} and the
       mock counts it down from the first read, then stops answering like a device that went to
       sleep (state.asleep; set it back to false to "wake" it) */
    sleepPending: (window.__mockPreset || {}).sleepPending || null,
    asleep: false,
    /* the USB connector as Quick Setup's USB step sees it (2026-10-07): a preset or
       __mockState.usb names the scene, usbScene() below spells it out; null = the
       routes' own answers (the USB page probe's) */
    usb: (window.__mockPreset || {}).usb || null,
    /* the Quick Setup reconnect screen (2026-10-06): `offline` = this page's
       own origin is out of reach (the phone left the access point);
       `linkAnswers` = WiCAN answers at the mDNS link (the phone has arrived
       on the home WiFi and WiCAN joined it) */
    offline: false,
    linkAnswers: false,
    /* the connection test (2026-10-06, POST/GET /api/wifi/try): tryResult =
       connected | password | not_found | refused | no_ip | timeout, the
       answer after tryDelayMs; tryMode "ap" refuses the POST (no station
       interface); trial = the running/last trial, tryPosts counts starts */
    tryResult: (window.__mockPreset || {}).tryResult || "connected",
    tryDelayMs: typeof (window.__mockPreset || {}).tryDelayMs === "number" ? window.__mockPreset.tryDelayMs : 1500,
    tryMode: (window.__mockPreset || {}).tryMode || "apsta",
    trial: null,
    tryPosts: 0,
    holds: 0,                 /* POST /api/sleep/hold presses this boot (three allowed) */
    holdUntil: 0,
    /* the CAN link and autopid's bus guard (2026-10-03): null = a healthy 500k bus, nothing parked */
    canLink: (window.__mockPreset || {}).can || null,
    busGuard: (window.__mockPreset || {}).busGuard || null,
    /* rows not sent because their own init sets a protocol this bus cannot take (2026-10-05): {reason, ms} */
    busRefused: (window.__mockPreset || {}).busRefused || null,
    /* trouble codes (2026-10-03): null = DTC off, no scan yet; "obd" = an
       OBD-II car with codes; "wwh" = an ISO 27145 vehicle, two ECUs */
    dtc: (window.__mockPreset || {}).dtc || null,
    dtcReport: null,                  /* set by a clear */
    /* the car a detection finds: "wwh" = an ISO 27145 van on 29-bit ids */
    detect: (window.__mockPreset || {}).detect || "obd2",
    /* the UDS Tool's live path + exclusive switch (GET/POST /api/uds) */
    uds: { backend_setting: "auto", backend_active: "isotp", provider: "esp_isotp", can_running: true,
           exclusive: true, exclusive_default: true, holding: false, autopid_paused: false, session_active: false,
           exclusive_idle_ms: 10000, max_request: 64, max_response: 4096, last: null },
    j2534Exclusive: true,
    /* this boot's restart record (probes swap it: power_wake / periodic_wake / panic …) */
    lastRestart: { reason: "software", planned: true, planned_reason: "config_apply", source: "config_server" },
    dirs: new Set(),            /* folders created through /api/fs/mkdir */
    loggerRunning: false,       /* true -> /api/logger reports the active file (write-locked) */
    sdMounted: true,            /* false -> /api/status says no card, /sd listings fail */
    autopidCfg: (window.__mockPreset || {}).autopidCfg || {
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

  /* an ISO 27145 vehicle's scan rows: service 22, the data one byte
     further in, each row addressed to the ECU that owns the value */
  const WWH_ROWS = [
    ["22F404", "CalcEngineLoad", "B3*0.3921568692", "%", "ATSH18DA00F1"],
    ["22F405", "EngineCoolantTemp", "B3-40", "degC", "ATSH18DA00F1"],
    ["22F40C", "EngineRPM", "[B3:B4]*0.25", "rpm", "ATSH18DA00F1"],
    ["22F40D", "VehicleSpeed", "B3", "km/h", "ATSH18DA00F1"],
    ["22F43C", "CatTempBank1Sens1", "[B3:B4]*0.1-40", "degC", "ATSH18DA3DF1"],
  ].map(([cmd, name, expr, unit, init]) => ({
    pid: parseInt(cmd.slice(4), 16), cmd, name, init,
    parameters: [{ name, expression: expr, unit, class: "" }],
  }));
  const WWH_VIN = "1WCANWWH0TRUCK001";
  /* a J1939 truck's rows (TASK_j1939_wwh.md phase 5): one row per SPN of the
     built-in table, PGN:<hex> commands, little-endian expressions */
  const J1939_ROWS = [
    ["F004", 190, "EngineSpeed", "(B3+B4*256)*0.125", "rpm", "none", 0, 8031.875],
    ["F004", 513, "ActualEnginePercentTorque", "B2-125", "%", "none", -125, 125],
    ["F003", 91, "AccelPedalPosition1", "B1*0.4", "%", "none", 0, 100],
    ["FEF1", 84, "WheelBasedVehicleSpeed", "(B1+B2*256)*0.00390625", "km/h", "speed", 0, 250.99609375],
    ["FEEE", 110, "EngineCoolantTemperature", "B0-40", "degC", "temperature", -40, 210],
    ["FEEF", 100, "EngineOilPressure", "B3*4", "kPa", "pressure", 0, 1000],
    ["FEE5", 247, "EngineTotalHours", "(B0+B1*256+B2*65536+B3*16777216)*0.05", "hours", "duration", 0, 210554060.75],
  ].map(([pgn, spn, name, expression, unit, cls, min, max]) => ({
    pgn, spn, cmd: "PGN:" + pgn, name, parameters: [{ name, expression, unit, class: cls, min, max }],
  }));
  const J1939_VIN = "1WCANJ1939TRUCK01";
  const j1939Listening = () => (window.__mockPreset || {}).j1939Listening === true;
  /* phase 6: `mode active`, the address claimed (the listener is up then) */
  const j1939Active = () => (window.__mockPreset || {}).j1939Active === true;
  const scanRows = () => (state.detect === "wwh" ? WWH_ROWS : state.detect === "j1939" ? J1939_ROWS : STD_ROWS);
  const scanCar = () => (state.detect === "wwh"
    ? { key: WWH_VIN, protocol: "7", dialect: "uds", fingerprint: "2429aa55", ecus: "18DAF13D:80000001,18DAF100:981B8003", j1939: false }
    : state.detect === "j1939"
    /* a J1939-only truck: no chip protocol; the VIN comes from the listener (BAM), the
       sample path without it knows the controllers alone */
    ? { key: j1939Listening() ? J1939_VIN : "fp:9be17165", vin: j1939Listening() ? J1939_VIN : "", protocol: "", dialect: "j1939", fingerprint: "9be17165", ecus: "0:0,B:0", j1939: true }
    : { key: "1WCAN0FW0P0000001", protocol: "6", dialect: "obd2", fingerprint: "9a3f17c2", ecus: "7E8:183F8003", j1939: false });

  /* GET /api/autopid/dtc, the shape of 2026-10-03: `path`, and a report
     with `sources` (the lamp per ECU) and `items` (who reported what) */
  const DTC_EMPTY = { valid: false, ts: 0, mil: false, mil_count: 0, ecus: 0, protocol: "obd",
                      stored: [], pending: [], permanent: [], new: [], sources: [], items: [] };
  const DTC_REPORTS = {
    obd: { valid: true, ts: 1790960000, mil: true, mil_count: 2, ecus: 1, protocol: "obd",
           stored: ["P0300", "P0420"], pending: ["P0171"], permanent: [], new: [],
           sources: [{ ecu: "7E8", mil: true, count: 2 }],
           items: [{ code: "P0300", kind: "stored", ecu: "7E8" }, { code: "P0420", kind: "stored", ecu: "7E8" },
                   { code: "P0171", kind: "pending", ecu: "7E8" }],
           desc: { P0300: "Random/Multiple Cylinder Misfire Detected", P0420: "Catalyst System Efficiency Below Threshold" } },
    wwh: { valid: true, ts: 1790960000, mil: true, mil_count: 2, ecus: 2, protocol: "wwh",
           stored: ["P0420", "P20EE"], pending: ["P2463-1F"], permanent: ["P0420"], new: [],
           sources: [{ ecu: "18DAF100", mil: true, count: 1 }, { ecu: "18DAF13D", mil: true, count: 1 }],
           items: [{ code: "P0420", kind: "stored", ecu: "18DAF100", status: 8, severity: 2 },
                   { code: "P20EE", kind: "stored", ecu: "18DAF13D", status: 12, severity: 2 },
                   { code: "P2463-1F", kind: "pending", ecu: "18DAF100", status: 4, severity: 4 },
                   { code: "P0420", kind: "permanent", ecu: "18DAF100", status: 8 }],
           desc: { P0420: "Catalyst System Efficiency Below Threshold" } },
    /* a J1939 truck (phase 5): the DM1 of two controllers, heard, not asked */
    j1939: { valid: true, ts: 1790960000, mil: true, mil_count: 3, ecus: 2, protocol: "j1939", j1939: true,
             lamps: { mil: true, rsl: false, awl: true, pl: false },
             stored: ["SPN110-0", "SPN3226-4", "SPN520192-31"], pending: [], permanent: [], new: [],
             sources: [{ sa: 0, lamps: { mil: true, rsl: false, awl: true, pl: false }, mil: true, count: 2 },
                       { sa: 11, lamps: { mil: false, rsl: false, awl: false, pl: false }, mil: false, count: 1 }],
             items: [{ code: "SPN110-0", kind: "stored", sa: 0, oc: 5 }, { code: "SPN3226-4", kind: "stored", sa: 0, oc: 1 },
                     { code: "SPN520192-31", kind: "stored", sa: 11 }],
             desc: {} },
  };
  /* phase 6, active mode: the previously active codes (DM2, asked for at the
     scan) join the report as pending items */
  const j1939ActiveReport = () => Object.assign(JSON.parse(JSON.stringify(DTC_REPORTS.j1939)), {
    pending: ["SPN100-1"],
    items: DTC_REPORTS.j1939.items.concat([{ code: "SPN100-1", kind: "pending", sa: 0, oc: 2 }]) });
  const dtcDoc = () => ({ enabled: !!state.dtc, allow_clear: !!state.dtc, scanning: false,
                          path: state.dtc === "wwh" ? "wwh" : state.dtc === "j1939" ? "j1939" : "obd",
                          report: state.dtcReport || (state.dtc === "j1939" && j1939Active() ? j1939ActiveReport()
                                                      : state.dtc ? DTC_REPORTS[state.dtc] : DTC_EMPTY) });
  /* GET /api/j1939: the listener (phase 4), off on a new device; `mode`,
     `claim` and `tx` since active mode (phase 6) */
  const j1939Claim = () => (j1939Active()
    ? { state: "claimed", address: 249, preferred: 249, tx_ready: true, name: "3C651A0000810080", claims_sent: 1, contests: 0, won: 0, lost: 0, held: 0, requests_answered: 0, cannot: 0 }
    : { state: "idle", address: 254, preferred: 249, tx_ready: j1939Listening(), name: "3C651A0000810080", claims_sent: 0, contests: 0, won: 0, lost: 0, held: 0, requests_answered: 0, cannot: 0 });
  const j1939Tx = () => ({ frames: j1939Active() ? 7 : 0, failed: 0, requests: j1939Active() ? 4 : 0, acks: 0, nacks: 0, nacks_sent: 0, tp_to_me: j1939Active() ? 1 : 0, tp_cts: j1939Active() ? 1 : 0, tp_eoma: j1939Active() ? 1 : 0, tp_aborts: 0, tp_reply_lost: 0 });
  const j1939Doc = () => (j1939Listening() || j1939Active()
    ? { enabled: true, state: "listening", bus: "j1939", mode: j1939Active() ? "active" : "listen", claim: j1939Claim(), tx: j1939Tx(), vin: J1939_VIN, can: { running: true, baud_kbps: 250, link: "verified", listen_only: !j1939Active() },
        stats: { rx_frames: 3545, rx_data: 3473, rx_tp_cm: 16, rx_tp_dt: 48, rx_diag: 0, rx_foreign: 0, queue_drops: 0, messages: 3489, not_kept: 0, evicted: 0, long_evicted: 0, entries: 19, entries_max: 384, sources: 2, tp_open: 0, tp_max: 8 },
        tp: { started: 16, completed: 16, seq_errors: 0, timeouts: 0, aborted: 0, replaced: 0, open: 0, no_session: 0, orphan_dt: 0, bad_cm: 0 } }
    : { enabled: false, state: "off", bus: "unknown", mode: "listen", claim: j1939Claim(), tx: j1939Tx(), vin: null, can: { running: false, baud_kbps: 500, link: "stopped", listen_only: true },
        stats: { rx_frames: 0, rx_data: 0, rx_tp_cm: 0, rx_tp_dt: 0, rx_diag: 0, rx_foreign: 0, queue_drops: 0, messages: 0, not_kept: 0, evicted: 0, long_evicted: 0, entries: 0, entries_max: 384, sources: 0, tp_open: 0, tp_max: 8 },
        tp: { started: 0, completed: 0, seq_errors: 0, timeouts: 0, aborted: 0, replaced: 0, open: 0, no_session: 0, orphan_dt: 0, bad_cm: 0 } });

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
    components: Object.keys(S).sort().map((n) => ({ name: n, version: 1, degraded: false, pending_reboot: !!S[n].pending })),
  });

  const FIXED = {
    "/api/status": () => J({
      bits: { awake: true, sleep: false, sta_connected: state.staConnected !== false, mqtt_connected: state.mqttConnected !== false, ble_connected: false, sdcard_mounted: state.sdMounted !== false, ble_enabled: false, sta_enabled: true, ap_enabled: true, autopid_enabled: true, home_mode: false, drive_mode: false, smartconnect: false, sta_ap_overlap: false, time_synced: true, vpn_enabled: true, wake_voltage_ok: !state.sleepPending, eth_connected: false, autopid_idle: false, motion: false, sta_suspended: false, ap_suspended: false, ble_suspended: false },
      network_connected: true, uptime: "02:14:09", version: "v6.0.0-preview", partition: "ota_0",
      boot_count: 42, unexpected_resets: 1, device_id: "14c19f44e349",
      memory: { internal: { total: 274580, free: 71103, min_free: 63587, largest_block: 45056 }, psram: { total: 8272000, free: 7734508, min_free: 7524288 } }, /* no largest_block for PSRAM in the polled status (2026-10-03) */
      temp_c: 38.4,
      health: { log_errors: 0, log_warnings: 7,
        flash: { writes: 0, write_bytes: 0, erases: 0, erase_bytes: 0 },
        caps: { settings: { used: 34, cap: 40 }, cmdline: { used: 34, cap: 48 }, bridge_ep: { used: 14, cap: 24 } },
        faults: S.__faults ? S.__faults.length : 1 },
    }),
    "/api/faults": () => J({ faults: S.__faults || (S.__faults = [
      { code: "registry_headroom", detail: "bridge_tr at 3/4", count: 1,
        first_time: 1784400000, last_time: 1784400000 }]) }),
    "/api/wifi/try": (path, method, body) => {
      if (method === "POST") {
        const b = body || {};   /* handle() parsed it already */
        if (!b.ssid || b.ssid.length > 32 || (b.password && (b.password.length < 8 || b.password.length > 63))) return J({ error: "need ssid (1..32) and password (8..63)" }, 400);
        if (state.tryMode === "ap") return J({ error: "the station is off: the test needs Access point + Station" }, 409);
        if (state.trial && state.trial.state === "running" && Date.now() < state.trial.t0 + state.tryDelayMs) return J({ error: "a test is already running" }, 409);
        state.tryPosts++;
        /* `channel` (2026-10-08): the scan row's, 0 for a name typed by hand; the probe reads it back */
        state.trial = { state: "running", ssid: b.ssid, password: b.password || "", channel: b.channel || 0, t0: Date.now(), result: state.tryResult };
        return J({ state: "running", ssid: b.ssid, result: "none", reason: 0, took_ms: 0, age_s: 0 }, 202);
      }
      const t = state.trial;
      if (!t) return J({ state: "idle", ssid: "", result: "none", reason: 0, took_ms: 0, age_s: 0 });
      if (Date.now() < t.t0 + state.tryDelayMs) return J({ state: "running", ssid: t.ssid, result: "none", reason: 0, took_ms: 0, age_s: 0 });
      const reasons = { password: 204, not_found: 201, refused: 203, no_ip: 0, timeout: 0, connected: 0 };
      const o = { state: "done", ssid: t.ssid, result: t.result, reason: reasons[t.result] || 0, took_ms: t.result === "connected" ? 4200 : t.result === "timeout" ? 20000 : 6100, age_s: Math.floor((Date.now() - t.t0 - state.tryDelayMs) / 1000) };
      if (t.result === "connected") { o.ip = "10.42.0.62"; o.rssi = -58; o.channel = 6; }
      if (t.result === "no_ip") { o.channel = 6; o.took_ms = 10000; }
      return J(o);
    },
    "/api/wifi/status": () => J({ enabled: true, sta_connected: state.staConnected !== false, ip: state.staConnected !== false ? "10.42.0.62" : "", ap_started: true, ap_default_password: state.apDefaultPassword === true, clients: 0, ap_ip: "192.168.80.1", dns: ["10.42.0.1", "1.1.1.1"],
      sta_attempt: { ssid: "HomeWiFi", reason: 204, fail_count: 3, deprioritised: true } }),
    "/api/destinations": () => J({ enabled: S.data_destinations.values.enabled !== false, running: true, network: true, mqtt: true,
      destinations: (S.data_destinations.values.destinations || []).map((d, i) => ({ name: d.name, type: d.type, enabled: d.enabled !== false, url: d.url, period_s: d.period_s, auth: d.auth,
        has_token: i === 1, has_api_key: false, cert_set: d.cert_set, success: i === 0 ? 412 : 0, fail: i === 1 ? 3 : 0, skipped_offline: 0, consecutive_failures: i === 1 ? 3 : 0,
        backoff_s: i === 1 ? 10 : 0, next_in_s: 4, last_status: i === 1 ? 503 : (i === 0 ? 0 : 0), last_error: i === 1 ? "http=503" : "", last_error_time: i === 1 ? new Date().toISOString() : "",
        last_ok_time: i === 0 ? new Date().toISOString() : "", full_sent: i === 1 })) }),
    "/api/webhook": () => J({ url: state.webhookUrl !== undefined ? state.webhookUrl : S.ha_webhooks.values.url, enabled: true, interval: 15, manual_override: false, data_mode: "changed", gzip: false, status: "ok", last_post: new Date().toISOString(), retries: 0, success_count: 512, fail_count: 3, last_error: "", last_error_time: "" }),
    "/api/vpn": () => J({ state: "connected", type: "wireguard", endpoint: "vpn.example.com:51820", ts_ip: "", ts_peers: 0, connects: 1, failures: 0, uptime_s: 8040 }),
    "/api/usb": () => J(usbScene() ? usbScene().usb : { enabled: true, device_present: true, host_active: true, eth_connected: true, driver: "cdc_ncm", ip: "192.168.7.2", attaches: 1 }),
    /* espnetlink_link: paired steady state by default; state.espnlBlocked
       flips to the fresh-device hold (factory AP password, 2026-09-07);
       state.espnlUnsupported to the stale-dongle-firmware case (bench
       2026-09-08: a July build, api 6, 404 on the WiFi-modem routes) */
    "/api/espnetlink": () => J(usbScene() ? usbScene().nl : state.espnlUnsupported
      ? { enabled: true, mode: "usb_rndis", auto_pair: true, paired: false, ssid: "", device_id: "206ef1894a5d", uplink: "espnetlink_usb", on_link: true, host: "192.168.7.1",
          pair_blocked_factory_pw: false, last_error: "the dongle firmware cannot select the USB class (no usb_dev_ethernet settings): it stays on CDC-NCM. Update the dongle firmware",
          dongle_fw: "v1.22-41-gf0e8804-dirty", dongle_api: 6, dongle_api_min: 7, health_unsupported: true,
          usb: { attached: true, pair_state: "unsupported", cuts: 0, vbus_cycles: 0, errors: 1 }, gps: { valid: true, age_ms: 900, satellites: 13 }, dongle: { valid: false }, polls: 30, failures: 0, link_ups: 1 }
      : state.espnlBlocked
      ? { enabled: true, mode: "wifi_modem", auto_pair: true, paired: false, ssid: "", device_id: "206ef1894a5d", uplink: "none", on_link: false, host: "",
          pair_blocked_factory_pw: true, last_error: "the access point still has the factory password: set a new one (8 to 63 characters)",
          dongle_fw: "v1.22-41-gf2f6aa2", dongle_api: 7, dongle_api_min: 7, health_unsupported: false,
          usb: { attached: true, pair_state: "hold", cuts: 0, vbus_cycles: 0, errors: 0 }, gps: { valid: false, age_ms: 0 }, dongle: { valid: false }, polls: 0, failures: 0, link_ups: 0 }
      : { enabled: true, mode: "wifi_modem", auto_pair: true, paired: true, ssid: "ESPNetLink_894A5D", device_id: "206ef1894a5d", uplink: "espnetlink", on_link: true, host: "192.168.80.1",
          pair_blocked_factory_pw: false, last_error: "",
          dongle_fw: "v1.22-41-gf2f6aa2", dongle_api: 7, dongle_api_min: 7, health_unsupported: false,
          usb: { attached: false, pair_state: "idle", cuts: 0, vbus_cycles: 0, errors: 0 }, gps: { valid: true, age_ms: 1200, satellites: 7 },
          dongle: { valid: true, lte_connected: true, rssi_dbm: -59, operator: "ALDI Mobile", network_type: "eMTC", gps_fix: true, usb_data: false }, polls: 42, failures: 0, link_ups: 1 }),
    "/api/usb/acm": () => J(usbScene() ? usbScene().acm : { connected: true }),
    "/api/gps": () => J(usbScene() ? usbScene().gps : { valid: true, latitude: -37.905350, longitude: 145.145047, accuracy: 6, altitude: 88.8, speed: 1.0, heading: 270.5, satellites: 7, age_ms: 1200 }),
    "/api/battery": () => J({ voltage: state.batteryV }),
    /* the native CAN bus (state.canEnabled=false: a fresh device, bus off) */
    /* 2026-10-03: the node listens before it talks; state.canLink (a probe's
       window.__mockPreset.can, or set later) overrides the link fields */
    "/api/can": () => J({ enabled: state.canEnabled !== false, running: state.canEnabled !== false, silent: false, baud_auto: false, baud_kbps: 500, baud_detected: 500, state: state.canEnabled === false ? "stopped" : "running", listen_only: state.canEnabled === false, verified: state.canEnabled !== false, tx: 1543, rx: 89231, tx_errors: 0, rx_errors: 0, arb_lost: 0, bus_errors: state.busErrors || 0, rx_missed: 0, dispatch_drops: 0, rx_bad: 0, rx_deaf: 0, tx_refused: 0, link_switches: 0, link_demotions: 0, bus_off: 0, recoveries: 0,
      err: { stuff: 0, form: 0, bit: 0, ack: 0, other: 0 }, probe: { result: "none", baud_kbps: 0, frames: 0, age_ms: 0 },
      subscribers: [{ idx: 0, name: "bridge", drops: 0 }, { idx: 1, name: "logger", drops: 0 }], subscribers_max: 16, ...(state.canLink || {}) }),
    "/api/bridges": () => J({ bridges: ((S.bridge_manager && S.bridge_manager.values.bridges) || []).map((b) => ({ ...b, up: b.enabled !== false, stats: { a2b_chunks: 0, b2a_chunks: 0, a2b_bytes: 0, b2a_bytes: 0, send_errors: 0, codec_errors: 0 } })) }),
    "/api/ws": () => J({ channels: ((S.websocket_manager && S.websocket_manager.values.channels) || []).map((c) => ({ ...c, up: c.enabled !== false, stats: { clients: 0, frames_in: 0, frames_out: 0, bytes_in: 0, bytes_out: 0, rx_drops: 0, tx_drops: 0, refused: 0 } })) }),
    "/api/autopid": () => { state.polls += state.groupOn ? 4 : 0; const nowUs = Date.now() * 1000; return J({
      groups: [{ name: "default", enabled: state.groupOn, period_ms: 1000 }],
      params: Object.entries(state.dash).map(([name, value]) => ({
        name, value: value + (Math.random() - 0.5) * (name === "RPM" ? 40 : 2),
        unit: { RPM: "rpm", Speed: "km/h", Coolant: "°C", SOC_BMS: "%" }[name] || "",
        ts_us: nowUs - (name === "SOC_BMS" ? 45e6 : 800e3),   /* SOC_BMS deliberately stale */
      })).concat([{ name: "gps_speed", unit: "km/h", value: 61.6, ts_us: nowUs - 1.2e6, external: true }]),
      stats: { running: state.groupOn && !state.busGuard, paused_voltage: false, paused_bus: !!state.busGuard, paused_diag: false, polls_ok: state.polls, polls_failed: 2, pids: 4, filters: 1,
        passive_ok: 0, passive_failed: 0, passive_published: 0, passive_requested: 0, passive_refused: 0, j1939_listening: j1939Listening() || j1939Active(), j1939_active: j1939Active(),
        period_floor_ms: 50, sub_floor_pids: 0, now_us: nowUs },
      /* the bus guard (2026-10-03): state.busGuard = a parked poller's reason */
      bus_guard: state.busGuard
        ? { bus: "live", bus_kbps: 250, verdict: "park", parked: true, reason: state.busGuard }
        : state.busRefused
          ? { bus: "live", bus_kbps: 250, verdict: "allow", parked: false, reason: "vehicle bus live at 250 kbit/s",
              refused: 12, refused_reason: state.busRefused.reason, refused_ms: state.busRefused.ms }
          : { bus: "silent", bus_kbps: 0, verdict: "allow", parked: false, reason: "vehicle bus silent", refused: 0 },
    }); },
    /* the registry as the firmware serves it (2026-09-17): keys per event, params_schema + undoable per action, value prefixes */
    "/api/events/sources": () => J([
      { event: "timer.tick", description: "settings-defined periodic timers", keys: [{ key: "timer", type: "string" }] },
      { event: "autopid.param", description: "a parameter changed", keys: [{ key: "param", type: "string" }, { key: "value", type: "number" }, { key: "unit", type: "string" }, { key: "group", type: "string" }] },
      { event: "autopid.pid_failed", description: "a poll failed", keys: [{ key: "pid", type: "string" }, { key: "streak", type: "integer" }] },
      { event: "autopid.dtc", description: "a NEW trouble code appeared", keys: [{ key: "code", type: "string" }, { key: "status", type: "string" }, { key: "mil", type: "boolean" }] },
      { event: "wifi.sta", description: "the STA link came up (connected=true, ssid) or dropped", keys: [{ key: "connected", type: "boolean" }, { key: "ssid", type: "string" }] },
      { event: "imu.bump", description: "Bump/impact detected", keys: [{ key: "axes", type: "string" }] },
      { event: "imu.motion", description: "Motion state changed", keys: [{ key: "state", type: "string" }] },
      { event: "battery.threshold", description: "Voltage crossed a watch threshold", keys: [{ key: "edge", type: "string" }, { key: "volts", type: "number" }] },
      { event: "status.bit", description: "A device status bit changed", keys: [{ key: "bit", type: "string" }, { key: "set", type: "boolean" }] },
      { event: "mqtt.rx", description: "an MQTT message arrived", keys: [{ key: "topic", type: "string" }, { key: "payload", type: "string" }] },
      { event: "vpn.state", description: "VPN connection state changed", keys: [{ key: "state", type: "string" }] },
    ]),
    "/api/events/actions": () => J([
      { name: "log.note", params_schema: { type: "object", properties: { message: { type: "string" } } }, undoable: false },
      { name: "mqtt.publish", params_schema: { type: "object", properties: { topic: { type: "string" }, payload: { type: "string" }, qos: { type: "integer" }, retain: { type: "boolean" } } }, undoable: false },
      { name: "http.post", params_schema: { type: "object", properties: { url: { type: "string" }, body: { type: "string" } } }, undoable: false },
      { name: "autopid.group", params_schema: { type: "object", properties: { group: { type: "string" }, enabled: { type: "boolean" }, period_ms: { type: "integer", minimum: 0 } }, required: ["group", "enabled"] }, undoable: true },
      { name: "led.indicate", params_schema: { type: "object", properties: { r: { type: "integer" }, g: { type: "integer" }, b: { type: "integer" }, mode: { type: "string", enum: ["solid", "off", "blink_slow", "blink_fast"] } } }, undoable: true },
      { name: "led.clear", params_schema: { type: "object", properties: {} }, undoable: false },
      { name: "script.run", params_schema: { type: "object", properties: { name: { type: "string" } } }, undoable: false },
      { name: "obd.request", params_schema: { type: "object", properties: { cmd: { type: "string" } } }, undoable: false },
      { name: "autopid.dtc_scan", params_schema: { type: "object", properties: {} }, undoable: false },
      { name: "logger.gate", params_schema: { type: "object", properties: { enabled: { type: "boolean" }, reason: { type: "string" } } }, undoable: false },
    ]),
    "/api/events/values": () => J(["autopid.data", "autopid.", "battery.voltage", "time.iso", "time.epoch", "wifi.ssid", "wifi.connected"]),
    "/api/events/rules": () => J(((S.event_manager && S.event_manager.values.rules) || []).map((r, i) => ({ name: r.name, enabled: r.enabled !== false, undo: !!r.undo, active: false, fired: i === 0 ? 3 : 0, last_fired_age_s: i === 0 ? 120 : -1 }))),
    "/api/autopid/dtc": () => J(dtcDoc()),
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
    /* a probe puts a crash note on this boot's record through
       __mockState.lastRestart.crash, and a crash report into flash through
       __mockState.crashReport = {stored_time, time_valid, firmware, streak,
       parked, crash} (restart_tracker/HTTP_API.md) */
    "/api/restart/history": () => J({ boot_count: 42, unexpected_resets: 1, elf_sha: "76a961dd5f08aabb",
      brake: { verdict: "normal", streak: 0, limit: 3, parks: 0, settled: true, settle_s: 600, report_budget: 4 },
      ...(state.crashReport ? { report: state.crashReport } : {}), records: [
      { seq: 42, mode: "normal", settled: true, ...state.lastRestart, flags: 0, boot_time: now() - 8040, time_valid: true, request_time: now() - 8041, request_uptime_ms: 120033 },
      { seq: 41, reason: "poweron", planned: false, planned_reason: "none", source: "unknown", mode: "normal", settled: true, flags: 0, boot_time: now() - 90000, time_valid: true, request_time: 0, request_uptime_ms: 0 },
    ] }),
    /* the stored report as the firmware's text (restart_tracker_report_core.c) */
    "/api/restart/report": () => {
      const r = state.crashReport;
      if (!r) return J({ error: "no crash report is stored" }, 404);
      const c = r.crash || {};
      return T("WiCAN crash report\nDevice:    68ee8f5a653d\nFirmware:  " + (r.firmware || "not the one that stored this report")
        + "\nImage:     " + (c.elf_sha || "not recorded")
        + "\nStored:    " + (r.time_valid ? new Date(r.stored_time * 1000).toISOString().replace("T", " ").slice(0, 19) + " UTC" : "the clock was not set")
        + (r.streak >= 2 || r.parked ? "\nLoop:      " + r.streak + " crashes in a row" + (r.parked ? "; the device parked itself" : "") : "")
        + "\nCrash:     " + (c.summary || "") + ((c.backtrace || []).length ? "\nBacktrace: " + c.backtrace.join(" ") : "") + "\n");
    },
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
    "/api/j2534": () => J({ enabled: false, port: 6809, listening: false, client_connected: false, device_open: false, channels: 0,
                            frames_rx: 0, frames_tx: 0, allow_reflash: false, allow_lan: false, exclusive: !!state.j2534Exclusive,
                            autopid_paused: false, phase: "2 (CAN + ISO15765 channels)" }),
    "/api/uds": () => J(state.uds),
    "/api/sleep": () => J(sleepStatus()),
    /* the keep-awake button (2026-10-06): both deadlines out to now + minutes where that is
       later, three times per boot; 409 when nothing counts or the three are used */
    "/api/sleep/hold": (full, method, body) => {
      const minutes = body && typeof body.minutes === "number" ? body.minutes : 10;
      if (minutes < 1 || minutes > 30) return J({ error: "minutes must be 1..30" }, 400);
      const sp = state.sleepPending;
      if (!sp) return J({ error: "nothing is counting" }, 409);
      if (state.holds >= 3) return J({ error: "hold limit reached" }, 409);
      if (!sp.t0) sp.t0 = Date.now();
      const now = Date.now();
      const left = Math.max(0, sp.in_s - (now - sp.t0) / 1000);
      sp.t0 = now; sp.in_s = Math.max(left, minutes * 60);
      state.holds += 1; state.holdUntil = now + minutes * 60000;
      return J(sleepStatus());
    },
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
    "/api/j1939": () => J(j1939Doc()),
    "/api/autopid/std_scan/result": () => (state.scan.status === "done"
      ? J({ version: 1, protocol: "0", protocol_detected: scanCar().protocol, dialect: scanCar().dialect, vin: scanCar().vin !== undefined ? scanCar().vin : scanCar().key,
            fingerprint: scanCar().fingerprint, supported: scanRows(), found: scanRows().length, ts: now(),
            ...(state.detect === "wwh" ? { uds_protocol_id: 1 } : {}),
            /* the network step (phase 5): on every detection since 2026-10-03 */
            j1939: !!scanCar().j1939, j1939_listening: j1939Listening(), bus_kbps: state.detect === "j1939" ? 250 : 500,
            key: scanCar().key, known: !!(state.vehicles.vehicles.find((v) => v.key === scanCar().key) || {}).profile,
            name: (state.vehicles.vehicles.find((v) => v.key === scanCar().key) || {}).name || "" })
      : J({ error: "no scan stored" }, 404)),
    /* the vehicle store (autopid second pass, 2026-10-01) */
    "/api/autopid/vehicles": () => J({ current: state.vehicles.current, max: 8,
      vehicles: state.vehicles.vehicles.map((v) => ({ ...v, current: v.key === state.vehicles.current })) }),
    "/api/autopid/std_table": () => J({ version: 1, supported: STD_ROWS, found: STD_ROWS.length }),
    "/api/autopid/config": () => J(state.autopidCfg),
    "/api/settings": () => J(settingsList()),
  };

  async function handle(path, opts, full) {
    const method = ((opts && opts.method) || "GET").toUpperCase();
    const body = opts && typeof opts.body === "string" ? (() => { try { return JSON.parse(opts.body); } catch { return null; } })() : null;

    /* mutations */
    if (method === "POST" && path === "/api/uds") {
      if (body && typeof body.exclusive === "boolean") { state.uds.exclusive = body.exclusive; if (!body.exclusive) { state.uds.holding = false; state.uds.autopid_paused = false; } }
      return J(state.uds);
    }
    if (method === "POST" && path === "/api/uds/session") {
      const on = !!(body && body.action === "begin");
      state.uds.session_active = on;
      if (on && state.uds.exclusive) { state.uds.holding = true; state.uds.autopid_paused = true; }
      return J({ ...state.uds, ok: true });
    }
    if (method === "POST" && path === "/api/uds/request") {
      const req = String((body && body.data) || "").trim().toUpperCase().replace(/\s+/g, " ");
      const sid = parseInt(req.split(" ")[0] || "0", 16);
      let resp;
      if (req === "22 F1 90") resp = "62 F1 90 " + [..."1WCAN0FW0P0000001"].map((c) => c.charCodeAt(0).toString(16).toUpperCase()).join(" ");
      else if (req === "10 02") resp = "50 02 00 32 01 F4";
      else if (sid === 0x3E) resp = "7E 00";
      else if (sid === 0x19) resp = "59 02 FF";
      else resp = "7F " + sid.toString(16).toUpperCase().padStart(2, "0") + " 31";
      const bytes = resp.split(" ");
      const negative = bytes[0] === "7F";
      const r = { ok: true, response: resp, length: bytes.length, positive: !negative, sid: parseInt(negative ? bytes[1] : bytes[0], 16),
                  pending: 0, elapsed_ms: 24, backend: state.uds.backend_active };
      if (negative) { r.nrc = 0x31; r.nrc_name = "requestOutOfRange"; }
      if (state.uds.exclusive) { state.uds.holding = true; state.uds.autopid_paused = true; }
      state.uds.last = { age_ms: 0, ok: true, tx_id: String((body && body.tx_id) || "7E0"), rx_id: String((body && body.rx_id) || "7E8"),
                         req_sid: sid, sid: r.sid, positive: r.positive, pending: 0, elapsed_ms: 24, backend: r.backend };
      return J(r);
    }
    if (method === "POST" && path === "/api/j2534") {
      if (body && typeof body.exclusive === "boolean") state.j2534Exclusive = body.exclusive;
      return FIXED["/api/j2534"]();
    }
    if (method === "POST" && (path === "/api/autopid/std_scan" || path === "/api/autopid/vehicles/detect")) {
      /* three phases like the firmware: protocol detect, VIN, support bitmaps;
         at the end the store gets (or recognises) the simulator's car */
      if (state.scan.status === "running") return J({ error: "scan running" }, 409);
      state.scan = { status: "running", phase: "protocol", found: 0 };
      setTimeout(() => { state.scan = { status: "running", phase: "vin", found: 0 }; }, 800);
      setTimeout(() => { state.scan = { status: "running", phase: "pids", found: 0 }; }, 1600);
      setTimeout(() => { state.scan = { status: "running", phase: "network", found: 0 }; }, 2100);
      setTimeout(() => {
        state.scan = { status: "done", phase: "idle", found: scanRows().length, ts: now(), stored: true };
        const car = scanCar(), key = car.key;
        let e = state.vehicles.vehicles.find((v) => v.key === key);
        if (!e) {
          e = { key, vin: car.vin !== undefined ? car.vin : key, fingerprint: car.fingerprint, name: "", protocol: car.protocol, chip_protocol: car.protocol,
                dialect: car.dialect, j1939: !!car.j1939, ecus: car.ecus, profile: "", specific_init: "",
                std_supported: scanRows().length, pending_profile: true, first_seen: now(), last_seen: now(), scan_ts: now() };
          state.vehicles.vehicles.push(e);
        } else { e.last_seen = now(); e.scan_ts = now(); e.std_supported = scanRows().length; }
        state.vehicles.current = key;
      }, 2500);
      return J({ started: true }, 202);
    }
    let vm = path.match(/^\/api\/autopid\/vehicles\/([^/]+)(\/activate)?$/);
    if (vm) {
      const key = decodeURIComponent(vm[1]);
      const e = state.vehicles.vehicles.find((v) => v.key === key);
      if (!e) return J({ error: "unknown vehicle" }, 404);
      if (method === "PUT" && !vm[2]) {
        if (body && typeof body.name === "string") e.name = body.name;
        if (body && typeof body.profile === "string") { e.profile = body.profile; e.pending_profile = false; e.specific_init = typeof body.specific_init === "string" ? body.specific_init : (body.profile ? e.specific_init : ""); }
        else if (body && typeof body.specific_init === "string") e.specific_init = body.specific_init;
        state.vehiclePuts = (state.vehiclePuts || []).concat([{ key, body }]);
        return J({ ...e, current: e.key === state.vehicles.current });
      }
      if (method === "POST" && vm[2]) { state.vehicles.current = key; return J({ ok: true }); }
      if (method === "DELETE" && !vm[2]) {
        state.vehicles.vehicles = state.vehicles.vehicles.filter((v) => v.key !== key);
        if (state.vehicles.current === key) state.vehicles.current = "";
        return T("", 204);
      }
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
    if (method === "DELETE" && path === "/api/restart/report") {
      state.crashReport = null; state.crashReportClears = (state.crashReportClears || 0) + 1;
      return J({ cleared: true });
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
      /* the params stream: 120 points, 30 s apart, for the requested name, NDJSON or CSV rows */
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
    if (method === "POST" && path === "/api/restart") { Object.values(S).forEach((x) => { x.pending = false; }); return J({ ok: true }); }
    if (method === "POST" && path === "/api/faults/clear") { S.__faults = []; return J({ cleared: true }); }
    if (method === "POST" && path === "/api/vpn/keygen") return J({ public_key: "MockPubKey000000000000000000000000000000000=" });
    if (method === "POST" && path === "/api/autopid/dtc/scan") return J({ started: true }, 202);
    if (method === "POST" && path === "/api/autopid/dtc/clear") {
      /* everything goes but the permanent codes; the lamps go out */
      const r = dtcDoc().report, before = r.stored.length;
      state.dtcReport = { ...r, mil: false, mil_count: 0, stored: [], pending: [], new: [],
                          sources: r.sources.map((s) => ({ ...s, mil: false, count: 0 })),
                          items: r.items.filter((it) => it.kind === "permanent") };
      state.lastDtcClear = body;
      return J({ ok: true, cleared: true, before, after: 0 });
    }
    if (method === "POST" && path === "/api/destinations/test") {
      const d = (S.data_destinations.values.destinations || []).find((x) => x.name === (body && body.name));
      if (!d) return J({ error: "unknown destination" }, 404);
      return J({ ok: d.name !== "cloud", status: d.type === "mqtt" ? 0 : (d.name === "cloud" ? 503 : 200), elapsed_ms: 87, error: d.name === "cloud" ? "http=503" : "" });
    }
    if (method === "POST" && path === "/api/autopid/test") {
      // 2026-09-16 shape: one shot decodes every expression; "transcript"
      // lists each exchange as the firmware ran it (type init, PID init,
      // ATCRA, request, ATCRA off)
      const expr = body && body.expression;
      const exprs = body && Array.isArray(body.expressions) ? body.expressions : null;
      const raw = "7EC 10 27 62 01 01 FF F7 E7\n7EC 21 FF 84 84 84 84 84 84";
      const sent = [];
      for (const chain of [body && body.init]) for (const c of String(chain || "").split(";")) if (c.trim()) sent.push(c.trim());
      if (body && body.rxheader) sent.push("ATCRA" + body.rxheader);
      const transcript = sent.map((c) => "> " + c + "\n< OK\n").join("") +
        "> " + ((body && body.cmd) || "") + "\n< " + raw.replace(/\n/g, " ") + "\n" +
        (body && body.rxheader ? "> ATCRA\n< OK\n" : "");
      const rnd = () => Math.round(Math.random() * 900) / 10;
      return J({ ok: true, raw, elapsed_ms: 42, transcript, payload: "62 01 01 FF F7 E7 FF 84 84 84 84 84 84",
        value: expr ? rnd() : undefined, values: exprs ? exprs.map(() => rnd()) : undefined });
    }
    if (method === "POST" && path === "/api/usb/acm/cmd") {
      // canned dongle-console replies captured from a live ESPNetLink
      // (v1.22, BG95-M5): echo + payload + esp> prompt, as on the wire
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
    /* the firmware key is auth_mode (wifi_manager_status.c), not auth */
    if (path === "/api/wifi/scan") return J({ networks: [
      { ssid: "HomeWiFi", rssi: -48, auth_mode: "WPA2_PSK", channel: 6, bssid: "aa:bb:cc:00:00:01" },
      { ssid: "HomeWiFi", rssi: -61, auth_mode: "WPA2_PSK", channel: 1, bssid: "aa:bb:cc:00:00:02" },
      { ssid: "Neighbor", rssi: -77, auth_mode: "WPA2_WPA3_PSK", channel: 11, bssid: "aa:bb:cc:00:00:03" },
      { ssid: "CafeFree", rssi: -70, auth_mode: "OPEN", channel: 6, bssid: "aa:bb:cc:00:00:04" },
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
      if (method === "PUT") { state.puts = (state.puts || 0) + 1; S[c].values = { ...body }; S[c].pending = true; return J({ changed: true }); }
      return J({ ...S[c].values, degraded: false, pending_reboot: false });
    }

    if (path.endsWith("/vehicle_profiles.json")) return J({"cars": [{"car_model": "AAA: Generic", "init": "ATSP6;", "pids": [{"pid": "010C1", "parameters": [{"name": "EngineRPM", "expression": "[B3:B4]*0.25", "unit": "RPM", "class": "frequency"}]}, {"pid": "010D1", "parameters": [{"name": "VehicleSpeed", "expression": "B3", "unit": "km/h", "class": "speed"}]}, {"pid": "01051", "parameters": [{"name": "Coolant", "expression": "B3-40", "unit": "°C", "class": "temperature"}]}, {"pid": "012F1", "parameters": [{"name": "FuelLevel", "expression": "B3/2.55", "unit": "%", "class": "none"}]}, {"pid": "010F1", "parameters": [{"name": "IntakeAirTemp", "expression": "B3-40", "unit": "°C", "class": "temperature"}]}, {"pid": "01111", "parameters": [{"name": "Throttle", "expression": "B3/2.55", "unit": "%", "class": "none"}]}, {"pid": "01101", "parameters": [{"name": "MAF", "expression": "[B3:B4]*0.01", "unit": "g/s", "class": "none"}]}, {"pid": "010A1", "parameters": [{"name": "FuelPressure", "expression": "B3*3", "unit": "kPa", "class": "pressure"}]}, {"pid": "01061", "parameters": [{"name": "ShortTermFuelTrim", "expression": "(B3/1.28)-100", "unit": "%", "class": "none"}]}, {"pid": "01A61", "parameters": [{"name": "Odometer", "expression": "[B3:B6]", "unit": "km", "class": "distance"}]}]}, {"car_model": "Hyundai: Ioniq2017", "init": "ATSP6;ATSH7E4;ATST96;", "pids": [{"pid": "21057", "parameters": [{"name": "SOC_DISPLAY", "expression": "B39/2", "unit": "%", "class": "battery"}, {"name": "SOH", "expression": "[B33:B34]/10", "unit": "%", "class": ""}]}, {"pid": "2101", "parameters": [{"name": "SOC_BMS", "expression": "B09/2", "unit": "%", "class": "battery"}, {"name": "Charger_Connected", "expression": "B14:5", "unit": "", "class": ""}, {"name": "Charging", "expression": "B14:7", "unit": "", "class": ""}, {"name": "HV_Charger_Connected", "expression": "B14:6", "unit": "", "class": ""}]}]}, {"car_model": "Kia/Hyundai: Niro/Soul/Kona", "init": "ATST96;", "pids": [{"pid_init": "ATSH7E4;", "pid": "2201019", "parameters": [{"name": "SOC_BMS", "expression": "B10/2", "unit": "%", "class": "battery"}, {"name": "Max_REGEN", "expression": "[B11:B12]/100", "unit": "kW", "class": "power"}, {"name": "Max_Power", "expression": "[B13:B14]/100", "unit": "kW", "class": "power"}, {"name": "Batt_Current", "expression": "(65536-([B17:B18]))/10", "unit": "A", "class": "current"}, {"name": "HV_Volts", "expression": "[B19:B20]/10", "unit": "V", "class": "voltage"}, {"name": "HV_Power", "expression": "([B19:B20]/10)*((65536-([B17:B18]))/10)", "unit": "W", "class": "power"}, {"name": "Batt_MaxT", "expression": "B21", "unit": "°C", "class": "temperature"}, {"name": "Batt_MinT", "expression": "B22", "unit": "°C", "class": "temperature"}, {"name": "Batt_Temp_1", "expression": "B23", "unit": "°C", "class": "temperature"}, {"name": "Batt_Temp_2", "expression": "B25", "unit": "°C", "class": "temperature"}, {"name": "Batt_Temp_3", "expression": "B26", "unit": "°C", "class": "temperature"}, {"name": "Batt_Temp_4", "expression": "B27", "unit": "°C", "class": "temperature"}, {"name": "Batt_InletT", "expression": "B30", "unit": "°C", "class": "temperature"}, {"name": "Max_Cell_V", "expression": "B31/50", "unit": "V", "class": "voltage"}, {"name": "Max_Cell_V_No", "expression": "B33", "unit": "none", "class": "none"}, {"name": "Min_Cell_V", "expression": "B34/50", "unit": "V", "class": "voltage"}, {"name": "Min_Cell_V_No", "expression": "B35", "unit": "none", "class": "none"}, {"name": "Aux_Batt_Volts", "expression": "B38*0.1", "unit": "V", "class": "voltage"}]}, {"pid_init": "ATSH7E4;", "pid": "2201057", "parameters": [{"name": "SOH", "expression": "[B34:B35]/10", "unit": "%", "class": "battery"}, {"name": "SOC_D", "expression": "B41/2", "unit": "%", "class": "battery"}, {"name": "Min_Cell_Det_No", "expression": "B39", "unit": "none", "class": "none"}, {"name": "Max_Cell_Det_No", "expression": "B36", "unit": "none", "class": "none"}, {"name": "Min_Cell_Det", "expression": "[B37:B38]/10", "unit": "%", "class": "battery"}]}, {"pid_init": "ATSH7E2;", "pid": "21014", "parameters": [{"name": "GearSelector_Raw", "expression": "B10", "unit": "none", "class": "none"}, {"name": "Speed_Vehicle", "expression": "[B19:B20]/100", "unit": "%", "class": "battery"}, {"name": "Car_Ready", "expression": "B26:3", "unit": "none", "class": "none"}, {"name": "Car_ParkBreak", "expression": "B26:5", "unit": "none", "class": "none"}]}]}]});
    if (FIXED[path]) return FIXED[path](full || path, method, body);   /* a few mutate on method + body */
    return J({ error: "mock: " + method + " " + path + " not implemented" }, 404);
  }

  /* XMLHttpRequest over the same mock: pages that want upload progress use XHR */
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
  window.__mockSettings = S;    /* probes read what a page staged / PUT (values per component) */
  window.fetch = (url, opts) => {
    /* a countdown that ran out: the device is asleep and answers nothing */
    const sp = state.sleepPending;
    if (sp && sp.t0 && Date.now() >= sp.t0 + sp.in_s * 1000) state.asleep = true;
    if (state.asleep) return Promise.reject(new TypeError("mock: the device is asleep"));
    const u = String(url);
    /* another origin (the mDNS link wican_<id>.local, or the address the
       connection test returned) is another device-side door: the reconnect
       screen probes them with no-CORS fetches */
    if (/^http:\/\/(wican_[0-9a-f]+\.local|\d+\.\d+\.\d+\.\d+)(:\d+)?(\/|$)/i.test(u) && new URL(u).host !== location.host) {
      state.linkProbes = (state.linkProbes || 0) + 1;
      return state.linkAnswers ? Promise.resolve({ ok: false, status: 0, type: "opaque", headers: { get: () => null }, json: async () => { throw new TypeError("opaque"); }, text: async () => "" })
        : Promise.reject(new TypeError("mock: " + u + " is out of reach"));
    }
    if (state.offline) return Promise.reject(new TypeError("mock: the page's origin is out of reach"));
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
