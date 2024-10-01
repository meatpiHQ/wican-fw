# Profiles for Jaguar Vehicles

## I-PACE

This profile is based off the work of [dernotte](https://github.com/dernotte) for the [I-PACE component of OVMS3](https://github.com/openvehicles/Open-Vehicle-Monitoring-System-3/tree/master/vehicle/OVMS.V3/components/vehicle_jaguaripace). See [here](https://github.com/openvehicles/Open-Vehicle-Monitoring-System-3/blob/master/vehicle/OVMS.V3/components/vehicle_jaguaripace/src/ipace_obd_pids.h) for the list of indentified PIDs. This [OVMS forum thread](https://www.openvehicles.com/node/2423) shows some of the preliminary work prior to the OVMS component along with some other contributors.

Only the PIDs associated with the Battery Energy Control Module (BECM - header 7E4) are currently supported. Attempts to reach the other modules (e.g. HVAC, TPMS) results in a timeout error. The VIN PID on the BECM is also not supported - it is a multi frame message.

These are confirmed working on a model year 2019 I-PACE.
