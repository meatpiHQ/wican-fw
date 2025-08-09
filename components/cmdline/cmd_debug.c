/*
 * WiCAN - Debug command
 */
#include "cmd_debug.h"
#include "cmdline.h"
#include "esp_console.h"
#include "argtable3/argtable3.h"
#include "esp_log.h"
#include <stdio.h>
#include <string.h>
#include <sys/stat.h>
#include "hw_config.h"
#include "cJSON.h"

static const char *TAG = "cmd_debug";

static struct {
    struct arg_int *enable;
    struct arg_end *end;
} debug_args;

static esp_err_t read_file_into_buffer(const char *path, char **out_buf, size_t *out_len)
{
    FILE *f = fopen(path, "r");
    if (!f) return ESP_FAIL;
    if (fseek(f, 0, SEEK_END) != 0) { fclose(f); return ESP_FAIL; }
    long sz = ftell(f);
    if (sz < 0) { fclose(f); return ESP_FAIL; }
    rewind(f);
    char *buf = (char*)malloc((size_t)sz + 1);
    if (!buf) { fclose(f); return ESP_ERR_NO_MEM; }
    size_t n = fread(buf, 1, (size_t)sz, f);
    fclose(f);
    buf[n] = 0;
    if (out_buf) *out_buf = buf; else free(buf);
    if (out_len) *out_len = n;
    return ESP_OK;
}

static esp_err_t write_buffer_to_file_atomic(const char *path, const char *data)
{
    char tmppath[128];
    snprintf(tmppath, sizeof(tmppath), "%s.tmp", path);
    FILE *f = fopen(tmppath, "w");
    if (!f) return ESP_FAIL;
    size_t n = fwrite(data, 1, strlen(data), f);
    fclose(f);
    if (n != strlen(data)) { remove(tmppath); return ESP_FAIL; }
    // Replace original
    remove(path);
    if (rename(tmppath, path) != 0) { remove(tmppath); return ESP_FAIL; }
    return ESP_OK;
}

static esp_err_t persist_debug_state(bool enable)
{
    const char *cfg_path = FS_MOUNT_POINT "/config.json";
    cJSON *root = NULL;

    // Try to read existing config
    char *buf = NULL; size_t len = 0;
    if (read_file_into_buffer(cfg_path, &buf, &len) == ESP_OK && buf && len > 0) {
        root = cJSON_Parse(buf);
        free(buf);
    }

    if (!root) {
        root = cJSON_CreateObject();
        if (!root) return ESP_ERR_NO_MEM;
    }

    cJSON *debug_item = cJSON_GetObjectItem(root, "debug");
    if (!debug_item) {
        cJSON_AddStringToObject(root, "debug", enable ? "enabled" : "disabled");
    } else {
        if (cJSON_IsString(debug_item)) {
            cJSON_SetValuestring(debug_item, enable ? "enabled" : "disabled");
        } else {
            // Replace non-string with string
            cJSON_DeleteItemFromObject(root, "debug");
            cJSON_AddStringToObject(root, "debug", enable ? "enabled" : "disabled");
        }
    }

    char *out = cJSON_PrintUnformatted(root);
    cJSON_Delete(root);
    if (!out) return ESP_ERR_NO_MEM;

    esp_err_t res = write_buffer_to_file_atomic(cfg_path, out);
    cJSON_free(out);
    return res;
}

static void apply_runtime_logging(bool enable)
{
    // Set default log level
    esp_log_level_set("*", enable ? ESP_LOG_DEBUG : ESP_LOG_WARN);
}

static int cmd_debug(int argc, char **argv)
{
    int nerrors = arg_parse(argc, argv, (void **)&debug_args);
    if (nerrors != 0) {
        arg_print_errors(stderr, debug_args.end, argv[0]);
        return 1;
    }

    if (debug_args.enable->count == 0) {
        cmdline_printf("Usage: debug -e 0|1\n");
        return 1;
    }

    int en = debug_args.enable->ival[0];
    if (en != 0 && en != 1) {
        cmdline_printf("Error: -e must be 0 or 1\n");
        return 1;
    }

    bool enable = (en == 1);

    apply_runtime_logging(enable);
    esp_err_t pr = persist_debug_state(enable);
    if (pr != ESP_OK) {
        cmdline_printf("Warning: failed to persist to %s/config.json\n", FS_MOUNT_POINT);
    }
    if(enable) {
        cmdline_printf("Debug output might severely affect performance make sure to disable it in production\n");
    } 
    cmdline_printf("Debug %s\nOK\n", enable ? "enabled" : "disabled");
    return 0;
}

esp_err_t cmd_debug_register(void)
{
    debug_args.enable = arg_int1("e", "enable", "0|1", "Enable (1) or disable (0) debug logging and persist to config.json");
    debug_args.end = arg_end(2);

    const esp_console_cmd_t cmd = {
        .command = "debug",
        .help = "Enable/disable debug logging and persist to config.json",
        .hint = "Usage: debug -e 0|1",
        .func = &cmd_debug,
        .argtable = &debug_args
    };
    return esp_console_cmd_register(&cmd);
}
