#pragma once

#include "esp_err.h"
#include "esp_http_server.h"

#ifdef __cplusplus
extern "C" {
#endif

// Initialization for certificate manager (ensure directories etc.)
esp_err_t cert_manager_init(void);

// Register current certificate manager endpoints (single-cert for now, will evolve to multi-cert)
esp_err_t cert_manager_register_handlers(httpd_handle_t server);

// Maximum number of stored named certificate sets
#define CERT_MANAGER_MAX_SETS 10

// Utilities to load certificate file contents into memory (caller must free) for existing single-cert paths
esp_err_t cert_manager_load_ca(char **buf_out, size_t *len_out);
esp_err_t cert_manager_load_client_cert(char **buf_out, size_t *len_out);
esp_err_t cert_manager_load_client_key(char **buf_out, size_t *len_out);

// Paths (exposed for components needing direct fopen). Returned pointers are constant.
const char * cert_manager_get_ca_path(void);
const char * cert_manager_get_client_cert_path(void);
const char * cert_manager_get_client_key_path(void);

#ifdef __cplusplus
}
#endif
