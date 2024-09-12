<img src="https://github.com/slimelec/ollie-hw/blob/master/images/mpi_logo.png" width=300>

[www.meatpi.com](https://www.meatpi.com)
---
## MeatPi [Discord server](https://discord.com/invite/2hpHVDmyfw)
## Please update to the [latest firmware version](https://github.com/meatpiHQ/wican-fw/releases/)
---
# [Official Docs](https://meatpihq.github.io/wican-fw/)
We are currently in the process of migrating from this large README.md into the proper docs.

## Order on [**Mouser**](https://www.mouser.com/c/?m=MeatPi) or [**Crowd Supply!**](https://www.crowdsupply.com/meatpi-electronics)

<br/><br/>

---
# WiCAN PRO

### Ongoing crowdfunding campaign for a new product in development! If you would like to support this project, please subscribe for updates on [**Crowd Supply!**](https://www.crowdsupply.com/meatpi-electronics/wican-pro)

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
## [webCAN](http://webcan.meatpi.com/) Coming soon!

- [Important Notes](#important-notes)
- [API](#api)
- [Build](#build)
- [Description](#description)
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
- [Firmware Update](#firmware-update)
  - [OTA](#1-ota)
  - [USB Flash](#2-usb-flash)

# **Important Notes**:

- The OBD2 adapter is not designed to powered of the USB connecter. The USB connector can power the adapter to flash custom firmware or hardreset the deivce and can also be used for debuging.
- It is highly recommanded to turn OFF the BLE if not used. Otherwise it might affect the preformance.
- When the BLE is connected, the device configuration access point will be disabled i.e you won't be able to configure the device unless you disconnect the BLE, by turning off the BLE on your phone or device.
- If AP+Station mode is enabled, only use station IP to communicate with the device and AP must be used for configuration only.
  
# **API**
[API Documentation](https://drive.google.com/drive/folders/1qJelUAHGrn_YbNIP0Jk_KmNENG-hKbtl?usp=sharing)

[Programing examples](https://github.com/meatpiHQ/programming_examples/tree/master/CAN)

# **Build**:

1. Install ESP-IDF >= v5.1.
2. Clone project.
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

### USB Device Configuration (WiCAN-USB only):
1. Go to garage then click on the dashboard.
2. Click Add connection.
3. Select Adapter (CAN/LIN)
4. Select RealDash CAN
5. Select SERIAL/USB
6. Select device
7. Set Baud RATE: 4000000 (This is the usb/serial buadrate not the CAN bitrate)
8. Click next then Done
   
### WiFi Device Configuration:

1. Go to configuration webpage.
2. Select the baudrate
3. Set "Port Type" = TCP
4. Set "Protocol" = reladash 66
5. Click submit changes.

### RealDash Configuration:
1. Go to garage then click on the dashboard.
2. Click Add connection.
3. Select Adapter (CAN/LIN)
4. Select RealDash CAN
5. Select WIFI/LAN
6. Enter IP and Port
7. Click Done

### BLE Device Configuration:

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

---

© 2023 meatPi Electronics | www.meatpi.com | PO Box 5005 Clayton, VIC 3168, Australia
