# Some Footnotes for creating profile for Volvo XC40

### Among platform

Some online references seems working on older Volvo, but not CMA
For example:
Info from https://www.volvov40club.com/threads/volvo-service.35611/page-2?post_id=297437#post-297437 look promising
[VCC]TotalDistance","ODO","0x22dd01","int24(A:B:C)",0,1000000,"km","","","",1

However studying ELM327 log from Car Scanning

PID: 22DD011 @ 30733km

> 1EC6EE80 0662DD01 00780D

PID: 22DD011 @ 30776km

> 1EC6EE80 0662DD01 007838

That concluded "expression" for odo is "([B4:B7])/256"

Current Status for XC40 BEV.json

- Tested on XC40 BEV P8 MY24
- For SOC (known as [BECM] HV Battery SOC), confirmed PID 2240281 but expression is still missing. Please use "Standard PID - 5B-HybdBatPackRemLife" introduced since Firmware 4.0 as workaround

Also check with volvo2mqtt (but just shocked as project discontinued few days ago (Jan 02, 2025) https://github.com/Dielee/volvo2mqtt ) but there are new integration for HA, if your region support.
