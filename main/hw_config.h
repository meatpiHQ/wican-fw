 /* This file is part of the WiCAN project.
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
#ifndef GW_CONFIG_h
#define GW_CONFIG_h

#include "esp_err.h"
#include "hal/adc_types.h"
#include "hal/spi_types.h"

// This header is the only place that uses HARDWARE_VER.
// Then, in each HARDWARE_VER block, we define the pin mapping
// and feature macros (HW_HAS_*, CAN_BUS_COUNT, VBAT_*, etc)
// that are actually used by the .c files.

#define FS_MOUNT_POINT              "/littlefs"

#if HARDWARE_VER == WICAN_CUSTOM

// ---- Custom WiCAN: ESP32-S3-WROOM-1 N16R8, two CAN buses, no STN chip. ---- //
// These values come essentially verbatim from the hwconfig.h/main.c
// in Ali's provided test firmware for the custom WiCAN.

// -- Bus 0: TWAI controller on ESP -> SN65HVD233 #1 -- //

#define TX_GPIO_NUM                 2
#define RX_GPIO_NUM                 1
// No CAN standby GPIO on the custom WiCAN: the VP233 (SN65HVD233) has no standby/enable
// pin at all. This differs from the V300, which uses a VP230 (SN65HVD230)
// whose RS pin (GPIO6) selects high-speed vs standby--hence it defines
// CAN_STDBY_GPIO_NUM, but the custom WiCAN does not.

// -- Bus 1: MCP2515 (SPI2) -> SN65HVD233 #2 -- //

#define HW_HAS_MCP2515               1
#define CAN_BUS_COUNT                2
#define MCP2515_SPI_HOST             SPI2_HOST
#define MCP2515_SCLK_GPIO_NUM        17
#define MCP2515_MOSI_GPIO_NUM        16
#define MCP2515_MISO_GPIO_NUM        15
#define MCP2515_CS_GPIO_NUM          18
#define MCP2515_INT_GPIO_NUM         7
#define MCP2515_RST_GPIO_NUM         8    // active-low: HIGH = run, LOW = reset
#define MCP2515_SPI_CLOCK_HZ         5000000
#define MCP2515_OSCILLATOR_HZ        8000000

// These apply to both buses
#define CAN_DEFAULT_BITRATE          500000
#define CAN_DEFAULT_SAMPLE_POINT_PERMILL 875

#define CONNECTED_LED_GPIO_NUM       41
#define ACTIVE_LED_GPIO_NUM          40
#define PWR_LED_GPIO_NUM             42
#define LED_ON                       0
#define LED_OFF                      1
#define PWR_LED_ON                   0
#define PWR_LED_OFF                  1

// VBAT sense: R1=62K, R2=6.2K divider (x11) to ADC1 ch3
#define VBAT_ADC_CHANNEL             ADC_CHANNEL_3
#define VBAT_ADC_ATTEN               ADC_ATTEN_DB_6
#define VBAT_DIVIDER_R1_OHM          62000
#define VBAT_DIVIDER_R2_OHM          6200
// volts = pin_mV * NUM / (DEN * 1000)
#define VBAT_SCALE_NUM               (VBAT_DIVIDER_R1_OHM + VBAT_DIVIDER_R2_OHM)
#define VBAT_SCALE_DEN               VBAT_DIVIDER_R2_OHM
// TODO(ejones): 0.0 is a guess. V300 uses an empirical +0.2V fudge; we should
// calibrate this based on ground truth measurements from another device
#define VBAT_READ_OFFSET_V           0.0f

#elif HARDWARE_VER == WICAN_V300

#define TX_GPIO_NUM             	0
#define RX_GPIO_NUM             	3
#define CAN_STDBY_GPIO_NUM			6

#define HW_HAS_MCP2515              0
#define CAN_BUS_COUNT               1

#define CONNECTED_LED_GPIO_NUM		8
#define ACTIVE_LED_GPIO_NUM			9
#define PWR_LED_GPIO_NUM			7
// LEDs are active low, except the PWR LED, which appears
// to be active-high from its historical use in main.c.
#define LED_ON                      0
#define LED_OFF                     1
#define PWR_LED_ON                  1
#define PWR_LED_OFF                 0

// VBAT sense (mV*116)/(16*1000) plus +0.2V fudge */
#define VBAT_ADC_CHANNEL            ADC_CHANNEL_4
#define VBAT_ADC_ATTEN              ADC_ATTEN_DB_12
#define VBAT_SCALE_NUM              116
#define VBAT_SCALE_DEN              16
#define VBAT_READ_OFFSET_V          0.2f

#else
#error "HARDWARE_VER must be WICAN_V300 or WICAN_CUSTOM"
#endif

esp_err_t hw_config_get_device_id(char *uid);

#endif
