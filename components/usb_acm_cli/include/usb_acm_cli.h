/*
 * USB CDC-ACM CLI (host-side transport)
 *
 * Connects to a CDC-ACM device node (e.g. /dev/ttyACM0) and exposes simple
 * send/read APIs to talk to the remote device CLI (e.g. ESPNetLink).
 */

#pragma once

#include <stdbool.h>
#include <stdint.h>

#include "esp_err.h"

#include "freertos/FreeRTOS.h"

#ifdef __cplusplus
extern "C" {
#endif

typedef struct
{
    bool enable;
    const char *dev_path;           // e.g. "/dev/ttyACM0"
    uint32_t reconnect_delay_ms;    // delay between open retries
    bool assert_dtr;                // assert DTR after attach (often required to get output)
    bool assert_rts;                // assert RTS after attach
    uint32_t line_state_delay_ms;   // delay before setting line state (default: 200ms)
    uint32_t rx_task_stack_size;
    UBaseType_t rx_task_priority;
    int rx_task_core_id;            // -1 = no pinning
    size_t rx_buffer_size;          // bytes buffered from device (default: 2048)
} usb_acm_cli_config_t;

esp_err_t usb_acm_cli_start(const usb_acm_cli_config_t *cfg);
esp_err_t usb_acm_cli_stop(void);

bool usb_acm_cli_is_connected(void);
esp_err_t usb_acm_cli_acquire(uint32_t timeout_ms);
void usb_acm_cli_release(void);
esp_err_t usb_acm_cli_send_line(const char *line);
esp_err_t usb_acm_cli_read(uint8_t *buf, size_t buf_len, uint32_t timeout_ms, size_t *out_len);

#ifdef __cplusplus
}
#endif
