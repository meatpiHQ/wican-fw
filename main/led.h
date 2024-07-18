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

#ifndef LED_h
#define LED_h
#define LED_I2C_TIMEOUT_MS          100
#define AW2023_PWM0_LEVEL           0x34
#define AW2023_PWM1_LEVEL           0x35
#define AW2023_PWM2_LEVEL           0x36

void led_init(i2c_port_t i2c_num);
esp_err_t led_set_level(uint8_t red, uint8_t green, uint8_t blue);

#endif
