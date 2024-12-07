/**
 * @file obd2_pids.h
 * @brief OBD-II PID definitions
 * @generated 2024-12-07 11:14:11
 */

#ifndef OBD2_PIDS_H
#define OBD2_PIDS_H

#ifdef __cplusplus
extern "C" {
#endif
#include <stdint.h>
/**
 * @struct pid_info_t
 * @brief Structure containing PID information
 */
typedef struct {
    const char *name;           
    const char *description;    
    const char *class;
    uint8_t bit_start;         
    uint8_t bit_length;        
    float scale;               
    float offset;              
    float min;                 
    float max;                 
    const char *unit;          
    uint8_t is_encoded;        
} pid_info_t;
/**
 * @brief Standard OBD-II PIDs
 */
static const pid_info_t standard_pids[] = {
    {
        .name = "PIDsSupported_01_20",        /* PIDs supported [01 - 20] */
        .class = "enum",
        .bit_start = 31,
        .bit_length = 32,
        .scale = 1,
        .offset = 0,
        .min = 0,
        .max = 0,
        .unit = "Encoded"
    },
    {
        .name = "MonitorStatus",        /* Monitor status since DTCs cleared */
        .class = "enum",
        .bit_start = 31,
        .bit_length = 32,
        .scale = 1,
        .offset = 0,
        .min = 0,
        .max = 0,
        .unit = "Encoded"
    },
    {
        .name = "FreezeDTC",        /* Freeze DTC */
        .class = "enum",
        .bit_start = 31,
        .bit_length = 16,
        .scale = 1,
        .offset = 0,
        .min = 0,
        .max = 0,
        .unit = "Encoded"
    },
    {
        .name = "FuelSystemStatus",        /* Fuel system status */
        .class = "gas",
        .bit_start = 31,
        .bit_length = 16,
        .scale = 1,
        .offset = 0,
        .min = 0,
        .max = 0,
        .unit = "Encoded"
    },
    {
        .name = "CalcEngineLoad",        /* Calculated engine load */
        .class = "None",
        .bit_start = 31,
        .bit_length = 8,
        .scale = 0.3921568627,
        .offset = 0,
        .min = 0,
        .max = 100,
        .unit = "%"
    },
    {
        .name = "EngineCoolantTemp",        /* Engine coolant temperature */
        .class = "temperature",
        .bit_start = 31,
        .bit_length = 8,
        .scale = 1,
        .offset = -40,
        .min = -40,
        .max = 215,
        .unit = "degC"
    },
    {
        .name = "ShortFuelTrimBank1",        /* Short term fuel trim (bank 1) */
        .class = "None",
        .bit_start = 31,
        .bit_length = 8,
        .scale = 0.78125,
        .offset = -100,
        .min = -100,
        .max = 99,
        .unit = "%"
    },
    {
        .name = "LongFuelTrimBank1",        /* Long term fuel trim (bank 1) */
        .class = "None",
        .bit_start = 31,
        .bit_length = 8,
        .scale = 0.78125,
        .offset = -100,
        .min = -100,
        .max = 99,
        .unit = "%"
    },
    {
        .name = "ShortFuelTrimBank2",        /* Short term fuel trim (bank 2) */
        .class = "None",
        .bit_start = 31,
        .bit_length = 8,
        .scale = 0.78125,
        .offset = -100,
        .min = -100,
        .max = 99,
        .unit = "%"
    },
    {
        .name = "LongFuelTrimBank2",        /* Long term fuel trim (bank 2) */
        .class = "None",
        .bit_start = 31,
        .bit_length = 8,
        .scale = 0.78125,
        .offset = -100,
        .min = -100,
        .max = 99,
        .unit = "%"
    },
    {
        .name = "FuelPressure",        /* Fuel pressure (gauge pressure) */
        .class = "pressure",
        .bit_start = 31,
        .bit_length = 8,
        .scale = 3,
        .offset = 0,
        .min = 0,
        .max = 765,
        .unit = "kPa"
    },
    {
        .name = "IntakeManiAbsPress",        /* Intake manifold absolute pressure */
        .class = "pressure",
        .bit_start = 31,
        .bit_length = 8,
        .scale = 1,
        .offset = 0,
        .min = 0,
        .max = 255,
        .unit = "kPa"
    },
    {
        .name = "EngineRPM",        /* Engine speed */
        .class = "speed",
        .bit_start = 31,
        .bit_length = 16,
        .scale = 0.25,
        .offset = 0,
        .min = 0,
        .max = 16384,
        .unit = "rpm"
    },
    {
        .name = "VehicleSpeed",        /* Vehicle speed */
        .class = "speed",
        .bit_start = 31,
        .bit_length = 8,
        .scale = 1,
        .offset = 0,
        .min = 0,
        .max = 255,
        .unit = "km/h"
    },
    {
        .name = "TimingAdvance",        /* Timing advance */
        .class = "None",
        .bit_start = 31,
        .bit_length = 8,
        .scale = 0.5,
        .offset = -64,
        .min = -64,
        .max = 64,
        .unit = "deg"
    },
    {
        .name = "IntakeAirTemperature",        /* Intake air temperature */
        .class = "temperature",
        .bit_start = 31,
        .bit_length = 8,
        .scale = 1,
        .offset = -40,
        .min = -40,
        .max = 215,
        .unit = "degC"
    },
    {
        .name = "MAFAirFlowRate",        /* Mass air flow sensor air flow rate */
        .class = "volume_flow_rate",
        .bit_start = 31,
        .bit_length = 16,
        .scale = 0.01,
        .offset = 0,
        .min = 0,
        .max = 655,
        .unit = "grams/sec"
    },
    {
        .name = "ThrottlePosition",        /* Throttle position */
        .class = "None",
        .bit_start = 31,
        .bit_length = 8,
        .scale = 0.3921568627,
        .offset = 0,
        .min = 0,
        .max = 100,
        .unit = "%"
    },
    {
        .name = "CmdSecAirStatus",        /* Commanded secondary air status */
        .class = "enum",
        .bit_start = 31,
        .bit_length = 8,
        .scale = 1,
        .offset = 0,
        .min = 0,
        .max = 0,
        .unit = "Encoded"
    },
    {
        .name = "OxySensorsPresent_2Banks",        /* Oxygen sensors present (2 banks) */
        .class = "None",
        .bit_start = 0,
        .bit_length = 0,
        .scale = 0,
        .offset = 0,
        .min = 0,
        .max = 0,
        .unit = ""
    },
    {
        .name = "OxySensor1_Volt",        /* Oxygen sensor 1 (voltage) */
        .class = "voltage",
        .bit_start = 31,
        .bit_length = 8,
        .scale = 0.005,
        .offset = 0,
        .min = 0,
        .max = 1,
        .unit = "volts"
    },
    {
        .name = "OxySensor2_Volt",        /* Oxygen sensor 2 (voltage) */
        .class = "voltage",
        .bit_start = 31,
        .bit_length = 8,
        .scale = 0.005,
        .offset = 0,
        .min = 0,
        .max = 1,
        .unit = "volts"
    },
    {
        .name = "OxySensor3_Volt",        /* Oxygen sensor 3 (voltage) */
        .class = "voltage",
        .bit_start = 31,
        .bit_length = 8,
        .scale = 0.005,
        .offset = 0,
        .min = 0,
        .max = 1,
        .unit = "volts"
    },
    {
        .name = "OxySensor4_Volt",        /* Oxygen sensor 4 (voltage) */
        .class = "voltage",
        .bit_start = 31,
        .bit_length = 8,
        .scale = 0.005,
        .offset = 0,
        .min = 0,
        .max = 1,
        .unit = "volts"
    },
    {
        .name = "OxySensor5_Volt",        /* Oxygen sensor 5 (voltage) */
        .class = "voltage",
        .bit_start = 31,
        .bit_length = 8,
        .scale = 0.005,
        .offset = 0,
        .min = 0,
        .max = 1,
        .unit = "volts"
    },
    {
        .name = "OxySensor6_Volt",        /* Oxygen sensor 6 (voltage) */
        .class = "voltage",
        .bit_start = 31,
        .bit_length = 8,
        .scale = 0.005,
        .offset = 0,
        .min = 0,
        .max = 1,
        .unit = "volts"
    },
    {
        .name = "OxySensor7_Volt",        /* Oxygen sensor 7 (voltage) */
        .class = "voltage",
        .bit_start = 31,
        .bit_length = 8,
        .scale = 0.005,
        .offset = 0,
        .min = 0,
        .max = 1,
        .unit = "volts"
    },
    {
        .name = "OxySensor8_Volt",        /* Oxygen sensor 8 (voltage) */
        .class = "voltage",
        .bit_start = 31,
        .bit_length = 8,
        .scale = 0.005,
        .offset = 0,
        .min = 0,
        .max = 1,
        .unit = "volts"
    },
    {
        .name = "OBDStandard",        /* OBD standards the vehicle conforms to */
        .class = "enum",
        .bit_start = 31,
        .bit_length = 8,
        .scale = 1,
        .offset = 0,
        .min = 0,
        .max = 0,
        .unit = "Encoded"
    },
    {
        .name = "OxySensorsPresent_4Banks",        /* Oxygen sensors present (4 banks) */
        .class = "None",
        .bit_start = 0,
        .bit_length = 0,
        .scale = 0,
        .offset = 0,
        .min = 0,
        .max = 0,
        .unit = ""
    },
    {
        .name = "AuxiliaryInputStatus",        /* Auxiliary input status */
        .class = "None",
        .bit_start = 0,
        .bit_length = 0,
        .scale = 0,
        .offset = 0,
        .min = 0,
        .max = 0,
        .unit = ""
    },
    {
        .name = "TimeSinceEngStart",        /* Run time since engine start */
        .class = "duration",
        .bit_start = 31,
        .bit_length = 16,
        .scale = 1,
        .offset = 0,
        .min = 0,
        .max = 65535,
        .unit = "seconds"
    },
    {
        .name = "PIDsSupported_21_40",        /* PIDs supported [21 - 40] */
        .class = "enum",
        .bit_start = 31,
        .bit_length = 32,
        .scale = 1,
        .offset = 0,
        .min = 0,
        .max = 0,
        .unit = "Encoded"
    },
    {
        .name = "DistanceMILOn",        /* Distance traveled with MIL on */
        .class = "distance",
        .bit_start = 31,
        .bit_length = 16,
        .scale = 1,
        .offset = 0,
        .min = 0,
        .max = 65535,
        .unit = "km"
    },
    {
        .name = "FuelRailPres",        /* Fuel rail pres. (rel. to manifold vacuum) */
        .class = "pressure",
        .bit_start = 31,
        .bit_length = 16,
        .scale = 0.079,
        .offset = 0,
        .min = 0,
        .max = 5177,
        .unit = "kPa"
    },
    {
        .name = "FuelRailGaug",        /* Fuel rail gauge pres. (diesel, gas inject) */
        .class = "pressure",
        .bit_start = 31,
        .bit_length = 16,
        .scale = 10,
        .offset = 0,
        .min = 0,
        .max = 655350,
        .unit = "kPa"
    },
    {
        .name = "OxySensor1_FAER",        /* Oxygen sensor 1 (air-fuel equiv. ratio) */
        .class = "gas",
        .bit_start = 31,
        .bit_length = 16,
        .scale = 0.00003051757813,
        .offset = 0,
        .min = 0,
        .max = 2,
        .unit = "ratio"
    },
    {
        .name = "OxySensor2_FAER",        /* Oxygen sensor 2 (air-fuel equiv. ratio) */
        .class = "gas",
        .bit_start = 31,
        .bit_length = 16,
        .scale = 0.00003051757813,
        .offset = 0,
        .min = 0,
        .max = 2,
        .unit = "ratio"
    },
    {
        .name = "OxySensor3_FAER",        /* Oxygen sensor 3 (air-fuel equiv. ratio) */
        .class = "gas",
        .bit_start = 31,
        .bit_length = 16,
        .scale = 0.00003051757813,
        .offset = 0,
        .min = 0,
        .max = 2,
        .unit = "ratio"
    },
    {
        .name = "OxySensor4_FAER",        /* Oxygen sensor 4 (air-fuel equiv. ratio) */
        .class = "gas",
        .bit_start = 31,
        .bit_length = 16,
        .scale = 0.00003051757813,
        .offset = 0,
        .min = 0,
        .max = 2,
        .unit = "ratio"
    },
    {
        .name = "OxySensor5_FAER",        /* Oxygen sensor 5 (air-fuel equiv. ratio) */
        .class = "gas",
        .bit_start = 31,
        .bit_length = 16,
        .scale = 0.00003051757813,
        .offset = 0,
        .min = 0,
        .max = 2,
        .unit = "ratio"
    },
    {
        .name = "OxySensor6_FAER",        /* Oxygen sensor 6 (air-fuel equiv. ratio) */
        .class = "gas",
        .bit_start = 31,
        .bit_length = 16,
        .scale = 0.00003051757813,
        .offset = 0,
        .min = 0,
        .max = 2,
        .unit = "ratio"
    },
    {
        .name = "OxySensor7_FAER",        /* Oxygen sensor 7 (air-fuel equiv. ratio) */
        .class = "gas",
        .bit_start = 31,
        .bit_length = 16,
        .scale = 0.00003051757813,
        .offset = 0,
        .min = 0,
        .max = 2,
        .unit = "ratio"
    },
    {
        .name = "OxySensor8_FAER",        /* Oxygen sensor 8 (air-fuel equiv. ratio) */
        .class = "gas",
        .bit_start = 31,
        .bit_length = 16,
        .scale = 0.00003051757813,
        .offset = 0,
        .min = 0,
        .max = 2,
        .unit = "ratio"
    },
    {
        .name = "CmdEGR",        /* Commanded EGR */
        .class = "None",
        .bit_start = 31,
        .bit_length = 8,
        .scale = 0.3921568627,
        .offset = 0,
        .min = 0,
        .max = 100,
        .unit = "%"
    },
    {
        .name = "EGRError",        /* EGR Error */
        .class = "None",
        .bit_start = 31,
        .bit_length = 8,
        .scale = 0.78125,
        .offset = -100,
        .min = -100,
        .max = 99,
        .unit = "%"
    },
    {
        .name = "CmdEvapPurge",        /* Commanded evaporative purge */
        .class = "None",
        .bit_start = 31,
        .bit_length = 8,
        .scale = 0.3921568627,
        .offset = 0,
        .min = 0,
        .max = 100,
        .unit = "%"
    },
    {
        .name = "FuelTankLevel",        /* Fuel tank level input */
        .class = "None",
        .bit_start = 31,
        .bit_length = 8,
        .scale = 0.3921568627,
        .offset = 0,
        .min = 0,
        .max = 100,
        .unit = "%"
    },
    {
        .name = "WarmUpsSinceCodeClear",        /* Warmups since DTCs cleared */
        .class = "None",
        .bit_start = 31,
        .bit_length = 8,
        .scale = 1,
        .offset = 0,
        .min = 0,
        .max = 255,
        .unit = "count"
    },
    {
        .name = "DistanceSinceCodeClear",        /* Distance traveled since DTCs cleared */
        .class = "distance",
        .bit_start = 31,
        .bit_length = 16,
        .scale = 1,
        .offset = 0,
        .min = 0,
        .max = 65535,
        .unit = "km"
    },
    {
        .name = "EvapSysVaporPres",        /* Evap. system vapor pressure */
        .class = "pressure",
        .bit_start = 31,
        .bit_length = 16,
        .scale = 0.25,
        .offset = 0,
        .min = -8192,
        .max = 8192,
        .unit = "Pa"
    },
    {
        .name = "AbsBaroPres",        /* Absolute barometric pressure */
        .class = "pressure",
        .bit_start = 31,
        .bit_length = 8,
        .scale = 1,
        .offset = 0,
        .min = 0,
        .max = 255,
        .unit = "kPa"
    },
    {
        .name = "OxySensor1_FAER",        /* Oxygen sensor 1 (air-fuel equiv. ratio) */
        .class = "gas",
        .bit_start = 31,
        .bit_length = 16,
        .scale = 0.00003051757813,
        .offset = 0,
        .min = 0,
        .max = 2,
        .unit = "ratio"
    },
    {
        .name = "OxySensor2_FAER",        /* Oxygen sensor 2 (air-fuel equiv. ratio) */
        .class = "gas",
        .bit_start = 31,
        .bit_length = 16,
        .scale = 0.00003051757813,
        .offset = 0,
        .min = 0,
        .max = 2,
        .unit = "ratio"
    },
    {
        .name = "OxySensor3_FAER",        /* Oxygen sensor 3 (air-fuel equiv. ratio) */
        .class = "gas",
        .bit_start = 31,
        .bit_length = 16,
        .scale = 0.00003051757813,
        .offset = 0,
        .min = 0,
        .max = 2,
        .unit = "ratio"
    },
    {
        .name = "OxySensor4_FAER",        /* Oxygen sensor 4 (air-fuel equiv. ratio) */
        .class = "gas",
        .bit_start = 31,
        .bit_length = 16,
        .scale = 0.00003051757813,
        .offset = 0,
        .min = 0,
        .max = 2,
        .unit = "ratio"
    },
    {
        .name = "OxySensor5_FAER",        /* Oxygen sensor 5 (air-fuel equiv. ratio) */
        .class = "gas",
        .bit_start = 31,
        .bit_length = 16,
        .scale = 0.00003051757813,
        .offset = 0,
        .min = 0,
        .max = 2,
        .unit = "ratio"
    },
    {
        .name = "OxySensor6_FAER",        /* Oxygen sensor 6 (air-fuel equiv. ratio) */
        .class = "gas",
        .bit_start = 31,
        .bit_length = 16,
        .scale = 0.00003051757813,
        .offset = 0,
        .min = 0,
        .max = 2,
        .unit = "ratio"
    },
    {
        .name = "OxySensor7_FAER",        /* Oxygen sensor 7 (air-fuel equiv. ratio) */
        .class = "gas",
        .bit_start = 31,
        .bit_length = 16,
        .scale = 0.00003051757813,
        .offset = 0,
        .min = 0,
        .max = 2,
        .unit = "ratio"
    },
    {
        .name = "OxySensor8_FAER",        /* Oxygen sensor 8 (air-fuel equiv. ratio) */
        .class = "gas",
        .bit_start = 31,
        .bit_length = 16,
        .scale = 0.00003051757813,
        .offset = 0,
        .min = 0,
        .max = 2,
        .unit = "ratio"
    },
    {
        .name = "CatTempBank1Sens1",        /* Catalyst temperature (bank 1, sensor 1) */
        .class = "temperature",
        .bit_start = 31,
        .bit_length = 16,
        .scale = 0.1,
        .offset = -40,
        .min = -40,
        .max = 6514,
        .unit = "degC"
    },
    {
        .name = "CatTempBank2Sens1",        /* Catalyst temperature (bank 2, sensor 1) */
        .class = "temperature",
        .bit_start = 31,
        .bit_length = 16,
        .scale = 0.1,
        .offset = -40,
        .min = -40,
        .max = 6514,
        .unit = "degC"
    },
    {
        .name = "CatTempBank1Sens2",        /* Catalyst temperature (bank 1, sensor 2) */
        .class = "temperature",
        .bit_start = 31,
        .bit_length = 16,
        .scale = 0.1,
        .offset = -40,
        .min = -40,
        .max = 6514,
        .unit = "degC"
    },
    {
        .name = "CatTempBank2Sens2",        /* Catalyst temperature (bank 2, sensor 2) */
        .class = "temperature",
        .bit_start = 31,
        .bit_length = 16,
        .scale = 0.1,
        .offset = -40,
        .min = -40,
        .max = 6514,
        .unit = "degC"
    },
    {
        .name = "PIDsSupported_41_60",        /* PIDs supported [41 - 60] */
        .class = "enum",
        .bit_start = 31,
        .bit_length = 32,
        .scale = 1,
        .offset = 0,
        .min = 0,
        .max = 0,
        .unit = "Encoded"
    },
    {
        .name = "MonStatusDriveCycle",        /* Monitor status this drive cycle */
        .class = "enum",
        .bit_start = 31,
        .bit_length = 32,
        .scale = 1,
        .offset = 0,
        .min = 0,
        .max = 0,
        .unit = "Encoded"
    },
    {
        .name = "ControlModuleVolt",        /* Control module voltage */
        .class = "voltage",
        .bit_start = 31,
        .bit_length = 16,
        .scale = 0.001,
        .offset = 0,
        .min = 0,
        .max = 66,
        .unit = "V"
    },
    {
        .name = "AbsLoadValue",        /* Absolute load value */
        .class = "None",
        .bit_start = 31,
        .bit_length = 16,
        .scale = 0.3921568627,
        .offset = 0,
        .min = 0,
        .max = 25700,
        .unit = "%"
    },
    {
        .name = "FuelAirCmdEquiv",        /* Commanded air-fuel equiv. ratio  */
        .class = "gas",
        .bit_start = 31,
        .bit_length = 16,
        .scale = 0.00003051757813,
        .offset = 0,
        .min = 0,
        .max = 2,
        .unit = "ratio"
    },
    {
        .name = "RelThrottlePos",        /* Relative throttle position */
        .class = "None",
        .bit_start = 31,
        .bit_length = 8,
        .scale = 0.3921568627,
        .offset = 0,
        .min = 0,
        .max = 100,
        .unit = "%"
    },
    {
        .name = "AmbientAirTemp",        /* Ambient air temperature */
        .class = "temperature",
        .bit_start = 31,
        .bit_length = 8,
        .scale = 1,
        .offset = -40,
        .min = -40,
        .max = 215,
        .unit = "degC"
    },
    {
        .name = "AbsThrottlePosB",        /* Absolute throttle position B */
        .class = "None",
        .bit_start = 31,
        .bit_length = 8,
        .scale = 0.3921568627,
        .offset = 0,
        .min = 0,
        .max = 100,
        .unit = "%"
    },
    {
        .name = "AbsThrottlePosC",        /* Absolute throttle position C */
        .class = "None",
        .bit_start = 31,
        .bit_length = 8,
        .scale = 0.3921568627,
        .offset = 0,
        .min = 0,
        .max = 100,
        .unit = "%"
    },
    {
        .name = "AbsThrottlePosD",        /* Accelerator pedal position D */
        .class = "None",
        .bit_start = 31,
        .bit_length = 8,
        .scale = 0.3921568627,
        .offset = 0,
        .min = 0,
        .max = 100,
        .unit = "%"
    },
    {
        .name = "AbsThrottlePosE",        /* Accelerator pedal position E */
        .class = "None",
        .bit_start = 31,
        .bit_length = 8,
        .scale = 0.3921568627,
        .offset = 0,
        .min = 0,
        .max = 100,
        .unit = "%"
    },
    {
        .name = "AbsThrottlePosF",        /* Accelerator pedal position F */
        .class = "None",
        .bit_start = 31,
        .bit_length = 8,
        .scale = 0.3921568627,
        .offset = 0,
        .min = 0,
        .max = 100,
        .unit = "%"
    },
    {
        .name = "CmdThrottleAct",        /* Commanded throttle actuator */
        .class = "None",
        .bit_start = 31,
        .bit_length = 8,
        .scale = 0.3921568627,
        .offset = 0,
        .min = 0,
        .max = 100,
        .unit = "%"
    },
    {
        .name = "TimeRunMILOn",        /* Time run with MIL on */
        .class = "duration",
        .bit_start = 31,
        .bit_length = 16,
        .scale = 1,
        .offset = 0,
        .min = 0,
        .max = 65535,
        .unit = "minutes"
    },
    {
        .name = "TimeSinceCodeClear",        /* Time since DTCs cleared */
        .class = "duration",
        .bit_start = 31,
        .bit_length = 16,
        .scale = 1,
        .offset = 0,
        .min = 0,
        .max = 65535,
        .unit = "minutes"
    },
    {
        .name = "Max_FAER",        /* Max fuel-air equiv. ratio */
        .class = "gas",
        .bit_start = 31,
        .bit_length = 8,
        .scale = 1,
        .offset = 0,
        .min = 0,
        .max = 255,
        .unit = "ratio"
    },
    {
        .name = "Max_AirFlowMAF",        /* Max air flow rate from MAF sensor */
        .class = "volume_flow_rate",
        .bit_start = 31,
        .bit_length = 8,
        .scale = 10,
        .offset = 0,
        .min = 0,
        .max = 2550,
        .unit = "g/s"
    },
    {
        .name = "FuelType",        /* Fuel type */
        .class = "gas",
        .bit_start = 31,
        .bit_length = 8,
        .scale = 1,
        .offset = 0,
        .min = 0,
        .max = 0,
        .unit = "Encoded"
    },
    {
        .name = "EthanolFuelPct",        /* Ethanol fuel percentage */
        .class = "None",
        .bit_start = 31,
        .bit_length = 8,
        .scale = 0.3921568627,
        .offset = 0,
        .min = 0,
        .max = 100,
        .unit = "%"
    },
    {
        .name = "AbsEvapSysVapPres",        /* Absolute evap system vapor pressure */
        .class = "pressure",
        .bit_start = 31,
        .bit_length = 16,
        .scale = 0.005,
        .offset = 0,
        .min = 0,
        .max = 328,
        .unit = "kPa"
    },
    {
        .name = "EvapSysVapPres",        /* Evap system vapor pressure */
        .class = "pressure",
        .bit_start = 31,
        .bit_length = 16,
        .scale = 1,
        .offset = -32767,
        .min = -32767,
        .max = 32768,
        .unit = "Pa"
    },
    {
        .name = "ShortSecOxyTrimBank1",        /* Short term sec. oxygen trim (bank 1) */
        .class = "None",
        .bit_start = 31,
        .bit_length = 8,
        .scale = 0.78125,
        .offset = -100,
        .min = -100,
        .max = 99,
        .unit = "%"
    },
    {
        .name = "LongSecOxyTrimBank1",        /* Long term sec. oxygen trim (bank 1) */
        .class = "None",
        .bit_start = 31,
        .bit_length = 8,
        .scale = 0.78125,
        .offset = -100,
        .min = -100,
        .max = 99,
        .unit = "%"
    },
    {
        .name = "ShortSecOxyTrimBank2",        /* Short term sec. oxygen trim (bank 2) */
        .class = "None",
        .bit_start = 31,
        .bit_length = 8,
        .scale = 0.78125,
        .offset = -100,
        .min = -100,
        .max = 99,
        .unit = "%"
    },
    {
        .name = "LongSecOxyTrimBank2",        /* Long term sec. oxygen trim (bank 2) */
        .class = "None",
        .bit_start = 31,
        .bit_length = 8,
        .scale = 0.78125,
        .offset = -100,
        .min = -100,
        .max = 99,
        .unit = "%"
    },
    {
        .name = "FuelRailAbsPres",        /* Fuel rail absolute pressure */
        .class = "pressure",
        .bit_start = 31,
        .bit_length = 16,
        .scale = 10,
        .offset = 0,
        .min = 0,
        .max = 655350,
        .unit = "kPa"
    },
    {
        .name = "RelAccelPedalPos",        /* Relative accelerator pedal position */
        .class = "None",
        .bit_start = 31,
        .bit_length = 8,
        .scale = 0.3921568627,
        .offset = 0,
        .min = 0,
        .max = 100,
        .unit = "%"
    },
    {
        .name = "HybrBatPackRemLife",        /* Hybrid battery pack remaining life */
        .class = "battery",
        .bit_start = 31,
        .bit_length = 8,
        .scale = 0.3921568627,
        .offset = 0,
        .min = 0,
        .max = 100,
        .unit = "%"
    },
    {
        .name = "EngineOilTemp",        /* Engine oil temperature */
        .class = "temperature",
        .bit_start = 31,
        .bit_length = 8,
        .scale = 1,
        .offset = -40,
        .min = -40,
        .max = 215,
        .unit = "degC"
    },
    {
        .name = "FuelInjectionTiming",        /* Fuel injection timing */
        .class = "gas",
        .bit_start = 31,
        .bit_length = 16,
        .scale = 0.0078125,
        .offset = -210,
        .min = -210,
        .max = 302,
        .unit = "deg"
    },
    {
        .name = "EngineFuelRate",        /* Engine fuel rate */
        .class = "gas",
        .bit_start = 31,
        .bit_length = 16,
        .scale = 0.05,
        .offset = 0,
        .min = 0,
        .max = 3277,
        .unit = "L/h"
    },
    {
        .name = "EmissionReq",        /* Emission requirements */
        .class = "enum",
        .bit_start = 31,
        .bit_length = 8,
        .scale = 1,
        .offset = 0,
        .min = 0,
        .max = 0,
        .unit = "Encoded"
    },
    {
        .name = "PIDsSupported_61_80",        /* PIDs supported [61 - 80] */
        .class = "enum",
        .bit_start = 31,
        .bit_length = 32,
        .scale = 1,
        .offset = 0,
        .min = 0,
        .max = 0,
        .unit = "Encoded"
    },
    {
        .name = "DemandEngTorqPct",        /* Demanded engine percent torque */
        .class = "None",
        .bit_start = 31,
        .bit_length = 8,
        .scale = 1,
        .offset = -125,
        .min = -125,
        .max = 130,
        .unit = "%"
    },
    {
        .name = "ActualEngTorqPct",        /* Actual engine percent torque */
        .class = "None",
        .bit_start = 31,
        .bit_length = 8,
        .scale = 1,
        .offset = -125,
        .min = -125,
        .max = 130,
        .unit = "%"
    },
    {
        .name = "EngRefTorq",        /* Engine reference torque */
        .class = "None",
        .bit_start = 31,
        .bit_length = 16,
        .scale = 1,
        .offset = 0,
        .min = 0,
        .max = 65535,
        .unit = "Nm"
    },
    {
        .name = "EngPctTorq_Idle",        /* Engine pct. torque (idle) */
        .class = "None",
        .bit_start = 31,
        .bit_length = 8,
        .scale = 1,
        .offset = -125,
        .min = -125,
        .max = 130,
        .unit = "%"
    },
    {
        .name = "AuxInputOutput",        /* Auxiliary input/output supported */
        .class = "enum",
        .bit_start = 31,
        .bit_length = 8,
        .scale = 1,
        .offset = 0,
        .min = 0,
        .max = 0,
        .unit = "Encoded"
    },
    {
        .name = "MAFSensorA",        /* Mass air flow sensor (A) */
        .class = "volume_flow_rate",
        .bit_start = 39,
        .bit_length = 16,
        .scale = 0.03125,
        .offset = 0,
        .min = 0,
        .max = 2048,
        .unit = "grams/sec"
    },
    {
        .name = "EngineCoolantTemp1",        /* Engine coolant temperature (sensor 1) */
        .class = "temperature",
        .bit_start = 39,
        .bit_length = 8,
        .scale = 1,
        .offset = -40,
        .min = -40,
        .max = 215,
        .unit = "degC"
    },
    {
        .name = "IntakeAirTempSens1",        /* Intake air temperature (sensor 1) */
        .class = "temperature",
        .bit_start = 39,
        .bit_length = 8,
        .scale = 1,
        .offset = -40,
        .min = -40,
        .max = 215,
        .unit = "degC"
    },
    {
        .name = "CmdEGR_EGRError",        /* Commanded EGR and EGR error */
        .class = "None",
        .bit_start = 0,
        .bit_length = 0,
        .scale = 0,
        .offset = 0,
        .min = 0,
        .max = 0,
        .unit = ""
    },
    {
        .name = "CmdDieselIntAir",        /* Com. diesel intake air flow ctr/position */
        .class = "volume_flow_rate",
        .bit_start = 0,
        .bit_length = 0,
        .scale = 0,
        .offset = 0,
        .min = 0,
        .max = 0,
        .unit = ""
    },
    {
        .name = "ExhaustGasTemp",        /* Exhaust gas recirculation temperature */
        .class = "temperature",
        .bit_start = 0,
        .bit_length = 0,
        .scale = 0,
        .offset = 0,
        .min = 0,
        .max = 0,
        .unit = ""
    },
    {
        .name = "CmdThrottleActRel",        /* Com. throttle actuator ctr./position */
        .class = "None",
        .bit_start = 0,
        .bit_length = 0,
        .scale = 0,
        .offset = 0,
        .min = 0,
        .max = 0,
        .unit = ""
    },
    {
        .name = "FuelPresContrSys",        /* Fuel pressure control system */
        .class = "pressure",
        .bit_start = 0,
        .bit_length = 0,
        .scale = 0,
        .offset = 0,
        .min = 0,
        .max = 0,
        .unit = ""
    },
    {
        .name = "InjPresContrSys",        /* Injection pressure control system */
        .class = "pressure",
        .bit_start = 0,
        .bit_length = 0,
        .scale = 0,
        .offset = 0,
        .min = 0,
        .max = 0,
        .unit = ""
    },
    {
        .name = "TurboComprPres",        /* Turbocharger compressor inlet pres. */
        .class = "None",
        .bit_start = 0,
        .bit_length = 0,
        .scale = 0,
        .offset = 0,
        .min = 0,
        .max = 0,
        .unit = ""
    },
    {
        .name = "BoostPresCntrl",        /* Boost pressure control */
        .class = "pressure",
        .bit_start = 0,
        .bit_length = 0,
        .scale = 0,
        .offset = 0,
        .min = 0,
        .max = 0,
        .unit = ""
    },
    {
        .name = "VariableGeoTurboVGTCtr",        /* Variable geometry turbo control */
        .class = "None",
        .bit_start = 0,
        .bit_length = 0,
        .scale = 0,
        .offset = 0,
        .min = 0,
        .max = 0,
        .unit = ""
    },
    {
        .name = "WastegateControl",        /* Wastegate control */
        .class = "None",
        .bit_start = 0,
        .bit_length = 0,
        .scale = 0,
        .offset = 0,
        .min = 0,
        .max = 0,
        .unit = ""
    },
    {
        .name = "ExhaustPressure",        /* Exhaust pressure */
        .class = "pressure",
        .bit_start = 0,
        .bit_length = 0,
        .scale = 0,
        .offset = 0,
        .min = 0,
        .max = 0,
        .unit = ""
    },
    {
        .name = "TurbochargerRpm",        /* Turbocharger RPM */
        .class = "speed",
        .bit_start = 0,
        .bit_length = 0,
        .scale = 0,
        .offset = 0,
        .min = 0,
        .max = 0,
        .unit = ""
    },
    {
        .name = "TurbochargerTemperature",        /* Turbocharger temperature */
        .class = "temperature",
        .bit_start = 0,
        .bit_length = 0,
        .scale = 0,
        .offset = 0,
        .min = 0,
        .max = 0,
        .unit = ""
    },
    {
        .name = "TurbochargerTemperature",        /* Turbocharger temperature */
        .class = "temperature",
        .bit_start = 0,
        .bit_length = 0,
        .scale = 0,
        .offset = 0,
        .min = 0,
        .max = 0,
        .unit = ""
    },
    {
        .name = "ChargeAirCoolerTemperature",        /* Charge air cooler temperature */
        .class = "temperature",
        .bit_start = 0,
        .bit_length = 0,
        .scale = 0,
        .offset = 0,
        .min = 0,
        .max = 0,
        .unit = ""
    },
    {
        .name = "EGT_Bank1",        /* EGT (bank 1) */
        .class = "None",
        .bit_start = 0,
        .bit_length = 0,
        .scale = 0,
        .offset = 0,
        .min = 0,
        .max = 0,
        .unit = ""
    },
    {
        .name = "EGT_Bank2",        /* EGT (bank 2) */
        .class = "None",
        .bit_start = 0,
        .bit_length = 0,
        .scale = 0,
        .offset = 0,
        .min = 0,
        .max = 0,
        .unit = ""
    },
    {
        .name = "DPF_DifferentialPressure",        /* Diesel particulate filter - diff. pressure */
        .class = "pressure",
        .bit_start = 0,
        .bit_length = 0,
        .scale = 0,
        .offset = 0,
        .min = 0,
        .max = 0,
        .unit = ""
    },
    {
        .name = "DPF",        /* Diesel particulate filter */
        .class = "None",
        .bit_start = 0,
        .bit_length = 0,
        .scale = 0,
        .offset = 0,
        .min = 0,
        .max = 0,
        .unit = ""
    },
    {
        .name = "DPF_Temperature",        /* Diesel particulate filter - temperature */
        .class = "temperature",
        .bit_start = 31,
        .bit_length = 16,
        .scale = 0.1,
        .offset = -40,
        .min = -40,
        .max = 6514,
        .unit = "degC"
    },
    {
        .name = "NOx_NTE_ControlAreaStatus",        /* NOx NTE control area status */
        .class = "None",
        .bit_start = 0,
        .bit_length = 0,
        .scale = 0,
        .offset = 0,
        .min = 0,
        .max = 0,
        .unit = ""
    },
    {
        .name = "PM_NTE_ControlAreaStatus",        /* PM NTE control area status */
        .class = "None",
        .bit_start = 0,
        .bit_length = 0,
        .scale = 0,
        .offset = 0,
        .min = 0,
        .max = 0,
        .unit = ""
    },
    {
        .name = "EngineRunTime",        /* Engine run time */
        .class = "duration",
        .bit_start = 0,
        .bit_length = 0,
        .scale = 0,
        .offset = 0,
        .min = 0,
        .max = 0,
        .unit = "seconds"
    },
    {
        .name = "PIDsSupported_81_A0",        /* PIDs supported [81 - A0] */
        .class = "enum",
        .bit_start = 31,
        .bit_length = 32,
        .scale = 1,
        .offset = 0,
        .min = 0,
        .max = 0,
        .unit = "Encoded"
    },
    {
        .name = "EngineRunTime_AECD",        /* Engine run time for AECD */
        .class = "duration",
        .bit_start = 0,
        .bit_length = 0,
        .scale = 0,
        .offset = 0,
        .min = 0,
        .max = 0,
        .unit = ""
    },
    {
        .name = "EngineRunTime_AECD",        /* Engine run time for AECD */
        .class = "duration",
        .bit_start = 0,
        .bit_length = 0,
        .scale = 0,
        .offset = 0,
        .min = 0,
        .max = 0,
        .unit = ""
    },
    {
        .name = "NOxSensor",        /* NOx sensor */
        .class = "None",
        .bit_start = 0,
        .bit_length = 0,
        .scale = 0,
        .offset = 0,
        .min = 0,
        .max = 0,
        .unit = ""
    },
    {
        .name = "ManifoldSurfaceTemperature",        /* Manifold surface temperature */
        .class = "temperature",
        .bit_start = 0,
        .bit_length = 0,
        .scale = 0,
        .offset = 0,
        .min = 0,
        .max = 0,
        .unit = ""
    },
    {
        .name = "NOxReagentSystem",        /* NOx reagent system */
        .class = "None",
        .bit_start = 0,
        .bit_length = 0,
        .scale = 0,
        .offset = 0,
        .min = 0,
        .max = 0,
        .unit = ""
    },
    {
        .name = "PM_Sensor",        /* Particulate matter sensor */
        .class = "None",
        .bit_start = 0,
        .bit_length = 0,
        .scale = 0,
        .offset = 0,
        .min = 0,
        .max = 0,
        .unit = ""
    },
    {
        .name = "IntakeManifoldAbsolutePressure",        /* Intake manifold absolute pressure */
        .class = "pressure",
        .bit_start = 0,
        .bit_length = 0,
        .scale = 0,
        .offset = 0,
        .min = 0,
        .max = 0,
        .unit = ""
    },
    {
        .name = "SCR_InduceSystem",        /* SCR induce system */
        .class = "None",
        .bit_start = 0,
        .bit_length = 0,
        .scale = 0,
        .offset = 0,
        .min = 0,
        .max = 0,
        .unit = ""
    },
    {
        .name = "RunTimeForAECD_11_15",        /* Run time for AECD #11-#15 */
        .class = "duration",
        .bit_start = 0,
        .bit_length = 0,
        .scale = 0,
        .offset = 0,
        .min = 0,
        .max = 0,
        .unit = ""
    },
    {
        .name = "RunTimeForAECD_16_20",        /* Run time for AECD #16-#20 */
        .class = "duration",
        .bit_start = 0,
        .bit_length = 0,
        .scale = 0,
        .offset = 0,
        .min = 0,
        .max = 0,
        .unit = ""
    },
    {
        .name = "DieselAftertreatment",        /* Diesel aftertreatment */
        .class = "None",
        .bit_start = 0,
        .bit_length = 0,
        .scale = 0,
        .offset = 0,
        .min = 0,
        .max = 0,
        .unit = ""
    },
    {
        .name = "O2Sensor_WideRange",        /* O2 sensor (wide range) */
        .class = "None",
        .bit_start = 0,
        .bit_length = 0,
        .scale = 0,
        .offset = 0,
        .min = 0,
        .max = 0,
        .unit = ""
    },
    {
        .name = "ThrottlePositionG",        /* Throttle position G */
        .class = "None",
        .bit_start = 31,
        .bit_length = 8,
        .scale = 0.3921568627,
        .offset = 0,
        .min = 0,
        .max = 100,
        .unit = "%"
    },
    {
        .name = "EngineFrictionPercentTorque",        /* Engine friction percent torque */
        .class = "None",
        .bit_start = 31,
        .bit_length = 8,
        .scale = 1,
        .offset = -125,
        .min = -125,
        .max = 130,
        .unit = "%"
    },
    {
        .name = "PMSensorBank1_2",        /* Particulate matter sensor (bank 1 & 2) */
        .class = "None",
        .bit_start = 0,
        .bit_length = 0,
        .scale = 0,
        .offset = 0,
        .min = 0,
        .max = 0,
        .unit = ""
    },
    {
        .name = "WWH_OBD_SysInfo",        /* WWH-OBD vehicle OBD system Info */
        .class = "None",
        .bit_start = 0,
        .bit_length = 0,
        .scale = 0,
        .offset = 0,
        .min = 0,
        .max = 0,
        .unit = "hours"
    },
    {
        .name = "WWH_OBD_SysInfo",        /* WWH-OBD vehicle OBD system Info */
        .class = "None",
        .bit_start = 0,
        .bit_length = 0,
        .scale = 0,
        .offset = 0,
        .min = 0,
        .max = 0,
        .unit = "hours"
    },
    {
        .name = "FuelSystemControl",        /* Fuel system control */
        .class = "gas",
        .bit_start = 0,
        .bit_length = 0,
        .scale = 0,
        .offset = 0,
        .min = 0,
        .max = 0,
        .unit = ""
    },
    {
        .name = "WWH_OBD_CtrSupport",        /* WWH-OBD counters support */
        .class = "None",
        .bit_start = 0,
        .bit_length = 0,
        .scale = 0,
        .offset = 0,
        .min = 0,
        .max = 0,
        .unit = "hours"
    },
    {
        .name = "NOxWarningInducementSys",        /* NOx warning and inducement system */
        .class = "None",
        .bit_start = 0,
        .bit_length = 0,
        .scale = 0,
        .offset = 0,
        .min = 0,
        .max = 0,
        .unit = ""
    },
    {
        .name = "EGT_Sensor",        /* EGT sensor */
        .class = "None",
        .bit_start = 0,
        .bit_length = 0,
        .scale = 0,
        .offset = 0,
        .min = 0,
        .max = 0,
        .unit = ""
    },
    {
        .name = "EGT_Sensor",        /* EGT sensor */
        .class = "None",
        .bit_start = 0,
        .bit_length = 0,
        .scale = 0,
        .offset = 0,
        .min = 0,
        .max = 0,
        .unit = ""
    },
    {
        .name = "Hybrid_EV_System",        /* Hybrid/EV sys. data, battery, voltage */
        .class = "voltage",
        .bit_start = 0,
        .bit_length = 0,
        .scale = 0,
        .offset = 0,
        .min = 0,
        .max = 0,
        .unit = ""
    },
    {
        .name = "DieselExhaustFluidSensorData",        /* Diesel exhaust fluid sensor data */
        .class = "None",
        .bit_start = 0,
        .bit_length = 0,
        .scale = 0,
        .offset = 0,
        .min = 0,
        .max = 0,
        .unit = ""
    },
    {
        .name = "O2SensorData",        /* O2 sensor data */
        .class = "None",
        .bit_start = 0,
        .bit_length = 0,
        .scale = 0,
        .offset = 0,
        .min = 0,
        .max = 0,
        .unit = ""
    },
    {
        .name = "EngineFuelRate",        /* Engine fuel rate */
        .class = "gas",
        .bit_start = 0,
        .bit_length = 0,
        .scale = 0,
        .offset = 0,
        .min = 0,
        .max = 0,
        .unit = "g/s"
    },
    {
        .name = "EngineExhaustFlowRate",        /* Engine exhaust flow rate */
        .class = "volume_flow_rate",
        .bit_start = 0,
        .bit_length = 0,
        .scale = 0,
        .offset = 0,
        .min = 0,
        .max = 0,
        .unit = "kg/h"
    },
    {
        .name = "FuelSystemPercentageUse",        /* Fuel system percentage use */
        .class = "temperature",
        .bit_start = 0,
        .bit_length = 0,
        .scale = 0,
        .offset = 0,
        .min = 0,
        .max = 0,
        .unit = ""
    },
    {
        .name = "PIDsSupported_A1_C0",        /* PIDs supported [A1 - C0] */
        .class = "enum",
        .bit_start = 31,
        .bit_length = 32,
        .scale = 1,
        .offset = 0,
        .min = 0,
        .max = 0,
        .unit = "Encoded"
    },
    {
        .name = "NOxSensorCorrectedData",        /* NOx sensor corrected data */
        .class = "None",
        .bit_start = 0,
        .bit_length = 0,
        .scale = 0,
        .offset = 0,
        .min = 0,
        .max = 0,
        .unit = "ppm"
    },
    {
        .name = "CylinderFuelRate",        /* Cylinder fuel rate */
        .class = "gas",
        .bit_start = 31,
        .bit_length = 16,
        .scale = 0.03125,
        .offset = 0,
        .min = 0,
        .max = 2048,
        .unit = "mg/stroke"
    },
    {
        .name = "EvapSystemVaporPressure",        /* Evap system vapor pressure */
        .class = "pressure",
        .bit_start = 0,
        .bit_length = 0,
        .scale = 0,
        .offset = 0,
        .min = 0,
        .max = 0,
        .unit = ""
    },
    {
        .name = "TransmissionActualGear",        /* Transmission actual gear */
        .class = "None",
        .bit_start = 47,
        .bit_length = 16,
        .scale = 0.001,
        .offset = 0,
        .min = 0,
        .max = 66,
        .unit = "ratio"
    },
    {
        .name = "ComDieselExhaustFluidDosing",        /* Cmd. diesel exhaust fluid dosing */
        .class = "None",
        .bit_start = 39,
        .bit_length = 8,
        .scale = 0.5,
        .offset = 0,
        .min = 0,
        .max = 128,
        .unit = "%"
    },
    {
        .name = "Odometer",        /* Odometer */
        .class = "distance",
        .bit_start = 31,
        .bit_length = 32,
        .scale = 0.1,
        .offset = 0,
        .min = 0,
        .max = 429000000,
        .unit = "km"
    },
    {
        .name = "NOxSensorConc3_4",        /* NOx concentration 3, 4 */
        .class = "None",
        .bit_start = 0,
        .bit_length = 0,
        .scale = 0,
        .offset = 0,
        .min = 0,
        .max = 0,
        .unit = ""
    },
    {
        .name = "NOxSensorCorrectConc3_4",        /* NOx corrected concentration (3, 4) */
        .class = "None",
        .bit_start = 0,
        .bit_length = 0,
        .scale = 0,
        .offset = 0,
        .min = 0,
        .max = 0,
        .unit = ""
    },
    {
        .name = "PIDsSupported_C1_E0",        /* PIDs supported [C1 - E0] */
        .class = "enum",
        .bit_start = 31,
        .bit_length = 32,
        .scale = 1,
        .offset = 0,
        .min = 0,
        .max = 0,
        .unit = "Encoded"
    },
};

#ifdef __cplusplus
}
#endif

#endif /* OBD2_PIDS_H */
