/* Internal WireGuard backend for VPN manager */
#pragma once

#include <esp_err.h>
#include <stdbool.h>
#include "include/vpn_manager.h"

#ifdef __cplusplus
extern "C" {
#endif

// Validate WG configuration fields

// Init/Deinit backend (creates wg context)
esp_err_t vpn_wg_init(const vpn_wireguard_config_t *cfg);
esp_err_t vpn_wg_deinit(void);

// Start/Stop connection
esp_err_t vpn_wg_start(void);
esp_err_t vpn_wg_stop(void);

// Helpers
bool vpn_wg_is_peer_up(void);
esp_err_t vpn_wg_set_default_route(void);

#ifdef __cplusplus
}
#endif
