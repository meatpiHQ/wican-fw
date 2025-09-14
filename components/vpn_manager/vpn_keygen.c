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
#include <mbedtls/ctr_drbg.h>
#include <mbedtls/entropy.h>
#include <mbedtls/ecp.h>
#include <mbedtls/base64.h>
#include <mbedtls/private_access.h>
#include <esp_system.h>
#include <esp_log.h>

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

// Write MPI value in little-endian order of exactly out_len bytes.
// Uses big-endian write from mbedTLS then reverses.
static int mpi_write_binary_le(const mbedtls_mpi *X, unsigned char *out, size_t out_len)
{
    // Temporary big-endian buffer
    unsigned char tmp[64];
    if (out_len > sizeof(tmp))
    {
        return -1;
    }
    memset(tmp, 0, sizeof(tmp));
    int ret = mbedtls_mpi_write_binary(X, tmp + (sizeof(tmp) - out_len), out_len);
    if (ret != 0)
    {
        return ret;
    }
    // Reverse to little-endian
    for (size_t i = 0; i < out_len; ++i)
    {
        out[i] = tmp[sizeof(tmp) - 1 - i];
    }
    return 0;
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

    mbedtls_entropy_context entropy;
    mbedtls_ctr_drbg_context ctr_drbg;
    mbedtls_ecp_group grp;
    mbedtls_mpi d;
    mbedtls_ecp_point Q;

    mbedtls_entropy_init(&entropy);
    mbedtls_ctr_drbg_init(&ctr_drbg);
    mbedtls_ecp_group_init(&grp);
    mbedtls_mpi_init(&d);
    mbedtls_ecp_point_init(&Q);

    const char *pers = "wican-wg-keygen";

    int ret = mbedtls_ctr_drbg_seed(&ctr_drbg, mbedtls_entropy_func, &entropy,
                                    (const unsigned char *)pers, strlen(pers));
    if (ret != 0)
    {
        ESP_LOGE(TAG_WG_KG, "ctr_drbg_seed failed: -0x%04x", -ret);
        goto cleanup;
    }

    ret = mbedtls_ecp_group_load(&grp, MBEDTLS_ECP_DP_CURVE25519);
    if (ret != 0)
    {
        ESP_LOGE(TAG_WG_KG, "ecp_group_load failed: -0x%04x", -ret);
        goto cleanup;
    }

    ret = mbedtls_ecp_gen_keypair(&grp, &d, &Q, mbedtls_ctr_drbg_random, &ctr_drbg);
    if (ret != 0)
    {
        ESP_LOGE(TAG_WG_KG, "gen_keypair failed: -0x%04x", -ret);
        goto cleanup;
    }

    unsigned char sk[32];
    unsigned char pk[32];
    memset(sk, 0, sizeof(sk));
    memset(pk, 0, sizeof(pk));

    // Write private scalar and public X coordinate in little-endian (X25519 format)
    ret = mpi_write_binary_le(&d, sk, sizeof(sk));
    if (ret != 0)
    {
        ESP_LOGE(TAG_WG_KG, "write sk failed: -0x%04x", -ret);
        goto cleanup_wipe;
    }

    {
        unsigned char pub_tmp[65];
        size_t plen = 0;
        memset(pub_tmp, 0, sizeof(pub_tmp));
        ret = mbedtls_ecp_point_write_binary(&grp, &Q,
                                             MBEDTLS_ECP_PF_UNCOMPRESSED,
                                             &plen, pub_tmp, sizeof(pub_tmp));
        if (ret != 0)
        {
            ESP_LOGE(TAG_WG_KG, "point_write_binary failed: -0x%04x", -ret);
            goto cleanup_wipe;
        }

        if (plen == 32)
        {
            // Big-endian X only -> reverse to little-endian
            for (size_t i = 0; i < 32; ++i)
            {
                pk[i] = pub_tmp[31 - i];
            }
        }
        else if (plen == 65 && pub_tmp[0] == 0x04)
        {
            // Uncompressed: 0x04 || X(32) || Y(32). Take X and reverse.
            for (size_t i = 0; i < 32; ++i)
            {
                pk[i] = pub_tmp[1 + 31 - i];
            }
        }
        else
        {
            ESP_LOGE(TAG_WG_KG, "unexpected public key size: %u", (unsigned)plen);
            goto cleanup_wipe;
        }
    }

    // Base64 encode
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

cleanup:
    mbedtls_ecp_point_free(&Q);
    mbedtls_mpi_free(&d);
    mbedtls_ecp_group_free(&grp);
    mbedtls_ctr_drbg_free(&ctr_drbg);
    mbedtls_entropy_free(&entropy);

    return err;
}
