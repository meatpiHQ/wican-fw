/** @file main_park.h
 *  @brief THE CRASH PARK (Ali, 2026-10-05): when the restart tracker's
 *         crash-loop brake says three runs in a row crashed before they
 *         settled, the boot does not start the firmware a fourth time. It
 *         parks: LED breathing red, CAN transceiver in standby, OBD chip
 *         asleep, USB rail off, the ESP32 napping in light sleep, as in
 *         sleep mode, so a bad firmware or a bad setting cannot drain the
 *         vehicle's battery or wear the flash. No WiFi, no settings, no
 *         filesystem: nothing that could crash again. Measured on the bench
 *         unit at 13.5 V: 47 mA parked, 44 mA in sleep mode, 162 mA awake.
 *
 *         It ends by a power cycle (the count is RAM), by the button (hold
 *         it about 3 s: one normal start; keep holding and that start goes
 *         to safe mode, where the crash report is), or, when the build has
 *         a park retry, by its timer. A start that crashes again before it
 *         settles parks again at once. */
#pragma once

/** Call right after restart_tracker_init(). Returns at once on a normal
 *  boot; NEVER returns when the brake's verdict is to park. */
void main_park_check(void);
