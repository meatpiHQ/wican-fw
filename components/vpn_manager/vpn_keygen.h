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
#include <stddef.h>

#ifdef __cplusplus
extern "C" {
#endif

// Generate a WireGuard keypair using X25519.
// Outputs Base64 strings (null-terminated) for private and public keys.
// Either output buffer may be NULL to skip that output.
// private_key_b64_size and public_key_b64_size must be >= 64.
// Returns ESP_OK on success.
esp_err_t vpn_keygen_generate_wireguard_keys(char *private_key_b64,
                                             size_t private_key_b64_size,
                                             char *public_key_b64,
                                             size_t public_key_b64_size);

#ifdef __cplusplus
}
#endif
