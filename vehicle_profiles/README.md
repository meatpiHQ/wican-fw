# Vehicle Profiles - DO NOT DOWNLOAD THESE DIRECTLY

This folder contains various vehicle profiles to enable people to easily get their car working with WiCAN adapters. To use them please download the main [vehicle profiles](../vehicle_profiles.json) file

The Names of files/folders do not matter to the end result, they are just there to help organize the various files.

If you would like to add/update a car please make sure it is formatted properly, see various car configs for examples

**car_model**: "Make: Model"

**init**: Initialization string. AT command to initialize the OBD adapter, such as setting the protocol, header, and any other car-specific commands relevant to OBD communication. Every command must end with a semicolon ;

**pids**: Parameter ID request

**parameters**: Parameters within a specific PID request. A PID response might contain multiple parameters, such as battery State of Health (SoH) and battery voltage. This object is structured as a set of short name - expression pairs

**short_name**: No whitespace and not necessarily human readable

**expression**: The expression used to calculate the parameter value

Every parameter must have a corresponding entry in [params.json](../.vehicle_profiles/params.json) with the following information:

**short_name**: Identical to the entry in the vehicle profile

**description**: A longer, human-readable descriptive name

**settings**: An object structred as a set with two fields: unit and class

**unit**: The unit of the parameter, e.g., %, °C, or others, see [Home Assistant List](https://www.home-assistant.io/integrations/sensor/#device-class)

**class**: The class of the sensor, see [Home Assistant List](https://www.home-assistant.io/integrations/sensor/#device-class)

**min**: The minimum value of the sensor

**max**: The maximum value of the sensor

**type**: The type of the sensor

**Example:**

```
{
    "car_model": "Hyundai: Ioniq5/Ioniq6/Kona",
    "init": "ATSH7E4;ATST96;",
    "pids": [
        {
          "pid": "2201019",
          "parameters": {
                  "SOC_BMS": "B10/2",
                  "Aux_Batt_Volts": "B38*0.1"
          }
        },
        {
          "pid": "2201057",
          "parameters": {
                  "SOH": "[B34:B35]/10"
          }

        }
    ]
}

```

**Example coresponding entry in params.json:**

```
  "SOC_BMS": {
    "description": "State of charge according to the BMS.",
    "settings": {
      "unit": "%",
      "class": "battery",
      "min": "0",
      "max": "100"
    }
  }
```
