idf_component_register(
    SRCS "https_client_mgr.c" 
    INCLUDE_DIRS "."
    REQUIRES esp_timer main esp-tls esp_http_client mbedtls 
)