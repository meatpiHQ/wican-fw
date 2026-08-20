#ifndef __HSM_H__
#define __HSM_H__

// Small hierarchical state machine.
//
// State model:
//   parent points toward the root; initial points to a composite state's
//   default child. The active state is always a leaf. Trees must be acyclic,
//   initial must name a direct child, and total depth must not exceed
//   SM_MAX_DEPTH. State definitions must remain unchanged, and referenced
//   storage must remain valid while the machine is active. Callbacks may be NULL.
//
// Callback order:
//   tick   global, then root -> leaf
//   rx     global, then leaf -> root
//   event  leaf -> root, until handled or a transition occurs
//   fwd    global, then leaf -> root, until a filter returns a decision
//
// Transitions take effect after the current callback; the latest request wins.
// A transition stops the current per-state walk; one from the global phase
// (direct or via sm_send_event) resolves before the walk begins, so the walk
// runs against the new state. A composite target descends through initial to a
// leaf. Shared ancestors stay active on sibling transitions. Self and ancestor
// transitions exit and re-enter the target. Exit handlers run leaf -> root,
// enter handlers root -> leaf. Enter/exit handlers may request follow-up
// transitions, with a limit of eight per transition chain.
//
// A state's ctx is cleared before that state is entered and preserved while
// transitions remain inside its subtree. sm_transition_arg supplies a value
// for enter handlers; plain sm_transition supplies 0.
//
// The engine is single-task and non-reentrant. Nested sm_send_event is allowed
// only from global tick/rx hooks and tick/rx/event handlers. Enter/exit
// handlers may request transitions but must not start a dispatch. Fwd handlers
// are pure filters and must not trigger transitions. All sm_t fields are
// engine-owned after sm_init.

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include "driver/twai.h"
#include "can.h"

// decision for a single frame about to be forwarded/bridged to a bus
// TODO(ejones): perhaps distinguish between PASSTHROUGH override (active) vs 
// no handling + bubble up?
typedef enum {
    FWD_BLOCK,
    FWD_MODIFIED,
    FWD_PASSTHROUGH,
} fwd_result_t;

// maximum nesting depth of a state (root-level state = depth 1)
#define SM_MAX_DEPTH 4

// event identifiers are defined by each state machine's owner
typedef int sm_event_t;

typedef struct sm sm_t;
typedef struct sm_state sm_state_t;

struct sm_state {
    const char *name;          // required; used in transition logs
    const sm_state_t *parent;  // NULL for root-level states
    const sm_state_t *initial; // composite states: default child; NULL for leaves
    void *ctx;                 // optional writable context zeroed on entry
    size_t ctx_size;
    void (*enter)(sm_t *sm);
    void (*exit)(sm_t *sm);
    void (*tick)(sm_t *sm);
    // return true to stop the event from bubbling to the parent state
    bool (*event)(sm_t *sm, sm_event_t ev);
    void (*rx)(sm_t *sm, const twai_message_t *msg, can_bus_t bus);
    fwd_result_t (*fwd)(sm_t *sm, twai_message_t *msg, can_bus_t bus);
};

// callbacks that run unconditionally, before any per-state dispatch
typedef struct {
    void (*tick)(sm_t *sm);
    void (*rx)(sm_t *sm, const twai_message_t *msg, can_bus_t bus);
    fwd_result_t (*fwd)(sm_t *sm, twai_message_t *msg, can_bus_t bus);
} sm_hooks_t;

struct sm {
    const char *tag;               // machine name for logging
    const sm_state_t *current;     // always a leaf; NULL until sm_init
    const sm_hooks_t *global;
    const sm_state_t *pending;     // transition requested during dispatch
    intptr_t pending_arg;          // entry argument for the pending transition
    intptr_t entry_arg;            // entry argument of the transition being applied
    int64_t now;                   // timestamp of the dispatch in progress
    int64_t entered_ts[SM_MAX_DEPTH]; // entry time per depth (0 = outermost)
    uint32_t leaf_ticks;           // completed tick dispatches since leaf entry
    uint32_t generation;           // detects transitions from nested events
};

// Except for sm_init, all functions require a non-NULL, initialized sm.

// Initialize a fresh machine. tag and initial are required; global is optional.
// Enters initial's ancestors, then follows its initial chain to a leaf.
// Stored pointers must outlive the machine.
void sm_init(sm_t *sm, const char *tag, const sm_state_t *initial, const sm_hooks_t *global);
// Handle one tick.
void sm_tick(sm_t *sm);
// Handle one received frame; msg is required and valid for this call.
void sm_rx(sm_t *sm, const twai_message_t *msg, can_bus_t bus);
// Filter one frame in place; msg is required and writable.
fwd_result_t sm_fwd(sm_t *sm, twai_message_t *msg, can_bus_t bus);
// Handle an event; may be nested from global hooks and tick/rx/event handlers.
// May not be called from enter/exit/fwd.
void sm_send_event(sm_t *sm, sm_event_t ev);
// Request a transition from a callback; target is required. Never call from fwd.
void sm_transition(sm_t *sm, const sm_state_t *target);
// Same, with an argument available to enter handlers through sm_entry_arg.
void sm_transition_arg(sm_t *sm, const sm_state_t *target, intptr_t arg);
// Transition argument; only valid in enter handlers; 0 for sm_init/sm_transition.
intptr_t sm_entry_arg(const sm_t *sm);

// Whether the required state is the current leaf or an ancestor; invalid in enter.
bool sm_in(const sm_t *sm, const sm_state_t *state);
// Time in a required state at the latest captured timestamp; 0 if inactive.
// Not valid during entry.
int64_t sm_time_in_us(const sm_t *sm, const sm_state_t *state);
// Completed ticks in the current leaf; its first tick sees 0. Not valid in enter.
uint32_t sm_ticks_in_state(const sm_t *sm);
// Timestamp captured at initialization or the latest tick, frame, or event.
int64_t sm_now(const sm_t *sm);

#endif
