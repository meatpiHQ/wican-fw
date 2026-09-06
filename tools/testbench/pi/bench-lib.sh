#!/bin/bash
# Shared helpers for the rpi001 bench toolkit (sourced, not executed).
# Role names come from /etc/udev/rules.d/70-bench-radios.rules; the
# resolvers below fall back to driver/MAC lookup so every tool also works
# on a Pi that has not rebooted into the new names yet.

# non-login ssh sessions lack /usr/sbin (iw, rfkill live there)
export PATH="/usr/local/bin:/usr/sbin:/sbin:$PATH"

BENCH_STATE_DIR=/var/lib/bench
SSID="WICAN_TEST_AP"
HOTSPOT_PRIMARY="wican-bench"      # USB stick (wtest0) — the internal brcmfmac radio goes deaf in AP mode (2026-09-06)
HOTSPOT_TWIN="wican-bench-w0"      # internal radio (wint0) — failover only, keep parked (autoconnect no)
UPLINK_CON="Nachos_8042_5G 2"      # non-load-bearing convenience uplink
ETH_CON="eth-bench"
ETH_ADDR="192.168.90.2"

blog() { echo "$1"; logger -t "${BENCH_TAG:-bench}" "$1"; }

# role -> MAC. THE single source of truth for the rig's radio MACs, in
# sync with 70-bench-radios.rules (which udev reads before this file
# exists). deploy_bench_pi.sh also re-pins the MAC-bound NM profiles from
# here, so a stick swap means editing exactly these two files.
#
# 2026-07-31: wtest0/wtest1 became MT7612U (0e8d:7612); the retired
# RTL8822CU pair was 90:de:80:19:3a:bf / 90:de:80:d1:f9:cb.
bench_role_mac() {
    case "$1" in
        wint0)  echo "2c:cf:67:f5:b6:60" ;;
        wtest0) echo "90:de:80:88:cf:08" ;;
        wtest1) echo "90:de:80:88:ce:5f" ;;
        *) return 1 ;;
    esac
}

# role -> interface. Prefers the pinned name; falls back to MAC.
bench_iface() {
    local role="$1" mac=""
    [ -e "/sys/class/net/$role" ] && { echo "$role"; return 0; }
    mac=$(bench_role_mac "$role") || return 1
    local i
    for i in /sys/class/net/wlan*; do
        [ -e "$i" ] || continue
        if [ "$(cat "$i/address" 2>/dev/null)" = "$mac" ]; then
            basename "$i"; return 0
        fi
    done
    return 1
}

# interface -> role (inverse of bench_iface)
bench_role() {
    local ifc="$1" r
    for r in wint0 wtest0 wtest1; do
        [ "$(bench_iface "$r")" = "$ifc" ] && { echo "$r"; return 0; }
    done
    return 1
}

# NM connection active?
con_active() {
    nmcli -t -f NAME connection show --active 2>/dev/null | grep -qx "$1"
}

# device an active connection sits on
con_device() {
    nmcli -t -f NAME,DEVICE connection show --active 2>/dev/null |
        awk -F: -v c="$1" '$1==c{print $2}'
}

# a radio to truth-test-scan with: never one in AP mode, prefer a fully
# idle radio, but the uplink stick is a legal LAST choice — a station-mode
# scan is allowed while associated, and since the control plane moved to
# ethernet (2026-07-26) the uplink is non-load-bearing by design.
# $1 = interface to EXCLUDE (the one under test). May echo nothing.
pick_scanner() {
    local exclude="$1" uplink_if r ifc pass
    uplink_if=$(con_device "$UPLINK_CON")
    for pass in 1 2; do
        for r in wtest0 wtest1 wint0; do
            ifc=$(bench_iface "$r") || continue
            [ "$ifc" = "$exclude" ] && continue
            [ "$pass" = 1 ] && [ "$ifc" = "$uplink_if" ] && continue
            # never scan from an AP-mode radio (results are stale/empty)
            iw dev "$ifc" info 2>/dev/null | grep -q "type AP" && continue
            echo "$ifc"; return 0
        done
    done
    return 1
}

# TRUTH TEST: is $2 (ssid) genuinely on air, seen from $1 (scanner iface)?
# nmcli "activated" lies (phantom-AP mode, twice on 2026-07-26) — only a
# scan from a DIFFERENT radio proves beacons.
ssid_on_air() {
    local scanner="$1" ssid="$2"
    nmcli dev wifi rescan ifname "$scanner" >/dev/null 2>&1
    sleep 5
    nmcli -t -f SSID dev wifi list ifname "$scanner" 2>/dev/null |
        grep -qx "$ssid"
}

# TRUTH TEST, per-radio: is the AP hosted on the iface with MAC $2
# genuinely beaconing? (The twins share one SSID, so only the BSSID —
# which equals the hosting iface's MAC — identifies WHICH radio is on
# air.) Seen from scanner $1.
bssid_on_air() {
    local scanner="$1" mac="$2"
    nmcli dev wifi rescan ifname "$scanner" >/dev/null 2>&1
    sleep 5
    nmcli -f BSSID dev wifi list ifname "$scanner" 2>/dev/null |
        grep -qi "$mac"
}

# USB bus/dev path of an interface's parent device, for usbreset
usb_busdev() {
    local ifc="$1" dev
    dev=$(readlink -f "/sys/class/net/$ifc/device" 2>/dev/null) || return 1
    # walk up to the USB device level (has busnum/devnum)
    while [ -n "$dev" ] && [ "$dev" != "/" ]; do
        if [ -e "$dev/busnum" ] && [ -e "$dev/devnum" ]; then
            printf '%03d/%03d\n' "$(cat "$dev/busnum")" "$(cat "$dev/devnum")"
            return 0
        fi
        dev=$(dirname "$dev")
    done
    return 1
}
