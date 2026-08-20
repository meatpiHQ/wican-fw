// host-side behavioral test: drives the precondition state machine with a
// fake clock and a recording can_send
//
// precondition.c is #included (not linked) so the test can inspect states
// and per-state context directly.
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
#include <stdbool.h>
#include <sys/wait.h>
#include <unistd.h>
#include "test_support.h"
#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"
#include "can.h"
#include "config_server.h"

// ---- stubs ----
typedef struct { can_bus_t bus; twai_message_t msg; } sent_t;
static sent_t sent[4096];
static int sent_count = 0;
esp_err_t can_send(can_bus_t bus, twai_message_t *message, TickType_t ticks_to_wait) {
    (void)ticks_to_wait;
    if (sent_count < (int)(sizeof(sent) / sizeof(sent[0]))) {
        sent[sent_count].bus = bus;
        sent[sent_count].msg = *message;
    }
    sent_count++;
    return 0;
}

typedef struct { unsigned char data[64]; int has; UBaseType_t size; } fake_queue_t;
QueueHandle_t xQueueCreate(UBaseType_t len, UBaseType_t item_size) {
    (void)len;
    fake_queue_t *q = calloc(1, sizeof(*q));
    q->size = item_size;
    return q;
}
BaseType_t xQueueOverwrite(QueueHandle_t qh, const void *item) {
    fake_queue_t *q = qh;
    memcpy(q->data, item, q->size);
    q->has = 1;
    return 1;
}
BaseType_t xQueuePeek(QueueHandle_t qh, void *item, TickType_t w) {
    (void)w;
    fake_queue_t *q = qh;
    if (!q->has) return 0;
    memcpy(item, q->data, q->size);
    return 1;
}

static int8_t cfg_button = SW_STAR;
static int8_t cfg_mode = ONCE;
static int8_t cfg_press = PRESS_SHORT;
int8_t config_server_precon_button(void) { return cfg_button; }
int8_t config_server_precon_mode(void) { return cfg_mode; }
int8_t config_server_precon_press(void) { return cfg_press; }

#include "precondition.c"

// ---- harness ----
static void expect_state(const char *name) {
    CHECK_MSG(strcmp(precon_sm.current->name, name) == 0,
              "expected state %s, got %s", name, precon_sm.current->name);
}

static void rx_frame(uint32_t id, const uint8_t d[8], can_bus_t bus) {
    twai_message_t f = {0};
    f.identifier = id;
    f.data_length_code = 8;
    memcpy(f.data, d, 8);
    precondition_can_rx_hook(&f, bus);
}

static void press(void)   { uint8_t d[8] = {0}; d[5] = 0x10; rx_frame(0x448, d, CAN_BUS_0); }
static void release(void) { uint8_t d[8] = {0}; rx_frame(0x448, d, CAN_BUS_0); }
static void toggle(void)  { press(); fake_now += 100000; release(); }
static void car_status(uint8_t b, can_bus_t bus) { uint8_t d[8] = {0}; d[1] = b; rx_frame(0x2AD, d, bus); }
static void car_power(bool ready) { uint8_t d[8] = {0}; d[0] = ready ? 0x04 : 0x00; rx_frame(0x038, d, CAN_BUS_0); }

static void tick1(void) { fake_now += 40000; precondition_tick(); }
static void advance_us(int64_t us) {
    int64_t end = fake_now + us;
    while (fake_now < end) tick1();
}

static void advance_until_state(const char *name, int64_t max_us) {
    int64_t end = fake_now + max_us;
    while (fake_now < end && strcmp(precon_sm.current->name, name) != 0) tick1();
    expect_state(name);
}

static fwd_result_t fwd(uint32_t id, can_bus_t bus, twai_message_t *out) {
    twai_message_t f = {0};
    f.identifier = id;
    f.data_length_code = 8;
    fwd_result_t r = precondition_fwd_hook(&f, bus);
    if (out) *out = f;
    return r;
}

static void check_start_burst_msgs(int base) {
    bool recorded = base >= 0
                    && base + 6 <= sent_count
                    && base + 6 <= (int)(sizeof(sent) / sizeof(sent[0]));
    CHECK(recorded);
    if (!recorded) {
        return;
    }
    for (int i = 0; i < 6; i++) {
        uint8_t expected[8] = {0};
        if (i < 3) {
            expected[3] = 0x40;
            expected[4] = 0x03;
        } else {
            expected[3] = 0xE0;
            expected[4] = 0x07;
        }
        CHECK(sent[base + i].bus == CAR_BUS);
        CHECK(sent[base + i].msg.identifier == 0x0C7);
        CHECK(sent[base + i].msg.data_length_code == 8);
        CHECK(memcmp(sent[base + i].msg.data, expected, sizeof(expected)) == 0);
    }
}

static void check_stop_burst_msgs(int base) {
    bool recorded = base >= 0
                    && base + 6 <= sent_count
                    && base + 6 <= (int)(sizeof(sent) / sizeof(sent[0]));
    CHECK(recorded);
    if (!recorded) {
        return;
    }
    for (int i = 0; i < 6; i++) {
        uint8_t expected[8] = {0};
        if (i >= 3) {
            expected[3] = 0xE0;
            expected[4] = 0x07;
        }
        CHECK(sent[base + i].bus == CAR_BUS);
        CHECK(sent[base + i].msg.identifier == 0x0C7);
        CHECK(sent[base + i].msg.data_length_code == 8);
        CHECK(memcmp(sent[base + i].msg.data, expected, sizeof(expected)) == 0);
    }
}

static void run_short_press(void) {
    precondition_init();
    expect_state("idle");

    // --- platform without status frames: no retries, fake-active at 70s ---
    sent_count = 0;
    toggle();
    expect_state("start-burst");
    for (int i = 0; i < 6; i++) tick1();
    CHECK(sent_count == 6);
    check_start_burst_msgs(0);
    expect_state("wait-starting");
    // display: BLUE_4 flag, no retry digit (unknown platform)
    twai_message_t m;
    CHECK(fwd(0x4E8, CAN_BUS_0, &m) == FWD_MODIFIED);
    CHECK((m.data[6] & 0x0F) == 0x4);         // FLAG_BLUE_4
    CHECK(fwd(0x0C7, CAN_BUS_0, NULL) == FWD_BLOCK);
    CHECK(fwd(0x4ED, CAN_BUS_0, &m) == FWD_MODIFIED);
    CHECK(m.data[5] == 0x10 && m.data[6] == 0xA0 && m.data[7] == 0x00);
    CHECK(fwd(0x0C7, CAN_BUS_1, NULL) == FWD_PASSTHROUGH);  // head unit bus untouched
    CHECK(fwd(0x4E8, CAN_BUS_1, NULL) == FWD_PASSTHROUGH);
    CHECK(fwd(0x4CC, CAN_BUS_1, NULL) == FWD_PASSTHROUGH);
    sent_count = 0;
    advance_us(71000000);
    expect_state("active");
    CHECK(sent_count == 0);                    // no retries without status frames
    CHECK(fwd(0x4E8, CAN_BUS_0, NULL) == FWD_PASSTHROUGH);  // display off in active
    CHECK(fwd(0x0C7, CAN_BUS_0, NULL) == FWD_BLOCK);        // still blocking
    // stop without status frames: burst then straight to idle, no display
    fake_now += 2000000;
    toggle();
    expect_state("stop-burst");
    CHECK(fwd(0x4E8, CAN_BUS_0, NULL) == FWD_PASSTHROUGH);
    sent_count = 0;
    for (int i = 0; i < 6; i++) tick1();
    CHECK(sent_count == 6);
    check_stop_burst_msgs(0);
    expect_state("idle");

    // --- happy path with status frames ---
    toggle();
    expect_state("start-burst");
    car_status(0x01, CAN_BUS_1);               // head unit bus: must be ignored
    CHECK(!platform.status_frame_available);
    car_status(0x01, CAN_BUS_0);               // idle status during burst: ignored but latched
    CHECK(platform.status_frame_available);
    expect_state("start-burst");
    for (int i = 0; i < 6; i++) tick1();
    expect_state("wait-starting");
    CHECK(fwd(0x4E8, CAN_BUS_0, &m) == FWD_MODIFIED);
    CHECK((m.data[6] & 0x0F) == 0x1);          // FLAG_BLUE_1 before starting confirm
    car_status(0x45, CAN_BUS_0);               // EV6-style "starting"
    expect_state("wait-started");
    CHECK(fwd(0x4E8, CAN_BUS_0, &m) == FWD_MODIFIED);
    CHECK((m.data[6] & 0x0F) == 0x0);          // FLAG_DESTINATION after starting confirm
    car_status(0x55, CAN_BUS_0);               // EV6-style "started"
    expect_state("active");
    // downgrade: active -> starting again
    car_status(0x05, CAN_BUS_0);
    expect_state("wait-started");
    car_status(0x15, CAN_BUS_0);
    expect_state("active");
    // "complete" in ONCE mode -> real stop
    car_status(0x01, CAN_BUS_0);
    expect_state("stop-burst");
    car_status(0x01, CAN_BUS_0);               // stop confirm ignored during burst
    expect_state("stop-burst");
    CHECK(fwd(0x4E8, CAN_BUS_1, NULL) == FWD_PASSTHROUGH);
    CHECK(fwd(0x4CC, CAN_BUS_1, NULL) == FWD_PASSTHROUGH);
    CHECK(fwd(0x4E8, CAN_BUS_0, &m) == FWD_MODIFIED);  // stop retry display (retries=0)
    CHECK((m.data[0] & 0x0F) == 0x3);          // DIST_UNIT_FT on first stop attempt
    for (int i = 0; i < 6; i++) tick1();
    expect_state("wait-stopped");
    car_status(0x01, CAN_BUS_0);
    expect_state("idle");

    // --- debounce ---
    toggle();
    expect_state("start-burst");
    toggle();                                   // within 1s: debounced away
    CHECK(sm_in(&precon_sm, &S_REQUESTED));
    advance_us(1200000);
    expect_state("wait-starting");
    toggle();                                   // past debounce: stop
    expect_state("stop-burst");
    toggle();                                   // toggle during stopping restarts
    expect_state("start-burst");
    advance_us(1200000);
    toggle();
    for (int i = 0; i < 6; i++) tick1();
    expect_state("wait-stopped");
    car_status(0x01, CAN_BUS_0);
    expect_state("idle");

    // --- start retries, then give-up with silent stop ---
    toggle();
    sent_count = 0;
    for (int i = 0; i < 6; i++) tick1();
    expect_state("wait-starting");
    CHECK(requested.retries == 0);
    for (int r = 1; r <= 4; r++) {
        advance_until_state("start-burst", 11000000);   // >10s: retry
        CHECK(requested.retries == r);
        for (int i = 0; i < 6; i++) tick1();
        expect_state("wait-starting");
    }
    CHECK(sent_count == 5 * 6);
    CHECK(fwd(0x4E8, CAN_BUS_0, &m) == FWD_MODIFIED);
    CHECK((m.data[0] >> 4) == 4);               // retry count in tenths digit
    // retries exhausted: give up with a silent stop
    int base = sent_count;
    advance_until_state("stop-burst", 11000000);
    CHECK(stopping.retries == 4);
    CHECK(fwd(0x4E8, CAN_BUS_0, NULL) == FWD_PASSTHROUGH);  // silent stop: no display
    for (int i = 0; i < 6; i++) tick1();
    expect_state("wait-stopped");
    CHECK(sent_count == base + 6);
    check_stop_burst_msgs(base);
    advance_us(10100000);                       // no stop retries in the give-up case
    expect_state("idle");
    CHECK(sent_count == base + 6);

    // --- wait-started timeout retries at 70s, confirm mid-burst ---
    toggle();
    for (int i = 0; i < 6; i++) tick1();
    car_status(0x05, CAN_BUS_0);
    expect_state("wait-started");
    advance_us(10100000);
    expect_state("wait-started");               // 10s retry does NOT apply here
    advance_until_state("start-burst", 61000000);  // ...but 70s does
    CHECK(requested.retries == 1);
    sent_count = 0;
    for (int i = 0; i < 3; i++) tick1();        // mid-burst
    expect_state("start-burst");
    car_status(0x15, CAN_BUS_0);                // started confirm mid-burst
    expect_state("start-burst");                // burst still runs to completion
    CHECK(fwd(0x4E8, CAN_BUS_0, NULL) == FWD_PASSTHROUGH);  // but display is off
    for (int i = 0; i < 3; i++) tick1();
    CHECK(sent_count == 6);                     // full burst sent despite confirm
    expect_state("active");
    // stop retries
    fake_now += 2000000;
    toggle();
    for (int i = 0; i < 6; i++) tick1();
    expect_state("wait-stopped");
    CHECK(stopping.retries == 0);
    advance_until_state("stop-burst", 11000000);
    CHECK(stopping.retries == 1);
    for (int i = 0; i < 6; i++) tick1();
    expect_state("wait-stopped");
    car_status(0x01, CAN_BUS_0);
    expect_state("idle");

    // --- the car turning off silently abandons an attempt (all modes) ---
    car_power(true);
    expect_state("idle");                      // ready edge does nothing in once mode
    toggle();
    for (int i = 0; i < 6; i++) tick1();
    expect_state("wait-starting");
    sent_count = 0;
    car_power(false);
    expect_state("idle");
    CHECK(sent_count == 0);                    // no stop burst: nothing left to stop
    CHECK(fwd(0x0C7, CAN_BUS_0, NULL) == FWD_PASSTHROUGH);

    // --- car off during an active stop abandons the stop ---
    car_power(true);
    toggle();
    for (int i = 0; i < 6; i++) tick1();
    expect_state("wait-starting");
    fake_now += 2000000;
    toggle();
    expect_state("stop-burst");
    sent_count = 0;
    car_power(false);
    expect_state("idle");
    advance_us(15000000);
    CHECK(sent_count == 0);                    // no burst continuation, no retries
}

static void run_long_press(void) {
    precondition_init();
    expect_state("idle");
    // quick press+release in long mode: nothing happens
    toggle();
    advance_us(2000000);
    expect_state("idle");
    // hold crossing the threshold fires once, without release
    press();
    advance_us(500000);
    expect_state("idle");
    sent_count = 0;
    advance_us(600000);                         // crosses 1s: fires exactly once
    CHECK(sm_in(&precon_sm, &S_REQUESTED));
    CHECK(sent_count > 0);                      // burst began on the fire tick
    advance_us(2000000);                        // keep holding: no second fire
    CHECK(sm_in(&precon_sm, &S_REQUESTED));     // (a re-fire would stop it)
    release();
    CHECK(sm_in(&precon_sm, &S_REQUESTED));     // release in long mode is a no-op
}

static void run_continuous(void) {
    precondition_init();
    expect_state("idle");
    car_power(true);
    expect_state("idle");                       // ready edge alone starts nothing

    // manual start shows the countdown, confirms, hands off to MANAGED
    toggle();
    expect_state("start-burst");
    twai_message_t m;
    CHECK(fwd(0x4E8, CAN_BUS_0, &m) == FWD_MODIFIED);
    for (int i = 0; i < 6; i++) tick1();
    expect_state("wait-starting");
    car_status(0x05, CAN_BUS_0);
    expect_state("wait-started");
    car_status(0x15, CAN_BUS_0);
    expect_state("managed");
    // MITM stays on while the BMU manages; the display does not
    CHECK(fwd(0x0C7, CAN_BUS_0, NULL) == FWD_BLOCK);
    CHECK(fwd(0x4ED, CAN_BUS_0, &m) == FWD_MODIFIED);
    CHECK(m.data[5] == 0x10 && m.data[6] == 0xA0 && m.data[7] == 0x00);
    CHECK(fwd(0x4E8, CAN_BUS_0, NULL) == FWD_PASSTHROUGH);

    // while the BMU reports running, no re-requests ever fire
    sent_count = 0;
    advance_us(310000000LL);
    car_status(0x15, CAN_BUS_0);
    advance_us(310000000LL);
    CHECK(sent_count == 0);
    expect_state("managed");

    // BMU stops: the idle edge arms the 5-minute re-nudge
    car_status(0x01, CAN_BUS_0);
    expect_state("managed");
    advance_us(295000000LL);                    // just under 5 minutes
    CHECK(sent_count == 0);
    expect_state("managed");
    advance_until_state("start-burst", 10000000LL);
    CHECK(requested.kind == ATTEMPT_PERIODIC);
    CHECK(fwd(0x4E8, CAN_BUS_0, NULL) == FWD_PASSTHROUGH);  // periodic attempts are silent
    sent_count = 0;
    for (int i = 0; i < 6; i++) tick1();
    CHECK(sent_count == 6);
    check_start_burst_msgs(0);
    expect_state("wait-starting");

    // periodic attempts are one-shot: a 10s timeout goes back to MANAGED
    sent_count = 0;
    advance_until_state("managed", 11000000LL);
    CHECK(sent_count == 0);                     // no retry bursts, no stop burst

    // next interval: nudge again, confirm mid-burst
    car_status(0x01, CAN_BUS_0);                // re-arm (ctx was re-zeroed)
    advance_until_state("start-burst", 302000000LL);
    car_status(0x15, CAN_BUS_0);
    for (int i = 0; i < 6; i++) tick1();        // burst still runs to completion
    expect_state("managed");

    // toggle while managed: latch off, active stop
    fake_now += 2000000;                        // clear the start/stop debounce
    sent_count = 0;
    toggle();
    expect_state("stop-burst");
    for (int i = 0; i < 6; i++) tick1();
    check_stop_burst_msgs(0);
    expect_state("wait-stopped");
    car_status(0x01, CAN_BUS_0);
    expect_state("idle");
    // latch is off: idle status + 5 minutes produce nothing
    sent_count = 0;
    advance_us(310000000LL);
    CHECK(sent_count == 0);
    expect_state("idle");
    CHECK(fwd(0x0C7, CAN_BUS_0, NULL) == FWD_PASSTHROUGH);

    // car-off while managed drops the latch entirely (continuous)
    toggle();
    for (int i = 0; i < 6; i++) tick1();
    car_status(0x05, CAN_BUS_0);
    car_status(0x15, CAN_BUS_0);
    expect_state("managed");
    car_power(false);
    expect_state("idle");
    sent_count = 0;
    car_power(true);                            // ready again: nothing restarts
    expect_state("idle");
    car_status(0x01, CAN_BUS_0);
    advance_us(310000000LL);
    CHECK(sent_count == 0);

    // give-up with the latch on parks in MANAGED (no silent stop burst)
    toggle();
    expect_state("start-burst");
    for (int i = 0; i < 6; i++) tick1();
    expect_state("wait-starting");
    for (int r = 1; r <= 4; r++) {
        advance_until_state("start-burst", 11000000LL);
        CHECK(requested.retries == r);
        for (int i = 0; i < 6; i++) tick1();
        expect_state("wait-starting");
    }
    sent_count = 0;
    advance_until_state("managed", 11000000LL);
    CHECK(sent_count == 0);                     // no stop burst on repeating give-up
    CHECK(fwd(0x0C7, CAN_BUS_0, NULL) == FWD_BLOCK);  // MITM stays latched
}

static void run_persistent(void) {
    precondition_init();
    expect_state("idle");
    car_power(true);
    expect_state("idle");                       // nothing stored in flash yet

    // manual start mirrors the latch to flash
    toggle();
    expect_state("start-burst");
    CHECK(!fake_nvs_exists);                    // the flash mirror trails by one tick
    tick1();
    CHECK(fake_nvs_exists && fake_nvs_value == 1);
    for (int i = 0; i < 5; i++) tick1();
    car_status(0x05, CAN_BUS_0);
    car_status(0x15, CAN_BUS_0);
    expect_state("managed");

    // car off: the session survives in MANAGED, latch stays in flash
    unsigned int commits_before = fake_nvs_commit_count;
    car_power(false);
    expect_state("managed");
    CHECK(fake_nvs_value == 1);
    CHECK(fwd(0x0C7, CAN_BUS_0, NULL) == FWD_BLOCK);   // MITM still latched
    // no nudges while the car is off
    sent_count = 0;
    car_status(0x01, CAN_BUS_0);                // arms idle, but car not ready
    advance_us(310000000LL);
    CHECK(sent_count == 0);
    expect_state("managed");

    // car back to READY: leave time for the car to finish coming up before the
    // relaunch, then show the countdown as a reminder
    sent_count = 0;
    car_power(true);
    expect_state("car-start-delay");
    CHECK(fwd(0x4E8, CAN_BUS_0, NULL) == FWD_PASSTHROUGH);
    advance_us(PRECONDITION_CAR_START_DELAY_US - 40000);
    expect_state("car-start-delay");
    CHECK(sent_count == 0);
    tick1();
    expect_state("start-burst");
    CHECK(requested.kind == ATTEMPT_CAR_START);
    CHECK(fwd(0x4E8, CAN_BUS_0, NULL) == FWD_MODIFIED);
    for (int i = 0; i < 6; i++) tick1();
    CHECK(sent_count == 6);
    check_start_burst_msgs(0);
    car_status(0x05, CAN_BUS_0);
    car_status(0x15, CAN_BUS_0);
    expect_state("managed");

    // BMU stops: the 5-minute re-nudge re-enters REQUESTED; neither it nor the
    // relaunch above may touch flash (since the commit happens in the tick)
    car_status(0x01, CAN_BUS_0);
    advance_until_state("start-burst", 302000000LL);
    CHECK(requested.kind == ATTEMPT_PERIODIC);
    for (int i = 0; i < 6; i++) tick1();
    car_status(0x05, CAN_BUS_0);
    car_status(0x15, CAN_BUS_0);
    expect_state("managed");
    CHECK(fake_nvs_commit_count == commits_before);

    // toggle off from managed clears the flash latch and stops actively
    fake_now += 2000000;
    toggle();
    expect_state("stop-burst");
    CHECK(!repeating_enabled);
    tick1();
    CHECK(fake_nvs_value == 0);                 // clear flushed on the next tick
    for (int i = 0; i < 5; i++) tick1();
    expect_state("wait-stopped");
    car_status(0x01, CAN_BUS_0);
    expect_state("idle");
    // next car power cycle: nothing relaunches
    car_power(false);
    car_power(true);
    expect_state("idle");
}

// fresh process with the flash latch already set: a WiCAN power cycle while
// persistent preconditioning was enabled
static void run_persistent_restore(void) {
    fake_nvs_exists = true;
    fake_nvs_value = 1;
    precondition_init();
    expect_state("managed");                    // restored latch parks in MANAGED
    CHECK(fwd(0x0C7, CAN_BUS_0, NULL) == FWD_BLOCK);  // MITM guards the latch from boot
    // no nudges before the car is ready
    sent_count = 0;
    car_status(0x01, CAN_BUS_0);
    advance_us(310000000LL);
    CHECK(sent_count == 0);
    expect_state("managed");
    car_power(true);
    expect_state("car-start-delay");            // first ready edge starts the delay
    CHECK(fwd(0x4E8, CAN_BUS_0, NULL) == FWD_PASSTHROUGH);  // no countdown before the try
    advance_us(PRECONDITION_CAR_START_DELAY_US - 40000);
    expect_state("car-start-delay");
    CHECK(sent_count == 0);
    tick1();
    expect_state("start-burst");
    CHECK(requested.kind == ATTEMPT_CAR_START);
    CHECK(fwd(0x4E8, CAN_BUS_0, NULL) == FWD_MODIFIED);  // countdown shown
    for (int i = 0; i < 6; i++) tick1();
    car_status(0x05, CAN_BUS_0);
    car_status(0x15, CAN_BUS_0);
    expect_state("managed");
    CHECK(fake_nvs_commit_count == 0);          // the restore itself never rewrites flash
}

static void run_persistent_disable_before_ready(void) {
    fake_nvs_exists = true;
    fake_nvs_value = 1;
    precondition_init();
    expect_state("managed");

    // within the debounce window after boot the toggle is swallowed
    toggle();
    expect_state("managed");
    CHECK(repeating_enabled);

    fake_now += 2000000;
    toggle();
    expect_state("stop-burst");                 // disable = active stop, even before ready
    CHECK(!repeating_enabled);
    tick1();
    CHECK(fake_nvs_value == 0);                 // latch clear flushed to flash
    for (int i = 0; i < 5; i++) tick1();
    expect_state("idle");                       // no status frame ever seen: no wait

    car_power(true);
    expect_state("idle");                       // disabled: ready must not relaunch
}

static void run_persistent_write_retry(void) {
    precondition_init();
    expect_state("idle");

    toggle();
    expect_state("start-burst");
    tick1();
    CHECK(fake_nvs_value == 1);

    fake_now += 2000000;
    fake_nvs_commit_failures = 1;
    toggle();
    expect_state("stop-burst");
    CHECK(!repeating_enabled);
    tick1();                                    // first flush attempt fails
    CHECK(fake_nvs_commit_failures == 0);
    CHECK(fake_nvs_value == 1);                 // failed commit left flash stale

    advance_us(PRECONDITION_NVS_RETRY_US - 80000);
    CHECK(fake_nvs_value == 1);                 // still inside the backoff
    advance_us(160000);
    CHECK(fake_nvs_value == 0);                 // retry landed

    car_power(true);
    expect_state("idle");                      // the stale enable cannot relaunch
}

static void run_persistent_write_retry_limit(void) {
    precondition_init();
    toggle();
    tick1();
    CHECK(fake_nvs_value == 1);

    fake_now += 2000000;
    fake_nvs_commit_failures = PRECONDITION_NVS_MAX_RETRIES + 2U;
    toggle();

    // initial attempt + MAX_RETRIES retries, then the budget is spent
    advance_us((PRECONDITION_NVS_MAX_RETRIES + 1U) * PRECONDITION_NVS_RETRY_US);
    CHECK(persistent_save_failures == PRECONDITION_NVS_MAX_RETRIES + 1U);
    CHECK(fake_nvs_value == 1);                 // flash left stale
    unsigned int failures_left = fake_nvs_commit_failures;
    CHECK(failures_left == 1U);

    advance_us(2 * PRECONDITION_NVS_RETRY_US);
    CHECK(fake_nvs_commit_failures == failures_left);  // no writes after the cap
    CHECK(fake_nvs_value == 1);
}

// a stored persistent latch must not leak into other modes
static void run_once_ignores_stored_latch(void) {
    fake_nvs_exists = true;
    fake_nvs_value = 1;
    precondition_init();
    car_power(true);
    expect_state("idle");                       // no relaunch in once mode
    toggle();
    for (int i = 0; i < 6; i++) tick1();
    car_status(0x05, CAN_BUS_0);
    car_status(0x15, CAN_BUS_0);
    expect_state("active");                     // not managed
}

// ---- suite table ----
// each suite runs in its own forked process: the config caches, the repeating
// latch, the fake NVS, and the platform discovery flags all live in statics
// that latch on first use
typedef struct {
    const char *name;
    int8_t mode;
    int8_t press;
    void (*fn)(void);
} suite_t;

static const suite_t suites[] = {
    {"precondition short-press once", ONCE, PRESS_SHORT, run_short_press},
    {"precondition long-press once", ONCE, PRESS_LONG, run_long_press},
    {"precondition continuous", CONTINUOUS, PRESS_SHORT, run_continuous},
    {"precondition persistent", PERSISTENT, PRESS_SHORT, run_persistent},
    {"precondition persistent restore", PERSISTENT, PRESS_SHORT, run_persistent_restore},
    {"precondition persistent disable before ready", PERSISTENT, PRESS_SHORT, run_persistent_disable_before_ready},
    {"precondition persistent write retry", PERSISTENT, PRESS_SHORT, run_persistent_write_retry},
    {"precondition persistent write retry limit", PERSISTENT, PRESS_SHORT, run_persistent_write_retry_limit},
    {"precondition once ignores stored latch", ONCE, PRESS_SHORT, run_once_ignores_stored_latch},
};
#define NUM_SUITES (sizeof(suites) / sizeof(suites[0]))

static int run_suite(const suite_t *s) {
    cfg_mode = s->mode;
    cfg_press = s->press;
    s->fn();
    return test_report(s->name);
}

int main(int argc, char **argv) {
    // single-suite run, handy for debugging: test_precondition <substring>
    if (argc > 1) {
        for (size_t i = 0; i < NUM_SUITES; i++) {
            if (strstr(suites[i].name, argv[1]) != NULL) {
                return run_suite(&suites[i]);
            }
        }
        fprintf(stderr, "no suite matching '%s'\n", argv[1]);
        return 1;
    }
    int rc = 0;
    for (size_t i = 0; i < NUM_SUITES; i++) {
        pid_t child = fork();
        if (child < 0) {
            perror("fork");
            return 1;
        }
        if (child == 0) {
            exit(run_suite(&suites[i]));
        }
        int status = 0;
        waitpid(child, &status, 0);
        if (!WIFEXITED(status) || WEXITSTATUS(status) != 0) {
            rc = 1;
        }
    }
    return rc;
}
