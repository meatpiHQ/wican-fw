#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include  "freertos/queue.h"
#include "freertos/event_groups.h"
#include "esp_wifi.h"
#include "esp_system.h"
#include "esp_event.h"
#include "nvs_flash.h"
#include "driver/gpio.h"
#include "driver/twai.h"
#include "esp_log.h"
#include <string.h>
#include "esp_log.h"
#include "mqtt_client.h"
#include "ver.h"
#include "cJSON.h"
#include "wifi_network.h"
#include "mqtt.h"
#include <stdbool.h>
#include <ctype.h>
#include "esp_timer.h"
#include "lwip/sockets.h"
#include "elm327.h"

static const char* TAG = "autopid";

static void autopid_task(void *pvParameters)
{
    static uint8_t buf_sp[] = "atsp6\r";
    static uint8_t buf[] = "0902\r";
    twai_message_t tx_msg;
    static QueueHandle_t autopid_Queue;

    vTaskDelay(pdMS_TO_TICKS(1000));
    elm327_process_cmd(buf_sp, strlen((char*)buf_sp), &tx_msg, &autopid_Queue);
	while(1)
	{
        elm327_process_cmd(buf, strlen((char*)buf), &tx_msg, &autopid_Queue);
        vTaskDelay(pdMS_TO_TICKS(5000));
    }
}

void autopid_mqtt_pub(char* str, uint32_t len, QueueHandle_t *q)
{
	// static xdev_buffer xsend_buffer;

	if(len == 0)
	{
		// xsend_buffer.usLen = strlen(str);
        ESP_LOGI(TAG, "\r\n%s", str);
	}
	else
	{
		// xsend_buffer.usLen = len;
	}
	// memcpy(xsend_buffer.ucElement, str, xsend_buffer.usLen);

    // ESP_LOGI(TAG, )
}

void autopid_init(void)
{
    xTaskCreate(autopid_task, "autopid_task", 1024*5, (void*)AF_INET, 5, NULL);
}