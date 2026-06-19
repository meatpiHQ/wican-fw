idf_component_register(
    SRCS "fast_log.c" "poll_log.c"
    INCLUDE_DIRS "."
    REQUIRES main autopid csv_logger driver esp_timer
)
