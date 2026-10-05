/**
 * @file main_park.c
 * @brief The crash park (see the header). Composition-root code like safe
 *        mode: it runs INSTEAD of the composition, on the boot task's
 *        internal stack (light sleep runs with the cache off), and calls
 *        only what needs no init: the LED chip's pre-settings path, the OBD
 *        chip's pins, sleep_manager's board-level sleep entry.
 *
 *        Order matters twice. The tracker is told "this boot parks" before
 *        anything here touches hardware, so a crash in here is known for
 *        what it is and the next boot parks BARE: without the LED, the one
 *        part of a park with a driver behind it (I2C). The pins stay in a
 *        bare park: they are plain GPIO writes, and they are the park. With
 *        nothing touched the board draws 134 mA asleep, with the pins set
 *        47 mA (bench, 2026-10-05). And the LED comes first of the
 *        hardware: it is what tells the user, and it must not wait for the
 *        OBD chip's half second.
 */
#include "main_park.h"

#include <stdio.h>

#include "driver/gpio.h"
#include "esp_log.h"
#include "esp_sleep.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

#include "i2c_bus.h"
#include "led_manager.h"
#include "obd_chip.h"
#include "restart_tracker.h"
#include "sleep_manager.h"

#include "main_safemode.h"

static const char *TAG = "park";

#define PARK_NAP_US         (2u * 1000u * 1000u) /* sleep_manager's nap     */
#define PARK_BUTTON_HOLD_MS 1000   /* held this long, awake, is a press     */
#define PARK_RESLEEP_MAX    6      /* OBD chip re-sleeps in a row, then stop */

/** The button, sampled awake only: the pad's pull-up is its awake
 *  configuration, and a level that sags during a nap is not a press. */
static bool button_held(void)
{
    for (int ms = 0; ms < PARK_BUTTON_HOLD_MS; ms += 20)
    {
        if (gpio_get_level(MAIN_SAFEMODE_BUTTON_GPIO) != 0)
        {
            return false;
        }

        vTaskDelay(pdMS_TO_TICKS(20));
    }

    return true;
}

/** One normal start. The tracker keeps the streak over this restart: if
 *  the start crashes before it settles, the next boot is back here. */
static void __attribute__((noreturn)) leave(const char *why,
                                            restart_tracker_source_t source)
{
    ESP_LOGW(TAG, "park ends (%s): one normal start", why);
    vTaskDelay(pdMS_TO_TICKS(100)); /* let the line out */
    restart_tracker_restart(RESTART_TRACKER_PLANNED_REASON_PARK_RETRY, source,
                            0);
}

/** Everything that draws current or talks to the vehicle, down. */
static void board_down(bool with_led)
{
    if (with_led)
    {
        (void)i2c_bus_init();

        esp_err_t err = led_manager_boot_breathe(255, 0, 0);

        if (err != ESP_OK)
        {
            ESP_LOGW(TAG, "LED: %s (parking without it)",
                     esp_err_to_name(err));
        }
    }

    (void)obd_chip_park();
    sleep_manager_board_down();
}

void main_park_check(void)
{
    restart_tracker_brake_t brake;

    if (restart_tracker_get_brake(&brake) != ESP_OK ||
        brake.verdict == RESTART_TRACKER_BOOT_NORMAL)
    {
        return; /* the normal boot */
    }

    bool bare = (brake.verdict == RESTART_TRACKER_BOOT_PARK_BARE);

    (void)restart_tracker_set_boot_mode(
        (restart_tracker_boot_mode_t)brake.verdict);

    ESP_LOGW(TAG, "%u runs in a row crashed before they settled: parked "
             "asleep%s", (unsigned)brake.streak,
             bare ? ", without the LED (the park itself had crashed)" : "");
    ESP_LOGW(TAG, "to start again: cycle the power, or hold the button until "
             "the LED turns sky blue; keep holding until yellow for safe mode "
             "and the crash report");

    if (brake.retry_after_s != 0U)
    {
        ESP_LOGW(TAG, "it tries one start by itself in %lu s",
                 (unsigned long)brake.retry_after_s);
    }

    /* the bench's line (the WICAN HEALTH / FLASH / CAPS family) */
    printf("WICAN PARK streak=%u parks=%u retry_s=%lu bare=%d\n",
           (unsigned)brake.streak, (unsigned)brake.parks,
           (unsigned long)brake.retry_after_s, bare ? 1 : 0);

    board_down(!bare);

    vTaskDelay(pdMS_TO_TICKS(200)); /* the console, before the first nap */

    int64_t retry_at_us = (brake.retry_after_s != 0U)
                              ? esp_timer_get_time() +
                                    (int64_t)brake.retry_after_s * 1000000LL
                              : 0;
    uint8_t resleeps = 0;

    while (true)
    {
        esp_sleep_enable_timer_wakeup(PARK_NAP_US);
        esp_light_sleep_start();
        vTaskDelay(1); /* one tick awake: the idle tasks get their turn */

        if (gpio_get_level(MAIN_SAFEMODE_BUTTON_GPIO) == 0 && button_held())
        {
            leave("the button", RESTART_TRACKER_SOURCE_BUTTON);
        }

        /* esp_timer counts through the naps; the tick does not */
        if (retry_at_us != 0 && esp_timer_get_time() >= retry_at_us)
        {
            leave("its timer", RESTART_TRACKER_SOURCE_PARK);
        }

        /* the chip woke up (the vehicle's bus can do that): put it back,
           as the sleep loop does; give up after a few in a row rather than
           fight it for good */
        if (!obd_chip_status_ok())
        {
            resleeps = 0;
        }
        else if (resleeps < PARK_RESLEEP_MAX)
        {
            resleeps++;
            ESP_LOGW(TAG, "OBD chip awake while parked; back to sleep (%u/%u)",
                     (unsigned)resleeps, (unsigned)PARK_RESLEEP_MAX);
            (void)obd_chip_park();
        }
    }
}
