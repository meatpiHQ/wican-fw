#!/usr/bin/env python3
"""data_destinations end-to-end bench (PC orchestrator, receivers on rpi001).

The real-life matrix for Automate > Data destinations: every destination
type against receivers the bench controls, then the things a car does to
a device - the server disappears, the broker disappears, the WiFi
disappears and comes back - with the counters, the backoff and the log
as the witnesses. Verdict line: DATA DEST PASS / FAIL.

Legs (each prints PASS/FAIL checks):
  A  preflight: firmware has /api/destinations; originals saved
  B  cert_manager: upload the bench CA as set `ddbench` and CA + client
     pair as `ddmtls` through /api/certs (the System > Certificates page's
     raw POSTs); listed back
  C  configure 8 destinations + MQTT (one submit = one planned reboot):
       mq    mqtt  ~/dd (retained)
       h     http  :8199/h   basic auth, extra query, first push = full
       hs    https :8443/hs  cert_set ddbench, bearer token   (positive TLS)
       hsx   https :8443/hsx NO cert set  -> the bundle must REFUSE it
       mt    https :8444/mt  cert_set ddmtls (server demands a client cert)
       mtx   https :8444/mtx cert_set ddbench (CA only) -> must fail
       abrp  abrp  :8445 mock Iternio, api_key in the query, car_model
       abrph abrp  :8445 api_key as Authorization: APIKEY header
  D  steady state (OBSERVE s): deliveries per destination at the receiver
     (headers, query, first-push shape, tlm contents, mTLS peer CN, MQTT
     retained messages), negatives at 0 success, counters consistent,
     E lines = only the IDF client's TLS/connect lines from the two
     deliberately-failing rows
  E  POST /api/destinations/test: ok for hs, not ok for hsx, 404 unknown
  F  ABRP logical error: the mock answers {"status":"error"} on HTTP 200
     -> the row fails (last_error abrp: ...), recovers when cleared
  G  server down: receiver killed -> fail counts grow, backoff engages
     (>= 3 consecutive, backoff_s >= 10, attempts spaced out); receiver
     back -> success resumes within 60 s, backoff cleared
  H  broker down: mosquitto stopped -> `mq` skipped_offline grows with NO
     fail/backoff, mqtt:false; started -> messages resume
  I  WiFi down (needs --ap-if: the Pi's client on the DUT's own AP):
     hotspot down -> observed via the AP path: network:false, skips grow,
     fails do not; hotspot up -> deliveries resume within 180 s
  J  restore everything (settings + one planned reboot, cert sets
     deleted, receiver stopped, mosquitto + hotspot up)

  python data_destinations_bench.py --dut 10.42.1.194 --pi rpi001 --pi-ip 10.42.1.1 \
      [--ap-if wtest1 --ap-ip 192.168.0.10] [--observe 45] [--skip-wifi]
      [--abrp-token <ABRP user token> --abrp-key <Iternio api_key>]
  The last pair adds a LIVE api.iternio.com row (in the header-variant
  mock's slot) and asserts Iternio's {"status":"ok"} on it; without
  credentials ABRP is proven against the MOCK endpoint only.
"""
import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request

fails = []
metrics = []
D0_SUBMIT = 0


def check(name, ok, detail=""):
    print(("PASS" if ok else "FAIL") + ": " + name + (f"  ({detail})" if detail else ""), flush=True)
    if not ok:
        fails.append(name)


def note(msg):
    print("  " + msg, flush=True)


# ---- DUT API (direct, and through the Pi's client radio on the DUT AP) --------

A = None  # args


def api(path, method="GET", body=None, timeout=10, retries=6, raw=None, base=None):
    data = raw.encode() if raw is not None else (json.dumps(body).encode() if body is not None else None)
    last = None
    for _ in range(retries):
        req = urllib.request.Request((base or f"http://{A.dut}") + path, data=data, method=method,
                                     headers={"Content-Type": "application/json" if raw is None else "application/x-pem-file"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                t = r.read().decode()
                return r.status, (json.loads(t) if t.strip().startswith(("{", "[")) else t)
        except urllib.error.HTTPError as e:
            t = e.read().decode()
            return e.code, (json.loads(t) if t.strip().startswith(("{", "[")) else t)
        except (urllib.error.URLError, OSError) as e:
            last = e
            time.sleep(2)
    raise last


def api_via_ap(path):
    """GET through the Pi's client interface on the DUT's own AP (for the
    WiFi-down leg, when the STA path is gone)."""
    rc, out = pi(f"curl -s -m 6 --interface {A.ap_if} http://{A.ap_ip}{path}")
    if rc != 0 or not out.strip().startswith(("{", "[")):
        return None
    return json.loads(out)


def pi(cmd, timeout=60):
    """ssh to the Pi; 255 = transport failure (the Windows OpenSSH cold-
    handshake flake, benchlib's SSH_RETRIES lesson) -> retried twice."""
    for attempt in range(3):
        r = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", A.pi, cmd],
                           capture_output=True, text=True, timeout=timeout)
        if r.returncode != 255:
            return r.returncode, (r.stdout + r.stderr).strip()
        time.sleep(2)
    return 255, (r.stdout + r.stderr).strip()


def wait_up(deadline_s=180):
    t0 = time.time()
    while time.time() - t0 < deadline_s:
        try:
            code, _ = api("/api/info", timeout=4, retries=1)
            if code == 200:
                return time.time() - t0
        except Exception:
            pass
        time.sleep(3)
    return None


def submit_and_wait(label):
    """Submit (the device reboots iff something changed) - and when nothing
    changed (a re-run after an aborted run left the same table applied)
    restart anyway: every leg's counters, `full_sent` and the log ring
    must start from a fresh boot."""
    boots0 = boot_count()
    code, r = api("/api/settings/submit", "POST", {})
    changed = isinstance(r, dict) and bool(r.get("reboot"))
    if not changed:
        note(f"{label}: submit changed nothing - restarting for a fresh state")
        api("/api/restart", "POST", {})
    # "back" = the boot counter moved: a probe that lands BEFORE the reboot
    # (the device flushes the response first) must not count as back
    t0 = time.time()
    up = None
    while time.time() - t0 < 180:
        time.sleep(3)
        try:
            b = boot_count()
        except Exception:
            continue
        if b is not None and b != boots0:
            up = time.time() - t0
            break
    note(f"{label}: DUT back after {up:.0f} s (boot {boots0} -> {b})" if up else f"{label}: DUT did NOT come back")
    if up is None:
        raise SystemExit("DUT lost after submit")
    time.sleep(5)


def boot_count():
    code, h = api("/api/restart/history", timeout=6, retries=1)
    return h.get("boot_count") if code == 200 and isinstance(h, dict) else None


def clean_doc(d):
    return {k: v for k, v in d.items() if k not in ("degraded", "pending_reboot")}


def dest_status():
    code, st = api("/api/destinations")
    if code != 200 or not isinstance(st, dict):
        return None
    return {d["name"]: d for d in st.get("destinations", [])} | {"__top": st}


# ---- receiver on the Pi --------------------------------------------------------------

def receiver_start():
    here = os.path.dirname(os.path.abspath(__file__))
    src = os.path.join(here, "..", "actors", "dd_receiver.py")
    subprocess.run(["scp", "-q", src, f"{A.pi}:/tmp/dd_receiver.py"], check=True, timeout=30)
    # a transient systemd unit: a plain nohup child dies with the ssh session.
    # No `pkill -f dd_receiver` here: it matches the remote shell running THIS
    # command line (the path is in it) and kills the ssh session (rc 255).
    pi("sudo -n systemctl stop dd_receiver 2>/dev/null; "
       "sudo -n systemd-run --unit=dd_receiver --collect -p User=meatpi -p WorkingDirectory=/home/meatpi "
       "python3 -u /tmp/dd_receiver.py --tls-dir /home/meatpi/wican-tls")
    for _ in range(10):
        if dump(0) is not None:
            return True
        time.sleep(1)
    rc, out = pi("systemctl is-active dd_receiver; sudo -n journalctl -u dd_receiver --no-pager -n 3")
    note("receiver not reachable from here: " + " | ".join(out.splitlines())[:300])
    return False


def receiver_stop():
    pi("sudo -n systemctl stop dd_receiver 2>/dev/null; true")


def dump(since=0):
    try:
        with urllib.request.urlopen(f"http://{A.pi_ctl}:8199/__dump?since={since}", timeout=6) as r:
            return json.loads(r.read().decode())
    except Exception:
        return None


def mqtt_dump(since=0):
    try:
        with urllib.request.urlopen(f"http://{A.pi_ctl}:8199/__mqtt?since={since}", timeout=6) as r:
            return json.loads(r.read().decode())
    except Exception:
        return None


def control(**kw):
    req = urllib.request.Request(f"http://{A.pi_ctl}:8199/__control", data=json.dumps(kw).encode(), method="POST")
    with urllib.request.urlopen(req, timeout=6) as r:
        return json.loads(r.read().decode())


def by_path(recs, path):
    return [r for r in (recs or []) if r["path"] == path or r["path"].startswith(path + "?")]


# ---- log ring: E lines with the documented whitelist -----------------------------

IDF_CLIENT_TAGS = ("esp-tls", "esp-tls-mbedtls", "HTTP_CLIENT", "transport_base", "esp_http_client",
                   "transport", "esp-x509-crt-bundle")
# tags whose E lines are NOT this feature's (pass --ignore-tag to extend):
# the bench DUT's SD card reports `disk I/O error` from data_logger on every
# boot (fault logger_storage_error, pre-existing, 2026-09-19)
IGNORE_TAGS = ["data_logger"]


def ring():
    try:
        code, t = api("/api/logs/ring", timeout=15)
        return t if code == 200 and isinstance(t, str) else ""
    except Exception:
        return ""


def new_e_lines(prev, now):
    """E lines in `now` that were not in `prev` (the ring is chronological)."""
    prev_lines = prev.splitlines()
    now_lines = now.splitlines()
    start = 0
    if prev_lines:
        tail = prev_lines[-1]
        for i in range(len(now_lines) - 1, -1, -1):
            if now_lines[i] == tail:
                start = i + 1
                break
    out = []
    for ln in now_lines[start:]:
        if ln.startswith("E (") or ln.startswith("\x1b[0;31mE ("):
            out.append(ln.replace("\x1b[0;31m", "").replace("\x1b[0m", ""))
    return out


def classify_e(lines):
    """(whitelisted, offending)."""
    ok, bad = [], []
    for ln in lines:
        tag = ln.split(") ", 1)[1].split(":", 1)[0].strip() if ") " in ln else ""
        if tag in IGNORE_TAGS:
            continue
        (ok if tag in IDF_CLIENT_TAGS else bad).append(ln)
    return ok, bad


# ---- the destinations table ---------------------------------------------------------

def bench_table(pi_ip, abrp_token="", abrp_key=""):
    base = lambda **kw: {"enabled": True, "period_s": 3, "auth": "none", "auth_token": "", "auth_name": "",
                         "basic_username": "", "basic_password": "", "api_key": "", "query": "", "cert_set": "",
                         "car_model": "", "retain": True, "full_first": True} | kw
    return [
        base(name="mq", type="mqtt", url="~/dd"),
        base(name="h", type="http", url=f"http://{pi_ip}:8199/h", auth="basic", basic_username="bench",
             basic_password="pw-1", query="src=bench&leg=h"),
        base(name="hs", type="https", url=f"https://{pi_ip}:8443/hs", cert_set="ddbench", auth="bearer",
             auth_token="bench-bearer-1"),
        base(name="hsx", type="https", url=f"https://{pi_ip}:8443/hsx", full_first=False),
        base(name="mt", type="https", url=f"https://{pi_ip}:8444/mt", cert_set="ddmtls", full_first=False),
        base(name="mtx", type="https", url=f"https://{pi_ip}:8444/mtx", cert_set="ddbench", full_first=False),
        base(name="abrp", type="abrp", url=f"https://{pi_ip}:8445/1/tlm/send", cert_set="ddbench",
             auth="api_key_query", auth_token="user-token-1", api_key="key-1", car_model="bench:car"),
        base(name="abrph", type="abrp", url=f"https://{pi_ip}:8445/1/tlm/send", cert_set="ddbench",
             auth="api_key_header", auth_token="user-token-2", api_key="key-2"),
    ] if not abrp_token else [
        # the LIVE api.iternio.com row takes the header-variant mock's slot
        # (the table holds 8): real user token + Iternio api_key, the
        # built-in CA bundle, the default endpoint (url empty)
        base(name="abrplive", type="abrp", url="", auth="api_key_query", auth_token=abrp_token,
             api_key=abrp_key, car_model="", period_s=5),
    ]


# ---- legs ---------------------------------------------------------------------------

def leg_a():
    print("== A preflight")
    code, info = api("/api/info")
    check("A: DUT answers /api/info", code == 200 and isinstance(info, dict), str(code))
    code, st = api("/api/destinations")
    check("A: firmware has /api/destinations", code == 200 and isinstance(st, dict) and "destinations" in st, str(code))
    code, caps = api("/api/status")
    caps = (caps.get("health", {}) or {}).get("caps", {}) if isinstance(caps, dict) else {}
    ok = all(caps.get(k, {}).get("cap", 0) - caps.get(k, {}).get("used", 0) >= 2 for k in ("settings", "cmdline"))
    check("A: settings + cmdline registries keep >= 2 headroom", ok, json.dumps({k: caps.get(k) for k in ("settings", "cmdline")}))
    return info.get("device_id", "")


def leg_b():
    print("== B cert_manager")
    rc, ca = pi("cat ~/wican-tls/ca.pem")
    rc2, crt = pi("cat ~/wican-tls/client.crt")
    rc3, key = pi("cat ~/wican-tls/client.key")
    check("B: bench CA + client pair readable on the Pi", rc == 0 and rc2 == 0 and rc3 == 0 and "BEGIN CERTIFICATE" in ca)
    for name, parts in (("ddbench", {"ca": ca}), ("ddmtls", {"ca": ca, "cert": crt, "key": key})):
        for t, pem in parts.items():
            code, r = api(f"/api/certs/upload?set={name}&type={t}", "POST", raw=pem, timeout=20)
            check(f"B: upload {name}/{t}", code in (200, 201), f"{code} {r}")
    code, lst = api("/api/certs")
    sets = {s["name"]: s for s in (lst.get("sets", []) if isinstance(lst, dict) else [])}
    check("B: ddbench listed with a CA", sets.get("ddbench", {}).get("ca") is True, json.dumps(sets.get("ddbench")))
    check("B: ddmtls listed with CA + client cert + key",
          sets.get("ddmtls", {}).get("ca") is True and sets["ddmtls"].get("cert") and sets["ddmtls"].get("key"), json.dumps(sets.get("ddmtls")))


def leg_c(orig_dd, orig_mq):
    print("== C configure + submit")
    dd = dict(orig_dd)
    dd["enabled"] = True
    dd["destinations"] = bench_table(A.pi_ip, A.abrp_token, A.abrp_key)
    code, r = api("/api/settings/data_destinations", "PUT", dd)
    check("C: PUT data_destinations accepted", code == 200, f"{code} {r}")
    code, back = api("/api/settings/data_destinations")
    names = [d["name"] for d in back.get("destinations", [])] if isinstance(back, dict) else []
    check("C: 8 rows staged", len(names) == 8, ",".join(names))
    secret = next((d for d in back.get("destinations", []) if d["name"] == "hs"), {})
    check("C: secrets redacted on GET (hs.auth_token == '')", secret.get("auth_token") == "" and secret.get("url", "").endswith("/hs"))
    # a second PUT of the redacted document must keep the stored secrets (name-matched rows)
    code, r = api("/api/settings/data_destinations", "PUT", clean_doc(back))
    check("C: PUT of the redacted GET body is accepted (secrets kept by name)", code == 200, f"{code} {r}")
    # validation: an enabled https row with an http:// URL is refused
    bad = dict(dd)
    bad["destinations"] = dd["destinations"][:-1] + [{"name": "bad", "type": "https", "url": "http://x/y"}]
    code, r = api("/api/settings/data_destinations", "PUT", bad)
    check("C: https row with http:// URL rejected with the reason", code == 400 and "http://" in json.dumps(r), f"{code} {r}")
    mq = dict(orig_mq)
    mq.update({"enabled": True, "url": f"mqtt://{A.pi_ip}:1883", "cert_set": "", "ca_file": ""})
    code, r = api("/api/settings/mqtt_manager", "PUT", mq)
    check("C: PUT mqtt_manager accepted", code == 200, f"{code} {r}")
    # the receiver index BEFORE the reboot: the FIRST push after boot (the
    # full one) lands while the bench is still waiting for the DUT
    global D0_SUBMIT
    D0_SUBMIT = len(dump(0) or [])
    submit_and_wait("C")


def leg_d(device_id, observe):
    print(f"== D steady state ({observe} s)")
    ring0 = ring()
    d0 = len(dump(0) or [])
    m0 = len(mqtt_dump(0) or [])
    time.sleep(observe)
    recs = dump(d0) or []
    since_boot = dump(D0_SUBMIT) or recs
    mq = mqtt_dump(m0) or []
    st = dest_status() or {}
    ring1 = ring()
    # the poster walks the 8 rows sequentially (two of them fail their TLS
    # handshake on purpose): a lap runs ~5-8 s, so a 3 s period delivers
    # about once per lap
    exp_min = max(2, observe // 9)
    # h: basic auth + extra query + first push full, later data only
    h = by_path(recs, "/h")
    check(f"D: h received >= {exp_min} posts", len(h) >= exp_min, str(len(h)))
    h_boot = by_path(since_boot, "/h")
    if h_boot:
        b0 = json.loads(h_boot[0]["body"]) if h_boot[0]["body"].startswith("{") else {}
        check("D: h first push after boot carries config + status + autopid_data",
              all(k in b0 for k in ("config", "status", "autopid_data")) and b0["status"].get("device_id") == device_id,
              ",".join(sorted(b0.keys())))
    if h:
        later = [json.loads(r["body"]) for r in h if r["body"].startswith("{") and "config" not in r["body"][:12]]
        check("D: h later pushes are autopid_data only", later and all(set(b.keys()) == {"autopid_data"} for b in later),
              ",".join(sorted(later[0].keys())) if later else "none")
        check("D: h carries Basic auth + the extra query",
              h[0]["headers"].get("authorization", "").startswith("Basic YmVuY2g6cHctMQ==")
              and h[0]["query"].get("src") == "bench" and h[0]["query"].get("leg") == "h", json.dumps({"auth": h[0]["headers"].get("authorization"), "q": h[0]["query"]}))
        check("D: h payload has a timestamp",
              isinstance(b0.get("autopid_data", {}).get("timestamp"), (int, float)))
    # hs: TLS through the cert set + bearer
    hs = by_path(recs, "/hs")
    check(f"D: hs (https, cert_set ddbench) received >= {exp_min}", len(hs) >= exp_min, str(len(hs)))
    if hs:
        check("D: hs carries Bearer auth", hs[0]["headers"].get("authorization") == "Bearer bench-bearer-1",
              hs[0]["headers"].get("authorization", ""))
    # hsx: the built-in bundle must refuse the bench CA
    check("D: hsx (https, NO cert set) never delivered", len(by_path(recs, "/hsx")) == 0, str(len(by_path(recs, "/hsx"))))
    check("D: hsx counters: fail > 0, success == 0, TLS error recorded",
          st.get("hsx", {}).get("fail", 0) > 0 and st.get("hsx", {}).get("success", 1) == 0 and st["hsx"].get("last_error", "") != "",
          json.dumps({k: st.get("hsx", {}).get(k) for k in ("success", "fail", "last_error")}))
    # mt: mutual TLS
    mt = by_path(recs, "/mt")
    check(f"D: mt (mutual TLS, cert_set ddmtls) received >= {exp_min}", len(mt) >= exp_min, str(len(mt)))
    if mt:
        check("D: mt server saw the client certificate", mt[0]["peer_cn"] != "", mt[0]["peer_cn"])
    check("D: mtx (CA only against the mTLS port) never delivered, fails counted",
          len(by_path(recs, "/mtx")) == 0 and st.get("mtx", {}).get("fail", 0) > 0 and st["mtx"].get("success", 1) == 0,
          json.dumps({k: st.get("mtx", {}).get(k) for k in ("success", "fail", "last_error")}))
    # abrp: form + api_key query + tlm contents
    ab = [r for r in by_path(recs, "/1/tlm/send") if r.get("abrp", {}).get("token") == "user-token-1"]
    abh = [r for r in by_path(recs, "/1/tlm/send") if r.get("abrp", {}).get("token") == "user-token-2"]
    check(f"D: abrp received >= {exp_min}", len(ab) >= exp_min, str(len(ab)))
    if ab:
        a = ab[0]["abrp"]
        check("D: abrp api_key in the query + form body", a["api_key"] == "key-1" and ab[0]["query"].get("api_key") == "key-1"
              and ab[0]["headers"].get("content-type", "").startswith("application/x-www-form-urlencoded"), json.dumps(ab[0]["query"]))
        tlm = a.get("tlm") or {}
        check("D: abrp tlm has utc + car_model (+ mapped values)", isinstance(tlm.get("utc"), (int, float)) and tlm.get("car_model") == "bench:car",
              json.dumps(tlm)[:200])
    if A.abrp_token:
        live = st.get("abrplive", {})
        check("D: LIVE api.iternio.com accepted the telemetry (HTTP 200 + status ok, no fail)",
              live.get("success", 0) >= 2 and live.get("fail", 0) == 0 and live.get("last_status") == 200,
              json.dumps({k: live.get(k) for k in ("success", "fail", "last_status", "last_error")}))
    else:
        check(f"D: abrph received >= {exp_min}", len(abh) >= exp_min, str(len(abh)))
        if abh:
            check("D: abrph api_key as Authorization: APIKEY header", abh[0]["abrp"]["auth_header"] == "APIKEY key-2"
                  and "api_key" not in abh[0]["query"], abh[0]["abrp"]["auth_header"])
    check("D: abrp rows count success, no fail", st.get("abrp", {}).get("success", 0) >= exp_min and st.get("abrp", {}).get("fail", 0) == 0
          and st.get("abrph", {}).get("fail", 0) == 0, json.dumps({k: (st.get(k, {}).get("success"), st.get(k, {}).get("fail")) for k in ("abrp", "abrph")}))
    # mqtt
    topic = f"wican/{device_id}/dd"
    mine = [m for m in mq if m["topic"] == topic]
    check(f"D: MQTT {topic} received >= {exp_min}", len(mine) >= exp_min, str(len(mine)))
    if mine:
        p = json.loads(mine[-1]["payload"]) if mine[-1]["payload"].startswith("{") else {}
        check("D: MQTT payload is the flat snapshot with a timestamp", isinstance(p.get("timestamp"), (int, float)), mine[-1]["payload"][:120])
    rc, out = pi(f"timeout 4 mosquitto_sub -h localhost -t '{topic}' -C 1 -v 2>&1")
    check("D: MQTT retained snapshot readable by a new subscriber", rc == 0 and topic in out and '"timestamp"' in out, out[:120])
    # counters vs receiver
    for name, path in (("h", "/h"), ("hs", "/hs"), ("mt", "/mt")):
        s = st.get(name, {})
        n = len(by_path(recs, path))
        check(f"D: {name} counters consistent (success {s.get('success')} >= received {n}, fail 0, backoff 0)",
              s.get("success", 0) >= n and s.get("fail", 0) == 0 and s.get("backoff_s", 0) == 0 and s.get("consecutive_failures", 0) == 0)
    check("D: status reports network + mqtt up", st.get("__top", {}).get("network") is True and st["__top"].get("mqtt") is True)
    check("D: h full_sent flag set", st.get("h", {}).get("full_sent") is True)
    # E lines
    e = new_e_lines(ring0, ring1)
    wl, bad = classify_e(e)
    check("D: no E lines beyond the IDF client's TLS/connect lines (hsx/mtx)", not bad, "\n      ".join(bad[:5]))
    note(f"D: {len(wl)} whitelisted IDF-client E lines from the two failing TLS rows")
    metrics.append(("steady_posts_h", len(h)))


def leg_e():
    print("== E test endpoint")
    code, r = api("/api/destinations/test", "POST", {"name": "hs"}, timeout=30)
    check("E: test hs -> ok 2xx", code == 200 and r.get("ok") is True and 200 <= r.get("status", 0) < 300, f"{code} {r}")
    code, r = api("/api/destinations/test", "POST", {"name": "hsx"}, timeout=30)
    check("E: test hsx -> ok false with a TLS/transport error", code == 200 and r.get("ok") is False and r.get("error"), f"{code} {r}")
    code, r = api("/api/destinations/test", "POST", {"name": "nope"}, timeout=30)
    check("E: test unknown -> 404", code == 404, f"{code} {r}")
    code, r = api("/api/destinations/test", "POST", {"name": "mq"}, timeout=30)
    check("E: test mq -> ok", code == 200 and r.get("ok") is True, f"{code} {r}")


def leg_f():
    print("== F ABRP logical error (HTTP 200 + status error)")
    control(abrp_reject=True)
    st0 = dest_status()
    time.sleep(14)
    st1 = dest_status()
    control(abrp_reject=False)
    grew = st1["abrp"]["fail"] > st0["abrp"]["fail"]
    check("F: abrp fails counted on status!=ok", grew and st1["abrp"]["last_error"].startswith("abrp:"),
          json.dumps({k: st1["abrp"].get(k) for k in ("fail", "last_error", "last_status")}))
    check("F: abrp HTTP status was 200 while failing", st1["abrp"].get("last_status") == 200, str(st1["abrp"].get("last_status")))
    time.sleep(25)
    st2 = dest_status()
    check("F: abrp recovers after the mock is cleared", st2["abrp"]["success"] > st1["abrp"]["success"] and st2["abrp"]["consecutive_failures"] == 0,
          json.dumps({k: st2["abrp"].get(k) for k in ("success", "consecutive_failures", "backoff_s")}))


def leg_g():
    print("== G server down / back (backoff)")
    st0 = dest_status()
    ring0 = ring()
    receiver_stop()
    t0 = time.time()
    time.sleep(45)
    st1 = dest_status()
    h0, h1 = st0["h"], st1["h"]
    dfail = h1["fail"] - h0["fail"]
    check("G: h fails counted while the server is down", dfail >= 3, str(dfail))
    check("G: backoff engaged (>= 3 consecutive, backoff_s >= 10)", h1["consecutive_failures"] >= 3 and h1["backoff_s"] >= 10,
          json.dumps({k: h1.get(k) for k in ("consecutive_failures", "backoff_s", "last_error")}))
    # without backoff a 3 s period gives ~15 attempts in 45 s; the ladder
    # (3 at 3 s, then 10, 20 ...) keeps it around 5-7
    check("G: attempts spaced out by the backoff (<= 8 fails in 45 s)", dfail <= 8, str(dfail))
    check("G: last_error names the transport failure", h1["last_error"].startswith("esp_err=") or h1["last_error"].startswith("http="), h1["last_error"])
    ok = receiver_start()
    check("G: receiver back", ok)
    d0 = 0
    t1 = time.time()
    recovered = False
    while time.time() - t1 < 90:
        time.sleep(5)
        st2 = dest_status()
        if st2 and st2["h"]["consecutive_failures"] == 0 and st2["h"]["success"] > h1["success"]:
            recovered = True
            break
    check("G: h delivering again within 90 s, backoff cleared", recovered and st2["h"]["backoff_s"] == 0,
          json.dumps({k: (st2 or {}).get("h", {}).get(k) for k in ("success", "consecutive_failures", "backoff_s")}))
    metrics.append(("recover_after_server_back_s", round(time.time() - t1)))
    e = new_e_lines(ring0, ring())
    wl, bad = classify_e(e)
    check("G: no E lines beyond the IDF client's connect lines", not bad, "\n      ".join(bad[:5]))


def leg_h(device_id):
    print("== H broker down / back")
    st0 = dest_status()
    pi("sudo -n systemctl stop mosquitto")
    time.sleep(25)
    st1 = dest_status()
    mq0, mq1 = st0["mq"], st1["mq"]
    check("H: mq skipped_offline grows while the broker is down", mq1["skipped_offline"] > mq0["skipped_offline"],
          f"{mq0['skipped_offline']} -> {mq1['skipped_offline']}")
    check("H: broker outage is NOT a failure (fail unchanged, no backoff)", mq1["fail"] <= mq0["fail"] + 1 and mq1["backoff_s"] == 0,
          json.dumps({k: mq1.get(k) for k in ("fail", "backoff_s", "consecutive_failures")}))
    check("H: status shows mqtt disconnected", st1["__top"]["mqtt"] is False)
    pi("sudo -n systemctl start mosquitto")
    # the receiver's subscriber reconnects by itself; count from now
    m0 = len(mqtt_dump(0) or [])
    t1 = time.time()
    got = 0
    while time.time() - t1 < 90:
        time.sleep(5)
        got = len([m for m in (mqtt_dump(m0) or []) if m["topic"] == f"wican/{device_id}/dd"])
        if got >= 2:
            break
    check("H: MQTT deliveries resume after the broker is back (within 90 s)", got >= 2, f"{got} in {time.time() - t1:.0f} s")
    st2 = dest_status()
    check("H: mq success grows again", st2["mq"]["success"] > st1["mq"]["success"])


def leg_i(device_id):
    print("== I WiFi down / back")
    if not A.ap_if:
        note("I: skipped (no --ap-if); the STA path is the only one to the DUT")
        return
    # the Pi's "phone" profile on the DUT's own AP (re-joined after every DUT
    # reboot: NetworkManager does not re-associate by itself)
    pi(f"sudo -n nmcli dev wifi rescan ifname {A.ap_if} 2>/dev/null; sleep 3; sudo -n nmcli con up {A.ap_con} 2>&1 | tail -1")
    time.sleep(4)
    via_ap = api_via_ap("/api/destinations")
    check("I: DUT reachable through its own AP before the leg", via_ap is not None)
    if via_ap is None:
        return
    st0 = {d["name"]: d for d in via_ap["destinations"]}
    d0 = len(dump(0) or [])
    pi("sudo -n nmcli con down wican-bench")
    t_down = time.time()
    time.sleep(40)
    via = api_via_ap("/api/destinations")
    check("I: DUT still answers on its AP while the hotspot is gone", via is not None)
    if via:
        st1 = {d["name"]: d for d in via["destinations"]}
        check("I: network reported down", via["network"] is False)
        check("I: h skipped_offline grows, fails do not pile up (<= 2)",
              st1["h"]["skipped_offline"] > st0["h"]["skipped_offline"] and st1["h"]["fail"] - st0["h"]["fail"] <= 2,
              json.dumps({k: (st0["h"].get(k), st1["h"].get(k)) for k in ("skipped_offline", "fail", "backoff_s")}))
        check("I: no backoff from the outage", st1["h"]["backoff_s"] == 0 and st1["hs"]["backoff_s"] == 0)
    recs_during = [r for r in (dump(d0) or []) if r["t"] > t_down + 10]
    check("I: nothing reached the receiver while the WiFi was down", len(recs_during) == 0, str(len(recs_during)))
    pi("sudo -n nmcli con up wican-bench")
    t_up = time.time()
    # the DUT has the dongle AP as a fallback: after the hotspot vanished it
    # sits there and comes back on the roam-to-preferred interval; the rig's
    # assoc-status stall (reason 204, brief §7) can deprioritise the hotspot
    # for 300 s - keep the free radio rescanning (the documented nudge) and
    # allow that window
    up = None
    while time.time() - t_up < 330:
        pi(f"sudo -n nmcli dev wifi rescan ifname {A.ap_if} >/dev/null 2>&1; true", timeout=20)
        try:
            code, _ = api("/api/info", timeout=4, retries=1)
            if code == 200:
                up = time.time() - t_up
                break
        except Exception:
            pass
        time.sleep(5)
    check("I: DUT back on the hotspot within 330 s", up is not None, f"{up:.0f} s" if up else "no")
    metrics.append(("rejoin_after_hotspot_up_s", round(up) if up else -1))
    d1 = len(dump(0) or [])
    got = 0
    t2 = time.time()
    while time.time() - t2 < 60:
        time.sleep(5)
        got = len(by_path(dump(d1) or [], "/h"))
        if got >= 2:
            break
    check("I: deliveries resume after the reconnect (>= 2 within 60 s)", got >= 2, f"{got}")
    st2 = dest_status()
    check("I: counters after the round trip: h consecutive 0, backoff 0", st2 and st2["h"]["consecutive_failures"] == 0 and st2["h"]["backoff_s"] == 0)


def leg_j(orig_dd, orig_mq):
    print("== J restore")
    control(abrp_reject=False)
    code, _ = api("/api/settings/data_destinations", "PUT", clean_doc(orig_dd))
    check("J: data_destinations restored", code == 200, str(code))
    code, _ = api("/api/settings/mqtt_manager", "PUT", clean_doc(orig_mq))
    check("J: mqtt_manager restored", code == 200, str(code))
    submit_and_wait("J")
    for name in ("ddbench", "ddmtls"):
        code, _ = api(f"/api/certs?set={name}", "DELETE")
        check(f"J: cert set {name} deleted", code in (200, 204), str(code))
    receiver_stop()
    # `nmcli con up` on an ACTIVE hotspot re-activates it and drops the DUT's
    # station link (run 6 lost the final check to that): only when it is down
    pi("sudo -n systemctl start mosquitto; sudo -n nmcli -t -f NAME con show --active | grep -qx wican-bench "
       "|| sudo -n nmcli con up wican-bench >/dev/null 2>&1; true")
    code, st = api("/api/destinations")
    check("J: device runs the original table again", code == 200 and len(st.get("destinations", [])) == len(orig_dd.get("destinations", [])))


def main():
    global A
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dut", required=True, help="DUT address on the bench hotspot (e.g. 10.42.1.194)")
    ap.add_argument("--pi", default="rpi001", help="ssh host running the receivers + mosquitto")
    ap.add_argument("--pi-ip", default="10.42.1.1", help="the Pi as the DUT sees it (hotspot gateway)")
    ap.add_argument("--pi-ctl", default="192.168.90.2", help="the Pi as this PC reaches it (dump port 8199)")
    ap.add_argument("--ap-if", default="", help="Pi interface joined to the DUT's own AP (WiFi-down leg)")
    ap.add_argument("--ap-ip", default="192.168.0.10", help="the DUT's AP address")
    ap.add_argument("--ap-con", default="wican-bench-ap", help="the Pi's NM profile joining the DUT's AP")
    ap.add_argument("--observe", type=int, default=45)
    ap.add_argument("--skip-wifi", action="store_true")
    ap.add_argument("--skip-broker", action="store_true")
    ap.add_argument("--ignore-tag", action="append", default=[], help="E-line tags to ignore (unrelated known faults)")
    ap.add_argument("--abrp-token", default="", help="LIVE leg: your ABRP user token (ABRP app: Settings > Live data > Generic)")
    ap.add_argument("--abrp-key", default="", help="LIVE leg: the Iternio developer api_key (with --abrp-token)")
    ap.add_argument("--originals", help="JSON {data_destinations, mqtt_manager} to restore INSTEAD of what the DUT "
                                        "reports now (re-run after an aborted run left the bench table applied)")
    A = ap.parse_args()
    IGNORE_TAGS.extend(A.ignore_tag)
    if A.skip_wifi:
        A.ap_if = ""
    t0 = time.time()
    device_id = leg_a()
    code, orig_dd = api("/api/settings/data_destinations")
    code2, orig_mq = api("/api/settings/mqtt_manager")
    if code != 200 or code2 != 200:
        raise SystemExit("cannot read the original settings")
    orig_dd, orig_mq = clean_doc(orig_dd), clean_doc(orig_mq)
    if A.originals:
        saved = json.load(open(A.originals, encoding="utf-8"))
        orig_dd, orig_mq = saved["data_destinations"], saved["mqtt_manager"]
    keep = os.path.join(os.environ.get("TEMP", "."), "dd_bench_originals.json")
    json.dump({"data_destinations": orig_dd, "mqtt_manager": orig_mq}, open(keep, "w", encoding="utf-8"), indent=1)
    note(f"originals: {len(orig_dd.get('destinations', []))} destinations, mqtt enabled={orig_mq.get('enabled')} (saved to {keep})")
    check("A: receiver up on the Pi", receiver_start())
    try:
        leg_b()
        leg_c(orig_dd, orig_mq)
        leg_d(device_id, A.observe)
        leg_e()
        leg_f()
        leg_g()
        if not A.skip_broker:
            leg_h(device_id)
        leg_i(device_id)
    finally:
        try:
            leg_j(orig_dd, orig_mq)
        except Exception as e:
            check("J: restore completed", False, str(e))
    print("\n== metrics")
    for k, v in metrics:
        print(f"METRIC {k} = {v}")
    print(f"\n{len(fails)} failures in {time.time() - t0:.0f} s")
    for f in fails:
        print("  - " + f)
    print("DATA DEST " + ("PASS" if not fails else "FAIL"))
    sys.exit(0 if not fails else 1)


if __name__ == "__main__":
    main()
