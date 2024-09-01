# Vehicle Profiles - DO NOT DOWNLOAD THESE DIRECTLY

This folder contains various vehicle profiles to enable people to easily get their car working with WiCAN adapters. To use them please download the main [vehicle profiles](../vehicle_profiles.json) file

The Names of files/folders do not matter to the end result, they are just there to help organize the various files.

If you would like to add/update a car please make sure it is formatted properly, see various car configs for examples

**car_model**: "Make: Model"

**init**: Initialization string. AT command to initialize the OBD adapter, such as setting the protocol, header, and any other car-specific commands relevant to OBD communication. Every command must end with a semicolon ;

**pids**: Parameter ID request


**parameters**: Parameters within a specific PID request. A PID response might contain multiple parameters, such as battery State of Health (SoH) and battery voltage.

**expression**: The expression used to calculate the parameter value

**unit**: The unit of the parameter, e.g., %, °C, or others, see [Home Assistant List](https://www.home-assistant.io/integrations/sensor/#device-class)

**class**: The class of the sensor, see [Home Assistant List](https://www.home-assistant.io/integrations/sensor/#device-class)

**Example:**
```
{
    "car_model": "Hyundai: Ioniq5/Ioniq6/Kona",
    "init": "ATSH7E4;ATST96;",
    "pids": [
        {
            "pid": "2201019",
            "parameters": [
                {
                    "name": "SOC_BMS",
                    "expression": "B10/2",
                    "unit": "%"
                },
                {
                    "name": "Aux_Batt_Volts",
                    "expression": "B38*0.1",
                    "unit": "V"
                }
            ]
        },
        {
            "pid": "2201057",
            "parameters": [
                {
                    "name": "SOH",
                    "expression": "[B34:B35]/10",
                    "unit": "%"
                }
            ]
        }
    ]
}

```
