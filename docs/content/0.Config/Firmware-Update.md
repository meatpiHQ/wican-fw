# Firmware Update

## 1. OTA:
1. Download the latest release version, or compile your own.
1. Go the device [configuration page](#device-configuration).
2. Click on the "About" tab.
3. Click on "Choose File".
4. Select the binary file. Eample: wican-fw_v130.bin
5. Click update, update should take about 30sec.

**NOTE: After flashing, the device configuration might be erased.**

**Note: for firmware version v1.00 use USB cable to flash the unit.**

![image](/config/firmware/about.png)

## 2. USB Flash:

Use the [**ESP flash tool**](https://www.espressif.com/en/support/download/other-tools) to update the firmware, just follow the same setting in the picture below. Make sure to select ESP32-C3 and USB mode. **esptool.py** also can also be used to flash a new firmware.
1. Download [**ESP flash tool**](https://www.espressif.com/en/support/download/other-tools)
2. Download the latest firmware zip file from the [**releases**](https://github.com/meatpiHQ/wican-fw/releases) page. 
3. Select ESP32C3, develop and USB then click ok

![image](/config/firmware/flash-select.png)

4. Set the configuration as the picture below, select and fill in the address for each binary.

![image](/config/firmware/flash-config.png)

5. Short the pins as shown, then plug in the USB cable.

### OBD 
![image](/config/firmware/obd.png)

### USB
![image](/config/firmware/usb.png)


6. After you plug in the USB cable the Orange LED will light up. Now click START button on the flash tool.

**NOTE: After flashing, the device configuration might be erased.**

