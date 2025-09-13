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

// Preload /vpn_config.json into PSRAM once and reuse it (avoids reopening file)
esp_err_t vpn_config_preload(void);
// Get a read-only pointer to the cached JSON (NULL if not loaded)
const char *vpn_config_get_json_ptr(size_t *out_len);

#ifdef __cplusplus
}
#endif
