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
#include "freertos/semphr.h"
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
#include "sleep_mode.h"
#include "ble.h"
#include "esp_sleep.h"
#include "lwip/sockets.h"
#include "lwip/dns.h"
#include "lwip/netdb.h"

#include "esp_log.h"
#include "mqtt_client.h"
#include "ver.h"
#include "cJSON.h"
#include "wifi_network.h"
#include "mqtt.h"
#include <stdbool.h>
#include <ctype.h>
#include "esp_timer.h"
#include "expression_parser.h"
#include "dev_status.h"
#include "wc_timer.h"
#include "wifi_mgr.h"
// TLS & Certificate Manager
#include "cert_manager.h"
#include "esp_crt_bundle.h"

#define TAG 		"MQTT"
#define MQTT_TX_RX_BUF_SIZE         (1024*20)
#define MQTT_OUT_BUF_SIZE           (1024*20)
#define MQTT_BATTERY_TIMER_MS        (1000*60*10)

static EventGroupHandle_t s_mqtt_event_group = NULL;
static StaticEventGroup_t mqtt_event_group_buffer;
#define MQTT_CONNECTED_BIT 			BIT0
#define PUB_SUCCESS_BIT     		BIT1
static esp_mqtt_client_handle_t client = NULL;
static char *device_id;
static char *mqtt_sub_topic;
static char *mqtt_status_topic;
static char *mqtt_cmd_topic;
static char *mqtt_rsp_topic;
static char *mqtt_battery_topic;
static char battery_buffer[64];
static uint8_t mqtt_led = 0;

static QueueHandle_t *xmqtt_tx_queue;
static uint8_t mqtt_elm327_log = 0;
static SemaphoreHandle_t xmqtt_semaphore;
// Persistently allocated MQTT URI override when using TLS
static char *g_mqtt_uri_override = NULL;


typedef struct 
{
    uint32_t can_id;
    char name[16];
	int32_t pid;
    int32_t pidi;
    uint32_t start_bit;
    uint32_t bit_length;
    char expression[32];
    uint32_t cycle;
	int64_t logtime;
} CANFilter;

static CANFilter *mqtt_canflt_values = NULL;
static uint32_t mqtt_canflt_size = 0;

static void log_error_if_nonzero(const char *message, int error_code)
{
    if (error_code != 0) {
        ESP_LOGE(TAG, "%s: 0x%x", message, error_code);
    }
}


static void mqtt_event_handler(void *handler_args, esp_event_base_t base, int32_t event_id, void *event_data)
{
    ESP_LOGD(TAG, "Event dispatched from event loop base=%s, event_id=%ld", base, event_id);
    esp_mqtt_event_handle_t event = event_data;
//    esp_mqtt_client_handle_t client = event->client;

    switch ((esp_mqtt_event_id_t)event_id)
    {
		case MQTT_EVENT_CONNECTED:
			ESP_LOGI(TAG, "MQTT_EVENT_CONNECTED");
			dev_status_set_bits(DEV_MQTT_CONNECTED_BIT);
            if(config_server_mqtt_tx_en_config())
            {
                esp_mqtt_client_subscribe(client, mqtt_sub_topic, 0);
            }
			
            esp_mqtt_client_subscribe(client, mqtt_cmd_topic, 0);
			gpio_set_level(mqtt_led, 0);
			esp_mqtt_client_publish(client, mqtt_status_topic, "{\"status\": \"online\"}", 0, 0, 1);

            static float vbatt = 0;
            if (sleep_mode_get_voltage(&vbatt) == ESP_OK) 
            {
                // Use snprintf for buffer safety
                int len = snprintf(battery_buffer, sizeof(battery_buffer), 
                                "{\"battery_voltage\": %.2f}", vbatt);
                
                if (len > 0 && len < sizeof(battery_buffer)) 
                {
                    // Check MQTT publish result
                    int msg_id = esp_mqtt_client_publish(client, mqtt_battery_topic, 
                                                    battery_buffer, 0, 0, 1);
                    if (msg_id < 0)
                    {
                        ESP_LOGE(TAG, "Failed to publish battery voltage");
                    }
                }
            } 
            else
            {
                ESP_LOGE(TAG, "Failed to read battery voltage");
            }

            xEventGroupSetBits(s_mqtt_event_group, MQTT_CONNECTED_BIT);
			break;
		case MQTT_EVENT_DISCONNECTED:
			ESP_LOGI(TAG, "MQTT_EVENT_DISCONNECTED");
            dev_status_clear_bits(DEV_MQTT_CONNECTED_BIT);
			xEventGroupClearBits(s_mqtt_event_group, MQTT_CONNECTED_BIT);
			gpio_set_level(mqtt_led, 1);
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
//			printf("TOPIC=%.*s\r\n", event->topic_len, event->topic);
//			printf("DATA=%.*s\r\n", event->data_len, event->data);
			break;
		case MQTT_EVENT_ERROR:
            ESP_LOGI(TAG, "MQTT_EVENT_ERROR");
            if (event->error_handle) {
                if (event->error_handle->error_type == MQTT_ERROR_TYPE_TCP_TRANSPORT) {
                    log_error_if_nonzero("reported from esp-tls", event->error_handle->esp_tls_last_esp_err);
                    log_error_if_nonzero("reported from tls stack", event->error_handle->esp_tls_stack_err);
                    log_error_if_nonzero("captured as transport's socket errno",  event->error_handle->esp_transport_sock_errno);
                    ESP_LOGI(TAG, "Last errno (%d): %s", event->error_handle->esp_transport_sock_errno,
                             strerror(event->error_handle->esp_transport_sock_errno));
                }
            }
			break;
		default:
			ESP_LOGI(TAG, "Other event id:%d", event->event_id);
			break;
    }
}

//receive can topic:wican/84f703406f75/can/rx
///received message json format:{"bus":"0","type":"rx","ts":34610,"frame":[{"id":123,"dlc":8,"rtr":false,"extd":false,"data":[1,2,3,4,5,6,7,8]},{"id":124,"dlc":8,"rtr":false,"extd":true,"data":[1,2,3,4,5,6,7,8]}]}

//send to can topic:wican/84f703406f75/can/tx
//send message json format: {"bus":0,"type":"tx","frame":[{"id":123,"dlc":8,"rtr":false,"extd":true,"data":[1,2,3,4,5,6,7,8]},{"id":124,"dlc":8,"rtr":false,"extd":true,"data":[1,2,3,4,5,6,7,8]}]}

//															 	    id:0x7E0                                          PID: 47 or0x2F
//get fuel level send: {"bus":0,"type":"tx","ts":35519,"frame":[{"id":2016,"dlc":8,"rtr":false,"extd":false,"data":[2,1,47,170,170,170,170,170]}]}

static void mqtt_parse_data(void *handler_args, esp_event_base_t base, int32_t event_id, void *event_data)
{
    twai_message_t can_frame;
    cJSON *root = NULL;
    cJSON *frame = NULL;
    esp_mqtt_event_handle_t event = event_data;
    
    if ((mqtt_elm327_log == 0) && event->topic && (event->topic_len == (int)strlen(mqtt_sub_topic)) &&
        (memcmp(event->topic, mqtt_sub_topic, event->topic_len) == 0))
    {
        root = cJSON_Parse(event->data);

        if (root == NULL)
        {
            ESP_LOGE(TAG, "Failed to parse JSON data");
            goto end;
        }

        cJSON *frame_ary = cJSON_GetObjectItem(root, "frame");

        if (frame_ary == NULL || !cJSON_IsArray(frame_ary))
        {
            ESP_LOGE(TAG, "Missing 'frame' object in JSON");
            goto end;
        }

        for (int i = 0; i < cJSON_GetArraySize(frame_ary); i++)
        {
            frame = cJSON_GetArrayItem(frame_ary, i);

            if (frame == NULL)
            {
                ESP_LOGE(TAG, "Error parsing JSON frame");
                goto end;
            }

            cJSON *frame_data = cJSON_GetObjectItem(frame, "data");

            if (frame_data == NULL || !cJSON_IsArray(frame_data))
            {
                ESP_LOGE(TAG, "Missing or invalid 'data' array in JSON");
                goto end;
            }

            cJSON *frame_id = cJSON_GetObjectItem(frame, "id");
            if (frame_id == NULL || !cJSON_IsNumber(frame_id))
            {
                ESP_LOGE(TAG, "Missing or invalid 'id' value in JSON");
                goto end;
            }

            cJSON *frame_dlc = cJSON_GetObjectItem(frame, "dlc");
            if (frame_dlc == NULL || !cJSON_IsNumber(frame_dlc))
            {
                ESP_LOGE(TAG, "Missing or invalid 'dlc' value in JSON");
                goto end;
            }

            cJSON *frame_extd = cJSON_GetObjectItem(frame, "extd");
            if (frame_extd == NULL || !cJSON_IsBool(frame_extd))
            {
                ESP_LOGE(TAG, "Missing or invalid 'extd' value in JSON");
                goto end;
            }

            cJSON *frame_rtr = cJSON_GetObjectItem(frame, "rtr");
            if (frame_rtr == NULL || !cJSON_IsBool(frame_rtr))
            {
                ESP_LOGE(TAG, "Missing or invalid 'rtr' value in JSON");
                goto end;
            }

            can_frame.identifier = cJSON_IsTrue(frame_extd) ? (frame_id->valueint & TWAI_EXTD_ID_MASK) : (frame_id->valueint & TWAI_STD_ID_MASK);
            can_frame.data_length_code = (frame_dlc->valueint > 8) ? 8 : frame_dlc->valueint;
            can_frame.extd = cJSON_IsTrue(frame_extd) ? 1 : 0;
            can_frame.rtr = cJSON_IsTrue(frame_rtr) ? 1 : 0;

            // ESP_LOGI(TAG, "frame_id: %d, frame_dlc: %d", can_frame.identifier, can_frame.data_length_code);

            for (int j = 0; j < cJSON_GetArraySize(frame_data); j++)
            {
                can_frame.data[j] = cJSON_GetArrayItem(frame_data, j)->valueint;
            }
            can_frame.self = 0;
            // can_enable();
            can_send(&can_frame, 1);
        }
    }
    else if (event->topic && (event->topic_len == (int)strlen(mqtt_cmd_topic)) &&
             (memcmp(event->topic, mqtt_cmd_topic, event->topic_len) == 0))
    {
        static char cmd_response[32] = {0};

        root = cJSON_Parse(event->data);

        if (root == NULL)
        {
            ESP_LOGE(TAG, "Failed to parse JSON data");
            goto end;
        }

        cJSON *cmd = cJSON_GetObjectItem(root, "cmd");

        if (cmd == NULL || !cJSON_IsString(cmd))
        {
            ESP_LOGE(TAG, "Missing or invalid 'cmd' value in JSON");
            goto end;
        }

        if (strcmp(cmd->valuestring, "reboot") == 0)
        {
            ESP_LOGI(TAG, "Reboot command received");
            sprintf(cmd_response, "{\"rsp\": \"ok\"}");
            mqtt_publish(mqtt_rsp_topic, cmd_response, strlen(cmd_response), 0, 0);
            // Perform the reboot operation here
            vTaskDelay(pdMS_TO_TICKS(2000));
            esp_restart();
        }
        else if(strcmp(cmd->valuestring, "get_vbatt") == 0)
        {
            float vbatt = 0;
            sleep_mode_get_voltage(&vbatt);
            sprintf(cmd_response, "{\"battery_voltage\": %f}", vbatt);
            mqtt_publish(mqtt_rsp_topic, cmd_response, strlen(cmd_response), 0, 0);
        }
        else
        {
            ESP_LOGW(TAG, "Unknown command received: %s", cmd->valuestring);
        }
    }

end:
    if (root != NULL)
    {
        cJSON_Delete(root);
    }
}

int mqtt_connected(void)
{
	EventBits_t uxBits;
	if(s_mqtt_event_group != NULL)
	{
		uxBits = xEventGroupGetBits(s_mqtt_event_group);

		return (uxBits & MQTT_CONNECTED_BIT)?1:0;
	}
	else return 0;
}

static int8_t mqtt_canflt_find_id(uint32_t id, uint8_t start_index)
{
	if(mqtt_canflt_size == 0)
	{
		return -1;
	}
	for(uint32_t i = start_index; i < mqtt_canflt_size; i++)
	{
		if(mqtt_canflt_values[i].can_id == id)
		{
			return i;
		}
	}

	return -1;
}

#define JSON_BUF_SIZE		2048
static void mqtt_task(void *pvParameters)
{
	char json_buffer[JSON_BUF_SIZE] = {0};
	char tmp[150];
	mqtt_can_message_t tx_frame;
	char mqtt_topic[64];
    char mqtt_elm327_topic[64];
	uint64_t can_data = 0;

	// sprintf(mqtt_topic, "wican/%s/can/rx", device_id);
    strcpy(mqtt_topic, config_server_get_mqtt_rx_topic());
    sprintf(mqtt_elm327_topic, "wican/%s/elm327", device_id);

    // Don't hard-block MQTT startup on STA connectivity.
    // The MQTT client has auto-reconnect enabled, so it can start early and connect once WiFi is up.
    while (!wifi_mgr_is_enabled())
    {
        vTaskDelay(pdMS_TO_TICKS(250));
        dev_status_wait_for_bits(DEV_AWAKE_BIT, portMAX_DELAY);
    }

	esp_mqtt_client_register_event(client, ESP_EVENT_ANY_ID, mqtt_event_handler, NULL);
	esp_mqtt_client_register_event(client, MQTT_EVENT_DATA, mqtt_parse_data, NULL);
    esp_err_t start_err = esp_mqtt_client_start(client);
    if (start_err != ESP_OK) {
        ESP_LOGE(TAG, "esp_mqtt_client_start failed: %s", esp_err_to_name(start_err));
    }
    ESP_LOGI(TAG, "MQTT client task started");
	while(1)
	{
		xQueuePeek(*xmqtt_tx_queue, ( void * ) &tx_frame, portMAX_DELAY);
        dev_status_wait_for_bits(DEV_AWAKE_BIT, portMAX_DELAY);
        
		if(mqtt_connected())
		{
			json_buffer[0] = 0;
            if(tx_frame.type == MQTT_CAN)
            {
                if(mqtt_canflt_size != 0)
                {
                    uint8_t start_index = 0;
                    int8_t found_index = -1;
                    static uint64_t value = 0;
                    static double expression_result = 0;
                    
                    xQueueReceive(*xmqtt_tx_queue, ( void * ) &tx_frame, 0);

                    while ((found_index = mqtt_canflt_find_id(tx_frame.frame.identifier, start_index)) != -1)
                    {
                        

                        // Check if expecting PID
                        if(mqtt_canflt_values[found_index].pid != -1)
                        {
                            if(mqtt_canflt_values[found_index].pid != tx_frame.frame.data[mqtt_canflt_values[found_index].pidi])
                            {
                                start_index = found_index + 1;
                                continue;
                            }
                        }

                        if(esp_timer_get_time() - mqtt_canflt_values[found_index].logtime < (mqtt_canflt_values[found_index].cycle*1000))
                        {
                            break;
                        }
                        mqtt_canflt_values[found_index].logtime = esp_timer_get_time();
                        for (uint8_t i = 0; i < 8; i++) 
                        {
                            can_data = (can_data << 8) | tx_frame.frame.data[i];
                        }

                        uint64_t start_bit = 64 - mqtt_canflt_values[found_index].start_bit - mqtt_canflt_values[found_index].bit_length;
                        uint64_t bit_length = mqtt_canflt_values[found_index].bit_length;

                        uint64_t mask = ((1ULL << bit_length) - 1ULL) << start_bit;

                        value = (can_data & mask) >> start_bit;



                        ESP_LOGI(TAG, "-----------");
                        ESP_LOGI(TAG, "can_data: %llx, mask: %llx, value: %llx\r\n", can_data, mask, value);

                        if(evaluate_expression((uint8_t *)mqtt_canflt_values[found_index].expression, (uint8_t *)tx_frame.frame.data, (double)value, &expression_result) )
                        {
                            ESP_LOGI(TAG, "Expression result: %lf", expression_result);

                            sprintf(json_buffer, "{\"%s\": %lf}", mqtt_canflt_values[found_index].name, expression_result);

                            mqtt_publish(mqtt_topic, json_buffer, 0, 0, 0);
                        }
                        else
                        {
                            ESP_LOGE(TAG, "evaluate_expression error");
                        }

                        start_index = found_index + 1;
                    }
                }
                else if(config_server_mqtt_rx_en_config())
                {
                    sprintf(json_buffer, "{\"bus\":\"0\",\"type\":\"rx\",\"ts\":%lu,\"frame\":[", (pdTICKS_TO_MS(xTaskGetTickCount())%60000));

                    if(strlen(json_buffer) < (sizeof(json_buffer) -128))
                    {
                        while(xQueuePeek(*xmqtt_tx_queue, ( void * ) &tx_frame, 0) == pdTRUE)
                        {
                            xQueueReceive(*xmqtt_tx_queue, ( void * ) &tx_frame, 0);
                            sprintf(tmp, "{\"id\":%lu,\"dlc\":%u,\"rtr\":%s,\"extd\":%s,\"data\":[%u,%u,%u,%u,%u,%u,%u,%u]},",tx_frame.frame.identifier, tx_frame.frame.data_length_code, tx_frame.frame.rtr?"true":"false",
                                                                                                                        tx_frame.frame.extd?"true":"false",tx_frame.frame.data[0], tx_frame.frame.data[1], tx_frame.frame.data[2], tx_frame.frame.data[3],
                                                                                                                        tx_frame.frame.data[4], tx_frame.frame.data[5], tx_frame.frame.data[6], tx_frame.frame.data[7]);
                            strcat((char*)json_buffer, (char*)tmp);

                            if(strlen(json_buffer) > (JSON_BUF_SIZE - 100))
                            {
                                break;
                            }
                        }
                        json_buffer[strlen(json_buffer)-1] = 0;
                    }
                    strcat((char*)json_buffer, "]}");

                    mqtt_publish(mqtt_topic, json_buffer, 0, 0, 0);
                }
            }
            else
            {
                xQueueReceive(*xmqtt_tx_queue, ( void * ) &tx_frame, 0);
                if(tx_frame.type == MQTT_RX)
                {
                    sprintf(json_buffer, "{\"bus\":\"0\",\"type\":\"rx\",\"ts\":%lu,\"frame\":[", (pdTICKS_TO_MS(xTaskGetTickCount())%60000));
                    ESP_LOGI(TAG, "tx_frame.type: MQTT_RX");
                }
                else if(tx_frame.type == MQTT_TX)
                {
                    sprintf(json_buffer, "{\"bus\":\"0\",\"type\":\"tx\",\"ts\":%lu,\"frame\":[", (pdTICKS_TO_MS(xTaskGetTickCount())%60000));
                    ESP_LOGI(TAG, "tx_frame.type: MQTT_TX");
                }
                
                sprintf(tmp, "{\"id\":%lu,\"dlc\":%u,\"rtr\":%s,\"extd\":%s,\"data\":[%u,%u,%u,%u,%u,%u,%u,%u]},",tx_frame.frame.identifier, tx_frame.frame.data_length_code, tx_frame.frame.rtr?"true":"false",
                                                                                                            tx_frame.frame.extd?"true":"false",tx_frame.frame.data[0], tx_frame.frame.data[1], tx_frame.frame.data[2], tx_frame.frame.data[3],
                                                                                                            tx_frame.frame.data[4], tx_frame.frame.data[5], tx_frame.frame.data[6], tx_frame.frame.data[7]);
                strcat((char*)json_buffer, (char*)tmp);
                json_buffer[strlen(json_buffer)-1] = 0;
                strcat((char*)json_buffer, "]}");

                mqtt_publish(mqtt_elm327_topic, json_buffer, 0, 0, 0);
            }

		}
		else
		{
			xQueueReceive(*xmqtt_tx_queue, ( void * ) &tx_frame, 0);
		}
		vTaskDelay(pdMS_TO_TICKS(1));
	}
}

static void mqtt_load_filter(void)
{
    char *canflt_json = config_server_get_mqtt_canflt();

    cJSON *root = cJSON_Parse(canflt_json);
    ESP_LOGW(TAG, "mqtt_load_filter start");

    if (root == NULL) 
    {
        ESP_LOGE(TAG, "error parsing canflt_json");
        return;
    }

    cJSON *can_flt = cJSON_GetObjectItem(root, "can_flt");

    if (can_flt == NULL || !cJSON_IsArray(can_flt)) 
    {
        cJSON_Delete(root);
        ESP_LOGE(TAG, "(can_flt == NULL || !cJSON_IsArray(can_flt)) ");
        return;
    }

    mqtt_canflt_size = cJSON_GetArraySize(can_flt);
    mqtt_canflt_values = (CANFilter *)heap_caps_malloc(mqtt_canflt_size * sizeof(CANFilter), MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    memset(mqtt_canflt_values, 0, mqtt_canflt_size * sizeof(CANFilter));
    if (mqtt_canflt_values == NULL) 
    {
        cJSON_Delete(root);
        ESP_LOGE(TAG, "(mqtt_canflt_values == NULL)");
        mqtt_canflt_size = 0;
        return;
    }

    for (uint32_t i = 0; i < mqtt_canflt_size; i++) 
    {
        cJSON *item = cJSON_GetArrayItem(can_flt, i);
        if (!cJSON_IsObject(item)) 
        {
            free(mqtt_canflt_values);
            cJSON_Delete(root);
            ESP_LOGE(TAG, "Failed to get json array can_flt");
            mqtt_canflt_size = 0;
            return;
        }

        // Extract values from the JSON object and populate the CANFilter structure
        cJSON *can_id = cJSON_GetObjectItem(item, "CANID");
        cJSON *name = cJSON_GetObjectItem(item, "Name");
        cJSON *pid = cJSON_GetObjectItem(item, "PID");  // Added 'PID' field
        cJSON *pidi = cJSON_GetObjectItem(item, "PIDIndex");  // Added 'PID' field
        cJSON *start_bit = cJSON_GetObjectItem(item, "StartBit");
        cJSON *bit_length = cJSON_GetObjectItem(item, "BitLength");
        cJSON *expression = cJSON_GetObjectItem(item, "Expression");
        cJSON *cycle = cJSON_GetObjectItem(item, "Cycle");

        //if pidi is null set it to default 2
        if(pidi == NULL)
        {
            pidi = cJSON_CreateNumber(2);
        }

        if (cJSON_IsNumber(can_id) && cJSON_IsString(name) && cJSON_IsNumber(pid) && cJSON_IsNumber(pidi) &&
            cJSON_IsNumber(start_bit) && cJSON_IsNumber(bit_length) && cJSON_IsString(expression) && cJSON_IsNumber(cycle)) 
        {
            mqtt_canflt_values[i].can_id = (uint32_t)can_id->valuedouble;
            strncpy(mqtt_canflt_values[i].name, name->valuestring, sizeof(mqtt_canflt_values[i].name));
            mqtt_canflt_values[i].pid = (int32_t)pid->valuedouble;  // Set 'pid' field
            mqtt_canflt_values[i].pidi = (int32_t)pidi->valuedouble;
            mqtt_canflt_values[i].start_bit = (uint32_t)start_bit->valuedouble;
            mqtt_canflt_values[i].bit_length = (uint32_t)bit_length->valuedouble;
            strncpy(mqtt_canflt_values[i].expression, expression->valuestring, sizeof(mqtt_canflt_values[i].expression));
            mqtt_canflt_values[i].cycle = (uint32_t)cycle->valuedouble;
            mqtt_canflt_values[i].logtime = 0;

            ESP_LOGI(TAG, "Loaded CAN Filter %lu: CAN ID=%lu, PID=%ld, PIDIndex=%ld, Name=%s, Start Bit=%lu, Bit Length=%lu, Expression=%s, Cycle=%lu",
                     i, mqtt_canflt_values[i].can_id, mqtt_canflt_values[i].pid, mqtt_canflt_values[i].pidi, mqtt_canflt_values[i].name,
                     mqtt_canflt_values[i].start_bit, mqtt_canflt_values[i].bit_length,
                     mqtt_canflt_values[i].expression, mqtt_canflt_values[i].cycle);
        }
        else 
        {
            free(mqtt_canflt_values);
            cJSON_Delete(root);
            ESP_LOGE(TAG, "cJSON_IsNumber(can_id)");
            mqtt_canflt_size = 0;
            return;
        }
    }

    cJSON_Delete(root);
}

void mqtt_publish(const char *topic, const char *data, int len, int qos, int retain)
{
    int data_len = len;

    if( (config_server_mqtt_en_config() == 1) && mqtt_connected() && (xSemaphoreTake( xmqtt_semaphore, pdMS_TO_TICKS(2000) ) == pdTRUE) )
    {
        if(len == 0)
        {
            data_len = strlen(data);
        }

        if(data_len < MQTT_TX_RX_BUF_SIZE)
        {
            esp_mqtt_client_publish(client, topic, data, len, qos, retain);
        }
        else
        {
            static const char* error_msg = "{\"error\": \"Data length exceeds the data size\"}";
            esp_mqtt_client_publish(client, topic, error_msg, strlen(error_msg), qos, 0);
        }
        
        xSemaphoreGive( xmqtt_semaphore );
    }
}

void mqtt_init(char* id, uint8_t connected_led, QueueHandle_t *xtx_queue)
{
    dev_status_clear_bits(DEV_MQTT_CONNECTED_BIT);
    xmqtt_semaphore = xSemaphoreCreateMutex();
    xmqtt_tx_queue = xtx_queue;
    mqtt_led = connected_led;
    device_id = id;

    mqtt_sub_topic = heap_caps_malloc(128, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    mqtt_status_topic = heap_caps_malloc(128, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    mqtt_cmd_topic = heap_caps_malloc(128, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    mqtt_rsp_topic = heap_caps_malloc(128, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    mqtt_battery_topic = heap_caps_malloc(128, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    
    // Check if allocation was successful
    if (!mqtt_sub_topic || !mqtt_status_topic || !mqtt_cmd_topic || 
        !mqtt_rsp_topic || !mqtt_battery_topic) {
        ESP_LOGE(TAG, "Failed to allocate MQTT topic buffers in PSRAM");
        return;
    }
    
    // Initialize buffers to empty strings
    mqtt_sub_topic[0] = '\0';
    mqtt_status_topic[0] = '\0';
    mqtt_cmd_topic[0] = '\0';
    mqtt_rsp_topic[0] = '\0';
    mqtt_battery_topic[0] = '\0';

    // Initialize topic strings BEFORE using them in MQTT config
    strcpy(mqtt_sub_topic, config_server_get_mqtt_tx_topic());
    strcpy(mqtt_status_topic, config_server_get_mqtt_status_topic());
    esp_mqtt_client_config_t mqtt_cfg;

    memset(&mqtt_cfg, 0, sizeof(mqtt_cfg));

    // mqtt_cfg.session.protocol_ver = MQTT_PROTOCOL_V_5;
    mqtt_cfg.broker.address.uri = config_server_get_mqtt_url();
    mqtt_cfg.broker.address.port = config_server_get_mqtt_port();

    mqtt_cfg.credentials.username = config_server_get_mqtt_user();
    mqtt_cfg.credentials.authentication.password = config_server_get_mmqtt_pass();

    mqtt_cfg.network.disable_auto_reconnect = false;
    mqtt_cfg.network.reconnect_timeout_ms = 5000;

    mqtt_cfg.session.keepalive = 30;
    mqtt_cfg.session.last_will.topic = mqtt_status_topic;
    mqtt_cfg.session.last_will.retain = 1;
    mqtt_cfg.session.last_will.msg = "{\"status\": \"offline\"}";

    mqtt_cfg.buffer.size = MQTT_TX_RX_BUF_SIZE;
    mqtt_cfg.buffer.out_size = MQTT_OUT_BUF_SIZE;

    // Enable TLS (MQTTS) if configured
    if (config_server_get_mqtt_security_enabled()) {
        ESP_LOGI(TAG, "MQTTS enabled, configuring TLS settings");
        const char *orig_uri = config_server_get_mqtt_url();
        if (orig_uri) {
            // Ensure URI uses mqtts:// scheme
            const char *prefix = "mqtt://";
            size_t pre_len = strlen(prefix);
            size_t orig_len = strlen(orig_uri);
            if (orig_len >= pre_len && strncasecmp(orig_uri, prefix, pre_len) == 0) {
                // Build mqtts://<rest>
                size_t rest_len = orig_len - pre_len;
                size_t new_len = 8 /*mqtts://*/ + rest_len + 1;
                if (g_mqtt_uri_override) {
                    heap_caps_free(g_mqtt_uri_override);
                    g_mqtt_uri_override = NULL;
                }
                g_mqtt_uri_override = heap_caps_malloc(new_len, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
                if (g_mqtt_uri_override) {
                    strcpy(g_mqtt_uri_override, "mqtts://");
                    strlcpy(g_mqtt_uri_override + 8, orig_uri + pre_len, new_len - 8);
                    mqtt_cfg.broker.address.uri = g_mqtt_uri_override;
                } else {
                    ESP_LOGE(TAG, "Failed to alloc mqtts URI override");
                }
            } else if (orig_len >= 8 && strncasecmp(orig_uri, "mqtts://", 8) == 0) {
                // Already mqtts
                mqtt_cfg.broker.address.uri = orig_uri;
            } else {
                // Unknown scheme, leave as-is
                mqtt_cfg.broker.address.uri = orig_uri;
            }
        }
        ESP_LOGI(TAG, "MQTT Broker URI: %s", mqtt_cfg.broker.address.uri);
        // Certificate selection via Certificate Manager
        const char *cert_set = config_server_get_mqtt_cert_set();
        if (cert_set && strcmp(cert_set, "default") != 0) {
            size_t ca_len = 0, cli_len = 0, key_len = 0;
            const char *ca  = cert_manager_get_set_ca_ptr(cert_set, &ca_len);
            const char *cli = cert_manager_get_set_client_cert_ptr(cert_set, &cli_len);
            const char *key = cert_manager_get_set_client_key_ptr(cert_set, &key_len);

            // ESP_LOG_BUFFER_HEXDUMP(TAG, ca, strlen(ca), ESP_LOG_WARN);
            // ESP_LOG_BUFFER_HEXDUMP(TAG, cli, strlen(cli), ESP_LOG_WARN);
            // ESP_LOG_BUFFER_HEXDUMP(TAG, key, strlen(key), ESP_LOG_WARN);

            ESP_LOGI(TAG, "ca str len, ca_len: %d, %d", strlen(ca), ca_len);
            ESP_LOGI(TAG, "cli str len, cli_len: %d, %d", strlen(cli), cli_len);
            ESP_LOGI(TAG, "key str len, key_len: %d, %d", strlen(key), key_len);
            
            if (ca) {
                mqtt_cfg.broker.verification.certificate = ca;
                mqtt_cfg.broker.verification.certificate_len = ca_len;
                ESP_LOGI(TAG, "MQTTS using CA from cert set '%s'", cert_set);
            } else {
                // Fallback to certificate bundle
                mqtt_cfg.broker.verification.crt_bundle_attach = esp_crt_bundle_attach;
                ESP_LOGW(TAG, "CA missing in set '%s', using bundle", cert_set);
            }

            // Optional mTLS
            if (cli && key) {
                mqtt_cfg.credentials.authentication.certificate = cli;
                mqtt_cfg.credentials.authentication.certificate_len = cli_len;
                mqtt_cfg.credentials.authentication.key = key;
                mqtt_cfg.credentials.authentication.key_len = key_len;
                ESP_LOGI(TAG, "MQTTS using client cert+key from cert set '%s'", cert_set);
            }
        } else {
            // Default trust: use built-in certificate bundle
            mqtt_cfg.broker.verification.crt_bundle_attach = esp_crt_bundle_attach;
            ESP_LOGI(TAG, "MQTTS using built-in certificate bundle (default)");
        }
        // skip_cert_common_name_check (user-configurable)
        mqtt_cfg.broker.verification.skip_cert_common_name_check = config_server_get_mqtt_skip_cn_check();
    }

    // uint32_t keep_alive = 0;
    // if(config_server_get_keep_alive(&keep_alive) != -1)
    // {
    //     mqtt_cfg.session.keepalive = keep_alive;
    // }
    // else
    // {
    //     mqtt_cfg.session.keepalive = 30;
    // }
    // ESP_LOGI(TAG, "MQTT Keep Alive: %d", mqtt_cfg.session.keepalive);
    sprintf(mqtt_cmd_topic, "wican/%s/cmd",device_id);
    sprintf(mqtt_rsp_topic, "wican/%s/rsp",device_id);
    sprintf(mqtt_battery_topic, "wican/%s/battery",device_id);
    ESP_LOGI(TAG, "device_id: %s, mqtt_cfg.uri: %s", device_id, mqtt_cfg.broker.address.uri);
    mqtt_elm327_log = config_server_mqtt_elm327_log();
	mqtt_load_filter();
    s_mqtt_event_group = xEventGroupCreateStatic(&mqtt_event_group_buffer);
    client = esp_mqtt_client_init(&mqtt_cfg);
    if (client == NULL) {
        ESP_LOGE(TAG, "esp_mqtt_client_init failed (client == NULL)");
        return;
    }

    static StackType_t *mqtt_task_stack;
    static StaticTask_t mqtt_task_buffer;
    static const uint32_t mqtt_task_stack_size = 1024*15;
    mqtt_task_stack = heap_caps_malloc(mqtt_task_stack_size * sizeof(StackType_t), MALLOC_CAP_SPIRAM|MALLOC_CAP_8BIT);

    if (mqtt_task_stack == NULL)
    {
        ESP_LOGE(TAG, "Failed to allocate MQTT task stack memory");
        return;
    }
    
    // Create static task
    TaskHandle_t mqtt_task_handle = xTaskCreateStatic(
        mqtt_task,
        "mqtt_task",
        mqtt_task_stack_size,
        (void*)AF_INET,
        5,
        mqtt_task_stack,
        &mqtt_task_buffer
    );
    
    if (mqtt_task_handle == NULL)
    {
        ESP_LOGE(TAG, "Failed to create MQTT task");
        heap_caps_free(mqtt_task_stack);
        return;
    }
}

