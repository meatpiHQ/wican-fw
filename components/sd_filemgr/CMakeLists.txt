idf_component_register(
    SRCS "sd_filemgr.c"
    INCLUDE_DIRS "."
    REQUIRES main csv_logger esp_http_server json fatfs event_log
)
