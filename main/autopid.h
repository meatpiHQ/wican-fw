#ifndef __AUTO_PID_H__
#define __AUTO_PID_H__

typedef struct {
    char name[32];              // Name
    char pid_init[32];          // PID init string
    char pid_command[10];       // PID command string
    char expression[32];        // Expression string
    char destination[64];       // Example: file name or mqtt topic
    int64_t timer;              // Timer for managing periodic actions
    uint32_t period;            // Period in ms, frequency of data collection or action
    uint8_t expression_type;    // Expression type (evaluates data from sensors, etc.)
    uint8_t type;               // Log type, could be MQTT or file-based
}__attribute__((aligned(1),packed)) pid_req_t ;

void autopid_mqtt_pub(char* str, uint32_t len, QueueHandle_t *q);
void autopid_init(void);
#endif
