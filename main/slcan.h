
#ifndef SLCAN_h
#define SLCAN_h
#include "driver/twai.h"

// maximum rx buffer len: extended CAN frame with timestamp
#define SL_OPEN			0
#define SL_CLOSE		1
#define SL_SPEED		2
#define SL_MUTE			3
#define SL_AUTO			4
#define SL_REMOTE_STD	5
#define SL_DATA_STD		6
#define SL_REMOTE_EXT	7
#define SL_DATA_EXT		8
#define SL_VER			9
#define SL_TSTAMP		10
#define SL_ACP_CODE		11
#define SL_MASK_REG		12
#define SL_SERIAL		13
#define SL_STATUS		14
#define SL_CUS_SPEED	15
#define SL_NONE			255

enum can_bitrate {
    CAN_BITRATE_10K = 0,
    CAN_BITRATE_20K,
    CAN_BITRATE_50K,
    CAN_BITRATE_100K,
    CAN_BITRATE_125K,
    CAN_BITRATE_250K,
    CAN_BITRATE_500K,
    CAN_BITRATE_750K,
    CAN_BITRATE_1000K,

	CAN_BITRATE_INVALID,
};

#define SLCAN_MTU 30 // (sizeof("T1111222281122334455667788EA5F\r")+1)


#define SLCAN_STD_ID_LEN 3
#define SLCAN_EXT_ID_LEN 8


typedef struct _sl_message
{
	uint8_t data[SLCAN_MTU];
	uint8_t len;
}sl_message_t;
char* slcan_parse_str(uint8_t *buf, uint8_t len, twai_message_t *frame, uint8_t *processed_index);
int8_t slcan_parse_frame(uint8_t *buf, twai_message_t *frame);

#endif
