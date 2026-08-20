// host-test stub for esp_log.h: log straight to stdout
#pragma once
#include <stdio.h>

#define ESP_LOGE(tag, fmt, ...) printf("%s: " fmt "\n", tag, ##__VA_ARGS__)
#define ESP_LOGW(tag, fmt, ...) printf("%s: " fmt "\n", tag, ##__VA_ARGS__)
#define ESP_LOGI(tag, fmt, ...) printf("%s: " fmt "\n", tag, ##__VA_ARGS__)
