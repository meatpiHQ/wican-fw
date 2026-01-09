#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <esp_http_server.h>

#ifdef __cplusplus
extern "C" {
#endif

void ws_router_on_open(void);
bool ws_router_handle_frame(httpd_req_t *req, const uint8_t *payload, size_t len);

#ifdef __cplusplus
}
#endif
