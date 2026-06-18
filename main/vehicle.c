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

// Engine-running gate (Task #6). DEDICATED logging threshold, fully decoupled from the
// deep-sleep voltage (sleep_volt): the alternator-charging level (~13.2 V) is far above the
// sleep/battery-protection level (~12.4 V) and a resting battery (~12.6-12.8 V), so a parked
// car no longer reads "ignition on". Hysteresis (a fixed band below the ON edge) plus the
// 0.1 V ADC quantization avoids flapping at idle; csv_logger adds a 3 s ON->OFF debounce on
// top for cranking dips. This gate is consumed ONLY by csv_logger (the writer task), so it
// never touches sleep/wake or battery protection -- logging-only, cannot brick.
#define VEHICLE_IGN_HYSTERESIS_V 0.3f   // OFF edge = engine_on_volt - band; 3x the 0.1 V ADC step

static float engine_on_volt = 13.2f;    // set in vehicle_init() from config (config_server_get_engine_volt)
// Held ignition state for hysteresis. Mutated only by the single caller (csv_logger writer
// task) -> no lock needed. Boots OFF so logging starts only once charging is actually seen.
static vehicle_ignition_state_t ign_state = VEHICLE_STATE_IGNITION_OFF;

vehicle_ignition_state_t vehicle_ignition_state(void)
{
    float batt_voltage = 0;

    if(sleep_mode_get_voltage(&batt_voltage) != ESP_OK)
    {
        // Transient read failure: KEEP the last latched state (return INVALID; csv_logger
        // also holds last-state on INVALID). Do not flap to OFF on a single bad read.
        return VEHICLE_STATE_IGNITION_INVALID;
    }

    if(batt_voltage >= engine_on_volt)
    {
        ign_state = VEHICLE_STATE_IGNITION_ON;
    }
    else if(batt_voltage <= (engine_on_volt - VEHICLE_IGN_HYSTERESIS_V))
    {
        ign_state = VEHICLE_STATE_IGNITION_OFF;
    }
    // else: inside the hysteresis band -> hold ign_state unchanged

    return ign_state;
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
    engine_on_volt = vehicle_config->engine_on_volt;
    ign_state = VEHICLE_STATE_IGNITION_OFF;   // explicit boot state: start OFF
}
