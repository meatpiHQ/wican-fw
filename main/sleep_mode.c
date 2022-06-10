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

#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include  "freertos/queue.h"
#include "freertos/event_groups.h"
#include "esp_wifi.h"
#include "esp_system.h"
#include "esp_event.h"
#include "nvs_flash.h"
#include "driver/gpio.h"
#include "esp_log.h"
#include <string.h>
#include "comm_server.h"
#include "lwip/sockets.h"
#include "driver/twai.h"
#include "types.h"
#include "config_server.h"
#include "realdash.h"
#include "slcan.h"
#include "can.h"
#include "ble.h"
#include "wifi_network.h"
#include "esp_mac.h"
#include "esp_ota_ops.h"
#include "nvs.h"
#include "nvs_flash.h"
#include "driver/adc.h"
#include "esp_adc_cal.h"
#include "sleep_mode.h"
#include "ble.h"
#include "esp_sleep.h"

#define TIMES              256
#define GET_UNIT(x)        ((x>>3) & 0x1)
#define TAG 		__func__
#if CONFIG_IDF_TARGET_ESP32
#define ADC_RESULT_BYTE     2
#define ADC_CONV_LIMIT_EN   1                       //For ESP32, this should always be set to 1
#define ADC_CONV_MODE       ADC_CONV_SINGLE_UNIT_1  //ESP32 only supports ADC1 DMA mode
#define ADC_OUTPUT_TYPE     ADC_DIGI_OUTPUT_FORMAT_TYPE1
#elif CONFIG_IDF_TARGET_ESP32S2
#define ADC_RESULT_BYTE     2
#define ADC_CONV_LIMIT_EN   0
#define ADC_CONV_MODE       ADC_CONV_BOTH_UNIT
#define ADC_OUTPUT_TYPE     ADC_DIGI_OUTPUT_FORMAT_TYPE2
#elif CONFIG_IDF_TARGET_ESP32C3 || CONFIG_IDF_TARGET_ESP32H2 || CONFIG_IDF_TARGET_ESP32C2
#define ADC_RESULT_BYTE     4
#define ADC_CONV_LIMIT_EN   0
#define ADC_CONV_MODE       ADC_CONV_ALTER_UNIT     //ESP32C3 only supports alter mode
#define ADC_OUTPUT_TYPE     ADC_DIGI_OUTPUT_FORMAT_TYPE2
#elif CONFIG_IDF_TARGET_ESP32S3
#define ADC_RESULT_BYTE     4
#define ADC_CONV_LIMIT_EN   0
#define ADC_CONV_MODE       ADC_CONV_SINGLE_UNIT_1
#define ADC_OUTPUT_TYPE     ADC_DIGI_OUTPUT_FORMAT_TYPE2
#endif

#if CONFIG_IDF_TARGET_ESP32C3 || CONFIG_IDF_TARGET_ESP32S3 || CONFIG_IDF_TARGET_ESP32H2 || CONFIG_IDF_TARGET_ESP32C2
#if CONFIG_IDF_TARGET_ESP32C3
static uint16_t adc1_chan_mask = BIT(4);
static adc_channel_t channel[1] = {ADC1_CHANNEL_4};
#else
static uint16_t adc1_chan_mask = BIT(6);
//static uint16_t adc2_chan_mask = BIT(0);
static adc_channel_t channel[1] = {ADC1_CHANNEL_6};
#endif
#endif
#if CONFIG_IDF_TARGET_ESP32S2
static uint16_t adc1_chan_mask = BIT(2) | BIT(3);
static uint16_t adc2_chan_mask = BIT(0);
static adc_channel_t channel[3] = {ADC1_CHANNEL_2, ADC1_CHANNEL_3, (ADC2_CHANNEL_0 | 1 << 3)};
#endif
#if CONFIG_IDF_TARGET_ESP32
static uint16_t adc1_chan_mask = BIT(7);
static uint16_t adc2_chan_mask = 0;
static adc_channel_t channel[1] = {ADC1_CHANNEL_7};
#endif
#define THRESHOLD_VOLTAGE		13.1f
#define SLEEP_TIME_DELAY		(60*1000*1000)
#define WAKEUP_TIME_DELAY		(6*1000*1000)

static QueueHandle_t voltage_queue;
static esp_adc_cal_characteristics_t adc1_chars;


static void continuous_adc_init(uint16_t adc1_chan_mask, uint16_t adc2_chan_mask, adc_channel_t *channel, uint8_t channel_num)
{
    adc_digi_init_config_t adc_dma_config = {
        .max_store_buf_size = 1024,
        .conv_num_each_intr = TIMES,
        .adc1_chan_mask = adc1_chan_mask,
//        .adc2_chan_mask = adc2_chan_mask,
    };
    ESP_ERROR_CHECK(adc_digi_initialize(&adc_dma_config));

    adc_digi_configuration_t dig_cfg = {
        .conv_limit_en = ADC_CONV_LIMIT_EN,
        .conv_limit_num = 250,
        .sample_freq_hz = 10 * 1000,
        .conv_mode = ADC_CONV_MODE,
        .format = ADC_OUTPUT_TYPE,
    };

    adc_digi_pattern_config_t adc_pattern[SOC_ADC_PATT_LEN_MAX] = {0};
    dig_cfg.pattern_num = channel_num;
    for (int i = 0; i < channel_num; i++) {
        uint8_t unit = GET_UNIT(channel[i]);
        uint8_t ch = channel[i] & 0x7;
        adc_pattern[i].atten = ADC_ATTEN_DB_11;
        adc_pattern[i].channel = ch;
        adc_pattern[i].unit = unit;
        adc_pattern[i].bit_width = SOC_ADC_DIGI_MAX_BITWIDTH;

        ESP_LOGI(TAG, "adc_pattern[%d].atten is :%x", i, adc_pattern[i].atten);
        ESP_LOGI(TAG, "adc_pattern[%d].channel is :%x", i, adc_pattern[i].channel);
        ESP_LOGI(TAG, "adc_pattern[%d].unit is :%x", i, adc_pattern[i].unit);
    }
    dig_cfg.adc_pattern = adc_pattern;
    ESP_ERROR_CHECK(adc_digi_controller_configure(&dig_cfg));
    esp_adc_cal_characterize(ADC_UNIT_1, ADC_ATTEN_DB_11, SOC_ADC_DIGI_MAX_BITWIDTH, 0, &adc1_chars);


}

//ADC Attenuation
#define ADC_EXAMPLE_ATTEN           ADC_ATTEN_DB_11

//ADC Calibration
#if CONFIG_IDF_TARGET_ESP32
#define ADC_EXAMPLE_CALI_SCHEME     ESP_ADC_CAL_VAL_EFUSE_VREF
#elif CONFIG_IDF_TARGET_ESP32S2
#define ADC_EXAMPLE_CALI_SCHEME     ESP_ADC_CAL_VAL_EFUSE_TP
#elif CONFIG_IDF_TARGET_ESP32C3
#define ADC_EXAMPLE_CALI_SCHEME     ESP_ADC_CAL_VAL_EFUSE_TP
#elif CONFIG_IDF_TARGET_ESP32S3
#define ADC_EXAMPLE_CALI_SCHEME     ESP_ADC_CAL_VAL_EFUSE_TP_FIT
#endif
static esp_adc_cal_characteristics_t adc1_chars;
static bool adc_calibration_init(void)
{
    esp_err_t ret;
    bool cali_enable = false;

    ret = esp_adc_cal_check_efuse(ADC_EXAMPLE_CALI_SCHEME);
    if (ret == ESP_ERR_NOT_SUPPORTED) {
        ESP_LOGW(TAG, "Calibration scheme not supported, skip software calibration");
    } else if (ret == ESP_ERR_INVALID_VERSION) {
        ESP_LOGW(TAG, "eFuse not burnt, skip software calibration");
    } else if (ret == ESP_OK) {
        cali_enable = true;
        esp_adc_cal_characterize(ADC_UNIT_1, ADC_EXAMPLE_ATTEN, ADC_WIDTH_BIT_DEFAULT, 0, &adc1_chars);
    } else {
        ESP_LOGE(TAG, "Invalid arg");
    }

    return cali_enable;
}

#if !CONFIG_IDF_TARGET_ESP32
static bool check_valid_data(const adc_digi_output_data_t *data)
{
    const unsigned int unit = data->type2.unit;
    if (unit > 2) return false;
    if (data->type2.channel >= SOC_ADC_CHANNEL_NUM(unit)) return false;

    return true;
}
#endif

#define RUN_STATE			0
#define	SLEEP_DETECTED		1
#define SLEEP_STATE			2
#define WAKEUP_STATE		3

static void adc_task(void *pvParameters)
{
    esp_err_t ret;
    uint32_t ret_num = 0;
    uint8_t result[TIMES] = {0};
    static uint8_t sleep_state = 0;
    static int64_t sleep_detect_time = 0;
    static int64_t wakeup_detect_time = 0;

    memset(result, 0xcc, TIMES);
    adc_calibration_init();
    continuous_adc_init(adc1_chan_mask, adc1_chan_mask, channel, sizeof(channel) / sizeof(adc_channel_t));
    adc_digi_start();

    while(1)
    {
    	uint32_t count;
    	uint64_t avg;
    	uint32_t adc_val;
    	for(int j = 0; j < 10; j++)
    	{
			ret = adc_digi_read_bytes(result, TIMES, &ret_num, ADC_MAX_DELAY);
			if (ret == ESP_OK || ret == ESP_ERR_INVALID_STATE)
			{
				if (ret == ESP_ERR_INVALID_STATE)
				{
					/**
					 * @note 1
					 * Issue:
					 * As an example, we simply print the result out, which is super slow. Therefore the conversion is too
					 * fast for the task to handle. In this condition, some conversion results lost.
					 *
					 * Reason:
					 * When this error occurs, you will usually see the task watchdog timeout issue also.
					 * Because the conversion is too fast, whereas the task calling `adc_digi_read_bytes` is slow.
					 * So `adc_digi_read_bytes` will hardly block. Therefore Idle Task hardly has chance to run. In this
					 * example, we add a `vTaskDelay(1)` below, to prevent the task watchdog timeout.
					 *
					 * Solution:
					 * Either decrease the conversion speed, or increase the frequency you call `adc_digi_read_bytes`
					 */
				}

	//            ESP_LOGI("TASK:", "ret is %x, ret_num is %d", ret, ret_num);
				count = 0;
				avg = 0;
				for (int i = 0; i < ret_num; i += ADC_RESULT_BYTE)
				{
					adc_digi_output_data_t *p = (void*)&result[i];
		#if CONFIG_IDF_TARGET_ESP32
					ESP_LOGI(TAG, "Unit: %d, Channel: %d, Value: %x", 1, p->type1.channel, p->type1.data);
		#else
					if (ADC_CONV_MODE == ADC_CONV_SINGLE_UNIT_1 || ADC_CONV_MODE == ADC_CONV_ALTER_UNIT)
					{
						if (check_valid_data(p))
						{
							count++;
							avg += (esp_adc_cal_raw_to_voltage(p->type2.data, &adc1_chars));
	//                        ESP_LOGI(TAG, "Unit: %d,_Channel: %d, Value: %d", p->type2.unit+1, p->type2.channel, p->type2.data);
						}
						else
						{
							// abort();
							ESP_LOGI(TAG, "Invalid data [%d_%d_%x]", p->type2.unit+1, p->type2.channel, p->type2.data);
						}
					}
		#if CONFIG_IDF_TARGET_ESP32S2
					else if (ADC_CONV_MODE == ADC_CONV_SINGLE_UNIT_2) {
						ESP_LOGI(TAG, "Unit: %d, Channel: %d, Value: %x", 2, p->type1.channel, p->type1.data);
					} else if (ADC_CONV_MODE == ADC_CONV_SINGLE_UNIT_1) {
						ESP_LOGI(TAG, "Unit: %d, Channel: %d, Value: %x", 1, p->type1.channel, p->type1.data);
					}
		#endif  //#if CONFIG_IDF_TARGET_ESP32S2
		#endif
				}
				//See `note 1`
//				ESP_LOGI(TAG, "value: %u",(uint32_t)(avg/count));
				vTaskDelay(10);
			} else if (ret == ESP_ERR_TIMEOUT) {
				/**
				 * ``ESP_ERR_TIMEOUT``: If ADC conversion is not finished until Timeout, you'll get this return error.
				 * Here we set Timeout ``portMAX_DELAY``, so you'll never reach this branch.
				 */
				ESP_LOGW(TAG, "No data, increase timeout or reduce conv_num_each_intr");
				vTaskDelay(2000);
			}
    	}
    	adc_val = (uint32_t)(avg/count);
    	float battery_voltage;

    	battery_voltage = (adc_val*116)/(16*1000.0f);
    	xQueueOverwrite( voltage_queue, &battery_voltage );

    	switch(sleep_state)
    	{
    		case RUN_STATE:
    		{
    	    	if(battery_voltage < THRESHOLD_VOLTAGE)
    	    	{
    	    		ESP_LOGI(TAG, "low voltage, value: %u, voltage: %f",adc_val, battery_voltage);
    	    		sleep_detect_time = esp_timer_get_time();
    	    		sleep_state++;
    	    	}
    			break;
    		}
    		case SLEEP_DETECTED:
    		{
    	    	if(battery_voltage > THRESHOLD_VOLTAGE)
    	    	{
    	    		ESP_LOGI(TAG, "low voltage, value: %u, voltage: %f",adc_val, battery_voltage);
    	    		sleep_state = RUN_STATE;
    	    	}

    	    	if((esp_timer_get_time() - sleep_detect_time) > SLEEP_TIME_DELAY)
    	    	{
    	    		sleep_state = SLEEP_STATE;
//    	    		wifi_network_deinit();
//    	    		ble_disable();
    	    	}

    			break;
    		}
    		case SLEEP_STATE:
    		{
    			ESP_LOGI(TAG, "Go to sleep");
    	    	if(battery_voltage > THRESHOLD_VOLTAGE)
    	    	{
    	    		wakeup_detect_time = esp_timer_get_time();
    	    		ESP_LOGI(TAG, "low voltage, value: %u, voltage: %f",adc_val, battery_voltage);
    	    		sleep_state = WAKEUP_STATE;
    	    	}
    			break;
    		}
    		case WAKEUP_STATE:
    		{
    	    	if(battery_voltage > THRESHOLD_VOLTAGE)
    	    	{
    	    		if((esp_timer_get_time() - wakeup_detect_time) > WAKEUP_TIME_DELAY)
    	    		{
        	    		ESP_LOGI(TAG, "Wake up now...");
//        	    		sleep_state = RUN_STATE;
        	    		vTaskDelay(3000 / portTICK_PERIOD_MS);
        	    		esp_restart();

    	    		}
    	    	}
    	    	else if(battery_voltage < THRESHOLD_VOLTAGE)
    	    	{
    	    		sleep_state = SLEEP_STATE;
    	    	}
    			break;
    		}
    	}

//    	ESP_LOGI(TAG, "value: %u",adc_val);
    	if(sleep_state == SLEEP_STATE)
    	{
    		ESP_LOGW(TAG, "sleeping");
    		wifi_network_deinit();
    		ble_disable();
    		esp_sleep_enable_timer_wakeup(2*1000000);
    		esp_light_sleep_start();;
    	}
    	else vTaskDelay(1000);
    }

    adc_digi_stop();
    ret = adc_digi_deinitialize();
    assert(ret == ESP_OK);
}

int8_t sleep_mode_get_voltage(float *val)
{

	if(xQueueReceive( voltage_queue, val, 0 ))
	{
		return 1;
	}
	else return -1;
}

int8_t sleep_mode_init(void)
{
	voltage_queue = xQueueCreate(1, sizeof( float) );
	xTaskCreate(adc_task, "adc_task", 4096, (void*)AF_INET, 5, NULL);

	return 1;
}
