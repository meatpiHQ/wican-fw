#!/usr/bin/env bash
# Join a WiCAN's own access point from one of the Pi's free radios, so a
# DUT that just lost its station settings (a factory reset: the config
# backup bench's b9 leg, issue #987, 2026-10-16) can still be reached:
#
#   dut_ap_join.sh up <ssid> [psk] [ifname]   # waits for the AP, joins, prints the gateway
#   dut_ap_join.sh down [ifname]              # leaves and deletes the profile
#
# The profile is `wican-dut-reset` (added with sudo -n: the bench user may not
# add connections by itself), autoconnect off, WPA2-PSK spelled out
# (NetworkManager's default key management refuses the device's WPA2 AP).
# The PSK defaults to the firmware's factory AP password. The gateway of the
# joined network is the DUT (192.168.0.10 on a fresh unit); the PC reaches
# it through a forward: ssh -N -L 18082:<gateway>:80 rpi001. Run ON the Pi.
set -u
MODE="${1:-}"
PROFILE=wican-dut-reset

case "$MODE" in
    up)
        SSID="${2:?ssid}"
        PSK="${3:-@meatpi#}"
        IF="${4:-wtest1}"
        sudo -n nmcli con delete "$PROFILE" >/dev/null 2>&1 || true
        sudo -n nmcli con add type wifi ifname "$IF" con-name "$PROFILE" ssid "$SSID" \
            wifi-sec.key-mgmt wpa-psk wifi-sec.psk "$PSK" ipv4.method auto \
            connection.autoconnect no >/dev/null || { echo "profile not added"; exit 2; }
        deadline=$((SECONDS + 150))
        while [ $SECONDS -lt $deadline ]; do
            if nmcli -t -f SSID dev wifi list ifname "$IF" --rescan yes 2>/dev/null | grep -qx "$SSID"; then
                break
            fi
            sleep 5
        done
        nmcli -t -f SSID dev wifi list ifname "$IF" 2>/dev/null | grep -qx "$SSID" || { echo "AP $SSID not seen within 150 s"; exit 3; }
        sudo -n nmcli con up "$PROFILE" >/dev/null 2>&1 || { echo "join failed"; exit 4; }
        GW=$(nmcli -g IP4.GATEWAY dev show "$IF" 2>/dev/null | head -1)
        for i in $(seq 1 20); do
            if curl -s -m 3 "http://$GW/api/info" | grep -q device_id; then
                echo "DUT AP joined on $IF, gateway $GW"
                exit 0
            fi
            sleep 3
        done
        echo "joined $SSID on $IF (gateway $GW) but /api/info does not answer"
        exit 5
        ;;
    down)
        IF="${2:-wtest1}"
        sudo -n nmcli con down "$PROFILE" >/dev/null 2>&1 || true
        sudo -n nmcli con delete "$PROFILE" >/dev/null 2>&1 || true
        echo "left the DUT AP on $IF, profile deleted"
        ;;
    *)
        echo "usage: $0 up <ssid> [psk] [ifname] | down [ifname]"
        exit 1
        ;;
esac
