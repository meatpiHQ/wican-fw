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
#include "esp_log.h"
#include <ctype.h>
#include <string.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <float.h>
#include <time.h>
#include "cJSON.h"
#include "esp_heap_caps.h"
#include "esp_log.h"
#include "hw_config.h"
#include "obd2_standard_pids.h"
#include "autopid_config.h"
#define TAG "AUTO_PID_CFG"

// Forward declaration
const std_pid_t *get_pid_from_string(const char *pid_string);

static char *strdup_psram(const char *s) {
    if (!s) return NULL;
    size_t len = strlen(s) + 1;
    char *copy = heap_caps_malloc(len, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    if (!copy) copy = heap_caps_malloc(len, MALLOC_CAP_8BIT);
    if (!copy) return NULL;
    memcpy(copy, s, len);
    return copy;
}

static void replace_atsp_with_attp(char *str) {
    if (!str) return;
    for (size_t i = 0; str[i] != '\0'; i++) {
        if (tolower((unsigned char)str[i]) != 'a') continue;
        if (tolower((unsigned char)str[i+1]) != 't') continue;
        size_t j = i + 2;
        while (str[j] && isspace((unsigned char)str[j])) j++;
        if (tolower((unsigned char)str[j]) != 's') continue;
        size_t k = j + 1;
        while (str[k] && isspace((unsigned char)str[k])) k++;
        if (tolower((unsigned char)str[k]) != 'p') continue;
        str[i] = 'A'; str[i+1] = 'T'; str[j] = 'T'; str[k] = 'P';
        i = k;
    }
}

static char *normalize_init_string(const char *src) {
    if (!src || src[0] == '\0') return NULL;
    char *out = strdup_psram(src);
    if (!out) return NULL;
    for (size_t i = 0; out[i] != '\0'; i++) {
        if (out[i] == ';') out[i] = '\r';
    }
    replace_atsp_with_attp(out);
    return out;
}

static cJSON *parse_json_file(FILE *f) {
    fseek(f, 0, SEEK_END);
    long fsize = ftell(f);
    fseek(f, 0, SEEK_SET);
    char *buffer = malloc((size_t)fsize + 1);
    if (!buffer) return NULL;
    fread(buffer, (size_t)fsize, 1, f);
    buffer[fsize] = 0;
    cJSON *root = cJSON_Parse(buffer);
    free(buffer);
    return root;
}

static const char *json_get_string(const cJSON *item) {
    return (item && cJSON_IsString(item) && item->valuestring) ? item->valuestring : NULL;
}

static float json_item_to_float(const cJSON *item, float default_value) {
    if (!item) return default_value;
    if (cJSON_IsNumber(item)) return (float)item->valuedouble;
    const char *s = json_get_string(item);
    if (s && s[0] != '\0') return (float)atof(s);
    return default_value;
}

static uint32_t json_item_to_u32(const cJSON *item, uint32_t default_value) {
    if (!item) return default_value;
    if (cJSON_IsNumber(item)) {
        if (item->valuedouble < 0) return default_value;
        return (uint32_t)item->valuedouble;
    }
    const char *s = json_get_string(item);
    if (s && s[0] != '\0') return (uint32_t)atoi(s);
    return default_value;
}

static char *json_strdup_key_or_default(const cJSON *obj, const char *key, const char *default_value) {
    if (!obj || !key) return default_value ? strdup_psram(default_value) : NULL;
    cJSON *item = cJSON_GetObjectItem(obj, key);
    if (!item) return default_value ? strdup_psram(default_value) : NULL;
    if (cJSON_IsString(item) && item->valuestring) return strdup_psram(item->valuestring);
    return default_value ? strdup_psram(default_value) : NULL;
}

static destination_type_t destination_type_from_string(const char *t) {
    if (!t) return DEST_DEFAULT;
    if (strcasecmp(t, "MQTT_Topic") == 0) return DEST_MQTT_TOPIC;
    if (strcasecmp(t, "MQTT_WallBox") == 0) return DEST_MQTT_WALLBOX;
    if (strcasecmp(t, "HTTP") == 0) return DEST_HTTP;
    if (strcasecmp(t, "HTTPS") == 0) return DEST_HTTPS;
    if (strcasecmp(t, "ABRP_API") == 0) return DEST_ABRP_API;
    return DEST_DEFAULT;
}

static sensor_type_t json_sensor_type(const cJSON *obj, const char *key, sensor_type_t default_value) {
    if (!obj || !key) return default_value;
    const cJSON *item = cJSON_GetObjectItem((cJSON *)obj, key);
    const char *s = json_get_string(item);
    if (!s) return default_value;
    return (strcmp(s, "binary") == 0) ? BINARY_SENSOR : SENSOR;
}

static void parse_parameter_object(parameter_t *out_param, const cJSON *param_obj,
                                   const char *name_key, const char *expr_key,
                                   const char *unit_key, const char *class_key,
                                   const char *sensor_type_key,
                                   const char *min_key, const char *max_key,
                                   const char *period_key,
                                   const char *destination_key, const char *destination_type_key)
{
    if (!out_param || !param_obj) return;

    {
        const cJSON *enabled_item = cJSON_GetObjectItem((cJSON *)param_obj, "enabled");
        out_param->enabled = (enabled_item && cJSON_IsBool(enabled_item)) ? cJSON_IsTrue(enabled_item) : true;
    }

    {
        const cJSON *onchange_item = cJSON_GetObjectItem((cJSON *)param_obj, "onchange");
        out_param->onchange = (onchange_item && cJSON_IsBool(onchange_item)) ? cJSON_IsTrue(onchange_item) : false;
    }

    out_param->name = json_strdup_key_or_default(param_obj, name_key, "none");
    out_param->expression = json_strdup_key_or_default(param_obj, expr_key, "none");
    out_param->unit = json_strdup_key_or_default(param_obj, unit_key, "none");
    out_param->class = json_strdup_key_or_default(param_obj, class_key, "none");
    out_param->sensor_type = json_sensor_type(param_obj, sensor_type_key, SENSOR);

    out_param->min = json_item_to_float(min_key ? cJSON_GetObjectItem((cJSON *)param_obj, min_key) : NULL, FLT_MAX);
    out_param->max = json_item_to_float(max_key ? cJSON_GetObjectItem((cJSON *)param_obj, max_key) : NULL, FLT_MAX);
    out_param->period = json_item_to_u32(period_key ? cJSON_GetObjectItem((cJSON *)param_obj, period_key) : NULL, 0);

    // Destination Parsing
    out_param->destination = NULL;
    const char *dest_keys[] = { destination_key, "destination", "Destination", "send_to", "Send_to", NULL };
    for (int i = 0; dest_keys[i]; i++) {
        if (!dest_keys[i]) continue;
        cJSON *item = cJSON_GetObjectItem((cJSON *)param_obj, dest_keys[i]);
        if (item && cJSON_IsString(item) && item->valuestring) {
            out_param->destination = strdup_psram(item->valuestring);
            break;
        }
    }
    if (!out_param->destination) out_param->destination = strdup_psram("none");

    // Destination Type Parsing
    out_param->destination_type = DEST_DEFAULT;
    const char *type_keys[] = { destination_type_key, "type", "Type", "destination_type", NULL };
    bool type_found = false;
    for (int i = 0; type_keys[i]; i++) {
        if (!type_keys[i]) continue;
        cJSON *item = cJSON_GetObjectItem((cJSON *)param_obj, type_keys[i]);
        if (item && cJSON_IsString(item) && item->valuestring) {
            out_param->destination_type = destination_type_from_string(item->valuestring);
            type_found = true;
            break;
        }
    }
    if (!type_found) out_param->destination_type = DEST_DEFAULT;

    out_param->timer = 0;
    out_param->value = FLT_MAX;
    out_param->failed = false;
    
    // Initialize change tracking
    out_param->last_sent_value = -FLT_MAX; 
}


 static int parse_pids_to_master_list(cJSON *pids_array, autopid_config_t *cfg, int start_idx, pid_type_t type) {
    if (!pids_array || !cfg->pids) return 0;

    int idx = start_idx;
    cJSON *pid;
    cJSON_ArrayForEach(pid, pids_array) {
        pid_data_t *curr_pid = &cfg->pids[idx];
        memset(curr_pid, 0, sizeof(pid_data_t));

        cJSON *pid_item = cJSON_GetObjectItem(pid, "pid"); 
        if(!pid_item) pid_item = cJSON_GetObjectItem(pid, "PID");
        
        cJSON *init_item = cJSON_GetObjectItem(pid, "init");
        if(!init_item) init_item = cJSON_GetObjectItem(pid, "pid_init");
        
        cJSON *period_item = cJSON_GetObjectItem(pid, "period");
        if(!period_item) period_item = cJSON_GetObjectItem(pid, "Period");
        
        cJSON *rxheader_item = cJSON_GetObjectItem(pid, "header");
        if(!rxheader_item) rxheader_item = cJSON_GetObjectItem(pid, "ReceiveHeader");
        
        cJSON *enabled_item = cJSON_GetObjectItem(pid, "enabled");

        if (pid_item && pid_item->valuestring) {
            size_t len = strlen(pid_item->valuestring);
            curr_pid->cmd = heap_caps_malloc(len + 2, MALLOC_CAP_8BIT | MALLOC_CAP_SPIRAM);
            if (curr_pid->cmd) {
                if(type == PID_STD) sprintf(curr_pid->cmd, "01%s\r", pid_item->valuestring);
                else {
                    strcpy(curr_pid->cmd, pid_item->valuestring);
                    strcat(curr_pid->cmd, "\r");
                }
            }
        }

        if (init_item && init_item->valuestring) curr_pid->init = normalize_init_string(init_item->valuestring);
        
        if (period_item) curr_pid->period = json_item_to_u32(period_item, 10000);
        else curr_pid->period = cfg->cycle;

        curr_pid->rxheader = (rxheader_item && rxheader_item->valuestring) ? strdup_psram(rxheader_item->valuestring) : NULL;
        curr_pid->pid_type = type;
        curr_pid->enabled = (enabled_item && cJSON_IsBool(enabled_item)) ? cJSON_IsTrue(enabled_item) : true;

        // [CRITICAL FIX] Support Nested "parameters" Array
        cJSON *params = cJSON_GetObjectItem(pid, "parameters");
        
        if (params && cJSON_IsArray(params)) {
            // Nested parameters array found
            curr_pid->parameters_count = cJSON_GetArraySize(params);
            if (curr_pid->parameters_count > 0) {
                curr_pid->parameters = heap_caps_calloc(curr_pid->parameters_count, sizeof(parameter_t), MALLOC_CAP_8BIT | MALLOC_CAP_SPIRAM);
                int p_idx = 0;
                cJSON *p;
                cJSON_ArrayForEach(p, params) {
                    parse_parameter_object(&curr_pid->parameters[p_idx], p, 
                        "name", "expression", "unit", "class", "sensor_type", "min", "max", "period", "send_to", "type");
                    // Inherit PID period if parameter period is missing
                    if (curr_pid->parameters[p_idx].period == 0) curr_pid->parameters[p_idx].period = curr_pid->period;
                    p_idx++;
                }
            }
        } 
        else {
            // Fallback: Flat Structure (Old Format)
            curr_pid->parameters_count = 1;
            curr_pid->parameters = heap_caps_calloc(1, sizeof(parameter_t), MALLOC_CAP_8BIT | MALLOC_CAP_SPIRAM);
            parse_parameter_object(&curr_pid->parameters[0], pid, 
                "name", "expression", "unit", "class", "sensor_type", "min", "max", "period", "send_to", "type");
        }

        // Initialize update mode for first parameter
        if (curr_pid->parameters_count > 0) {
            cJSON *update_item = cJSON_GetObjectItem(pid, "update");
            if (update_item && update_item->valuestring) {
                curr_pid->parameters[0].update_mode = strdup_psram(update_item->valuestring);
            } else {
                curr_pid->parameters[0].update_mode = strdup_psram("always");
            }
            // [CRITICAL] Initialize last_sent_values for ALL parameters
            for(int k=0; k<curr_pid->parameters_count; k++) {
                curr_pid->parameters[k].last_sent_value = -FLT_MAX;
            }
        }

        idx++;
    }
    return (idx - start_idx);
}

static bool parse_frame_id(const cJSON *frame_id_item, uint32_t *out_frame_id) {
    if (!out_frame_id || !frame_id_item) return false;
    if (cJSON_IsNumber(frame_id_item)) {
        if (frame_id_item->valuedouble < 0) return false;
        *out_frame_id = (uint32_t)frame_id_item->valuedouble;
        return true;
    }
    if (cJSON_IsString(frame_id_item) && frame_id_item->valuestring) {
        const char *s = frame_id_item->valuestring;
        int base = 16;
        if (s[0] == '0' && (s[1] == 'x' || s[1] == 'X')) base = 0;
        char *endptr = NULL;
        unsigned long v = strtoul(s, &endptr, base);
        if (endptr == s) return false;
        *out_frame_id = (uint32_t)v;
        return true;
    }
    return false;
}

static cJSON *load_json_root_from_mount(const char *filename) {
    char path[128];
    snprintf(path, sizeof(path), FS_MOUNT_POINT "/%s", filename);
    FILE *f = fopen(path, "r");
    if (!f) return NULL;
    cJSON *root = parse_json_file(f);
    fclose(f);
    if (root) ESP_LOGI(TAG, "Loaded JSON file: %s", path);
    else ESP_LOGW(TAG, "Failed to parse JSON file: %s", path);
    return root;
}

static int count_car_data_pids(void) {
    int count = 0;
    cJSON *root = load_json_root_from_mount("car_data.json");
    if (!root) {
        ESP_LOGE(TAG, "count_car_data_pids: Failed to load car_data.json");
        return 0;
    }
    cJSON *cars = cJSON_GetObjectItem(root, "cars");
    if (cars && cJSON_IsArray(cars)) {
        cJSON *car = cJSON_GetArrayItem(cars, 0);
        if (car) {
            cJSON *pids = cJSON_GetObjectItem(car, "pids");
            if (pids && cJSON_IsArray(pids)) {
                count = cJSON_GetArraySize(pids);
                ESP_LOGI(TAG, "count_car_data_pids: Found %d PIDs", count);
            }
        }
    }
    cJSON_Delete(root);
    return count;
}

static int count_auto_pid_pids(void) {
    int count = 0;
    cJSON *root = load_json_root_from_mount("auto_pid.json");
    if (!root) return 0;
    cJSON *pids = cJSON_GetObjectItem(root, "pids");
    cJSON *std_pids = cJSON_GetObjectItem(root, "std_pids");
    if (pids && cJSON_IsArray(pids)) count += cJSON_GetArraySize(pids);
    if (std_pids && cJSON_IsArray(std_pids)) count += cJSON_GetArraySize(std_pids);
    cJSON_Delete(root);
    return count;
}

static void parse_can_filter_object(can_filter_t *out, const cJSON *filter) {
    if (!out || !filter || !cJSON_IsObject(filter)) return;
    uint32_t frame_id = 0;
    if (!parse_frame_id(cJSON_GetObjectItem((cJSON *)filter, "frame_id"), &frame_id)) return;
    out->frame_id = frame_id;
    out->is_extended = (frame_id > 0x7FF);
    cJSON *params = cJSON_GetObjectItem((cJSON *)filter, "parameters");
    if (!params || !cJSON_IsArray(params)) return;
    int param_count = cJSON_GetArraySize(params);
    if (param_count <= 0) return;
    out->parameters_count = (uint32_t)param_count;
    out->parameters = (parameter_t *)heap_caps_calloc((size_t)param_count, sizeof(parameter_t), MALLOC_CAP_8BIT | MALLOC_CAP_SPIRAM);
    if (!out->parameters) { out->parameters_count = 0; return; }
    cJSON *param = NULL;
    int pi = 0;
    cJSON_ArrayForEach(param, params) {
        if (!param || !cJSON_IsObject(param) || pi >= param_count) { pi++; continue; }
        parse_parameter_object(&out->parameters[pi], param, "name", "expression", "unit", "class", "sensor_type", "min", "max", "period", "send_to", "type");
        if (out->parameters[pi].period == 0) out->parameters[pi].period = 5000;
        pi++;
    }
}

static void autopid_cfg_append_can_filters_from_array(autopid_config_t *cfg, const cJSON *can_filters_arr, bool is_vehicle_specific) {
    if (!cfg || !can_filters_arr || !cJSON_IsArray(can_filters_arr)) return;
    int add_count = cJSON_GetArraySize((cJSON *)can_filters_arr);
    if (add_count <= 0) return;
    uint32_t old_count = cfg->can_filters_count;
    uint32_t new_count = old_count + (uint32_t)add_count;
    can_filter_t *new_arr = (can_filter_t *)heap_caps_calloc((size_t)new_count, sizeof(can_filter_t), MALLOC_CAP_8BIT | MALLOC_CAP_SPIRAM);
    if (!new_arr) return;
    if (cfg->can_filters && old_count > 0) {
        memcpy(new_arr, cfg->can_filters, sizeof(can_filter_t) * old_count);
        free(cfg->can_filters);
    }
    for (int i = 0; i < add_count; i++) {
        cJSON *filter = cJSON_GetArrayItem((cJSON *)can_filters_arr, i);
        parse_can_filter_object(&new_arr[old_count + (uint32_t)i], filter);
        new_arr[old_count + (uint32_t)i].is_vehicle_specific = is_vehicle_specific;
    }
    cfg->can_filters = new_arr;
    cfg->can_filters_count = new_count;
}

static void parse_auto_pid_json(autopid_config_t *autopid_config, int *pid_index) {
    if (!autopid_config || !pid_index) return;
    cJSON *root = load_json_root_from_mount("auto_pid.json");
    if (!root) return;
    int idx = *pid_index;
    cJSON *init_item = cJSON_GetObjectItem(root, "initialisation");
    cJSON *webhook_data_mode_item = cJSON_GetObjectItem(root, "webhook_data_mode");
    cJSON *car_model_item = cJSON_GetObjectItem(root, "car_model");
    cJSON *ecu_protocol_item = cJSON_GetObjectItem(root, "ecu_protocol");
    cJSON *ha_discovery_item = cJSON_GetObjectItem(root, "ha_discovery");
    cJSON *disable_on_sleep_voltage_item = cJSON_GetObjectItem(root, "disable_on_sleep_voltage");
    cJSON *pid_polling_min_voltage_item = cJSON_GetObjectItem(root, "pid_polling_min_voltage");
    cJSON *cycle_item = cJSON_GetObjectItem(root, "cycle");
    cJSON *pid_validation_item = cJSON_GetObjectItem(root, "pid_validation");
    cJSON *standard_pids_item = cJSON_GetObjectItem(root, "standard_pids");
    cJSON *specific_pids_item = cJSON_GetObjectItem(root, "car_specific");
    cJSON *can_filters_item = cJSON_GetObjectItem(root, "can_filters");
    cJSON *group_destination_item = cJSON_GetObjectItem(root, "destination");
    cJSON *group_dest_type_item = cJSON_GetObjectItem(root, "group_dest_type");
    cJSON *destinations_array = cJSON_GetObjectItem(root, "destinations");
    cJSON *group_api_token_item = cJSON_GetObjectItem(root, "group_api_token");

    if (init_item && init_item->valuestring) autopid_config->custom_init = normalize_init_string(init_item->valuestring);
    autopid_config->grouping = strdup_psram("enable");
    autopid_config->webhook_data_mode = (webhook_data_mode_item && webhook_data_mode_item->valuestring && strlen(webhook_data_mode_item->valuestring) > 1) ? strdup_psram(webhook_data_mode_item->valuestring) : strdup_psram("changed");
    
    autopid_config->vehicle_model = car_model_item ? strdup_psram(car_model_item->valuestring) : NULL;
    autopid_config->std_ecu_protocol = ecu_protocol_item ? strdup_psram(ecu_protocol_item->valuestring) : NULL;
    
    autopid_config->ha_discovery_en = false;
    if (ha_discovery_item) {
        if (cJSON_IsString(ha_discovery_item) && ha_discovery_item->valuestring) autopid_config->ha_discovery_en = (strcmp(ha_discovery_item->valuestring, "enable") == 0);
        else if (cJSON_IsBool(ha_discovery_item)) autopid_config->ha_discovery_en = cJSON_IsTrue(ha_discovery_item);
    }

    // Backward-compatible default: do NOT disable AutoPID on low voltage unless explicitly enabled.
    autopid_config->disable_on_sleep_voltage = false;
    autopid_config->disable_pid_requests_on_sleep_voltage = false;
    autopid_config->disable_pid_requests_on_automate_threshold = false;
    if (disable_on_sleep_voltage_item)
    {
        if (cJSON_IsString(disable_on_sleep_voltage_item) && disable_on_sleep_voltage_item->valuestring)
        {
            const char *v = disable_on_sleep_voltage_item->valuestring;
            // Supported values:
            //  - "disable" (default)
            //  - "enable" (pause AutoPID entirely on low voltage)
            //  - "disable_pid_requests" (skip PID requests but keep CAN filters active)
            //  - "automate_threshold" (skip PID requests below pid_polling_min_voltage; CAN filters remain active)
            autopid_config->disable_on_sleep_voltage = (strcmp(v, "enable") == 0);
            autopid_config->disable_pid_requests_on_sleep_voltage = (strcmp(v, "disable_pid_requests") == 0);
            autopid_config->disable_pid_requests_on_automate_threshold = (strcmp(v, "automate_threshold") == 0);
        }
        else if (cJSON_IsBool(disable_on_sleep_voltage_item))
        {
            autopid_config->disable_on_sleep_voltage = cJSON_IsTrue(disable_on_sleep_voltage_item);
            autopid_config->disable_pid_requests_on_sleep_voltage = false;
            autopid_config->disable_pid_requests_on_automate_threshold = false;
        }
    }

    // PID polling minimum voltage (used only for automate_threshold mode)
    autopid_config->pid_polling_min_voltage = 13.1f;
    if (pid_polling_min_voltage_item)
    {
        float v = autopid_config->pid_polling_min_voltage;
        if (cJSON_IsNumber(pid_polling_min_voltage_item))
        {
            v = (float)pid_polling_min_voltage_item->valuedouble;
        }
        else if (cJSON_IsString(pid_polling_min_voltage_item) && pid_polling_min_voltage_item->valuestring)
        {
            v = (float)atof(pid_polling_min_voltage_item->valuestring);
        }

        // Basic sanity clamp: ignore clearly invalid values.
        if (v >= 9.0f && v <= 18.0f)
        {
            autopid_config->pid_polling_min_voltage = v;
        }
    }
    
    if (cycle_item && cycle_item->valuestring && strlen(cycle_item->valuestring) > 0) autopid_config->cycle = atoi(cycle_item->valuestring);
    else if (cycle_item && cycle_item->valueint) autopid_config->cycle = cycle_item->valueint;
    else autopid_config->cycle = 10000;
    
    autopid_config->pid_std_en = false;
    if (standard_pids_item) {
        if (cJSON_IsString(standard_pids_item) && standard_pids_item->valuestring) autopid_config->pid_std_en = (strcmp(standard_pids_item->valuestring, "enable") == 0);
        else if (cJSON_IsBool(standard_pids_item)) autopid_config->pid_std_en = cJSON_IsTrue(standard_pids_item);
    }
    
    autopid_config->pid_specific_en = false;
    if (specific_pids_item) {
        if (cJSON_IsString(specific_pids_item) && specific_pids_item->valuestring) autopid_config->pid_specific_en = (strcmp(specific_pids_item->valuestring, "enable") == 0);
        else if (cJSON_IsBool(specific_pids_item)) autopid_config->pid_specific_en = cJSON_IsTrue(specific_pids_item);
    }
    
    autopid_config->pid_validation_en = true; 
    if (pid_validation_item) {
        if (cJSON_IsString(pid_validation_item) && pid_validation_item->valuestring) autopid_config->pid_validation_en = (strcmp(pid_validation_item->valuestring, "enable") == 0);
        else if (cJSON_IsBool(pid_validation_item)) autopid_config->pid_validation_en = cJSON_IsTrue(pid_validation_item);
    }
    
    autopid_config->group_destination = (group_destination_item && group_destination_item->valuestring) ? strdup_psram(group_destination_item->valuestring) : NULL;
    if (group_dest_type_item && group_dest_type_item->valuestring) autopid_config->group_destination_type = destination_type_from_string(group_dest_type_item->valuestring);
    else autopid_config->group_destination_type = DEST_DEFAULT;
    
    // Legacy destinations loader
    autopid_config->destinations = NULL;
    autopid_config->destinations_count = 0;
    if (destinations_array && cJSON_IsArray(destinations_array)) {
        int count = cJSON_GetArraySize(destinations_array);
        if (count > AUTOPID_MAX_DESTINATIONS) count = AUTOPID_MAX_DESTINATIONS;
        if (count > 0) {
            autopid_config->destinations = (group_destination_t *)heap_caps_calloc(count, sizeof(group_destination_t), MALLOC_CAP_8BIT | MALLOC_CAP_SPIRAM);
            if (autopid_config->destinations) {
                autopid_config->destinations_count = count;
                for (int di = 0; di < count; di++) {
                    cJSON *d = cJSON_GetArrayItem(destinations_array, di);
                    if (!d) continue;
                    group_destination_t *gd = &autopid_config->destinations[di];
                    cJSON *type_item = cJSON_GetObjectItem(d, "type");
                    cJSON *dest_item = cJSON_GetObjectItem(d, "destination");
                    cJSON *cycle_item2 = cJSON_GetObjectItem(d, "cycle");
                    cJSON *api_token_item = cJSON_GetObjectItem(d, "api_token");
                    cJSON *cert_set_item = cJSON_GetObjectItem(d, "cert_set");
                    cJSON *enabled_item = cJSON_GetObjectItem(d, "enabled");
                    const char *type_str = (type_item && cJSON_IsString(type_item)) ? type_item->valuestring : "Default";
                    gd->type = destination_type_from_string(type_str);
                    gd->destination = (dest_item && cJSON_IsString(dest_item)) ? strdup_psram(dest_item->valuestring) : NULL;
                    if (cycle_item2 && cycle_item2->valuestring && strlen(cycle_item2->valuestring) > 0) gd->cycle = (uint32_t)atoi(cycle_item2->valuestring);
                    else if (cycle_item2 && cycle_item2->valueint) gd->cycle = (uint32_t)cycle_item2->valueint;
                    else gd->cycle = 10000;
                    gd->api_token = (api_token_item && cJSON_IsString(api_token_item)) ? strdup_psram(api_token_item->valuestring) : NULL;
                    gd->cert_set = (cert_set_item && cJSON_IsString(cert_set_item)) ? strdup_psram(cert_set_item->valuestring) : strdup_psram("default");
                    gd->enabled = (enabled_item && cJSON_IsBool(enabled_item)) ? cJSON_IsTrue(enabled_item) : false;
                }
            }
        }
    } else {
        if (autopid_config->group_destination || group_dest_type_item) {
            autopid_config->destinations = (group_destination_t *)heap_caps_calloc(1, sizeof(group_destination_t), MALLOC_CAP_8BIT | MALLOC_CAP_SPIRAM);
            if (autopid_config->destinations) {
                autopid_config->destinations_count = 1;
                autopid_config->destinations[0].type = autopid_config->group_destination_type;
                autopid_config->destinations[0].destination = autopid_config->group_destination ? strdup_psram(autopid_config->group_destination) : NULL;
                autopid_config->destinations[0].cycle = (autopid_config->cycle > 0) ? autopid_config->cycle : 10000;
                autopid_config->destinations[0].api_token = (group_api_token_item && group_api_token_item->valuestring) ? strdup_psram(group_api_token_item->valuestring) : NULL;
                autopid_config->destinations[0].cert_set = strdup_psram("default");
                autopid_config->destinations[0].enabled = false;
            }
        }
    }

    cJSON *pids = cJSON_GetObjectItem(root, "pids");
    if (pids) {
        cJSON *pid;
        cJSON_ArrayForEach(pid, pids) {
            pid_data_t *curr_pid = &autopid_config->pids[idx];
            cJSON *init_item2 = cJSON_GetObjectItem(pid, "Init");
            cJSON *pid_item = cJSON_GetObjectItem(pid, "PID");
            cJSON *period_item = cJSON_GetObjectItem(pid, "Period");
            cJSON *rxheader_item = cJSON_GetObjectItem(pid, "header");
            cJSON *enabled_item = cJSON_GetObjectItem(pid, "enabled");
            if (cJSON_GetArraySize(pids) > 0) autopid_config->pid_custom_en = true;
            curr_pid->cmd = pid_item ? (char *)heap_caps_malloc(strlen(pid_item->valuestring) + 2, MALLOC_CAP_8BIT | MALLOC_CAP_SPIRAM) : NULL;
            if (curr_pid->cmd && pid_item && strlen(pid_item->valuestring) > 1) {
                strcpy(curr_pid->cmd, pid_item->valuestring);
                strcat(curr_pid->cmd, "\r");
            }
            curr_pid->init = (init_item2 && init_item2->valuestring) ? normalize_init_string(init_item2->valuestring) : NULL;
            if (period_item && period_item->valuestring && strlen(period_item->valuestring) > 0) curr_pid->period = atoi(period_item->valuestring);
            else if (period_item && period_item->valueint) curr_pid->period = (uint32_t)period_item->valueint;
            else curr_pid->period = 10000;
            curr_pid->rxheader = rxheader_item ? strdup_psram(rxheader_item->valuestring) : NULL;
            curr_pid->pid_type = PID_CUSTOM;
            curr_pid->enabled = (enabled_item && cJSON_IsBool(enabled_item)) ? cJSON_IsTrue(enabled_item) : true;
            curr_pid->parameters_count = 1;
            curr_pid->parameters = (parameter_t *)heap_caps_calloc(1, sizeof(parameter_t), MALLOC_CAP_8BIT | MALLOC_CAP_SPIRAM);
            if (curr_pid->parameters) parse_parameter_object(curr_pid->parameters, pid, "Name", "Expression", "unit", "class", "sensor_type", "MinValue", "MaxValue", "Period", "Send_to", "Type");
            idx++;
        }
    }

    cJSON *std_pids = cJSON_GetObjectItem(root, "std_pids");
    if (std_pids) {
        cJSON *pid;
        cJSON_ArrayForEach(pid, std_pids) {
            pid_data_t *curr_pid = &autopid_config->pids[idx];
            curr_pid->pid_type = PID_STD;
            cJSON *enabled_item = cJSON_GetObjectItem(pid, "enabled");
            curr_pid->enabled = (enabled_item && cJSON_IsBool(enabled_item)) ? cJSON_IsTrue(enabled_item) : true;
            char std_init_buf[64];
            int is_protocol_68 = 1, is_protocol_79 = 0;
            const char *sh_value = "";
            curr_pid->parameters_count = 1;
            curr_pid->parameters = (parameter_t *)heap_caps_calloc(1, sizeof(parameter_t), MALLOC_CAP_8BIT | MALLOC_CAP_SPIRAM);
            if (curr_pid->parameters) {
                cJSON *rxheader_item = cJSON_GetObjectItem(pid, "ReceiveHeader");
                parse_parameter_object(curr_pid->parameters, pid, "Name", NULL, NULL, NULL, "sensor_type", NULL, NULL, "Period", "Send_to", "Type");
                if (curr_pid->parameters->period == 0) curr_pid->parameters->period = 10000;
                curr_pid->parameters->min = FLT_MAX;
                curr_pid->parameters->max = FLT_MAX;
                curr_pid->rxheader = rxheader_item ? strdup_psram(rxheader_item->valuestring) : NULL;
                if (autopid_config->std_ecu_protocol) {
                    is_protocol_68 = (strcmp(autopid_config->std_ecu_protocol, "6") == 0 || strcmp(autopid_config->std_ecu_protocol, "8") == 0);
                    is_protocol_79 = (strcmp(autopid_config->std_ecu_protocol, "7") == 0 || strcmp(autopid_config->std_ecu_protocol, "9") == 0);
                }
                if (is_protocol_68) sh_value = "7DF"; else if (is_protocol_79) sh_value = "18DB33F1";
                if (is_protocol_68 || is_protocol_79) {
                    if (curr_pid->rxheader && strlen(curr_pid->rxheader) > 0) snprintf(std_init_buf, sizeof(std_init_buf), "ATTP%s\rATSH%s\rATCRA%s\r", autopid_config->std_ecu_protocol, sh_value, curr_pid->rxheader);
                    else snprintf(std_init_buf, sizeof(std_init_buf), "ATTP%s\rATSH%s\rATCRA\r", autopid_config->std_ecu_protocol, sh_value);
                    autopid_config->standard_init = strdup_psram(std_init_buf);
                } else {
                    if (autopid_config->std_ecu_protocol && autopid_config->std_ecu_protocol[0] != '\0' && strcmp(autopid_config->std_ecu_protocol, "0") != 0) {
                        snprintf(std_init_buf, sizeof(std_init_buf), "ATTP%s\r", autopid_config->std_ecu_protocol);
                        autopid_config->standard_init = strdup_psram(std_init_buf);
                    } else {
                        autopid_config->standard_init = strdup_psram("ATTP0\r");
                    }
                }
                if (curr_pid->parameters->name && strlen(curr_pid->parameters->name) > 0) {
                    const std_pid_t *pid_info = get_pid_from_string(curr_pid->parameters->name);
                    if (pid_info) {
                        for (int i = 0; i < pid_info->num_params; i++) {
                            if (strcmp(pid_info->params[i].name, strchr(curr_pid->parameters->name, '-') + 1) == 0) {
                                curr_pid->parameters->class = strdup_psram(pid_info->params[i].class);
                                curr_pid->parameters->unit = strdup_psram(pid_info->params[i].unit);
                                char pid_hex[3];
                                strncpy(pid_hex, curr_pid->parameters->name, 2);
                                pid_hex[2] = '\0';
                                curr_pid->cmd = heap_caps_malloc(8, MALLOC_CAP_8BIT | MALLOC_CAP_SPIRAM);
                                if (curr_pid->cmd) sprintf(curr_pid->cmd, "01%s\r", pid_hex);
                            }
                        }
                    }
                }
            }
            idx++;
        }
    }

    if (can_filters_item && cJSON_IsArray(can_filters_item)) {
        autopid_cfg_append_can_filters_from_array(autopid_config, can_filters_item, false);
    }

    *pid_index = idx;
    cJSON_Delete(root);
}

// ------------------------------------------------------------------------------------------------
// [FIXED] Parse Car Data JSON Implementation (Missing in previous version)
// ------------------------------------------------------------------------------------------------
static void parse_car_data_json(autopid_config_t *autopid_config, int *pid_index) {
    if (!autopid_config || !pid_index) return;
    cJSON *root = load_json_root_from_mount("car_data.json");
    if (!root) {
        ESP_LOGE(TAG, "parse_car_data_json: Failed to load car_data.json");
        return;
    }
    int idx = *pid_index;

    cJSON *cars = cJSON_GetObjectItem(root, "cars");
    if (cars) {
        cJSON *car = cJSON_GetArrayItem(cars, 0);
        if (car) {
            cJSON *init_item = cJSON_GetObjectItem(car, "init");
            if (init_item && cJSON_IsString(init_item) && init_item->valuestring) autopid_config->specific_init = normalize_init_string(init_item->valuestring);
            
            cJSON *can_filters = cJSON_GetObjectItem(car, "can_filters");
            if (can_filters && cJSON_IsArray(can_filters)) autopid_cfg_append_can_filters_from_array(autopid_config, can_filters, true);

            cJSON *pids = cJSON_GetObjectItem(car, "pids");
            if (pids) {
                ESP_LOGI(TAG, "parse_car_data_json: Found %d PIDs in 'pids' array", cJSON_GetArraySize(pids));
                cJSON *pid;
                cJSON_ArrayForEach(pid, pids) {
                    pid_data_t *curr_pid = &autopid_config->pids[idx];
                    cJSON *pid_item = cJSON_GetObjectItem(pid, "pid");
                    cJSON *pid_init_item = cJSON_GetObjectItem(pid, "pid_init");
                    cJSON *enabled_item = cJSON_GetObjectItem(pid, "enabled");

                    curr_pid->enabled = (enabled_item && cJSON_IsBool(enabled_item)) ? cJSON_IsTrue(enabled_item) : true;
                    curr_pid->init = (pid_init_item && pid_init_item->valuestring) ? normalize_init_string(pid_init_item->valuestring) : NULL;
                    
                    if (pid_item && pid_item->valuestring) {
                        size_t cmd_len = strlen(pid_item->valuestring);
                        if (cmd_len > 0) {
                            curr_pid->cmd = (char *)heap_caps_malloc(cmd_len + 2, MALLOC_CAP_8BIT | MALLOC_CAP_SPIRAM);
                            if (curr_pid->cmd) {
                                strncpy(curr_pid->cmd, pid_item->valuestring, cmd_len);
                                curr_pid->cmd[cmd_len] = '\r';
                                curr_pid->cmd[cmd_len + 1] = '\0';
                            }
                        }
                    }
                    curr_pid->pid_type = PID_SPECIFIC;

                    cJSON *params = cJSON_GetObjectItem(pid, "parameters");
                    if (params) {
                        int param_count = cJSON_GetArraySize(params);
                        curr_pid->parameters_count = param_count;
                        curr_pid->parameters = (parameter_t *)heap_caps_calloc(param_count, sizeof(parameter_t), MALLOC_CAP_8BIT | MALLOC_CAP_SPIRAM);
                        cJSON *param;
                        int param_index = 0;
                        cJSON_ArrayForEach(param, params) {
                            if (!param || !cJSON_IsObject(param)) { param_index++; continue; }
                            parse_parameter_object(&curr_pid->parameters[param_index], param, "name", "expression", "unit", "class", "sensor_type", "min", "max", "period", "send_to", "type");
                            if (curr_pid->parameters[param_index].period == 0) curr_pid->parameters[param_index].period = autopid_config->cycle;
                            param_index++;
                        }
                    }
                    idx++;
                }
            } else {
                ESP_LOGE(TAG, "parse_car_data_json: 'pids' object not found or not array");
            }
        }
    }
    *pid_index = idx;
    cJSON_Delete(root);
}


static detection_method_t detection_method_from_str(const char* str) {
    if (!str) return DETECTION_ALWAYS;
    if (strcasecmp(str, "voltage") == 0) return DETECTION_VOLTAGE;
    if (strcasecmp(str, "engine_running") == 0) return DETECTION_ADAPTIVE_RPM;
    if (strcasecmp(str, "adaptive_rpm") == 0) return DETECTION_ADAPTIVE_RPM;
    if (strcasecmp(str, "mqtt_on_demand") == 0) return DETECTION_MQTT;
    return DETECTION_ALWAYS;
}


autopid_config_t *load_autopid_config(void)
{
    ESP_LOGI(TAG, "Loading Autopid Config (Linear Mode)");

    autopid_config_t *cfg = (autopid_config_t *)heap_caps_calloc(1, sizeof(autopid_config_t), MALLOC_CAP_8BIT | MALLOC_CAP_SPIRAM);
    if (!cfg) return NULL;
    
    // 1. Load Files
    cJSON *root_auto = load_json_root_from_mount("auto_pid.json");
    cJSON *root_car = load_json_root_from_mount("car_data.json");

    // 2. Load Global Settings
    if (root_auto) {
        cJSON *init_item = cJSON_GetObjectItem(root_auto, "initialisation");
        if(init_item && init_item->valuestring) cfg->custom_init = normalize_init_string(init_item->valuestring);
        cJSON *cycle = cJSON_GetObjectItem(root_auto, "cycle");
        cfg->cycle = json_item_to_u32(cycle, 10000);
        cJSON *grouping = cJSON_GetObjectItem(root_auto, "grouping");
        if (grouping && grouping->valuestring) cfg->grouping = strdup_psram(grouping->valuestring);
    }

    // 3. Count Totals (Legacy + Grouped PIDs) to Allocate Master List
    int total_pids = count_auto_pid_pids() + count_car_data_pids();
    
    // Helpers to find group objects
    cJSON *groups_auto = root_auto ? cJSON_GetObjectItem(root_auto, "pid_groups") : NULL;
    cJSON *groups_car = NULL;
    
    if (root_car) {
        cJSON *cars = cJSON_GetObjectItem(root_car, "cars");
        if (cars && cJSON_IsArray(cars)) {
            cJSON *active_car = cJSON_GetArrayItem(cars, 0); 
            if (active_car) {
                groups_car = cJSON_GetObjectItem(active_car, "pid_groups");
                // Also load specific init
                cJSON *car_init = cJSON_GetObjectItem(active_car, "init");
                if (car_init && car_init->valuestring) cfg->specific_init = normalize_init_string(car_init->valuestring);
            }
        }
        if (!groups_car) groups_car = cJSON_GetObjectItem(root_car, "pid_groups");
    }

    // Count Group PIDs
    if (groups_auto && cJSON_IsArray(groups_auto)) {
        cJSON *g; cJSON_ArrayForEach(g, groups_auto) {
            cJSON *gpids = cJSON_GetObjectItem(g, "pids");
            if(gpids) total_pids += cJSON_GetArraySize(gpids);
        }
    }
    if (groups_car && cJSON_IsArray(groups_car)) {
        cJSON *g; cJSON_ArrayForEach(g, groups_car) {
            cJSON *gpids = cJSON_GetObjectItem(g, "pids");
            if(gpids) total_pids += cJSON_GetArraySize(gpids);
        }
    }

    // 4. Allocate Master PID List
    if(total_pids > 0) {
        cfg->pids = heap_caps_calloc(total_pids, sizeof(pid_data_t), MALLOC_CAP_8BIT | MALLOC_CAP_SPIRAM);
    }
    cfg->pid_count = 0; // Will increment as we fill

    // 5. Load Legacy PIDs (Fills cfg->pids starting at index 0)
    int idx = 0;
    parse_auto_pid_json(cfg, &idx); 
    parse_car_data_json(cfg, &idx);
    cfg->pid_count = idx; 

    // 6. Load Groups (Linear Allocation)
    int total_group_count = 0;
    if (groups_auto) total_group_count += cJSON_GetArraySize(groups_auto);
    if (groups_car) total_group_count += cJSON_GetArraySize(groups_car);

    if (total_group_count > 0) {
        cfg->use_groups = true;
        cfg->pid_custom_en = true; 
        cfg->group_count = total_group_count;
        cfg->groups = heap_caps_calloc(cfg->group_count, sizeof(pid_group_t), MALLOC_CAP_8BIT | MALLOC_CAP_SPIRAM);
        
        int g_i = 0;
        cJSON *group_sources[2] = { groups_auto, groups_car };

        for(int src=0; src < 2; src++) {
            if (group_sources[src] == NULL) continue;

            cJSON *g;
            cJSON_ArrayForEach(g, group_sources[src]) {
                if (g_i >= cfg->group_count) break;
                pid_group_t *grp = &cfg->groups[g_i];
                
                // Parse Group Config
                cJSON* init = cJSON_GetObjectItem(g, "init");
                cJSON* cond = cJSON_GetObjectItem(g, "condition");
                cJSON* period = cJSON_GetObjectItem(g, "period");
		cJSON* enabled_item = cJSON_GetObjectItem(g, "enabled");
                
                grp->name = json_strdup_key_or_default(g, "group_name", "Group");
                grp->enabled = (enabled_item && cJSON_IsBool(enabled_item)) ? cJSON_IsTrue(enabled_item) : true;
                grp->init = init ? normalize_init_string(init->valuestring) : NULL;
                grp->detection_method = detection_method_from_str(json_get_string(cond));
                grp->period = period ? json_item_to_u32(period, 0) : 0;

		/* [NEW] Explicitly default MQTT flag to false on boot */
                grp->mqtt_active_flag = false;
                
                // Parse PIDs directly into Master List
                cJSON *gpids = cJSON_GetObjectItem(g, "pids");
                if (gpids && cJSON_IsArray(gpids)) {
                    int start_idx = cfg->pid_count; // Current end of master list
                    
                    // Add to master list
                    int added = parse_pids_to_master_list(gpids, cfg, start_idx, PID_CUSTOM);
                    cfg->pid_count += added;
                    
                    // Link immediately
                    grp->pid_count = added;
                    if (added > 0) {
                        grp->pids = heap_caps_calloc(added, sizeof(pid_data_t*), MALLOC_CAP_8BIT | MALLOC_CAP_SPIRAM);
                        for(int k=0; k<added; k++) {
                            // Point to the exact slots we just filled
                            grp->pids[k] = &cfg->pids[start_idx + k];
                        }
                    }
                    ESP_LOGI(TAG, "Group %d loaded with %d PIDs", g_i, added);
                }
                g_i++;
            }
        }
    } else {
        // Fallback: Legacy Mode
        cfg->use_groups = false;
        if (idx > 0) {  
            cfg->group_count = 1;
            cfg->groups = heap_caps_calloc(1, sizeof(pid_group_t), MALLOC_CAP_8BIT | MALLOC_CAP_SPIRAM);
            cfg->groups[0].name = strdup_psram("Legacy");
            cfg->groups[0].period = 0; 
            cfg->groups[0].pid_count = idx;
            cfg->groups[0].detection_method = DETECTION_ALWAYS;
            cfg->groups[0].pids = heap_caps_calloc(idx, sizeof(pid_data_t*), MALLOC_CAP_8BIT | MALLOC_CAP_SPIRAM);
            for(int k=0; k<idx; k++) {
                cfg->groups[0].pids[k] = &cfg->pids[k];
            }
        }
    }

    if (root_auto) cJSON_Delete(root_auto);
    if (root_car) cJSON_Delete(root_car);
    
    ESP_LOGI(TAG, "Config Loaded. Groups: %d, PIDs: %ld", cfg->group_count, cfg->pid_count);
    return cfg;
}
