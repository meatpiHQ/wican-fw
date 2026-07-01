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

#include "vpn_keygen.h"

#include <string.h>
#include <stdio.h>
#include <stdbool.h>
#include <mbedtls/base64.h>
#include <esp_random.h>
#include <esp_system.h>
#include <esp_log.h>

#include "x25519.h"

static const char *TAG_WG_KG = "VPN_KEYGEN";

static void secure_wipe(void *v, size_t n)
{
    if (v == NULL)
    {
        return;
    }
    volatile unsigned char *p = (volatile unsigned char *)v;
    while (n--)
    {
        *p++ = 0;
    }
}

static esp_err_t b64_encode(const unsigned char *in, size_t in_len,
                            char *out, size_t out_size)
{
    size_t olen = 0;
    int r = mbedtls_base64_encode((unsigned char *)out, out_size, &olen, in, in_len);
    if (r != 0)
    {
        return ESP_FAIL;
    }
    if (olen < out_size)
    {
        out[olen] = '\0';
    }
    return ESP_OK;
}

esp_err_t vpn_keygen_generate_wireguard_keys(char *private_key_b64,
                                             size_t private_key_b64_size,
                                             char *public_key_b64,
                                             size_t public_key_b64_size)
{
    if ((private_key_b64 == NULL && public_key_b64 == NULL))
    {
        return ESP_ERR_INVALID_ARG;
    }
    if ((private_key_b64 && private_key_b64_size < 64) ||
        (public_key_b64 && public_key_b64_size < 64))
    {
        return ESP_ERR_INVALID_SIZE;
    }

    esp_err_t err = ESP_FAIL;

    unsigned char sk[32];
    unsigned char pk[32];

    // Curve25519 private key: 32 random bytes (clamping is applied internally
    // by x25519_base() at each point of use, matching upstream WireGuard).
    esp_fill_random(sk, sizeof(sk));

    if (x25519_base(pk, sk, 1) != 0)
    {
        ESP_LOGE(TAG_WG_KG, "x25519_base failed");
        goto cleanup_wipe;
    }

    if (private_key_b64)
    {
        err = b64_encode(sk, sizeof(sk), private_key_b64, private_key_b64_size);
        if (err != ESP_OK)
        {
            goto cleanup_wipe;
        }
    }

    if (public_key_b64)
    {
        err = b64_encode(pk, sizeof(pk), public_key_b64, public_key_b64_size);
        if (err != ESP_OK)
        {
            goto cleanup_wipe;
        }
    }

    err = ESP_OK;

cleanup_wipe:
    secure_wipe(sk, sizeof(sk));
    secure_wipe(pk, sizeof(pk));

    return err;
}
