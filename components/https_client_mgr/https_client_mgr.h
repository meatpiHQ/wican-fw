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

 #ifndef HTTPS_CLIENT_MGR_H
 #define HTTPS_CLIENT_MGR_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include "esp_err.h"
#include "esp_tls.h"

#ifdef __cplusplus
extern "C" {
#endif

/**
 * @brief Response structure for HTTPS requests
 */
typedef struct {
    char *data;             /*!< Response data buffer */
    int data_len;           /*!< Length of response data */
    int status_code;        /*!< HTTP status code */
    bool is_success;        /*!< Whether the request was successful */
} https_client_mgr_response_t;

/**
 * @brief Request method types
 */
typedef enum {
    HTTPS_METHOD_GET,       /*!< HTTP GET method */
    HTTPS_METHOD_POST,      /*!< HTTP POST method */
    HTTPS_METHOD_PUT,       /*!< HTTP PUT method */
    HTTPS_METHOD_DELETE     /*!< HTTP DELETE method */
} https_client_mgr_method_t;

/**
 * @brief Key/Value pair for query parameters
 */
typedef struct {
    const char *key;        /*!< Query parameter key (must be URL-safe or pre-encoded) */
    const char *value;      /*!< Query parameter value (must be URL-safe or pre-encoded) */
} https_client_mgr_query_kv_t;

/**
 * @brief Authentication configuration for HTTPS requests
 *
 * All fields are optional; set whichever applies. Multiple mechanisms can be
 * combined (e.g., x-api-key header + Bearer token + extra query params).
 */
typedef struct {
    // API key in header (e.g., x-api-key: <key>)
    const char *api_key_header_name;  /*!< Header name for API key, defaults to "x-api-key" if set to NULL while api_key is provided */
    const char *api_key;              /*!< API key value to put in header */

    // API key in query (e.g., ?api_key=<key>)
    const char *api_key_query_name;   /*!< Query parameter name for API key (e.g., "api_key"). If provided along with api_key, will be appended to URL */

    // Bearer token (Authorization: Bearer <token>)
    const char *bearer_token;         /*!< Bearer token string */

    // Basic auth (Authorization: Basic base64(user:pass))
    const char *basic_username;       /*!< Username for Basic auth */
    const char *basic_password;       /*!< Password for Basic auth */

    // Extra query parameters to append to URL
    const https_client_mgr_query_kv_t *extra_query; /*!< Array of extra query params to append to URL */
    size_t extra_query_count;         /*!< Number of items in extra_query */

    // Custom signer callback to inject a header (e.g., HMAC). The callback should
    // set out_header_key and out_header_value. Return ESP_OK on success.
    esp_err_t (*custom_signer)(https_client_mgr_method_t method,
                               const char *url,
                               const char *body,
                               size_t body_len,
                               void *user_ctx,
                               char out_header_key[64],
                               char out_header_value[256]);
    void *custom_signer_ctx;          /*!< Opaque context passed to custom_signer */
} https_client_mgr_auth_t;

/**
 * @brief Configuration for HTTPS client
 */
typedef struct {
    bool use_global_ca_store;                   /*!< Use global CA store for server verification */
    bool use_crt_bundle;                        /*!< Use certificate bundle for server verification */
    const char *url;                            /*!< URL for the request */
    const char *cert_pem;                       /*!< Certificate data in PEM format for server verification, if needed */
    size_t cert_len;                            /*!< Length of the certificate data */
    const char *client_cert_pem;                /*!< Client certificate in PEM format for client verification */
    size_t client_cert_len;                     /*!< Length of the client certificate data */
    const char *client_key_pem;                 /*!< Client private key in PEM format for client verification */
    size_t client_key_len;                      /*!< Length of the client key data */
    esp_tls_client_session_t *client_session;   /*!< TLS client session to reuse */
    bool skip_common_name;                      /*!< Skip CN verification */
    int timeout_ms;                             /*!< Connection timeout in ms */
    bool non_blocking;                          /*!< Non-blocking connection if true */
} https_client_mgr_config_t;

/**
 * @brief Initialize the HTTPS client manager
 * 
 * @return ESP_OK on success, appropriate error code otherwise
 */
esp_err_t https_client_mgr_init(void);

/**
 * @brief Deinitialize the HTTPS client manager
 * 
 * @return ESP_OK on success, appropriate error code otherwise
 */
esp_err_t https_client_mgr_deinit(void);

/**
 * @brief Perform an HTTPS GET request
 * 
 * @param url URL to send the request to
 * @param response Response structure to be filled
 * @return ESP_OK on success, appropriate error code otherwise
 */
esp_err_t https_client_mgr_get(const char *url, https_client_mgr_response_t *response);

/**
 * @brief Perform an HTTPS POST request
 * 
 * @param url URL to send the request to
 * @param data Data to send in the request body
 * @param data_len Length of the data
 * @param content_type MIME type of the request body
 * @param response Response structure to be filled
 * @return ESP_OK on success, appropriate error code otherwise
 */
esp_err_t https_client_mgr_post(const char *url, const char *data, size_t data_len, 
                           const char *content_type, https_client_mgr_response_t *response);

/**
 * @brief Perform an HTTPS request with advanced configuration
 * 
 * @param config Configuration for the HTTPS client
 * @param method HTTP method to use
 * @param data Data to send in the request body (for POST/PUT)
 * @param data_len Length of the data
 * @param headers Additional HTTP headers as key-value pairs
 * @param response Response structure to be filled
 * @return ESP_OK on success, appropriate error code otherwise
 */
esp_err_t https_client_mgr_request(const https_client_mgr_config_t *config, https_client_mgr_method_t method,
                              const char *data, size_t data_len, const char **headers,
                              https_client_mgr_response_t *response);

/**
 * @brief Perform an HTTPS request with generic authentication helpers.
 *
 * This builds common auth schemes automatically:
 * - x-api-key header (or custom header name)
 * - API key in query (appended to URL)
 * - Authorization: Bearer <token>
 * - Authorization: Basic <base64(username:password)>
 * - Optional custom signer callback to add a signature header
 * - Optional extra query parameters
 *
 * Any headers passed in extra_headers are also included. Content-Type can be
 * specified explicitly via content_type (recommended for POST/PUT JSON, etc.).
 *
 * @param config Base HTTPS client config (url required)
 * @param method HTTP method
 * @param data Optional request body
 * @param data_len Length of request body
 * @param content_type Content-Type header value to set (e.g., "application/json"), or NULL to use default behavior
 * @param auth Authentication configuration (may be NULL if none)
 * @param extra_headers Additional headers as "Key: Value" strings (NULL-terminated array), may be NULL
 * @param response Output response
 * @return ESP_OK on success, error code otherwise
 */
esp_err_t https_client_mgr_request_with_auth(const https_client_mgr_config_t *config,
                                             https_client_mgr_method_t method,
                                             const char *data,
                                             size_t data_len,
                                             const char *content_type,
                                             const https_client_mgr_auth_t *auth,
                                             const char **extra_headers,
                                             https_client_mgr_response_t *response);

/**
 * @brief Download a file from an HTTPS URL
 * 
 * @param url URL to download from
 * @param save_path Path to save the downloaded file
 * @param progress_cb Callback function to report download progress (can be NULL)
 * @return ESP_OK on success, appropriate error code otherwise
 */
esp_err_t https_client_mgr_download_file(const char *url, const char *save_path, 
                                    void (*progress_cb)(size_t downloaded_size, size_t total_size));

/**
 * @brief Set global CA certificate store
 * 
 * @param cacert_pem PEM format certificate data
 * @param cacert_len Length of certificate data
 * @return ESP_OK on success, appropriate error code otherwise
 */
esp_err_t https_client_mgr_set_global_ca_store(const uint8_t *cacert_pem, size_t cacert_len);

/**
 * @brief Free the global CA certificate store
 * 
 * @return ESP_OK on success, appropriate error code otherwise
 */
esp_err_t https_client_mgr_free_global_ca_store(void);

/**
 * @brief Free a response structure
 * 
 * @param response Response structure to free
 */
void https_client_mgr_free_response(https_client_mgr_response_t *response);

#ifdef __cplusplus
}
#endif

#endif /* https_client_mgr_MGR_H */