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
#   legs:   register -> tailnet IP -> disco ping -> ICMP -> HTTP through
#           the tunnel -> DUT peer-detail surface -> the DERP relay up
#           (/api/vpn derp, 2026-10-31) -> a subnet route the betty peer
#           advertises learned from the map and a request through it
#           (2026-11-01) -> the Tailscale task stacks on PSRAM -> internal
#           RAM flat over 60 s (`up`) or over the soak (`run`); then the
#           soak loop.
#
# KEY HYGIENE (same rules as wg_public_soak.sh):
#   * headscale's noise private key + node db live only on betty; `down`
#     wipes /var/lib/headscale AND /etc/headscale AND the peer state.
#   * the disposable preauth key (1h) is created on betty, stored root-only
#     (/root/.ts_bench_key, 0600), read once into the orchestrator's memory,
#     and delivered to the DUT via curl stdin (-d @-) so it never appears in
#     argv, logs, or this repo. The betty peer reads it from the file
#     directly. It is NEVER printed; `down` blanks the DUT copy.
#   * `run` tears everything down via an EXIT trap.
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

FAILS=()
check() { # name ok [detail]
    local d="${3:-}"
    if [ "$2" = "1" ]; then echo "PASS: $1${d:+ ($d)}"
    else echo "FAIL: $1${d:+ ($d)}"; FAILS+=("$1"); fi
}
dut() { $PI "curl -s -m 10 $*"; }
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
DD_ROW_DROP="d['destinations']=[r for r in d.get('destinations',[]) if r.get('name')!='tsroute']"
DD_ROW_ADD="${DD_ROW_DROP}
d['enabled']=True
d['destinations'].append({'name':'tsroute','type':'http','enabled':True,'url':'http://${SUBNET_IP}:${RECV_PORT}/route','period_s':86400,'auth':'none','auth_token':'','auth_name':'','basic_username':'','basic_password':'','api_key':'','query':'','cert_set':'','car_model':'','retain':True,'full_first':True})"
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
    if [ "$1" = "run" ]; then
        MINS="${2:-10}"
        teardown() { echo; echo "=== teardown (trap) ==="; "$0" down; }
        trap teardown EXIT INT TERM
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
    # separate ssh for the kills: the bracket trick protects against the
    # pattern itself, but the START block below carries the plain
    # 'headscale serve' string in its nohup line: same-shell pkill would
    # self-match through THAT and kill the block (burned twice here)
    $SRVSSH "pkill -f '$BASE/[h]eadscale serve' 2>/dev/null || true
        pkill -f '[t]ailscaled.*tsb.sock' 2>/dev/null || true; sleep 1"
    $SRVSSH "set -e; umask 077
        rm -rf /var/lib/headscale /etc/headscale /run/tsb.sock $BASE/tsb_state
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
    # the destination row the subnet leg tests with (one submit for both)
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

    # --- real peer on betty (reads the key from the root-only file itself)
    $SRVSSH "nohup $BASE/ts/tailscaled --state=$BASE/tsb_state --socket=/run/tsb.sock > /var/log/tsdb.log 2>&1 < /dev/null & sleep 4
        $BASE/ts/tailscale --socket=/run/tsb.sock up --login-server http://127.0.0.1:${HSPORT} \
            --auth-key \"file:$KEYFILE\" --hostname betty-peer --accept-dns=false 2>/dev/null \
        || $BASE/ts/tailscale --socket=/run/tsb.sock up --login-server http://127.0.0.1:${HSPORT} \
            --auth-key \"\$(cat $KEYFILE)\" --hostname betty-peer --accept-dns=false"
    RC=$?
    check "betty peer joined" "$([ "$RC" = "0" ] && echo 1 || echo 0)" "rc=$RC"

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
    scp -q -o BatchMode=yes "$RECEIVER_SRC" "$SRV:$BASE/dd_receiver.py"
    # the pkill in its own ssh call: the start block below carries the plain
    # 'dd_receiver.py' in its nohup line, and a same-shell pkill self-matches
    # through it and kills the block (the headscale trap above, met again
    # here on 2026-10-31)
    $SRVSSH "pkill -f '[d]d_receiver.py' 2>/dev/null; sleep 1; true"
    $SRVSSH "ip addr add ${SUBNET_IP}/24 dev lo 2>/dev/null || true
        nohup python3 $BASE/dd_receiver.py --no-tls --mqtt none > /var/log/ddr_bench.log 2>&1 < /dev/null &
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
    R=0; end=$((SECONDS+120))
    while [ $SECONDS -lt $end ]; do
        V4=$(dut "-s -m 6 http://${DUT_IP}/api/vpn" 2>/dev/null)
        echo "$V4" | grep -q "\"routes\":\[[^]]*\"${SUBNET}\"" && R=1 && break
        sleep 5
    done
    check "subnet route learned from the map" "$R" "$(echo "$V4" | sed -n 's/.*\("routes":\[[^]]*\]\).*/\1/p')"
    T=$(dut "-s -m 40 -X POST http://${DUT_IP}/api/destinations/test -H 'Content-Type: application/json' -d '{\"name\":\"tsroute\"}'" 2>/dev/null)
    check "destination test through the subnet route answers" "$(echo "$T" | grep -q '"ok":true' && echo 1 || echo 0)" "$(echo "$T" | head -c 200)"
    REC=$($SRVSSH "sleep 2; curl -s -m 5 'http://127.0.0.1:${RECV_PORT}/__dump?since=${N0:-0}' | python3 -c \"
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
    ;;

status)
    echo "=== headscale nodes ==="; $SRVSSH "$BASE/headscale nodes list 2>/dev/null || echo DOWN"
    echo "=== betty peer ==="; tsq status 2>/dev/null || echo "peer down"
    echo "=== DUT vpn ==="; dut "-s http://${DUT_IP}/api/vpn"; echo
    ;;

down)
    echo "=== tearing down ==="
    # DUT first (blank the key), tolerate an unreachable DUT
    VCFG=$(dut_vpn_config "d.update({'enabled':False,'type':'wireguard','ts_auth_key':'','ts_control_url':'','ts_device_name':''})" 2>/dev/null)
    if [ -n "${VCFG:-}" ]; then
        printf '%s' "$VCFG" | dut_put_vpn | sed 's/^/DUT vpn disable PUT -> /'; echo
        echo "DUT destination row removed -> $(dut_dd_config "$DD_ROW_DROP" 2>/dev/null)"
        dut "-X POST http://${DUT_IP}/api/settings/submit" >/dev/null 2>&1 && echo "DUT submitted (VPN off, key blanked)"
    else
        echo "DUT unreachable: blank ts_auth_key and drop the tsroute destination manually when it is back"
    fi
    # the subnet router leg's pieces on betty (the pkill alone in its call:
    # it would self-match through any other mention of the receiver)
    $SRVSSH "pkill -f '[d]d_receiver.py' 2>/dev/null; true"
    $SRVSSH "ip addr del ${SUBNET_IP}/24 dev lo 2>/dev/null; true"
    $SRVSSH "$BASE/ts/tailscale --socket=/run/tsb.sock down 2>/dev/null
        pkill -f '[t]ailscaled.*tsb.sock' 2>/dev/null || true
        pkill -f '$BASE/[h]eadscale serve' 2>/dev/null || true
        sleep 1
        rm -rf /var/lib/headscale /etc/headscale $BASE/tsb_state /run/tsb.sock $KEYFILE
        left=\$(ls /var/lib/headscale /etc/headscale $BASE/tsb_state $KEYFILE 2>/dev/null | wc -l)
        pgrep -f '$BASE/([h]eadscale|ts/[t]ailscaled)' >/dev/null && echo 'WARNING: processes still up' || echo \"betty: control+peer down, state+keys wiped (residue files: \$left)\""
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
