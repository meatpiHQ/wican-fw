#!/usr/bin/env python3
"""Count IPv4 packets from the DUT to the tailnet range (100.64.0.0/10) that
appear in clear text on the bench AP's interface. Run on the Pi as root:
    sudo -n python3 ts_leak_sniff.py <seconds> <dut-ip> [iface]
A tunnel packet never shows here (it is UDP to a public address or TCP to a
relay); a line printed here is a packet that left on the station instead."""
import socket
import struct
import sys
import time

dur = int(sys.argv[1])
dut = sys.argv[2]
iface = sys.argv[3] if len(sys.argv) > 3 else "usb0"
s = socket.socket(socket.AF_PACKET, socket.SOCK_RAW, socket.ntohs(0x0800))
s.bind((iface, 0))
s.settimeout(1.0)
dutb = socket.inet_aton(dut)
end = time.time() + dur
n = 0
print("SNIFF START %s on %s for %d s" % (time.strftime("%H:%M:%S"), iface, dur), flush=True)
while time.time() < end:
    try:
        pkt = s.recv(2048)
    except socket.timeout:
        continue
    ip = pkt[14:34]
    if len(ip) < 20:
        continue
    src, dst = ip[12:16], ip[16:20]
    if src != dutb or dst[0] != 100 or not 64 <= dst[1] <= 127:
        continue
    n += 1
    info = ""
    if ip[9] == 6:
        ihl = (ip[0] & 15) * 4
        tcp = pkt[14 + ihl:14 + ihl + 20]
        if len(tcp) >= 14:
            sport, dport = struct.unpack("!HH", tcp[:4])
            info = " tcp %d->%d flags=0x%02x" % (sport, dport, tcp[13])
    print("%s LEAK %s -> %s proto=%d%s" % (time.strftime("%H:%M:%S"), dut,
                                           socket.inet_ntoa(dst), ip[9], info), flush=True)
print("SNIFF DONE %s leaks=%d over %d s" % (time.strftime("%H:%M:%S"), n, dur), flush=True)
