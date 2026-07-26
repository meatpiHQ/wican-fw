/**
 * @file main_glue.c
 * @brief Composition glue (Architecture §11): every callback that
 *        connects two components lives here, so neither side ever
 *        depends on the other. app_main wires each one at the boot
 *        point where both sides exist.
 */
#include <stddef.h>

#include "esp_attr.h"
#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

#include "autopid.h"
#include "ble_manager.h"
#include "button_manager.h"
#include "cmdline_manager.h"
#include "data_logger.h"
#include "external_storage.h"
#include "filesystem.h"
#include "led_manager.h"
#include "ota_manager.h"
#include "restart_tracker.h"
#include "usb_acm_cli.h"
#include "wifi_manager.h"

#include "main_glue.h"

/** Composition glue: SD mount events flip filesystem's /sd backend. */
static void on_sd_event(bool mounted)
{
    filesystem_sd_set_mounted(mounted);
}

void main_glue_wire_sd(void)
{
    external_storage_set_callback(on_sd_event);
}

/** Composition glue: the OTA session drives the "do not power off" LED
 *  indication (fast-blinking red beats everything while flashing). */
static void on_ota_event(ota_manager_state_t state)
{
    static const led_manager_state_t OTA_LED =
        { .mode = LED_MANAGER_BLINK_FAST, .r = 255 };

    if (state == OTA_MANAGER_RECEIVING)
    {
        led_manager_set(LED_MANAGER_PRIO_CRITICAL, &OTA_LED);
    }
    else
    {
        led_manager_clear(LED_MANAGER_PRIO_CRITICAL);
    }
}

void main_glue_wire_ota_led(void)
{
    ota_manager_set_event_cb(on_ota_event);
}

/** Composition glue: complete CLI lines from the BLE console run through
 *  cmdline_manager; output notifies back over the CLI OUT characteristic
 *  (neither component depends on the other). */
static void ble_cli_sink(const char *data, size_t len, void *arg)
{
    (void)arg;
    ble_manager_cli_write(data, len);
}

static void on_ble_cli_line(const char *line)
{
    /* NimBLE host task context — enqueue only. Inline exec here wedged
     * the BLE stack on long commands (GATT "Unlikely Error"); the
     * dispatcher task runs the line and streams output via the sink.
     * On a full queue the async API answers "busy" itself. */
    (void)cmdline_manager_exec_line_async(line, ble_cli_sink, NULL);
}

void main_glue_wire_ble_cli(void)
{
    ble_manager_set_cli_handler(on_ble_cli_line);
}

/** Composition glue: the ESPNetLink dongle's GPS fixes (usb_acm_cli's poll)
 *  become first-class autopid parameters, so a fix reaches HA
 *  (autopid_data), the data_logger, the dashboard and event rules with no
 *  per-consumer GPS code. Only a live fix is published; the last known
 *  position persists in autopid's cache. Poll-task context — non-blocking. */
static void gps_to_autopid(const usb_acm_gps_t *g)
{
    if (g == NULL || !g->valid)
    {
        return;
    }

    autopid_publish_external("gps_latitude",   "\xC2\xB0", g->latitude);
    autopid_publish_external("gps_longitude",  "\xC2\xB0", g->longitude);
    autopid_publish_external("gps_altitude",   "m",        g->altitude_m);
    autopid_publish_external("gps_speed",      "km/h",     g->speed_kmph);
    autopid_publish_external("gps_heading",    "\xC2\xB0", g->heading_deg);
    autopid_publish_external("gps_satellites", "",
                             (double)g->satellites);
}

void main_glue_wire_gps(void)
{
    usb_acm_cli_set_gps_sink(gps_to_autopid);
}

/** Composition glue: autopid samples -> the logger's params stream
 *  (gated by data_logger's autopid_log setting; both components stay
 *  ignorant of each other). */
void main_glue_wire_autopid_logger(void)
{
    autopid_set_value_sink(data_logger_autopid_sink);
}

/* ---- button CONFIG MODE (meatpi 2026-07-19, legacy config_mode.c) -----------
 * Long-press while running -> "let me configure the device": BLE off,
 * AP forced up whatever the mode, LED alternating yellow/blue (the
 * legacy pattern), and a 10 min timeout that reboots back into the
 * configured mode — held open while an AP client is attached (someone
 * is mid-configuration). Policy lives HERE: button_manager only reports
 * the press; wifi/ble/led stay ignorant of each other. */

#define GLUE_CONFIG_TIMEOUT_MS 600000

static TaskHandle_t s_cfgmode_task;
static StaticTask_t s_cfgmode_tcb;         /* internal: FreeRTOS object */
static StackType_t s_cfgmode_stack[3072] EXT_RAM_BSS_ATTR; /* LED/count
    only — the radio calls happened before the task spawns */

static void config_mode_task(void *arg)
{
    static const char *TAG = "main";
    bool led_phase = false;
    int idle_ms = 0;

    (void)arg;

    while (idle_ms < GLUE_CONFIG_TIMEOUT_MS)
    {
        /* legacy pattern: alternate solid yellow / solid blue each tick */
        led_manager_state_t s =
        {
            .mode = LED_MANAGER_SOLID,
            .r = led_phase ? 255 : 0,
            .g = led_phase ? 255 : 0,
            .b = led_phase ? 0 : 255,
        };

        led_manager_set(LED_MANAGER_PRIO_ALERT, &s);
        led_phase = !led_phase;

        vTaskDelay(pdMS_TO_TICKS(1000));

        if (wifi_manager_get_ap_station_count() > 0)
        {
            idle_ms = 0; /* someone is configuring — hold the mode open */
        }
        else
        {
            idle_ms += 1000;
        }
    }

    ESP_LOGW(TAG, "config mode timeout — rebooting to configured state");
    led_manager_clear(LED_MANAGER_PRIO_ALERT);
    restart_tracker_restart(RESTART_TRACKER_PLANNED_REASON_CONFIG_RECOVERY,
                            RESTART_TRACKER_SOURCE_BUTTON, 0);
}

static void on_button_longpress(void)
{
    static const char *TAG = "main";

    if (s_cfgmode_task != NULL)
    {
        ESP_LOGI(TAG, "config mode already active");
        return;
    }

    ESP_LOGI(TAG, "button long-press: entering CONFIG MODE "
                  "(BLE off, AP forced up, %d min timeout)",
             GLUE_CONFIG_TIMEOUT_MS / 60000);
    ble_manager_stop();
    wifi_manager_config_ap();
    s_cfgmode_task = xTaskCreateStatic(
        config_mode_task, "config_mode",
        sizeof(s_cfgmode_stack) / sizeof(s_cfgmode_stack[0]), NULL, 5,
        s_cfgmode_stack, &s_cfgmode_tcb);
}

void main_glue_wire_button(void)
{
    button_manager_set_longpress_cb(on_button_longpress);
}
