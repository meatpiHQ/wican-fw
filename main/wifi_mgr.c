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
#include "freertos/queue.h"
#include "freertos/event_groups.h"
#include "esp_wifi.h"
#include "esp_system.h"
#include "esp_event.h"
#include "esp_mac.h"
#include "nvs_flash.h"
#include "driver/gpio.h"
#include "esp_log.h"
#include "esp_attr.h" // for EXT_RAM_ATTR
#include <string.h>
#include <stdlib.h>
#include "lwip/sockets.h"
#include <cJSON.h>
#include "wifi_mgr.h"
#include "dev_status.h"

static const char *TAG = "WiFi_Manager";
static esp_netif_t* ap_netif = NULL;
static esp_netif_t* sta_netif = NULL;
static TaskHandle_t reconnect_task_handle = NULL;
static EventGroupHandle_t wifi_event_group = NULL;
static StaticEventGroup_t wifi_event_group_buffer;
static wifi_mgr_config_t wifi_config;
static wifi_mgr_callbacks_t user_callbacks = {0};
static QueueHandle_t sta_ip_queue = NULL;
static QueueHandle_t ap_stations_queue = NULL;
// Static queue storage in PSRAM
static StaticQueue_t sta_ip_queue_struct;
static uint8_t sta_ip_queue_storage[1 * sizeof(char[16])];
static StaticQueue_t ap_stations_queue_struct;
static uint8_t ap_stations_queue_storage[1 * sizeof(uint16_t)];
// Ban/blacklist parameters for SSIDs that repeatedly fail authentication
#ifndef WIFI_MGR_AUTH_FAIL_THRESHOLD
#define WIFI_MGR_AUTH_FAIL_THRESHOLD 3
#endif
#ifndef WIFI_MGR_BAN_DURATION_MS
#define WIFI_MGR_BAN_DURATION_MS 600000 // 10 minutes
#endif
#ifndef WIFI_MGR_MAX_TRACKED_SSIDS
#define WIFI_MGR_MAX_TRACKED_SSIDS 12 // primary + up to 11 fallbacks
#endif

// Internal status structure (not exposed to external modules)
typedef struct {
    bool initialized;
    bool enabled;
    wifi_mgr_mode_t current_mode;
    bool sta_connected;
    bool ap_started;
    char sta_ip[16];
    int sta_retry_count;
    uint16_t ap_connected_stations;
    char last_attempted_ssid[33];
} wifi_mgr_status_t;

static wifi_mgr_status_t wifi_status = {0};
// Cursor for sequential non-scan attempts across primary and fallbacks
static int s_select_seq_cursor = -1;

static void wifi_event_handler(void* arg, esp_event_base_t event_base, int32_t event_id, void* event_data);
static void wifi_reconnect_task(void* pvParameters);
static wifi_country_t country_01 = {.cc = "01", .schan = 1, .nchan = 14, .policy = WIFI_COUNTRY_POLICY_AUTO};

// Per-SSID transient failure state
typedef struct {
    char ssid[33];
    uint8_t auth_fail_count;
    TickType_t banned_until; // absolute tick when ban expires; 0 => not banned
} ssid_fail_state_t;

static ssid_fail_state_t s_fail_states[WIFI_MGR_MAX_TRACKED_SSIDS] = {0};

static esp_err_t wifi_mgr_apply_sta_runtime_config(const char* ssid, const char* password, wifi_auth_mode_t auth_mode) {
    if (ssid == NULL || !ssid[0]) {
        return ESP_ERR_INVALID_ARG;
    }

    wifi_config_t sta_config = {0};
    sta_config.sta.threshold.authmode = auth_mode;
    sta_config.sta.rm_enabled = 1;
    sta_config.sta.btm_enabled = 1;
    sta_config.sta.scan_method = WIFI_ALL_CHANNEL_SCAN;
    sta_config.sta.sort_method = WIFI_CONNECT_AP_BY_SIGNAL;
    sta_config.sta.bssid_set = false;
    sta_config.sta.pmf_cfg.capable = true;
    sta_config.sta.pmf_cfg.required = false;

    strncpy((char*)sta_config.sta.ssid, ssid, sizeof(sta_config.sta.ssid) - 1);
    sta_config.sta.ssid[sizeof(sta_config.sta.ssid) - 1] = '\0';

    if (password != NULL) {
        strncpy((char*)sta_config.sta.password, password, sizeof(sta_config.sta.password) - 1);
        sta_config.sta.password[sizeof(sta_config.sta.password) - 1] = '\0';
    } else {
        sta_config.sta.password[0] = '\0';
    }

    return esp_wifi_set_config(WIFI_IF_STA, &sta_config);
}

static ssid_fail_state_t* wifi_mgr_fs_find_or_create(const char* ssid) {
    if (!ssid || !ssid[0]) return NULL;
    // Look for existing
    for (int i = 0; i < WIFI_MGR_MAX_TRACKED_SSIDS; ++i) {
        if (s_fail_states[i].ssid[0] && strcmp(s_fail_states[i].ssid, ssid) == 0) {
            return &s_fail_states[i];
        }
    }
    // Find empty slot
    for (int i = 0; i < WIFI_MGR_MAX_TRACKED_SSIDS; ++i) {
        if (s_fail_states[i].ssid[0] == '\0') {
            strncpy(s_fail_states[i].ssid, ssid, sizeof(s_fail_states[i].ssid) - 1);
            s_fail_states[i].ssid[sizeof(s_fail_states[i].ssid) - 1] = '\0';
            s_fail_states[i].auth_fail_count = 0;
            s_fail_states[i].banned_until = 0;
            return &s_fail_states[i];
        }
    }
    return NULL; // no slot
}

static void wifi_mgr_fs_clear(const char* ssid) {
    if (!ssid || !ssid[0]) return;
    for (int i = 0; i < WIFI_MGR_MAX_TRACKED_SSIDS; ++i) {
        if (s_fail_states[i].ssid[0] && strcmp(s_fail_states[i].ssid, ssid) == 0) {
            s_fail_states[i].auth_fail_count = 0;
            s_fail_states[i].banned_until = 0;
            return;
        }
    }
}

static bool wifi_mgr_fs_should_skip(const char* ssid) {
    if (!ssid || !ssid[0]) return false;
    TickType_t now = xTaskGetTickCount();
    for (int i = 0; i < WIFI_MGR_MAX_TRACKED_SSIDS; ++i) {
        if (s_fail_states[i].ssid[0] && strcmp(s_fail_states[i].ssid, ssid) == 0) {
            if (s_fail_states[i].banned_until != 0) {
                if ((int32_t)(s_fail_states[i].banned_until - now) > 0) {
                    return true; // still banned
                } else {
                    // expired
                    s_fail_states[i].banned_until = 0;
                }
            }
            return false;
        }
    }
    return false;
}

static void wifi_mgr_fs_on_auth_failure(const char* ssid) {
    ssid_fail_state_t* st = wifi_mgr_fs_find_or_create(ssid);
    if (!st) return;
    if (st->banned_until) return; // already banned, no need to count more
    st->auth_fail_count++;
    if (st->auth_fail_count >= WIFI_MGR_AUTH_FAIL_THRESHOLD) {
        st->auth_fail_count = 0;
        st->banned_until = xTaskGetTickCount() + pdMS_TO_TICKS(WIFI_MGR_BAN_DURATION_MS);
        ESP_LOGW(TAG, "Banning SSID '%s' for %d ms due to repeated auth failures", ssid, WIFI_MGR_BAN_DURATION_MS);
    } else {
        ESP_LOGW(TAG, "Auth failure %u/%u for SSID '%s'", st->auth_fail_count, WIFI_MGR_AUTH_FAIL_THRESHOLD, ssid);
    }
}

static void wifi_mgr_update_last_attempted_from_current_config(void) {
    wifi_config_t cur = {0};
    if (esp_wifi_get_config(WIFI_IF_STA, &cur) == ESP_OK) {
        snprintf(wifi_status.last_attempted_ssid, sizeof(wifi_status.last_attempted_ssid), "%s", (char*)cur.sta.ssid);
    } else {
        wifi_status.last_attempted_ssid[0] = '\0';
    }
}

static bool wifi_mgr_reason_is_auth_related(uint8_t reason) {
    // Conservative set of reasons that commonly indicate bad credentials/handshake issues
    switch (reason) {
        case WIFI_REASON_AUTH_EXPIRE:
        case WIFI_REASON_4WAY_HANDSHAKE_TIMEOUT:
        case WIFI_REASON_INVALID_PMKID:
        case WIFI_REASON_MIC_FAILURE:
        case WIFI_REASON_AUTH_FAIL:
            return true;
        default:
            return false;
    }
}

// Helper: check if an SSID exists in the scanned AP list
static bool wifi_mgr_ssid_present(const wifi_ap_record_t* ap_records, uint16_t ap_count, const char* ssid) {
    if (!ssid || !ssid[0] || !ap_records || ap_count == 0) return false;
    for (uint16_t i = 0; i < ap_count; ++i) {
        if (strcmp((const char*)ap_records[i].ssid, ssid) == 0) {
            return true;
        }
    }
    return false;
}

// Internal: Scan and choose primary or first available fallback, then connect
static void wifi_mgr_scan_select_and_connect(void) {
    // Only relevant when STA is part of the current mode
    if (!(wifi_config.mode == WIFI_MGR_MODE_STA || wifi_config.mode == WIFI_MGR_MODE_APSTA)) {
        esp_wifi_connect();
        return;
    }

    // If nothing to choose from (no primary and no fallbacks), just connect
    if ((wifi_config.sta_ssid[0] == '\0') && (wifi_config.fallback_count == 0)) {
        esp_wifi_connect();
        return;
    }

    // Determine current WiFi mode and switch if necessary to allow scanning
    wifi_mode_t current_mode;
    bool switched_mode = false;
    if (esp_wifi_get_mode(&current_mode) == ESP_OK) {
        if (current_mode == WIFI_MODE_AP) {
            ESP_LOGI(TAG, "Switching from AP to APSTA mode for selection scan");
            if (esp_wifi_set_mode(WIFI_MODE_APSTA) == ESP_OK) {
                switched_mode = true;
                vTaskDelay(pdMS_TO_TICKS(100));
            } else {
                ESP_LOGW(TAG, "Failed to switch to APSTA for scan; proceeding");
            }
        }
    }

    // Perform a blocking scan
    wifi_scan_config_t scan_config = {
        .ssid = NULL,
        .bssid = NULL,
        .channel = 0,
        .show_hidden = true,
        .scan_type = WIFI_SCAN_TYPE_ACTIVE,
        .scan_time.active.min = 100,
        .scan_time.active.max = 300
    };

    esp_err_t ret;
    const int max_attempts = 3;
    for (int a = 0; a < max_attempts; ++a) {
        ret = esp_wifi_scan_start(&scan_config, true);
        if (ret == ESP_OK) break;
        if (ret == ESP_ERR_WIFI_STATE) {
            // Backoff to let state settle
            int delay_ms = 200 + a * 200; // 200, 400, 600
            vTaskDelay(pdMS_TO_TICKS(delay_ms));
            continue;
        }
        break;
    }
    if (ret != ESP_OK) {
        wifi_mode_t dbg_mode; esp_wifi_get_mode(&dbg_mode);
        ESP_LOGW(TAG, "Scan failed (%s) in mode %d, attempting sequential connect without scan", esp_err_to_name(ret), dbg_mode);
        if (switched_mode) {
            esp_wifi_set_mode(WIFI_MODE_AP);
            vTaskDelay(pdMS_TO_TICKS(100));
        }
        // Sequential attempt: rotate over primary and fallbacks by index
        int total = (wifi_config.sta_ssid[0] ? 1 : 0) + wifi_config.fallback_count;
        if (total <= 0) {
            wifi_mgr_update_last_attempted_from_current_config();
            esp_wifi_connect();
            return;
        }
        for (int i = 0; i < total; ++i) {
            s_select_seq_cursor = (s_select_seq_cursor + 1) % total;
            const char* chosen_ssid = NULL;
            const char* chosen_pass = NULL;
            wifi_auth_mode_t chosen_auth = WIFI_AUTH_WPA2_PSK;
            int fb_index = -1;
            if (wifi_config.sta_ssid[0] && s_select_seq_cursor == 0) {
                chosen_ssid = wifi_config.sta_ssid;
                chosen_pass = wifi_config.sta_password;
                chosen_auth = wifi_config.sta_auth_mode;
                ESP_LOGI(TAG, "Sequential connect (no scan): primary SSID: %s", chosen_ssid);
            } else {
                fb_index = wifi_config.sta_ssid[0] ? (s_select_seq_cursor - 1) : s_select_seq_cursor;
                chosen_ssid = wifi_config.fallbacks[fb_index].ssid;
                chosen_pass = wifi_config.fallbacks[fb_index].password;
                chosen_auth = wifi_config.fallbacks[fb_index].auth_mode;
                ESP_LOGI(TAG, "Sequential connect (no scan): fallback[%d] SSID: %s", fb_index, chosen_ssid);
            }
            if (chosen_ssid && wifi_mgr_fs_should_skip(chosen_ssid)) {
                ESP_LOGW(TAG, "Skipping banned SSID '%s' in no-scan path", chosen_ssid);
                continue;
            }
            if (chosen_ssid) {
                if (wifi_mgr_apply_sta_runtime_config(chosen_ssid, chosen_pass ? chosen_pass : "", chosen_auth) != ESP_OK) {
                    ESP_LOGW(TAG, "Failed to set STA config for sequential connect; using current");
                }
                snprintf(wifi_status.last_attempted_ssid, sizeof(wifi_status.last_attempted_ssid), "%s", chosen_ssid);
                esp_wifi_connect();
                return;
            }
        }
        // No unbanned candidates; attempt next candidate ignoring ban
        for (int i = 0; i < total; ++i) {
            s_select_seq_cursor = (s_select_seq_cursor + 1) % total;
            const char* chosen_ssid = NULL;
            const char* chosen_pass = NULL;
            wifi_auth_mode_t chosen_auth = WIFI_AUTH_WPA2_PSK;
            int fb_index = -1;
            if (wifi_config.sta_ssid[0] && s_select_seq_cursor == 0) {
                chosen_ssid = wifi_config.sta_ssid;
                chosen_pass = wifi_config.sta_password;
                chosen_auth = wifi_config.sta_auth_mode;
                ESP_LOGW(TAG, "No unbanned SSID; retrying primary '%s' despite ban", chosen_ssid);
            } else {
                fb_index = wifi_config.sta_ssid[0] ? (s_select_seq_cursor - 1) : s_select_seq_cursor;
                chosen_ssid = wifi_config.fallbacks[fb_index].ssid;
                chosen_pass = wifi_config.fallbacks[fb_index].password;
                chosen_auth = wifi_config.fallbacks[fb_index].auth_mode;
                ESP_LOGW(TAG, "No unbanned SSID; retrying fallback[%d] '%s' despite ban", fb_index, chosen_ssid);
            }
            if (chosen_ssid) {
                if (wifi_mgr_apply_sta_runtime_config(chosen_ssid, chosen_pass ? chosen_pass : "", chosen_auth) != ESP_OK) {
                    ESP_LOGW(TAG, "Failed to set STA config for sequential connect; using current");
                }
                snprintf(wifi_status.last_attempted_ssid, sizeof(wifi_status.last_attempted_ssid), "%s", chosen_ssid);
                esp_wifi_connect();
                return;
            }
        }
        ESP_LOGW(TAG, "No candidates available to connect in no-scan path");
        return;
    }

    uint16_t ap_count = 0;
    esp_wifi_scan_get_ap_num(&ap_count);
    if (ap_count == 0) {
        ESP_LOGI(TAG, "No APs found; connecting with current STA config");
        if (switched_mode) {
            esp_wifi_set_mode(WIFI_MODE_AP);
            vTaskDelay(pdMS_TO_TICKS(100));
        }
        esp_wifi_connect();
        return;
    }

    wifi_ap_record_t *ap_records = malloc(sizeof(wifi_ap_record_t) * ap_count);
    if (!ap_records) {
        ESP_LOGW(TAG, "No memory for AP records; connecting with current STA config");
        if (switched_mode) {
            esp_wifi_set_mode(WIFI_MODE_AP);
            vTaskDelay(pdMS_TO_TICKS(100));
        }
        esp_wifi_connect();
        return;
    }

    ret = esp_wifi_scan_get_ap_records(&ap_count, ap_records);
    if (ret != ESP_OK) {
        ESP_LOGW(TAG, "Get AP records failed (%s); connecting with current STA config", esp_err_to_name(ret));
        free(ap_records);
        if (switched_mode) {
            esp_wifi_set_mode(WIFI_MODE_AP);
            vTaskDelay(pdMS_TO_TICKS(100));
        }
        esp_wifi_connect();
        return;
    }

    const char* chosen_ssid = NULL;
    const char* chosen_pass = NULL;
    wifi_auth_mode_t chosen_auth = wifi_config.sta_auth_mode;

    // Prefer primary if present
    if (wifi_mgr_ssid_present(ap_records, ap_count, wifi_config.sta_ssid) && !wifi_mgr_fs_should_skip(wifi_config.sta_ssid)) {
        chosen_ssid = wifi_config.sta_ssid;
        chosen_pass = wifi_config.sta_password;
        chosen_auth = wifi_config.sta_auth_mode;
        ESP_LOGI(TAG, "Selected primary SSID: %s", chosen_ssid);
    } else {
        if (wifi_mgr_ssid_present(ap_records, ap_count, wifi_config.sta_ssid) && wifi_mgr_fs_should_skip(wifi_config.sta_ssid)) {
            ESP_LOGW(TAG, "Primary SSID '%s' is banned currently; will try fallbacks", wifi_config.sta_ssid);
        }
        // Otherwise pick first available fallback by priority
        for (uint8_t i = 0; i < wifi_config.fallback_count; ++i) {
            if (wifi_mgr_ssid_present(ap_records, ap_count, wifi_config.fallbacks[i].ssid)) {
                if (wifi_mgr_fs_should_skip(wifi_config.fallbacks[i].ssid)) {
                    ESP_LOGW(TAG, "Skipping banned fallback[%u] SSID: %s", i, wifi_config.fallbacks[i].ssid);
                    continue;
                }
                chosen_ssid = wifi_config.fallbacks[i].ssid;
                chosen_pass = wifi_config.fallbacks[i].password;
                chosen_auth = wifi_config.fallbacks[i].auth_mode;
                ESP_LOGI(TAG, "Selected fallback[%u] SSID: %s", i, chosen_ssid);
                break;
            }
        }
        // If still none chosen, allow banned choice: prefer primary if present, else first present fallback
        if (!chosen_ssid) {
            if (wifi_mgr_ssid_present(ap_records, ap_count, wifi_config.sta_ssid)) {
                chosen_ssid = wifi_config.sta_ssid;
                chosen_pass = wifi_config.sta_password;
                chosen_auth = wifi_config.sta_auth_mode;
                ESP_LOGW(TAG, "All candidates banned; selecting primary '%s' anyway", chosen_ssid);
            } else {
                for (uint8_t i = 0; i < wifi_config.fallback_count; ++i) {
                    if (wifi_mgr_ssid_present(ap_records, ap_count, wifi_config.fallbacks[i].ssid)) {
                        chosen_ssid = wifi_config.fallbacks[i].ssid;
                        chosen_pass = wifi_config.fallbacks[i].password;
                        chosen_auth = wifi_config.fallbacks[i].auth_mode;
                        ESP_LOGW(TAG, "All candidates banned; selecting fallback[%u] '%s' anyway", i, chosen_ssid);
                        break;
                    }
                }
            }
        }
    }

    if (chosen_ssid) {
        // Update running STA config and connect
        if (wifi_mgr_apply_sta_runtime_config(chosen_ssid, chosen_pass ? chosen_pass : "", chosen_auth) != ESP_OK) {
            ESP_LOGW(TAG, "Failed to set selected STA config; connecting with current config");
            wifi_mgr_update_last_attempted_from_current_config();
            esp_wifi_connect();
        } else {
            snprintf(wifi_status.last_attempted_ssid, sizeof(wifi_status.last_attempted_ssid), "%s", chosen_ssid);
            esp_wifi_connect();
        }
    } else {
        ESP_LOGI(TAG, "No preferred networks present; connecting with current STA config if any");
        wifi_mgr_update_last_attempted_from_current_config();
        if (wifi_status.last_attempted_ssid[0]) {
            esp_wifi_connect();
        } else {
            ESP_LOGW(TAG, "Current STA config SSID is empty; deferring connect");
        }
    }

    free(ap_records);
    if (switched_mode) {
        esp_wifi_set_mode(WIFI_MODE_AP);
        vTaskDelay(pdMS_TO_TICKS(100));
    }
}
/**
 * Initialize WiFi Manager with configuration
 */
esp_err_t wifi_mgr_init(wifi_mgr_config_t* config) {
    if (wifi_status.initialized) {
        ESP_LOGW(TAG, "WiFi Manager already initialized");
        return ESP_OK;
    }
    
    if (config == NULL) {
        ESP_LOGE(TAG, "Configuration cannot be NULL");
        return ESP_ERR_INVALID_ARG;
    }
    
    ESP_LOGI(TAG, "Initializing WiFi Manager...");
    
    // Copy configuration
    memcpy(&wifi_config, config, sizeof(wifi_mgr_config_t));
    
    // Initialize network interface (CRITICAL - must be first!)
    esp_err_t ret = esp_netif_init();
    if (ret != ESP_OK) {
        ESP_LOGE(TAG, "Failed to initialize network interface: %s", esp_err_to_name(ret));
        return ret;
    }
    
    // Create event group
    wifi_event_group = xEventGroupCreateStatic(&wifi_event_group_buffer);
    if (wifi_event_group == NULL) {
        ESP_LOGE(TAG, "Failed to create event group");
        return ESP_ERR_NO_MEM;
    }
    
    // Create status queues (static, backed by PSRAM)
    sta_ip_queue = xQueueCreateStatic(
        1,                     // uxQueueLength
        sizeof(char[16]),      // uxItemSize
        sta_ip_queue_storage,  // pucQueueStorageBuffer
        &sta_ip_queue_struct   // pxQueueBuffer
    );
    ap_stations_queue = xQueueCreateStatic(
        1,                            // uxQueueLength
        sizeof(uint16_t),             // uxItemSize
        ap_stations_queue_storage,    // pucQueueStorageBuffer
        &ap_stations_queue_struct     // pxQueueBuffer
    );

    if (sta_ip_queue == NULL || ap_stations_queue == NULL) {
        ESP_LOGE(TAG, "Failed to create status queues");
        if (sta_ip_queue) vQueueDelete(sta_ip_queue);
        if (ap_stations_queue) vQueueDelete(ap_stations_queue);
        esp_event_handler_unregister(WIFI_EVENT, ESP_EVENT_ANY_ID, &wifi_event_handler);
        esp_wifi_deinit();
        esp_netif_destroy(ap_netif);
        esp_netif_destroy(sta_netif);
        vEventGroupDelete(wifi_event_group);
        return ESP_ERR_NO_MEM;
    }

    // Initialize with default values
    char default_ip[16] = "";
    uint16_t default_stations = 0;
    xQueueOverwrite(sta_ip_queue, default_ip);
    xQueueOverwrite(ap_stations_queue, &default_stations);

    // Initialize network interfaces
    ap_netif = esp_netif_create_default_wifi_ap();
    sta_netif = esp_netif_create_default_wifi_sta();
    
    if (ap_netif == NULL || sta_netif == NULL) {
        ESP_LOGE(TAG, "Failed to create network interfaces");
        if (ap_netif) esp_netif_destroy(ap_netif);
        if (sta_netif) esp_netif_destroy(sta_netif);
        if (sta_ip_queue) vQueueDelete(sta_ip_queue);
        if (ap_stations_queue) vQueueDelete(ap_stations_queue);
        vEventGroupDelete(wifi_event_group);
        return ESP_ERR_NO_MEM;
    }
    
    // Initialize WiFi
    wifi_init_config_t cfg = WIFI_INIT_CONFIG_DEFAULT();
    ret = esp_wifi_init(&cfg);
    if (ret != ESP_OK) {
        ESP_LOGE(TAG, "WiFi init failed: %s", esp_err_to_name(ret));
        esp_netif_destroy(ap_netif);
        esp_netif_destroy(sta_netif);
        if (sta_ip_queue) vQueueDelete(sta_ip_queue);
        if (ap_stations_queue) vQueueDelete(ap_stations_queue);
        vEventGroupDelete(wifi_event_group);
        return ret;
    }
    esp_wifi_set_country(&country_01);
    // Register event handlers
    ret = esp_event_handler_register(WIFI_EVENT, ESP_EVENT_ANY_ID, &wifi_event_handler, NULL);
    if (ret != ESP_OK) {
        ESP_LOGE(TAG, "Failed to register WiFi event handler: %s", esp_err_to_name(ret));
        esp_wifi_deinit();
        esp_netif_destroy(ap_netif);
        esp_netif_destroy(sta_netif);
        if (sta_ip_queue) vQueueDelete(sta_ip_queue);
        if (ap_stations_queue) vQueueDelete(ap_stations_queue);
        vEventGroupDelete(wifi_event_group);
        return ret;
    }
    
    ret = esp_event_handler_register(IP_EVENT, IP_EVENT_STA_GOT_IP, &wifi_event_handler, NULL);
    if (ret != ESP_OK) {
        ESP_LOGE(TAG, "Failed to register IP event handler: %s", esp_err_to_name(ret));
        esp_event_handler_unregister(WIFI_EVENT, ESP_EVENT_ANY_ID, &wifi_event_handler);
        esp_wifi_deinit();
        esp_netif_destroy(ap_netif);
        esp_netif_destroy(sta_netif);
        if (sta_ip_queue) vQueueDelete(sta_ip_queue);
        if (ap_stations_queue) vQueueDelete(ap_stations_queue);
        vEventGroupDelete(wifi_event_group);
        return ret;
    }
    
    // Set WiFi storage to RAM
    ret = esp_wifi_set_storage(WIFI_STORAGE_RAM);
    if (ret != ESP_OK) {
        ESP_LOGW(TAG, "Failed to set WiFi storage mode: %s", esp_err_to_name(ret));
        // Continue anyway, this is not critical
    }
    
    // Set power save mode
    ret = esp_wifi_set_ps(wifi_config.power_save_mode);
    if (ret != ESP_OK) {
        ESP_LOGW(TAG, "Failed to set power save mode: %s", esp_err_to_name(ret));
        // Continue anyway, this is not critical
    }
    
    wifi_status.initialized = true;
    wifi_status.current_mode = WIFI_MGR_MODE_OFF;
    
    ESP_LOGI(TAG, "WiFi Manager initialized successfully");
    return ESP_OK;
}

/**
 * Deinitialize WiFi Manager
 */
esp_err_t wifi_mgr_deinit(void) {
    if (!wifi_status.initialized) {
        return ESP_OK;
    }
    
    ESP_LOGI(TAG, "Deinitializing WiFi Manager...");
    
    // Set initialized to false immediately to prevent re-entry
    wifi_status.initialized = false;
    
    // Clear ALL event bits first
    if (wifi_event_group != NULL) {
        xEventGroupClearBits(wifi_event_group, 0xFFFFFF);
    }
    
    // Disable WiFi first
    wifi_mgr_disable();
    
    // Delete reconnect task if exists
    if (reconnect_task_handle != NULL) {
        vTaskDelete(reconnect_task_handle);
        reconnect_task_handle = NULL;
    }
    
    // Unregister event handlers
    esp_event_handler_unregister(WIFI_EVENT, ESP_EVENT_ANY_ID, &wifi_event_handler);
    esp_event_handler_unregister(IP_EVENT, IP_EVENT_STA_GOT_IP, &wifi_event_handler);
    
    // Deinitialize WiFi
    esp_err_t ret = esp_wifi_deinit();
    if (ret != ESP_OK) {
        ESP_LOGW(TAG, "WiFi deinit failed: %s", esp_err_to_name(ret));
    }
    
    // Clean up network interfaces
    if (ap_netif) {
        esp_netif_destroy(ap_netif);
        ap_netif = NULL;
    }
    if (sta_netif) {
        esp_netif_destroy(sta_netif);
        sta_netif = NULL;
    }
    
    // Clean up queues
    if (sta_ip_queue != NULL) {
        vQueueDelete(sta_ip_queue);
        sta_ip_queue = NULL;
    }
    if (ap_stations_queue != NULL) {
        vQueueDelete(ap_stations_queue);
        ap_stations_queue = NULL;
    }
    
    // DON'T delete the event group - preserve it for status monitoring
    // The event group will be reused on next init
    
    // Clear all status and config
    memset(&wifi_status, 0, sizeof(wifi_status));
    memset(&wifi_config, 0, sizeof(wifi_config));
    
    ESP_LOGI(TAG, "WiFi Manager deinitialized");
    return ESP_OK;
}

/**
 * Enable WiFi with current configuration
 */
esp_err_t wifi_mgr_enable(void) {
    if (!wifi_status.initialized) {
        ESP_LOGE(TAG, "WiFi Manager not initialized");
        return ESP_ERR_INVALID_STATE;
    }
    
    if (wifi_status.enabled && wifi_status.current_mode == wifi_config.mode) {
        ESP_LOGW(TAG, "WiFi already enabled with requested mode %d", wifi_config.mode);
        return ESP_OK;
    }
    
    // If enabled but different mode, disable first
    if (wifi_status.enabled) {
        ESP_LOGI(TAG, "WiFi enabled but mode different, disabling first...");
        wifi_mgr_disable();
    }
    
    ESP_LOGI(TAG, "Enabling WiFi with mode %d...", wifi_config.mode);
    
    esp_err_t ret = ESP_OK;
    
    // Configure WiFi based on mode
    switch (wifi_config.mode) {
        case WIFI_MGR_MODE_STA:
            ret = esp_wifi_set_mode(WIFI_MODE_STA);
            if(sta_netif){
                esp_netif_set_default_netif(sta_netif);
            }
            break;
        case WIFI_MGR_MODE_AP:
            ret = esp_wifi_set_mode(WIFI_MODE_AP);
            break;
        case WIFI_MGR_MODE_APSTA:
            ret = esp_wifi_set_mode(WIFI_MODE_APSTA);
            break;
        default:
            ESP_LOGE(TAG, "Invalid WiFi mode");
            return ESP_ERR_INVALID_ARG;
    }
    
    if (ret != ESP_OK) {
        ESP_LOGE(TAG, "Failed to set WiFi mode");
        return ret;
    }
    
    // Configure STA if needed
    if (wifi_config.mode == WIFI_MGR_MODE_STA || wifi_config.mode == WIFI_MGR_MODE_APSTA) {
        wifi_config_t sta_config = {
            .sta = {
                .threshold.authmode = wifi_config.sta_auth_mode,
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
        strcpy((char*)sta_config.sta.ssid, wifi_config.sta_ssid);
        strcpy((char*)sta_config.sta.password, wifi_config.sta_password);
        
        ret = esp_wifi_set_config(WIFI_IF_STA, &sta_config);
        if (ret != ESP_OK) {
            ESP_LOGE(TAG, "Failed to set STA config");
            return ret;
        }
    }
    
    // Configure AP if needed
    if (wifi_config.mode == WIFI_MGR_MODE_AP || wifi_config.mode == WIFI_MGR_MODE_APSTA) {
        wifi_config_t ap_config = {
            .ap = {
                .channel = wifi_config.ap_channel,
                .max_connection = wifi_config.ap_max_connections,
                .authmode = wifi_config.ap_auth_mode,
                .beacon_interval = 100,
            },
        };
        strcpy((char*)ap_config.ap.ssid, wifi_config.ap_ssid);
        strcpy((char*)ap_config.ap.password, wifi_config.ap_password);
        
        ret = esp_wifi_set_config(WIFI_IF_AP, &ap_config);
        if (ret != ESP_OK) {
            ESP_LOGE(TAG, "Failed to set AP config");
            return ret;
        }
        
        // Set AP IP configuration
        esp_netif_ip_info_t ip_info;
        IP4_ADDR(&ip_info.ip, 192, 168, 0, 10);
        IP4_ADDR(&ip_info.gw, 192, 168, 0, 10);
        IP4_ADDR(&ip_info.netmask, 255, 255, 255, 0);
        
        esp_netif_dhcps_stop(ap_netif);
        esp_netif_set_ip_info(ap_netif, &ip_info);
        esp_netif_dhcps_start(ap_netif);
    }
    
    // Start WiFi
    ret = esp_wifi_start();
    if (ret != ESP_OK) {
        ESP_LOGE(TAG, "Failed to start WiFi");
        return ret;
    }
    
    // Update status BEFORE creating reconnect task
    wifi_status.enabled = true;
    wifi_status.current_mode = wifi_config.mode;
    
    // Set event bits BEFORE creating reconnect task
    xEventGroupSetBits(wifi_event_group, WIFI_INIT_BIT | WIFI_ENABLED_BIT);
    
    // Create reconnect task if STA auto-reconnect is enabled
    if ((wifi_config.mode == WIFI_MGR_MODE_STA || wifi_config.mode == WIFI_MGR_MODE_APSTA) 
        && wifi_config.sta_auto_reconnect && reconnect_task_handle == NULL) {
        
        xTaskCreate(wifi_reconnect_task, "wifi_reconnect", 4096, NULL, 5, &reconnect_task_handle);
    }
    
    ESP_LOGI(TAG, "WiFi enabled with mode: %d", wifi_config.mode);
    return ESP_OK;
}

/**
 * Disable WiFi
 */
esp_err_t wifi_mgr_disable(void) {
    if (!wifi_status.enabled) {
        return ESP_OK;
    }
    
    ESP_LOGI(TAG, "Disabling WiFi...");
    
    // CRITICAL: Clear event bits FIRST to prevent reconnect task from acting
    xEventGroupClearBits(wifi_event_group, WIFI_INIT_BIT | WIFI_ENABLED_BIT | WIFI_CONNECTED_BIT | WIFI_AP_STARTED_BIT | WIFI_DISCONNECTED_BIT | WIFI_STA_GOT_IP_BIT);
    
    // Delete reconnect task
    if (reconnect_task_handle != NULL) {
        vTaskDelete(reconnect_task_handle);
        reconnect_task_handle = NULL;
    }
    
    // Disconnect STA if connected (but don't wait for event since we cleared bits)
    if (wifi_status.sta_connected) {
        ESP_LOGI(TAG, "Disconnecting STA...");
        esp_wifi_disconnect();
        vTaskDelay(pdMS_TO_TICKS(100)); // Just a short delay for the disconnect command
    }
    
    // Stop WiFi
    esp_err_t ret = esp_wifi_stop();
    if (ret != ESP_OK) {
        ESP_LOGE(TAG, "WiFi stop failed: %s", esp_err_to_name(ret));
    }
    
    // Allow time for complete shutdown
    vTaskDelay(pdMS_TO_TICKS(200));
    
    // Update status
    wifi_status.enabled = false;
    wifi_status.sta_connected = false;
    wifi_status.ap_started = false;
    wifi_status.current_mode = WIFI_MGR_MODE_OFF;
    wifi_status.ap_connected_stations = 0;
    memset(wifi_status.sta_ip, 0, sizeof(wifi_status.sta_ip));
    
    // Clear device status bits
    dev_status_clear_sta_connected();
    dev_status_clear_sta_enabled();
    dev_status_clear_ap_enabled();
    
    // Update queues
    char empty_ip[16] = "";
    uint16_t zero_stations = 0;
    if (sta_ip_queue) xQueueOverwrite(sta_ip_queue, empty_ip);
    if (ap_stations_queue) xQueueOverwrite(ap_stations_queue, &zero_stations);
    
    ESP_LOGI(TAG, "WiFi disabled");
    return ESP_OK;
}

/**
 * Set WiFi mode
 */
esp_err_t wifi_mgr_set_mode(wifi_mgr_mode_t mode) {
    wifi_config.mode = mode;

    if (!wifi_status.enabled) {
        wifi_status.current_mode = mode;
    }
    
    if (wifi_status.enabled) {
        ESP_LOGI(TAG, "Mode change requested to %d, restarting WiFi...", mode);
        
        // CRITICAL: Clear event bits FIRST to prevent reconnect task interference
        xEventGroupClearBits(wifi_event_group, WIFI_INIT_BIT | WIFI_ENABLED_BIT | WIFI_CONNECTED_BIT | WIFI_AP_STARTED_BIT | WIFI_DISCONNECTED_BIT | WIFI_STA_GOT_IP_BIT);
        
        // Delete reconnect task if it exists
        if (reconnect_task_handle != NULL) {
            vTaskDelete(reconnect_task_handle);
            reconnect_task_handle = NULL;
        }
        
        // Disconnect STA if connected (without waiting for events)
        if (wifi_status.sta_connected) {
            ESP_LOGI(TAG, "Disconnecting STA before mode change...");
            esp_wifi_disconnect();
            vTaskDelay(pdMS_TO_TICKS(100)); // Brief delay for disconnect command
        }
        
        // Stop WiFi
        esp_wifi_stop();
        vTaskDelay(pdMS_TO_TICKS(200)); // Allow time for cleanup
        
        // Clear status before restart
        wifi_status.enabled = false;
        wifi_status.sta_connected = false;
        wifi_status.ap_started = false;
        wifi_status.current_mode = WIFI_MGR_MODE_OFF;
        wifi_status.ap_connected_stations = 0;
        memset(wifi_status.sta_ip, 0, sizeof(wifi_status.sta_ip));
        
        // Clear device status bits
        dev_status_clear_sta_connected();
        dev_status_clear_sta_enabled();
        dev_status_clear_ap_enabled();
        
        // Update queues
        char empty_ip[16] = "";
        uint16_t zero_stations = 0;
        if (sta_ip_queue) xQueueOverwrite(sta_ip_queue, empty_ip);
        if (ap_stations_queue) xQueueOverwrite(ap_stations_queue, &zero_stations);
        
        // Restart with new mode
        return wifi_mgr_enable();
    }
    
    return ESP_OK;
}

/**
 * Get current WiFi mode
 */
wifi_mgr_mode_t wifi_mgr_get_mode(void) {
    return wifi_status.current_mode;
}

/**
 * Configure STA settings
 */
esp_err_t wifi_mgr_set_sta_config(const char* ssid, const char* password, wifi_auth_mode_t auth_mode) {
    if (ssid == NULL || password == NULL) {
        return ESP_ERR_INVALID_ARG;
    }
    
    if (strlen(ssid) >= sizeof(wifi_config.sta_ssid) || strlen(password) >= sizeof(wifi_config.sta_password)) {
        return ESP_ERR_INVALID_SIZE;
    }
    
    strcpy(wifi_config.sta_ssid, ssid);
    strcpy(wifi_config.sta_password, password);
    wifi_config.sta_auth_mode = auth_mode;
    
    ESP_LOGI(TAG, "STA config updated: SSID=%s", ssid);
    
    // If WiFi is already enabled and in STA mode, update the running config
    if (wifi_status.enabled && 
        (wifi_status.current_mode == WIFI_MGR_MODE_STA || 
         wifi_status.current_mode == WIFI_MGR_MODE_APSTA)) {

        ESP_LOGI(TAG, "Updating running STA configuration...");
        
        wifi_config_t sta_config = {
            .sta = {
                .threshold.authmode = auth_mode,
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
        strcpy((char*)sta_config.sta.ssid, ssid);
        strcpy((char*)sta_config.sta.password, password);
        
        esp_err_t ret = esp_wifi_set_config(WIFI_IF_STA, &sta_config);
        if (ret != ESP_OK) {
            ESP_LOGE(TAG, "Failed to update running STA config: %s", esp_err_to_name(ret));
            return ret;
        }
        
        ESP_LOGI(TAG, "Running STA configuration updated successfully");
    }
    
    return ESP_OK;
}

/**
 * Set STA auto-reconnect behavior
 */
esp_err_t wifi_mgr_set_sta_auto_reconnect(bool enable, int max_retry) {
    wifi_config.sta_auto_reconnect = enable;
    wifi_config.sta_max_retry = max_retry;
    
    ESP_LOGI(TAG, "STA auto-reconnect: %s, max_retry: %d", enable ? "enabled" : "disabled", max_retry);
    return ESP_OK;
}

/**
 * Connect to STA network
 */
esp_err_t wifi_mgr_sta_connect(void) {
    if (!wifi_status.enabled) {
        return ESP_ERR_INVALID_STATE;
    }
    wifi_mgr_update_last_attempted_from_current_config();
    return esp_wifi_connect();
}

/**
 * Disconnect from STA network
 */
esp_err_t wifi_mgr_sta_disconnect(void) {
    if (!wifi_status.enabled) {
        return ESP_ERR_INVALID_STATE;
    }
    
    // Check if STA is actually connected
    if (!wifi_status.sta_connected) {
        ESP_LOGW(TAG, "STA not connected, disconnect not needed");
        return ESP_OK;
    }
    
    return esp_wifi_disconnect();
}

/**
 * Configure AP settings
 */
esp_err_t wifi_mgr_set_ap_config(const char* ssid, const char* password, uint8_t channel, uint8_t max_connections) {
    if (ssid == NULL || password == NULL) {
        return ESP_ERR_INVALID_ARG;
    }
    
    if (strlen(ssid) >= sizeof(wifi_config.ap_ssid) || strlen(password) >= sizeof(wifi_config.ap_password)) {
        return ESP_ERR_INVALID_SIZE;
    }
    
    strcpy(wifi_config.ap_ssid, ssid);
    strcpy(wifi_config.ap_password, password);
    wifi_config.ap_channel = channel;
    wifi_config.ap_max_connections = max_connections;
    
    ESP_LOGI(TAG, "AP config updated: SSID=%s, Channel=%d", ssid, channel);
    return ESP_OK;
}

/**
 * Set AP auto-disable behavior
 */
esp_err_t wifi_mgr_set_ap_auto_disable(bool enable) {
    wifi_config.ap_auto_disable = enable;
    ESP_LOGI(TAG, "AP auto-disable: %s", enable ? "enabled" : "disabled");
    return ESP_OK;
}

/**
 * Check if STA is connected
 */
bool wifi_mgr_is_sta_connected(void) {
    if (wifi_event_group == NULL) {
        return false;
    }
    EventBits_t bits = xEventGroupGetBits(wifi_event_group);
    return (bits & WIFI_CONNECTED_BIT) != 0;
}

/**
 * Check if AP is started
 */
bool wifi_mgr_is_ap_started(void) {
    if (wifi_event_group == NULL) {
        return false;
    }
    EventBits_t bits = xEventGroupGetBits(wifi_event_group);
    return (bits & WIFI_AP_STARTED_BIT) != 0;
}

/**
 * Check if WiFi is enabled
 */
bool wifi_mgr_is_enabled(void) {
    if (wifi_event_group == NULL) {
        return false;
    }
    EventBits_t bits = xEventGroupGetBits(wifi_event_group);
    return (bits & WIFI_ENABLED_BIT) != 0;
}

/**
 * Get STA IP address
 */
char* wifi_mgr_get_sta_ip(void) {
    static char sta_ip_buffer[16] = "";
    
    if (sta_ip_queue != NULL) {
        xQueuePeek(sta_ip_queue, sta_ip_buffer, 0);
    }
    return sta_ip_buffer;
}

/**
 * Get number of connected AP stations
 */
uint16_t wifi_mgr_get_ap_connected_stations(void) {
    if (ap_stations_queue == NULL) {
        return 0;
    }
    
    uint16_t count = 0;
    xQueuePeek(ap_stations_queue, &count, 0);
    return count;
}

/**
 * Get event group handle for external status monitoring
 */
EventGroupHandle_t wifi_mgr_get_event_group(void) {
    return wifi_event_group;
}

/**
 * Scan for available networks
 */
char* wifi_mgr_scan_networks(void) {
    if (!wifi_status.enabled) {
        ESP_LOGE(TAG, "WiFi not enabled");
        return NULL;
    }
    
    // Check if current mode supports scanning
    wifi_mode_t current_mode;
    esp_err_t ret = esp_wifi_get_mode(&current_mode);
    if (ret != ESP_OK) {
        ESP_LOGE(TAG, "Failed to get WiFi mode: %s", esp_err_to_name(ret));
        return NULL;
    }

    if (current_mode == WIFI_MODE_NULL) {
        ESP_LOGE(TAG, "WiFi not started");
        return NULL;
    }

    // If in AP-only mode, temporarily switch to APSTA for scanning
    bool switched_mode = false;
    if (current_mode == WIFI_MODE_AP) {
        ESP_LOGI(TAG, "Switching from AP to APSTA mode for scan");
        ret = esp_wifi_set_mode(WIFI_MODE_APSTA);
        if (ret != ESP_OK) {
            ESP_LOGE(TAG, "Failed to switch to APSTA mode: %s", esp_err_to_name(ret));
            return NULL;
        }
        switched_mode = true;
        // Give time for mode switch
        vTaskDelay(pdMS_TO_TICKS(100));
    }

    wifi_scan_config_t scan_config = {
        .ssid = NULL,
        .bssid = NULL,
        .channel = 0,
        .show_hidden = true,
        .scan_type = WIFI_SCAN_TYPE_ACTIVE,
        .scan_time.active.min = 100,
        .scan_time.active.max = 300
    };

    ESP_LOGI(TAG, "Starting WiFi scan");

    ret = esp_wifi_scan_start(&scan_config, true);
    if (ret != ESP_OK) {
        ESP_LOGE(TAG, "WiFi scan start failed: %s", esp_err_to_name(ret));
        // Restore mode if we switched
        if (switched_mode) {
            esp_wifi_set_mode(WIFI_MODE_AP);
            vTaskDelay(pdMS_TO_TICKS(100));
        }
        // Return empty result instead of NULL for failed scans
        cJSON *empty_json = cJSON_CreateObject();
        cJSON *empty_array = cJSON_CreateArray();
        cJSON_AddItemToObject(empty_json, "networks", empty_array);
        cJSON_AddStringToObject(empty_json, "error", "Scan failed");
        char *json_str = cJSON_Print(empty_json);
        cJSON_Delete(empty_json);
        return json_str;
    }
    
    uint16_t ap_count = 0;
    esp_wifi_scan_get_ap_num(&ap_count);
    
    if (ap_count == 0) {
        ESP_LOGI(TAG, "No access points found");
        cJSON *empty_json = cJSON_CreateObject();
        cJSON *empty_array = cJSON_CreateArray();
        cJSON_AddItemToObject(empty_json, "networks", empty_array);
        char *json_str = cJSON_Print(empty_json);
        cJSON_Delete(empty_json);
        return json_str;
    }
    
    wifi_ap_record_t *ap_records = malloc(sizeof(wifi_ap_record_t) * ap_count);
    if (ap_records == NULL) {
        ESP_LOGE(TAG, "Failed to allocate memory for AP records");
        return NULL;
    }
    
    ret = esp_wifi_scan_get_ap_records(&ap_count, ap_records);
    if (ret != ESP_OK) {
        ESP_LOGE(TAG, "Failed to get AP records: %s", esp_err_to_name(ret));
        free(ap_records);
        return NULL;
    }
    
    cJSON *root = cJSON_CreateObject();
    cJSON *networks = cJSON_CreateArray();
    cJSON_AddItemToObject(root, "networks", networks);
    
    for (int i = 0; i < ap_count; i++) {
        cJSON *ap = cJSON_CreateObject();
        
        cJSON_AddStringToObject(ap, "ssid", (char*)ap_records[i].ssid);
        cJSON_AddNumberToObject(ap, "rssi", ap_records[i].rssi);
        cJSON_AddNumberToObject(ap, "channel", ap_records[i].primary);
        
        const char *auth_mode;
        switch (ap_records[i].authmode) {
            case WIFI_AUTH_OPEN: auth_mode = "OPEN"; break;
            case WIFI_AUTH_WEP: auth_mode = "WEP"; break;
            case WIFI_AUTH_WPA_PSK: auth_mode = "WPA_PSK"; break;
            case WIFI_AUTH_WPA2_PSK: auth_mode = "WPA2_PSK"; break;
            case WIFI_AUTH_WPA_WPA2_PSK: auth_mode = "WPA_WPA2_PSK"; break;
            case WIFI_AUTH_WPA3_PSK: auth_mode = "WPA3_PSK"; break;
            case WIFI_AUTH_WPA2_WPA3_PSK: auth_mode = "WPA2_WPA3_PSK"; break;
            default: auth_mode = "UNKNOWN"; break;
        }
        cJSON_AddStringToObject(ap, "auth_mode", auth_mode);
        
        char mac_str[18];
        snprintf(mac_str, sizeof(mac_str), "%02x:%02x:%02x:%02x:%02x:%02x",
                 ap_records[i].bssid[0], ap_records[i].bssid[1], ap_records[i].bssid[2],
                 ap_records[i].bssid[3], ap_records[i].bssid[4], ap_records[i].bssid[5]);
        cJSON_AddStringToObject(ap, "bssid", mac_str);
        
        cJSON_AddItemToArray(networks, ap);
    }
    
    char *json_str = cJSON_PrintUnformatted(root);
    
    cJSON_Delete(root);
    free(ap_records);
    
    ESP_LOGI(TAG, "Scan completed, found %d access points", ap_count);
    return json_str;
}

/**
 * Set callback functions
 */
esp_err_t wifi_mgr_set_callbacks(wifi_mgr_callbacks_t* callbacks) {
    if (callbacks != NULL) {
        memcpy(&user_callbacks, callbacks, sizeof(wifi_mgr_callbacks_t));
    } else {
        memset(&user_callbacks, 0, sizeof(wifi_mgr_callbacks_t));
    }
    return ESP_OK;
}

/**
 * Set power save mode
 */
esp_err_t wifi_mgr_set_power_save_mode(wifi_ps_type_t mode) {
    wifi_config.power_save_mode = mode;
    if (wifi_status.enabled) {
        return esp_wifi_set_ps(mode);
    }
    return ESP_OK;
}

/**
 * WiFi event handler
 */
static void wifi_event_handler(void* arg, esp_event_base_t event_base, int32_t event_id, void* event_data) {
    if (event_base == WIFI_EVENT) {
        switch (event_id) {
            case WIFI_EVENT_STA_START:
                ESP_LOGI(TAG, "STA started");
                dev_status_set_sta_enabled();
                if (wifi_config.sta_auto_reconnect) {
                    // If fallbacks configured, scan and select; else connect directly
                    if (wifi_config.fallback_count > 0) {
                        wifi_mgr_scan_select_and_connect();
                    } else {
                        wifi_mgr_update_last_attempted_from_current_config();
                        esp_wifi_connect();
                    }
                }
                break;
                
            case WIFI_EVENT_STA_CONNECTED:
                ESP_LOGI(TAG, "STA connected");
                
                break;
                
            case WIFI_EVENT_STA_DISCONNECTED:
                ESP_LOGI(TAG, "STA disconnected");
                wifi_status.sta_connected = false;
                memset(wifi_status.sta_ip, 0, sizeof(wifi_status.sta_ip));
                // Update queue with empty IP
                if (sta_ip_queue) xQueueOverwrite(sta_ip_queue, wifi_status.sta_ip);
                xEventGroupClearBits(wifi_event_group, WIFI_CONNECTED_BIT | WIFI_STA_GOT_IP_BIT | WIFI_STA_AP_OVERLAP_BIT);
                xEventGroupSetBits(wifi_event_group, WIFI_DISCONNECTED_BIT);
                dev_status_clear_sta_connected();

                if (event_data) {
                    wifi_event_sta_disconnected_t* e = (wifi_event_sta_disconnected_t*)event_data;
                    ESP_LOGW(TAG, "Disconnect reason: %d, last SSID: '%s'", e->reason, wifi_status.last_attempted_ssid);
                    if (wifi_status.last_attempted_ssid[0] && wifi_mgr_reason_is_auth_related(e->reason)) {
                        wifi_mgr_fs_on_auth_failure(wifi_status.last_attempted_ssid);
                    }
                }
                
                if (user_callbacks.sta_disconnected) {
                    user_callbacks.sta_disconnected();
                }
                
                // Only switch back to APSTA if we're actually in STA mode and not in a mode transition
                if (wifi_config.mode == WIFI_MGR_MODE_APSTA && wifi_config.ap_auto_disable) {
                    wifi_mode_t current_mode;
                    if (esp_wifi_get_mode(&current_mode) == ESP_OK && current_mode == WIFI_MODE_STA) {
                        // Add a small delay to avoid rapid mode switching
                        vTaskDelay(pdMS_TO_TICKS(1000));
                        // Check if we're still disconnected before switching back
                        if (!wifi_status.sta_connected) {
                            ESP_LOGI(TAG, "Switching back to APSTA mode");
                            esp_err_t ret = esp_wifi_set_mode(WIFI_MODE_APSTA);
                            if (ret != ESP_OK) {
                                ESP_LOGE(TAG, "Failed to switch back to APSTA mode: %s", esp_err_to_name(ret));
                            }
                        }
                    }
                }
                break;
                
            case WIFI_EVENT_AP_START:
                ESP_LOGI(TAG, "AP started");
                wifi_status.ap_started = true;
                xEventGroupSetBits(wifi_event_group, WIFI_AP_STARTED_BIT);
                dev_status_set_ap_enabled();
                break;
                
            case WIFI_EVENT_AP_STOP:
                ESP_LOGI(TAG, "AP stopped");
                wifi_status.ap_started = false;
                wifi_status.ap_connected_stations = 0;
                // Update queue
                if (ap_stations_queue) xQueueOverwrite(ap_stations_queue, &wifi_status.ap_connected_stations);
                xEventGroupClearBits(wifi_event_group, WIFI_AP_STARTED_BIT);
                // Clear overlap indicator when AP stops
                xEventGroupClearBits(wifi_event_group, WIFI_STA_AP_OVERLAP_BIT);
                dev_status_clear_ap_enabled();
                break;
                
            case WIFI_EVENT_AP_STACONNECTED: {
                wifi_event_ap_staconnected_t* event = (wifi_event_ap_staconnected_t*) event_data;
                ESP_LOGI(TAG, "Station connected to AP: " MACSTR ", AID=%d", MAC2STR(event->mac), event->aid);
                wifi_status.ap_connected_stations++;
                // Update queue
                if (ap_stations_queue) xQueueOverwrite(ap_stations_queue, &wifi_status.ap_connected_stations);
                
                if (user_callbacks.ap_station_connected) {
                    user_callbacks.ap_station_connected(event->mac, event->aid);
                }
                break;
            }
            
            case WIFI_EVENT_AP_STADISCONNECTED: {
                wifi_event_ap_stadisconnected_t* event = (wifi_event_ap_stadisconnected_t*) event_data;
                ESP_LOGI(TAG, "Station disconnected from AP: " MACSTR ", AID=%d", MAC2STR(event->mac), event->aid);
                if (wifi_status.ap_connected_stations > 0) {
                    wifi_status.ap_connected_stations--;
                }
                // Update queue
                if (ap_stations_queue) xQueueOverwrite(ap_stations_queue, &wifi_status.ap_connected_stations);
                
                if (user_callbacks.ap_station_disconnected) {
                    user_callbacks.ap_station_disconnected(event->mac, event->aid);
                }
                break;
            }
        }
    } else if (event_base == IP_EVENT && event_id == IP_EVENT_STA_GOT_IP) {
        ip_event_got_ip_t* event = (ip_event_got_ip_t*) event_data;
        ESP_LOGI(TAG, "Got IP: " IPSTR, IP2STR(&event->ip_info.ip));
        
        wifi_status.sta_connected = true;
        wifi_status.sta_retry_count = 0;
        snprintf(wifi_status.sta_ip, sizeof(wifi_status.sta_ip), IPSTR, IP2STR(&event->ip_info.ip));
        // Update queue
        if (sta_ip_queue) xQueueOverwrite(sta_ip_queue, wifi_status.sta_ip);
        // Clear failure/bans for the successful SSID
        if (wifi_status.last_attempted_ssid[0]) {
            wifi_mgr_fs_clear(wifi_status.last_attempted_ssid);
        }
        
        xEventGroupSetBits(wifi_event_group, WIFI_CONNECTED_BIT | WIFI_STA_GOT_IP_BIT);
        xEventGroupClearBits(wifi_event_group, WIFI_DISCONNECTED_BIT);
        dev_status_set_sta_connected();
        if (user_callbacks.sta_connected) {
            user_callbacks.sta_connected();
        }

        // Auto-disable AP if in APSTA mode
        if (wifi_config.mode == WIFI_MGR_MODE_APSTA && wifi_config.ap_auto_disable) {
            wifi_mode_t current_mode;
            if (esp_wifi_get_mode(&current_mode) == ESP_OK && current_mode == WIFI_MODE_APSTA) {
                ESP_LOGI(TAG, "Auto-disabling AP mode");
                // Don't stop WiFi completely - just change mode to preserve STA connection
                esp_err_t ret = esp_wifi_set_mode(WIFI_MODE_STA);
                if(sta_netif){
                    esp_netif_set_default_netif(sta_netif);
                }
                if (ret != ESP_OK) {
                    ESP_LOGE(TAG, "Failed to switch to STA mode: %s", esp_err_to_name(ret));
                }
            }
        }

        // If AP is enabled (APSTA/AUTO mode), ap_auto_disable is false, and STA is connected, update AP channel to match STA
        if ((wifi_config.mode == WIFI_MGR_MODE_APSTA)
            && !wifi_config.ap_auto_disable) {
            wifi_mode_t current_mode;
            static wifi_config_t ap_cfg;
            static wifi_config_t sta_cfg;
            if (esp_wifi_get_mode(&current_mode) == ESP_OK && current_mode == WIFI_MODE_APSTA) {
                // Get STA config to read channel
                if (esp_wifi_get_config(WIFI_IF_STA, &sta_cfg) == ESP_OK) {
                    uint8_t sta_channel = sta_cfg.sta.channel;
                    // Get AP config
                    if (esp_wifi_get_config(WIFI_IF_AP, &ap_cfg) == ESP_OK) {
                        if (ap_cfg.ap.channel != sta_channel) {
                            ESP_LOGI(TAG, "Updating AP channel to match STA channel: %d", sta_channel);
                            ap_cfg.ap.channel = sta_channel;
                            esp_err_t ret = esp_wifi_set_config(WIFI_IF_AP, &ap_cfg);
                            if (ret != ESP_OK) {
                                ESP_LOGE(TAG, "Failed to update AP channel: %s", esp_err_to_name(ret));
                            }
                        }
                    }
                }
            }

            // Also check if AP and STA are on overlapping IP ranges and set/clear WIFI_STA_AP_OVERLAP_BIT
            if (ap_netif != NULL) {
                esp_netif_ip_info_t ap_ip_info;
                if (esp_netif_get_ip_info(ap_netif, &ap_ip_info) == ESP_OK) {
                    uint32_t sta_ip = event->ip_info.ip.addr;
                    uint32_t sta_mask = event->ip_info.netmask.addr;
                    uint32_t ap_ip = ap_ip_info.ip.addr;
                    uint32_t ap_mask = ap_ip_info.netmask.addr;
                    // Overlap if either subnet contains the other's IP (for contiguous masks)
                    bool overlap = (((ap_ip & sta_mask) == (sta_ip & sta_mask)) ||
                                     ((sta_ip & ap_mask) == (ap_ip & ap_mask)));
                    if (overlap) {
                        xEventGroupSetBits(wifi_event_group, WIFI_STA_AP_OVERLAP_BIT);
                        dev_status_set_bits(DEV_STA_AP_OVERLAP_BIT);
                        ESP_LOGW(TAG, "STA/AP subnet overlap detected");
                    } else {
                        xEventGroupClearBits(wifi_event_group, WIFI_STA_AP_OVERLAP_BIT);
                        dev_status_clear_bits(DEV_STA_AP_OVERLAP_BIT);
                    }
                }
            }
        }
    }
}

/**
 * WiFi reconnect task
 */
static void wifi_reconnect_task(void* pvParameters) {
    const TickType_t reconnect_delay = pdMS_TO_TICKS(5000); // 5 seconds
    const TickType_t ap_client_check_delay = pdMS_TO_TICKS(10000); // 10 seconds
    
    ESP_LOGI(TAG, "WiFi reconnect task started");
    
    // Wait a moment for WiFi to fully initialize
    vTaskDelay(pdMS_TO_TICKS(1000));
    
    while (1) {
        // Check if WiFi is still initialized and enabled
        EventBits_t current_bits = xEventGroupGetBits(wifi_event_group);
        if (!(current_bits & WIFI_INIT_BIT) || !(current_bits & WIFI_ENABLED_BIT)) {
            ESP_LOGI(TAG, "WiFi not initialized or enabled (bits: 0x%08lX), reconnect task exiting", current_bits);
            break;
        }
        
        // Check if we should attempt reconnection
        if (!wifi_config.sta_auto_reconnect || !wifi_status.enabled) {
            ESP_LOGI(TAG, "Auto-reconnect disabled or WiFi disabled, skipping");
            vTaskDelay(reconnect_delay);
            continue;
        }
        
        // Check if STA is already connected
        if (wifi_status.sta_connected) {
            vTaskDelay(reconnect_delay);
            continue;
        }
        
        // Check if we have a disconnection event or if we're already disconnected
        bool should_reconnect = false;
        if (current_bits & WIFI_DISCONNECTED_BIT) {
            // Clear the disconnected bit since we're handling it
            xEventGroupClearBits(wifi_event_group, WIFI_DISCONNECTED_BIT);
            should_reconnect = true;
        } else if (!wifi_status.sta_connected) {
            // STA is disconnected but no event bit set, still try to reconnect
            should_reconnect = true;
        }
        
        if (!should_reconnect) {
            vTaskDelay(reconnect_delay);
            continue;
        }
        
        // Check if AP has connected stations - pause reconnection to avoid channel switching
        if (wifi_status.ap_connected_stations > 0) {
            ESP_LOGI(TAG, "AP has %d connected stations, pausing STA reconnection to avoid channel switching", 
                     wifi_status.ap_connected_stations);
            vTaskDelay(ap_client_check_delay); // Wait and check again, don't consume the disconnect event
            continue;
        }
        
        // Check retry limit
        if (wifi_config.sta_max_retry >= 0 && wifi_status.sta_retry_count >= wifi_config.sta_max_retry) {
            ESP_LOGW(TAG, "Max retry attempts reached (%d), waiting before next attempt", wifi_config.sta_max_retry);
            vTaskDelay(pdMS_TO_TICKS(60000)); // Wait 1 minute before trying again
            wifi_status.sta_retry_count = 0; // Reset retry count after long wait
            continue;
        }
        
        // Wait before reconnecting
        vTaskDelay(reconnect_delay); 
        
        // Double-check conditions before attempting reconnection
        current_bits = xEventGroupGetBits(wifi_event_group);
        if (wifi_status.enabled && !wifi_status.sta_connected && 
            (current_bits & WIFI_INIT_BIT) && (current_bits & WIFI_ENABLED_BIT)) {
            
            ESP_LOGI(TAG, "Attempting to reconnect (attempt %d)", wifi_status.sta_retry_count + 1);
            wifi_status.sta_retry_count++;
            
            esp_err_t ret;
            if (wifi_config.fallback_count > 0) {
                wifi_mgr_scan_select_and_connect();
                ret = ESP_OK; // function itself triggers connect and logs
            } else {
                wifi_mgr_update_last_attempted_from_current_config();
                if (wifi_status.last_attempted_ssid[0] && wifi_mgr_fs_should_skip(wifi_status.last_attempted_ssid)) {
                    ESP_LOGW(TAG, "Current SSID '%s' is banned; deferring reconnect", wifi_status.last_attempted_ssid);
                    ret = ESP_OK;
                } else {
                    ret = esp_wifi_connect();
                }
            }
            if (ret != ESP_OK) {
                ESP_LOGE(TAG, "WiFi connect failed: %s", esp_err_to_name(ret));
            }
        } else {
            ESP_LOGI(TAG, "Skipping reconnect - conditions not met (enabled:%d, connected:%d, bits:0x%08lX)", 
                     wifi_status.enabled, wifi_status.sta_connected, current_bits);
        }
    }
    
    ESP_LOGI(TAG, "WiFi reconnect task ended");
    reconnect_task_handle = NULL;
    vTaskDelete(NULL);
}
