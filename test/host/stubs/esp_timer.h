// host-test stub for esp_timer.h: the fake clock behind it is defined in
// support/test_support.h and advanced manually by each test
#pragma once
#include <stdint.h>

int64_t esp_timer_get_time(void);
