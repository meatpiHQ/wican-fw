#!/usr/bin/env bash
# The Pi side of the bench_ap instrument: share 10.42.1.0/24 (DHCP via NM's
# dnsmasq, the subnet the mt76 hotspot served) on the P4's CDC-NCM interface,
# and park the mt76 hotspot while bench_ap serves the DUT.
#
# Lease-file shim: the bench scripts read /var/lib/NetworkManager/
# dnsmasq-wtest0.leases (the hotspot's file). While bench_ap serves, that path
# becomes a symlink to the NCM interface's file, so discovery works unchanged;
# `down` puts the real file back (the same kind of shim bench-startup.sh keeps
# for the pre-rename wlan0/wlan1 names).
#
#   bench_ap_net.sh up      detect the cdc_ncm interface, bring wican-bench-usb up, park wican-bench, shim the leases
#   bench_ap_net.sh down    the other way round
#   bench_ap_net.sh status
set -u
CON=wican-bench-usb
HOTSPOT=wican-bench
TWIN=wican-bench-w0
LEASES_DIR=/var/lib/NetworkManager
SHIM=$LEASES_DIR/dnsmasq-wtest0.leases

ncm_if() {
    for d in /sys/class/net/*; do
        drv=$(readlink -f "$d/device/driver" 2>/dev/null)
        case "$drv" in *cdc_ncm*|*cdc_ether*) basename "$d"; return 0;; esac
    done
    return 1
}

case "${1:-status}" in
  up)
    IF=$(ncm_if) || { echo "no cdc_ncm interface (is the P4's OTG port plugged in and bench_ap running?)"; exit 1; }
    echo "ncm interface: $IF"
    if ! nmcli -t -f NAME con show | grep -qx "$CON"; then
        sudo -n nmcli con add type ethernet ifname "$IF" con-name "$CON" \
            ipv4.method shared ipv4.addresses 10.42.1.1/24 ipv6.method disabled \
            connection.autoconnect yes >/dev/null
    else
        sudo -n nmcli con modify "$CON" connection.interface-name "$IF" >/dev/null
    fi
    # park BOTH mt76/brcm hotspots and keep them parked: NetworkManager brought the
    # twin back by itself after a plain `con down` (2026-10-02)
    for h in "$HOTSPOT" "$TWIN"; do
        sudo -n nmcli con modify "$h" connection.autoconnect no >/dev/null 2>&1 || true
        sudo -n nmcli con down "$h" >/dev/null 2>&1 || true
    done
    sudo -n nmcli con up "$CON" >/dev/null || { echo "could not bring $CON up"; exit 1; }
    pgrep -f bench_ap_daemon.py >/dev/null || { setsid nohup python3 "$(dirname "$0")/bench_ap_daemon.py" >/tmp/bench_ap_daemon.out 2>&1 < /dev/null & disown; echo "bench_ap daemon started (console log /tmp/bench_ap.console.log)"; }
    sudo -n touch "$LEASES_DIR/dnsmasq-$IF.leases"
    if sudo -n test -f "$SHIM" && ! sudo -n test -L "$SHIM"; then
        sudo -n mv -f "$SHIM" "$SHIM.parked"
    fi
    sudo -n ln -sfn "dnsmasq-$IF.leases" "$SHIM"
    echo "$CON up on $IF (10.42.1.1/24); leases: dnsmasq-$IF.leases (shimmed as dnsmasq-wtest0.leases); $HOTSPOT parked"
    ;;
  down)
    sudo -n nmcli con down "$CON" >/dev/null 2>&1 || true
    if sudo -n test -L "$SHIM"; then
        sudo -n rm -f "$SHIM"
        if sudo -n test -f "$SHIM.parked"; then sudo -n mv -f "$SHIM.parked" "$SHIM"; else sudo -n touch "$SHIM"; fi
    fi
    for h in "$HOTSPOT" "$TWIN"; do
        sudo -n nmcli con modify "$h" connection.autoconnect yes >/dev/null 2>&1 || true
    done
    sudo -n nmcli con up "$HOTSPOT" >/dev/null 2>&1 && echo "$HOTSPOT back up, leases file restored"
    ;;
  status)
    IF=$(ncm_if) && echo "ncm interface: $IF" || echo "ncm interface: none"
    nmcli -t -f NAME,DEVICE,STATE con show --active | grep -E "$CON|$HOTSPOT|$TWIN" || echo "neither $CON nor $HOTSPOT active"
    pgrep -f bench_ap_daemon.py >/dev/null && echo "bench_ap daemon: running" || echo "bench_ap daemon: not running"
    # the NM directory is root-only: test the shim with sudo
    sudo -n test -L "$SHIM" && echo "leases shim: $(sudo -n readlink "$SHIM")" || echo "leases shim: off"
    ;;
  *) echo "usage: $0 up|down|status"; exit 2;;
esac
