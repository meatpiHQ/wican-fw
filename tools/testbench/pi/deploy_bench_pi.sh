#!/bin/bash
# deploy_bench_pi.sh — install/refresh the rpi001 bench toolkit.
# Run ON the Pi from this directory (synced via the usual tar/scp):
#   ssh rpi001 "cd ~/wican/tools/testbench/pi && bash deploy_bench_pi.sh"
# Idempotent. Prints REBOOT REQUIRED when the udev role names are not
# live yet (a rename only applies at enumeration).
set -e
cd "$(dirname "$0")"

echo "== installing bench toolkit"
sudo install -m 644 bench-lib.sh /usr/local/lib/bench-lib.sh
sudo install -m 755 bench-health bench-radio-reset bench-recover \
     bench-startup.sh wican-bench-watchdog.sh /usr/local/bin/
sudo install -m 644 70-bench-radios.rules /etc/udev/rules.d/
sudo install -m 644 50-usb-wifi-nosuspend.rules /etc/udev/rules.d/
sudo install -m 644 bench-startup.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable bench-startup.service >/dev/null
sudo systemctl enable wican-bench-watchdog.timer >/dev/null 2>&1 || true

echo "== rebinding NM profiles to role names"
# wican-bench-w0 was interface-name-bound to wlan0; bind to wint0 (the
# udev-pinned name of the internal radio).
sudo nmcli connection modify wican-bench-w0 connection.interface-name wint0
# stale duplicate uplink profile must never grab a test radio
sudo nmcli connection modify "Nachos_8042_5G 1" connection.autoconnect no 2>/dev/null || true

# The twin and uplink profiles are MAC-PINNED, so they survive interface
# renames but NOT a stick swap — a replaced radio leaves them bound to a
# MAC that no longer exists and they silently never activate (2026-07-31,
# the MT7612U swap). Re-pin them from bench_role_mac(), the single source
# of truth, on every deploy.
. ./bench-lib.sh
repin() {  # <connection> <role>
    local con="$1" mac
    mac=$(bench_role_mac "$2") || return 0
    sudo nmcli connection show "$con" >/dev/null 2>&1 || return 0
    sudo nmcli connection modify "$con" 802-11-wireless.mac-address "$mac"
    echo "   re-pinned '$con' -> $2 ($mac)"
}
repin "$HOTSPOT_TWIN" wtest0
repin "$UPLINK_CON"   wtest1

echo "== state dir"
sudo mkdir -p /var/lib/bench

if [ -e /sys/class/net/wint0 ]; then
    echo "== role names live - running bench-health"
    bench-health || true
else
    echo "REBOOT REQUIRED: role names (wint0/wtest0/wtest1) apply at next boot"
fi
echo "DEPLOY DONE"
