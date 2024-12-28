#include <string.h>
#include <sys/unistd.h>
#include <sys/stat.h>
#include "esp_vfs_fat.h"
#include "sdmmc_cmd.h"       
#include "driver/sdmmc_host.h"
#include "sdcard.h"
#include "hw_config.h"

#define MOUNT_POINT "/sdcard"

static const char *TAG = "sd_card";
static sdmmc_card_t *s_card = NULL;
static bool s_card_mounted = false;

esp_err_t sd_card_init(void) 
{
    if (s_card_mounted)
    {
        ESP_LOGI(TAG, "SD card already mounted");
        return ESP_OK;
    }

    esp_err_t ret;
    // Mount configuration
    esp_vfs_fat_sdmmc_mount_config_t mount_config = 
    {
        .format_if_mount_failed = false,
        .max_files = 5,
        .allocation_unit_size = 16 * 1024
    };

    ESP_LOGI(TAG, "Initializing SD card");
    
    // Initialize SDMMC peripheral
    sdmmc_host_t host = SDMMC_HOST_DEFAULT();
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
    ret = esp_vfs_fat_sdmmc_mount(MOUNT_POINT, &host, &slot_config, &mount_config, &s_card);

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

    s_card_mounted = true;
    ESP_LOGI(TAG, "SD card mounted successfully");
    
    // Print card info
    sdmmc_card_print_info(stdout, s_card);
    
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
    esp_err_t ret = esp_vfs_fat_sdcard_unmount(MOUNT_POINT, s_card);
    if (ret != ESP_OK) 
    {
        ESP_LOGE(TAG, "Failed to unmount SD card");
        return ret;
    }

    s_card_mounted = false;
    s_card = NULL;
    ESP_LOGI(TAG, "SD card unmounted successfully");
    return ESP_OK;
}

bool sdcard_is_available(void) 
{
    if (!s_card_mounted || s_card == NULL) 
    {
        return false;
    }

    // Just check if we can get the card status
    esp_err_t err = sdmmc_get_status(s_card);
    if (err != ESP_OK) 
    {
        ESP_LOGW(TAG, "Failed to get card status");
        return false;
    }

    return true;
}

const char* sdcard_get_mount_point(void) 
{
    return MOUNT_POINT;
}

bool sdcard_is_mounted(void)
{
    return s_card_mounted;
}

esp_err_t sdcard_get_info(sdmmc_card_info_t *info) 
{
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

    return ESP_OK;
}


esp_err_t sdcard_test_rw(void)
{
    if (!sdcard_is_available())
    {
        ESP_LOGE(TAG, "SD card not available for testing");
        return ESP_ERR_INVALID_STATE;
    }

    const char *test_path = MOUNT_POINT "/test.txt";
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
