idf_component_register(
    SRCS "ws_server.c"
    INCLUDE_DIRS "include"
    REQUIRES esp_http_server freertos log esp_system wican_common
)
