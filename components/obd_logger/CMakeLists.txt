idf_component_register(
    SRCS "obd_logger.c" "obd_logger_iface.c" "obd_logger_db_manager.c"
    INCLUDE_DIRS "."
    REQUIRES littlefs sqlite3 esp_timer main esp_http_server 
)