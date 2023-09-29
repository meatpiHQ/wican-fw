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

#define TAG 		__func__

static EventGroupHandle_t s_mqtt_event_group = NULL;
#define MQTT_CONNECTED_BIT 			BIT0
#define PUB_SUCCESS_BIT     		BIT1
static esp_mqtt_client_handle_t client = NULL;
static char *device_id;
static char mqtt_sub_topic[128];
static char mqtt_status_topic[128];
static uint8_t mqtt_led = 0;

static QueueHandle_t *xmqtt_tx_queue;

typedef struct 
{
    uint32_t can_id;
    char name[16];
	int32_t pid;
    uint32_t start_bit;
    uint32_t bit_length;
    char expression[32];
    uint32_t cycle;
	int64_t logtime;
} CANFilter;

static CANFilter *mqtt_canflt_values = NULL;
static uint32_t mqtt_canflt_size = 0;



#define MAX_STACK_SIZE 100

typedef struct 
{
    double items[MAX_STACK_SIZE];
    int top;
} Stack;

static void initialize(Stack *stack) 
{
    stack->top = -1;
}

static bool isEmpty(Stack *stack) 
{
    return stack->top == -1;
}

static void push(Stack *stack, double item) 
{
    if (stack->top == MAX_STACK_SIZE - 1) 
	{
        ESP_LOGE(TAG, "Stack overflow\n");
    } 
	else 
	{
        stack->items[++stack->top] = item;
    }
}

// Function to pop a double value from the stack
static double pop(Stack *stack) 
{
    if (isEmpty(stack)) 
	{
        ESP_LOGE(TAG, "Stack underflow\n");
        return 0.0; // Return a default value
    } 
	else 
	{
        return stack->items[stack->top--];
    }
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
static bool evaluate_expression(uint8_t *expression, double V, double *result) 
{
    Stack operandStack;
    Stack operatorStack;
    initialize(&operandStack);
    initialize(&operatorStack);

    uint8_t i = 0;
    while (expression[i] != '\0') 
	{
        if (isspace(expression[i])) 
		{
            i++;
            continue; // Skip whitespace
        }
        if (isdigit(expression[i]) || (expression[i] == '.' && isdigit(expression[i + 1]))) 
		{
            char numBuffer[20];
            int j = 0;
            while (isdigit(expression[i]) || expression[i] == '.') 
			{
                numBuffer[j++] = expression[i++];
            }
            numBuffer[j] = '\0';
            double num = atof(numBuffer);
            push(&operandStack, num);
        } 
		else if (expression[i] == 'V') 
		{
            push(&operandStack, V); // Substitute the value of V
            i++;
        } 
		else if (expression[i] == '(') 
		{
            push(&operatorStack, expression[i]);
            i++;
        } 
		else if (expression[i] == ')') 
		{
            while (!isEmpty(&operatorStack) && operatorStack.items[operatorStack.top] != '(') 
			{
                char operator = pop(&operatorStack);
                double operand2 = pop(&operandStack);
                double operand1 = pop(&operandStack);

                if (operator == '+') 
				{
                    push(&operandStack, operand1 + operand2);
                } 
				else if (operator == '-') 
				{
                    push(&operandStack, operand1 - operand2);
                } 
				else if (operator == '*') 
				{
                    push(&operandStack, operand1 * operand2);
                } 
				else if (operator == '/') 
				{
                    if (operand2 == 0) 
					{
                        ESP_LOGE(TAG, "Division by zero\n");
                        return false; // Return failure
                    }
                    push(&operandStack, operand1 / operand2);
                }
            }
            // Pop '(' from the operator stack
            if (!isEmpty(&operatorStack) && operatorStack.items[operatorStack.top] == '(') 
			{
                pop(&operatorStack);
            } 
			else 
			{
                ESP_LOGE(TAG, "Mismatched parentheses\n");
                return false; // Return failure
            }
            i++;
        } 
		else if (expression[i] == '+' || expression[i] == '-' || expression[i] == '*' || expression[i] == '/') 
		{
            while (!isEmpty(&operatorStack) &&
                   (operatorStack.items[operatorStack.top] == '*' || operatorStack.items[operatorStack.top] == '/') &&
                   (expression[i] == '+' || expression[i] == '-')) 
				   {
                char operator = pop(&operatorStack);
                double operand2 = pop(&operandStack);
                double operand1 = pop(&operandStack);

                if (operator == '+') 
				{
                    push(&operandStack, operand1 + operand2);
                } 
				else if (operator == '-') 
				{
                    push(&operandStack, operand1 - operand2);
                } 
				else if (operator == '*') 
				{
                    push(&operandStack, operand1 * operand2);
                } 
				else if (operator == '/') 
				{
                    if (operand2 == 0) 
					{
                        ESP_LOGE(TAG, "Division by zero\n");
                        return false; // Return failure
                    }
                    push(&operandStack, operand1 / operand2);
                }
            }
            push(&operatorStack, expression[i]);
            i++;
        } 
		else 
		{
            ESP_LOGE(TAG, "Invalid character");
            return false; // Return failure
        }
    }

    // Check for remaining '(' in operator stack
    while (!isEmpty(&operatorStack)) 
	{
        char operator = pop(&operatorStack);
        if (operator == '(') 
		{
            ESP_LOGE(TAG, "Mismatched parentheses\n");
            return false; // Return failure
        }
        double operand2 = pop(&operandStack);
        double operand1 = pop(&operandStack);

        if (operator == '+') 
		{
            push(&operandStack, operand1 + operand2);
        } 
		else if (operator == '-') 
		{
            push(&operandStack, operand1 - operand2);
        } 
		else if (operator == '*') 
		{
            push(&operandStack, operand1 * operand2);
        } 
		else if (operator == '/') 
		{
            if (operand2 == 0) 
			{
                ESP_LOGE(TAG, "Division by zero\n");
                return false; // Return failure
            }
            push(&operandStack, operand1 / operand2);
        }
    }

    if (isEmpty(&operandStack) || operandStack.top != 0) 
	{
        ESP_LOGE(TAG, "Invalid expression\n");
        return false; // Return failure
    }

    *result = operandStack.items[0];
    return true; // Return success
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
			xEventGroupSetBits(s_mqtt_event_group, MQTT_CONNECTED_BIT);

			esp_mqtt_client_subscribe(client, mqtt_sub_topic, 0);
			gpio_set_level(mqtt_led, 0);
			esp_mqtt_client_publish(client, mqtt_status_topic, "{\"status\": \"online\"}", 0, 0, 0);
			break;
		case MQTT_EVENT_DISCONNECTED:
			ESP_LOGI(TAG, "MQTT_EVENT_DISCONNECTED");
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

    if (strncmp(event->topic, mqtt_sub_topic, strlen(mqtt_sub_topic)) == 0)
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
            can_enable();
            can_send(&can_frame, 1);
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

#define JSON_BUF_SIZE		2048
static void mqtt_task(void *pvParameters)
{
	static xdev_buffer tx_buffer;
	static char json_buffer[JSON_BUF_SIZE] = {0};
	static char tmp[150];
	twai_message_t tx_frame;
	static char mqtt_topic[128];
	static uint64_t can_data = 0;

	sprintf(mqtt_topic, "wican/%s/can/rx", device_id);

	while(!wifi_network_is_connected())
	{
		vTaskDelay(pdMS_TO_TICKS(1000));
	}

	esp_mqtt_client_register_event(client, ESP_EVENT_ANY_ID, mqtt_event_handler, NULL);
	esp_mqtt_client_register_event(client, MQTT_EVENT_DATA, mqtt_parse_data, NULL);
	esp_mqtt_client_start(client);

	while(1)
	{
		xQueuePeek(*xmqtt_tx_queue, ( void * ) &tx_frame, portMAX_DELAY);
		if(mqtt_connected())
		{
			json_buffer[0] = 0;

			if(mqtt_canflt_size != 0)
			{
				uint8_t start_index = 0;
				int8_t found_index = -1;
				static uint64_t value = 0;
				static double expression_result = 0;
				
				xQueueReceive(*xmqtt_tx_queue, ( void * ) &tx_buffer, 0);

				while ((found_index = mqtt_canflt_find_id(tx_frame.identifier, start_index)) != -1)
				{
					

					// Check if expecting PID
					if(mqtt_canflt_values[found_index].pid != -1)
					{
						if(mqtt_canflt_values[found_index].pid != tx_frame.data[2])
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
						can_data = (can_data << 8) | tx_frame.data[i];
					}

					uint64_t start_bit = 64 - mqtt_canflt_values[found_index].start_bit - mqtt_canflt_values[found_index].bit_length;
					uint64_t bit_length = mqtt_canflt_values[found_index].bit_length;

					uint64_t mask = ((1ULL << bit_length) - 1ULL) << start_bit;

					value = (can_data & mask) >> start_bit;



					ESP_LOGI(TAG, "-----------");
					ESP_LOGI(TAG, "can_data: %llx, mask: %llx, value: %llx\r\n", can_data, mask, value);

					if(evaluate_expression((uint8_t *)mqtt_canflt_values[found_index].expression, (double)value, &expression_result) )
					{
						ESP_LOGI(TAG, "Expression result: %lf", expression_result);

						sprintf(json_buffer, "{\"%s\": %lf}", mqtt_canflt_values[found_index].name, expression_result);

						esp_mqtt_client_publish(client, mqtt_topic, json_buffer, 0, 0, 0);
					}
					else
					{
						ESP_LOGE(TAG, "evaluate_expression error");
					}

					start_index = found_index + 1;
				}
			}
			else
			{
				sprintf(json_buffer, "{\"bus\":\"0\",\"type\":\"rx\",\"ts\":%lu,\"frame\":[", (pdTICKS_TO_MS(xTaskGetTickCount())%60000));

				if(strlen(json_buffer) < (sizeof(json_buffer) -128))
				{
					while(xQueuePeek(*xmqtt_tx_queue, ( void * ) &tx_buffer, 0) == pdTRUE)
					{
						xQueueReceive(*xmqtt_tx_queue, ( void * ) &tx_buffer, 0);
						sprintf(tmp, "{\"id\":%lu,\"dlc\":%u,\"rtr\":%s,\"extd\":%s,\"data\":[%u,%u,%u,%u,%u,%u,%u,%u]},",tx_frame.identifier, tx_frame.data_length_code, tx_frame.rtr?"true":"false",
																													tx_frame.extd?"true":"false",tx_frame.data[0], tx_frame.data[1], tx_frame.data[2], tx_frame.data[3],
																													tx_frame.data[4], tx_frame.data[5], tx_frame.data[6], tx_frame.data[7]);
						strcat((char*)json_buffer, (char*)tmp);

						if(strlen(json_buffer) > (JSON_BUF_SIZE - 100))
						{
							break;
						}
					}
					json_buffer[strlen(json_buffer)-1] = 0;
				}
				strcat((char*)json_buffer, "]}");

				esp_mqtt_client_publish(client, mqtt_topic, json_buffer, 0, 0, 0);
			}

		}
		else
		{
			xQueueReceive(*xmqtt_tx_queue, ( void * ) &tx_buffer, 0);
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
    mqtt_canflt_values = (CANFilter *)malloc(mqtt_canflt_size * sizeof(CANFilter));

    if (mqtt_canflt_values == NULL) 
    {
        cJSON_Delete(root);
        ESP_LOGE(TAG, "(mqtt_canflt_values == NULL)");
        return;
    }

    for (uint32_t i = 0; i < mqtt_canflt_size; i++) 
    {
        cJSON *item = cJSON_GetArrayItem(can_flt, i);
        if (!cJSON_IsObject(item)) 
        {
            free(mqtt_canflt_values);
            cJSON_Delete(root);
            ESP_LOGE(TAG, "(uint32_t i = 0; i < mqtt_canflt_size; i++)");
            return;
        }

        // Extract values from the JSON object and populate the CANFilter structure
        cJSON *can_id = cJSON_GetObjectItem(item, "CANID");
        cJSON *name = cJSON_GetObjectItem(item, "Name");
        cJSON *pid = cJSON_GetObjectItem(item, "PID");  // Added 'PID' field
        cJSON *start_bit = cJSON_GetObjectItem(item, "StartBit");
        cJSON *bit_length = cJSON_GetObjectItem(item, "BitLength");
        cJSON *expression = cJSON_GetObjectItem(item, "Expression");
        cJSON *cycle = cJSON_GetObjectItem(item, "Cycle");

        if (cJSON_IsNumber(can_id) && cJSON_IsString(name) && cJSON_IsNumber(pid) &&
            cJSON_IsNumber(start_bit) && cJSON_IsNumber(bit_length) && cJSON_IsString(expression) && cJSON_IsNumber(cycle)) 
        {
            mqtt_canflt_values[i].can_id = (uint32_t)can_id->valuedouble;
            strncpy(mqtt_canflt_values[i].name, name->valuestring, sizeof(mqtt_canflt_values[i].name));
            mqtt_canflt_values[i].pid = (int32_t)pid->valuedouble;  // Set 'pid' field
            mqtt_canflt_values[i].start_bit = (uint32_t)start_bit->valuedouble;
            mqtt_canflt_values[i].bit_length = (uint32_t)bit_length->valuedouble;
            strncpy(mqtt_canflt_values[i].expression, expression->valuestring, sizeof(mqtt_canflt_values[i].expression));
            mqtt_canflt_values[i].cycle = (uint32_t)cycle->valuedouble;
            mqtt_canflt_values[i].logtime = 0;

            ESP_LOGI(TAG, "Loaded CAN Filter %lu: CAN ID=%lu, PID=%ld, Name=%s, Start Bit=%lu, Bit Length=%lu, Expression=%s, Cycle=%lu",
                     i, mqtt_canflt_values[i].can_id, mqtt_canflt_values[i].pid, mqtt_canflt_values[i].name,
                     mqtt_canflt_values[i].start_bit, mqtt_canflt_values[i].bit_length,
                     mqtt_canflt_values[i].expression, mqtt_canflt_values[i].cycle);
        }
        else 
        {
            free(mqtt_canflt_values);
            cJSON_Delete(root);
            ESP_LOGE(TAG, "cJSON_IsNumber(can_id)");
            return;
        }
    }

    cJSON_Delete(root);
}

void mqtt_init(char* id, uint8_t connected_led, QueueHandle_t *xtx_queue)
{
    esp_mqtt_client_config_t mqtt_cfg = {
		// .session.protocol_ver = MQTT_PROTOCOL_V_5,
		.broker.address.uri = config_server_get_mqtt_url(),
		.broker.address.port = config_server_get_mqtt_port(),
		.credentials.username = config_server_get_mqtt_user(),
		.credentials.authentication.password = config_server_get_mmqtt_pass(),
		.network.disable_auto_reconnect = false,
		.session.keepalive = 30,
		.session.last_will.topic = mqtt_status_topic,
		.session.last_will.msg = "{\"status\": \"offline\"}",
		.network.reconnect_timeout_ms = 5000,
		.buffer.size = 1024*5,
		.buffer.out_size = 1024*5,
    };
    xmqtt_tx_queue = xtx_queue;
    mqtt_led = connected_led;
    device_id = id;
    sprintf(mqtt_sub_topic, "wican/%s/can/tx", device_id);
    sprintf(mqtt_status_topic, "wican/%s/status", device_id);

    ESP_LOGI(TAG, "device_id: %s, mqtt_cfg.uri: %s", device_id, mqtt_cfg.broker.address.uri);

	mqtt_load_filter();
    s_mqtt_event_group = xEventGroupCreate();
    client = esp_mqtt_client_init(&mqtt_cfg);

    xTaskCreate(mqtt_task, "mqtt_task", 4096, (void*)AF_INET, 5, NULL);
}

