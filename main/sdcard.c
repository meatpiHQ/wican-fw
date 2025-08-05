#include <string.h>
#include <sys/unistd.h>
#include <sys/stat.h>
#include "esp_vfs_fat.h"
#include "sdmmc_cmd.h"       
#include "driver/sdmmc_host.h"
#include "sdcard.h"
#include "hw_config.h"
#include "esp_ota_ops.h"
#include "esp_system.h"
#include "esp_heap_caps.h"  
#include "hw_config.h"
#include "esp_littlefs.h"
#include "dev_status.h"
#include "filesystem.h"

#define OTA_BUFFER_SIZE 4096  

static const char *TAG = "SDCARD";
#ifdef USE_SD_FATFS
static sdmmc_card_t *s_card = NULL;
#else
static sdmmc_card_t sdcard;
#endif
static bool s_card_mounted = false;

esp_err_t sdcard_perform_ota_update(const char* firmware_path)
{
    if (!sdcard_is_mounted() || !sdcard_is_available()) 
    {
        ESP_LOGE(TAG, "SD card not available for OTA update");
        return ESP_ERR_INVALID_STATE;
    }

    // Construct full path
    char full_path[128];
    snprintf(full_path, sizeof(full_path), "%s%s", SD_CARD_MOUNT_POINT, firmware_path);
    
    // Open firmware file
    FILE *firmware_file = fopen(full_path, "rb");
    if (firmware_file == NULL) 
    {
        ESP_LOGE(TAG, "Failed to open firmware file: %s", full_path);
        return ESP_ERR_NOT_FOUND;
    }
    
    ESP_LOGI(TAG, "Deleting config file before OTA update");

    // Delete config file if it exists
    ESP_LOGI(TAG, "Deleting config file before OTA update");

    // Delete config file if it exists
    filesystem_init();
    
    // Delete all configuration files
    filesystem_delete_config_files();

    ESP_LOGI(TAG, "Starting OTA from SD card file: %s", full_path);
    
    // Get file size
    struct stat file_stat;
    if (stat(full_path, &file_stat) != 0) 
    {
        ESP_LOGE(TAG, "Failed to get firmware file size");
        fclose(firmware_file);
        return ESP_FAIL;
    }
    
    const esp_partition_t *update_partition = esp_ota_get_next_update_partition(NULL);
    if (update_partition == NULL) 
    {
        ESP_LOGE(TAG, "Failed to get OTA update partition");
        fclose(firmware_file);
        return ESP_FAIL;
    }
    
    ESP_LOGI(TAG, "Writing to partition: %s (size: %lu)", update_partition->label, update_partition->size);
    
    esp_ota_handle_t ota_handle;
    esp_err_t err = esp_ota_begin(update_partition, file_stat.st_size, &ota_handle);
    if (err != ESP_OK) 
    {
        ESP_LOGE(TAG, "Failed to begin OTA update: %s", esp_err_to_name(err));
        fclose(firmware_file);
        return err;
    }
    
    // Allocate buffer in internal RAM
    uint8_t *buffer = malloc(OTA_BUFFER_SIZE);
    if (buffer == NULL) 
    {
        ESP_LOGE(TAG, "Failed to allocate buffer in internal RAM");
        fclose(firmware_file);
        esp_ota_abort(ota_handle);
        return ESP_ERR_NO_MEM;
    }
    
    ESP_LOGI(TAG, "OTA buffer allocated in internal RAM");
    
    // Read and write firmware in chunks
    size_t bytes_read = 0;
    size_t total_bytes_read = 0;
    int last_percentage = -1;  // Track last percentage to avoid too many log messages
    
    while ((bytes_read = fread(buffer, 1, OTA_BUFFER_SIZE, firmware_file)) > 0) 
    {
        err = esp_ota_write(ota_handle, buffer, bytes_read);
        if (err != ESP_OK) 
        {
            ESP_LOGE(TAG, "Failed to write OTA data: %s", esp_err_to_name(err));
            fclose(firmware_file);
            free(buffer);
            esp_ota_abort(ota_handle);
            return err;
        }
        
        total_bytes_read += bytes_read;
        
        // Calculate and print percentage progress
        int current_percentage = (total_bytes_read * 100) / file_stat.st_size;
        
        // Only print when percentage changes to avoid flooding the logs
        if (current_percentage != last_percentage) 
        {
            ESP_LOGI(TAG, "OTA progress: %d%% (%d/%d bytes)", 
                    current_percentage, 
                    total_bytes_read, 
                    (int)file_stat.st_size);
            last_percentage = current_percentage;
        }
    }
    
    // Free the allocated buffer
    free(buffer);
    fclose(firmware_file);
    
    // Finalize OTA update
    err = esp_ota_end(ota_handle);
    if (err != ESP_OK) 
    {
        ESP_LOGE(TAG, "Failed to finalize OTA update: %s", esp_err_to_name(err));
        return err;
    }
    
    // Set new boot partition
    err = esp_ota_set_boot_partition(update_partition);
    if (err != ESP_OK) 
    {
        ESP_LOGE(TAG, "Failed to set boot partition: %s", esp_err_to_name(err));
        return err;
    }
    
    ESP_LOGI(TAG, "OTA update successful, rebooting system...");
    
    // Optional: Add a delay before reboot to ensure logs are printed
    vTaskDelay(pdMS_TO_TICKS(1000));
    
    // Reboot system to apply update
    esp_restart();
    
    return ESP_OK;  // This will never be reached due to restart
}

esp_err_t sd_card_init(void) 
{
    if (s_card_mounted)
    {
        ESP_LOGI(TAG, "SD card already mounted");
        return ESP_OK;
    }

    esp_err_t ret;


    ESP_LOGI(TAG, "Initializing SD card");
    
    // Initialize SDMMC peripheral
    sdmmc_host_t host = SDMMC_HOST_DEFAULT();
    host.max_freq_khz = SDMMC_FREQ_HIGHSPEED;
    // This initializes the slot without card detect (CD) and write protect (WP) signals
    sdmmc_slot_config_t slot_config = SDMMC_SLOT_CONFIG_DEFAULT();

    slot_config.width = 4; // 4-bit bus mode
    slot_config.clk = SDCARD_CLK;
    slot_config.cmd = SDCARD_CMD;
    slot_config.d0 = SDCARD_D0;
    slot_config.d1 = SDCARD_D1;
    slot_config.d2 = SDCARD_D2;
    slot_config.d3 = SDCARD_D3;

    // Mount the filesystem
    #ifdef USE_SD_FATFS
    // Mount configuration
    esp_vfs_fat_sdmmc_mount_config_t mount_config = 
    {
        .format_if_mount_failed = true,
        .max_files = 5,
        .allocation_unit_size = 65536
    };

    // Mount the filesystem
    ret = esp_vfs_fat_sdmmc_mount(SD_CARD_MOUNT_POINT, &host, &slot_config, &mount_config, &s_card);

    if (ret != ESP_OK) 
    {
        if (ret == ESP_FAIL) 
        {
            ESP_LOGE(TAG, "Failed to mount filesystem");
        } 
        else 
        {
            ESP_LOGE(TAG, "Failed to initialize the card (%s)", esp_err_to_name(ret));
        }
        return ret;
    }
        // Print card info
        sdmmc_card_print_info(stdout, s_card);
        ESP_LOGI(TAG, "FAT filesystem mounted successfully");
    #else
        ESP_LOGI(TAG, "Initializing LittleFS filesystem");
        
        // Mount SD card
        ret = sdmmc_host_init();
        if (ret != ESP_OK) {
            ESP_LOGE(TAG, "Failed to initialize host: %s", esp_err_to_name(ret));
            return ret;
        }
        
        ret = sdmmc_host_init_slot(SDMMC_HOST_SLOT_1, &slot_config);
        if (ret != ESP_OK) {
            ESP_LOGE(TAG, "Failed to initialize slot: %s", esp_err_to_name(ret));
            return ret;
        }
        
        // Card detection
        ret = sdmmc_card_init(&host, &sdcard);
        if (ret != ESP_OK) {
            ESP_LOGE(TAG, "Failed to initialize SD card: %s", esp_err_to_name(ret));
            return ret;
        }
        // Print card info
        ESP_LOGI(TAG, "SD card detected: Size: %lluMB", ((uint64_t)sdcard.csd.capacity * sdcard.csd.sector_size) / (1024 * 1024));
        
        esp_vfs_littlefs_conf_t conf = {
            .base_path = MOUNT_POINT,
            .partition_label = NULL,  // Not using internal flash partition
            .partition = NULL,        // Not using internal flash partition
            .sdcard = &sdcard,           // Using SD card
            .format_if_mount_failed = true,
            .dont_mount = false,
            .read_only = false,
            .grow_on_mount = true,
        };

        // Use settings defined above to initialize and mount LittleFS filesystem.
        ret = esp_vfs_littlefs_register(&conf);

        if (ret != ESP_OK) {
            if (ret == ESP_FAIL) {
                ESP_LOGE(TAG, "Failed to mount or format filesystem");
            } else if (ret == ESP_ERR_NOT_FOUND) {
                ESP_LOGE(TAG, "Failed to find LittleFS on SD card");
            } else {
                ESP_LOGE(TAG, "Failed to initialize LittleFS on SD card (%s)", esp_err_to_name(ret));
            }
            return ret;
        }

        size_t total = 0, used = 0;
        ret = esp_littlefs_sdmmc_info(&sdcard, &total, &used);
        if (ret != ESP_OK) {
            ESP_LOGE(TAG, "Failed to get LittleFS SD card information (%s)", esp_err_to_name(ret));
            esp_littlefs_format_sdmmc(&sdcard);
        } else {
            ESP_LOGI(TAG, "LittleFS SD card info: total: %u bytes, used: %u bytes", total, used);
            ESP_LOGI(TAG, "SD card LittleFS partition size: total: %f MB, used: %f MB", (float)(total/(1024*1024)), (float)(used/(1024*1024)));
        }
    #endif

    s_card_mounted = true;
    dev_status_set_bits(DEV_SDCARD_MOUNTED_BIT);
    ESP_LOGI(TAG, "SD card mounted successfully");
    
    return ESP_OK;
}

esp_err_t sd_card_deinit(void) 
{
    if (!s_card_mounted) 
    {
        ESP_LOGI(TAG, "SD card not mounted");
        return ESP_OK;
    }

    // Unmount partition and disable SDMMC
    #ifdef USE_SD_FATFS
    esp_err_t ret = esp_vfs_fat_sdcard_unmount(SD_CARD_MOUNT_POINT, s_card);
    if (ret != ESP_OK) 
    {
        ESP_LOGE(TAG, "Failed to unmount SD card");
        return ret;
    }
    s_card = NULL;
    #else
    esp_err_t ret = esp_vfs_littlefs_unregister(MOUNT_POINT);
    if (ret != ESP_OK) 
    {
        ESP_LOGE(TAG, "Failed to unmount LittleFS filesystem (%s)", esp_err_to_name(ret));
        return ret;
    }
    #endif
    s_card_mounted = false;
    
    ESP_LOGI(TAG, "SD card unmounted successfully");
    return ESP_OK;
}

bool sdcard_is_available(void) 
{
    #ifdef USE_SD_FATFS
    if (!s_card_mounted || s_card == NULL) 
    {
        return false;
    }
    #else
    if (!s_card_mounted) 
    {
        return false;
    }
    #endif

    // Just check if we can get the card status
    #ifdef USE_SD_FATFS
    esp_err_t err = sdmmc_get_status(s_card);
    #else
    esp_err_t err = sdmmc_get_status(&sdcard);
    #endif
    if (err != ESP_OK) 
    {
        ESP_LOGW(TAG, "Failed to get card status");
        return false;
    }

    return true;
}

const char* sdcard_get_mount_point(void) 
{
    return SD_CARD_MOUNT_POINT;
}

bool sdcard_is_mounted(void)
{
    return s_card_mounted;
}

esp_err_t sdcard_get_info(sdmmc_card_info_t *info) 
{
    #ifdef USE_SD_FATFS
    if (!s_card_mounted || !sdcard_is_available() || s_card == NULL) 
    {
        return ESP_ERR_INVALID_STATE;
    }

    info->capacity = (((uint64_t) s_card->csd.capacity) * s_card->csd.sector_size) / (1024 * 1024);
    info->sector_size = s_card->csd.sector_size;
    info->speed = s_card->max_freq_khz;
    info->bus_width = s_card->host.slot;
    
    // Add card name
    strncpy(info->name, s_card->cid.name, sizeof(info->name) - 1);
    info->name[sizeof(info->name) - 1] = '\0';
    
    // Add card type
    if (s_card->is_sdio) {
        info->type = CARD_TYPE_SDIO;
    } else if (s_card->is_mmc) {
        info->type = CARD_TYPE_MMC;
    } else {
        info->type = (s_card->ocr & (1 << 30)) ? CARD_TYPE_SDHC : CARD_TYPE_SDSC;
    }
    #else
    if (!s_card_mounted || !sdcard_is_available()) 
    {
        return ESP_ERR_INVALID_STATE;
    }

    info->capacity = (((uint64_t) sdcard.csd.capacity) * sdcard.csd.sector_size) / (1024 * 1024);
    info->sector_size = sdcard.csd.sector_size;
    info->speed = sdcard.max_freq_khz;
    info->bus_width = sdcard.host.slot;
    
    // Add card name
    strncpy(info->name, sdcard.cid.name, sizeof(info->name) - 1);
    info->name[sizeof(info->name) - 1] = '\0';
    
    // Add card type
    if (sdcard.is_sdio) {
        info->type = CARD_TYPE_SDIO;
    } else if (sdcard.is_mmc) {
        info->type = CARD_TYPE_MMC;
    } else {
        info->type = (sdcard.ocr & (1 << 30)) ? CARD_TYPE_SDHC : CARD_TYPE_SDSC;
    }
    #endif

    return ESP_OK;
}


esp_err_t sdcard_test_rw(void)
{
    if (!sdcard_is_available())
    {
        ESP_LOGE(TAG, "SD card not available for testing");
        return ESP_ERR_INVALID_STATE;
    }

    const char *test_path = SD_CARD_MOUNT_POINT "/test.txt";
    const char *test_content = "WiCAN SD Card Test";
    char read_buffer[32] = {0};
    
    // Test write
    FILE *f = fopen(test_path, "w");
    if (f == NULL)
    {
        ESP_LOGE(TAG, "Failed to open file for writing");
        return ESP_FAIL;
    }
    
    fprintf(f, "%s", test_content);
    fclose(f);
    
    // Test read
    f = fopen(test_path, "r");
    if (f == NULL)
    {
        ESP_LOGE(TAG, "Failed to open file for reading");
        return ESP_FAIL;
    }
    
    size_t bytes_read = fread(read_buffer, 1, strlen(test_content), f);
    fclose(f);
    
    // Verify content
    if (bytes_read != strlen(test_content) || 
        strcmp(read_buffer, test_content) != 0)
    {
        ESP_LOGE(TAG, "Read/write verification failed");
        return ESP_FAIL;
    }
    
    // Clean up test file
    unlink(test_path);
    
    ESP_LOGI(TAG, "SD card read/write test passed");
    return ESP_OK;
}
