#!/bin/bash
# wg-bench — start/stop the LOCAL WireGuard test server on rpi001.
#
# LOCAL-ONLY test rig (never expose a public endpoint for bench work):
# serves 10.66.0.1/24 on udp/51821 at the Pi's hotspot address
# (10.42.0.1), point-to-point to the WiCAN (peer 10.66.0.2/32) — no NAT,
# no ip_forward. Config: /etc/wireguard/wg-bench.conf.
#
#   wg-bench start                 bring the server up (idempotent)
#   wg-bench stop                  tear it down
#   wg-bench status                interface + last-handshake + transfer
#   wg-bench peer <DUT_PUBKEY>     replace the DUT peer entry (after a
#                                  device-side keygen), keeps 10.66.0.2/32
#
# Installed on rpi001 at /usr/local/bin/wg-bench (repo copy:
# tools/testbench/wg_bench_server.sh).
set -u

CONF=/etc/wireguard/wg-bench.conf
IF=wg-bench

usage() { sed -n '2,16p' "$0"; exit 1; }

is_up() { ip link show "$IF" >/dev/null 2>&1; }

case "${1:-}" in
start)
    if is_up; then
        echo "wg-bench: already up"
    else
        sudo wg-quick up "$IF" || exit 1
    fi
    echo "--- listening ---"
    ss -ulnp 2>/dev/null | grep -w 51821 || echo "WARNING: udp/51821 not listening"
    sudo wg show "$IF"
    ;;
stop)
    if is_up; then
        sudo wg-quick down "$IF"
        echo "wg-bench: stopped"
    else
        echo "wg-bench: not running"
    fi
    ;;
status)
    if ! is_up; then
        echo "wg-bench: DOWN"
        exit 1
    fi
    sudo wg show "$IF"
    echo "--- peer reachability (tunnel ping 10.66.0.2) ---"
    ping -c 2 -W 2 10.66.0.2 >/dev/null 2>&1 && echo "10.66.0.2 answers over the tunnel" \
        || echo "10.66.0.2 not answering (no handshake yet, or DUT down)"
    ;;
peer)
    [ -n "${2:-}" ] || usage
    # replace the [Peer] PublicKey line, keep AllowedIPs 10.66.0.2/32
    sudo sed -i "/^\[Peer\]/,/^$/ s|^PublicKey.*|PublicKey = $2|" "$CONF"
    echo "peer public key updated"
    if is_up; then
        sudo wg-quick down "$IF"
        sudo wg-quick up "$IF"
        echo "server bounced with the new peer"
    fi
    ;;
*)
    usage
    ;;
esac
