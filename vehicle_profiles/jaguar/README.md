# Profiles for Jaguar Vehicles

## I-PACE

This profile is based off the work of [dernotte](https://github.com/dernotte) for the [I-PACE component of OVMS3](https://github.com/openvehicles/Open-Vehicle-Monitoring-System-3/tree/master/vehicle/OVMS.V3/components/vehicle_jaguaripace). See [here](https://github.com/openvehicles/Open-Vehicle-Monitoring-System-3/blob/master/vehicle/OVMS.V3/components/vehicle_jaguaripace/src/ipace_obd_pids.h) for the list of indentified PIDs. This [OVMS forum thread](https://www.openvehicles.com/node/2423) shows some of the preliminary work prior to the OVMS component along with some other contributors. 

Additional resources:
- I-PACE PID [spreadsheet](https://docs.google.com/spreadsheets/d/1wNMtpPqMAejNeOZGsPCcgau8HODROzceFcUSfk2lVz8/) maintained by [dernotte](https://github.com/dernotte).
- I-PACE forum [thread](https://www.i-paceforum.com/threads/hv-battery-reports-autel-scanner-tool-can-do-it.8773/) for idenditfying PIDs for HV battery reports.

Not all of the PIDs are currently supported. Attempts to reach some modules (e.g. HVAC, TPMS) results in a timeout error. The VIN and location PIDs are also not supported - they are a multi frame message.

### PID Listing

These are confirmed working on a model year 2019 I-PACE.

|Supported|Name|PID|Module|Description|Notes|
|:-------:|----|---|------|-----------|-----|
|✅|batterySoC|4910|BECM (7E4)| The "true" HV battery state of charge||
|✅|batterySoCMin|4911|BECM (7E4)|||
|✅|batterySoCMax|4914|BECM (7E4)|||
|✅|batterySoHCapacity|4918|BECM (7E4)|||
|✅|batterySoHCapacityMin|4919|BECM (7E4)|||
|✅|batterySoHCapacityMax|491A|BECM (7E4)|||
|✅|batterySoHPower|4915|BECM (7E4)|||
|✅|batterySoHPowerMin|4916|BECM (7E4)|||
|✅|batterySoHPowerMax|4917|BECM (7E4)|||
|✅|batteryCellMinVolt|4904|BECM (7E4)|Minimum voltage of any of the 108 HV battery cells||
|✅|batteryCellMaxVolt|4903|BECM (7E4)|Maximum voltage of any of the 108 HV battery cells||
|✅|batteryVolt|490F|BECM (7E4)|Voltage of HV battery||
|✅|batteryCurrent|490C|BECM (7E4)|Current of HV battery|Positive is discharging|
|✅|batteryTempMin|4906|BECM (7E4)|Minimum temperature of any of the 108 HV battery cells||
|✅|batteryTempMax|4905|BECM (7E4)|Maximum temperature of any of the 108 HV battery cells||
|✅|batteryTempAvg|4907|BECM (7E4)|Average temperature of any of the 108 HV battery cells||
|✅|batteryMaxRegen|4913|BECM (7E4)|Maximum allowed regen rate (kW)||
|✅|batteryCoolantOutletTemp|491B|BECM (7E4)|Battery Coolant Outlet Temperature (°C)||
|✅|batteryCoolantInletTemp|491C|BECM (7E4)|Battery Coolant Inlet Temperature (°C)||
|✅|odometer|DD01|BECM (7E4)|||
|✅|cabinTemp|DD04|BECM (7E4)|||
|✅|vehicleSpeed|DD09|BECM (7E4)|Current speed in km/h||
|❌|frontLeftTirePressure|2076|TPMS (751)|Front Left Tire Pressure (kPa)|pid timeout|
|❌|frontRightTirePressure|2077|TPMS (751)|Front Right Tire Pressure (kPa)|pid timeout|
|❌|rearLeftTirePressure|2078|TPMS (751)|Rear Left Tire Pressure (kPa)|pid timeout|
|❌|rearRightTirePressure|2079|TPMS (751)|Rear Right Tire Pressure (kPa)|pid timeout|
|❌|frontLeftTireTemp|2A0A|TPMS (751)|Front Left Tire Temperature (°C)|pid timeout|
|❌|frontRightTireTemp|2A0B|TPMS (751)|Front Right Tire Temperature (°C)|pid timeout|
|❌|rearLeftTireTemp|2A0C|TPMS (751)|Rear Left Tire Temperature (°C)|pid timeout|
|❌|rearRighttTireTemp|2A0D|TPMS (751)|Rear Right Tire Temperature (°C)|pid timeout|
|❌|ambientTemp|9924|HVAC (733)|Ambient Temperature (°C)|pid timeout|
|✅|mainsACVoltage|498F|BCCM (7E5)|Voltage provided by EVSE (V)||
|✅|mainsACCurrent|498F|BCCM (7E5)|Current drawn from EVSE (A)||
|❌|controlPilotPWMDutyCycle|49A8|BCCM (7E5)|EVSE Control Pilot Duty Cycle (%)|formula issue|
|❌|controlPilotFrequency|49A8|BCCM (7E5)|EVSE Control Pilot Frequency (Hz)|formula issue|
|❌|controlPilotStatusCode|49A8|BCCM (7E5)|EVSE Control Pilot Status Code|formula issue|
|❌|controlPilotVoltage|49A8|BCCM (7E5)|EVSE Control Pilot Voltage (V)|formula issue|
|❌|evseMaxCurrent|49A8|BCCM (7E5)|Calculated EVSE Max Current (A)|formula issue|
|❌|DCDCOutputCurrent|4208|DCDC (753)|DCDC 12V Supply Output Current (A)|pid timeout|
|❌|DCDCOutputVoltage|420A|DCDC (753)|DCDC 12V Supply Output Voltage (A)|pid timeout|
|❌|vin|F190|BECM (7E4)|Vehicle Idenification Number|Multi frame message|
|❌|location|A0A6|TCU (754)|GPS Location|Multi frame message|
