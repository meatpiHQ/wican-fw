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

#include "esp_console.h"
#include "esp_err.h"
#include <stdint.h>

typedef void (*cmdline_output_func_t)(const char *data, size_t len);

esp_err_t cmdline_init(void);
esp_err_t cmdline_safemode_init(void);
void cmdline_set_tcp_output_func(cmdline_output_func_t func);
void cmdline_set_ble_output_func(cmdline_output_func_t func);
void cmdline_printf(const char *fmt, ...);
esp_err_t cmdline_run(const char *cmd);
esp_err_t cmdline_run_on_ble(const char *cmd);
void cmdline_print_prompt_on_ble(void);