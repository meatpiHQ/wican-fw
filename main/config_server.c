/*
 * This file is part of the WiCAN project.
 *
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
#include <time.h>

#include "config_server.h"
#include "filesystem.h"
#include "cJSON.h"
#include "dev_status.h"
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
#include "hw_config.h"
#include "ha_webhooks.h"

#define WIFI_CONNECTED_BIT			BIT0
#define WS_CONNECTED_BIT			BIT1
TaskHandle_t xwebsocket_handle = NULL;
static EventGroupHandle_t xServerEventGroup = NULL;
static QueueHandle_t xip_Queue = NULL;

static QueueHandle_t *xTX_Queue, *xRX_Queue;

static uint8_t ws_led;
#define TAG __func__

httpd_handle_t server = NULL;
char *device_config_file = NULL;
static char *mqtt_canflt_file = NULL;

static char *device_id;
static const char logo[] = {"<svg data-bbox=\"8.091 171.26 470.264 169.479\" overflow=\"hidden\" y=\"0\" x=\"0\" class=\"fl-svgdocument\" id=\"_haaUNSc7au_3N3MChrVwU\" data-svgdocument=\"\" style=\"max-height: 500px\" viewBox=\"0 160.427008 490.496 285.696\" xmlns=\"http://www.w3.org/2000/svg\" width=\"490.496\" height=\"285.696\">  <path transform=\"matrix(1.8832271099090576, 0, 0, 1.8832271099090576, -2.199237823486328, 160.8857421875)\" fill=\"#46d2eb\" d=\"M50.459 95.503c-2.519 0-4.975-0.259-7.383-0.655l2.064-5.143  c1.744 0.236 3.511 0.399 5.319 0.399c2.501 0 4.94-0.261 7.313-0.703l-5.998-6.497h-8.514h-1.8h-1.8v-5.398h-5.899  c-0.623 1.07-1.771 1.8-3.099 1.8c-1.988 0-3.601-1.612-3.601-3.601s1.612-3.6 3.601-3.6c1.329 0 2.477 0.728 3.099 1.8h5.899v-1.8  v-3.601h3.6H61.26v0.77l4.416 4.784c0.315-0.09 0.641-0.153 0.983-0.153c1.988 0 3.6 1.612 3.6 3.601c0 1.987-1.611 3.598-3.6 3.598  s-3.599-1.61-3.599-3.598c0-0.296 0.044-0.579 0.11-0.854l-4.197-4.547H43.261v7.2H54.06v0.768l7.725 8.366  c6.576-1.96 12.414-5.601 17.071-10.395l-5.483-5.939H72.06V55.081l-5.435-5.883c-1.503 1.309-3.101 2.702-4.651 4.053  c-0.299 0.263-0.893 0.779-0.893 0.779l-1.621 0.024v0.051h-7.699c-0.623 1.072-1.771 1.801-3.1 1.801c-1.988 0-3.6-1.612-3.6-3.601  s1.612-3.599 3.6-3.599c1.329 0 2.477 0.728 3.1 1.8h7.699v0.393l10.799-9.623V27.107h-7.198v7.7  c1.07 0.623 1.798 1.771 1.798 3.099c0 1.988-1.61 3.601-3.599 3.601c-1.987 0-3.6-1.612-3.6-3.601c0-1.329 0.728-2.477 1.8-3.099  v-7.7v-1.8v-1.8h10.799v-7.183c-1.426-0.825-2.89-1.593-4.421-2.238c-0.573 0.264-1.206 0.422-1.879 0.422  c-2.484 0-4.499-2.015-4.499-4.5c0-2.484 2.015-4.499 4.499-4.499c2.243 0 4.087 1.644 4.428 3.788  c15.918 6.93 27.07 22.741 27.07 41.209C95.457 75.357 75.312 95.503 50.459 95.503z M81.058 52.306  c-1.988 0-3.599-1.61-3.599-3.599c0-1.329 0.729-2.477 1.799-3.101V30.708h1.8h1.801h1.87c-2.753-4.731-6.471-8.829-10.869-12.054  v26.453h-2.542c-0.584 0.508-1.25 1.09-1.978 1.723l6.525 7.069l-0.271 0.207h0.064v5.4h2.3c0.622-1.072 1.77-1.8 3.099-1.8  c1.988 0 3.601 1.612 3.601 3.601c0 1.987-1.612 3.6-3.601 3.6c-1.329 0-2.477-0.729-3.099-1.8h-2.3v6.168l5.598 6.063  c5.489-6.791 8.802-15.415 8.802-24.831c0-5.783-1.266-11.257-3.498-16.199h-3.702v11.298c1.072 0.625 1.8 1.772 1.8 3.101  C84.658 50.695 83.046 52.306 81.058 52.306z M50.459 10.908c-1.214 0-2.412 0.076-3.598 0.183v11.386l4.416 4.784  c0.314-0.09 0.64-0.153 0.982-0.153c1.988 0 3.601 1.612 3.601 3.601c0 1.988-1.612 3.6-3.601 3.6s-3.599-1.612-3.599-3.6  c0-0.295 0.044-0.579 0.111-0.853l-2.535-2.748h-1.918l-6.35 5.536l4.86 5.263h0.431v0.468l0.018 0.02l-0.018 0.016v22.897H61.76  c0.622-1.073 1.77-1.801 3.099-1.801c1.988 0 3.601 1.612 3.601 3.601s-1.612 3.599-3.601 3.599c-1.329 0-2.477-0.729-3.099-1.799  H43.261h-1.8h-1.8V39.782L23.152 21.899c-1 0.953-1.942 1.963-2.839 3.015L35.63 41.507h0.431v0.467l0.018 0.02l-0.018 0.016v21.097  v3.599h-3.599h-9h-1.798h-1.8v-8.999h-8.327c2.744 14.895 13.823 26.857 28.229 30.896l-2.025 5.049  C19.088 88.158 5.464 70.936 5.464 50.507c0-24.852 20.146-44.997 44.996-44.997c2.447 0 4.831 0.248 7.172 0.624l-2.062 5.142  C53.893 11.06 52.196 10.908 50.459 10.908z M18.068 27.79c-4.534 6.425-7.206 14.251-7.206 22.717c0 1.214 0.063 2.412 0.171 3.599  h12.428v1.801v1.8v5.4h9V43.382l-5.627-6.095l-5.231 7.245c0.031 0.188 0.06 0.378 0.06 0.575c0 1.988-1.612 3.6-3.6 3.6  s-3.6-1.612-3.6-3.6s1.612-3.6 3.6-3.6c0.401 0 0.779 0.081 1.139 0.202l5.142-7.12L18.068 27.79z M43.261 11.594  c-6.497 1.195-12.418 3.998-17.389 7.942l9.656 10.461l7.733-6.742V11.594z\" clip-rule=\"evenodd\" fill-rule=\"evenodd\" id=\"_efnfk55a8_25\"/>  <path d=\"M 19.6-40.6L 19.6 0L 4 0L 4-64.2L 23.1-64.2L 33.5-29.9L 43.9-64.2L 63-64.2L 63 0L 47.5 0L 47.5-40.8L 39.5-13.9L 27.5-13.9L 19.6-40.6ZM 84.7-49.9L 84.7-49.9L 96.9-49.9Q 111.6-49.9  111.6-37.5L 111.6-37.5L 111.6-20.5L 86.1-20.5L 86.1-13.9Q 86.1-11.7  88.6-11.7L 88.6-11.7L 93-11.7Q 95.5-11.7  95.5-14.1L 95.5-14.1L 95.5-16.9L 111.6-16.9L 111.6-12.4Q 111.6 0  96.9 0L 96.9 0L 84.7 0Q 70 0  70-12.4L 70-12.4L 70-37.5Q 70-49.9  84.7-49.9ZM 86.1-36L 86.1-29.7L 95.5-29.7L 95.5-36Q 95.5-38.2  93-38.2L 93-38.2L 88.6-38.2Q 86.1-38.2  86.1-36L 86.1-36ZM 141.3-43.5L 141.3-49.9L 158-49.9L 158 0L 141 0L 141-5.4Q 139.1 0  132.2 0L 132.2 0L 130.4 0Q 117.1 0  117.1-12.4L 117.1-12.4L 117.1-37.5Q 117.1-49.9  130.4-49.9L 130.4-49.9L 132.2-49.9Q 139.9-49.9  141.3-43.5L 141.3-43.5ZM 136.6-12.5L 136.6-12.5L 138.3-12.5Q 141-12.5  141-15.5L 141-15.5L 141-34.4Q 141-37.4  138.3-37.4L 138.3-37.4L 136.6-37.4Q 134.1-37.4  134.1-35.2L 134.1-35.2L 134.1-14.7Q 134.1-12.5  136.6-12.5ZM 191.3-49.9L 191.3-37.4L 185-37.4L 185-14.7Q 185-12.5  187.5-12.5L 187.5-12.5L 191.2-12.5L 191.2 0L 182.7 0Q 168 0  168-12.4L 168-12.4L 168-37.4L 162.5-37.4L 162.5-49.9L 168-49.9L 168-59.7L 185-59.7L 185-49.9L 191.3-49.9ZM 196.3 0L 196.3-64.2L 226-64.2Q 240.7-64.2  240.7-51.8L 240.7-51.8L 240.7-33.4Q 240.7-21  226-21L 226-21L 213.8-21L 213.8 0L 196.3 0ZM 223.2-35.7L 223.2-35.7L 223.2-49.5Q 223.2-51.7  220.7-51.7L 220.7-51.7L 213.8-51.7L 213.8-33.5L 220.7-33.5Q 223.2-33.5  223.2-35.7ZM 264-54.9L 246.4-54.9L 246.4-66.9L 264-66.9L 264-54.9ZM 263.7-49.9L 263.7 0L 246.7 0L 246.7-49.9L 263.7-49.9Z\" transform=\"matrix(1.1063517332077026, 0, 0, 1.1063517332077026, 186.2774200439453, 270.212646484375)\" id=\"_efnfk55a8_13\" data-fl-textpath=\"\" font-weight=\"700\" fill=\"#24478f\" y=\"0\" x=\"0\" font-family=\"Teko\" href=\"\" dy=\"0\" dx=\"0\" offset=\"0\" letter-spacing=\"0\" font-size=\"100\" text-anchor=\"start\"/>  <path d=\"M 17.7-49.9L 17.7-49.9L 29.9-49.9Q 44.6-49.9  44.6-37.5L 44.6-37.5L 44.6-20.5L 19.1-20.5L 19.1-13.9Q 19.1-11.7  21.6-11.7L 21.6-11.7L 26-11.7Q 28.5-11.7  28.5-14.1L 28.5-14.1L 28.5-16.9L 44.6-16.9L 44.6-12.4Q 44.6 0  29.9 0L 29.9 0L 17.7 0Q 3 0  3-12.4L 3-12.4L 3-37.5Q 3-49.9  17.7-49.9ZM 19.1-36L 19.1-29.7L 28.5-29.7L 28.5-36Q 28.5-38.2  26-38.2L 26-38.2L 21.6-38.2Q 19.1-38.2  19.1-36L 19.1-36ZM 68.1-66.9L 68.1 0L 51.1 0L 51.1-66.9L 68.1-66.9ZM 89.8-49.9L 89.8-49.9L 102-49.9Q 116.7-49.9  116.7-37.5L 116.7-37.5L 116.7-20.5L 91.2-20.5L 91.2-13.9Q 91.2-11.7  93.7-11.7L 93.7-11.7L 98.1-11.7Q 100.6-11.7  100.6-14.1L 100.6-14.1L 100.6-16.9L 116.7-16.9L 116.7-12.4Q 116.7 0  102 0L 102 0L 89.8 0Q 75.1 0  75.1-12.4L 75.1-12.4L 75.1-37.5Q 75.1-49.9  89.8-49.9ZM 91.2-36L 91.2-29.7L 100.6-29.7L 100.6-36Q 100.6-38.2  98.1-38.2L 98.1-38.2L 93.7-38.2Q 91.2-38.2  91.2-36L 91.2-36ZM 162.3-37.5L 162.3-30.6L 145.9-30.6L 145.9-35.2Q 145.9-37.4  143.4-37.4L 143.4-37.4L 141.7-37.4Q 139.2-37.4  139.2-35.2L 139.2-35.2L 139.2-14.7Q 139.2-12.5  141.7-12.5L 141.7-12.5L 143.4-12.5Q 145.9-12.5  145.9-14.7L 145.9-14.7L 145.9-19.3L 162.3-19.3L 162.3-12.4Q 162.3 0  147.6 0L 147.6 0L 136.9 0Q 122.2 0  122.2-12.4L 122.2-12.4L 122.2-37.5Q 122.2-49.9  136.9-49.9L 136.9-49.9L 147.6-49.9Q 162.3-49.9  162.3-37.5L 162.3-37.5ZM 193.6-49.9L 193.6-37.4L 187.3-37.4L 187.3-14.7Q 187.3-12.5  189.8-12.5L 189.8-12.5L 193.5-12.5L 193.5 0L 185 0Q 170.3 0  170.3-12.4L 170.3-12.4L 170.3-37.4L 164.8-37.4L 164.8-49.9L 170.3-49.9L 170.3-59.7L 187.3-59.7L 187.3-49.9L 193.6-49.9ZM 226.1-50.4L 226.1-50.4L 227.6-50.4L 227.6-35.6L 221.9-35.6Q 215.6-35.6  215.6-28.8L 215.6-28.8L 215.6 0L 198.6 0L 198.6-49.9L 215.3-49.9L 215.3-42.2Q 216.1-46  218.9-48.2Q 221.7-50.4  226.1-50.4ZM 245.8-49.9L 245.8-49.9L 258.5-49.9Q 266.1-49.9  269.4-46.7Q 272.7-43.5  272.7-37.5L 272.7-37.5L 272.7-12.4Q 272.7-6.4  269.4-3.2Q 266.1 0  258.5 0L 258.5 0L 245.8 0Q 238.2 0  234.9-3.2Q 231.6-6.4  231.6-12.4L 231.6-12.4L 231.6-37.5Q 231.6-43.5  234.9-46.7Q 238.2-49.9  245.8-49.9ZM 256-14L 256-14L 256-35.9Q 256-38.1  253.5-38.1L 253.5-38.1L 250.8-38.1Q 248.3-38.1  248.3-35.9L 248.3-35.9L 248.3-14Q 248.3-11.8  250.8-11.8L 250.8-11.8L 253.5-11.8Q 256-11.8  256-14ZM 306-49.9L 306-49.9L 307.8-49.9Q 321.1-49.9  321.1-37.5L 321.1-37.5L 321.1 0L 304.1 0L 304.1-35.2Q 304.1-37.4  301.6-37.4L 301.6-37.4L 299.9-37.4Q 297.2-37.4  297.2-34.4L 297.2-34.4L 297.2 0L 280.2 0L 280.2-49.9L 296.9-49.9L 296.9-43.5Q 298.3-49.9  306-49.9ZM 345.9-54.9L 328.3-54.9L 328.3-66.9L 345.9-66.9L 345.9-54.9ZM 345.6-49.9L 345.6 0L 328.6 0L 328.6-49.9L 345.6-49.9ZM 392.7-37.5L 392.7-30.6L 376.3-30.6L 376.3-35.2Q 376.3-37.4  373.8-37.4L 373.8-37.4L 372.1-37.4Q 369.6-37.4  369.6-35.2L 369.6-35.2L 369.6-14.7Q 369.6-12.5  372.1-12.5L 372.1-12.5L 373.8-12.5Q 376.3-12.5  376.3-14.7L 376.3-14.7L 376.3-19.3L 392.7-19.3L 392.7-12.4Q 392.7 0  378 0L 378 0L 367.3 0Q 352.6 0  352.6-12.4L 352.6-12.4L 352.6-37.5Q 352.6-49.9  367.3-49.9L 367.3-49.9L 378-49.9Q 392.7-49.9  392.7-37.5L 392.7-37.5ZM 420.2-13.2L 420.2-13.2L 420.2-17.2Q 420.2-19.4  417.7-19.4L 417.7-19.4L 411.6-19.4Q 397.2-19.4  397.2-31.7L 397.2-31.7L 397.2-37.5Q 397.2-49.9  411.9-49.9L 411.9-49.9L 421.2-49.9Q 435.9-49.9  435.9-37.5L 435.9-37.5L 435.9-33.3L 419.7-33.3L 419.7-36.7Q 419.7-38.9  417.2-38.9L 417.2-38.9L 416.2-38.9Q 413.7-38.9  413.7-36.7L 413.7-36.7L 413.7-32.3Q 413.7-30.1  416.2-30.1L 416.2-30.1L 422.3-30.1Q 430-30.1  433.35-26.95Q 436.7-23.8  436.7-17.9L 436.7-17.9L 436.7-12.4Q 436.7 0  422 0L 422 0L 412.2 0Q 397.5 0  397.5-12.4L 397.5-12.4L 397.5-16.5L 414-16.5L 414-13.2Q 414-11  416.5-11L 416.5-11L 417.7-11Q 420.2-11  420.2-13.2Z\" transform=\"matrix(0.5377987017018887, 0, 0, 0.4702223133115182, 189.08943087167052, 309.63039515694595)\" id=\"_eg9ak381l_44\" data-fl-textpath=\"\" font-weight=\"700\" fill=\"#24478f\" y=\"0\" x=\"0\" font-family=\"Teko\" href=\"\" dy=\"0\" dx=\"0\" offset=\"0\" letter-spacing=\"0\" font-size=\"100\" text-anchor=\"start\"/></svg>"};

extern const unsigned char homepage_start[] asm("_binary_homepage_full_html_start");
extern const unsigned char homepage_end[]   asm("_binary_homepage_full_html_end");

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

const char device_config_default[] = "{\"wifi_mode\":\"AP\",\"ap_ch\":\"6\",\"webhook_en\":\"enable\",\"sta_ssid\":\"MeatPi\",\"sta_pass\":\"TomatoSauce\",\"sta_security\":\"wpa3\",\"can_datarate\":\"500K\",\"can_mode\":\"normal\",\"port_type\":\"tcp\",\"port\":\"3333\",\"ap_pass\":\"@meatpi#\",\"protocol\":\"slcan\",\"ble_pass\":\"123456\",\"ble_status\":\"disable\",\"sleep_status\":\"disable\",\"sleep_volt\":\"13.1\",\"wakeup_volt\":\"13.5\",\"batt_alert\":\"disable\",\"batt_alert_ssid\":\"MeatPi\",\"batt_alert_pass\":\"TomatoSauce\",\"batt_alert_volt\":\"11.0\",\"batt_alert_protocol\":\"mqtt\",\"batt_alert_url\":\"mqtt://mqtt.eclipseprojects.io\",\"batt_alert_port\":\"1883\",\"batt_alert_topic\":\"CAR1/voltage\",\"batt_mqtt_user\":\"meatpi\",\"batt_mqtt_pass\":\"meatpi\",\"batt_alert_time\":\"1\",\"mqtt_en\":\"disable\",\"mqtt_elm327_log\":\"disable\",\"mqtt_url\":\"mqtt://127.0.0.1\",\"mqtt_port\":\"1883\",\"mqtt_user\":\"meatpi\",\"mqtt_pass\":\"meatpi\",\"keep_alive\":\"30\",\"mqtt_tx_topic\":\"wican/%s/can/tx\",\"mqtt_rx_topic\":\"wican/%s/can/rx\",\"mqtt_status_topic\":\"wican/%s/can/status\"}";
static device_config_t device_config;
TimerHandle_t xrestartTimer;

/* Max length a file path can have on storage */
#if defined(CONFIG_LITTLEFS_OBJ_NAME_LEN)
#define FILE_PATH_MAX (ESP_VFS_PATH_MAX + CONFIG_LITTLEFS_OBJ_NAME_LEN)
#elif defined(CONFIG_SPIFFS_OBJ_NAME_LEN)
#define FILE_PATH_MAX (ESP_VFS_PATH_MAX + CONFIG_SPIFFS_OBJ_NAME_LEN)
#else
#define FILE_PATH_MAX (ESP_VFS_PATH_MAX + 32)
#endif
/* Scratch buffer size */
#define SCRATCH_BUFSIZE  4096

#define MAX_FILE_SIZE   (2000*1024) // 200 KB
#define MAX_FILE_SIZE_STR "200KB"

struct file_server_data {
    /* Base path of file storage */
    char base_path[ESP_VFS_PATH_MAX + 1];

    /* Scratch buffer for temporary storage during file transfer */
    char scratch[SCRATCH_BUFSIZE];
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

int8_t config_server_get_webhook_en(void)
{
	// Backward-compatible default: enabled unless explicitly set to "disable"
	if (strcmp(device_config.webhook_en, "disable") == 0)
	{
		return 0;
	}
	return 1;
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
	return SLCAN;
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

    xTimerStart( xrestartTimer, 0 );
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

	ESP_LOGI(TAG, "reboot");
	xTimerStart( xrestartTimer, 0 );
    // esp_restart();
    return ESP_OK;
}

static esp_err_t logo_handler(httpd_req_t *req)
{
    const char* resp_str = (const char*)logo;
	httpd_resp_set_type(req, "image/svg+xml");
    httpd_resp_send(req, (const char*)resp_str, HTTPD_RESP_USE_STRLEN);
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
	char *resp_str = config_server_get_status_json(false);
	if (!resp_str)
	{
		httpd_resp_send_err(req, HTTPD_500_INTERNAL_SERVER_ERROR, "Failed to build status JSON");
		return ESP_FAIL;
	}

	httpd_resp_set_type(req, "application/json");
	httpd_resp_send(req, resp_str, HTTPD_RESP_USE_STRLEN);
	free(resp_str);
	return ESP_OK;
}

char *config_server_get_status_json(bool remove_sensitive_info)
{
	char ip_str[20] = {0};
	config_server_get_sta_ip(ip_str);

	cJSON *root = cJSON_CreateObject();
	if (!root)
		return NULL;

	char fver[16] = {0};
	char hver[32] = {0};
	const esp_partition_t *running = esp_ota_get_running_partition();
	esp_app_desc_t running_app_info;
	memset(&running_app_info, 0, sizeof(running_app_info));
	uint32_t firmware_ver_minor = 0, firmware_ver_major = 0;

	if (running && esp_ota_get_partition_description(running, &running_app_info) == ESP_OK)
	{
		(void)sscanf(running_app_info.version, "v%ld.%ld", &firmware_ver_major, &firmware_ver_minor);
	}
	snprintf(fver, sizeof(fver), "%ld.%02ld", firmware_ver_major, firmware_ver_minor);
	snprintf(hver, sizeof(hver), "WiCAN-%s", HARDWARE_VERSION);

	cJSON_AddStringToObject(root, "wifi_mode", device_config.wifi_mode);
	cJSON_AddStringToObject(root, "ap_ch", device_config.ap_ch);
	cJSON_AddStringToObject(root, "ap_auto_disable", device_config.ap_auto_disable);
	cJSON_AddStringToObject(root, "webhook_en", device_config.webhook_en);

	if (!remove_sensitive_info)
	{
		cJSON_AddStringToObject(root, "sta_ssid", device_config.sta_ssid);
		cJSON_AddStringToObject(root, "sta_pass", device_config.sta_pass);
		cJSON_AddStringToObject(root, "sta_security", device_config.sta_security);
		cJSON_AddStringToObject(root, "sta_ip", ip_str);
	}

	cJSON_AddStringToObject(root, "sta_status", (wifi_network_is_connected() ? "Connected" : "Not Connected"));
	cJSON_AddBoolToObject(root, "sta_connected", wifi_network_is_connected());
	cJSON_AddStringToObject(root, "mdns", wc_mdns_get_hostname());
	cJSON_AddStringToObject(root, "ble_status", device_config.ble_status);
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
	cJSON_AddStringToObject(root, "sleep_time", device_config.sleep_time);
	cJSON_AddStringToObject(root, "wakeup_volt", device_config.wakeup_volt);
	cJSON_AddStringToObject(root, "wakeup_time", device_config.wakeup_time);

	cJSON_AddStringToObject(root, "batt_alert", device_config.batt_alert);
	cJSON_AddStringToObject(root, "batt_alert_protocol", device_config.batt_alert_protocol);
	cJSON_AddStringToObject(root, "batt_alert_volt", device_config.batt_alert_volt);
	cJSON_AddStringToObject(root, "batt_alert_topic", device_config.batt_alert_topic);
	cJSON_AddStringToObject(root, "batt_alert_time", device_config.batt_alert_time);

	if (!remove_sensitive_info)
	{
		cJSON_AddStringToObject(root, "batt_alert_ssid", device_config.batt_alert_ssid);
		cJSON_AddStringToObject(root, "batt_alert_pass", device_config.batt_alert_pass);
		cJSON_AddStringToObject(root, "batt_alert_url", device_config.batt_alert_url);
		cJSON_AddStringToObject(root, "batt_alert_port", device_config.batt_alert_port);
		cJSON_AddStringToObject(root, "batt_mqtt_user", device_config.batt_mqtt_user);
		cJSON_AddStringToObject(root, "batt_mqtt_pass", device_config.batt_mqtt_pass);
	}

	{
		char volt[12] = {0};
		float tmp = 0;
		if (sleep_mode_get_voltage(&tmp) == 1)
			snprintf(volt, sizeof(volt), "%.1fV", tmp);
		else
			strlcpy(volt, "N/A", sizeof(volt));
		cJSON_AddStringToObject(root, "batt_voltage", volt);
	}

	char uptime_str[32];
	dev_status_format_uptime(uptime_str, sizeof(uptime_str));
	if(uptime_str[0] == '\0')
	{
		strlcpy(uptime_str, "N/A", sizeof(uptime_str));
	}
	uptime_str[sizeof(uptime_str) - 1] = '\0';
	cJSON_AddStringToObject(root, "uptime", uptime_str);

	cJSON_AddStringToObject(root, "mqtt_en", device_config.mqtt_en);
	if (!remove_sensitive_info)
	{
		cJSON_AddStringToObject(root, "mqtt_url", device_config.mqtt_url);
		cJSON_AddStringToObject(root, "mqtt_port", device_config.mqtt_port);
		cJSON_AddStringToObject(root, "mqtt_user", device_config.mqtt_user);
		cJSON_AddStringToObject(root, "mqtt_pass", device_config.mqtt_pass);
	}
	cJSON_AddStringToObject(root, "mqtt_tx_topic", device_config.mqtt_tx_topic);
	cJSON_AddStringToObject(root, "mqtt_rx_topic", device_config.mqtt_rx_topic);
	cJSON_AddStringToObject(root, "mqtt_status_topic", device_config.mqtt_status_topic);

	cJSON_AddStringToObject(root, "device_id", device_id ? device_id : "");
	if (autopid_get_ecu_status())
		cJSON_AddStringToObject(root, "ecu_status", "online");
	else
		cJSON_AddStringToObject(root, "ecu_status", "offline");

	// Timestamp (Unix epoch seconds) - may be 0 if time not set
	{
		time_t now;
		time(&now);
		cJSON_AddNumberToObject(root, "timestamp", (double)now);
	}

	char *resp_str = cJSON_PrintUnformatted(root);
	cJSON_Delete(root);
	return resp_str;
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

    xTimerStart( xrestartTimer, 0 );

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

    char *available_pids = malloc(5120);
    if (available_pids == NULL) {
        httpd_resp_send_500(req);
        return ESP_FAIL;
    }
    memset(available_pids, 0, 5120);
    
    if (autopid_find_standard_pid(protocol_num, available_pids, 5120) == ESP_OK) {
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
    .user_ctx  = NULL
};
static const httpd_uri_t scan_available_pids_uri = {
    .uri       = "/scan_available_pids",
    .method    = HTTP_GET,
    .handler   = scan_available_pids_handler,
    .user_ctx  = NULL
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

	//*****
	key = cJSON_GetObjectItem(root,"keep_alive");
	if(key == 0)
	{
		strcpy(device_config.keep_alive, "30");
	}
	else
	{
		uint32_t keep_alive = atoi(device_config.keep_alive);

		if(keep_alive > 120 && keep_alive < 1)
		{
			strcpy(device_config.keep_alive, "30");
		}

		strcpy(device_config.keep_alive, key->valuestring);
	}

	ESP_LOGE(TAG, "device_config.keep_alive: %s", device_config.keep_alive);
	//*****

	//*****
	strlcpy(device_config.webhook_en, "enable", sizeof(device_config.webhook_en));
	key = cJSON_GetObjectItem(root, "webhook_en");
	if (key)
	{
		if (cJSON_IsString(key) && key->valuestring && strlen(key->valuestring) > 0)
		{
			strlcpy(device_config.webhook_en, key->valuestring, sizeof(device_config.webhook_en));
		}
		else if (cJSON_IsBool(key))
		{
			strlcpy(device_config.webhook_en, cJSON_IsTrue(key) ? "enable" : "disable", sizeof(device_config.webhook_en));
		}
	}
	ESP_LOGI(TAG, "device_config.webhook_en: %s", device_config.webhook_en);
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
static uint8_t fs_loaded_flag = 0;
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

    if(xServerEventGroup == NULL)
    {
    	xServerEventGroup = xEventGroupCreate();
    	config_server_wifi_connected(0);
    }

    if(xip_Queue == NULL)
    {
    	xip_Queue = xQueueCreate(1, 20);
    }

    if(fs_loaded_flag == 0)
    {
		filesystem_init();

		// Use POSIX and C standard library functions to work with files.
		// First create a file.
		ESP_LOGI(TAG, "Opening file");
		FILE* f = fopen(FS_MOUNT_POINT"/config.json", "r");
		if (f == NULL)
		{
			ESP_LOGI(TAG, "Config file does not exist, load default");
			f = fopen(FS_MOUNT_POINT"/config.json", "w");
//			fwrite(device_config_default , 1 , sizeof(device_config_default) , f );
			fprintf(f, device_config_default, (char*)device_id, (char*)device_id, (char*)device_id);
			fclose(f);
			f = fopen(FS_MOUNT_POINT"/config.json", "r");
		}
		fseek(f, 0, SEEK_END);
		int filesize = ftell(f);
		fseek(f, 0, SEEK_SET);

		device_config_file = malloc(filesize+1);
		ESP_LOGI(__func__, "File size: %d", filesize);
		fseek(f, 0, SEEK_SET);
		fread(device_config_file, sizeof(char), filesize, f);
		device_config_file[filesize] = 0;
		fseek(f, 0, SEEK_SET);
		ESP_LOGI(TAG, "config.json: %s", device_config_file);
		config_server_load_cfg(device_config_file);
		
		FILE* f1 = fopen(FS_MOUNT_POINT"/mqtt_canfilt.json", "r");
		if (f1 != NULL)
		{
			fseek(f1, 0, SEEK_END);
			filesize = ftell(f1);
			fseek(f1, 0, SEEK_SET);
			mqtt_canflt_file = malloc(filesize+1);
			ESP_LOGI(__func__, "mqtt_canflt_file File size: %d", filesize);
			fseek(f1, 0, SEEK_SET);
			fread(mqtt_canflt_file, sizeof(char), filesize, f1);
			mqtt_canflt_file[filesize] = 0;
			fseek(f1, 0, SEEK_SET);
			fclose(f1);
			ESP_LOGI(TAG, "mqtt_canfilt.json: %s", mqtt_canflt_file);
		}

		fs_loaded_flag = 1;
    }

		// Initialize Home Assistant webhook cache early (before any /api/webhook requests).
		// This must not rely on PSRAM (e.g., ESP32-C3 has none).
		ha_webhooks_init();

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
	config.max_uri_handlers = 32;
	config.stack_size = 5120;
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
		httpd_register_uri_handler(server, &scan_available_pids_uri);
		ha_webhooks_register_handlers(server);
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
	config.max_uri_handlers = 32;
	// Ensure webhook cache is initialized after restarts too.
	ha_webhooks_init();
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
		httpd_register_uri_handler(server, &scan_available_pids_uri);
		ha_webhooks_register_handlers(server);
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

        xTaskCreate(websocket_task, "ws_task", 4096, (void*)AF_INET, 5, &xwebsocket_handle);

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
	char *endptr;
	*sleep_volt = strtof(device_config.sleep_volt, &endptr);

	// Check for conversion errors
	if (*endptr != '\0' || endptr == device_config.sleep_volt)
	{
		return -1;
	}

	if(*sleep_volt >= 12.0f && *sleep_volt <= 15.0f)
	{
		return 1;
	}
	return -1;
}

int8_t config_server_get_wakeup_volt(float *wakeup_volt)
{
	char *endptr;
	*wakeup_volt = strtof(device_config.wakeup_volt, &endptr);

	// Check for conversion errors
	if (*endptr != '\0' || endptr == device_config.wakeup_volt)
	{
		return -1;
	}

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

int8_t config_server_get_keep_alive(uint32_t *keep_alive)
{
    char *endptr;
    long kp_alive = strtol(device_config.keep_alive, &endptr, 10);
    
    // Check for conversion errors
    if (*endptr != '\0' || endptr == device_config.keep_alive)
	{
        return -1;
    }
    
    // Validate range
    if (kp_alive < 1 || kp_alive > 120)
	{
        return -1;
    }
    
    *keep_alive = (uint32_t)kp_alive;
    return 1;
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
	char *endptr;
	*alert_volt = strtof(device_config.batt_alert_volt, &endptr);

	// Check for conversion errors
	if (*endptr != '\0' || endptr == device_config.batt_alert_volt)
	{
		return -1;
	}

	if(*alert_volt >= 8.0f && *alert_volt <= 15.0f)
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
	xTimerStart( xrestartTimer, 0 );
	free((void *)resp_str);
    cJSON_Delete(root);
}

