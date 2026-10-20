### Update instructions
- Firmware 6.x: open the device page, System > Firmware update > Check for updates, or upload `wican-fw_obd_pro_v{version}.bin` by hand.
- Firmware 4.x: open the configuration page, System tab, Firmware Update section, upload `wican-fw_obd_pro_v{version}.bin` and press Update. The first boot restarts once more; see Before you update.
- Home Assistant: the MeatPi integration offers this version in the Firmware update entity.

### In case of a boot loop
Perform a [factory reset](https://meatpihq.github.io/wican-fw/factory-reset/factory-reset).

### Home Assistant
Firmware 6.x requires the [meatpi-multi-device](https://github.com/meatpiHQ/ha-wican/tree/meatpi-multi-device) branch of the ha-wican integration.
