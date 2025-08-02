# Auto Connect Module Usage

The Auto Connect module provides intelligent switching between drive mode and home mode based on vehicle ignition state, with configurable WiFi and BLE connectivity options.

## Features

### Drive Mode
- Activated when ignition is ON for 2+ seconds
- **Either BLE OR WiFi connectivity (mutually exclusive)**
- Optional WiFi connection to drive-specific SSID
- Disconnects from home WiFi when active

### Home Mode  
- Activated when ignition is OFF for 5+ seconds
- Always enables BLE for configuration
- Configurable AP+STA mode for home connectivity
- Connects to home WiFi network
- Provides AP for device configuration

## Configuration Structure

```c
typedef struct {
    // Home mode configuration
    bool home_mode_enable_ap_sta;       // Enable AP+STA mode in home mode
    char home_ssid[32];                 // Home WiFi SSID
    char home_password[64];             // Home WiFi password
    
    // Drive mode configuration
    bool drive_mode_enable_ble;         // Enable BLE in drive mode (mutually exclusive with WiFi)
    bool drive_mode_enable_wifi;        // Enable WiFi in drive mode (mutually exclusive with BLE)
    char drive_ssid[32];                // Drive mode WiFi SSID (optional)
    char drive_password[64];            // Drive mode WiFi password (optional)
} smartconnect_config_t;
```

## Usage Example

```c
#include "smartconnect.h"

void app_main() {
    // Initialize auto connect
    smartconnect_init();
    
    // Configure auto connect settings
    smartconnect_config_t config = {
        .home_mode_enable_ap_sta = true,
        .home_ssid = "MyHomeWiFi",
        .home_password = "homepassword123",
        .drive_mode_enable_ble = true,    // BLE only in drive mode
        .drive_mode_enable_wifi = false,  // WiFi disabled (either BLE OR WiFi)
        .drive_ssid = "",
        .drive_password = ""
    };
    
    smartconnect_set_config(&config);
    smartconnect_save_config();
}

// Alternative: WiFi enabled in drive mode (BLE will be disabled)
void configure_drive_wifi() {
    smartconnect_config_t config;
    smartconnect_get_config(&config);
    
    config.drive_mode_enable_ble = false;   // Disable BLE
    config.drive_mode_enable_wifi = true;   // Enable WiFi (either BLE OR WiFi)
    strcpy(config.drive_ssid, "HotspotSSID");
    strcpy(config.drive_password, "hotspotpass");
    
    smartconnect_set_config(&config);
    smartconnect_save_config();
}
```

## State Machine

1. **INIT**: Initial state, determines first mode based on ignition
2. **WAITING_IGNITION_ON**: Ignition detected ON, waiting for stability
3. **WAITING_IGNITION_OFF**: Ignition detected OFF, waiting for stability  
4. **DRIVE_MODE**: Active drive mode with configured connectivity
5. **HOME_MODE**: Active home mode with AP+STA configuration

## API Functions

- `smartconnect_init()`: Initialize and start the auto connect task
- `smartconnect_set_config()`: Update configuration
- `smartconnect_get_config()`: Retrieve current configuration
- `smartconnect_get_state()`: Get current state for debugging
- `smartconnect_load_config()`: Load configuration from storage
- `smartconnect_save_config()`: Save configuration to storage

## Integration Notes

- The module integrates with `wifi_mgr.h` for WiFi management
- BLE control uses existing `ble.h` interface
- Vehicle ignition state from `vehicle.h`
- Configuration persistence can be integrated with `config_server.h`
