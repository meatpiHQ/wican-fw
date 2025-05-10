idf_component_register(
    SRCS "obd_logger.c"
    INCLUDE_DIRS "."
    REQUIRES littlefs sqlite3 esp_timer main
)