#!/usr/bin/env bash
# wg_ha_route.sh — the HA-over-VPN bench route (2026-09-09).
#
# The "car on the road talks to Home Assistant through its VPN" scenario:
#   * betty (public VPS) runs a WireGuard server 10.9.0.1/24 on udp/51820
#     and forwards between its peers;
#   * rpi001 (the bench HA, `~/ha`) is peer 10.9.0.3 — HA's internal_url is
#     then http://10.9.0.3:8123 and the integration registers THAT webhook
#     URL on the device;
#   * the DUT is peer 10.9.0.2 through the firmware's own vpn_manager
#     (split mode: AllowedIPs 10.9.0.0/24, no default route), so it reaches
#     the bench HA at 10.9.0.3 from ANY uplink — the bench hotspot or the
#     ESPNetLink LTE path — and HA reaches the DUT at 10.9.0.2 (the
#     integration's `vpn_ip` backup endpoint).
#
# Keys (same rules as wg_public_soak.sh): the server keypair is generated ON
# betty under umask 077 and never printed; rpi001's keypair lives in
# ~/wg_rpi001.key/.pub on rpi001; the DUT's private key never leaves the
# device (POST /api/vpn/keygen returns only the public key). Public keys are
# masked in the output.
#
#   wg_ha_route.sh up              # server + rpi001 peer + DUT client (DUT reboots once)
#   wg_ha_route.sh status          # server-side wg show, rpi001 tunnel, DUT /api/vpn
#   wg_ha_route.sh down [--wipe]   # tear down (idempotent); --wipe blanks the DUT config
#
# Env: DUT_IP (default 10.42.1.194 — the DUT on the wtest0 hotspot).
set -u
SRV=betty
PI_HOST=rpi001
IF=wg0
WG_PORT=51820
NET=10.9.0
DUT_IP="${DUT_IP:-10.42.1.194}"
PI="ssh -o BatchMode=yes -o ConnectTimeout=10 $PI_HOST"
SRVSSH="ssh -o BatchMode=yes -o ConnectTimeout=12 $SRV"

mask() { local k="$1"; echo "${k:0:6}…${k: -4}"; }
dut()  { $PI "curl -s -m 8 $*"; }
server_pubip() { $SRVSSH "curl -s -m 8 https://api.ipify.org"; }

server_up() {
    $SRVSSH "umask 077
        sudo -n install -d -m 700 /etc/wireguard
        sudo -n test -f /etc/wireguard/${IF}.key || wg genkey | sudo -n tee /etc/wireguard/${IF}.key >/dev/null
        sudo -n cat /etc/wireguard/${IF}.key | wg pubkey | sudo -n tee /etc/wireguard/${IF}.pub >/dev/null
        sudo -n bash -c 'cat > /etc/wireguard/${IF}.conf <<EOF
[Interface]
Address = ${NET}.1/24
ListenPort = ${WG_PORT}
PostUp   = sysctl -w net.ipv4.ip_forward=1; iptables -A FORWARD -i ${IF} -j ACCEPT; iptables -A FORWARD -o ${IF} -j ACCEPT
PostDown = iptables -D FORWARD -i ${IF} -j ACCEPT; iptables -D FORWARD -o ${IF} -j ACCEPT
PrivateKey = \$(cat /etc/wireguard/${IF}.key)
EOF
chmod 600 /etc/wireguard/${IF}.conf'"
    echo "server: config written (key masked, never printed)"
}

server_peer() { # $1 = peer public key, $2 = tunnel address
    $SRVSSH "sudo -n wg set ${IF} peer '$1' allowed-ips ${2}/32 2>/dev/null || true
        sudo -n grep -q '$1' /etc/wireguard/${IF}.conf || printf '\n[Peer]\nPublicKey = %s\nAllowedIPs = %s/32\n' '$1' '$2' | sudo -n tee -a /etc/wireguard/${IF}.conf >/dev/null"
}

pi_up() { # $1 = server pubkey, $2 = endpoint ip
    $PI "umask 077
        [ -f ~/wg_rpi001.key ] || (wg genkey > ~/wg_rpi001.key && wg pubkey < ~/wg_rpi001.key > ~/wg_rpi001.pub)
        sudo -n bash -c 'cat > /etc/wireguard/${IF}.conf <<EOF
[Interface]
Address = ${NET}.3/24
PrivateKey = \$(cat /home/meatpi/wg_rpi001.key)

[Peer]
PublicKey = $1
Endpoint = $2:${WG_PORT}
AllowedIPs = ${NET}.0/24
PersistentKeepalive = 25
EOF
chmod 600 /etc/wireguard/${IF}.conf'
        sudo -n wg-quick down ${IF} 2>/dev/null; sudo -n wg-quick up ${IF} 2>&1 | grep -v '^\[#\]' ; true"
}

case "${1:-}" in
up)
    EP=$(server_pubip)
    echo "server endpoint: ${EP}:${WG_PORT}  net ${NET}.0/24 (betty .1, rpi001 .3, DUT .2)"
    server_up
    $SRVSSH "sudo -n wg-quick down ${IF} 2>/dev/null; sudo -n wg-quick up ${IF}" >/dev/null 2>&1
    SRVPUB=$($SRVSSH "sudo -n cat /etc/wireguard/${IF}.pub")
    echo "server pubkey: $(mask "$SRVPUB")"

    # rpi001 = peer .3
    PIPUB=$($PI "cat ~/wg_rpi001.pub 2>/dev/null || (umask 077; wg genkey > ~/wg_rpi001.key; wg pubkey < ~/wg_rpi001.key | tee ~/wg_rpi001.pub)")
    echo "rpi001 pubkey: $(mask "$PIPUB")"
    server_peer "$PIPUB" "${NET}.3"
    pi_up "$SRVPUB" "$EP"
    sleep 4
    if $PI "ping -c 2 -W 3 ${NET}.1 >/dev/null 2>&1"; then echo "PASS: rpi001 -> betty tunnel (ping ${NET}.1)"
    else echo "FAIL: rpi001 cannot ping ${NET}.1 through the tunnel"; exit 1; fi

    # DUT = peer .2 (firmware vpn_manager, split mode)
    DUTPUB=$(dut "-X POST http://${DUT_IP}/api/vpn/keygen" | python3 -c 'import json,sys;print(json.load(sys.stdin)["public_key"])')
    echo "DUT pubkey:    $(mask "$DUTPUB")"
    server_peer "$DUTPUB" "${NET}.2"
    VCFG=$(dut "-s http://${DUT_IP}/api/settings/vpn_manager" | SRVPUB="$SRVPUB" EP="$EP" PORT="$WG_PORT" NET="$NET" python3 -c '
import json,sys,os
d=json.load(sys.stdin)
for k in ("degraded","pending_reboot"): d.pop(k,None)
d.update({"enabled":True,"type":"wireguard",
          "endpoint":os.environ["EP"],"port":int(os.environ["PORT"]),
          "peer_public_key":os.environ["SRVPUB"],
          "address":os.environ["NET"]+".2","allowed_ip":os.environ["NET"]+".0",
          "allowed_ip_mask":"255.255.255.0","keepalive_s":25,"default_route":False})
print(json.dumps(d))')
    code=$($PI "curl -s -o /dev/null -w '%{http_code}' -m 8 -X PUT http://${DUT_IP}/api/settings/vpn_manager -H 'Content-Type: application/json' -d '$VCFG'")
    echo "DUT vpn PUT -> $code"
    dut "-X POST http://${DUT_IP}/api/settings/submit" >/dev/null; echo "DUT submitted (rebooting)"
    sleep 30
    OK=0
    for i in $(seq 1 24); do
        st=$(dut "http://${DUT_IP}/api/vpn" 2>/dev/null | python3 -c 'import json,sys;print(json.load(sys.stdin).get("state",""))' 2>/dev/null)
        [ "$st" = "connected" ] && { OK=1; break; }
        sleep 5
    done
    if [ "$OK" = "1" ]; then echo "PASS: DUT /api/vpn state=connected"
    else echo "FAIL: DUT vpn not connected within ~150 s (state='${st:-?}')"; exit 1; fi
    if $PI "ping -c 2 -W 3 ${NET}.2 >/dev/null 2>&1"; then echo "PASS: rpi001 -> DUT through the tunnel (ping ${NET}.2 via betty)"
    else echo "FAIL: rpi001 cannot ping the DUT at ${NET}.2 through the tunnel"; exit 1; fi
    if $PI "curl -s -m 8 http://${NET}.2/api/info | grep -q device_id"; then echo "PASS: DUT HTTP API reachable at ${NET}.2"
    else echo "FAIL: DUT HTTP API not reachable at ${NET}.2"; exit 1; fi
    echo "WG HA ROUTE UP"
    ;;
status)
    echo "--- betty"; $SRVSSH "sudo -n wg show ${IF} 2>&1 | grep -v 'private\|preshared'"
    echo "--- rpi001"; $PI "sudo -n wg show ${IF} 2>&1 | grep -v 'private\|preshared'; ping -c 1 -W 2 ${NET}.1 >/dev/null 2>&1 && echo 'ping .1 ok' || echo 'ping .1 FAIL'; ping -c 1 -W 3 ${NET}.2 >/dev/null 2>&1 && echo 'ping .2 (DUT) ok' || echo 'ping .2 (DUT) FAIL'"
    echo "--- DUT"; dut "http://${DUT_IP}/api/vpn"; echo
    ;;
down)
    WIPE="${2:-}"
    $PI "sudo -n wg-quick down ${IF} 2>/dev/null; sudo -n rm -f /etc/wireguard/${IF}.conf; echo 'rpi001: tunnel down'"
    $SRVSSH "sudo -n wg-quick down ${IF} 2>/dev/null; sudo -n rm -f /etc/wireguard/${IF}.conf /etc/wireguard/${IF}.key /etc/wireguard/${IF}.pub; echo 'betty: server down, config + keys wiped'"
    for host in "${DUT_IP}" "${NET}.2"; do
        VCFG=$(dut "-s -m 5 http://${host}/api/settings/vpn_manager" | WIPE="$WIPE" python3 -c '
import json,sys,os
d=json.load(sys.stdin)
for k in ("degraded","pending_reboot"): d.pop(k,None)
d["enabled"]=False
if os.environ["WIPE"]=="--wipe":
    d.update({"endpoint":"","peer_public_key":"","address":"","allowed_ip":"0.0.0.0","allowed_ip_mask":"0.0.0.0"})
print(json.dumps(d))' 2>/dev/null) || continue
        [ -n "$VCFG" ] || continue
        code=$($PI "curl -s -o /dev/null -w '%{http_code}' -m 8 -X PUT http://${host}/api/settings/vpn_manager -H 'Content-Type: application/json' -d '$VCFG'")
        echo "DUT (${host}) vpn disabled${WIPE:+ + wiped} -> $code"; dut "-X POST http://${host}/api/settings/submit" >/dev/null; echo "DUT submitted (rebooting)"
        break
    done
    ;;
*)
    sed -n 2,26p "$0"; exit 2 ;;
esac
