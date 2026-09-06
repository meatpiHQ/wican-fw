/**
 * @file main.c
 * @brief WiCAN Pro firmware — the composition root (Architecture §11).
 *
 * main owns nothing but the ORDER: init the owners bottom-up, let every
 * component register (settings/log descriptors, HTTP routes, bridge
 * jacks — see components/bridge_endpoints), run the ONE settings apply
 * pass, then start everything. A component that fails to start leaves the
 * device degraded, never bricked (§4.3): boot always completes.
 *
 * Not composed yet (their components don't exist): internal CAN
 * (TWAI), autopid, sleep_manager. The BLE/WiFi coexistence policy hook
 * watches the dev-status bits here when needed — v1 runs both radios
 * (coex firmware arbitrates airtime).
 */
#include <stdio.h>

#include "esp_event.h"

#include "esp_log.h"
#include "esp_netif.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "nvs_flash.h"

#include "api_http.h"
#include "autopid.h"
#include "ha_webhooks.h"
#include "data_logger.h"
#include "sleep_manager.h"
#include "usb_host_manager.h"
#include "usb_acm_cli.h"
#include "espnetlink_link.h"
#include "j2534_server.h"
#include "iperf_manager.h"
#include "vpn_manager.h"
#include "main_sleep.h"
#include "battery_monitor.h"
#include "ble_manager.h"
#include "cert_manager.h"
#include "bridge_manager.h"
#include "can_manager.h"
#include "cmdline_manager.h"
#include "ext_manager.h"
#include "uds_manager.h"
#include "script_engine.h"
#include "dev_status_manager.h"
#include "event_manager.h"
#include "external_storage.h"
#include "filesystem.h"
#include "http_client_manager.h"
#include "http_server_manager.h"
#include "i2c_bus.h"
#include "web_ui_v2.h"
#include "imu_manager.h"
#include "interface_manager.h"
#include "led_manager.h"
#include "log_manager.h"
#include "log_sinks.h"
#include "mdns_manager.h"
#include "mqtt_manager.h"
#include "obd_chip.h"
#include "obd_gate.h"
#include "ota_manager.h"
#include "restart_tracker.h"
#include "rtc_manager.h"
#include "settings_manager.h"
#include "socket_manager.h"
#include "websocket_manager.h"
#include "wifi_manager.h"

#include "button_manager.h"
#include "main_bench.h"
#include "main_boot.h"
#include "main_cli.h"
#include "main_glue.h"
#include "main_heap.h"
#include "main_safemode.h"
#include "bridge_endpoints.h"
#include "mqtt_can.h"
#include "translator_gvret.h"
#include "translator_realdash.h"
#include "translator_slcan.h"

static const char *TAG = "main";

void app_main(void)
{
    /* THE VERY FIRST act: button held at power-on = SAFE MODE — a bare
       recovery environment that never loads settings (a corrupt config
       must not be able to crash it). Released = one GPIO read of cost. */
    if (main_safemode_check())
    {
        main_safemode_enter(); /* never returns */
    }

    /* BEFORE any component init: every cJSON allocation firmware-wide
       goes to PSRAM (main_heap policy) */
    main_heap_route_cjson_to_psram();

    /* ---- storage prerequisites ------------------------------------------ */
    esp_err_t err = nvs_flash_init();

    if (err == ESP_ERR_NVS_NO_FREE_PAGES ||
        err == ESP_ERR_NVS_NEW_VERSION_FOUND)
    {
        nvs_flash_erase();
        err = nvs_flash_init();
    }

    if (err != ESP_OK)
    {
        /* settings live on their OWN LittleFS partition, not NVS — a dead
           NVS mainly costs WiFi PHY-cal cache. Degrade, don't loop. */
        ESP_LOGE(TAG, "nvs_flash_init failed: %s (continuing degraded)",
                 esp_err_to_name(err));
    }

    /* ---- log pipeline first: everything below needs somewhere to log -----
       (log_manager_init failure falls back to the raw IDF console) */
    main_boot_init("log_manager_init", log_manager_init);
    main_boot_init("restart_tracker_init", restart_tracker_init);
    main_boot_init("dev_status_manager_init", dev_status_manager_init);

    /* ---- HAL + core owners ------------------------------------------------ */
    main_boot_init("external_storage_init", external_storage_init);
    main_boot_init("filesystem_init", filesystem_init);
    /* glue: the composition root wires SD mount events to the /sd backend
       (neither component depends on the other) */
    main_glue_wire_sd();
    main_boot_init("settings_manager_init", settings_manager_init);
    main_boot_init("log_manager_register_settings",
              log_manager_register_settings);
    /* same pattern for the other early-init components: their {cli}
       settings descriptors register here; each component then registers
       its own console commands on the settings boot apply */
    main_boot_init("dev_status_manager_register_settings",
              dev_status_manager_register_settings);
    main_boot_init("restart_tracker_register_settings",
              restart_tracker_register_settings);
    main_boot_init("external_storage_register_settings",
              external_storage_register_settings);
    main_boot_init("filesystem_register_settings",
              filesystem_register_settings);

    /* §9.2 exception: settings_manager can't register with log_manager
       (cycle) — the composition root does it */
    static const log_descriptor_t SETTINGS_LOG =
        { "settings_manager", ESP_LOG_INFO };

    log_manager_register(&SETTINGS_LOG);

    main_boot_init("esp_netif_init", esp_netif_init);
    main_boot_init("esp_event_loop_create_default",
              esp_event_loop_create_default);
    main_boot_init("http_server_manager_init", http_server_manager_init);
    main_boot_init("i2c_bus_init", i2c_bus_init); /* the shared peripheral bus */

    /* ---- components register descriptors / routes / endpoints ------------ */
    main_boot_init("wifi_manager_init", wifi_manager_init);
    /* the bus-conversation gate BEFORE its two requester sides */
    main_boot_init("obd_gate_init", obd_gate_init);
    main_boot_init("obd_chip_init", obd_chip_init);
    main_boot_init("ble_manager_init", ble_manager_init);
    main_boot_init("socket_manager_init", socket_manager_init);
    main_boot_init("websocket_manager_init", websocket_manager_init);
    /* the external log sinks (tcp/udp/ws/file) — registers its settings;
       its sinks join log_manager at start (below) */
    main_boot_init("log_sinks_init", log_sinks_init);
    main_boot_init("bridge_manager_init", bridge_manager_init);
    main_boot_init("can_manager_init", can_manager_init);
    /* optional add-on packs register their settings/jacks/providers
       here (no-op in stock builds — see ext_manager.h) */
    main_boot_init("ext_manager_init", ext_manager_init);
    main_boot_init("uds_manager_init", uds_manager_init);
    main_boot_init("script_engine_init", script_engine_init);
    main_boot_init("led_manager_init", led_manager_init);
    main_boot_init("rtc_manager_init", rtc_manager_init);
    main_boot_init("imu_manager_init", imu_manager_init);
    main_boot_init("battery_monitor_init", battery_monitor_init);
    main_boot_init("cert_manager_init", cert_manager_init);
    main_boot_init("http_client_manager_init", http_client_manager_init);
    main_boot_init("mdns_manager_init", mdns_manager_init);
    main_boot_init("mqtt_manager_init", mqtt_manager_init);
    main_boot_init("mqtt_can_init", mqtt_can_init);
    main_boot_init("vpn_manager_init", vpn_manager_init);
    main_boot_init("iperf_manager_init", iperf_manager_init);
    main_boot_init("sleep_manager_init", sleep_manager_init);
    main_boot_init("usb_host_manager_init", usb_host_manager_init);
    main_boot_init("usb_acm_cli_init", usb_acm_cli_init);
    main_boot_init("espnetlink_link_init", espnetlink_link_init);
    main_boot_init("j2534_server_init", j2534_server_init);
    main_boot_init("cmdline_manager_init", cmdline_manager_init);
    main_boot_init("button_manager_init", button_manager_init);
    main_boot_init("interface_manager_init", interface_manager_init);
    main_boot_init("event_manager_init", event_manager_init); /* BEFORE the
        components that declare sources/actions in their inits */
    main_boot_init("autopid_init", autopid_init); /* /data mounted above */
    main_boot_init("ha_webhooks_init", ha_webhooks_init); /* HA telemetry link */
    main_boot_init("data_logger_init", data_logger_init);
    main_boot_init("ota_manager_init", ota_manager_init); /* before api_http */
    main_boot_init("api_http_init", api_http_init);
    /* network-trust lockdown: untrusted STA networks get 403 on every
       inbound admin surface (API/UI/WS); own-AP + USB stay open */
    (void)http_server_manager_set_request_gate(
        wifi_manager_http_request_allowed);
    main_boot_init("wifi_manager_register_http", wifi_manager_register_http);
    main_boot_init("battery_monitor_register_http",
              battery_monitor_register_http);
    main_boot_init("led_manager_register_http", led_manager_register_http);
    main_boot_init("autopid_register_http", autopid_register_http);
    main_boot_init("ha_webhooks_register_http", ha_webhooks_register_http);
    main_boot_init("event_manager_register_http", event_manager_register_http);
    main_boot_init("rtc_manager_register_http", rtc_manager_register_http);
    main_boot_init("data_logger_register_http", data_logger_register_http);
    main_boot_init("imu_manager_register_http", imu_manager_register_http);
    main_boot_init("cert_manager_register_http", cert_manager_register_http);
    main_boot_init("vpn_manager_register_http", vpn_manager_register_http);
    main_boot_init("sleep_manager_register_http",
              sleep_manager_register_http);
    main_boot_init("usb_host_manager_register_http",
              usb_host_manager_register_http);
    main_boot_init("usb_acm_cli_register_http", usb_acm_cli_register_http);
    main_boot_init("espnetlink_link_register_http",
              espnetlink_link_register_http);
    main_boot_init("j2534_server_register_http",
              j2534_server_register_http);
    main_boot_init("can_manager_register_http", can_manager_register_http);
    main_boot_init("uds_manager_register_http", uds_manager_register_http);
    main_boot_init("script_engine_register_http", script_engine_register_http);
    /* CLI commands: each component registers its own INSIDE its settings
       apply, gated by its `cli` setting (meatpi 2026-07-05 — ownership
       lives in the component, not here); main_cli adds only the
       composites + pending stubs */
    main_boot_init("main_cli_register", main_cli_register);
    /* the adapter layer: fixed jacks now; the settings-named socket/WS
       jacks at its start (after the settings apply pass below) */
    main_boot_init("bridge_endpoints_init", bridge_endpoints_init);
    /* CAN framing codecs (bridge_manager translators) — register before
       bridge_manager_start builds the configured bridges. */
    main_boot_init("translator_slcan_init", translator_slcan_init);
    main_boot_init("translator_realdash_init", translator_realdash_init);
    main_boot_init("translator_gvret_init", translator_gvret_init);
    /* built-in web UI: v2 XOR none (Kconfig WICAN_WEBUI). Registers its
       asset table only if it's the selected build; the catch-all is
       installed last, so /api + /ws always win. */
    main_boot_init("web_ui_v2_register", web_ui_v2_register);
    /* glue: OTA session state drives the CRITICAL LED indication */
    main_glue_wire_ota_led();
    /* glue: BLE console lines run through cmdline_manager */
    main_glue_wire_ble_cli();

    /* ---- the ONE settings apply pass, then start everything --------------- */
    main_boot_init("settings_manager_start", settings_manager_start);
    main_boot_init("log_manager_start", log_manager_start);

    bool fs_ok = main_boot_start("filesystem", filesystem_start);
    bool sd_ok = main_boot_start("external_storage", external_storage_start);
    /* LED first among the peripherals: boot state is visible early */
    bool led_ok = main_boot_start("led_manager", led_manager_start);
    bool rtc_ok = main_boot_start("rtc_manager", rtc_manager_start);
    bool imu_ok = main_boot_start("imu_manager", imu_manager_start);
    bool batt_ok = main_boot_start("battery_monitor",
                                   battery_monitor_start);
    bool wifi_ok = main_boot_start("wifi_manager", wifi_manager_start);
    /* esp-mdns tracks interface events itself — safe pre-network */
    bool mdns_ok = main_boot_start("mdns_manager", mdns_manager_start);
    /* certs before mqtt: cert_set consumers borrow at start */
    bool cert_ok = main_boot_start("cert_manager", cert_manager_start);
    /* network-gated internally; starts its own waiter task */
    bool mqtt_ok = main_boot_start("mqtt_manager", mqtt_manager_start);
    /* network-gated internally like mqtt; no ordering coupling (meatpi
       2026-07-07 — consumers behind the tunnel just retry) */
    main_boot_start("vpn_manager", vpn_manager_start);
    /* passive diagnostic; sessions start from the `iperf` CLI command */
    main_boot_start("iperf_manager", iperf_manager_start);
    /* glue: imu/battery events -> <prefix>/events via the async path */
    /* glue: the MQTT bench surface (bench/cmd burst + bench/echo) */
    main_boot_start("main_bench", main_bench_start);
    /* async since 2026-07-26 (was ~2 s of the 4.1 s boot): returns once
       the bring-up task is launched; consumers gate inside obd_chip, so
       obd=1 in the boot line = launch ok, chip outcome = the component's
       own "started:"/"bring-up failed" log lines */
    bool obd_ok = main_boot_start("obd_chip", obd_chip_start);
    bool ble_ok = main_boot_start("ble_manager", ble_manager_start);
    /* arbitration AFTER both radios exist (it actuates them) */
    main_boot_start("interface_manager", interface_manager_start);
    /* AFTER obd_chip + battery_monitor: it polls one, watches the other */
    /* CAN bus up before its consumers (firmware ISO-TP + add-on jacks
     * need the shared handle); pack endpoints are registered before
     * bridge_manager pulls them */
    main_boot_start("can_manager", can_manager_start);
    main_boot_start("ext_manager", ext_manager_start);
    main_boot_start("uds_manager", uds_manager_start);
    main_boot_start("script_engine", script_engine_start);
    bool apid_ok = main_boot_start("autopid", autopid_start);
    /* HA telemetry poster — reads autopid's snapshot + cached config */
    main_boot_start("ha_webhooks", ha_webhooks_start);
    /* AFTER external_storage: its writer follows the card mount state */
    main_boot_start("data_logger", data_logger_start);
    /* glue: autopid samples -> the logger's params stream */
    main_glue_wire_autopid_logger();
    /* the wired uplink; network-gated consumers pick it up via the
       ETH_CONNECTED bit exactly like the STA */
    main_boot_start("usb_host_manager", usb_host_manager_start);
    main_boot_start("usb_acm_cli", usb_acm_cli_start);
    /* the ESPNetLink dongle as the internet uplink: zero-touch pairing
       over USB, then its WiFi AP (USB = power only) or USB-Ethernet;
       AFTER wifi_manager + usb_host_manager (it reads both) */
    main_boot_start("espnetlink_link", espnetlink_link_start);
    /* glue: dongle GPS fixes (console poll OR the HTTP poll over the
       AP/USB) -> autopid parameters (gps_*), so they flow to HA / logger /
       dashboard / rules like any polled value; and the HTTP cache backs
       /api/gps when there is no console */
    main_glue_wire_gps();
    main_boot_start("j2534_server", j2534_server_start);
    /* glue: button long-press -> config mode (AP up, BLE off);
       start AFTER both radios exist (the callback actuates them) */
    main_glue_wire_button();
    main_boot_start("button_manager", button_manager_start);
    /* glue first (the ordered shutdown callback), then arm — LAST
       among the features: everything it powers down exists by now */
    main_sleep_wire();
    main_boot_start("sleep_manager", sleep_manager_start);
    /* dispatcher + timers; sources/actions registered at inits above */
    bool evt_ok = main_boot_start("event_manager", event_manager_start);
    bool sock_ok = main_boot_start("socket_manager", socket_manager_start);
    /* ws routes must register before http_server_manager_start (below) */
    bool ws_ok = main_boot_start("websocket_manager",
                                 websocket_manager_start);
    /* jacks from the applied settings (socket/WS names) + the USB
       port-B UART — MUST precede bridge_manager_start (registration
       is pre-start only) */
    bool usb_ok = main_boot_start("bridge_endpoints",
                                  bridge_endpoints_start);
    /* mqtt0 jack + canmqtt codec (no-op when mqtt_can disabled) —
       same pre-bridge_manager_start registration contract */
    main_boot_start("mqtt_can", mqtt_can_start);
    /* external log sinks AFTER websocket_manager (ws_log channel) and
       external_storage (file sink mount-follow); all gates default off */
    main_boot_start("log_sinks", log_sinks_start);
    bool br_ok = main_boot_start("bridge_manager", bridge_manager_start);
    /* the CLI must be up before bridges pull its endpoint queue */
    bool cli_ok = main_boot_start("cmdline_manager", cmdline_manager_start);
    bool ota_ok = main_boot_start("ota_manager", ota_manager_start);
    bool http_ok = main_boot_start("http_server_manager",
                                   http_server_manager_start);

    dev_status_manager_set(DEV_STATUS_BIT_AWAKE);

    /* ---- boot report (the system bench parses these lines) ---------------- */
    printf("WICAN BOOT id=%s fs=%d sd=%d led=%d rtc=%d imu=%d batt=%d "
           "wifi=%d mdns=%d cert=%d mqtt=%d obd=%d ble=%d sock=%d ws=%d "
           "bridge=%d cli=%d usb=%d ota=%d http=%d apid=%d evt=%d\n",
           dev_status_manager_device_id(), fs_ok, sd_ok, led_ok, rtc_ok,
           imu_ok, batt_ok, wifi_ok, mdns_ok, cert_ok, mqtt_ok, obd_ok,
           ble_ok, sock_ok, ws_ok, br_ok, cli_ok, usb_ok, ota_ok, http_ok,
           apid_ok, evt_ok);

    dev_status_memory_t mem;

    if (dev_status_manager_memory(&mem) == ESP_OK)
    {
        printf("WICAN MEM internal_free=%lu internal_largest=%lu "
               "psram_free=%lu psram_largest=%lu\n",
               (unsigned long)mem.internal.free,
               (unsigned long)mem.internal.largest_block,
               (unsigned long)mem.psram.free,
               (unsigned long)mem.psram.largest_block);
    }

    main_boot_ram_map_print();
    main_boot_health_report();

    ESP_LOGI(TAG, "WiCAN Pro up (v6 composition)");

    /* fragmentation watch: DEBUG-level memory report every 60 s (§12b);
       the flash-churn tripwire rides the same cadence */
    while (true)
    {
        vTaskDelay(pdMS_TO_TICKS(60000));

        main_boot_flash_watch();

        if (dev_status_manager_memory(&mem) == ESP_OK)
        {
            ESP_LOGD(TAG, "mem: int %lu/%lu (largest %lu, min %lu) "
                     "psram %lu/%lu (largest %lu)",
                     (unsigned long)mem.internal.free,
                     (unsigned long)mem.internal.total,
                     (unsigned long)mem.internal.largest_block,
                     (unsigned long)mem.internal.min_free,
                     (unsigned long)mem.psram.free,
                     (unsigned long)mem.psram.total,
                     (unsigned long)mem.psram.largest_block);
        }
    }
}
