idf_component_register(
    SRCS "main.c"
         "main_bench.c"
         "main_boot.c"
         "main_cli.c"
         "main_glue.c"
         "main_heap.c"
         "main_safemode.c"
         "main_sleep.c"
    INCLUDE_DIRS "."
    EMBED_FILES "web/safemode.html"
    REQUIRES api_http autopid ha_webhooks battery_monitor ble_manager bridge_manager
             bridge_endpoints
             can_manager ext_manager uds_manager script_engine
             usb_acm_cli espnetlink_link j2534_server
             data_logger event_manager
             cert_manager cmdline_manager dev_status_manager
             external_storage filesystem http_client_manager
             http_server_manager i2c_bus
             imu_manager interface_manager iperf_manager led_manager
             log_manager log_sinks mdns_manager
             mqtt_can mqtt_manager obd_chip obd_gate ota_manager
             restart_tracker rtc_manager settings_manager socket_manager
             sleep_manager usb_host_manager vpn_manager
             websocket_manager wifi_manager esp_driver_uart esp_netif
             web_ui_v2
             translator_slcan translator_realdash translator_gvret
             esp_timer nvs_flash app_update spi_flash
             button_manager multipart_upload esp_wifi esp_http_server
             esp_partition esp_driver_gpio
)
