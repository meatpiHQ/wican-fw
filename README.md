<img src="https://github.com/slimelec/ollie-hw/blob/master/images/mpi_logo.png" width=300>

[www.meatpi.com](https://www.meatpi.com)
---
## MeatPi [Discord server](https://discord.gg/WXy8KQCE7V)
## Please update to the [latest firmware version](https://github.com/meatpiHQ/wican-fw/releases/)

## Order on [**Mouser**](https://www.mouser.com/c/?m=MeatPi) or [**Crowd Supply!**](https://www.crowdsupply.com/meatpi-electronics/wican)

<br/><br/>

---
![65465](https://github.com/meatpiHQ/wican-fw/assets/94690098/537b5062-cb8a-485f-9354-6c351d08aa49)

## WiCAN-USB and WiCAN-OBD2 Pinouts

### WiCAN-USB Pinout

![WiCAN-USB Pinout](https://user-images.githubusercontent.com/94690098/218081136-fc3da520-1851-497e-90dc-ccc5d6543f1f.png)

To activate the 120R termination resistor, connect the TR pin to CANH. The other end of the termination resistor is internally connected to CANL.

### WiCAN-OBD2 Pinout

![WiCAN-OBD2 Pinout](https://user-images.githubusercontent.com/94690098/182854687-911bae04-9bdd-4947-8363-e088e278b3b8.png)

## Features Wishlist

- ~~CAN Auto baudrate~~ **DONE**
- ~~Support ELM327 OBD2 protocol~~ **DONE**
- ~~Home Assistant Integrations~~ **DONE**
- Extended ELM327 support
- Simultaneous support for ELM327 and RealDash protocols over WiFi and BLE
- Email alerts for battery issues
- Expanded CAN parameter alerts from DBC files
- Extra WiFi settings support

## Important Notes

- OBD2 adapter not designed to be powered by USB for long-term use.
- BLE should be turned off if not in use to avoid performance issues.
- Device configuration access point disabled when BLE is connected.
- Use station IP in AP+Station mode for communication; use AP for configuration only.

## API and Build

- [API Documentation](https://drive.google.com/drive/folders/1qJelUAHGrn_YbNIP0Jk_KmNENG-hKbtl?usp=sharing)
- [Programming Examples](https://github.com/meatpiHQ/programming_examples/tree/master/CAN)

### Build Instructions

1. Install ESP-IDF v4.4.
2. Clone the project.
3. Open and build the project.

## Description

WiCAN, based on ESP32-C3, is a versatile CAN adapter for car hacking and CAN-bus development, available in OBD-II and USB-CAN formats. It uses Wi-Fi or BLE for interface with RealDash, allowing custom dashboards. It features a power-saving mode for low battery consumption and supports a range of vehicle-diagnostic APIs and libraries.

### WiCAN-OBD

![WiCAN-OBD](https://user-images.githubusercontent.com/94690098/231444160-08842087-55ad-4165-8291-b379da63aeeb.png)

- Ideal for car enthusiasts wanting to modernize head-unit displays.
- Supports RealDash for custom graphics interfaces.
- Sends battery alerts when voltage drops below a set threshold.

# WiCAN-USB
![image](https://user-images.githubusercontent.com/94690098/231443956-fbf2de46-ef19-4ba5-83b1-6058ab123f56.png)

WiCAN-USB can be powered through USB or through a screw-terminal connector. The hardware and firmware is almost identical to WiCAN-OBD, but with an extended voltage range up to 36 V. Many of the projects we work on involve 12 V or 24 V trucks, and WiCAN-USB comes in handy when we need to monitor those trucks remotely, from the comfort of our desks, by configuring them to connect to Wi-Fi. And, thanks to its low cost, we don’t need to worry about breaking or losing it the WiCAN-USB device itself.

WiCAN-USB can also be used as a USB-to-CAN adapter when Wi-Fi connectivity is not available or when a hardwired connection is needed.

# WiCAN-USB as a USB-to-CAN adapter

Traditional USB-to-CAN connectors are often expensive and do not always offer optimal efficiency. In contrast, the ESP32-based WiCAN presents a readily available and cost-effective solution, making it an excellent choice for low-frequency CAN bus applications and providing great value for its cost.

Although WiCAN performs well for low-frequency data transmission, it may not be suitable for all frequency ranges. Nonetheless, it excels in many applications, particularly those with less demanding data rates.

Integrating WiCAN into your setup is straightforward and economical, requiring either customised shields for your ESP-32 module or merely jumper wires. With minimal expense, you can significantly enhance your system's capabilities.

## Code Customization
To make the baud rate compatible with the dev-kit we changed it from 3000000 to 4000000.
Refer the file: []
## Setup Guide


## Configuration
### WiFi/CAN Configuration

1. Power the device using USB or OBD-II.
2. Connect to the WiFi AP with SSID WiCAN_xxxxxxxxxxxx using password: @meatpi#.
3. Open http://192.168.80.1/ in a web browser for configuration.
4. Change WiFi and CAN settings, then submit changes.

### BUSMaster Configuration

1. Use the provided BUSMaster version.
2. Configure device and BUSMaster settings as specified.
3. Connect to the device via TCP.

### RealDash Configuration

1. Configure device for WiFi or BLE.
2. Add RealDash CAN connection in the RealDash app.

### SavvyCAN Configuration

1. Connect to the device AP.
2. Open SavvyCAN and configure a new network connection.

### SocketCAN Configuration

#### WiFi

1. Set protocol to "slcan" in device configuration.
2. Create a virtual serial port over TCP on Linux:

```bash
sudo socat pty,link=/dev/netcan0,raw tcp:192.168.80.1:3333 &
sudo slcand -o -c -s8 /dev/netcan0 can0
sudo ifconfig can0 txqueuelen 1000
sudo ifconfig can0 up
```

#### USB

```bash
sudo slcand -o -s6 -t sw -S 4000000 /dev/ttyACM0 can0
sudo ifconfig can0 txqueuelen 1000
sudo ifconfig can0 up
```

## ELM327 OBD2 Protocol

1. Set protocol to "elm327" in device configuration.
2. Configure connection in RealDash for WiFi or BLE.

### RealDash OBD2 Configuration

1. Add OBD2 connection in RealDash.
2. Configure car protocol and request only first reply.

## MQTT

### Status

- Status Topic: `wican/xxxxxxxxxxxx/status`
- Status Message JSON: `{"status": "offline"}` or `{"status": "online"}`

### Transmit and Receive Frames

- Transmit Topic: `wican/xxxxxxxxxxxx/can/tx`
- Transmit Message JSON: 
```json
{"bus":0,"type":"tx","frame":[{"id":123,"dlc":8,"rtr":false,"extd":true,"data":[1,2,3,4,5,6,7,8]}]}
```

- Receive Topic: `wican/xxxxxxxxxxxx/can/rx`
- Received Message JSON: 
```json
{"bus":0,"type":"rx","ts":34610,"frame":[{"id":123,"dlc":8,"rtr":false,"extd":false,"data":[1,2,3,4,5,6,7,8]}]}
```

### OBDII PID Request Example

- Request Example: 
```json
{ "bus": "0", "type": "tx", "frame": [{ "id": 2015, "dlc": 8, "rtr": false, "extd": false, "data": [2, 1, 70, 170, 170, 170, 170, 170] }] }
```

- Response Example: 
```json
{"bus":"0","type":"rx","ts":51561,"frame":[{"id":2024,"dlc":8,"rtr":false,"extd":false,"data":[3,65,91,170,0,0,0,0]}]}
```

### CAN to JSON Interpreter

This feature converts CAN messages to JSON, applying calculations as specified.

- Configuration includes CAN ID, name, PID, start bit, bit length, expression, and cycle time.

- JSON Format Example:
```json
{"VehicleSpeed": 23.000000}
{"EngineSpeed": 1165.000000}
{"CustomID": 3594.400000}
```
