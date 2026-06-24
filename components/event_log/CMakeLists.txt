idf_component_register(
    SRCS "event_log.c"
    INCLUDE_DIRS "include"
    REQUIRES freertos fatfs vfs esp_timer log esp_http_server
)
