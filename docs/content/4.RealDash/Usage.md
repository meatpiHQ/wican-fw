## RealDash
WiCAN-OBD and WiCAN-USB can connect with [RealDash](https://realdash.net) using either WiFi or BLE. BLE is supported only on Android and iOS. Windows 10 supports only WiFi connections. Additionally, _WiCAN-USB can connect using the USB interface_.

**The protocol and CAN bitrate must be set using the configuration page**

### USB Device Configuration (WiCAN-USB only):
1. Go to garage then click on the dashboard.
1. Click Add connection.
1. Select Adapter (CAN/LIN)
1. Select RealDash CAN
1. Select SERIAL/USB
1. Select device
1. Set Baud RATE: 4000000 (This is the usb/serial buadrate not the CAN bitrate)
1. Click next then Done

### WiFi Device Configuration:

1. Go to configuration webpage.
1. Select the baudrate
1. Set "Port Type" = TCP
1. Set "Protocol" = reladash 66
1. Click submit changes.

### RealDash Configuration:
1. Go to garage then click on the dashboard.
1. Click Add connection.
1. Select Adapter (CAN/LIN)
1. Select RealDash CAN
1. Select WIFI/LAN
1. Enter IP and Port
1. Click Done

### BLE Device Configuration:

**If you're using firmware verion v1.64 or below please update to the [latest version](https://github.com/meatpiHQ/wican-fw/releases/) before enabling BLE**

1. Go to configuration webpage.
1. Select the baudrate
1. Set the "BLE Status" to enable

**Note: When the BLE is connected, the device will automatically turn off the WiFi configuration access point. Once BLE is disconnected the configuration access point will turn back on.**  

### OBD2

1. Go to garage then click on the dashboard.
1. Click Add connection.
1. Select Adapter ``` OBD2 ```
1. Select Bluetooth or WiFi
1. If WiFi fill in the IP 192.168.80.1 and port 3333.
1. Click on ``` OBD2 PROTOCOL ``` and select your car protocol, (11 bit ID, 500Kbit) or (11 bit ID, 250Kbit)
1. Activate ``` Request Only First Reply ```
1. Click Done.
