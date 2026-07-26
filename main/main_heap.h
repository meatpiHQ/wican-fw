/** @file main_heap.h
 *  @brief Composition-root allocator policy: firmware-wide heap routing
 *         (§12b — internal RAM is the scarce resource). */
#pragma once

/** Route ALL cJSON allocations to PSRAM (internal fallback). Call
 *  BEFORE any component init — the hooks must be in place before the
 *  first cJSON node exists. */
void main_heap_route_cjson_to_psram(void);
