/** @file main_boot.h
 *  @brief Composition-root boot scaffolding: the degrade-never-halt
 *         init/start step helpers and the boot RAM map they feed
 *         (`WICAN RAMMAP <step>=<bytes>` — one boot prints the whole
 *         internal-RAM consumption map; the system bench parses it). */
#pragma once

#include <stdbool.h>

#include "esp_err.h"

/** Init/register one step; a failure degrades, never halts boot. */
bool main_boot_init(const char *name, esp_err_t (*fn)(void));

/** Start one component; a failure degrades, never halts boot (§4.3). */
bool main_boot_start(const char *name, esp_err_t (*start_fn)(void));

/** Print the recorded per-step internal-heap deltas (>256 B only). */
void main_boot_ram_map_print(void);

/** Boot health report (2026-07-19): print `WICAN HEALTH/FLASH/CAPS/
 *  FAULTS` for the bench and LATCH fault codes on breaches (boot
 *  errors, flash budget, registry headroom). Call once, after start. */
void main_boot_health_report(void);

/** Flash-churn tripwire — call from the 60 s watch loop; latches one
 *  `flash_churn` fault per boot when erases rise 10 windows straight. */
void main_boot_flash_watch(void);
