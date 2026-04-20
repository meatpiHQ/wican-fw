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

#include "restart_tracker_coredump.h"

#include <dirent.h>
#include <errno.h>
#include <inttypes.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <sys/unistd.h>
#include <time.h>

#include <esp_core_dump.h>
#include <esp_log.h>
#include <esp_partition.h>
#include <esp_timer.h>
#include <esp_vfs.h>

#include <freertos/FreeRTOS.h>
#include <freertos/task.h>

#include "restart_tracker.h"

static const char *TAG = "restart_tracker_cd";

#define RESTART_TRACKER_COREDUMP_CHUNK_SIZE 4096U
#define RESTART_TRACKER_COREDUMP_ARCHIVE_TASK_STACK 8192U
#define RESTART_TRACKER_COREDUMP_ARCHIVE_TASK_PRIORITY 4U

typedef struct
{
    char name[256];
    char data_path[768];
    char meta_path[768];
    time_t mtime;
} restart_tracker_coredump_file_t;

typedef struct
{
    char dir_path[512];
    char panic_reason[192];
    char time_tag[32];
    char task_tag[32];
    char base_name[96];
    char data_path[768];
    char meta_path[768];
    restart_tracker_record_t latest_record;
    esp_core_dump_summary_t summary;
} restart_tracker_coredump_archive_ctx_t;

static char s_coredump_mount_path[ESP_VFS_PATH_MAX + 1];
static TaskHandle_t s_coredump_archive_task;

static bool restart_tracker_coredump_has_suffix(const char *value, const char *suffix)
{
    size_t value_len;
    size_t suffix_len;

    if (value == NULL || suffix == NULL)
    {
        return false;
    }

    value_len = strlen(value);
    suffix_len = strlen(suffix);
    if (suffix_len > value_len)
    {
        return false;
    }

    return strcmp(value + value_len - suffix_len, suffix) == 0;
}

static void restart_tracker_coredump_format_timestamp(time_t timestamp, char *buf, size_t buf_len, const char *format)
{
    struct tm timeinfo;

    if (buf == NULL || buf_len == 0U)
    {
        return;
    }

    if (timestamp <= 946684800 || gmtime_r(&timestamp, &timeinfo) == NULL || strftime(buf, buf_len, format, &timeinfo) == 0U)
    {
        strlcpy(buf, "unsynced", buf_len);
    }
}

static void restart_tracker_coredump_sanitize_filename_component(const char *input, char *output, size_t output_len)
{
    size_t out_index = 0;

    if (output == NULL || output_len == 0U)
    {
        return;
    }

    if (input == NULL)
    {
        strlcpy(output, "unknown", output_len);
        return;
    }

    for (size_t i = 0; input[i] != '\0' && out_index + 1U < output_len; ++i)
    {
        char ch = input[i];
        if ((ch >= 'a' && ch <= 'z') ||
            (ch >= 'A' && ch <= 'Z') ||
            (ch >= '0' && ch <= '9'))
        {
            output[out_index++] = ch;
        }
        else if (ch == '-' || ch == '_')
        {
            output[out_index++] = ch;
        }
        else if (ch == ' ' || ch == '.')
        {
            output[out_index++] = '_';
        }
    }

    output[out_index] = '\0';
    if (output[0] == '\0')
    {
        strlcpy(output, "unknown", output_len);
    }
}

static esp_err_t restart_tracker_coredump_get_directory(char *path, size_t path_len)
{
    int written;

    if (path == NULL || path_len == 0U)
    {
        return ESP_ERR_INVALID_ARG;
    }

    if (s_coredump_mount_path[0] == '\0')
    {
        return ESP_ERR_INVALID_STATE;
    }

    written = snprintf(path, path_len, "%s/%s", s_coredump_mount_path, RESTART_TRACKER_COREDUMP_SUBDIR);
    if (written <= 0 || written >= (int)path_len)
    {
        return ESP_ERR_INVALID_SIZE;
    }

    return ESP_OK;
}

static esp_err_t restart_tracker_coredump_ensure_dir(const char *path)
{
    struct stat st;

    if (path == NULL)
    {
        return ESP_ERR_INVALID_ARG;
    }

    if (stat(path, &st) == 0)
    {
        return S_ISDIR(st.st_mode) ? ESP_OK : ESP_FAIL;
    }

    if (mkdir(path, 0775) == 0 || errno == EEXIST)
    {
        return ESP_OK;
    }

    ESP_LOGE(TAG, "Failed to create directory %s (errno=%d)", path, errno);
    return ESP_FAIL;
}

bool restart_tracker_coredump_filename_is_valid(const char *name)
{
    if (name == NULL || name[0] == '\0')
    {
        return false;
    }

    if (strstr(name, "..") != NULL || strchr(name, '/') != NULL || strchr(name, '\\') != NULL)
    {
        return false;
    }

    return restart_tracker_coredump_has_suffix(name, ".elf");
}

static int restart_tracker_coredump_file_compare(const void *lhs, const void *rhs)
{
    const restart_tracker_coredump_file_t *left = (const restart_tracker_coredump_file_t *)lhs;
    const restart_tracker_coredump_file_t *right = (const restart_tracker_coredump_file_t *)rhs;

    if (left->mtime < right->mtime)
    {
        return -1;
    }
    if (left->mtime > right->mtime)
    {
        return 1;
    }

    return strcmp(left->name, right->name);
}

static esp_err_t restart_tracker_coredump_prune_files(size_t keep_count)
{
    char dir_path[512];
    DIR *dir;
    struct dirent *entry;
    size_t count = 0;
    size_t index = 0;
    restart_tracker_coredump_file_t *files;

    if (restart_tracker_coredump_get_directory(dir_path, sizeof(dir_path)) != ESP_OK)
    {
        return ESP_OK;
    }

    dir = opendir(dir_path);
    if (dir == NULL)
    {
        return ESP_OK;
    }

    while ((entry = readdir(dir)) != NULL)
    {
        if (restart_tracker_coredump_has_suffix(entry->d_name, ".elf"))
        {
            ++count;
        }
    }

    if (count <= keep_count)
    {
        closedir(dir);
        return ESP_OK;
    }

    rewinddir(dir);
    files = calloc(count, sizeof(*files));
    if (files == NULL)
    {
        closedir(dir);
        return ESP_ERR_NO_MEM;
    }

    while ((entry = readdir(dir)) != NULL && index < count)
    {
        struct stat st;

        if (!restart_tracker_coredump_has_suffix(entry->d_name, ".elf"))
        {
            continue;
        }

        strlcpy(files[index].name, entry->d_name, sizeof(files[index].name));
        snprintf(files[index].data_path, sizeof(files[index].data_path), "%s/%s", dir_path, entry->d_name);
        snprintf(files[index].meta_path,
                 sizeof(files[index].meta_path),
                 "%s/%.*s.json",
                 dir_path,
                 (int)(strlen(entry->d_name) - 4U),
                 entry->d_name);

        if (stat(files[index].data_path, &st) == 0)
        {
            files[index].mtime = st.st_mtime;
        }
        ++index;
    }
    closedir(dir);

    qsort(files, index, sizeof(*files), restart_tracker_coredump_file_compare);

    for (size_t i = 0; i + keep_count < index; ++i)
    {
        unlink(files[i].data_path);
        unlink(files[i].meta_path);
        ESP_LOGI(TAG, "Pruned archived coredump %s", files[i].name);
    }

    free(files);
    return ESP_OK;
}

#if CONFIG_ESP_COREDUMP_ENABLE_TO_FLASH
static esp_err_t restart_tracker_coredump_write_file(const esp_partition_t *partition, size_t image_size, const char *path)
{
    FILE *file;
    uint8_t *buffer;
    size_t offset = 0;

    if (partition == NULL || path == NULL)
    {
        return ESP_ERR_INVALID_ARG;
    }

    file = fopen(path, "wb");
    if (file == NULL)
    {
        ESP_LOGE(TAG, "Failed to open %s for coredump archive", path);
        return ESP_FAIL;
    }

    buffer = malloc(RESTART_TRACKER_COREDUMP_CHUNK_SIZE);
    if (buffer == NULL)
    {
        fclose(file);
        unlink(path);
        return ESP_ERR_NO_MEM;
    }

    while (offset < image_size)
    {
        size_t chunk = image_size - offset;
        if (chunk > RESTART_TRACKER_COREDUMP_CHUNK_SIZE)
        {
            chunk = RESTART_TRACKER_COREDUMP_CHUNK_SIZE;
        }

        esp_err_t err = esp_partition_read(partition, offset, buffer, chunk);
        if (err != ESP_OK)
        {
            ESP_LOGE(TAG, "Failed to read coredump partition at offset %u (%s)", (unsigned)offset, esp_err_to_name(err));
            free(buffer);
            fclose(file);
            unlink(path);
            return err;
        }

        if (fwrite(buffer, 1, chunk, file) != chunk)
        {
            ESP_LOGE(TAG, "Failed to write archived coredump %s", path);
            free(buffer);
            fclose(file);
            unlink(path);
            return ESP_FAIL;
        }

        offset += chunk;
    }

    free(buffer);
    fclose(file);
    return ESP_OK;
}

static void restart_tracker_coredump_add_optional_string(cJSON *obj, const char *key, const char *value)
{
    if (obj != NULL && key != NULL && value != NULL && value[0] != '\0')
    {
        cJSON_AddStringToObject(obj, key, value);
    }
}

static esp_err_t restart_tracker_coredump_write_metadata(const char *path,
                                                         const char *file_name,
                                                         size_t image_size,
                                                         bool image_valid,
                                                         const restart_tracker_record_t *latest_record,
                                                         bool latest_valid,
                                                         const esp_core_dump_summary_t *summary,
                                                         bool summary_valid,
                                                         const char *panic_reason,
                                                         bool panic_reason_valid,
                                                         time_t archived_time)
{
    cJSON *root = cJSON_CreateObject();
    char archived_timestamp[32];
    char *payload;
    FILE *file;

    if (root == NULL)
    {
        return ESP_ERR_NO_MEM;
    }

    restart_tracker_coredump_format_timestamp(archived_time,
                                              archived_timestamp,
                                              sizeof(archived_timestamp),
                                              "%Y-%m-%dT%H:%M:%SZ");

    cJSON_AddStringToObject(root, "file_name", file_name);
    cJSON_AddNumberToObject(root, "size_bytes", (double)image_size);
    cJSON_AddBoolToObject(root, "coredump_valid", image_valid);
    cJSON_AddNumberToObject(root, "archived_timestamp_unix", (double)archived_time);
    cJSON_AddStringToObject(root, "archived_timestamp", archived_timestamp);

    if (latest_valid)
    {
        cJSON_AddNumberToObject(root, "sequence", latest_record->sequence);
        cJSON_AddNumberToObject(root, "actual_reset_reason_code", latest_record->actual_reset_reason);
        cJSON_AddStringToObject(root,
                                "actual_reset_reason",
                                restart_tracker_reset_reason_to_str((esp_reset_reason_t)latest_record->actual_reset_reason));
        cJSON_AddBoolToObject(root, "was_planned", latest_record->was_planned != 0U);
    }

    cJSON_AddBoolToObject(root, "summary_available", summary_valid);
    if (summary_valid)
    {
        cJSON_AddNumberToObject(root, "exc_pc", summary->exc_pc);
        cJSON_AddNumberToObject(root, "exc_tcb", summary->exc_tcb);
        restart_tracker_coredump_add_optional_string(root, "crashed_task", summary->exc_task);
        restart_tracker_coredump_add_optional_string(root, "app_elf_sha256", (const char *)summary->app_elf_sha256);
    }

    if (panic_reason_valid)
    {
        cJSON_AddStringToObject(root, "panic_reason", panic_reason);
    }

    payload = cJSON_PrintUnformatted(root);
    cJSON_Delete(root);
    if (payload == NULL)
    {
        return ESP_ERR_NO_MEM;
    }

    file = fopen(path, "w");
    if (file == NULL)
    {
        free(payload);
        return ESP_FAIL;
    }

    if (fwrite(payload, 1, strlen(payload), file) != strlen(payload))
    {
        fclose(file);
        free(payload);
        unlink(path);
        return ESP_FAIL;
    }

    fclose(file);
    free(payload);
    return ESP_OK;
}

static esp_err_t restart_tracker_coredump_archive_if_present(void)
{
    restart_tracker_coredump_archive_ctx_t *ctx;
    const esp_partition_t *core_part;
    size_t image_addr = 0;
    size_t image_size = 0;
    esp_err_t err;
    bool image_valid = false;
    bool latest_valid = false;
    bool summary_valid = false;
    bool panic_reason_valid = false;
    time_t archived_time = time(NULL);
    uint64_t fallback_tag = (uint64_t)(esp_timer_get_time() / 1000ULL);

    ctx = calloc(1, sizeof(*ctx));
    if (ctx == NULL)
    {
        return ESP_ERR_NO_MEM;
    }

    err = esp_core_dump_image_get(&image_addr, &image_size);
    if (err == ESP_ERR_NOT_FOUND)
    {
        restart_tracker_coredump_prune_files(RESTART_TRACKER_COREDUMP_MAX_FILES);
        free(ctx);
        return ESP_OK;
    }
    if (err != ESP_OK)
    {
        ESP_LOGW(TAG, "Failed to inspect coredump partition (%s)", esp_err_to_name(err));
        restart_tracker_coredump_prune_files(RESTART_TRACKER_COREDUMP_MAX_FILES);
        free(ctx);
        return err;
    }

    core_part = esp_partition_find_first(ESP_PARTITION_TYPE_DATA, ESP_PARTITION_SUBTYPE_DATA_COREDUMP, NULL);
    if (core_part == NULL)
    {
        ESP_LOGE(TAG, "Coredump partition not found while archiving");
        free(ctx);
        return ESP_ERR_NOT_FOUND;
    }

    image_valid = (esp_core_dump_image_check() == ESP_OK);
    latest_valid = (restart_tracker_get_latest_record(&ctx->latest_record) == ESP_OK);
    summary_valid = image_valid && (esp_core_dump_get_summary(&ctx->summary) == ESP_OK);
    panic_reason_valid = image_valid &&
                         (esp_core_dump_get_panic_reason(ctx->panic_reason, sizeof(ctx->panic_reason)) == ESP_OK);

    restart_tracker_coredump_format_timestamp(archived_time,
                                              ctx->time_tag,
                                              sizeof(ctx->time_tag),
                                              "%Y%m%dT%H%M%SZ");
    restart_tracker_coredump_sanitize_filename_component(summary_valid ? ctx->summary.exc_task : "unknown",
                                                         ctx->task_tag,
                                                         sizeof(ctx->task_tag));

    if (strcmp(ctx->time_tag, "unsynced") == 0)
    {
        snprintf(ctx->base_name,
                 sizeof(ctx->base_name),
                 "coredump_%04lu_u%" PRIu64 "_%s",
                 (unsigned long)(latest_valid ? ctx->latest_record.sequence : 0UL),
                 fallback_tag,
                 ctx->task_tag);
    }
    else
    {
        snprintf(ctx->base_name,
                 sizeof(ctx->base_name),
                 "coredump_%04lu_%s_%s",
                 (unsigned long)(latest_valid ? ctx->latest_record.sequence : 0UL),
                 ctx->time_tag,
                 ctx->task_tag);
    }

    err = restart_tracker_coredump_get_directory(ctx->dir_path, sizeof(ctx->dir_path));
    if (err != ESP_OK)
    {
        free(ctx);
        return err;
    }

    err = restart_tracker_coredump_ensure_dir(ctx->dir_path);
    if (err != ESP_OK)
    {
        free(ctx);
        return err;
    }

    snprintf(ctx->data_path, sizeof(ctx->data_path), "%s/%s.elf", ctx->dir_path, ctx->base_name);
    snprintf(ctx->meta_path, sizeof(ctx->meta_path), "%s/%s.json", ctx->dir_path, ctx->base_name);

    ESP_LOGW(TAG,
             "Archiving coredump (%u bytes, valid=%d) from 0x%08x to %s",
             (unsigned)image_size,
             image_valid,
             (unsigned)image_addr,
             ctx->data_path);

    err = restart_tracker_coredump_write_file(core_part, image_size, ctx->data_path);
    if (err != ESP_OK)
    {
        free(ctx);
        return err;
    }

    err = restart_tracker_coredump_write_metadata(ctx->meta_path,
                                                  strrchr(ctx->data_path, '/') != NULL ? strrchr(ctx->data_path, '/') + 1 : ctx->data_path,
                                                  image_size,
                                                  image_valid,
                                                  &ctx->latest_record,
                                                  latest_valid,
                                                  &ctx->summary,
                                                  summary_valid,
                                                  ctx->panic_reason,
                                                  panic_reason_valid,
                                                  archived_time);
    if (err != ESP_OK)
    {
        ESP_LOGW(TAG, "Failed to write coredump metadata %s (%s)", ctx->meta_path, esp_err_to_name(err));
    }

    err = esp_core_dump_image_erase();
    if (err != ESP_OK)
    {
        ESP_LOGE(TAG, "Failed to erase raw coredump after archiving (%s)", esp_err_to_name(err));
        free(ctx);
        return err;
    }

    restart_tracker_coredump_prune_files(RESTART_TRACKER_COREDUMP_MAX_FILES);
    free(ctx);
    return ESP_OK;
}
#else
static esp_err_t restart_tracker_coredump_archive_if_present(void)
{
    restart_tracker_coredump_prune_files(RESTART_TRACKER_COREDUMP_MAX_FILES);
    return ESP_OK;
}
#endif

static cJSON *restart_tracker_coredump_read_metadata_file(const char *path)
{
    FILE *file;
    long size;
    char *buffer;
    size_t read_size;
    cJSON *json;

    file = fopen(path, "r");
    if (file == NULL)
    {
        return NULL;
    }

    fseek(file, 0, SEEK_END);
    size = ftell(file);
    fseek(file, 0, SEEK_SET);
    if (size <= 0)
    {
        fclose(file);
        return NULL;
    }

    buffer = malloc((size_t)size + 1U);
    if (buffer == NULL)
    {
        fclose(file);
        return NULL;
    }

    read_size = fread(buffer, 1, (size_t)size, file);
    fclose(file);
    if (read_size != (size_t)size)
    {
        free(buffer);
        return NULL;
    }

    buffer[size] = '\0';
    json = cJSON_Parse(buffer);
    free(buffer);
    return json;
}

static void restart_tracker_coredump_archive_task(void *arg)
{
    esp_err_t err = restart_tracker_coredump_archive_if_present();

    if (err != ESP_OK && err != ESP_ERR_NOT_FOUND)
    {
        ESP_LOGW(TAG, "Background coredump archive failed (%s)", esp_err_to_name(err));
    }

    s_coredump_archive_task = NULL;
    vTaskDelete(NULL);
}

esp_err_t restart_tracker_coredump_on_storage_ready(const char *mount_path)
{
    BaseType_t task_ok;

    if (mount_path == NULL || mount_path[0] == '\0')
    {
        return ESP_ERR_INVALID_ARG;
    }

    if (strlcpy(s_coredump_mount_path, mount_path, sizeof(s_coredump_mount_path)) >= sizeof(s_coredump_mount_path))
    {
        return ESP_ERR_INVALID_SIZE;
    }

    if (s_coredump_archive_task != NULL)
    {
        return ESP_OK;
    }

    task_ok = xTaskCreate(restart_tracker_coredump_archive_task,
                          "rt_cd_archive",
                          RESTART_TRACKER_COREDUMP_ARCHIVE_TASK_STACK,
                          NULL,
                          RESTART_TRACKER_COREDUMP_ARCHIVE_TASK_PRIORITY,
                          &s_coredump_archive_task);
    if (task_ok != pdPASS)
    {
        s_coredump_archive_task = NULL;
        ESP_LOGE(TAG, "Failed to start coredump archive task");
        return ESP_ERR_NO_MEM;
    }

    return ESP_OK;
}

cJSON *restart_tracker_coredump_list_to_json(const char *download_url_prefix)
{
    char dir_path[512];
    DIR *dir;
    struct dirent *entry;
    cJSON *response = cJSON_CreateObject();
    cJSON *files;
    size_t count = 0;
    esp_err_t path_err;

    if (response == NULL)
    {
        return NULL;
    }

    cJSON_AddBoolToObject(response, "available", s_coredump_mount_path[0] != '\0');
    cJSON_AddNumberToObject(response, "limit", RESTART_TRACKER_COREDUMP_MAX_FILES);
    files = cJSON_AddArrayToObject(response, "files");

    path_err = restart_tracker_coredump_get_directory(dir_path, sizeof(dir_path));
    if (path_err != ESP_OK)
    {
        cJSON_AddStringToObject(response, "error", esp_err_to_name(path_err));
        cJSON_AddNumberToObject(response, "count", 0);
        return response;
    }

    cJSON_AddStringToObject(response, "path", dir_path);

    dir = opendir(dir_path);
    if (dir == NULL)
    {
        cJSON_AddNumberToObject(response, "count", 0);
        return response;
    }

    while ((entry = readdir(dir)) != NULL)
    {
        char data_path[768];
        char meta_path[768];
        char download_url[512];
        struct stat st;
        cJSON *item;
        cJSON *metadata;

        if (!restart_tracker_coredump_filename_is_valid(entry->d_name))
        {
            continue;
        }

        snprintf(data_path, sizeof(data_path), "%s/%s", dir_path, entry->d_name);
        if (stat(data_path, &st) != 0)
        {
            continue;
        }

        item = cJSON_CreateObject();
        if (item == NULL)
        {
            continue;
        }

        snprintf(meta_path,
                 sizeof(meta_path),
                 "%s/%.*s.json",
                 dir_path,
                 (int)(strlen(entry->d_name) - 4U),
                 entry->d_name);
        snprintf(download_url,
                 sizeof(download_url),
                 "%s?name=%s",
                 download_url_prefix != NULL ? download_url_prefix : "/restart_tracker/coredumps/download",
                 entry->d_name);

        cJSON_AddStringToObject(item, "name", entry->d_name);
        cJSON_AddNumberToObject(item, "size_bytes", (double)st.st_size);
        cJSON_AddNumberToObject(item, "modified_timestamp_unix", (double)st.st_mtime);
        cJSON_AddStringToObject(item, "download_url", download_url);

        metadata = restart_tracker_coredump_read_metadata_file(meta_path);
        if (metadata != NULL)
        {
            cJSON_AddItemToObject(item, "metadata", metadata);
            cJSON_AddBoolToObject(item, "has_metadata", true);
        }
        else
        {
            cJSON_AddNullToObject(item, "metadata");
            cJSON_AddBoolToObject(item, "has_metadata", false);
        }

        cJSON_AddItemToArray(files, item);
        ++count;
    }

    closedir(dir);
    cJSON_AddNumberToObject(response, "count", count);
    return response;
}

esp_err_t restart_tracker_coredump_send_file(httpd_req_t *req, const char *name)
{
    char dir_path[512];
    char path[768];
    char disposition[180];
    struct stat st;
    FILE *file;
    char *buffer;
    size_t read_size;
    esp_err_t err;

    if (req == NULL || !restart_tracker_coredump_filename_is_valid(name))
    {
        return httpd_resp_send_err(req, HTTPD_400_BAD_REQUEST, "invalid name");
    }

    err = restart_tracker_coredump_get_directory(dir_path, sizeof(dir_path));
    if (err != ESP_OK)
    {
        httpd_resp_set_status(req, "503 Service Unavailable");
        return httpd_resp_send(req, "storage unavailable", HTTPD_RESP_USE_STRLEN);
    }

    snprintf(path, sizeof(path), "%s/%s", dir_path, name);
    if (stat(path, &st) != 0)
    {
        return httpd_resp_send_err(req, HTTPD_404_NOT_FOUND, "not found");
    }

    file = fopen(path, "rb");
    if (file == NULL)
    {
        return httpd_resp_send_err(req, HTTPD_500_INTERNAL_SERVER_ERROR, "open failed");
    }

    httpd_resp_set_type(req, "application/octet-stream");
    snprintf(disposition, sizeof(disposition), "attachment; filename=\"%s\"", name);
    httpd_resp_set_hdr(req, "Content-Disposition", disposition);

    buffer = malloc(RESTART_TRACKER_COREDUMP_CHUNK_SIZE);
    if (buffer == NULL)
    {
        fclose(file);
        return httpd_resp_send_err(req, HTTPD_500_INTERNAL_SERVER_ERROR, "alloc failed");
    }

    while ((read_size = fread(buffer, 1, RESTART_TRACKER_COREDUMP_CHUNK_SIZE, file)) > 0)
    {
        if (httpd_resp_send_chunk(req, buffer, read_size) != ESP_OK)
        {
            free(buffer);
            fclose(file);
            return ESP_FAIL;
        }
    }

    free(buffer);
    fclose(file);
    return httpd_resp_send_chunk(req, NULL, 0);
}