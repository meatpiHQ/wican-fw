// shared plumbing for host-side tests.
//
// include this exactly once per test program: it defines (not just declares)
// the fake clock and the failure counter. that is safe because each test
// binary is a single test translation unit plus the firmware sources it
// links, none of which include this header.
#ifndef TEST_SUPPORT_H
#define TEST_SUPPORT_H

#include <stdio.h>
#include <stdint.h>

// the fake clock behind the esp_timer.h stub; tests advance it manually
int64_t fake_now = 0;
int64_t esp_timer_get_time(void) { return fake_now; }

static int test_failures = 0;

// CHECK_MSG(cond, fmt, ...): record a failure with file/line and extra
// printf-style context if cond is false. CHECK(cond) is the plain form.
#define CHECK_MSG(cond, ...) do { \
    if (!(cond)) { \
        printf("FAIL %s:%d: %s", __FILE__, __LINE__, #cond); \
        printf("  " __VA_ARGS__); \
        printf("\n"); \
        test_failures++; \
    } \
} while (0)
#define CHECK(cond) CHECK_MSG(cond, "")

// print a one-line verdict and return the process exit code
static int test_report(const char *name) {
    if (test_failures == 0) {
        printf("%s: all checks passed\n", name);
        return 0;
    }
    printf("%s: %d check(s) FAILED\n", name, test_failures);
    return 1;
}

#endif
