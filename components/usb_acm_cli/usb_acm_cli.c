#include "usb_acm_cli.h"

#include <stdbool.h>
#include <stddef.h>
#include <string.h>

#include "usbh_core.h"
#include "usbh_cdc_acm.h"

#include "esp_err.h"
#include "esp_heap_caps.h"
#include "esp_log.h"

#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "freertos/stream_buffer.h"
#include "freertos/task.h"

#define USB_ACM_CLI_DEFAULT_RECONNECT_DELAY_MS 1000
#define USB_ACM_CLI_DEFAULT_RX_STACK (4096)
#define USB_ACM_CLI_DEFAULT_RX_PRIO (5)
#define USB_ACM_CLI_DEFAULT_RX_BUF_SIZE (2048)
#define USB_ACM_CLI_DEFAULT_LINE_STATE_DELAY_MS (200)

static const char *TAG = "usb_acm_cli";

static usb_acm_cli_config_t s_cfg;
static TaskHandle_t s_task = NULL;
static volatile bool s_stop = false;
static StreamBufferHandle_t s_rx_sb = NULL;
static SemaphoreHandle_t s_tx_lock = NULL;
static StaticSemaphore_t s_tx_lock_buffer;
static SemaphoreHandle_t s_session_lock = NULL;
static StaticSemaphore_t s_session_lock_buffer;
static StaticStreamBuffer_t s_rx_sb_buffer;
static uint8_t *s_rx_sb_storage = NULL;
static StackType_t *s_task_stack = NULL;
static StaticTask_t s_task_tcb;

static uint8_t *s_rx_dma_buf = NULL; // Internal SRAM buffer for USB RX DMA
static uint8_t *s_tx_dma_buf = NULL; // Internal SRAM buffer for USB TX DMA
#define USB_DMA_BUF_SIZE 64

static struct usbh_cdc_acm *s_acm = NULL;
static volatile bool s_line_state_pending = false;
static TickType_t s_line_state_deadline = 0;

static esp_err_t usb_acm_cli_write_bytes(const uint8_t *data, size_t len)
{
    if (data == NULL || len == 0)
    {
        return ESP_OK;
    }

    struct usbh_cdc_acm *acm = s_acm;
    if (acm == NULL)
    {
        return ESP_ERR_INVALID_STATE;
    }

    if (s_tx_lock)
    {
        (void)xSemaphoreTake(s_tx_lock, portMAX_DELAY);
    }

    size_t total = 0;
    while (total < len)
    {
        uint32_t chunk = (uint32_t)(len - total);
        if (chunk > USB_DMA_BUF_SIZE) {
            chunk = USB_DMA_BUF_SIZE;
        }

        // Copy to internal SRAM buffer for DMA safety (caller's data may be in PSRAM)
        memcpy(s_tx_dma_buf, data + total, chunk);

        int ret = usbh_cdc_acm_bulk_out_transfer(acm, s_tx_dma_buf, chunk, 5000);
        if (ret <= 0)
        {
            if (s_tx_lock)
            {
                (void)xSemaphoreGive(s_tx_lock);
            }
            return ESP_FAIL;
        }

        total += (size_t)ret;
    }

    if (s_tx_lock)
    {
        (void)xSemaphoreGive(s_tx_lock);
    }

    return ESP_OK;
}

static void usb_acm_cli_task(void *arg)
{
    (void)arg;
    while (!s_stop)
    {
        struct usbh_cdc_acm *acm = s_acm;
        if (acm == NULL)
        {
            vTaskDelay(pdMS_TO_TICKS(50));
            continue;
        }

        if (s_line_state_pending)
        {
            TickType_t now = xTaskGetTickCount();
            if ((int32_t)(now - s_line_state_deadline) >= 0)
            {
                s_line_state_pending = false;

                if (s_cfg.assert_dtr || s_cfg.assert_rts)
                {
                    int ret = usbh_cdc_acm_set_line_state(acm, s_cfg.assert_dtr, s_cfg.assert_rts);
                    if (ret < 0)
                    {
                        ESP_LOGW(TAG, "CDC-ACM set line state failed (%d)", ret);
                    }
                    else
                    {
                        ESP_LOGI(TAG, "CDC-ACM line state set (DTR=%d RTS=%d)", s_cfg.assert_dtr ? 1 : 0, s_cfg.assert_rts ? 1 : 0);
                    }
                }
            }
        }

        // s_rx_dma_buf is in internal SRAM (DMA-capable), not on the PSRAM stack
        int r = usbh_cdc_acm_bulk_in_transfer(acm, s_rx_dma_buf, 64, 100);
        if (r > 0)
        {
            if (s_rx_sb)
            {
                (void)xStreamBufferSend(s_rx_sb, s_rx_dma_buf, (size_t)r, 0);
            }
        }
    }

    s_task = NULL;
    vTaskDelete(NULL);
}

esp_err_t usb_acm_cli_start(const usb_acm_cli_config_t *cfg)
{
    BaseType_t ok;
    uint32_t stack_depth_words;

    if (s_task != NULL)
    {
        return ESP_ERR_INVALID_STATE;
    }

    memset(&s_cfg, 0, sizeof(s_cfg));
    if (cfg)
    {
        s_cfg = *cfg;
    }

    if (!s_cfg.enable)
    {
        return ESP_OK;
    }

    if (s_cfg.reconnect_delay_ms == 0)
    {
        s_cfg.reconnect_delay_ms = USB_ACM_CLI_DEFAULT_RECONNECT_DELAY_MS;
    }

    if (s_cfg.line_state_delay_ms == 0)
    {
        s_cfg.line_state_delay_ms = USB_ACM_CLI_DEFAULT_LINE_STATE_DELAY_MS;
    }

    if (s_cfg.rx_task_stack_size == 0)
    {
        s_cfg.rx_task_stack_size = USB_ACM_CLI_DEFAULT_RX_STACK;
    }

    if (s_cfg.rx_task_priority == 0)
    {
        s_cfg.rx_task_priority = USB_ACM_CLI_DEFAULT_RX_PRIO;
    }

    if (s_cfg.rx_buffer_size == 0)
    {
        s_cfg.rx_buffer_size = USB_ACM_CLI_DEFAULT_RX_BUF_SIZE;
    }

    s_stop = false;

    // USB DMA buffers MUST be in internal SRAM, not PSRAM.
    // DMA writes to PSRAM are invisible to the CPU due to cache coherency.
    s_rx_dma_buf = (uint8_t *)heap_caps_malloc(USB_DMA_BUF_SIZE, MALLOC_CAP_INTERNAL | MALLOC_CAP_DMA);
    s_tx_dma_buf = (uint8_t *)heap_caps_malloc(USB_DMA_BUF_SIZE, MALLOC_CAP_INTERNAL | MALLOC_CAP_DMA);
    if (s_rx_dma_buf == NULL || s_tx_dma_buf == NULL)
    {
        heap_caps_free(s_rx_dma_buf);
        s_rx_dma_buf = NULL;
        heap_caps_free(s_tx_dma_buf);
        s_tx_dma_buf = NULL;
        return ESP_ERR_NO_MEM;
    }

    s_rx_sb_storage = (uint8_t *)heap_caps_malloc(s_cfg.rx_buffer_size, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    if (s_rx_sb_storage == NULL)
    {
        goto fail_free_dma;
    }

    s_rx_sb = xStreamBufferCreateStatic(s_cfg.rx_buffer_size, 1, s_rx_sb_storage, &s_rx_sb_buffer);
    if (s_rx_sb == NULL)
    {
        goto fail_free_sb_storage;
    }

    s_tx_lock = xSemaphoreCreateMutexStatic(&s_tx_lock_buffer);
    if (s_tx_lock == NULL)
    {
        goto fail_free_sb;
    }

    s_session_lock = xSemaphoreCreateMutexStatic(&s_session_lock_buffer);
    if (s_session_lock == NULL)
    {
        goto fail_free_lock;
    }

    s_task_stack = (StackType_t *)heap_caps_malloc(s_cfg.rx_task_stack_size, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    if (s_task_stack == NULL)
    {
        goto fail_free_session_lock;
    }

    stack_depth_words = (uint32_t)(s_cfg.rx_task_stack_size / sizeof(StackType_t));
    if (stack_depth_words == 0)
    {
        stack_depth_words = USB_ACM_CLI_DEFAULT_RX_STACK / sizeof(StackType_t);
    }

    if (s_cfg.rx_task_core_id >= 0)
    {
        s_task = xTaskCreateStaticPinnedToCore(
            usb_acm_cli_task,
            "usb_acm_cli",
            stack_depth_words,
            NULL,
            s_cfg.rx_task_priority,
            s_task_stack,
            &s_task_tcb,
            s_cfg.rx_task_core_id);
        ok = (s_task != NULL) ? pdPASS : errCOULD_NOT_ALLOCATE_REQUIRED_MEMORY;
    }
    else
    {
        s_task = xTaskCreateStatic(
            usb_acm_cli_task,
            "usb_acm_cli",
            stack_depth_words,
            NULL,
            s_cfg.rx_task_priority,
            s_task_stack,
            &s_task_tcb);
        ok = (s_task != NULL) ? pdPASS : errCOULD_NOT_ALLOCATE_REQUIRED_MEMORY;
    }

    if (ok != pdPASS)
    {
        s_task = NULL;
        heap_caps_free(s_task_stack);
        s_task_stack = NULL;
        goto fail_free_session_lock;
    }

    return ESP_OK;

fail_free_lock:
    s_tx_lock = NULL;
fail_free_session_lock:
    s_session_lock = NULL;
fail_free_sb:
    vStreamBufferDelete(s_rx_sb);
    s_rx_sb = NULL;
fail_free_sb_storage:
    heap_caps_free(s_rx_sb_storage);
    s_rx_sb_storage = NULL;
fail_free_dma:
    heap_caps_free(s_rx_dma_buf);
    s_rx_dma_buf = NULL;
    heap_caps_free(s_tx_dma_buf);
    s_tx_dma_buf = NULL;
    return ESP_ERR_NO_MEM;
}

esp_err_t usb_acm_cli_stop(void)
{
    if (s_task == NULL)
    {
        return ESP_OK;
    }

    s_stop = true;

    while (s_task != NULL)
    {
        vTaskDelay(pdMS_TO_TICKS(10));
    }

    s_acm = NULL;

    if (s_rx_sb)
    {
        vStreamBufferDelete(s_rx_sb);
        s_rx_sb = NULL;
    }

    if (s_rx_sb_storage != NULL)
    {
        heap_caps_free(s_rx_sb_storage);
        s_rx_sb_storage = NULL;
    }

    s_tx_lock = NULL;
    s_session_lock = NULL;

    if (s_task_stack != NULL)
    {
        heap_caps_free(s_task_stack);
        s_task_stack = NULL;
    }

    if (s_rx_dma_buf != NULL)
    {
        heap_caps_free(s_rx_dma_buf);
        s_rx_dma_buf = NULL;
    }

    if (s_tx_dma_buf != NULL)
    {
        heap_caps_free(s_tx_dma_buf);
        s_tx_dma_buf = NULL;
    }

    return ESP_OK;
}

bool usb_acm_cli_is_connected(void)
{
    return (s_acm != NULL);
}

esp_err_t usb_acm_cli_acquire(uint32_t timeout_ms)
{
    if (s_session_lock == NULL)
    {
        return ESP_ERR_INVALID_STATE;
    }

    if (xSemaphoreTake(s_session_lock, pdMS_TO_TICKS(timeout_ms)) != pdTRUE)
    {
        return ESP_ERR_TIMEOUT;
    }

    return ESP_OK;
}

void usb_acm_cli_release(void)
{
    if (s_session_lock != NULL)
    {
        xSemaphoreGive(s_session_lock);
    }
}

esp_err_t usb_acm_cli_send_line(const char *line)
{
    if (line == NULL)
    {
        return ESP_ERR_INVALID_ARG;
    }

    if (s_acm == NULL)
    {
        return ESP_ERR_INVALID_STATE;
    }

    const size_t len = strlen(line);
    if (len > 0)
    {
        esp_err_t err = usb_acm_cli_write_bytes((const uint8_t *)line, len);
        if (err != ESP_OK)
        {
            return err;
        }
    }

    const uint8_t eol = '\r';
    return usb_acm_cli_write_bytes(&eol, 1);
}

esp_err_t usb_acm_cli_read(uint8_t *buf, size_t buf_len, uint32_t timeout_ms, size_t *out_len)
{
    if (out_len)
    {
        *out_len = 0;
    }

    if (buf == NULL || buf_len == 0)
    {
        return ESP_ERR_INVALID_ARG;
    }

    if (s_rx_sb == NULL)
    {
        return ESP_ERR_INVALID_STATE;
    }

    const TickType_t ticks = pdMS_TO_TICKS(timeout_ms);
    size_t r = xStreamBufferReceive(s_rx_sb, buf, buf_len, ticks);
    if (out_len)
    {
        *out_len = r;
    }

    return ESP_OK;
}

// CherryUSB hook points (these are weak in usbh_cdc_acm.c)
void usbh_cdc_acm_run(struct usbh_cdc_acm *cdc_acm_class)
{
    if (cdc_acm_class == NULL)
    {
        return;
    }

    if (s_acm == NULL)
    {
        s_acm = cdc_acm_class;

        if (s_rx_sb)
        {
            xStreamBufferReset(s_rx_sb);
        }

        s_line_state_pending = true;
        s_line_state_deadline = xTaskGetTickCount() + pdMS_TO_TICKS(s_cfg.line_state_delay_ms);

        ESP_LOGI(TAG, "CDC-ACM attached (minor=%u)", (unsigned)cdc_acm_class->minor);
    }
}

void usbh_cdc_acm_stop(struct usbh_cdc_acm *cdc_acm_class)
{
    if (cdc_acm_class == NULL)
    {
        return;
    }

    if (s_acm == cdc_acm_class)
    {
        s_acm = NULL;
        s_line_state_pending = false;
        ESP_LOGI(TAG, "CDC-ACM detached (minor=%u)", (unsigned)cdc_acm_class->minor);
    }
}
