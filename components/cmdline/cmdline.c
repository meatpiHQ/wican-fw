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

#define PROMPT "wican> "
#define MAX_CMDLINE_LENGTH 256
#define TCP_PORT 23
#define MAX_CLIENTS 1
#define RECV_BUF_SIZE (MAX_CMDLINE_LENGTH)
#define MAX_HISTORY_LINES 50

static const char* TAG = "cmdline";
static TaskHandle_t tcp_console_task_handle = NULL;
static int server_socket = -1;
static int client_socket = -1;
static bool is_cmdline_initialized = false;
static cmdline_output_func_t output_func = NULL;

static void tcp_console_write(const char* data, size_t len)
{
    if (client_socket < 0) {
        return;
    }
    send(client_socket, data, len, 0);
}

void cmdline_printf(const char *fmt, ...)
{
    char buf[256];
    va_list args;
    va_start(args, fmt);
    vsnprintf(buf, sizeof(buf), fmt, args);
    va_end(args);
    
    printf("%s", buf);
    if (output_func) {
        output_func(buf, strlen(buf));
    }
}

void cmdline_set_output_func(cmdline_output_func_t func)
{
    output_func = func;
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
    cmd_factoryreset_register();
}

static void process_command(char* cmd)
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
    
    int ret;
    esp_err_t err = esp_console_run(cmd_copy, &ret);
    
    if (err == ESP_ERR_NOT_FOUND) {
        if (output_func) output_func("Command not found\n", 18);
    } else if (err == ESP_ERR_INVALID_ARG) {
        if (output_func) output_func("Invalid arguments\n", 18);
    } else if (err == ESP_OK && ret != ESP_OK) {
        if (output_func) output_func("Command returned non-zero error code\n", 37);
    } else if (err != ESP_OK) {
        if (output_func) output_func("Internal error\n", 15);
    }
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
                        process_command(cmd_buf);
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
    
    tcp_console_task_stack = heap_caps_malloc(4096, MALLOC_CAP_SPIRAM|MALLOC_CAP_8BIT);
    
    if (tcp_console_task_stack == NULL) {
        ESP_LOGE(TAG, "Failed to allocate TCP console task stack memory");
        return ESP_ERR_NO_MEM;
    }
    
    tcp_console_task_handle = xTaskCreateStatic(
        tcp_console_task,
        "tcp_console",
        4096,
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

    cmdline_set_output_func(tcp_console_write);
    tcp_console_init();
    register_all_commands();
    
    return esp_console_start_repl(repl);
}