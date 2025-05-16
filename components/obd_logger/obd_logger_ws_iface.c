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
 #include "obd_logger_ws_iface.h"
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

 #define TAG "OBD_LOGGER_WS_IFACE"
 
 // Configuration constants
 typedef struct {
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
 
 // Task handles
 static TaskHandle_t sql_task_handle = NULL;
 static TaskHandle_t resp_task_handle = NULL;
 
 // Queue handles
 static QueueHandle_t sql_queue = NULL;
 static QueueHandle_t resp_queue = NULL;
 
 // Connection tracking
 typedef struct {
     httpd_handle_t hd;
     int fd;
     bool active;
 } Connection;
 
 #define MAX_CONNECTIONS 10
 static Connection connections[MAX_CONNECTIONS];
 static SemaphoreHandle_t conn_mutex = NULL;
 
 // SQL request structure
 typedef struct {
     httpd_handle_t hd;
     int fd;
     char *query;
 } SQLRequest;
 
 // Response structure
 typedef struct {
     httpd_handle_t hd;
     int fd;
     char *data;
     size_t len;
     bool is_error;
 } Response;
 
 // Response builder structure
 typedef struct {
     char *buffer;
     size_t capacity;
     size_t len;
     bool headers_written;
 } ResponseBuilder;
 
 // Error codes
 typedef enum {
     OBD_LOG_OK = 0,
     OBD_LOG_ERR_MEMORY,
     OBD_LOG_ERR_QUEUE,
     OBD_LOG_ERR_DB_LOCK,
     OBD_LOG_ERR_SQL_EXEC,
     OBD_LOG_ERR_CONN_FULL
 } OBDLogError;
 
 // Error message formatting
 static const char* error_to_string(OBDLogError error) {
     switch (error) {
         case OBD_LOG_ERR_MEMORY: return "Memory allocation failed";
         case OBD_LOG_ERR_QUEUE: return "Queue operation failed";
         case OBD_LOG_ERR_DB_LOCK: return "Failed to acquire database lock";
         case OBD_LOG_ERR_SQL_EXEC: return "SQL execution failed";
         case OBD_LOG_ERR_CONN_FULL: return "Connection limit reached";
         default: return "Unknown error";
     }
 }
 
 // Connection management functions
 static int add_connection(httpd_handle_t hd, int fd) {
     xSemaphoreTake(conn_mutex, portMAX_DELAY);
     for (int i = 0; i < MAX_CONNECTIONS; i++) {
         if (!connections[i].active) {
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
 
 static void remove_connection(int fd) {
     xSemaphoreTake(conn_mutex, portMAX_DELAY);
     for (int i = 0; i < MAX_CONNECTIONS; i++) {
         if (connections[i].fd == fd && connections[i].active) {
             connections[i].active = false;
             break;
         }
     }
     xSemaphoreGive(conn_mutex);
 }
 
 // Response builder functions
 static ResponseBuilder* response_builder_create(size_t capacity) {
     ResponseBuilder *builder = malloc(sizeof(ResponseBuilder));
     if (!builder) return NULL;
     
     builder->buffer = malloc(capacity);
     if (!builder->buffer) {
         free(builder);
         return NULL;
     }
     
     builder->capacity = capacity;
     builder->len = 0;
     builder->headers_written = false;
     memset(builder->buffer, 0, capacity);
     
     return builder;
 }
 
 static void response_builder_destroy(ResponseBuilder *builder) {
     if (builder) {
         free(builder->buffer);
         free(builder);
     }
 }
 
 static bool response_builder_append(ResponseBuilder *builder, const char *data, size_t len) {
     if (builder->len + len >= builder->capacity) {
         return false;
     }
     
     memcpy(builder->buffer + builder->len, data, len);
     builder->len += len;
     return true;
 }
 
 static bool response_builder_append_string(ResponseBuilder *builder, const char *str) {
     return response_builder_append(builder, str, strlen(str));
 }
 
 static void response_builder_add_headers(ResponseBuilder *builder, char **col_names, int col_count) {
     if (builder->headers_written) return;
     
     // Add column headers
     for (int i = 0; i < col_count; i++) {
         if (i > 0) response_builder_append_string(builder, "|");
         response_builder_append_string(builder, col_names[i]);
     }
     response_builder_append_string(builder, "\n");
     
     // Add separator line
     for (int i = 0; i < col_count; i++) {
         if (i > 0) response_builder_append_string(builder, "+");
         for (size_t j = 0; j < strlen(col_names[i]); j++) {
             response_builder_append_string(builder, "-");
         }
     }
     response_builder_append_string(builder, "\n");
     
     builder->headers_written = true;
 }
 
 static bool response_builder_needs_flush(ResponseBuilder *builder) {
     return builder->len > (builder->capacity * CONFIG.chunk_threshold_percent / 100);
 }
 
 // SQL callback context
 typedef struct {
     httpd_handle_t hd;
     int fd;
     ResponseBuilder *builder;
 } SQLCallbackContext;
 
 // Improved SQL callback
 static int sql_callback(void *data, int argc, char **argv, char **col_names) {
     SQLCallbackContext *ctx = (SQLCallbackContext *)data;
     
     // Add headers if not already added
     response_builder_add_headers(ctx->builder, col_names, argc);
     
     // Add data row
     for (int i = 0; i < argc; i++) {
         if (i > 0) response_builder_append_string(ctx->builder, "|");
         const char *value = argv[i] ? argv[i] : "NULL";
         response_builder_append_string(ctx->builder, value);
     }
     response_builder_append_string(ctx->builder, "\n");
     
     // Check if we need to flush
     if (response_builder_needs_flush(ctx->builder)) {
         Response resp = {
             .hd = ctx->hd,
             .fd = ctx->fd,
             .data = strdup(ctx->builder->buffer),
             .len = ctx->builder->len,
             .is_error = false
         };
         
         if (resp.data && xQueueSend(resp_queue, &resp, pdMS_TO_TICKS(CONFIG.queue_timeout_ms)) == pdTRUE) {
             // Reset builder for next batch
             ctx->builder->len = 0;
             ctx->builder->headers_written = true;  // Keep headers flag
         } else {
             free(resp.data);
             return 1;  // Stop processing
         }
     }
     
     return 0;
 }
 
 // Send error response
 static void send_error_response(httpd_handle_t hd, int fd, OBDLogError error) {
     const char DEL = 0x04;
     char error_msg[256];
     snprintf(error_msg, sizeof(error_msg), "ERROR%c%s", DEL, error_to_string(error));
     
     Response resp = {
         .hd = hd,
         .fd = fd,
         .data = strdup(error_msg),
         .len = strlen(error_msg),
         .is_error = true
     };
     
     if (resp.data) {
         if (xQueueSend(resp_queue, &resp, pdMS_TO_TICKS(CONFIG.queue_timeout_ms)) != pdTRUE) {
             free(resp.data);
         }
     }
 }
 
 // SQL execution task
 static void sql_execution_task(void *pvParameters) {
     SQLRequest request;
     
     while (1) {
         if (xQueueReceive(sql_queue, &request, portMAX_DELAY) == pdTRUE) {
             ESP_LOGI(TAG, "Executing SQL: %s", request.query);
             
             // Create context
             SQLCallbackContext ctx = {
                 .hd = request.hd,
                 .fd = request.fd,
                 .builder = response_builder_create(CONFIG.max_response_size)
             };
             
             if (!ctx.builder) {
                 send_error_response(request.hd, request.fd, OBD_LOG_ERR_MEMORY);
                 goto cleanup;
             }
             
             // Execute query
             if (obd_logger_lock(CONFIG.db_lock_timeout_ms) != ESP_OK) {
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
             if (ctx.builder->len > 0) {
                 Response resp = {
                     .hd = request.hd,
                     .fd = request.fd,
                     .data = strdup(ctx.builder->buffer),
                     .len = ctx.builder->len,
                     .is_error = false
                 };
                 
                 if (resp.data) {
                     if (xQueueSend(resp_queue, &resp, pdMS_TO_TICKS(CONFIG.queue_timeout_ms)) != pdTRUE) {
                         free(resp.data);
                     }
                 }
             }
             
             if (rc != 0) {
                 send_error_response(request.hd, request.fd, OBD_LOG_ERR_SQL_EXEC);
             }
             
             response_builder_destroy(ctx.builder);
             
         cleanup:
             free(request.query);
         }
     }
 }
 
 // Response sending task
 static void response_task(void *pvParameters) {
     Response resp;
     
     while (1) {
         if (xQueueReceive(resp_queue, &resp, portMAX_DELAY) == pdTRUE) {
             ESP_LOGI(TAG, "Sending response: %zu bytes, error=%d", resp.len, resp.is_error);
             
             httpd_ws_frame_t ws_pkt = {
                 .payload = (uint8_t*)resp.data,
                 .len = resp.len,
                 .type = HTTPD_WS_TYPE_TEXT
             };
             
             esp_err_t ret = httpd_ws_send_frame_async(resp.hd, resp.fd, &ws_pkt);
             if (ret != ESP_OK) {
                 ESP_LOGE(TAG, "Failed to send WebSocket frame: %d", ret);
             }
             
             free(resp.data);
         }
     }
 }
 
 // WebSocket URI handler
 const httpd_uri_t obd_logger_ws = {
     .uri = "/obd_logger_ws",
     .method = HTTP_GET,
     .handler = obd_logger_ws_handler,
     .user_ctx = NULL,
     .is_websocket = true
 };
 
 // WebSocket handler
 esp_err_t obd_logger_ws_handler(httpd_req_t *req) {
     if (req->method == HTTP_GET) {
         int fd = httpd_req_to_sockfd(req);
         if (add_connection(req->handle, fd) < 0) {
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
     if (ret != ESP_OK) {
         ESP_LOGE(TAG, "Failed to get frame length: %d", ret);
         return ret;
     }
     
     if (ws_pkt.len == 0) {
         return ESP_OK;
     }
     
     // Allocate buffer for frame data
     uint8_t *buf = malloc(ws_pkt.len + 1);
     if (!buf) {
         ESP_LOGE(TAG, "Failed to allocate frame buffer");
         return ESP_ERR_NO_MEM;
     }
     
     ws_pkt.payload = buf;
     
     // Receive frame data
     ret = httpd_ws_recv_frame(req, &ws_pkt, ws_pkt.len);
     if (ret != ESP_OK) {
         ESP_LOGE(TAG, "Failed to receive frame: %d", ret);
         free(buf);
         return ret;
     }
     
     // Null terminate the string
     buf[ws_pkt.len] = '\0';
     
     ESP_LOGI(TAG, "Received: %s", buf);
     
     if (ws_pkt.type == HTTPD_WS_TYPE_TEXT) {
         SQLRequest request = {
             .hd = req->handle,
             .fd = httpd_req_to_sockfd(req),
             .query = strdup((char *)buf)
         };
         
         if (!request.query) {
             ESP_LOGE(TAG, "Failed to allocate query memory");
             free(buf);
             return ESP_ERR_NO_MEM;
         }
         
         if (xQueueSend(sql_queue, &request, pdMS_TO_TICKS(CONFIG.queue_timeout_ms)) != pdTRUE) {
             ESP_LOGE(TAG, "Failed to queue SQL request");
             free(request.query);
             free(buf);
             return ESP_ERR_TIMEOUT;
         }
         
         ESP_LOGI(TAG, "SQL query queued");
     } else if (ws_pkt.type == HTTPD_WS_TYPE_CLOSE) {
         int fd = httpd_req_to_sockfd(req);
         remove_connection(fd);
         ESP_LOGI(TAG, "WebSocket connection closed: fd=%d", fd);
     }
     
     free(buf);
     return ESP_OK;
 }
 #define CHUNK_SIZE 1024*64
// Database file download handler
esp_err_t obd_logger_db_download_handler(httpd_req_t *req) {
    // Get the database file path
    // extern char db_path[128]; // Make sure this is externally accessible or pass it to the function
    
    ESP_LOGI(TAG, "DB download request received for file: %s", "/sdcard/obd_data.db");
    
    // // Check if database exists
    // struct stat file_stat;
    // if (stat(db_path, &file_stat) != 0) {
    //     ESP_LOGE(TAG, "Database file not found");
    //     httpd_resp_send_404(req);
    //     return ESP_FAIL;
    // }
    
    obd_logger_lock_close(); // Lock the database to prevent concurrent access
    
    // Set response headers
    httpd_resp_set_type(req, "application/octet-stream");
    httpd_resp_set_hdr(req, "Content-Disposition", "attachment; filename=wican_obd_data.db");
    
    // Open the file
    int file = open("/sdcard/obd_logger.db", 0666);
    if (file < 0) {
        ESP_LOGE(TAG, "Failed to open database file");
        obd_logger_unlock_open();
        httpd_resp_send_500(req);
        return ESP_FAIL;
    }
    
    // Read and send file in chunks
    // char* chunk = malloc(CHUNK_SIZE);
    void* chunk = heap_caps_malloc(CHUNK_SIZE, MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT);
    if (chunk == NULL) {
        ESP_LOGE(TAG, "Failed to allocate memory for file chunk");
        close(file);
        obd_logger_unlock_open();
        httpd_resp_send_500(req);
        return ESP_FAIL;
    }
    
    size_t bytes_read;
    esp_err_t ret = ESP_OK;
    
    // ESP_LOGI(TAG, "Starting to send database file, size: %ld bytes", file_stat.st_size);
    
    while ((bytes_read = read(file, chunk, CHUNK_SIZE)) > 0) {
        if(bytes_read < CHUNK_SIZE) {
            ESP_LOGI(TAG, "End of file reached, bytes read: %zu", bytes_read);
            
        }
        if (httpd_resp_send_chunk(req, chunk, bytes_read) != ESP_OK) {
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
    // obd_logger_unlock();
    obd_logger_unlock_open(); // Unlock the database after sending the file

    
    ESP_LOGI(TAG, "Database file download %s", (ret == ESP_OK) ? "completed successfully" : "failed");
    return ret;
}

const httpd_uri_t db_download_uri = {
    .uri = "/download_db",
    .method = HTTP_GET,
    .handler = obd_logger_db_download_handler,
    .user_ctx = NULL
};



// Initialization function
esp_err_t obd_logger_ws_iface_init(void) {
    // Create mutex for connection management
    conn_mutex = xSemaphoreCreateMutex();
    if (!conn_mutex) {
        ESP_LOGE(TAG, "Failed to create connection mutex");
        return ESP_ERR_NO_MEM;
    }
    
    // Initialize connection array
    memset(connections, 0, sizeof(connections));
    
    // Create queues
    sql_queue = xQueueCreate(CONFIG.sql_queue_size, sizeof(SQLRequest));
    if (!sql_queue) {
        ESP_LOGE(TAG, "Failed to create SQL queue");
        vSemaphoreDelete(conn_mutex);
        return ESP_ERR_NO_MEM;
    }
    
    resp_queue = xQueueCreate(CONFIG.resp_queue_size, sizeof(Response));
    if (!resp_queue) {
        ESP_LOGE(TAG, "Failed to create response queue");
        vQueueDelete(sql_queue);
        vSemaphoreDelete(conn_mutex);
        return ESP_ERR_NO_MEM;
    }
    
    // Allocate stack memory in PSRAM for tasks
    static StackType_t *sql_task_stack, *resp_task_stack;
    static StaticTask_t sql_task_buffer, resp_task_buffer;
    
    sql_task_stack = heap_caps_malloc(CONFIG.sql_task_stack_size, MALLOC_CAP_SPIRAM|MALLOC_CAP_8BIT);
    resp_task_stack = heap_caps_malloc(CONFIG.resp_task_stack_size, MALLOC_CAP_SPIRAM|MALLOC_CAP_8BIT);
    
    if (sql_task_stack == NULL || resp_task_stack == NULL) {
        ESP_LOGE(TAG, "Failed to allocate task stack memory");
        if (sql_task_stack) heap_caps_free(sql_task_stack);
        if (resp_task_stack) heap_caps_free(resp_task_stack);
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
        &sql_task_buffer
    );
    
    resp_task_handle = xTaskCreateStatic(
        response_task,
        "resp_task",
        CONFIG.resp_task_stack_size,
        NULL,
        5,
        resp_task_stack,
        &resp_task_buffer
    );
    
    if (sql_task_handle == NULL || resp_task_handle == NULL) {
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
 void obd_logger_ws_iface_deinit(void) {
     // Delete tasks
     if (sql_task_handle) vTaskDelete(sql_task_handle);
     if (resp_task_handle) vTaskDelete(resp_task_handle);
     
     // Delete queues
     if (sql_queue) vQueueDelete(sql_queue);
     if (resp_queue) vQueueDelete(resp_queue);
     
     // Delete mutex
     if (conn_mutex) vSemaphoreDelete(conn_mutex);
     
     ESP_LOGI(TAG, "OBD logger WebSocket interface deinitialized");
 }