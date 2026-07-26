/**
 * @file main_bench.c
 * @brief Composition glue: the MQTT bench surface (BENCHMARKS.md rows are
 *        measured through this). Driven from the bench host over MQTT —
 *        which also exercises the handler registry end to end:
 *
 *  - `<prefix>/bench/cmd`  {"n":1000,"size":512[,"gap_ms":5]}
 *        → the bench task blasts n payloads of size bytes to
 *          `<prefix>/bench/out` via publish_async (optional pacing),
 *          then publishes {"sent","ok","dropped_full","ms"} to
 *          `<prefix>/bench/result`.
 *  - `<prefix>/bench/echo` → payload bounced to `<prefix>/bench/echo_re`
 *        straight from the handler (RTT measurement; also proves
 *        publish_async is safe from the esp-mqtt event task).
 *
 * Trust model matches the rest of v1 (AP-mode/bench): anyone on the
 * broker can make the device publish test data. Payloads are inert.
 */
#include <stdio.h>
#include <string.h>

#include "esp_attr.h"
#include "esp_heap_caps.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"
#include "freertos/task.h"

#include "cJSON.h"

#include "http_client_manager.h"
#include "mqtt_manager.h"

#include "main_bench.h"

static const char *TAG = "main";

#define BENCH_MAX_N    20000
#define BENCH_MAX_SIZE 4000

typedef struct
{
    uint32_t n;
    uint32_t size;
    uint32_t gap_ms;
    char url[128];  /* set = an http_client_manager exercise instead   */
    char save[64];  /* with url: download to this path instead of GET  */
    char cert_set[25]; /* with url: TLS verification via cert_manager  */
} bench_cmd_t;

static QueueHandle_t s_cmd_q;
static StaticQueue_t s_cmd_q_buf;  /* internal: FreeRTOS object */
static uint8_t s_cmd_q_store[1 * sizeof(bench_cmd_t)];
static TaskHandle_t s_task;
static StaticTask_t s_tcb;         /* internal: FreeRTOS object */
static uint8_t s_payload[BENCH_MAX_SIZE] EXT_RAM_BSS_ATTR;

/* The bench task's 8 KB INTERNAL stack (download() ends in flash
   writes, TLS handshakes run on it — §2 corollary) is allocated on the
   FIRST bench command: a dev surface must not tax production boots. */
#define BENCH_STACK_BYTES 8192

static void bench_task(void *arg);

static void ensure_bench_task(void)
{
    if (s_task != NULL)
    {
        return;
    }

    StackType_t *stack = heap_caps_malloc(
        BENCH_STACK_BYTES * sizeof(StackType_t),
        MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT);

    if (stack == NULL)
    {
        ESP_LOGE(TAG, "bench stack alloc failed");
        return;
    }

    s_task = xTaskCreateStatic(bench_task, "mqtt_bench",
                               BENCH_STACK_BYTES, NULL, 3, stack, &s_tcb);
}

static void on_cmd(const char *topic, const uint8_t *data, size_t len,
                   void *arg)
{
    (void)topic;
    (void)arg;

    char buf[96];

    if (len >= sizeof(buf))
    {
        return;
    }

    memcpy(buf, data, len);
    buf[len] = '\0';

    cJSON *root = cJSON_Parse(buf);
    const cJSON *n = cJSON_GetObjectItemCaseSensitive(root, "n");
    const cJSON *size = cJSON_GetObjectItemCaseSensitive(root, "size");
    const cJSON *gap = cJSON_GetObjectItemCaseSensitive(root, "gap_ms");
    const cJSON *url = cJSON_GetObjectItemCaseSensitive(root, "url");
    const cJSON *save = cJSON_GetObjectItemCaseSensitive(root, "save");
    bench_cmd_t cmd =
    {
        .n = cJSON_IsNumber(n) ? (uint32_t)n->valueint : 0,
        .size = cJSON_IsNumber(size) ? (uint32_t)size->valueint : 0,
        .gap_ms = cJSON_IsNumber(gap) ? (uint32_t)gap->valueint : 0,
    };

    const cJSON *cset = cJSON_GetObjectItemCaseSensitive(root,
                                                         "cert_set");

    snprintf(cmd.url, sizeof(cmd.url), "%s",
             cJSON_IsString(url) ? url->valuestring : "");
    snprintf(cmd.save, sizeof(cmd.save), "%s",
             cJSON_IsString(save) ? save->valuestring : "");
    snprintf(cmd.cert_set, sizeof(cmd.cert_set), "%s",
             cJSON_IsString(cset) ? cset->valuestring : "");
    cJSON_Delete(root);

    if (cmd.url[0] != '\0' ||
        (cmd.n > 0 && cmd.n <= BENCH_MAX_N && cmd.size >= 16 &&
         cmd.size <= BENCH_MAX_SIZE))
    {
        ensure_bench_task();
        xQueueSend(s_cmd_q, &cmd, 0); /* busy = ignored */
    }
}

/** http_client_manager exercise (GET or download), result to
 *  bench/result. Runs in the bench task — a normal-task consumer, the
 *  component's intended calling context. */
static void run_http_cmd(const bench_cmd_t *cmd)
{
    char result[192];

    if (cmd->save[0] != '\0')
    {
        esp_err_t err = http_client_manager_download(cmd->url, cmd->save,
                                                     NULL);

        snprintf(result, sizeof(result),
                 "{\"http\":\"download\",\"err\":\"%s\",\"save\":\"%s\"}",
                 esp_err_to_name(err), cmd->save);
    }
    else
    {
        http_client_response_t resp;
        http_client_request_t req =
        {
            .url = cmd->url,
            .cert_set = (cmd->cert_set[0] != '\0') ? cmd->cert_set
                                                   : NULL,
        };
        esp_err_t err = http_client_manager_request(&req, &resp);

        if (err == ESP_OK)
        {
            snprintf(result, sizeof(result),
                     "{\"http\":\"get\",\"status\":%d,\"len\":%u,"
                     "\"head\":\"%.32s\"}",
                     resp.status_code, (unsigned)resp.len,
                     (resp.data != NULL) ? resp.data : "");
            http_client_manager_free(&resp);
        }
        else
        {
            snprintf(result, sizeof(result),
                     "{\"http\":\"get\",\"err\":\"%s\"}",
                     esp_err_to_name(err));
        }
    }

    char topic[96];

    snprintf(topic, sizeof(topic), "%s/bench/result",
             mqtt_manager_topic_prefix());
    mqtt_manager_publish(topic, result, strlen(result), 0, false);
    ESP_LOGI(TAG, "http bench: %s", result);
}

static void on_echo(const char *topic, const uint8_t *data, size_t len,
                    void *arg)
{
    (void)topic;
    (void)arg;

    char reply_topic[96];

    snprintf(reply_topic, sizeof(reply_topic), "%s/bench/echo_re",
             mqtt_manager_topic_prefix());
    /* async from the event task: non-blocking by design */
    mqtt_manager_publish_async(reply_topic, data, len, 0, false);
}

static void bench_task(void *arg)
{
    (void)arg;

    while (true)
    {
        bench_cmd_t cmd;

        if (xQueueReceive(s_cmd_q, &cmd, portMAX_DELAY) != pdTRUE)
        {
            continue;
        }

        if (cmd.url[0] != '\0')
        {
            run_http_cmd(&cmd);
            continue;
        }

        char out_topic[96];
        char result[192];

        snprintf(out_topic, sizeof(out_topic), "%s/bench/out",
                 mqtt_manager_topic_prefix());

        mqtt_manager_stats_t before;
        mqtt_manager_stats_t after;

        mqtt_manager_stats(&before);

        uint32_t ok = 0;
        int64_t t0 = esp_timer_get_time();

        for (uint32_t i = 0; i < cmd.n; i++)
        {
            /* seq at the front so the host can spot gaps */
            int hdr = snprintf((char *)s_payload, cmd.size, "%08lu,",
                               (unsigned long)i);

            memset(s_payload + hdr, 'x', cmd.size - (uint32_t)hdr);

            if (mqtt_manager_publish_async(out_topic, s_payload,
                                           cmd.size, 0, false) == ESP_OK)
            {
                ok++;
            }

            if (cmd.gap_ms > 0)
            {
                vTaskDelay(pdMS_TO_TICKS(cmd.gap_ms));
            }
        }

        int64_t elapsed_ms = (esp_timer_get_time() - t0) / 1000;

        mqtt_manager_stats(&after);
        /* let the ring drain before reporting so "ms" covers enqueue
         * only and the result arrives after the data */
        vTaskDelay(pdMS_TO_TICKS(500));
        snprintf(result, sizeof(result),
                 "{\"sent\":%lu,\"ok\":%lu,\"dropped_full\":%lu,"
                 "\"dropped_offline\":%lu,\"enqueue_ms\":%lld}",
                 (unsigned long)cmd.n, (unsigned long)ok,
                 (unsigned long)(after.dropped_full - before.dropped_full),
                 (unsigned long)(after.dropped_offline -
                                 before.dropped_offline),
                 (long long)elapsed_ms);

        char result_topic[96];

        snprintf(result_topic, sizeof(result_topic), "%s/bench/result",
                 mqtt_manager_topic_prefix());
        mqtt_manager_publish(result_topic, result, strlen(result), 0,
                             false);
        ESP_LOGI(TAG, "mqtt bench: %s", result);
    }
}

esp_err_t main_bench_start(void)
{
    static char cmd_filter[96];
    static char echo_filter[96];

    s_cmd_q = xQueueCreateStatic(1, sizeof(bench_cmd_t), s_cmd_q_store,
                                 &s_cmd_q_buf);

    if (s_cmd_q == NULL)
    {
        return ESP_FAIL;
    }

    snprintf(cmd_filter, sizeof(cmd_filter), "%s/bench/cmd",
             mqtt_manager_topic_prefix());
    snprintf(echo_filter, sizeof(echo_filter), "%s/bench/echo",
             mqtt_manager_topic_prefix());
    mqtt_manager_register_handler(cmd_filter, on_cmd, NULL);
    mqtt_manager_register_handler(echo_filter, on_echo, NULL);
    /* the task itself is created lazily by the first bench command */
    return ESP_OK;
}
