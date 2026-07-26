/**
 * @file main_boot.c
 * @brief Boot scaffolding for the composition root: the init/start step
 *        helpers (degrade, never halt — §4.3), the boot RAM map every
 *        step feeds, and the boot HEALTH report (2026-07-19): error/
 *        warning counts, flash-op counters, registry occupancy — printed
 *        for the bench AND latched as fault codes when out of budget.
 */
#include "esp_intr_alloc.h"
#include <stdio.h>

#include "esp_heap_caps.h"
#include "esp_log.h"
#include "esp_timer.h"

#include "bridge_manager.h"
#include "cmdline_manager.h"
#include "dev_status_manager.h"
#include "log_manager.h"
#include "settings_manager.h"

#include "main_boot.h"

static const char *TAG = "main";

/* ---- boot RAM map (meatpi 2026-07-05: find the internal-RAM eaters) ---------
 * Every init/start step records its INTERNAL-heap delta; the table prints
 * after the boot line as `WICAN RAMMAP <step>=<bytes>` (positive =
 * consumed). One boot = the whole consumption map. Since 2026-07-19 each
 * step also records its DURATION; steps >= 50 ms print as
 * `WICAN BOOTTIME <step>=<ms>` — the boot-latency map (Ali: "it takes
 * long to boot?"). */
typedef struct
{
    const char *name;
    int32_t     delta;
    uint32_t    ms;
} ram_step_t;

static ram_step_t s_ram_map[96]; /* 64 overflowed once register_http +
                                    start steps grew — a full boot is
                                    ~80 recorded steps now */
static size_t s_ram_map_n;

static uint32_t internal_free_now(void)
{
    return heap_caps_get_free_size(MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT);
}

static void ram_map_record(const char *name, uint32_t before,
                           int64_t t_start_us)
{
    if (s_ram_map_n < sizeof(s_ram_map) / sizeof(s_ram_map[0]))
    {
        s_ram_map[s_ram_map_n].name = name;
        s_ram_map[s_ram_map_n].delta =
            (int32_t)before - (int32_t)internal_free_now();
        s_ram_map[s_ram_map_n].ms =
            (uint32_t)((esp_timer_get_time() - t_start_us) / 1000);
        s_ram_map_n++;
    }
}

void main_boot_ram_map_print(void)
{
    for (size_t i = 0; i < s_ram_map_n; i++)
    {
        if (s_ram_map[i].delta > 256 || s_ram_map[i].delta < -256)
        {
            printf("WICAN RAMMAP %s=%ld\n", s_ram_map[i].name,
                   (long)s_ram_map[i].delta);
        }
    }

    for (size_t i = 0; i < s_ram_map_n; i++)
    {
        if (s_ram_map[i].ms >= 50)
        {
            printf("WICAN BOOTTIME %s=%lu\n", s_ram_map[i].name,
                   (unsigned long)s_ram_map[i].ms);
        }
    }
}

/** Start one component; a failure degrades, never halts boot (§4.3). */
bool main_boot_start(const char *name, esp_err_t (*start_fn)(void))
{
    uint32_t before = internal_free_now();
    int64_t t0 = esp_timer_get_time();
    esp_err_t err = start_fn();

    if (err != ESP_OK)
    {
        ESP_LOGE(TAG, "%s_start failed: %s (continuing degraded)", name,
                 esp_err_to_name(err));
    }

    ram_map_record(name, before, t0);
    return err == ESP_OK;
}

/* ---- boot health report (meatpi 2026-07-19: detect the silent-failure
 * classes — every-boot flash rewrites, registry overflow, ESP_LOGE'd
 * degradations nobody read. Printed lines are bench-parsed; breaches
 * LATCH a fault code (the automotive-DTC model: manual clear only). */

/* Steady-state boot flash budget. A clean boot after the 2026-07-19
 * settings fix measures ~10 erases (littlefs metadata); the pre-fix bug
 * (37 files rewritten every boot) measured 37+. First boot after a
 * defaults change legitimately fills+persists — the budget stays above
 * that but far below the rewrite-everything pathology. */
#define BOOT_ERASE_BUDGET 64

/** Report a registry's occupancy; latch a fault when nearly full. */
static void cap_check(const char *name, size_t used, size_t cap)
{
    printf("WICAN CAPS %s=%u/%u\n", name, (unsigned)used, (unsigned)cap);

    if (cap - used < 2)
    {
        char detail[48];

        snprintf(detail, sizeof(detail), "%s at %u/%u", name,
                 (unsigned)used, (unsigned)cap);
        dev_status_manager_fault_raise("registry_headroom", detail);
    }
}

void main_boot_health_report(void)
{
    uint32_t errors = 0;
    uint32_t warnings = 0;

    log_manager_health(&errors, &warnings);
    printf("WICAN HEALTH errors=%lu warnings=%lu\n",
           (unsigned long)errors, (unsigned long)warnings);

    if (errors > 0)
    {
        char detail[48];

        snprintf(detail, sizeof(detail), "%lu error lines at boot",
                 (unsigned long)errors);
        dev_status_manager_fault_raise("boot_errors", detail);
    }

    dev_status_flash_t fl;

    if (dev_status_manager_flash(&fl) == ESP_OK)
    {
        printf("WICAN FLASH writes=%lu wbytes=%lu erases=%lu ebytes=%lu\n",
               (unsigned long)fl.write_count, (unsigned long)fl.write_bytes,
               (unsigned long)fl.erase_count, (unsigned long)fl.erase_bytes);

        if (fl.erase_count > BOOT_ERASE_BUDGET)
        {
            char detail[48];

            snprintf(detail, sizeof(detail), "%lu erases during boot",
                     (unsigned long)fl.erase_count);
            dev_status_manager_fault_raise("boot_flash_budget", detail);
        }
    }

    size_t used;
    size_t cap;
    int eu, ec, tu, tc;

    settings_manager_capacity(&used, &cap);
    cap_check("settings", used, cap);
    cmdline_manager_capacity(&used, &cap);
    cap_check("cmdline", used, cap);
    log_manager_sinks_capacity(&used, &cap);
    cap_check("log_sinks", used, cap);
    bridge_manager_capacity(&eu, &ec, &tu, &tc);
    cap_check("bridge_ep", (size_t)eu, (size_t)ec);
    cap_check("bridge_tr", (size_t)tu, (size_t)tc);

    printf("WICAN FAULTS active=%d\n",
           dev_status_manager_faults(NULL, 0));
    esp_intr_dump(NULL); /* TEMP 2026-07-21: ISR core map for the affinity experiment */
}

/** Flash-churn tripwire for the 60 s watch loop: latches ONE fault per
 *  boot when flash erases grow in many consecutive windows — the
 *  "component rewrites flash on a timer" class, caught in the field. */
void main_boot_flash_watch(void)
{
    static uint32_t s_last_erases;
    static int      s_rising_windows;
    static bool     s_raised;
    dev_status_flash_t fl;

    if (dev_status_manager_flash(&fl) != ESP_OK)
    {
        return;
    }

    s_rising_windows = (fl.erase_count > s_last_erases)
                           ? s_rising_windows + 1 : 0;
    s_last_erases = fl.erase_count;

    if (s_rising_windows >= 10 && !s_raised)
    {
        s_raised = true; /* data_logger-class streaming is legit — the
                            fault flags the pattern; the human judges */
        char detail[48];

        snprintf(detail, sizeof(detail), "erases rising 10x60s (now %lu)",
                 (unsigned long)fl.erase_count);
        dev_status_manager_fault_raise("flash_churn", detail);
    }
}

/** Init/register one step. NEVER panics: a deterministic init failure
 *  under ESP_ERROR_CHECK would be a permanent boot loop — the one thing
 *  a field device must not do. A failed init leaves that component
 *  unconfigured (its _start() then refuses per §4.3 step 5) and the rest
 *  of the device keeps working: serial console, and usually the AP + OTA
 *  path for recovery. */
bool main_boot_init(const char *name, esp_err_t (*fn)(void))
{
    uint32_t before = internal_free_now();
    int64_t t0 = esp_timer_get_time();
    esp_err_t err = fn();

    if (err != ESP_OK)
    {
        ESP_LOGE(TAG, "%s failed: %s (continuing degraded)", name,
                 esp_err_to_name(err));
    }

    ram_map_record(name, before, t0);
    return err == ESP_OK;
}
