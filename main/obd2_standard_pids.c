#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "esp_heap_caps.h"
#include "cJSON.h"
#include "obd2_standard_pids.h"

/**
 * @brief Create JSON array containing all standard OBD2 PIDs information in simple format
 * @return Pointer to JSON string allocated in PSRAM, or NULL on failure
 * @note Caller is responsible for freeing the returned memory using heap_caps_free()
 */
char* create_standard_pids_json(void) {
    cJSON *root = cJSON_CreateArray();
    if (root == NULL) {
        return NULL;
    }

    // Iterate through all possible PID values (0x00 to 0xFF)
    for (int pid_num = 0; pid_num < 256; pid_num++) {
        const std_pid_t* pid = get_pid(pid_num);
        if (pid == NULL || pid->base_name == NULL) {
            continue; // Skip undefined PIDs
        }

        // Add each parameter as a separate object
        for (int param_idx = 0; param_idx < pid->num_params; param_idx++) {
            const std_parameter_t* param = &pid->params[param_idx];
            
            cJSON *param_obj = cJSON_CreateObject();
            if (param_obj == NULL) {
                cJSON_Delete(root);
                return NULL;
            }

            // Create name in format "PID-ParameterName"
            char name_buffer[128];
            if (pid->num_params == 1) {
                // Single parameter: use format "0C-EngineRPM"
                snprintf(name_buffer, sizeof(name_buffer), "%02X-%s", pid_num, param->name);
            } else {
                // Multiple parameters: use format "14-OxySensor1_Volt", "14-OxySensor1_STFT"
                snprintf(name_buffer, sizeof(name_buffer), "%02X-%s", pid_num, param->name);
            }

            cJSON_AddStringToObject(param_obj, "name", name_buffer);
            cJSON_AddStringToObject(param_obj, "unit", param->unit ? param->unit : "");
            cJSON_AddStringToObject(param_obj, "class", param->class ? param->class : "none");

            cJSON_AddItemToArray(root, param_obj);
        }
    }

    // Convert to string
    char *json_string = cJSON_PrintUnformatted(root);
    cJSON_Delete(root);

    if (json_string == NULL) {
        return NULL;
    }

    // Calculate size and allocate in PSRAM
    size_t json_len = strlen(json_string);
    char *psram_json = (char*)heap_caps_malloc(json_len + 1, MALLOC_CAP_SPIRAM);
    
    if (psram_json != NULL) {
        strcpy(psram_json, json_string);
    }
    
    // Free the temporary cJSON string
    cJSON_free(json_string);

    return psram_json;
}

/**
 * @brief Get standard PIDs information as JSON string
 * @return Pointer to JSON string in PSRAM, or NULL on failure
 * @note This function caches the result. Subsequent calls return the same pointer.
 */
char* get_standard_pids_json(void) {
    static char* cached_json = NULL;
    
    if (cached_json == NULL) {
        cached_json = create_standard_pids_json();
    }
    
    return cached_json;
}

/**
 * @brief Free the standard PIDs JSON string from PSRAM
 */
void free_standard_pids_json(void) {
    static char* cached_json = NULL;
    
    // Access the static variable through a function call
    char* current_json = get_standard_pids_json();
    if (current_json != NULL) {
        heap_caps_free(current_json);
        cached_json = NULL;
    }
}