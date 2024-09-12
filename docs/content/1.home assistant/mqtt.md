---
title: MQTT
---

# Home Assistant and WiCan over MQTT
This tutorial will show you how to get up and running with Home Assistant and WiCan using MQTT. We are making the below assumptions

* Home Assistant OS is installed, so you can use addons.
* Your Vehicle already has a Vehicle Profile created

## Installing and Configuring MQTT
* [Follow the official docs](https://github.com/home-assistant/addons/blob/master/mosquitto/DOCS.md) for installing MQTT Addon
* Create a WiCan user for HA via `Settings -> People -> Users`. Make sure to record the username and password
* Make sure your WiCan has Station mode enabled
* On the `Settings` Tab of the WiCan website fill in the following settings
    * `MQTT URL` This should be the IP/domain of your HA server. If you are using a reverse proxy you will also need to forward port 1883
    * `MQTT Port` HA uses the default port 1883
    * `MQTT User` This is the user you just created
    * `MQTT Pass` The Password for the user you just created
    * `TX Topic` Default is fine
    * `RX Topic` Default is fine
    * `Status Topic` Default is fine
    * `MQTT elm327 log` Leave Disabled
    * `Protocol` Change to "AutoPID"
* On the `Automate` Tab
    * Upload the latest `vehicle_profiles.json` you view it [here](https://github.com/meatpiHQ/wican-fw/blob/main/vehicle_profiles.json) Just press the download button on the file.
    * Enable Vehicle Specific
    * Select your car from the dropdown.
    * Enable Grouping
    * Enable Home Assistant Discovery
    * Press `Store`
Thats it, restart the WiCan from the `About` tab, and you should start to get values in Home Assistant from the WiCan.

## Debugging

### MQTT Explorer
You can use the MQTT Explorer to view the MQTT data to see whats happening. This can be downloaded as a desktop app, or [as a HA addon](https://community.home-assistant.io/t/addon-mqtt-explorer-new-version/603739)

If you are having issues getting it working we recommend using MQTT explorer to make sure your `status` topic is getting a status, this at least confirms the MQTT part is at least working.