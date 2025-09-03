/*
 * This file is part of the WiCAN project.
 *
 * Copyright (C) 2022  Meatpi Electronics.
 * Written by Ali Slim <ali@meatpi.com>
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
 *
 * You should have received a copy of the GNU General Public License
 * along with this program.  If not, see <http://www.gnu.org/licenses/>.
 */

#ifndef SYNC_SYS_TIME_H
#define SYNC_SYS_TIME_H

#ifdef __cplusplus
extern "C" {
#endif

/**
 * @brief Initialize the system time synchronization task
 * 
 * This function creates a FreeRTOS task that handles SNTP time synchronization.
 * The task will:
 * - Wait for network connectivity
 * - Initialize SNTP with multiple servers for redundancy
 * - Perform initial time synchronization with retry mechanism
 * - Sync RTCM (Real-Time Clock Module) when time sync is successful
 * - Maintain periodic re-synchronization every hour
 * - Update RTCM during periodic sync to keep hardware clock accurate
 * 
 * The task is created with static allocation using PSRAM memory.
 */
void sync_sys_time_init(void);

#ifdef __cplusplus
}
#endif

#endif /* SYNC_SYS_TIME_H */