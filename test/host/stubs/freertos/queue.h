// host-test stub for freertos/queue.h: tests that exercise queue-using code
// provide the implementations (see test_precondition.c for a 1-deep fake)
#pragma once
#include "freertos/FreeRTOS.h"

typedef void *QueueHandle_t;

QueueHandle_t xQueueCreate(UBaseType_t len, UBaseType_t item_size);
BaseType_t xQueueOverwrite(QueueHandle_t q, const void *item);
BaseType_t xQueuePeek(QueueHandle_t q, void *item, TickType_t wait);
BaseType_t xQueueReceive(QueueHandle_t q, void *item, TickType_t wait);
