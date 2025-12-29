idf_component_register(
    SRCS "cmd_ping.c"
    INCLUDE_DIRS "include"
    REQUIRES console lwip esp_netif
)
