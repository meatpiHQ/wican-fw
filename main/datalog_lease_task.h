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

#ifndef __DATALOG_LEASE_TASK_H__
#define __DATALOG_LEASE_TASK_H__

/*
 * Dead-man's-switch reaper (no-reboot coexistence, task #36;
 * docs/internal/WICAN_DEADMAN_AUTORESUME.md).
 *
 * A 1 Hz task that brick-safely auto-resumes the WiCAN datalogger when the host (NC-Flash)
 * vanishes mid-coexistence -- laptop lid closed, host crash, Wi-Fi drop -- the wireless analog
 * of "the Tactrix cable was unplugged". It resumes ONLY when the bus is provably unowned
 * (no flash active, no host bus-claim), the host is provably gone (lease expired AND its
 * 35001 socket generation gone) and the bus is provably idle (>= 300 ms no TX/RX). It never
 * touches the codec-owned FLASH_ACTIVE_BIT; a wedged flash raises a sticky alarm instead.
 *
 * Start once at boot on WICAN_PRO after can_init + slcan_port_init + the datalogger init.
 * Idempotent.
 */
void datalog_lease_task_start(void);

#endif /* __DATALOG_LEASE_TASK_H__ */
