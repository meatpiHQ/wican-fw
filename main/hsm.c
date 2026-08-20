#include <string.h>
#include "esp_log.h"
#include "esp_timer.h"
#include "hsm.h"

#define TAG "hsm"

// fill path[0..n-1] with the ancestor chain of s, outermost first; returns n
static int ancestor_path(const sm_state_t *s, const sm_state_t *path[SM_MAX_DEPTH]) {
    int n = 0;
    for (const sm_state_t *p = s; p != NULL; p = p->parent) {
        n++;
    }
    if (n > SM_MAX_DEPTH) {
        ESP_LOGE(TAG, "state %s exceeds SM_MAX_DEPTH", s->name);
        return 0;
    }
    int i = n;
    for (const sm_state_t *p = s; p != NULL; p = p->parent) {
        i--;
        path[i] = p;
    }
    return n;
}

// zero the state's engine-managed context (if any), stamp its entry time,
// and run its enter handler
static void enter_state(sm_t *sm, const sm_state_t *s, int depth) {
    if (s->ctx != NULL) {
        memset(s->ctx, 0, s->ctx_size);
    }
    sm->entered_ts[depth] = sm->now;
    if (s->enter != NULL) {
        s->enter(sm);
    }
}

static void do_transition(sm_t *sm, const sm_state_t *target) {
    const sm_state_t *src_path[SM_MAX_DEPTH];
    const sm_state_t *dst_path[SM_MAX_DEPTH];
    int ns = ancestor_path(sm->current, src_path);
    int nd = ancestor_path(target, dst_path);
    const char *from_name = sm->current->name;

    // depth of the first level where the two paths diverge; everything above
    // it stays active through the transition
    int keep = 0;
    while (keep < ns && keep < nd && src_path[keep] == dst_path[keep]) {
        keep++;
    }
    // external-transition rule: when one state contains the other (including
    // a self-transition), exit and re-enter the inner one instead of no-op
    if ((keep == ns || keep == nd) && keep > 0) {
        keep--;
    }

    for (int i = ns - 1; i >= keep; i--) {
        if (src_path[i]->exit != NULL) {
            src_path[i]->exit(sm);
        }
    }

    for (int i = keep; i < nd; i++) {
        enter_state(sm, dst_path[i], i);
    }

    // descend through default children until we reach a leaf
    const sm_state_t *leaf = target;
    int depth = nd;
    while (leaf->initial != NULL && depth < SM_MAX_DEPTH) {
        leaf = leaf->initial;
        enter_state(sm, leaf, depth);
        depth++;
    }

    sm->current = leaf;
    sm->leaf_ticks = 0;
    sm->generation++;
    ESP_LOGI(sm->tag, "%s -> %s", from_name, leaf->name);
}

// apply any transition requested by a handler; returns true if one happened
// (i.e. the caller should stop dispatching the current tick/event/frame)
static bool apply_if_pending(sm_t *sm) {
    if (sm->pending == NULL) {
        return false;
    }
    // enter/exit handlers may request follow-up transitions (max 8)
    for (int trans_count = 0; sm->pending != NULL && trans_count < 8; trans_count++) {
        const sm_state_t *target = sm->pending;
        sm->pending = NULL;
        sm->entry_arg = sm->pending_arg;
        sm->pending_arg = 0;
        do_transition(sm, target);
    }
    if (sm->pending != NULL) {
        ESP_LOGE(sm->tag, "transition loop; dropping transition to %s", sm->pending->name);
        sm->pending = NULL;
    }
    return true;
}

void sm_init(sm_t *sm, const char *tag, const sm_state_t *initial, const sm_hooks_t *global) {
    memset(sm, 0, sizeof(*sm));
    sm->tag = tag;
    sm->global = global;
    sm->now = esp_timer_get_time();

    // enter the initial state from the root down, then descend to a leaf
    const sm_state_t *path[SM_MAX_DEPTH];
    int n = ancestor_path(initial, path);
    for (int i = 0; i < n; i++) {
        enter_state(sm, path[i], i);
    }
    const sm_state_t *leaf = initial;
    while (leaf->initial != NULL && n < SM_MAX_DEPTH) {
        leaf = leaf->initial;
        enter_state(sm, leaf, n);
        n++;
    }
    sm->current = leaf;
    apply_if_pending(sm);
    ESP_LOGI(sm->tag, "init -> %s", sm->current->name);
}

void sm_tick(sm_t *sm) {
    if (sm->current == NULL) {
        return;
    }
    sm->now = esp_timer_get_time();
    if (sm->global != NULL && sm->global->tick != NULL) {
        // a transition caused by the global hook resolves before the
        // per-state walk, which then runs against the new state
        sm->global->tick(sm);
        apply_if_pending(sm);
    }
    const sm_state_t *path[SM_MAX_DEPTH];
    int n = ancestor_path(sm->current, path);
    uint32_t gen = sm->generation;
    for (int i = 0; i < n; i++) {
        if (path[i]->tick != NULL) {
            path[i]->tick(sm);
            apply_if_pending(sm);
            // any transition (direct or via a nested event) ends the walk;
            // `path` and the remaining handlers belong to the old state
            if (sm->generation != gen) {
                return;
            }
        }
    }
    sm->leaf_ticks++;
}

void sm_rx(sm_t *sm, const twai_message_t *msg, can_bus_t bus) {
    if (sm->current == NULL) {
        return;
    }
    sm->now = esp_timer_get_time();
    if (sm->global != NULL && sm->global->rx != NULL) {
        // a transition caused by the global hook resolves before the
        // per-state walk, which then runs against the new state
        sm->global->rx(sm, msg, bus);
        apply_if_pending(sm);
    }
    uint32_t gen = sm->generation;
    for (const sm_state_t *s = sm->current; s != NULL; s = s->parent) {
        if (s->rx != NULL) {
            s->rx(sm, msg, bus);
            apply_if_pending(sm);
            // any transition (direct or via a nested event) ends the walk;
            // continuing along s->parent would visit exited states
            if (sm->generation != gen) {
                return;
            }
        }
    }
}

fwd_result_t sm_fwd(sm_t *sm, twai_message_t *msg, can_bus_t bus) {
    if (sm->current == NULL) {
        return FWD_PASSTHROUGH;
    }
    sm->now = esp_timer_get_time();
    uint32_t gen = sm->generation;
    fwd_result_t result = FWD_PASSTHROUGH;
    if (sm->global != NULL && sm->global->fwd != NULL) {
        result = sm->global->fwd(sm, msg, bus);
    }
    for (const sm_state_t *s = sm->current; result == FWD_PASSTHROUGH && s != NULL; s = s->parent) {
        if (s->fwd != NULL) {
            result = s->fwd(sm, msg, bus);
        }
    }
    // fwd handlers are pure filters; catch both a directly requested
    // transition and one applied inside a nested sm_send_event
    if (sm->pending != NULL || sm->generation != gen) {
        ESP_LOGW(sm->tag, "transition during fwd dispatch");
        apply_if_pending(sm);
    }
    return result;
}

void sm_send_event(sm_t *sm, sm_event_t ev) {
    if (sm->current == NULL) {
        return;
    }
    sm->now = esp_timer_get_time();
    uint32_t gen = sm->generation;
    for (const sm_state_t *s = sm->current; s != NULL; s = s->parent) {
        if (s->event == NULL) {
            continue;
        }
        bool handled = s->event(sm, ev);
        apply_if_pending(sm);
        // stop on any transition (the handler's own, or one applied inside a
        // recursive sm_send_event) or once a handler claims the event
        if (sm->generation != gen || handled) {
            return;
        }
    }
}

void sm_transition(sm_t *sm, const sm_state_t *target) {
    sm_transition_arg(sm, target, 0);
}

void sm_transition_arg(sm_t *sm, const sm_state_t *target, intptr_t arg) {
    if (sm->pending != NULL && sm->pending != target) {
        ESP_LOGW(sm->tag, "overriding pending transition %s with %s",
                 sm->pending->name, target->name);
    }
    sm->pending = target;
    sm->pending_arg = arg;
}

intptr_t sm_entry_arg(const sm_t *sm) {
    return sm->entry_arg;
}

bool sm_in(const sm_t *sm, const sm_state_t *state) {
    for (const sm_state_t *s = sm->current; s != NULL; s = s->parent) {
        if (s == state) {
            return true;
        }
    }
    return false;
}

int64_t sm_time_in_us(const sm_t *sm, const sm_state_t *state) {
    const sm_state_t *path[SM_MAX_DEPTH];
    int n = ancestor_path(sm->current, path);
    for (int i = 0; i < n; i++) {
        if (path[i] == state) {
            return sm->now - sm->entered_ts[i];
        }
    }
    return 0;
}

uint32_t sm_ticks_in_state(const sm_t *sm) {
    return sm->leaf_ticks;
}

int64_t sm_now(const sm_t *sm) {
    return sm->now;
}
