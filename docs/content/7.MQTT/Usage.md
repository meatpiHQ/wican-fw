## CAN/ODBII over MQTT

To exchange CAN messages via MQTT the [MQTT client has to be enabled and configured](../0.Config/1.WiFi.md).

The [WiCAN device ID](../0.Config/1.WiFi.md) will also be needed to communicate with the device.

### 1. Status:

When the device connects to the MQTT broker it will publish a status message to the status topic.

- Topic: `wican/xxxxxxxxxxxx/status`
- Payload: `{"status": "offline"}`or `{"status": "online"}`

### 2. Request CAN frames (TX):

Request CAN frames have to follow this schema:
![image](/mqtt/transmit_json_schema.png)

Explanations:
- **bus**: Is always 0. Reserved for future application
- **type**: tx or rx 
- **dlc**: 0 to 8
- **rtr**: true or false
- **extd**: true or false
- **id**: 11 or 29 bit ID

**Example:**
- Topic: wican/xxxxxxxxxxxx/can/tx
- Payload: `{ "bus": "0", "type": "tx", "frame": [{ "id": 2015, "dlc": 8, "rtr": false, "extd": false, "data": [2, 1, 70, 170, 170, 170, 170, 170] }] };`

### 3. Receive CAN Frames (RX)

To receive CAN frames simply subscribe to the receive topic `wican/xxxxxxxxxxxx/can/rx`. Each MQTT message might contain more than 1 frame.

Received messages follow this JSON schema:
![image](/mqtt/recieve_json_schema.png)

**Example:**
- Topic: wican/xxxxxxxxxxxx/can/rx
- Payload: `{"bus":0,"type":"rx","ts":34610,"frame":[{"id":123,"dlc":8,"rtr":false,"extd":false,"data":[1,2,3,4,5,6,7,8]},{"id":124,"dlc":8,"rtr":false,"extd":true,"data":[1,2,3,4,5,6,7,8]}]}`


### 3. OBDII PID Request Over MQTT

There is [list of standard PIDs](https://en.wikipedia.org/wiki/OBD-II_PIDs).

The frames meet the following criteria:
- **bus**: Is always 0. Reserved for future application
- **dlc**: Always 8
- **rtr**: false
- **extd**: false, if the car obd2 protocol is 11bit ID, 500 kbaud or 250 kbaud
- **extd**: true, if the car obd2 protocol is 29bit ID, 500 kbaud or 250 kbaud
- **id**: 11 bit OBD2 request ECU ID should be 2015 (that's 0x7DF in HEX)
- **id**: 29 bit OBD2 request ECU ID should be 417018865 (that's 0x18DB33F1 in HEX)

![image](/mqtt/pid_request.png)

#### Example: Ambient temperature
Get ambient temp request, PID is 70
 
##### Request
- Topic: wican/xxxxxxxxxxxx/can/tx
- Payload: `{ "bus": "0", "type": "tx", "frame": [{ "id": 2015, "dlc": 8, "rtr": false, "extd": false, "data": [2, 1, 70, 170, 170, 170, 170, 170] }] };`


#### Example: Battery SoC
This PID request should work on most EVs, **however it's not possible to know it will work on certain EV model unless it's tested on that specific car model**.

#### Request
- Topic: wican/xxxxxxxxxxxx/can/tx
- Payload: `{"bus":"0","type":"tx","frame":[{"id":2015,"dlc":8,"rtr":false,"extd":false,"data":[2,1,91,170,170,170,170,170]}]}`

#### Response
- Topic: wican/xxxxxxxxxxxx/can/rx
- Payload: `{"bus":"0","type":"rx","ts":51561,"frame":[{"id":2024,"dlc":8,"rtr":false,"extd":false,"data":[3,65,91,**170**,0,0,0,0]}]}`

The SoC is calculated as (170 x 100)/255 = **66.67%**.

### 4. CAN to JSON interpreter - Filtering

This feature enables you to convert CAN messages into JSON format, apply calculations as specified by your expressions, and send the resulting data to an MQTT broker at predefined intervals. Below, we provide a comprehensive guide on how to effectively utilize this powerful feature.

**Note: When a filter is added, all other CAN messages will be ignored, ensuring that only the configured filtered messages are sent.**

![image](/mqtt/filtering.png)


### Configuration Parameters:

**CAN ID: Frame ID in DEC**
This parameter refers to the CAN message identifier in decimal format.

**Name**
Specify the JSON key under which the extracted value will be stored.

**PID: PID Number in DEC**
If the frame is a response to a PID request, set this parameter to the relevant PID number. Otherwise, for non-PID frames, it should be set to -1.

**Index: PID index**
For all standard PID requests, this should be set to 2, referring to byte 2 in the response. In certain special cases, this value may differ from 2. If the PID is set to -1, the interpreter will disregard this value.

**Start Bit**
Indicate the bit from which you want to commence extracting the value. For instance, if Byte 0 is represented as 0xA0 in binary (b1010 0000), 'Bit 0' would correspond to 1, 'Bit 1' to 0, and so forth.

**Bit Length**
This parameter denotes the number of bits comprising the value you intend to extract.

**Expression**
Define the mathematical operations to apply to the extracted value. It's crucial to include the letter 'V' to represent the extracted value within your expression.

**Cycle**
Specify the time interval in milliseconds between JSON messages sent to MQTT. The cycle time should fall within the range of 100 to 10,000 milliseconds.


#### JSON Format

WiCAN processes CAN frames, and the resulting JSON data follows this structure:

```
{"VehicleSpeed": 23.000000}
{"EngineSpeed": 1165.000000}
{"CustomID": 3594.400000}
```

### PID response frames Example:

Name|CAN ID| Byte 0 | Byte 1 | Byte 2 | Byte 3 | Byte 4 | Byte 5 | Byte 6 | Byte 7 
---|--- | --- | --- | --- |--- |--- |--- |--- |---
Engine Speed|2024| 4| 65| **12**| _18_| _52_| 170| 170| 170
| |  |  | | PID| Value 1| Value 2  |  |

The Engine Speed is PID response so "PID" must be set 12. The value we need to extract is 16bit in Byte 3 and Byte 4. So we set the "Start Bit" to 24 and "Bit Length" to 16. The extracted value will be (11 * 256)+68 = 4660. Now to get the engine speed in rpm we must multiple by 0.25. So the "Expression" will be V * 0.25= 1165

Name|CAN ID| Byte 0 | Byte 1 | Byte 2 | Byte 3 | Byte 4 | Byte 5 | Byte 6 | Byte 7 
---|--- | --- | --- | --- |--- |--- |--- |--- |---
Vehicle Speed|2024| 4| 65| **13**| _23_|170| 170| 170| 170
| |  |  | | PID| Value 1|   |  |

The Vehicle  Speed is PID response so "PID" must be set 13. The value we need to extract is 8bit in Byte 3. So we set the "Start Bit" to 24 and "Bit Length" to 8. The extracted value will be 23. No need for any operation to get the speed so we set the "Expression" to V, so the value will be 23.

### Other CAN Frames Example:

Name|CAN ID| Byte 0 | Byte 1 | Byte 2 | Byte 3 | Byte 4 | Byte 5 | Byte 6 | Byte 7 
---|--- | --- | --- | --- |--- |--- |--- |--- |---
Custom ID|356| 170| 170| _35_| _16_|170| 170| 170| 170

The PID in the configuration needs to set to -1. The value we need to extract is 16bit in Byte 2 and Byte 3. So we set the "Start Bit" to 16 and "Bit Length" to 16. The extracted value will be (35*256)+16 = 8976. Now to get the value we must add 10 to the Value and divide by 2.5. So the "Expression" will be (V+10)/2.5 = 3594.4

Name|CAN ID| Byte 0 | Byte 1 | Byte 2 | Byte 3 | Byte 4 | Byte 5 | Byte 6 | Byte 7 
---|--- | --- | --- | --- |--- |--- |--- |--- |---
Custom ID|355| 170| 170| 170| 170|_5_| _34_| 170| 170

The PID in the configuration should be set to -1. The values to extract are from Byte 4 and Byte 5. The formula used here is (5 + 34)/2, so the expression to set is (B4 + B5)/2.