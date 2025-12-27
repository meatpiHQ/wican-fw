#include "wireguard-platform.h"

#include <stdlib.h>
#include <time.h>
#include <inttypes.h>
#include <lwip/sys.h>
#include <mbedtls/entropy.h>
#include <mbedtls/ctr_drbg.h>
#include <esp_system.h>
#include <esp_err.h>
#include <esp_log.h>

#include "crypto.h"

#define ENTROPY_MINIMUM_REQUIRED_THRESHOLD	(134)
#define ENTROPY_FUNCTION_DATA	NULL
#define ENTROPY_CUSTOM_DATA		NULL
#define ENTROPY_CUSTOM_DATA_LENGTH (0)
#define TAG "wireguard-platform"

static struct mbedtls_ctr_drbg_context random_context;
static struct mbedtls_entropy_context entropy_context;

static int entropy_hw_random_source( void *data, unsigned char *output, size_t len, size_t *olen ) {
	esp_fill_random(output, len);
	*olen = len;
	return 0;
}

esp_err_t wireguard_platform_init() {
	int mbedtls_err;
	esp_err_t err;

	mbedtls_entropy_init(&entropy_context);
	mbedtls_ctr_drbg_init(&random_context);
	mbedtls_err = mbedtls_entropy_add_source(
			&entropy_context,
			entropy_hw_random_source,
			ENTROPY_FUNCTION_DATA,
			ENTROPY_MINIMUM_REQUIRED_THRESHOLD,
			MBEDTLS_ENTROPY_SOURCE_STRONG);
	if (mbedtls_err != 0) {
		ESP_LOGE(TAG, "mbedtls_entropy_add_source: %i", mbedtls_err);
		err = ESP_FAIL;
		goto fail;
	}
	mbedtls_err = mbedtls_ctr_drbg_seed(
			&random_context,
			mbedtls_entropy_func,
			&entropy_context,
			ENTROPY_CUSTOM_DATA,
			ENTROPY_CUSTOM_DATA_LENGTH);
	if (mbedtls_err != 0) {
		ESP_LOGE(TAG, "mbedtls_ctr_drbg_seed: %i", mbedtls_err);
		err = ESP_FAIL;
		goto fail;
	}
	err = ESP_OK;
fail:
	return err;
}

void wireguard_random_bytes(void *bytes, size_t size) {
	mbedtls_ctr_drbg_random(&random_context, bytes, size);
}

uint32_t wireguard_sys_now() {
	// Default to the LwIP system time
	return sys_now();
}

void wireguard_tai64n_now(uint8_t *output) {
	// See https://cr.yp.to/libtai/tai64.html
	// 64 bit seconds from 1970 = 8 bytes
	// 32 bit nano seconds from current second

	struct timeval tv;
	gettimeofday(&tv, NULL);

	uint64_t seconds = 0x400000000000000aULL + tv.tv_sec;
	uint32_t nanos = tv.tv_usec * 1000;
	U64TO8_BIG(output + 0, seconds);
	U32TO8_BIG(output + 8, nanos);
}

bool wireguard_is_under_load() {
	return false;
}
// vim: noexpandtab
