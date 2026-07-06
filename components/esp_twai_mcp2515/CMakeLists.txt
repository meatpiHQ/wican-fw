idf_component_register(
    SRCS "esp_twai_mcp2515.c"
    INCLUDE_DIRS "include"
    REQUIRES esp_driver_twai
    PRIV_REQUIRES esp_driver_gpio esp_driver_spi esp_timer
)
