// host-test stub for main/config_server.h: only the precondition-facing
// surface. the constants must stay in sync with the real header.
#pragma once
#include <stdint.h>

#define ONCE 0
#define CONTINUOUS 1
#define PERSISTENT 2
#define PRESS_SHORT 0
#define PRESS_LONG 1
#define BUTTON_DISABLED -1
#define SW_STAR 0
#define AVN_STAR 1
#define AVN_TUNER_IN 2
#define AVN_VOL_IN 3
#define SW_MODE 4
#define SW_SPEAK 5
#define SW_CALL 6
#define SW_VOL_IN 7
#define SW_VOL_UP 8
#define SW_VOL_DOWN 9
#define SW_SKIP_UP 10
#define SW_SKIP_DOWN 11
#define SW_OK 12
#define AVN_MAP 13
#define AVN_NAV 14
#define AVN_MEDIA 15
#define AVN_TUNER_UP 16
#define AVN_TUNER_DOWN 17
#define EV6_AVN_SETUP 18
#define NUM_PRECON_BUTTONS 19

int8_t config_server_precon_button(void);
int8_t config_server_precon_mode(void);
int8_t config_server_precon_press(void);
