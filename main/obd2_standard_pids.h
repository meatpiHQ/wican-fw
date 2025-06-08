#ifndef OBD2_PIDS_H
#define OBD2_PIDS_H

#include <stdint.h>

// Structure for a single std_parameter_t within a std_pid_t
typedef struct {
    const char* name;
    const char* unit;
    float scale;
    float offset;
    float min;
    float max;
    uint8_t bit_start;
    uint8_t bit_length;
    const char* class;  // std_parameter_t classification
} std_parameter_t;

// Structure for a single std_pid_t
typedef struct {
    const char* base_name;
    const std_parameter_t* params;
    uint8_t num_params;
} std_pid_t;

// std_parameter_t definitions for each std_pid_t
static const std_parameter_t pid_0_params[] = {
    {
        .name = "PIDsSupported_01_20",
        .unit = "Encoded",
        .scale = 1.0f,
        .offset = 0.0f,
        .min = 0.0f,
        .max = 0.0f,
        .bit_start = 31,
        .bit_length = 32,
        .class = "none"
    },
};

static const std_parameter_t pid_1_params[] = {
    {
        .name = "MonitorStatus",
        .unit = "Encoded",
        .scale = 1.0f,
        .offset = 0.0f,
        .min = 0.0f,
        .max = 0.0f,
        .bit_start = 31,
        .bit_length = 32,
        .class = "none"
    },
};

static const std_parameter_t pid_2_params[] = {
    {
        .name = "FreezeDTC",
        .unit = "Encoded",
        .scale = 1.0f,
        .offset = 0.0f,
        .min = 0.0f,
        .max = 0.0f,
        .bit_start = 31,
        .bit_length = 16,
        .class = "none"
    },
};

static const std_parameter_t pid_3_params[] = {
    {
        .name = "FuelSystemStatus",
        .unit = "Encoded",
        .scale = 1.0f,
        .offset = 0.0f,
        .min = 0.0f,
        .max = 0.0f,
        .bit_start = 31,
        .bit_length = 16,
        .class = "none"
    },
};

static const std_parameter_t pid_4_params[] = {
    {
        .name = "CalcEngineLoad",
        .unit = "%",
        .scale = 0.3921568627f,
        .offset = 0.0f,
        .min = 0.0f,
        .max = 100.0f,
        .bit_start = 31,
        .bit_length = 8,
        .class = "power_factor"
    },
};

static const std_parameter_t pid_5_params[] = {
    {
        .name = "EngineCoolantTemp",
        .unit = "degC",
        .scale = 1.0f,
        .offset = -40.0f,
        .min = -40.0f,
        .max = 215.0f,
        .bit_start = 31,
        .bit_length = 8,
        .class = "temperature"
    },
};

static const std_parameter_t pid_6_params[] = {
    {
        .name = "ShortFuelTrimBank1",
        .unit = "%",
        .scale = 0.78125f,
        .offset = -100.0f,
        .min = -100.0f,
        .max = 99.0f,
        .bit_start = 31,
        .bit_length = 8,
        .class = "volume_storage"
    },
};

static const std_parameter_t pid_7_params[] = {
    {
        .name = "LongFuelTrimBank1",
        .unit = "%",
        .scale = 0.78125f,
        .offset = -100.0f,
        .min = -100.0f,
        .max = 99.0f,
        .bit_start = 31,
        .bit_length = 8,
        .class = "volume_storage"
    },
};

static const std_parameter_t pid_8_params[] = {
    {
        .name = "ShortFuelTrimBank2",
        .unit = "%",
        .scale = 0.78125f,
        .offset = -100.0f,
        .min = -100.0f,
        .max = 99.0f,
        .bit_start = 31,
        .bit_length = 8,
        .class = "volume_storage"
    },
};

static const std_parameter_t pid_9_params[] = {
    {
        .name = "LongFuelTrimBank2",
        .unit = "%",
        .scale = 0.78125f,
        .offset = -100.0f,
        .min = -100.0f,
        .max = 99.0f,
        .bit_start = 31,
        .bit_length = 8,
        .class = "volume_storage"
    },
};

static const std_parameter_t pid_10_params[] = {
    {
        .name = "FuelPressure",
        .unit = "kPa",
        .scale = 3.0f,
        .offset = 0.0f,
        .min = 0.0f,
        .max = 765.0f,
        .bit_start = 31,
        .bit_length = 8,
        .class = "pressure"
    },
};

static const std_parameter_t pid_11_params[] = {
    {
        .name = "IntakeManiAbsPress",
        .unit = "kPa",
        .scale = 1.0f,
        .offset = 0.0f,
        .min = 0.0f,
        .max = 255.0f,
        .bit_start = 31,
        .bit_length = 8,
        .class = "pressure"
    },
};

static const std_parameter_t pid_12_params[] = {
    {
        .name = "EngineRPM",
        .unit = "rpm",
        .scale = 0.25f,
        .offset = 0.0f,
        .min = 0.0f,
        .max = 16384.0f,
        .bit_start = 31,
        .bit_length = 16,
        .class = "speed"
    },
};

static const std_parameter_t pid_13_params[] = {
    {
        .name = "VehicleSpeed",
        .unit = "km/h",
        .scale = 1.0f,
        .offset = 0.0f,
        .min = 0.0f,
        .max = 255.0f,
        .bit_start = 31,
        .bit_length = 8,
        .class = "speed"
    },
};

static const std_parameter_t pid_14_params[] = {
    {
        .name = "TimingAdvance",
        .unit = "deg",
        .scale = 0.5f,
        .offset = -64.0f,
        .min = -64.0f,
        .max = 64.0f,
        .bit_start = 31,
        .bit_length = 8,
        .class = "none"
    },
};

static const std_parameter_t pid_15_params[] = {
    {
        .name = "IntakeAirTemperature",
        .unit = "degC",
        .scale = 1.0f,
        .offset = -40.0f,
        .min = -40.0f,
        .max = 215.0f,
        .bit_start = 31,
        .bit_length = 8,
        .class = "temperature"
    },
};

static const std_parameter_t pid_16_params[] = {
    {
        .name = "MAFAirFlowRate",
        .unit = "grams/sec",
        .scale = 0.01f,
        .offset = 0.0f,
        .min = 0.0f,
        .max = 655.0f,
        .bit_start = 31,
        .bit_length = 16,
        .class = "volume_flow_rate"
    },
};

static const std_parameter_t pid_17_params[] = {
    {
        .name = "ThrottlePosition",
        .unit = "%",
        .scale = 0.3921568627f,
        .offset = 0.0f,
        .min = 0.0f,
        .max = 100.0f,
        .bit_start = 31,
        .bit_length = 8,
        .class = "none"
    },
};

static const std_parameter_t pid_18_params[] = {
    {
        .name = "CmdSecAirStatus",
        .unit = "Encoded",
        .scale = 1.0f,
        .offset = 0.0f,
        .min = 0.0f,
        .max = 0.0f,
        .bit_start = 31,
        .bit_length = 8,
        .class = "none"
    },
};

static const std_parameter_t pid_19_params[] = {
    {
        .name = "OxySensorsPresent_2Banks",
        .unit = "none",
        .scale = 1.0f,
        .offset = 0.0f,
        .min = 0.0f,
        .max = 0.0f,
        .bit_start = 0,
        .bit_length = 0,
        .class = "pressure"
    },
};

static const std_parameter_t pid_20_params[] = {
    {
        .name = "OxySensor1_Volt",
        .unit = "volts",
        .scale = 0.005f,
        .offset = 0.0f,
        .min = 0.0f,
        .max = 1.0f,
        .bit_start = 31,
        .bit_length = 8,
        .class = "voltage"
    },
    {
        .name = "OxySensor1_STFT",
        .unit = "%",
        .scale = 0.78125f,
        .offset = -100.0f,
        .min = -100.0f,
        .max = 99.0f,
        .bit_start = 39,
        .bit_length = 8,
        .class = "none"
    },
};

static const std_parameter_t pid_21_params[] = {
    {
        .name = "OxySensor2_Volt",
        .unit = "volts",
        .scale = 0.005f,
        .offset = 0.0f,
        .min = 0.0f,
        .max = 1.0f,
        .bit_start = 31,
        .bit_length = 8,
        .class = "voltage"
    },
    {
        .name = "OxySensor2_STFT",
        .unit = "%",
        .scale = 0.78125f,
        .offset = -100.0f,
        .min = -100.0f,
        .max = 99.0f,
        .bit_start = 39,
        .bit_length = 8,
        .class = "none"
    },
};

static const std_parameter_t pid_22_params[] = {
    {
        .name = "OxySensor3_Volt",
        .unit = "volts",
        .scale = 0.005f,
        .offset = 0.0f,
        .min = 0.0f,
        .max = 1.0f,
        .bit_start = 31,
        .bit_length = 8,
        .class = "voltage"
    },
    {
        .name = "OxySensor3_STFT",
        .unit = "%",
        .scale = 0.78125f,
        .offset = -100.0f,
        .min = -100.0f,
        .max = 99.0f,
        .bit_start = 39,
        .bit_length = 8,
        .class = "none"
    },
};

static const std_parameter_t pid_23_params[] = {
    {
        .name = "OxySensor4_Volt",
        .unit = "volts",
        .scale = 0.005f,
        .offset = 0.0f,
        .min = 0.0f,
        .max = 1.0f,
        .bit_start = 31,
        .bit_length = 8,
        .class = "voltage"
    },
    {
        .name = "OxySensor4_STFT",
        .unit = "%",
        .scale = 0.78125f,
        .offset = -100.0f,
        .min = -100.0f,
        .max = 99.0f,
        .bit_start = 39,
        .bit_length = 8,
        .class = "none"
    },
};

static const std_parameter_t pid_24_params[] = {
    {
        .name = "OxySensor5_Volt",
        .unit = "volts",
        .scale = 0.005f,
        .offset = 0.0f,
        .min = 0.0f,
        .max = 1.0f,
        .bit_start = 31,
        .bit_length = 8,
        .class = "voltage"
    },
    {
        .name = "OxySensor5_STFT",
        .unit = "%",
        .scale = 0.78125f,
        .offset = -100.0f,
        .min = -100.0f,
        .max = 99.0f,
        .bit_start = 39,
        .bit_length = 8,
        .class = "none"
    },
};

static const std_parameter_t pid_25_params[] = {
    {
        .name = "OxySensor6_Volt",
        .unit = "volts",
        .scale = 0.005f,
        .offset = 0.0f,
        .min = 0.0f,
        .max = 1.0f,
        .bit_start = 31,
        .bit_length = 8,
        .class = "voltage"
    },
    {
        .name = "OxySensor6_STFT",
        .unit = "%",
        .scale = 0.78125f,
        .offset = -100.0f,
        .min = -100.0f,
        .max = 99.0f,
        .bit_start = 39,
        .bit_length = 8,
        .class = "none"
    },
};

static const std_parameter_t pid_26_params[] = {
    {
        .name = "OxySensor7_Volt",
        .unit = "volts",
        .scale = 0.005f,
        .offset = 0.0f,
        .min = 0.0f,
        .max = 1.0f,
        .bit_start = 31,
        .bit_length = 8,
        .class = "voltage"
    },
    {
        .name = "OxySensor7_STFT",
        .unit = "%",
        .scale = 0.78125f,
        .offset = -100.0f,
        .min = -100.0f,
        .max = 99.0f,
        .bit_start = 39,
        .bit_length = 8,
        .class = "none"
    },
};

static const std_parameter_t pid_27_params[] = {
    {
        .name = "OxySensor8_Volt",
        .unit = "volts",
        .scale = 0.005f,
        .offset = 0.0f,
        .min = 0.0f,
        .max = 1.0f,
        .bit_start = 31,
        .bit_length = 8,
        .class = "voltage"
    },
    {
        .name = "OxySensor8_STFT",
        .unit = "%",
        .scale = 0.78125f,
        .offset = -100.0f,
        .min = -100.0f,
        .max = 99.0f,
        .bit_start = 39,
        .bit_length = 8,
        .class = "none"
    },
};

static const std_parameter_t pid_28_params[] = {
    {
        .name = "OBDStandard",
        .unit = "Encoded",
        .scale = 1.0f,
        .offset = 0.0f,
        .min = 0.0f,
        .max = 0.0f,
        .bit_start = 31,
        .bit_length = 8,
        .class = "none"
    },
};

static const std_parameter_t pid_29_params[] = {
    {
        .name = "OxySensorsPresent_4Banks",
        .unit = "none",
        .scale = 1.0f,
        .offset = 0.0f,
        .min = 0.0f,
        .max = 0.0f,
        .bit_start = 0,
        .bit_length = 0,
        .class = "pressure"
    },
};

static const std_parameter_t pid_30_params[] = {
    {
        .name = "AuxiliaryInputStatus",
        .unit = "none",
        .scale = 1.0f,
        .offset = 0.0f,
        .min = 0.0f,
        .max = 0.0f,
        .bit_start = 0,
        .bit_length = 0,
        .class = "none"
    },
};

static const std_parameter_t pid_31_params[] = {
    {
        .name = "TimeSinceEngStart",
        .unit = "seconds",
        .scale = 1.0f,
        .offset = 0.0f,
        .min = 0.0f,
        .max = 65535.0f,
        .bit_start = 31,
        .bit_length = 16,
        .class = "duration"
    },
};

static const std_parameter_t pid_32_params[] = {
    {
        .name = "PIDsSupported_21_40",
        .unit = "Encoded",
        .scale = 1.0f,
        .offset = 0.0f,
        .min = 0.0f,
        .max = 0.0f,
        .bit_start = 31,
        .bit_length = 32,
        .class = "none"
    },
};

static const std_parameter_t pid_33_params[] = {
    {
        .name = "DistanceMILOn",
        .unit = "km",
        .scale = 1.0f,
        .offset = 0.0f,
        .min = 0.0f,
        .max = 65535.0f,
        .bit_start = 31,
        .bit_length = 16,
        .class = "distance"
    },
};

static const std_parameter_t pid_34_params[] = {
    {
        .name = "FuelRailPres",
        .unit = "kPa",
        .scale = 0.079f,
        .offset = 0.0f,
        .min = 0.0f,
        .max = 5177.0f,
        .bit_start = 31,
        .bit_length = 16,
        .class = "pressure"
    },
};

static const std_parameter_t pid_35_params[] = {
    {
        .name = "FuelRailGaug",
        .unit = "kPa",
        .scale = 10.0f,
        .offset = 0.0f,
        .min = 0.0f,
        .max = 655350.0f,
        .bit_start = 31,
        .bit_length = 16,
        .class = "pressure"
    },
};

static const std_parameter_t pid_36_params[] = {
    {
        .name = "OxySensor1_FAER",
        .unit = "ratio",
        .scale = 3.051757813e-05f,
        .offset = 0.0f,
        .min = 0.0f,
        .max = 2.0f,
        .bit_start = 31,
        .bit_length = 16,
        .class = "gas"
    },
    {
        .name = "OxySensor1_Volt",
        .unit = "volts",
        .scale = 0.0001220703125f,
        .offset = 0.0f,
        .min = 0.0f,
        .max = 2.0f,
        .bit_start = 47,
        .bit_length = 16,
        .class = "voltage"
    },
};

static const std_parameter_t pid_37_params[] = {
    {
        .name = "OxySensor2_FAER",
        .unit = "ratio",
        .scale = 3.051757813e-05f,
        .offset = 0.0f,
        .min = 0.0f,
        .max = 2.0f,
        .bit_start = 31,
        .bit_length = 16,
        .class = "gas"
    },
    {
        .name = "OxySensor2_Volt",
        .unit = "volts",
        .scale = 0.0001220703125f,
        .offset = 0.0f,
        .min = 0.0f,
        .max = 8.0f,
        .bit_start = 47,
        .bit_length = 16,
        .class = "voltage"
    },
};

static const std_parameter_t pid_38_params[] = {
    {
        .name = "OxySensor3_FAER",
        .unit = "ratio",
        .scale = 3.051757813e-05f,
        .offset = 0.0f,
        .min = 0.0f,
        .max = 2.0f,
        .bit_start = 31,
        .bit_length = 16,
        .class = "gas"
    },
    {
        .name = "OxySensor3_Volt",
        .unit = "volts",
        .scale = 0.0001220703125f,
        .offset = 0.0f,
        .min = 0.0f,
        .max = 8.0f,
        .bit_start = 47,
        .bit_length = 16,
        .class = "voltage"
    },
};

static const std_parameter_t pid_39_params[] = {
    {
        .name = "OxySensor4_FAER",
        .unit = "ratio",
        .scale = 3.051757813e-05f,
        .offset = 0.0f,
        .min = 0.0f,
        .max = 2.0f,
        .bit_start = 31,
        .bit_length = 16,
        .class = "gas"
    },
    {
        .name = "OxySensor4_Volt",
        .unit = "volts",
        .scale = 0.0001220703125f,
        .offset = 0.0f,
        .min = 0.0f,
        .max = 8.0f,
        .bit_start = 47,
        .bit_length = 16,
        .class = "voltage"
    },
};

static const std_parameter_t pid_40_params[] = {
    {
        .name = "OxySensor5_FAER",
        .unit = "ratio",
        .scale = 3.051757813e-05f,
        .offset = 0.0f,
        .min = 0.0f,
        .max = 2.0f,
        .bit_start = 31,
        .bit_length = 16,
        .class = "gas"
    },
    {
        .name = "OxySensor5_Volt",
        .unit = "volts",
        .scale = 0.0001220703125f,
        .offset = 0.0f,
        .min = 0.0f,
        .max = 8.0f,
        .bit_start = 47,
        .bit_length = 16,
        .class = "voltage"
    },
};

static const std_parameter_t pid_41_params[] = {
    {
        .name = "OxySensor6_FAER",
        .unit = "ratio",
        .scale = 3.051757813e-05f,
        .offset = 0.0f,
        .min = 0.0f,
        .max = 2.0f,
        .bit_start = 31,
        .bit_length = 16,
        .class = "gas"
    },
    {
        .name = "OxySensor6_Volt",
        .unit = "volts",
        .scale = 0.0001220703125f,
        .offset = 0.0f,
        .min = 0.0f,
        .max = 8.0f,
        .bit_start = 47,
        .bit_length = 16,
        .class = "voltage"
    },
};

static const std_parameter_t pid_42_params[] = {
    {
        .name = "OxySensor7_FAER",
        .unit = "ratio",
        .scale = 3.051757813e-05f,
        .offset = 0.0f,
        .min = 0.0f,
        .max = 2.0f,
        .bit_start = 31,
        .bit_length = 16,
        .class = "gas"
    },
    {
        .name = "OxySensor7_Volt",
        .unit = "volts",
        .scale = 0.0001220703125f,
        .offset = 0.0f,
        .min = 0.0f,
        .max = 8.0f,
        .bit_start = 47,
        .bit_length = 16,
        .class = "voltage"
    },
};

static const std_parameter_t pid_43_params[] = {
    {
        .name = "OxySensor8_FAER",
        .unit = "ratio",
        .scale = 3.051757813e-05f,
        .offset = 0.0f,
        .min = 0.0f,
        .max = 2.0f,
        .bit_start = 31,
        .bit_length = 16,
        .class = "gas"
    },
    {
        .name = "OxySensor8_Volt",
        .unit = "volts",
        .scale = 0.0001220703125f,
        .offset = 0.0f,
        .min = 0.0f,
        .max = 8.0f,
        .bit_start = 47,
        .bit_length = 16,
        .class = "voltage"
    },
};

static const std_parameter_t pid_44_params[] = {
    {
        .name = "CmdEGR",
        .unit = "%",
        .scale = 0.3921568627f,
        .offset = 0.0f,
        .min = 0.0f,
        .max = 100.0f,
        .bit_start = 31,
        .bit_length = 8,
        .class = "none"
    },
};

static const std_parameter_t pid_45_params[] = {
    {
        .name = "EGRError",
        .unit = "%",
        .scale = 0.78125f,
        .offset = -100.0f,
        .min = -100.0f,
        .max = 99.0f,
        .bit_start = 31,
        .bit_length = 8,
        .class = "none"
    },
};

static const std_parameter_t pid_46_params[] = {
    {
        .name = "CmdEvapPurge",
        .unit = "%",
        .scale = 0.3921568627f,
        .offset = 0.0f,
        .min = 0.0f,
        .max = 100.0f,
        .bit_start = 31,
        .bit_length = 8,
        .class = "none"
    },
};

static const std_parameter_t pid_47_params[] = {
    {
        .name = "FuelTankLevel",
        .unit = "%",
        .scale = 0.3921568627f,
        .offset = 0.0f,
        .min = 0.0f,
        .max = 100.0f,
        .bit_start = 31,
        .bit_length = 8,
        .class = "volume_storage"
    },
};

static const std_parameter_t pid_48_params[] = {
    {
        .name = "WarmUpsSinceCodeClear",
        .unit = "count",
        .scale = 1.0f,
        .offset = 0.0f,
        .min = 0.0f,
        .max = 255.0f,
        .bit_start = 31,
        .bit_length = 8,
        .class = "none"
    },
};

static const std_parameter_t pid_49_params[] = {
    {
        .name = "DistanceSinceCodeClear",
        .unit = "km",
        .scale = 1.0f,
        .offset = 0.0f,
        .min = 0.0f,
        .max = 65535.0f,
        .bit_start = 31,
        .bit_length = 16,
        .class = "distance"
    },
};

static const std_parameter_t pid_50_params[] = {
    {
        .name = "EvapSysVaporPres",
        .unit = "Pa",
        .scale = 0.25f,
        .offset = 0.0f,
        .min = -8192.0f,
        .max = 8192.0f,
        .bit_start = 31,
        .bit_length = 16,
        .class = "pressure"
    },
};

static const std_parameter_t pid_51_params[] = {
    {
        .name = "AbsBaroPres",
        .unit = "kPa",
        .scale = 1.0f,
        .offset = 0.0f,
        .min = 0.0f,
        .max = 255.0f,
        .bit_start = 31,
        .bit_length = 8,
        .class = "atmospheric_pressure"
    },
};

static const std_parameter_t pid_52_params[] = {
    {
        .name = "OxySensor1_FAER",
        .unit = "ratio",
        .scale = 3.051757813e-05f,
        .offset = 0.0f,
        .min = 0.0f,
        .max = 2.0f,
        .bit_start = 31,
        .bit_length = 16,
        .class = "gas"
    },
    {
        .name = "OxySensor1_Crnt",
        .unit = "mA",
        .scale = 0.00390625f,
        .offset = -128.0f,
        .min = -128.0f,
        .max = 128.0f,
        .bit_start = 47,
        .bit_length = 16,
        .class = "current"
    },
};

static const std_parameter_t pid_53_params[] = {
    {
        .name = "OxySensor2_FAER",
        .unit = "ratio",
        .scale = 3.051757813e-05f,
        .offset = 0.0f,
        .min = 0.0f,
        .max = 2.0f,
        .bit_start = 31,
        .bit_length = 16,
        .class = "gas"
    },
    {
        .name = "OxySensor2_Crnt",
        .unit = "mA",
        .scale = 0.00390625f,
        .offset = -128.0f,
        .min = -128.0f,
        .max = 128.0f,
        .bit_start = 47,
        .bit_length = 16,
        .class = "current"
    },
};

static const std_parameter_t pid_54_params[] = {
    {
        .name = "OxySensor3_FAER",
        .unit = "ratio",
        .scale = 3.051757813e-05f,
        .offset = 0.0f,
        .min = 0.0f,
        .max = 2.0f,
        .bit_start = 31,
        .bit_length = 16,
        .class = "gas"
    },
    {
        .name = "OxySensor3_Crnt",
        .unit = "mA",
        .scale = 0.00390625f,
        .offset = -128.0f,
        .min = -128.0f,
        .max = 128.0f,
        .bit_start = 47,
        .bit_length = 16,
        .class = "current"
    },
};

static const std_parameter_t pid_55_params[] = {
    {
        .name = "OxySensor4_FAER",
        .unit = "ratio",
        .scale = 3.051757813e-05f,
        .offset = 0.0f,
        .min = 0.0f,
        .max = 2.0f,
        .bit_start = 31,
        .bit_length = 16,
        .class = "gas"
    },
    {
        .name = "OxySensor4_Crnt",
        .unit = "mA",
        .scale = 0.00390625f,
        .offset = -128.0f,
        .min = -128.0f,
        .max = 128.0f,
        .bit_start = 47,
        .bit_length = 16,
        .class = "current"
    },
};

static const std_parameter_t pid_56_params[] = {
    {
        .name = "OxySensor5_FAER",
        .unit = "ratio",
        .scale = 3.051757813e-05f,
        .offset = 0.0f,
        .min = 0.0f,
        .max = 2.0f,
        .bit_start = 31,
        .bit_length = 16,
        .class = "gas"
    },
    {
        .name = "OxySensor5_Crnt",
        .unit = "mA",
        .scale = 0.00390625f,
        .offset = -128.0f,
        .min = -128.0f,
        .max = 128.0f,
        .bit_start = 47,
        .bit_length = 16,
        .class = "current"
    },
};

static const std_parameter_t pid_57_params[] = {
    {
        .name = "OxySensor6_FAER",
        .unit = "ratio",
        .scale = 3.051757813e-05f,
        .offset = 0.0f,
        .min = 0.0f,
        .max = 2.0f,
        .bit_start = 31,
        .bit_length = 16,
        .class = "gas"
    },
    {
        .name = "OxySensor6_Crnt",
        .unit = "mA",
        .scale = 0.00390625f,
        .offset = -128.0f,
        .min = -128.0f,
        .max = 128.0f,
        .bit_start = 47,
        .bit_length = 16,
        .class = "current"
    },
};

static const std_parameter_t pid_58_params[] = {
    {
        .name = "OxySensor7_FAER",
        .unit = "ratio",
        .scale = 3.051757813e-05f,
        .offset = 0.0f,
        .min = 0.0f,
        .max = 2.0f,
        .bit_start = 31,
        .bit_length = 16,
        .class = "gas"
    },
    {
        .name = "OxySensor7_Crnt",
        .unit = "mA",
        .scale = 0.00390625f,
        .offset = -128.0f,
        .min = -128.0f,
        .max = 128.0f,
        .bit_start = 47,
        .bit_length = 16,
        .class = "current"
    },
};

static const std_parameter_t pid_59_params[] = {
    {
        .name = "OxySensor8_FAER",
        .unit = "ratio",
        .scale = 3.051757813e-05f,
        .offset = 0.0f,
        .min = 0.0f,
        .max = 2.0f,
        .bit_start = 31,
        .bit_length = 16,
        .class = "gas"
    },
    {
        .name = "OxySensor8_Crnt",
        .unit = "mA",
        .scale = 0.00390625f,
        .offset = -128.0f,
        .min = -128.0f,
        .max = 128.0f,
        .bit_start = 47,
        .bit_length = 16,
        .class = "current"
    },
};

static const std_parameter_t pid_60_params[] = {
    {
        .name = "CatTempBank1Sens1",
        .unit = "degC",
        .scale = 0.1f,
        .offset = -40.0f,
        .min = -40.0f,
        .max = 6514.0f,
        .bit_start = 31,
        .bit_length = 16,
        .class = "temperature"
    },
};

static const std_parameter_t pid_61_params[] = {
    {
        .name = "CatTempBank2Sens1",
        .unit = "degC",
        .scale = 0.1f,
        .offset = -40.0f,
        .min = -40.0f,
        .max = 6514.0f,
        .bit_start = 31,
        .bit_length = 16,
        .class = "temperature"
    },
};

static const std_parameter_t pid_62_params[] = {
    {
        .name = "CatTempBank1Sens2",
        .unit = "degC",
        .scale = 0.1f,
        .offset = -40.0f,
        .min = -40.0f,
        .max = 6514.0f,
        .bit_start = 31,
        .bit_length = 16,
        .class = "temperature"
    },
};

static const std_parameter_t pid_63_params[] = {
    {
        .name = "CatTempBank2Sens2",
        .unit = "degC",
        .scale = 0.1f,
        .offset = -40.0f,
        .min = -40.0f,
        .max = 6514.0f,
        .bit_start = 31,
        .bit_length = 16,
        .class = "temperature"
    },
};

static const std_parameter_t pid_64_params[] = {
    {
        .name = "PIDsSupported_41_60",
        .unit = "Encoded",
        .scale = 1.0f,
        .offset = 0.0f,
        .min = 0.0f,
        .max = 0.0f,
        .bit_start = 31,
        .bit_length = 32,
        .class = "none"
    },
};

static const std_parameter_t pid_65_params[] = {
    {
        .name = "MonStatusDriveCycle",
        .unit = "Encoded",
        .scale = 1.0f,
        .offset = 0.0f,
        .min = 0.0f,
        .max = 0.0f,
        .bit_start = 31,
        .bit_length = 32,
        .class = "none"
    },
};

static const std_parameter_t pid_66_params[] = {
    {
        .name = "ControlModuleVolt",
        .unit = "V",
        .scale = 0.001f,
        .offset = 0.0f,
        .min = 0.0f,
        .max = 66.0f,
        .bit_start = 31,
        .bit_length = 16,
        .class = "voltage"
    },
};

static const std_parameter_t pid_67_params[] = {
    {
        .name = "AbsLoadValue",
        .unit = "%",
        .scale = 0.3921568627f,
        .offset = 0.0f,
        .min = 0.0f,
        .max = 25700.0f,
        .bit_start = 31,
        .bit_length = 16,
        .class = "power_factor"
    },
};

static const std_parameter_t pid_68_params[] = {
    {
        .name = "FuelAirCmdEquiv",
        .unit = "ratio",
        .scale = 3.051757813e-05f,
        .offset = 0.0f,
        .min = 0.0f,
        .max = 2.0f,
        .bit_start = 31,
        .bit_length = 16,
        .class = "none"
    },
};

static const std_parameter_t pid_69_params[] = {
    {
        .name = "RelThrottlePos",
        .unit = "%",
        .scale = 0.3921568627f,
        .offset = 0.0f,
        .min = 0.0f,
        .max = 100.0f,
        .bit_start = 31,
        .bit_length = 8,
        .class = "none"
    },
};

static const std_parameter_t pid_70_params[] = {
    {
        .name = "AmbientAirTemp",
        .unit = "degC",
        .scale = 1.0f,
        .offset = -40.0f,
        .min = -40.0f,
        .max = 215.0f,
        .bit_start = 31,
        .bit_length = 8,
        .class = "temperature"
    },
};

static const std_parameter_t pid_71_params[] = {
    {
        .name = "AbsThrottlePosB",
        .unit = "%",
        .scale = 0.3921568627f,
        .offset = 0.0f,
        .min = 0.0f,
        .max = 100.0f,
        .bit_start = 31,
        .bit_length = 8,
        .class = "none"
    },
};

static const std_parameter_t pid_72_params[] = {
    {
        .name = "AbsThrottlePosC",
        .unit = "%",
        .scale = 0.3921568627f,
        .offset = 0.0f,
        .min = 0.0f,
        .max = 100.0f,
        .bit_start = 31,
        .bit_length = 8,
        .class = "none"
    },
};

static const std_parameter_t pid_73_params[] = {
    {
        .name = "AbsThrottlePosD",
        .unit = "%",
        .scale = 0.3921568627f,
        .offset = 0.0f,
        .min = 0.0f,
        .max = 100.0f,
        .bit_start = 31,
        .bit_length = 8,
        .class = "none"
    },
};

static const std_parameter_t pid_74_params[] = {
    {
        .name = "AbsThrottlePosE",
        .unit = "%",
        .scale = 0.3921568627f,
        .offset = 0.0f,
        .min = 0.0f,
        .max = 100.0f,
        .bit_start = 31,
        .bit_length = 8,
        .class = "none"
    },
};

static const std_parameter_t pid_75_params[] = {
    {
        .name = "AbsThrottlePosF",
        .unit = "%",
        .scale = 0.3921568627f,
        .offset = 0.0f,
        .min = 0.0f,
        .max = 100.0f,
        .bit_start = 31,
        .bit_length = 8,
        .class = "none"
    },
};

static const std_parameter_t pid_76_params[] = {
    {
        .name = "CmdThrottleAct",
        .unit = "%",
        .scale = 0.3921568627f,
        .offset = 0.0f,
        .min = 0.0f,
        .max = 100.0f,
        .bit_start = 31,
        .bit_length = 8,
        .class = "none"
    },
};

static const std_parameter_t pid_77_params[] = {
    {
        .name = "TimeRunMILOn",
        .unit = "minutes",
        .scale = 1.0f,
        .offset = 0.0f,
        .min = 0.0f,
        .max = 65535.0f,
        .bit_start = 31,
        .bit_length = 16,
        .class = "duration"
    },
};

static const std_parameter_t pid_78_params[] = {
    {
        .name = "TimeSinceCodeClear",
        .unit = "minutes",
        .scale = 1.0f,
        .offset = 0.0f,
        .min = 0.0f,
        .max = 65535.0f,
        .bit_start = 31,
        .bit_length = 16,
        .class = "duration"
    },
};

static const std_parameter_t pid_79_params[] = {
    {
        .name = "Max_FAER",
        .unit = "ratio",
        .scale = 1.0f,
        .offset = 0.0f,
        .min = 0.0f,
        .max = 255.0f,
        .bit_start = 31,
        .bit_length = 8,
        .class = "gas"
    },
    {
        .name = "Max_OxySensVol",
        .unit = "V",
        .scale = 1.0f,
        .offset = 0.0f,
        .min = 0.0f,
        .max = 255.0f,
        .bit_start = 39,
        .bit_length = 8,
        .class = "voltage"
    },
    {
        .name = "Max_OxySensCrnt",
        .unit = "mA",
        .scale = 1.0f,
        .offset = 0.0f,
        .min = 0.0f,
        .max = 255.0f,
        .bit_start = 47,
        .bit_length = 8,
        .class = "current"
    },
    {
        .name = "Max_IntManiAbsPres",
        .unit = "kPa",
        .scale = 10.0f,
        .offset = 0.0f,
        .min = 0.0f,
        .max = 2550.0f,
        .bit_start = 55,
        .bit_length = 8,
        .class = "pressure"
    },
};

static const std_parameter_t pid_80_params[] = {
    {
        .name = "Max_AirFlowMAF",
        .unit = "g/s",
        .scale = 10.0f,
        .offset = 0.0f,
        .min = 0.0f,
        .max = 2550.0f,
        .bit_start = 31,
        .bit_length = 8,
        .class = "none"
    },
};

static const std_parameter_t pid_81_params[] = {
    {
        .name = "FuelType",
        .unit = "Encoded",
        .scale = 1.0f,
        .offset = 0.0f,
        .min = 0.0f,
        .max = 0.0f,
        .bit_start = 31,
        .bit_length = 8,
        .class = "none"
    },
};

static const std_parameter_t pid_82_params[] = {
    {
        .name = "EthanolFuelPct",
        .unit = "%",
        .scale = 0.3921568627f,
        .offset = 0.0f,
        .min = 0.0f,
        .max = 100.0f,
        .bit_start = 31,
        .bit_length = 8,
        .class = "volume_storage"
    },
};

static const std_parameter_t pid_83_params[] = {
    {
        .name = "AbsEvapSysVapPres",
        .unit = "kPa",
        .scale = 0.005f,
        .offset = 0.0f,
        .min = 0.0f,
        .max = 328.0f,
        .bit_start = 31,
        .bit_length = 16,
        .class = "pressure"
    },
};

static const std_parameter_t pid_84_params[] = {
    {
        .name = "EvapSysVapPres",
        .unit = "Pa",
        .scale = 1.0f,
        .offset = -32767.0f,
        .min = -32767.0f,
        .max = 32768.0f,
        .bit_start = 31,
        .bit_length = 16,
        .class = "pressure"
    },
};

static const std_parameter_t pid_85_params[] = {
    {
        .name = "ShortSecOxyTrimBank1",
        .unit = "%",
        .scale = 0.78125f,
        .offset = -100.0f,
        .min = -100.0f,
        .max = 99.0f,
        .bit_start = 31,
        .bit_length = 8,
        .class = "none"
    },
    {
        .name = "ShortSecOxyTrimBank3",
        .unit = "%",
        .scale = 0.78125f,
        .offset = -100.0f,
        .min = -100.0f,
        .max = 99.0f,
        .bit_start = 39,
        .bit_length = 8,
        .class = "none"
    },
};

static const std_parameter_t pid_86_params[] = {
    {
        .name = "LongSecOxyTrimBank1",
        .unit = "%",
        .scale = 0.78125f,
        .offset = -100.0f,
        .min = -100.0f,
        .max = 99.0f,
        .bit_start = 31,
        .bit_length = 8,
        .class = "none"
    },
    {
        .name = "LongSecOxyTrimBank3",
        .unit = "%",
        .scale = 0.78125f,
        .offset = -100.0f,
        .min = -100.0f,
        .max = 99.0f,
        .bit_start = 39,
        .bit_length = 8,
        .class = "none"
    },
};

static const std_parameter_t pid_87_params[] = {
    {
        .name = "ShortSecOxyTrimBank2",
        .unit = "%",
        .scale = 0.78125f,
        .offset = -100.0f,
        .min = -100.0f,
        .max = 99.0f,
        .bit_start = 31,
        .bit_length = 8,
        .class = "none"
    },
    {
        .name = "ShortSecOxyTrimBank4",
        .unit = "%",
        .scale = 0.78125f,
        .offset = -100.0f,
        .min = -100.0f,
        .max = 99.0f,
        .bit_start = 39,
        .bit_length = 8,
        .class = "none"
    },
};

static const std_parameter_t pid_88_params[] = {
    {
        .name = "LongSecOxyTrimBank2",
        .unit = "%",
        .scale = 0.78125f,
        .offset = -100.0f,
        .min = -100.0f,
        .max = 99.0f,
        .bit_start = 31,
        .bit_length = 8,
        .class = "none"
    },
    {
        .name = "LongSecOxyTrimBank4",
        .unit = "%",
        .scale = 0.78125f,
        .offset = -100.0f,
        .min = -100.0f,
        .max = 99.0f,
        .bit_start = 39,
        .bit_length = 8,
        .class = "none"
    },
};

static const std_parameter_t pid_89_params[] = {
    {
        .name = "FuelRailAbsPres",
        .unit = "kPa",
        .scale = 10.0f,
        .offset = 0.0f,
        .min = 0.0f,
        .max = 655350.0f,
        .bit_start = 31,
        .bit_length = 16,
        .class = "pressure"
    },
};

static const std_parameter_t pid_90_params[] = {
    {
        .name = "RelAccelPedalPos",
        .unit = "%",
        .scale = 0.3921568627f,
        .offset = 0.0f,
        .min = 0.0f,
        .max = 100.0f,
        .bit_start = 31,
        .bit_length = 8,
        .class = "none"
    },
};

static const std_parameter_t pid_91_params[] = {
    {
        .name = "HybrBatPackRemLife",
        .unit = "%",
        .scale = 0.3921568627f,
        .offset = 0.0f,
        .min = 0.0f,
        .max = 100.0f,
        .bit_start = 31,
        .bit_length = 8,
        .class = "none"
    },
};

static const std_parameter_t pid_92_params[] = {
    {
        .name = "EngineOilTemp",
        .unit = "degC",
        .scale = 1.0f,
        .offset = -40.0f,
        .min = -40.0f,
        .max = 215.0f,
        .bit_start = 31,
        .bit_length = 8,
        .class = "temperature"
    },
};

static const std_parameter_t pid_93_params[] = {
    {
        .name = "FuelInjectionTiming",
        .unit = "deg",
        .scale = 0.0078125f,
        .offset = -210.0f,
        .min = -210.0f,
        .max = 302.0f,
        .bit_start = 31,
        .bit_length = 16,
        .class = "none"
    },
};

static const std_parameter_t pid_94_params[] = {
    {
        .name = "EngineFuelRate",
        .unit = "L/h",
        .scale = 0.05f,
        .offset = 0.0f,
        .min = 0.0f,
        .max = 3277.0f,
        .bit_start = 31,
        .bit_length = 16,
        .class = "none"
    },
};

static const std_parameter_t pid_95_params[] = {
    {
        .name = "EmissionReq",
        .unit = "Encoded",
        .scale = 1.0f,
        .offset = 0.0f,
        .min = 0.0f,
        .max = 0.0f,
        .bit_start = 31,
        .bit_length = 8,
        .class = "none"
    },
};

static const std_parameter_t pid_96_params[] = {
    {
        .name = "PIDsSupported_61_80",
        .unit = "Encoded",
        .scale = 1.0f,
        .offset = 0.0f,
        .min = 0.0f,
        .max = 0.0f,
        .bit_start = 31,
        .bit_length = 32,
        .class = "none"
    },
};

static const std_parameter_t pid_97_params[] = {
    {
        .name = "DemandEngTorqPct",
        .unit = "%",
        .scale = 1.0f,
        .offset = -125.0f,
        .min = -125.0f,
        .max = 130.0f,
        .bit_start = 31,
        .bit_length = 8,
        .class = "none"
    },
};

static const std_parameter_t pid_98_params[] = {
    {
        .name = "ActualEngTorqPct",
        .unit = "%",
        .scale = 1.0f,
        .offset = -125.0f,
        .min = -125.0f,
        .max = 130.0f,
        .bit_start = 31,
        .bit_length = 8,
        .class = "none"
    },
};

static const std_parameter_t pid_99_params[] = {
    {
        .name = "EngRefTorq",
        .unit = "Nm",
        .scale = 1.0f,
        .offset = 0.0f,
        .min = 0.0f,
        .max = 65535.0f,
        .bit_start = 31,
        .bit_length = 16,
        .class = "none"
    },
};

static const std_parameter_t pid_100_params[] = {
    {
        .name = "EngPctTorq_Idle",
        .unit = "%",
        .scale = 1.0f,
        .offset = -125.0f,
        .min = -125.0f,
        .max = 130.0f,
        .bit_start = 31,
        .bit_length = 8,
        .class = "none"
    },
    {
        .name = "EngPctTorq_EP1",
        .unit = "%",
        .scale = 1.0f,
        .offset = -125.0f,
        .min = -125.0f,
        .max = 130.0f,
        .bit_start = 39,
        .bit_length = 8,
        .class = "none"
    },
    {
        .name = "EngPctTorq_EP2",
        .unit = "%",
        .scale = 1.0f,
        .offset = -125.0f,
        .min = -125.0f,
        .max = 130.0f,
        .bit_start = 47,
        .bit_length = 8,
        .class = "none"
    },
    {
        .name = "EngPctTorq_EP3",
        .unit = "%",
        .scale = 1.0f,
        .offset = -125.0f,
        .min = -125.0f,
        .max = 130.0f,
        .bit_start = 55,
        .bit_length = 8,
        .class = "none"
    },
    {
        .name = "EngPctTorq_EP4",
        .unit = "%",
        .scale = 1.0f,
        .offset = -125.0f,
        .min = -125.0f,
        .max = 130.0f,
        .bit_start = 63,
        .bit_length = 8,
        .class = "none"
    },
};

static const std_parameter_t pid_101_params[] = {
    {
        .name = "AuxInputOutput",
        .unit = "Encoded",
        .scale = 1.0f,
        .offset = 0.0f,
        .min = 0.0f,
        .max = 0.0f,
        .bit_start = 31,
        .bit_length = 8,
        .class = "none"
    },
};

static const std_parameter_t pid_102_params[] = {
    {
        .name = "MAFSensorA",
        .unit = "grams/sec",
        .scale = 0.03125f,
        .offset = 0.0f,
        .min = 0.0f,
        .max = 2048.0f,
        .bit_start = 39,
        .bit_length = 16,
        .class = "none"
    },
    {
        .name = "MAFSensorB",
        .unit = "grams/sec",
        .scale = 0.03125f,
        .offset = 0.0f,
        .min = 0.0f,
        .max = 2048.0f,
        .bit_start = 55,
        .bit_length = 16,
        .class = "none"
    },
};

static const std_parameter_t pid_103_params[] = {
    {
        .name = "EngineCoolantTemp1",
        .unit = "degC",
        .scale = 1.0f,
        .offset = -40.0f,
        .min = -40.0f,
        .max = 215.0f,
        .bit_start = 39,
        .bit_length = 8,
        .class = "temperature"
    },
    {
        .name = "EngineCoolantTemp2",
        .unit = "degC",
        .scale = 1.0f,
        .offset = -40.0f,
        .min = -40.0f,
        .max = 215.0f,
        .bit_start = 47,
        .bit_length = 8,
        .class = "temperature"
    },
};

static const std_parameter_t pid_104_params[] = {
    {
        .name = "IntakeAirTempSens1",
        .unit = "degC",
        .scale = 1.0f,
        .offset = -40.0f,
        .min = -40.0f,
        .max = 215.0f,
        .bit_start = 39,
        .bit_length = 8,
        .class = "temperature"
    },
    {
        .name = "IntakeAirTempSens2",
        .unit = "degC",
        .scale = 1.0f,
        .offset = -40.0f,
        .min = -40.0f,
        .max = 215.0f,
        .bit_start = 47,
        .bit_length = 8,
        .class = "temperature"
    },
};

static const std_parameter_t pid_105_params[] = {
    {
        .name = "CmdEGR_EGRError",
        .unit = "none",
        .scale = 1.0f,
        .offset = 0.0f,
        .min = 0.0f,
        .max = 0.0f,
        .bit_start = 0,
        .bit_length = 0,
        .class = "none"
    },
};

static const std_parameter_t pid_106_params[] = {
    {
        .name = "CmdDieselIntAir",
        .unit = "none",
        .scale = 1.0f,
        .offset = 0.0f,
        .min = 0.0f,
        .max = 0.0f,
        .bit_start = 0,
        .bit_length = 0,
        .class = "none"
    },
};

static const std_parameter_t pid_107_params[] = {
    {
        .name = "ExhaustGasTemp",
        .unit = "none",
        .scale = 1.0f,
        .offset = 0.0f,
        .min = 0.0f,
        .max = 0.0f,
        .bit_start = 0,
        .bit_length = 0,
        .class = "temperature"
    },
};

static const std_parameter_t pid_108_params[] = {
    {
        .name = "CmdThrottleActRel",
        .unit = "none",
        .scale = 1.0f,
        .offset = 0.0f,
        .min = 0.0f,
        .max = 0.0f,
        .bit_start = 0,
        .bit_length = 0,
        .class = "none"
    },
};

static const std_parameter_t pid_109_params[] = {
    {
        .name = "FuelPresContrSys",
        .unit = "none",
        .scale = 1.0f,
        .offset = 0.0f,
        .min = 0.0f,
        .max = 0.0f,
        .bit_start = 0,
        .bit_length = 0,
        .class = "pressure"
    },
};

static const std_parameter_t pid_110_params[] = {
    {
        .name = "InjPresContrSys",
        .unit = "none",
        .scale = 1.0f,
        .offset = 0.0f,
        .min = 0.0f,
        .max = 0.0f,
        .bit_start = 0,
        .bit_length = 0,
        .class = "pressure"
    },
};

static const std_parameter_t pid_111_params[] = {
    {
        .name = "TurboComprPres",
        .unit = "none",
        .scale = 1.0f,
        .offset = 0.0f,
        .min = 0.0f,
        .max = 0.0f,
        .bit_start = 0,
        .bit_length = 0,
        .class = "pressure"
    },
};

static const std_parameter_t pid_112_params[] = {
    {
        .name = "BoostPresCntrl",
        .unit = "none",
        .scale = 1.0f,
        .offset = 0.0f,
        .min = 0.0f,
        .max = 0.0f,
        .bit_start = 0,
        .bit_length = 0,
        .class = "pressure"
    },
};

static const std_parameter_t pid_113_params[] = {
    {
        .name = "VariableGeoTurboVGTCtr",
        .unit = "none",
        .scale = 1.0f,
        .offset = 0.0f,
        .min = 0.0f,
        .max = 0.0f,
        .bit_start = 0,
        .bit_length = 0,
        .class = "none"
    },
};

static const std_parameter_t pid_114_params[] = {
    {
        .name = "WastegateControl",
        .unit = "none",
        .scale = 1.0f,
        .offset = 0.0f,
        .min = 0.0f,
        .max = 0.0f,
        .bit_start = 0,
        .bit_length = 0,
        .class = "none"
    },
};

static const std_parameter_t pid_115_params[] = {
    {
        .name = "ExhaustPressure",
        .unit = "none",
        .scale = 1.0f,
        .offset = 0.0f,
        .min = 0.0f,
        .max = 0.0f,
        .bit_start = 0,
        .bit_length = 0,
        .class = "pressure"
    },
};

static const std_parameter_t pid_116_params[] = {
    {
        .name = "TurbochargerRpm",
        .unit = "none",
        .scale = 1.0f,
        .offset = 0.0f,
        .min = 0.0f,
        .max = 0.0f,
        .bit_start = 0,
        .bit_length = 0,
        .class = "speed"
    },
};

static const std_parameter_t pid_117_params[] = {
    {
        .name = "TurbochargerTemperature",
        .unit = "none",
        .scale = 1.0f,
        .offset = 0.0f,
        .min = 0.0f,
        .max = 0.0f,
        .bit_start = 0,
        .bit_length = 0,
        .class = "temperature"
    },
};

static const std_parameter_t pid_118_params[] = {
    {
        .name = "TurbochargerTemperature",
        .unit = "none",
        .scale = 1.0f,
        .offset = 0.0f,
        .min = 0.0f,
        .max = 0.0f,
        .bit_start = 0,
        .bit_length = 0,
        .class = "temperature"
    },
};

static const std_parameter_t pid_119_params[] = {
    {
        .name = "ChargeAirCoolerTemperature",
        .unit = "none",
        .scale = 1.0f,
        .offset = 0.0f,
        .min = 0.0f,
        .max = 0.0f,
        .bit_start = 0,
        .bit_length = 0,
        .class = "temperature"
    },
};

static const std_parameter_t pid_120_params[] = {
    {
        .name = "EGT_Bank1",
        .unit = "none",
        .scale = 1.0f,
        .offset = 0.0f,
        .min = 0.0f,
        .max = 0.0f,
        .bit_start = 0,
        .bit_length = 0,
        .class = "none"
    },
};

static const std_parameter_t pid_121_params[] = {
    {
        .name = "EGT_Bank2",
        .unit = "none",
        .scale = 1.0f,
        .offset = 0.0f,
        .min = 0.0f,
        .max = 0.0f,
        .bit_start = 0,
        .bit_length = 0,
        .class = "none"
    },
};

static const std_parameter_t pid_122_params[] = {
    {
        .name = "DPF_DifferentialPressure",
        .unit = "none",
        .scale = 1.0f,
        .offset = 0.0f,
        .min = 0.0f,
        .max = 0.0f,
        .bit_start = 0,
        .bit_length = 0,
        .class = "pressure"
    },
};

static const std_parameter_t pid_123_params[] = {
    {
        .name = "DPF",
        .unit = "none",
        .scale = 1.0f,
        .offset = 0.0f,
        .min = 0.0f,
        .max = 0.0f,
        .bit_start = 0,
        .bit_length = 0,
        .class = "none"
    },
};

static const std_parameter_t pid_124_params[] = {
    {
        .name = "DPF_Temperature",
        .unit = "degC",
        .scale = 0.1f,
        .offset = -40.0f,
        .min = -40.0f,
        .max = 6514.0f,
        .bit_start = 31,
        .bit_length = 16,
        .class = "temperature"
    },
};

static const std_parameter_t pid_125_params[] = {
    {
        .name = "NOx_NTE_ControlAreaStatus",
        .unit = "none",
        .scale = 1.0f,
        .offset = 0.0f,
        .min = 0.0f,
        .max = 0.0f,
        .bit_start = 0,
        .bit_length = 0,
        .class = "none"
    },
};

static const std_parameter_t pid_126_params[] = {
    {
        .name = "PM_NTE_ControlAreaStatus",
        .unit = "none",
        .scale = 1.0f,
        .offset = 0.0f,
        .min = 0.0f,
        .max = 0.0f,
        .bit_start = 0,
        .bit_length = 0,
        .class = "none"
    },
};

static const std_parameter_t pid_127_params[] = {
    {
        .name = "EngineRunTime",
        .unit = "seconds",
        .scale = 1.0f,
        .offset = 0.0f,
        .min = 0.0f,
        .max = 0.0f,
        .bit_start = 0,
        .bit_length = 0,
        .class = "duration"
    },
};

static const std_parameter_t pid_128_params[] = {
    {
        .name = "PIDsSupported_81_A0",
        .unit = "Encoded",
        .scale = 1.0f,
        .offset = 0.0f,
        .min = 0.0f,
        .max = 0.0f,
        .bit_start = 31,
        .bit_length = 32,
        .class = "none"
    },
};

static const std_parameter_t pid_129_params[] = {
    {
        .name = "EngineRunTime_AECD",
        .unit = "none",
        .scale = 1.0f,
        .offset = 0.0f,
        .min = 0.0f,
        .max = 0.0f,
        .bit_start = 0,
        .bit_length = 0,
        .class = "duration"
    },
};

static const std_parameter_t pid_130_params[] = {
    {
        .name = "EngineRunTime_AECD",
        .unit = "none",
        .scale = 1.0f,
        .offset = 0.0f,
        .min = 0.0f,
        .max = 0.0f,
        .bit_start = 0,
        .bit_length = 0,
        .class = "duration"
    },
};

static const std_parameter_t pid_131_params[] = {
    {
        .name = "NOxSensor",
        .unit = "none",
        .scale = 1.0f,
        .offset = 0.0f,
        .min = 0.0f,
        .max = 0.0f,
        .bit_start = 0,
        .bit_length = 0,
        .class = "none"
    },
};

static const std_parameter_t pid_132_params[] = {
    {
        .name = "ManifoldSurfaceTemperature",
        .unit = "none",
        .scale = 1.0f,
        .offset = 0.0f,
        .min = 0.0f,
        .max = 0.0f,
        .bit_start = 0,
        .bit_length = 0,
        .class = "temperature"
    },
};

static const std_parameter_t pid_133_params[] = {
    {
        .name = "NOxReagentSystem",
        .unit = "none",
        .scale = 1.0f,
        .offset = 0.0f,
        .min = 0.0f,
        .max = 0.0f,
        .bit_start = 0,
        .bit_length = 0,
        .class = "none"
    },
};

static const std_parameter_t pid_134_params[] = {
    {
        .name = "PM_Sensor",
        .unit = "none",
        .scale = 1.0f,
        .offset = 0.0f,
        .min = 0.0f,
        .max = 0.0f,
        .bit_start = 0,
        .bit_length = 0,
        .class = "none"
    },
};

static const std_parameter_t pid_135_params[] = {
    {
        .name = "IntakeManifoldAbsolutePressure",
        .unit = "none",
        .scale = 1.0f,
        .offset = 0.0f,
        .min = 0.0f,
        .max = 0.0f,
        .bit_start = 0,
        .bit_length = 0,
        .class = "pressure"
    },
};

static const std_parameter_t pid_136_params[] = {
    {
        .name = "SCR_InduceSystem",
        .unit = "none",
        .scale = 1.0f,
        .offset = 0.0f,
        .min = 0.0f,
        .max = 0.0f,
        .bit_start = 0,
        .bit_length = 0,
        .class = "none"
    },
};

static const std_parameter_t pid_137_params[] = {
    {
        .name = "RunTimeForAECD_11_15",
        .unit = "none",
        .scale = 1.0f,
        .offset = 0.0f,
        .min = 0.0f,
        .max = 0.0f,
        .bit_start = 0,
        .bit_length = 0,
        .class = "duration"
    },
};

static const std_parameter_t pid_138_params[] = {
    {
        .name = "RunTimeForAECD_16_20",
        .unit = "none",
        .scale = 1.0f,
        .offset = 0.0f,
        .min = 0.0f,
        .max = 0.0f,
        .bit_start = 0,
        .bit_length = 0,
        .class = "duration"
    },
};

static const std_parameter_t pid_139_params[] = {
    {
        .name = "DieselAftertreatment",
        .unit = "none",
        .scale = 1.0f,
        .offset = 0.0f,
        .min = 0.0f,
        .max = 0.0f,
        .bit_start = 0,
        .bit_length = 0,
        .class = "none"
    },
};

static const std_parameter_t pid_140_params[] = {
    {
        .name = "O2Sensor_WideRange",
        .unit = "none",
        .scale = 1.0f,
        .offset = 0.0f,
        .min = 0.0f,
        .max = 0.0f,
        .bit_start = 0,
        .bit_length = 0,
        .class = "none"
    },
};

static const std_parameter_t pid_141_params[] = {
    {
        .name = "ThrottlePositionG",
        .unit = "%",
        .scale = 0.3921568627f,
        .offset = 0.0f,
        .min = 0.0f,
        .max = 100.0f,
        .bit_start = 31,
        .bit_length = 8,
        .class = "none"
    },
};

static const std_parameter_t pid_142_params[] = {
    {
        .name = "EngineFrictionPercentTorque",
        .unit = "%",
        .scale = 1.0f,
        .offset = -125.0f,
        .min = -125.0f,
        .max = 130.0f,
        .bit_start = 31,
        .bit_length = 8,
        .class = "none"
    },
};

static const std_parameter_t pid_143_params[] = {
    {
        .name = "PMSensorBank1_2",
        .unit = "none",
        .scale = 1.0f,
        .offset = 0.0f,
        .min = 0.0f,
        .max = 0.0f,
        .bit_start = 0,
        .bit_length = 0,
        .class = "none"
    },
};

static const std_parameter_t pid_144_params[] = {
    {
        .name = "WWH_OBD_SysInfo",
        .unit = "hours",
        .scale = 1.0f,
        .offset = 0.0f,
        .min = 0.0f,
        .max = 0.0f,
        .bit_start = 0,
        .bit_length = 0,
        .class = "none"
    },
};

static const std_parameter_t pid_145_params[] = {
    {
        .name = "WWH_OBD_SysInfo",
        .unit = "hours",
        .scale = 1.0f,
        .offset = 0.0f,
        .min = 0.0f,
        .max = 0.0f,
        .bit_start = 0,
        .bit_length = 0,
        .class = "none"
    },
};

static const std_parameter_t pid_146_params[] = {
    {
        .name = "FuelSystemControl",
        .unit = "none",
        .scale = 1.0f,
        .offset = 0.0f,
        .min = 0.0f,
        .max = 0.0f,
        .bit_start = 0,
        .bit_length = 0,
        .class = "none"
    },
};

static const std_parameter_t pid_147_params[] = {
    {
        .name = "WWH_OBD_CtrSupport",
        .unit = "hours",
        .scale = 1.0f,
        .offset = 0.0f,
        .min = 0.0f,
        .max = 0.0f,
        .bit_start = 0,
        .bit_length = 0,
        .class = "none"
    },
};

static const std_parameter_t pid_148_params[] = {
    {
        .name = "NOxWarningInducementSys",
        .unit = "none",
        .scale = 1.0f,
        .offset = 0.0f,
        .min = 0.0f,
        .max = 0.0f,
        .bit_start = 0,
        .bit_length = 0,
        .class = "none"
    },
};

static const std_parameter_t pid_152_params[] = {
    {
        .name = "EGT_Sensor",
        .unit = "none",
        .scale = 1.0f,
        .offset = 0.0f,
        .min = 0.0f,
        .max = 0.0f,
        .bit_start = 0,
        .bit_length = 0,
        .class = "none"
    },
};

static const std_parameter_t pid_153_params[] = {
    {
        .name = "EGT_Sensor",
        .unit = "none",
        .scale = 1.0f,
        .offset = 0.0f,
        .min = 0.0f,
        .max = 0.0f,
        .bit_start = 0,
        .bit_length = 0,
        .class = "none"
    },
};

static const std_parameter_t pid_154_params[] = {
    {
        .name = "Hybrid_EV_System",
        .unit = "none",
        .scale = 1.0f,
        .offset = 0.0f,
        .min = 0.0f,
        .max = 0.0f,
        .bit_start = 0,
        .bit_length = 0,
        .class = "none"
    },
};

static const std_parameter_t pid_155_params[] = {
    {
        .name = "DieselExhaustFluidSensorData",
        .unit = "%",
        .scale = 0.3921f,
        .offset = 0.0f,
        .min = 0.0f,
        .max = 100.0f,
        .bit_start = 55,
        .bit_length = 8,
        .class = "none"
    },
};

static const std_parameter_t pid_156_params[] = {
    {
        .name = "O2SensorData",
        .unit = "none",
        .scale = 1.0f,
        .offset = 0.0f,
        .min = 0.0f,
        .max = 0.0f,
        .bit_start = 0,
        .bit_length = 0,
        .class = "none"
    },
};

static const std_parameter_t pid_157_params[] = {
    {
        .name = "EngineFuelRate",
        .unit = "g/s",
        .scale = 1.0f,
        .offset = 0.0f,
        .min = 0.0f,
        .max = 0.0f,
        .bit_start = 0,
        .bit_length = 0,
        .class = "none"
    },
};

static const std_parameter_t pid_158_params[] = {
    {
        .name = "EngineExhaustFlowRate",
        .unit = "kg/h",
        .scale = 1.0f,
        .offset = 0.0f,
        .min = 0.0f,
        .max = 0.0f,
        .bit_start = 0,
        .bit_length = 0,
        .class = "none"
    },
};

static const std_parameter_t pid_159_params[] = {
    {
        .name = "FuelSystemPercentageUse",
        .unit = "none",
        .scale = 1.0f,
        .offset = 0.0f,
        .min = 0.0f,
        .max = 0.0f,
        .bit_start = 0,
        .bit_length = 0,
        .class = "temperature"
    },
};

static const std_parameter_t pid_160_params[] = {
    {
        .name = "PIDsSupported_A1_C0",
        .unit = "Encoded",
        .scale = 1.0f,
        .offset = 0.0f,
        .min = 0.0f,
        .max = 0.0f,
        .bit_start = 31,
        .bit_length = 32,
        .class = "none"
    },
};

static const std_parameter_t pid_161_params[] = {
    {
        .name = "NOxSensorCorrectedData",
        .unit = "ppm",
        .scale = 1.0f,
        .offset = 0.0f,
        .min = 0.0f,
        .max = 0.0f,
        .bit_start = 0,
        .bit_length = 0,
        .class = "none"
    },
};

static const std_parameter_t pid_162_params[] = {
    {
        .name = "CylinderFuelRate",
        .unit = "mg/stroke",
        .scale = 0.03125f,
        .offset = 0.0f,
        .min = 0.0f,
        .max = 2048.0f,
        .bit_start = 31,
        .bit_length = 16,
        .class = "none"
    },
};

static const std_parameter_t pid_163_params[] = {
    {
        .name = "EvapSystemVaporPressure",
        .unit = "none",
        .scale = 1.0f,
        .offset = 0.0f,
        .min = 0.0f,
        .max = 0.0f,
        .bit_start = 0,
        .bit_length = 0,
        .class = "pressure"
    },
};

static const std_parameter_t pid_164_params[] = {
    {
        .name = "TransmissionActualGear",
        .unit = "ratio",
        .scale = 0.001f,
        .offset = 0.0f,
        .min = 0.0f,
        .max = 66.0f,
        .bit_start = 47,
        .bit_length = 16,
        .class = "none"
    },
};

static const std_parameter_t pid_165_params[] = {
    {
        .name = "ComDieselExhaustFluidDosing",
        .unit = "%",
        .scale = 0.5f,
        .offset = 0.0f,
        .min = 0.0f,
        .max = 128.0f,
        .bit_start = 39,
        .bit_length = 8,
        .class = "none"
    },
};

static const std_parameter_t pid_166_params[] = {
    {
        .name = "Odometer",
        .unit = "km",
        .scale = 0.1f,
        .offset = 0.0f,
        .min = 0.0f,
        .max = 429000000.0f,
        .bit_start = 31,
        .bit_length = 32,
        .class = "distance"
    },
};

static const std_parameter_t pid_167_params[] = {
    {
        .name = "NOxSensorConc3_4",
        .unit = "none",
        .scale = 1.0f,
        .offset = 0.0f,
        .min = 0.0f,
        .max = 0.0f,
        .bit_start = 0,
        .bit_length = 0,
        .class = "none"
    },
};

static const std_parameter_t pid_168_params[] = {
    {
        .name = "NOxSensorCorrectConc3_4",
        .unit = "none",
        .scale = 1.0f,
        .offset = 0.0f,
        .min = 0.0f,
        .max = 0.0f,
        .bit_start = 0,
        .bit_length = 0,
        .class = "none"
    },
};

static const std_parameter_t pid_192_params[] = {
    {
        .name = "PIDsSupported_C1_E0",
        .unit = "Encoded",
        .scale = 1.0f,
        .offset = 0.0f,
        .min = 0.0f,
        .max = 0.0f,
        .bit_start = 31,
        .bit_length = 32,
        .class = "none"
    },
};

static const std_pid_t pid_array[] = {
    [0x00] = {
        .base_name = "PIDsSupported_01_20",
        .params = pid_0_params,
        .num_params = 1
    },
    [0x01] = {
        .base_name = "MonitorStatus",
        .params = pid_1_params,
        .num_params = 1
    },
    [0x02] = {
        .base_name = "FreezeDTC",
        .params = pid_2_params,
        .num_params = 1
    },
    [0x03] = {
        .base_name = "FuelSystemStatus",
        .params = pid_3_params,
        .num_params = 1
    },
    [0x04] = {
        .base_name = "CalcEngineLoad",
        .params = pid_4_params,
        .num_params = 1
    },
    [0x05] = {
        .base_name = "EngineCoolantTemp",
        .params = pid_5_params,
        .num_params = 1
    },
    [0x06] = {
        .base_name = "ShortFuelTrimBank1",
        .params = pid_6_params,
        .num_params = 1
    },
    [0x07] = {
        .base_name = "LongFuelTrimBank1",
        .params = pid_7_params,
        .num_params = 1
    },
    [0x08] = {
        .base_name = "ShortFuelTrimBank2",
        .params = pid_8_params,
        .num_params = 1
    },
    [0x09] = {
        .base_name = "LongFuelTrimBank2",
        .params = pid_9_params,
        .num_params = 1
    },
    [0x0A] = {
        .base_name = "FuelPressure",
        .params = pid_10_params,
        .num_params = 1
    },
    [0x0B] = {
        .base_name = "IntakeManiAbsPress",
        .params = pid_11_params,
        .num_params = 1
    },
    [0x0C] = {
        .base_name = "EngineRPM",
        .params = pid_12_params,
        .num_params = 1
    },
    [0x0D] = {
        .base_name = "VehicleSpeed",
        .params = pid_13_params,
        .num_params = 1
    },
    [0x0E] = {
        .base_name = "TimingAdvance",
        .params = pid_14_params,
        .num_params = 1
    },
    [0x0F] = {
        .base_name = "IntakeAirTemperature",
        .params = pid_15_params,
        .num_params = 1
    },
    [0x10] = {
        .base_name = "MAFAirFlowRate",
        .params = pid_16_params,
        .num_params = 1
    },
    [0x11] = {
        .base_name = "ThrottlePosition",
        .params = pid_17_params,
        .num_params = 1
    },
    [0x12] = {
        .base_name = "CmdSecAirStatus",
        .params = pid_18_params,
        .num_params = 1
    },
    [0x13] = {
        .base_name = "OxySensorsPresent_2Banks",
        .params = pid_19_params,
        .num_params = 1
    },
    [0x14] = {
        .base_name = "OxySensor1_Volt",
        .params = pid_20_params,
        .num_params = 2
    },
    [0x15] = {
        .base_name = "OxySensor2_Volt",
        .params = pid_21_params,
        .num_params = 2
    },
    [0x16] = {
        .base_name = "OxySensor3_Volt",
        .params = pid_22_params,
        .num_params = 2
    },
    [0x17] = {
        .base_name = "OxySensor4_Volt",
        .params = pid_23_params,
        .num_params = 2
    },
    [0x18] = {
        .base_name = "OxySensor5_Volt",
        .params = pid_24_params,
        .num_params = 2
    },
    [0x19] = {
        .base_name = "OxySensor6_Volt",
        .params = pid_25_params,
        .num_params = 2
    },
    [0x1A] = {
        .base_name = "OxySensor7_Volt",
        .params = pid_26_params,
        .num_params = 2
    },
    [0x1B] = {
        .base_name = "OxySensor8_Volt",
        .params = pid_27_params,
        .num_params = 2
    },
    [0x1C] = {
        .base_name = "OBDStandard",
        .params = pid_28_params,
        .num_params = 1
    },
    [0x1D] = {
        .base_name = "OxySensorsPresent_4Banks",
        .params = pid_29_params,
        .num_params = 1
    },
    [0x1E] = {
        .base_name = "AuxiliaryInputStatus",
        .params = pid_30_params,
        .num_params = 1
    },
    [0x1F] = {
        .base_name = "TimeSinceEngStart",
        .params = pid_31_params,
        .num_params = 1
    },
    [0x20] = {
        .base_name = "PIDsSupported_21_40",
        .params = pid_32_params,
        .num_params = 1
    },
    [0x21] = {
        .base_name = "DistanceMILOn",
        .params = pid_33_params,
        .num_params = 1
    },
    [0x22] = {
        .base_name = "FuelRailPres",
        .params = pid_34_params,
        .num_params = 1
    },
    [0x23] = {
        .base_name = "FuelRailGaug",
        .params = pid_35_params,
        .num_params = 1
    },
    [0x24] = {
        .base_name = "OxySensor1_FAER",
        .params = pid_36_params,
        .num_params = 2
    },
    [0x25] = {
        .base_name = "OxySensor2_FAER",
        .params = pid_37_params,
        .num_params = 2
    },
    [0x26] = {
        .base_name = "OxySensor3_FAER",
        .params = pid_38_params,
        .num_params = 2
    },
    [0x27] = {
        .base_name = "OxySensor4_FAER",
        .params = pid_39_params,
        .num_params = 2
    },
    [0x28] = {
        .base_name = "OxySensor5_FAER",
        .params = pid_40_params,
        .num_params = 2
    },
    [0x29] = {
        .base_name = "OxySensor6_FAER",
        .params = pid_41_params,
        .num_params = 2
    },
    [0x2A] = {
        .base_name = "OxySensor7_FAER",
        .params = pid_42_params,
        .num_params = 2
    },
    [0x2B] = {
        .base_name = "OxySensor8_FAER",
        .params = pid_43_params,
        .num_params = 2
    },
    [0x2C] = {
        .base_name = "CmdEGR",
        .params = pid_44_params,
        .num_params = 1
    },
    [0x2D] = {
        .base_name = "EGRError",
        .params = pid_45_params,
        .num_params = 1
    },
    [0x2E] = {
        .base_name = "CmdEvapPurge",
        .params = pid_46_params,
        .num_params = 1
    },
    [0x2F] = {
        .base_name = "FuelTankLevel",
        .params = pid_47_params,
        .num_params = 1
    },
    [0x30] = {
        .base_name = "WarmUpsSinceCodeClear",
        .params = pid_48_params,
        .num_params = 1
    },
    [0x31] = {
        .base_name = "DistanceSinceCodeClear",
        .params = pid_49_params,
        .num_params = 1
    },
    [0x32] = {
        .base_name = "EvapSysVaporPres",
        .params = pid_50_params,
        .num_params = 1
    },
    [0x33] = {
        .base_name = "AbsBaroPres",
        .params = pid_51_params,
        .num_params = 1
    },
    [0x34] = {
        .base_name = "OxySensor1_FAER",
        .params = pid_52_params,
        .num_params = 2
    },
    [0x35] = {
        .base_name = "OxySensor2_FAER",
        .params = pid_53_params,
        .num_params = 2
    },
    [0x36] = {
        .base_name = "OxySensor3_FAER",
        .params = pid_54_params,
        .num_params = 2
    },
    [0x37] = {
        .base_name = "OxySensor4_FAER",
        .params = pid_55_params,
        .num_params = 2
    },
    [0x38] = {
        .base_name = "OxySensor5_FAER",
        .params = pid_56_params,
        .num_params = 2
    },
    [0x39] = {
        .base_name = "OxySensor6_FAER",
        .params = pid_57_params,
        .num_params = 2
    },
    [0x3A] = {
        .base_name = "OxySensor7_FAER",
        .params = pid_58_params,
        .num_params = 2
    },
    [0x3B] = {
        .base_name = "OxySensor8_FAER",
        .params = pid_59_params,
        .num_params = 2
    },
    [0x3C] = {
        .base_name = "CatTempBank1Sens1",
        .params = pid_60_params,
        .num_params = 1
    },
    [0x3D] = {
        .base_name = "CatTempBank2Sens1",
        .params = pid_61_params,
        .num_params = 1
    },
    [0x3E] = {
        .base_name = "CatTempBank1Sens2",
        .params = pid_62_params,
        .num_params = 1
    },
    [0x3F] = {
        .base_name = "CatTempBank2Sens2",
        .params = pid_63_params,
        .num_params = 1
    },
    [0x40] = {
        .base_name = "PIDsSupported_41_60",
        .params = pid_64_params,
        .num_params = 1
    },
    [0x41] = {
        .base_name = "MonStatusDriveCycle",
        .params = pid_65_params,
        .num_params = 1
    },
    [0x42] = {
        .base_name = "ControlModuleVolt",
        .params = pid_66_params,
        .num_params = 1
    },
    [0x43] = {
        .base_name = "AbsLoadValue",
        .params = pid_67_params,
        .num_params = 1
    },
    [0x44] = {
        .base_name = "FuelAirCmdEquiv",
        .params = pid_68_params,
        .num_params = 1
    },
    [0x45] = {
        .base_name = "RelThrottlePos",
        .params = pid_69_params,
        .num_params = 1
    },
    [0x46] = {
        .base_name = "AmbientAirTemp",
        .params = pid_70_params,
        .num_params = 1
    },
    [0x47] = {
        .base_name = "AbsThrottlePosB",
        .params = pid_71_params,
        .num_params = 1
    },
    [0x48] = {
        .base_name = "AbsThrottlePosC",
        .params = pid_72_params,
        .num_params = 1
    },
    [0x49] = {
        .base_name = "AbsThrottlePosD",
        .params = pid_73_params,
        .num_params = 1
    },
    [0x4A] = {
        .base_name = "AbsThrottlePosE",
        .params = pid_74_params,
        .num_params = 1
    },
    [0x4B] = {
        .base_name = "AbsThrottlePosF",
        .params = pid_75_params,
        .num_params = 1
    },
    [0x4C] = {
        .base_name = "CmdThrottleAct",
        .params = pid_76_params,
        .num_params = 1
    },
    [0x4D] = {
        .base_name = "TimeRunMILOn",
        .params = pid_77_params,
        .num_params = 1
    },
    [0x4E] = {
        .base_name = "TimeSinceCodeClear",
        .params = pid_78_params,
        .num_params = 1
    },
    [0x4F] = {
        .base_name = "Max_FAER",
        .params = pid_79_params,
        .num_params = 4
    },
    [0x50] = {
        .base_name = "Max_AirFlowMAF",
        .params = pid_80_params,
        .num_params = 1
    },
    [0x51] = {
        .base_name = "FuelType",
        .params = pid_81_params,
        .num_params = 1
    },
    [0x52] = {
        .base_name = "EthanolFuelPct",
        .params = pid_82_params,
        .num_params = 1
    },
    [0x53] = {
        .base_name = "AbsEvapSysVapPres",
        .params = pid_83_params,
        .num_params = 1
    },
    [0x54] = {
        .base_name = "EvapSysVapPres",
        .params = pid_84_params,
        .num_params = 1
    },
    [0x55] = {
        .base_name = "ShortSecOxyTrimBank1",
        .params = pid_85_params,
        .num_params = 2
    },
    [0x56] = {
        .base_name = "LongSecOxyTrimBank1",
        .params = pid_86_params,
        .num_params = 2
    },
    [0x57] = {
        .base_name = "ShortSecOxyTrimBank2",
        .params = pid_87_params,
        .num_params = 2
    },
    [0x58] = {
        .base_name = "LongSecOxyTrimBank2",
        .params = pid_88_params,
        .num_params = 2
    },
    [0x59] = {
        .base_name = "FuelRailAbsPres",
        .params = pid_89_params,
        .num_params = 1
    },
    [0x5A] = {
        .base_name = "RelAccelPedalPos",
        .params = pid_90_params,
        .num_params = 1
    },
    [0x5B] = {
        .base_name = "HybrBatPackRemLife",
        .params = pid_91_params,
        .num_params = 1
    },
    [0x5C] = {
        .base_name = "EngineOilTemp",
        .params = pid_92_params,
        .num_params = 1
    },
    [0x5D] = {
        .base_name = "FuelInjectionTiming",
        .params = pid_93_params,
        .num_params = 1
    },
    [0x5E] = {
        .base_name = "EngineFuelRate",
        .params = pid_94_params,
        .num_params = 1
    },
    [0x5F] = {
        .base_name = "EmissionReq",
        .params = pid_95_params,
        .num_params = 1
    },
    [0x60] = {
        .base_name = "PIDsSupported_61_80",
        .params = pid_96_params,
        .num_params = 1
    },
    [0x61] = {
        .base_name = "DemandEngTorqPct",
        .params = pid_97_params,
        .num_params = 1
    },
    [0x62] = {
        .base_name = "ActualEngTorqPct",
        .params = pid_98_params,
        .num_params = 1
    },
    [0x63] = {
        .base_name = "EngRefTorq",
        .params = pid_99_params,
        .num_params = 1
    },
    [0x64] = {
        .base_name = "EngPctTorq_Idle",
        .params = pid_100_params,
        .num_params = 5
    },
    [0x65] = {
        .base_name = "AuxInputOutput",
        .params = pid_101_params,
        .num_params = 1
    },
    [0x66] = {
        .base_name = "MAFSensorA",
        .params = pid_102_params,
        .num_params = 2
    },
    [0x67] = {
        .base_name = "EngineCoolantTemp1",
        .params = pid_103_params,
        .num_params = 2
    },
    [0x68] = {
        .base_name = "IntakeAirTempSens1",
        .params = pid_104_params,
        .num_params = 2
    },
    [0x69] = {
        .base_name = "CmdEGR_EGRError",
        .params = pid_105_params,
        .num_params = 1
    },
    [0x6A] = {
        .base_name = "CmdDieselIntAir",
        .params = pid_106_params,
        .num_params = 1
    },
    [0x6B] = {
        .base_name = "ExhaustGasTemp",
        .params = pid_107_params,
        .num_params = 1
    },
    [0x6C] = {
        .base_name = "CmdThrottleActRel",
        .params = pid_108_params,
        .num_params = 1
    },
    [0x6D] = {
        .base_name = "FuelPresContrSys",
        .params = pid_109_params,
        .num_params = 1
    },
    [0x6E] = {
        .base_name = "InjPresContrSys",
        .params = pid_110_params,
        .num_params = 1
    },
    [0x6F] = {
        .base_name = "TurboComprPres",
        .params = pid_111_params,
        .num_params = 1
    },
    [0x70] = {
        .base_name = "BoostPresCntrl",
        .params = pid_112_params,
        .num_params = 1
    },
    [0x71] = {
        .base_name = "VariableGeoTurboVGTCtr",
        .params = pid_113_params,
        .num_params = 1
    },
    [0x72] = {
        .base_name = "WastegateControl",
        .params = pid_114_params,
        .num_params = 1
    },
    [0x73] = {
        .base_name = "ExhaustPressure",
        .params = pid_115_params,
        .num_params = 1
    },
    [0x74] = {
        .base_name = "TurbochargerRpm",
        .params = pid_116_params,
        .num_params = 1
    },
    [0x75] = {
        .base_name = "TurbochargerTemperature",
        .params = pid_117_params,
        .num_params = 1
    },
    [0x76] = {
        .base_name = "TurbochargerTemperature",
        .params = pid_118_params,
        .num_params = 1
    },
    [0x77] = {
        .base_name = "ChargeAirCoolerTemperature",
        .params = pid_119_params,
        .num_params = 1
    },
    [0x78] = {
        .base_name = "EGT_Bank1",
        .params = pid_120_params,
        .num_params = 1
    },
    [0x79] = {
        .base_name = "EGT_Bank2",
        .params = pid_121_params,
        .num_params = 1
    },
    [0x7A] = {
        .base_name = "DPF_DifferentialPressure",
        .params = pid_122_params,
        .num_params = 1
    },
    [0x7B] = {
        .base_name = "DPF",
        .params = pid_123_params,
        .num_params = 1
    },
    [0x7C] = {
        .base_name = "DPF_Temperature",
        .params = pid_124_params,
        .num_params = 1
    },
    [0x7D] = {
        .base_name = "NOx_NTE_ControlAreaStatus",
        .params = pid_125_params,
        .num_params = 1
    },
    [0x7E] = {
        .base_name = "PM_NTE_ControlAreaStatus",
        .params = pid_126_params,
        .num_params = 1
    },
    [0x7F] = {
        .base_name = "EngineRunTime",
        .params = pid_127_params,
        .num_params = 1
    },
    [0x80] = {
        .base_name = "PIDsSupported_81_A0",
        .params = pid_128_params,
        .num_params = 1
    },
    [0x81] = {
        .base_name = "EngineRunTime_AECD",
        .params = pid_129_params,
        .num_params = 1
    },
    [0x82] = {
        .base_name = "EngineRunTime_AECD",
        .params = pid_130_params,
        .num_params = 1
    },
    [0x83] = {
        .base_name = "NOxSensor",
        .params = pid_131_params,
        .num_params = 1
    },
    [0x84] = {
        .base_name = "ManifoldSurfaceTemperature",
        .params = pid_132_params,
        .num_params = 1
    },
    [0x85] = {
        .base_name = "NOxReagentSystem",
        .params = pid_133_params,
        .num_params = 1
    },
    [0x86] = {
        .base_name = "PM_Sensor",
        .params = pid_134_params,
        .num_params = 1
    },
    [0x87] = {
        .base_name = "IntakeManifoldAbsolutePressure",
        .params = pid_135_params,
        .num_params = 1
    },
    [0x88] = {
        .base_name = "SCR_InduceSystem",
        .params = pid_136_params,
        .num_params = 1
    },
    [0x89] = {
        .base_name = "RunTimeForAECD_11_15",
        .params = pid_137_params,
        .num_params = 1
    },
    [0x8A] = {
        .base_name = "RunTimeForAECD_16_20",
        .params = pid_138_params,
        .num_params = 1
    },
    [0x8B] = {
        .base_name = "DieselAftertreatment",
        .params = pid_139_params,
        .num_params = 1
    },
    [0x8C] = {
        .base_name = "O2Sensor_WideRange",
        .params = pid_140_params,
        .num_params = 1
    },
    [0x8D] = {
        .base_name = "ThrottlePositionG",
        .params = pid_141_params,
        .num_params = 1
    },
    [0x8E] = {
        .base_name = "EngineFrictionPercentTorque",
        .params = pid_142_params,
        .num_params = 1
    },
    [0x8F] = {
        .base_name = "PMSensorBank1_2",
        .params = pid_143_params,
        .num_params = 1
    },
    [0x90] = {
        .base_name = "WWH_OBD_SysInfo",
        .params = pid_144_params,
        .num_params = 1
    },
    [0x91] = {
        .base_name = "WWH_OBD_SysInfo",
        .params = pid_145_params,
        .num_params = 1
    },
    [0x92] = {
        .base_name = "FuelSystemControl",
        .params = pid_146_params,
        .num_params = 1
    },
    [0x93] = {
        .base_name = "WWH_OBD_CtrSupport",
        .params = pid_147_params,
        .num_params = 1
    },
    [0x94] = {
        .base_name = "NOxWarningInducementSys",
        .params = pid_148_params,
        .num_params = 1
    },
    [0x98] = {
        .base_name = "EGT_Sensor",
        .params = pid_152_params,
        .num_params = 1
    },
    [0x99] = {
        .base_name = "EGT_Sensor",
        .params = pid_153_params,
        .num_params = 1
    },
    [0x9A] = {
        .base_name = "Hybrid_EV_System",
        .params = pid_154_params,
        .num_params = 1
    },
    [0x9B] = {
        .base_name = "DieselExhaustFluidSensorData",
        .params = pid_155_params,
        .num_params = 1
    },
    [0x9C] = {
        .base_name = "O2SensorData",
        .params = pid_156_params,
        .num_params = 1
    },
    [0x9D] = {
        .base_name = "EngineFuelRate",
        .params = pid_157_params,
        .num_params = 1
    },
    [0x9E] = {
        .base_name = "EngineExhaustFlowRate",
        .params = pid_158_params,
        .num_params = 1
    },
    [0x9F] = {
        .base_name = "FuelSystemPercentageUse",
        .params = pid_159_params,
        .num_params = 1
    },
    [0xA0] = {
        .base_name = "PIDsSupported_A1_C0",
        .params = pid_160_params,
        .num_params = 1
    },
    [0xA1] = {
        .base_name = "NOxSensorCorrectedData",
        .params = pid_161_params,
        .num_params = 1
    },
    [0xA2] = {
        .base_name = "CylinderFuelRate",
        .params = pid_162_params,
        .num_params = 1
    },
    [0xA3] = {
        .base_name = "EvapSystemVaporPressure",
        .params = pid_163_params,
        .num_params = 1
    },
    [0xA4] = {
        .base_name = "TransmissionActualGear",
        .params = pid_164_params,
        .num_params = 1
    },
    [0xA5] = {
        .base_name = "ComDieselExhaustFluidDosing",
        .params = pid_165_params,
        .num_params = 1
    },
    [0xA6] = {
        .base_name = "Odometer",
        .params = pid_166_params,
        .num_params = 1
    },
    [0xA7] = {
        .base_name = "NOxSensorConc3_4",
        .params = pid_167_params,
        .num_params = 1
    },
    [0xA8] = {
        .base_name = "NOxSensorCorrectConc3_4",
        .params = pid_168_params,
        .num_params = 1
    },
    [0xC0] = {
        .base_name = "PIDsSupported_C1_E0",
        .params = pid_192_params,
        .num_params = 1
    },
};


#define PID_ARRAY_SIZE (sizeof(pid_array) / sizeof(pid_array[0]))

static inline const std_pid_t* get_pid(uint8_t pid_number) {
    return (pid_number < PID_ARRAY_SIZE) ? &pid_array[pid_number] : NULL;
}

/**
 * @brief Create JSON string containing all standard OBD2 PIDs information
 * @return Pointer to JSON string allocated in PSRAM, or NULL on failure
 * @note Caller is responsible for freeing the returned memory using heap_caps_free()
 */
char* create_standard_pids_json(void);

/**
 * @brief Get standard PIDs information as JSON string (cached)
 * @return Pointer to JSON string in PSRAM, or NULL on failure
 */
char* get_standard_pids_json(void);

/**
 * @brief Free the standard PIDs JSON string from PSRAM
 */
void free_standard_pids_json(void);

/**
 * @brief Get information for a specific PID as JSON
 * @param pid_number The PID number (0x00 to 0xFF)
 * @return Pointer to JSON string allocated in PSRAM, or NULL if PID not found
 * @note Caller is responsible for freeing the returned memory using heap_caps_free()
 */
char* get_pid_info_json(uint8_t pid_number);

#endif // OBD2_PIDS_H
