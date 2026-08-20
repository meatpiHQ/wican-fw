// engine-level tests for hierarchical transitions and dispatch loops. A
// transition applied inside a nested sm_send_event must stop the enclosing
// per-state walk, while global hooks keep their run-before-the-walk semantics.
//
// state tree:   A (composite) -> A1 (initial leaf)
//                             -> A2 (leaf)
//               B (leaf)
#include <string.h>
#include <stdint.h>
#include <stdbool.h>
#include "test_support.h"
#include "hsm.h"

// ---- call log ----
static char logbuf[4096];
static void log_add(const char *s) {
    size_t used = strlen(logbuf);
    size_t available = sizeof(logbuf) - used;
    int written = snprintf(logbuf + used, available, "%s ", s);
    CHECK_MSG(written >= 0 && (size_t)written < available,
              "call log overflow while appending %s", s);
}
static void log_reset(void) { logbuf[0] = 0; }
static bool log_contains(const char *s) { return strstr(logbuf, s) != NULL; }

// CHECK, plus the call log for context on failure
#define CHECKL(cond) CHECK_MSG(cond, "log: %s", logbuf)

// ---- test machine ----
static sm_t hsm;
static const sm_state_t S_A, S_A1, S_A2, S_B;

enum { EV_GO = 1, EV_OUTER, EV_INNER, EV_RESET, EV_TO_A2, EV_SELF, EV_TO_A };

// per-test scripts, cleared by the handler that consumes them
static bool a_tick_sends_go;      // A.tick fires EV_GO (nested-event transition)
static bool a_tick_goes_direct;   // A.tick calls sm_transition itself
static bool a1_rx_sends_go;       // A1.rx fires EV_GO
static bool a1_outer_sends_inner; // A1.event(OUTER) fires EV_INNER, returns false
static bool g_tick_sends_go;      // global tick hook fires EV_GO
static bool g_tick_goes_direct;   // global tick hook calls sm_transition itself

// sm_ticks_in_state() as seen by the most recent tick handler call
static uint32_t a_tick_saw = 99, a1_tick_saw = 99, b_tick_saw = 99;

static void a_enter(sm_t *sm) { (void)sm; log_add("A.enter"); }
static void a_exit(sm_t *sm) { (void)sm; log_add("A.exit"); }
static void a_tick(sm_t *sm) {
    log_add("A.tick");
    a_tick_saw = sm_ticks_in_state(sm);
    if (a_tick_sends_go) {
        a_tick_sends_go = false;
        sm_send_event(sm, EV_GO);
    }
    if (a_tick_goes_direct) {
        a_tick_goes_direct = false;
        sm_transition(sm, &S_B);
    }
}
static bool a_event(sm_t *sm, sm_event_t ev) {
    switch (ev) {
    case EV_GO:
        log_add("A.event.go");
        sm_transition(sm, &S_B);
        return true;
    case EV_INNER:
        log_add("A.event.inner");
        sm_transition(sm, &S_B);
        return true;
    case EV_OUTER:
        log_add("A.event.outer");
        return true;
    default:
        return false;
    }
}
static void a_rx(sm_t *sm, const twai_message_t *msg, can_bus_t bus) {
    (void)sm; (void)msg; (void)bus;
    log_add("A.rx");
}

static void a1_enter(sm_t *sm) { (void)sm; log_add("A1.enter"); }
static void a1_exit(sm_t *sm) { (void)sm; log_add("A1.exit"); }
static void a1_tick(sm_t *sm) {
    log_add("A1.tick");
    a1_tick_saw = sm_ticks_in_state(sm);
}
static bool a1_event(sm_t *sm, sm_event_t ev) {
    if (ev == EV_OUTER && a1_outer_sends_inner) {
        a1_outer_sends_inner = false;
        log_add("A1.event.outer");
        sm_send_event(sm, EV_INNER);
        return false; // unhandled
    }
    if (ev == EV_TO_A2) {
        log_add("A1.event.to-a2");
        sm_transition(sm, &S_A2);
        return true;
    }
    return false;
}
static void a1_rx(sm_t *sm, const twai_message_t *msg, can_bus_t bus) {
    (void)msg; (void)bus;
    log_add("A1.rx");
    if (a1_rx_sends_go) {
        a1_rx_sends_go = false;
        sm_send_event(sm, EV_GO);
    }
}

static void a2_enter(sm_t *sm) { (void)sm; log_add("A2.enter"); }
static void a2_exit(sm_t *sm) { (void)sm; log_add("A2.exit"); }
static bool a2_event(sm_t *sm, sm_event_t ev) {
    if (ev == EV_SELF) {
        log_add("A2.event.self");
        sm_transition(sm, &S_A2);
        return true;
    }
    if (ev == EV_TO_A) {
        log_add("A2.event.to-a");
        sm_transition(sm, &S_A);
        return true;
    }
    return false;
}

static void b_enter(sm_t *sm) { (void)sm; log_add("B.enter"); }
static void b_exit(sm_t *sm) { (void)sm; log_add("B.exit"); }
static void b_tick(sm_t *sm) {
    log_add("B.tick");
    b_tick_saw = sm_ticks_in_state(sm);
}
static bool b_event(sm_t *sm, sm_event_t ev) {
    if (ev == EV_GO) {
        log_add("B.event.go");
        sm_transition(sm, &S_A);
        return true;
    }
    if (ev == EV_RESET) {
        sm_transition(sm, &S_A);
        return true;
    }
    return false;
}

static const sm_state_t S_A = {
    .name = "A", .initial = &S_A1,
    .enter = a_enter, .exit = a_exit,
    .tick = a_tick, .event = a_event, .rx = a_rx,
};
static const sm_state_t S_A1 = {
    .name = "A1", .parent = &S_A,
    .enter = a1_enter, .exit = a1_exit,
    .tick = a1_tick, .event = a1_event, .rx = a1_rx,
};
static const sm_state_t S_A2 = {
    .name = "A2", .parent = &S_A,
    .enter = a2_enter, .exit = a2_exit,
    .event = a2_event,
};
static const sm_state_t S_B = {
    .name = "B",
    .enter = b_enter, .exit = b_exit,
    .tick = b_tick, .event = b_event,
};

static void g_tick(sm_t *sm) {
    log_add("G.tick");
    if (g_tick_sends_go) {
        g_tick_sends_go = false;
        sm_send_event(sm, EV_GO);
    }
    if (g_tick_goes_direct) {
        g_tick_goes_direct = false;
        sm_transition(sm, &S_B);
    }
}
static void g_rx(sm_t *sm, const twai_message_t *msg, can_bus_t bus) {
    (void)sm; (void)msg; (void)bus;
    log_add("G.rx");
}
static const sm_hooks_t hooks = { .tick = g_tick, .rx = g_rx };

// ---- helpers ----
static void tick(void) {
    fake_now += 40000;
    sm_tick(&hsm);
}
static void rx(void) {
    twai_message_t msg = { .identifier = 0x123, .data_length_code = 8 };
    fake_now += 1000;
    sm_rx(&hsm, &msg, CAN_BUS_0);
}
// return to A/A1 between tests (from B) and clear the log
static void reset_to_a(void) {
    if (hsm.current == &S_B) {
        sm_send_event(&hsm, EV_RESET);
    }
    CHECKL(hsm.current == &S_A1);
    log_reset();
}

int main(void) {
    // A leaf is a valid initial state; its ancestors are entered first.
    sm_t leaf_initial_hsm;
    sm_init(&leaf_initial_hsm, "test-hsm-leaf-init", &S_A2, NULL);
    CHECKL(leaf_initial_hsm.current == &S_A2);
    CHECKL(strcmp(logbuf, "A.enter A2.enter ") == 0);
    log_reset();

    sm_init(&hsm, "test-hsm", &S_A, &hooks);
    CHECKL(hsm.current == &S_A1);
    CHECKL(log_contains("A.enter A1.enter"));
    log_reset();

    // nested-event transition from a per-state tick handler ends the walk:
    // A.tick sends EV_GO, A handles it by moving to B; the stale A1.tick
    // must not run, and B's first real tick must still see 0 ticks-in-state
    a_tick_sends_go = true;
    tick();
    CHECKL(hsm.current == &S_B);
    CHECKL(log_contains("A.tick A.event.go A1.exit A.exit B.enter"));
    CHECKL(!log_contains("A1.tick"));
    b_tick_saw = 99;
    tick();
    CHECKL(b_tick_saw == 0);
    reset_to_a();

    // nested-event transition from a per-state rx handler ends the walk:
    // A1.rx sends EV_GO (handled by A -> B); exited A must not see the frame
    a1_rx_sends_go = true;
    rx();
    CHECKL(hsm.current == &S_B);
    CHECKL(log_contains("G.rx A1.rx A.event.go"));
    CHECKL(!log_contains("A.rx"));
    reset_to_a();

    // transition inside a recursive sm_send_event ends the outer event's
    // bubbling: A1.event(OUTER) sends EV_INNER (handled by A -> B); EV_OUTER
    // must not keep bubbling to the now-exited A
    a1_outer_sends_inner = true;
    sm_send_event(&hsm, EV_OUTER);
    CHECKL(hsm.current == &S_B);
    CHECKL(log_contains("A1.event.outer A.event.inner"));
    CHECKL(!log_contains("A.event.outer"));
    reset_to_a();

    // direct transition from a per-state tick handler still ends the walk
    a_tick_goes_direct = true;
    tick();
    CHECKL(hsm.current == &S_B);
    CHECKL(!log_contains("A1.tick"));
    log_reset();

    // global-hook exception: an event sent by the global tick hook
    // transitions (B -> A), and the per-state walk still runs on the
    // same tick, against the new state, with 0 ticks-in-state
    g_tick_sends_go = true;
    a_tick_saw = a1_tick_saw = 99;
    tick();
    CHECKL(hsm.current == &S_A1);
    CHECKL(log_contains("G.tick B.event.go B.exit A.enter A1.enter A.tick A1.tick"));
    CHECKL(a_tick_saw == 0 && a1_tick_saw == 0);
    log_reset();

    // uniform rule: a direct sm_transition from the global tick hook also
    // resolves before the walk, which then runs against the new state
    g_tick_goes_direct = true;
    b_tick_saw = 99;
    tick();
    CHECKL(hsm.current == &S_B);
    CHECKL(log_contains("G.tick A1.exit A.exit B.enter B.tick"));
    CHECKL(!log_contains("A.tick") && !log_contains("A1.tick"));
    CHECKL(b_tick_saw == 0);
    log_reset();

    reset_to_a();

    // a sibling transition keeps the shared parent active
    sm_send_event(&hsm, EV_TO_A2);
    CHECKL(hsm.current == &S_A2);
    CHECKL(strcmp(logbuf, "A1.event.to-a2 A1.exit A2.enter ") == 0);
    log_reset();

    // a self-transition exits and re-enters the leaf
    sm_send_event(&hsm, EV_SELF);
    CHECKL(hsm.current == &S_A2);
    CHECKL(strcmp(logbuf, "A2.event.self A2.exit A2.enter ") == 0);
    log_reset();

    // targeting an ancestor re-enters it and follows its initial child
    sm_send_event(&hsm, EV_TO_A);
    CHECKL(hsm.current == &S_A1);
    CHECKL(strcmp(logbuf,
                  "A2.event.to-a A2.exit A.exit A.enter A1.enter ") == 0);

    return test_report("hsm engine");
}
