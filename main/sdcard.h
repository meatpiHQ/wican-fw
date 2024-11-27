#ifndef SD_CARD_H
#define SD_CARD_H

#include "esp_err.h"

#ifdef __cplusplus
extern "C" {
#endif

#define MOUNT_POINT "/sdcard"

/**
 * @brief Initialize and mount the SD card
 * 
 * This function initializes the SDMMC peripheral and mounts the FAT filesystem.
 * If the card is already mounted, it returns ESP_OK without doing anything.
 * 
 * @return esp_err_t ESP_OK on success, ESP_FAIL or other error codes on failure
 */
esp_err_t sd_card_init(void);

/**
 * @brief Deinitialize and unmount the SD card
 * 
 * This function unmounts the filesystem and disables the SDMMC peripheral.
 * If the card is not mounted, it returns ESP_OK without doing anything.
 * 
 * @return esp_err_t ESP_OK on success, error code on failure
 */
esp_err_t sd_card_deinit(void);

/**
 * @brief Check if SD card is available and working
 * 
 * This function checks:
 * - If the card is mounted
 * - If the card is responding to status commands
 * - If the card is in a valid state
 * - If there are any error flags set
 * 
 * @return true if SD card is available and working
 * @return false if SD card is not available or has errors
 */
bool sdcard_is_available(void);

/**
 * @brief Get the mount point of the SD card
 * 
 * @return const char* Mount point path ("/sdcard")
 */
const char* sdcard_get_mount_point(void);

/**
 * @brief Check if card is currently mounted
 * 
 * @return true if card is mounted
 * @return false if card is not mounted
 */
bool sdcard_is_mounted(void);

#ifdef __cplusplus
}
#endif

#endif 
