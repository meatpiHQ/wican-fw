#include "usb_acm_cli.h"

#include <stdbool.h>
#include <stddef.h>
#include <string.h>

#include "usbh_core.h"
#include "usbh_cdc_acm.h"

#include "esp_err.h"
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
        if (chunk > 512) {
            chunk = 512;
        }

        int ret = usbh_cdc_acm_bulk_out_transfer(acm, (uint8_t *)(data + total), chunk, 5000);
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

        uint8_t tmp[64];
        int r = usbh_cdc_acm_bulk_in_transfer(acm, tmp, sizeof(tmp), 100);
        if (r > 0)
        {
            if (s_rx_sb)
            {
                (void)xStreamBufferSend(s_rx_sb, tmp, (size_t)r, 0);
            }
        }
    }

    s_task = NULL;
    vTaskDelete(NULL);
}

esp_err_t usb_acm_cli_start(const usb_acm_cli_config_t *cfg)
{
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

    s_rx_sb = xStreamBufferCreate(s_cfg.rx_buffer_size, 1);
    if (s_rx_sb == NULL)
    {
        return ESP_ERR_NO_MEM;
    }

    s_tx_lock = xSemaphoreCreateMutex();
    if (s_tx_lock == NULL)
    {
        vStreamBufferDelete(s_rx_sb);
        s_rx_sb = NULL;
        return ESP_ERR_NO_MEM;
    }

    BaseType_t ok;
    if (s_cfg.rx_task_core_id >= 0)
    {
        ok = xTaskCreatePinnedToCore(
            usb_acm_cli_task,
            "usb_acm_cli",
            (uint32_t)(s_cfg.rx_task_stack_size / sizeof(StackType_t)),
            NULL,
            s_cfg.rx_task_priority,
            &s_task,
            s_cfg.rx_task_core_id);
    }
    else
    {
        ok = xTaskCreate(
            usb_acm_cli_task,
            "usb_acm_cli",
            (uint32_t)(s_cfg.rx_task_stack_size / sizeof(StackType_t)),
            NULL,
            s_cfg.rx_task_priority,
            &s_task);
    }

    if (ok != pdPASS)
    {
        s_task = NULL;
        vStreamBufferDelete(s_rx_sb);
        s_rx_sb = NULL;
        vSemaphoreDelete(s_tx_lock);
        s_tx_lock = NULL;
        return ESP_ERR_NO_MEM;
    }

    return ESP_OK;
}

esp_err_t usb_acm_cli_stop(void)
{
    if (s_task == NULL)
    {
        return ESP_OK;
    }

    s_stop = true;

    s_acm = NULL;

    if (s_rx_sb)
    {
        vStreamBufferDelete(s_rx_sb);
        s_rx_sb = NULL;
    }

    if (s_tx_lock)
    {
        vSemaphoreDelete(s_tx_lock);
        s_tx_lock = NULL;
    }

    return ESP_OK;
}

bool usb_acm_cli_is_connected(void)
{
    return (s_acm != NULL);
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

    const uint8_t eol[2] = { '\r', '\n' };
    return usb_acm_cli_write_bytes(eol, sizeof(eol));
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

        struct cdc_line_coding lc;
        memset(&lc, 0, sizeof(lc));
        lc.dwDTERate = 115200;
        lc.bDataBits = 8;
        lc.bParityType = 0;
        lc.bCharFormat = 0;
        (void)usbh_cdc_acm_set_line_coding(cdc_acm_class, &lc);

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
