/** @file main_safemode.h
 *  @brief SAFE MODE (meatpi 2026-07-19, legacy safemode.c semantics):
 *         hold the button (GPIO8) while power is applied -> the device
 *         boots a BARE-MINIMUM recovery environment instead of the
 *         composition: default AP (WiCAN_<mac> / @meatpi# on
 *         192.168.0.10 — ALWAYS the defaults, whatever the stored
 *         settings say) + a 3-route web server (recovery page, firmware
 *         upload, factory reset). Settings are NEVER loaded — a corrupt
 *         config cannot crash safe mode. Reboots after 10 min with no
 *         AP client. LED: sky-blue while deciding, solid yellow in safe
 *         mode (legacy pattern). */
#pragma once

#include <stdbool.h>

/** Call FIRST in app_main, before any component init. Polls the button
 *  (no interrupts): released (the normal case) returns false in
 *  microseconds; held for the full 5 s returns true — the caller then
 *  calls main_safemode_enter() (split so app_main reads as a decision). */
bool main_safemode_check(void);

/** Run safe mode. NEVER returns (recovery actions end in a reboot). */
void main_safemode_enter(void);
