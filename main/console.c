#include "console.h"
#include "esp_log.h"
#include "esp_vfs_cdcacm.h"
#include "driver/uart.h"
#include "linenoise/linenoise.h"
#include "nvs.h"
#include "nvs_flash.h"
#include "esp_ota_ops.h"
#include "esp_app_desc.h"
#include "imu.h"
#include "rtcm.h"
#include "led.h"
#include "sleep_mode.h"
#include "hw_config.h"
#include "sdcard.h"
#include "esp_heap_caps.h"
#include <string.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "esp_system.h"
#include "esp_log.h"
#include "esp_console.h"
#include "esp_vfs.h"
#include "esp_timer.h"
#include "lwip/sockets.h"


#define PROMPT "wican> "
#define MAX_CMDLINE_LENGTH 256
#define MAX_CMDLINE_ARGS 8
#define TCP_PORT 23
#define MAX_CLIENTS 1
#define RECV_BUF_SIZE (MAX_CMDLINE_LENGTH)

static TaskHandle_t tcp_console_task_handle = NULL;
static int server_socket = -1;
static int client_socket = -1;
static bool is_console_initialized = false;

static const char* TAG = "console";

typedef struct {
    const char *command;
    const char *help;
    const char *hint;
    esp_console_cmd_func_t func;
} console_cmd_t;

static struct {
    struct arg_lit *sync;
    struct arg_lit *read;
    struct arg_end *end;
} rtcm_args;

static struct {
    struct arg_lit *id;
    struct arg_end *end;
} imu_args;

static struct {
    struct arg_lit *id;
    struct arg_end *end;
} led_args;

struct {
    struct arg_lit *voltage;
    struct arg_lit *reboot;
    struct arg_lit *id;
    struct arg_lit *memory;
    struct arg_end *end;
} system_args;

static struct {
    struct arg_lit *info;
    struct arg_lit *test;
    struct arg_end *end;
} sdcard_args;

static void tcp_console_write(const char* data, size_t len)
{
    if (client_socket < 0)
    {
        return;
    }
    send(client_socket, data, len, 0);
}

static void console_printf(const char *fmt, ...)
{
    char buf[256];
    va_list args;
    va_start(args, fmt);
    vsnprintf(buf, sizeof(buf), fmt, args);
    va_end(args);
    printf("%s", buf);
    tcp_console_write(buf, strlen(buf));
}

static int cmd_version(int argc, char **argv)
{
    const esp_partition_t *running = esp_ota_get_running_partition();
    esp_app_desc_t running_app_info;

    if (esp_ota_get_partition_description(running, &running_app_info) != ESP_OK) {
        console_printf("Error: Failed to get partition info\n");
        return 1;
    }

    console_printf("Version: %s\n", running_app_info.version);
    console_printf("Project Name: %s\n", running_app_info.project_name);
    console_printf("Build Time: %s %s\n", running_app_info.date, running_app_info.time);
    console_printf("IDF Version: %s\n", running_app_info.idf_ver);
    console_printf("Running Partition: %s\n", running->label);
    console_printf("OK\n");
    return 0;
}

static int cmd_status(int argc, char **argv) 
{
    console_printf("System Status: OK\n");
    return 0;
}

static int cmd_imu(int argc, char **argv)
{
    int nerrors = arg_parse(argc, argv, (void **)&imu_args);
    if (nerrors != 0) {
        arg_print_errors(stderr, imu_args.end, argv[0]);
        return 1;
    }

    if (imu_args.id->count > 0) {
        uint8_t id;
        if (imu_get_device_id(&id) != ESP_OK) {
            console_printf("Error: Failed to read IMU ID\n");
            return 1;
        }
        console_printf("IMU Device ID: 0x%02X\n", id);
        console_printf("OK\n");
        return 0;
    }

    console_printf("Error: No valid subcommand\n");
    return 1;
}

static int cmd_rtcm(int argc, char **argv)
{
    int nerrors = arg_parse(argc, argv, (void **)&rtcm_args);
    if (nerrors != 0) {
        arg_print_errors(stderr, rtcm_args.end, argv[0]);
        return 1;
    }

    if (rtcm_args.sync->count > 0) {
        // Sync time from internet
        if (rtcm_sync_internet_time() != ESP_OK) {
            console_printf("Error: Failed to sync time\n");
            return 1;
        }
        console_printf("Time synchronized successfully\n");
        console_printf("OK\n");
        return 0;
    }

    if (rtcm_args.read->count > 0) {
        uint8_t hour, min, sec;
        uint8_t year, month, day, weekday;
        
        esp_err_t ret_time = rtcm_get_time(&hour, &min, &sec);
        esp_err_t ret_date = rtcm_get_date(&year, &month, &day, &weekday);

        if (ret_time == ESP_OK && ret_date == ESP_OK) {
            console_printf("20%02X-%02X-%02X %02X:%02X:%02X (Day %d)\n", 
                   year, month, day, hour, min, sec, weekday);
            console_printf("OK\n");
            return 0;
        } else {
            console_printf("Error: Failed to read RTC time/date\n");
            return 1;
        }
    }

    console_printf("Error: No valid subcommand\n");
    return 1;
}

static int cmd_led(int argc, char **argv)
{
    int nerrors = arg_parse(argc, argv, (void **)&led_args);
    if (nerrors != 0) {
        arg_print_errors(stderr, led_args.end, argv[0]);
        return 1;
    }

    if (led_args.id->count > 0) {
        uint8_t id;
        if (led_get_device_id(&id) != ESP_OK) {
            console_printf("Error: Failed to read LED driver ID\n");
            return 1;
        }
        console_printf("LED Driver ID: 0x%02X\n", id);
        console_printf("OK\n");
        return 0;
    }

    console_printf("Error: No valid subcommand\n");
    return 1;
}

static int cmd_system(int argc, char **argv)
{
    int nerrors = arg_parse(argc, argv, (void **)&system_args);
    if (nerrors != 0) 
    {
        arg_print_errors(stderr, system_args.end, argv[0]);
        return 1;
    }

    if (system_args.voltage->count > 0) 
    {
        float voltage;
        if (sleep_mode_get_voltage(&voltage) != ESP_OK) 
        {
            console_printf("Error: Failed to read voltage\n");
            return 1;
        }
        console_printf("System Voltage: %.2f V\n", voltage);
        console_printf("OK\n");
        return 0;
    }

    if (system_args.reboot->count > 0)
    {
        console_printf("System will reboot now...\n");
        vTaskDelay(pdMS_TO_TICKS(2000));
        
        esp_restart();
        return 0;
    }

    if (system_args.id->count > 0)
    {
        char device_id[13];
        if (hw_config_get_device_id(device_id) != ESP_OK)
        {
            console_printf("Error: Failed to read device ID\n");
            return 1;
        }
        console_printf("Device ID: %s\n", device_id);
        console_printf("OK\n");
        return 0;
    }

    if (system_args.memory->count > 0)
    {
        // Get memory stats
        uint32_t free_internal = heap_caps_get_free_size(MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT);
        uint32_t free_psram = heap_caps_get_free_size(MALLOC_CAP_SPIRAM);
        uint32_t largest_internal = heap_caps_get_largest_free_block(MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT);
        uint32_t largest_psram = heap_caps_get_largest_free_block(MALLOC_CAP_SPIRAM);
        uint32_t min_free_internal = heap_caps_get_minimum_free_size(MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT);
        uint32_t min_free_psram = heap_caps_get_minimum_free_size(MALLOC_CAP_SPIRAM);

        // Get total heap size
        multi_heap_info_t info;
        heap_caps_get_info(&info, MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT);
        uint32_t total_internal = info.total_free_bytes + info.total_allocated_bytes;

        console_printf("RAM Free: %lu bytes\n", free_internal);
        console_printf("RAM Largest block: %lu bytes\n", largest_internal);
        console_printf("RAM Total: %lu bytes\n", total_internal);
        console_printf("RAM Min free ever: %lu bytes\n", min_free_internal);
        console_printf("PSRAM Free: %lu bytes\n", free_psram);
        console_printf("PSRAM Largest block: %lu bytes\n", largest_psram);
        console_printf("PSRAM Min free ever: %lu bytes\n", min_free_psram);
        console_printf("OK\n");
        return 0;
    }

    console_printf("Error: No valid subcommand\n");
    return 1;
}

static int cmd_sdcard(int argc, char **argv)
{
    int nerrors = arg_parse(argc, argv, (void **)&sdcard_args);
    if (nerrors != 0) 
    {
        arg_print_errors(stderr, sdcard_args.end, argv[0]);
        return 1;
    }

    if (sdcard_args.info->count > 0)
    {
        sdmmc_card_info_t card_info;
        if (sdcard_get_info(&card_info) != ESP_OK)
        {
            console_printf("Error: Failed to read SD card info\n");
            return 1;
        }
        console_printf("SD Card Info:\n");
        console_printf("Name: %s\n", card_info.name);
        console_printf("Type: %s\n", card_info.type == CARD_TYPE_SDHC ? "SDHC/SDXC" : 
                            card_info.type == CARD_TYPE_MMC ? "MMC" : 
                            card_info.type == CARD_TYPE_SDIO ? "SDIO" : "SDSC");
        console_printf("Capacity: %.2f GB\n", ((float)card_info.capacity/1024));
        console_printf("Sector Size: %d bytes\n", card_info.sector_size);
        console_printf("Speed: %lu KHz\n", card_info.speed);
        console_printf("OK\n");
        return 0;
    }

    if (sdcard_args.test->count > 0)
    {
        if (sdcard_test_rw() != ESP_OK)
        {
            console_printf("Error: SD card test failed\n");
            return 1;
        }
        console_printf("SD card test passed successfully\n");
        console_printf("OK\n");
        return 0;
    }

    console_printf("Error: No valid subcommand\n");
    return 1;
}

static void console_register_commands(void)
{
    esp_console_register_help_command();
    
    // Initialize IMU command arguments
    imu_args.id = arg_lit0("i", "id", "Get IMU device ID");
    imu_args.end = arg_end(2);

    // Initialize RTCM command arguments
    rtcm_args.sync = arg_lit0("s", "sync", "Sync time from internet");
    rtcm_args.read = arg_lit0("r", "read", "Read current time and date");
    rtcm_args.end = arg_end(2);

    // Initialize LED command arguments with other arg initializations
    led_args.id = arg_lit0("i", "id", "Get LED driver device ID");
    led_args.end = arg_end(2);
    
    system_args.voltage = arg_lit0("v", "voltage", "Get system voltage");
    system_args.reboot = arg_lit0("r", "reboot", "Reboot system");
    system_args.id = arg_lit0("i", "id", "Get device ID");
    system_args.memory = arg_lit0("m", "memory", "Get heap memory info");
    system_args.end = arg_end(5);

    sdcard_args.info = arg_lit0("i", "info", "Get SD card information");
    sdcard_args.test = arg_lit0("t", "test", "Test SD card read/write");
    sdcard_args.end = arg_end(3);

    const console_cmd_t cmd_table[] = {
        {"version", "Get firmware version", NULL, &cmd_version},
        {"status", "Get system status", NULL, &cmd_status},
        {"imu", "IMU control and status", NULL, &cmd_imu},
        {"rtc", "RTC module control", NULL, &cmd_rtcm},
        {"led", "LED driver control", NULL, &cmd_led},
        {"system", "System control and status", NULL, &cmd_system},
        {"sdcard", "SD card control and status", NULL, &cmd_sdcard},
        {NULL, NULL, NULL, NULL}
    };
    
    const console_cmd_t *cmd = &cmd_table[0];
    while (cmd->command != NULL)
    {
        const esp_console_cmd_t console_cmd = {
            .command = cmd->command,
            .help = cmd->help,
            .hint = cmd->hint,
            .func = cmd->func,
        };
        ESP_ERROR_CHECK(esp_console_cmd_register(&console_cmd));
        cmd++;
    }
}

static void process_command(char* cmd)
{
    // Create local copy of command to preserve original
    char cmd_copy[MAX_CMDLINE_LENGTH];
    strncpy(cmd_copy, cmd, MAX_CMDLINE_LENGTH - 1);
    cmd_copy[MAX_CMDLINE_LENGTH - 1] = '\0';

    // Trim trailing whitespace and newlines
    size_t cmd_len = strlen(cmd_copy);
    while (cmd_len > 0 && (cmd_copy[cmd_len - 1] == ' ' || 
           cmd_copy[cmd_len - 1] == '\r' || 
           cmd_copy[cmd_len - 1] == '\n'))
    {
        cmd_copy[--cmd_len] = '\0';
    }

    // Skip empty commands
    if (cmd_len == 0)
    {
        tcp_console_write(PROMPT, strlen(PROMPT));
        return;
    }
    int ret;
    esp_err_t err = esp_console_run(cmd_copy, &ret);
    // Handle command execution results
    if (err == ESP_ERR_NOT_FOUND)
    {
        tcp_console_write("Command not found\r\n", 20);
    }
    else if (err == ESP_ERR_INVALID_ARG)
    {
        // command was empty or invalid
        tcp_console_write("Invalid arguments\r\n", 20);
    }
    else if (err == ESP_OK && ret != ESP_OK)
    {
        tcp_console_write("Command returned non-zero error code\r\n", 39);
    }
    else if (err != ESP_OK)
    {
        tcp_console_write("Internal error\r\n", 17);
    }

    // Always print prompt after command execution
    tcp_console_write(PROMPT, strlen(PROMPT));
}

// Main TCP console task
static void tcp_console_task(void* arg)
{
    struct sockaddr_in server_addr = {
        .sin_family = AF_INET,
        .sin_port = htons(TCP_PORT),
        .sin_addr.s_addr = htonl(INADDR_ANY)
    };

    // Create server socket
    server_socket = socket(AF_INET, SOCK_STREAM, 0);
    if (server_socket < 0)
    {
        ESP_LOGE(TAG, "Failed to create server socket");
        vTaskDelete(NULL);
        return;
    }

    // Enable address reuse
    int opt = 1;
    setsockopt(server_socket, SOL_SOCKET, SO_REUSEADDR, &opt, sizeof(opt));

    // Bind socket
    if (bind(server_socket, (struct sockaddr*)&server_addr, sizeof(server_addr)) != 0)
    {
        ESP_LOGE(TAG, "Socket bind failed");
        close(server_socket);
        vTaskDelete(NULL);
        return;
    }

    // Listen for connections
    if (listen(server_socket, 1) != 0)
    {
        ESP_LOGE(TAG, "Socket listen failed");
        close(server_socket);
        vTaskDelete(NULL);
        return;
    }

    ESP_LOGI(TAG, "TCP Console started on port %d", TCP_PORT);

    char *recv_buf = heap_caps_malloc(RECV_BUF_SIZE, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    char *cmd_buf = heap_caps_malloc(MAX_CMDLINE_LENGTH, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    
    if (!recv_buf || !cmd_buf)
    {
        ESP_LOGE(TAG, "Failed to allocate console buffers in PSRAM");
        // Free any successful allocations
        if (recv_buf) heap_caps_free(recv_buf);
        if (cmd_buf) heap_caps_free(cmd_buf);
        vTaskDelete(NULL);
        return;
    }
    
    size_t cmd_pos = 0;

    while (1)
    {
        struct sockaddr_in client_addr;
        socklen_t client_addr_len = sizeof(client_addr);

        // Accept client connection
        client_socket = accept(server_socket, (struct sockaddr*)&client_addr, &client_addr_len);
        if (client_socket < 0)
        {
            ESP_LOGE(TAG, "Accept failed");
            continue;
        }

        ESP_LOGI(TAG, "Client connected");
        
        // Clear command buffer at start of new connection
        memset(cmd_buf, 0, sizeof(cmd_buf));
        cmd_pos = 0;

        tcp_console_write(PROMPT, strlen(PROMPT));

        // Handle client communication
        while (1)
        {
            int len = recv(client_socket, recv_buf, sizeof(recv_buf) - 1, 0);
            if (len <= 0)
            {
                ESP_LOGI(TAG, "Client disconnected");
                break;
            }

            // Process received data character by character
            for (int i = 0; i < len; i++)
            {
                char c = recv_buf[i];
                
                // Handle backspace
                if (c == 0x7f || c == 0x08)
                {
                    if (cmd_pos > 0)
                    {
                        cmd_pos--;
                        tcp_console_write("\b \b", 3);
                    }
                    continue;
                }
                
                // Handle newline
                if (c == '\r' || c == '\n')
                {
                    tcp_console_write("\r\n", 2);
                    if (cmd_pos > 0)
                    {
                        cmd_buf[cmd_pos] = '\0';
                        process_command(cmd_buf);
                        // Clear command buffer after processing
                        memset(cmd_buf, 0, sizeof(cmd_buf));
                        cmd_pos = 0;
                    }
                    else
                    {
                        tcp_console_write(PROMPT, strlen(PROMPT));
                    }
                    continue;
                }
                
                // Echo character if not control character
                if (c >= 32 && c < 127)
                {
                    tcp_console_write(&c, 1);
                    
                    // Add to command buffer if space available
                    if (cmd_pos < sizeof(cmd_buf) - 1)
                    {
                        cmd_buf[cmd_pos++] = c;
                    }
                }
            }
        }

        // Cleanup client connection
        close(client_socket);
        client_socket = -1;
    }
}

static esp_err_t tcp_console_init(void) 
{
    if (is_console_initialized)
    {
        ESP_LOGW(TAG, "TCP Console already initialized");
        return ESP_ERR_INVALID_STATE;
    }

    // Create TCP console task
    BaseType_t task_created = xTaskCreate(tcp_console_task, 
                                        "tcp_console", 
                                        4096, 
                                        NULL, 
                                        5, 
                                        &tcp_console_task_handle);
    
    if (task_created != pdPASS)
    {
        ESP_LOGE(TAG, "Failed to create TCP console task");
        // esp_console_deinit();
        return ESP_ERR_NO_MEM;
    }

    is_console_initialized = true;
    return ESP_OK;
}

static void tcp_console_deinit(void)
{
    if (!is_console_initialized)
    {
        return;
    }

    if (client_socket >= 0)
    {
        close(client_socket);
        client_socket = -1;
    }
    
    if (server_socket >= 0)
    {
        close(server_socket);
        server_socket = -1;
    }

    if (tcp_console_task_handle)
    {
        vTaskDelete(tcp_console_task_handle);
        tcp_console_task_handle = NULL;
    }

    esp_console_deinit();
    is_console_initialized = false;
}


esp_err_t console_init(void)
{
    esp_console_repl_t *repl = NULL;
    esp_console_repl_config_t repl_config = ESP_CONSOLE_REPL_CONFIG_DEFAULT();
    repl_config.task_stack_size = (10*1024);
    repl_config.prompt = PROMPT;
    repl_config.max_cmdline_length = 256;
    
#if CONFIG_ESP_CONSOLE_USB_CDC
    esp_console_dev_usb_cdc_config_t cdc_config = ESP_CONSOLE_DEV_CDC_CONFIG_DEFAULT();
    ESP_ERROR_CHECK(esp_console_new_repl_usb_cdc(&cdc_config, &repl_config, &repl));
#elif CONFIG_ESP_CONSOLE_UART
    esp_console_dev_uart_config_t uart_config = ESP_CONSOLE_DEV_UART_CONFIG_DEFAULT();
    ESP_ERROR_CHECK(esp_console_new_repl_uart(&uart_config, &repl_config, &repl));
#endif
    tcp_console_init();
    console_register_commands();
    
    return esp_console_start_repl(repl);
}


