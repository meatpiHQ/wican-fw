/** @file main_glue.h
 *  @brief Composition-root glue: the cross-component callbacks main
 *         installs so components stay ignorant of each other (§11).
 *         Each wire function defines AND installs its callback
 *         (main_sleep_wire precedent); app_main calls each one at the
 *         boot point where both sides exist. */
#pragma once

/** SD mount events flip filesystem's /sd backend. */
void main_glue_wire_sd(void);

/** OTA session state drives the CRITICAL LED indication. */
void main_glue_wire_ota_led(void);

/** BLE console lines run through cmdline_manager (async). */
void main_glue_wire_ble_cli(void);

/** Dongle GPS fixes (console or HTTP poll) become autopid parameters
 *  (gps_*); the HTTP cache backs /api/gps without a console. */
void main_glue_wire_gps(void);

/** The USB connector summary provider for GET /api/status (2026-10-13):
 *  after the USB components started. */
void main_glue_wire_usb_status(void);

/** Autopid samples feed data_logger's params stream. */
void main_glue_wire_autopid_logger(void);

/** Button long-press -> CONFIG MODE (BLE off, AP forced up, legacy
 *  yellow/blue LED alternation, 10 min timeout-reboot held open while
 *  an AP client is attached). */
void main_glue_wire_button(void);

/** Firmware updates (2026-10-18): the product's image names for
 *  ota_manager's refusal, the uplink word (and whether it costs mobile
 *  data) for fw_update's cellular policy, and the HA push's update keys.
 *  After ota_manager_init / fw_update_init / ha_webhooks_init. */
void main_glue_wire_fw_update(void);

/** The factory reset the web route performs is the device's full wipe
 *  (main_factory.c), not the settings partition alone (2026-10-26).
 *  After api_http_init. */
void main_glue_wire_factory_reset(void);
