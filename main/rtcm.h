#ifndef _RTCM_H_
#define _RTCM_H_

#include <stdint.h>
#include "esp_err.h"
#include "driver/i2c.h"
#include <time.h>

esp_err_t rtcm_init(i2c_port_t i2c_num);
esp_err_t rtcm_get_time(uint8_t *hour, uint8_t *min, uint8_t *sec);
esp_err_t rtcm_set_time(uint8_t hour, uint8_t min, uint8_t sec);
esp_err_t rtcm_get_date(uint8_t *year, uint8_t *month, uint8_t *day, uint8_t *weekday);
esp_err_t rtcm_set_date(uint8_t year, uint8_t month, uint8_t day, uint8_t weekday);
esp_err_t rtcm_get_device_id(uint8_t *id);
esp_err_t rtcm_sync_internet_time(void);
esp_err_t rtcm_get_timezone(int *timezone_offset);
esp_err_t rtcm_get_iso8601_time(char *timestamp, size_t max_len);
time_t rtcm_bcd_to_unix_timestamp(uint8_t hour, uint8_t min, uint8_t sec, 
uint8_t year, uint8_t month, uint8_t day);
time_t rtcm_get_unix_timestamp(void);
esp_err_t rtcm_sync_system_time_from_rtc(void);
#endif /* _RTCM_H_ */
