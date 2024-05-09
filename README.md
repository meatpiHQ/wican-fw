<img src="https://github.com/slimelec/ollie-hw/blob/master/images/mpi_logo.png" width=300>

[www.meatpi.com](https://www.meatpi.com)
---
## MeatPi [Discord server](https://discord.com/invite/2hpHVDmyfw)
## Please update to the [latest firmware version](https://github.com/meatpiHQ/wican-fw/releases/)

## Order on [**Mouser**](https://www.mouser.com/c/?m=MeatPi) or [**Crowd Supply!**](https://www.crowdsupply.com/meatpi-electronics)

<br/><br/>

---
# WiCAN PRO

launching soon on [**Crowd Supply!**](https://www.crowdsupply.com/meatpi-electronics/wican-pro)

![image](https://github.com/meatpiHQ/wican-fw/assets/94690098/1648a50f-d5a4-40fc-9794-78b8b57ccc0d)


# WiCAN

![65465](https://github.com/meatpiHQ/wican-fw/assets/94690098/537b5062-cb8a-485f-9354-6c351d08aa49)

## WiCAN-USB Pinout

<p align="center">
<img src="https://user-images.githubusercontent.com/94690098/218081136-fc3da520-1851-497e-90dc-ccc5d6543f1f.png" >
</p>

### *** To activate the 120R termination resistor, simply connect the TR pin to CANH. The other end of the termination resistor is connected to CANL internally.


## WiCAN-OBD2 Pinout

<p align="center">
<img src="https://user-images.githubusercontent.com/94690098/182854687-911bae04-9bdd-4947-8363-e088e278b3b8.png" >
</p>

---
## [webCAN](http://webcan.meatpi.com/) Comming soon!

- [Features Wishlist](#features-wishlist)
- [Important Notes](#important-notes)
- [API](#api)
- [Build](#build)
- [Description](#description)
- [Features](#features)
  - [Sleep mode](#1-sleep-mode)
  - [Battery Alert](#2-battery-alert)
- [Configuration](#configuration)
  - [WiFi/CAN Configuration](#1-wifican-configuration)
  - [BUSMaster](#2-busmaster)
  - [Realdash](#3-realdash)
  - [SavvyCAN](#4-savvycan)
  - [webCAN](http://webcan.meatpi.com)
- [SocketCAN](#socketcan)
  - [WiFi](#1-wifi)
  - [USB](#2-usb)
- [ELM327 OBD2 Protocol](#elm327-obd2-protocol)
- [MQTT](#mqtt)
  - [Status](#1-status)
  - [MQTT TX RX Frame](#2-mqtt-tx-rx-frame)
  - [Receive Frames](#3-receive-frames)
  - [Transmit Frames](#4-transmit-frames)
  - [OBDII PID Request Over MQTT](#5-obdii-pid-request-over-mqtt)
  - [Request Battery SoC MQTT Example](#6-request-battery-soc-mqtt-example)
  - [CAN to JSON interpreter - Filtring](#7-can-to-json-interpreter---filtring)
- [Home Assistant](#home-assistant)

- [Firmware Update](#firmware-update)
  - [OTA](#1-ota)
  - [USB Flash](#2-usb-flash)

# **Features Wishlist**:

- ~~CAN Auto baudrate~~ **DONE**
- ~~Support ELM327 OBD2 protocol~~ **DONE** (Basic support) 
- ~~Home Assistant Integrations~~ **DONE**
- Extended ELM327 support 
- Support ELM327 protocol and Realdash protocol simultaneously on WiFi and BLE 
- Add option to send an Email on battery alert 
- Expand alerts to include other CAN parameters parsed from DBC files.
- Support for extra wifi settings
# **Important Notes**:

- The OBD2 adapter is not designed to powered of the USB connecter. The USB connector can power the adapter to flash custom firmware or hardreset the deivce and can also be used for debuging.
- It is highly recommanded to turn OFF the BLE if not used. Otherwise it might affect the preformance.
- When the BLE is connected, the device configuration access point will be disabled i.e you won't be able to configure the device unless you disconnect the BLE, by turning off the BLE on your phone or device.
- If AP+Station mode is enabled, only use station IP to communicate with the device and AP must be used for configuration only.
  
# **API**
[API Documentation](https://drive.google.com/drive/folders/1qJelUAHGrn_YbNIP0Jk_KmNENG-hKbtl?usp=sharing)

[Programing examples](https://github.com/meatpiHQ/programming_examples/tree/master/CAN)

# **Build**:

1. Install ESP-IDF v4.4.
2. Clone porject.
3. Open project and build.

# **Description**:

WiCAN is a powerful ESP32-C3-based CAN adapter for car hacking and general CAN-bus development. It is available in two form factors, OBD-II and standard USB-CAN. The original firmware can use Wi-Fi or BLE to interface directly with RealDash, which allows you to create your own custom dashboards with stunning graphics. It is available for Android, iOS, and Windows 10. WiCAN connects to your existing Wi-Fi network and any device on that network, where it allows you to configure Wi-Fi and CAN settings through a built-in web interface. Both versions have a power-saving mode that detects when the voltage drops under 13 V or some other preset value. When this power-saving mode is engaged, WiCAN is capable of entering sleep mode, which drops current consumption below 1 mA.

WiCAN is a simple, ready-to-use solution for CAN-bus development and hacking. It accelerates development by providing vehicle-diagnostic APIs and libraries in various languages and for various operating systems. WiCAN works with a large array of pre-existing vehicle-diagnostic libraries, including RealDash, SavvyCAN, BUSmaster, python-can/SocketCA, and more. APIs are also available for LabView, C#, VB.Net, Delphi, and Python in case you’re writing your own software.

# WiCAN-OBD
![image](https://user-images.githubusercontent.com/94690098/231444160-08842087-55ad-4165-8291-b379da63aeeb.png)

WiCAN-OBD will be of great interest to car enthusiasts and tinkers who want to modernize or customize the head-unit displays in their cars using RealDash. Check out some examples of the available graphic interfaces, which are supported by a robust collection of Manuals & Tutorials to get you started with RealDash.

Another great feature of WiCAN-OBD is its MQTT battery alerts. It can monitor your battery voltage and send an alert if that voltage drops under a set threshold. This feature is especially important for users who own multiple cars they do not use regularly.

# WiCAN-USB
![image](https://user-images.githubusercontent.com/94690098/231443956-fbf2de46-ef19-4ba5-83b1-6058ab123f56.png)

WiCAN-USB can be powered through USB or through a screw-terminal connector. The hardware and firmware is almost identical to WiCAN-OBD, but with an extended voltage range up to 36 V. Many of the projects we work on involve 12 V or 24 V trucks, and WiCAN-USB comes in handy when we need to monitor those trucks remotely, from the comfort of our desks, by configuring them to connect to Wi-Fi. And, thanks to its low cost, we don’t need to worry about breaking or losing it the WiCAN-USB device itself.

WiCAN-USB can also be used as a USB-to-CAN adapter when Wi-Fi connectivity is not available or when a hardwired connection is needed.


## [**Programming Examples**](https://github.com/meatpiHQ/programming_examples/tree/master/CAN)

### **Features and Specifications**:

- Supports CAN2.0A/B up to 1Mbits.
- Works with Realdash, based on "realdash 66"
- Supports SocketCAN and works with BUSMaster
- Supports TCP and UDP
- WiFi can be used in AP and station mode
- WiFi and CAN configured using web interface.
- Diode protection for the USB port

![Modes](https://user-images.githubusercontent.com/94690098/222961571-bd137341-808a-4f0a-9528-789fe24d640e.png "Connection Mode")

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
You need to download the right version of BUSMaster provided in this [**Link**](https://drive.google.com/drive/folders/1qJelUAHGrn_YbNIP0Jk_KmNENG-hKbtl?usp=sharing). Here is how to setup the hardware. 

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
WiCAN-OBD and WiCAN-USB can connect with RealDash using either WiFi or BLE. **The protocol and CAN bitrate must be set using the configuration page**. BLE is supported only on Android and iOS. Windows 10 supports only WiFi connections. Additionally, _WiCAN-USB can connect using the USB interface_.

## **USB Device Configuration (WiCAN-USB only):**
1. Go to garage then click on the dashboard.
2. Click Add connection.
3. Select Adapter (CAN/LIN)
4. Select RealDash CAN
5. Select SERIAL/USB
6. Select device
7. Set Baud RATE: 4000000 (This is the usb/serial buadrate not the CAN bitrate)
8. Click next then Done
   
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

**If you're using firmware verion v1.64 or below please update to the [latest version](https://github.com/meatpiHQ/wican-fw/releases/) before enabling BLE**

1. Go to configuration webpage.
2. Select the baudrate
3. Set the "BLE Status" to enable

**Note: When the BLE is connected, the device will automatically turn off the WiFi configuration access point. Once BLE is disconnected the configuration access point will turn back on.**  

## 4. SavvyCAN

1. Download [SavvyCAN](https://www.savvycan.com/)
2. Connect to the device AP.
3. Open SavvyCAN and Click Connection->Open Connection Window->Add New Device
4. Select "Network Connection", if you're on the same network it auto detect the IP.
5. Click Create New Connection.
6. Then select "Enable Bus" checkbox.

# SocketCAN

## 1. WIFi:

Change to protocol in the device configuration page to "slcan", then create a virtual serial port over TCP on your Linux machine. If WiCAN is connected to your home network replace "192.168.80.1" with device IP.

```
sudo socat pty,link=/dev/netcan0,raw tcp:192.168.80.1:3333 &
sudo slcand -o -c -s8 /dev/netcan0 can0
sudo ifconfig can0 txqueuelen 1000
sudo ifconfig can0 up
```

## 2. USB

```
sudo slcand -o -s6 -t sw -S 4000000 /dev/ttyACM0 can0
sudo ifconfig can0 txqueuelen 1000
sudo ifconfig can0 up
```
# ELM327 OBD2 Protocol

**If you're using firmware verion v1.64 or below please update to the [latest version](https://github.com/meatpiHQ/wican-fw/releases/) before enabling BLE**

1. Go to configuration webpage.
2. Select the baudrate
3. Set "Port Type" = TCP
4. Set "Protocol" = elm327
5. Enable BLE if needed. [Note](https://github.com/meatpiHQ/wican-fw#important-notes)
6. Click submit changes.

### OBD2 in RealDash 

1. Go to garage then click on the dashboard.
2. Click Add connection.
3. Select Adapter ``` OBD2 ```
4. Select Bluetooth or WiFi
5. If WiFi fill in the IP 192.168.80.1 and port 3333. 
6. Click on ``` OBD2 PROTOCOL ``` and select your car protocol, (11 bit ID, 500Kbit) or (11 bit ID, 250Kbit)
7. Activate ``` Request Only First Reply ```
8. Click Done.

# MQTT

## If you're using firmware verion v1.64 or below please update to the [latest version](https://github.com/meatpiHQ/wican-fw/releases/) before enabling MQTT

Currently only non-secure MQTT is supported, it's highly recommended that you only use it with local MQTT broker and not use public brokers otherwise your CAN bus might be publicly exposed.

![image](https://user-images.githubusercontent.com/94690098/196186799-7299fe23-8d98-40e0-ad72-a4aeaf695110.png)

To use MQTT client simply Enable in the configuration page and fill in the broker details. You also need to note the device ID, which will be used to communicate with the device. The device ID "xxxxxxxxxxxx" is part of the AP ssid mentioned in [WiFi/CAN Configuration](#1-wifican-configuration) WiCAN_xxxxxxxxxxxx. This will alow you to communicate with multiple WiCAN device if needed.

Example: If the AP ssid is "WiCAN_112233445566", the device ID is 112233445566. 

## 1. Status:  

When the device connects to the MQTT broker it will publish a status message to the status topic.

### - Status Topic: wican/xxxxxxxxxxxx/status
### - Status Message JSON:  

{"status": "offline"} or {"status": "online"}

## 2. MQTT TX RX Frame:

```
bus: Is always 0. Thats reserved for future application
type: tx or rx 
dlc: 0 to 8
rtr: true or false
extd: true or false
id: 11 or 29 bit ID
```
**Example:** { "bus": "0", "type": "tx", "frame": [{ "id": 2015, "dlc": 8, "rtr": false, "extd": false, "data": [2, 1, 70, 170, 170, 170, 170, 170] }] };

## 3. Receive Frames:

To receive CAN frames simply subscribe to the receive topic. Each MQTT message might contain more than 1 frame.

### - Receive Topic: wican/xxxxxxxxxxxx/can/rx
### - Received Message JSON: 

{"bus":0,"type":"rx","ts":34610,"frame":[{"id":123,"dlc":8,"rtr":false,"extd":false,"data":[1,2,3,4,5,6,7,8]},{"id":124,"dlc":8,"rtr":false,"extd":true,"data":[1,2,3,4,5,6,7,8]}]}
### - Received Message JSON Schema:
![image](https://user-images.githubusercontent.com/94690098/196184692-40c84504-580c-449f-9876-3d373dd11560.png)

## 4. Transmit Frames:

### - Transmit Topic: wican/xxxxxxxxxxxx/can/tx
### - Transmit Message JSON: 
{"bus":0,"type":"tx","frame":[{"id":123,"dlc":8,"rtr":false,"extd":true,"data":[1,2,3,4,5,6,7,8]},{"id":124,"dlc":8,"rtr":false,"extd":true,"data":[1,2,3,4,5,6,7,8]}]}

### - Transmit Message JSON Schema:
![image](https://user-images.githubusercontent.com/94690098/196187228-3923204e-1b87-4ece-bb72-406e338a5831.png)

## 5. OBDII PID Request Over MQTT

```
bus: Is always 0. Reserved for future application
dlc: Always 8
rtr: false
extd: false, if the car obd2 protocol is 11bit ID, 500 kbaud or 250 kbaud
extd: true, if the car obd2 protocol is 29bit ID, 500 kbaud or 250 kbaud
id: 11 bit OBD2 request ECU ID should be 2015 (that's 0x7DF in HEX)
id: 29 bit OBD2 request ECU ID should be 417018865 (that's 0x18DB33F1 in HEX)
```

![image](https://github.com/meatpiHQ/wican-fw/assets/94690098/7726d4fe-1d6f-4b1e-be93-ea4304a47942)

### Example: Get ambient temp request, PID is 70

{ "bus": "0", "type": "tx", "frame": [{ "id": 2015, "dlc": 8, "rtr": false, "extd": false, "data": [2, 1, 70, 170, 170, 170, 170, 170] }] };

### [List of Standard PIDs](https://en.wikipedia.org/wiki/OBD-II_PIDs) 

## 6. Request Battery SoC MQTT Example

This PID request should work on most EVs, **however it's not possible to know it will work on certain EV model unless it's tested on that specific car model**.

### Request 

{"bus":"0","type":"tx","frame":[{"id":2015,"dlc":8,"rtr":false,"extd":false,"data":[2,1,91,170,170,170,170,170]}]}

### Response

{"bus":"0","type":"rx","ts":51561,"frame":[{"id":2024,"dlc":8,"rtr":false,"extd":false,"data":[3,65,91,**170**,0,0,0,0]}]}

The SoC = (170 x 100)/255 = **66.67%**

## 7. CAN to JSON interpreter - Filtring 


This feature enables you to convert CAN messages into JSON format, apply calculations as specified by your expressions, and send the resulting data to an MQTT broker at predefined intervals. Below, we provide a comprehensive guide on how to effectively utilize this powerful feature.

**Note: When a filter is added, all other CAN messages will be ignored, ensuring that only the configured filtered messages are sent.**

<img width="1496" alt="image" src="https://github.com/meatpiHQ/wican-fw/assets/94690098/18e87820-2b5f-4823-947d-0d1c412a6e3d">


### Configuration Parameters:

**CAN ID: Frame ID in DEC**
This parameter refers to the CAN message identifier in decimal format.

**Name**
Specify the JSON key under which the extracted value will be stored.

**PID: PID Number in DEC**
If the frame is a response to a PID request, set this parameter to the relevant PID number. Otherwise, for non-PID frames, it should be set to -1.

**Index: PID index**
For all standard PID requests, this should be set to 2, referring to byte 2 in the response. In certain special cases, this value may differ from 2. If the PID is set to -1, the interpreter will disregard this value.

**Start Bit**
Indicate the bit from which you want to commence extracting the value. For instance, if Byte 0 is represented as 0xA0 in binary (b1010 0000), 'Bit 0' would correspond to 1, 'Bit 1' to 0, and so forth.

**Bit Length**
This parameter denotes the number of bits comprising the value you intend to extract.

**Expression**
Define the mathematical operations to apply to the extracted value. It's crucial to include the letter 'V' to represent the extracted value within your expression.

**Cycle**
Specify the time interval in milliseconds between JSON messages sent to MQTT. The cycle time should fall within the range of 100 to 10,000 milliseconds.


### JSON Format 

WiCAN processes CAN frames, and the resulting JSON data follows this structure:

```
{"VehicleSpeed": 23.000000}
{"EngineSpeed": 1165.000000}
{"CustomID": 3594.400000}
```

### PID response frames Example:

Name|CAN ID| Byte 0 | Byte 1 | Byte 2 | Byte 3 | Byte 4 | Byte 5 | Byte 6 | Byte 7 
---|--- | --- | --- | --- |--- |--- |--- |--- |---
Engine Speed|2024| 4| 65| **12**| _18_| _52_| 170| 170| 170
| |  |  | | PID| Value 1| Value 2  |  |  

The Engine Speed is PID response so "PID" must be set 12. The value we need to extract is 16bit in Byte 3 and Byte 4. So we set the "Start Bit" to 24 and "Bit Length" to 16. The extracted value will be (11 * 256)+68 = 4660. Now to get the engine speed in rpm we must multiple by 0.25. So the "Expression" will be V * 0.25= 1165

Name|CAN ID| Byte 0 | Byte 1 | Byte 2 | Byte 3 | Byte 4 | Byte 5 | Byte 6 | Byte 7 
---|--- | --- | --- | --- |--- |--- |--- |--- |---
Vehicle Speed|2024| 4| 65| **13**| _23_|170| 170| 170| 170
| |  |  | | PID| Value 1|   |  |  

The Vehicle  Speed is PID response so "PID" must be set 13. The value we need to extract is 8bit in Byte 3. So we set the "Start Bit" to 24 and "Bit Length" to 8. The extracted value will be 23. No need for any operation to get the speed so we set the "Expression" to V, so the value will be 23.

### Other CAN Frames Example:

Name|CAN ID| Byte 0 | Byte 1 | Byte 2 | Byte 3 | Byte 4 | Byte 5 | Byte 6 | Byte 7 
---|--- | --- | --- | --- |--- |--- |--- |--- |---
Custom ID|356| 170| 170| _35_| _16_|170| 170| 170| 170

The PID in the configuration needs to set to -1. The value we need to extract is 16bit in Byte 2 and Byte 3. So we set the "Start Bit" to 16 and "Bit Length" to 16. The extracted value will be (35*256)+16 = 8976. Now to get the value we must add 10 to the Value and divide by 2.5. So the "Expression" will be (V+10)/2.5 = 3594.4

Name|CAN ID| Byte 0 | Byte 1 | Byte 2 | Byte 3 | Byte 4 | Byte 5 | Byte 6 | Byte 7 
---|--- | --- | --- | --- |--- |--- |--- |--- |---
Custom ID|355| 170| 170| 170| 170|_5_| _34_| 170| 170

The PID in the configuration should be set to -1. The values to extract are from Byte 4 and Byte 5. The formula used here is (5 + 34)/2, so the expression to set is (B4 + B5)/2.

# Home Assistant

### 1. EV Battery Examples [Wiki Page](https://github.com/meatpiHQ/wican-fw/wiki/EV-Battery-SoC-in-Home-Assistant-%E2%80%90-Example)
In this example, we will learn how to set up Home Assistant to request the battery State of Charge (SoC) without using Node-RED. This example has been tested on an ORA Funky Cat vehicle and can also serve as a reference for requesting other Parameter IDs (PIDs) in addition to SoC. EV Battery Examples [Wiki Page](https://github.com/meatpiHQ/wican-fw/wiki/EV-Battery-SoC-in-Home-Assistant-%E2%80%90-Example)

### 2. Node-RED Example 

WiCAN is able to send CAN bus messages to Home Assistant using MQTT protocol. I found that using Node-RED is the simplest way to create automation based on the CAN messages received. This short video highlights some of the steps https://youtu.be/oR5HQIUPR9I

1. Install Home Assistant [Mosquitto broker add-on](https://github.com/home-assistant/addons/blob/master/mosquitto/DOCS.md)
2. Create Home Assistant new user account for WiCAN. These user credentials will be used to set up the MQTT setting for WiCAN.
3. Connect to WiCAN access point WiCAN_xxxxxxxxxxxx then, using a web browser, go to http://192.168.80.1/
4. Set the "Mode" to Ap+Station
5. Fill in your Home WiFi network SSID and Password.
6. Enable [MQTT](#mqtt) and fill in the Home Assistant credentials created in step 2
7. Install Home Assistant [Node-RED Add-on](https://github.com/hassio-addons/addon-node-red)
8. Download [wican_example_flow.json](https://github.com/meatpiHQ/wican-fw/blob/main/ha/wican_example_flow.json) and replace **device_id** with your WiCAN ID.
9. Open Node-RED Add-on and import the edited "wican_example_flow.json"
10. Double click on the subsction Node and edit the server fill in MQTT broker IP address and credentials created in step 2
11. Click deploy.
12. To create a new MQTT sensor, you’ll need to edit the configuration.yaml file by adding the following lines:

```
mqtt:
  sensor:
    - name: "Amb Temp"
      state_topic: "CAR1/Amb_Temp"
      unit_of_measurement: "C"
      value_template: "{{ value_json.amb_temp }}"
    - name: "Fuel Level"
      state_topic: "CAR1/Fuel_Level"
      unit_of_measurement: "%"
      value_template: "{{ value_json.fuel_level }}"
```
11. Restart Home assistant
12. After restart go to dashboard and Add new Card entity.

## Workflow summery

In this example we've got one Node subscribed to topic ``` wican/device_id/status ```, when WiCAN connects to the local network it will publish ``` {"status": "online"}  ```. Once the Node function ``` Send Get  Amb Temp req ``` receives online status it will send a OBD2 request to get the ambient temperature. The OBD2 request message looks like this:

``` { "bus": "0", "type": "tx", "frame": [{ "id": 2015, "dlc": 8, "rtr": false, "extd": false, "data": [2, 1, 70, 170, 170, 170, 170, 170] }] } ```

Frame ID : 2015 or 0x7DF
PID: 70 or 0x46

Here is a good [reference](https://www.csselectronics.com/pages/obd2-pid-table-on-board-diagnostics-j1979) on how to construct a PID request. Note select DEC from drop down list.

We have another Node subscribed to topic ``` wican/device_id/can/rx ```, once the car ECU responds to the request the function ``` Parse Amb Temp RSP ``` will parse the message and publish a message to topic ``` CAR1/Amb_Temp ```. Notice that when you edited ``` configuration.yaml ``` file create an MQTT Entity that subscribes to ``` CAR1/Amb_Temp ``` and expects a message ``` { value_json.amb_temp } ``` with unit_of_measurement C.

![image](https://user-images.githubusercontent.com/94690098/204269457-32f6e6b5-c9be-44d0-b41c-36fa61b82258.png)


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

## 2. USB Flash:

Use the [**ESP flash tool**](https://www.espressif.com/en/support/download/other-tools) to update the firmware, just follow the same setting in the picture below. Make sure to select ESP32-C3 and USB mode. **esptool.py** also can also be used to flash a new firmware.
1. Download [**ESP flash tool**](https://www.espressif.com/en/support/download/other-tools)
2. Download the latest firmware zip file from the [**releases**](https://github.com/meatpiHQ/wican-fw/releases) page. 
3. Select ESP32C3, develop and USB then click ok

![image](https://user-images.githubusercontent.com/94690098/182028074-7cd55122-a129-4fd3-bad9-e66f1f8d3ca3.png)

4. Set the configuration as the picture below, select and fill in the address for each binary.

<img src="https://user-images.githubusercontent.com/94690098/182028212-e8e90e71-7758-4d2d-88dc-aebf95a2e4a8.png" width="350" height="600" >

5. Short the pins as shown, then plug in the USB cable.

### OBD 
![image](https://user-images.githubusercontent.com/94690098/182028671-18d523de-466d-4c28-998d-c4330dd33ae7.png)

### USB
![image](https://github.com/meatpiHQ/wican-fw/assets/94690098/b9c9fe35-6ec1-4a64-8753-134fabd43c0c)


6. After you plug in the USB cable the Orange LED will light up. Now click START button on the flash tool.

**NOTE: After flashing, the device configuration might be erased.**


---

© 2023 meatPi Electronics | www.meatpi.com | PO Box 5005 Clayton, VIC 3168, Australia
