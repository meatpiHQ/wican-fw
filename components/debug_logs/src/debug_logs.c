// Debug logs component implementation (UDP transport)
#include <stdio.h>
#include <string.h>
#include <stdlib.h>
#include <stdarg.h>
#include <errno.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/queue.h"
#include "lwip/sockets.h"
#include "lwip/inet.h"
#include "esp_timer.h"
#include "esp_heap_caps.h"
#include "debug_logs.h"
#include "debug_logs_config.h"

#if DEBUG_LOGS_ENABLE

typedef struct
{
    uint16_t len;
    uint8_t level;
    char line[DEBUG_LOG_MAX_LINE];
} debug_log_msg_t;

// NOTE:
// DEBUG_LOG_MAX_LINE is large (default 10KB). Do NOT place debug_log_msg_t on the caller's stack.
// To avoid stack overflows/corruption (which can manifest as Cache/MMU faults), we use a fixed
// message pool and pass indices through small queues.
static QueueHandle_t s_free_queue = NULL;
static QueueHandle_t s_pending_queue = NULL;

// Log task handle for wakeups (normal + ISR)
static TaskHandle_t s_dbg_task_handle = NULL;

// Normal (task-context) pool + index queues
#if ( configSUPPORT_STATIC_ALLOCATION == 1 )
static StaticQueue_t s_free_queue_struct;
static StaticQueue_t s_pending_queue_struct;
static uint8_t s_free_queue_storage[DEBUG_LOGS_QUEUE_LEN * sizeof(uint8_t)];
static uint8_t s_pending_queue_storage[DEBUG_LOGS_QUEUE_LEN * sizeof(uint8_t)];
static debug_log_msg_t *s_pool = NULL; // allocated at init (prefer PSRAM)
#endif

// ISR-safe staging: do not access PSRAM from ISR context.
// ISR copies into a small internal pool and notifies the log task.
#ifndef DEBUG_LOGS_ISR_POOL_LEN
#define DEBUG_LOGS_ISR_POOL_LEN 8
#endif

#if ( configSUPPORT_STATIC_ALLOCATION == 1 ) && ( DEBUG_LOGS_ISR_POOL_LEN > 0 )
static QueueHandle_t s_isr_free_queue = NULL;
static QueueHandle_t s_isr_pending_queue = NULL;
static StaticQueue_t s_isr_free_queue_struct;
static StaticQueue_t s_isr_pending_queue_struct;
static uint8_t s_isr_free_queue_storage[DEBUG_LOGS_ISR_POOL_LEN * sizeof(uint8_t)];
static uint8_t s_isr_pending_queue_storage[DEBUG_LOGS_ISR_POOL_LEN * sizeof(uint8_t)];
static debug_log_msg_t s_isr_pool[DEBUG_LOGS_ISR_POOL_LEN];
#endif
static int s_udp_sock = -1;
static struct sockaddr_in s_dest_addr;
static debug_log_level_t s_current_level = (debug_log_level_t)DEBUG_LOGS_LEVEL;
static uint32_t s_send_ok = 0;
static uint32_t s_send_err = 0;
static bool (*s_network_ready)(void) = NULL;

static void debug_logs_udp_open(void)
{
    if (s_udp_sock >= 0)
    {
        return;
    }
    s_udp_sock = socket(AF_INET, SOCK_DGRAM, IPPROTO_IP);
    if (s_udp_sock < 0)
    {
        return; // silent fail; retry later
    }
    // Enable broadcast always; harmless if unicast
    int br = 1;
    setsockopt(s_udp_sock, SOL_SOCKET, SO_BROADCAST, &br, sizeof(br));
}

static void debug_logs_udp_send(const char *data, size_t len)
{
    if (s_udp_sock < 0)
    {
        debug_logs_udp_open();
    }
    if (s_udp_sock < 0)
    {
        return;
    }
    if (len == 0)
    {
        return;
    }
    // best effort
    ssize_t r = sendto(s_udp_sock, data, len, 0, (struct sockaddr*)&s_dest_addr, sizeof(s_dest_addr));
    if (r < 0)
    {
        s_send_err++;
        if (s_send_err < 8 || (s_send_err & 0xFF) == 0)
        {
            // Lightweight diagnostic to UART so user sees failures
            // printf("[dbg_logs] send fail err=%d total_err=%lu\n", errno, (unsigned long)s_send_err);
        }
    }
    else
    {
        s_send_ok++;
    }
}

static const char *lvl_char(uint8_t lvl)
{
    switch(lvl)
    {
        case DEBUG_LOG_LEVEL_ERROR: return "E";
        case DEBUG_LOG_LEVEL_WARN: return "W";
        case DEBUG_LOG_LEVEL_INFO: return "I";
        case DEBUG_LOG_LEVEL_DEBUG: return "D";
        case DEBUG_LOG_LEVEL_VERBOSE: return "V";
        default: return "?";
    }
}

static void debug_logs_task(void *arg)
{
    for(;;)
    {
        // Sleep until there is something to do (woken by task notify)
        (void)ulTaskNotifyTake(pdTRUE, portMAX_DELAY);

#if ( configSUPPORT_STATIC_ALLOCATION == 1 )
        uint8_t idx;
#endif

#if ( configSUPPORT_STATIC_ALLOCATION == 1 ) && ( DEBUG_LOGS_ISR_POOL_LEN > 0 )
        // Drain ISR-staged messages first
        while (s_isr_pending_queue && xQueueReceive(s_isr_pending_queue, &idx, 0) == pdTRUE)
        {
            if (idx < DEBUG_LOGS_ISR_POOL_LEN)
            {
                debug_log_msg_t *p = &s_isr_pool[idx];
                if (s_network_ready)
                {
                    if (s_network_ready())
                    {
                        debug_logs_udp_send(p->line, p->len);
                    }
                }
                else
                {
                    debug_logs_udp_send(p->line, p->len);
                }
            }
            // Return buffer to free pool
            if (s_isr_free_queue)
            {
                (void)xQueueSend(s_isr_free_queue, &idx, 0);
            }
        }
#endif

        // Drain normal (task-context) pool
#if ( configSUPPORT_STATIC_ALLOCATION == 1 )
        while (s_pending_queue && xQueueReceive(s_pending_queue, &idx, 0) == pdTRUE)
        {
            if (s_pool && idx < DEBUG_LOGS_QUEUE_LEN)
            {
                debug_log_msg_t *p = &s_pool[idx];
                if (s_network_ready)
                {
                    if (s_network_ready())
                    {
                        debug_logs_udp_send(p->line, p->len);
                    }
                }
                else
                {
                    debug_logs_udp_send(p->line, p->len);
                }
            }

            if (s_free_queue)
            {
                (void)xQueueSend(s_free_queue, &idx, 0);
            }
        }
#endif
    }
}

// Internal storage for optional static task (TCB always in internal RAM BSS; stack may be in PSRAM)
#if ( configSUPPORT_STATIC_ALLOCATION == 1 )
static StaticTask_t s_dbg_task_tcb;
static StackType_t *s_dbg_task_stack = NULL; // allocated at init if PSRAM requested
#endif

void debug_logs_init(bool (*network_ready_fn)(void), bool use_psram_stack)
{
    if (s_pending_queue)
    {
        return;
    }

#if ( configSUPPORT_STATIC_ALLOCATION == 1 )
    // Allocate pool (prefer PSRAM). This is where the large 10KB messages live.
    if (!s_pool)
    {
        const size_t pool_bytes = (size_t)DEBUG_LOGS_QUEUE_LEN * sizeof(debug_log_msg_t);
        s_pool = (debug_log_msg_t*)heap_caps_malloc(pool_bytes, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
        if (!s_pool)
        {
            s_pool = (debug_log_msg_t*)heap_caps_malloc(pool_bytes, MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT);
        }
        if (s_pool)
        {
            memset(s_pool, 0, pool_bytes);
        }
    }
    if (!s_pool)
    {
        return; // allocation failed
    }

    // Create index queues in internal RAM
    s_free_queue = xQueueCreateStatic(DEBUG_LOGS_QUEUE_LEN, sizeof(uint8_t), s_free_queue_storage, &s_free_queue_struct);
    s_pending_queue = xQueueCreateStatic(DEBUG_LOGS_QUEUE_LEN, sizeof(uint8_t), s_pending_queue_storage, &s_pending_queue_struct);
    if (!s_free_queue || !s_pending_queue)
    {
        return;
    }

    for (uint8_t i = 0; i < (uint8_t)DEBUG_LOGS_QUEUE_LEN; i++)
    {
        (void)xQueueSend(s_free_queue, &i, 0);
    }
#endif

#if ( configSUPPORT_STATIC_ALLOCATION == 1 ) && ( DEBUG_LOGS_ISR_POOL_LEN > 0 )
    // Create ISR staging queues in internal RAM
    if (!s_isr_free_queue)
    {
        s_isr_free_queue = xQueueCreateStatic(DEBUG_LOGS_ISR_POOL_LEN, sizeof(uint8_t), s_isr_free_queue_storage, &s_isr_free_queue_struct);
        s_isr_pending_queue = xQueueCreateStatic(DEBUG_LOGS_ISR_POOL_LEN, sizeof(uint8_t), s_isr_pending_queue_storage, &s_isr_pending_queue_struct);
        if (s_isr_free_queue && s_isr_pending_queue)
        {
            for (uint8_t i = 0; i < (uint8_t)DEBUG_LOGS_ISR_POOL_LEN; i++)
            {
                (void)xQueueSend(s_isr_free_queue, &i, 0);
            }
        }
        else
        {
            // If staging init failed, disable ISR path (it will safely drop)
            s_isr_free_queue = NULL;
            s_isr_pending_queue = NULL;
        }
    }
#endif

    s_network_ready = network_ready_fn;
    memset(&s_dest_addr, 0, sizeof(s_dest_addr));
    s_dest_addr.sin_family = AF_INET;
    s_dest_addr.sin_port = htons(DEBUG_LOGS_UDP_PORT);
    s_dest_addr.sin_addr.s_addr = inet_addr(DEBUG_LOGS_UDP_DEST_IP);
    debug_logs_udp_open();

    const uint32_t stack_words = (40*1024); // original size

#if ( configSUPPORT_STATIC_ALLOCATION == 1 )
    if (use_psram_stack)
    {
        // Allocate stack in PSRAM (8-bit accessible). If allocation fails, fall back.
        if (!s_dbg_task_stack)
        {
            s_dbg_task_stack = (StackType_t*)heap_caps_malloc(stack_words * sizeof(StackType_t), MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
        }
        if (s_dbg_task_stack)
        {
            TaskHandle_t h = xTaskCreateStaticPinnedToCore(
                debug_logs_task,
                "dbg_logs",
                stack_words,
                NULL,
                4,
                s_dbg_task_stack,
                &s_dbg_task_tcb,
                tskNO_AFFINITY);
            if (h)
            {
                s_dbg_task_handle = h;
                return; // success
            }
            // If creation failed, free and fall back
            heap_caps_free(s_dbg_task_stack);
            s_dbg_task_stack = NULL;
        }
    }
#endif

    // Fallback: normal dynamic allocation (internal RAM)
    (void)xTaskCreatePinnedToCore(debug_logs_task, "dbg_logs", stack_words, NULL, 4, &s_dbg_task_handle, tskNO_AFFINITY);
}

void debug_logs_set_level(debug_log_level_t level)
{
    s_current_level = level;
}

debug_log_level_t debug_logs_get_level(void)
{
    return s_current_level;
}

bool debug_logs_send_line(const char *line)
{
    if (!s_pending_queue || !s_free_queue || !line)
    {
        return false;
    }

#if ( configSUPPORT_STATIC_ALLOCATION == 1 )
    uint8_t idx;
    if (xQueueReceive(s_free_queue, &idx, 0) != pdTRUE)
    {
        return false; // pool exhausted -> drop
    }
    if (!s_pool || idx >= DEBUG_LOGS_QUEUE_LEN)
    {
        return false;
    }
    debug_log_msg_t *msg = &s_pool[idx];
    size_t len = strnlen(line, DEBUG_LOG_MAX_LINE - 1);
    msg->level = DEBUG_LOG_LEVEL_INFO; // unknown
    if (len >= DEBUG_LOG_MAX_LINE - 1)
    {
        len = DEBUG_LOG_MAX_LINE - 2;
    }
    memcpy(msg->line, line, len);
    if (len == 0 || line[len-1] != '\n')
    {
        msg->line[len++]='\n';
    }
    msg->line[len]='\0';
    msg->len = (uint16_t)len;

    if (xQueueSend(s_pending_queue, &idx, 0) != pdTRUE)
    {
        // pending full (shouldn't happen if sizes match), return slot
        (void)xQueueSend(s_free_queue, &idx, 0);
        return false;
    }
#else
    return false;
#endif
    if (s_dbg_task_handle)
    {
        xTaskNotifyGive(s_dbg_task_handle);
    }
    return true;
}

bool debug_logs_send_raw(const char *data, size_t len)
{
    if (!s_pending_queue || !s_free_queue || !data || len == 0)
    {
        return false;
    }

    const uint8_t *p = (const uint8_t*)data;
    bool ok = true;

    while (len > 0)
    {
        size_t chunk = len;
        if (chunk > DEBUG_LOG_MAX_LINE)
        {
            chunk = DEBUG_LOG_MAX_LINE;
        }

#if ( configSUPPORT_STATIC_ALLOCATION == 1 )
        uint8_t idx;
        if (xQueueReceive(s_free_queue, &idx, 0) != pdTRUE)
        {
            ok = false;
            break;
        }
        if (!s_pool || idx >= DEBUG_LOGS_QUEUE_LEN)
        {
            ok = false;
            break;
        }
        debug_log_msg_t *msg = &s_pool[idx];
        msg->level = DEBUG_LOG_LEVEL_INFO; // unknown
        msg->len = (uint16_t)chunk;
        memcpy(msg->line, p, chunk);

        if (xQueueSend(s_pending_queue, &idx, 0) != pdTRUE)
        {
            (void)xQueueSend(s_free_queue, &idx, 0);
            ok = false;
            break;
        }
#else
        ok = false;
        break;
#endif

        if (s_dbg_task_handle)
        {
            xTaskNotifyGive(s_dbg_task_handle);
        }

        p += chunk;
        len -= chunk;
    }

    return ok;
}

bool debug_logs_send_line_isr(const char *line, BaseType_t *pxHigherPriorityTaskWoken)
{
    if (!s_pending_queue || !line)
    {
        return false;
    }

#if ( configSUPPORT_STATIC_ALLOCATION == 1 ) && ( DEBUG_LOGS_ISR_POOL_LEN > 0 )
    // Stage into internal RAM pool to avoid PSRAM access from ISR
    if (!s_isr_free_queue || !s_isr_pending_queue)
    {
        return false;
    }

    uint8_t idx;
    if (xQueueReceiveFromISR(s_isr_free_queue, &idx, pxHigherPriorityTaskWoken) != pdTRUE)
    {
        return false; // no free buffer
    }

    size_t len = strnlen(line, DEBUG_LOG_MAX_LINE - 1);
    debug_log_msg_t *msg = &s_isr_pool[idx];
    msg->level = DEBUG_LOG_LEVEL_INFO;
    if (len >= DEBUG_LOG_MAX_LINE - 1)
    {
        len = DEBUG_LOG_MAX_LINE - 2;
    }
    memcpy(msg->line, line, len);
    if (len == 0 || line[len-1] != '\n')
    {
        msg->line[len++]='\n';
    }
    msg->line[len]='\0';
    msg->len = (uint16_t)len;

    if (xQueueSendFromISR(s_isr_pending_queue, &idx, pxHigherPriorityTaskWoken) != pdTRUE)
    {
        // Return buffer on failure
        (void)xQueueSendFromISR(s_isr_free_queue, &idx, pxHigherPriorityTaskWoken);
        return false;
    }
    if (s_dbg_task_handle)
    {
        vTaskNotifyGiveFromISR(s_dbg_task_handle, pxHigherPriorityTaskWoken);
    }
    return true;
#else
    (void)pxHigherPriorityTaskWoken;
    return false;
#endif
}

bool debug_logs_send_raw_isr(const char *data, size_t len, BaseType_t *pxHigherPriorityTaskWoken)
{
    if (!s_pending_queue || !data || len == 0)
    {
        return false;
    }

#if ( configSUPPORT_STATIC_ALLOCATION == 1 ) && ( DEBUG_LOGS_ISR_POOL_LEN > 0 )
    if (!s_isr_free_queue || !s_isr_pending_queue)
    {
        return false;
    }

    const uint8_t *p = (const uint8_t*)data;
    bool ok = true;

    while (len > 0)
    {
        size_t chunk = len;
        if (chunk > DEBUG_LOG_MAX_LINE)
        {
            chunk = DEBUG_LOG_MAX_LINE;
        }

        uint8_t idx;
        if (xQueueReceiveFromISR(s_isr_free_queue, &idx, pxHigherPriorityTaskWoken) != pdTRUE)
        {
            ok = false;
            break;
        }

        debug_log_msg_t *msg = &s_isr_pool[idx];
        msg->level = DEBUG_LOG_LEVEL_INFO;
        msg->len = (uint16_t)chunk;
        memcpy(msg->line, p, chunk);

        if (xQueueSendFromISR(s_isr_pending_queue, &idx, pxHigherPriorityTaskWoken) != pdTRUE)
        {
            (void)xQueueSendFromISR(s_isr_free_queue, &idx, pxHigherPriorityTaskWoken);
            ok = false;
            break;
        }

        if (s_dbg_task_handle)
        {
            vTaskNotifyGiveFromISR(s_dbg_task_handle, pxHigherPriorityTaskWoken);
        }

        p += chunk;
        len -= chunk;
    }

    return ok;
#else
    (void)pxHigherPriorityTaskWoken;
    return false;
#endif
}

static void format_prefix(char *buf, size_t buf_sz, debug_log_level_t level, const char *tag)
{
    uint64_t ts = esp_timer_get_time() / 1000ULL; // ms
    const char *task_name = pcTaskGetName(NULL);
    if (!task_name)
    {
        task_name = "?";
    }
    // Fixed-width fields for prettier aligned logs
    // - timestamp: right-aligned width 10
    // - task/tag: left-aligned, truncated/padded to 12
    int n = snprintf(buf, buf_sz, "[%10llu][%s][%-12.12s][%-12.12s] ",
                     (unsigned long long)ts,
                     lvl_char(level),
                     task_name,
                     tag ? tag : "");
    if (n < 0)
    {
        n = 0;
    }
    if ((size_t)n >= buf_sz)
    {
        buf[buf_sz-1]='\0';
    }
}

void debug_logs_vlog(debug_log_level_t level, const char *tag, const char *fmt, va_list args)
{
    if (level > s_current_level)
    {
        return;
    }
    if (!s_pending_queue || !s_free_queue)
    {
        return;
    }

#if ( configSUPPORT_STATIC_ALLOCATION == 1 )
    uint8_t idx;
    if (xQueueReceive(s_free_queue, &idx, 0) != pdTRUE)
    {
        return; // pool exhausted -> drop
    }
    if (!s_pool || idx >= DEBUG_LOGS_QUEUE_LEN)
    {
        return;
    }
    debug_log_msg_t *msg = &s_pool[idx];
    msg->level = (uint8_t)level;

    format_prefix(msg->line, sizeof(msg->line), level, tag);
    size_t used = strlen(msg->line);
    if (used >= sizeof(msg->line)-2)
    {
        used = sizeof(msg->line)-2;
    }
    int rem = (int)(sizeof(msg->line)-1 - used);
    int n = vsnprintf(msg->line + used, rem, fmt, args);
    if (n < 0)
    {
        n = 0;
    }
    size_t total = used + (size_t)n;
    if (total >= sizeof(msg->line)-1)
    {
        total = sizeof(msg->line)-2;
    }
    if (total == 0 || msg->line[total-1] != '\n')
    {
        msg->line[total++]='\n';
    }
    msg->line[total]='\0';
    msg->len = (uint16_t)total;

    if (xQueueSend(s_pending_queue, &idx, 0) == pdTRUE)
    {
        if (s_dbg_task_handle)
        {
            xTaskNotifyGive(s_dbg_task_handle);
        }
    }
    else
    {
        (void)xQueueSend(s_free_queue, &idx, 0);
    }
#endif
}

void debug_logs_log(debug_log_level_t level, const char *tag, const char *fmt, ...)
{
    va_list ap;
    va_start(ap, fmt);
    debug_logs_vlog(level, tag, fmt, ap);
    va_end(ap);
}

int debug_logs_set_udp_destination(const char *ipv4, uint16_t port)
{
    if (!ipv4)
    {
        return -1;
    }
    uint32_t addr = inet_addr(ipv4);
    if (addr == INADDR_NONE)
    {
        return -2;
    }
    s_dest_addr.sin_addr.s_addr = addr;
    s_dest_addr.sin_port = htons(port);
    return 0;
}

// Optional stats for troubleshooting
void debug_logs_get_stats(uint32_t *ok, uint32_t *err)
{
    if (ok)
    {
        *ok = s_send_ok;
    }
    if (err)
    {
        *err = s_send_err;
    }
}

void debug_logs_set_network_ready(bool (*network_ready_fn)(void))
{
    s_network_ready = network_ready_fn;
}

#endif // DEBUG_LOGS_ENABLE

