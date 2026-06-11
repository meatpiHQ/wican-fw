idf_component_register(
    SRCS "csv_logger.c"
    INCLUDE_DIRS "."
    REQUIRES main esp_timer json
)
