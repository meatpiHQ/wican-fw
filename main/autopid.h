#ifndef __AUTO_PID_H__
#define __AUTO_PID_H__


void autopid_mqtt_pub(char* str, uint32_t len, QueueHandle_t *q);
void autopid_init(void);
#endif
