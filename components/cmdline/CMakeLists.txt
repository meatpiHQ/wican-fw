idf_component_register(
    SRCS "cmdline.c" "cmd_version.c" "cmd_system.c" "cmd_imu.c" "cmd_rtc.c" "cmd_led.c" "cmd_sdcard.c" "cmd_wifi.c" "cmd_wusb.c" "cmd_status.c" "cmd_factoryreset.c" "cmd_autopid.c" "cmd_debug.c"
    INCLUDE_DIRS "include"
    REQUIRES esp_timer main console lwip esp_app_format esp_wifi driver esp_netif heap eil__i2cdev json
)