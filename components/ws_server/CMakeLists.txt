# Component CMakeLists.txt configuration
# See the ESP-IDF programming guide for detailed documentation

idf_component_register(
    SRCS
        "ws_server.c"
        "ws_router.c"
    INCLUDE_DIRS
        "include"
    REQUIRES
        esp_http_server
        freertos
        log
        cjson
        cmdline
        wican_common
)
