/*
 * This file is part of the WiCAN project.
 *
 * Copyright (C) 2022  Meatpi Electronics.
 * Written by Ali Slim <ali@meatpi.com>
 *
 * This program is free software: you can redistribute it and/or modify
 * it under the terms of the GNU General Public License as published by
 * the Free Software Foundation, either version 3 of the License, or
 * (at your option) any later version.
 *
 * This program is distributed in the hope that it will be useful,
 * but WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
 * GNU General Public License for more details.
 *
 * You should have received a copy of the GNU General Public License
 * along with this program.  If not, see <http://www.gnu.org/licenses/>.
 */

#include "cmdline.h"
#include "esp_console.h"
#include "esp_vfs.h"
#include "esp_log.h"
#include "esp_heap_caps.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "lwip/sockets.h"
#include "lwip/netdb.h"
#include "lwip/sys.h"
#include <string.h>
#include <stdarg.h>
#include <errno.h>

#include "cmd_version.h"
#include "cmd_system.h"
#include "cmd_imu.h"
#include "cmd_rtc.h"
#include "cmd_led.h"
#include "cmd_sdcard.h"
#include "cmd_wifi.h"
#include "cmd_wusb.h"
#include "cmd_status.h"
#include "cmd_factoryreset.h"
#include "cmd_autopid.h"
#include "cmd_debug.h"

#define PROMPT "wican> "
#define MAX_CMDLINE_LENGTH 256
#define TCP_PORT 23
#define MAX_CLIENTS 1
#define RECV_BUF_SIZE (MAX_CMDLINE_LENGTH)
#define MAX_HISTORY_LINES 50

static const char* TAG = "CMDLINE";
static TaskHandle_t tcp_console_task_handle = NULL;
static int server_socket = -1;
static int client_socket = -1;
static bool is_cmdline_initialized = false;
static cmdline_output_func_t tcp_output_func = NULL;
static cmdline_output_func_t ble_output_func = NULL;

// Command source and IO routing context
typedef enum {
    CMD_SRC_UART = 0,
    CMD_SRC_TCP,
    CMD_SRC_BLE,
} cmd_source_t;

typedef struct {
    cmd_source_t src;
    cmdline_output_func_t tcp; // writer for TCP (may be NULL)
    cmdline_output_func_t ble; // writer for BLE (may be NULL)
} cmd_io_t;

static const cmd_io_t k_uart_io = { .src = CMD_SRC_UART, .tcp = NULL, .ble = NULL };
static const cmd_io_t *s_current_io = &k_uart_io;

static inline void cmdline_set_current_io(const cmd_io_t *io)
{
    s_current_io = (io ? io : &k_uart_io);
}

static void tcp_console_write(const char* data, size_t len)
{
    if (client_socket < 0 || data == NULL || len == 0) {
        return;
    }

    size_t total_sent = 0;
    while (total_sent < len) {
        size_t remaining = len - total_sent;
        size_t chunk = remaining > 1024 ? 1024 : remaining; // send in manageable chunks
        ssize_t n = send(client_socket, data + total_sent, chunk, 0);
        if (n > 0) {
            total_sent += (size_t)n;
        } else if (n < 0) {
            int err = errno;
            if (err == EINTR) {
                continue; // interrupted, retry
            }
            if (err == EAGAIN || err == EWOULDBLOCK) {
                vTaskDelay(pdMS_TO_TICKS(5));
                continue; // try again after a short wait
            }
            // unrecoverable send error
            break;
        } else {
            // n == 0, connection likely closed
            break;
        }
        // yield occasionally to avoid starving other tasks
        taskYIELD();
    }
}

void cmdline_printf(const char *fmt, ...)
{
    // First, compute the required length
    va_list args1;
    va_start(args1, fmt);
    va_list args2;
    va_copy(args2, args1);
    int needed = vsnprintf(NULL, 0, fmt, args1);
    va_end(args1);
    if (needed < 0) {
        va_end(args2);
        return;
    }

    size_t size = (size_t)needed + 1; // include NUL
    char *buf = (char *)heap_caps_malloc(size, MALLOC_CAP_8BIT);
    if (!buf) {
        va_end(args2);
        return;
    }

    vsnprintf(buf, size, fmt, args2);
    va_end(args2);

    // Route output to the active IO context only
    switch (s_current_io->src) {
        case CMD_SRC_TCP:
            if (s_current_io->tcp) {
                s_current_io->tcp(buf, strlen(buf));
            } else if (tcp_output_func) {
                tcp_output_func(buf, strlen(buf));
            } else {
                printf("%s", buf);
            }
            break;
        case CMD_SRC_BLE:
            if (s_current_io->ble) {
                s_current_io->ble(buf, strlen(buf));
            } else if (ble_output_func) {
                ble_output_func(buf, strlen(buf));
            } else {
                printf("%s", buf);
            }
            break;
        case CMD_SRC_UART:
        default:
            printf("%s", buf);
            break;
    }

    heap_caps_free(buf);
}

void cmdline_set_tcp_output_func(cmdline_output_func_t func)
{
    tcp_output_func = func;
}

void cmdline_set_ble_output_func(cmdline_output_func_t func)
{
    ble_output_func = func;
}

void cmdline_print_prompt_on_ble(void)
{
    if (ble_output_func) {
        ble_output_func(PROMPT, strlen(PROMPT));
    }
}

static void register_all_commands(void)
{
    esp_console_register_help_command();
    cmd_version_register();
    cmd_status_register();
    cmd_system_register();
    cmd_imu_register();
    cmd_rtc_register();
    cmd_led_register();
    cmd_sdcard_register();
    cmd_wifi_register();
    cmd_wusb_register();
    cmd_autopid_register();
}

// New: process command with IO context
static void process_command_io(char* cmd, const cmd_io_t *io)
{
    char cmd_copy[MAX_CMDLINE_LENGTH];
    strncpy(cmd_copy, cmd, MAX_CMDLINE_LENGTH - 1);
    cmd_copy[MAX_CMDLINE_LENGTH - 1] = '\0';

    size_t cmd_len = strlen(cmd_copy);
    while (cmd_len > 0 && (cmd_copy[cmd_len - 1] == ' ' || 
           cmd_copy[cmd_len - 1] == '\r' || 
           cmd_copy[cmd_len - 1] == '\n')) {
        cmd_copy[--cmd_len] = '\0';
    }

    if (cmd_len == 0) {
        return;
    }

    const cmd_io_t *prev = s_current_io;
    cmdline_set_current_io(io);
    
    int ret;
    esp_err_t err = esp_console_run(cmd_copy, &ret);
    
    if (err == ESP_ERR_NOT_FOUND) {
        cmdline_printf("Command not found\n");
    } else if (err == ESP_ERR_INVALID_ARG) {
        cmdline_printf("Invalid arguments\n");
    } else if (err == ESP_OK && ret != ESP_OK) {
        cmdline_printf("Command returned non-zero error code\n");
    } else if (err != ESP_OK) {
        cmdline_printf("Internal error\n");
    }

    cmdline_set_current_io(prev);
}

// Backward-compatible wrapper defaults to UART
static void process_command(char* cmd)
{
    process_command_io(cmd, &k_uart_io);
}

esp_err_t cmdline_run(const char *cmd)
{
    if (cmd == NULL) return ESP_ERR_INVALID_ARG;

    char cmd_copy[MAX_CMDLINE_LENGTH];
    strncpy(cmd_copy, cmd, MAX_CMDLINE_LENGTH - 1);
    cmd_copy[MAX_CMDLINE_LENGTH - 1] = '\0';

    // Trim trailing spaces/CR/LF
    size_t cmd_len = strlen(cmd_copy);
    while (cmd_len > 0 && (cmd_copy[cmd_len - 1] == ' ' ||
                           cmd_copy[cmd_len - 1] == '\r' ||
                           cmd_copy[cmd_len - 1] == '\n')) {
        cmd_copy[--cmd_len] = '\0';
    }

    // Ignore empty
    if (cmd_len == 0) return ESP_OK;

    const cmd_io_t *prev = s_current_io;
    cmdline_set_current_io(&k_uart_io);

    int ret = 0;
    esp_err_t err = esp_console_run(cmd_copy, &ret);

    if (err == ESP_ERR_NOT_FOUND) {
        cmdline_printf("Command not found\n");
    } else if (err == ESP_ERR_INVALID_ARG) {
        cmdline_printf("Invalid arguments\n");
    } else if (err == ESP_OK && ret != ESP_OK) {
        cmdline_printf("Command returned non-zero error code\n");
    } else if (err != ESP_OK) {
        cmdline_printf("Internal error\n");
    }

    cmdline_set_current_io(prev);
    return err;
}

// Run a command with BLE as the active output sink
esp_err_t cmdline_run_on_ble(const char *cmd)
{
    if (cmd == NULL) return ESP_ERR_INVALID_ARG;

    char cmd_copy[MAX_CMDLINE_LENGTH];
    strncpy(cmd_copy, cmd, MAX_CMDLINE_LENGTH - 1);
    cmd_copy[MAX_CMDLINE_LENGTH - 1] = '\0';

    size_t cmd_len = strlen(cmd_copy);
    while (cmd_len > 0 && (cmd_copy[cmd_len - 1] == ' ' ||
                           cmd_copy[cmd_len - 1] == '\r' ||
                           cmd_copy[cmd_len - 1] == '\n')) {
        cmd_copy[--cmd_len] = '\0';
    }
    if (cmd_len == 0) return ESP_OK;

    cmd_io_t ble_io = { .src = CMD_SRC_BLE, .tcp = NULL, .ble = ble_output_func };
    const cmd_io_t *prev = s_current_io;
    cmdline_set_current_io(&ble_io);

    int ret = 0;
    esp_err_t err = esp_console_run(cmd_copy, &ret);

    if (err == ESP_ERR_NOT_FOUND) {
        cmdline_printf("Command not found\n");
    } else if (err == ESP_ERR_INVALID_ARG) {
        cmdline_printf("Invalid arguments\n");
    } else if (err == ESP_OK && ret != ESP_OK) {
        cmdline_printf("Command returned non-zero error code\n");
    } else if (err != ESP_OK) {
        cmdline_printf("Internal error\n");
    }

    cmdline_set_current_io(prev);
    return err;
}

static void tcp_console_task(void* arg)
{
    struct sockaddr_in server_addr = {
        .sin_family = AF_INET,
        .sin_port = htons(TCP_PORT),
        .sin_addr.s_addr = htonl(INADDR_ANY)
    };

    server_socket = socket(AF_INET, SOCK_STREAM, 0);
    if (server_socket < 0) {
        ESP_LOGE(TAG, "Failed to create server socket");
        vTaskDelete(NULL);
        return;
    }

    int opt = 1;
    setsockopt(server_socket, SOL_SOCKET, SO_REUSEADDR, &opt, sizeof(opt));
    
    int keepalive = 1;
    int keepidle = (60*5);
    int keepintvl = 10;
    int keepcnt = 3;
    setsockopt(server_socket, SOL_SOCKET, SO_KEEPALIVE, &keepalive, sizeof(keepalive));
    setsockopt(server_socket, IPPROTO_TCP, TCP_KEEPIDLE, &keepidle, sizeof(keepidle));
    setsockopt(server_socket, IPPROTO_TCP, TCP_KEEPINTVL, &keepintvl, sizeof(keepintvl));
    setsockopt(server_socket, IPPROTO_TCP, TCP_KEEPCNT, &keepcnt, sizeof(keepcnt));

    if (bind(server_socket, (struct sockaddr*)&server_addr, sizeof(server_addr)) != 0) {
        ESP_LOGE(TAG, "Socket bind failed");
        close(server_socket);
        vTaskDelete(NULL);
        return;
    }

    if (listen(server_socket, 1) != 0) {
        ESP_LOGE(TAG, "Socket listen failed");
        close(server_socket);
        vTaskDelete(NULL);
        return;
    }

    ESP_LOGI(TAG, "TCP Console started on port %d", TCP_PORT);

    char *recv_buf = heap_caps_malloc(RECV_BUF_SIZE, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    char *cmd_buf = heap_caps_malloc(MAX_CMDLINE_LENGTH, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    
    if (!recv_buf || !cmd_buf) {
        ESP_LOGE(TAG, "Failed to allocate console buffers in PSRAM");
        if (recv_buf) heap_caps_free(recv_buf);
        if (cmd_buf) heap_caps_free(cmd_buf);
        vTaskDelete(NULL);
        return;
    }

    size_t cmd_pos = 0;

    while (1) {
        struct sockaddr_in client_addr;
        socklen_t client_addr_len = sizeof(client_addr);

        client_socket = accept(server_socket, (struct sockaddr*)&client_addr, &client_addr_len);
        if (client_socket < 0) {
            ESP_LOGE(TAG, "Accept failed");
            continue;
        }
        
        ESP_LOGI(TAG, "Client connected from %s", inet_ntoa(client_addr.sin_addr));
        
        struct timeval timeout;
        timeout.tv_sec = (60*5);
        timeout.tv_usec = 0;
        setsockopt(client_socket, SOL_SOCKET, SO_RCVTIMEO, &timeout, sizeof(timeout));

        const char* welcome_msg = "\nWelcome to WiCAN Console\n"
                                "Type 'help' for available commands\n";
        tcp_console_write(welcome_msg, strlen(welcome_msg));

        memset(cmd_buf, 0, MAX_CMDLINE_LENGTH);
        cmd_pos = 0;

        tcp_console_write(PROMPT, strlen(PROMPT));

        // Build IO context for this TCP session
        cmd_io_t tcp_io = { .src = CMD_SRC_TCP, .tcp = tcp_console_write, .ble = NULL };

        while (1) {
            int len = recv(client_socket, recv_buf, RECV_BUF_SIZE - 1, 0);
            if (len <= 0) {
                ESP_LOGI(TAG, "Client disconnected");
                break;
            }

            for (int i = 0; i < len; i++) {
                char c = recv_buf[i];
                
                if (c == 0x7f || c == 0x08) {
                    if (cmd_pos > 0) {
                        cmd_buf[--cmd_pos] = '\0';
                        tcp_console_write("\b \b", 3);
                    }
                    continue;
                }
                
                if (c == '\n') {
                    if (cmd_pos > 0) {
                        cmd_buf[cmd_pos] = '\0';
                        process_command_io(cmd_buf, &tcp_io);
                        memset(cmd_buf, 0, MAX_CMDLINE_LENGTH);
                        cmd_pos = 0;
                    }
                    tcp_console_write(PROMPT, strlen(PROMPT));
                    continue;
                }
                
                if (c >= 32 && c < 127) {
                    if (cmd_pos < MAX_CMDLINE_LENGTH - 1) {
                        cmd_buf[cmd_pos++] = c;
                    }
                }
            }
        }

        close(client_socket);
        client_socket = -1;
    }
}

static esp_err_t tcp_console_init(void) 
{
    if (is_cmdline_initialized) {
        ESP_LOGW(TAG, "TCP Console already initialized");
        return ESP_ERR_INVALID_STATE;
    }

    static StackType_t *tcp_console_task_stack;
    static StaticTask_t tcp_console_task_buffer;
    const size_t tcp_console_task_stack_size = 1024 * 5;
    tcp_console_task_stack = heap_caps_malloc(tcp_console_task_stack_size, MALLOC_CAP_SPIRAM|MALLOC_CAP_8BIT);

    if (tcp_console_task_stack == NULL) {
        ESP_LOGE(TAG, "Failed to allocate TCP console task stack memory");
        return ESP_ERR_NO_MEM;
    }
    
    tcp_console_task_handle = xTaskCreateStatic(
        tcp_console_task,
        "tcp_console",
        tcp_console_task_stack_size,
        NULL,
        5,
        tcp_console_task_stack,
        &tcp_console_task_buffer
    );
    
    if (tcp_console_task_handle == NULL) {
        ESP_LOGE(TAG, "Failed to create TCP console task");
        heap_caps_free(tcp_console_task_stack);
        return ESP_ERR_NO_MEM;
    }

    is_cmdline_initialized = true;
    return ESP_OK;
}

esp_err_t cmdline_safemode_init(void)
{
    esp_console_repl_t *repl = NULL;
    esp_console_repl_config_t repl_config = ESP_CONSOLE_REPL_CONFIG_DEFAULT();
    repl_config.task_stack_size = (4*1024);
    repl_config.prompt = PROMPT;
    repl_config.max_cmdline_length = 256;
    
#if CONFIG_ESP_CONSOLE_USB_CDC
    esp_console_dev_usb_cdc_config_t cdc_config = ESP_CONSOLE_DEV_CDC_CONFIG_DEFAULT();
    ESP_ERROR_CHECK(esp_console_new_repl_usb_cdc(&cdc_config, &repl_config, &repl));
#elif CONFIG_ESP_CONSOLE_UART
    esp_console_dev_uart_config_t uart_config = ESP_CONSOLE_DEV_UART_CONFIG_DEFAULT();

    esp_err_t ret = esp_console_new_repl_uart(&uart_config, &repl_config, &repl);
    if (ret != ESP_OK) {
        ESP_LOGE(TAG, "Failed to create UART REPL: %d", ret);
        return ret;
    }
#endif

    esp_console_register_help_command();
    cmd_version_register();
    cmd_factoryreset_register();
    cmd_system_register();
    cmd_debug_register();
    
    return esp_console_start_repl(repl);
}

esp_err_t cmdline_init(void)
{
    esp_console_repl_t *repl = NULL;
    esp_console_repl_config_t repl_config = ESP_CONSOLE_REPL_CONFIG_DEFAULT();
    repl_config.task_stack_size = (4*1024);
    repl_config.prompt = PROMPT;
    repl_config.max_cmdline_length = 256;
    
#if CONFIG_ESP_CONSOLE_USB_CDC
    esp_console_dev_usb_cdc_config_t cdc_config = ESP_CONSOLE_DEV_CDC_CONFIG_DEFAULT();
    ESP_ERROR_CHECK(esp_console_new_repl_usb_cdc(&cdc_config, &repl_config, &repl));
#elif CONFIG_ESP_CONSOLE_UART
    esp_console_dev_uart_config_t uart_config = ESP_CONSOLE_DEV_UART_CONFIG_DEFAULT();

    esp_err_t ret = esp_console_new_repl_uart(&uart_config, &repl_config, &repl);
    if (ret != ESP_OK) {
        ESP_LOGE(TAG, "Failed to create UART REPL: %d", ret);
        return ret;
    }
#endif

    // Set TCP transport writer and start TCP console
    cmdline_set_tcp_output_func(tcp_console_write);
    tcp_console_init();
    register_all_commands();
    
    return esp_console_start_repl(repl);
}