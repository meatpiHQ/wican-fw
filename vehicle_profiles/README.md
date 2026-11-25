# Vehicle Profiles - DO NOT DOWNLOAD THESE DIRECTLY

This folder contains various vehicle profiles to enable people to easily get their car working with WiCAN adapters. To use them please download the main [vehicle profiles](../vehicle_profiles.json) file.

The names of files/folders do not matter to the end result; they are just there to help organize the various files.

If you would like to add/update a car please make sure it is formatted properly according to [`.vehicle_profiles/schema.json`](../.vehicle_profiles/schema.json).

## Profile Fields

- **`car_model`** *(required)*: `"Make: Model"` (e.g., `"Ford: Focus RS (MK3)"`). The UI picker splits the make and model at the colon.
- **`init`** *(required)*: Initialization string. AT commands sent once to initialize the OBD adapter (setting the protocol, headers, flow control, etc.). Every command must end with a semicolon (`;`).
- **`pids`** *(required)*: Array of Parameter ID (PID) requests to poll.
  - **`pid`** *(required)*: The diagnostic PID request string (e.g., `"224028"`).
  - **`pid_init`** *(optional)*: Per-PID AT initialization commands sent before polling this PID. Every command must end with a semicolon (`;`).
  - **`bus`** *(optional)*: Auxiliary CAN bus for this PID (`"ms"`, `"sw"`, or `"ch"`). Can also be set at the top level of the profile.
  - **`bus_kbps`** *(optional)*: Auxiliary CAN bus bit rate in kbps. Can also be set at the top level of the profile.
  - **`parameters`** *(required)*: Array of parameter objects decoded from the PID response.
- **`filters`** *(optional)*: Array of passive broadcast CAN frame filters for frames the vehicle broadcasts continuously without being polled.
  - **`name`** *(optional)*: Optional display name/label for the filter frame.
  - **`frame_id`** *(required)*: Target CAN ID (e.g., `"0x2F0"` or numeric).
  - **`monitor_ms`** *(optional)*: Duration in milliseconds to listen for the frame during each cycle (default `1000`).
  - **`period_ms`** *(optional)*: Custom polling interval in milliseconds.
  - **`parameters`** *(required)*: Array of parameter objects decoded from the broadcast frame data bytes (`B0` = first data byte).
- **`extends`** *(optional)*: Relative path to another profile in `vehicle_profiles/` that this profile inherits from and overrides.
- **`note`** *(optional)*: Short note displayed alongside the model name in the generated documentation.
- **`comment`** *(optional)*: Internal notes/comments for the profile (stripped from the built output).
- **`add_to_docs`** *(optional)*: Set to `false` to exclude the profile from the generated supported vehicles list (typically used for generic profiles).

## Parameter Object Fields

Each entry in a `parameters` array contains:
- **`name`** *(required)*: Parameter name used in MQTT/Home Assistant (no spaces).
- **`expression`** *(required)*: Mathematical expression used to calculate the parameter value from reply/frame bytes (e.g., `"B4-40"`, `"([B4:B5]/10)/2.036"`).
- **`unit`** *(required)*: Unit of measurement (e.g., `"%"`, `"°C"`, `"V"`, `"km/h"`, or `""`).
- **`class`** *(required)*: Home Assistant sensor device class (e.g., `"battery"`, `"temperature"`, `"voltage"`, `"pressure"`, `"speed"`, `"distance"`, or `"none"`). See [Home Assistant Sensor Device Classes](https://www.home-assistant.io/integrations/sensor/#device-class).
- **`min` / `max`** *(optional)*: Minimum/maximum valid value bounds.
- **`period`** *(optional)*: Custom polling period in milliseconds.
- **`type`** *(optional)*: Home Assistant entity type (e.g., `"binary_sensor"`).
- **`mux_expr` / `mux_val`** *(optional, filters only)*: Multiplexer selector expression and value for multiplexed broadcast frames.

## Example

```json
{
  "car_model": "Ford: Focus RS (MK3)",
  "init": "ATE0;ATL0;ATH0;ATR1;ATST32;ATSP6;ATCAF1;",
  "pids": [
    {
      "pid": "224028",
      "pid_init": "ATSH000726;STCAFCP726,72E;",
      "parameters": [
        {
          "name": "SOC",
          "expression": "B4",
          "unit": "%",
          "class": "battery"
        }
      ]
    },
    {
      "pid": "222813",
      "pid_init": "ATSH000726;STCAFCP726,72E;",
      "parameters": [
        {
          "name": "TYRE_P_FL",
          "expression": "([B4:B5]/10)/2.036",
          "unit": "psi",
          "class": "pressure"
        }
      ]
    }
  ],
  "filters": [
    {
      "frame_id": "0x2F0",
      "monitor_ms": 500,
      "parameters": [
        {
          "name": "COOLANT_TMP",
          "expression": "(((B4 & 0x03) * 256) + B5) - 60",
          "unit": "°C",
          "class": "temperature"
        }
      ]
    }
  ]
}
```

