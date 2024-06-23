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
#include "expression_parser.h"

#define  TAG            __func__

static void autopid_task(void *pvParameters)
{
    static uint8_t buf_sp[] = "atsp6\r\nath1\r\nati\r\nati\r\nats1\r\n";
    static uint8_t buf_sh[] = "ath1\r\n";
    static uint8_t buf_i[] = "ati\r\n";
    static uint8_t buf[] = "0902\r\n";
    static uint8_t buf1[] = "010C\r\n";
    static uint8_t buf2[] = "2335\r\n";
    twai_message_t tx_msg;
    static QueueHandle_t autopid_Queue;

    vTaskDelay(pdMS_TO_TICKS(15000));
    elm327_process_cmd(buf_i, strlen((char*)buf_i), &tx_msg, &autopid_Queue);
    vTaskDelay(pdMS_TO_TICKS(1000));
    elm327_process_cmd(buf_sp, strlen((char*)buf_sp), &tx_msg, &autopid_Queue);
    vTaskDelay(pdMS_TO_TICKS(1000));
    // elm327_process_cmd(buf_sh, strlen((char*)buf_sh), &tx_msg, &autopid_Queue);
    // vTaskDelay(pdMS_TO_TICKS(1000));
	while(1)
	{
        elm327_process_cmd(buf, strlen((char*)buf), &tx_msg, &autopid_Queue);
        vTaskDelay(pdMS_TO_TICKS(2000));
        elm327_process_cmd(buf1, strlen((char*)buf1), &tx_msg, &autopid_Queue);
        vTaskDelay(pdMS_TO_TICKS(2000));
        elm327_process_cmd(buf2, strlen((char*)buf2), &tx_msg, &autopid_Queue);
        vTaskDelay(pdMS_TO_TICKS(2000));
    }
}

#include <stdio.h>
#include <string.h>
#include <ctype.h>

#define BUFFER_SIZE 256

typedef enum {
    WAITING_FOR_START = 0,
    READING_LINES
} autopid_state_t;
static uint32_t auto_pid_header;
static uint8_t  auto_pid_buf[BUFFER_SIZE];
static uint8_t auto_pid_data_len = 0;
static int buf_index = 0;
static autopid_state_t state = WAITING_FOR_START;

void append_data(const char* token) {
    if (isxdigit((int)token[0]) && isxdigit((int)token[1]) && strlen((int)token) == 2) {
        if (buf_index < BUFFER_SIZE - 3) { // Ensure room for 2 hex chars and a space/null
            memcpy(&auto_pid_buf[buf_index], token, 2);
            buf_index += 2;
            auto_pid_buf[buf_index++] = ' '; // Space for readability
        }
    }
}

void strip_line_endings(char* buffer) {
    char* pos;
    if ((pos = strchr(buffer, '\r')) != NULL) *pos = '\0';
    if ((pos = strchr(buffer, '\n')) != NULL) *pos = '\0';
}
void autopid_parse_rsp(char* str) 
{
    if(strchr(str, '>') == NULL)
    {
        strip_line_endings(str);
    }
    // else
    // {
    //     state = READING_LINES;
    // }

    switch(state)
    {
        case WAITING_FOR_START:
        {
            int8_t count = sscanf(str, "%lX %X %X %X %X %X %X %X %X", &auto_pid_header, (unsigned int *)&auto_pid_buf[0], (unsigned int *)&auto_pid_buf[1], (unsigned int *)&auto_pid_buf[2],
                                (unsigned int *)&auto_pid_buf[3], (unsigned int *)&auto_pid_buf[4], (unsigned int *)&auto_pid_buf[5],
                                (unsigned int *)&auto_pid_buf[6], (unsigned int *)&auto_pid_buf[7]);
            if(count >= 2 && count <= 9)
            {
                if(auto_pid_buf[0] == 0x10) //indicates multi frame response 
                {
                    buf_index = count-1;
                    auto_pid_data_len = auto_pid_buf[1];
                }
                else
                {
                    buf_index = count-1;
                }
                state = READING_LINES;
            }
            else
            {
                buf_index = 0;
            }
            ESP_LOGI(TAG, "count: %u", count);
            break;
        }
        case READING_LINES:
        {
            if(strchr(str, '>') != NULL)
            {
                state = WAITING_FOR_START;
                if(buf_index != 0)
                {
                    ESP_LOGI(TAG, "buf_index: %u", buf_index);
                    ESP_LOG_BUFFER_HEXDUMP(TAG, auto_pid_buf, buf_index, ESP_LOG_INFO);
                    // bool evaluate_expression(uint8_t *expression,  uint8_t *data, double V, double *result);
                    static uint8_t exp[] = "B10-B17";
                    static double value = 0;
                    static double expression_result = 0;
                    if(auto_pid_buf[2] == 0x49 && auto_pid_buf[3] == 2)
                    {
                        if(evaluate_expression(exp, auto_pid_buf, value, &expression_result))
                        {
                            ESP_LOGW(TAG, "Expression result: %lf", expression_result);
                        }
                        else
                        {
                            ESP_LOGE(TAG, "Error evaluate_expression");
                        }
                    }
                }
                break;
            }
            else
            {
                int8_t count = sscanf(str, "%lX %X %X %X %X %X %X %X %X", &auto_pid_header, (unsigned int *)&auto_pid_buf[buf_index], (unsigned int *)&auto_pid_buf[buf_index+1], (unsigned int *)&auto_pid_buf[buf_index+2],
                                (unsigned int *)&auto_pid_buf[buf_index+3], (unsigned int *)&auto_pid_buf[buf_index+4], (unsigned int *)&auto_pid_buf[buf_index+5],
                                (unsigned int *)&auto_pid_buf[buf_index+6], (unsigned int *)&auto_pid_buf[buf_index+7]);
                if(count == 9)
                {
                    buf_index+=8;
                }
                else
                {
                    state = WAITING_FOR_START;
                }
                break;
            }
            
        }
    }
}
// void autopid_parse_rsp(char* str) {
//     static char* current_pos;
//     static char buffer[BUFFER_SIZE];
//     current_pos = str;
//     strncpy(buffer, str, BUFFER_SIZE - 1);
//     buffer[BUFFER_SIZE - 1] = '\0';
//     if(strchr(str, '>') == NULL)
//     {
//         strip_line_endings(buffer);
//     }
//     printf("Processing: %s\n", buffer);
//     printf("Current state: %d\n", state);

//     // if (buffer[0] == '>') 
//     if(strchr(str, '>') != NULL)
//     {
//         state = DATA_READY;
//     } else {
//         if (state == WAITING_FOR_START) {
//             state = READING_LINES;
//         }

//         // Skip the '7E8' address and continuation byte if present
//         current_pos = buffer;
//         for (int i = 0; i < 2; i++) {
//             current_pos = strchr(current_pos, ' ');
//             if (current_pos) current_pos++;
//         }

//         while (current_pos) {
//             char* next_space = strchr(current_pos, ' ');
//             if (next_space) {
//                 *next_space = '\0'; // Terminate the current token
//             }
//             append_data(current_pos);
//             if (!next_space) break;
//             current_pos = next_space + 1;
//         }
//     }

//     if (state == DATA_READY) {
//         if (buf_index > 0) auto_pid_buf[buf_index - 1] = '\0'; // Remove last space
//         printf("Received Data: %s\n", auto_pid_buf);
//         memset(auto_pid_buf, 0, sizeof(auto_pid_buf));
//         buf_index = 0;
//         current_pos = 0;
//         buffer[0] = 0;
//         state = WAITING_FOR_START;
//     }
// }


void autopid_mqtt_pub(char* str, uint32_t len, QueueHandle_t *q)
{
    // static autopid_state_t state = WAITING_FOR_START;
    static uint8_t auto_pid_buf[128];
    static uint8_t expected_lines;
    uint8_t expected_bytes = 0;
    static uint8_t received_bytes;
    char* space = strchr(str, ' '); 

	if(strlen(str) != 0)
	{
        // ESP_LOG_BUFFER_HEXDUMP(TAG, str, strlen(str), ESP_LOG_INFO);
        ESP_LOGI(__func__, "%s", str);
        autopid_parse_rsp(str);
        //if byte after the first space is 0x10 then its a multi line response otherwise, this is the only line
        // if its a multi line response then the byte after 0x10 is the total number of bytes, 
        // wait for '>' to indicate the response ended, this line will contain 6 bytes and the rest are in the other lines, each line after that will contain 7 bytes 
        // switch(state)
        // {
        //     case WAITING_FOR_START:
        //     {
        //         if (*(space + 1) == '1' && *(space + 2) == '0') 
        //         {
        //             expected_bytes = hexToInt(space + 3);
        //             if (expected_bytes == 0) {
        //                 printf("Invalid hexadecimal value.\n");
        //                 return;
        //             }
        //             received_bytes = 0; // Reset received_bytes for each new message start
        //             state = READING_LINES;
        //             int header_size = space - str + 9; // Calculate actual header size
        //             int initial_data_length = strlen(str) - header_size;
        //             strncpy(auto_pid_buf, str + header_size, initial_data_length);
        //             received_bytes += initial_data_length;
        //         }
        //     }


        // }



	}
	else
	{

        
	}
}

void autopid_init(void)
{
    xTaskCreate(autopid_task, "autopid_task", 1024*5, (void*)AF_INET, 5, NULL);
}