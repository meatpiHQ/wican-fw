<img src="https://github.com/slimelec/ollie-hw/blob/master/images/mpi_logo.png" width=300>

[www.meatpi.com](https://www.meatpi.com)
---

**Note: This is the intial release although all the functions and features work, optimization/clean up are still required.**

### **Build**

1. Install ESP-IDF v4.4.
2. Clone porject.
3. Open project and build.

### **Description**:

WiCAN is a **OBD-II WiFi/BLE** to **CAN** adapter based on **ESP32-C3**, it is the great tool for car hacking. The original firmware can interface directly with RealDash over WiFI or BLE. [**Realdash **](http://realdash.net/)allows you to create your own custom dashboard with stunning graphics, it's available on ANDROID, IOS AND WINDOWS 10.It can connect to your existing WiFi network or any device can connect to it's **WiFi access point**.

WiCAN also works with **BUSMaster**, for monitoring and logging CAN bus traffic.

**Also available API for LabView, C#, VB.Net, Delphi, Python** - In case you're writing your own software.

WiFi and CAN configuration can be set configuration web server.

**Note: Schematics and source code Firmware will released soon!**

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

# Configuration:
--------

# 1. WiFi/CAN Configuration:
1. Power up the device using the USB cable, or by plugging into the OBD-II connector. 
2. The blue LED will light ON, and a WiFi device access point will start. The SSID will look like: WiCAN_xxxxxxxxxxxx
3. Connect to the SSID using the default password: @meatpi#
4. Using a web browser, go to http://192.168.80.1/ 
5. The status menu shows the device current configuration, if in Ap+Station mode it will show the device IP on your local network if connected successfully.
6. The WiFi menu lets you configure the WiFi parameters. It is recommended that you change the AP access point.
7. The CAN menu allows to choose the protocol set the bitrate and TCP/UPD port number.
8. When ready click submit Changes button and the device will store the configuration reboot immediately.

![Configuration page](https://github.com/meatpiHQ/WiCAN/blob/main/images/settings40.png?raw=true "Config page")

# 2. BUSMaster
You need to download the right version of BUSMaster provided in this [**Link**](https://bit.ly/3yGgGTm). Here is how to setup the hardware. 

## **Device Configuration:**

1. Go to configuration webpage.
2. Baudrate can be set in BUSMaster configuration
3. Set "Port Type" = TCP
4. Set "Protocol" = slcan
5. Click submit changes.

## **BUSMaster Configuration:**

1. Select VSCom CAN-API by clicking on 'Driver Selection -> VSCom CAN-API"
2. Then Click on 'Channel Configuration -> Advanced' 
3. Fill in the IP and port. **Example: 192.168.80.1:3333**
4. Check the 'Hardware Timestamps' check box.
5. Choose the Baudrate.
6. Click 'OK', then Click the Connect button on the top left corner.
  

<img src="https://user-images.githubusercontent.com/94690098/158798541-0317aa4f-ebf5-4e57-83b0-ea3fefeaf4e9.png" width="350" height="500" >

# 3. ReadDash
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

# 4. Firmware Update
Use the ESP flash tool to update the firmware, just follow the same setting in the picture below. Also "esptool.py" can also be used to flash a new firmware.

<img src="https://user-images.githubusercontent.com/94690098/158790496-31827bf3-4bda-47db-971d-ac1d53ad7972.PNG" width="350" height="600" >


---

© 2022 meatPi Electronics | www.meatpi.com | PO Box 5005 Clayton, VIC 3168, AustraliaS