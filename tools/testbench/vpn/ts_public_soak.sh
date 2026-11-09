#!/bin/bash
# ---------------------------------------------------------------------------
# ts_public_soak.sh: Tailscale (headscale) test of the WiCAN DUT against a
# PUBLIC control server on the "betty" VPS, over the DUT's real uplink
# (LTE/CGNAT via espnetlink, or STA). Public twin of ts_bench_target.py
# (the rpi001 local headscale rig).
#
# Topology:
#   betty:  headscale 0.23.0 on :8080 (HTTP control)  +  a real tailscale
#           peer node "betty-peer" (static build, own tun)
#   DUT:    joins as "wican-pub" via vpn_manager type=tailscale
#   legs:   register -> relay-only subnet requests through rekey (UDP
#           blocked before the peer joins) -> [run only: a second peer
#           opens a session itself, the DUT's requests to that peer's OWN
#           address through rekey, both peers away until every session
#           key expired, the request to the peer's own address again] ->
#           unblock -> direct DISCO, ICMP, HTTP and subnet request ->
#           PSRAM task stacks -> RAM flat over the soak -> zero
#           errors/faults -> exact restore.
#
# KEY HYGIENE (same rules as wg_public_soak.sh):
#   * headscale's noise private key + node db live only on betty; `down`
#     wipes /var/lib/headscale AND /etc/headscale AND the peer state.
#   * the disposable preauth key (1h) is created on betty, stored root-only
#     (/root/.ts_bench_key, 0600), read once into the orchestrator's memory,
#     and delivered to the DUT via curl stdin (-d @-) so it never appears in
#     argv, logs, or this repo. The betty peer reads it from the file
#     directly. It is NEVER printed; `down` blanks the DUT copy.
#   * Exact DUT settings are saved privately under state/ts-public-<ip>
#     (gitignored) until `down` verifies their restoration after restart.
#   * `run` tears everything down via an EXIT trap; cleanup failures fail it.
#
# Usage:
#   ts_public_soak.sh provision       # one-time: fetch binaries onto betty
#   ts_public_soak.sh up              # headscale + peer + DUT join + legs
#   ts_public_soak.sh status          # nodes, peer status line, DUT /api/vpn
#   ts_public_soak.sh down            # teardown + wipe ALL state/keys
#   ts_public_soak.sh run [minutes]   # up -> soak (default 10) -> down
#
# Expected final line of `run`: TS PUBLIC PASS
# ---------------------------------------------------------------------------
set -u

SRV=betty                        # ssh alias for the VPS
EP_DNS="${WG_BENCH_DNS:-}"       # optional: server DNS name (env var, NOT
                                 # committed, private server, public repo).
                                 # When set and matching the server IP, the
                                 # control URL uses the NAME so the
                                 # firmware's DNS path runs too.
BASE=/opt/tsbench                # binaries live here (no secrets, reusable)
HSPORT=8080
KEYFILE=/root/.ts_bench_key
DUT_IP="${DUT_IP:-10.42.0.62}"                # DUT on the Pi hotspot (management path)
PI="ssh -o BatchMode=yes -o ConnectTimeout=10 rpi001"
SRVSSH="ssh -o BatchMode=yes -o ConnectTimeout=12 $SRV"
HS_VER=0.23.0                    # same version the rpi001 rig verified
TS_VER=1.78.1
SNAPSHOT="$(dirname "$0")/../../../state/ts-public-${DUT_IP}"

stop_bench_process() {
    local name="$1"
    $SRVSSH "if [ -f $BASE/${name}.pid ]; then
        pid=\$(cat $BASE/${name}.pid)
        case \$pid in ''|*[!0-9]*) echo 'invalid bench PID' >&2; exit 1;; esac
        if [ -d /proc/\$pid ]; then
            grep -zqF '$BASE/' /proc/\$pid/cmdline || {
                echo 'bench PID no longer belongs to this rig' >&2; exit 1;
            }
            kill -TERM \$pid || exit 1
            for n in 1 2 3 4 5; do
                [ ! -d /proc/\$pid ] && break
                sleep 1
            done
            [ ! -d /proc/\$pid ] || { echo 'bench process did not stop' >&2; exit 1; }
        fi
        rm $BASE/${name}.pid
    fi"
}

FAILS=()
check() { # name ok [detail]
    local d="${3:-}"
    if [ "$2" = "1" ]; then echo "PASS: $1${d:+ ($d)}"
    else echo "FAIL: $1${d:+ ($d)}"; FAILS+=("$1"); fi
}
dut() { $PI "curl -sS -m 10 $*"; }
tsq() { $SRVSSH "$BASE/ts/tailscale --socket=/run/tsb.sock $*"; }
# the subnet router leg (2026-11-01): a network betty advertises (an alias on
# its loopback) that the DUT must learn from the map and reach through the
# tunnel; the receiver on betty records the request's source address
SUBNET=10.99.0.0/24
SUBNET_IP=10.99.0.1
RECV_PORT=8199
RECEIVER_SRC="$(dirname "$0")/../actors/dd_receiver.py"
recv_count() { # records the receiver holds ("" when it is down)
    $SRVSSH "curl -s -m 5 'http://127.0.0.1:${RECV_PORT}/__dump?since=0'" 2>/dev/null | python3 -c \
        "import json,sys; print(len(json.load(sys.stdin)))" 2>/dev/null
}
dut_dd_config() { # $1 = python mutation of the data_destinations document, PUT back
    dut "-s http://${DUT_IP}/api/settings/data_destinations" | python3 -c "
import json,sys
d=json.load(sys.stdin)
for k in ('degraded','pending_reboot'): d.pop(k,None)
${1}
print(json.dumps(d))" | $PI "curl -s -o /dev/null -w '%{http_code}' -m 10 \
        -X PUT http://${DUT_IP}/api/settings/data_destinations \
        -H 'Content-Type: application/json' -d @-"
}
recv_paths() { # $1 = a path ending: the records the receiver holds for it ("" when it is down)
    $SRVSSH "curl -s -m 5 'http://127.0.0.1:${RECV_PORT}/__dump?since=0'" 2>/dev/null | python3 -c \
        "import json,sys; print(sum(1 for x in json.load(sys.stdin) if x.get('path','').endswith('$1')))" 2>/dev/null
}
# the peer's-own-address legs (2026-11-09, `run` only; the case of a Home
# Assistant webhook at HA's tailnet address that times out for ever while
# the VPN says connected, on beta.8): a second node, betty-peer2, userspace
# networking (no tun: tailscaled hands a connection to its tailnet address
# on to the same port of localhost, where the receiver listens). headscale
# hands out addresses in order from a fresh database (the DUT .1, betty-peer
# .2), so the second peer's address is known before it joins, which the
# destination row needs: rows apply at the DUT's restart. The join checks it.
PEER2_IP=100.64.0.3
PEER2_SOCKS=127.0.0.1:1055       # the second peer's own requests leave through its SOCKS5 port
PEER2_FLAGS="--tun=userspace-networking --socks5-server=${PEER2_SOCKS}"
LAPSE_S="${TS_LAPSE_S:-200}"     # both peers away: past WireGuard's REJECT_AFTER_TIME (180 s)
tsq2() { $SRVSSH "$BASE/ts/tailscale --socket=/run/tsb2.sock $*"; }
peer_daemon() { # pid-name state-dir socket port log [flags]: a tailscaled of this rig (its state kept)
    $SRVSSH "nohup $BASE/ts/tailscaled ${6:-} --state=$BASE/$2 --socket=$3 --port=$4 >> $5 2>&1 < /dev/null &
        echo \$! > $BASE/$1.pid"
}
peer_running() { # $1 = socket: the backend runs (up to 60 s)
    local end=$((SECONDS+60))
    while [ $SECONDS -lt $end ]; do
        $SRVSSH "$BASE/ts/tailscale --socket=$1 status --json 2>/dev/null" | python3 -c \
            "import json,sys; sys.exit(0 if json.load(sys.stdin).get('BackendState')=='Running' else 1)" 2>/dev/null && return 0
        sleep 5
    done
    return 1
}
dd_test() { # $1 = destination row: one delivery now, the DUT's answer
    dut "-s -m 40 -X POST http://${DUT_IP}/api/destinations/test -H 'Content-Type: application/json' -d '{\"name\":\"$1\"}'" 2>/dev/null
}
DD_ROW_DROP="d['destinations']=[r for r in d.get('destinations',[]) if r.get('name') not in ('tsroute','tspeer')]"
DD_ROW_ADD="${DD_ROW_DROP}
d['enabled']=True
for n,u in (('tsroute','http://${SUBNET_IP}:${RECV_PORT}/route'),('tspeer','http://${PEER2_IP}:${RECV_PORT}/peer')):
    d['destinations'].append({'name':n,'type':'http','enabled':False,'url':u,'period_s':86400,'auth':'none','auth_token':'','auth_name':'','basic_username':'','basic_password':'','api_key':'','query':'','cert_set':'','car_model':'','retain':True,'full_first':True})"
dut_int_free() { # the DUT's internal RAM free, bytes ("" when unreachable)
    dut "-s -m 6 http://${DUT_IP}/api/status" 2>/dev/null | python3 -c \
        "import json,sys; print(json.load(sys.stdin)['memory']['internal']['free'])" 2>/dev/null
}

dut_put_vpn() { # reads mutated-config JSON on stdin, PUTs it via curl stdin
    $PI "curl -s -o /dev/null -w '%{http_code}' -m 10 \
        -X PUT http://${DUT_IP}/api/settings/vpn_manager \
        -H 'Content-Type: application/json' -d @-"
}

dut_vpn_config() { # $1 = python mutation applied to the current settings
    dut "-s http://${DUT_IP}/api/settings/vpn_manager" | python3 -c "
import json,sys,os
d=json.load(sys.stdin)
for k in ('degraded','pending_reboot'): d.pop(k,None)
${1}
print(json.dumps(d))"
}

case "${1:-}" in
provision)
    $SRVSSH "set -e; mkdir -p $BASE
        if [ ! -x $BASE/headscale ]; then
            curl -fsSL -o $BASE/headscale https://github.com/juanfont/headscale/releases/download/v${HS_VER}/headscale_${HS_VER}_linux_amd64
            chmod +x $BASE/headscale
        fi
        if [ ! -x $BASE/ts/tailscaled ]; then
            curl -fsSL https://pkgs.tailscale.com/stable/tailscale_${TS_VER}_amd64.tgz | tar xz -C $BASE
            rm -rf $BASE/ts && mv $BASE/tailscale_${TS_VER}_amd64 $BASE/ts
        fi
        $BASE/headscale version
        $BASE/ts/tailscale version | head -1"
    echo "provisioned"
    ;;

up|run)
    umask 077
    if [ -e "$SNAPSHOT" ]; then
        echo "Saved settings already exist at $SNAPSHOT; run down to restore them first"
        exit 1
    fi
    mkdir -p "$SNAPSHOT" || exit 1
    for comp in vpn_manager data_destinations; do
        if ! dut "-f http://${DUT_IP}/api/settings/${comp}" > "$SNAPSHOT/$comp.json"; then
            echo "Cannot save $comp settings; DUT left unchanged"
            rm -f "$SNAPSHOT/vpn_manager.json" "$SNAPSHOT/data_destinations.json"
            rmdir "$SNAPSHOT"
            exit 1
        fi
    done
    if [ "$1" = "run" ]; then
        MINS="${2:-10}"
        teardown() {
            local result=$?
            trap - EXIT
            echo; echo "=== teardown (trap) ==="
            "$0" down || result=1
            exit "$result"
        }
        trap teardown EXIT
        trap 'exit 130' INT
        trap 'exit 143' TERM
    fi

    EP_IP=$($SRVSSH "curl -s -m 8 https://api.ipify.org")
    EP="$EP_IP"
    if [ -n "$EP_DNS" ]; then
        DNS_IP=$(python3 -c "import socket;print(socket.gethostbyname('${EP_DNS}'))" 2>/dev/null)
        if [ "$DNS_IP" = "$EP_IP" ]; then
            EP="$EP_DNS"   # DUT resolves the control host itself
        else
            echo "WARNING: WG_BENCH_DNS -> ${DNS_IP:-unresolved} != server ${EP_IP}; using IP control URL"
        fi
    fi
    echo "control: http://${EP}:${HSPORT}"

    # --- headscale up from FRESH state (phantom-peers gate, see
    # BUG_TS_PHANTOM_PEERS.md: a stale node db can mask NVS-cached phantoms)
    # Only stop processes whose PID files were written by this rig.
    stop_bench_process headscale || exit 1
    stop_bench_process tailscaled || exit 1
    stop_bench_process tailscaled2 || exit 1
    $SRVSSH "set -e; umask 077
        rm -rf /var/lib/headscale /etc/headscale /run/tsb.sock $BASE/tsb_state /run/tsb2.sock $BASE/tsb2_state
        mkdir -p /etc/headscale /var/lib/headscale /var/run/headscale
        cat > /etc/headscale/config.yaml <<EOF
server_url: http://${EP}:${HSPORT}
listen_addr: 0.0.0.0:${HSPORT}
metrics_listen_addr: 127.0.0.1:9090
grpc_listen_addr: 127.0.0.1:50443
grpc_allow_insecure: false
noise:
  private_key_path: /var/lib/headscale/noise_private.key
prefixes:
  v4: 100.64.0.0/10
  v6: fd7a:115c:a1e0::/48
  allocation: sequential
derp:
  server:
    enabled: false
  urls:
    - https://controlplane.tailscale.com/derpmap/default
  auto_update_enabled: true
  update_frequency: 24h
disable_check_updates: true
ephemeral_node_inactivity_timeout: 30m
database:
  type: sqlite
  sqlite:
    path: /var/lib/headscale/db.sqlite
    write_ahead_log: true
log:
  format: text
  level: info
dns:
  magic_dns: false
  base_domain: bench.internal
  nameservers:
    global: []
    split: {}
  search_domains: []
  extra_records: []
unix_socket: /var/run/headscale/headscale.sock
unix_socket_permission: \"0770\"
logtail:
  enabled: false
randomize_client_port: false
EOF
        nohup $BASE/headscale serve > /var/log/hs_bench.log 2>&1 < /dev/null &
        echo \$! > $BASE/headscale.pid
        sleep 4"
    HS_OK=$($SRVSSH "curl -s -m 5 -o /dev/null -w '%{http_code}' http://127.0.0.1:${HSPORT}/health")
    check "headscale serving" "$([ "$HS_OK" = "200" ] && echo 1 || echo 0)" "health=$HS_OK"
    PUB_OK=$(curl -s -m 10 -o /dev/null -w '%{http_code}' "http://${EP}:${HSPORT}/health")
    check "control publicly reachable" "$([ "$PUB_OK" = "200" ] && echo 1 || echo 0)" "health=$PUB_OK"
    if [ "$HS_OK" != "200" ]; then
        echo "headscale failed to start: aborting before touching the DUT"
        $SRVSSH "tail -5 /var/log/hs_bench.log 2>/dev/null"
        exit 1
    fi

    # --- disposable preauth key (root-only file, never printed)
    $SRVSSH "umask 077
        $BASE/headscale users create wican >/dev/null 2>&1
        $BASE/headscale preauthkeys create --user wican --reusable --expiration 1h 2>/dev/null | tail -1 > $KEYFILE"
    KLEN=$($SRVSSH "wc -c < $KEYFILE")
    check "preauth key created" "$([ "${KLEN:-0}" -gt 20 ] && echo 1 || echo 0)"
    if [ "${KLEN:-0}" -le 20 ]; then
        echo "no preauth key: aborting before touching the DUT"
        exit 1
    fi
    TSKEY=$($SRVSSH "cat $KEYFILE")

    # --- DUT -> tailscale (key via stdin, never argv)
    CODE=$(TSKEY="$TSKEY" EP="$EP" HSPORT="$HSPORT" dut_vpn_config \
"d.update({'enabled':True,'type':'tailscale',
           'ts_auth_key':os.environ['TSKEY'],
           'ts_device_name':'wican-pub',
           'ts_control_url':os.environ['EP']+':'+os.environ['HSPORT']})" \
        | dut_put_vpn)
    check "DUT vpn PUT" "$([ "$CODE" = "200" ] && echo 1 || echo 0)" "http $CODE"
    # Test runs disabled rows too. Keep scheduled delivery off so no request
    # precedes the receiver, peer or route, and every request is accounted for.
    CODE=$(dut_dd_config "$DD_ROW_ADD")
    check "DUT destination row PUT" "$([ "$CODE" = "200" ] && echo 1 || echo 0)" "http $CODE"
    dut "-X POST http://${DUT_IP}/api/settings/submit" >/dev/null
    echo "DUT submitted (rebooting)"

    # --- wait for CONNECTED + tailnet IP (LTE can be slow)
    V=""; end=$((SECONDS+300))
    while [ $SECONDS -lt $end ]; do
        V=$(dut "-s http://${DUT_IP}/api/vpn" 2>/dev/null)
        echo "$V" | grep -q '"state":"connected"' && echo "$V" | grep -q '"ts_ip":"100\.' && break
        sleep 5
    done
    TSIP=$(echo "$V" | sed -n 's/.*"ts_ip":"\([0-9.]*\)".*/\1/p')
    check "tailscale CONNECTED" "$(echo "$V" | grep -q '"state":"connected"' && echo 1 || echo 0)" "$V"
    check "tailnet IP assigned" "$([ -n "$TSIP" ] && echo 1 || echo 0)" "$TSIP"
    NODES=$($SRVSSH "$BASE/headscale nodes list 2>/dev/null | grep -c wican-pub")
    check "node registered in headscale" "$([ "${NODES:-0}" -ge 1 ] && echo 1 || echo 0)"

    # --- the relay-only pass (2026-11-03; the field report of an MQTT broker
    # behind a home subnet router that the WiCAN on a phone hotspot could
    # not reach while the VPN said connected and listed the route): ALL UDP
    # between betty and the DUT's public address is dropped on betty BEFORE
    # the peer joins, so no direct path can exist in either direction and
    # the peer never contacts the DUT: the DUT must open the session itself,
    # through the relay (DERP is TCP 443 to the public relays, not to betty,
    # so it is untouched). The drop is by the DUT's public IP, not by port:
    # tailscaled binds more than one UDP socket (a --port pin covers only one;
    # the first attempt dropped port 41641 and the DUT still found a direct
    # path through the second socket, latched betty's address and black-holed
    # its own data). Every earlier run had a direct path to betty's public
    # address and the peer pinging first, which hid two defects: lwIP traffic
    # never started a WireGuard handshake (the driver answered ERR_CONN and
    # nobody initiated), and the relay sent through the DUT's own home region
    # only (Dallas by default), which answers PeerGone for a peer homed
    # anywhere else (Sydney for betty). The rules come off before the
    # direct-path legs. DUT_PUB = the Pi's public IP (the DUT's NAT).
    DUT_PUB=$($PI "curl -s -m 8 https://api.ipify.org" 2>/dev/null)
    if [[ ! "$DUT_PUB" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]] ||
       [ "$DUT_PUB" = "$EP_IP" ]; then
        echo "No distinct DUT public IPv4 address; cannot enforce the relay-only pass"
        exit 1
    else
        echo "relay-only: dropping all UDP between betty and the DUT ($DUT_PUB)"
        printf '%s' "$DUT_PUB" > "$SNAPSHOT/public_ip"
        $SRVSSH "iptables -I INPUT -p udp -s ${DUT_PUB} -m comment --comment ts-public-bench -j DROP
            iptables -I OUTPUT -p udp -d ${DUT_PUB} -m comment --comment ts-public-bench -j DROP" || exit 1
    fi

    # --- real peer on betty (reads the key from the root-only file itself)
    # --port pinned so betty-peer's main socket is deterministic in the log
    $SRVSSH "nohup $BASE/ts/tailscaled --verbose=2 --state=$BASE/tsb_state --socket=/run/tsb.sock --port=41641 > /var/log/tsdb.log 2>&1 < /dev/null &
        echo \$! > $BASE/tailscaled.pid
        sleep 4
        $BASE/ts/tailscale --socket=/run/tsb.sock up --login-server http://127.0.0.1:${HSPORT} \
            --auth-key \"file:$KEYFILE\" --hostname betty-peer --accept-dns=false 2>/dev/null \
        || $BASE/ts/tailscale --socket=/run/tsb.sock up --login-server http://127.0.0.1:${HSPORT} \
            --auth-key \"\$(cat $KEYFILE)\" --hostname betty-peer --accept-dns=false"
    RC=$?
    check "betty peer joined" "$([ "$RC" = "0" ] && echo 1 || echo 0)" "rc=$RC"
    REL=$(tsq "status --json" 2>/dev/null | python3 -c "import json,sys; print(json.load(sys.stdin)['Self'].get('Relay',''))" 2>/dev/null)
    echo "betty peer's home relay: ${REL:-?} (the DUT's own is region 9, dfw, unless the map lacks it)"

    # --- the second peer (2026-11-09, `run` only; see PEER2_IP above): it
    # joins behind the same UDP drop and stays silent until it opens its
    # session to the DUT, after the first subnet request below
    if [ "$1" = "run" ]; then
        peer_daemon tailscaled2 tsb2_state /run/tsb2.sock 41642 /var/log/tsdb_p2.log "$PEER2_FLAGS"
        $SRVSSH "sleep 4
            $BASE/ts/tailscale --socket=/run/tsb2.sock up --login-server http://127.0.0.1:${HSPORT} \
                --auth-key \"file:$KEYFILE\" --hostname betty-peer2 --accept-dns=false 2>/dev/null"
        RC=$?
        check "second peer joined" "$([ "$RC" = "0" ] && echo 1 || echo 0)" "rc=$RC"
        P2=$(tsq2 "ip -4" 2>/dev/null | head -1)
        check "second peer holds the address the destination row was staged with" \
            "$([ "$P2" = "$PEER2_IP" ] && echo 1 || echo 0)" "${P2:-none} (staged $PEER2_IP)"
    fi

    # --- the DERP relay (2026-10-31, issue 1024): the public Tailscale relays
    # over TLS on :443 (the derpmap above). Every handshake failed on v6
    # until the Ed25519 meta certificate was tolerated again; /api/vpn
    # `derp` is the firmware's own word on it.
    D=0; end=$((SECONDS+90))
    while [ $SECONDS -lt $end ]; do
        V3=$(dut "-s -m 6 http://${DUT_IP}/api/vpn" 2>/dev/null)
        echo "$V3" | grep -q '"derp":true' && D=1 && break
        sleep 5
    done
    check "DERP relay connected" "$D" "$(echo "$V3" | sed -n 's/.*\("derp":[a-z]*\).*/\1/p')"

    # --- a subnet router (2026-11-01; the Discord question "should pushing
    # routes via tailscale work?"): betty advertises SUBNET, an alias on its
    # loopback, headscale approves it, and the DUT must learn it from the
    # map (/api/vpn routes) and reach an address in it through the tunnel:
    # the receiver on betty records the request's source, the DUT's tailnet
    # address. Before 2026-11-01 the firmware ignored a peer's AllowedIPs
    # beyond its own address, so the request left over the station and
    # died (the reporter's "WiCAN says it can't connect to the MQTT server").
    # Since 2026-11-03 this first request runs in the relay-only pass (see
    # above): the DUT initiates, no direct path, the peer silent until now.
    scp -q -o BatchMode=yes "$RECEIVER_SRC" "$SRV:$BASE/dd_receiver.py"
    stop_bench_process receiver || exit 1
    $SRVSSH "ip addr add ${SUBNET_IP}/24 dev lo 2>/dev/null || true
        nohup python3 $BASE/dd_receiver.py --no-tls --mqtt none > /var/log/ddr_bench.log 2>&1 < /dev/null &
        echo \$! > $BASE/receiver.pid
        $BASE/ts/tailscale --socket=/run/tsb.sock set --advertise-routes=${SUBNET} 2>&1 | tail -2
        sleep 3
        $BASE/headscale routes list -o json 2>/dev/null | python3 -c \"
import json,sys
for r in json.load(sys.stdin) or []:
    if r.get('prefix')=='${SUBNET}': print(r.get('id'))\" | while read id; do
            $BASE/headscale routes enable -r \$id >/dev/null 2>&1 && echo \"route \$id (${SUBNET}) enabled on headscale\"
        done"
    RL=$($SRVSSH "$BASE/headscale routes list 2>/dev/null | grep -F '${SUBNET}' | head -1")
    check "subnet route advertised and enabled on headscale" "$(echo "$RL" | grep -qi 'true.*true' && echo 1 || echo 0)" "$RL"
    N0=$(recv_count)
    check "receiver up on betty" "$([ -n "$N0" ] && echo 1 || echo 0)" "records=${N0:-?}"
    R0=$(recv_paths /route)
    R=0; end=$((SECONDS+120))
    while [ $SECONDS -lt $end ]; do
        V4=$(dut "-s -m 6 http://${DUT_IP}/api/vpn" 2>/dev/null)
        echo "$V4" | grep -q "\"routes\":\[[^]]*\"${SUBNET}\"" && R=1 && break
        sleep 5
    done
    check "subnet route learned from the map" "$R" "$(echo "$V4" | sed -n 's/.*\("routes":\[[^]]*\]\).*/\1/p')"
    T=$(dut "-s -m 40 -X POST http://${DUT_IP}/api/destinations/test -H 'Content-Type: application/json' -d '{\"name\":\"tsroute\"}'" 2>/dev/null)
    check "relay-only: a request the DUT initiates through the subnet route answers" "$(echo "$T" | grep -q '"ok":true' && echo 1 || echo 0)" "$(echo "$T" | head -c 200)"
    REC=$($SRVSSH "sleep 2; curl -s -m 5 'http://127.0.0.1:${RECV_PORT}/__dump?since=${N0:-0}' | python3 -c \"
import json,sys
r=[x for x in json.load(sys.stdin) if x.get('path','').endswith('/route')]
print(r[-1]['peer'] if r else 'none')\"" 2>/dev/null)
    check "relay-only: the subnet router saw the request from the tailnet address" "$([ -n "$TSIP" ] && [ "$REC" = "$TSIP" ] && echo 1 || echo 0)" "peer=$REC (expected $TSIP)"
    echo "relay-only path: $(tsq status 2>/dev/null | grep -i wican || echo '?')"
    check "relay-only: the peer has no false direct endpoint" \
        "$(tsq status 2>/dev/null | grep -qE 'wican-pub.*relay' && echo 1 || echo 0)"
    # --- a session the PEER opens (2026-11-09, `run` only): the second peer
    # asks the DUT for a page through the relay, the way Home Assistant
    # talks to a WiCAN it reaches over the tailnet. The DUT is the responder
    # of that session and never asked for it; the warm loop below then sends
    # the DUT's own requests to that peer's address through it, across the
    # peer's rekey. That is the state the lapse leg needs: on beta.8 the key
    # the first rekey left behind never expired and hid the dead session.
    PEER_LEGS=0
    if [ "$1" = "run" ] && echo "$T" | grep -q '"ok":true'; then
        PEER_LEGS=1
        H2=$($SRVSSH "curl -s -m 45 -o /dev/null -w '%{http_code}' --socks5-hostname $PEER2_SOCKS http://$TSIP/api/status")
        check "relay-only: the second peer opens a session to the DUT itself (a page through the relay)" \
            "$([ "$H2" = "200" ] && echo 1 || echo 0)" "code=${H2:-none}"
        P0=$(recv_paths /peer)
    fi
    if echo "$T" | grep -q '"ok":true'; then
        # Exercise a warm session through WireGuard's 120 s rekey boundary.
        for n in 1 2 3 4 5; do
            sleep 30
            T=$(dut "-s -m 40 -X POST http://${DUT_IP}/api/destinations/test -H 'Content-Type: application/json' -d '{\"name\":\"tsroute\"}'")
            check "relay-only: warm request $n" \
                "$(echo "$T" | grep -q '\"ok\":true,\"status\":200' && echo 1 || echo 0)" "$T"
            if [ "$PEER_LEGS" = "1" ]; then
                T=$(dd_test tspeer)
                check "relay-only: request $n to the second peer's own address (the session it opened)" \
                    "$(echo "$T" | grep -q '\"ok\":true,\"status\":200' && echo 1 || echo 0)" "$T"
            fi
        done
        R2=$(recv_paths /route)
        check "relay-only: all six requests arrived exactly once" \
            "$([ -n "$R0" ] && [ -n "$R2" ] && [ "$((R2 - R0))" -eq 6 ] && echo 1 || echo 0)" \
            "records ${R0:-?} -> ${R2:-?}"
        if [ "$PEER_LEGS" = "1" ]; then
            P1=$(recv_paths /peer)
            check "relay-only: the five requests to the second peer's own address arrived exactly once" \
                "$([ -n "$P0" ] && [ -n "$P1" ] && [ "$((P1 - P0))" -eq 5 ] && echo 1 || echo 0)" \
                "records ${P0:-?} -> ${P1:-?}"
        fi
    fi

    # --- the lapse (2026-11-09, `run` only; the case of a webhook at Home
    # Assistant's tailnet address that times out for ever while the VPN
    # says connected). Both peers go away for LAPSE_S, past WireGuard's
    # REJECT_AFTER_TIME, and the DUT sends them nothing: every session key
    # on the DUT is dead when the second peer comes back, and that peer
    # stays silent. The DUT's next request to the peer's OWN address then
    # has to bring the session up by itself, which takes two things that
    # beta.8 lacked: the packet must reach the tunnel while its link is down
    # (the tailnet's own network in ip4_routes: a peer's address used to be
    # left to lwIP's netif walk, which skips a netif whose link is down, so
    # the SYN left on the station in clear text), and the key a rekey left
    # behind must have expired with the session (it kept the peer reading
    # as up, the want was dropped and nothing ever handshook again; the
    # failure this leg shows on beta.8). The first peer returns afterwards:
    # its session, which the DUT had opened, must carry the subnet route
    # again too.
    if [ "$PEER_LEGS" = "1" ]; then
        echo "lapse: both peers away for ${LAPSE_S} s, the DUT silent"
        stop_bench_process tailscaled || exit 1
        stop_bench_process tailscaled2 || exit 1
        sleep "$LAPSE_S"
        peer_daemon tailscaled2 tsb2_state /run/tsb2.sock 41642 /var/log/tsdb_p2.log "$PEER2_FLAGS"
        peer_running /run/tsb2.sock
        check "after the lapse: the second peer is back (silent)" "$([ $? = 0 ] && echo 1 || echo 0)"
        sleep 15    # its relay connection, before the DUT's first packet for it
        P1=$(recv_paths /peer)
        T=$(dd_test tspeer)
        check "after the lapse: a request to the second peer's own address answers (the DUT opens the session)" \
            "$(echo "$T" | grep -q '\"ok\":true,\"status\":200' && echo 1 || echo 0)" "$(echo "$T" | head -c 200)"
        T=$(dd_test tspeer)
        check "after the lapse: a second request on that session answers" \
            "$(echo "$T" | grep -q '\"ok\":true,\"status\":200' && echo 1 || echo 0)" "$(echo "$T" | head -c 200)"
        P2N=$(recv_paths /peer)
        check "after the lapse: both requests arrived exactly once" \
            "$([ -n "$P1" ] && [ -n "$P2N" ] && [ "$((P2N - P1))" -eq 2 ] && echo 1 || echo 0)" \
            "records ${P1:-?} -> ${P2N:-?}"
        peer_daemon tailscaled tsb_state /run/tsb.sock 41641 /var/log/tsdb.log --verbose=2
        peer_running /run/tsb.sock
        check "after the lapse: the first peer is back" "$([ $? = 0 ] && echo 1 || echo 0)"
        R=0; end=$((SECONDS+90))
        while [ $SECONDS -lt $end ]; do
            V4=$(dut "-s -m 6 http://${DUT_IP}/api/vpn" 2>/dev/null)
            echo "$V4" | grep -q "\"routes\":\[[^]]*\"${SUBNET}\"" && R=1 && break
            sleep 5
        done
        check "after the lapse: the subnet route is listed" "$R" "$(echo "$V4" | sed -n 's/.*\("routes":\[[^]]*\]\).*/\1/p')"
        sleep 15
        T=$(dd_test tsroute)
        check "after the lapse: a request through the subnet route answers" \
            "$(echo "$T" | grep -q '\"ok\":true,\"status\":200' && echo 1 || echo 0)" "$(echo "$T" | head -c 200)"
        echo "after the lapse: $(tsq status 2>/dev/null | grep -i wican || echo '?')"
    fi
    # the pass is only worth its name while the drops bit: the OUTPUT rule's
    # packet counter (UDP to the DUT that betty tried to send and could not)
    DROPPED=$($SRVSSH "iptables -L OUTPUT -v -n 2>/dev/null | grep -F '${DUT_PUB}' | awk '{print \$1}' | head -1")
    check "relay-only: the direct UDP path was blocked throughout" "$([ -n "$DUT_PUB" ] && [ "$DUT_PUB" != "$EP_IP" ] && [ "${DROPPED:-0}" -gt 0 ] 2>/dev/null && echo 1 || echo 0)" "dut_pub=${DUT_PUB:-?} dropped=${DROPPED:-0} packets"

    # the direct UDP path back on: the direct-path legs below
    if [ -f "$SNAPSHOT/public_ip" ]; then
        $SRVSSH "iptables -D INPUT -p udp -s ${DUT_PUB} -m comment --comment ts-public-bench -j DROP
            iptables -D OUTPUT -p udp -d ${DUT_PUB} -m comment --comment ts-public-bench -j DROP" || exit 1
        rm "$SNAPSHOT/public_ip"
    fi

    # --- data plane over the real path
    # Give disco time to punch a direct path before the ping legs (over
    # LTE the first punch can take >30 s; ICMP legs ran too early once
    # and false-failed while HTTP-over-DERP passed).
    end=$((SECONDS+60))
    while [ $SECONDS -lt $end ]; do
        tsq "status" 2>/dev/null | grep -qiE "wican.*direct" && break
        sleep 5
    done
    P=$(tsq "ping -c 5 --timeout 5s $TSIP" 2>&1 | tail -1)
    check "disco ping" "$(echo "$P" | grep -q pong && echo 1 || echo 0)" "$P"
    I=$($SRVSSH "ping -c 8 -W 4 -q $TSIP" | grep -E "received" || true)
    check "ICMP through tunnel" "$(echo "$I" | grep -qE ' [1-8] received' && echo 1 || echo 0)" "$I"
    H=$($SRVSSH "curl -s -m 30 -o /dev/null -w '%{http_code}' http://$TSIP/api/status")
    check "HTTP through tunnel" "$([ "$H" = "200" ] && echo 1 || echo 0)" "code=$H"
    V2=$(dut "-s http://${DUT_IP}/api/vpn")
    check "DUT sees the peer" "$(echo "$V2" | grep -q '"ts_peers":0' && echo 0 || echo 1)" "$(echo "$V2" | head -c 240)"
    echo "path: $(tsq status 2>/dev/null | grep -i wican || echo '?')"

    # --- the subnet route once more, now with the direct path up (the
    # 2026-11-01 leg as it was): the receiver records the second request
    N1=$(recv_count)
    T=$(dut "-s -m 40 -X POST http://${DUT_IP}/api/destinations/test -H 'Content-Type: application/json' -d '{\"name\":\"tsroute\"}'" 2>/dev/null)
    check "destination test through the subnet route answers" "$(echo "$T" | grep -q '"ok":true' && echo 1 || echo 0)" "$(echo "$T" | head -c 200)"
    REC=$($SRVSSH "sleep 2; curl -s -m 5 'http://127.0.0.1:${RECV_PORT}/__dump?since=${N1:-0}' | python3 -c \"
import json,sys
r=[x for x in json.load(sys.stdin) if x.get('path','').endswith('/route')]
print(r[-1]['peer'] if r else 'none')\"" 2>/dev/null)
    check "the subnet router saw the request from the tailnet address" "$([ -n "$TSIP" ] && [ "$REC" = "$TSIP" ] && echo 1 || echo 0)" "peer=$REC (expected $TSIP)"

    # --- the Tailscale task stacks (2026-11-01): the state task and the
    # WireGuard manager live on PSRAM stacks now (their NVS work runs on the
    # flash worker); a build that moved them back shows here
    S=$(dut "-s -m 6 http://${DUT_IP}/api/status/tasks" 2>/dev/null | python3 -c "
import json,sys
t={x['name']:x for x in json.load(sys.stdin)['tasks']}
print(' '.join(f\"{n}={t[n].get('stack_ext')}/{t[n].get('stack_hw')}\" if n in t else f'{n}=missing' for n in ('vpn_state','ml_wg_mgr')))" 2>/dev/null)
    check "vpn_state and ml_wg_mgr stacks in PSRAM" "$(echo "$S" | grep -q 'vpn_state=True' && echo "$S" | grep -q 'ml_wg_mgr=True' && echo 1 || echo 0)" "$S"

    # --- internal RAM flat (the leak gate of the same day): a refused relay
    # cost 21 KB PSRAM + 230 B internal per attempt, several a minute
    M0=$(dut_int_free)
    echo "internal RAM free after the legs: ${M0} B"
    if [ "$1" = "up" ]; then
        sleep 60
        M1=$(dut_int_free)
        check "internal RAM flat over 60 s" "$([ -n "$M0" ] && [ -n "$M1" ] && [ $((M0 - M1)) -lt 4096 ] && echo 1 || echo 0)" "${M0} -> ${M1} B"
    fi

    # --- soak
    if [ "$1" = "run" ]; then
        echo "=== soak ${MINS} min ==="
        end=$(( $(date +%s) + MINS*60 ))
        while [ "$(date +%s)" -lt "$end" ]; do
            sleep 30
            S=$(dut "-s -m 6 http://${DUT_IP}/api/vpn" 2>/dev/null |
                sed -n 's/.*"state":"\([a-z]*\)".*"ts_peers":\([0-9]*\),"derp":\([a-z]*\).*/state=\1 peers=\2 derp=\3/p')
            L=$(tsq status 2>/dev/null | grep -i wican | sed 's/  */ /g' | cut -c1-90)
            echo "$(date +%H:%M:%S)  DUT ${S:-unreachable!}  int free $(dut_int_free) B  |  ${L:-peer line missing}"
        done
        M1=$(dut_int_free)
        check "internal RAM flat over the soak" "$([ -n "$M0" ] && [ -n "$M1" ] && [ $((M0 - M1)) -lt 4096 ] && echo 1 || echo 0)" "${M0} -> ${M1} B"
    fi
    HEALTH=$(dut "-f http://${DUT_IP}/api/status")
    check "zero error logs and zero faults" \
        "$(echo "$HEALTH" | grep -q '\"log_errors\":0' &&
           echo "$HEALTH" | grep -q '\"faults\":0' && echo 1 || echo 0)"
    ;;

status)
    echo "=== headscale nodes ==="; $SRVSSH "$BASE/headscale nodes list 2>/dev/null || echo DOWN"
    echo "=== betty peer ==="; tsq status 2>/dev/null || echo "peer down"
    echo "=== DUT vpn ==="; dut "-s http://${DUT_IP}/api/vpn"; echo
    ;;

down)
    echo "=== tearing down ==="
    RESTORED=1
    for comp in vpn_manager data_destinations; do
        if [ -f "$SNAPSHOT/$comp.json" ]; then
            CODE=$($PI "curl -s -m 10 -o /dev/null -w '%{http_code}' -X PUT \
                http://${DUT_IP}/api/settings/${comp} -H 'Content-Type: application/json' \
                -d @-" < "$SNAPSHOT/$comp.json")
            if [ "$CODE" != "200" ]; then
                echo "Restore $comp failed (HTTP $CODE); saved settings retained"
                RESTORED=0
            fi
        fi
    done
    if [ "$RESTORED" = "1" ] && [ -d "$SNAPSHOT" ]; then
        dut "-f -X POST http://${DUT_IP}/api/settings/submit" >/dev/null || RESTORED=0
        echo "DUT settings restore submitted (planned restart)"
        sleep 8
        for comp in vpn_manager data_destinations; do
            end=$((SECONDS+90))
            MATCH=0
            while [ $SECONDS -lt $end ]; do
                if dut "-f http://${DUT_IP}/api/settings/${comp}" > "$SNAPSHOT/verify.json" &&
                   cmp -s "$SNAPSHOT/$comp.json" "$SNAPSHOT/verify.json"; then
                    MATCH=1
                    break
                fi
                sleep 3
            done
            if [ "$MATCH" != "1" ]; then
                echo "Restored $comp settings did not match; snapshot retained"
                RESTORED=0
            fi
        done
        rm -f "$SNAPSHOT/verify.json"
    fi
    # Remove only the receiver, alias and tagged firewall rules of this rig.
    stop_bench_process receiver || exit 1
    $SRVSSH "ip addr del ${SUBNET_IP}/24 dev lo 2>/dev/null; true"
    # Use the saved address even if the DUT's public IP has changed.
    if [ -f "$SNAPSHOT/public_ip" ]; then
        DUT_PUB=$(cat "$SNAPSHOT/public_ip")
        $SRVSSH "iptables -D INPUT -p udp -s ${DUT_PUB} -m comment --comment ts-public-bench -j DROP
            iptables -D OUTPUT -p udp -d ${DUT_PUB} -m comment --comment ts-public-bench -j DROP" || exit 1
        rm "$SNAPSHOT/public_ip"
    fi
    stop_bench_process tailscaled || exit 1
    stop_bench_process tailscaled2 || exit 1
    stop_bench_process headscale || exit 1
    $SRVSSH "
        rm -rf /var/lib/headscale /etc/headscale $BASE/tsb_state /run/tsb.sock $BASE/tsb2_state /run/tsb2.sock $KEYFILE
        left=\$(ls /var/lib/headscale /etc/headscale $BASE/tsb_state $BASE/tsb2_state $KEYFILE 2>/dev/null | wc -l)
        echo \"betty: control+peer down, state+keys wiped (residue files: \$left)\"" || exit 1
    if [ "$RESTORED" = "1" ] && [ -d "$SNAPSHOT" ]; then
        rm "$SNAPSHOT/vpn_manager.json" "$SNAPSHOT/data_destinations.json"
        rmdir "$SNAPSHOT"
    fi
    [ "$RESTORED" = "1" ] || exit 1
    ;;

*)
    sed -n '2,34p' "$0"; exit 1 ;;
esac

if [ "${1:-}" = "up" ] || [ "${1:-}" = "run" ]; then
    if [ "${#FAILS[@]}" -gt 0 ]; then
        echo "TS PUBLIC FAIL: ${FAILS[*]}"
        exit 1
    fi
    echo "TS PUBLIC PASS"
fi
