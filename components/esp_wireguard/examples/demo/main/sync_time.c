#include <time.h>
#include <inttypes.h>
#include <esp_sntp.h>
#include <esp_log.h>

#include "sync_time.h"

#define TAG "sync_time"

static void time_sync_notification_cb(struct timeval *tv)
{
	ESP_LOGI(TAG, "Time synced");
}

static void initialize_sntp(void)
{
	ESP_LOGI(TAG, "Initializing SNTP");
	sntp_setoperatingmode(SNTP_OPMODE_POLL);
	sntp_setservername(0, "pool.ntp.org");
	sntp_set_time_sync_notification_cb(time_sync_notification_cb);
#ifdef CONFIG_SNTP_TIME_SYNC_METHOD_SMOOTH
	sntp_set_sync_mode(SNTP_SYNC_MODE_SMOOTH);
#endif
	sntp_init();
}

void obtain_time(void)
{
	int retry = 0;
	const int retry_count = 20;

	initialize_sntp();
	while (sntp_get_sync_status() == SNTP_SYNC_STATUS_RESET && ++retry < retry_count) {
		ESP_LOGI(TAG, "Waiting for system time to be set... (%i/%i)", retry, retry_count);
		vTaskDelay(2000 / portTICK_PERIOD_MS);
	}
}
// vim: noexpandtab
