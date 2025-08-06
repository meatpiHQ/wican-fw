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


#include "vehicle.h"
#include "freertos/FreeRTOS.h"
#include "freertos/event_groups.h"
#include "esp_log.h"
#include "sleep_mode.h"
#include "imu.h"

static const char *TAG = "VEHICLE";
static EventGroupHandle_t vehicle_event_group;
static float voltage_at_ignition = 0;

vehicle_ignition_state_t vehicle_ignition_state(void)
{
    float batt_voltage = 0;

    if(sleep_mode_get_voltage(&batt_voltage) == ESP_OK)
    {
        if(batt_voltage > voltage_at_ignition)
        {
            return VEHICLE_STATE_IGNITION_ON;
        }
        else
        {
            return VEHICLE_STATE_IGNITION_OFF;
        }
    }
    else
    {
        return VEHICLE_STATE_IGNITION_INVALID;
    }
}

vehicle_motion_state_t vehicle_motion_state(void)
{
    activity_state_t imu_state = imu_get_activity_state();
    
    switch(imu_state)
    {
        case ACTIVITY_STATE_STATIONARY:
            return VEHICLE_MOTION_STATIONARY;
            
        case ACTIVITY_STATE_ACTIVE:
            return VEHICLE_MOTION_ACTIVE;
            
        case ACTIVITY_STATE_INVALID:
        default:
            return VEHICLE_MOTION_INVALID;
    }
}

void vehicle_init(vehicle_config_t *vehicle_config)
{
    voltage_at_ignition = vehicle_config->voltage_at_ignition;
}
