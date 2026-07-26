/**
 * @file main_sleep.c
 * @brief Composition-root glue: the ordered shutdown sequence
 *        sleep_manager runs right before powering down (the
 *        prepare-callback pattern — sleep_manager itself depends on
 *        none of these components). Order mirrors legacy
 *        sleep_mode.c: data producers first, tunnels/advertisements,
 *        then the radios, storage last.
 */
#include "autopid.h"
#include "ble_manager.h"
#include "can_manager.h"
#include "data_logger.h"
#include "ext_manager.h"
#include "led_manager.h"
#include "uds_manager.h"
#include "external_storage.h"
#include "mdns_manager.h"
#include "mqtt_manager.h"
#include "sleep_manager.h"
#include "usb_host_manager.h"
#include "usb_acm_cli.h"
#include "j2534_server.h"
#include "vpn_manager.h"
#include "wifi_manager.h"

#include "main_sleep.h"

static void on_prepare_sleep(void)
{
    (void)autopid_stop();
    (void)uds_manager_stop();
    (void)ext_manager_stop();
    (void)can_manager_stop();
    (void)data_logger_stop();
    (void)mqtt_manager_stop();
    (void)vpn_manager_stop();
    (void)mdns_manager_stop();
    (void)j2534_server_stop();
    (void)usb_acm_cli_stop();
    (void)usb_host_manager_stop();
    (void)wifi_manager_stop();
    (void)ble_manager_stop();
    (void)external_storage_stop();
    (void)led_manager_stop(); /* LED dark through the naps (legacy
                                 led_set_level(0,0,0) parity) */
}

void main_sleep_wire(void)
{
    (void)sleep_manager_set_prepare_cb(on_prepare_sleep);
}
