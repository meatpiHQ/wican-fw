# demo example

## What the example does

The example connects to a WireGuard server. When the link is up, the device
sends ICMP echo requests, and shows ping statistics. The ICMP session loops
forever.

The main task then disconnects from the peer, and re-connects to the peer.

## Requirements

* An ESP32 or ESP8266 development board
* WiFi network
* [`wireguard-tools`](https://github.com/WireGuard/wireguard-tools)
* A WireGuard server

## Generating keys

```console
wg genkey | tee private.key | wg pubkey > public.key
```

## Log

```console
I (100) esp_image: segment 0: paddr=00010020 vaddr=3f400020 size=1600ch ( 90124) map
I (141) esp_image: segment 1: paddr=00026034 vaddr=3ffb0000 size=03f0ch ( 16140) load
I (148) esp_image: segment 2: paddr=00029f48 vaddr=40080000 size=060d0h ( 24784) load
I (158) esp_image: segment 3: paddr=00030020 vaddr=400d0020 size=7691ch (485660) map
I (334) esp_image: segment 4: paddr=000a6944 vaddr=400860d0 size=0f14ch ( 61772) load
I (360) esp_image: segment 5: paddr=000b5a98 vaddr=50000000 size=00010h (    16) load
I (371) boot: Loaded app from partition at offset 0x10000
I (371) boot: Disabling RNG early entropy source...
I (382) cpu_start: Pro cpu up.
I (382) cpu_start: Starting app cpu, entry point is 0x4008127c
0x4008127c: call_start_cpu1 at /usr/home/trombik/github/esp-idf/components/esp_system/port/cpu_start.c:150

I (0) cpu_start: App cpu up.
I (397) cpu_start: Pro cpu start user code
I (397) cpu_start: cpu freq: 160000000
I (397) cpu_start: Application information:
I (401) cpu_start: Project name:     demo
I (406) cpu_start: App version:      4a3c45b
I (411) cpu_start: Compile time:     Jan  6 2022 15:39:56
I (417) cpu_start: ELF file SHA256:  45cef6b78497cd9f...
I (423) cpu_start: ESP-IDF:          v4.3.2
I (428) heap_init: Initializing. RAM available for dynamic allocation:
I (435) heap_init: At 3FFAE6E0 len 00001920 (6 KiB): DRAM
I (441) heap_init: At 3FFB8150 len 00027EB0 (159 KiB): DRAM
I (447) heap_init: At 3FFE0440 len 00003AE0 (14 KiB): D/IRAM
I (454) heap_init: At 3FFE4350 len 0001BCB0 (111 KiB): D/IRAM
I (460) heap_init: At 4009521C len 0000ADE4 (43 KiB): IRAM
I (467) spi_flash: detected chip: generic
I (471) spi_flash: flash io: dio
W (475) spi_flash: Detected size(4096k) larger than the size in the binary image header(2048k). Using the size in the binary image header.
I (489) cpu_start: Starting scheduler on PRO CPU.
I (0) cpu_start: Starting scheduler on APP CPU.
I (602) wifi:wifi driver task: 3ffc1e00, prio:23, stack:6656, core=0
I (602) system_api: Base MAC address is not set
I (602) system_api: read default base MAC address from EFUSE
I (622) wifi:wifi firmware version: eb52264
I (622) wifi:wifi certification version: v7.0
I (622) wifi:config NVS flash: enabled
I (622) wifi:config nano formating: disabled
I (632) wifi:Init data frame dynamic rx buffer num: 32
I (632) wifi:Init management frame dynamic rx buffer num: 32
I (642) wifi:Init management short buffer num: 32
I (642) wifi:Init dynamic tx buffer num: 32
I (652) wifi:Init static rx buffer size: 1600
I (652) wifi:Init static rx buffer num: 10
I (662) wifi:Init dynamic rx buffer num: 32
I (662) wifi_init: rx ba win: 6
I (662) wifi_init: tcpip mbox: 32
I (672) wifi_init: udp mbox: 6
I (672) wifi_init: tcp mbox: 6
I (672) wifi_init: tcp tx win: 5744
I (682) wifi_init: tcp rx win: 5744
I (682) wifi_init: tcp mss: 1440
I (692) wifi_init: WiFi IRAM OP enabled
I (692) wifi_init: WiFi RX IRAM OP enabled
I (702) phy_init: phy_version 4670,719f9f6,Feb 18 2021,17:07:07
I (812) wifi:mode : sta (24:62:ab:ff:2f:d0)
I (812) wifi:enable tsf
I (822) wifi:new:<11,0>, old:<1,0>, ap:<255,255>, sta:<11,0>, prof:1
I (822) wifi:state: init -> auth (b0)
I (832) wifi:state: auth -> assoc (0)
I (832) wifi:state: assoc -> run (10)
I (852) wifi:connected with makers, aid = 2, channel 11, BW20, bssid = 18:c2:bf:d2:de:d8
I (852) wifi:security: WPA2-PSK, phy: bg, rssi: -85
I (862) wifi:pm start, type: 1

I (952) wifi:AP's beacon interval = 102400 us, DTIM period = 2
I (2082) esp_netif_handlers: sta ip: 192.168.99.52, mask: 255.255.255.0, gw: 192.168.99.254
I (2082) demo: got ip:192.168.99.52
I (2082) demo: Connected to ap SSID:makers
I (2092) sync_time: Initializing SNTP
I (2092) sync_time: Waiting for system time to be set... (1/20)
I (3792) sync_time: Time synced
I (4102) demo: The current date/time in New York is: Thu Jan  6 03:40:19 2022
I (4102) demo: Initializing WireGuard.
I (4102) demo: Connecting to the peer.
I (4102) esp_wireguard: allowed_ip: 192.168.4.58
I (4162) esp_wireguard: Peer: 192.168.99.19 (192.168.99.19:12912)
I (4212) esp_wireguard: Connecting to 192.168.99.19:12912
I (5212) demo: Peer is down
I (6212) demo: Peer is down
I (7212) demo: Peer is down
I (8212) demo: Peer is up
I (8212) demo: Initializing ping...
I (8212) demo: ICMP echo target: 192.168.4.1
I (8222) demo: 64 bytes from 192.168.4.1 icmp_seq=1 ttl=255 time=6 ms
I (9222) demo: 64 bytes from 192.168.4.1 icmp_seq=2 ttl=255 time=9 ms
I (10222) demo: 64 bytes from 192.168.4.1 icmp_seq=3 ttl=255 time=7 ms
I (11222) demo: 64 bytes from 192.168.4.1 icmp_seq=4 ttl=255 time=6 ms
I (12212) demo: 64 bytes from 192.168.4.1 icmp_seq=5 ttl=255 time=3 ms
I (13222) demo: 64 bytes from 192.168.4.1 icmp_seq=6 ttl=255 time=7 ms
I (14222) demo: 64 bytes from 192.168.4.1 icmp_seq=7 ttl=255 time=8 ms
I (15212) demo: 64 bytes from 192.168.4.1 icmp_seq=8 ttl=255 time=4 ms
I (16212) demo: 64 bytes from 192.168.4.1 icmp_seq=9 ttl=255 time=2 ms
I (17222) demo: 64 bytes from 192.168.4.1 icmp_seq=10 ttl=255 time=12 ms
I (18212) demo: Disconnecting.
I (18212) demo: Disconnected.
I (19212) demo: From 192.168.4.1 icmp_seq=11 timeout
I (20212) demo: From 192.168.4.1 icmp_seq=12 timeout
I (21212) demo: From 192.168.4.1 icmp_seq=13 timeout
I (22212) demo: From 192.168.4.1 icmp_seq=14 timeout
I (23212) demo: From 192.168.4.1 icmp_seq=15 timeout
I (24212) demo: From 192.168.4.1 icmp_seq=16 timeout
I (25212) demo: From 192.168.4.1 icmp_seq=17 timeout
I (26212) demo: From 192.168.4.1 icmp_seq=18 timeout
I (27212) demo: From 192.168.4.1 icmp_seq=19 timeout
I (28212) demo: From 192.168.4.1 icmp_seq=20 timeout
I (28212) demo: Connecting.
I (28212) esp_wireguard: allowed_ip: 192.168.4.58
I (28262) esp_wireguard: Peer: 192.168.99.19 (192.168.99.19:12912)
I (28312) esp_wireguard: Connecting to 192.168.99.19:12912
I (29212) demo: From 192.168.4.1 icmp_seq=21 timeout
I (30212) demo: From 192.168.4.1 icmp_seq=22 timeout
I (31212) demo: From 192.168.4.1 icmp_seq=23 timeout
I (32212) demo: From 192.168.4.1 icmp_seq=24 timeout
I (33212) demo: From 192.168.4.1 icmp_seq=25 timeout
I (34312) demo: Peer is up
I (35132) demo: From 192.168.4.1 icmp_seq=26 timeout
I (35132) demo: 64 bytes from 192.168.4.1 icmp_seq=27 ttl=255 time=3 ms
I (35212) demo: 64 bytes from 192.168.4.1 icmp_seq=28 ttl=255 time=5 ms
I (36212) demo: 64 bytes from 192.168.4.1 icmp_seq=29 ttl=255 time=3 ms
I (37222) demo: 64 bytes from 192.168.4.1 icmp_seq=30 ttl=255 time=6 ms
I (38212) demo: 64 bytes from 192.168.4.1 icmp_seq=31 ttl=255 time=3 ms
I (39222) demo: 64 bytes from 192.168.4.1 icmp_seq=32 ttl=255 time=5 ms
I (40222) demo: 64 bytes from 192.168.4.1 icmp_seq=33 ttl=255 time=13 ms
I (41222) demo: 64 bytes from 192.168.4.1 icmp_seq=34 ttl=255 time=5 ms
I (42222) demo: 64 bytes from 192.168.4.1 icmp_seq=35 ttl=255 time=7 ms
I (43222) demo: 64 bytes from 192.168.4.1 icmp_seq=36 ttl=255 time=8 ms
I (44232) demo: 64 bytes from 192.168.4.1 icmp_seq=37 ttl=255 time=18 ms
I (44312) demo: Disconnecting.
I (44312) demo: Disconnected.
I (46212) demo: From 192.168.4.1 icmp_seq=38 timeout
I (47212) demo: From 192.168.4.1 icmp_seq=39 timeout
I (48212) demo: From 192.168.4.1 icmp_seq=40 timeout
I (49212) demo: From 192.168.4.1 icmp_seq=41 timeout
I (50212) demo: From 192.168.4.1 icmp_seq=42 timeout
I (51212) demo: From 192.168.4.1 icmp_seq=43 timeout
I (52212) demo: From 192.168.4.1 icmp_seq=44 timeout
I (53212) demo: From 192.168.4.1 icmp_seq=45 timeout
I (54212) demo: From 192.168.4.1 icmp_seq=46 timeout
I (54312) demo: Connecting.
I (54312) esp_wireguard: allowed_ip: 192.168.4.58
I (54362) esp_wireguard: Peer: 192.168.99.19 (192.168.99.19:12912)
I (54412) esp_wireguard: Connecting to 192.168.99.19:12912
I (55212) demo: From 192.168.4.1 icmp_seq=47 timeout
I (56212) demo: From 192.168.4.1 icmp_seq=48 timeout
I (57212) demo: From 192.168.4.1 icmp_seq=49 timeout
I (58212) demo: From 192.168.4.1 icmp_seq=50 timeout
I (59212) demo: From 192.168.4.1 icmp_seq=51 timeout
I (60212) demo: From 192.168.4.1 icmp_seq=52 timeout
I (61212) demo: From 192.168.4.1 icmp_seq=53 timeout
I (61412) demo: Peer is up
I (62272) demo: From 192.168.4.1 icmp_seq=54 timeout
I (62272) demo: 64 bytes from 192.168.4.1 icmp_seq=55 ttl=255 time=4 ms
I (63212) demo: 64 bytes from 192.168.4.1 icmp_seq=56 ttl=255 time=4 ms
I (64212) demo: 64 bytes from 192.168.4.1 icmp_seq=57 ttl=255 time=3 ms
I (65222) demo: 64 bytes from 192.168.4.1 icmp_seq=58 ttl=255 time=5 ms
I (66212) demo: 64 bytes from 192.168.4.1 icmp_seq=59 ttl=255 time=3 ms
I (67212) demo: 64 bytes from 192.168.4.1 icmp_seq=60 ttl=255 time=3 ms
I (68212) demo: 64 bytes from 192.168.4.1 icmp_seq=61 ttl=255 time=3 ms
I (69212) demo: 64 bytes from 192.168.4.1 icmp_seq=62 ttl=255 time=3 ms
```
