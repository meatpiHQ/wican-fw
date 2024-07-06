#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/queue.h"
#include "esp_log.h"
#include <string.h>
#include "driver/twai.h"
#include "esp_timer.h"
#include "lwip/sockets.h"
#include "elm327.h"
#include "autopid.h"
#include "expression_parser.h"
#include "mqtt.h"
#include "cJSON.h"

#define TAG __func__
#define BUFFER_SIZE 256
#define QUEUE_SIZE 10

typedef enum
{
    WAITING_FOR_START = 0,
    READING_LINES
} autopid_state_t;

typedef struct {
    uint8_t data[BUFFER_SIZE];
    size_t length;
} response_t;


static uint32_t auto_pid_header;
static uint8_t auto_pid_buf[BUFFER_SIZE];
static uint8_t auto_pid_data_len = 0;
static int buf_index = 0;
static autopid_state_t state = WAITING_FOR_START;
static QueueHandle_t autopidQueue;
// typedef struct __attribute__((packed)){
//     char name[32];              // Name
//     char pid_init[32];          // PID init string
//     char pid_command[10];       // PID command string
//     char expression[32];        // Expression string
//     char destination[64];       // Example: file name or mqtt topic
//     int64_t timer;              // Timer for managing periodic actions
//     uint32_t period;            // Period in ms, frequency of data collection or action
//     uint8_t expression_type;    // Expression type (evaluates data from sensors, etc.)
//     uint8_t type;               // Log type, could be MQTT or file-based
// } pid_req_t ;
pid_req_t pid_req[] = {
    {"speed",     "", "010D", "B3", "mqtt_speed", 0, 2000, 0, 0},
    {"vin",     "", "0902", "[B5:B8]*0.25", "mqtt_vin", 0, 2000, 0, 0},
};

void append_data(const char *token)
{
    if (isxdigit((int)token[0]) && isxdigit((int)token[1]) && strlen(token) == 2)
    {
        if (buf_index < BUFFER_SIZE - 3) // Ensure room for 2 hex chars and a space/null
        {
            memcpy(&auto_pid_buf[buf_index], token, 2);
            buf_index += 2;
            auto_pid_buf[buf_index++] = ' '; // Space for readability
        }
    }
}

void strip_line_endings(char *buffer)
{
    char *pos;
    if ((pos = strchr(buffer, '\r')) != NULL)
        *pos = '\0';
    if ((pos = strchr(buffer, '\n')) != NULL)
        *pos = '\0';
}

void autopid_parse_rsp(char *str)
{
    response_t response;
    
    if (strchr(str, '>') == NULL)
    {
        strip_line_endings(str);
    }

    switch (state)
    {
        case WAITING_FOR_START:
        {
            if (strchr(str, '>') != NULL)
            {
                ESP_LOGI(__func__, "Found end");
                ESP_LOG_BUFFER_HEXDUMP(TAG, str, strlen(str), ESP_LOG_INFO);
                
                strncpy((char *)response.data, str, BUFFER_SIZE);
                response.length = strlen(str);
                
                if (xQueueSend(autopidQueue, &response, pdMS_TO_TICKS(1000)) != pdPASS)
                {
                    ESP_LOGE(TAG, "Failed to send to queue");
                }
            }
            int8_t count = sscanf(str, "%lX %X %X %X %X %X %X %X %X", &auto_pid_header, (unsigned int *)&auto_pid_buf[0], (unsigned int *)&auto_pid_buf[1], (unsigned int *)&auto_pid_buf[2],
                                (unsigned int *)&auto_pid_buf[3], (unsigned int *)&auto_pid_buf[4], (unsigned int *)&auto_pid_buf[5],
                                (unsigned int *)&auto_pid_buf[6], (unsigned int *)&auto_pid_buf[7]);
            if (count >= 2 && count <= 9)
            {
                if (auto_pid_buf[0] == 0x10) // Indicates multi-frame response
                {
                    buf_index = count - 1;
                    auto_pid_data_len = auto_pid_buf[1];
                }
                else
                {
                    buf_index = count - 1;
                }
                state = READING_LINES;
            }
            else
            {
                buf_index = 0;
            }
            break;
        }
        case READING_LINES:
        {
            if (strchr(str, '>') != NULL)
            {
                ESP_LOGI(__func__, "Found response end, response: %s", str);
                state = WAITING_FOR_START;
                if (buf_index != 0)
                {
                    ESP_LOG_BUFFER_HEXDUMP(TAG, auto_pid_buf, buf_index, ESP_LOG_INFO);
                    
                    memcpy(response.data, auto_pid_buf, buf_index);
                    response.length = buf_index;
                    
                    if (xQueueSend(autopidQueue, &response, pdMS_TO_TICKS(1000)) != pdPASS)
                    {
                        ESP_LOGE(TAG, "Failed to send to queue");
                    }
                }
                break;
            }
            else
            {
                int8_t count = sscanf(str, "%lX %X %X %X %X %X %X %X %X", &auto_pid_header, (unsigned int *)&auto_pid_buf[buf_index], (unsigned int *)&auto_pid_buf[buf_index + 1], (unsigned int *)&auto_pid_buf[buf_index + 2],
                                    (unsigned int *)&auto_pid_buf[buf_index + 3], (unsigned int *)&auto_pid_buf[buf_index + 4], (unsigned int *)&auto_pid_buf[buf_index + 5],
                                    (unsigned int *)&auto_pid_buf[buf_index + 6], (unsigned int *)&auto_pid_buf[buf_index + 7]);
                if (count == 9)
                {
                    buf_index += 8;
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

void autopid_mqtt_pub(char *str, uint32_t len, QueueHandle_t *q)
{
    if (strlen(str) != 0)
    {
        // ESP_LOGI(__func__, "%s", str);
        // ESP_LOG_BUFFER_HEXDUMP(TAG, str, strlen(str), ESP_LOG_INFO);
        autopid_parse_rsp(str);
    }
}

static void autopid_task(void *pvParameters)
{
    static uint8_t buf_sp[] = "atsp6\rath1\rati\rati\rats1\r";
    static uint8_t buf_i[] = "ati\r";
    twai_message_t tx_msg;
    static response_t response;
    // static char response_str[100];
    
    autopidQueue = xQueueCreate(QUEUE_SIZE, sizeof(response_t));
    if (autopidQueue == NULL)
    {
        ESP_LOGE(TAG, "Failed to create queue");
        vTaskDelete(NULL);
        return;
    }

    vTaskDelay(pdMS_TO_TICKS(15000));
    elm327_process_cmd(buf_i, strlen((char *)buf_i), &tx_msg, &autopidQueue);
    
    vTaskDelay(pdMS_TO_TICKS(1000));
    elm327_process_cmd(buf_sp, strlen((char *)buf_sp), &tx_msg, &autopidQueue);
    vTaskDelay(pdMS_TO_TICKS(1000));

    while ((xQueueReceive(autopidQueue, &response, pdMS_TO_TICKS(1000)) == pdPASS))
    {
        /* code */
    }
    
    uint32_t num_of_pid = sizeof(pid_req) / sizeof(pid_req_t);
    
    for(uint32_t i = 0; i < num_of_pid; i++)
    {
        strcat(pid_req[i].pid_command, "\r");
    }

    while (1)
    {
        for(uint32_t i = 0; i < num_of_pid; i++)
        {
            if( esp_timer_get_time() > pid_req[i].timer )
            {
                pid_req[i].timer = esp_timer_get_time() + pid_req[i].period*1000;

                elm327_process_cmd((uint8_t*)pid_req[i].pid_command , strlen(pid_req[i].pid_command), &tx_msg, &autopidQueue);
                ESP_LOGI(TAG, "Sending command: %s", pid_req[i].pid_command);
                if (xQueueReceive(autopidQueue, &response, pdMS_TO_TICKS(1000)) == pdPASS)
                {
                    double result;

                    ESP_LOGI(TAG, "Received response for: %s", pid_req[i].pid_command);
                    ESP_LOGI(TAG, "Response length: %d", response.length);
                    ESP_LOG_BUFFER_HEXDUMP(TAG, response.data, response.length, ESP_LOG_INFO);
                    if(evaluate_expression((uint8_t*)pid_req[i].expression, response.data, 0, &result))
                    {
                        cJSON *rsp_json = cJSON_CreateObject();
                        if (rsp_json == NULL) 
                        {
                            ESP_LOGI(TAG, "Failed to create cJSON object");
                            break;
                        }

                        // Add the name and result to the JSON object
                        cJSON_AddNumberToObject(rsp_json, pid_req[i].name, result);
                        
                        // Convert the cJSON object to a string
                        char *response_str = cJSON_PrintUnformatted(rsp_json);
                        if (response_str == NULL) 
                        {
                            ESP_LOGI(TAG, "Failed to print cJSON object");
                            cJSON_Delete(rsp_json); // Clean up cJSON object
                            break;
                        }

                        ESP_LOGI(TAG, "Expression result, Name: %s: %lf", pid_req[i].name, result);
                        mqtt_publish(pid_req[i].destination, response_str, 0, 0, 0);

                        // Free the JSON string and cJSON object
                        free(response_str);
                        cJSON_Delete(rsp_json);
                        vTaskDelay(pdMS_TO_TICKS(10));
                    }
                    else
                    {
                        ESP_LOGE(TAG, "Failed Expression: %s", pid_req[i].expression);
                    }
                }
                else
                {
                    ESP_LOGE(TAG, "Timeout waiting for response for: %s", pid_req[i].pid_command);
                }
            }
        }
        vTaskDelay(pdMS_TO_TICKS(2000));
    }
}

void autopid_init(void)
{
    xTaskCreate(autopid_task, "autopid_task", 1024 * 5, (void *)AF_INET, 5, NULL);
}
