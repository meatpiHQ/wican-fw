#!/bin/bash
# bench-startup — assert the canonical bench state at boot (plan §4.2).
# A Pi reboot converges to a KNOWN state instead of whatever NM remembers.
# Logs "BENCH READY" (journal: journalctl -t bench-startup) when done.
BENCH_TAG=bench-startup
. /usr/local/lib/bench-lib.sh

blog "asserting canonical bench state"
sudo mkdir -p "$BENCH_STATE_DIR"

# NetworkManager settled enough to talk to
nm-online -s -q --timeout=30

# canonical autoconnect flags (TESTBENCH.md): both hotspot twins yes
# (the watchdog keeps at least one on air), uplink yes (non-load-bearing),
# stale duplicate uplink profile parked.
nmcli connection modify "$HOTSPOT_PRIMARY" connection.autoconnect yes 2>/dev/null
nmcli connection modify "$HOTSPOT_TWIN"    connection.autoconnect yes 2>/dev/null
nmcli connection modify "Nachos_8042_5G 1" connection.autoconnect no  2>/dev/null
nmcli connection modify "$ETH_CON"         connection.autoconnect yes 2>/dev/null

# the DUT hotspot must be up (autoconnect usually did it already)
for i in $(seq 1 12); do
    con_active "$HOTSPOT_PRIMARY" && break
    [ "$i" = 4 ] && nmcli connection up "$HOTSPOT_PRIMARY" >/dev/null 2>&1
    sleep 5
done

systemctl is-active --quiet mosquitto || sudo systemctl start mosquitto
sudo systemctl enable --now wican-bench-watchdog.timer >/dev/null 2>&1

# nothing load-bearing lives in /tmp by policy (tmpfs — re-scp per run);
# state that must survive reboots belongs in $BENCH_STATE_DIR.

# compat shims: pre-rename scripts read dnsmasq-wlan{0,1}.leases; point
# the old names at the role-named files (dangling until a client joins —
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
