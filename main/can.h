#ifndef __CAN_H__
#define __CAN_H__


#define TX_GPIO_NUM             	0
#define RX_GPIO_NUM             	3

#define CAN_5K				0
#define CAN_10K				1
#define CAN_20K				2
#define CAN_25K				3
#define CAN_50K				4
#define CAN_100K			5
#define CAN_125K			6
#define CAN_250K			7
#define CAN_500K			8
#define CAN_800K			9
#define CAN_1000K			10

typedef struct {
	uint8_t bus_state;
	uint8_t silent;
	uint8_t loopback;
	uint8_t auto_tx;
	uint16_t brp;
	uint8_t phase_seg1;
	uint8_t phase_seg2;
	uint8_t sjw;
	uint32_t filter;
	uint32_t mask;
}can_cfg_t;


void can_enable(void);
void can_disable(void);
void can_set_silent(uint8_t flag);
void can_set_loopback(uint8_t flag);
void can_set_auto_retransmit(uint8_t flag);
void can_set_filter(uint32_t f);
void can_set_mask(uint32_t m);
void can_set_bitrate(uint8_t rate);
esp_err_t can_receive(twai_message_t *message, TickType_t ticks_to_wait);
esp_err_t can_send(twai_message_t *message, TickType_t ticks_to_wait);
void can_init(void);
uint8_t can_is_silent(void);
bool can_is_enabled(void);
#endif
