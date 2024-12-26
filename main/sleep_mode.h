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


#ifndef SLEEP_MODE_h
#define SLEEP_MODE_h

#if HARDWARE_VER != WICAN_PRO
int8_t sleep_mode_init(uint8_t enable, float sleep_volt);
int8_t sleep_mode_get_voltage(float *val);
#elif HARDWARE_VER == WICAN_PRO
// #define HV_PRO_V140     1

// State machine states
typedef enum {
    STATE_NORMAL,
    STATE_LOW_VOLTAGE,
    STATE_SLEEPING,
    STATE_WAKE_PENDING
} system_state_t;

typedef struct {
    system_state_t state;
    float voltage;
    uint32_t timer;
} sleep_state_info_t;

void sleep_mode_init(void);
esp_err_t sleep_mode_get_voltage(float *val);
esp_err_t sleep_mode_get_state(sleep_state_info_t *state_info);
void sleep_mode_print_wakeup_reason(void);

#endif


#endif
