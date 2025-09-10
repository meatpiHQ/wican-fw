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

 #include <string.h>
 #include <stdlib.h>
 #include <stdio.h>
 #include <inttypes.h>
 #include "freertos/FreeRTOS.h"
 #include "freertos/task.h"
 #include "esp_log.h"
 #include "esp_tls.h"
 #include "esp_crt_bundle.h"
 #include "esp_system.h"
 #include "esp_http_client.h"
 #include "https_client_mgr.h"
 #include "mbedtls/base64.h"
 
 static const char *TAG = "HTTPS_CLIENT_MGR";
 
 #define DEFAULT_TIMEOUT_MS 10000
 #define MAX_HTTP_RECV_BUFFER (1024*8)  
 #define MAX_HTTP_OUTPUT_BUFFER 2048
 #define BASE64_ENC_OUT_LEN(n) ((((n) + 2) / 3) * 4)
 
 /**
  * @brief Helper function to read HTTP response data
  */
 static esp_err_t read_http_response(esp_http_client_handle_t client, https_client_mgr_response_t *response)
 {
     // Get status code
     response->status_code = esp_http_client_get_status_code(client);
     response->is_success = (response->status_code >= 200 && response->status_code < 300);
     
     // Get content length
     int content_length = esp_http_client_get_content_length(client);
     if (content_length <= 0) {
         content_length = MAX_HTTP_OUTPUT_BUFFER; // Default size if unknown
     }
     
     // Allocate memory for response data
    response->data = heap_caps_malloc(content_length + 1, MALLOC_CAP_SPIRAM|MALLOC_CAP_8BIT);
     if (!response->data) {
         ESP_LOGE(TAG, "Failed to allocate memory for response data");
         return ESP_ERR_NO_MEM;
     }
     
     // Read response data
     response->data_len = 0;
     int read_len;
     size_t buffer_size = content_length + 1;
     
     do {
         // Check if buffer needs to be expanded
         if (response->data_len + MAX_HTTP_RECV_BUFFER > buffer_size) {
             buffer_size *= 2;
             char *new_buffer = heap_caps_realloc(response->data, buffer_size, MALLOC_CAP_SPIRAM|MALLOC_CAP_8BIT);
             if (!new_buffer) {
                 ESP_LOGE(TAG, "Failed to reallocate response buffer");
                 free(response->data);
                 response->data = NULL;
                 return ESP_ERR_NO_MEM;
             }
             response->data = new_buffer;
         }
         
         read_len = esp_http_client_read(client, 
                                        response->data + response->data_len, 
                                        MAX_HTTP_RECV_BUFFER);
         
         if (read_len > 0) {
             response->data_len += read_len;
         }
     } while (read_len > 0);
     
     // Null terminate the response data
     if (response->data) {
         response->data[response->data_len] = '\0';
     }
     
     ESP_LOGI(TAG, "Response received: status code %d, %zu bytes", 
              response->status_code, response->data_len);
     
     return ESP_OK;
 }
 
 /**
  * @brief Initialize the HTTPS client manager
  */
 esp_err_t https_client_mgr_init(void)
 {
     ESP_LOGI(TAG, "Initializing HTTPS client manager");
     // Nothing special needed for initialization - but keeping the function
     // for future expansion
     return ESP_OK;
 }
 
 /**
  * @brief Deinitialize the HTTPS client manager
  */
 esp_err_t https_client_mgr_deinit(void)
 {
     ESP_LOGI(TAG, "Deinitializing HTTPS client manager");
     // Nothing special needed for deinitialization - but keeping the function
     // for future expansion
     return ESP_OK;
 }

/**
 * @brief Append query parameters to a URL (simple helper; assumes input URL length < 1024)
 */
static char *append_query_params(const char *base_url,
                                 const https_client_mgr_query_kv_t *params,
                                 size_t count)
{
    if (!base_url || !params || count == 0) {
        return strdup(base_url ? base_url : "");
    }

    size_t base_len = strlen(base_url);
    size_t cap = base_len + 1 + count * 64; // rough estimate
    char *out = heap_caps_malloc(cap, MALLOC_CAP_SPIRAM|MALLOC_CAP_8BIT);
    if (!out) return NULL;
    strcpy(out, base_url);

    bool has_q = (strchr(base_url, '?') != NULL);
    for (size_t i = 0; i < count; ++i) {
        const char *k = params[i].key ? params[i].key : "";
        const char *v = params[i].value ? params[i].value : "";
        size_t need = strlen(out) + 1 + strlen(k) + 1 + strlen(v) + 1; // +sep +k + '=' + v + '\0'
        if (need > cap) {
            cap = need + 64;
            char *tmp = heap_caps_realloc(out, cap, MALLOC_CAP_SPIRAM|MALLOC_CAP_8BIT);
            if (!tmp) { free(out); return NULL; }
            out = tmp;
        }
        strcat(out, has_q ? "&" : "?");
        has_q = true;
        strcat(out, k);
        strcat(out, "=");
        strcat(out, v);
    }
    return out;
}

/**
 * @brief Build Basic auth header value into provided buffer as "Basic <base64>"
 */
static esp_err_t build_basic_auth_value(const char *user, const char *pass,
                                        char out[256])
{
    if (!user) user = "";
    if (!pass) pass = "";
    size_t up_len = strlen(user) + 1 + strlen(pass);
    char *tmp = heap_caps_malloc(up_len + 1, MALLOC_CAP_SPIRAM|MALLOC_CAP_8BIT);
    if (!tmp) return ESP_ERR_NO_MEM;
    sprintf(tmp, "%s:%s", user, pass);

    size_t enc_len = BASE64_ENC_OUT_LEN(up_len);
    char *enc = heap_caps_malloc(enc_len + 1, MALLOC_CAP_SPIRAM|MALLOC_CAP_8BIT);
    if (!enc) { free(tmp); return ESP_ERR_NO_MEM; }

    int outlen = 0;
    // Use ESP-IDF base64 encoder
    esp_err_t err = mbedtls_base64_encode((unsigned char *)enc, enc_len + 1,
                                          (size_t *)&outlen,
                                          (const unsigned char *)tmp,
                                          up_len) == 0 ? ESP_OK : ESP_FAIL;
    if (err == ESP_OK) {
        enc[outlen] = '\0';
        snprintf(out, 256, "Basic %s", enc);
    }
    free(tmp);
    free(enc);
    return err;
}
 
 /**
  * @brief Perform an HTTPS request with advanced configuration
  */
 esp_err_t https_client_mgr_request(const https_client_mgr_config_t *config, https_client_mgr_method_t method,
                               const char *data, size_t data_len, const char **headers,
                               https_client_mgr_response_t *response)
 {
     if (!config || !config->url || !response) {
         return ESP_ERR_INVALID_ARG;
     }
     
     // Configure HTTP client
     esp_http_client_config_t http_config = {
         .url = config->url,
         .timeout_ms = config->timeout_ms > 0 ? config->timeout_ms : DEFAULT_TIMEOUT_MS,
         .is_async = config->non_blocking,
         .skip_cert_common_name_check = config->skip_common_name
     };
     
     // Configure TLS certificate validation
     if (config->use_crt_bundle) {
         http_config.crt_bundle_attach = esp_crt_bundle_attach;
     } else if (config->use_global_ca_store) {
         http_config.use_global_ca_store = true;
     } else if (config->cert_pem != NULL) {
         http_config.cert_pem = (char *)config->cert_pem;
         http_config.cert_len = config->cert_len;
     }
     
     // Configure client certificates for mutual TLS
     if (config->client_cert_pem != NULL && config->client_key_pem != NULL) {
         http_config.client_cert_pem = (char *)config->client_cert_pem;
         http_config.client_cert_len = config->client_cert_len;
         http_config.client_key_pem = (char *)config->client_key_pem;
         http_config.client_key_len = config->client_key_len;
     }
     
     // Initialize HTTP client
     esp_http_client_handle_t client = esp_http_client_init(&http_config);
     if (!client) {
         ESP_LOGE(TAG, "Failed to initialize HTTP client");
         return ESP_FAIL;
     }
     
     // Set HTTP method
     esp_http_client_method_t http_method;
     switch (method) {
         case HTTPS_METHOD_POST:
             http_method = HTTP_METHOD_POST;
             break;
         case HTTPS_METHOD_PUT:
             http_method = HTTP_METHOD_PUT;
             break;
         case HTTPS_METHOD_DELETE:
             http_method = HTTP_METHOD_DELETE;
             break;
         case HTTPS_METHOD_GET:
         default:
             http_method = HTTP_METHOD_GET;
             break;
     }
     esp_http_client_set_method(client, http_method);
     
     // Set default headers
     esp_http_client_set_header(client, "User-Agent", "ESP32 WiCAN/1.0");
     
     // Set default Content-Type for POST/PUT if none provided in headers
     bool has_content_type = false;
     
     // Set custom headers
     if (headers) {
         for (int i = 0; headers[i] != NULL; i++) {
             // Extract header key and value
             char *header_copy = strdup(headers[i]);
             if (!header_copy) {
                 ESP_LOGE(TAG, "Failed to allocate memory for header");
                 esp_http_client_cleanup(client);
                 return ESP_ERR_NO_MEM;
             }
             
             char *colon = strchr(header_copy, ':');
             if (colon) {
                 *colon = '\0';
                 char *key = header_copy;
                 char *value = colon + 1;
                 
                 // Skip leading spaces in value
                 while (*value == ' ') value++;
                 
                 // Check if this is a Content-Type header
                 if (strcasecmp(key, "Content-Type") == 0) {
                     has_content_type = true;
                 }
                 
                 // Set header
                 esp_http_client_set_header(client, key, value);
             }
             
             free(header_copy);
         }
     }
     
     // Set default Content-Type if needed
     if (!has_content_type && (method == HTTPS_METHOD_POST || method == HTTPS_METHOD_PUT) && data) {
         esp_http_client_set_header(client, "Content-Type", "application/x-www-form-urlencoded");
     }
     
     // Set post/put data
     if ((method == HTTPS_METHOD_POST || method == HTTPS_METHOD_PUT) && data) {
         esp_http_client_set_post_field(client, data, data_len);
     }
     
     ESP_LOGI(TAG, "Sending request to %s", config->url);
     
     // Perform request
     esp_err_t err = esp_http_client_perform(client);
     if (err != ESP_OK) {
         ESP_LOGE(TAG, "HTTP request failed: %s", esp_err_to_name(err));
         esp_http_client_cleanup(client);
         return err;
     }
     
     // Read response
     err = read_http_response(client, response);
     if (err != ESP_OK) {
         ESP_LOGE(TAG, "Failed to read HTTP response");
         esp_http_client_cleanup(client);
         return err;
     }
     
     // Clean up
     esp_http_client_cleanup(client);
     
     ESP_LOGI(TAG, "Request completed, status code: %d, response size: %d bytes", 
              response->status_code, response->data_len);
     
     return ESP_OK;
 }

/**
 * @brief Perform an HTTPS request with generic authentication helpers.
 */
esp_err_t https_client_mgr_request_with_auth(const https_client_mgr_config_t *config,
                                             https_client_mgr_method_t method,
                                             const char *data,
                                             size_t data_len,
                                             const char *content_type,
                                             const https_client_mgr_auth_t *auth,
                                             const char **extra_headers,
                                             https_client_mgr_response_t *response)
{
    if (!config || !config->url || !response) {
        return ESP_ERR_INVALID_ARG;
    }

    // Build URL with query params if needed
    char *temp_url = NULL;
    https_client_mgr_query_kv_t stack_params[8];
    size_t param_count = 0;

    if (auth) {
        if (auth->api_key && auth->api_key_query_name && param_count < 8) {
            stack_params[param_count++] = (https_client_mgr_query_kv_t){ auth->api_key_query_name, auth->api_key };
        }
        if (auth->extra_query && auth->extra_query_count > 0) {
            size_t copy_n = auth->extra_query_count;
            if (param_count + copy_n > 8) copy_n = 8 - param_count;
            for (size_t i = 0; i < copy_n; ++i) {
                stack_params[param_count++] = auth->extra_query[i];
            }
        }
    }

    if (param_count > 0) {
        temp_url = append_query_params(config->url, stack_params, param_count);
        if (!temp_url) return ESP_ERR_NO_MEM;
    }

    https_client_mgr_config_t cfg = *config; // copy
    if (temp_url) cfg.url = temp_url;

    // Build headers list dynamically
    // We may add up to: Content-Type, API key header, Authorization (Bearer/Basic), Custom signer header
    const int MAX_AUTO_HEADERS = 6;
    const char **headers = calloc(MAX_AUTO_HEADERS + 8 /*user*/, sizeof(char *));
    if (!headers) { if (temp_url) free(temp_url); return ESP_ERR_NO_MEM; }
    int h = 0;

    // Content-Type if provided
    char *content_header = NULL;
    if (content_type) {
        size_t len = strlen(content_type) + 15;
        content_header = heap_caps_malloc(len, MALLOC_CAP_SPIRAM|MALLOC_CAP_8BIT);
        if (!content_header) { free(headers); if (temp_url) free(temp_url); return ESP_ERR_NO_MEM; }
        snprintf(content_header, len, "Content-Type: %s", content_type);
        headers[h++] = content_header;
    }

    char *api_key_header = NULL;
    char *auth_header = NULL;
    char *custom_header = NULL;

    if (auth) {
        // API key header
        if (auth->api_key && (!auth->api_key_query_name)) {
            const char *name = auth->api_key_header_name ? auth->api_key_header_name : "x-api-key";
            size_t len = strlen(name) + 2 + strlen(auth->api_key) + 1;
            api_key_header = heap_caps_malloc(len, MALLOC_CAP_SPIRAM|MALLOC_CAP_8BIT);
            if (!api_key_header) { goto OOM; }
            snprintf(api_key_header, len, "%s: %s", name, auth->api_key);
            headers[h++] = api_key_header;
        }

        // Authorization header (Bearer or Basic)
        if (auth->bearer_token) {
            size_t len = strlen(auth->bearer_token) + strlen("Authorization: Bearer ") + 1;
            auth_header = heap_caps_malloc(len, MALLOC_CAP_SPIRAM|MALLOC_CAP_8BIT);
            if (!auth_header) { goto OOM; }
            snprintf(auth_header, len, "Authorization: Bearer %s", auth->bearer_token);
            headers[h++] = auth_header;
        } else if (auth->basic_username || auth->basic_password) {
            char val[256];
            if (build_basic_auth_value(auth->basic_username, auth->basic_password, val) == ESP_OK) {
                size_t len = strlen(val) + strlen("Authorization: ") + 1;
                auth_header = heap_caps_malloc(len, MALLOC_CAP_SPIRAM|MALLOC_CAP_8BIT);
                if (!auth_header) { goto OOM; }
                snprintf(auth_header, len, "Authorization: %s", val);
                headers[h++] = auth_header;
            }
        }

        // Custom signer
        if (auth->custom_signer) {
            char key[64] = {0};
            char value[256] = {0};
            esp_err_t se = auth->custom_signer(method, cfg.url, data, data_len, auth->custom_signer_ctx, key, value);
            if (se == ESP_OK && key[0] && value[0]) {
                size_t len = strlen(key) + 2 + strlen(value) + 1;
                custom_header = heap_caps_malloc(len, MALLOC_CAP_SPIRAM|MALLOC_CAP_8BIT);
                if (!custom_header) { goto OOM; }
                snprintf(custom_header, len, "%s: %s", key, value);
                headers[h++] = custom_header;
            }
        }
    }

    // Copy extra headers from user
    if (extra_headers) {
        for (int i = 0; extra_headers[i] != NULL && h < (MAX_AUTO_HEADERS + 8 - 1); ++i) {
            headers[h++] = extra_headers[i];
        }
    }
    headers[h] = NULL;

    esp_err_t ret = https_client_mgr_request(&cfg, method, data, data_len, headers, response);

    // Free only what we allocated here (not extra_headers entries)
    if (content_header) free(content_header);
    if (api_key_header) free(api_key_header);
    if (auth_header) free(auth_header);
    if (custom_header) free(custom_header);
    free(headers);
    if (temp_url) free(temp_url);
    return ret;

OOM:
    if (content_header) free(content_header);
    if (api_key_header) free(api_key_header);
    if (auth_header) free(auth_header);
    if (custom_header) free(custom_header);
    free(headers);
    if (temp_url) free(temp_url);
    return ESP_ERR_NO_MEM;
}
 
 /**
  * @brief Perform an HTTPS GET request
  */
 esp_err_t https_client_mgr_get(const char *url, https_client_mgr_response_t *response)
 {
     if (!url || !response) {
         return ESP_ERR_INVALID_ARG;
     }
     
     // Create a basic configuration for the GET request
     https_client_mgr_config_t config = {
         .url = url,
         .use_crt_bundle = true,
         .timeout_ms = DEFAULT_TIMEOUT_MS,
         .non_blocking = false
     };
     
     return https_client_mgr_request(&config, HTTPS_METHOD_GET, NULL, 0, NULL, response);
 }
 
 /**
  * @brief Perform an HTTPS POST request
  */
 esp_err_t https_client_mgr_post(const char *url, const char *data, size_t data_len, 
                            const char *content_type, https_client_mgr_response_t *response)
 {
     if (!url || !response || (!data && data_len > 0)) {
         return ESP_ERR_INVALID_ARG;
     }
     
     // Create a basic configuration for the POST request
     https_client_mgr_config_t config = {
         .url = url,
         .use_crt_bundle = true,
         .timeout_ms = DEFAULT_TIMEOUT_MS,
         .non_blocking = false
     };
     
     const char *headers[2] = {NULL};
     
     // If content_type is provided, create a Content-Type header
     if (content_type) {
         char *content_header = heap_caps_malloc(strlen(content_type) + 15, MALLOC_CAP_SPIRAM|MALLOC_CAP_8BIT); // "Content-Type: " + content_type + null
         if (!content_header) {
             return ESP_ERR_NO_MEM;
         }
         sprintf(content_header, "Content-Type: %s", content_type);
         headers[0] = content_header;
     }
     
     esp_err_t ret = https_client_mgr_request(&config, HTTPS_METHOD_POST, data, data_len, headers, response);
     
     // Free allocated memory for the content-type header
     if (content_type) {
         free((void*)headers[0]);
     }
     
     return ret;
 }
 
 /**
  * @brief Download a file from an HTTPS URL
  */
esp_err_t https_client_mgr_download_file(const char *url, const char *save_path, 
                                     void (*progress_cb)(size_t downloaded_size, size_t total_size))
{
    if (!url || !save_path) {
        return ESP_ERR_INVALID_ARG;
    }
    esp_log_level_set("HTTP_CLIENT", ESP_LOG_VERBOSE);
    // Open the output file
    FILE *f = fopen(save_path, "wb");
    if (!f) {
        ESP_LOGE(TAG, "Failed to open file for writing: %s", save_path);
        return ESP_FAIL;
    }
    
    esp_http_client_config_t config = {
        .url = url,
        .cert_pem = NULL,
        .crt_bundle_attach = esp_crt_bundle_attach,
        .timeout_ms = DEFAULT_TIMEOUT_MS,
        .buffer_size_tx = 1024*8,
        .keep_alive_enable = false,
        .use_global_ca_store = true
    };
    
    esp_http_client_handle_t client = esp_http_client_init(&config);
    if (client == NULL) {
        ESP_LOGE(TAG, "Failed to initialize HTTP client");
        fclose(f);
        remove(save_path);  // Remove the file if client init fails
        return ESP_FAIL;
    }
    
    esp_err_t err = esp_http_client_open(client, 0);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "Failed to open HTTP connection: %s", esp_err_to_name(err));
        esp_http_client_cleanup(client);
        fclose(f);
        remove(save_path);  // Remove the file if connection fails
        return err;
    }
    
    int content_length = esp_http_client_fetch_headers(client);
    ESP_LOGI(TAG, "Content length: %d", content_length);
    
    size_t downloaded_size = 0;
    char *buffer = heap_caps_malloc(MAX_HTTP_RECV_BUFFER, MALLOC_CAP_SPIRAM|MALLOC_CAP_8BIT);
    if (!buffer) {
        ESP_LOGE(TAG, "Failed to allocate memory for download buffer");
        esp_http_client_cleanup(client);
        fclose(f);
        remove(save_path);  // Remove the file if buffer allocation fails
        return ESP_ERR_NO_MEM;
    }
    
    int read_len;
    while ((read_len = esp_http_client_read(client, buffer, MAX_HTTP_RECV_BUFFER)) > 0) {
        if (fwrite(buffer, 1, read_len, f) != read_len) {
            ESP_LOGE(TAG, "Failed to write to file");
            free(buffer);
            esp_http_client_cleanup(client);
            fclose(f);
            remove(save_path);  // Remove the file if writing fails
            return ESP_FAIL;
        }
        
        downloaded_size += read_len;
        
        if (progress_cb && content_length > 0) {
            progress_cb(downloaded_size, content_length);
        }
    }
    
    free(buffer);
    esp_http_client_cleanup(client);
    fclose(f);
    
    if (read_len < 0) {
        ESP_LOGE(TAG, "Error reading data");
        remove(save_path);  // Remove the file if reading failed
        return ESP_FAIL;
    }
    
    ESP_LOGI(TAG, "Download complete: %zu bytes", downloaded_size);
    return ESP_OK;
}

/**
 * @brief Set global CA certificate store
 */
esp_err_t https_client_mgr_set_global_ca_store(const uint8_t *cacert_pem, size_t cacert_len)
{
    if (!cacert_pem || cacert_len == 0) {
        return ESP_ERR_INVALID_ARG;
    }
    
    return esp_tls_set_global_ca_store(cacert_pem, cacert_len);
}

/**
 * @brief Free the global CA certificate store
 */
esp_err_t https_client_mgr_free_global_ca_store(void)
{
    esp_tls_free_global_ca_store();
    return ESP_OK;
}

/**
 * @brief Free a response structure
 */
void https_client_mgr_free_response(https_client_mgr_response_t *response)
{
    if (response) {
        if (response->data) {
            free(response->data);
            response->data = NULL;
        }
        response->data_len = 0;
        response->status_code = 0;
        response->is_success = false;
    }
}
