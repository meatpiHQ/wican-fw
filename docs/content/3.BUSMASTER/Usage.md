## BUSMASTER
[BUSMASTER](https://rbei-etas.github.io/busmaster/) is an Open Source Software tool to Simulate, Analyze and Test data bus systems such as CAN, LIN.
You need to download the right version of BUSMaster provided in this [**Link**](https://drive.google.com/drive/folders/1qJelUAHGrn_YbNIP0Jk_KmNENG-hKbtl?usp=sharing).

Here is how to set up the hardware:

### **Device Configuration:**

1. Go to configuration webpage.
1. Baudrate can be set in BUSMaster configuration
1. Set "Port Type" = TCP
1. Set "Protocol" = slcan
1. Click submit changes.

### **BUSMaster Configuration:**

1. Select VSCom CAN-API by clicking on 'Driver Selection -> VSCom CAN-API"
1. Then Click on 'Channel Configuration -> Advanced'
1. Fill in the IP and port. **Example: 192.168.80.1:3333**
1. Check the 'Hardware Timestamps' check box.
1. Choose the Baudrate.
1. Click 'OK', then Click the Connect button on the top left corner.

![image](/busmaster/vscan_api_device_config.png)
