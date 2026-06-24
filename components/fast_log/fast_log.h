/*
 * This file is part of the WiCAN project.
 *
 * Copyright (C) 2022  Meatpi Electronics.
 *
 * This program is free software: you can redistribute it and/or modify
 * it under the terms of the GNU General Public License as published by
 * the Free Software Foundation, either version 3 of the License, or
 * (at your option) any later version.
 *
 * This program is distributed in the hope that it will be useful,
 * but WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
 * GNU General Public License for more details.
 */

#ifndef __FAST_LOG_H__
#define __FAST_LOG_H__

#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/**
 * @brief Native-TWAI fast datalogger (Task #18) -- Phase A: passive broadcast capture.
 *
 * Brings up the native TWAI controller in LISTEN_ONLY mode and decodes the broadcast CAN
 * frames configured in auto_pid.json (the can_filters[] array) at full bus rate, feeding
 * the existing wide-CSV logger. It BYPASSES the ELM327 emulation + AutoPID poll loop
 * entirely, so high-rate channels (RPM/VSS/ECT/TPS...) log at ~50Hz instead of ~0.6Hz.
 *
 * Phase A never transmits (LISTEN_ONLY), so it cannot ACK or perturb the vehicle bus and
 * cannot drive the controller bus-off. Native polling of mode-01/mode-22 PIDs is Phase B.
 *
 * Call ONCE at boot from the protocol==FAST_LOG branch in app_main, AFTER safe_mode_check()
 * and esp_ota_mark_app_valid_cancel_rollback(), so a fault here can never block the
 * button -> AP -> /upload_firmware recovery path. An independent one-shot RTC crash-guard
 * makes a bring-up fault self-recover in a single reboot (never boot-loops).
 *
 * @param id          Device UID (reserved; unused in Phase A -- kept for parity with autopid_init).
 * @param log_period  Reserved (unused in Phase A).
 */
void fast_log_init(char *id, uint32_t log_period);

#ifdef __cplusplus
}
#endif

#endif /* __FAST_LOG_H__ */
