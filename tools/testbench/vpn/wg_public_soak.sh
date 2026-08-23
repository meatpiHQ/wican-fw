#!/bin/bash
# ---------------------------------------------------------------------------
# wg_public_soak.sh — WireGuard soak test of the WiCAN DUT against a PUBLIC
# endpoint (the "betty" VPS), over the real LTE/CGNAT path.
#
# KEY HYGIENE (the whole point):
#   * The SERVER keypair is generated ON betty at runtime under `umask 077`,
#     stored only in /etc/wireguard/<IF>.conf (root, 0600), and NEVER printed.
#   * The DUT keypair is generated ON-DEVICE (`POST /api/vpn/keygen`); the
#     private key never leaves the WiCAN. We only ever handle its PUBLIC key.
#   * Public keys are masked in all output. No key or config is written to
#     this repo, the PC, or stdout.
#   * `down` (also run by the EXIT trap in `run`) tears the tunnel down on
#     betty AND disables it on the DUT, then WIPES the server config + keys.
#     With `--wipe` it also blanks the DUT's endpoint/peer so no bench VPN
#     config lingers in NVS.
#
# Usage:
#   wg_public_soak.sh up   [split|full]   # bring the tunnel up (default split)
#   wg_public_soak.sh status              # server-side wg show + DUT vpn state
#   wg_public_soak.sh down [--wipe]       # tear down everything (idempotent)
#   wg_public_soak.sh run  [split|full] [minutes]  # up -> soak -> guaranteed down
#
# split = AllowedIPs 10.9.0.0/24 (management via STA stays alive, full
#         observability). full = 0.0.0.0/0 (betty NATs egress; DUT HTTP
#         management drops — observe via `status` on betty + the serial log).
# ---------------------------------------------------------------------------
set -u

SRV=betty                       # ssh alias for the VPS
EP_DNS="${WG_BENCH_DNS:-}"      # optional: server DNS name (env var, NOT
                                # committed — private server, public repo).
                                # When set and matching the server IP, the
                                # DUT gets the NAME as its endpoint so the
                                # firmware's DNS-resolution path runs too.
IF=wg0
WG_PORT=51820
NET=10.9.0                      # tunnel /24: server .1, DUT .2
DUT_IP="${DUT_IP:-10.42.0.62}"               # DUT on the Pi hotspot
PI="ssh -o BatchMode=yes -o ConnectTimeout=10 rpi001"   # DUT reached via Pi
SRVSSH="ssh -o BatchMode=yes -o ConnectTimeout=12 $SRV"

mask() { local k="$1"; echo "${k:0:6}…${k: -4}"; }         # never show a full key
dut()  { $PI "curl -s -m 8 $*"; }                          # curl the DUT via Pi

server_pubip() { $SRVSSH "curl -s -m 8 https://api.ipify.org"; }

server_up() {
    # generate keys + config in-place on betty; nothing sensitive crosses ssh
    $SRVSSH "umask 077
        install -d -m 700 /etc/wireguard
        [ -f /etc/wireguard/${IF}.key ] || wg genkey > /etc/wireguard/${IF}.key
        wg pubkey < /etc/wireguard/${IF}.key > /etc/wireguard/${IF}.pub
        cat > /etc/wireguard/${IF}.conf <<EOF
[Interface]
Address = ${NET}.1/24
ListenPort = ${WG_PORT}
PostUp   = sysctl -w net.ipv4.ip_forward=1; iptables -t nat -A POSTROUTING -s ${NET}.0/24 -o eth0 -j MASQUERADE; iptables -A FORWARD -i ${IF} -j ACCEPT; iptables -A FORWARD -o ${IF} -j ACCEPT
PostDown = iptables -t nat -D POSTROUTING -s ${NET}.0/24 -o eth0 -j MASQUERADE; iptables -D FORWARD -i ${IF} -j ACCEPT; iptables -D FORWARD -o ${IF} -j ACCEPT
PrivateKey = \$(cat /etc/wireguard/${IF}.key)
EOF
        chmod 600 /etc/wireguard/${IF}.conf"
    echo "server: config written (key masked, never printed)"
}

server_peer() { # $1 = DUT public key
    $SRVSSH "wg set ${IF} peer '$1' allowed-ips ${NET}.2/32 2>/dev/null || true
        # persist into the conf so a restart keeps the peer
        grep -q '$1' /etc/wireguard/${IF}.conf || printf '\n[Peer]\nPublicKey = %s\nAllowedIPs = %s.2/32\n' '$1' '${NET}' >> /etc/wireguard/${IF}.conf"
}

case "${1:-}" in
up|run)
    MODE="${2:-split}"
    # full = default_route ON (WG becomes the system default → exercises the
    # route-restore path, the crash suspect); split = subnet-only, default off
    if [ "$MODE" = "full" ]; then ALLOWED="0.0.0.0"; MASK="0.0.0.0"; DEFROUTE=true
    else ALLOWED="${NET}.0"; MASK="255.255.255.0"; DEFROUTE=false; fi

    if [ "$1" = "run" ]; then
        MINS="${3:-15}"
        teardown() { echo; echo "=== teardown (trap) ==="; "$0" down --wipe; }
        trap teardown EXIT INT TERM
    fi

    EP_IP=$(server_pubip)
    EP="$EP_IP"
    if [ -n "$EP_DNS" ]; then
        DNS_IP=$(python3 -c "import socket;print(socket.gethostbyname('${EP_DNS}'))" 2>/dev/null)
        if [ "$DNS_IP" = "$EP_IP" ]; then
            EP="$EP_DNS"   # DUT resolves the name itself (firmware DNS path)
        else
            echo "WARNING: WG_BENCH_DNS -> ${DNS_IP:-unresolved} != server ${EP_IP}; using IP endpoint"
        fi
    fi
    echo "endpoint: ${EP}:${WG_PORT}  mode=${MODE} allowed=${ALLOWED}/${MASK}"
    server_up
    $SRVSSH "wg-quick down ${IF} 2>/dev/null; wg-quick up ${IF}" >/dev/null 2>&1
    SRVPUB=$($SRVSSH "cat /etc/wireguard/${IF}.pub")
    echo "server pubkey: $(mask "$SRVPUB")"

    # fresh on-device DUT keypair; only its PUBLIC key comes back
    DUTPUB=$(dut "-X POST http://${DUT_IP}/api/vpn/keygen" | python3 -c 'import json,sys;print(json.load(sys.stdin)["public_key"])')
    echo "DUT pubkey:    $(mask "$DUTPUB")"
    server_peer "$DUTPUB"

    # DUT settings (do NOT send private_key — keygen already set it in NVS)
    VCFG=$(dut "-s http://${DUT_IP}/api/settings/vpn_manager" | SRVPUB="$SRVPUB" EP="$EP" ALLOWED="$ALLOWED" MASK="$MASK" PORT="$WG_PORT" NET="$NET" DEFROUTE="$DEFROUTE" python3 -c '
import json,sys,os
d=json.load(sys.stdin)
for k in ("degraded","pending_reboot"): d.pop(k,None)
d.update({"enabled":True,"type":"wireguard",
          "endpoint":os.environ["EP"],"port":int(os.environ["PORT"]),
          "peer_public_key":os.environ["SRVPUB"],
          "address":os.environ["NET"]+".2","allowed_ip":os.environ["ALLOWED"],
          "allowed_ip_mask":os.environ["MASK"],"keepalive_s":25,
          "default_route":os.environ["DEFROUTE"]=="true"})
print(json.dumps(d))')
    code=$($PI "curl -s -o /dev/null -w '%{http_code}' -m 8 -X PUT http://${DUT_IP}/api/settings/vpn_manager -H 'Content-Type: application/json' -d '$VCFG'")
    echo "DUT vpn PUT -> $code"
    dut "-X POST http://${DUT_IP}/api/settings/submit" >/dev/null; echo "DUT submitted (rebooting)"

    # self-verify (canonical-bench duty, parity with vpn_bench_target):
    # handshake within 90 s of the reboot, then ICMP through the tunnel
    # to the DUT's tunnel address. Split mode keeps AllowedIPs at the
    # NETWORK address = the BUG_WG_NETIF_ADDR regression gate (the
    # netif must still come up as ${NET}.2, proven by the ping).
    echo "waiting for the DUT reboot + handshake ..."
    sleep 30
    OK=0
    for i in $(seq 1 20); do
        hs=$($SRVSSH "wg show ${IF} latest-handshakes 2>/dev/null | awk '{print \$2}'")
        now=$($SRVSSH "date +%s")
        if [ -n "${hs:-}" ] && [ "${hs:-0}" -gt 0 ] && [ $(( now - hs )) -lt 30 ]; then OK=1; break; fi
        sleep 5
    done
    if [ "$OK" = "1" ]; then echo "PASS: handshake established"
    else echo "FAIL: no handshake within ~130 s"; [ "$1" = "up" ] && exit 1; fi
    if $SRVSSH "ping -c 3 -W 2 ${NET}.2 >/dev/null 2>&1"; then
        echo "PASS: ICMP through the tunnel (${NET}.2)"
    else
        echo "FAIL: no ICMP through the tunnel"; [ "$1" = "up" ] && exit 1
    fi

    if [ "$1" = "run" ]; then
        echo "=== soak ${MINS} min ==="
        end=$(( $(date +%s) + MINS*60 ))
        while [ "$(date +%s)" -lt "$end" ]; do
            sleep 20
            hs=$($SRVSSH "wg show ${IF} latest-handshakes 2>/dev/null | awk '{print \$2}'")
            tx=$($SRVSSH "wg show ${IF} transfer 2>/dev/null | awk '{print \$2\"/\"\$3}'")
            now=$(date +%s); age=$(( now - ${hs:-0} ))
            echo "$(date +%H:%M:%S)  handshake ${age}s ago  transfer ${tx:-?}"
        done
    fi
    ;;

status)
    echo "=== betty wg ${IF} ==="; $SRVSSH "wg show ${IF} 2>/dev/null || echo 'DOWN'"
    echo "=== DUT vpn ==="; dut "-s http://${DUT_IP}/api/vpn" || echo "(DUT HTTP unreachable — expected under full tunnel)"
    ;;

down)
    echo "=== tearing down ==="
    $SRVSSH "wg-quick down ${IF} 2>/dev/null; rm -f /etc/wireguard/${IF}.conf /etc/wireguard/${IF}.key /etc/wireguard/${IF}.pub; echo 'betty: tunnel down, keys wiped'"
    # disable on the DUT (idempotent even if it is mid-reboot / unreachable)
    if [ "${2:-}" = "--wipe" ]; then
        VCFG=$(dut "-s http://${DUT_IP}/api/settings/vpn_manager" 2>/dev/null | python3 -c '
import json,sys
try: d=json.load(sys.stdin)
except: sys.exit(1)
for k in ("degraded","pending_reboot"): d.pop(k,None)
d.update({"enabled":False,"endpoint":"","peer_public_key":"","default_route":False})
print(json.dumps(d))' 2>/dev/null)
    else
        VCFG=$(dut "-s http://${DUT_IP}/api/settings/vpn_manager" 2>/dev/null | python3 -c '
import json,sys
try: d=json.load(sys.stdin)
except: sys.exit(1)
for k in ("degraded","pending_reboot"): d.pop(k,None)
d["enabled"]=False
print(json.dumps(d))' 2>/dev/null)
    fi
    if [ -n "${VCFG:-}" ]; then
        $PI "curl -s -o /dev/null -w 'DUT vpn disable PUT -> %{http_code}\n' -m 8 -X PUT http://${DUT_IP}/api/settings/vpn_manager -H 'Content-Type: application/json' -d '$VCFG'"
        dut "-X POST http://${DUT_IP}/api/settings/submit" >/dev/null 2>&1 && echo "DUT submitted (VPN off)"
    else
        echo "DUT unreachable — disable it manually when it is back (vpn off in settings)"
    fi
    ;;

*)
    sed -n '2,40p' "$0"; exit 1 ;;
esac
