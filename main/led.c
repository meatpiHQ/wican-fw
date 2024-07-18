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

#include "driver/i2c.h"
#include "led.h"
static i2c_port_t led_i2c = I2C_NUM_MAX;

static esp_err_t AW2023_register_read(uint8_t reg_addr, uint8_t *data, size_t len)
{
    if(led_i2c >= I2C_NUM_MAX)
    {
        return ESP_ERR_INVALID_ARG;
    }
    return i2c_master_write_read_device(led_i2c, 0x45, &reg_addr, 1, data, len, pdMS_TO_TICKS(LED_I2C_TIMEOUT_MS));
}

static esp_err_t AW2023_register_write_byte(uint8_t reg_addr, uint8_t data)
{
    int ret;
    uint8_t write_buf[2];
    if(led_i2c >= I2C_NUM_MAX)
    {
        return ESP_ERR_INVALID_ARG;
    }
    
    write_buf[0] = reg_addr;
    write_buf[1] = data;

    ret = i2c_master_write_to_device(led_i2c, 0x45, write_buf, 2, pdMS_TO_TICKS(LED_I2C_TIMEOUT_MS));

    return ret;
}

esp_err_t led_set_level(uint8_t red, uint8_t green, uint8_t blue)
{
    esp_err_t ret0, ret1, ret2;
    
    ret0 = AW2023_register_write_byte(AW2023_PWM0_LEVEL, red);
    ret1 = AW2023_register_write_byte(AW2023_PWM1_LEVEL, green);
    ret2 = AW2023_register_write_byte(AW2023_PWM2_LEVEL, blue);

    if(ret0 != ESP_OK || ret1 != ESP_OK || ret2 != ESP_OK )
    {
        return ESP_FAIL;
    }
    else return ESP_OK;
}

void led_init(i2c_port_t i2c_num)
{
    led_i2c = i2c_num;
    AW2023_register_write_byte(0x00, 0x55);
    // ESP_ERROR_CHECK(AW2023_register_read(0x00, &data_byte, 1));
    // ESP_LOGI(__func__, "ID: %u", data_byte);
    AW2023_register_write_byte(0x01, 0x01);
    AW2023_register_write_byte(0x30, 0x0F);
    AW2023_register_write_byte(0x31, 0x03);
    AW2023_register_write_byte(0x32, 0x03);
    AW2023_register_write_byte(0x33, 0x03);
}