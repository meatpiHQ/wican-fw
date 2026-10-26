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

#include <stdio.h>
#include <string.h>

#include "api_http.h"
#include "autopid.h"
#include "ble_manager.h"
#include "button_manager.h"
#include "cmdline_manager.h"
#include "data_logger.h"
#include "external_storage.h"
#include "filesystem.h"
#include "fw_update.h"
#include "ha_webhooks.h"
#include "led_manager.h"
#include "ota_manager.h"
#include "restart_tracker.h"
#include "usb_acm_cli.h"
#include "espnetlink_link.h"
#include "wifi_manager.h"
#include "dev_status_manager.h"
#include "usb_host_manager.h"

#include "main_factory.h"
#include "main_glue.h"

/** Composition glue: SD mount events flip filesystem's /sd backend. */
static void on_sd_event(bool mounted)
{
    filesystem_sd_set_mounted(mounted);
}

/** The SD slot summary for GET /api/status (2026-10-25): the web UI's
 *  notice bar and the pages that mention the card read it on the poll
 *  they make anyway. Composition glue: dev_status_manager must not know
 *  external_storage. False = no card in the slot (no `sd` object). */
static bool sd_summary(dev_status_sd_t *out)
{
    external_storage_status_t st;

    if (external_storage_status(&st) != ESP_OK ||
        st.state == EXTERNAL_STORAGE_ABSENT)
    {
        return false;
    }

    snprintf(out->state, sizeof(out->state), "%s",
             external_storage_state_str(st.state));
    snprintf(out->reason, sizeof(out->reason), "%s", st.reason);
    snprintf(out->name, sizeof(out->name), "%s", st.card_known ? st.name : "");
    out->capacity_bytes = st.card_known ? st.capacity_bytes : 0;
    out->serial = st.card_known ? st.serial : 0;
    return true;
}

void main_glue_wire_sd(void)
{
    external_storage_set_callback(on_sd_event);
    dev_status_manager_set_sd_provider(sd_summary);
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
    /* NimBLE host task context: enqueue only. Inline exec here wedged
     * the BLE stack on long commands (GATT "Unlikely Error"); the
     * dispatcher task runs the line and streams output via the sink.
     * On a full queue the async API answers "busy" itself. */
    (void)cmdline_manager_exec_line_async(line, ble_cli_sink, NULL);
}

void main_glue_wire_ble_cli(void)
{
    ble_manager_set_cli_handler(on_ble_cli_line);
}

/** Composition glue: the ESPNetLink dongle's GPS fixes (usb_acm_cli's
 *  console poll, or espnetlink_link's HTTP poll over the dongle's AP / USB
 *  link) become first-class autopid parameters, so a fix reaches HA
 *  (autopid_data), the data_logger, the dashboard and event rules with no
 *  per-consumer GPS code. Only a live fix is published; the last known
 *  position persists in autopid's cache. Poll-task context: non-blocking. */
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
    espnetlink_link_set_gps_sink(gps_to_autopid);
    /* /api/gps (usb_acm_cli's route) serves the HTTP-polled fix when the
       console has none: the WiFi-modem topology has no console at all */
    usb_acm_cli_set_gps_fallback(espnetlink_link_gps_get);
}

/** The USB connector summary for GET /api/status (2026-10-13): the web UI's
 *  header pill and ESPNetLink bar read it on the poll they make anyway.
 *  The kind follows the wizard's own rule (setup.js usbKind): the dongle
 *  whenever espnetlink_link has it in hand (on the wire or as a console),
 *  else an Ethernet driver with a device, else a receiver by its NMEA
 *  stream or the u-blox vendor id, else whatever enumerated. Composition
 *  glue: dev_status_manager must not know the USB components. */
static bool usb_summary(dev_status_usb_t *out)
{
    usb_host_manager_status_t u;
    espnetlink_link_status_t nl;
    usb_acm_gps_t g;

    if (usb_host_manager_status(&u) != ESP_OK)
    {
        return false;
    }

    bool have_nl = espnetlink_link_status(&nl) == ESP_OK;
    bool is_nl = have_nl && (nl.usb_attached || nl.dongle_console ||
                             (u.dev_present && u.dev_vid == 0x303a &&
                              u.dev_pid == 0x4007));

    if (is_nl)
    {
        out->kind = DEV_STATUS_USB_ESPNETLINK;
        snprintf(out->device_id, sizeof(out->device_id), "%s", nl.device_id);
        snprintf(out->sim, sizeof(out->sim), "%s", nl.dongle_sim);
        out->lte = nl.health_valid && nl.lte_connected;
        snprintf(out->operator_name, sizeof(out->operator_name), "%s",
                 nl.operator_name);
        out->fix = nl.gps_valid || (nl.health_valid && nl.dongle_gps_fix);
        out->satellites = espnetlink_link_gps_get(&g) == ESP_OK
                              ? g.satellites : 0;
        out->no_fix_s = nl.no_fix_s;
        out->no_fix_hint = nl.no_fix_hint;
        return true;
    }
    if (u.eth_connected || (u.device_present && u.driver[0] != '\0'))
    {
        out->kind = DEV_STATUS_USB_ETH;
        out->link = u.eth_connected && u.ip[0] != '\0';
        snprintf(out->ip, sizeof(out->ip), "%s", u.ip);
        snprintf(out->product, sizeof(out->product), "%s", u.dev_product);
        return true;
    }
    if (u.dev_present)
    {
        bool nmea = usb_acm_cli_connected() &&
                    strcmp(usb_acm_cli_mode(), "nmea") == 0;

        snprintf(out->product, sizeof(out->product), "%s", u.dev_product);
        if (nmea || u.dev_vid == 0x1546)
        {
            out->kind = DEV_STATUS_USB_GPS;
            out->fix = usb_acm_cli_gps_get(&g) == ESP_OK;
            out->satellites = out->fix ? g.satellites : 0;
            return true;
        }
        out->kind = DEV_STATUS_USB_OTHER;
        return true;
    }
    out->kind = DEV_STATUS_USB_NONE;
    return true;
}

void main_glue_wire_usb_status(void)
{
    dev_status_manager_set_usb_provider(usb_summary);
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
 * configured mode: held open while an AP client is attached (someone
 * is mid-configuration). Policy lives HERE: button_manager only reports
 * the press; wifi/ble/led stay ignorant of each other. */

#define GLUE_CONFIG_TIMEOUT_MS 600000

static TaskHandle_t s_cfgmode_task;
static StaticTask_t s_cfgmode_tcb;         /* internal: FreeRTOS object */
static StackType_t s_cfgmode_stack[3072] EXT_RAM_BSS_ATTR; /* LED/count
    only: the radio calls happened before the task spawns */

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
            idle_ms = 0; /* someone is configuring: hold the mode open */
        }
        else
        {
            idle_ms += 1000;
        }
    }

    ESP_LOGW(TAG, "config mode timeout: rebooting to configured state");
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

/* ---- firmware updates (2026-10-18, internal/TASK_fw_update.md) ----------------
 * Policy lives HERE: fw_update knows neither the dongle nor the radios, and
 * ota_manager knows no product. */

/* the project names a WiCAN PRO image may carry: the constant name of the
   new builds, the versioned names of the v6 alfas and the v4.5x factory
   firmware (the bench-proven downgrade stays open), and the legacy tree's */
static const char *const WICAN_IMAGE_PREFIXES[] =
{
    "wican-fw_obd_pro",
    "wican-pro",
};

/** The uplink a download would take, and whether it is the dongle's LTE:
 *  the station on a network of its own carries the default route (the
 *  wire does not take it over, prefer_usb_route false); on the dongle's
 *  own access point or the USB link alone the bytes are mobile data. */
static const char *fwu_uplink(bool *is_cellular)
{
    espnetlink_link_status_t nl;
    bool have_nl = espnetlink_link_status(&nl) == ESP_OK;

    *is_cellular = false;

    if (have_nl && nl.uplink == ESPNETLINK_UPLINK_ESPNETLINK_AP)
    {
        *is_cellular = true;
        return "espnetlink";
    }

    if (dev_status_manager_is_set(DEV_STATUS_BIT_STA_CONNECTED))
    {
        return "wifi";
    }

    if (have_nl && nl.uplink == ESPNETLINK_UPLINK_ESPNETLINK_USB)
    {
        *is_cellular = true;
        return "espnetlink_usb";
    }

    if (dev_status_manager_is_set(DEV_STATUS_BIT_ETH_CONNECTED))
    {
        return "eth";
    }

    return "none";
}

/** The HA push's `fw_update_available` / `fw_latest_version` (poster task,
 *  a PSRAM stack: the snapshot is a static, and fw_update_status() reads
 *  no flash by contract since the beta.1 field crash of 2026-10-21). */
static bool fwu_ha_provider(bool *available, char *latest, size_t cap)
{
    static fw_update_status_t st EXT_RAM_BSS_ATTR;

    if (fw_update_status(&st) != ESP_OK)
    {
        return false;
    }

    *available = st.enabled && st.available && st.installable;
    snprintf(latest, cap, "%s", (st.enabled && st.have_latest) ? st.latest_version
                                                              : "");
    return true;
}

/* ---- the dongle relay (2026-10-19): espnetlink_link's calls in the shape
 * fw_update asked for (fw_update_dongle_ops_t) */

static void endpoint_copy(const espnetlink_endpoint_t *in,
                          fw_update_dongle_endpoint_t *out)
{
    memset(out, 0, sizeof(*out));
    out->present = in->present;
    out->reachable = in->reachable;
    out->steady = in->steady;
    out->usb_attached = in->usb_attached;
    strlcpy(out->host, in->host, sizeof(out->host));
    strlcpy(out->via, in->via, sizeof(out->via));
    strlcpy(out->device_id, in->device_id, sizeof(out->device_id));
    strlcpy(out->fw_version, in->fw_version, sizeof(out->fw_version));
    out->api_level = in->api_level;
}

static esp_err_t fwd_endpoint(fw_update_dongle_endpoint_t *out)
{
    espnetlink_endpoint_t ep;
    esp_err_t err = espnetlink_link_endpoint(&ep);

    if (err != ESP_OK)
    {
        memset(out, 0, sizeof(*out));
        return err;
    }

    endpoint_copy(&ep, out);
    return ESP_OK;
}

static esp_err_t fwd_hold(bool hold)
{
    return espnetlink_link_hold(hold);
}

static void fwd_expect_reboot(void)
{
    espnetlink_link_expect_dongle_reboot();
}

static esp_err_t fwd_identify(int timeout_ms, fw_update_dongle_endpoint_t *out)
{
    espnetlink_endpoint_t ep;
    esp_err_t err = espnetlink_link_identify_now(timeout_ms, &ep);

    if (out != NULL)
    {
        if (err == ESP_OK)
        {
            endpoint_copy(&ep, out);
        }
        else
        {
            memset(out, 0, sizeof(*out));
        }
    }

    return err;
}

static const fw_update_dongle_ops_t FWD_OPS =
{
    .endpoint = fwd_endpoint,
    .hold = fwd_hold,
    .expect_reboot = fwd_expect_reboot,
    .identify = fwd_identify,
};

void main_glue_wire_fw_update(void)
{
    (void)fw_update_set_dongle_ops(&FWD_OPS);
    (void)ota_manager_set_product(
        "WiCAN PRO", WICAN_IMAGE_PREFIXES,
        sizeof(WICAN_IMAGE_PREFIXES) / sizeof(WICAN_IMAGE_PREFIXES[0]));
    (void)fw_update_set_uplink_provider(fwu_uplink);
    (void)ha_webhooks_set_fw_provider(fwu_ha_provider);
}

void main_glue_wire_factory_reset(void)
{
    (void)api_http_set_factory_reset(main_factory_reset);
}
