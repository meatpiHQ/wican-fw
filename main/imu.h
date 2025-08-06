#ifndef IMU_H
#define IMU_H

#include <esp_err.h>
#include "icm42670.h"

typedef enum {
    ACTIVITY_STATE_STATIONARY = 0,
    ACTIVITY_STATE_ACTIVE = 1,
    ACTIVITY_STATE_INVALID
} activity_state_t;

esp_err_t imu_init(i2c_port_t i2c_num, gpio_num_t sda_gpio, gpio_num_t scl_gpio, gpio_num_t int_gpio, uint8_t threshold);
esp_err_t imu_config_wom(uint8_t threshold);
esp_err_t imu_enable_wom(bool enable);
esp_err_t imu_read_accel(float *ax, float *ay, float *az);
esp_err_t imu_read_gyro(float *gx, float *gy, float *gz);
esp_err_t imu_read_temp(float *temp);
esp_err_t imu_get_device_id(uint8_t *id);
esp_err_t imu_set_accel_fsr(icm42670_accel_fsr_t fsr);
esp_err_t imu_set_gyro_fsr(icm42670_gyro_fsr_t fsr);
activity_state_t imu_get_activity_state(void);

#endif
