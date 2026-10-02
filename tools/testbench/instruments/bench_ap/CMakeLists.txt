# bench_ap: the bench's "home WiFi" as an instrument (ESP32-P4 Function EV
# board, WiFi through the on-board ESP32-C6 over esp_hosted). A layer-2
# bridge between the board's Ethernet and a WPA2 soft-AP; the Pi on the
# Ethernet side stays the network's brain (DHCP, mDNS, broker).
cmake_minimum_required(VERSION 3.22)

include($ENV{IDF_PATH}/tools/cmake/project.cmake)
idf_build_set_property(MINIMAL_BUILD ON)
project(bench_ap)
