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

 
#include "imu.h"
#include "esp_log.h"
#include "driver/i2c.h"
#include "driver/gpio.h"
#include "hw_config.h"
#include "wc_timer.h"
#include "rtc.h"
#include "dev_status.h"
#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"

#define TAG "imu"
#define STATIONARY_TIME_MS      3000

static icm42670_t dev = {0};
static QueueHandle_t imu_motion_evt_queue = NULL;
static QueueHandle_t activity_state_queue = NULL;
static uint8_t imu_wom_threshold = 8; // Default threshold value
// Static storage for imu_motion_evt_queue (length 10, item size uint32_t)
static StaticQueue_t imu_motion_evt_queue_struct;
static uint8_t imu_motion_evt_queue_storage[10 * sizeof(uint32_t)];
// Static storage for activity_state_queue (length 1, item size activity_state_t)
static StaticQueue_t activity_state_queue_struct;
static uint8_t activity_state_queue_storage[1 * sizeof(activity_state_t)];

void IRAM_ATTR imu_isr_handler(void* arg)
{
    uint32_t gpio_num = (uint32_t) arg;
    xQueueSendFromISR(imu_motion_evt_queue, &gpio_num, NULL);
}

static void imu_motion_task(void *pvParameters)
{
    uint32_t gpio_num;
    activity_state_t current_state = ACTIVITY_STATE_STATIONARY;
    activity_state_t last_reported_state = ACTIVITY_STATE_STATIONARY;

    // Get configurable WoM threshold from configuration
    // The threshold value is an 8-bit value where 1 LSB = 3.9mg for ICM-42670-P
    // Range: 1-32, where each unit represents 3.9mg of acceleration
    uint8_t threshold = imu_wom_threshold;
    ESP_LOGI(TAG, "Using IMU threshold: %d (%.1fmg)", threshold, threshold * 3.9f);
    
    esp_err_t ret = imu_config_wom(threshold);
    if (ret != ESP_OK) {
        ESP_LOGE(TAG, "Failed to configure WoM");
        return;
    }

    ret = imu_enable_wom(true);
    if (ret != ESP_OK) {
        ESP_LOGE(TAG, "Failed to enable WoM");
        return;
    }

    ESP_LOGI(TAG, "Wake on Motion enabled - Move the device to trigger interrupt");

    // Initialize timer for stationary detection
    wc_timer_t motion_timer;
    wc_timer_set(&motion_timer, 0);  // Initialize to expired


    activity_state_queue = xQueueCreateStatic(1, sizeof(activity_state_t), activity_state_queue_storage, &activity_state_queue_struct);
    if (activity_state_queue == NULL)
    {
        ESP_LOGE(TAG, "Failed to create activity state queue");
        vTaskDelete(NULL);
        return;
    }

    xQueueOverwrite(activity_state_queue, &current_state);

    while (1) 
    {
        dev_status_wait_for_bits(DEV_AWAKE_BIT, portMAX_DELAY);
        // Wait for WoM interrupt event
        if (xQueueReceive(imu_motion_evt_queue, &gpio_num, pdMS_TO_TICKS(100)))
        {
            // Motion detected by WoM
            wc_timer_set(&motion_timer, STATIONARY_TIME_MS);
            
            // If we were stationary, change to active
            if (current_state == ACTIVITY_STATE_STATIONARY)
            {
                current_state = ACTIVITY_STATE_ACTIVE;
                ESP_LOGI(TAG, "State changed to ACTIVE");
            }
        }

        // Check for transition to stationary state
        if (current_state == ACTIVITY_STATE_ACTIVE)
        {
            if (wc_timer_is_expired(&motion_timer))
            {
                current_state = ACTIVITY_STATE_STATIONARY;
                ESP_LOGI(TAG, "State changed to STATIONARY");
            }
        }

        // Report state change if it has changed
        if (current_state != last_reported_state)
        {
            // Overwrite the queue with new state
            xQueueOverwrite(activity_state_queue, &current_state);
            last_reported_state = current_state;
        }

        vTaskDelay(pdMS_TO_TICKS(200));
    }
}

activity_state_t imu_get_activity_state(void)
{
    activity_state_t state;

    if (activity_state_queue == NULL) {
        return ACTIVITY_STATE_INVALID;
    }

    // Peek at the current state without removing it from queue
    if (xQueuePeek(activity_state_queue, &state, 0) != pdTRUE) {
        return ACTIVITY_STATE_INVALID;
    }

    return state;
}

//TODO: scl_gpio, scl_gpio amd int_gpio are not used
esp_err_t imu_init(i2c_port_t i2c_num, gpio_num_t sda_gpio, gpio_num_t scl_gpio, gpio_num_t int_gpio, uint8_t threshold)
{
    esp_err_t ret;

    // Store the threshold for use by the motion task
    imu_wom_threshold = threshold;

    // Configure GPIO for IMU interrupt
    gpio_config_t io_conf = {
        .intr_type = GPIO_INTR_POSEDGE,
        .mode = GPIO_MODE_INPUT,
        .pin_bit_mask = (1ULL<<IMU_INT_GPIO_NUM),
        .pull_down_en = 0,
        .pull_up_en = 0,
    };
    
    gpio_config(&io_conf);

    imu_motion_evt_queue = xQueueCreateStatic(10, sizeof(uint32_t), imu_motion_evt_queue_storage, &imu_motion_evt_queue_struct);

    gpio_install_isr_service(0);
    gpio_isr_handler_add(IMU_INT_GPIO_NUM, imu_isr_handler, (void *)IMU_INT_GPIO_NUM);

    // Initialize ICM42670 device
    ret = icm42670_init_desc(&dev, ICM42670_I2C_ADDR_GND, i2c_num, 
                            sda_gpio, scl_gpio);
    if (ret != ESP_OK) {
        ESP_LOGE(TAG, "Failed to init device descriptor");
        return ret;
    }

    // Initialize the sensor
    ret = icm42670_init(&dev);
    if (ret != ESP_OK) {
        ESP_LOGE(TAG, "Failed to init device");
        return ret;
    }

    // Configure default settings
    ret = icm42670_set_gyro_fsr(&dev, ICM42670_GYRO_RANGE_2000DPS);
    if (ret != ESP_OK) {
        ESP_LOGE(TAG, "Failed to set gyro FSR");
        return ret;
    }

    ret = icm42670_set_accel_fsr(&dev, ICM42670_ACCEL_RANGE_16G);
    if (ret != ESP_OK) {
        ESP_LOGE(TAG, "Failed to set accel FSR");
        return ret;
    }

    // Set default power modes
    ret = icm42670_set_gyro_pwr_mode(&dev, ICM42670_GYRO_ENABLE_LN_MODE);
    if (ret != ESP_OK) {
        ESP_LOGE(TAG, "Failed to set gyro power mode");
        return ret;
    }

    ret = icm42670_set_accel_pwr_mode(&dev, ICM42670_ACCEL_ENABLE_LN_MODE);
    if (ret != ESP_OK) {
        ESP_LOGE(TAG, "Failed to set accel power mode");
        return ret;
    }

    ESP_LOGI(TAG, "IMU initialized successfully");

    // Allocate stack memory in PSRAM for the IMU motion task
    static StackType_t *imu_motion_task_stack;
    static StaticTask_t imu_motion_task_buffer;
    
    imu_motion_task_stack = heap_caps_malloc(1024*3, MALLOC_CAP_SPIRAM|MALLOC_CAP_8BIT);
    
    if (imu_motion_task_stack == NULL)
    {
        ESP_LOGE(TAG, "Failed to allocate IMU motion task stack memory");
        return ESP_FAIL;
    }
    
    // Create static task
    TaskHandle_t imu_task_handle = xTaskCreateStatic(
        imu_motion_task,
        "imu_motion_task",
        1024*3,
        NULL,
        5,
        imu_motion_task_stack,
        &imu_motion_task_buffer
    );
    
    if (imu_task_handle == NULL)
    {
        ESP_LOGE(TAG, "Failed to create IMU motion task");
        heap_caps_free(imu_motion_task_stack);
        return ESP_FAIL;
    }

    ESP_LOGI(TAG, "IMU motion task created successfully");
    return ESP_OK;
}

esp_err_t imu_config_wom(uint8_t threshold)
{
    const icm42670_int_config_t int_config = {
        .mode = ICM42670_INT_MODE_PULSED,
        .drive = ICM42670_INT_DRIVE_PUSH_PULL,
        .polarity = ICM42670_INT_POLARITY_ACTIVE_HIGH,
    };

    ESP_ERROR_CHECK_WITHOUT_ABORT(icm42670_config_int_pin(&dev, 1, int_config));

    icm42670_int_source_t sources = {0};
    sources.wom_x = true;
    sources.wom_y = true;
    sources.wom_z = true;

    ESP_ERROR_CHECK_WITHOUT_ABORT(icm42670_set_int_sources(&dev, 1, sources));

    const icm42670_wom_config_t wom_config = {
        .trigger = ICM42670_WOM_INT_DUR_FIRST,
        .logical_mode = ICM42670_WOM_INT_MODE_ALL_OR,
        .reference = ICM42670_WOM_MODE_REF_INITIAL,
        .wom_x_threshold = threshold,
        .wom_y_threshold = threshold,
        .wom_z_threshold = threshold,
    };

    return icm42670_config_wom(&dev, wom_config);
}

esp_err_t imu_enable_wom(bool enable)
{
    esp_err_t ret = ESP_OK;

    if (enable) {
        // Configure optimal settings for WoM
        ret = icm42670_set_accel_odr(&dev, ICM42670_ACCEL_ODR_200HZ);
        if (ret != ESP_OK) return ret;

        ret = icm42670_set_accel_avg(&dev, ICM42670_ACCEL_AVG_8X);
        if (ret != ESP_OK) return ret;

        ret = icm42670_set_gyro_pwr_mode(&dev, ICM42670_GYRO_DISABLE);
        if (ret != ESP_OK) return ret;

        ret = icm42670_set_low_power_clock(&dev, ICM42670_LP_CLK_WUO);
        if (ret != ESP_OK) return ret;

        ret = icm42670_set_accel_pwr_mode(&dev, ICM42670_ACCEL_ENABLE_LP_MODE);
        if (ret != ESP_OK) return ret;
    }

    return icm42670_enable_wom(&dev, enable);
}

esp_err_t imu_read_accel(float *ax, float *ay, float *az)
{
    int16_t raw_x, raw_y, raw_z;
    esp_err_t ret;

    ret = icm42670_read_raw_data(&dev, ICM42670_REG_ACCEL_DATA_X1, &raw_x);
    if (ret != ESP_OK) return ret;

    ret = icm42670_read_raw_data(&dev, ICM42670_REG_ACCEL_DATA_Y1, &raw_y);
    if (ret != ESP_OK) return ret;

    ret = icm42670_read_raw_data(&dev, ICM42670_REG_ACCEL_DATA_Z1, &raw_z);
    if (ret != ESP_OK) return ret;

    // Convert to g's (assuming ±16g range)
    const float scale = 16.0f / 32768.0f;
    *ax = raw_x * scale;
    *ay = raw_y * scale;
    *az = raw_z * scale;

    return ESP_OK;
}

esp_err_t imu_read_gyro(float *gx, float *gy, float *gz)
{
    int16_t raw_x, raw_y, raw_z;
    esp_err_t ret;

    ret = icm42670_read_raw_data(&dev, ICM42670_REG_GYRO_DATA_X1, &raw_x);
    if (ret != ESP_OK) return ret;

    ret = icm42670_read_raw_data(&dev, ICM42670_REG_GYRO_DATA_Y1, &raw_y);
    if (ret != ESP_OK) return ret;

    ret = icm42670_read_raw_data(&dev, ICM42670_REG_GYRO_DATA_Z1, &raw_z);
    if (ret != ESP_OK) return ret;

    // Convert to degrees/sec (assuming ±2000 dps range)
    const float scale = 2000.0f / 32768.0f;
    *gx = raw_x * scale;
    *gy = raw_y * scale;
    *gz = raw_z * scale;

    return ESP_OK;
}

esp_err_t imu_read_temp(float *temp)
{
    return icm42670_read_temperature(&dev, temp);
}

esp_err_t imu_get_device_id(uint8_t *id)
{
    uint16_t value;
    esp_err_t ret = icm42670_read_raw_data(&dev, ICM42670_REG_WHO_AM_I, (int16_t*)&value);
    
    if (ret == ESP_OK) {
        *id = (value>>8) & 0xFF;
    }
    return ret;
}

esp_err_t imu_set_accel_fsr(icm42670_accel_fsr_t fsr)
{
    return icm42670_set_accel_fsr(&dev, fsr);
}

esp_err_t imu_set_gyro_fsr(icm42670_gyro_fsr_t fsr)
{
    return icm42670_set_gyro_fsr(&dev, fsr);
}