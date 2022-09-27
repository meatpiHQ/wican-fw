# Setup battery alerts in Home Assistant

![image](https://user-images.githubusercontent.com/94690098/192513730-79944a6d-73d0-4bd3-934e-1b2432c083f0.png)


In this update I’d like to talk about battery alerts and how to set it up. Battery alerts are particularly useful if you own multiple cars that you don't often use. All cars have "parasitic current draw"  between 50mA to 85mA which eventually empties the battery if the car is not turned on, some cars can last about 30 days or 4 weeks before it needs to be jump started. WiCAN can help you keep an eye on the battery voltage as the battery discharges and send you an alert when the preset value is reached. In this tutorial you’ll create a simple Mqtt sensor and automation in Home Assistant, so when low battery is detected a text to speech notification will be sent to Google Home speaker.

Battery alerts are easy to set up, assuming you have a wifi connection where you park your car and you have a home assistant running on your network. Here’s how to set it up:

1- Install Home Assistant [Mosquitto broker add-on](https://github.com/home-assistant/addons/blob/master/mosquitto/DOCS.md)

2- Create Home Assistant new user account for WiCAN. These user credentials will be used to set up the mqtt setting for WiCAN.

3- Connect to WiCAN access point WiCAN_xxxxxxxxxxxx, then using a web browser, go to http://192.168.80.1/

4- Enable Sleep mode and Battery Alert.

5- Fill in the Battery Alert configuration. In the Alert URL fill in your Home Assistant ip address and the Mqtt User/password created in step 2.

![image](https://user-images.githubusercontent.com/94690098/192513194-0decddc1-0be3-4231-bece-814d26671766.png)

6- To create a new MQTT sensor, you need to edit “configuration.yaml” file and add the following lines to the file:
mqtt:
  sensor:
    - name: "Car Battery"
      state_topic: "CAR1/voltage"
      unit_of_measurement: "V"
      value_template: "{{ value_json.battery_voltage }}"

7- Restart Home Assistant, then edit your dashboard to add a new card with “Car Battery” entity.

8- Finally create a new automation by setting the MQTT topic, entity name and battery voltage value.

![image](https://user-images.githubusercontent.com/94690098/192513519-9f531503-b93f-4712-b57d-7f8dd06ba4dc.png)
