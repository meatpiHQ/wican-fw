// host-test stub for ESP-IDF nvs.h: an in-memory single-slot fake covering
// only the surface precondition.c uses. tests seed and inspect the backing
// store directly through the fake_nvs_* variables.
#pragma once
#include <stdbool.h>
#include <stdint.h>

typedef int esp_err_t;  // duplicate of the stub can.h typedef (identical, so legal)
typedef uint32_t nvs_handle_t;
typedef enum { NVS_READONLY, NVS_READWRITE } nvs_open_mode_t;

#define ESP_OK 0
#define ESP_ERR_NVS_NOT_FOUND 0x1102

// single-slot backing store, shared by every namespace/key
static bool fake_nvs_exists = false;
static uint8_t fake_nvs_value = 0;
static bool fake_nvs_write_pending = false;
static uint8_t fake_nvs_pending_value = 0;
static unsigned int fake_nvs_commit_failures = 0;
static unsigned int fake_nvs_commit_count = 0;  // every nvs_commit call, failed or not

static inline const char *esp_err_to_name(esp_err_t err) {
    (void)err;
    return "ERR";
}

static inline esp_err_t nvs_open(const char *ns, nvs_open_mode_t mode, nvs_handle_t *out) {
    (void)ns;
    (void)mode;
    *out = 1U;
    return ESP_OK;
}

static inline esp_err_t nvs_get_u8(nvs_handle_t handle, const char *key, uint8_t *out) {
    (void)handle;
    (void)key;
    if (!fake_nvs_exists) {
        return ESP_ERR_NVS_NOT_FOUND;
    }
    *out = fake_nvs_value;
    return ESP_OK;
}

static inline esp_err_t nvs_set_u8(nvs_handle_t handle, const char *key, uint8_t value) {
    (void)handle;
    (void)key;
    fake_nvs_write_pending = true;
    fake_nvs_pending_value = value;
    return ESP_OK;
}

static inline esp_err_t nvs_commit(nvs_handle_t handle) {
    (void)handle;
    fake_nvs_commit_count++;
    if (fake_nvs_commit_failures > 0U) {
        fake_nvs_commit_failures--;
        return 1;
    }
    if (fake_nvs_write_pending) {
        fake_nvs_exists = true;
        fake_nvs_value = fake_nvs_pending_value;
        fake_nvs_write_pending = false;
    }
    return ESP_OK;
}

static inline void nvs_close(nvs_handle_t handle) {
    (void)handle;
    fake_nvs_write_pending = false;
}
