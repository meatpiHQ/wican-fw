#pragma once

#include <esp_err.h>
#include "include/vpn_manager.h"

#ifdef __cplusplus
extern "C" {
#endif

esp_err_t vpn_config_save(const vpn_config_t *config);
esp_err_t vpn_config_load(vpn_config_t *config);
esp_err_t vpn_config_parse_wg(const char *config_text, vpn_wireguard_config_t *config);
esp_err_t vpn_config_generate_wg_keys(char *public_key, size_t public_key_size);

#ifdef __cplusplus
}
#endif
