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

// Forward declaration (implemented in autopid.c)
const std_pid_t *get_pid_from_string(const char *pid_string);

static char *strdup_psram(const char *s)
{
    if (!s)
        return NULL;

    size_t len = strlen(s) + 1;
    // Prefer PSRAM when available, but fall back to internal RAM.
    char *copy = heap_caps_malloc(len, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    if (!copy)
    {
        copy = heap_caps_malloc(len, MALLOC_CAP_8BIT);
    }
    if (!copy)
        return NULL;

    memcpy(copy, s, len);
    return copy;
}

// Helper function to replace all occurrences of "ATSP" with "ATTP" in a string.
// Normalizes case and tolerates whitespace, e.g. "atsp6", "AT SP 6", "At s p6".
static void replace_atsp_with_attp(char *str)
{
    if (!str)
        return;

    for (size_t i = 0; str[i] != '\0'; i++)
    {
        unsigned char c0 = (unsigned char)str[i];
        if (tolower(c0) != 'a')
            continue;

        unsigned char c1 = (unsigned char)str[i + 1];
        if (c1 == 0 || tolower(c1) != 't')
            continue;

        size_t j = i + 2;
        while (str[j] != '\0' && isspace((unsigned char)str[j]))
            j++;

        if (str[j] == '\0' || tolower((unsigned char)str[j]) != 's')
            continue;

        size_t k = j + 1;
        while (str[k] != '\0' && isspace((unsigned char)str[k]))
            k++;

        if (str[k] == '\0' || tolower((unsigned char)str[k]) != 'p')
            continue;

        // Force canonical form "ATTP" (preserve any spacing in-between)
        str[i] = 'A';
        str[i + 1] = 'T';
        str[j] = 'T';
        str[k] = 'P';

        // Continue scanning after the matched 'P'
        i = k;
    }
}

static char *normalize_init_string(const char *src)
{
    if (!src || src[0] == '\0')
    {
        return NULL;
    }

    char *out = strdup_psram(src);
    if (!out)
    {
        return NULL;
    }

    // Replace semicolons with carriage returns
    for (size_t i = 0; out[i] != '\0'; i++)
    {
        if (out[i] == ';')
        {
            out[i] = '\r';
        }
    }

    replace_atsp_with_attp(out);
    return out;
}

static cJSON *parse_json_file(FILE *f)
{
    fseek(f, 0, SEEK_END);
    long fsize = ftell(f);
    fseek(f, 0, SEEK_SET);

    char *buffer = malloc((size_t)fsize + 1);
    if (!buffer)
    {
        return NULL;
    }

    fread(buffer, (size_t)fsize, 1, f);
    buffer[fsize] = 0;

    cJSON *root = cJSON_Parse(buffer);
    free(buffer);

    return root;
}

static const char *json_get_string(const cJSON *item)
{
    return (item && cJSON_IsString(item) && item->valuestring) ? item->valuestring : NULL;
}

static float json_item_to_float(const cJSON *item, float default_value)
{
    if (!item)
        return default_value;
    if (cJSON_IsNumber(item))
        return (float)item->valuedouble;
    const char *s = json_get_string(item);
    if (s && s[0] != '\0')
        return (float)atof(s);
    return default_value;
}

static uint32_t json_item_to_u32(const cJSON *item, uint32_t default_value)
{
    if (!item)
        return default_value;
    if (cJSON_IsNumber(item))
    {
        if (item->valuedouble < 0)
            return default_value;
        return (uint32_t)item->valuedouble;
    }
    const char *s = json_get_string(item);
    if (s && s[0] != '\0')
        return (uint32_t)atoi(s);
    return default_value;
}

static char *json_strdup_key_or_default(const cJSON *obj, const char *key, const char *default_value)
{
    if (!obj || !key)
        return default_value ? strdup_psram(default_value) : NULL;
    const cJSON *item = cJSON_GetObjectItem((cJSON *)obj, key);
    const char *s = json_get_string(item);
    if (s && s[0] != '\0')
        return strdup_psram(s);
    return default_value ? strdup_psram(default_value) : NULL;
}

static destination_type_t destination_type_from_string(const char *t)
{
    if (!t)
        return DEST_DEFAULT;
    if (strcmp(t, "MQTT_Topic") == 0)
        return DEST_MQTT_TOPIC;
    if (strcmp(t, "MQTT_WallBox") == 0)
        return DEST_MQTT_WALLBOX;
    if (strcmp(t, "HTTP") == 0)
        return DEST_HTTP;
    if (strcmp(t, "HTTPS") == 0)
        return DEST_HTTPS;
    if (strcmp(t, "ABRP_API") == 0)
        return DEST_ABRP_API;
    return DEST_DEFAULT;
}

static destination_type_t json_destination_type(const cJSON *obj, const char *key, destination_type_t default_value)
{
    if (!obj || !key)
        return default_value;
    const cJSON *item = cJSON_GetObjectItem((cJSON *)obj, key);
    const char *s = json_get_string(item);
    return s ? destination_type_from_string(s) : default_value;
}

// Forward declarations for CAN filters parsing/merge (implemented later in file)
static void autopid_cfg_append_can_filters_from_array(autopid_config_t *cfg, const cJSON *can_filters_arr, bool is_vehicle_specific);

static sensor_type_t json_sensor_type(const cJSON *obj, const char *key, sensor_type_t default_value)
{
    if (!obj || !key)
        return default_value;
    const cJSON *item = cJSON_GetObjectItem((cJSON *)obj, key);
    const char *s = json_get_string(item);
    if (!s)
        return default_value;
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
    if (!out_param || !param_obj)
        return;

    out_param->name = json_strdup_key_or_default(param_obj, name_key, "none");
    out_param->expression = json_strdup_key_or_default(param_obj, expr_key, "none");
    out_param->unit = json_strdup_key_or_default(param_obj, unit_key, "none");
    out_param->class = json_strdup_key_or_default(param_obj, class_key, "none");
    out_param->sensor_type = json_sensor_type(param_obj, sensor_type_key, SENSOR);

    out_param->min = json_item_to_float(min_key ? cJSON_GetObjectItem((cJSON *)param_obj, min_key) : NULL, FLT_MAX);
    out_param->max = json_item_to_float(max_key ? cJSON_GetObjectItem((cJSON *)param_obj, max_key) : NULL, FLT_MAX);
    out_param->period = json_item_to_u32(period_key ? cJSON_GetObjectItem((cJSON *)param_obj, period_key) : NULL, 0);

    out_param->destination = json_strdup_key_or_default(param_obj, destination_key, "none");
    out_param->destination_type = json_destination_type(param_obj, destination_type_key, DEST_DEFAULT);

    out_param->timer = 0;
    out_param->value = FLT_MAX;
    out_param->failed = false;
}

static bool parse_frame_id(const cJSON *frame_id_item, uint32_t *out_frame_id)
{
    if (out_frame_id == NULL || frame_id_item == NULL)
    {
        return false;
    }

    if (cJSON_IsNumber(frame_id_item))
    {
        if (frame_id_item->valuedouble < 0)
        {
            return false;
        }
        *out_frame_id = (uint32_t)frame_id_item->valuedouble;
        return true;
    }

    if (cJSON_IsString(frame_id_item) && frame_id_item->valuestring != NULL)
    {
        const char *s = frame_id_item->valuestring;
        int base = 16;
        if (s[0] == '0' && (s[1] == 'x' || s[1] == 'X'))
        {
            base = 0;
        }
        char *endptr = NULL;
        unsigned long v = strtoul(s, &endptr, base);
        if (endptr == s)
        {
            return false;
        }
        *out_frame_id = (uint32_t)v;
        return true;
    }

    return false;
}

static cJSON *load_json_root_from_mount(const char *filename)
{
    if (!filename)
        return NULL;

    char path[128];
    snprintf(path, sizeof(path), FS_MOUNT_POINT "/%s", filename);

    FILE *f = fopen(path, "r");
    if (!f)
    {
        ESP_LOGW(TAG, "Failed to open JSON file: %s", path);
        return NULL;
    }

    cJSON *root = parse_json_file(f);
    fclose(f);

    if (!root)
    {
        ESP_LOGW(TAG, "Failed to parse JSON file: %s", path);
    }
    else
    {
        ESP_LOGI(TAG, "Loaded JSON file: %s", path);
    }
    return root;
}

static int count_car_data_pids(void)
{
    int count = 0;
    cJSON *root = load_json_root_from_mount("car_data.json");
    if (!root)
        return 0;

    cJSON *cars = cJSON_GetObjectItem(root, "cars");
    cJSON *car = cars ? cJSON_GetArrayItem(cars, 0) : NULL;
    cJSON *pids = car ? cJSON_GetObjectItem(car, "pids") : NULL;
    if (pids && cJSON_IsArray(pids))
    {
        count = cJSON_GetArraySize(pids);
    }

    cJSON_Delete(root);
    return count;
}

static int count_auto_pid_pids(void)
{
    int count = 0;
    cJSON *root = load_json_root_from_mount("auto_pid.json");
    if (!root)
        return 0;

    cJSON *pids = cJSON_GetObjectItem(root, "pids");
    cJSON *std_pids = cJSON_GetObjectItem(root, "std_pids");

    if (pids && cJSON_IsArray(pids))
    {
        count += cJSON_GetArraySize(pids);
    }
    if (std_pids && cJSON_IsArray(std_pids))
    {
        count += cJSON_GetArraySize(std_pids);
    }

    cJSON_Delete(root);
    return count;
}

static void parse_auto_pid_json(autopid_config_t *autopid_config, int *pid_index)
{
    if (!autopid_config || !pid_index)
        return;

    cJSON *root = load_json_root_from_mount("auto_pid.json");
    if (!root)
        return;

    int idx = *pid_index;

    cJSON *init_item = cJSON_GetObjectItem(root, "initialisation");
    cJSON *grouping_item = cJSON_GetObjectItem(root, "grouping");
    cJSON *webhook_data_mode_item = cJSON_GetObjectItem(root, "webhook_data_mode");
    cJSON *car_model_item = cJSON_GetObjectItem(root, "car_model");
    cJSON *ecu_protocol_item = cJSON_GetObjectItem(root, "ecu_protocol");
    cJSON *ha_discovery_item = cJSON_GetObjectItem(root, "ha_discovery");
    cJSON *cycle_item = cJSON_GetObjectItem(root, "cycle");
    cJSON *standard_pids_item = cJSON_GetObjectItem(root, "standard_pids");
    cJSON *specific_pids_item = cJSON_GetObjectItem(root, "car_specific");
    cJSON *can_filters_item = cJSON_GetObjectItem(root, "can_filters");
    cJSON *group_destination_item = cJSON_GetObjectItem(root, "destination");   // legacy single destination
    cJSON *group_dest_type_item = cJSON_GetObjectItem(root, "group_dest_type"); // legacy single type
    cJSON *destinations_array = cJSON_GetObjectItem(root, "destinations");      // new multi-destination array
    cJSON *group_api_token_item = cJSON_GetObjectItem(root, "group_api_token"); // legacy api token

    if (init_item && init_item->valuestring)
    {
        autopid_config->custom_init = normalize_init_string(init_item->valuestring);
    }
    else
    {
        autopid_config->custom_init = NULL;
    }

    autopid_config->grouping = (grouping_item && grouping_item->valuestring && strlen(grouping_item->valuestring) > 1) ? strdup_psram(grouping_item->valuestring) : strdup_psram("disable");
    autopid_config->webhook_data_mode = (webhook_data_mode_item && webhook_data_mode_item->valuestring && strlen(webhook_data_mode_item->valuestring) > 1) ? strdup_psram(webhook_data_mode_item->valuestring) : strdup_psram("changed");
    autopid_config->vehicle_model = car_model_item ? strdup_psram(car_model_item->valuestring) : NULL;
    autopid_config->std_ecu_protocol = ecu_protocol_item ? strdup_psram(ecu_protocol_item->valuestring) : NULL;
    autopid_config->ha_discovery_en = ha_discovery_item ? (strcmp(ha_discovery_item->valuestring, "enable") == 0) : false;

    if (cycle_item && cycle_item->valuestring && strlen(cycle_item->valuestring) > 0)
        autopid_config->cycle = atoi(cycle_item->valuestring);
    else if (cycle_item && cycle_item->valueint)
        autopid_config->cycle = cycle_item->valueint;
    else
        autopid_config->cycle = 10000;

    autopid_config->pid_std_en = standard_pids_item ? (strcmp(standard_pids_item->valuestring, "enable") == 0) : false;
    autopid_config->pid_specific_en = specific_pids_item ? (strcmp(specific_pids_item->valuestring, "enable") == 0) : false;
    autopid_config->group_destination = group_destination_item ? strdup_psram(group_destination_item->valuestring) : NULL;
    // Map legacy group_dest_type to enum (for backward compatibility)
    if (group_dest_type_item && group_dest_type_item->valuestring)
    {
        autopid_config->group_destination_type = destination_type_from_string(group_dest_type_item->valuestring);
    }
    else
    {
        autopid_config->group_destination_type = DEST_DEFAULT;
    }

    // Parse new destinations array (up to a reasonable max, e.g. 6)
    autopid_config->destinations = NULL;
    autopid_config->destinations_count = 0;
    if (destinations_array && cJSON_IsArray(destinations_array))
    {
        int count = cJSON_GetArraySize(destinations_array);
        if (count > 0)
        {
            autopid_config->destinations = (group_destination_t *)heap_caps_calloc(
                count,
                sizeof(group_destination_t),
                MALLOC_CAP_8BIT | MALLOC_CAP_SPIRAM);
            if (autopid_config->destinations)
            {
                autopid_config->destinations_count = count;
                for (int di = 0; di < count; di++)
                {
                    cJSON *d = cJSON_GetArrayItem(destinations_array, di);
                    if (!d)
                        continue;
                    group_destination_t *gd = &autopid_config->destinations[di];
                    cJSON *type_item = cJSON_GetObjectItem(d, "type");
                    cJSON *dest_item = cJSON_GetObjectItem(d, "destination");
                    cJSON *cycle_item2 = cJSON_GetObjectItem(d, "cycle");
                    cJSON *api_token_item = cJSON_GetObjectItem(d, "api_token");
                    cJSON *cert_set_item = cJSON_GetObjectItem(d, "cert_set");
                    cJSON *enabled_item = cJSON_GetObjectItem(d, "enabled");
                    cJSON *auth_item = cJSON_GetObjectItem(d, "auth");
                    cJSON *qp_arr = cJSON_GetObjectItem(d, "query_params");

                    const char *type_str = type_item && cJSON_IsString(type_item) ? type_item->valuestring : "Default";
                    gd->type = destination_type_from_string(type_str);
                    gd->destination = dest_item && cJSON_IsString(dest_item) ? strdup_psram(dest_item->valuestring) : NULL;

                    // Ensure scheme prefix for HTTP/HTTPS destinations if missing
                    if (gd->destination && (gd->type == DEST_HTTP || gd->type == DEST_HTTPS || gd->type == DEST_ABRP_API))
                    {
                        bool has_http = (strncmp(gd->destination, "http://", 7) == 0);
                        bool has_https = (strncmp(gd->destination, "https://", 8) == 0);
                        if (!has_http && !has_https)
                        {
                            const char *prefix = (gd->type == DEST_HTTPS || gd->type == DEST_ABRP_API) ? "https://" : "http://";
                            size_t new_len = strlen(prefix) + strlen(gd->destination) + 1;
                            char *with_prefix = (char *)heap_caps_malloc(new_len, MALLOC_CAP_8BIT | MALLOC_CAP_SPIRAM);
                            if (with_prefix)
                            {
                                strcpy(with_prefix, prefix);
                                strcat(with_prefix, gd->destination);
                                free(gd->destination);
                                gd->destination = with_prefix;
                            }
                        }
                    }

                    if (cycle_item2 && cycle_item2->valuestring && strlen(cycle_item2->valuestring) > 0)
                        gd->cycle = (uint32_t)atoi(cycle_item2->valuestring);
                    else if (cycle_item2 && cycle_item2->valueint)
                        gd->cycle = (uint32_t)cycle_item2->valueint;
                    else
                        gd->cycle = 10000;

                    gd->api_token = api_token_item && cJSON_IsString(api_token_item) ? strdup_psram(api_token_item->valuestring) : NULL;
                    if (gd->type != DEST_ABRP_API)
                    {
                        gd->cert_set = cert_set_item && cJSON_IsString(cert_set_item) ? strdup_psram(cert_set_item->valuestring) : strdup_psram("default");
                    }
                    else
                    {
                        gd->cert_set = strdup_psram("default");
                    }
                    gd->enabled = enabled_item && cJSON_IsBool(enabled_item) ? cJSON_IsTrue(enabled_item) : false;

                    // Parse optional auth
                    gd->auth.type = DEST_AUTH_NONE;
                    if (auth_item && cJSON_IsObject(auth_item))
                    {
                        cJSON *atype = cJSON_GetObjectItem(auth_item, "type");
                        if (atype && cJSON_IsString(atype))
                        {
                            const char *ts = atype->valuestring;
                            if (strcmp(ts, "bearer") == 0)
                                gd->auth.type = DEST_AUTH_BEARER;
                            else if (strcmp(ts, "api_key_header") == 0)
                                gd->auth.type = DEST_AUTH_API_KEY_HEADER;
                            else if (strcmp(ts, "api_key_query") == 0)
                                gd->auth.type = DEST_AUTH_API_KEY_QUERY;
                            else if (strcmp(ts, "basic") == 0)
                                gd->auth.type = DEST_AUTH_BASIC;
                            else
                                gd->auth.type = DEST_AUTH_NONE;
                        }
                        cJSON *bearer = cJSON_GetObjectItem(auth_item, "bearer");
                        if (bearer && cJSON_IsString(bearer) && bearer->valuestring && bearer->valuestring[0])
                            gd->auth.bearer = strdup_psram(bearer->valuestring);
                        cJSON *hn = cJSON_GetObjectItem(auth_item, "api_key_header_name");
                        if (hn && cJSON_IsString(hn) && hn->valuestring && hn->valuestring[0])
                            gd->auth.api_key_header_name = strdup_psram(hn->valuestring);
                        cJSON *ak = cJSON_GetObjectItem(auth_item, "api_key");
                        if (ak && cJSON_IsString(ak) && ak->valuestring)
                            gd->auth.api_key = strdup_psram(ak->valuestring);
                        cJSON *qn = cJSON_GetObjectItem(auth_item, "api_key_query_name");
                        if (qn && cJSON_IsString(qn) && qn->valuestring && qn->valuestring[0])
                            gd->auth.api_key_query_name = strdup_psram(qn->valuestring);
                        cJSON *bu = cJSON_GetObjectItem(auth_item, "basic_username");
                        if (bu && cJSON_IsString(bu) && bu->valuestring)
                            gd->auth.basic_username = strdup_psram(bu->valuestring);
                        cJSON *bp = cJSON_GetObjectItem(auth_item, "basic_password");
                        if (bp && cJSON_IsString(bp) && bp->valuestring)
                            gd->auth.basic_password = strdup_psram(bp->valuestring);
                    }
                    else
                    {
                        // Back-compat: if HTTP/HTTPS and api_token exists, set bearer auth implicitly
                        if ((gd->type == DEST_HTTP || gd->type == DEST_HTTPS) && gd->api_token && strlen(gd->api_token) > 0)
                        {
                            gd->auth.type = DEST_AUTH_BEARER;
                            gd->auth.bearer = strdup_psram(gd->api_token);
                        }
                    }

                    // Parse optional query params array
                    if (qp_arr && cJSON_IsArray(qp_arr))
                    {
                        int qn = cJSON_GetArraySize(qp_arr);
                        if (qn > 0)
                        {
                            gd->query_params = (dest_query_kv_t *)heap_caps_calloc(qn, sizeof(dest_query_kv_t), MALLOC_CAP_8BIT | MALLOC_CAP_SPIRAM);
                            if (gd->query_params)
                            {
                                gd->query_params_count = qn;
                                for (int qi = 0; qi < qn; ++qi)
                                {
                                    cJSON *kv = cJSON_GetArrayItem(qp_arr, qi);
                                    if (!kv || !cJSON_IsObject(kv))
                                        continue;
                                    cJSON *k = cJSON_GetObjectItem(kv, "key");
                                    cJSON *v = cJSON_GetObjectItem(kv, "value");
                                    if (k && cJSON_IsString(k) && k->valuestring)
                                        gd->query_params[qi].key = strdup_psram(k->valuestring);
                                    if (v && cJSON_IsString(v) && v->valuestring)
                                        gd->query_params[qi].value = strdup_psram(v->valuestring);
                                }
                            }
                        }
                    }

                    // Normalize cycle: treat 0 as default 10000 ms
                    if (gd->cycle == 0)
                        gd->cycle = 10000;
                    gd->publish_timer = 0; // immediate eligibility
                    gd->consec_failures = 0;
                    gd->backoff_ms = 0;
                }

                // Compact array: keep only entries with valid destination and enabled
                uint32_t write_idx = 0;
                for (uint32_t read_idx = 0; read_idx < autopid_config->destinations_count; ++read_idx)
                {
                    group_destination_t *src = &autopid_config->destinations[read_idx];
                    if (src->enabled && src->destination && strlen(src->destination) > 0)
                    {
                        if (write_idx != read_idx)
                        {
                            autopid_config->destinations[write_idx] = *src;
                            memset(src, 0, sizeof(group_destination_t));
                        }
                        write_idx++;
                    }
                }
                autopid_config->destinations_count = write_idx;
            }
        }
    }
    else
    {
        // No new destinations array: fabricate one from legacy fields if present
        if (autopid_config->group_destination || group_dest_type_item)
        {
            autopid_config->destinations = (group_destination_t *)heap_caps_calloc(
                1,
                sizeof(group_destination_t),
                MALLOC_CAP_8BIT | MALLOC_CAP_SPIRAM);
            if (autopid_config->destinations)
            {
                autopid_config->destinations_count = 1;
                autopid_config->destinations[0].type = autopid_config->group_destination_type;
                autopid_config->destinations[0].destination = autopid_config->group_destination ? strdup_psram(autopid_config->group_destination) : NULL;
                if (autopid_config->destinations[0].destination && (autopid_config->destinations[0].type == DEST_HTTP || autopid_config->destinations[0].type == DEST_HTTPS))
                {
                    bool has_http = (strncmp(autopid_config->destinations[0].destination, "http://", 7) == 0);
                    bool has_https = (strncmp(autopid_config->destinations[0].destination, "https://", 8) == 0);
                    if (!has_http && !has_https)
                    {
                        const char *prefix = (autopid_config->destinations[0].type == DEST_HTTPS) ? "https://" : "http://";
                        size_t new_len = strlen(prefix) + strlen(autopid_config->destinations[0].destination) + 1;
                        char *with_prefix = (char *)heap_caps_malloc(new_len, MALLOC_CAP_8BIT | MALLOC_CAP_SPIRAM);
                        if (with_prefix)
                        {
                            strcpy(with_prefix, prefix);
                            strcat(with_prefix, autopid_config->destinations[0].destination);
                            free(autopid_config->destinations[0].destination);
                            autopid_config->destinations[0].destination = with_prefix;
                        }
                    }
                }
                autopid_config->destinations[0].cycle = autopid_config->cycle;
                if (autopid_config->destinations[0].cycle == 0)
                {
                    autopid_config->destinations[0].cycle = 10000;
                }
                autopid_config->destinations[0].api_token = group_api_token_item && group_api_token_item->valuestring ? strdup_psram(group_api_token_item->valuestring) : NULL;
                autopid_config->destinations[0].cert_set = strdup_psram("default");
                autopid_config->destinations[0].enabled = false;
                if ((autopid_config->destinations[0].type == DEST_HTTP || autopid_config->destinations[0].type == DEST_HTTPS) && autopid_config->destinations[0].api_token)
                {
                    autopid_config->destinations[0].auth.type = DEST_AUTH_BEARER;
                    autopid_config->destinations[0].auth.bearer = strdup_psram(autopid_config->destinations[0].api_token);
                }
                else
                {
                    autopid_config->destinations[0].auth.type = DEST_AUTH_NONE;
                }
                autopid_config->destinations[0].publish_timer = 0;
                autopid_config->destinations[0].consec_failures = 0;
                autopid_config->destinations[0].backoff_ms = 0;
                if (!autopid_config->destinations[0].destination || strlen(autopid_config->destinations[0].destination) == 0)
                {
                    autopid_config->destinations_count = 0;
                }
            }
        }
    }

    // Load custom pids
    cJSON *pids = cJSON_GetObjectItem(root, "pids");
    if (pids)
    {
        cJSON *pid;
        cJSON_ArrayForEach(pid, pids)
        {
            pid_data_t *curr_pid = &autopid_config->pids[idx];

            cJSON *init_item2 = cJSON_GetObjectItem(pid, "Init");
            cJSON *pid_item = cJSON_GetObjectItem(pid, "PID");
            cJSON *period_item = cJSON_GetObjectItem(pid, "Period");
            cJSON *rxheader_item = cJSON_GetObjectItem(pid, "header");

            if (cJSON_GetArraySize(pids) > 0)
            {
                autopid_config->pid_custom_en = true;
            }

            curr_pid->cmd = pid_item ? (char *)heap_caps_malloc(strlen(pid_item->valuestring) + 2, MALLOC_CAP_8BIT | MALLOC_CAP_SPIRAM) : NULL;
            if (curr_pid->cmd && pid_item && strlen(pid_item->valuestring) > 1)
            {
                strcpy(curr_pid->cmd, pid_item->valuestring);
                strcat(curr_pid->cmd, "\r");
            }

            curr_pid->init = NULL;
            if (init_item2 && init_item2->valuestring)
            {
                curr_pid->init = normalize_init_string(init_item2->valuestring);
            }

            if (period_item && period_item->valuestring && strlen(period_item->valuestring) > 0)
                curr_pid->period = atoi(period_item->valuestring);
            else if (period_item && period_item->valueint)
                curr_pid->period = (uint32_t)period_item->valueint;
            else
                curr_pid->period = 10000;

            curr_pid->rxheader = rxheader_item ? strdup_psram(rxheader_item->valuestring) : NULL;
            curr_pid->pid_type = PID_CUSTOM;

            curr_pid->parameters_count = 1;
            curr_pid->parameters = (parameter_t *)heap_caps_calloc(1, sizeof(parameter_t), MALLOC_CAP_8BIT | MALLOC_CAP_SPIRAM);
            if (curr_pid->parameters)
            {
                parse_parameter_object(
                    curr_pid->parameters,
                    pid,
                    "Name",
                    "Expression",
                    "unit",
                    "class",
                    "sensor_type",
                    "MinValue",
                    "MaxValue",
                    "Period",
                    "Send_to",
                    "Type");
            }

            idx++;
        }
    }

    // Load standard pids
    cJSON *std_pids = cJSON_GetObjectItem(root, "std_pids");
    if (std_pids)
    {
        cJSON *pid;
        cJSON_ArrayForEach(pid, std_pids)
        {
            pid_data_t *curr_pid = &autopid_config->pids[idx];
            curr_pid->pid_type = PID_STD;

            char std_init_buf[64];
            int is_protocol_68 = 1;
            int is_protocol_79 = 0;
            const char *sh_value = "";

            curr_pid->parameters_count = 1;
            curr_pid->parameters = (parameter_t *)heap_caps_calloc(1, sizeof(parameter_t), MALLOC_CAP_8BIT | MALLOC_CAP_SPIRAM);
            if (curr_pid->parameters)
            {
                cJSON *rxheader_item = cJSON_GetObjectItem(pid, "ReceiveHeader");

                parse_parameter_object(
                    curr_pid->parameters,
                    pid,
                    "Name",
                    NULL,
                    NULL,
                    NULL,
                    "sensor_type",
                    NULL,
                    NULL,
                    "Period",
                    "Send_to",
                    "Type");
                if (curr_pid->parameters->period == 0)
                {
                    curr_pid->parameters->period = 10000;
                }
                curr_pid->parameters->min = FLT_MAX;
                curr_pid->parameters->max = FLT_MAX;

                curr_pid->rxheader = rxheader_item ? strdup_psram(rxheader_item->valuestring) : NULL;

                if (autopid_config->std_ecu_protocol)
                {
                    is_protocol_68 = (strcmp(autopid_config->std_ecu_protocol, "6") == 0 ||
                                      strcmp(autopid_config->std_ecu_protocol, "8") == 0);
                    is_protocol_79 = (strcmp(autopid_config->std_ecu_protocol, "7") == 0 ||
                                      strcmp(autopid_config->std_ecu_protocol, "9") == 0);
                }

                if (is_protocol_68)
                {
                    sh_value = "7DF";
                }
                else if (is_protocol_79)
                {
                    sh_value = "18DB33F1";
                }

                if (is_protocol_68 || is_protocol_79)
                {
                    if (curr_pid->rxheader != NULL && strlen(curr_pid->rxheader) > 0)
                    {
                        snprintf(std_init_buf, sizeof(std_init_buf), "ATTP%s\rATSH%s\rATCRA%s\r",
                                 autopid_config->std_ecu_protocol, sh_value, curr_pid->rxheader);
                    }
                    else
                    {
                        snprintf(std_init_buf, sizeof(std_init_buf), "ATTP%s\rATSH%s\rATCRA\r",
                                 autopid_config->std_ecu_protocol, sh_value);
                    }
                    autopid_config->standard_init = strdup_psram(std_init_buf);
                }
                else
                {
                    autopid_config->standard_init = strdup_psram("ATTP0\r  ");
                }

                if (curr_pid->parameters->name != NULL && strlen(curr_pid->parameters->name) > 0)
                {
                    const std_pid_t *pid_info = get_pid_from_string(curr_pid->parameters->name);
                    if (pid_info)
                    {
                        for (int i = 0; i < pid_info->num_params; i++)
                        {
                            if (strcmp(pid_info->params[i].name, strchr(curr_pid->parameters->name, '-') + 1) == 0)
                            {
                                curr_pid->parameters->class = strdup_psram(pid_info->params[i].class);
                                curr_pid->parameters->unit = strdup_psram(pid_info->params[i].unit);
                                char pid_hex[3];
                                strncpy(pid_hex, curr_pid->parameters->name, 2);
                                pid_hex[2] = '\0';

                                curr_pid->cmd = heap_caps_malloc(8, MALLOC_CAP_8BIT | MALLOC_CAP_SPIRAM);
                                if (curr_pid->cmd)
                                {
                                    sprintf(curr_pid->cmd, "01%s\r", pid_hex);
                                }
                            }
                        }
                    }
                }
            }

            idx++;
        }
    }

    // Parse optional custom CAN filters (top-level can_filters)
    // Note: filters from car_data.json are loaded later and appended.
    // IMPORTANT: do this once per config load (not once per PID entry).
    if (can_filters_item && cJSON_IsArray(can_filters_item))
    {
        autopid_cfg_append_can_filters_from_array(autopid_config, can_filters_item, false);
    }

    *pid_index = idx;
    cJSON_Delete(root);
}

// ---- CAN filters parsing/merge helpers ----
// These are file-local helpers but need to be callable from parse_auto_pid_json above.
// We expose them via a private symbol to avoid re-ordering large parts of this file.

static void parse_can_filter_object(can_filter_t *out, const cJSON *filter)
{
    if (!out || !filter || !cJSON_IsObject(filter))
    {
        return;
    }

    uint32_t frame_id = 0;
    if (!parse_frame_id(cJSON_GetObjectItem((cJSON *)filter, "frame_id"), &frame_id))
    {
        return;
    }

    out->frame_id = frame_id;
    out->is_extended = (frame_id > 0x7FF);

    cJSON *params = cJSON_GetObjectItem((cJSON *)filter, "parameters");
    if (!params || !cJSON_IsArray(params))
    {
        return;
    }

    int param_count = cJSON_GetArraySize(params);
    if (param_count <= 0)
    {
        return;
    }

    out->parameters_count = (uint32_t)param_count;
    out->parameters = (parameter_t *)heap_caps_calloc((size_t)param_count, sizeof(parameter_t), MALLOC_CAP_8BIT | MALLOC_CAP_SPIRAM);
    if (!out->parameters)
    {
        out->parameters_count = 0;
        return;
    }

    cJSON *param = NULL;
    int pi = 0;
    cJSON_ArrayForEach(param, params)
    {
        if (!param || !cJSON_IsObject(param) || pi >= param_count)
        {
            pi++;
            continue;
        }

        parse_parameter_object(
            &out->parameters[pi],
            param,
            "name",
            "expression",
            "unit",
            "class",
            "sensor_type",
            "min",
            "max",
            "period",
            "send_to",
            "type");

        if (out->parameters[pi].period == 0)
        {
            out->parameters[pi].period = 5000;
        }

        pi++;
    }
}

static void autopid_cfg_append_can_filters_from_array(autopid_config_t *cfg, const cJSON *can_filters_arr, bool is_vehicle_specific)
{
    if (!cfg || !can_filters_arr || !cJSON_IsArray(can_filters_arr))
    {
        return;
    }

    int add_count = cJSON_GetArraySize((cJSON *)can_filters_arr);
    if (add_count <= 0)
    {
        return;
    }

    uint32_t old_count = cfg->can_filters_count;
    uint32_t new_count = old_count + (uint32_t)add_count;

    can_filter_t *new_arr = (can_filter_t *)heap_caps_calloc((size_t)new_count, sizeof(can_filter_t), MALLOC_CAP_8BIT | MALLOC_CAP_SPIRAM);
    if (!new_arr)
    {
        return;
    }

    // Shallow-copy old entries (struct holds pointers); we don't free nested pointers.
    if (cfg->can_filters && old_count > 0)
    {
        memcpy(new_arr, cfg->can_filters, sizeof(can_filter_t) * old_count);
        free(cfg->can_filters);
    }

    // Parse appended entries into tail
    for (int i = 0; i < add_count; i++)
    {
        cJSON *filter = cJSON_GetArrayItem((cJSON *)can_filters_arr, i);
        parse_can_filter_object(&new_arr[old_count + (uint32_t)i], filter);
        new_arr[old_count + (uint32_t)i].is_vehicle_specific = is_vehicle_specific;
    }

    cfg->can_filters = new_arr;
    cfg->can_filters_count = new_count;
}

static void parse_car_data_json(autopid_config_t *autopid_config, int *pid_index)
{
    if (!autopid_config || !pid_index)
        return;

    cJSON *root = load_json_root_from_mount("car_data.json");
    if (!root)
        return;

    int idx = *pid_index;

    cJSON *cars = cJSON_GetObjectItem(root, "cars");
    if (cars)
    {
        cJSON *car = cJSON_GetArrayItem(cars, 0);
        if (car)
        {
            cJSON *init_item = cJSON_GetObjectItem(car, "init");
            if (init_item && cJSON_IsString(init_item) && init_item->valuestring)
            {
                autopid_config->specific_init = normalize_init_string(init_item->valuestring);
                ESP_LOGI(TAG, "car_data init='%s' -> specific_init=%p", init_item->valuestring, (void *)autopid_config->specific_init);
            }
            else
            {
                ESP_LOGW(TAG, "car_data 'init' missing or not a string");
            }

            cJSON *can_filters = cJSON_GetObjectItem(car, "can_filters");
            if (can_filters && cJSON_IsArray(can_filters))
            {
                autopid_cfg_append_can_filters_from_array(autopid_config, can_filters, true);
            }

            cJSON *pids = cJSON_GetObjectItem(car, "pids");
            if (pids)
            {
                cJSON *pid;
                cJSON_ArrayForEach(pid, pids)
                {
                    pid_data_t *curr_pid = &autopid_config->pids[idx];
                    cJSON *pid_item = cJSON_GetObjectItem(pid, "pid");
                    cJSON *pid_init_item = cJSON_GetObjectItem(pid, "pid_init");

                    curr_pid->init = NULL;
                    if (pid_init_item && pid_init_item->valuestring)
                    {
                        curr_pid->init = normalize_init_string(pid_init_item->valuestring);
                    }

                    curr_pid->cmd = NULL;
                    if (pid_item && pid_item->valuestring)
                    {
                        size_t cmd_len = strlen(pid_item->valuestring);
                        if (cmd_len > 0)
                        {
                            curr_pid->cmd = (char *)heap_caps_malloc(cmd_len + 2, MALLOC_CAP_8BIT | MALLOC_CAP_SPIRAM);
                            if (curr_pid->cmd)
                            {
                                strncpy(curr_pid->cmd, pid_item->valuestring, cmd_len);
                                curr_pid->cmd[cmd_len] = '\r';
                                curr_pid->cmd[cmd_len + 1] = '\0';
                            }
                        }
                    }

                    curr_pid->pid_type = PID_SPECIFIC;

                    cJSON *params = cJSON_GetObjectItem(pid, "parameters");
                    if (params)
                    {
                        int param_count = cJSON_GetArraySize(params);
                        curr_pid->parameters_count = param_count;
                        curr_pid->parameters = (parameter_t *)heap_caps_calloc(param_count, sizeof(parameter_t), MALLOC_CAP_8BIT | MALLOC_CAP_SPIRAM);
                        cJSON *param;
                        int param_index = 0;
                        cJSON_ArrayForEach(param, params)
                        {
                            if (!param || !cJSON_IsObject(param))
                            {
                                param_index++;
                                continue;
                            }

                            parse_parameter_object(
                                &curr_pid->parameters[param_index],
                                param,
                                "name",
                                "expression",
                                "unit",
                                "class",
                                "sensor_type",
                                "min",
                                "max",
                                "period",
                                "send_to",
                                "type");
                            if (curr_pid->parameters[param_index].period == 0)
                            {
                                curr_pid->parameters[param_index].period = autopid_config->cycle;
                            }
                            param_index++;
                        }
                    }

                    idx++;
                }
            }
        }
    }

    *pid_index = idx;
    cJSON_Delete(root);
}

autopid_config_t *load_autopid_config(void)
{
    int total_pids = 0;
    int car_data_pids = 0;
    int auto_pids = 0;

    car_data_pids = count_car_data_pids();
    auto_pids = count_auto_pid_pids();

    total_pids = car_data_pids + auto_pids;

    ESP_LOGI(TAG, "Allocating memory for %d pids...", total_pids);
    autopid_config_t *autopid_config = (autopid_config_t *)heap_caps_calloc(1, sizeof(autopid_config_t), MALLOC_CAP_8BIT | MALLOC_CAP_SPIRAM);
    if (!autopid_config)
        return NULL;

    autopid_config->last_successful_pid_time = 0;
    autopid_config->can_filters = NULL;
    autopid_config->can_filters_count = 0;

    if (total_pids == 0)
    {
        ESP_LOGE(TAG, "No PIDs found in car_data.json or auto_pid.json");
        return autopid_config;
    }

    autopid_config->pids = (pid_data_t *)heap_caps_calloc(total_pids, sizeof(pid_data_t), MALLOC_CAP_8BIT | MALLOC_CAP_SPIRAM);
    if (!autopid_config->pids)
    {
        free(autopid_config);
        return NULL;
    }
    int pid_index = 0;

    ESP_LOGI(TAG, "Loading auto_pid.json pids...");
    parse_auto_pid_json(autopid_config, &pid_index);

    parse_car_data_json(autopid_config, &pid_index);

    autopid_config->pid_count = total_pids;

    return autopid_config;
}
