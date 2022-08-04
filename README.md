<img src="https://github.com/slimelec/ollie-hw/blob/master/images/mpi_logo.png" width=300>

[www.meatpi.com](https://www.meatpi.com)
---
<br/><br/>

## Launching on [**Crowd Supply**](https://www.crowdsupply.com/meatpi-electronics/wican) soon click [**here**](https://www.crowdsupply.com/meatpi-electronics/wican) to subscribe for notification. 

<br/><br/>

---

<p align="center">
<img src="https://user-images.githubusercontent.com/94690098/182027614-9d580e96-2a8e-4fe3-8672-bb6a6fd616f2.png" >
</p>

## WiCAN-USB Pinout

<p align="center">
<img src="https://user-images.githubusercontent.com/94690098/182854398-6d143970-f07d-4a5f-ae9f-35ac566af170.png" >
</p>

## WiCAN-OBD2 Pinout

<p align="center">
<img src="https://user-images.githubusercontent.com/94690098/182854687-911bae04-9bdd-4947-8363-e088e278b3b8.png" >
</p>

---

- [Important Notes](#important-notes)
- [Build](#build)
- [Description](#description)
- [Features](#features)
  - [Sleep mode](#1-sleep-mode)
  - [Battery Alert](#2-battery-alert)
- [Configuration](#configuration)
  - [WiFi/CAN Configuration](#1-wifican-configuration)
  - [BUSMaster](#2-busmaster)
  - [Realdash](#3-realdash)
- [Firmware Update](#firmware-update)
  - [OTA](#1-ota)
  - [USB](#2-usb)

**Note: This is the intial release although all the functions and features work, optimization/clean up are still required.**
# **Important Notes**:

- The OBD2 adapter is not designed to powered of the USB connecter. The USB connector can power the adapter to flash custom firmware or hardreset the deivce and can also be used for debuging.
- It is highly recommanded to turn OFF the BLE if not used. Otherwise it might affect the preformance.
- When the BLE is connected, the device configuration access point will be disabled i.e you won't be able to configure the device unless you disconnect the BLE, by turning off the BLE on your phone or device.

# **Build**:

1. Install ESP-IDF v4.4.
2. Clone porject.
3. Open project and build.

# **Description**:

WiCAN is a **OBD-II WiFi/BLE** to **CAN** adapter based on **ESP32-C3**, it is the great tool for car hacking. The original firmware can interface directly with RealDash over WiFI or BLE. [**Realdash**](http://realdash.net/) allows you to create your own custom dashboard with stunning graphics, it's available on ANDROID, IOS AND WINDOWS 10.It can connect to your existing WiFi network or any device can connect to it's **WiFi access point**.

WiCAN also works with **BUSMaster**, for monitoring and logging CAN bus traffic.

**Also available API for LabView, C#, VB.Net, Delphi, Python** - In case you're writing your own software.

WiFi and CAN configuration can be set configuration web server.


## [**Programming Examples**](https://github.com/meatpiHQ/programming_examples/tree/master/CAN)

### **Features and Specifications**:

- Supports CAN2.0A/B up to 1Mbits.
- Works with Realdash, based on "realdash 66"
- Supports SocketCAN and works with BUSMaster
- Supports TCP and UDP
- WiFi can be used in AP and station mode
- WiFi and CAN configured using web interface.
- Diode protection for the USB port

![Modes](https://github.com/meatpiHQ/WiCAN/blob/main/images/modes.PNG?raw=true "Connection Mode" )

# Features:
--------

## 1. Sleep Mode:

WiCAN can be permanently attached to the car without worrying about draining the battery. It monitors the battery voltage and can detect when the alternator is ON. When the engine is ON the battery continuously charging and the voltage will be about 13.5V(can vary with different car models). When the engine is OFF the battery voltage drops to about 12.8V(full battery) -  WiCAN can detect this drop, and if the voltage remains under the threshold for **3 min** it will go to sleep and the current consumption will drop below **1mA**. If the engine is turned ON it will immediately wake up and enable the WiFi/BLE. 
The threshold voltage is configurable and can be set in the configuration page, the default value is 13V. 

## 2. Battery Alert:

This is an important feature for most car enthusiast who own multiple cars that are only driven few times a year.  Basically if a car is not used in few month the battery will go flat and needs to be jumped. WiCAN can be configured to send an alert when the battery voltage drops under a certain level. Sleep mode has to be enabled for this feature to be useful. **For now alerts can be sent on MQTT, more protocols are coming soon. If there is a specific protocol you want to be supported let me know.**

![image](https://user-images.githubusercontent.com/94690098/182034543-8025c5ab-5e38-43a0-9ec8-014d4301fcf0.png)

# Configuration:
--------

## 1. WiFi/CAN Configuration:
1. Power up the device using the USB cable, or by plugging into the OBD-II connector. 
2. The blue LED will light ON, and a WiFi device access point will start. The SSID will look like: WiCAN_xxxxxxxxxxxx
3. Connect to the SSID using the default password: @meatpi#
4. Using a web browser, go to http://192.168.80.1/ 
5. The status menu shows the device current configuration, if in Ap+Station mode it will show the device IP on your local network if connected successfully.
6. The WiFi menu lets you configure the WiFi parameters. It is recommended that you change the AP access point.
7. The CAN menu allows to choose the protocol set the bitrate and TCP/UPD port number.
8. When ready click submit Changes button and the device will store the configuration reboot immediately.

**Note: If you intend to use the device in AP mode it is recommand that you disable the BLE function**

![Configuration page](https://github.com/meatpiHQ/WiCAN/blob/main/images/settings40.png?raw=true "Config page")

## 2. BUSMaster
You need to download the right version of BUSMaster provided in this [**Link**](https://bit.ly/3yGgGTm). Here is how to setup the hardware. 

### **Device Configuration:**

1. Go to configuration webpage.
2. Baudrate can be set in BUSMaster configuration
3. Set "Port Type" = TCP
4. Set "Protocol" = slcan
5. Click submit changes.

### **BUSMaster Configuration:**

1. Select VSCom CAN-API by clicking on 'Driver Selection -> VSCom CAN-API"
2. Then Click on 'Channel Configuration -> Advanced' 
3. Fill in the IP and port. **Example: 192.168.80.1:3333**
4. Check the 'Hardware Timestamps' check box.
5. Choose the Baudrate.
6. Click 'OK', then Click the Connect button on the top left corner.
  

<img src="https://user-images.githubusercontent.com/94690098/158798541-0317aa4f-ebf5-4e57-83b0-ea3fefeaf4e9.png" width="350" height="500" >

## 3. RealDash
WiCAN can connect with RealDash using WiFi or BLE. The Protocol and CAN bitrate must be set using the configuration page. BLE is only support on Android and IOS. Windows 10 only supports WiFi connection.

## **WiFi Device Configuration:**

1. Go to configuration webpage.
2. Select the baudrate
3. Set "Port Type" = TCP
4. Set "Protocol" = reladash 66
5. Click submit changes.

## **RealDash Configuration**
1. Go to garage then click on the dashboard.
2. Click Add connection.
3. Select Adapter (CAN/LIN)
4. Select RealDash CAN
5. Select WIFI/LAN
6. Enter IP and Port
7. Click Done

## **BLE Device Configuration:**

1. Go to configuration webpage.
2. Select the baudrate
3. Set the "BLE Status" to enable

**Note: When the BLE is connected, the device will automatically turn off the WiFi configuration access point. Once BLE is disconnected the configuration access point will turn back on.**  

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

<img src="https://user-images.githubusercontent.com/94690098/163678507-f9822f57-bbe1-42a4-82c4-501cd7834ba0.png" width="350" height="300" >

## 2. USB:

Use the [**ESP flash tool**](https://www.espressif.com/en/support/download/other-tools) to update the firmware, just follow the same setting in the picture below. Make sure to select ESP32-C3 and USB mode. **esptool.py** also can also be used to flash a new firmware.
1. Download [**ESP flash tool**](https://www.espressif.com/en/support/download/other-tools)
2. Download the latest firmware zip file from the [**releases**](https://github.com/meatpiHQ/wican-fw/releases) page. 
3. Select ESP32C3, develop and USB then click ok

![image](https://user-images.githubusercontent.com/94690098/182028074-7cd55122-a129-4fd3-bad9-e66f1f8d3ca3.png)

4. Set the configuration as the picture below, select and fill in the address for each binary.

<img src="https://user-images.githubusercontent.com/94690098/182028212-e8e90e71-7758-4d2d-88dc-aebf95a2e4a8.png" width="350" height="600" >

5. Short the pins as shown, the plug in the USB cable.

![image](https://user-images.githubusercontent.com/94690098/182028671-18d523de-466d-4c28-998d-c4330dd33ae7.png)

6. After you plug in the USB cable the Orange LED will light up. Now click START button on the flash tool.

**NOTE: After flashing, the device configuration might be erased.**


---

© 2022 meatPi Electronics | www.meatpi.com | PO Box 5005 Clayton, VIC 3168, AustraliaS
