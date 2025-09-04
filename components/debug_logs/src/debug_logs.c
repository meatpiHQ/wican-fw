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
#include "debug_logs.h"
#include "debug_logs_config.h"

#if DEBUG_LOGS_ENABLE

typedef struct
{
    uint16_t len;
    uint8_t level;
    char line[DEBUG_LOG_MAX_LINE];
} debug_log_msg_t;

static QueueHandle_t s_log_queue = NULL;
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
            printf("[dbg_logs] send fail err=%d total_err=%lu\n", errno, (unsigned long)s_send_err);
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
    debug_log_msg_t msg;
    for(;;)
    {
        if (xQueueReceive(s_log_queue, &msg, portMAX_DELAY) == pdTRUE)
        {
            if (s_network_ready)
            {
                if(s_network_ready())
                {
                    debug_logs_udp_send(msg.line, msg.len);
                }
            }
            else
            {
                debug_logs_udp_send(msg.line, msg.len);
            }
        }
    }
}

void debug_logs_init(bool (*network_ready_fn)(void))
{
    if (s_log_queue)
    {
        return;
    }
    s_log_queue = xQueueCreate(DEBUG_LOGS_QUEUE_LEN, sizeof(debug_log_msg_t));
    if (!s_log_queue)
    {
        return; // allocation failed
    }
    s_network_ready = network_ready_fn;
    memset(&s_dest_addr, 0, sizeof(s_dest_addr));
    s_dest_addr.sin_family = AF_INET;
    s_dest_addr.sin_port = htons(DEBUG_LOGS_UDP_PORT);
    s_dest_addr.sin_addr.s_addr = inet_addr(DEBUG_LOGS_UDP_DEST_IP);
    debug_logs_udp_open();
    xTaskCreatePinnedToCore(debug_logs_task, "dbg_logs", 3072, NULL, 4, NULL, tskNO_AFFINITY);
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
    if (!s_log_queue || !line)
    {
        return false;
    }
    size_t len = strnlen(line, DEBUG_LOG_MAX_LINE - 1);
    debug_log_msg_t msg;
    msg.level = DEBUG_LOG_LEVEL_INFO; // unknown
    if (len >= DEBUG_LOG_MAX_LINE - 1)
    {
        len = DEBUG_LOG_MAX_LINE - 2;
    }
    memcpy(msg.line, line, len);
    if (len == 0 || line[len-1] != '\n')
    {
        msg.line[len++]='\n';
    }
    msg.line[len]='\0';
    msg.len = (uint16_t)len;
    if (xQueueSend(s_log_queue, &msg, 0) != pdTRUE)
    {
        // queue full -> drop
        return false;
    }
    return true;
}

bool debug_logs_send_line_isr(const char *line, BaseType_t *pxHigherPriorityTaskWoken)
{
    if (!s_log_queue || !line)
    {
        return false;
    }
    size_t len = strnlen(line, DEBUG_LOG_MAX_LINE - 1);
    debug_log_msg_t msg;
    msg.level = DEBUG_LOG_LEVEL_INFO;
    if (len >= DEBUG_LOG_MAX_LINE - 1)
    {
        len = DEBUG_LOG_MAX_LINE - 2;
    }
    memcpy(msg.line, line, len);
    if (len == 0 || line[len-1] != '\n')
    {
        msg.line[len++]='\n';
    }
    msg.line[len]='\0';
    msg.len = (uint16_t)len;
    if (xQueueSendFromISR(s_log_queue, &msg, pxHigherPriorityTaskWoken) != pdTRUE)
    {
        return false;
    }
    return true;
}

static void format_prefix(char *buf, size_t buf_sz, debug_log_level_t level, const char *tag)
{
    uint64_t ts = esp_timer_get_time() / 1000ULL; // ms
    const char *task_name = pcTaskGetName(NULL);
    if (!task_name)
    {
        task_name = "?";
    }
    int n = snprintf(buf, buf_sz, "[%llu][%s][%s][%s] ",(unsigned long long)ts, lvl_char(level), task_name, tag?tag:"");
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
    if (!s_log_queue)
    {
        return;
    }

    char line[DEBUG_LOG_MAX_LINE];
    format_prefix(line, sizeof(line), level, tag);
    size_t used = strlen(line);
    if (used >= sizeof(line)-2)
    {
        used = sizeof(line)-2;
    }
    int rem = (int)(sizeof(line)-1 - used);
    int n = vsnprintf(line + used, rem, fmt, args);
    if (n < 0)
    {
        n = 0;
    }
    size_t total = used + (size_t)n;
    if (total >= sizeof(line)-1)
    {
        total = sizeof(line)-2; // leave room for \n and \0
    }
    if (total == 0 || line[total-1] != '\n')
    {
        line[total++]='\n';
    }
    line[total]='\0';

    debug_log_msg_t msg;
    msg.level = (uint8_t)level;
    msg.len = (uint16_t)total;
    memcpy(msg.line, line, total+1);
    (void)xQueueSend(s_log_queue, &msg, 0); // drop if full
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

