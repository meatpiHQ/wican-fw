# Features
--------

## 1. Sleep Mode:

WiCAN can be permanently attached to the car without worrying about draining the battery. It monitors the battery voltage and can detect when the alternator is ON. When the engine is ON the battery continuously charging and the voltage will be about 13.5V(can vary with different car models). When the engine is OFF the battery voltage drops to about 12.8V(full battery) -  WiCAN can detect this drop, and if the voltage remains under the threshold for **3 min** it will go to sleep and the current consumption will drop below **1mA**. If the engine is turned ON it will immediately wake up and enable the WiFi/BLE. 
The threshold voltage is configurable and can be set in the configuration page, the default value is 13V. 

## 2. Battery Alert:

This is an important feature for most car enthusiast who own multiple cars that are only driven few times a year.  Basically if a car is not used in few month the battery will go flat and needs to be jumped. WiCAN can be configured to send an alert when the battery voltage drops under a certain level. Sleep mode has to be enabled for this feature to be useful. **For now alerts can be sent on MQTT, more protocols are coming soon. If there is a specific protocol you want to be supported let me know.**

![image](/battery-alert.png)

# **Features Wishlist**:

- ~~CAN Auto baudrate~~ **DONE**
- ~~Support ELM327 OBD2 protocol~~ **DONE** (Basic support) 
- ~~Home Assistant Integrations~~ **DONE**
- Extended ELM327 support 
- Support ELM327 protocol and Realdash protocol simultaneously on WiFi and BLE 
- Add option to send an Email on battery alert 
- Expand alerts to include other CAN parameters parsed from DBC files.
- Support for extra wifi settings