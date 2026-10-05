/**
 * @file main_safemode.c
 * @brief SAFE MODE — the v6 port of legacy safemode.c (see the header
 *        for semantics). Composition-root code by design: it runs
 *        INSTEAD of the composition, uses no component settings, and
 *        touches only what recovery needs (WiFi AP + httpd + OTA +
 *        factory erase). Button is POLLED, never an interrupt.
 *
 *        2026-10-05: safe mode is also where a user gets the crash report
 *        of a device that no longer starts (the crash park breathes red,
 *        the user holds the button while plugging it in): the report the
 *        restart tracker keeps in NVS is on the page, as text to copy and
 *        as a file to download, and on the console. NVS is the one store
 *        safe mode opens anyway; no filesystem is mounted for it.
 */
#include "main_safemode.h"

#include <stdio.h>
#include <string.h>

#include "driver/gpio.h"
#include "esp_attr.h"
#include "esp_event.h"
#include "esp_log.h"
#include "esp_mac.h"
#include "esp_netif.h"
#include "esp_ota_ops.h"
#include "esp_partition.h"
#include "esp_wifi.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "nvs_flash.h"

#include "i2c_bus.h"
#include "led_manager.h"
#include "multipart_upload.h"
#include "restart_tracker.h"

#include "esp_http_server.h"

static const char *TAG = "safemode";

#define SM_BUTTON_GPIO     MAIN_SAFEMODE_BUTTON_GPIO
#define SM_HOLD_MS         5000   /* legacy: 5 s to latch safe mode      */
#define SM_IDLE_TIMEOUT_MS 600000 /* reboot after 10 min with no client */

/* ---- boot-hold detection ---------------------------------------------------- */

bool main_safemode_check(void)
{
    /* polled input, pull-up, active low — no interrupts (meatpi) */
    gpio_reset_pin(SM_BUTTON_GPIO);
    gpio_set_direction(SM_BUTTON_GPIO, GPIO_MODE_INPUT);
    gpio_set_pull_mode(SM_BUTTON_GPIO, GPIO_PULLUP_ONLY);
    vTaskDelay(pdMS_TO_TICKS(2)); /* let the pull-up settle before the
                                     released-pin read */

    int lvl = gpio_get_level(SM_BUTTON_GPIO);

    ESP_LOGI(TAG, "boot button level=%d (0 = pressed)", lvl);

    if (lvl != 0)
    {
        return false; /* the normal boot: cost is one GPIO read */
    }

    /* held at power-on: legacy feedback = sky blue while deciding */
    ESP_LOGI(TAG, "button held at boot — hold %d s for safe mode",
             SM_HOLD_MS / 1000);
    i2c_bus_init();
    led_manager_boot_color(135, 206, 235);

    int held_ms = 0;

    while (gpio_get_level(SM_BUTTON_GPIO) == 0)
    {
        vTaskDelay(pdMS_TO_TICKS(100));
        held_ms += 100;

        if (held_ms % 1000 == 0)
        {
            ESP_LOGI(TAG, "held %d ms ...", held_ms);
        }

        if (held_ms >= SM_HOLD_MS)
        {
            return true; /* caller runs main_safemode_run via us below */
        }
    }

    ESP_LOGI(TAG, "released after %d ms — normal boot", held_ms);
    return false;
}

/* ---- OTA upload (multipart, the legacy handler shape) ----------------------- */

typedef struct
{
    esp_ota_handle_t       handle;
    const esp_partition_t *partition;
    bool                   started;
    esp_err_t              last_err;
} sm_ota_state_t;

static bool ota_on_part_begin(const multipart_part_info_t *info,
                              void *user_ctx)
{
    sm_ota_state_t *st = user_ctx;

    if (info == NULL || strcasecmp(info->name, "firmware") != 0)
    {
        return false; /* only the "firmware" form field */
    }

    st->partition = esp_ota_get_next_update_partition(NULL);

    if (st->partition == NULL)
    {
        st->last_err = ESP_ERR_NOT_FOUND;
        return false;
    }

    st->last_err = esp_ota_begin(st->partition, OTA_WITH_SEQUENTIAL_WRITES,
                                 &st->handle);

    if (st->last_err != ESP_OK)
    {
        ESP_LOGE(TAG, "esp_ota_begin: %s", esp_err_to_name(st->last_err));
        return false;
    }

    st->started = true;
    ESP_LOGI(TAG, "firmware upload started (%s)", info->filename);
    return true;
}

static esp_err_t ota_on_part_data(const char *data, size_t len,
                                  void *user_ctx)
{
    sm_ota_state_t *st = user_ctx;

    if (st->started && len > 0)
    {
        st->last_err = esp_ota_write(st->handle, data, len);

        if (st->last_err != ESP_OK)
        {
            ESP_LOGE(TAG, "esp_ota_write: %s",
                     esp_err_to_name(st->last_err));
            return st->last_err;
        }
    }

    return ESP_OK;
}

static void ota_on_finished(void *user_ctx)
{
    sm_ota_state_t *st = user_ctx;

    if (!st->started || st->last_err != ESP_OK)
    {
        return;
    }

    st->last_err = esp_ota_end(st->handle);

    if (st->last_err == ESP_OK)
    {
        st->last_err = esp_ota_set_boot_partition(st->partition);
    }

    if (st->last_err == ESP_OK)
    {
        ESP_LOGI(TAG, "firmware update successful");
    }
    else
    {
        ESP_LOGE(TAG, "OTA finalize: %s", esp_err_to_name(st->last_err));
    }
}

/* ---- the routes ---------------------------------------------------------------- */

static esp_err_t root_handler(httpd_req_t *req)
{
    /* the LEGACY safemode.html verbatim (main/web/safemode.html,
       EMBED_FILES — Ali prefers the original look; its endpoints match
       these routes exactly) */
    extern const uint8_t safemode_html_start[] asm("_binary_safemode_html_start");
    extern const uint8_t safemode_html_end[] asm("_binary_safemode_html_end");

    httpd_resp_set_type(req, "text/html");
    return httpd_resp_send(req, (const char *)safemode_html_start,
                           safemode_html_end - safemode_html_start);
}

static esp_err_t upload_handler(httpd_req_t *req)
{
    ESP_LOGI(TAG, "firmware upload, %d bytes", req->content_len);

    multipart_upload_handlers_t handlers =
    {
        .on_part_begin = ota_on_part_begin,
        .on_part_data = ota_on_part_data,
        .on_finished = ota_on_finished,
    };
    sm_ota_state_t state = { 0 };
    multipart_upload_config_t cfg = multipart_upload_default_config();

    cfg.rx_buf_size = 4096;

    esp_err_t err = multipart_upload_handle(req, &handlers, &state, &cfg);

    if (err != ESP_OK || !state.started || state.last_err != ESP_OK)
    {
        if (state.started && state.last_err != ESP_OK)
        {
            esp_ota_abort(state.handle);
        }

        httpd_resp_send_err(req, HTTPD_400_BAD_REQUEST,
                            "Firmware upload failed");
        return ESP_FAIL;
    }

    httpd_resp_sendstr(req, "OK");
    vTaskDelay(pdMS_TO_TICKS(3000));
    restart_tracker_restart(RESTART_TRACKER_PLANNED_REASON_OTA_APPLY,
                            RESTART_TRACKER_SOURCE_SAFE_MODE, 0);
    return ESP_OK;
}

/** The crash report NVS holds, as the text a user sends on. Registered
 *  twice: `/crash_report` for the page, `/crash_report.txt` (user_ctx set)
 *  as a download. */
static esp_err_t crash_report_handler(httpd_req_t *req)
{
    /* PSRAM: one httpd worker, one request at a time */
    static restart_tracker_report_t report EXT_RAM_BSS_ATTR;
    static char text[RESTART_TRACKER_REPORT_TEXT_MAX] EXT_RAM_BSS_ATTR;

    httpd_resp_set_type(req, "text/plain; charset=utf-8");
    httpd_resp_set_hdr(req, "Cache-Control", "no-store");

    if (restart_tracker_get_report(&report) != ESP_OK)
    {
        httpd_resp_set_status(req, "404 Not Found");
        return httpd_resp_sendstr(req,
                                  "No crash report is stored on this device.\n");
    }

    int len = restart_tracker_report_text(&report, text, sizeof(text));

    if (len >= (int)sizeof(text))
    {
        len = (int)sizeof(text) - 1;
    }

    if (req->user_ctx != NULL)
    {
        httpd_resp_set_hdr(req, "Content-Disposition",
                           "attachment; filename=\"wican_crash_report.txt\"");
    }

    return httpd_resp_send(req, text, (len > 0) ? len : 0);
}

static esp_err_t factory_reset_handler(httpd_req_t *req)
{
    ESP_LOGI(TAG, "factory reset requested");

    /* raw partition erase — deliberately NOT settings_manager (safe mode
       must recover from a settings layer too corrupt to load) */
    const esp_partition_t *settings = esp_partition_find_first(
        ESP_PARTITION_TYPE_DATA, ESP_PARTITION_SUBTYPE_ANY, "settings");

    if (settings != NULL)
    {
        esp_partition_erase_range(settings, 0, settings->size);
    }

    nvs_flash_erase();

    /* the crash report is no setting: it goes back in, so that a user who
       resets first and asks for help second still has it (the console's
       `factoryreset` never touched NVS either) */
    if (nvs_flash_init() == ESP_OK)
    {
        (void)restart_tracker_restore_report();
    }

    httpd_resp_sendstr(req, "OK");
    vTaskDelay(pdMS_TO_TICKS(2000));
    restart_tracker_restart(RESTART_TRACKER_PLANNED_REASON_FACTORY_RESET,
                            RESTART_TRACKER_SOURCE_SAFE_MODE, 0);
    return ESP_OK;
}

/* ---- safe mode proper -------------------------------------------------------- */

/** The stored crash report on the console, for a user with a cable. */
static void print_crash_report(void)
{
    static restart_tracker_report_t report EXT_RAM_BSS_ATTR;
    static char text[RESTART_TRACKER_REPORT_TEXT_MAX] EXT_RAM_BSS_ATTR;

    if (restart_tracker_get_report(&report) != ESP_OK)
    {
        ESP_LOGI(TAG, "no crash report is stored");
        return;
    }

    restart_tracker_report_text(&report, text, sizeof(text));
    printf("---- stored crash report ----\n%s-----------------------------\n",
           text);
}

static void safemode_run(void)
{
    ESP_LOGI(TAG, "entering SAFE MODE");
    led_manager_boot_color(255, 255, 0); /* legacy: solid yellow */

    /* NVS first (2026-10-05): the tracker reads the stored crash report
       from it, and stores the note of the run that just ended */
    esp_err_t err = nvs_flash_init();

    if (err == ESP_ERR_NVS_NO_FREE_PAGES ||
        err == ESP_ERR_NVS_NEW_VERSION_FOUND)
    {
        nvs_flash_erase();
        nvs_flash_init();
    }

    restart_tracker_init();
    (void)restart_tracker_set_boot_mode(RESTART_TRACKER_BOOT_SAFE);
    print_crash_report();

    esp_netif_init();
    esp_event_loop_create_default();

    esp_netif_t *ap_netif = esp_netif_create_default_wifi_ap();
    wifi_init_config_t init_cfg = WIFI_INIT_CONFIG_DEFAULT();

    if (esp_wifi_init(&init_cfg) != ESP_OK)
    {
        ESP_LOGE(TAG, "wifi init failed — recovery unavailable");
        goto park;
    }

    /* ALWAYS the factory identity, whatever the stored settings say */
    wifi_config_t ap_cfg = { 0 };
    uint8_t mac[6] = { 0 };

    esp_read_mac(mac, ESP_MAC_WIFI_SOFTAP);
    snprintf((char *)ap_cfg.ap.ssid, sizeof(ap_cfg.ap.ssid),
             "WiCAN_%02x%02x%02x%02x%02x%02x", mac[0], mac[1], mac[2],
             mac[3], mac[4], mac[5]);
    strcpy((char *)ap_cfg.ap.password, "@meatpi#");
    ap_cfg.ap.ssid_len = strlen((char *)ap_cfg.ap.ssid);
    ap_cfg.ap.channel = 6;
    ap_cfg.ap.authmode = WIFI_AUTH_WPA2_PSK;
    ap_cfg.ap.max_connection = 4;
    ap_cfg.ap.pmf_cfg.capable = true;

    esp_wifi_set_storage(WIFI_STORAGE_RAM);
    esp_wifi_set_ps(WIFI_PS_NONE);
    esp_wifi_set_mode(WIFI_MODE_AP);
    esp_wifi_set_config(WIFI_IF_AP, &ap_cfg);

    esp_netif_ip_info_t ip = { 0 };

    ip.ip.addr = ESP_IP4TOADDR(192, 168, 0, 10);
    ip.gw.addr = ip.ip.addr;
    ip.netmask.addr = ESP_IP4TOADDR(255, 255, 255, 0);

    if (ap_netif != NULL)
    {
        esp_netif_dhcps_stop(ap_netif);
        esp_netif_set_ip_info(ap_netif, &ip);
        esp_netif_dhcps_start(ap_netif);
    }

    if (esp_wifi_start() != ESP_OK)
    {
        ESP_LOGE(TAG, "wifi start failed — recovery unavailable");
        goto park;
    }

    httpd_handle_t server = NULL;
    httpd_config_t hcfg = HTTPD_DEFAULT_CONFIG();

    hcfg.stack_size = 12 * 1024;
    hcfg.max_uri_handlers = 6; /* 5 routes below */

    if (httpd_start(&server, &hcfg) == ESP_OK)
    {
        const httpd_uri_t ROOT =
            { .uri = "/", .method = HTTP_GET, .handler = root_handler };
        const httpd_uri_t UPLOAD =
            { .uri = "/upload_firmware", .method = HTTP_POST,
              .handler = upload_handler };
        const httpd_uri_t RESET =
            { .uri = "/factory_reset", .method = HTTP_POST,
              .handler = factory_reset_handler };
        const httpd_uri_t REPORT =
            { .uri = "/crash_report", .method = HTTP_GET,
              .handler = crash_report_handler };
        const httpd_uri_t REPORT_FILE =
            { .uri = "/crash_report.txt", .method = HTTP_GET,
              .handler = crash_report_handler, .user_ctx = (void *)1 };

        httpd_register_uri_handler(server, &ROOT);
        httpd_register_uri_handler(server, &UPLOAD);
        httpd_register_uri_handler(server, &RESET);
        httpd_register_uri_handler(server, &REPORT);
        httpd_register_uri_handler(server, &REPORT_FILE);
        ESP_LOGI(TAG, "safe mode up: AP '%s' @ 192.168.0.10",
                 (char *)ap_cfg.ap.ssid);
    }
    else
    {
        ESP_LOGE(TAG, "httpd start failed — recovery unavailable");
    }

park:
    /* idle timeout: reboot after 10 min with NO AP client — a client
       being attached (someone recovering) holds safe mode open */
    {
        int idle_ms = 0;

        while (true)
        {
            vTaskDelay(pdMS_TO_TICKS(1000));

            wifi_sta_list_t sta = { 0 };

            if (esp_wifi_ap_get_sta_list(&sta) == ESP_OK && sta.num > 0)
            {
                idle_ms = 0;
            }
            else
            {
                idle_ms += 1000;
            }

            if (idle_ms >= SM_IDLE_TIMEOUT_MS)
            {
                ESP_LOGW(TAG, "safe mode idle timeout — rebooting");
                restart_tracker_restart(
                    RESTART_TRACKER_PLANNED_REASON_SAFE_MODE,
                    RESTART_TRACKER_SOURCE_SAFE_MODE, 0);
            }
        }
    }
}

/* Called by app_main when main_safemode_check() returned true — split so
 * the check stays cheap and this file owns the whole story. */
void main_safemode_enter(void)
{
    safemode_run(); /* never returns */
}
