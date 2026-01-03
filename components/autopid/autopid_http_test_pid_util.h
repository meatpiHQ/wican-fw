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

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

bool autopid_test_pid_raw_ensure(size_t cap);
void autopid_test_pid_raw_reset(void);
const char *autopid_test_pid_raw_get(void);
void autopid_test_pid_raw_snippet(char *dst, size_t dstsz);

bool autopid_test_pid_send_cmd_sync(const char *cmd, uint32_t timeout_ms, bool capture);
void autopid_test_pid_run_init_sequence(const char *commands, uint32_t per_cmd_timeout_ms);
void autopid_test_pid_restore_autopid_safe_elm_state(void);

bool autopid_test_pid_contains_case_insensitive(const char *haystack, const char *needle);
bool autopid_test_pid_parse_hex_byte_stream(const char *s, uint8_t *out, size_t out_max, uint32_t *out_len);
bool autopid_test_pid_find_response_window(const uint8_t *bytes,
                                           uint32_t bytes_len,
                                           uint8_t positive_service,
                                           uint8_t pid_byte,
                                           const uint8_t **out_ptr,
                                           uint32_t *out_len);

#ifdef __cplusplus
}
#endif
