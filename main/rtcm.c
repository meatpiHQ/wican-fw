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

#include "rtcm.h"
#include "driver/i2c.h"
#include "esp_log.h"
#include "esp_http_client.h"
#include "cJSON.h"
#include <string.h>
#include <sys/param.h>
#include <stdlib.h>
#include "esp_netif_sntp.h"
#include "lwip/ip_addr.h"
#include "esp_sntp.h"
#include "esp_heap_caps.h"

#define TAG "rtcm"

#define RTCM_I2C_TIMEOUT_MS 1000

#define RX8130_ADDR             0x32

#define RX8130_REG_SEC          0x10
#define RX8130_REG_MIN          0x11 
#define RX8130_REG_HOUR         0x12
#define RX8130_REG_CTRL1        0x30
#define RX8130_REG_CTRL2        0x32
#define RX8130_REG_EVT_CTRL     0x1C
#define RX8130_REG_EVT1         0x1D
#define RX8130_REG_EVT2         0x1E
#define RX8130_REG_EVT3         0x1F
#define RX8130_REG_WEEK         0x13
#define RX8130_REG_DAY          0x14
#define RX8130_REG_MONTH        0x15
#define RX8130_REG_YEAR         0x16
#define RX8130_REG_ID           0x17

#define MAX_HTTP_OUTPUT_BUFFER 4096

static i2c_port_t rtcm_i2c = I2C_NUM_MAX;

static esp_err_t rx8130_register_read(uint8_t reg_addr, uint8_t *data, size_t len)
{
    return i2c_master_write_read_device(rtcm_i2c, RX8130_ADDR, &reg_addr, 1, data, len, pdMS_TO_TICKS(RTCM_I2C_TIMEOUT_MS));
}

static esp_err_t rx8130_register_write(uint8_t reg_addr, uint8_t data)
{
    uint8_t write_buf[2] = {reg_addr, data};
    return i2c_master_write_to_device(rtcm_i2c, RX8130_ADDR, write_buf, 2, pdMS_TO_TICKS(RTCM_I2C_TIMEOUT_MS));
}

esp_err_t rtcm_get_time(uint8_t *hour, uint8_t *min, uint8_t *sec)
{
    esp_err_t ret;

    ret = rx8130_register_read(RX8130_REG_SEC, sec, 1);
    if (ret != ESP_OK) return ret;

    ret = rx8130_register_read(RX8130_REG_MIN, min, 1);
    if (ret != ESP_OK) return ret;

    ret = rx8130_register_read(RX8130_REG_HOUR, hour, 1);
    return ret;
}

esp_err_t rtcm_set_time(uint8_t hour, uint8_t min, uint8_t sec)
{
    esp_err_t ret;

    ret = rx8130_register_write(RX8130_REG_SEC, sec);
    if (ret != ESP_OK) return ret;

    ret = rx8130_register_write(RX8130_REG_MIN, min);
    if (ret != ESP_OK) return ret;

    ret = rx8130_register_write(RX8130_REG_HOUR, hour);
    return ret;
}

esp_err_t rtcm_get_date(uint8_t *year, uint8_t *month, uint8_t *day, uint8_t *weekday)
{
    esp_err_t ret;

    ret = rx8130_register_read(RX8130_REG_YEAR, year, 1);
    if (ret != ESP_OK) return ret;

    ret = rx8130_register_read(RX8130_REG_MONTH, month, 1);
    if (ret != ESP_OK) return ret;

    ret = rx8130_register_read(RX8130_REG_DAY, day, 1);
    if (ret != ESP_OK) return ret;

    ret = rx8130_register_read(RX8130_REG_WEEK, weekday, 1);
    return ret;
}

esp_err_t rtcm_set_date(uint8_t year, uint8_t month, uint8_t day, uint8_t weekday)
{
    esp_err_t ret;

    ret = rx8130_register_write(RX8130_REG_YEAR, year);
    if (ret != ESP_OK) return ret;

    ret = rx8130_register_write(RX8130_REG_MONTH, month);
    if (ret != ESP_OK) return ret;

    ret = rx8130_register_write(RX8130_REG_DAY, day);
    if (ret != ESP_OK) return ret;

    ret = rx8130_register_write(RX8130_REG_WEEK, weekday);
    return ret;
}

esp_err_t rtcm_get_device_id(uint8_t *id)
{
    return rx8130_register_read(RX8130_REG_ID, id, 1);
}

esp_err_t rtcm_get_iso8601_time(char *timestamp, size_t max_len)
{
    if (timestamp == NULL || max_len < 20) {
        return ESP_ERR_INVALID_ARG;
    }
    
    // Try to get time from RTCM module
    uint8_t hour, min, sec;
    uint8_t year, month, day, weekday;
    
    if (rtcm_get_time(&hour, &min, &sec) == ESP_OK && 
        rtcm_get_date(&year, &month, &day, &weekday) == ESP_OK) {
        
        // Convert BCD format to decimal
        uint8_t hour_dec = ((hour >> 4) & 0x0F) * 10 + (hour & 0x0F);
        uint8_t min_dec = ((min >> 4) & 0x0F) * 10 + (min & 0x0F);
        uint8_t sec_dec = ((sec >> 4) & 0x0F) * 10 + (sec & 0x0F);
        uint8_t year_dec = ((year >> 4) & 0x0F) * 10 + (year & 0x0F);
        uint8_t month_dec = ((month >> 4) & 0x0F) * 10 + (month & 0x0F);
        uint8_t day_dec = ((day >> 4) & 0x0F) * 10 + (day & 0x0F);
        
        // Format timestamp 
        snprintf(timestamp, max_len, "20%02d-%02d-%02dT%02d:%02d:%02d", 
                year_dec, month_dec, day_dec, hour_dec, min_dec, sec_dec);
                
        return ESP_OK;
    } else {
        // Use system time as fallback
        time_t now;
        struct tm timeinfo;
        
        time(&now);
        localtime_r(&now, &timeinfo);
        strftime(timestamp, max_len, "%Y-%m-%dT%H:%M:%S", &timeinfo);
        
        ESP_LOGW(TAG, "RTCM time not available, using system time: %s", timestamp);
        return ESP_OK;
    }
}

time_t rtcm_bcd_to_unix_timestamp(uint8_t hour, uint8_t min, uint8_t sec, 
                                 uint8_t year, uint8_t month, uint8_t day)
{
    // Convert BCD format to decimal
    uint8_t hour_dec = ((hour >> 4) & 0x0F) * 10 + (hour & 0x0F);
    uint8_t min_dec = ((min >> 4) & 0x0F) * 10 + (min & 0x0F);
    uint8_t sec_dec = ((sec >> 4) & 0x0F) * 10 + (sec & 0x0F);
    uint8_t year_dec = ((year >> 4) & 0x0F) * 10 + (year & 0x0F);
    uint8_t month_dec = ((month >> 4) & 0x0F) * 10 + (month & 0x0F);
    uint8_t day_dec = ((day >> 4) & 0x0F) * 10 + (day & 0x0F);
    
    // Calculate Unix timestamp
    // Note: This is a simplified calculation that doesn't account for leap years perfectly
    // but is sufficient for most applications
    struct tm timeinfo;
    timeinfo.tm_year = 100 + year_dec; // Years since 1900 (assuming 20xx)
    timeinfo.tm_mon = month_dec - 1;   // Months are 0-based
    timeinfo.tm_mday = day_dec;
    timeinfo.tm_hour = hour_dec;
    timeinfo.tm_min = min_dec;
    timeinfo.tm_sec = sec_dec;
    timeinfo.tm_isdst = -1;            // Not used
    
    // Validate time components to avoid invalid timestamps
    if (timeinfo.tm_year < 100 || timeinfo.tm_year > 200 ||  // Year from 2000-2100
        timeinfo.tm_mon < 0 || timeinfo.tm_mon > 11 ||       // Month 0-11
        timeinfo.tm_mday < 1 || timeinfo.tm_mday > 31 ||     // Day 1-31
        timeinfo.tm_hour < 0 || timeinfo.tm_hour > 23 ||     // Hour 0-23
        timeinfo.tm_min < 0 || timeinfo.tm_min > 59 ||       // Minute 0-59
        timeinfo.tm_sec < 0 || timeinfo.tm_sec > 59) {       // Second 0-59
        ESP_LOGE(TAG, "Invalid time components: %02d-%02d-%02d %02d:%02d:%02d", 
                 year_dec, month_dec, day_dec, hour_dec, min_dec, sec_dec);
        return 0;
    }
    
    time_t unix_timestamp = mktime(&timeinfo);
    if (unix_timestamp < 0) {
        ESP_LOGE(TAG, "Failed to convert time to Unix timestamp");
        return 0;
    }
    
    return unix_timestamp;
}

time_t rtcm_get_unix_timestamp(void)
{
    uint8_t hour, min, sec;
    uint8_t year, month, day, weekday;
    
    // Read current time and date from RTC
    if (rtcm_get_time(&hour, &min, &sec) != ESP_OK || 
        rtcm_get_date(&year, &month, &day, &weekday) != ESP_OK) {
        ESP_LOGE(TAG, "Failed to get time/date from RTC");
        return 0;
    }
    
    return rtcm_bcd_to_unix_timestamp(hour, min, sec, year, month, day);
}

esp_err_t _http_event_handler(esp_http_client_event_t *evt)
{
    static int output_len;

    switch(evt->event_id)
    {
        case HTTP_EVENT_ERROR:
            ESP_LOGE(TAG, "HTTP_EVENT_ERROR");
            break;
        case HTTP_EVENT_ON_CONNECTED:
            ESP_LOGI(TAG, "HTTP_EVENT_ON_CONNECTED");
            break;
        case HTTP_EVENT_HEADER_SENT:
            ESP_LOGI(TAG, "HTTP_EVENT_HEADER_SENT");
            break;
        case HTTP_EVENT_ON_HEADER:
            ESP_LOGI(TAG, "HTTP_EVENT_ON_HEADER, key=%s, value=%s", evt->header_key, evt->header_value);
            break;
        case HTTP_EVENT_REDIRECT:
            ESP_LOGD(TAG, "HTTP_EVENT_REDIRECT");
            esp_http_client_set_header(evt->client, "From", "user@example.com");
            esp_http_client_set_header(evt->client, "Accept", "text/html");
            esp_http_client_set_redirection(evt->client);
            break;
        case HTTP_EVENT_ON_DATA:
            ESP_LOGI(TAG, "HTTP_EVENT_ON_DATA, len=%d", evt->data_len);
            if (output_len == 0 && evt->user_data)
            {
                memset(evt->user_data, 0, MAX_HTTP_OUTPUT_BUFFER);
            }

            if (!esp_http_client_is_chunked_response(evt->client))
            {
                int copy_len = 0;
                if (evt->user_data)
                {
                    copy_len = MIN(evt->data_len, (MAX_HTTP_OUTPUT_BUFFER - output_len));
                    if (copy_len)
                    {
                        memcpy(evt->user_data + output_len, evt->data, copy_len);
                    }
                }
                output_len += copy_len;
            }
            break;

        case HTTP_EVENT_ON_FINISH:
            ESP_LOGI(TAG, "HTTP_EVENT_ON_FINISH");
            output_len = 0;
            break;

        case HTTP_EVENT_DISCONNECTED:
            ESP_LOGI(TAG, "HTTP_EVENT_DISCONNECTED");
            output_len = 0;
            break;
    }
    return ESP_OK;
}

static esp_err_t rtcm_set_time_zone(void) 
{
    ESP_LOGI(TAG, "Getting timezone from worldtimeapi.org");

    char *local_response_buffer = heap_caps_malloc(MAX_HTTP_OUTPUT_BUFFER + 1, MALLOC_CAP_SPIRAM);
    if (local_response_buffer == NULL)
    {
        ESP_LOGE(TAG, "Failed to allocate response buffer in PSRAM");
        return ESP_ERR_NO_MEM;
    }

    memset(local_response_buffer, 0, MAX_HTTP_OUTPUT_BUFFER + 1);
    
    esp_http_client_config_t config = {
        .url = "http://worldtimeapi.org/api/ip",
        .event_handler = _http_event_handler,
        .user_data = local_response_buffer,
    };
    
    esp_http_client_handle_t client = esp_http_client_init(&config);
    ESP_LOGI(TAG, "Performing HTTP request");
    esp_err_t err = esp_http_client_perform(client);
    
    if (err == ESP_OK)
    {
        ESP_LOGI(TAG, "HTTP request successful");
        ESP_LOG_BUFFER_HEXDUMP(TAG, local_response_buffer, strlen(local_response_buffer), ESP_LOG_INFO);
        cJSON *root = cJSON_Parse(local_response_buffer);
        if (root)
        {
            ESP_LOGI(TAG, "JSON parsed successfully");
            
            // Get raw_offset and dst_offset from worldtimeapi
            cJSON *raw_offset = cJSON_GetObjectItem(root, "raw_offset");
            cJSON *dst_offset = cJSON_GetObjectItem(root, "dst_offset");
            
            if (raw_offset && dst_offset)
            {
                int total_offset = (raw_offset->valueint + dst_offset->valueint) / 3600; // Convert to hours
                char tz_buf[32];
                snprintf(tz_buf, sizeof(tz_buf), "UTC%+d", -total_offset);
                ESP_LOGI(TAG, "Setting timezone offset: %s", tz_buf);
                setenv("TZ", tz_buf, 1);
                tzset();
            }
            else
            {
                ESP_LOGE(TAG, "No offset found in JSON response");
            }
            cJSON_Delete(root);
        }
        else
        {
            ESP_LOGE(TAG, "Failed to parse JSON response");
        }
    }
    else
    {
        ESP_LOGE(TAG, "HTTP request failed: %s", esp_err_to_name(err));
    }
    
    esp_http_client_cleanup(client);
    heap_caps_free(local_response_buffer);
    return err;
}

static void time_sync_notification_cb(struct timeval *tv)
{
    ESP_LOGI(TAG, "Time synchronized from SNTP");
}

static esp_err_t update_rtc_from_system_time(void)
{
    time_t now;
    struct tm timeinfo;
    
    time(&now);
    localtime_r(&now, &timeinfo);
    
    // Convert to BCD format for RX8130
    uint8_t hour = ((timeinfo.tm_hour / 10) << 4) | (timeinfo.tm_hour % 10);
    uint8_t min = ((timeinfo.tm_min / 10) << 4) | (timeinfo.tm_min % 10);
    uint8_t sec = ((timeinfo.tm_sec / 10) << 4) | (timeinfo.tm_sec % 10);
    uint8_t year = (((timeinfo.tm_year % 100) / 10) << 4) | ((timeinfo.tm_year % 100) % 10);
    uint8_t month = (((timeinfo.tm_mon + 1) / 10) << 4) | ((timeinfo.tm_mon + 1) % 10);
    uint8_t day = ((timeinfo.tm_mday / 10) << 4) | (timeinfo.tm_mday % 10);
    uint8_t weekday = timeinfo.tm_wday;
    
    esp_err_t ret;
    
    // Update RTC time
    ret = rtcm_set_time(hour, min, sec);
    if (ret != ESP_OK) return ret;
    
    // Update RTC date
    ret = rtcm_set_date(year, month, day, weekday);
    return ret;
}

esp_err_t rtcm_sync_internet_time(void)
{
    // First get timezone from internet
    esp_err_t ret = rtcm_set_time_zone();
    if (ret != ESP_OK)
    {
        ESP_LOGE(TAG, "Failed to get timezone");
        return ret;
    }

    // Initialize SNTP
    static esp_sntp_config_t config = ESP_NETIF_SNTP_DEFAULT_CONFIG("pool.ntp.org");
    config.sync_cb = time_sync_notification_cb;
    config.smooth_sync = true;
    esp_netif_sntp_init(&config);

    // Wait for time to be set
    int retry = 0;
    const int retry_count = 15;
    while (esp_netif_sntp_sync_wait(2000 / portTICK_PERIOD_MS) == ESP_ERR_TIMEOUT && ++retry < retry_count)
    {
        ESP_LOGI(TAG, "Waiting for SNTP sync... (%d/%d)", retry, retry_count);
    }

    if (retry == retry_count)
    {
        ESP_LOGE(TAG, "SNTP sync failed");
        esp_netif_sntp_deinit();
        return ESP_FAIL;
    }

    // Update RTC with synchronized time
    ret = update_rtc_from_system_time();
    if (ret != ESP_OK)
    {
        ESP_LOGE(TAG, "Failed to update RTC with synchronized time");
    }

    esp_netif_sntp_deinit();
    return ret;
}

esp_err_t rtcm_sync_system_time_from_rtc(void)
{
    uint8_t hour, min, sec;
    uint8_t year, month, day, weekday;
    esp_err_t ret;
    
    // Read current time and date from RTC
    ret = rtcm_get_time(&hour, &min, &sec);
    if (ret != ESP_OK) {
        ESP_LOGE(TAG, "Failed to get time from RTC");
        return ret;
    }
    
    ret = rtcm_get_date(&year, &month, &day, &weekday);
    if (ret != ESP_OK) {
        ESP_LOGE(TAG, "Failed to get date from RTC");
        return ret;
    }
    
    // Convert BCD format to decimal
    uint8_t hour_dec = ((hour >> 4) & 0x0F) * 10 + (hour & 0x0F);
    uint8_t min_dec = ((min >> 4) & 0x0F) * 10 + (min & 0x0F);
    uint8_t sec_dec = ((sec >> 4) & 0x0F) * 10 + (sec & 0x0F);
    uint8_t year_dec = ((year >> 4) & 0x0F) * 10 + (year & 0x0F);
    uint8_t month_dec = ((month >> 4) & 0x0F) * 10 + (month & 0x0F);
    uint8_t day_dec = ((day >> 4) & 0x0F) * 10 + (day & 0x0F);
    
    // Set up the timespec structure
    struct timeval tv;
    struct tm timeinfo = {
        .tm_sec = sec_dec,
        .tm_min = min_dec,
        .tm_hour = hour_dec,
        .tm_mday = day_dec,
        .tm_mon = month_dec - 1,  // tm_mon is 0-based (0-11)
        .tm_year = 100 + year_dec, // Years since 1900, assuming 20xx
        .tm_isdst = -1            // Let the system determine DST
    };
    
    // Convert to timestamp
    time_t timestamp = mktime(&timeinfo);
    if (timestamp == -1) {
        ESP_LOGE(TAG, "Failed to convert RTC time to timestamp");
        return ESP_FAIL;
    }
    
    // Set system time
    tv.tv_sec = timestamp;
    tv.tv_usec = 0;
    ret = settimeofday(&tv, NULL);
    
    if (ret != 0) {
        ESP_LOGE(TAG, "Failed to set system time from RTC: %d", ret);
        return ESP_FAIL;
    }
    
    ESP_LOGI(TAG, "System time synchronized from RTC: %04d-%02d-%02d %02d:%02d:%02d", 
             2000 + year_dec, month_dec, day_dec, hour_dec, min_dec, sec_dec);
             
    return ESP_OK;
}

esp_err_t rtcm_init(i2c_port_t i2c_num)
{
    esp_err_t ret;

    rtcm_i2c = i2c_num;
    // Initialize RX8130 registers
    ret = rx8130_register_write(RX8130_REG_CTRL1, 0x00);
    if (ret != ESP_OK) return ret;

    ret = rx8130_register_write(RX8130_REG_CTRL2, 0xC7);
    if (ret != ESP_OK) return ret;

    ret = rx8130_register_write(RX8130_REG_EVT_CTRL, 0x04);
    if (ret != ESP_OK) return ret;

    ret = rx8130_register_write(RX8130_REG_EVT1, 0x00);
    if (ret != ESP_OK) return ret;

    ret = rx8130_register_write(RX8130_REG_EVT2, 0x40);
    if (ret != ESP_OK) return ret;

    ret = rx8130_register_write(RX8130_REG_EVT3, 0x10);
    if (ret != ESP_OK) return ret;

    ESP_LOGI(TAG, "RTC module initialized");
    return ESP_OK;
}
