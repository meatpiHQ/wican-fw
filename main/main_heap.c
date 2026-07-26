/**
 * @file main_heap.c
 * @brief Firmware-wide allocator policy, decided once at the
 *        composition root (§12b).
 */
#include <stdlib.h>

#include "cJSON.h"
#include "esp_heap_caps.h"

#include "main_heap.h"

/* ---- cJSON heap -> PSRAM (2026-07-09, the settings-apply reclaim) -----------
 * Internal RAM is the scarce resource (§12b); JSON is the config/telemetry
 * lingua franca and its trees live long (settings registry alone held
 * ~17 KB of internal at boot: nodes >32 B already went to PSRAM under
 * SPIRAM_MALLOC_ALWAYSINTERNAL=32, but every short key/value string
 * stayed internal). cJSON is only ever touched from task context, so ALL
 * of it belongs in PSRAM — the sqlite SQLITE_CONFIG_MALLOC precedent.
 * Fallback to the default policy if PSRAM is exhausted/absent. */
static void *cjson_psram_malloc(size_t n)
{
    void *p = heap_caps_malloc(n, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);

    return (p != NULL) ? p : malloc(n);
}

static void cjson_psram_free(void *p)
{
    free(p); /* heap_caps pointers free through the same allocator */
}

void main_heap_route_cjson_to_psram(void)
{
    static const cJSON_Hooks CJSON_PSRAM =
    {
        .malloc_fn = cjson_psram_malloc,
        .free_fn = cjson_psram_free,
    };

    cJSON_InitHooks((cJSON_Hooks *)&CJSON_PSRAM);
}
