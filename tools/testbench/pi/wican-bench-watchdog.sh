#!/bin/bash
# wican-bench beacon watchdog v2 (role names, 2026-07-26; v1 2026-07-17).
# nmcli "activated" lies — verify the SSID is genuinely ON AIR by scanning
# from a second radio; bounce the hotspot if not, and fail over between
# the internal radio (wican-bench-w0/wint0, PRIMARY — the sticks are the
# unreliable parties) and the USB twin (wican-bench/wtest0) if the bounce
# does not heal it. Respects HIL parking: autoconnect=no on the primary =
# deliberately down. Escalates to bench-recover when both twins fail.
BENCH_TAG=wican-bench-watchdog
. /usr/local/lib/bench-lib.sh

PRIMARY="$HOTSPOT_PRIMARY"    # wican-bench-w0 (wint0)
FAILOVER="$HOTSPOT_TWIN"      # wican-bench (wtest0)

# parked on purpose? (HIL park_persistent sets autoconnect no)
if [ "$(nmcli -g connection.autoconnect connection show "$PRIMARY" 2>/dev/null)" = "no" ]; then
    exit 0
fi

# a bench is ONBOARDING (wican-dut = a factory-AP client on a bench
# radio): the hotspots are legitimately elsewhere — stand down. The
# watchdog fought system_bench 2026-07-26 (bounced + failed over
# mid-onboard and left the primary down).
if con_active wican-dut; then
    exit 0
fi

active_con=""
for c in "$PRIMARY" "$FAILOVER"; do
    con_active "$c" && { active_con="$c"; break; }
done

if [ -z "$active_con" ]; then
    blog "no hotspot active - starting $PRIMARY"
    nmcli connection up "$PRIMARY" >/dev/null 2>&1 || true
    active_con="$PRIMARY"
    sleep 5
fi

restore_primary() {
    # canonical state = the PRIMARY active (both twins may be up); a past
    # failover must not leave it down forever
    if ! con_active "$PRIMARY" && \
       [ "$(nmcli -g connection.autoconnect connection show "$PRIMARY" 2>/dev/null)" = "yes" ]; then
        blog "restoring primary $PRIMARY"
        nmcli connection up "$PRIMARY" >/dev/null 2>&1 || true
    fi
}

hs_if=$(con_device "$active_con")
scanner=$(pick_scanner "$hs_if") || exit 0   # nothing to scan with

# TWO scan attempts before acting: the only free scanner is often the
# 5 GHz-associated uplink stick, which under-reports 2.4 GHz beacons —
# a single missed scan caused a false phantom + needless failover
# (2026-07-26)
if ssid_on_air "$scanner" "$SSID" || ssid_on_air "$scanner" "$SSID"; then
    restore_primary
    exit 0
fi

blog "$SSID NOT on air (active=$active_con if=$hs_if, 2 scans) - bouncing"
nmcli connection down "$active_con" >/dev/null 2>&1
sleep 2
nmcli connection up "$active_con" >/dev/null 2>&1
sleep 6
ssid_on_air "$scanner" "$SSID" && { blog "healed by bounce ($active_con)"; exit 0; }

other="$FAILOVER"; [ "$active_con" = "$FAILOVER" ] && other="$PRIMARY"
blog "bounce failed - failing over to $other"
nmcli connection down "$active_con" >/dev/null 2>&1
sleep 2
nmcli connection up "$other" >/dev/null 2>&1
sleep 6
# scanner may now be the new hotspot iface — re-pick
hs_if=$(con_device "$other")
if scanner=$(pick_scanner "$hs_if") && ssid_on_air "$scanner" "$SSID"; then
    blog "healed by failover to $other"
    restore_primary
else
    blog "STILL NOT ON AIR after failover - escalating to bench-recover"
    bench-recover >/dev/null 2>&1 &
fi
