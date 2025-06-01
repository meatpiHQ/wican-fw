/*
 * SPDX-FileCopyrightText: 2022-2024 Espressif Systems (Shanghai) CO LTD
 *
 * SPDX-License-Identifier: CC0-1.0
 */

#include <stdio.h>
#include <string.h>
#include <inttypes.h>

#include "esp_log.h"
#include "esp_system.h"
#include "esp_err.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/semphr.h"

#include "usb/cdc_acm_host.h"
#include "usb/vcp_ch34x.hpp"
#include "usb/vcp_cp210x.hpp"
#include "usb/vcp_ftdi.hpp"
#include "usb/vcp.hpp"
#include "usb/usb_host.h"
#include "driver/gpio.h"
#include "dev_status.h"

using namespace esp_usb;

// Change these values to match your needs
#define EXAMPLE_BAUDRATE     (115200)
#define EXAMPLE_STOP_BITS    (0)      // 0: 1 stopbit, 1: 1.5 stopbits, 2: 2 stopbits
#define EXAMPLE_PARITY       (0)      // 0: None, 1: Odd, 2: Even, 3: Mark, 4: Space
#define EXAMPLE_DATA_BITS    (8)

// Additional definitions from second example
#define EXAMPLE_USB_HOST_PRIORITY   (20)
#define EXAMPLE_USB_DEVICE_VID      (0x1546)
#define EXAMPLE_USB_DEVICE_PID      (0x01A7)
#define EXAMPLE_USB_DEVICE_DUAL_PID (0x4002)
#define EXAMPLE_TX_TIMEOUT_MS       (1000)

namespace {
static const char *TAG = "usb";
static SemaphoreHandle_t device_disconnected_sem;

static bool handle_rx(const uint8_t *data, size_t data_len, void *arg)
{
    ESP_LOGI(TAG, "Received data: %.*s", data_len, data);
    ESP_LOG_BUFFER_HEXDUMP(TAG, data, data_len, ESP_LOG_INFO);
    return true;
}

static void handle_event(const cdc_acm_host_dev_event_data_t *event, void *user_ctx)
{
    switch (event->type) {
    case CDC_ACM_HOST_ERROR:
        ESP_LOGE(TAG, "CDC-ACM error has occurred, err_no = %d", event->data.error);
        break;
    case CDC_ACM_HOST_DEVICE_DISCONNECTED:
        ESP_LOGI(TAG, "Device suddenly disconnected");
        if (event->data.cdc_hdl) {
            ESP_ERROR_CHECK(cdc_acm_host_close(event->data.cdc_hdl));
        }
        xSemaphoreGive(device_disconnected_sem);
        break;
    case CDC_ACM_HOST_SERIAL_STATE:
        ESP_LOGI(TAG, "Serial state notif 0x%04X", event->data.serial_state.val);
        break;
    case CDC_ACM_HOST_NETWORK_CONNECTION:
    default: 
        ESP_LOGW(TAG, "Unsupported CDC event: %i", event->type);
        break;
    }
}

static void usb_lib_task(void *arg)
{
    while (1) {
        uint32_t event_flags;

        dev_status_wait_for_bits(DEV_AWAKE_BIT, portMAX_DELAY);
        usb_host_lib_handle_events(portMAX_DELAY, &event_flags);
        if (event_flags & USB_HOST_LIB_EVENT_FLAGS_NO_CLIENTS) {
            ESP_ERROR_CHECK(usb_host_device_free_all());
        }
        if (event_flags & USB_HOST_LIB_EVENT_FLAGS_ALL_FREE) {
            ESP_LOGI(TAG, "USB: All devices freed");
        }
    }
}

static void send_test_data(std::unique_ptr<CdcAcmDevice>& vcp, cdc_acm_dev_hdl_t cdc_dev)
{
    static const uint8_t data1[] = "set_led 22 0 0\r\n";
    static const uint8_t data2[] = "set_led 22 22 22\r\n";

    if (vcp != nullptr) {
        ESP_LOGI(TAG, "Sending data through VCP interface");
        ESP_ERROR_CHECK_WITHOUT_ABORT(vcp->tx_blocking((uint8_t *)data1, sizeof(data1)));
        vTaskDelay(pdMS_TO_TICKS(5000));
        ESP_ERROR_CHECK_WITHOUT_ABORT(vcp->tx_blocking((uint8_t *)data2, sizeof(data2)));
    } else if (cdc_dev != NULL) {
        ESP_LOGI(TAG, "Sending data through CDC-ACM interface");
        ESP_ERROR_CHECK_WITHOUT_ABORT(cdc_acm_host_data_tx_blocking(cdc_dev, data1, sizeof(data1), EXAMPLE_TX_TIMEOUT_MS));
        vTaskDelay(pdMS_TO_TICKS(5000));
        ESP_ERROR_CHECK_WITHOUT_ABORT(cdc_acm_host_data_tx_blocking(cdc_dev, data2, sizeof(data2), EXAMPLE_TX_TIMEOUT_MS));
    }
}

static void configure_line_coding(std::unique_ptr<CdcAcmDevice>& vcp, cdc_acm_dev_hdl_t cdc_dev)
{
    cdc_acm_line_coding_t line_coding = {
        .dwDTERate = EXAMPLE_BAUDRATE,
        .bCharFormat = EXAMPLE_STOP_BITS,
        .bParityType = EXAMPLE_PARITY,
        .bDataBits = EXAMPLE_DATA_BITS,
    };

    if (vcp != nullptr) {
        ESP_ERROR_CHECK(vcp->line_coding_set(&line_coding));
    } else if (cdc_dev != NULL) {
        ESP_ERROR_CHECK(cdc_acm_host_line_coding_set(cdc_dev, &line_coding));
    }
}
}

extern "C" void usb_host_init(void)
{
    device_disconnected_sem = xSemaphoreCreateBinary();
    assert(device_disconnected_sem);

    gpio_reset_pin(GPIO_NUM_11);
    gpio_set_direction(GPIO_NUM_11, GPIO_MODE_OUTPUT);
    gpio_set_level(GPIO_NUM_11, 1);
    vTaskDelay(pdMS_TO_TICKS(2000));
    
    ESP_LOGI(TAG, "Installing USB Host");
    usb_host_config_t host_config = {
        .skip_phy_setup = false,
        .intr_flags = ESP_INTR_FLAG_LEVEL2|ESP_INTR_FLAG_SHARED
    };
    ESP_ERROR_CHECK(usb_host_install(&host_config));

    // Allocate stack memory in PSRAM for the USB library task
    static StackType_t *usb_lib_task_stack;
    static StaticTask_t usb_lib_task_buffer;
    
    usb_lib_task_stack = (StackType_t*)heap_caps_malloc(4096, MALLOC_CAP_SPIRAM|MALLOC_CAP_8BIT);
    
    if (usb_lib_task_stack == NULL)
    {
        ESP_LOGE(TAG, "Failed to allocate USB library task stack memory");
        return;
    }
    
    // Create static task
    TaskHandle_t usb_task_handle = xTaskCreateStatic(
        usb_lib_task,
        "usb_lib",
        4096,
        NULL,
        EXAMPLE_USB_HOST_PRIORITY,
        usb_lib_task_stack,
        &usb_lib_task_buffer
    );
    
    if (usb_task_handle == NULL)
    {
        ESP_LOGE(TAG, "Failed to create USB library task");
        heap_caps_free(usb_lib_task_stack);
        return;
    }

    ESP_LOGI(TAG, "Installing CDC-ACM driver");
    ESP_ERROR_CHECK(cdc_acm_host_install(NULL));

    // Register VCP drivers to VCP service
    VCP::register_driver<FT23x>();
    VCP::register_driver<CP210x>();
    VCP::register_driver<CH34x>();

    while (true) {
        const cdc_acm_host_device_config_t dev_config = {
            .connection_timeout_ms = 1000,
            .out_buffer_size = 512,
            .in_buffer_size = 512,
            .event_cb = handle_event,
            .data_cb = handle_rx,
            .user_arg = NULL,
        };

        // Try generic VCP device first
        ESP_LOGI(TAG, "Opening USB device as VCP...");
        auto vcp = std::unique_ptr<CdcAcmDevice>(VCP::open(&dev_config));
        cdc_acm_dev_hdl_t cdc_dev = NULL;
        
        if (vcp == nullptr) {
            ESP_LOGI(TAG, "Trying specific VID/PID device...");
            esp_err_t err = cdc_acm_host_open(EXAMPLE_USB_DEVICE_VID, EXAMPLE_USB_DEVICE_PID, 0, &dev_config, &cdc_dev);
            if (ESP_OK != err) {
                ESP_LOGI(TAG, "Trying dual PID device...");
                err = cdc_acm_host_open(EXAMPLE_USB_DEVICE_VID, EXAMPLE_USB_DEVICE_DUAL_PID, 0, &dev_config, &cdc_dev);
                if (ESP_OK != err) {
                    ESP_LOGI(TAG, "Failed to open any device");
                    continue;
                }
            }
            cdc_acm_host_desc_print(cdc_dev);
        }

        vTaskDelay(pdMS_TO_TICKS(10));

        ESP_LOGI(TAG, "Setting up line coding");
        configure_line_coding(vcp, cdc_dev);

        // Send test data using appropriate method
        send_test_data(vcp, cdc_dev);

        ESP_LOGI(TAG, "Done. You can reconnect the USB device to run again.");
        xSemaphoreTake(device_disconnected_sem, portMAX_DELAY);
    }
}