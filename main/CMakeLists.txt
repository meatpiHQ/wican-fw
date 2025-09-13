# Component CMakeLists.txt configuration
# See the ESP-IDF programming guide for detailed documentation

# Define source files
set(srcs
    "main.c"
    "comm_server.c"
    "config_server.c"
    "realdash.c"
    "slcan.c"
    "can.c"
    "ble.c"
    "wifi_network.c"
    "gvret.c"
    "wc_uart.c"
    "mqtt.c"
    "sleep_mode.c"
    "expression_parser.c"
    "led.c"
    "obd.c"
    "wusb3801.c"
    "wc_mdns.c"
    "wc_timer.c"
    "sdcard.c"
    "icm42670.c"
    "imu.c"
    "rtcm.c"
    "console.c"
    "hw_config.c"
    "dev_status.c"
    "obd2_standard_pids.c"
    "smartconnect.c"
    "vehicle.c"
    "wifi_mgr.c"
    "filesystem.c"
    "safemode.c"
    "multipart_upload.c"
    "sync_sys_time.c"
)

# Define required components
set(requires
    esp_timer
    esp_wifi
    nvs_flash
    fatfs
    vfs
    driver
    esp-tls
    esp_adc
    esp_eth
    log
    app_update
    esp_http_server
    bt
    spiffs
    freertos
    mqtt
    json
    wear_levelling
    esp_http_client
    spi_flash
    obd_logger
    https_client_mgr
    unity
    cmdline
    zorxx__multipart-parser
    debug_logs
    cert_manager
    vpn_manager
    autopid
)

# Register the component
idf_component_register(
    SRCS 
        "console.c"
        "rtcm.c"
        "usb_host.cpp"
        "ftp.c"
        "elm327.c"
        "obd.c"
        "wusb3801.c"
        "${srcs}"
        INCLUDE_DIRS "." 
        INCLUDE_DIRS        # Public include directories
        PRIV_INCLUDE_DIRS   # Private include directories
        REQUIRES "${requires}"
        PRIV_REQUIRES       spi_flash # Private requirements
        EMBED_FILES 
        "web/src/homepage.html"
        "web/src/dashboard.html"
        "web/src/dashboard.js"
        "web/src/lucide_icons.js"
        "web/src/main.js"
        "web/src/dashboard_live.js"
        "web/safemode.html"
        # "obd_fw/V2.3.18.txt"
        "obd_fw/V2.3.22.txt"
        "logo.txt"
)