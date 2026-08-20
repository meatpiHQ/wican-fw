#ifndef __PRECONDITION_H__
#define __PRECONDITION_H__

#include <stdbool.h>
#include <stdint.h>
#include "hsm.h"

void precondition_init(void);
void precondition_can_rx_hook(twai_message_t *to_push, can_bus_t rx_bus);
fwd_result_t precondition_fwd_hook(twai_message_t *to_send, can_bus_t fwd_bus);
void precondition_tick(void);

typedef struct {
    int8_t min_c;
    int8_t max_c;
    int64_t updated_at_us;
} precondition_temperature_t;

bool precondition_get_battery_temperature(precondition_temperature_t *out);

#endif
