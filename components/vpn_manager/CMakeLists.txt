idf_component_register(SRCS "vpn_manager.c" 
                             "vpn_manager_http.c"
                             "vpn_wireguard.c"
                             "vpn_config.c"
                       INCLUDE_DIRS "include"
                       REQUIRES esp_wireguard
                                esp_http_server 
                                json
                                esp_netif
                                freertos
                                esp_wifi
                                esp_system
                       PRIV_REQUIRES main)
