idf_component_register(SRCS "ha_webhooks_config.c"
                            "ha_webhooks_http.c"
                       INCLUDE_DIRS "."
                       REQUIRES esp_http_server
                                json
                       PRIV_REQUIRES main)
