idf_component_register(
    SRCS "restart_tracker.c"
         "restart_tracker_http.c"
    INCLUDE_DIRS "include"
    REQUIRES esp_timer esp_hw_support log esp_mm esp_http_server json
)