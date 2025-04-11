/*
 * This file is part of the WiCAN project.
 *
 * Copyright (C) 2022  Meatpi Electronics.
 * Written by Ali Slim <ali@meatpi.com>
 *
 * This program is free software: you can redistribute it and/or modify
 * it under the terms of the GNU General Public License as published by
 * the Free Software Foundation, either version 3 of the License, or
 * (at your option) any later version.
 *
 * This program is distributed in the hope that it will be useful,
 * but WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
 * GNU General Public License for more details.
 *
 * You should have received a copy of the GNU General Public License
 * along with this program.  If not, see <http://www.gnu.org/licenses/>.
 */

 #include "freertos/FreeRTOS.h"
 #include "freertos/task.h"
 #include  "freertos/queue.h"
 #include "freertos/event_groups.h"
 #include "esp_wifi.h"
 #include "esp_system.h"
 #include "esp_event.h"
 #include "esp_mac.h"
 #include "nvs_flash.h"
 #include "driver/gpio.h"
 #include "esp_log.h"
 #include <string.h>
 #include "lwip/sockets.h"
 #include "config_server.h"
 #include "ble.h"
 
 #define WIFI_CONNECTED_BIT 			BIT0
 #define WIFI_FAIL_BIT     			BIT1
 #define WIFI_DISCONNECTED_BIT      	BIT2
 #define WIFI_INIT_BIT     		 	BIT3
 #define WIFI_CONNECT_IDLE_BIT     	BIT4
 #define EXAMPLE_ESP_MAXIMUM_RETRY 	10
 
 static const char *WIFI_TAG = "wifi_network";
 static esp_netif_t* ap_netif;
 static esp_netif_t* sta_netif;
 static TaskHandle_t xwifi_handle = NULL;
 static int s_retry_num = 0;
 static EventGroupHandle_t s_wifi_event_group = NULL;
 static const TickType_t connect_delay[] = {1000, 1000, 1000, 1000, 1000,1000};
 static int8_t ap_auto_disable = 0;
 
 static void wifi_network_event_handler(void* arg, esp_event_base_t event_base,
                                 int32_t event_id, void* event_data)
 {
     static bool sta_successfuly_connected = false;
     
     if (event_base == WIFI_EVENT && event_id == WIFI_EVENT_STA_START)
     {
         ESP_LOGI(WIFI_TAG, "WIFI_EVENT_STA_START");
         esp_wifi_connect();
     }
     else if (event_base == WIFI_EVENT && event_id == WIFI_EVENT_STA_DISCONNECTED)
     {
         ESP_LOGI(WIFI_TAG, "WIFI_EVENT_STA_DISCONNECTED");
 
         xEventGroupSetBits(s_wifi_event_group, WIFI_DISCONNECTED_BIT);
         xEventGroupSetBits(s_wifi_event_group, WIFI_CONNECT_IDLE_BIT);
         xEventGroupClearBits(s_wifi_event_group, WIFI_CONNECTED_BIT);
 
         wifi_mode_t current_mode;
         esp_err_t err = esp_wifi_get_mode(&current_mode);
 
         if (err == ESP_OK && current_mode == WIFI_MODE_STA && !sta_successfuly_connected && ap_auto_disable)
         {
             ESP_LOGI(WIFI_TAG, "Switching to APSTA mode");
             esp_wifi_stop();
             esp_wifi_set_mode(WIFI_MODE_APSTA);
             esp_wifi_start();
             esp_wifi_connect();
         }
 
         sta_successfuly_connected = false;
     }
     else if (event_base == IP_EVENT && event_id == IP_EVENT_STA_GOT_IP)
     {
         wifi_mode_t current_mode;
         esp_err_t err = esp_wifi_get_mode(&current_mode);
 
         if (err == ESP_OK && current_mode == WIFI_MODE_APSTA && ap_auto_disable)
         {
             ip_event_got_ip_t *event = (ip_event_got_ip_t *)event_data;
             ESP_LOGI(WIFI_TAG, "Got IP: " IPSTR, IP2STR(&event->ip_info.ip));
             
             // Disable AP mode
             ESP_LOGI(WIFI_TAG, "Disabling AP mode...");
             esp_wifi_stop();
             esp_wifi_set_mode(WIFI_MODE_STA);
             sta_successfuly_connected = true;
             esp_wifi_start();
             esp_wifi_connect();
         }
         else
         {
             ip_event_got_ip_t *event = (ip_event_got_ip_t *)event_data;
             static char sta_ip[20] = {0};
             sprintf(sta_ip, "%d.%d.%d.%d", IP2STR(&event->ip_info.ip));
 
             config_server_set_sta_ip(sta_ip);
             s_retry_num = 0;
 
             ESP_LOGI(WIFI_TAG, "Got IP: " IPSTR, IP2STR(&event->ip_info.ip));
             if(ap_auto_disable)
             {
                 ESP_LOGI(WIFI_TAG, "Already in station-only mode");
             }
         }
  
         xEventGroupSetBits(s_wifi_event_group, WIFI_CONNECTED_BIT);
         xEventGroupClearBits(s_wifi_event_group, WIFI_DISCONNECTED_BIT);
         xEventGroupSetBits(s_wifi_event_group, WIFI_CONNECT_IDLE_BIT);
     }
 
     if (event_id == WIFI_EVENT_AP_STACONNECTED)
     {
         ESP_LOGI(WIFI_TAG, "WIFI_EVENT_AP_STACONNECTED");
         wifi_event_ap_staconnected_t* event = (wifi_event_ap_staconnected_t*) event_data;
         ESP_LOGI(WIFI_TAG, "station "MACSTR" join, AID=%d",
                  MAC2STR(event->mac), event->aid);
         if(config_server_get_ble_config())
         {
             ble_disable();
             ESP_LOGW(WIFI_TAG, "disable ble");
         }
     }
     else if (event_id == WIFI_EVENT_AP_STADISCONNECTED)
     {
         ESP_LOGI(WIFI_TAG, "WIFI_EVENT_AP_STADISCONNECTED");
         wifi_event_ap_stadisconnected_t* event = (wifi_event_ap_stadisconnected_t*) event_data;
         ESP_LOGI(WIFI_TAG, "station "MACSTR" leave, AID=%d",
                  MAC2STR(event->mac), event->aid);
         if(config_server_get_ble_config())
         {
             ble_enable();
             ESP_LOGW(WIFI_TAG, "enable ble");
         }
     }
     else if(event_id == WIFI_EVENT_AP_START)
     {
         ESP_LOGI(WIFI_TAG, "WIFI_EVENT_AP_START");
     }
 }
 
 void wifi_network_deinit(void)
 {
     xEventGroupWaitBits(s_wifi_event_group,
                         WIFI_CONNECT_IDLE_BIT,
                         pdFALSE,
                         pdFALSE,
                         portMAX_DELAY);
 
     xEventGroupClearBits(s_wifi_event_group, WIFI_INIT_BIT);
 
     ESP_LOGW(WIFI_TAG, "wifi deinit");
 
     esp_wifi_disconnect();
     esp_err_t err = esp_wifi_stop();
 
     esp_event_handler_unregister(IP_EVENT,
                                     IP_EVENT_STA_GOT_IP,
                                     &wifi_network_event_handler);
     esp_event_handler_unregister(WIFI_EVENT,
                                     ESP_EVENT_ANY_ID,
                                     &wifi_network_event_handler);
     if (err == ESP_ERR_WIFI_NOT_INIT)
     {
         return;
     }
 }
 void wifi_network_restart(void)
 {
     xEventGroupSetBits(s_wifi_event_group, WIFI_INIT_BIT);
     esp_err_t err = esp_wifi_start();
     esp_wifi_disconnect();
     if (err == ESP_ERR_WIFI_NOT_INIT)
     {
         return;
     }
 }
 
 void wifi_network_stop(void)
 {
     xEventGroupClearBits(s_wifi_event_group, WIFI_INIT_BIT);
     esp_wifi_stop();
 }
 
 void wifi_network_start(void)
 {
     xEventGroupSetBits(s_wifi_event_group, WIFI_INIT_BIT);
     esp_err_t err = esp_wifi_start();
     if (err == ESP_ERR_WIFI_NOT_INIT)
     {
         ESP_LOGE(WIFI_TAG, "wifi failed to start");
         return;
     }
 }
 
 bool wifi_network_is_connected(void)
 {
     EventBits_t ux_bits;
     if(s_wifi_event_group != NULL)
     {
         ux_bits = xEventGroupGetBits(s_wifi_event_group);
 
         return (ux_bits & WIFI_CONNECTED_BIT);
     }
     else return 0;
 }
 
 static void wifi_conn_task(void *pvParameters)
 {
     while(1)
     {
         xEventGroupWaitBits(s_wifi_event_group,
                     WIFI_INIT_BIT | WIFI_DISCONNECTED_BIT,
                     pdFALSE,
                     pdTRUE,
                     portMAX_DELAY);
         ESP_LOGI(WIFI_TAG, "Trying to connect...");
         xEventGroupClearBits(s_wifi_event_group, WIFI_CONNECT_IDLE_BIT);
         esp_wifi_connect();
         xEventGroupWaitBits(s_wifi_event_group,
                     WIFI_CONNECT_IDLE_BIT,
                     pdFALSE,
                     pdTRUE,
                     portMAX_DELAY);
         vTaskDelay(pdTICKS_TO_MS(connect_delay[s_retry_num++]));
         s_retry_num %= (sizeof(connect_delay)/sizeof(TickType_t));
     }
 }
 
 void wifi_network_init(char* sta_ssid, char* sta_pass)
 {
     if(s_wifi_event_group == NULL)
     {
         s_wifi_event_group = xEventGroupCreate();
         ap_netif = esp_netif_create_default_wifi_ap();
 
         sta_netif = esp_netif_create_default_wifi_sta();
     }
 
     if(xEventGroupGetBits(s_wifi_event_group) & WIFI_INIT_BIT)
     {
         return;
     }
 
     ap_auto_disable = config_server_get_ap_auto_disable();
 
     int8_t channel = config_server_get_ap_ch();
     if(channel == -1)
     {
         channel = 6;
     }
     ESP_LOGE(WIFI_TAG, "AP Channel:%d", channel);
 
     wifi_init_config_t cfg = WIFI_INIT_CONFIG_DEFAULT();
     ESP_ERROR_CHECK(esp_wifi_init(&cfg));
 
     if(config_server_get_ble_config())
     {
         ESP_ERROR_CHECK( esp_wifi_set_ps(WIFI_PS_MIN_MODEM) );
     }
     else
     {
         ESP_ERROR_CHECK( esp_wifi_set_ps(WIFI_PS_NONE) );
     }
 
     esp_event_handler_instance_t instance_any_id;
     esp_event_handler_instance_t instance_got_ip;
     ESP_ERROR_CHECK(esp_event_handler_instance_register(WIFI_EVENT,
                                                         ESP_EVENT_ANY_ID,
                                                         &wifi_network_event_handler,
                                                         NULL,
                                                         &instance_any_id));
     ESP_ERROR_CHECK(esp_event_handler_instance_register(IP_EVENT,
                                                         IP_EVENT_STA_GOT_IP,
                                                         &wifi_network_event_handler,
                                                         NULL,
                                                         &instance_got_ip));
     ESP_ERROR_CHECK(esp_wifi_set_storage(WIFI_STORAGE_RAM));
     static wifi_config_t wifi_config_sta = {
         .sta = {
             .ssid = "",
             .password = "",
             /* Setting a password implies station will connect to all security modes including WEP/WPA.
              * However these modes are deprecated and not advisable to be used. Incase your Access point
              * doesn't support WPA2, these mode can be enabled by commenting below line */
             .threshold.authmode = WIFI_AUTH_WPA3_PSK,
             .rm_enabled = 1,
             .btm_enabled = 1,
             .scan_method = WIFI_ALL_CHANNEL_SCAN,
             .sort_method = WIFI_CONNECT_AP_BY_SIGNAL,
             .bssid_set = false,
 
             .pmf_cfg = {
                 .capable = true,
                 .required = false
             },
         },
     };
 
     if(config_server_get_sta_security() == WIFI_WPA3_PSK)
     {
         wifi_config_sta.sta.threshold.authmode = WIFI_AUTH_WPA3_PSK;
     }
     else
     {
         wifi_config_sta.sta.threshold.authmode = WIFI_AUTH_WPA2_PSK;
     }
 
     static wifi_config_t wifi_config_ap =
     {
         .ap = {
             .max_connection = 4,
             .authmode = WIFI_AUTH_WPA2_WPA3_PSK
         },
     };
     wifi_config_ap.ap.channel = channel;
 
     if(config_server_get_wifi_mode() == APSTA_MODE || (sta_ssid != 0 && sta_pass != 0))
     {
         if(sta_ssid == 0 && sta_pass == 0)
         {
             strcpy( (char*)wifi_config_sta.sta.ssid, (char*)config_server_get_sta_ssid());
             strcpy( (char*)wifi_config_sta.sta.password, (char*)config_server_get_sta_pass());
         }
         else
         {
             strcpy( (char*)wifi_config_sta.sta.ssid, (char*)sta_ssid);
             strcpy( (char*)wifi_config_sta.sta.password, (char*)sta_pass);
         }
         ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_APSTA));
         ESP_ERROR_CHECK(esp_wifi_set_config(WIFI_IF_STA, &wifi_config_sta) );
         if(xwifi_handle == NULL)
         {
             xTaskCreate(wifi_conn_task, "wifi_conn_task", 4096, (void*)AF_INET, 5, &xwifi_handle);
         }
     }
     else
     {
         ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_AP));
     }
 
 
 
     uint8_t derived_mac_addr[6] = {0};
     ESP_ERROR_CHECK(esp_read_mac(derived_mac_addr, ESP_MAC_WIFI_SOFTAP));
     sprintf((char *)wifi_config_ap.ap.ssid,"WiCAN_%02x%02x%02x%02x%02x%02x",
             derived_mac_addr[0], derived_mac_addr[1], derived_mac_addr[2],
             derived_mac_addr[3], derived_mac_addr[4], derived_mac_addr[5]);
     strcpy( (char*)wifi_config_ap.ap.password, (char*)config_server_get_ap_pass());
 
     esp_err_t hostname_err = esp_netif_set_hostname(sta_netif, (char *)wifi_config_ap.ap.ssid);
     if (hostname_err == ESP_OK)
     {
         ESP_LOGI(WIFI_TAG, "Hostname set to: %s", (char *)wifi_config_ap.ap.ssid);
     }
     else
     {
         ESP_LOGE(WIFI_TAG, "Failed to set hostname: %s", esp_err_to_name(hostname_err));
     }
     
     esp_netif_ip_info_t ipInfo;
     #if HARDWARE_VER == WICAN_PRO
     IP4_ADDR(&ipInfo.ip, 192,168,0,10);
     IP4_ADDR(&ipInfo.gw, 192,168,0,10);
     #else
     IP4_ADDR(&ipInfo.ip, 192,168,80,1);
     IP4_ADDR(&ipInfo.gw, 192,168,80,1);
     #endif
     IP4_ADDR(&ipInfo.netmask, 255,255,255,0);
     esp_netif_dhcps_stop(ap_netif);
     esp_netif_set_ip_info(ap_netif, &ipInfo);
     esp_netif_dhcps_start(ap_netif);
 
     ESP_ERROR_CHECK(esp_wifi_set_config(WIFI_IF_AP, &wifi_config_ap));
     ESP_ERROR_CHECK(esp_wifi_set_bandwidth(WIFI_IF_AP, WIFI_BW_HT20));
     ESP_ERROR_CHECK(esp_wifi_start());
     xEventGroupSetBits(s_wifi_event_group, WIFI_INIT_BIT);
     xEventGroupSetBits(s_wifi_event_group, WIFI_DISCONNECTED_BIT);
     xEventGroupSetBits(s_wifi_event_group, WIFI_CONNECT_IDLE_BIT);
 
     ESP_LOGI(WIFI_TAG, "wifi_init finished.");
 
 }
 