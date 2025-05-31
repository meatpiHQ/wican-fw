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
 */

#include <esp_wifi.h>
#include <esp_event.h>
#include <esp_log.h>
#include <esp_system.h>
#include <nvs_flash.h>
#include <sys/param.h>
#include "esp_netif.h"
#include "esp_http_server.h"
#include "obd_logger_iface.h"
#include "obd_logger.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/queue.h"
#include "freertos/semphr.h"
#include <string.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <stdbool.h>
#include <sys/unistd.h>
#include <sys/stat.h>
#include <sys/time.h>
#include <fcntl.h>
#include <string.h>
#include <assert.h>
#include "esp_err.h"
#include "esp_random.h"
#include "driver/sdmmc_types.h"
#include "sdmmc_cmd.h"
#include "wear_levelling.h"
#include "esp_log.h"
#include "esp_check.h"
#include "obd_logger_db_manager.h"

#define TAG "OBD_LOGGER_IFACE"
#define OBD_LOGS_URI "/obd_logs*"

static esp_err_t obd_logger_db_download_handler(httpd_req_t *req);
static esp_err_t obd_logger_handler(httpd_req_t *req);

// Configuration constants
typedef struct
{
    const size_t sql_task_stack_size;
    const size_t resp_task_stack_size;
    const size_t sql_queue_size;
    const size_t resp_queue_size;
    const size_t max_response_size;
    const size_t chunk_threshold_percent;
    const uint32_t db_lock_timeout_ms;
    const uint32_t queue_timeout_ms;
} OBDLoggerConfig;

static const OBDLoggerConfig CONFIG = {
    .sql_task_stack_size = 4096,
    .resp_task_stack_size = 4096,
    .sql_queue_size = 10,
    .resp_queue_size = 20,
    .max_response_size = 4096,
    .chunk_threshold_percent = 75,
    .db_lock_timeout_ms = 5000,
    .queue_timeout_ms = 1000
};

const httpd_uri_t db_download_uri = {
    .uri = "/download_db",
    .method = HTTP_GET,
    .handler = obd_logger_db_download_handler,
    .user_ctx = NULL
};

// WebSocket URI handler
const httpd_uri_t obd_logger_ws = {
    .uri = "/obd_logger_ws",
    .method = HTTP_GET,
    .handler = obd_logger_handler,
    .user_ctx = NULL,
    .is_websocket = true
};

// Task handles
static TaskHandle_t sql_task_handle = NULL;
static TaskHandle_t resp_task_handle = NULL;

// Queue handles
static QueueHandle_t sql_queue = NULL;
static QueueHandle_t resp_queue = NULL;

// Connection tracking
typedef struct
{
    httpd_handle_t hd;
    int fd;
    bool active;
} Connection;

#define MAX_CONNECTIONS 10
static Connection connections[MAX_CONNECTIONS];
static SemaphoreHandle_t conn_mutex = NULL;

// SQL request structure
typedef struct
{
    httpd_handle_t hd;
    int fd;
    char *query;
} SQLRequest;

// Response structure
typedef struct
{
    httpd_handle_t hd;
    int fd;
    char *data;
    size_t len;
    bool is_error;
} Response;

// Response builder structure
typedef struct
{
    char *buffer;
    size_t capacity;
    size_t len;
    bool headers_written;
} ResponseBuilder;

// Error codes
typedef enum
{
    OBD_LOG_OK = 0,
    OBD_LOG_ERR_MEMORY,
    OBD_LOG_ERR_QUEUE,
    OBD_LOG_ERR_DB_LOCK,
    OBD_LOG_ERR_SQL_EXEC,
    OBD_LOG_ERR_CONN_FULL
} OBDLogError;

// Error message formatting
static const char *error_to_string(OBDLogError error)
{
    switch (error)
    {
    case OBD_LOG_ERR_MEMORY:
        return "Memory allocation failed";
    case OBD_LOG_ERR_QUEUE:
        return "Queue operation failed";
    case OBD_LOG_ERR_DB_LOCK:
        return "Failed to acquire database lock";
    case OBD_LOG_ERR_SQL_EXEC:
        return "SQL execution failed";
    case OBD_LOG_ERR_CONN_FULL:
        return "Connection limit reached";
    default:
        return "Unknown error";
    }
}

// Connection management functions
static int add_connection(httpd_handle_t hd, int fd)
{
    xSemaphoreTake(conn_mutex, portMAX_DELAY);
    for (int i = 0; i < MAX_CONNECTIONS; i++)
    {
        if (!connections[i].active)
        {
            connections[i].hd = hd;
            connections[i].fd = fd;
            connections[i].active = true;
            xSemaphoreGive(conn_mutex);
            return i;
        }
    }
    xSemaphoreGive(conn_mutex);
    return -1;
}

static void remove_connection(int fd)
{
    xSemaphoreTake(conn_mutex, portMAX_DELAY);
    for (int i = 0; i < MAX_CONNECTIONS; i++)
    {
        if (connections[i].fd == fd && connections[i].active)
        {
            connections[i].active = false;
            break;
        }
    }
    xSemaphoreGive(conn_mutex);
}

// Response builder functions
static ResponseBuilder *response_builder_create(size_t capacity)
{
    ResponseBuilder *builder = malloc(sizeof(ResponseBuilder));
    if (!builder)
        return NULL;

    builder->buffer = malloc(capacity);
    if (!builder->buffer)
    {
        free(builder);
        return NULL;
    }

    builder->capacity = capacity;
    builder->len = 0;
    builder->headers_written = false;
    memset(builder->buffer, 0, capacity);

    return builder;
}

static void response_builder_destroy(ResponseBuilder *builder)
{
    if (builder)
    {
        free(builder->buffer);
        free(builder);
    }
}

static bool response_builder_append(ResponseBuilder *builder, const char *data, size_t len)
{
    if (builder->len + len >= builder->capacity)
    {
        return false;
    }

    memcpy(builder->buffer + builder->len, data, len);
    builder->len += len;
    return true;
}

static bool response_builder_append_string(ResponseBuilder *builder, const char *str)
{
    return response_builder_append(builder, str, strlen(str));
}

static void response_builder_add_headers(ResponseBuilder *builder, char **col_names, int col_count)
{
    if (builder->headers_written)
        return;

    // Add column headers
    for (int i = 0; i < col_count; i++)
    {
        if (i > 0)
            response_builder_append_string(builder, "|");
        response_builder_append_string(builder, col_names[i]);
    }
    response_builder_append_string(builder, "\n");

    // Add separator line
    for (int i = 0; i < col_count; i++)
    {
        if (i > 0)
            response_builder_append_string(builder, "+");
        for (size_t j = 0; j < strlen(col_names[i]); j++)
        {
            response_builder_append_string(builder, "-");
        }
    }
    response_builder_append_string(builder, "\n");

    builder->headers_written = true;
}

static bool response_builder_needs_flush(ResponseBuilder *builder)
{
    return builder->len > (builder->capacity * CONFIG.chunk_threshold_percent / 100);
}

// SQL callback context
typedef struct
{
    httpd_handle_t hd;
    int fd;
    ResponseBuilder *builder;
} SQLCallbackContext;

// Improved SQL callback
static int sql_callback(void *data, int argc, char **argv, char **col_names)
{
    SQLCallbackContext *ctx = (SQLCallbackContext *)data;

    // Add headers if not already added
    response_builder_add_headers(ctx->builder, col_names, argc);

    // Add data row
    for (int i = 0; i < argc; i++)
    {
        if (i > 0)
            response_builder_append_string(ctx->builder, "|");
        const char *value = argv[i] ? argv[i] : "NULL";
        response_builder_append_string(ctx->builder, value);
    }
    response_builder_append_string(ctx->builder, "\n");

    // Check if we need to flush
    if (response_builder_needs_flush(ctx->builder))
    {
        Response resp = {
            .hd = ctx->hd,
            .fd = ctx->fd,
            .data = strdup(ctx->builder->buffer),
            .len = ctx->builder->len,
            .is_error = false};

        if (resp.data && xQueueSend(resp_queue, &resp, pdMS_TO_TICKS(CONFIG.queue_timeout_ms)) == pdTRUE)
        {
            // Reset builder for next batch
            ctx->builder->len = 0;
            ctx->builder->headers_written = true; // Keep headers flag
        }
        else
        {
            free(resp.data);
            return 1; // Stop processing
        }
    }

    return 0;
}

// Send error response
static void send_error_response(httpd_handle_t hd, int fd, OBDLogError error)
{
    const char DEL = 0x04;
    char error_msg[256];
    snprintf(error_msg, sizeof(error_msg), "ERROR%c%s", DEL, error_to_string(error));

    Response resp = {
        .hd = hd,
        .fd = fd,
        .data = strdup(error_msg),
        .len = strlen(error_msg),
        .is_error = true};

    if (resp.data)
    {
        if (xQueueSend(resp_queue, &resp, pdMS_TO_TICKS(CONFIG.queue_timeout_ms)) != pdTRUE)
        {
            free(resp.data);
        }
    }
}

// SQL execution task
static void sql_execution_task(void *pvParameters)
{
    SQLRequest request;

    while (1)
    {
        if (xQueueReceive(sql_queue, &request, portMAX_DELAY) == pdTRUE)
        {
            ESP_LOGI(TAG, "Executing SQL: %s", request.query);

            // Create context
            SQLCallbackContext ctx = {
                .hd = request.hd,
                .fd = request.fd,
                .builder = response_builder_create(CONFIG.max_response_size)};

            if (!ctx.builder)
            {
                send_error_response(request.hd, request.fd, OBD_LOG_ERR_MEMORY);
                goto cleanup;
            }

            // Execute query
            if (obd_logger_lock(CONFIG.db_lock_timeout_ms) != ESP_OK)
            {
                send_error_response(request.hd, request.fd, OBD_LOG_ERR_DB_LOCK);
                response_builder_destroy(ctx.builder);
                goto cleanup;
            }

            int64_t start = esp_timer_get_time();
            int rc = obd_logger_db_execute(request.query, sql_callback, &ctx);
            int64_t duration = esp_timer_get_time() - start;

            obd_logger_unlock();

            ESP_LOGI(TAG, "SQL completed in %lld µs, rc=%d", duration, rc);

            // Send any remaining data
            if (ctx.builder->len > 0)
            {
                Response resp = {
                    .hd = request.hd,
                    .fd = request.fd,
                    .data = strdup(ctx.builder->buffer),
                    .len = ctx.builder->len,
                    .is_error = false};

                if (resp.data)
                {
                    if (xQueueSend(resp_queue, &resp, pdMS_TO_TICKS(CONFIG.queue_timeout_ms)) != pdTRUE)
                    {
                        free(resp.data);
                    }
                }
            }

            if (rc != 0)
            {
                send_error_response(request.hd, request.fd, OBD_LOG_ERR_SQL_EXEC);
            }

            response_builder_destroy(ctx.builder);

        cleanup:
            free(request.query);
        }
    }
}

// Response sending task
static void response_task(void *pvParameters)
{
    Response resp;

    while (1)
    {
        if (xQueueReceive(resp_queue, &resp, portMAX_DELAY) == pdTRUE)
        {
            ESP_LOGI(TAG, "Sending response: %zu bytes, error=%d", resp.len, resp.is_error);

            httpd_ws_frame_t ws_pkt = {
                .payload = (uint8_t *)resp.data,
                .len = resp.len,
                .type = HTTPD_WS_TYPE_TEXT};

            esp_err_t ret = httpd_ws_send_frame_async(resp.hd, resp.fd, &ws_pkt);
            if (ret != ESP_OK)
            {
                ESP_LOGE(TAG, "Failed to send WebSocket frame: %d", ret);
            }

            free(resp.data);
        }
    }
}

// WebSocket handler
static esp_err_t obd_logger_handler(httpd_req_t *req)
{
    if (!obd_logger_is_initialized() || !obd_logger_is_enabled())
    {
        ESP_LOGE(TAG, "OBD Logger not initialized or enabled");
        httpd_resp_send_500(req);
        return ESP_FAIL;
    }

    if (req->method == HTTP_GET)
    {
        int fd = httpd_req_to_sockfd(req);
        if (add_connection(req->handle, fd) < 0)
        {
            ESP_LOGE(TAG, "Connection limit reached");
            return ESP_ERR_NO_MEM;
        }
        ESP_LOGI(TAG, "New WebSocket connection opened: fd=%d", fd);
        return ESP_OK;
    }
    httpd_ws_frame_t ws_pkt;
    memset(&ws_pkt, 0, sizeof(httpd_ws_frame_t));
    // Get frame length
    esp_err_t ret = httpd_ws_recv_frame(req, &ws_pkt, 0);
    if (ret != ESP_OK)
    {
        ESP_LOGE(TAG, "Failed to get frame length: %d", ret);
        return ret;
    }

    if (ws_pkt.len == 0)
    {
        return ESP_OK;
    }

    // Allocate buffer for frame data
    uint8_t *buf = malloc(ws_pkt.len + 1);
    if (!buf)
    {
        ESP_LOGE(TAG, "Failed to allocate frame buffer");
        return ESP_ERR_NO_MEM;
    }

    ws_pkt.payload = buf;

    // Receive frame data
    ret = httpd_ws_recv_frame(req, &ws_pkt, ws_pkt.len);
    if (ret != ESP_OK)
    {
        ESP_LOGE(TAG, "Failed to receive frame: %d", ret);
        free(buf);
        return ret;
    }

    // Null terminate the string
    buf[ws_pkt.len] = '\0';

    ESP_LOGI(TAG, "Received: %s", buf);

    if (ws_pkt.type == HTTPD_WS_TYPE_TEXT)
    {
        SQLRequest request = {
            .hd = req->handle,
            .fd = httpd_req_to_sockfd(req),
            .query = strdup((char *)buf)};

        if (!request.query)
        {
            ESP_LOGE(TAG, "Failed to allocate query memory");
            free(buf);
            return ESP_ERR_NO_MEM;
        }

        if (xQueueSend(sql_queue, &request, pdMS_TO_TICKS(CONFIG.queue_timeout_ms)) != pdTRUE)
        {
            ESP_LOGE(TAG, "Failed to queue SQL request");
            free(request.query);
            free(buf);
            return ESP_ERR_TIMEOUT;
        }

        ESP_LOGI(TAG, "SQL query queued");
    }
    else if (ws_pkt.type == HTTPD_WS_TYPE_CLOSE)
    {
        int fd = httpd_req_to_sockfd(req);
        remove_connection(fd);
        ESP_LOGI(TAG, "WebSocket connection closed: fd=%d", fd);
    }

    free(buf);
    return ESP_OK;
}

#define CHUNK_SIZE 1024 * 64
// Database file download handler
static esp_err_t obd_logger_db_download_handler(httpd_req_t *req)
{
    char db_path[128];
    esp_err_t ret;

    // Get the current database path from the DB manager
    ret = obd_db_manager_get_current_path(db_path, sizeof(db_path));
    if (ret != ESP_OK)
    {
        ESP_LOGE(TAG, "Failed to get current database path");
        httpd_resp_send_500(req);
        return ESP_FAIL;
    }

    ESP_LOGI(TAG, "DB download request received for file: %s", db_path);

    // Get just the filename part for the Content-Disposition header
    char *filename = strrchr(db_path, '/');
    if (filename == NULL)
    {
        filename = db_path; // Use full path if no slash found
    }
    else
    {
        filename++; // Skip the slash
    }

    obd_logger_lock_close(); // Lock the database to prevent concurrent access

    // Set response headers
    httpd_resp_set_type(req, "application/octet-stream");

    // Create proper content disposition with the actual filename
    char content_disposition[256];
    snprintf(content_disposition, sizeof(content_disposition),
             "attachment; filename=%s", filename);
    httpd_resp_set_hdr(req, "Content-Disposition", content_disposition);

    // Open the file
    int file = open(db_path, O_RDONLY);
    if (file < 0)
    {
        ESP_LOGE(TAG, "Failed to open database file: %s", db_path);
        obd_logger_unlock_open();
        httpd_resp_send_500(req);
        return ESP_FAIL;
    }

    // Get file size for logging
    struct stat file_stat;
    if (fstat(file, &file_stat) == 0)
    {
        ESP_LOGI(TAG, "Starting to send database file, size: %ld bytes", file_stat.st_size);
    }

    // First try internal memory for better performance
    void *chunk = heap_caps_malloc(CHUNK_SIZE, MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT);

    // If internal memory allocation fails, try PSRAM as fallback
    if (chunk == NULL)
    {
        ESP_LOGW(TAG, "Internal memory allocation failed for file chunk, trying PSRAM");
        chunk = heap_caps_malloc(CHUNK_SIZE, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    }

    // If both internal and PSRAM allocation fail, handle the error
    if (chunk == NULL)
    {
        ESP_LOGE(TAG, "Failed to allocate memory for file chunk (both internal and PSRAM)");
        close(file);
        obd_logger_unlock_open();
        httpd_resp_send_500(req);
        return ESP_FAIL;
    }

    size_t bytes_read;
    ret = ESP_OK;

    while ((bytes_read = read(file, chunk, CHUNK_SIZE)) > 0)
    {
        if (bytes_read < CHUNK_SIZE)
        {
            ESP_LOGI(TAG, "End of file reached, bytes read: %zu", bytes_read);
        }
        if (httpd_resp_send_chunk(req, chunk, bytes_read) != ESP_OK)
        {
            ESP_LOGE(TAG, "File sending failed");
            ret = ESP_FAIL;
            break;
        }
    }

    // Finalize the HTTP response
    httpd_resp_send_chunk(req, NULL, 0);

    // Clean up
    free(chunk);
    close(file);
    obd_logger_unlock_open(); // Unlock the database after sending the file

    ESP_LOGI(TAG, "Database file download %s", (ret == ESP_OK) ? "completed successfully" : "failed");
    return ret;
}

esp_err_t obd_logger_db_file_handler(httpd_req_t *req)
{
    char filepath[256] = {0};
    char base_path[128] = {0};
    esp_err_t ret = ESP_OK;

    // Get the request URI
    const char *uri = req->uri;
    ESP_LOGI(TAG, "DB file request received: %s", uri);

    // Skip OBD_LOGS_URI prefix
    const char *filename = uri + strlen(OBD_LOGS_URI);

    // If the path is empty or just "/" - serve the index file
    if (filename[0] == '\0' || (filename[0] == '/' && filename[1] == '\0'))
    {
        ESP_LOGI(TAG, "Serving DB index file");

        // Use the DB manager path to get the index file
        strncpy(base_path, DB_ROOT_PATH"/"DB_DIR_NAME, sizeof(base_path) - 1); // Default path

        // Get a database file to determine the base path
        obd_db_file_info_t db_files[1];
        size_t num_files = 0;
        if (obd_db_manager_get_file_list(db_files, 1, &num_files) == ESP_OK && num_files > 0)
        {
            // Extract the base path from the filename
            char *last_slash = strrchr(db_files[0].filename, '/');
            if (last_slash)
            {
                size_t base_len = last_slash - db_files[0].filename;
                strncpy(base_path, db_files[0].filename, base_len);
                base_path[base_len] = '\0';
            }
        }

        // Construct the index file path
        snprintf(filepath, sizeof(filepath), "%s/" DB_INDEX_FILENAME, base_path);
    }
    else
    {
        // Handle DB file request - the filename is the rest of the URI
        if (filename[0] == '/')
        {
            filename++; // Skip leading slash
        }

        // Check if this is a valid DB file request
        if (strstr(filename, DB_FILENAME_PREFIX) == filename &&
            strstr(filename, DB_FILENAME_EXTENSION) != NULL)
        {

            // This is a request for a specific DB file
            obd_db_file_info_t db_files[50];
            size_t num_files = 0;

            ret = obd_db_manager_get_file_list(db_files, 50, &num_files);
            if (ret != ESP_OK)
            {
                ESP_LOGE(TAG, "Failed to get DB file list");
                httpd_resp_send_err(req, HTTPD_500_INTERNAL_SERVER_ERROR, "Failed to get DB file list");
                return ESP_FAIL;
            }

            // Look for the requested file in the list
            bool file_found = false;
            for (size_t i = 0; i < num_files; i++)
            {
                if (strcmp(db_files[i].filename, filename) == 0)
                {
                    // Found the requested file
                    file_found = true;

                    // Construct the full path
                    char db_path[128];
                    ret = obd_db_manager_get_current_path(db_path, sizeof(db_path));
                    if (ret != ESP_OK)
                    {
                        ESP_LOGE(TAG, "Failed to get current DB path");
                        httpd_resp_send_err(req, HTTPD_500_INTERNAL_SERVER_ERROR, "Failed to get DB path");
                        return ESP_FAIL;
                    }

                    // Extract the base path (remove the filename)
                    char *last_slash = strrchr(db_path, '/');
                    if (last_slash)
                    {
                        size_t base_len = last_slash - db_path;
                        strncpy(base_path, db_path, base_len);
                        base_path[base_len] = '\0';

                        // Construct the full path to the requested file
                        snprintf(filepath, sizeof(filepath), "%s/%s", base_path, filename);
                    }
                    else
                    {
                        // Fallback if path doesn't have a slash, use DB_ROOT_PATH"/"DB_DIR_NAME
                        snprintf(filepath, sizeof(filepath), "%s/%s", DB_ROOT_PATH"/"DB_DIR_NAME, filename);
                    }
                    break;
                }
            }

            if (!file_found)
            {
                ESP_LOGE(TAG, "Requested DB file not found: %s", filename);
                httpd_resp_send_err(req, HTTPD_404_NOT_FOUND, "DB file not found");
                return ESP_FAIL;
            }
        }
        else
        {
            // Invalid filename pattern
            ESP_LOGE(TAG, "Invalid DB file request: %s", filename);
            httpd_resp_send_err(req, HTTPD_400_BAD_REQUEST, "Invalid DB file request");
            return ESP_FAIL;
        }
    }

    ESP_LOGI(TAG, "Serving file: %s", filepath);

    // Open the file for reading
    FILE *file = fopen(filepath, "r");
    if (!file)
    {
        ESP_LOGE(TAG, "Failed to open file: %s", filepath);
        httpd_resp_send_err(req, HTTPD_404_NOT_FOUND, "File not found");
        return ESP_FAIL;
    }

    // Get file size
    fseek(file, 0, SEEK_END);
    long file_size = ftell(file);
    fseek(file, 0, SEEK_SET);

    char size_str[16];
    snprintf(size_str, sizeof(size_str), "%ld", file_size);
    httpd_resp_set_hdr(req, "Content-Length", size_str);

    // Set appropriate content type based on file extension
    if (strstr(filepath, ".json") != NULL)
    {
        httpd_resp_set_type(req, "application/json");
    }
    else if (strstr(filepath, ".db") != NULL)
    {
        httpd_resp_set_type(req, "application/octet-stream");

        // For database files, set appropriate download headers
        char content_disposition[280] = {0};
        const char *filename_only = strrchr(filepath, '/');
        if (filename_only)
        {
            filename_only++; // Skip the slash
        }
        else
        {
            filename_only = filepath;
        }

        snprintf(content_disposition, sizeof(content_disposition),
                 "attachment; filename=%s", filename_only);
        httpd_resp_set_hdr(req, "Content-Disposition", content_disposition);
    }

    // If it's a DB file and we have an active database, lock it
    bool is_current_db = strstr(filepath, ".db") != NULL;
    if (is_current_db)
    {
        obd_logger_lock_close(); // Lock the database to prevent concurrent access
    }

    // Read and send file in chunks
    // First try internal memory for better performance
    char *buffer = heap_caps_malloc(CHUNK_SIZE, MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT);

    // If internal memory allocation fails, try PSRAM as fallback
    if (!buffer)
    {
        ESP_LOGW(TAG, "Internal memory allocation failed, trying PSRAM");
        buffer = heap_caps_malloc(CHUNK_SIZE, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    }

    // If both internal and PSRAM allocation fail, handle the error
    if (!buffer)
    {
        ESP_LOGE(TAG, "Failed to allocate memory for file buffer (both internal and PSRAM)");
        fclose(file);
        if (is_current_db)
            obd_logger_unlock_open();
        httpd_resp_send_err(req, HTTPD_500_INTERNAL_SERVER_ERROR, "Out of memory");
        return ESP_FAIL;
    }

    size_t bytes_read;
    while ((bytes_read = fread(buffer, 1, CHUNK_SIZE, file)) > 0)
    {
        if (httpd_resp_send_chunk(req, buffer, bytes_read) != ESP_OK)
        {
            ESP_LOGE(TAG, "File sending failed");
            ret = ESP_FAIL;
            break;
        }
    }

    // Send empty chunk to signal end of response
    httpd_resp_send_chunk(req, NULL, 0);

    // Clean up
    free(buffer);
    fclose(file);

    // If it was a DB file, unlock it
    if (is_current_db)
    {
        obd_logger_unlock_open();
    }

    ESP_LOGI(TAG, "File send complete: %s", filepath);
    return ret;
}

// URI handler configuration
const httpd_uri_t db_files_uri = {
    .uri = OBD_LOGS_URI,
    .method = HTTP_GET,
    .handler = obd_logger_db_file_handler,
    .user_ctx = NULL};

// Initialization function
esp_err_t obd_logger_iface_init(void)
{
    // Create mutex for connection management
    conn_mutex = xSemaphoreCreateMutex();
    if (!conn_mutex)
    {
        ESP_LOGE(TAG, "Failed to create connection mutex");
        return ESP_ERR_NO_MEM;
    }

    // Initialize connection array
    memset(connections, 0, sizeof(connections));

    // Create queues
    sql_queue = xQueueCreate(CONFIG.sql_queue_size, sizeof(SQLRequest));
    if (!sql_queue)
    {
        ESP_LOGE(TAG, "Failed to create SQL queue");
        vSemaphoreDelete(conn_mutex);
        return ESP_ERR_NO_MEM;
    }

    resp_queue = xQueueCreate(CONFIG.resp_queue_size, sizeof(Response));
    if (!resp_queue)
    {
        ESP_LOGE(TAG, "Failed to create response queue");
        vQueueDelete(sql_queue);
        vSemaphoreDelete(conn_mutex);
        return ESP_ERR_NO_MEM;
    }

    // Allocate stack memory in PSRAM for tasks
    static StackType_t *sql_task_stack, *resp_task_stack;
    static StaticTask_t sql_task_buffer, resp_task_buffer;

    sql_task_stack = heap_caps_malloc(CONFIG.sql_task_stack_size, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    resp_task_stack = heap_caps_malloc(CONFIG.resp_task_stack_size, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);

    if (sql_task_stack == NULL || resp_task_stack == NULL)
    {
        ESP_LOGE(TAG, "Failed to allocate task stack memory");
        if (sql_task_stack)
            heap_caps_free(sql_task_stack);
        if (resp_task_stack)
            heap_caps_free(resp_task_stack);
        goto error_cleanup;
    }

    // Create static tasks
    sql_task_handle = xTaskCreateStatic(
        sql_execution_task,
        "sql_task",
        CONFIG.sql_task_stack_size,
        NULL,
        5,
        sql_task_stack,
        &sql_task_buffer);

    resp_task_handle = xTaskCreateStatic(
        response_task,
        "resp_task",
        CONFIG.resp_task_stack_size,
        NULL,
        5,
        resp_task_stack,
        &resp_task_buffer);

    if (sql_task_handle == NULL || resp_task_handle == NULL)
    {
        ESP_LOGE(TAG, "Failed to create tasks");
        heap_caps_free(sql_task_stack);
        heap_caps_free(resp_task_stack);
        goto error_cleanup;
    }

    ESP_LOGI(TAG, "OBD logger WebSocket interface initialized");
    return ESP_OK;

error_cleanup:
    vQueueDelete(resp_queue);
    vQueueDelete(sql_queue);
    vSemaphoreDelete(conn_mutex);
    return ESP_ERR_NO_MEM;
}

// Cleanup function
void obd_logger_iface_deinit(void)
{
    // Delete tasks
    if (sql_task_handle)
        vTaskDelete(sql_task_handle);
    if (resp_task_handle)
        vTaskDelete(resp_task_handle);

    // Delete queues
    if (sql_queue)
        vQueueDelete(sql_queue);
    if (resp_queue)
        vQueueDelete(resp_queue);

    // Delete mutex
    if (conn_mutex)
        vSemaphoreDelete(conn_mutex);

    ESP_LOGI(TAG, "OBD logger WebSocket interface deinitialized");
}