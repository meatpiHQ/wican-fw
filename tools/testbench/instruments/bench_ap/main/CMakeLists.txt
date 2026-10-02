idf_component_register(SRCS "bench_ap_main.c"
                       PRIV_REQUIRES console esp_netif esp_wifi nvs_flash esp_timer
                       INCLUDE_DIRS ".")
