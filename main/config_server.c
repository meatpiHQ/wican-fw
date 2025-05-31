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



#include <esp_wifi.h>
#include <esp_event.h>
#include <esp_log.h>
#include <esp_system.h>
#include <sys/param.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/event_groups.h"
#include <nvs_flash.h>
#include <sys/param.h>
#include "nvs_flash.h"
#include "esp_netif.h"
#include "esp_eth.h"
#include "esp_tls_crypto.h"
#include <esp_http_server.h>
#include "freertos/timers.h"
#include "esp_err.h"
#include <stdio.h>
#include <string.h>
#include <sys/unistd.h>
#include <sys/stat.h>
#include "ff.h"
#if USE_FATFS	
#include "esp_vfs_fat.h"
#endif	

#include "esp_vfs.h"
#include "config_server.h"
#include "cJSON.h"
#include<stdio.h>
#include <stdlib.h>
#include "ver.h"

#include <esp_wifi.h>
#include <esp_event.h>
#include <esp_log.h>
#include <esp_system.h>
#include <nvs_flash.h>
#include <sys/param.h>
#include "nvs_flash.h"
#include "esp_netif.h"
#include "esp_eth.h"
#include "lwip/sockets.h"

#include "esp_http_server.h"
#include "comm_server.h"
#include "types.h"
#include "driver/gpio.h"
#include "wifi_network.h"
#include "esp_vfs.h"
#include "esp_ota_ops.h"
#include "can.h"
#include "ble.h"
#include "sleep_mode.h"
#include "autopid.h"
#include "wc_mdns.h"
#include "elm327.h"
#include "hw_config.h"
#include "rtcm.h"
#include "esp_littlefs.h"
#include "obd_logger_iface.h"
#include "https_client_mgr.h"
#include "sdcard.h"

#define WIFI_CONNECTED_BIT			BIT0
#define WS_CONNECTED_BIT			BIT1
TaskHandle_t xwebsocket_handle = NULL;
static EventGroupHandle_t xServerEventGroup = NULL;
static QueueHandle_t xip_Queue = NULL;

static QueueHandle_t *xTX_Queue, *xRX_Queue;

static uint8_t ws_led;
#define TAG "CONFIG_SERVER"

httpd_handle_t server = NULL;
char *device_config_file = NULL;
static char *mqtt_canflt_file = NULL;
static char *device_id;

extern const unsigned char homepage_start[] asm("_binary_homepage_html_start");
extern const unsigned char homepage_end[]   asm("_binary_homepage_html_end");
extern const unsigned char logo_start[] asm("_binary_logo_txt_start");
extern const unsigned char logo_end[]   asm("_binary_logo_txt_end");
extern const unsigned char dashboard_html_start[] asm("_binary_dashboard_html_start");
extern const unsigned char dashboard_html_end[] asm("_binary_dashboard_html_end");
extern const unsigned char dashboard_js_start[] asm("_binary_dashboard_js_start");
extern const unsigned char dashboard_js_end[] asm("_binary_dashboard_js_end");
extern const unsigned char chart_js_start[] asm("_binary_chart_js_start");
extern const unsigned char chart_js_end[] asm("_binary_chart_js_end");
extern const unsigned char sql_wasm_js_start[] asm("_binary_sqlwasm_js_start");
extern const unsigned char sql_wasm_js_end[] asm("_binary_sqlwasm_js_end");
extern const unsigned char sql_wasm_wasm_start[] asm("_binary_sqlwasm_wasm_start");
extern const unsigned char sql_wasm_wasm_end[] asm("_binary_sqlwasm_wasm_end");
extern const unsigned char bootstrap_bundle_min_js_start[] asm("_binary_bootstrap_bundle_min_js_start");
extern const unsigned char bootstrap_bundle_min_js_end[] asm("_binary_bootstrap_bundle_min_js_end");
extern const unsigned char daterangepicker_min_js_start[] asm("_binary_daterangepicker_min_js_start");
extern const unsigned char daterangepicker_min_js_end[] asm("_binary_daterangepicker_min_js_end");
extern const unsigned char jquery_min_js_start[] asm("_binary_jquery_min_js_start");
extern const unsigned char jquery_min_js_end[] asm("_binary_jquery_min_js_end");
extern const unsigned char moment_min_js_start[] asm("_binary_moment_min_js_start");
extern const unsigned char moment_min_js_end[] asm("_binary_moment_min_js_end");
extern const unsigned char chartjs_adapter_moment_min_js_start[] asm("_binary_chartjs_adapter_moment_min_js_start");
extern const unsigned char chartjs_adapter_moment_min_js_end[] asm("_binary_chartjs_adapter_moment_min_js_end");
extern const unsigned char jszip_min_js_start[] asm("_binary_jszip_min_js_start");
extern const unsigned char jszip_min_js_end[] asm("_binary_jszip_min_js_end");
extern const unsigned char daterangepicker_css_start[] asm("_binary_daterangepicker_css_start");
extern const unsigned char daterangepicker_css_end[] asm("_binary_daterangepicker_css_end");
extern const unsigned char bootstrap_min_css_start[] asm("_binary_bootstrap_min_css_start");
extern const unsigned char bootstrap_min_css_end[] asm("_binary_bootstrap_min_css_end");

typedef struct {
    const char *uri;
    const char *content_type;
    const unsigned char *data_start;
    const unsigned char *data_end;
    bool load_from_fs;      
    const char *fs_path;
	const char *download_uri;
} file_lookup_t;

static const file_lookup_t file_lookup[] = {
    {"/dashboard.html", "text/html", dashboard_html_start, dashboard_html_end, false, NULL, NULL},
    {"/dashboard.js", "application/javascript", dashboard_js_start, dashboard_js_end, false, NULL, NULL},
	{"/chartjs-adapter-moment.min.js", "application/javascript", NULL, NULL, true, SD_CARD_MOUNT_POINT"/wican_data/web/chartjs-adapter-moment.min.js", "https://cdn.jsdelivr.net/npm/chartjs-adapter-moment@1.0.0/dist/chartjs-adapter-moment.min.js"},
	{"/jquery-3.6.0.min.js", "application/javascript", NULL, NULL, true, SD_CARD_MOUNT_POINT"/wican_data/web/jquery-3.6.0.min.js", "https://code.jquery.com/jquery-3.6.0.min.js"},
	{"/bootstrap.bundle.min.js", "application/javascript", NULL, NULL, true, SD_CARD_MOUNT_POINT"/wican_data/web/bootstrap.bundle.min.js", "https://cdn.jsdelivr.net/npm/bootstrap@5.1.3/dist/js/bootstrap.bundle.min.js"},
	{"/moment.min.js", "application/javascript", NULL, NULL, true, SD_CARD_MOUNT_POINT"/wican_data/web/moment.min.js", "https://cdn.jsdelivr.net/npm/moment/moment.min.js"},
	{"/daterangepicker.min.js", "application/javascript", NULL, NULL, true, SD_CARD_MOUNT_POINT"/wican_data/web/daterangepicker.min.js", "https://cdn.jsdelivr.net/npm/daterangepicker/daterangepicker.min.js"},
	{"/sql-wasm.js", "application/javascript", NULL, NULL, true, SD_CARD_MOUNT_POINT"/wican_data/web/sql-wasm.js", "https://cdn.jsdelivr.net/npm/sql.js@1.8.0/dist/sql-wasm.js"},
	{"/chart.js", "application/javascript", NULL, NULL, true, SD_CARD_MOUNT_POINT"/wican_data/web/chart.js", "https://cdn.jsdelivr.net/npm/chart.js"},
	{"/jszip.min.js", "application/javascript", NULL, NULL, true, SD_CARD_MOUNT_POINT"/wican_data/web/jszip.min.js", "https://cdn.jsdelivr.net/npm/jszip@3.7.1/dist/jszip.min.js"},
	{"/sql-wasm.wasm", "application/wasm", NULL, NULL, true, SD_CARD_MOUNT_POINT"/wican_data/web/sql-wasm.wasm", "https://cdn.jsdelivr.net/npm/sql.js@1.8.0/dist/sql-wasm.wasm"},
	{"/bootstrap.min.css", "text/css", NULL, NULL, true, SD_CARD_MOUNT_POINT"/wican_data/web/bootstrap.min.css", "https://cdn.jsdelivr.net/npm/bootstrap@5.1.3/dist/css/bootstrap.min.css"},
	{"/daterangepicker.min.css", "text/css", NULL, NULL, true, SD_CARD_MOUNT_POINT"/wican_data/web/daterangepicker.min.css", "https://cdn.jsdelivr.net/npm/daterangepicker/daterangepicker.min.css"},
	{"/daterangepicker.css", "text/css", NULL, NULL, true, SD_CARD_MOUNT_POINT"/wican_data/web/daterangepicker.min.css", "https://cdn.jsdelivr.net/npm/daterangepicker/daterangepicker.css"},

	{NULL, NULL, NULL, NULL, false, NULL, NULL} // Sentinel to mark end of array
};

static char can_datarate_str[11][7] = {
								"5k",
								"10K",
								"20K",
								"25K",
								"50K",
								"100K",
								"125K",
								"250K",
								"500K",
								"800K",
								"1000K",
};

const char device_config_default[] = "{\"wifi_mode\":\"AP\",\"ap_ch\":\"6\",\"sta_ssid\":\"MeatPi\",\"sta_pass\":\"TomatoSauce\",\"sta_security\":\"wpa3\",\"can_datarate\":\"500K\",\
										\"can_mode\":\"normal\",\"port_type\":\"tcp\",\"port\":\"35000\",\"ap_pass\":\"@meatpi#\",\"protocol\":\"elm327\",\"ble_pass\":\"123456\",\
										\"ble_status\":\"disable\",\"sleep_status\":\"disable\",\"sleep_volt\":\"13.1\",\"wakeup_volt\":\"13.5\",\"batt_alert\":\"disable\",\
										\"batt_alert_ssid\":\"MeatPi\",\"batt_alert_pass\":\"TomatoSauce\",\"batt_alert_volt\":\"11.0\",\"batt_alert_protocol\":\"mqtt\",\
										\"batt_alert_url\":\"mqtt://mqtt.eclipseprojects.io\",\"batt_alert_port\":\"1883\",\"batt_alert_topic\":\"CAR1/voltage\",\"batt_mqtt_user\":\"meatpi\",\
										\"batt_mqtt_pass\":\"meatpi\",\"batt_alert_time\":\"1\",\"mqtt_en\":\"disable\",\"mqtt_elm327_log\":\"disable\",\"mqtt_url\":\"mqtt://127.0.0.1\",\"mqtt_port\":\"1883\",\
										\"mqtt_user\":\"meatpi\",\"mqtt_pass\":\"meatpi\",\"mqtt_tx_topic\":\"wican/%s/can/tx\",\"mqtt_rx_topic\":\"wican/%s/can/rx\",\"mqtt_status_topic\":\"wican/%s/can/status\",\
										\"logger_status\":\"disable\",\"log_filesystem\":\"littlefs\",\"log_storage\":\"sdcard\",\"log_period\":\"10\"}";

// const char device_config_default[] = "{\"wifi_mode\":\"AP\",\"ap_ch\":\"6\", \"ap_auto_disable\": \"disable\",\"sta_ssid\":\"MeatPi\",\"sta_pass\":\"TomatoSauce\",\"sta_security\":\"wpa3\",\"can_datarate\":\"500K\",\"can_mode\":\"normal\",\"port_type\":\"tcp\",\"port\":\"35000\",\"ap_pass\":\"@meatpi#\",\"protocol\":\"elm327\",\"ble_pass\":\"123456\",\"ble_status\":\"disable\",\"sleep_status\":\"disable\",\"sleep_volt\":\"13.1\",\"wakeup_volt\":\"13.5\",\"batt_alert\":\"disable\",\"batt_alert_ssid\":\"MeatPi\",\"batt_alert_pass\":\"TomatoSauce\",\"batt_alert_volt\":\"11.0\",\"batt_alert_protocol\":\"mqtt\",\"batt_alert_url\":\"mqtt://mqtt.eclipseprojects.io\",\"batt_alert_port\":\"1883\",\"batt_alert_topic\":\"CAR1/voltage\",\"batt_mqtt_user\":\"meatpi\",\"batt_mqtt_pass\":\"meatpi\",\"batt_alert_time\":\"1\",\"mqtt_en\":\"disable\",\"mqtt_elm327_log\":\"disable\",\"mqtt_url\":\"mqtt://127.0.0.1\",\"mqtt_port\":\"1883\",\"mqtt_user\":\"meatpi\",\"mqtt_pass\":\"meatpi\",\"mqtt_tx_topic\":\"wican/%s/can/tx\",\"mqtt_rx_topic\":\"wican/%s/can/rx\",\"mqtt_status_topic\":\"wican/%s/can/status\"}";
// const char device_config_default[] = "{\"wifi_mode\":\"AP\",\"ap_ch\":\"6\", \"ap_auto_disable\": \"disable\",\"sta_ssid\":\"MeatPi\",\"sta_pass\":\"TomatoSauce\",\"sta_security\":\"wpa3\",\"can_datarate\":\"500K\",\"can_mode\":\"normal\",\"port_type\":\"tcp\",\"port\":\"35000\",\"ap_pass\":\"@meatpi#\",\"protocol\":\"elm327\",\"ble_pass\":\"123456\",\"ble_status\":\"disable\",\"sleep_status\":\"disable\",\"sleep_volt\":\"13.1\",\"wakeup_volt\":\"13.5\",\"periodic_wakeup\":\"disable\",\"wakeup_interval\":\"5\",\"batt_alert\":\"disable\",\"batt_alert_ssid\":\"MeatPi\",\"batt_alert_pass\":\"TomatoSauce\",\"batt_alert_volt\":\"11.0\",\"batt_alert_protocol\":\"mqtt\",\"batt_alert_url\":\"mqtt://mqtt.eclipseprojects.io\",\"batt_alert_port\":\"1883\",\"batt_alert_topic\":\"CAR1/voltage\",\"batt_mqtt_user\":\"meatpi\",\"batt_mqtt_pass\":\"meatpi\",\"batt_alert_time\":\"1\",\"mqtt_en\":\"disable\",\"mqtt_elm327_log\":\"disable\",\"mqtt_url\":\"mqtt://127.0.0.1\",\"mqtt_port\":\"1883\",\"mqtt_user\":\"meatpi\",\"mqtt_pass\":\"meatpi\",\"mqtt_tx_topic\":\"wican/%s/can/tx\",\"mqtt_rx_topic\":\"wican/%s/can/rx\",\"mqtt_status_topic\":\"wican/%s/can/status\"}";
static device_config_t device_config;
TimerHandle_t xrestartTimer;

void config_server_reboot(void)
{
	gpio_set_level(CAN_STDBY_GPIO_NUM, 1);
	elm327_sleep();
	ESP_LOGI(TAG, "reboot");
	printf("reboot command\n");
	xTimerStart( xrestartTimer, 0 );
}

/* Max length a file path can have on storage */
#define FILE_PATH_MAX (ESP_VFS_PATH_MAX + CONFIG_SPIFFS_OBJ_NAME_LEN)
/* Scratch buffer size */
#define SCRATCH_BUFSIZE  (1024*100)

#define MAX_FILE_SIZE   (2000*1024) // 200 KB
#define MAX_FILE_SIZE_STR "200KB"

struct file_server_data {
    /* Base path of file storage */
    char base_path[ESP_VFS_PATH_MAX + 1];

    /* Scratch buffer for temporary storage during file transfer */
    char *scratch;
};


/* Copies the full path into destination buffer and returns
 * pointer to path (skipping the preceding base path) */
static const char* get_path_from_uri(char *dest, const char *base_path, const char *uri, size_t destsize)
{
    const size_t base_pathlen = strlen(base_path);
    size_t pathlen = strlen(uri);

    const char *quest = strchr(uri, '?');
    if (quest) {
        pathlen = MIN(pathlen, quest - uri);
    }
    const char *hash = strchr(uri, '#');
    if (hash) {
        pathlen = MIN(pathlen, hash - uri);
    }

    if (base_pathlen + pathlen + 1 > destsize) {
        /* Full path string won't fit into destination buffer */
        return NULL;
    }

    /* Construct full path (base + path) */
    strcpy(dest, base_path);
    strlcpy(dest + base_pathlen, uri, pathlen + 1);

    /* Return pointer to path, skipping the base */
    return dest + base_pathlen;
}

int8_t config_server_get_wifi_mode(void)
{
	if(strcmp(device_config.wifi_mode, "AP") == 0)
	{
		return AP_MODE;
	}
	else if(strcmp(device_config.wifi_mode, "APStation") == 0)
	{
		return APSTA_MODE;
	}
	return -1;
}

int8_t config_server_get_ap_ch(void)
{
	int ch_val = atoi(device_config.ap_ch);

	if(ch_val > 0 && ch_val < 15)
	{
		return ch_val;
	}
	return -1;
}

char *config_server_get_sta_ssid(void)
{
	return device_config.sta_ssid;
}

char *config_server_get_ap_pass(void)
{
	return device_config.ap_pass;
}

int config_server_ble_pass(void)
{
	int ble_pass = atoi(device_config.ble_pass);

	if(ble_pass > 0 && ble_pass <= 999999)
	{
		return ble_pass;
	}
	return -1;
}

char *config_server_get_sta_pass(void)
{
	return device_config.sta_pass;
}
int8_t config_server_protocol(void)
{
	if(strcmp(device_config.protocol, "slcan") == 0)
	{
		return SLCAN;
	}
	else if(strcmp(device_config.protocol, "realdash66") == 0)
	{
		return REALDASH;
	}
	else if(strcmp(device_config.protocol, "savvycan") == 0)
	{
		return SAVVYCAN;
	}
	else if(strcmp(device_config.protocol, "elm327") == 0)
	{
		return OBD_ELM327;
	}
	else if(strcmp(device_config.protocol, "auto_pid") == 0)
	{
		return AUTO_PID;
	}
	return OBD_ELM327;
}

int8_t config_server_get_can_rate(void)
{
	ESP_LOGI(TAG, "device_config.can_datarate:%s", device_config.can_datarate);
	if(strcmp(device_config.can_datarate, "5K") == 0)
	{
		return CAN_5K;
	}
	if(strcmp(device_config.can_datarate, "10K") == 0)
	{
		return CAN_10K;
	}
	if(strcmp(device_config.can_datarate, "20K") == 0)
	{
		return CAN_20K;
	}
	if(strcmp(device_config.can_datarate, "25K") == 0)
	{
		return CAN_25K;
	}
	else if(strcmp(device_config.can_datarate, "50K") == 0)
	{
		return CAN_50K;
	}
	else if(strcmp(device_config.can_datarate, "100K") == 0)
	{
		return CAN_100K;
	}
	else if(strcmp(device_config.can_datarate, "125K") == 0)
	{
		return CAN_125K;
	}
	else if(strcmp(device_config.can_datarate, "250K") == 0)
	{
		return CAN_250K;
	}
	else if(strcmp(device_config.can_datarate, "500K") == 0)
	{
		return CAN_500K;
	}
	else if(strcmp(device_config.can_datarate, "800K") == 0)
	{
		return CAN_800K;
	}
	else if(strcmp(device_config.can_datarate, "1000K") == 0)
	{
		return CAN_1000K;
	}
	else if(strcmp(device_config.can_datarate, "auto") == 0)
	{
		return CAN_AUTO;
	}

	return -1;
}


int8_t config_server_get_can_mode(void)
{
	if(strcmp(device_config.can_mode, "normal") == 0)
	{
		return CAN_NORMAL;
	}
	else if(strcmp(device_config.can_mode, "silent") == 0)
	{
		return CAN_SILENT;
	}
	return -1;
}

int8_t config_server_get_port_type(void)
{
	if(strcmp(device_config.port_type, "tcp") == 0)
	{
		return TCP_PORT;
	}
	else if(strcmp(device_config.port_type, "udp") == 0)
	{
		return UDP_PORT;
	}
	return -1;
}

int32_t config_server_get_port(void)
{
	int port_val = atoi(device_config.port);

	if(port_val > 0 && port_val <= 65535)
	{
		return port_val;
	}
	return -1;
}

// Create directories recursively if they don't exist
static esp_err_t create_dir_recursively(const char* path) {
    if (!path) return ESP_ERR_INVALID_ARG;
    
    char* temp_path = strdup(path);
    if (!temp_path) return ESP_ERR_NO_MEM;
    
    // Make sure the path ends with a delimiter
    size_t len = strlen(temp_path);
    if (temp_path[len-1] != '/') {
        char* new_path = realloc(temp_path, len + 2);
        if (!new_path) {
            free(temp_path);
            return ESP_ERR_NO_MEM;
        }
        temp_path = new_path;
        temp_path[len] = '/';
        temp_path[len+1] = '\0';
    }
    
    // Create parent directories
    for (char* p = temp_path + 1; *p; p++) {
        if (*p == '/') {
            *p = '\0';
            
            // Try to create directory
            if (mkdir(temp_path, 0755) != 0) {
                // Ignore error if directory already exists
                if (errno != EEXIST) {
                    ESP_LOGE(TAG, "Failed to create directory %s: %s", temp_path, strerror(errno));
                }
            } else {
                ESP_LOGI(TAG, "Created directory: %s", temp_path);
            }
            
            *p = '/';
        }
    }
    
    free(temp_path);
    return ESP_OK;
}

static esp_err_t file_handler(httpd_req_t *req)
{
    char *uri = req->uri;
	const uint32_t chunk_size = 1024 * 64;
    ESP_LOGI(TAG, "Request URI: %s", uri);
    
    // Look for the requested URI in our lookup table
    const file_lookup_t *file = file_lookup;
    while (file->uri != NULL) {
        if (strcmp(uri, file->uri) == 0) {
            ESP_LOGI(TAG, "Found file entry: %s", file->uri);
            
            // If configured to load from filesystem
            if (file->load_from_fs && file->fs_path != NULL) {
                struct stat st;
                if (stat(file->fs_path, &st) == 0) {
                    // File exists on filesystem, serve it
                    ESP_LOGI(TAG, "Serving file from filesystem: %s", file->fs_path);
                    FILE *fp = fopen(file->fs_path, "r");
                    if (fp) {
                        httpd_resp_set_type(req, file->content_type);
                        char *buffer = malloc(chunk_size);
                        if (!buffer) {
                            fclose(fp);
                            return ESP_ERR_NO_MEM;
                        }
                        
                        size_t bytes_read;
                        while ((bytes_read = fread(buffer, 1, chunk_size, fp)) > 0) {
                            httpd_resp_send_chunk(req, buffer, bytes_read);
                        }
                        httpd_resp_send_chunk(req, NULL, 0); // End response
                        free(buffer);
                        fclose(fp);
                        return ESP_OK;
                    }
                } else if (file->download_uri != NULL) {
					// File doesn't exist, try to download it
					ESP_LOGI(TAG, "File not found on filesystem, attempting to download from: %s", file->download_uri);
					
					esp_err_t ret = https_client_mgr_init();
					if (ret != ESP_OK) {
						ESP_LOGE(TAG, "Failed to initialize HTTPS client: %s", esp_err_to_name(ret));
						httpd_resp_send_500(req);
						return ESP_FAIL;
					}
					
					// Create directories recursively
					char *path_copy = strdup(file->fs_path);
					if (path_copy) {
						char *last_slash = strrchr(path_copy, '/');
						if (last_slash) {
							*last_slash = '\0';
							create_dir_recursively(path_copy);
						}
						free(path_copy);
					}
					
					// Download the file
					ret = https_client_mgr_download_file(
						file->download_uri,
						file->fs_path,
						NULL  // No progress callback in this context
					);
					
					https_client_mgr_deinit();
					
					if (ret == ESP_OK) {
						ESP_LOGI(TAG, "File downloaded successfully: %s", file->fs_path);
						
						// Now serve the downloaded file
						FILE *fp = fopen(file->fs_path, "r");
						if (fp) {
							httpd_resp_set_type(req, file->content_type);
							char *buffer = malloc(chunk_size);
							if (!buffer) {
								fclose(fp);
								httpd_resp_send_500(req);
								return ESP_ERR_NO_MEM;
							}
							
							size_t bytes_read;
							while ((bytes_read = fread(buffer, 1, chunk_size, fp)) > 0) {
								httpd_resp_send_chunk(req, buffer, bytes_read);
							}
							httpd_resp_send_chunk(req, NULL, 0); // End response
							free(buffer);
							fclose(fp);
							return ESP_OK;
						} else {
							ESP_LOGE(TAG, "Failed to open downloaded file: %s", file->fs_path);
						}
					} else {
						ESP_LOGE(TAG, "Failed to download file: %s, error: %s", 
								file->download_uri, esp_err_to_name(ret));
					}
				}				
            }
            
            // Fallback to serving from memory if filesystem loading failed or wasn't configured
            if (file->data_start != NULL && file->data_end != NULL) {
                ESP_LOGI(TAG, "Serving file from memory: %s", file->uri);
                httpd_resp_set_type(req, file->content_type);
                const size_t file_size = file->data_end - file->data_start;
                esp_err_t ret = httpd_resp_send(req, (const char*)file->data_start, file_size);
                return (ret == ESP_OK) ? ESP_OK : ESP_FAIL;
            }
            
            // If we get here, we couldn't serve the file
            ESP_LOGE(TAG, "Failed to serve file: %s", file->uri);
            httpd_resp_send_500(req);
            return ESP_FAIL;
        }
        file++;
    }
    
    // If we get here, the requested URI wasn't found in our lookup table
    // Return 404 Not Found
    httpd_resp_send_404(req);
    return ESP_OK;
}

static esp_err_t index_handler(httpd_req_t *req)
{
    httpd_resp_set_type(req, "text/html");
    
    const size_t homepage_size = homepage_end - homepage_start;
    
    esp_err_t ret = httpd_resp_send(req, (const char*)homepage_start, homepage_size);
    
    return (ret == ESP_OK) ? ESP_OK : ESP_FAIL;
}

static esp_err_t store_config_handler(httpd_req_t *req)
{
    char *buf = NULL;
    size_t buf_size = req->content_len;

    if (buf_size <= 0)
    {
        return ESP_FAIL; // Invalid content length
    }

    buf = (char *)malloc(buf_size);
    if (!buf)
    {
        return ESP_ERR_NO_MEM; // Memory allocation failure
    }

    int ret = httpd_req_recv(req, buf, buf_size);

    if (ret <= 0)
    {
        if (ret == HTTPD_SOCK_ERR_TIMEOUT)
        {
            // Retry receiving if timeout occurred
            free(buf);
            return ESP_FAIL;
        }
        // Handle read error
        free(buf);
        return ESP_FAIL;
    }

    FILE *f = fopen(FS_MOUNT_POINT"/config.json", "w");
    if (f)
    {
        // Write the received data into the file
        fwrite(buf, 1, buf_size, f);
        fclose(f);
    }
    else
    {
        // Handle file open error
        free(buf);
        return ESP_FAIL;
    }

    // Free dynamically allocated memory
    free(buf);

    const char *resp_str = "Configuration saved! Rebooting...";
    httpd_resp_send(req, resp_str, HTTPD_RESP_USE_STRLEN);

    // xTimerStart( xrestartTimer, 0 );
	config_server_reboot();
    return ESP_OK;
}

static esp_err_t store_canflt_handler(httpd_req_t *req)
{
    char *buf = NULL;
    size_t buf_size = req->content_len;

    if (buf_size <= 0)
    {
        return ESP_FAIL; // Invalid content length
    }

    buf = (char *)malloc(buf_size);
    if (!buf)
    {
        return ESP_ERR_NO_MEM; // Memory allocation failure
    }

    int ret = httpd_req_recv(req, buf, buf_size);

    if (ret <= 0)
    {
        if (ret == HTTPD_SOCK_ERR_TIMEOUT)
        {
            // Retry receiving if timeout occurred
            free(buf);
            return ESP_FAIL;
        }
        // Handle read error
        free(buf);
        return ESP_FAIL;
    }

    FILE *f = fopen(FS_MOUNT_POINT"/mqtt_canfilt.json", "w");
    if (f)
    {
        // Write the received data into the file
        fwrite(buf, 1, buf_size, f);
        fclose(f);
    }
    else
    {
        // Handle file open error
        free(buf);
        return ESP_FAIL;
    }

    // Free dynamically allocated memory
    free(buf);

	free(mqtt_canflt_file);
	mqtt_canflt_file = NULL;
	FILE* f1 = fopen(FS_MOUNT_POINT"/mqtt_canfilt.json", "r");
	if (f1 != NULL)
	{
		fseek(f1, 0, SEEK_END);
		int32_t filesize = ftell(f1);
		fseek(f1, 0, SEEK_SET);
		mqtt_canflt_file = malloc(filesize+1);
		ESP_LOGI(__func__, "mqtt_canflt_file File size: %ld", filesize);
		fseek(f1, 0, SEEK_SET);
		fread(mqtt_canflt_file, sizeof(char), filesize, f1);
		mqtt_canflt_file[filesize] = 0;
		fseek(f1, 0, SEEK_SET);
		ESP_LOGI(TAG, "mqtt_canfilt.json: %s", mqtt_canflt_file);
	}
    const char *resp_str = "CAN filter saved! Filter will take effect after submit.";
    httpd_resp_send(req, resp_str, HTTPD_RESP_USE_STRLEN);

    return ESP_OK;
}

static esp_err_t load_canflt_handler(httpd_req_t *req)
{
	if(mqtt_canflt_file != NULL)
	{
		const char* resp_str = (const char*)mqtt_canflt_file;
		httpd_resp_set_type(req, "application/json");
		httpd_resp_send(req, (const char*)resp_str, HTTPD_RESP_USE_STRLEN);
		ESP_LOGI(TAG, "mqtt_canflt_file: %s", mqtt_canflt_file);
	}
	else
	{
		const char* resp_str = (const char*) "NONE";
		httpd_resp_send(req, (const char*)resp_str, HTTPD_RESP_USE_STRLEN);
		ESP_LOGI(TAG, "mqtt_canflt_file: NONE");
	}

    return ESP_OK;
}

static esp_err_t load_pid_auto_handler(httpd_req_t *req)
{
    FILE *f = fopen(FS_MOUNT_POINT"/auto_pid.json", "r");
    if (f == NULL) 
	{
        const char* resp_str = "NONE";
        httpd_resp_send(req, resp_str, HTTPD_RESP_USE_STRLEN);
        ESP_LOGI(TAG, "auto_pid.json: NONE");
        return ESP_OK;
    }

    fseek(f, 0, SEEK_END);
    long filesize = ftell(f);
    fseek(f, 0, SEEK_SET);

    char *buf = malloc(filesize + 1);
    if (!buf)
	{
        fclose(f);
        httpd_resp_send_500(req);
        return ESP_FAIL;
    }

    size_t read = fread(buf, 1, filesize, f);
    fclose(f);
    
    if (read != filesize)
	{
        free(buf);
        httpd_resp_send_500(req);
        return ESP_FAIL;
    }

    buf[filesize] = 0;
    ESP_LOGI(TAG, "auto_pid.json: %s", buf);
    
	httpd_resp_set_type(req, "application/json");
    httpd_resp_send(req, buf, HTTPD_RESP_USE_STRLEN);
    free(buf);

    return ESP_OK;
}

static esp_err_t load_pid_auto_config_handler(httpd_req_t *req)
{
    const char *filepath = FS_MOUNT_POINT"/car_data.json";
    ESP_LOGI(TAG, "Opening file: %s", filepath);
    FILE *fd = fopen(filepath, "r");

    if (fd == NULL)
    {
        ESP_LOGE(TAG, "File does not exist: %s", filepath);
        httpd_resp_send(req, "NONE", HTTPD_RESP_USE_STRLEN);
        return ESP_OK;
    }

    // Seek to the end of the file to determine its size
    fseek(fd, 0, SEEK_END);
    long file_size = ftell(fd);
    rewind(fd);

    if (file_size <= 0)
    {
        ESP_LOGE(TAG, "File is empty or invalid: %s", filepath);
        fclose(fd);
        httpd_resp_send(req, "NONE", HTTPD_RESP_USE_STRLEN);
        return ESP_OK;
    }

    ESP_LOGI(TAG, "File size: %ld bytes", file_size);

    // Allocate memory on the heap to hold the file content
    char *buf = (char *)malloc(file_size + 1);
    if (buf == NULL)
    {
        ESP_LOGE(TAG, "Failed to allocate memory for file content");
        fclose(fd);
        httpd_resp_send_err(req, HTTPD_500_INTERNAL_SERVER_ERROR, "Memory allocation failed");
        return ESP_FAIL;
    }

    // Read the file into the buffer
    size_t read_len = fread(buf, 1, file_size, fd);
    fclose(fd);

    if (read_len != file_size)
    {
        ESP_LOGE(TAG, "Failed to read the entire file. Read %zu bytes out of %ld", read_len, file_size);
        free(buf);
        httpd_resp_send(req, "NONE", HTTPD_RESP_USE_STRLEN);
        return ESP_OK;
    }

    // Find the last closing brace and terminate the string there
    char *last_brace = strrchr(buf, '}');
    if (last_brace != NULL) {
        *(last_brace + 1) = '\0';  // Terminate string right after the last }
        read_len = last_brace - buf + 1;  // Update length to new size
    }

    ESP_LOGI(TAG, "Sending response: %s", buf);
    httpd_resp_set_type(req, "application/json");
    httpd_resp_send(req, buf, read_len);  // Use actual content length instead of HTTPD_RESP_USE_STRLEN
    
    free(buf);
    return ESP_OK;
}

static esp_err_t load_config_handler(httpd_req_t *req)
{
    const char* resp_str = (const char*)device_config_file;
	httpd_resp_set_type(req, "application/json");
    httpd_resp_send(req, (const char*)resp_str, HTTPD_RESP_USE_STRLEN);
    ESP_LOGI(TAG, "device_config_file: %s", device_config_file);
	UBaseType_t stack_high_watermark = uxTaskGetStackHighWaterMark(NULL);
	ESP_LOGI(TAG, "Task stack high watermark: %u words", stack_high_watermark);
    return ESP_OK;
}

static esp_err_t load_car_config_handler(httpd_req_t *req)
{
    char *response_str = autopid_get_config();
    
    if (response_str) {
        ESP_LOGI(TAG, "Sending response: %s", response_str);
        httpd_resp_set_type(req, "application/json");
        httpd_resp_send(req, response_str, HTTPD_RESP_USE_STRLEN);
        free(response_str);  // Free the allocated string
    } else {
        ESP_LOGE(TAG, "Failed to generate JSON response");
        httpd_resp_send_err(req, HTTPD_500_INTERNAL_SERVER_ERROR, "Failed to generate JSON");
    }
    
    return ESP_OK;
}


static esp_err_t system_reboot_handler(httpd_req_t *req)
{
	const char *resp_str = "Configuration saved! Rebooting...";
    httpd_resp_send(req, resp_str, HTTPD_RESP_USE_STRLEN);
	config_server_reboot();
	// elm327_sleep();
	// ESP_LOGI(TAG, "reboot");
	// xTimerStart( xrestartTimer, 0 );
    // esp_restart();
    return ESP_OK;
}

static esp_err_t system_commands_handler(httpd_req_t *req)
{
    char *buf = NULL;
    size_t buf_size = req->content_len;

    if (buf_size <= 0)
    {
        return ESP_FAIL;
    }

    buf = (char *)malloc(buf_size + 1);
    if (!buf)
    {
        ESP_LOGE(TAG, "Memory allocation failure");
        return ESP_ERR_NO_MEM;
    }

    int ret = httpd_req_recv(req, buf, buf_size);
    if (ret <= 0)
    {
        free(buf);
        return ESP_FAIL;
    }
    buf[ret] = 0;

    cJSON *root = cJSON_Parse(buf);
    if (root == NULL)
    {
        free(buf);
        return ESP_FAIL;
    }

    cJSON *command = cJSON_GetObjectItem(root, "command");
    if (command != NULL && cJSON_IsString(command) && command->valuestring != NULL)
    {
        const char *cmd = command->valuestring;
        ESP_LOGI(TAG, "Received command: %s", cmd);
  
        if (cmd != NULL && strlen(cmd) > 0)
        {
            if (strcmp(cmd, "reboot") == 0)
            {
                if (xrestartTimer != NULL)
                {
                    xTimerStart(xrestartTimer, 0);
                }
                else
                {
                    ESP_LOGE(TAG, "Restart timer is NULL");
                }
            }
            else if (strcmp(cmd, "force_update_obd") == 0)
            {
                elm327_update_obd(true);
            }
            else if (strcmp(cmd, "set_rtc_time") == 0)
            {
                // Get time parameters from JSON
                cJSON *hour = cJSON_GetObjectItem(root, "hour");
                cJSON *min = cJSON_GetObjectItem(root, "min");
                cJSON *sec = cJSON_GetObjectItem(root, "sec");
                cJSON *year = cJSON_GetObjectItem(root, "year");
                cJSON *month = cJSON_GetObjectItem(root, "month");
                cJSON *day = cJSON_GetObjectItem(root, "day");
                cJSON *weekday = cJSON_GetObjectItem(root, "weekday");
                
                // Check if all required parameters are present and valid
                if (hour && min && sec && year && month && day && weekday &&
                    cJSON_IsNumber(hour) && cJSON_IsNumber(min) && cJSON_IsNumber(sec) &&
                    cJSON_IsNumber(year) && cJSON_IsNumber(month) && cJSON_IsNumber(day) &&
                    cJSON_IsNumber(weekday))
                {
                    // Set time
                    esp_err_t time_err = rtcm_set_time(
                        (uint8_t)hour->valueint, 
                        (uint8_t)min->valueint, 
                        (uint8_t)sec->valueint
                    );
                    
                    // Set date
                    esp_err_t date_err = rtcm_set_date(
                        (uint8_t)year->valueint,
                        (uint8_t)month->valueint,
                        (uint8_t)day->valueint,
                        (uint8_t)weekday->valueint
                    );
                    
                    if (time_err == ESP_OK && date_err == ESP_OK) {
                        ESP_LOGI(TAG, "RTC time set successfully");
                    } else {
                        ESP_LOGE(TAG, "Failed to set RTC time: time_err=%d, date_err=%d", 
                                time_err, date_err);
                    }
                }
                else
                {
                    ESP_LOGE(TAG, "Missing or invalid parameters for set_rtc_time command");
                }
            }
        }
        else
        {
            ESP_LOGE(TAG, "Empty command received");
        }
    }
    else
    {
        ESP_LOGE(TAG, "Invalid or missing command");
    }
    cJSON_Delete(root);
    free(buf);

    const char *resp_str = "Command executed";
    httpd_resp_send(req, resp_str, HTTPD_RESP_USE_STRLEN);

    return ESP_OK;
}


static esp_err_t logo_handler(httpd_req_t *req)
{
    size_t logo_size = logo_end - logo_start;
	
	httpd_resp_set_type(req, "image/svg+xml");
    httpd_resp_send(req, (const char*)logo_start, logo_size);
    return ESP_OK;
}

static esp_err_t store_auto_data_handler(httpd_req_t *req)
{
    if (!req)
	{
        return ESP_ERR_INVALID_ARG;
    }

    char *buf = NULL;
    size_t buf_size = req->content_len;
    esp_err_t ret = ESP_OK;

    // Validate content length
    if (buf_size <= 0 || buf_size > MAX_FILE_SIZE)
	{
        ESP_LOGE(TAG, "Invalid content length: %d", buf_size);
        httpd_resp_send_err(req, HTTPD_400_BAD_REQUEST, "Invalid content length");
        return ESP_FAIL;
    }

    // Allocate buffer with extra byte for null termination
    buf = (char *)calloc(1, buf_size + 1);
    if (!buf)
	{
        ESP_LOGE(TAG, "Memory allocation failed for size %d", buf_size + 1);
        httpd_resp_send_err(req, HTTPD_500_INTERNAL_SERVER_ERROR, "Memory allocation failed");
        return ESP_ERR_NO_MEM;
    }

    // Receive data with timeout handling
    int received = httpd_req_recv(req, buf, buf_size);
    if (received <= 0)
	{
        ESP_LOGE(TAG, "Failed to receive data: %d", received);
        ret = ESP_FAIL;
        goto cleanup;
    }

    // Validate received data length
    if (received != buf_size)
	{
        ESP_LOGE(TAG, "Incomplete data received: %d/%d", received, buf_size);
        ret = ESP_FAIL;
        goto cleanup;
    }

    buf[received] = '\0';
    
    // Validate JSON format
    cJSON *json = cJSON_Parse(buf);
    if (!json)
	{
        ESP_LOGE(TAG, "Invalid JSON format");
        ret = ESP_FAIL;
        goto cleanup;
    }
    cJSON_Delete(json);

    ESP_LOGI(TAG, "Auto Table json: %s", buf);

    // Open file with error handling
    FILE *f = fopen(FS_MOUNT_POINT"/auto_pid.json", "w");
    if (!f)
	{
        ESP_LOGE(TAG, "Failed to open file for writing");
        ret = ESP_FAIL;
        goto cleanup;
    }

    // Write data with size verification
    size_t written = fwrite(buf, 1, received, f);
    if (written != received) 
	{
        ESP_LOGE(TAG, "File write failed: %d/%d bytes", written, received);
        fclose(f);
        ret = ESP_FAIL;
        goto cleanup;
    }

    fclose(f);

    // Send success response
    const char *resp_str = "Auto PID table will take effect after submit.";
    httpd_resp_send(req, resp_str, HTTPD_RESP_USE_STRLEN);

cleanup:
    if (buf)
	{
        free(buf);
    }
    
    if (ret != ESP_OK)
	{
        httpd_resp_send_err(req, HTTPD_500_INTERNAL_SERVER_ERROR, "Failed to store data");
    }

    return ret;
}

static esp_err_t store_car_data_handler(httpd_req_t *req)
{
    int total_len = req->content_len;
    int received = 0;
	const char *filepath = FS_MOUNT_POINT"/car_data.json";

    FILE *file = fopen(filepath, "w");
    if (!file)
    {
        ESP_LOGE(TAG, "Failed to open file for writing");
        httpd_resp_send_err(req, HTTPD_500_INTERNAL_SERVER_ERROR, "Failed to open file for writing");
        return ESP_FAIL;
    }

    static char buffer[16];
    while (received < total_len)
    {
        int ret = httpd_req_recv(req, buffer, sizeof(buffer));
        if (ret <= 0)
        {
            ESP_LOGE(TAG, "Failed to receive JSON data");
            fclose(file);
            httpd_resp_send_err(req, HTTPD_500_INTERNAL_SERVER_ERROR, "Failed to receive JSON data");
            return ESP_FAIL;
        }
        if (fwrite(buffer, 1, ret, file) != ret)
        {
            ESP_LOGE(TAG, "Failed to write data to file");
            fclose(file);
            httpd_resp_send_err(req, HTTPD_500_INTERNAL_SERVER_ERROR, "Failed to write data to file");
            return ESP_FAIL;
        }
        received += ret;
    }

    fclose(file);
    ESP_LOGI(TAG, "JSON data successfully stored");

    httpd_resp_send(req, "Data stored successfully", HTTPD_RESP_USE_STRLEN);
    return ESP_OK;
}

static esp_err_t check_status_handler(httpd_req_t *req)
{

	char ip_str[20] = {0};
	config_server_get_sta_ip(ip_str);
	cJSON *root = cJSON_CreateObject();
	static char fver[16];
	static char hver[32];
    const esp_partition_t *running = esp_ota_get_running_partition();
    static esp_app_desc_t running_app_info;
	uint32_t firmware_ver_minor = 0, firmware_ver_major = 0;

    if (esp_ota_get_partition_description(running, &running_app_info) != ESP_OK)
    {
    	running_app_info.version[0] = '1';
        ESP_LOGE(TAG, "Error getting partition_description");

    }


	if (sscanf(running_app_info.version, "v%ld.%ld", &firmware_ver_major, &firmware_ver_minor) == 2) 
	{
		ESP_LOGI(TAG, "Firmware version: %ld.%ld", firmware_ver_major, firmware_ver_minor);
	} 

    sprintf(fver, "%ld.%02ld", firmware_ver_major, firmware_ver_minor);
    sprintf(hver, "WiCAN-%s", HARDWARE_VERSION);

	cJSON_AddStringToObject(root, "wifi_mode", device_config.wifi_mode);
	cJSON_AddStringToObject(root, "ap_ch", device_config.ap_ch);
	cJSON_AddStringToObject(root, "ap_auto_disable", device_config.ap_auto_disable);
	cJSON_AddStringToObject(root, "sta_ssid", device_config.sta_ssid);
	cJSON_AddStringToObject(root, "sta_pass", device_config.sta_pass);
	cJSON_AddStringToObject(root, "sta_security", device_config.sta_security);
	cJSON_AddStringToObject(root, "sta_status", (wifi_network_is_connected()?"Connected":"Not Connected"));
	cJSON_AddStringToObject(root, "sta_ip", ip_str);
	cJSON_AddStringToObject(root, "mdns", wc_mdns_get_hostname());
	cJSON_AddStringToObject(root, "ble_status", device_config.ble_status);
//	cJSON_AddStringToObject(root, "can_datarate", device_config.can_datarate);
	cJSON_AddStringToObject(root, "can_datarate", can_datarate_str[can_get_bitrate()]);
	cJSON_AddStringToObject(root, "can_mode", device_config.can_mode);
	cJSON_AddStringToObject(root, "port_type", device_config.port_type);
	cJSON_AddStringToObject(root, "port", device_config.port);
	cJSON_AddStringToObject(root, "fw_version", fver);
	cJSON_AddStringToObject(root, "hw_version", hver);
	cJSON_AddStringToObject(root, "git_version", GIT_SHA);
	cJSON_AddStringToObject(root, "protocol", device_config.protocol);
	cJSON_AddStringToObject(root, "sleep_status", device_config.sleep_status);
	cJSON_AddStringToObject(root, "sleep_volt", device_config.sleep_volt);
	cJSON_AddStringToObject(root, "wakeup_volt", device_config.wakeup_volt);
	cJSON_AddStringToObject(root, "periodic_wakeup", device_config.periodic_wakeup);
	cJSON_AddStringToObject(root, "wakeup_interval", device_config.wakeup_interval);

	cJSON_AddStringToObject(root, "batt_alert", device_config.batt_alert);
	cJSON_AddStringToObject(root, "batt_alert_ssid", device_config.batt_alert_ssid);
	cJSON_AddStringToObject(root, "batt_alert_pass", device_config.batt_alert_pass);
	cJSON_AddStringToObject(root, "batt_alert_volt", device_config.batt_alert_volt);
	cJSON_AddStringToObject(root, "batt_alert_protocol", device_config.batt_alert_protocol);
	cJSON_AddStringToObject(root, "batt_alert_url", device_config.batt_alert_url);
	cJSON_AddStringToObject(root, "batt_alert_port", device_config.batt_alert_port);
	cJSON_AddStringToObject(root, "batt_alert_topic", device_config.batt_alert_topic);
	cJSON_AddStringToObject(root, "batt_alert_time", device_config.batt_alert_time);
	cJSON_AddStringToObject(root, "batt_mqtt_user", device_config.batt_mqtt_user);
	cJSON_AddStringToObject(root, "batt_mqtt_pass", device_config.batt_mqtt_pass);
	cJSON_AddStringToObject(root, "logger_status", device_config.logger_status);
	cJSON_AddStringToObject(root, "log_filesystem", device_config.log_filesystem);
	cJSON_AddStringToObject(root, "log_period", device_config.log_period);
	cJSON_AddStringToObject(root, "log_storage", device_config.log_storage);

	char volt[8]= {0};
	float tmp = 0;
	sleep_mode_get_voltage(&tmp);
	sprintf(volt, "%.1fV", tmp);
	cJSON_AddStringToObject(root, "batt_voltage", volt);

	cJSON_AddStringToObject(root, "mqtt_en", device_config.mqtt_en);
	cJSON_AddStringToObject(root, "mqtt_url", device_config.mqtt_url);
	cJSON_AddStringToObject(root, "mqtt_port", device_config.mqtt_port);
	cJSON_AddStringToObject(root, "mqtt_user", device_config.mqtt_user);
	cJSON_AddStringToObject(root, "mqtt_pass", device_config.mqtt_pass);
	cJSON_AddStringToObject(root, "mqtt_tx_topic", device_config.mqtt_tx_topic);
	cJSON_AddStringToObject(root, "mqtt_rx_topic", device_config.mqtt_rx_topic);
	cJSON_AddStringToObject(root, "mqtt_status_topic", device_config.mqtt_status_topic);
	cJSON_AddStringToObject(root, "device_id", device_id);
	cJSON_AddStringToObject(root, "sta_security", device_config.sta_security);

	if(autopid_get_ecu_status())
	{
		cJSON_AddStringToObject(root, "ecu_status", "online");
	}
	else
	{
		cJSON_AddStringToObject(root, "ecu_status", "offline");
	}
    const char *resp_str = cJSON_PrintUnformatted(root);

	httpd_resp_set_type(req, "application/json");
	httpd_resp_send(req, resp_str, HTTPD_RESP_USE_STRLEN);

    free((void *)resp_str);
    cJSON_Delete(root);
    return ESP_OK;
}

/*
 * Structure holding server handle
 * and internal socket fd in order
 * to use out of request send
 */
typedef  struct _async_resp_arg {
    httpd_handle_t hd;
    int fd;
}async_resp_arg_t;
static async_resp_arg_t rsp_arg;
/*
 * async send function, which we put into the httpd work queue
 */
//static void ws_async_send(void *arg)
//{
//    static const char * data = "Async data";
//    struct async_resp_arg *resp_arg = arg;
//    httpd_handle_t hd = resp_arg->hd;
//    int fd = resp_arg->fd;
//    httpd_ws_frame_t ws_pkt;
//    memset(&ws_pkt, 0, sizeof(httpd_ws_frame_t));
//    ws_pkt.payload = (uint8_t*)data;
//    ws_pkt.len = strlen(data);
//    ws_pkt.type = HTTPD_WS_TYPE_TEXT;
//
//    httpd_ws_send_frame_async(hd, fd, &ws_pkt);
//    free(resp_arg);
//}
//
//static esp_err_t trigger_async_send(httpd_handle_t handle, httpd_req_t *req)
//{
//    struct async_resp_arg *resp_arg = malloc(sizeof(struct async_resp_arg));
//    resp_arg->hd = req->handle;
//    resp_arg->fd = httpd_req_to_sockfd(req);
//    return httpd_queue_work(handle, ws_async_send, resp_arg);
//}

//static void ws_send(async_resp_arg_t resp, httpd_ws_frame_t *ws_pkt)
//{
//	httpd_ws_send_frame_async(resp.hd, resp.fd, ws_pkt);
//}

/*
 * This handler echos back the received ws data
 * and triggers an async send if certain message received
 */
static esp_err_t ws_handler(httpd_req_t *req)
{
    if (req->method == HTTP_GET)
    {
//        ESP_LOGI(TAG, "Handshake done, the new connection was opened");
        rsp_arg.hd = req->handle;
        rsp_arg.fd = httpd_req_to_sockfd(req);
//        tcp_server_suspend();
//        vTaskResume(xwebsocket_handle);
        gpio_set_level(ws_led, 0);
        xEventGroupSetBits( xServerEventGroup, WS_CONNECTED_BIT );
        return ESP_OK;
    }

    httpd_ws_frame_t ws_pkt;

    uint8_t *buf = NULL;
    memset(&ws_pkt, 0, sizeof(httpd_ws_frame_t));
    ws_pkt.type = HTTPD_WS_TYPE_TEXT;

    /* Set max_len = 0 to get the frame len */
    esp_err_t ret = httpd_ws_recv_frame(req, &ws_pkt, 0);
    if (ret != ESP_OK)
    {
        ESP_LOGE(TAG, "httpd_ws_recv_frame failed to get frame len with %d", ret);
        return ret;
    }
//    ESP_LOGI(TAG, "frame len is %d", ws_pkt.len);

    if (ws_pkt.len)
    {
        /* ws_pkt.len + 1 is for NULL termination as we are expecting a string */
        buf = calloc(1, ws_pkt.len + 1);
        if (buf == NULL)
        {
            ESP_LOGE(TAG, "Failed to calloc memory for buf");
            return ESP_ERR_NO_MEM;
        }
        ws_pkt.payload = buf;
        /* Set max_len = ws_pkt.len to get the frame payload */
        ret = httpd_ws_recv_frame(req, &ws_pkt, ws_pkt.len);
        if (ret != ESP_OK)
        {
            ESP_LOGE(TAG, "httpd_ws_recv_frame failed with %d", ret);
            free(buf);
            return ret;
        }
//        ESP_LOGI(TAG, "Got packet with message: %s", ws_pkt.payload);
    }
//    ESP_LOGI(TAG, "Packet type: %d", ws_pkt.type);

    static xdev_buffer rx_buffer;
    memcpy(rx_buffer.ucElement, ws_pkt.payload, ws_pkt.len);
    rx_buffer.dev_channel = DEV_WIFI_WS;
    rx_buffer.usLen = ws_pkt.len;

    xQueueSend( *xRX_Queue, ( void * ) &rx_buffer, portMAX_DELAY );
//    ws_send(rsp_arg, &ws_pkt);
//    if (ws_pkt.type == HTTPD_WS_TYPE_TEXT &&
//        strcmp((char*)ws_pkt.payload,"Trigger async") == 0)
//    {
//        free(buf);
//        return trigger_async_send(req->handle, req);
//    }
//
//    ret = httpd_ws_send_frame(req, &ws_pkt);
//    if (ret != ESP_OK)
//    {
//        ESP_LOGE(TAG, "httpd_ws_send_frame failed with %d", ret);
//    }
    free(buf);
    return ret;
}

/* Handler to upload a file onto the server */
static esp_err_t upload_post_handler(httpd_req_t *req)
{
    char filepath[FILE_PATH_MAX];
    uint32_t total_size = 0;

    if(config_server_get_ble_config())
    {
    	ble_disable();
    }
    can_disable();
    /* Skip leading "/upload" from URI to get filename */
    /* Note sizeof() counts NULL termination hence the -1 */
    const char *filename = get_path_from_uri(filepath, ((struct file_server_data *)req->user_ctx)->base_path,
                                             req->uri + sizeof("/upload") - 1, sizeof(filepath));
    if (!filename) {
        /* Respond with 500 Internal Server Error */
        httpd_resp_send_err(req, HTTPD_500_INTERNAL_SERVER_ERROR, "Filename too long");
        return ESP_FAIL;
    }

    /* Filename cannot have a trailing '/' */
    if (filename[strlen(filename) - 1] == '/') {
        ESP_LOGE(TAG, "Invalid filename : %s", filename);
        httpd_resp_send_err(req, HTTPD_500_INTERNAL_SERVER_ERROR, "Invalid filename");
        return ESP_FAIL;
    }

    // /* File cannot be larger than a limit */
    // if (req->content_len > MAX_FILE_SIZE)
    // {
    //     ESP_LOGE(TAG, "File too large : %d bytes", req->content_len);
    //     /* Respond with 400 Bad Request */
    //     httpd_resp_send_err(req, HTTPD_400_BAD_REQUEST,
    //                         "File size must be less than "
    //                         MAX_FILE_SIZE_STR "!");
    //     /* Return failure to close underlying connection else the
    //      * incoming file content will keep the socket busy */
    //     return ESP_FAIL;
    // }

    ESP_LOGI(TAG, "Receiving file : %s...", filename);

    /* Retrieve the pointer to scratch buffer for temporary storage */
    char *buf = ((struct file_server_data *)req->user_ctx)->scratch;
    int received = 0;

    /* Content length of the request gives
     * the size of the file being uploaded */
    int remaining = req->content_len;

    ///
    esp_err_t err;
    /* update handle : set by esp_ota_begin(), must be freed via esp_ota_end() */
    esp_ota_handle_t update_handle = 0 ;
    const esp_partition_t *update_partition = NULL;

    ESP_LOGI(TAG, "Starting OTA");

    const esp_partition_t *configured = esp_ota_get_boot_partition();
    const esp_partition_t *running = esp_ota_get_running_partition();

    if (configured != running) {
        ESP_LOGW(TAG, "Configured OTA boot partition at offset 0x%08lx, but running from offset 0x%08lx",
                 configured->address, running->address);
        ESP_LOGW(TAG, "(This can happen if either the OTA boot data or preferred boot image become corrupted somehow.)");
    }
    ESP_LOGI(TAG, "Running partition type %d subtype %d (offset 0x%08lx)",
             running->type, running->subtype, running->address);

    update_partition = esp_ota_get_next_update_partition(NULL);
    assert(update_partition != NULL);
    ESP_LOGI(TAG, "Writing to partition subtype %d at offset 0x%lx",
             update_partition->subtype, update_partition->address);
    err = esp_ota_begin(update_partition, OTA_SIZE_UNKNOWN, &update_handle);
    if (err != ESP_OK)
    {
        ESP_LOGE(TAG, "esp_ota_begin failed (%s)", esp_err_to_name(err));
        httpd_resp_send_err(req, HTTPD_500_INTERNAL_SERVER_ERROR, "esp_ota_begin failed");
        return ESP_FAIL;
    }

	char *ret = 0;
	char *boundary_start = 0;
	char *boundary_end = 0;
	uint8_t count = 0;

    while (remaining > 0)
    {
//        ESP_LOGI(TAG, "Remaining size : %d", remaining);
        /* Receive the file part by part into a buffer */
        if ((received = httpd_req_recv(req, buf, MIN(remaining, SCRATCH_BUFSIZE))) <= 0)
        {
            if (received == HTTPD_SOCK_ERR_TIMEOUT)
            {
                /* Retry if timeout occurred */
                continue;
            }


            ESP_LOGE(TAG, "File reception failed!");
            /* Respond with 500 Internal Server Error */
            httpd_resp_send_err(req, HTTPD_500_INTERNAL_SERVER_ERROR, "Failed to receive file");
            return ESP_FAIL;
        }

        if(boundary_start == 0)
        {
        	boundary_start = buf;
        	ret = memchr(boundary_start, '\n', 200);
        	boundary_end = ret + 1;
        	remaining -= ((boundary_end - boundary_start) + 1 + 2 + 1);//ignore boundary at end of file
        	//TODO: Better way to do this ??
        	while(1)
        	{
        		if(((ret[0] == 'T') && (ret[1] == 'y') && (ret[2] == 'p') && (ret[3] == 'e') &&
        		        			(ret[4] == ':')))
        		{
        			break;
        		}
        		ret++;
        	}
    		ret = memchr(ret, '\n', 200);
    		buf = ret + 3;
    		remaining -= (buf - boundary_start);
    		ESP_LOGI(TAG, "Real Remaining size : %d", remaining);
    		
    		received -= (buf - boundary_start);
        }
        total_size += received;
        /* Write buffer content to file on storage */
        if (received && (ESP_OK != esp_ota_write( update_handle, (const void *)buf, received)))
        {
            ESP_LOGE(TAG, "File write failed!");
            esp_ota_abort(update_handle);
            /* Respond with 500 Internal Server Error */
            httpd_resp_send_err(req, HTTPD_500_INTERNAL_SERVER_ERROR, "Failed to write file to storage");
            return ESP_FAIL;
        }

        if(count < 3)
        {
        	count++;
        }
        /* Keep track of remaining size of
         * the file left to be uploaded */
        remaining -= received;
        vTaskDelay(5 / portTICK_PERIOD_MS);
    }

    /* Close file upon upload completion */
    ESP_LOGI(TAG, "File reception complete: %lu", total_size);

    if ((received = httpd_req_recv(req, buf, SCRATCH_BUFSIZE)) <= 0)
    {
        ESP_LOGE(TAG, "File reception failed!");
        esp_ota_abort(update_handle);
        /* Respond with 500 Internal Server Error */
        httpd_resp_send_err(req, HTTPD_500_INTERNAL_SERVER_ERROR, "Failed to receive file");
        return ESP_FAIL;
     }

    err = esp_ota_end(update_handle);
    if (err != ESP_OK)
    {
        if (err == ESP_ERR_OTA_VALIDATE_FAILED)
        {
            ESP_LOGE(TAG, "Image validation failed, image is corrupted");
        }
        ESP_LOGE(TAG, "esp_ota_end failed (%s)!", esp_err_to_name(err));
        httpd_resp_send_err(req, HTTPD_500_INTERNAL_SERVER_ERROR, "esp_ota_end failed");
        return ESP_FAIL;
    }

    err = esp_ota_set_boot_partition(update_partition);
    if (err != ESP_OK)
    {
        ESP_LOGE(TAG, "esp_ota_set_boot_partition failed (%s)!", esp_err_to_name(err));
        httpd_resp_send_err(req, HTTPD_500_INTERNAL_SERVER_ERROR, "esp_ota_set_boot_partition failed");
        return ESP_FAIL;
    }

	config_server_reboot();
    // xTimerStart( xrestartTimer, 0 );

    /* Redirect onto root to see the updated file list */
    httpd_resp_set_status(req, "303 See Other");
    httpd_resp_set_hdr(req, "Location", "/");
#ifdef CONFIG_EXAMPLE_HTTPD_CONN_CLOSE_HEADER
    httpd_resp_set_hdr(req, "Connection", "close");
#endif
    httpd_resp_sendstr(req, "File uploaded successfully");
    return ESP_OK;
}

static esp_err_t upload_car_data_handler(httpd_req_t *req)
{
    char filepath[FILE_PATH_MAX];
    uint32_t total_size = 0;

    /* Skip leading "/upload" from URI to get filename */
    /* Note sizeof() counts NULL termination hence the -1 */
    const char *filename = get_path_from_uri(filepath, ((struct file_server_data *)req->user_ctx)->base_path,
                                             req->uri + sizeof("/upload") - 1, sizeof(filepath));
    if (!filename) {
        /* Respond with 500 Internal Server Error */
        httpd_resp_send_err(req, HTTPD_500_INTERNAL_SERVER_ERROR, "Filename too long");
        return ESP_FAIL;
    }

    /* Filename cannot have a trailing '/' */
    if (filename[strlen(filename) - 1] == '/') {
        ESP_LOGE(TAG, "Invalid filename : %s", filename);
        httpd_resp_send_err(req, HTTPD_500_INTERNAL_SERVER_ERROR, "Invalid filename");
        return ESP_FAIL;
    }

    /* File cannot be larger than a limit */
    if (req->content_len > MAX_FILE_SIZE)
    {
        ESP_LOGE(TAG, "File too large : %d bytes", req->content_len);
        /* Respond with 400 Bad Request */
        httpd_resp_send_err(req, HTTPD_400_BAD_REQUEST,
                            "File size must be less than "
                            MAX_FILE_SIZE_STR "!");
        /* Return failure to close underlying connection else the
         * incoming file content will keep the socket busy */
        return ESP_FAIL;
    }

    ESP_LOGI(TAG, "Receiving file : %s...", filename);

    /* Retrieve the pointer to scratch buffer for temporary storage */
    char *buf = ((struct file_server_data *)req->user_ctx)->scratch;
    int received = 0;

    /* Content length of the request gives
     * the size of the file being uploaded */
    int remaining = req->content_len;
	char *ret = 0;
	char *boundary_start = 0;
	char *boundary_end = 0;
	uint8_t count = 0;


	FILE *fd = fopen(filepath, "w");
    if (fd == NULL)
    {
        ESP_LOGE(TAG, "Failed to create file : %s", filepath);
        httpd_resp_send_err(req, HTTPD_500_INTERNAL_SERVER_ERROR, "Failed to create file");
        return ESP_FAIL;
    }
	// remaining
    while (remaining > 0)
    {
//        ESP_LOGI(TAG, "Remaining size : %d", remaining);
        /* Receive the file part by part into a buffer */
        if ((received = httpd_req_recv(req, buf, MIN(remaining, SCRATCH_BUFSIZE))) <= 0)
        {
            if (received == HTTPD_SOCK_ERR_TIMEOUT)
            {
                /* Retry if timeout occurred */
                continue;
            }


            ESP_LOGE(TAG, "File reception failed!");
            fclose(fd);
            unlink(filepath);
            httpd_resp_send_err(req, HTTPD_500_INTERNAL_SERVER_ERROR, "Failed to receive file");
            return ESP_FAIL;
        }

        if(boundary_start == 0)
        {
        	boundary_start = buf;
        	ret = memchr(boundary_start, '\n', 200);
        	boundary_end = ret + 1;
        	remaining -= ((boundary_end - boundary_start) + 1 + 2 + 1);//ignore boundary at end of file
        	//TODO: Better way to do this ??
        	while(1)
        	{
        		if(((ret[0] == 'T') && (ret[1] == 'y') && (ret[2] == 'p') && (ret[3] == 'e') &&
        		        			(ret[4] == ':')))
        		{
        			break;
        		}
        		ret++;
        	}
    		ret = memchr(ret, '\n', 200);
    		buf = ret + 3;
    		remaining -= (buf - boundary_start);
    		ESP_LOGI(TAG, "Real Remaining size : %d", remaining);
    		
    		received -= (buf - boundary_start);
        }
        total_size += received;
        /* Write buffer content to file on storage */
        if (received > 0)
        {
            if (fwrite(buf, 1, received, fd) != received)
            {
                ESP_LOGE(TAG, "File write failed!");
                fclose(fd);
                unlink(filepath);
                httpd_resp_send_err(req, HTTPD_500_INTERNAL_SERVER_ERROR, "Failed to write file to storage");
                return ESP_FAIL;
            }
        }

        if(count < 3)
        {
        	count++;
        }
        /* Keep track of remaining size of
         * the file left to be uploaded */
        remaining -= received;
        vTaskDelay(5 / portTICK_PERIOD_MS);
    }


    fclose(fd);

    ESP_LOGI(TAG, "File reception complete: %lu", total_size);
    httpd_resp_sendstr(req, "File uploaded successfully");
    return ESP_OK;
}

esp_err_t autopid_data_handler(httpd_req_t *req)
{
    char *data = autopid_data_read();
    
    if (data == NULL)
    {
        ESP_LOGE(TAG, "No data available");
        const char *response = "{\"error\":\"No data available\"}";
        httpd_resp_set_type(req, "application/json");
        httpd_resp_send(req, response, strlen(response));
        return ESP_OK;
    }

    httpd_resp_set_type(req, "application/json");

    httpd_resp_send(req, data, strlen(data));

    free(data);

    return ESP_OK;
}

#define MAX_AVAILABLE_PIDS_SIZE 		(1024*8)
static esp_err_t scan_available_pids_handler(httpd_req_t *req)
{
    char protocol[8];
    char param[32];
    uint8_t protocol_num = 6; // Default protocol

    if(config_server_protocol() != AUTO_PID)
    {
        httpd_resp_set_type(req, "application/json");
        const char *resp_str = "{\"text\":\"Set protocol to AutoPid and reboot to be able to scan\"}";
        httpd_resp_send(req, resp_str, strlen(resp_str));
        return ESP_OK;
    }
	
    if (httpd_req_get_url_query_str(req, param, sizeof(param)) == ESP_OK) {
        if (httpd_query_key_value(param, "protocol", protocol, sizeof(protocol)) == ESP_OK) {
            protocol_num = atoi(protocol);
            ESP_LOGI(TAG, "Scanning PIDs with protocol: %d", protocol_num);
        }
    }

    char *available_pids = malloc(MAX_AVAILABLE_PIDS_SIZE);
    if (available_pids == NULL) {
        httpd_resp_send_500(req);
        return ESP_FAIL;
    }
    memset(available_pids, 0, MAX_AVAILABLE_PIDS_SIZE);
    
    if (autopid_find_standard_pid(protocol_num, available_pids, MAX_AVAILABLE_PIDS_SIZE) == ESP_OK) {
        httpd_resp_set_type(req, "application/json");
        httpd_resp_send(req, available_pids, strlen(available_pids));
    }

    free(available_pids);
    return ESP_OK;
}

static const httpd_uri_t index_uri = {
    .uri       = "/",
    .method    = HTTP_GET,
    .handler   = index_handler,
    /* Let's pass response string in user
     * context to demonstrate it's usage */
    .user_ctx  = NULL
};
static const httpd_uri_t store_config_uri = {
    .uri       = "/store_config",
    .method    = HTTP_POST,
    .handler   = store_config_handler,
    /* Let's pass response string in user
     * context to demonstrate it's usage */
    .user_ctx  = NULL
};
static const httpd_uri_t store_canflt_uri = {
    .uri       = "/store_canflt",
    .method    = HTTP_POST,
    .handler   = store_canflt_handler,
    /* Let's pass response string in user
     * context to demonstrate it's usage */
    .user_ctx  = NULL
};
static const httpd_uri_t load_canflt_uri = {
    .uri       = "/load_canflt",
    .method    = HTTP_GET,
    .handler   = load_canflt_handler,
    /* Let's pass response string in user
     * context to demonstrate it's usage */
    .user_ctx  = NULL
};
static const httpd_uri_t load_pid_auto_uri = {
    .uri       = "/load_auto_pid",
    .method    = HTTP_GET,
    .handler   = load_pid_auto_handler,
    /* Let's pass response string in user
     * context to demonstrate it's usage */
    .user_ctx  = NULL
};
static const httpd_uri_t load_pid_auto_conf_uri = {
    .uri       = "/load_auto_pid_car_data",
    .method    = HTTP_GET,
    .handler   = load_pid_auto_config_handler,
    /* Let's pass response string in user
     * context to demonstrate it's usage */
    .user_ctx  = NULL
};
static const httpd_uri_t load_car_config_uri = {
    .uri       = "/load_car_config",
    .method    = HTTP_GET,
    .handler   = load_car_config_handler,
    /* Let's pass response string in user
     * context to demonstrate it's usage */
    .user_ctx  = NULL
};
static const httpd_uri_t check_status_uri = {
    .uri       = "/check_status",
    .method    = HTTP_GET,
    .handler   = check_status_handler,
    /* Let's pass response string in user
     * context to demonstrate it's usage */
    .user_ctx  = NULL
};
static const httpd_uri_t load_config_uri = {
    .uri       = "/load_config",
    .method    = HTTP_GET,
    .handler   = load_config_handler,
    /* Let's pass response string in user
     * context to demonstrate it's usage */
    .user_ctx  = NULL
};
static const httpd_uri_t logo_uri = {
    .uri       = "/logo.svg",
    .method    = HTTP_GET,
    .handler   = logo_handler,
    /* Let's pass response string in user
     * context to demonstrate it's usage */
    .user_ctx  = NULL
};
static const httpd_uri_t ws = {
        .uri        = "/ws",
        .method     = HTTP_GET,
        .handler    = ws_handler,
        .user_ctx   = NULL,
        .is_websocket = true
};
static struct file_server_data server_data = {.base_path = FS_MOUNT_POINT""};
//static struct file_server_data *server_data = NULL;
/* URI handler for uploading files to server */
static const httpd_uri_t file_upload = {
    .uri       = "/upload/ota.bin",   // Match all URIs of type /upload/path/to/file
    .method    = HTTP_POST,
    .handler   = upload_post_handler,
    .user_ctx  = &server_data    // Pass server data as context
};
static const httpd_uri_t system_reboot = {
    .uri       = "/system_reboot",   // Match all URIs of type /upload/path/to/file
    .method    = HTTP_POST,
    .handler   = system_reboot_handler,
    .user_ctx  = NULL    // Pass server data as context
};
static const httpd_uri_t store_auto_data_uri = {
    .uri       = "/store_auto_data",
    .method    = HTTP_POST,
    .handler   = store_auto_data_handler,
    /* Let's pass response string in user
     * context to demonstrate it's usage */
    .user_ctx  = NULL
};
static const httpd_uri_t upload_car_data = {
    .uri       = "/upload/car_data.json",   // Match all URIs of type /upload/path/to/file
    .method    = HTTP_POST,
    .handler   = upload_car_data_handler,
    .user_ctx  = &server_data    // Pass server data as context
};
static const httpd_uri_t autopid_data = {
    .uri       = "/autopid_data",   // Match all URIs of type /upload/path/to/file
    .method    = HTTP_GET,
    .handler   = autopid_data_handler,
    .user_ctx  = &server_data    // Pass server data as context
};
static const httpd_uri_t store_car_data_uri = {
    .uri       = "/store_car_data",
    .method    = HTTP_POST,
    .handler   = store_car_data_handler,
    /* Let's pass response string in user
     * context to demonstrate it's usage */
    .user_ctx  = NULL
};
static const httpd_uri_t system_commands = {
    .uri       = "/system_commands",   // Match all URIs of type /upload/path/to/file
    .method    = HTTP_POST,
    .handler   = system_commands_handler,
    .user_ctx  = NULL    // Pass server data as context
};
static const httpd_uri_t scan_available_pids_uri = {
    .uri       = "/scan_available_pids",
    .method    = HTTP_GET,
    .handler   = scan_available_pids_handler,
    .user_ctx  = NULL
};
static const httpd_uri_t file_uri = {
    .uri       = "/*",
    .method    = HTTP_GET,
    .handler   = file_handler,
    .user_ctx  = &server_data 
};
static void config_server_load_cfg(char *cfg)
{
	cJSON * root, *key = 0;
	root   = cJSON_Parse(cfg);
    struct stat st;

	key = cJSON_GetObjectItem(root,"wifi_mode");
	if(key == 0)
	{
		goto config_error;
	}
	strcpy(device_config.wifi_mode, key->valuestring);
	ESP_LOGI(TAG, "device_config.wifi_mode: %s", device_config.wifi_mode);

	key = cJSON_GetObjectItem(root,"ap_ch");
	if(key == 0)
	{
		goto config_error;
	}
	strcpy(device_config.ap_ch, key->valuestring);
	ESP_LOGI(TAG, "device_config.ap_ch: %s", device_config.ap_ch);

	key = cJSON_GetObjectItem(root,"sta_ssid");
	if(key == 0)
	{
		goto config_error;
	}
	if(strlen(key->valuestring) == 0 || strlen(key->valuestring) > 32)
	{
		goto config_error;
	}
	strcpy(device_config.sta_ssid, key->valuestring);
	ESP_LOGI(TAG, "device_config.sta_ssid: %s", device_config.sta_ssid);

	key = cJSON_GetObjectItem(root,"sta_pass");
	if(key == 0)
	{
		goto config_error;
	}
	if(strlen(key->valuestring) < 8 || strlen(key->valuestring) > 64)
	{
		goto config_error;
	}
	strcpy(device_config.sta_pass, key->valuestring);
	ESP_LOGI(TAG, "device_config.sta_pass: %s", device_config.sta_pass);

	key = cJSON_GetObjectItem(root,"can_datarate");
	if(key == 0)
	{
		goto config_error;
	}
	strcpy(device_config.can_datarate, key->valuestring);
	ESP_LOGI(TAG, "device_config.can_datarate: %s", device_config.can_datarate);

	key = cJSON_GetObjectItem(root,"can_mode");
	if(key == 0)
	{
		goto config_error;
	}
	strcpy(device_config.can_mode, key->valuestring);
	ESP_LOGI(TAG, "device_config.can_mode: %s", device_config.can_mode);

	key = cJSON_GetObjectItem(root,"port_type");
	if(key == 0)
	{
		goto config_error;
	}
	strcpy(device_config.port_type, key->valuestring);
	ESP_LOGI(TAG, "device_config.port_type: %s", device_config.port_type);

	key = cJSON_GetObjectItem(root,"port");
	if(key == 0)
	{
		goto config_error;
	}
	strcpy(device_config.port, key->valuestring);
	ESP_LOGI(TAG, "device_config.port: %s", device_config.port);


	key = cJSON_GetObjectItem(root,"ap_pass");
	if(key == 0)
	{
		goto config_error;
	}
	if(strlen(key->valuestring) < 8 || strlen(key->valuestring) > 64)
	{
		goto config_error;
	}
	strcpy(device_config.ap_pass, key->valuestring);
	ESP_LOGE(TAG, "device_config.ap_pass: %s", device_config.ap_pass);

	key = cJSON_GetObjectItem(root,"protocol");
	if(key == 0)
	{
		goto config_error;
	}
	if(strlen(key->valuestring) < 2 || strlen(key->valuestring) > 64)
	{
		goto config_error;
	}
	strcpy(device_config.protocol, key->valuestring);
	ESP_LOGE(TAG, "device_config.protocol: %s", device_config.protocol);

	key = cJSON_GetObjectItem(root,"ble_pass");
	if(key == 0)
	{
		goto config_error;
	}
	if(strlen(key->valuestring) < 4 || strlen(key->valuestring) > 16)
	{
		goto config_error;
	}
	strcpy(device_config.ble_pass, key->valuestring);
	ESP_LOGE(TAG, "device_config.ble_pass: %s", device_config.ble_pass);

	key = cJSON_GetObjectItem(root,"sleep_status");
	if(key == 0)
	{
		goto config_error;
	}

	strcpy(device_config.sleep_status, key->valuestring);
	ESP_LOGE(TAG, "device_config.sleep_status: %s", device_config.sleep_status);

	key = cJSON_GetObjectItem(root,"ble_status");
	if(key == 0)
	{
		goto config_error;
	}

	strcpy(device_config.ble_status, key->valuestring);
	ESP_LOGE(TAG, "device_config.ble_status: %s", device_config.ble_status);

	key = cJSON_GetObjectItem(root,"sleep_volt");
	if(key == 0)
	{
		goto config_error;
	}

	strcpy(device_config.sleep_volt, key->valuestring);
	ESP_LOGE(TAG, "device_config.sleep_volt: %s", device_config.sleep_volt);

	//*****
	key = cJSON_GetObjectItem(root,"batt_alert");
	if(key == 0 || (strlen(key->valuestring) > sizeof(device_config.batt_alert)))
	{
		goto config_error;
	}

	strcpy(device_config.batt_alert, key->valuestring);
	ESP_LOGE(TAG, "device_config.batt_alert: %s", device_config.batt_alert);
	//*****

	//*****
	key = cJSON_GetObjectItem(root,"batt_alert_ssid");
	if(key == 0 || (strlen(key->valuestring) > sizeof(device_config.batt_alert_ssid)))
	{
		goto config_error;
	}

	strcpy(device_config.batt_alert_ssid, key->valuestring);
	ESP_LOGE(TAG, "device_config.batt_alert_ssid: %s", device_config.batt_alert_ssid);
	//*****

	//*****
	key = cJSON_GetObjectItem(root,"batt_alert_pass");
	if(key == 0 || (strlen(key->valuestring) > sizeof(device_config.batt_alert_pass)))
	{
		goto config_error;
	}

	strcpy(device_config.batt_alert_pass, key->valuestring);
	ESP_LOGE(TAG, "device_config.batt_alert_pass: %s", device_config.batt_alert_pass);
	//*****

	//*****
	key = cJSON_GetObjectItem(root,"batt_alert_volt");
	if(key == 0 || (strlen(key->valuestring) > sizeof(device_config.batt_alert_volt)))
	{
		goto config_error;
	}

	strcpy(device_config.batt_alert_volt, key->valuestring);
	ESP_LOGE(TAG, "device_config.batt_alert_volt: %s", device_config.batt_alert_volt);
	//*****

	//*****
	key = cJSON_GetObjectItem(root,"batt_alert_protocol");
	if(key == 0 || (strlen(key->valuestring) > sizeof(device_config.batt_alert_protocol)))
	{
		goto config_error;
	}

	strcpy(device_config.batt_alert_protocol, key->valuestring);
	ESP_LOGE(TAG, "device_config.batt_alert_protocol: %s", device_config.batt_alert_protocol);
	//*****

	//*****
	key = cJSON_GetObjectItem(root,"batt_alert_url");
	if(key == 0 || (strlen(key->valuestring) > sizeof(device_config.batt_alert_url)))
	{
		goto config_error;
	}

	strcpy(device_config.batt_alert_url, key->valuestring);
	ESP_LOGE(TAG, "device_config.batt_alert_url: %s", device_config.batt_alert_url);
	//*****

	//*****
	key = cJSON_GetObjectItem(root,"batt_alert_port");
	if(key == 0 || (strlen(key->valuestring) > sizeof(device_config.batt_alert_port)))
	{
		goto config_error;
	}

	strcpy(device_config.batt_alert_port, key->valuestring);
	ESP_LOGE(TAG, "device_config.batt_alert_port: %s", device_config.batt_alert_port);
	//*****

	//*****
	key = cJSON_GetObjectItem(root,"batt_alert_topic");
	if(key == 0 || (strlen(key->valuestring) > sizeof(device_config.batt_alert_topic)))
	{
		goto config_error;
	}

	strcpy(device_config.batt_alert_topic, key->valuestring);
	ESP_LOGE(TAG, "device_config.batt_alert_topic: %s", device_config.batt_alert_topic);
	//*****

	//*****
	key = cJSON_GetObjectItem(root,"batt_mqtt_user");
	if(key == 0 || (strlen(key->valuestring) > sizeof(device_config.batt_mqtt_user)))
	{
		goto config_error;
	}

	strcpy(device_config.batt_mqtt_user, key->valuestring);
	ESP_LOGE(TAG, "device_config.batt_mqtt_user: %s", device_config.batt_mqtt_user);
	//*****

	//*****
	key = cJSON_GetObjectItem(root,"batt_mqtt_pass");
	if(key == 0 || (strlen(key->valuestring) > sizeof(device_config.batt_mqtt_pass)))
	{
		goto config_error;
	}

	strcpy(device_config.batt_mqtt_pass, key->valuestring);
	ESP_LOGE(TAG, "device_config.batt_mqtt_pass: %s", device_config.batt_mqtt_pass);
	//*****

	//*****
	key = cJSON_GetObjectItem(root,"batt_alert_time");
	if(key == 0 || (strlen(key->valuestring) > sizeof(device_config.batt_alert_time)))
	{
		goto config_error;
	}

	strcpy(device_config.batt_alert_time, key->valuestring);
	ESP_LOGE(TAG, "device_config.batt_alert_time: %s", device_config.batt_alert_time);
	//*****



	//*****
	key = cJSON_GetObjectItem(root,"mqtt_en");
	if(key == 0 || (strlen(key->valuestring) > sizeof(device_config.mqtt_en)))
	{
		goto config_error;
	}

	strcpy(device_config.mqtt_en, key->valuestring);
	ESP_LOGE(TAG, "device_config.mqtt_en: %s", device_config.mqtt_en);
	//*****

	//*****
	key = cJSON_GetObjectItem(root,"mqtt_url");
	if(key == 0 || (strlen(key->valuestring) > sizeof(device_config.mqtt_url)))
	{
		goto config_error;
	}

	strcpy(device_config.mqtt_url, key->valuestring);
	ESP_LOGE(TAG, "device_config.mqtt_url: %s", device_config.mqtt_url);
	//*****

	//*****
	key = cJSON_GetObjectItem(root,"mqtt_port");
	if(key == 0 || (strlen(key->valuestring) > sizeof(device_config.mqtt_port)))
	{
		goto config_error;
	}

	strcpy(device_config.mqtt_port, key->valuestring);
	ESP_LOGE(TAG, "device_config.mqtt_port: %s", device_config.mqtt_port);
	//*****

	//*****
	key = cJSON_GetObjectItem(root,"mqtt_user");
	if(key == 0 || (strlen(key->valuestring) > sizeof(device_config.mqtt_user)))
	{
		goto config_error;
	}

	strcpy(device_config.mqtt_user, key->valuestring);
	ESP_LOGE(TAG, "device_config.mqtt_user: %s", device_config.mqtt_user);
	//*****

	//*****
	key = cJSON_GetObjectItem(root,"mqtt_pass");
	if(key == 0 || (strlen(key->valuestring) > sizeof(device_config.mqtt_pass)))
	{
		goto config_error;
	}

	strcpy(device_config.mqtt_pass, key->valuestring);
	ESP_LOGE(TAG, "device_config.mqtt_pass: %s", device_config.mqtt_pass);
	//*****

	//*****
	key = cJSON_GetObjectItem(root,"mqtt_elm327_log");
	if(key == 0 || (strlen(key->valuestring) > sizeof(device_config.mqtt_elm327_log)))
	{
		goto config_error;
	}

	strcpy(device_config.mqtt_elm327_log, key->valuestring);
	ESP_LOGE(TAG, "device_config.mqtt_elm327_log: %s", device_config.mqtt_elm327_log);
	//*****

	//*****
	key = cJSON_GetObjectItem(root,"mqtt_tx_topic");
	if(key == 0 || (strlen(key->valuestring) > sizeof(device_config.mqtt_tx_topic)))
	{
		goto config_error;
	}
	if(strlen(key->valuestring) == 0)
	{
		sprintf(device_config.mqtt_tx_topic, "wican/%s/can/tx", device_id);
	}
	else
	{
		strcpy(device_config.mqtt_tx_topic, key->valuestring);
	}
	
	ESP_LOGE(TAG, "device_config.mqtt_tx_topic: %s", device_config.mqtt_tx_topic);
	//*****

	//*****
	key = cJSON_GetObjectItem(root,"mqtt_tx_en");
	if(key == 0 || (strlen(key->valuestring) > sizeof(device_config.mqtt_tx_en)))
	{
		strcpy(device_config.mqtt_tx_en,"disable");
	}
	else
	{
		strcpy(device_config.mqtt_tx_en, key->valuestring);
	}
	
	ESP_LOGE(TAG, "device_config.mqtt_tx_en: %s", device_config.mqtt_tx_en);
	//*****

	//*****
	key = cJSON_GetObjectItem(root,"mqtt_rx_en");
	if(key == 0 || (strlen(key->valuestring) > sizeof(device_config.mqtt_rx_en)))
	{
		strcpy(device_config.mqtt_rx_en,"disable");
	}
	else
	{
		strcpy(device_config.mqtt_rx_en, key->valuestring);
	}

	ESP_LOGE(TAG, "device_config.mqtt_rx_en: %s", device_config.mqtt_rx_en);
	//*****

	//*****

	//*****
	key = cJSON_GetObjectItem(root,"mqtt_rx_topic");
	if(key == 0 || (strlen(key->valuestring) > sizeof(device_config.mqtt_rx_topic)) || strlen(key->valuestring) == 0)
	{
		goto config_error;
	}
	strcpy(device_config.mqtt_rx_topic, key->valuestring);

	
	ESP_LOGE(TAG, "device_config.mqtt_rx_topic: %s", device_config.mqtt_rx_topic);
	//*****

	//*****
	key = cJSON_GetObjectItem(root,"mqtt_status_topic");
	if(key == 0 || (strlen(key->valuestring) > sizeof(device_config.mqtt_status_topic)) || strlen(key->valuestring) == 0)
	{
		goto config_error;
	}
	strcpy(device_config.mqtt_status_topic, key->valuestring);

	
	ESP_LOGE(TAG, "device_config.mqtt_status_topic: %s", device_config.mqtt_status_topic);
	//*****

	//*****
	key = cJSON_GetObjectItem(root,"wakeup_volt");
	if(key == 0)
	{
		strcpy(device_config.wakeup_volt, "13.5");
	}
	else
	{
		strcpy(device_config.wakeup_volt, key->valuestring);
	}

	ESP_LOGE(TAG, "device_config.wakeup_volt: %s", device_config.wakeup_volt);
	//*****
	
	//*****
	key = cJSON_GetObjectItem(root,"sleep_time");
	if(key == 0)
	{
		strcpy(device_config.sleep_time, "2");
	}
	else
	{
		uint32_t sleep_time = atoi(device_config.sleep_time);

		if(sleep_time > 30 && sleep_time < 1)
		{
			strcpy(device_config.sleep_time, "2");
		}

		strcpy(device_config.sleep_time, key->valuestring);
	}

	ESP_LOGE(TAG, "device_config.sleep_time: %s", device_config.sleep_time);
	//*****

	//*****
	key = cJSON_GetObjectItem(root,"sta_security");
	if(key == 0)
	{
		strcpy(device_config.sta_security, "wpa3");
	}
	else
	{
		strcpy(device_config.sta_security, key->valuestring);
	}

	ESP_LOGE(TAG, "device_config.sta_security: %s", device_config.wakeup_volt);
	//*****

	//*****
	key = cJSON_GetObjectItem(root,"logger_status");
	if(key == 0)
	{
		strcpy(device_config.logger_status, "disable");
	}
	else
	{
		strcpy(device_config.logger_status, key->valuestring);
	}
	ESP_LOGE(TAG, "device_config.logger_status: %s", device_config.logger_status);
	//*****

	//*****
	key = cJSON_GetObjectItem(root,"log_filesystem");
	if(key == 0)
	{
		strcpy(device_config.logger_status, "littlefs");
	}
	else
	{
		strcpy(device_config.log_filesystem, key->valuestring);
	}
	ESP_LOGE(TAG, "device_config.log_filesystem: %s", device_config.log_filesystem);
	//*****

	//*****
	key = cJSON_GetObjectItem(root,"log_storage");

	if(key == 0)
	{
		strcpy(device_config.sta_security, "sdcard");
	}
	else
	{
		strcpy(device_config.sta_security, key->valuestring);
	}

	ESP_LOGE(TAG, "device_config.log_storage: %s", device_config.log_storage);
	//*****

	//*****
	key = cJSON_GetObjectItem(root,"log_period");
	if(key == 0)
	{
		strcpy(device_config.log_period, "10");
	}
	else
	{
		uint32_t log_period = atoi(device_config.log_period);

		if(log_period > 300 && log_period < 1)
		{
			strcpy(device_config.log_period, "10");
		}

		strcpy(device_config.log_period, key->valuestring);
	}
	ESP_LOGE(TAG, "device_config.log_period: %s", device_config.log_period);
	//*****

	key = cJSON_GetObjectItem(root,"ap_auto_disable");
	if(key == 0)
	{
		strcpy(device_config.ap_auto_disable, "disable");
	}
	else
	{
		strcpy(device_config.ap_auto_disable, key->valuestring);
	}

	ESP_LOGE(TAG, "device_config.ap_auto_disable: %s", device_config.ap_auto_disable);

	//*****
	key = cJSON_GetObjectItem(root,"periodic_wakeup");
	if(key == 0)
	{
		strcpy(device_config.periodic_wakeup, "disable");
	}
	else
	{
		strcpy(device_config.periodic_wakeup, key->valuestring);
	}

	ESP_LOGE(TAG, "device_config.periodic_wakeup: %s", device_config.periodic_wakeup);
	//*****	

	//*****	
	key = cJSON_GetObjectItem(root,"wakeup_interval");
	if(key == 0)
	{
		strcpy(device_config.wakeup_interval, "5");
	}
	else
	{
		strcpy(device_config.wakeup_interval, key->valuestring);
	}

	ESP_LOGE(TAG, "device_config.wakeup_interval: %s", device_config.wakeup_interval);
	//*****	

	cJSON_Delete(root);
	return;


config_error:
    // Check if destination file exists before renaming

    if (stat(FS_MOUNT_POINT"/config.json", &st) == 0)
    {
    	ESP_LOGE(TAG, "config.json file error, restoring default");
        // Delete it if it exists
        unlink(FS_MOUNT_POINT"/config.json");
		FILE* f = fopen(FS_MOUNT_POINT"/config.json", "w");
		// sprintf(device_config_default, device_id, device_id);
		fprintf(f, device_config_default, (char*)device_id, (char*)device_id, (char*)device_id);
		fclose(f);
		vTaskDelay(3000 / portTICK_PERIOD_MS);
		esp_restart();
    }
	cJSON_Delete(root);
}

void config_server_wifi_connected(bool flag)
{
	if(flag)
	{
		xEventGroupSetBits( xServerEventGroup, WIFI_CONNECTED_BIT );
	}
	else
	{
		xEventGroupClearBits( xServerEventGroup, WIFI_CONNECTED_BIT );
	}
}
//
//bool config_server_get_wifi_connected(void)
//{
//	EventBits_t uxBits;
//	if(xServerEventGroup != NULL)
//	{
//		uxBits = xEventGroupGetBits(xServerEventGroup);
//
//		return (uxBits & WIFI_CONNECTED_BIT)?1:0;
//	}
//	else return 0;
//}

void config_server_set_sta_ip(char* ip)
{
	xQueueOverwrite(xip_Queue, ip);
}
void config_server_get_sta_ip(char* ip)
{
	xQueuePeek(xip_Queue, ip, 0);
}

void vrestartTimerCallback( TimerHandle_t xTimer )
{
//	vTaskDelay(1000 / portTICK_PERIOD_MS);
	esp_restart();
}


//static char* device_config = NULL;
static uint8_t esp_fatfs_flag = 0;
static httpd_config_t config = HTTPD_DEFAULT_CONFIG();
static httpd_handle_t config_server_init(void)
{
//	const char* base_path = "/"; //useless?
//
//    if (server_data)
//    {
//        ESP_LOGE(TAG, "File server already started");
//        return ESP_ERR_INVALID_STATE;
//    }
//
//    /* Allocate memory for server data */
//    server_data = calloc(1, sizeof(struct file_server_data));
//    if (!server_data)
//    {
//        ESP_LOGE(TAG, "Failed to allocate memory for server data");
//        return ESP_ERR_NO_MEM;
//    }
//
//    strlcpy(server_data->base_path, base_path,
//            sizeof(server_data->base_path));

    config.lru_purge_enable = true;
	config.uri_match_fn = httpd_uri_match_wildcard;
    if(xServerEventGroup == NULL)
    {
		server_data.scratch = malloc(SCRATCH_BUFSIZE);
    	xServerEventGroup = xEventGroupCreate();
    	config_server_wifi_connected(0);
    }

    if(xip_Queue == NULL)
    {
    	xip_Queue = xQueueCreate(1, 20);
    }

	

	if(esp_fatfs_flag == 0) 
	{
		#ifdef USE_FATFS
		static wl_handle_t s_wl_handle = WL_INVALID_HANDLE;
		ESP_LOGI(TAG, "Initializing FAT filesystem");

		const esp_vfs_fat_mount_config_t mount_config = {
			.max_files = 4,
			.format_if_mount_failed = true,
			.allocation_unit_size = CONFIG_WL_SECTOR_SIZE,
			.use_one_fat = false,
		};

		esp_err_t ret = esp_vfs_fat_spiflash_mount_rw_wl(FS_MOUNT_POINT"", "storage", &mount_config, &s_wl_handle);
		if (ret != ESP_OK) 
		{
			ESP_LOGE(TAG, "Failed to mount FATFS (%s)", esp_err_to_name(ret));
			return NULL;
		}
		
		ESP_LOGI(TAG, "FAT filesystem mounted successfully");
		#else
			ESP_LOGI(TAG, "Initializing LittleFS filesystem");
			
			esp_vfs_littlefs_conf_t conf = {
				.base_path = FS_MOUNT_POINT,
				.partition_label = "storage",
				.format_if_mount_failed = true,
				.dont_mount = false,
			};
			
			esp_err_t ret = esp_vfs_littlefs_register(&conf);
			if (ret != ESP_OK) 
			{
				ESP_LOGE(TAG, "Failed to mount LittleFS (%s)", esp_err_to_name(ret));
				return NULL;
			}
			
			ESP_LOGI(TAG, "LittleFS filesystem mounted successfully");

			size_t total = 0, used = 0;
			ret = esp_littlefs_info(conf.partition_label, &total, &used);
			if (ret != ESP_OK)
			{
					ESP_LOGE(TAG, "Failed to get LittleFS partition information (%s)", esp_err_to_name(ret));
			}
			else
			{
					ESP_LOGI(TAG, "Partition size: total: %d, used: %d", total, used);
			}

		#endif
		// Handle config.json
		FILE* f = fopen(FS_MOUNT_POINT"/config.json", "r");
		if (f == NULL)
		{
			ESP_LOGI(TAG, "Config file does not exist, loading default");
			f = fopen(FS_MOUNT_POINT"/config.json", "w");
			if (f != NULL)
			{
				fprintf(f, device_config_default, (char*)device_id, (char*)device_id, (char*)device_id);
				fclose(f);
				f = fopen(FS_MOUNT_POINT"/config.json", "r");
				ESP_LOGW(TAG, "Config file trying to load again");
			}
		}

		if (f != NULL)
		{
			fseek(f, 0, SEEK_END);
			long filesize = ftell(f);
			fseek(f, 0, SEEK_SET);

			device_config_file = heap_caps_malloc(filesize + 1, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
			if (device_config_file != NULL)
			{
				fread(device_config_file, sizeof(char), filesize, f);
				device_config_file[filesize] = 0;
				ESP_LOGI(TAG, "config.json: %s", device_config_file);
				fclose(f);	//close file after reading, config_server_load_cfg might unlink it
				config_server_load_cfg(device_config_file);
			}
			else
			{
				ESP_LOGE(TAG, "Failed to allocate memory for config file");
				fclose(f);
			}
			
		}

		// Handle mqtt_canfilt.json
		f = fopen(FS_MOUNT_POINT"/mqtt_canfilt.json", "r");
		if (f != NULL)
		{
			fseek(f, 0, SEEK_END);
			long filesize = ftell(f);
			fseek(f, 0, SEEK_SET);

			mqtt_canflt_file = heap_caps_malloc(filesize + 1, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
			if (mqtt_canflt_file != NULL)
			{
				fread(mqtt_canflt_file, sizeof(char), filesize, f);
				mqtt_canflt_file[filesize] = 0;
				ESP_LOGI(TAG, "mqtt_canfilt.json: %s", mqtt_canflt_file);
			}
			fclose(f);
		}
	}

    xrestartTimer= xTimerCreate
                       ( /* Just a text name, not used by the RTOS
                         kernel. */
                         "Timer",
                         /* The timer period in ticks, must be
                         greater than 0. */
						 (2000 / portTICK_PERIOD_MS),
                         /* The timers will auto-reload themselves
                         when they expire. */
                         pdTRUE,
                         /* The ID is used to store a count of the
                         number of times the timer has expired, which
                         is initialised to 0. */
                         ( void * ) 0,
                         /* Each timer calls the same callback when
                         it expires. */
                         vrestartTimerCallback
                       );

    // Start the httpd server
	config.max_uri_handlers = 23;
	config.stack_size = (10*1024);
	config.max_open_sockets = 15;
    ESP_LOGI(TAG, "Starting server on port: '%d'", config.server_port);
    if (httpd_start(&server, &config) == ESP_OK)
    {
        // Set URI handlers
        ESP_LOGI(TAG, "Registering URI handlers");
        httpd_register_uri_handler(server, &index_uri);
        httpd_register_uri_handler(server, &store_config_uri);
        httpd_register_uri_handler(server, &check_status_uri);
        httpd_register_uri_handler(server, &load_config_uri);
        httpd_register_uri_handler(server, &logo_uri);
        httpd_register_uri_handler(server, &ws);
        httpd_register_uri_handler(server, &file_upload);
		httpd_register_uri_handler(server, &system_reboot);
		httpd_register_uri_handler(server, &store_canflt_uri);
		httpd_register_uri_handler(server, &load_canflt_uri);
		httpd_register_uri_handler(server, &store_auto_data_uri);
		httpd_register_uri_handler(server, &load_pid_auto_uri);
		httpd_register_uri_handler(server, &load_pid_auto_conf_uri);
		httpd_register_uri_handler(server, &upload_car_data);
		httpd_register_uri_handler(server, &autopid_data);
		httpd_register_uri_handler(server, &load_car_config_uri);
		httpd_register_uri_handler(server, &store_car_data_uri);
		httpd_register_uri_handler(server, &system_commands);
		httpd_register_uri_handler(server, &scan_available_pids_uri);
		httpd_register_uri_handler(server, &obd_logger_ws);
		httpd_register_uri_handler(server, &db_download_uri);
		httpd_register_uri_handler(server, &db_files_uri);

		//last uri /*
		httpd_register_uri_handler(server, &file_uri);
		
        #if CONFIG_EXAMPLE_BASIC_AUTH
        httpd_register_basic_auth(server);
        #endif
        return server;
    }

    ESP_LOGI(TAG, "Error starting server!");
    return NULL;
}
void config_server_restart(void)
{
    // Start the httpd server
    ESP_LOGI(TAG, "Starting server on port: '%d'", config.server_port);
    if (httpd_start(&server, &config) == ESP_OK)
    {
        // Set URI handlers
        ESP_LOGI(TAG, "Registering URI handlers");
        httpd_register_uri_handler(server, &index_uri);
        httpd_register_uri_handler(server, &store_config_uri);
        httpd_register_uri_handler(server, &check_status_uri);
        httpd_register_uri_handler(server, &load_config_uri);
        httpd_register_uri_handler(server, &logo_uri);
        httpd_register_uri_handler(server, &ws);
        httpd_register_uri_handler(server, &file_upload);
		httpd_register_uri_handler(server, &system_reboot);
		httpd_register_uri_handler(server, &store_canflt_uri);
		httpd_register_uri_handler(server, &load_canflt_uri);
        return;
    }

    ESP_LOGI(TAG, "Error starting server!");
}
void config_server_stop(void)
{
    if (server)
    {
        ESP_LOGI(TAG, "Stopping webserver");
        httpd_stop(server);
        server = NULL;
    }
}
static void websocket_task(void *pvParameters)
{
	static xdev_buffer ucTX_Buffer;
	httpd_ws_frame_t ws_pkt;  
	ESP_LOGI(TAG, "websocket_task started");
	while(1)
	{
		xQueueReceive(*xTX_Queue, &ucTX_Buffer, portMAX_DELAY);

		memset(&ws_pkt, 0, sizeof(httpd_ws_frame_t));
		ws_pkt.payload = (uint8_t*)ucTX_Buffer.ucElement;
		ws_pkt.len = ucTX_Buffer.usLen;
		ws_pkt.type = HTTPD_WS_TYPE_TEXT;

	    esp_err_t ret = httpd_ws_send_frame_async(rsp_arg.hd, rsp_arg.fd, &ws_pkt);
	    if (ret != ESP_OK)
	    {
//	    	tcp_server_resume();
	    	gpio_set_level(ws_led, 1);
	    	xEventGroupClearBits( xServerEventGroup, WS_CONNECTED_BIT );
//	    	vTaskSuspend( NULL );

	        ESP_LOGE(TAG, "httpd_ws_send_frame_async failed  %d", ret);
	    }
	}

}

bool config_server_ws_connected(void)
{
	EventBits_t ux_bits;
	if(xServerEventGroup != NULL)
	{
		ux_bits = xEventGroupGetBits(xServerEventGroup);

		return (ux_bits & WS_CONNECTED_BIT);
	}
	else return 0;
}

void config_server_start(QueueHandle_t *xTXp_Queue, QueueHandle_t *xRXp_Queue, uint8_t connected_led, char * did)
{
    if (server == NULL)
    {
        device_id = did;
        ws_led = connected_led;
        xTX_Queue = xTXp_Queue;
        xRX_Queue = xRXp_Queue;
        ESP_LOGI(TAG, "Starting webserver");
        server = config_server_init();

        // Allocate stack memory in PSRAM for the websocket task
        static StackType_t *websocket_task_stack;
        static StaticTask_t websocket_task_buffer;
        
        websocket_task_stack = heap_caps_malloc(4096, MALLOC_CAP_SPIRAM|MALLOC_CAP_8BIT);
        
        if (websocket_task_stack == NULL)
        {
            ESP_LOGE(TAG, "Failed to allocate websocket task stack memory");
            return;
        }
        
        // Create static task
        xwebsocket_handle = xTaskCreateStatic(
            websocket_task,
            "ws_task",
            4096,
            (void*)AF_INET,
            5,
            websocket_task_stack,
            &websocket_task_buffer
        );
        
        if (xwebsocket_handle == NULL)
        {
            ESP_LOGE(TAG, "Failed to create websocket task");
            heap_caps_free(websocket_task_stack);
            return;
        }
    }
}

int8_t config_server_get_ble_config(void)
{
	if(strcmp(device_config.ble_status, "enable") == 0)
	{
		return 1;
	}
	else if(strcmp(device_config.ble_status, "disable") == 0)
	{
		return 0;
	}
	return -1;
}

int8_t config_server_get_sleep_config(void)
{
	if(strcmp(device_config.sleep_status, "enable") == 0)
	{
		return 1;
	}
	else if(strcmp(device_config.sleep_status, "disable") == 0)
	{
		return 0;
	}
	return -1;
}

int8_t config_server_get_sleep_volt(float *sleep_volt)
{
	*sleep_volt = atof(device_config.sleep_volt);

	if(*sleep_volt >= 12.0f && *sleep_volt <= 15.0f)
	{
		return 1;
	}
	return -1;
}

int8_t config_server_get_wakeup_volt(float *wakeup_volt)
{
	*wakeup_volt = atof(device_config.wakeup_volt);

	if(*wakeup_volt >= 12.0f && *wakeup_volt <= 15.0f)
	{
		return 1;
	}
	return -1;
}

int8_t config_server_get_sleep_time(uint32_t *sleep_time)
{
    char *endptr;
    long slp_t = strtol(device_config.sleep_time, &endptr, 10);
    
    // Check for conversion errors
    if (*endptr != '\0' || endptr == device_config.sleep_time)
	{
        return -1;
    }
    
    // Validate range
    if (slp_t < 1 || slp_t > 30)
	{
        return -1;
    }
    
    *sleep_time = (uint32_t)slp_t;
    return 1;
}

int8_t config_server_get_periodic_wakeup(void)
{
	if(strcmp(device_config.periodic_wakeup, "enable") == 0)
	{
		return 1;
	}
	else if(strcmp(device_config.periodic_wakeup, "disable") == 0)
	{
		return 0;
	}
	return -1;
}

int8_t config_server_get_wakeup_interval(uint32_t *wakeup_interval)
{
    char *endptr;
    long wk_int = strtol(device_config.wakeup_interval, &endptr, 10);
    
    // Check for conversion errors
    if (*endptr != '\0' || endptr == device_config.wakeup_interval)
	{
        return -1;
    }
    
    // Validate range
    if (wk_int < 1 || wk_int > 240)
	{
        return -1;
    }
    
    *wakeup_interval = (uint32_t)wk_int;
    return 1;
}

int8_t config_server_get_battery_alert_config(void)
{
	if(strcmp(device_config.batt_alert, "enable") == 0)
	{
		return 1;
	}
	else if(strcmp(device_config.batt_alert, "disable") == 0)
	{
		return 0;
	}
	return -1;
}

int32_t config_server_get_alert_port(void)
{
	int port_val = atoi(device_config.batt_alert_port);

	if(port_val > 0 && port_val <= 65535)
	{
		return port_val;
	}
	return -1;
}

char *config_server_get_alert_ssid(void)
{
	return device_config.batt_alert_ssid;
}

char *config_server_get_alert_pass(void)
{
	return device_config.batt_alert_pass;
}

char *config_server_get_alert_protocol(void)
{
	return device_config.batt_alert_protocol;
}

char *config_server_get_alert_url(void)
{
	return device_config.batt_alert_url;
}

char *config_server_get_alert_topic(void)
{
	return device_config.batt_alert_topic;
}

char *config_server_get_alert_mqtt_user(void)
{
	return device_config.batt_mqtt_user;
}

char *config_server_get_alert_mqtt_pass(void)
{
	return device_config.batt_mqtt_pass;
}

int config_server_get_alert_time(void)
{
	if(strcmp(device_config.batt_alert_time, "1") == 0)
	{
		return 1;
	}
	else if(strcmp(device_config.batt_alert_time, "6") == 0)
	{
		return 6;
	}
	else if(strcmp(device_config.batt_alert_time, "12") == 0)
	{
		return 12;
	}
	else if(strcmp(device_config.batt_alert_time, "24") == 0)
	{
		return 24;
	}
	else
	{
		return -1;
	}

}

int8_t config_server_get_alert_volt(float *alert_volt)
{
	*alert_volt = atof(device_config.batt_alert_volt);

	if(device_config.batt_alert_volt[2] != '.')
	{
		return -1;
	}

	if(*alert_volt > 8.0f && *alert_volt <= 15.0f)
	{
		return 1;
	}
	return -1;
}

int8_t config_server_mqtt_en_config(void)
{
	if(config_server_get_ble_config())
	{
		return 0;
	}
	if(strcmp(device_config.mqtt_en, "enable") == 0)
	{
		return 1;
	}
	else if(strcmp(device_config.mqtt_en, "disable") == 0)
	{
		return 0;
	}
	return -1;
}

int8_t config_server_mqtt_tx_en_config(void)
{
	if(strcmp(device_config.mqtt_tx_en, "enable") == 0)
	{
		return 1;
	}
	else if(strcmp(device_config.mqtt_tx_en, "disable") == 0)
	{
		return 0;
	}
	return -1;
}

int8_t config_server_mqtt_rx_en_config(void)
{
	if(strcmp(device_config.mqtt_rx_en, "enable") == 0)
	{
		return 1;
	}
	else if(strcmp(device_config.mqtt_rx_en, "disable") == 0)
	{
		return 0;
	}
	return -1;
}

int8_t config_server_mqtt_elm327_log(void)
{
	if(strcmp(device_config.mqtt_elm327_log, "enable") == 0)
	{
		return 1;
	}
	else if(strcmp(device_config.mqtt_elm327_log, "disable") == 0)
	{
		return 0;
	}
	return -1;
}
char *config_server_get_mqtt_url(void)
{
	return device_config.mqtt_url;
}

int32_t config_server_get_mqtt_port(void)
{
	int port_val = atoi(device_config.mqtt_port);

	if(port_val > 0 && port_val <= 65535)
	{
		return port_val;
	}
	return -1;
}

char *config_server_get_mqtt_user(void)
{
	return device_config.mqtt_user;
}

char *config_server_get_mmqtt_pass(void)
{
	return device_config.mqtt_pass;
}

char *config_server_get_mqtt_tx_topic(void)
{
	return device_config.mqtt_tx_topic;
}

char *config_server_get_mqtt_rx_topic(void)
{
	return device_config.mqtt_rx_topic;
}

char *config_server_get_mqtt_status_topic(void)
{
	return device_config.mqtt_status_topic;
}

char *config_server_get_mqtt_canflt(void)
{
	return mqtt_canflt_file;
}

wifi_security_t config_server_get_sta_security(void)
{
	if (strcmp(device_config.sta_security, "wpa2") == 0)
	{
		return WIFI_WPA2_PSK;
	}
	else if (strcmp(device_config.sta_security, "wpa3") == 0)
	{
		return WIFI_WPA3_PSK;
	}
	return WIFI_MAX;
}

int8_t config_server_get_logger_config(void)
{
	if(strcmp(device_config.logger_status, "enable") == 0)
	{
		return 1;
	}
	else if(strcmp(device_config.logger_status, "disable") == 0)
	{
		return 0;
	}

	return -1;
}

int8_t config_server_get_ap_auto_disable(void)
{
	if(strcmp(device_config.ap_auto_disable, "enable") == 0)
	{
		return 1;
	}
	else if(strcmp(device_config.ap_auto_disable, "disable") == 0)
	{
		return 0;
	}
	return -1;
}

log_filesystem_t config_server_get_log_filesystem(void)
{
	if(strcmp(device_config.log_filesystem, "littlefs") == 0)
	{
		return LOG_FS_LITTLEFS;
	}
	else if(strcmp(device_config.log_filesystem, "fatfs") == 0)
	{
		return LOG_FS_FATFS;
	}
	return MAX_LOG_FS;
}

log_storage_t config_server_get_log_storage(void)
{
	if(strcmp(device_config.log_storage, "sdcard") == 0)
	{
		return LOG_SDCARD;
	}
	else if(strcmp(device_config.log_storage, "internal") == 0)
	{
		return LOG_INTERNAL;
	}
	return MAX_LOG_STORAGE;
}

int8_t config_server_get_log_period(uint32_t *log_period)
{
	char *endptr;
	long log_int = strtol(device_config.log_period, &endptr, 10);
	
	// Check for conversion errors
	if (*endptr != '\0' || endptr == device_config.log_period)
	{
		return -1;
	}
	
	// Validate range
	if (log_int < 1 || log_int > 300)
	{
		return -1;
	}
	
	*log_period = (uint32_t)log_int;
	return 1;
}

void config_server_set_ble_config(uint8_t b)
{
	cJSON * root;
	root = cJSON_Parse(device_config_file);
	if(b == 1)
	{
		cJSON_SetValuestring(cJSON_GetObjectItem(root,"ble_status"), "enable");
	}
	else if(b==0)
	{
		cJSON_SetValuestring(cJSON_GetObjectItem(root,"ble_status"), "disable");
	}
	const char *resp_str = cJSON_Print(root);
	ESP_LOGI(TAG, "resp_str:%s", resp_str);
	FILE* f = fopen(FS_MOUNT_POINT"/config.json", "w");
	if (f != NULL)
	{
		fprintf(f, resp_str);
		fclose(f);
	}
	// xTimerStart( xrestartTimer, 0 );
	config_server_reboot();
	free((void *)resp_str);
    cJSON_Delete(root);
}

