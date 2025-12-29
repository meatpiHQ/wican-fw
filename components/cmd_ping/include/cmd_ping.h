#pragma once

#include "esp_err.h"

#ifdef __cplusplus
extern "C" {
#endif

// Registers the 'ping' command with esp_console.
esp_err_t cmd_ping_register(void);

#ifdef __cplusplus
}
#endif
