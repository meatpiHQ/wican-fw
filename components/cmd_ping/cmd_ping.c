#include "cmd_ping.h"

#include <inttypes.h>
#include <string.h>

#include "argtable3/argtable3.h"
#include "esp_console.h"
#include "esp_err.h"

#include "freertos/FreeRTOS.h"
#include "freertos/event_groups.h"

#include "lwip/netdb.h"
#include "lwip/inet.h"
#include "lwip/ip_addr.h"

#include <ping/ping_sock.h>

static const char *TAG = "cmd_ping";

void cmdline_printf(const char *fmt, ...);

static struct
{
    struct arg_str *host;
    struct arg_int *count;
    struct arg_int *interval;
    struct arg_int *timeout;
    struct arg_end *end;
} ping_args;

typedef struct
{
    EventGroupHandle_t done_evt;
    uint32_t transmitted;
    uint32_t received;
    uint32_t total_time_ms;
} ping_ctx_t;

#define PING_DONE_BIT BIT0

static bool resolve_target(const char *host, ip_addr_t *out)
{
    if (host == NULL || out == NULL)
    {
        return false;
    }

    memset(out, 0, sizeof(*out));

    ip4_addr_t ip4;
    if (ip4addr_aton(host, &ip4))
    {
        IP_ADDR4(out, ip4_addr1(&ip4), ip4_addr2(&ip4), ip4_addr3(&ip4), ip4_addr4(&ip4));
        return true;
    }

    struct addrinfo hint;
    memset(&hint, 0, sizeof(hint));
    hint.ai_family = AF_INET;

    struct addrinfo *res = NULL;
    if (lwip_getaddrinfo(host, NULL, &hint, &res) != 0 || res == NULL)
    {
        return false;
    }

    struct sockaddr_in *sa = (struct sockaddr_in *)res->ai_addr;
    if (sa != NULL)
    {
        ip4_addr_t ip;
        ip.addr = sa->sin_addr.s_addr;
        IP_ADDR4(out, ip4_addr1(&ip), ip4_addr2(&ip), ip4_addr3(&ip), ip4_addr4(&ip));
        lwip_freeaddrinfo(res);
        return true;
    }

    lwip_freeaddrinfo(res);
    return false;
}

static void on_ping_success(esp_ping_handle_t hdl, void *args)
{
    ping_ctx_t *ctx = (ping_ctx_t *)args;

    uint16_t seqno = 0;
    uint8_t ttl = 0;
    uint32_t elapsed_time = 0;
    uint32_t recv_len = 0;
    ip_addr_t target_addr;

    esp_ping_get_profile(hdl, ESP_PING_PROF_SEQNO, &seqno, sizeof(seqno));
    esp_ping_get_profile(hdl, ESP_PING_PROF_TTL, &ttl, sizeof(ttl));
    esp_ping_get_profile(hdl, ESP_PING_PROF_IPADDR, &target_addr, sizeof(target_addr));
    esp_ping_get_profile(hdl, ESP_PING_PROF_SIZE, &recv_len, sizeof(recv_len));
    esp_ping_get_profile(hdl, ESP_PING_PROF_TIMEGAP, &elapsed_time, sizeof(elapsed_time));

    (void)TAG;
    cmdline_printf("%" PRIu32 " bytes from %s icmp_seq=%" PRIu16 " ttl=%" PRIu8 " time=%" PRIu32 " ms\n",
                  recv_len,
                  ipaddr_ntoa(&target_addr),
                  seqno,
                  ttl,
                  elapsed_time);

    if (ctx != NULL)
    {
        ctx->received++;
    }
}

static void on_ping_timeout(esp_ping_handle_t hdl, void *args)
{
    ping_ctx_t *ctx = (ping_ctx_t *)args;

    uint16_t seqno = 0;
    ip_addr_t target_addr;

    esp_ping_get_profile(hdl, ESP_PING_PROF_SEQNO, &seqno, sizeof(seqno));
    esp_ping_get_profile(hdl, ESP_PING_PROF_IPADDR, &target_addr, sizeof(target_addr));

    cmdline_printf("From %s icmp_seq=%" PRIu16 " timeout\n", ipaddr_ntoa(&target_addr), seqno);

    (void)ctx;
}

static void on_ping_end(esp_ping_handle_t hdl, void *args)
{
    ping_ctx_t *ctx = (ping_ctx_t *)args;

    uint32_t transmitted = 0;
    uint32_t received = 0;
    uint32_t total_time_ms = 0;

    esp_ping_get_profile(hdl, ESP_PING_PROF_REQUEST, &transmitted, sizeof(transmitted));
    esp_ping_get_profile(hdl, ESP_PING_PROF_REPLY, &received, sizeof(received));
    esp_ping_get_profile(hdl, ESP_PING_PROF_DURATION, &total_time_ms, sizeof(total_time_ms));

    cmdline_printf("--- ping statistics ---\n");
    cmdline_printf("%" PRIu32 " packets transmitted, %" PRIu32 " received, time %" PRIu32 "ms\n",
                  transmitted,
                  received,
                  total_time_ms);

    if (ctx != NULL)
    {
        ctx->transmitted = transmitted;
        ctx->received = received;
        ctx->total_time_ms = total_time_ms;
        xEventGroupSetBits(ctx->done_evt, PING_DONE_BIT);
    }

    esp_ping_delete_session(hdl);
}

static int cmd_ping(int argc, char **argv)
{
    int nerrors = arg_parse(argc, argv, (void **)&ping_args);
    if (nerrors != 0)
    {
        arg_print_errors(stderr, ping_args.end, argv[0]);
        return 1;
    }

    const char *host = NULL;
    if (ping_args.host->count > 0)
    {
        host = ping_args.host->sval[0];
    }

    if (host == NULL || host[0] == '\0')
    {
        cmdline_printf("Usage: ping <host> [-c <count>] [-i <interval_ms>] [-t <timeout_ms>]\n");
        return 1;
    }

    int count = 4;
    int interval_ms = 1000;
    int timeout_ms = 1000;

    if (ping_args.count->count > 0)
    {
        count = ping_args.count->ival[0];
    }
    if (ping_args.interval->count > 0)
    {
        interval_ms = ping_args.interval->ival[0];
    }
    if (ping_args.timeout->count > 0)
    {
        timeout_ms = ping_args.timeout->ival[0];
    }

    if (count <= 0)
    {
        cmdline_printf("Error: count must be > 0\n");
        return 1;
    }

    if (interval_ms < 10)
    {
        interval_ms = 10;
    }

    if (timeout_ms < 10)
    {
        timeout_ms = 10;
    }

    ip_addr_t target_addr;
    if (!resolve_target(host, &target_addr))
    {
        cmdline_printf("Error: could not resolve '%s'\n", host);
        return 1;
    }

    cmdline_printf("PING %s (%s): %d packets\n", host, ipaddr_ntoa(&target_addr), count);

    esp_ping_config_t ping_config = ESP_PING_DEFAULT_CONFIG();
    ping_config.target_addr = target_addr;
    ping_config.count = (uint32_t)count;
    ping_config.interval_ms = (uint32_t)interval_ms;
    ping_config.timeout_ms = (uint32_t)timeout_ms;

    ping_ctx_t ctx;
    memset(&ctx, 0, sizeof(ctx));
    ctx.done_evt = xEventGroupCreate();
    if (ctx.done_evt == NULL)
    {
        cmdline_printf("Error: no memory\n");
        return 1;
    }

    esp_ping_callbacks_t cbs;
    memset(&cbs, 0, sizeof(cbs));
    cbs.on_ping_success = on_ping_success;
    cbs.on_ping_timeout = on_ping_timeout;
    cbs.on_ping_end = on_ping_end;
    cbs.cb_args = &ctx;

    esp_ping_handle_t ping = NULL;
    esp_err_t err = esp_ping_new_session(&ping_config, &cbs, &ping);
    if (err != ESP_OK)
    {
        vEventGroupDelete(ctx.done_evt);
        cmdline_printf("Error: esp_ping_new_session failed: %s\n", esp_err_to_name(err));
        return 1;
    }

    esp_ping_start(ping);

    xEventGroupWaitBits(ctx.done_evt, PING_DONE_BIT, pdTRUE, pdTRUE, portMAX_DELAY);
    vEventGroupDelete(ctx.done_evt);

    return 0;
}

esp_err_t cmd_ping_register(void)
{
    ping_args.host = arg_str1(NULL, NULL, "<host>", "IPv4 address or hostname");
    ping_args.count = arg_int0("c", "count", "<count>", "Number of packets (default: 4)");
    ping_args.interval = arg_int0("i", "interval", "<ms>", "Interval between packets (default: 1000)");
    ping_args.timeout = arg_int0("t", "timeout", "<ms>", "Per-packet timeout (default: 1000)");
    ping_args.end = arg_end(5);

    const esp_console_cmd_t cmd = {
        .command = "ping",
        .help = "Ping an IPv4 host",
        .hint = "Usage: ping <host> [-c N] [-i ms] [-t ms]",
        .func = &cmd_ping,
        .argtable = &ping_args,
    };

    return esp_console_cmd_register(&cmd);
}
