idf_component_register(SRCS "main.c" "sync_time.c"
                    INCLUDE_DIRS "."
                    REQUIRES esp_wifi lwip esp_wireguard nvs_flash)
