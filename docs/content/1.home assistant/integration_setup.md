---
title: Integration Setup
---

# Using WiCAN with Home Assistant

WiCAN can be integrated with Home Assistant using the official integration. This guide will walk you through the setup process, both for configuring the WiCAN device and setting it up in Home Assistant. Follow these steps to make WiCAN communicate seamlessly with Home Assistant and provide real-time data from your vehicle.

## Integration Device Setup


<img width="373" alt="image" src="https://github.com/user-attachments/assets/3febdd0f-5013-4f70-aadd-dba9fdebf2ec">



To connect WiCAN to Home Assistant, you first need to connect it to the same WiFi network as Home Assistant.

1. **Connect to WiCAN Access Point**:  
   Connect to the device's access point named **WiCAN\_xxxxxxx**. The default password is `@meatpi#`. It is recommended **to change the password** for safety reasons.

2. **Note the mDNS Name**:  
   In the **Status** tab, take note of the mDNS name. It should look something like `wican_xxxxxxxxxxxx.local`. `xxxxxxxxxxxx` is the device unique ID.

3. **Connect WiCAN to Home WiFi**:  
   Connect WiCAN to the same WiFi network as Home Assistant. In the WiCAN settings, change the **Mode** to **AP+Station**, and fill in your WiFi SSID and password.

4. **Submit and Reboot**:  
   Click on the **Submit** button. The device will reboot on its own.

5. **Access WiCAN via mDNS**:  
   Disconnect from the WiCAN access point, and using your web browser, go to the mDNS name you noted earlier, e.g., `wican_xxxxxxxxxxxx.local`.

6. **Enable Vehicle Specific Settings**:  
   On the webpage, go to the **Automate** tab and enable **Vehicle Specific** settings.

7. **Sync Vehicle Model**:  
   Press the <img width="17" alt="image" src="https://github.com/user-attachments/assets/6988becc-fb6a-467a-8a5e-6ea83ae3e00c"> button next to **Vehicle Model** to fetch supported vehicle models. 

8. **Select Vehicle Model**:  
   Select your vehicle model from the dropdown list.  
   If your vehicle model is not yet supported, please open an issue on GitHub, and we will walk you through adding support.

9. **Set Cycle Time**:  
   Set the **Cycle Time** in milliseconds. This defines how often WiCAN will request parameters from the ECU.

10. **Destination Topic** (optional):  
   This field is only used for MQTT communication.

11. **Store and Reboot**:  
    Click on the **Store** button, then go to the **About** tab to reboot the device.


## Integration HA Setup

The WiCAN integration is available through HACS. You can find the integration repository [here](https://github.com/jay-oswald/ha-wican).

1. **Install the Integration via HACS**:  
   Add the integration repository link to HACS and install the **WiCAN** integration.

2. **Navigate to Integrations**:  
   In Home Assistant, go to **Settings** > **Devices & Services** > **Integrations**.

3. **Add WiCAN Hub**:  
   Click on **Add Integration**, search for **WiCAN**, and select it.

4. **Enter mDNS**:  
   Enter the mDNS name that you noted earlier (`wican_xxxxxxxxxxxx.local`) to connect the WiCAN device.

After completing these steps, WiCAN will be connected to Home Assistant, and you will be able to monitor the available car parameters directly from the Home Assistant interface.


<img width="899" alt="image" src="https://github.com/user-attachments/assets/c778fc1c-2929-4b4c-b661-f6a788200f32">



