---
title: MQTT
---

# Integrating Home Assistant and WiCAN via MQTT

In this guide, you'll learn how to set up WiCAN with Home Assistant using MQTT to monitor your vehicle's data in real time. This integration provides seamless access to your vehicle's sensors and allows for automatic Home Assistant discovery.

## Prerequisites

Before you begin, ensure that the following requirements are met:

- **Home Assistant OS** is installed on your device, and you have access to the add-ons feature.
- Your **WiCAN device** is set up and has a vehicle profile created.

## Step 1: Installing and Configuring the MQTT Add-on

1. [Install the MQTT add-on](https://github.com/home-assistant/addons/blob/master/mosquitto/DOCS.md) by following the official documentation.
2. Create a **WiCAN user** for Home Assistant by navigating to `Settings -> People -> Users`. Record the username and password.
3. Ensure WiCAN is operating in **Station Mode**.
4. In the **Settings** tab of the WiCAN interface, input the following settings:
    - **MQTT URL**: Use the IP or domain of your Home Assistant server. If you're using a reverse proxy, forward port 1883.
    - **MQTT Port**: The default port for Home Assistant is 1883.
    - **MQTT User**: The username you created in Step 2.
    - **MQTT Password**: The password you created in Step 2.
    - **TX Topic, RX Topic, Status Topic**: The default values are sufficient.
    - **MQTT ELM327 Log**: Leave this option disabled unless you are debugging.
    - **Protocol**: Set this to **AutoPID**.

## Step 2: Configuring WiCAN Automation

1. In the **Automate** tab of the WiCAN interface:
    - Upload the latest `vehicle_profiles.json` file. You can download it [here](https://github.com/meatpiHQ/WiCAN-fw/blob/main/vehicle_profiles.json) by pressing the download button.
    - Enable **Vehicle-Specific Settings**.
    - Select your vehicle from the dropdown menu.
    - Set the **Destination Topic** where your vehicle data will be sent.
    - Set the **Cycle Time** for sensor updates in milliseconds
    - Enable **Home Assistant Discovery** to automatically detect the sensors in Home Assistant.
    - Press `Store` to save your changes.
    
2. Restart your WiCAN device from the **About** tab.

Now, your WiCAN should start sending sensor data to Home Assistant. You can view the available sensors by navigating to [Entities](https://my.home-assistant.io/redirect/entities) in Home Assistant.

## Step 3: Debugging and Troubleshooting

### Using MQTT Explorer

If you encounter issues, you can use **MQTT Explorer** to monitor your MQTT data and identify potential problems. 

- MQTT Explorer is available as a [desktop app](https://mqtt-explorer.com/) or [Home Assistant add-on](https://community.home-assistant.io/t/addon-mqtt-explorer-new-version/603739).

1. Check if the `status` topic is receiving updates. If it is, this confirms that the MQTT connection is functioning correctly.
2. If no data appears in Home Assistant, ensure that:
    - Your WiCAN is connected to the correct MQTT server.
    - Home Assistant discovery is enabled in the WiCAN settings.

### Additional Tips

- **Cycle Time**: Adjust the cycle time if sensor updates are too frequent or too slow.
- **WiCAN Reboot**: If you encounter connection issues, reboot both your WiCAN and Home Assistant.

