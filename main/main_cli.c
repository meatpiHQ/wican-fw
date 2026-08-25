/**
 * @file main_cli.c
 * @brief Composition-root CLI commands. Three kinds live here, not in a
 *        component:
 *         - `system`: a COMPOSITE (dev_status memory/tasks + battery
 *           voltage + restart_tracker reboot + chip/app info) — only
 *           main knows all of them; legacy argtable interface preserved
 *           (-v/-r/-i/-m, plus -t tasks as the v6 addition);
 *         - `debug`: log_manager's knob, but log_manager sits BELOW
 *           cmdline_manager (registering there would be a dependency
 *           cycle — same reason /api/logs lives in api_http);
 *         - pending stubs for legacy commands whose v6 backer doesn't
 *           exist yet (only the composition root knows what's missing).
 */
#include <inttypes.h>
#include <stdlib.h>
#include <string.h>

#include "argtable3/argtable3.h"
#include "esp_app_desc.h"
#include "esp_attr.h"
#include "esp_chip_info.h"
#include "esp_flash.h"
#include "esp_private/esp_clk.h"
#include "esp_system.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

#include "battery_monitor.h"
#include "ble_manager.h"
#include "cmdline_manager.h"
#include "dev_status_manager.h"
#include "log_manager.h"
#include "restart_tracker.h"
#include "settings_manager.h"

#include "main_cli.h"

/* ---- system (composite; legacy cmd_system.c interface) ------------------------ */

static struct
{
    struct arg_lit *voltage;
    struct arg_lit *reboot;
    struct arg_lit *info;
    struct arg_lit *memory;
    struct arg_lit *tasks;
    struct arg_end *end;
} s_system_args;

static int system_info(void)
{
    const esp_app_desc_t *app = esp_app_get_description();
    esp_chip_info_t chip;
    uint32_t flash_size = 0;
    char uptime[32];

    cmdline_printf("Device ID: %s\n", dev_status_manager_device_id());
    cmdline_printf("Running Partition: %s\n",
                   dev_status_manager_partition_label());
    cmdline_printf("App Version: %s\n", app->version);
    cmdline_printf("Project Name: %s\n", app->project_name);
    cmdline_printf("Build Time: %s %s\n", app->date, app->time);
    cmdline_printf("IDF Version: %s\n", app->idf_ver);

    esp_chip_info(&chip);
    cmdline_printf("Chip Model: %s\n",
                   chip.model == CHIP_ESP32S3 ? "ESP32-S3" : "Unknown");
    cmdline_printf("CPU Cores: %d\n", chip.cores);
    cmdline_printf("CPU Frequency: %d MHz\n",
                   esp_clk_cpu_freq() / 1000000);
    cmdline_printf("Chip Revision: v%d.%d\n", chip.revision / 100,
                   chip.revision % 100);

    if (esp_flash_get_size(NULL, &flash_size) == ESP_OK)
    {
        cmdline_printf("Flash Size: %" PRIu32 " MB\n",
                       flash_size / (1024 * 1024));
    }

    cmdline_printf("Minimum free heap size: %" PRIu32 " bytes\n",
                   esp_get_minimum_free_heap_size());

    if (dev_status_manager_format_uptime(uptime, sizeof(uptime)) > 0)
    {
        cmdline_printf("System Uptime: %s\n", uptime);
    }

    cmdline_printf("OK\n");
    return 0;
}

static int system_memory(void)
{
    dev_status_memory_t mem;

    if (dev_status_manager_memory(&mem) != ESP_OK)
    {
        cmdline_printf("Error: memory info unavailable\n");
        return 1;
    }

    cmdline_printf("RAM Free: %" PRIu32 " bytes\n", mem.internal.free);
    cmdline_printf("RAM Largest block: %" PRIu32 " bytes\n",
                   mem.internal.largest_block);
    cmdline_printf("RAM Total: %" PRIu32 " bytes\n", mem.internal.total);
    cmdline_printf("RAM Min free ever: %" PRIu32 " bytes\n",
                   mem.internal.min_free);
    cmdline_printf("PSRAM Free: %" PRIu32 " bytes\n", mem.psram.free);
    cmdline_printf("PSRAM Largest block: %" PRIu32 " bytes\n",
                   mem.psram.largest_block);
    cmdline_printf("PSRAM Min free ever: %" PRIu32 " bytes\n",
                   mem.psram.min_free);
    cmdline_printf("OK\n");
    return 0;
}

#define CLI_TASKS_MAX 48

static int system_tasks(void)
{
    /* two snapshots 1 s apart: CPU% = runtime delta over the window
       (× core count in the denominator — total_us is per-core time).
       PSRAM statics; serialized by the console (one command at a time) */
    static dev_status_task_t t0[CLI_TASKS_MAX] EXT_RAM_BSS_ATTR;
    static dev_status_task_t t1[CLI_TASKS_MAX] EXT_RAM_BSS_ATTR;
    size_t n0 = 0;
    size_t n1 = 0;
    uint64_t tot0 = 0;
    uint64_t tot1 = 0;
    esp_err_t err =
        dev_status_manager_task_stats(t0, CLI_TASKS_MAX, &n0, &tot0);

    if (err == ESP_OK)
    {
        vTaskDelay(pdMS_TO_TICKS(1000));
        err = dev_status_manager_task_stats(t1, CLI_TASKS_MAX, &n1, &tot1);
    }

    if (err != ESP_OK)
    {
        cmdline_printf("Error: tasks unavailable (%s)\n",
                       esp_err_to_name(err));
        return 1;
    }

    uint64_t window = (tot1 - tot0) * CONFIG_FREERTOS_NUMBER_OF_CORES;

    cmdline_printf("name              st core prio stack-hw   cpu%%\n");

    for (size_t i = 0; i < n1; i++)
    {
        /* match by name (task_stats sorts by runtime, so indexes move) */
        uint64_t prev = 0;

        for (size_t j = 0; j < n0; j++)
        {
            if (strcmp(t1[i].name, t0[j].name) == 0)
            {
                prev = t0[j].runtime_us;
                break;
            }
        }

        uint64_t delta = (t1[i].runtime_us >= prev)
                             ? t1[i].runtime_us - prev
                             : 0;
        unsigned pct10 = (window > 0)
                             ? (unsigned)((delta * 1000) / window)
                             : 0;
        char core[8] = "*";

        if (t1[i].core >= 0)
        {
            snprintf(core, sizeof(core), "%d", t1[i].core);
        }

        cmdline_printf("%-16s  %c  %3s  %3u %8u %3u.%u\n", t1[i].name,
                       t1[i].state, core, (unsigned)t1[i].prio,
                       (unsigned)t1[i].stack_hw, pct10 / 10, pct10 % 10);
    }

    cmdline_printf("tasks: %u  (st: X run R ready B blocked S suspended;"
                   " stack-hw = never-used bytes)\n",
                   (unsigned)n1);
    cmdline_printf("OK\n");
    return 0;
}

static int cmd_system(int argc, char **argv)
{
    int nerrors = arg_parse(argc, argv, (void **)&s_system_args);

    if (nerrors != 0)
    {
        arg_print_errors(stderr, s_system_args.end, argv[0]);
        return 1;
    }

    if (s_system_args.voltage->count > 0)
    {
        float voltage = 0; /* legacy read sleep_mode; v6 owner is
                              battery_monitor */

        if (battery_monitor_voltage(&voltage) != ESP_OK)
        {
            cmdline_printf("Error: Failed to read voltage\n");
            return 1;
        }

        cmdline_printf("System Voltage: %.2f V\n", voltage);
        cmdline_printf("OK\n");
        return 0;
    }

    if (s_system_args.reboot->count > 0)
    {
        cmdline_printf("System will reboot now...\n");
        vTaskDelay(pdMS_TO_TICKS(2000)); /* legacy grace period */
        restart_tracker_restart(
            RESTART_TRACKER_PLANNED_REASON_USER_REQUEST,
            RESTART_TRACKER_SOURCE_CMDLINE, 0);
    }

    if (s_system_args.info->count > 0)
    {
        return system_info();
    }

    if (s_system_args.memory->count > 0)
    {
        return system_memory();
    }

    if (s_system_args.tasks->count > 0)
    {
        return system_tasks();
    }

    cmdline_printf("Error: No valid subcommand\n");
    return 1;
}

/* ---- debug (log_manager's knob; cycle-safe home) ------------------------------- */

static int cmd_debug(int argc, char **argv)
{
    static const struct
    {
        const char *name;
        esp_log_level_t level;
    } LEVELS[] =
    {
        { "none", ESP_LOG_NONE },   { "error", ESP_LOG_ERROR },
        { "warn", ESP_LOG_WARN },   { "info", ESP_LOG_INFO },
        { "debug", ESP_LOG_DEBUG }, { "verbose", ESP_LOG_VERBOSE },
    };

    /* legacy interface: debug -e/--enable <0|1> — everything DEBUG/WARN */
    if (argc == 3 && (strcmp(argv[1], "-e") == 0 ||
                      strcmp(argv[1], "--enable") == 0))
    {
        if (strcmp(argv[2], "1") != 0 && strcmp(argv[2], "0") != 0)
        {
            cmdline_printf("Error: -e must be 0 or 1\n");
            return 1;
        }

        bool enable = (argv[2][0] == '1');

        log_manager_set_level("*", enable ? ESP_LOG_DEBUG : ESP_LOG_WARN);

        if (enable)
        {
            cmdline_printf("Debug output might severely affect "
                           "performance make sure to disable it in "
                           "production\n");
        }

        cmdline_printf("Debug %s\nOK\n", enable ? "enabled" : "disabled");
        return 0; /* ephemeral (reboot restores defaults); persist via
                     /api/settings/log_manager */
    }

    if (argc >= 3)
    {
        for (size_t i = 0; i < sizeof(LEVELS) / sizeof(LEVELS[0]); i++)
        {
            if (strcmp(argv[2], LEVELS[i].name) == 0)
            {
                esp_err_t err =
                    log_manager_set_level(argv[1], LEVELS[i].level);

                cmdline_printf("%s -> %s (%s; resets on reboot)\n",
                               argv[1], argv[2], esp_err_to_name(err));
                return err == ESP_OK ? 0 : 1;
            }
        }

        cmdline_printf("unknown level '%s'\n", argv[2]);
        return 1;
    }

    cmdline_printf("dropped log lines: %u\nsinks:\n",
                   (unsigned)log_manager_dropped_count());

    const char *name = NULL;
    bool enabled = false;

    for (size_t i = 0;
         log_manager_sink_get(i, &name, &enabled) == ESP_OK; i++)
    {
        cmdline_printf("  %s (%s)\n", name, enabled ? "on" : "off");
    }

    cmdline_printf("Usage: debug -e/--enable <0|1>\n");
    cmdline_printf(
        "       debug <tag> <none|error|warn|info|debug|verbose>\n");
    return 0;
}

/* ---- factoryreset (legacy two-step confirm flow, cmd_factoryreset.c) ------------ */

#define FACTORY_RESET_TIMEOUT_MS 60000 /* legacy 60 s confirm window */

static bool s_freset_pending;
static int64_t s_freset_deadline_ms;

static int64_t uptime_ms_now(void)
{
    return (int64_t)(xTaskGetTickCount() * portTICK_PERIOD_MS);
}

static int cmd_factoryreset(int argc, char **argv)
{
    bool confirm = (argc >= 2 && (strcmp(argv[1], "-c") == 0 ||
                                  strcmp(argv[1], "--confirm") == 0));

    if (confirm)
    {
        if (!s_freset_pending)
        {
            cmdline_printf("Error: No factory reset operation pending.\n");
            cmdline_printf("Please run 'factoryreset' first to initiate "
                           "the process.\n");
            return 1;
        }

        if (uptime_ms_now() > s_freset_deadline_ms)
        {
            s_freset_pending = false;
            cmdline_printf("Error: Factory reset confirmation timeout "
                           "expired.\n");
            cmdline_printf("Please run 'factoryreset' again to restart "
                           "the process.\n");
            return 1;
        }

        cmdline_printf("Starting factory reset...\n");
        cmdline_printf("Deleting settings files...\n");

        esp_err_t err = settings_manager_factory_reset();

        s_freset_pending = false;

        if (err != ESP_OK)
        {
            cmdline_printf("Error: factory reset failed (%s)\n",
                           esp_err_to_name(err));
            return 1;
        }

        cmdline_printf("Factory reset completed successfully.\n");
        cmdline_printf("System will reboot in 3 seconds...\n");
        vTaskDelay(pdMS_TO_TICKS(3000)); /* legacy grace (msg delivery) */
        restart_tracker_restart(RESTART_TRACKER_PLANNED_REASON_FACTORY_RESET,
                                RESTART_TRACKER_SOURCE_CMDLINE, 0);
    }

    if (s_freset_pending && uptime_ms_now() <= s_freset_deadline_ms)
    {
        cmdline_printf("Factory reset already pending. You have %ld "
                       "seconds to confirm.\n",
                       (long)((s_freset_deadline_ms - uptime_ms_now()) /
                              1000));
        cmdline_printf("Run 'factoryreset --confirm' to proceed or wait "
                       "for timeout.\n");
        return 0;
    }

    s_freset_pending = true;
    s_freset_deadline_ms = uptime_ms_now() + FACTORY_RESET_TIMEOUT_MS;

    cmdline_printf("=== FACTORY RESET WARNING ===\n");
    cmdline_printf("This will permanently delete ALL settings (WiFi, "
                   "BLE, MQTT, bridges, ...)\n");
    cmdline_printf("and reboot to factory defaults (AP mode).\n");
    cmdline_printf("Certificate sets and files on /data or the SD card "
                   "are NOT deleted.\n");
    cmdline_printf("\n");
    cmdline_printf("This action CANNOT be undone!\n");
    cmdline_printf("\n");
    cmdline_printf("To proceed, run: factoryreset --confirm\n");
    cmdline_printf("You have 60 seconds to confirm.\n");
    cmdline_printf("============================\n");
    return 0;
}

/* ---- blast (BLE TX throughput test source) --------------------------------------- */

/** Pump N bytes into the BLE data pipe (FFF1 notifies) — the bench's
 *  TX-throughput source when WiFi is down (the TCP path needs WiFi;
 *  with interface_manager active, a connected BLE client suspends
 *  WiFi, which is exactly the clean-radio measurement condition). */
static int cmd_blast(int argc, char **argv)
{
    static uint8_t buf[488] EXT_RAM_BSS_ATTR;
    size_t total = 262144;

    if (argc >= 2)
    {
        total = (size_t)strtoul(argv[1], NULL, 10);

        if (total == 0 || total > (4u << 20))
        {
            cmdline_printf("Error: bytes must be 1..4194304\n");
            return 1;
        }
    }

    for (size_t i = 0; i < sizeof(buf); i++)
    {
        buf[i] = (uint8_t)i;
    }

    size_t sent = 0;
    int64_t t0 = esp_timer_get_time();

    while (sent < total)
    {
        if (ble_manager_send(buf, sizeof(buf)) == ESP_OK)
        {
            sent += sizeof(buf);
        }
        else
        {
            vTaskDelay(pdMS_TO_TICKS(2)); /* queue full / not connected */

            if (esp_timer_get_time() - t0 > 60000000LL)
            {
                cmdline_printf("Error: blast timed out (%u sent)\n",
                               (unsigned)sent);
                return 1;
            }
        }
    }

    int64_t ms = (esp_timer_get_time() - t0) / 1000;

    cmdline_printf("blasted %u bytes enqueued in %lld ms\n",
                   (unsigned)sent, (long long)ms);
    return 0;
}

/* ---- pending stubs (no v6 backing component yet) -------------------------------- */

static int cmd_pending(int argc, char **argv)
{
    (void)argc;
    cmdline_printf("pending: %s is not in the v6 firmware yet\n",
                   argv[0]);
    return 0;
}

/* ---- registration ---------------------------------------------------------------- */

esp_err_t main_cli_register(void)
{
    s_system_args.voltage = arg_lit0("v", "voltage", "Get system voltage");
    s_system_args.reboot = arg_lit0("r", "reboot", "Reboot system");
    s_system_args.info =
        arg_lit0("i", "info", "Get system information including device ID");
    s_system_args.memory = arg_lit0("m", "memory", "Get heap memory info");
    s_system_args.tasks =
        arg_lit0("t", "tasks", "Get task stack headroom");
    s_system_args.end = arg_end(5);

    static const esp_console_cmd_t CMDS[] =
    {
        { .command = "system", .help = "System control and status",
          .hint = "Options: -v/--voltage, -r/--reboot, -i/--info, "
                  "-m/--memory, -t/--tasks",
          .func = cmd_system, .argtable = &s_system_args },
        { .command = "debug", .help = "Runtime log level control",
          .hint = "Options: -e/--enable <0|1>; or: debug <tag> "
                  "<none|error|warn|info|debug|verbose>",
          .func = cmd_debug },
        { .command = "blast",
          .help = "Pump N bytes to the BLE data pipe (throughput test)",
          .hint = "Usage: blast [bytes]", .func = cmd_blast },
        { .command = "eth", .help = "USB Ethernet status (pending)",
          .func = cmd_pending },
        { .command = "conn",
          .help = "Connection manager uplink status (pending)",
          .func = cmd_pending },
        { .command = "wusb",
          .help = "WUSB3801 USB-C controller (pending)",
          .func = cmd_pending },
        { .command = "ping", .help = "Ping a host (pending)",
          .func = cmd_pending },
        { .command = "factoryreset",
          .help = "Perform factory reset - deletes all settings and "
                  "reboots",
          .hint = "Options: -c/--confirm",
          .func = cmd_factoryreset },
    };

    for (size_t i = 0; i < sizeof(CMDS) / sizeof(CMDS[0]); i++)
    {
        esp_err_t err = cmdline_manager_register(&CMDS[i]);

        if (err != ESP_OK)
        {
            return err;
        }
    }

    return ESP_OK;
}
