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

static int cmd_version(int argc, char **argv)
{
    const esp_partition_t *running = esp_ota_get_running_partition();
    esp_app_desc_t running_app_info;

    if (esp_ota_get_partition_description(running, &running_app_info) != ESP_OK) {
        printf("Error: Failed to get partition info\n");
        return 1;
    }

    printf("Version: %s\n", running_app_info.version);
    printf("Project Name: %s\n", running_app_info.project_name);
    printf("Build Time: %s %s\n", running_app_info.date, running_app_info.time);
    printf("IDF Version: %s\n", running_app_info.idf_ver);
    printf("Running Partition: %s\n", running->label);
    printf("OK\n");
    return 0;
}

static int cmd_status(int argc, char **argv) 
{
    printf("System Status: OK\n");
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
            printf("Error: Failed to read IMU ID\n");
            return 1;
        }
        printf("IMU Device ID: 0x%02X\n", id);
        printf("OK\n");
        return 0;
    }

    printf("Error: No valid subcommand\n");
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
            printf("Error: Failed to sync time\n");
            return 1;
        }
        printf("Time synchronized successfully\n");
        printf("OK\n");
        return 0;
    }

    if (rtcm_args.read->count > 0) {
        uint8_t hour, min, sec;
        uint8_t year, month, day, weekday;
        
        esp_err_t ret_time = rtcm_get_time(&hour, &min, &sec);
        esp_err_t ret_date = rtcm_get_date(&year, &month, &day, &weekday);

        if (ret_time == ESP_OK && ret_date == ESP_OK) {
            printf("20%02X-%02X-%02X %02X:%02X:%02X (Day %d)\n", 
                   year, month, day, hour, min, sec, weekday);
            printf("OK\n");
            return 0;
        } else {
            printf("Error: Failed to read RTC time/date\n");
            return 1;
        }
    }

    printf("Error: No valid subcommand\n");
    return 1;
}

void console_register_commands(void)
{
    esp_console_register_help_command();
    
    // Initialize IMU command arguments
    imu_args.id = arg_lit0("i", "id", "Get IMU device ID");
    imu_args.end = arg_end(2);

    // Initialize RTCM command arguments
    rtcm_args.sync = arg_lit0("s", "sync", "Sync time from internet");
    rtcm_args.read = arg_lit0("r", "read", "Read current time and date");
    rtcm_args.end = arg_end(2);
    
    // Add RTCM command to command table
    const console_cmd_t cmd_table[] = {
        {"version", "Get firmware version", NULL, &cmd_version},
        {"status", "Get system status", NULL, &cmd_status},
        {"imu", "IMU control and status", NULL, &cmd_imu},
        {"rtc", "RTC module control", NULL, &cmd_rtcm},  // Add this line
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

esp_err_t console_init(void)
{
    esp_console_repl_t *repl = NULL;
    esp_console_repl_config_t repl_config = ESP_CONSOLE_REPL_CONFIG_DEFAULT();
    repl_config.task_stack_size = (10*1024);
    repl_config.prompt = "wican> ";
    repl_config.max_cmdline_length = 256;
    
#if CONFIG_ESP_CONSOLE_USB_CDC
    esp_console_dev_usb_cdc_config_t cdc_config = ESP_CONSOLE_DEV_CDC_CONFIG_DEFAULT();
    ESP_ERROR_CHECK(esp_console_new_repl_usb_cdc(&cdc_config, &repl_config, &repl));
#elif CONFIG_ESP_CONSOLE_UART
    esp_console_dev_uart_config_t uart_config = ESP_CONSOLE_DEV_UART_CONFIG_DEFAULT();
    ESP_ERROR_CHECK(esp_console_new_repl_uart(&uart_config, &repl_config, &repl));
#endif

    console_register_commands();
    
    return esp_console_start_repl(repl);
}
