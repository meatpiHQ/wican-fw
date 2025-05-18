#ifndef SD_CARD_H
#define SD_CARD_H

#include "esp_err.h"

#ifdef __cplusplus
extern "C" {
#endif

#define SD_CARD_MOUNT_POINT "/sdcard"

typedef enum {
    CARD_TYPE_SDSC = 0,
    CARD_TYPE_SDHC,
    CARD_TYPE_MMC,
    CARD_TYPE_SDIO
} card_type_t;

typedef struct {
    uint64_t capacity;     // Card capacity in bytes
    uint16_t sector_size;  // Sector size in bytes
    uint8_t type;         // Card type (SD/SDHC/SDXC)
    uint32_t speed;       // Maximum frequency in kHz
    uint8_t bus_width;    // Bus width (1 or 4 bit mode)
    char name[64];         // Card name from CID
} sdmmc_card_info_t;

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

/**
 * @brief Get SD card information
 *
 * @param[out] info Pointer to store card information including capacity, sector size, type, speed and bus width
 * @return ESP_OK if successful
 * @return ESP_ERR_INVALID_STATE if card is not mounted
 */
esp_err_t sdcard_get_info(sdmmc_card_info_t *info);

/**
 * @brief Test SD card read/write functionality by creating and verifying a test file
 * 
 * @return ESP_OK if test file was successfully written and verified
 * @return ESP_ERR_INVALID_STATE if card is not mounted or available
 * @return ESP_FAIL if file operations or data verification fails
 */
esp_err_t sdcard_test_rw(void);

/**
 * Perform OTA update from a firmware file on the SD card
 * @param firmware_path Path to firmware file relative to SD card mount point
 * @return ESP_OK on success, or an error code on failure
 */
esp_err_t sdcard_perform_ota_update(const char* firmware_path);

#ifdef __cplusplus
}
#endif

#endif 

