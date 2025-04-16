## SocketCAN

[SocketCAN](https://github.com/linux-can/) is a set of open source CAN drivers and a networking stack contributed by Volkswagen Research to the Linux kernel.

### 1. WiFi:
Change to protocol in the device configuration page to "slcan", then create a virtual serial port over TCP on your Linux machine. If WiCAN is connected to your home network replace "192.168.80.1" with device IP.

```
sudo socat pty,link=/dev/netcan0,raw tcp:192.168.80.1:3333 &
sudo slcand -o -c -s8 /dev/netcan0 can0
sudo ifconfig can0 txqueuelen 1000
sudo ifconfig can0 up
```

### 2. USB
```
sudo slcand -o -s6 -t sw -S 4000000 /dev/ttyACM0 can0
sudo ifconfig can0 txqueuelen 1000
sudo ifconfig can0 up
```
