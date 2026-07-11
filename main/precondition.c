#include <stdbool.h>
#include <stdint.h>
#include <string.h>
#include "esp_timer.h"
#include "esp_log.h"
#include "can.h"
#include "precondition.h"
#include "config_server.h"

#define TAG __func__

// ********************* 0x4E8 distance/flag display *********************

typedef enum {
    DIST_UNIT_M = 0x0U,
    DIST_UNIT_KM = 0x1U,
    DIST_UNIT_MI = 0x2U,
    DIST_UNIT_FT = 0x3U,
    DIST_UNIT_YD = 0x4U,
} dist_unit_t;

typedef enum {
    FLAG_DESTINATION = 0x0U,
    FLAG_BLUE_1 = 0x1U,
    FLAG_BLUE_2 = 0x2U,
    FLAG_BLUE_3 = 0x3U,
    FLAG_BLUE_4 = 0x4U,
    FLAG_NONE = 0xFU,
} flag_type_t;

// Set data bytes on a 0x4E8 CANPacket_t to display a distance and flag.
//   distance_int: integer part (0-65534, or 0xFFFF to hide number/unit)
//   distance_tenths: tenths digit (0-9, only shown when unit is km or mi and integer < 100)
//   unit: distance unit (see dist_unit_t)
//   flag: flag icon (see flag_type_t)
static void set_0x4e8_distance_flag(twai_message_t *packet, uint16_t distance_int, uint8_t distance_tenths, dist_unit_t unit, flag_type_t flag) {
    packet->data[0] = (uint8_t)(((distance_tenths & 0xFU) << 4U) | (unit & 0xFU));
    packet->data[4] = (uint8_t)(distance_int & 0xFFU);
    packet->data[5] = (uint8_t)((distance_int >> 8U) & 0xFFU);
    packet->data[6] = (packet->data[6] & 0xF0U) | (flag & 0xFU);
}

// ********************* precondition logic *********************

// is the user currently requesting preconditioning to be active?
static bool precondition_requested = false;
// timestamp of when the user requested preconditioning
static uint32_t precondition_requested_ts = 0U;
// timestamp of last attempt to send precondition message, used for retry logic
static uint32_t precondition_last_attempt_ts = 0U;
// number of ticks remaining to send precondition start/stop messages, used for initial burst logic
static uint8_t precondition_start_ticks_remaining = 0U;
static uint8_t precondition_stop_ticks_remaining = 0U;
// number of times we've retried sending precondition messages, used for retry logic
static uint8_t precondition_retries = 0U;
// has preconditioning been confirmed to be starting by the status frame?
static bool precondition_starting_confirmed = false;
// has preconditioning been confirmed to be active by the status frame?
static bool precondition_started_confirmed = false;
// has precondition stop been confirmed by the status frame?
static bool precondition_stop_confirmed = true;
// track previous state of activation button for edge detection
static bool activation_button_state_prev = false;
// is the status frame available? false if on unknown platform, true if we at any point receive a known status frame
static bool status_frame_available = false;

typedef struct {
    uint32_t frame_id;
    uint8_t byte_index;
    uint8_t byte_mask;
    uint8_t byte_value;
} message_payload_t;

// map of the buttons that can be used to activate preconditioning.
// note: SW buttons (0x448) have a periodic idle message;
//       AVN buttons (0x651/0x652) only send on press/release.
const static message_payload_t activation_messages[NUM_PRECON_BUTTONS] = {
    [SW_STAR]         = {0x448, 5, 0xF0, 0x10},
    [AVN_STAR]        = {0x652, 1, 0x0F, 0x04},
    [AVN_TUNER_IN]    = {0x651, 3, 0xF0, 0x40},
    [AVN_VOL_IN]      = {0x651, 1, 0xF0, 0x40},
    [SW_MODE]         = {0x448, 2, 0xF0, 0x40},
    [SW_SPEAK]        = {0x448, 2, 0x0F, 0x01},
    [SW_CALL]         = {0x448, 2, 0x0F, 0x04},
    [SW_VOL_IN]       = {0x448, 3, 0x0F, 0x01},
    [SW_VOL_UP]       = {0x448, 4, 0x0F, 0x01},
    [SW_VOL_DOWN]     = {0x448, 3, 0xF0, 0x40},
    [SW_SKIP_UP]      = {0x448, 3, 0xF0, 0x10},
    [SW_SKIP_DOWN]    = {0x448, 3, 0x0F, 0x04},
    [SW_OK]           = {0x448, 6, 0xF0, 0x10},
    [AVN_MAP]         = {0x652, 0, 0xF0, 0x40},
    [AVN_NAV]         = {0x652, 0, 0xF0, 0x10},
    [AVN_MEDIA]       = {0x652, 0, 0x0F, 0x01},
    [AVN_TUNER_UP]    = {0x652, 3, 0x0F, 0x04},
    [AVN_TUNER_DOWN]  = {0x652, 3, 0x0F, 0x01},
    [EV6_AVN_SETUP]   = {0x652, 1, 0x0F, 0x0D},
    
};
_Static_assert(sizeof(activation_messages) / sizeof(activation_messages[0])
               == NUM_PRECON_BUTTONS, "button table size mismatch");

// 0x2AD on Ioniq 5/EV6, 0x0A82AA03 on Ioniq 6
#define IS_STATUS_FRAME(frame_id) \
    ((frame_id) == 0x2ADU || (frame_id) == 0x0A82AA03U)

#define STATUS_MASK 0b00011111U  // i.e. 0x15 and 0x55 are both valid "started" status
#define STATUS_IDLE(status_byte) \
    (((status_byte) & STATUS_MASK) == 0x01U)
#define STATUS_STARTING(status_byte) \
    (((status_byte) & STATUS_MASK) == 0x05U)
#define STATUS_STARTED(status_byte) \
    (((status_byte) & STATUS_MASK) == 0x15U)

#define PRECONDITION_DEBOUNCE_US 1000000U  // 1 second
#define PRECONDITION_START_PHASE1_TICKS 3U // 4003 message
#define PRECONDITION_START_PHASE2_TICKS 3U // E007 message
#define PRECONDITION_START_TICKS (PRECONDITION_START_PHASE1_TICKS + PRECONDITION_START_PHASE2_TICKS)
#define PRECONDITION_STOP_PHASE1_TICKS 3U  // 0000 message
#define PRECONDITION_STOP_PHASE2_TICKS 3U  // E007 message
#define PRECONDITION_STOP_TICKS (PRECONDITION_STOP_PHASE1_TICKS + PRECONDITION_STOP_PHASE2_TICKS)
#define PRECONDITION_RETRY_US 10000000U  // 10 seconds
#define PRECONDITION_MAX_RETRIES 4U
#define PRECONDITION_STARTED_TIMEOUT_US 70000000U  // 70 seconds

#define CAR_BUS CAN_BUS_0
#define HEAD_UNIT_BUS CAN_BUS_1

#define SECONDS_UNTIL_START(elapsed) \
    (((elapsed) >= PRECONDITION_STARTED_TIMEOUT_US) ? 0U : \
     ((PRECONDITION_STARTED_TIMEOUT_US - (elapsed)) / 1000000U))

#define SECONDS_UNTIL_STOP_RETRY(elapsed) \
    (((elapsed) >= PRECONDITION_RETRY_US) ? 0U : \
     ((PRECONDITION_RETRY_US - (elapsed)) / 1000000U))

static uint32_t now_us(void) {
    return (uint32_t)esp_timer_get_time();
}

static uint32_t ts_elapsed(uint32_t now, uint32_t old) {
    return now - old;
}

static void send_precondition_start_msg(uint8_t ticks_remaining) {
    twai_message_t packet = {0};
    packet.identifier = 0x0C7U;
    packet.data_length_code = 8U;
    if (ticks_remaining > PRECONDITION_START_PHASE2_TICKS) {
        // send 0000004003000000 to 0x0C7
        packet.data[3] = 0x40U;
        packet.data[4] = 0x03U;
    } else {
        // send 000000E007000000 to 0x0C7
        packet.data[3] = 0xE0U;
        packet.data[4] = 0x07U;
    }
    // TODO(ejones): ensure that blocking for 1 tick is the right move here and elsewhere
    can_send(CAR_BUS, &packet, 1);
}

static void send_precondition_stop_msg(uint8_t ticks_remaining) {
    twai_message_t packet = {0};
    packet.identifier = 0x0C7U;
    packet.data_length_code = 8U;
    if (ticks_remaining <= PRECONDITION_STOP_PHASE2_TICKS) {
        // send 000000E007000000 to 0x0C7
        packet.data[3] = 0xE0U;
        packet.data[4] = 0x07U;
    }
    can_send(CAR_BUS, &packet, 1);
}

// Decide whether to block, modify, or passthrough a message for preconditioning.
// Modifies packet data in-place when returning FWD_MODIFIED.
// On single-bus builds only FWD_MODIFIED has an effect (FWD_BLOCK can't pull a
// frame that's already on the wire); on multi-bus builds the caller bridges,
// so FWD_BLOCK and FWD_PASSTHROUGH matter too. fwd_bus is the destination bus.
fwd_result_t precondition_fwd_hook(twai_message_t *to_send, can_bus_t fwd_bus) {
    // block 0x0C7 message so that the head unit doesn't turn off preconditioning on us
    if (precondition_requested && to_send->identifier == 0x0C7U && fwd_bus == CAR_BUS) {
        return FWD_BLOCK;
    }

    // MITM 0x4ED message while preconditioning is requested
    if (precondition_requested && to_send->identifier == 0x4EDU && fwd_bus == CAR_BUS) {
        to_send->data[5] = 0x10U;
        to_send->data[6] = 0xA0U;
        to_send->data[7] = 0x00U;
        return FWD_MODIFIED;
    }

    // we are currently starting preconditioning and want to display the countdown flag.
    if (precondition_requested && !precondition_started_confirmed && fwd_bus == CAR_BUS) {
        uint32_t now = now_us();
        uint32_t time_since_last_attempt = ts_elapsed(now, precondition_last_attempt_ts);
        if (to_send->identifier == 0x4E8U) {
            // i'm sorry for the nested ternary operators. i don't feel like fixing it right now
            set_0x4e8_distance_flag(
                to_send,
                SECONDS_UNTIL_START(time_since_last_attempt),
                // display retry count in tenths digit
                status_frame_available ? (precondition_retries % 10) : 0,
                status_frame_available ? (precondition_retries == 0 ? DIST_UNIT_YD : DIST_UNIT_KM) : DIST_UNIT_M,
                // switch to destination flag after we get 05 for 2AD
                status_frame_available ? (precondition_starting_confirmed ? FLAG_DESTINATION : FLAG_BLUE_1) : FLAG_BLUE_4
            );
            return FWD_MODIFIED;
        }

        if (to_send->identifier == 0x4CCU) {
            to_send->data[0] = 0x02U;
            return FWD_MODIFIED;
        }
    }

    // we are currently trying to stop preconditioning and want to display the retry status.
    if (!precondition_requested 
            && status_frame_available 
            && !precondition_stop_confirmed 
            && precondition_retries < PRECONDITION_MAX_RETRIES
            && fwd_bus == CAR_BUS) {
        uint32_t now = now_us();
        uint32_t time_since_last_attempt = ts_elapsed(now, precondition_last_attempt_ts);
        if (to_send->identifier == 0x4E8U) {
            set_0x4e8_distance_flag(
                to_send,
                SECONDS_UNTIL_STOP_RETRY(time_since_last_attempt),
                // display retry count in tenths digit
                precondition_retries % 10,
                precondition_retries == 0 ? DIST_UNIT_FT : DIST_UNIT_MI,
                FLAG_NONE
            );
            return FWD_MODIFIED;
        }

        if (to_send->identifier == 0x4CCU) {
            to_send->data[0] = 0x02U;
            return FWD_MODIFIED;
        }
    }

    // otherwise, passthrough without modification
    return FWD_PASSTHROUGH;
}

static void start_preconditioning(uint32_t now) {
    precondition_requested = true;
    precondition_requested_ts = now;
    precondition_last_attempt_ts = now;
    precondition_start_ticks_remaining = PRECONDITION_START_TICKS;
    precondition_starting_confirmed = false;
    precondition_started_confirmed = false;
    precondition_retries = 0U;
}

static void stop_preconditioning(uint32_t now) {
    precondition_requested = false;
    precondition_last_attempt_ts = now;
    precondition_stop_ticks_remaining = PRECONDITION_STOP_TICKS;
    precondition_stop_confirmed = false;
    precondition_retries = 0U;
}

// Cache the configured activation button type. A config change restarts the
// whole firmware, so the value is effectively constant for the lifetime of
// the process. Read it once on the first CAN message rather than on every frame.
static int8_t cached_precon_button_type(void) {
    static int8_t precon_button_type = 0;
    static bool loaded = false;
    if (!loaded) {
        precon_button_type = config_server_precon_button();
        loaded = true;
    }
    return precon_button_type;
}

void precondition_can_rx_hook(twai_message_t *to_push, can_bus_t rx_bus) {
    // 0x2AD/0x0A82AA03 status frame: second byte indicates precondition state
    //   Ioniq 5/6: 0x01 = off/idle, 0x05 = starting, 0x15 = fully running
    //   EV6: 0x41 = off/idle, 0x45 = starting, 0x55 = fully running
    // only trust status frames coming from the car itself; a same-ID frame on
    // the head unit bus must not drive the state machine
    if (IS_STATUS_FRAME(to_push->identifier) && rx_bus == CAR_BUS) {
        // we now know we have the status frame on the current car, so we should use it
        status_frame_available = true;

        uint8_t status = to_push->data[1];
        if (precondition_requested && !precondition_starting_confirmed) {
            if (STATUS_STARTING(status) || STATUS_STARTED(status)) {
                precondition_starting_confirmed = true;
            }
        }
        if (precondition_requested && !precondition_started_confirmed) {
            if (STATUS_STARTED(status)) {
                precondition_started_confirmed = true;
            }
        }
        if (precondition_requested && precondition_started_confirmed) {
            if (STATUS_STARTING(status)) {
                // preconditioning was previously fully active, but now it's only showing as starting.
                // this is a weird situation to be in; let's just reset the current attempt time,
                // and let the retry logic continue as normal if it doesn't resolve itself after a while
                precondition_last_attempt_ts = now_us();
                precondition_started_confirmed = false;
            }
            if (STATUS_IDLE(status)) {
                // preconditioning was previously fully active, but now it's showing as off.
                // it's possible that the car has reached the "Precondition complete" state.
                // until we have a better way to distinguish that state from a real failure mode (TODO(ejones)),
                // let's just assume everything is fine and reset our state
                uint32_t now = now_us();
                stop_preconditioning(now);
                // if we are in "once" mode, actually attempt to actively stop preconditioning.
                // this should prevent preconditioning from restarting once the battery falls back below temp.
                // TODO(ejones): this needs testing
                if(config_server_precon_mode() != ONCE) {
                    // TODO(ejones): continuous mode needs more development. for now,
                    // this just doesn't attempt to stop preconditoning and lets the BMU do what it wants
                    // once it reaches temp
                    precondition_stop_ticks_remaining = 0U;
                    precondition_stop_confirmed = true;
                }
            }
        }
        if (!precondition_requested && !precondition_stop_confirmed && precondition_stop_ticks_remaining == 0U) {
            if (STATUS_IDLE(status)) {
                precondition_stop_confirmed = true;
            }
        }
    }

    int8_t precon_button_type = cached_precon_button_type();
    if (precon_button_type == BUTTON_DISABLED) {
        // activation button disabled in config; don't listen for any button press
        return;
    }
    if (precon_button_type < 0 || precon_button_type >= NUM_PRECON_BUTTONS) {
        ESP_LOGE(TAG, "Invalid precondition button type: %d", precon_button_type);
        return;
    }
    // listen for activation button press (rising edge only), and toggle preconditioning
    message_payload_t button = activation_messages[precon_button_type];
    if (to_push->identifier == button.frame_id) {
        bool button_state = ((to_push->data[button.byte_index] & button.byte_mask) == button.byte_value);
        if (button_state && !activation_button_state_prev) {
            uint32_t now = now_us();
            if (!precondition_requested) {
                start_preconditioning(now);
            } else if (ts_elapsed(now, precondition_requested_ts) > PRECONDITION_DEBOUNCE_US) {
                stop_preconditioning(now);
            }
        }
        activation_button_state_prev = button_state;
    }

}

// called every 40ms
void precondition_tick(void) {
    uint32_t now = now_us();
    uint32_t time_since_last_attempt = ts_elapsed(now, precondition_last_attempt_ts);

    // we can only do retry logic if we know the status frame
    if (status_frame_available) {
        // give up and send one stop attempt if start retries exhausted
        if (precondition_requested
                && precondition_start_ticks_remaining == 0U
                && precondition_retries >= PRECONDITION_MAX_RETRIES
                && ((!precondition_starting_confirmed && time_since_last_attempt > PRECONDITION_RETRY_US)
                    || (!precondition_started_confirmed && time_since_last_attempt > PRECONDITION_STARTED_TIMEOUT_US))) {
            stop_preconditioning(now);
            precondition_retries = PRECONDITION_MAX_RETRIES;  // don't retry the stop; this is a failure case already
        }

        // retry start if not confirmed to be starting yet and it's been long enough
        if (precondition_requested && !precondition_starting_confirmed
                && precondition_start_ticks_remaining == 0U
                && precondition_retries < PRECONDITION_MAX_RETRIES
                && time_since_last_attempt > PRECONDITION_RETRY_US) {
            precondition_last_attempt_ts = now;
            precondition_start_ticks_remaining = PRECONDITION_START_TICKS;
            precondition_retries++;
        }

        // retry start if not confirmed to be started yet and it's been long enough (i.e. we got 2AD 05 but not 15 after a long time)
        if (precondition_requested && !precondition_started_confirmed
                && precondition_start_ticks_remaining == 0U
                && precondition_retries < PRECONDITION_MAX_RETRIES
                && time_since_last_attempt > PRECONDITION_STARTED_TIMEOUT_US) {
            precondition_last_attempt_ts = now;
            precondition_start_ticks_remaining = PRECONDITION_START_TICKS;
            precondition_retries++;
        }

        // retry stop if not confirmed
        if (!precondition_requested && !precondition_stop_confirmed
                && precondition_stop_ticks_remaining == 0U
                && precondition_retries < PRECONDITION_MAX_RETRIES
                && time_since_last_attempt > PRECONDITION_RETRY_US) {
            precondition_last_attempt_ts = now;
            precondition_stop_ticks_remaining = PRECONDITION_STOP_TICKS;
            precondition_retries++;
        }
    } else if (precondition_requested && time_since_last_attempt > PRECONDITION_STARTED_TIMEOUT_US) {
        // if we don't have status messages, we still want to stop displaying the UI after 70 seconds
        precondition_started_confirmed = true;
    }

    // send initial burst of start messages
    if (precondition_requested && precondition_start_ticks_remaining > 0U) {
        send_precondition_start_msg(precondition_start_ticks_remaining);
        precondition_start_ticks_remaining--;
    }

    // send initial burst of stop messages
    if (!precondition_requested && precondition_stop_ticks_remaining > 0U) {
        send_precondition_stop_msg(precondition_stop_ticks_remaining);
        precondition_stop_ticks_remaining--;
    }
}
