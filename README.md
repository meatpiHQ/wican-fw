<img src="https://github.com/slimelec/ollie-hw/blob/master/images/mpi_logo.png" width=300>

[www.meatpi.com](https://www.meatpi.com)
---
# [Documentation](https://meatpihq.github.io/wican-fw/) | [Firmware updates](https://github.com/meatpiHQ/wican-fw/releases/) | [Discord server](https://discord.com/invite/2hpHVDmyfw)

## Order on [**Mouser**](https://www.mouser.com/c/?m=MeatPi) or [**Crowd Supply!**](https://www.crowdsupply.com/meatpi-electronics)

---

# WiCAN PRO 4G Module

### ESPNetLink is an LTE and GPS module that provides IoT connectivity and real-time vehicle tracking for your WiCAN PRO adapter. To support this project, please subscribe for updates [**Crowd Supply!**](https://www.crowdsupply.com/meatpi-electronics/espnetlink)

![image](https://github.com/user-attachments/assets/5314bd88-768f-4e8c-8eea-dd2ed4649283)

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
- [ELM327 OBD2 Protocol](#elm327-obd2-protocol)
- [Firmware Update](https://meatpihq.github.io/wican-fw/config/firmware-update)
  - [OTA](https://meatpihq.github.io/wican-fw/config/firmware-update)
  - [USB Flash](https://meatpihq.github.io/wican-fw/config/firmware-update)

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

---

© 2024 meatPi Electronics | www.meatpi.com | PO Box 5005 Clayton, VIC 3168, Australia
