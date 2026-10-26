/** @file main_factory.h
 *  @brief The device's FACTORY RESET (2026-10-26, Ali: "factory reset is
 *         factory reset: it should bring the device back to its factory
 *         state; nothing on the SD card, everything else should go").
 *
 *  Everything on the internal flash that is not the firmware goes: the
 *  settings partition, the data partition (/data: the vehicles and their
 *  tables, the trouble-code databases and DBC files, the scripts, the
 *  dashboard, the certificate sets, the backup copy on the flash, the web
 *  UI cache) and NVS (the WiFi calibration cache, the latched faults, the
 *  firmware update job, the restart tracker's records). Two things go back
 *  into the fresh NVS on purpose: the stored crash report (no setting: a
 *  user who resets first and asks for help second still has it, safe
 *  mode's rule since 2026-10-05) and the backup key (the copy on the SD
 *  card and every backup file the device made stay openable; without it
 *  the undo of a reset from the card would need a key file nobody was
 *  asked to download). The SD card is never touched. The firmware slots,
 *  the OTA record and the PHY calibration partition are not data.
 *
 *  The caller restarts afterwards (restart_tracker, reason FACTORY_RESET):
 *  the next boot finds both LittleFS partitions blank, formats them
 *  quietly (the first-boot paths) and runs the defaults. Until 2026-10-26
 *  the console's `factoryreset` and the web route deleted the settings
 *  files only, and safe mode erased the settings partition and NVS.
 *
 *  The work runs on its own task with an internal-RAM stack (the flash
 *  cache is off while a block erases), whatever the caller's stack; the
 *  caller waits, a few seconds (the 5.9 MB data partition). Every step
 *  runs even after an earlier one failed (a wipe that stops at the first
 *  error leaves a half-reset device); the first error is returned. */
#pragma once

#include "esp_err.h"

/** The composition is up (main_cli's `factoryreset`, the web route
 *  through api_http_set_factory_reset): the settings partition through
 *  its owner, the data partition through filesystem_wipe(), NVS. */
esp_err_t main_factory_reset(void);

/** Safe mode (main_safemode.c): no component started, so the settings
 *  partition is erased raw (a settings layer too corrupt to load must not
 *  stand in the way), the data partition through filesystem_wipe() (which
 *  needs no init), NVS; the key is read from NVS first so it can go back. */
esp_err_t main_factory_reset_bare(void);
