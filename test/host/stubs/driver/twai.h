// host-test stub for ESP-IDF driver/twai.h: just the fields the code under
// test touches
#pragma once
#include <stdint.h>

typedef struct {
    uint32_t identifier;
    uint8_t data_length_code;
    uint8_t data[8];
} twai_message_t;
