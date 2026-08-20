// host-test stub for freertos/FreeRTOS.h: basic types only
#pragma once
#include <stdint.h>
#include <assert.h>

typedef uint32_t TickType_t;
typedef int BaseType_t;
typedef unsigned int UBaseType_t;
#define pdTRUE 1
#define configASSERT(x) assert(x)
