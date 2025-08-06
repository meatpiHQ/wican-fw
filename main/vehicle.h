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


#ifndef VEHICLE_H
#define VEHICLE_H

#include "freertos/FreeRTOS.h"
#include "freertos/event_groups.h"
#include "esp_log.h"
#include <stdbool.h>

#ifdef __cplusplus
extern "C" {
#endif

// Vehicle event bits
#define VEHICLE_IGNITION_ON_BIT     BIT0
#define VEHICLE_STATIONARY_BIT      BIT1

typedef enum {
    VEHICLE_STATE_IGNITION_OFF,
    VEHICLE_STATE_IGNITION_ON,
    VEHICLE_STATE_IGNITION_INVALID
} vehicle_ignition_state_t;

typedef enum {
    VEHICLE_MOTION_STATIONARY,
    VEHICLE_MOTION_ACTIVE,
    VEHICLE_MOTION_INVALID
} vehicle_motion_state_t;

typedef struct {
    float voltage_at_ignition;

} vehicle_config_t;




// typedef struct {
//     bool ignition_on;
//     bool stationary;
//     float voltage;
// } vehicle_state_t;


void vehicle_init(vehicle_config_t *vehicle_config);
vehicle_ignition_state_t vehicle_ignition_state(void);
vehicle_motion_state_t vehicle_motion_state(void);

#endif 
