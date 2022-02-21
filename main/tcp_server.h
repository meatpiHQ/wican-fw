#ifndef __TCP_SERVER_H__
#define __TCP_SERVER_H__
int8_t tcp_server_init(uint32_t port, QueueHandle_t *xTXp_Queue, QueueHandle_t *xRXp_Queue, uint8_t connected_led, uint8_t udp_en);
int8_t tcp_port_open(void);
typedef struct __ucTCP_Buffer
{
	int usLen;
	uint8_t ucElement[64];
}xTCP_Buffer;

void tcp_server_suspend(void);
void tcp_server_resume(void);
#endif
