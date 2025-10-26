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
#include "esp_timer.h"
#include "config_server.h"
#include "realdash.h"
#include "slcan.h"
#include "can.h"
#include "ble.h"
#include "esp_sleep.h"
#include "wifi_network.h"
#include "esp_mac.h"
#include "esp_ota_ops.h"
#include "nvs.h"
#include "sleep_mode.h"
#include "mqtt_client.h"
#include "ver.h"
#include "driver/rtc_io.h"
#include "led.h"
#include "obd.h"
#include "esp_adc/adc_oneshot.h"
#include "dev_status.h"
#include "wifi_mgr.h"

// #define TAG 		__func__
#define TAG         "SLEEP_MODE"

#if HARDWARE_VER != WICAN_PRO

#define TIMES              256
#define GET_UNIT(x)        ((x>>3) & 0x1)
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
//#define THRESHOLD_VOLTAGE		13.0f
#define SLEEP_TIME_DELAY		(180*1000*1000)
#define WAKEUP_TIME_DELAY		(200*1000)

static EventGroupHandle_t s_mqtt_event_group = NULL;
#define MQTT_CONNECTED_BIT 			BIT0
#define PUB_SUCCESS_BIT     		BIT1

static float sleep_voltage = 13.1f;
static uint8_t enable_sleep = 0;
static QueueHandle_t voltage_queue = NULL;
static esp_adc_cal_characteristics_t adc1_chars;
// Static queue storage for voltage_queue (queue length = 1, item size = sizeof(float))
static StaticQueue_t voltage_queue_struct;
static uint8_t voltage_queue_storage[sizeof(float)];


static void mqtt_event_handler(void *handler_args, esp_event_base_t base, int32_t event_id, void *event_data)
{
    ESP_LOGD(TAG, "Event dispatched from event loop base=%s, event_id=%ld", base, event_id);
    esp_mqtt_event_handle_t event = event_data;
//    esp_mqtt_client_handle_t client = event->client;

    switch ((esp_mqtt_event_id_t)event_id) {
    case MQTT_EVENT_CONNECTED:
        ESP_LOGI(TAG, "MQTT_EVENT_CONNECTED");
        xEventGroupSetBits(s_mqtt_event_group, MQTT_CONNECTED_BIT);
        break;
    case MQTT_EVENT_DISCONNECTED:
        ESP_LOGI(TAG, "MQTT_EVENT_DISCONNECTED");
        xEventGroupClearBits(s_mqtt_event_group, MQTT_CONNECTED_BIT);
//        esp_mqtt_client_stop(client);
        break;

    case MQTT_EVENT_SUBSCRIBED:
        ESP_LOGI(TAG, "MQTT_EVENT_SUBSCRIBED, msg_id=%d", event->msg_id);
        break;
    case MQTT_EVENT_UNSUBSCRIBED:
        ESP_LOGI(TAG, "MQTT_EVENT_UNSUBSCRIBED, msg_id=%d", event->msg_id);
        break;
    case MQTT_EVENT_PUBLISHED:
        ESP_LOGI(TAG, "MQTT_EVENT_PUBLISHED, msg_id=%d", event->msg_id);
        xEventGroupSetBits(s_mqtt_event_group, PUB_SUCCESS_BIT);
        break;
    case MQTT_EVENT_DATA:
        ESP_LOGI(TAG, "MQTT_EVENT_DATA");
        printf("TOPIC=%.*s\r\n", event->topic_len, event->topic);
        printf("DATA=%.*s\r\n", event->data_len, event->data);
        break;
    case MQTT_EVENT_ERROR:
        ESP_LOGI(TAG, "MQTT_EVENT_ERROR");
//        if (event->error_handle->error_type == MQTT_ERROR_TYPE_TCP_TRANSPORT) {
//            log_error_if_nonzero("reported from esp-tls", event->error_handle->esp_tls_last_esp_err);
//            log_error_if_nonzero("reported from tls stack", event->error_handle->esp_tls_stack_err);
//            log_error_if_nonzero("captured as transport's socket errno",  event->error_handle->esp_transport_sock_errno);
//            ESP_LOGI(TAG, "Last errno string (%s)", strerror(event->error_handle->esp_transport_sock_errno));
//
//        }
        break;
    default:
        ESP_LOGI(TAG, "Other event id:%d", event->event_id);
        break;
    }
}
static esp_mqtt_client_handle_t client = NULL;
static void mqtt_init(void)
{
    esp_mqtt_client_config_t mqtt_cfg = {
		.broker.address.uri = config_server_get_alert_url(),
		.broker.address.port = config_server_get_alert_port(),
		.credentials.username = config_server_get_alert_mqtt_user(),
		.credentials.authentication.password = config_server_get_alert_mqtt_pass(),
		.network.disable_auto_reconnect = true,
		.network.reconnect_timeout_ms = 4000,
//         .uri = config_server_get_alert_url(),
// 		.port = config_server_get_alert_port(),
// 		.username = config_server_get_alert_mqtt_user(),
// 		.password = config_server_get_alert_mqtt_pass(),
// //		.disable_auto_reconnect = 1,
// 		.reconnect_timeout_ms = 4000
    };
    ESP_LOGI(TAG, "mqtt_cfg.uri: %s", mqtt_cfg.broker.address.uri);
    if(client == NULL)
    {
    	client = esp_mqtt_client_init(&mqtt_cfg);
        /* The last argument may be used to pass data to the event handler, in this example mqtt_event_handler */
        esp_mqtt_client_register_event(client, ESP_EVENT_ANY_ID, mqtt_event_handler, NULL);
        esp_mqtt_client_start(client);
    }
    else
    {
    	esp_mqtt_client_reconnect(client);
    }

    EventBits_t bits = xEventGroupWaitBits(s_mqtt_event_group,
						MQTT_CONNECTED_BIT,
						pdFALSE,
						pdFALSE,
						pdMS_TO_TICKS(10000));
    if (bits & MQTT_CONNECTED_BIT)
    {
    	static char pub_data[128];
    	float batt_voltage = 0;
    	sleep_mode_get_voltage(&batt_voltage);
    	sprintf(pub_data, "{\"alert\": \"low_battery\", \"battery_voltage\": %f}", batt_voltage);
        int msg_id = esp_mqtt_client_publish(client, config_server_get_alert_topic(), pub_data, 0, 1, 0);
        ESP_LOGI(TAG, "publish, msg_id=%d", msg_id);

    }
    else
    {
    	ESP_LOGE(TAG, "unable to connect to broker...");
    }
}


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
    esp_adc_cal_characterize(ADC_UNIT_1, ADC_ATTEN_DB_11, ADC_WIDTH_BIT_DEFAULT, 0, &adc1_chars);


}

//ADC Attenuation
#define ADC_ATTEN           ADC_ATTEN_DB_11

//ADC Calibration
#if CONFIG_IDF_TARGET_ESP32
#define ADC_CALI_SCHEME     ESP_ADC_CAL_VAL_EFUSE_VREF
#elif CONFIG_IDF_TARGET_ESP32S2
#define ADC_CALI_SCHEME     ESP_ADC_CAL_VAL_EFUSE_TP
#elif CONFIG_IDF_TARGET_ESP32C3
#define ADC_CALI_SCHEME     ESP_ADC_CAL_VAL_EFUSE_TP
#elif CONFIG_IDF_TARGET_ESP32S3
#define ADC_CALI_SCHEME     ESP_ADC_CAL_VAL_EFUSE_TP_FIT
#endif
static esp_adc_cal_characteristics_t adc1_chars;
static bool adc_calibration_init(void)
{
    esp_err_t ret;
    bool cali_enable = false;

    ret = esp_adc_cal_check_efuse(ADC_CALI_SCHEME);
    if (ret == ESP_ERR_NOT_SUPPORTED) {
        ESP_LOGW(TAG, "Calibration scheme not supported, skip software calibration");
    } else if (ret == ESP_ERR_INVALID_VERSION) {
        ESP_LOGW(TAG, "eFuse not burnt, skip software calibration");
    } else if (ret == ESP_OK) {
        cali_enable = true;
        esp_adc_cal_characterize(ADC_UNIT_1, ADC_ATTEN, ADC_WIDTH_BIT_DEFAULT, 0, &adc1_chars);
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
    static int64_t pub_time = 0;
    static float alert_voltage = 0;
    static uint64_t alert_time;

    alert_time = config_server_get_alert_time();
    alert_time *= (3600000000);
//    alert_time = 10000000;
    ESP_LOGW(TAG, "%" PRIu64 "\n", alert_time);

    if(config_server_get_alert_volt(&alert_voltage) != -1)
    {
    	alert_voltage = 16.0f;
    }

    memset(result, 0xcc, TIMES);
    adc_calibration_init();
    continuous_adc_init(adc1_chan_mask, adc1_chan_mask, channel, sizeof(channel) / sizeof(adc_channel_t));
    adc_digi_start();

    while(1)
    {
    	uint32_t count = 0;
    	uint64_t avg = 0;
    	uint32_t adc_val = 0;
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
				}
				//See `note 1`
//				ESP_LOGI(TAG, "value: %u",(uint32_t)(avg/count));
				vTaskDelay(10);
			}
			else if (ret == ESP_ERR_TIMEOUT)
			{
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

    	if(HARDWARE_VER == WICAN_V300)
    	{
    		battery_voltage = (adc_val*116)/(16*1000.0f);
    	}
    	else if(HARDWARE_VER == WICAN_USB_V100)
    	{
    		battery_voltage = (adc_val*106.49f)/(6.49f*1000.0f);
    	}
    	battery_voltage += 0.2;
    	// if(project_hardware_rev == WICAN_V210)
    	// {
    	// 	battery_voltage = -1;
    	// }

    	xQueueOverwrite( voltage_queue, &battery_voltage );
    	if(enable_sleep == 1)
    	{
			switch(sleep_state)
			{
				case RUN_STATE:
				{
					if(battery_voltage < sleep_voltage)
					{
						ESP_LOGI(TAG, "low voltage, value: %lu, voltage: %f",adc_val, battery_voltage);
						sleep_detect_time = esp_timer_get_time();
						sleep_state++;
					}
					break;
				}
				case SLEEP_DETECTED:
				{
					if(battery_voltage > sleep_voltage)
					{
						ESP_LOGI(TAG, "low voltage, value: %lu, voltage: %f",adc_val, battery_voltage);
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
					if(battery_voltage > sleep_voltage)
					{
						wakeup_detect_time = esp_timer_get_time();
						ESP_LOGI(TAG, "low voltage, value: %lu, voltage: %f",adc_val, battery_voltage);
						sleep_state = WAKEUP_STATE;
					}

					if(config_server_get_battery_alert_config())
					{
						if(battery_voltage < alert_voltage)
						{
							ESP_LOGW(TAG, "battery alert!");
							if(((esp_timer_get_time() - pub_time) > alert_time) || (pub_time == 0))
							{
								pub_time = esp_timer_get_time();
								wifi_network_init(config_server_get_alert_ssid(), config_server_get_alert_pass());
								vTaskDelay(1000 / portTICK_PERIOD_MS);
								uint8_t count = 0;
								while(!wifi_network_is_connected())
								{
									vTaskDelay(1000 / portTICK_PERIOD_MS);
									if(count++ > 10)
									{
										break;
									}
								}
								if(wifi_network_is_connected())
								{
									ESP_LOGI(TAG, " wifi connectred try to publish");
									mqtt_init();
									EventBits_t bits = xEventGroupWaitBits(s_mqtt_event_group,
																			PUB_SUCCESS_BIT,
																			pdFALSE,
																			pdFALSE,
																			pdMS_TO_TICKS(10000));
									if (bits & PUB_SUCCESS_BIT)
									{
										ESP_LOGI(TAG, "publish ok");
										xEventGroupClearBits(s_mqtt_event_group, PUB_SUCCESS_BIT);
									}
									else
									{
										ESP_LOGE(TAG, "publish error");
									}
									esp_mqtt_client_disconnect(client);
									vTaskDelay(1000 / portTICK_PERIOD_MS);
									wifi_network_deinit();
								}
							}
						}
					}
					break;
				}
				case WAKEUP_STATE:
				{
					if(battery_voltage > sleep_voltage)
					{
						if((esp_timer_get_time() - wakeup_detect_time) > WAKEUP_TIME_DELAY)
						{
							ESP_LOGI(TAG, "Wake up now...");
							esp_restart();

						}
					}
					else if(battery_voltage < sleep_voltage)
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
				can_disable();
				wifi_network_deinit();
				ble_disable();
				esp_sleep_enable_timer_wakeup(2*1000000);
				esp_light_sleep_start();;
			}
			else vTaskDelay(pdMS_TO_TICKS(1000));
    	}
    	else
    	{
    		vTaskDelay(pdMS_TO_TICKS(1000));
    	}
    }

    adc_digi_stop();
    ret = adc_digi_deinitialize();
    assert(ret == ESP_OK);
}

int8_t sleep_mode_get_voltage(float *val)
{
	if(voltage_queue != NULL)
	{
		if(xQueuePeek( voltage_queue, val, 0 ))
		{
			return 1;
		}
		else return -1;
	}
	return -1;
}

int8_t sleep_mode_init(uint8_t enable, float sleep_volt)
{
	enable_sleep = enable;
	sleep_voltage = sleep_volt;
	ESP_LOGW(TAG, "sleep_volt: %2.2f", sleep_volt);
	s_mqtt_event_group = xEventGroupCreate();
    voltage_queue = xQueueCreateStatic(1, sizeof(float), voltage_queue_storage, &voltage_queue_struct);
	xTaskCreate(adc_task, "adc_task", 4096, (void*)AF_INET, 5, NULL);

	return 1;
}
#elif HARDWARE_VER == WICAN_PRO
#include <string.h>
#include <stdio.h>
#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
// #include "esp_adc/adc_continuous.h"
#include "esp_adc/adc_cali.h"
#include "esp_adc/adc_cali_scheme.h"
#include "elm327.h"
#include "math.h"
#include "wc_timer.h"
#include "hw_config.h"
#include "sdcard.h"
#include "ble.h"

#define ADC_UNIT          ADC_UNIT_1
#define ADC_CONV_MODE     ADC_CONV_SINGLE_UNIT_1
#define ADC_ATTEN         ADC_ATTEN_DB_6  // 0-3.3V
#define ADC_BIT_WIDTH     ADC_BITWIDTH_DEFAULT 
#define ADC_READ_LEN      256

#define ADC_OUTPUT_TYPE   ADC_DIGI_OUTPUT_FORMAT_TYPE2
#define ADC_GET_CHANNEL(p_data)   ((p_data)->type2.channel)
#define ADC_GET_DATA(p_data)      ((p_data)->type2.data)

#define CRITICAL_VOLTAGE  11.80f

static adc_oneshot_unit_handle_t adc_handle;
static adc_cali_handle_t cali_handle = NULL;
static bool do_calibration = false;
static QueueHandle_t voltage_queue = NULL;
static QueueHandle_t sleep_state_queue = NULL;
// Static queue storage for voltage_queue (queue length = 1, item size = sizeof(float))
static StaticQueue_t voltage_queue_struct;
static uint8_t voltage_queue_storage[sizeof(float)];
// Static queue storage for sleep_state_queue (queue length = 1, item size = sizeof(sleep_state_info_t))
static StaticQueue_t sleep_state_queue_struct;
static uint8_t sleep_state_queue_storage[sizeof(sleep_state_info_t)];

static void calibration_init(void)
{
    esp_err_t ret = ESP_FAIL;
    bool calibrated = false;

#if ADC_CALI_SCHEME_CURVE_FITTING_SUPPORTED
    ESP_LOGI(TAG, "calibration scheme version is %s", "Curve Fitting");
    adc_cali_curve_fitting_config_t cali_config = {
        .unit_id = ADC_UNIT,
        .chan = ADC_CHANNEL_3,
        .atten = ADC_ATTEN,
        .bitwidth = ADC_BIT_WIDTH,
    };
    ret = adc_cali_create_scheme_curve_fitting(&cali_config, &cali_handle);
    if (ret == ESP_OK) {
        calibrated = true;
    }
#endif

#if ADC_CALI_SCHEME_LINE_FITTING_SUPPORTED
    if (!calibrated) 
	{
        ESP_LOGI(TAG, "calibration scheme version is %s", "Line Fitting");
        adc_cali_line_fitting_config_t cali_config = 
		{
            .unit_id = ADC_UNIT,
            .atten = ADC_ATTEN,
            .bitwidth = ADC_BIT_WIDTH,
        };
        ret = adc_cali_create_scheme_line_fitting(&cali_config, &cali_handle);
        if (ret == ESP_OK) 
		{
            calibrated = true;
        }
    }
#endif

    do_calibration = calibrated;
    if (do_calibration) 
	{
        ESP_LOGI(TAG, "Calibration Success");
    } else {
        ESP_LOGW(TAG, "Calibration Failed");
    }
}

void oneshot_adc_init(void)
{
    // Initialize ADC
    adc_oneshot_unit_init_cfg_t init_config = {
        .unit_id = ADC_UNIT,
        .ulp_mode = ADC_ULP_MODE_DISABLE,
    };
    ESP_ERROR_CHECK(adc_oneshot_new_unit(&init_config, &adc_handle));

    // Configure ADC channel
    adc_oneshot_chan_cfg_t config = {
        .atten = ADC_ATTEN,
        .bitwidth = ADC_BIT_WIDTH,
    };
    ESP_ERROR_CHECK(adc_oneshot_config_channel(adc_handle, ADC_CHANNEL_3, &config));

    ESP_LOGI(TAG, "ADC channel: %d, Attenuation: %d", ADC_CHANNEL_3, ADC_ATTEN);

    calibration_init();
    // Initialize calibration
    // do_calibration = example_adc_calibration_init(ADC_UNIT, ADC_CHANNEL_3, ADC_ATTEN, &cali_handle);
}

static void update_battery_voltage(float *new_volt)
{
    if (new_volt != NULL) {
        xQueueOverwrite(voltage_queue, new_volt);
    }
}

esp_err_t read_ss_adc_voltage(float *voltage_out)
{
    if (voltage_out == NULL) {
        return ESP_ERR_INVALID_ARG;
    }

    const int NUM_SAMPLES = 8; // Similar to conv_frame_size in continuous version
    uint32_t sum_raw = 0;
    uint32_t valid_samples = 0;
    uint32_t min_raw = UINT32_MAX;
    uint32_t max_raw = 0;
    int sum_voltage = 0;

    // Take multiple readings
    for (int i = 0; i < NUM_SAMPLES; i++)
    {
        int raw_value;
        esp_err_t ret = adc_oneshot_read(adc_handle, ADC_CHANNEL_3, &raw_value);
        
        if (ret == ESP_OK && raw_value < 4096)
        {
            int voltage = 0;
            
            // Convert raw to voltage using calibration
            if (do_calibration)
            {
                ret = adc_cali_raw_to_voltage(cali_handle, raw_value, &voltage);
            } 
            else
            {
                voltage = (raw_value * 3300) / 4095;
            }
            
            if(ret == ESP_OK)
            {
                sum_raw += raw_value;
                sum_voltage += voltage;
                valid_samples++;
                
                if (raw_value < min_raw) min_raw = raw_value;
                if (raw_value > max_raw) max_raw = raw_value;
                
                // Print first few samples for debugging
                // if (valid_samples <= 5) 
                // {
                //     ESP_LOGI(TAG, "Sample[%d]: Chan=%d, Raw=%d, Voltage=%dmV", 
                //             (int)valid_samples, ADC_CHANNEL_3, raw_value, voltage);
                // }
            }
            else
            {
                ESP_LOGE(TAG, "ADC adc_cali_raw_to_voltage error: %d", ret);
            }
            // Small delay between readings
            // vTaskDelay(pdMS_TO_TICKS(1));
        }
        else
        {
            ESP_LOGE(TAG, "ADC read error: %d", ret);
        }
    }

    // Calculate averages
    if (valid_samples > 0) 
    {
        int avg_raw = sum_raw / valid_samples;
        float avg_voltage = (float)sum_voltage / valid_samples;
        
        #ifdef HV_PRO_V140
        float volt_rounded = ((float)avg_voltage * 7.25f) / 1000;
        #else
        float volt_rounded = ((float)avg_voltage * 11) / 1000;
        volt_rounded+=0.1f;  // Adjust for calibration offset
        #endif
        
        volt_rounded = roundf(volt_rounded * 10.0f) / 10.0f;
        *voltage_out = volt_rounded;
        
        ESP_LOGI(TAG, "Summary: Raw=%d (min=%lu, max=%lu, avg of %lu), Voltage=%.2f V [%s]", 
                 avg_raw, min_raw, max_raw, valid_samples, *voltage_out,
                 do_calibration ? "CALIBRATED" : "UNCALIBRATED");
                 
        return ESP_OK;
    }
    
    return ESP_ERR_INVALID_STATE;  // No valid samples
}

void configure_wakeup_sources(void)
{
    esp_sleep_enable_ext0_wakeup(OBD_READY_PIN, 0);
    rtc_gpio_pullup_dis(OBD_READY_PIN);
}

void enter_deep_sleep(void)
{
	static char response_buffer[32];
	static uint32_t response_len = 0;
	static int64_t response_cmd_time = 0;
	esp_err_t sleep_ret = ESP_OK;
    ESP_LOGI(TAG, "Entering deep sleep");
    configure_wakeup_sources();
    
    // adc_continuous_stop(adc_handle);
    
	if(gpio_get_level(OBD_READY_PIN) == 1)
	{
		printf("MIC is already sleeping!!!!!!!\r\n");
	}
    gpio_set_level(CAN_STDBY_GPIO_NUM, 1);
	// sleep_ret = elm327_sleep();
    
	vTaskDelay(pdMS_TO_TICKS(5000));

	if(sleep_ret == ESP_OK && gpio_get_level(OBD_READY_PIN) == 1)
	{
		printf("MIC chip is sleeping...\r\n");
	}
	else
	{
		printf("MIC sleep failed...\r\n");
	}
    gpio_set_level(CAN_STDBY_GPIO_NUM, 1);
	ESP_LOGI(TAG, "Going to sleep now");
	led_set_level(0,0,0);
    esp_wifi_stop();
    ble_disable();
    sd_card_deinit();
	vTaskDelay(pdMS_TO_TICKS(1000));
	// Enter deep sleep
	esp_deep_sleep_start();
}

void light_sleep_task(void *pvParameters)
{
    static float battery_voltage = 0.0;
    esp_err_t ret = ESP_FAIL;
    system_state_t current_state = STATE_NORMAL;
    sleep_state_info_t state_info = {0};
    static uint8_t sleep_en = -1;
    float sleep_voltage;
    float wakeup_voltage;
    static wc_timer_t voltage_read_timer;
    static wc_timer_t sleep_timer;
    static wc_timer_t wakeup_timer;
	static uint32_t sleep_time;
	static int8_t periodic_wakeup;
	static uint32_t wakeup_interval;
	static wc_timer_t periodic_wakeup_timer;

    // Initialize configuration
    sleep_en = config_server_get_sleep_config();
    if(config_server_get_sleep_volt(&sleep_voltage) == -1)
	{
        sleep_voltage = 13.1f;
    }

    // if(config_server_get_wakeup_volt(&wakeup_voltage) == -1) 
	// {
    //     wakeup_voltage = 13.4f;
    // }
    wakeup_voltage = sleep_voltage + 0.1f;
    
	if(config_server_get_sleep_time(&sleep_time) == -1)
	{
		sleep_time = 120000;
		ESP_LOGE(TAG, "Failed to get sleep time");
	}
	else
	{
		sleep_time *= 60000; //change min to ms
	}

	periodic_wakeup = config_server_get_periodic_wakeup();

    if(periodic_wakeup == -1)
    {
        periodic_wakeup = 0;
    }

    if(config_server_get_wakeup_interval(&wakeup_interval) == -1)
    {
        wakeup_interval = 30*60000; //5 min
        ESP_LOGW(TAG, "Failed to get wakeup interval, using default 5 min");
    }
    else
    {
        wakeup_interval *= 60000; //change min to ms
    }

    // Create queues (static allocation)
    voltage_queue = xQueueCreateStatic(1, sizeof(float), voltage_queue_storage, &voltage_queue_struct);
    sleep_state_queue = xQueueCreateStatic(1, sizeof(sleep_state_info_t), sleep_state_queue_storage, &sleep_state_queue_struct);

    // Initialize ADC
    // calibration_init();
    // continuous_adc_init();
    oneshot_adc_init();
    // ESP_ERROR_CHECK(adc_continuous_start(handle));

    // Log initial configuration
    ESP_LOGI(TAG, "Sleep task started. Sleep enabled: %d, Sleep voltage: %.2f, Wakeup voltage: %.2f, Sleep time: %lu, Periodic wakeup: %d, Wakeup interval: %lu", 
             sleep_en, sleep_voltage, wakeup_voltage, sleep_time, periodic_wakeup, wakeup_interval);

    // Initialize voltage read timer
    wc_timer_set(&voltage_read_timer, 10);
    vTaskDelay(pdMS_TO_TICKS(1000));
    while (1) 
	{
        // Read voltage every 3 seconds
        if(wc_timer_is_expired(&voltage_read_timer)) 
		{
            // ret = sleep_mode_get_voltage(&battery_voltage);
            // ret = read_adc_voltage(&battery_voltage);
            ret = read_ss_adc_voltage(&battery_voltage);
            wc_timer_set(&voltage_read_timer, 3000);
            if(ret == ESP_OK)
            {
                update_battery_voltage(&battery_voltage);
            }
        }

        if (ret == ESP_OK && sleep_en == 1) 
		{
            // State machine logic
            switch (current_state) 
			{
                case STATE_NORMAL:
                    if (battery_voltage < sleep_voltage) 
					{
                        ESP_LOGW(TAG, "Battery voltage low (%.2fV), starting low voltage timer", battery_voltage);
                        current_state = STATE_LOW_VOLTAGE;
                        wc_timer_set(&sleep_timer, sleep_time);
                    }
                    break;

                case STATE_LOW_VOLTAGE:
                    if (battery_voltage >= wakeup_voltage) 
					{
                        ESP_LOGI(TAG, "Battery voltage recovered (%.2fV)", battery_voltage);
                        current_state = STATE_NORMAL;
                    } 
                    else if (wc_timer_is_expired(&sleep_timer)) 
					{
                        ESP_LOGI(TAG, "Low voltage timeout expired, entering sleep mode");
                        current_state = STATE_SLEEPING;
                        gpio_set_level(CAN_STDBY_GPIO_NUM, 1);
                        dev_status_clear_bits(DEV_AWAKE_BIT);
                        dev_status_set_bits(DEV_SLEEP_BIT);
                        elm327_sleep();
                        can_disable();
                        wifi_mgr_deinit();
                        ble_disable();
                        led_set_level(0,0,0);
                        // Update immediately to prevenet elm327 wakeup 
                        state_info.state = current_state;
                        state_info.voltage = battery_voltage;
                        xQueueOverwrite(sleep_state_queue, &state_info);
                        vTaskDelay(pdMS_TO_TICKS(1000));
                        if(periodic_wakeup)
                        {
                            wc_timer_set(&periodic_wakeup_timer, wakeup_interval);
                        }
                    }
                    break;

                case STATE_SLEEPING:
                    if (battery_voltage >= wakeup_voltage) 
					{
                        ESP_LOGI(TAG, "Voltage above wakeup threshold, starting wakeup timer");
                        current_state = STATE_WAKE_PENDING;
                        wc_timer_set(&wakeup_timer, 1000); // 2 second timer for stable voltage
                    }
                    else if(battery_voltage > CRITICAL_VOLTAGE && periodic_wakeup && wc_timer_is_expired(&periodic_wakeup_timer))
                    {
                        ESP_LOGI(TAG, "Periodic wakeup timer expired, returning to normal mode");
                        // current_state = STATE_NORMAL;
                        esp_restart();
                    }
                    break;

                case STATE_WAKE_PENDING:
                    if (battery_voltage < wakeup_voltage) 
					{
                        // Voltage dropped again, go back to sleep
                        current_state = STATE_SLEEPING;
                    }
                    else if (wc_timer_is_expired(&wakeup_timer)) 
					{
                        ESP_LOGI(TAG, "Voltage stable above threshold, returning to normal mode");
                        // current_state = STATE_NORMAL;
                        esp_restart();
                    }
                    break;
            }

            // Update state info and send to queue
            state_info.state = current_state;
            state_info.voltage = battery_voltage;
            xQueueOverwrite(sleep_state_queue, &state_info);

            // Log current status
            ESP_LOGI(TAG, "State: %d, Battery: %.2fV", current_state, battery_voltage);
        } 
        else if (ret != ESP_OK) 
		{
            ESP_LOGW(TAG, "Failed to read ADC: %d", ret);
        }

        // Handle sleep entry
        // if(current_state == STATE_SLEEPING && gpio_get_level(OBD_READY_PIN) == 1) 
        // {
        //     // adc_continuous_stop(handle);
        //     ESP_LOGW(TAG, "Sleep...");
        //     esp_sleep_enable_timer_wakeup(2*1000000);
        //     esp_light_sleep_start();
        //     ESP_LOGW(TAG, "Wakeup...");
        //     if(gpio_get_level(OBD_READY_PIN) == 0)
        //     {
        //         esp_restart();
        //     }
        //     // adc_continuous_start(handle);
        // }
        
        if(current_state == STATE_SLEEPING) 
        {
            static wc_timer_t waketime = 0;
            ESP_LOGW(TAG, "Sleep...");
            ESP_LOGW(TAG, "Wake time: %lld", (esp_timer_get_time()-waketime)/1000);
            esp_sleep_enable_timer_wakeup(2*1000000);
            esp_light_sleep_start();
            waketime = esp_timer_get_time();
            ESP_LOGW(TAG, "Wakeup...");

            static uint8_t elm327_sleep_retries = 0;

            if(elm327_chip_get_status() == ELM327_READY)
            {
                ESP_LOGW(TAG, "ELM327 chip is NOT sleeping after wakeup, retrying... (%u/6)", elm327_sleep_retries);
                if(elm327_sleep_retries < 6)
                {
                    elm327_hardreset_chip();
                    vTaskDelay(pdMS_TO_TICKS(500));
                    elm327_sleep();
                    gpio_hold_en(OBD_READY_PIN);
                    rtc_gpio_hold_en(OBD_READY_PIN);
                    rtc_gpio_pulldown_en(OBD_READY_PIN);
                    gpio_deep_sleep_hold_en();
                    
                    vTaskDelay(pdMS_TO_TICKS(100));
                    elm327_sleep_retries++;
                }
                else
                {
                    ESP_LOGE(TAG, "ELM327 chip is still NOT sleeping after 6 retries, restarting...");
                    vTaskDelay(pdMS_TO_TICKS(100));
                    esp_restart();
                }
                if(elm327_chip_get_status() == ELM327_SLEEP)
                {
                    ESP_LOGW(TAG, "ELM327 chip is sleeping now");
                }
            }
            else
            {
                elm327_sleep_retries = 0;
            }
        }
        if(current_state != STATE_SLEEPING)
        {
            vTaskDelay(pdMS_TO_TICKS(1000));
        }
        
        vTaskDelay(pdMS_TO_TICKS(1));
    }
}

esp_err_t sleep_mode_get_state(sleep_state_info_t *state_info)
{
    if (state_info == NULL) 
	{
        return ESP_ERR_INVALID_ARG;
    }
    
    if (sleep_state_queue == NULL) 
	{
        return ESP_ERR_INVALID_STATE;
    }
    
    if (xQueuePeek(sleep_state_queue, state_info, 0) != pdTRUE) 
	{
        return ESP_ERR_NOT_FOUND;
    }
    
    return ESP_OK;
}

esp_err_t sleep_mode_get_voltage(float *val)
{
    if (voltage_queue != NULL)
    {
        if (xQueuePeek(voltage_queue, val, 0) == pdTRUE)
        {
            return ESP_OK;
        }
    }
    return ESP_ERR_NOT_FOUND;
}

void sleep_mode_print_wakeup_reason(void)
{
    esp_sleep_wakeup_cause_t wakeup_reason = esp_sleep_get_wakeup_cause();

    switch(wakeup_reason)
    {
        case ESP_SLEEP_WAKEUP_EXT0:
            ESP_LOGI(TAG, "Wake up from ext0");
            break;
        case ESP_SLEEP_WAKEUP_EXT1:
            {
                uint64_t wakeup_pin_mask = esp_sleep_get_ext1_wakeup_status();
                if (wakeup_pin_mask != 0) 
				{
                    int pin = __builtin_ffsll(wakeup_pin_mask) - 1;
                    ESP_LOGI(TAG, "Wake up from GPIO %d", pin);
                } 
				else 
				{
                    ESP_LOGI(TAG, "Wake up from GPIO (pin not identified)");
                }
            }
            break;
        case ESP_SLEEP_WAKEUP_TIMER:
            ESP_LOGI(TAG, "Wake up from timer");
            break;
        case ESP_SLEEP_WAKEUP_TOUCHPAD:
            ESP_LOGI(TAG, "Wake up from touchpad");
            break;
        case ESP_SLEEP_WAKEUP_ULP:
            ESP_LOGI(TAG, "Wake up from ULP");
            break;
        case ESP_SLEEP_WAKEUP_GPIO:
            ESP_LOGI(TAG, "Wake up from GPIO");
            break;
        case ESP_SLEEP_WAKEUP_UART:
            ESP_LOGI(TAG, "Wake up from UART");
            break;
        default:
            ESP_LOGI(TAG, "Wake up not caused by deep sleep: %d", wakeup_reason);
            break;
    }
}

void sleep_mode_init(void)
{
	// if(config_server_get_sleep_config())
	{
		// xTaskCreate(sleep_task, "sleep_task", 4096, (void*)AF_INET, 5, NULL);
        static StackType_t light_sleep_task_stack[4096];
        static StaticTask_t light_sleep_task_buffer;
        
        // Create static task
        TaskHandle_t sleep_task_handle = xTaskCreateStatic(
            light_sleep_task,
            "sleep_task",
            4096,
            (void*)AF_INET,
            5,
            light_sleep_task_stack,
            &light_sleep_task_buffer
        );
        
        if (sleep_task_handle == NULL)
        {
            ESP_LOGE(TAG, "Failed to create light sleep task");
            heap_caps_free(light_sleep_task_stack);
            return;
        }
	}
}

#endif