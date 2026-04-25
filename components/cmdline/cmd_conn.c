#include "cmd_conn.h"

#include "cmdline.h"

#include "argtable3/argtable3.h"
#include "esp_console.h"
#include "esp_netif.h"

#include "connection_manager.h"
#include "dev_status.h"

static struct
{
    struct arg_lit *info;
    struct arg_end *end;
} s_conn_args;

static void cmd_conn_print_netif(const char *label, esp_netif_t *netif, esp_netif_t *default_netif)
{
    esp_netif_ip_info_t ip_info;

    if (netif == NULL)
    {
        cmdline_printf("%s: not found\n", label);
        return;
    }

    cmdline_printf("%s: ifkey=%s%s\n",
                   label,
                   esp_netif_get_ifkey(netif),
                   (netif == default_netif) ? " (default)" : "");

    if (esp_netif_get_ip_info(netif, &ip_info) == ESP_OK)
    {
        cmdline_printf("  IP: " IPSTR "\n", IP2STR(&ip_info.ip));
        cmdline_printf("  MASK: " IPSTR "\n", IP2STR(&ip_info.netmask));
        cmdline_printf("  GW: " IPSTR "\n", IP2STR(&ip_info.gw));
        if (ip_info.ip.addr == 0)
        {
            cmdline_printf("  Note: IP is 0.0.0.0 (link up but DHCP/static IP not ready)\n");
        }
    }
    else
    {
        cmdline_printf("  IP: unavailable\n");
    }
}

static int cmd_conn(int argc, char **argv)
{
    connection_manager_status_t status;
    esp_netif_t *default_netif;
    esp_netif_t *wifi_netif;
    esp_netif_t *usb_netif;
    EventBits_t bits;
    int nerrors;

    nerrors = arg_parse(argc, argv, (void **)&s_conn_args);
    if (nerrors != 0)
    {
        arg_print_errors(stderr, s_conn_args.end, argv[0]);
        return 1;
    }

    if (s_conn_args.info->count == 0)
    {
        cmdline_printf("Usage: conn -i\n");
        return 1;
    }

    if (connection_manager_request_reconcile() != ESP_OK)
    {
        cmdline_printf("Warning: reconcile request failed\n");
    }

    if (connection_manager_get_status(&status) != ESP_OK)
    {
        cmdline_printf("Error: connection_manager status unavailable\n");
        return 1;
    }

    default_netif = esp_netif_get_default_netif();
    wifi_netif = esp_netif_get_handle_from_ifkey(CONNECTION_MANAGER_DEFAULT_WIFI_STA_IFKEY);
    usb_netif = esp_netif_get_handle_from_ifkey(CONNECTION_MANAGER_DEFAULT_USB_ETH_IFKEY);
    bits = dev_status_get_bits();

    cmdline_printf("Connection Manager:\n");
    cmdline_printf("  Initialized: %s\n", status.initialized ? "yes" : "no");
    cmdline_printf("  Active uplink: %s\n", connection_manager_uplink_to_str(status.active_uplink));
    cmdline_printf("  USB uplink enabled: %s\n", status.usb_uplink_enabled ? "yes" : "no");
    cmdline_printf("  Uplink priority: %s\n", connection_manager_uplink_policy_to_str(status.uplink_policy));
    cmdline_printf("  USB ifkey: %s\n", status.usb_eth_ifkey[0] ? status.usb_eth_ifkey : "(unset)");
    cmdline_printf("  USB fallback enabled: %s\n", status.usb_fallback_enabled ? "yes" : "no");
    cmdline_printf("  WiFi connected bit: %s\n", status.wifi_connected ? "set" : "clear");
    cmdline_printf("  USB connected bit: %s\n", status.usb_connected ? "set" : "clear");
    cmdline_printf("  dev_status bits: 0x%08lx\n", (unsigned long)bits);

    cmd_conn_print_netif("  WiFi STA netif", wifi_netif, default_netif);
    cmd_conn_print_netif("  USB ETH netif", usb_netif, default_netif);

    if (default_netif == NULL)
    {
        cmdline_printf("  Default netif: none\n");
    }

    cmdline_printf("OK\n");
    return 0;
}

esp_err_t cmd_conn_register(void)
{
    s_conn_args.info = arg_lit0("i", "info", "Show connection-manager uplink arbitration state");
    s_conn_args.end = arg_end(2);

    const esp_console_cmd_t cmd = {
        .command = "conn",
        .help = "Connection manager uplink status",
        .hint = "Usage: conn -i",
        .func = &cmd_conn,
        .argtable = &s_conn_args,
    };

    return cmdline_cmd_register(&cmd);
}