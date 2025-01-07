---
title: Car Scanner Setup
---

## Introduction to Car Scanner App

Car Scanner is a powerful diagnostic application that allows you to monitor your vehicle's performance and read diagnostic data in real-time. When paired with a WiCAN OBD adapter, it provides access to your vehicle's onboard diagnostic system through a WiFi connection.


---

## Connecting the WiCAN OBD Adapter

### 1. Plug in the WiCAN Device
- Insert the WiCAN adapter into your vehicle's OBD-II port.

### 2. Connect to WiCAN
You have two options to establish a connection:
- **WiCAN Access Point:** Connect directly to WiCAN’s Wi-Fi hotspot.
- **Home Wi-Fi Network:** Connect the WiCAN device to your local Wi-Fi network.

### 3. Configure the WiCAN Device
- Open the **WiCAN Device Configuration Page** by navigating to its IP address in a web browser.  
  - Default IP for WiCAN Access Point: `192.168.80.1`
- Set the protocol to **ELM327**.
- (Optional) Disable **MQTT** and **BLE** to optimize performance.
- Submit changes. The device will reboot after saving the configuration.

---

## Setting Up the Car Scanner App

### 4. Configure the Adapter in Car Scanner
1. Open the Car Scanner app and navigate to:  
   **Settings -> Adapter OBDII ELM327 -> WiFi**.
2. Fill in the adapter's IP and port:
   - **If connected to your local Wi-Fi network:** Use the assigned IP address.
   - **If connected to the WiCAN Access Point:** Use the default IP `192.168.80.1`.
   - Port: `3333`.
   - Alternatively, use the mDNS address displayed in the WiCAN **Status** tab:  
     `wican_xxxxxxxxxxxxxxxx.local`.

### 5. Set the ECU Protocol
1. Scroll down in Car Scanner settings and select:  
   **Advanced Settings -> ECU Protocol**.
2. Choose the protocol that matches your vehicle. WiCAN does not support "Auto Protocol Detection," so manual selection is necessary. Start with one of the following common protocols:
   - **6) ISO 15765-4 CAN (11 bit ID, 500 Kbaud)**
   - **7) ISO 15765-4 CAN (29 bit ID, 500 Kbaud)**
   - **8) ISO 15765-4 CAN (11 bit ID, 250 Kbaud)**
   - **9) ISO 15765-4 CAN (29 bit ID, 250 Kbaud)**

   If these don't work, try other protocols in the list through trial and error until successful.

---

## Connecting to the Vehicle

### 6. Establish a Connection
- Return to the Car Scanner app's main page and press **Connect**.

### 7. Verify the Connection
If successful, the app will display:
- **ELM Connection:** Connected  
- **ECU Connection:** Connected  

### 8. View Supported Sensors
- Press **All Sensors** to check available data streams from your vehicle.

---

## Notes and Tips
- Always select the correct protocol manually, as WiCAN doesn’t support auto protocol detection.
- For the best performance, ensure WiCAN is configured optimally (e.g., disabling unused features like MQTT and BLE).
