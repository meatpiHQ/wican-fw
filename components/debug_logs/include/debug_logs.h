#pragma once

#include <stdarg.h>
#include <stdint.h>
#include <stdbool.h>
#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"

#ifdef __cplusplus
extern "C" {
#endif

typedef enum {
    DEBUG_LOG_LEVEL_ERROR = 0,
    DEBUG_LOG_LEVEL_WARN  = 1,
    DEBUG_LOG_LEVEL_INFO  = 2,
    DEBUG_LOG_LEVEL_DEBUG = 3,
    DEBUG_LOG_LEVEL_VERBOSE = 4,
} debug_log_level_t;

// Initialize debug logs component.
// network_ready_fn: optional callback returning true when network is ready for UDP sends (can be NULL)
// use_psram_stack: if true and static allocation is supported, attempts to place the task stack in PSRAM.
//                  Falls back to normal dynamic allocation if PSRAM alloc fails or static allocation disabled.
void debug_logs_init(bool (*network_ready_fn)(void), bool use_psram_stack);
void debug_logs_set_level(debug_log_level_t level);
debug_log_level_t debug_logs_get_level(void);

// Core logging API: formatted log with tag
void debug_logs_log(debug_log_level_t level, const char *tag, const char *fmt, ...);
void debug_logs_vlog(debug_log_level_t level, const char *tag, const char *fmt, va_list args);

// Lightweight raw message enqueue (already formatted line)
bool debug_logs_send_line(const char *line); // returns false if dropped

// Raw ASCII payload enqueue (sent exactly as provided as UDP payload; no prefix/newline added)
bool debug_logs_send_raw(const char *data, size_t len); // returns false if dropped/partially dropped

// ISR-safe variant (will attempt to enqueue from ISR)
bool debug_logs_send_line_isr(const char *line, BaseType_t *pxHigherPriorityTaskWoken);

// ISR-safe raw ASCII payload enqueue (sent exactly as provided as UDP payload; no prefix/newline added)
bool debug_logs_send_raw_isr(const char *data, size_t len, BaseType_t *pxHigherPriorityTaskWoken);

// Optional runtime destination override (IPv4 string + port). Returns 0 on success.
int debug_logs_set_udp_destination(const char *ipv4, uint16_t port);

// Set or replace network readiness callback after init (optional)
void debug_logs_set_network_ready(bool (*network_ready_fn)(void));

// Macros for convenience (avoid evaluation if level filtered)
#define DEBUG_LOGE(tag, fmt, ...) do { if (debug_logs_get_level() >= DEBUG_LOG_LEVEL_ERROR) debug_logs_log(DEBUG_LOG_LEVEL_ERROR, tag, fmt, ##__VA_ARGS__); } while(0)
#define DEBUG_LOGW(tag, fmt, ...) do { if (debug_logs_get_level() >= DEBUG_LOG_LEVEL_WARN)  debug_logs_log(DEBUG_LOG_LEVEL_WARN,  tag, fmt, ##__VA_ARGS__); } while(0)
#define DEBUG_LOGI(tag, fmt, ...) do { if (debug_logs_get_level() >= DEBUG_LOG_LEVEL_INFO)  debug_logs_log(DEBUG_LOG_LEVEL_INFO,  tag, fmt, ##__VA_ARGS__); } while(0)
#define DEBUG_LOGD(tag, fmt, ...) do { if (debug_logs_get_level() >= DEBUG_LOG_LEVEL_DEBUG) debug_logs_log(DEBUG_LOG_LEVEL_DEBUG, tag, fmt, ##__VA_ARGS__); } while(0)
#define DEBUG_LOGV(tag, fmt, ...) do { if (debug_logs_get_level() >= DEBUG_LOG_LEVEL_VERBOSE) debug_logs_log(DEBUG_LOG_LEVEL_VERBOSE, tag, fmt, ##__VA_ARGS__); } while(0)

#ifdef __cplusplus
}
#endif
