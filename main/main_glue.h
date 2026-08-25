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

/** Autopid samples feed data_logger's params stream. */
void main_glue_wire_autopid_logger(void);

/** Button long-press -> CONFIG MODE (BLE off, AP forced up, legacy
 *  yellow/blue LED alternation, 10 min timeout-reboot held open while
 *  an AP client is attached). */
void main_glue_wire_button(void);
