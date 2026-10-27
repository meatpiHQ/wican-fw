#!/bin/bash
# bench-startup: assert the canonical bench state at boot (plan §4.2).
# A Pi reboot converges to a KNOWN state instead of whatever NM remembers.
# Logs "BENCH READY" (journal: journalctl -t bench-startup) when done.
BENCH_TAG=bench-startup
. /usr/local/lib/bench-lib.sh

blog "asserting canonical bench state"
sudo mkdir -p "$BENCH_STATE_DIR"

# NetworkManager settled enough to talk to
nm-online -s -q --timeout=30

# canonical autoconnect flags. Since 2026-10-02 the bench AP is the P4
# (bench_ap) on its CDC-NCM link, profile $P4_CON: whenever its interface
# is present it serves, and BOTH Pi hotspot twins stay parked (autoconnect
# no, down), or the DUT meets two WICAN_TEST_AP radios and roams between
# them. Until 2026-10-27 this script re-armed both twins at every Pi boot
# and the watchdog bounced the internal radio under the DUT during a
# release run (two WiFi drops, the MQTT errors they cost). Without the P4
# (no NCM interface) the old hotspot world applies: the USB-stick primary
# autoconnects, the internal twin stays parked (it goes deaf in AP mode,
# 2026-09-06). Uplink yes (non-load-bearing), the stale duplicate uplink
# profile parked.
nmcli connection modify "Nachos_8042_5G 1" connection.autoconnect no  2>/dev/null
nmcli connection modify "$ETH_CON"         connection.autoconnect yes 2>/dev/null
nmcli connection modify "$HOTSPOT_TWIN"    connection.autoconnect no  2>/dev/null

if ncm_if=$(p4_ncm_iface); then
    for h in "$HOTSPOT_PRIMARY" "$HOTSPOT_TWIN"; do
        nmcli connection modify "$h" connection.autoconnect no >/dev/null 2>&1
        nmcli connection down "$h" >/dev/null 2>&1
    done
    nmcli connection modify "$P4_CON" connection.autoconnect yes 2>/dev/null
    for i in $(seq 1 12); do
        con_active "$P4_CON" && break
        [ "$i" = 4 ] && nmcli connection up "$P4_CON" >/dev/null 2>&1
        sleep 5
    done
    # the lease-file shim bench_ap_net.sh keeps: the bench scripts read the
    # hotspot's file, which points at the NCM interface's while the P4 serves
    shim=/var/lib/NetworkManager/dnsmasq-wtest0.leases
    sudo touch "/var/lib/NetworkManager/dnsmasq-$ncm_if.leases"
    if [ -f "$shim" ] && [ ! -L "$shim" ]; then sudo mv -f "$shim" "$shim.parked"; fi
    sudo ln -sfn "dnsmasq-$ncm_if.leases" "$shim"
    blog "the P4 serves ($P4_CON on $ncm_if); both Pi hotspots parked"
else
    # no P4: the DUT hotspot must be up (autoconnect usually did it already)
    nmcli connection modify "$HOTSPOT_PRIMARY" connection.autoconnect yes 2>/dev/null
    for i in $(seq 1 12); do
        con_active "$HOTSPOT_PRIMARY" && break
        [ "$i" = 4 ] && nmcli connection up "$HOTSPOT_PRIMARY" >/dev/null 2>&1
        sleep 5
    done
    blog "no P4 NCM interface: the Pi hotspot $HOTSPOT_PRIMARY serves"
fi

systemctl is-active --quiet mosquitto || sudo systemctl start mosquitto
sudo systemctl enable --now wican-bench-watchdog.timer >/dev/null 2>&1

# nothing load-bearing lives in /tmp by policy (tmpfs: re-scp per run);
# state that must survive reboots belongs in $BENCH_STATE_DIR.

# compat shims: pre-rename scripts read dnsmasq-wlan{0,1}.leases; point
# the old names at the role-named files (dangling until a client joins,
# same failure mode as a missing file, which is what those scripts expect)
sudo ln -sfn dnsmasq-wint0.leases  /var/lib/NetworkManager/dnsmasq-wlan0.leases
sudo ln -sfn dnsmasq-wtest0.leases /var/lib/NetworkManager/dnsmasq-wlan1.leases

ready="BENCH READY eth=$(ip -4 -br addr show eth0 | awk '{print $3}')"
for r in wint0 wtest0 wtest1; do
    ifc=$(bench_iface "$r") || ifc=MISSING
    ready="$ready $r=$ifc"
done
blog "$ready"

# leave a rig verdict in the journal right after boot (informational)
bench-health || true
exit 0
