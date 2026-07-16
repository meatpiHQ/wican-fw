#include "wireguard-platform.h"

#include <stdlib.h>
#include <time.h>
#include <inttypes.h>
#include <lwip/sys.h>
#include <esp_system.h>
#include <esp_random.h>
#include <esp_err.h>
#include <esp_log.h>

#include "crypto.h"

#define TAG "wireguard-platform"

esp_err_t wireguard_platform_init() {
	// Randomness comes straight from the hardware RNG (esp_fill_random);
	// the mbedtls entropy/CTR_DRBG wrapper it replaced was seeded from the
	// same source and its headers were removed in Mbed TLS 4.x (IDF 6.0).
	return ESP_OK;
}

void wireguard_random_bytes(void *bytes, size_t size) {
	esp_fill_random(bytes, size);
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
