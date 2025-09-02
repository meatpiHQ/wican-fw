idf_component_register(SRCS "cert_manager_http.c" "cert_manager_core.c" "cert_manager.c"
                    INCLUDE_DIRS "include"
                    REQUIRES main esp_http_server json fatfs spiffs littlefs nvs_flash)
