#include "rtcm.h"
#include "driver/i2c.h"
#include "esp_log.h"

#define TAG "rtcm"

#define RTCM_I2C_TIMEOUT_MS 1000

#define RX8130_ADDR        0x32

#define RX8130_REG_SEC     0x10
#define RX8130_REG_MIN     0x11 
#define RX8130_REG_HOUR    0x12
#define RX8130_REG_CTRL1   0x30
#define RX8130_REG_CTRL2   0x32
#define RX8130_REG_EVT_CTRL 0x1C
#define RX8130_REG_EVT1    0x1D
#define RX8130_REG_EVT2    0x1E
#define RX8130_REG_EVT3    0x1F
#define RX8130_REG_WEEK    0x13
#define RX8130_REG_DAY     0x14
#define RX8130_REG_MONTH   0x15
#define RX8130_REG_YEAR    0x16
#define RX8130_REG_ID      0x17

static i2c_port_t rtcm_i2c = I2C_NUM_MAX;

static esp_err_t rx8130_register_read(uint8_t reg_addr, uint8_t *data, size_t len)
{
    return i2c_master_write_read_device(rtcm_i2c, RX8130_ADDR, &reg_addr, 1, data, len, pdMS_TO_TICKS(RTCM_I2C_TIMEOUT_MS));
}

static esp_err_t rx8130_register_write(uint8_t reg_addr, uint8_t data)
{
    uint8_t write_buf[2] = {reg_addr, data};
    return i2c_master_write_to_device(rtcm_i2c, RX8130_ADDR, write_buf, 2, pdMS_TO_TICKS(RTCM_I2C_TIMEOUT_MS));
}

esp_err_t rtcm_init(i2c_port_t i2c_num)
{
    esp_err_t ret;

    rtcm_i2c = i2c_num;
    // Initialize RX8130 registers
    ret = rx8130_register_write(RX8130_REG_CTRL1, 0x00);
    if (ret != ESP_OK) return ret;

    ret = rx8130_register_write(RX8130_REG_CTRL2, 0xC7);
    if (ret != ESP_OK) return ret;

    ret = rx8130_register_write(RX8130_REG_EVT_CTRL, 0x04);
    if (ret != ESP_OK) return ret;

    ret = rx8130_register_write(RX8130_REG_EVT1, 0x00);
    if (ret != ESP_OK) return ret;

    ret = rx8130_register_write(RX8130_REG_EVT2, 0x40);
    if (ret != ESP_OK) return ret;

    ret = rx8130_register_write(RX8130_REG_EVT3, 0x10);
    if (ret != ESP_OK) return ret;

    ESP_LOGI(TAG, "RTC module initialized");
    return ESP_OK;
}

esp_err_t rtcm_get_time(uint8_t *hour, uint8_t *min, uint8_t *sec)
{
    esp_err_t ret;

    ret = rx8130_register_read(RX8130_REG_SEC, sec, 1);
    if (ret != ESP_OK) return ret;

    ret = rx8130_register_read(RX8130_REG_MIN, min, 1);
    if (ret != ESP_OK) return ret;

    ret = rx8130_register_read(RX8130_REG_HOUR, hour, 1);
    return ret;
}

esp_err_t rtcm_set_time(uint8_t hour, uint8_t min, uint8_t sec)
{
    esp_err_t ret;

    ret = rx8130_register_write(RX8130_REG_SEC, sec);
    if (ret != ESP_OK) return ret;

    ret = rx8130_register_write(RX8130_REG_MIN, min);
    if (ret != ESP_OK) return ret;

    ret = rx8130_register_write(RX8130_REG_HOUR, hour);
    return ret;
}

esp_err_t rtcm_get_date(uint8_t *year, uint8_t *month, uint8_t *day, uint8_t *weekday)
{
    esp_err_t ret;

    ret = rx8130_register_read(RX8130_REG_YEAR, year, 1);
    if (ret != ESP_OK) return ret;

    ret = rx8130_register_read(RX8130_REG_MONTH, month, 1);
    if (ret != ESP_OK) return ret;

    ret = rx8130_register_read(RX8130_REG_DAY, day, 1);
    if (ret != ESP_OK) return ret;

    ret = rx8130_register_read(RX8130_REG_WEEK, weekday, 1);
    return ret;
}

esp_err_t rtcm_set_date(uint8_t year, uint8_t month, uint8_t day, uint8_t weekday)
{
    esp_err_t ret;

    ret = rx8130_register_write(RX8130_REG_YEAR, year);
    if (ret != ESP_OK) return ret;

    ret = rx8130_register_write(RX8130_REG_MONTH, month);
    if (ret != ESP_OK) return ret;

    ret = rx8130_register_write(RX8130_REG_DAY, day);
    if (ret != ESP_OK) return ret;

    ret = rx8130_register_write(RX8130_REG_WEEK, weekday);
    return ret;
}

esp_err_t rtcm_get_device_id(uint8_t *id)
{
    return rx8130_register_read(RX8130_REG_ID, id, 1);
}
