/*
 * bench_ap: the WiCAN bench's "home WiFi" as an instrument, a WiFi-to-USB
 * adapter. ESP32-P4-Function-EV board: a WPA2 soft-AP on the board's
 * ESP32-C6 (esp_hosted / esp_wifi_remote) and a USB CDC-NCM network device
 * on the P4's high-speed OTG port, joined by a raw layer-2 forwarder (every
 * frame from the USB side goes out of the AP, every frame the AP receives
 * goes to the USB side; no IP stack in between). The Pi on the USB side
 * stays the network's brain: it shares 10.42.1.0/24 on the NCM interface
 * (DHCP, mDNS, the broker), so every bench script keeps working.
 *
 * Why: the Pi's mt76 USB adapter stalls as an access point (association
 * status stall, reason 204) and lost the sleep matrix twice on 2026-10-01.
 * An ESP soft-AP is deterministic, and the console below lets the bench do
 * what a hotspot never will: drop a station, take the AP away and bring it
 * back, change channel, and log every association with a timestamp.
 *
 * Console (UART0, 115200, the CP2102N on the board's USB-UART port):
 *   ap                      status: ssid, channel, state, stations (mac, rssi), counters, heap
 *   ap on | ap off          start / stop the soft-AP (the USB side stays up)
 *   ap kick <mac>           deauthenticate one station
 *   ap channel <1..13>      change channel (restarts the AP)
 *   ap set <ssid> <psk>     store the credentials in NVS and apply them
 *   usb                     USB link state and frame counters
 *   restart                 reboot the board
 * Events are logged as "BENCH_AP: +sta <mac> aid=<n>" / "-sta <mac> aid=<n>
 * reason=<r>"; the bench parses those lines.
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "esp_console.h"
#include "esp_event.h"
#include "esp_heap_caps.h"
#include "esp_log.h"
#include "esp_mac.h"
#include "esp_netif.h"
#include "esp_private/wifi.h"
#include "esp_system.h"
#include "esp_timer.h"
#include "esp_wifi.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "nvs.h"
#include "nvs_flash.h"
#include "tinyusb.h"
#include "tinyusb_default_config.h"
#include "tinyusb_net.h"

static const char *TAG = "BENCH_AP";

#define AP_SSID_DEFAULT    "WICAN_BENCH_P4"
#define AP_PSK_DEFAULT     "wican-bench-p4"
#define AP_CHANNEL_DEFAULT 6
#define AP_MAX_STA         8
#define NVS_NS             "bench_ap"

typedef struct
{
    char    ssid[33];
    char    psk[65];
    uint8_t channel;
} ap_creds_t;

static ap_creds_t s_creds;
static bool s_ap_on;
static bool s_usb_up;
static uint32_t s_usb_to_ap, s_ap_to_usb, s_drop_ap_tx, s_drop_usb_tx;

/* ---- stored credentials ---------------------------------------------------- */

static void creds_load(void)
{
    nvs_handle_t h;
    size_t len;

    strlcpy(s_creds.ssid, AP_SSID_DEFAULT, sizeof(s_creds.ssid));
    strlcpy(s_creds.psk, AP_PSK_DEFAULT, sizeof(s_creds.psk));
    s_creds.channel = AP_CHANNEL_DEFAULT;

    if (nvs_open(NVS_NS, NVS_READONLY, &h) != ESP_OK)
    {
        return;
    }

    len = sizeof(s_creds.ssid);
    (void)nvs_get_str(h, "ssid", s_creds.ssid, &len);
    len = sizeof(s_creds.psk);
    (void)nvs_get_str(h, "psk", s_creds.psk, &len);
    (void)nvs_get_u8(h, "channel", &s_creds.channel);
    nvs_close(h);

    if (s_creds.channel < 1 || s_creds.channel > 13)
    {
        s_creds.channel = AP_CHANNEL_DEFAULT;
    }
}

static esp_err_t creds_save(void)
{
    nvs_handle_t h;
    esp_err_t err = nvs_open(NVS_NS, NVS_READWRITE, &h);

    if (err != ESP_OK)
    {
        return err;
    }

    (void)nvs_set_str(h, "ssid", s_creds.ssid);
    (void)nvs_set_str(h, "psk", s_creds.psk);
    (void)nvs_set_u8(h, "channel", s_creds.channel);
    err = nvs_commit(h);
    nvs_close(h);
    return err;
}

/* ---- the raw forwarder ------------------------------------------------------ */

/* a frame arrived from the Pi over USB: out of the AP (the driver delivers it
 * to the station that owns the destination MAC, or to all for broadcast) */
static esp_err_t usb_rx(void *buffer, uint16_t len, void *ctx)
{
    (void)ctx;

    if (s_ap_on)
    {
        if (esp_wifi_internal_tx(WIFI_IF_AP, buffer, len) == ESP_OK)
        {
            s_usb_to_ap++;
        }
        else
        {
            s_drop_ap_tx++;
        }
    }

    return ESP_OK;
}

/* the WiFi buffer TinyUSB holds right now; NULL once it gave it back */
static void *volatile s_tx_owned;

/* TinyUSB copied a frame we handed it: give the WiFi buffer back. The FIRST
 * argument is the buff_free_arg of tinyusb_net_send_sync() (the WiFi buffer),
 * the second the config's user_context (unused here, NULL). The first image
 * freed the second one: free(NULL), so every forwarded frame stayed on the
 * heap and the board asserted in esp_hosted's sdio_process_rx_task
 * (copy_payload = malloc failed) after ~330 KB from the stations
 * (2026-10-02: six resets in a day, 3 s under a download). */
static void usb_tx_done(void *eb, void *ctx)
{
    (void)ctx;
    s_tx_owned = NULL;
    esp_wifi_internal_free_rx_buffer(eb);
}

/* a frame arrived at the AP from a station: over USB to the Pi. Called from
 * one task (esp_hosted's rx task); usb_tx_done() runs in the TinyUSB task
 * before tinyusb_net_send_sync() returns, or never for this frame. Who owns
 * the buffer afterwards is read from s_tx_owned, not from the return code:
 * a send that timed out may still have gone out. */
static esp_err_t ap_rx(void *buffer, uint16_t len, void *eb)
{
    if (!s_usb_up)
    {
        s_drop_usb_tx++;
        esp_wifi_internal_free_rx_buffer(eb);
        return ESP_OK;
    }

    s_tx_owned = eb;
    (void)tinyusb_net_send_sync(buffer, len, eb, pdMS_TO_TICKS(50));

    if (s_tx_owned == eb)
    {
        /* TinyUSB never took it (not mounted, no room, timeout): ours to free */
        s_tx_owned = NULL;
        s_drop_usb_tx++;
        esp_wifi_internal_free_rx_buffer(eb);
        return ESP_OK;
    }

    s_ap_to_usb++;
    return ESP_OK;
}

/* ---- WiFi AP ----------------------------------------------------------------- */

static esp_err_t ap_apply_config(void)
{
    wifi_config_t cfg = { 0 };

    strlcpy((char *)cfg.ap.ssid, s_creds.ssid, sizeof(cfg.ap.ssid));
    cfg.ap.ssid_len = strlen(s_creds.ssid);
    strlcpy((char *)cfg.ap.password, s_creds.psk, sizeof(cfg.ap.password));
    cfg.ap.max_connection = AP_MAX_STA;
    cfg.ap.authmode = WIFI_AUTH_WPA2_PSK;
    cfg.ap.channel = s_creds.channel;
    cfg.ap.pmf_cfg.capable = true;
    cfg.ap.pmf_cfg.required = false;
    return esp_wifi_set_config(WIFI_IF_AP, &cfg);
}

static esp_err_t ap_start(void)
{
    esp_err_t err = ap_apply_config();

    if (err == ESP_OK)
    {
        err = esp_wifi_start();
    }

    if (err == ESP_OK)
    {
        s_ap_on = true;
        ESP_LOGI(TAG, "ap on: ssid=%s channel=%u", s_creds.ssid, s_creds.channel);
    }
    else
    {
        ESP_LOGW(TAG, "ap start failed: %s", esp_err_to_name(err));
    }

    return err;
}

static esp_err_t ap_stop(void)
{
    esp_err_t err = esp_wifi_stop();

    s_ap_on = false;
    ESP_LOGI(TAG, "ap off (%s)", esp_err_to_name(err));
    return err;
}

static void wifi_event_handler(void *arg, esp_event_base_t base, int32_t id, void *data)
{
    (void)arg;
    (void)base;

    if (id == WIFI_EVENT_AP_STACONNECTED)
    {
        const wifi_event_ap_staconnected_t *e = data;

        ESP_LOGI(TAG, "+sta " MACSTR " aid=%d", MAC2STR(e->mac), e->aid);
    }
    else if (id == WIFI_EVENT_AP_STADISCONNECTED)
    {
        const wifi_event_ap_stadisconnected_t *e = data;

        ESP_LOGI(TAG, "-sta " MACSTR " aid=%d reason=%d", MAC2STR(e->mac), e->aid, e->reason);
    }
    else if (id == WIFI_EVENT_AP_START)
    {
        /* the raw receive path: every frame from a station lands in ap_rx() */
        (void)esp_wifi_internal_reg_rxcb(WIFI_IF_AP, ap_rx);
        ESP_LOGI(TAG, "ap started");
    }
    else if (id == WIFI_EVENT_AP_STOP)
    {
        (void)esp_wifi_internal_reg_rxcb(WIFI_IF_AP, NULL);
        ESP_LOGI(TAG, "ap stopped");
    }
}

/* ---- USB NCM ------------------------------------------------------------------ */

static esp_err_t usb_init(void)
{
    const tinyusb_config_t tusb_cfg = TINYUSB_DEFAULT_CONFIG();
    esp_err_t err = tinyusb_driver_install(&tusb_cfg);

    if (err != ESP_OK)
    {
        ESP_LOGW(TAG, "tinyusb install failed: %s", esp_err_to_name(err));
        return err;
    }

    tinyusb_net_config_t net = { .on_recv_callback = usb_rx, .free_tx_buffer = usb_tx_done };

    /* the USB device's MAC: locally administered, derived from the P4's base
       MAC (the P4 has no WiFi MAC in efuse: ESP_MAC_WIFI_SOFTAP fails here) */
    if (esp_read_mac(net.mac_addr, ESP_MAC_BASE) != ESP_OK)
    {
        memcpy(net.mac_addr, (uint8_t[]){ 0x02, 0x50, 0x34, 0xBE, 0x4C, 0x01 }, 6);
    }

    net.mac_addr[0] = (uint8_t)((net.mac_addr[0] | 0x02) & 0xFE);
    net.mac_addr[5] ^= 0x55;
    err = tinyusb_net_init(&net);

    if (err != ESP_OK)
    {
        ESP_LOGW(TAG, "tinyusb net init failed: %s", esp_err_to_name(err));
        return err;
    }

    s_usb_up = true;
    ESP_LOGI(TAG, "usb ncm up, mac " MACSTR, MAC2STR(net.mac_addr));
    return ESP_OK;
}

/* ---- console ---------------------------------------------------------------- */

static int parse_mac(const char *s, uint8_t out[6])
{
    unsigned v[6];

    if (sscanf(s, "%x:%x:%x:%x:%x:%x", &v[0], &v[1], &v[2], &v[3], &v[4], &v[5]) != 6)
    {
        return -1;
    }

    for (int i = 0; i < 6; i++)
    {
        out[i] = (uint8_t)v[i];
    }

    return 0;
}

static void print_status(void)
{
    wifi_sta_list_t list = { 0 };

    printf("ap: %s ssid=%s channel=%u max=%d\n", s_ap_on ? "on" : "off", s_creds.ssid, s_creds.channel, AP_MAX_STA);

    if (s_ap_on && esp_wifi_ap_get_sta_list(&list) == ESP_OK)
    {
        printf("stations: %d\n", list.num);

        for (int i = 0; i < list.num; i++)
        {
            printf("  " MACSTR " rssi=%d\n", MAC2STR(list.sta[i].mac), list.sta[i].rssi);
        }
    }

    printf("frames: usb->ap %lu  ap->usb %lu  dropped ap-tx %lu usb-tx %lu\n",
           (unsigned long)s_usb_to_ap, (unsigned long)s_ap_to_usb,
           (unsigned long)s_drop_ap_tx, (unsigned long)s_drop_usb_tx);
    /* a forwarder that leaks dies within minutes of bench traffic: the heap
       belongs in the status so the bench sees it before the assert */
    printf("heap: free %lu  min %lu  largest %u\n",
           (unsigned long)esp_get_free_heap_size(), (unsigned long)esp_get_minimum_free_heap_size(),
           (unsigned)heap_caps_get_largest_free_block(MALLOC_CAP_DEFAULT));
    printf("uptime: %llu s\n", (unsigned long long)(esp_timer_get_time() / 1000000));
}

static int cmd_ap(int argc, char **argv)
{
    if (argc < 2)
    {
        print_status();
        return 0;
    }

    if (strcmp(argv[1], "on") == 0)
    {
        return s_ap_on ? 0 : (ap_start() == ESP_OK ? 0 : 1);
    }

    if (strcmp(argv[1], "off") == 0)
    {
        return s_ap_on ? (ap_stop() == ESP_OK ? 0 : 1) : 0;
    }

    if (strcmp(argv[1], "kick") == 0 && argc >= 3)
    {
        uint8_t mac[6];
        uint16_t aid = 0;

        if (parse_mac(argv[2], mac) != 0)
        {
            printf("bad mac\n");
            return 1;
        }

        if (esp_wifi_ap_get_sta_aid(mac, &aid) != ESP_OK || aid == 0)
        {
            printf("not associated\n");
            return 1;
        }

        esp_err_t err = esp_wifi_deauth_sta(aid);

        printf("kick " MACSTR " aid=%u: %s\n", MAC2STR(mac), aid, esp_err_to_name(err));
        return err == ESP_OK ? 0 : 1;
    }

    if (strcmp(argv[1], "channel") == 0 && argc >= 3)
    {
        int ch = atoi(argv[2]);

        if (ch < 1 || ch > 13)
        {
            printf("channel 1..13\n");
            return 1;
        }

        s_creds.channel = (uint8_t)ch;
        (void)creds_save();

        if (s_ap_on)
        {
            (void)ap_stop();
            vTaskDelay(pdMS_TO_TICKS(300));
            return ap_start() == ESP_OK ? 0 : 1;
        }

        return 0;
    }

    if (strcmp(argv[1], "set") == 0 && argc >= 4)
    {
        if (strlen(argv[2]) == 0 || strlen(argv[2]) > 32 || strlen(argv[3]) < 8 || strlen(argv[3]) > 63)
        {
            printf("ssid 1..32 chars, psk 8..63 chars\n");
            return 1;
        }

        strlcpy(s_creds.ssid, argv[2], sizeof(s_creds.ssid));
        strlcpy(s_creds.psk, argv[3], sizeof(s_creds.psk));
        printf("stored: %s\n", esp_err_to_name(creds_save()));

        if (s_ap_on)
        {
            (void)ap_stop();
            vTaskDelay(pdMS_TO_TICKS(300));
            return ap_start() == ESP_OK ? 0 : 1;
        }

        return 0;
    }

    printf("usage: ap | ap on | ap off | ap kick <mac> | ap channel <1..13> | ap set <ssid> <psk>\n");
    return 1;
}

static int cmd_usb(int argc, char **argv)
{
    (void)argc;
    (void)argv;
    printf("usb ncm: %s; frames usb->ap %lu ap->usb %lu dropped %lu/%lu\n", s_usb_up ? "up" : "down",
           (unsigned long)s_usb_to_ap, (unsigned long)s_ap_to_usb,
           (unsigned long)s_drop_ap_tx, (unsigned long)s_drop_usb_tx);
    return 0;
}

static int cmd_restart(int argc, char **argv)
{
    (void)argc;
    (void)argv;
    printf("restarting\n");
    vTaskDelay(pdMS_TO_TICKS(100));
    esp_restart();
    return 0;
}

static void register_commands(void)
{
    const esp_console_cmd_t cmds[] =
    {
        { .command = "ap", .help = "ap | on | off | kick <mac> | channel <n> | set <ssid> <psk>", .func = cmd_ap },
        { .command = "usb", .help = "USB NCM link and frame counters", .func = cmd_usb },
        { .command = "restart", .help = "reboot the board", .func = cmd_restart },
    };

    for (size_t i = 0; i < sizeof(cmds) / sizeof(cmds[0]); i++)
    {
        (void)esp_console_cmd_register(&cmds[i]);
    }
}

/* ---- app -------------------------------------------------------------------- */

/* The pre-3.0 P4 memory layout hands the 8 KB TCM ("SPM", 0x30100000) to the
 * general heap at second priority, and the flash-cache guard refuses a task
 * whose stack lives there (esp_task_stack_is_sane_cache_disabled: the first
 * nvs_flash_init() asserted on the 6 KB main stack placed in it). The main
 * and console stacks are sized past the region (sdkconfig / repl config);
 * this takes whatever is left out of circulation so no later task stack can
 * land there either. The allocation is meant to be leaked. */
static void reserve_tcm(void)
{
    size_t left = heap_caps_get_largest_free_block(MALLOC_CAP_SPM);
    size_t total = 0;

    while (left >= 64)
    {
        if (heap_caps_malloc(left, MALLOC_CAP_SPM) == NULL)
        {
            break;
        }

        total += left;
        left = heap_caps_get_largest_free_block(MALLOC_CAP_SPM);
    }

    ESP_LOGI(TAG, "tcm reserved: %u bytes kept away from task stacks", (unsigned)total);
}

void app_main(void)
{
    reserve_tcm();

    esp_err_t err = nvs_flash_init();

    if (err == ESP_ERR_NVS_NO_FREE_PAGES || err == ESP_ERR_NVS_NEW_VERSION_FOUND)
    {
        (void)nvs_flash_erase();
        (void)nvs_flash_init();
    }

    creds_load();
    ESP_ERROR_CHECK(esp_event_loop_create_default());
    ESP_ERROR_CHECK(esp_netif_init()); /* esp_wifi wants it; no netif is created: raw forwarding only */

    /* the AP on the C6: no netif, frames go through the raw callbacks */
    wifi_init_config_t wcfg = WIFI_INIT_CONFIG_DEFAULT();

    ESP_ERROR_CHECK(esp_wifi_init(&wcfg));
    ESP_ERROR_CHECK(esp_wifi_set_storage(WIFI_STORAGE_RAM));
    ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_AP));
    ESP_ERROR_CHECK(esp_event_handler_register(WIFI_EVENT, ESP_EVENT_ANY_ID, &wifi_event_handler, NULL));

    (void)usb_init();
    (void)ap_start();

    esp_console_repl_t *repl = NULL;
    esp_console_repl_config_t repl_cfg = ESP_CONSOLE_REPL_CONFIG_DEFAULT();
    esp_console_dev_uart_config_t uart_cfg = ESP_CONSOLE_DEV_UART_CONFIG_DEFAULT();

    repl_cfg.prompt = "bench_ap>";
    repl_cfg.task_stack_size = 8192; /* `ap set` writes NVS: a stack the TCM cannot hold */
    ESP_ERROR_CHECK(esp_console_new_repl_uart(&uart_cfg, &repl_cfg, &repl));
    register_commands();
    ESP_ERROR_CHECK(esp_console_start_repl(repl));
    ESP_LOGI(TAG, "ready: usb %s, ap %s on channel %u", s_usb_up ? "up" : "down", s_creds.ssid, s_creds.channel);
}
