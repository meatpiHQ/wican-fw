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
#include "wusb3801.h"
#include "esp_log.h"

#define TAG 		__func__

static i2c_port_t wusb3801_i2c = I2C_NUM_MAX;

static esp_err_t wusb3801_register_read(uint8_t reg_addr, uint8_t *data)
{
    if(wusb3801_i2c >= I2C_NUM_MAX)
    {
        return ESP_ERR_INVALID_ARG;
    }
    return i2c_master_write_read_device(wusb3801_i2c, WUSB3801_ADDRESS, &reg_addr, 1, data, 1, pdMS_TO_TICKS(WUSB3801_I2C_TIMEOUT_MS));
}

static esp_err_t wusb3801_register_write_byte(uint8_t reg_addr, uint8_t data)
{
    int ret;
    uint8_t write_buf[2];
    if(wusb3801_i2c >= I2C_NUM_MAX)
    {
        return ESP_ERR_INVALID_ARG;
    }
    
    write_buf[0] = reg_addr;
    write_buf[1] = data;

    ret = i2c_master_write_to_device(wusb3801_i2c, WUSB3801_ADDRESS, write_buf, 2, pdMS_TO_TICKS(WUSB3801_I2C_TIMEOUT_MS));

    return ret;
}

uint8_t wusb3801_get_dev_id(void)
{
    uint8_t dev_id = 0;
    wusb3801_register_read(WUSB3801_DEV_ID_REG, &dev_id);
    return dev_id;
}

uint8_t wusb3801_get_cc_stat(void)
{
    uint8_t cc_status = 0;
    wusb3801_register_read(WUSB3801_CC_STAT_REG, &cc_status);
    return cc_status;
}

void wusb3801_init(i2c_port_t i2c_num)
{
    uint8_t dev_id = 0;

    wusb3801_i2c = i2c_num;
    wusb3801_register_read(WUSB3801_DEV_ID_REG, &dev_id);

    ESP_LOGI(TAG, "wusb3801 device id: %02X", dev_id);
    // wusb3801_register_write_byte(WUSB3801_CTRL_REG, 0x0C);
    // wusb3801_register_read(WUSB3801_CTRL_REG, &dev_id);

    // ESP_LOGI(TAG, "WUSB3801_CTRL_REG: %02X", dev_id);
    // while(1)
    // {
        wusb3801_register_read(WUSB3801_CC_STAT_REG, &dev_id);

        // ESP_LOGI(TAG, "wusb3801 status: %02X", dev_id);
        // vTaskDelay(pdMS_TO_TICKS(3000));
    // }
}
