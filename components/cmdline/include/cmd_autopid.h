/*
 * WiCAN - cmd_autopid.h
 */
#ifndef CMD_AUTOPID_H
#define CMD_AUTOPID_H

#include "esp_err.h"

#ifdef __cplusplus
extern "C" {
#endif

// Register the 'autopid' console command
esp_err_t cmd_autopid_register(void);

#ifdef __cplusplus
}
#endif

#endif /* CMD_AUTOPID_H */
