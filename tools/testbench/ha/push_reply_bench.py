#!/usr/bin/env python3
"""Settings in the reply to a push: taken, left alone or refused, on the device.

The claim: Home Assistant can hand its webhook settings (the addresses and the
post interval) to the device in its reply to the device's own push, for a car
it cannot reach to register them (asleep at that moment, or on LTE without a
VPN). Each set of settings has a short revision; the device stores it with the
settings and reports it in every push (`status.webhook_rev`), Home Assistant
repeats its settings until the device reports the revision it wants. The
device takes settings only from where a registration would be accepted, and it
never reports a revision for settings that were changed since.

The receiver is this bench's own stand-in for Home Assistant: a server on the
Pi, plain http and https (a certificate made for the run that names a host
only, signed by a CA made for the run), that answers every push the way the
leg asks and notes the revision each push reports (never the payload). The Pi
also takes a public address for the run (TEST-NET-3, on its loopback: the DUT
reaches it through its gateway, the Pi) and publishes a name for it over mDNS,
so a push can go to a public address by IP and by name without leaving the
bench.

Legs:
  t0  as found: the webhook's settings and registration, the certificate sets
  t1  the Pi: the CA, the receiver, the public address, the name; the CA
      uploaded as the set `pushreply`
  a1  registered without a revision at http://<pi>: every push carries
      `status.webhook_rev`, empty
  a2  a 204: nothing changes
  a3  a 200 with a valid `webhook` object: taken, used from the next cycle,
      the next pushes report the new revision; the diff goes on (no snapshot)
  a4  the same reply again: nothing changes, no flash write
  a5  a 200 with no body, with `{}`: as a 204
  a6  not JSON, over 2048 bytes, interval 0, a scheme that is not http(s):
      ignored, settings and revision as they were, the pushes delivered
  a7  a 503 with a settings body: not read for settings, the push failed
  r1  the certificate set selected through the settings route and a restart:
      the settings taken from the reply and their revision survive both
  b1  plain http to a public address: registered WITH a revision (stored,
      reported); the reply's settings refused (untrusted)
  b2  https to a private address by IP (the name not compared): taken
  b3  https to a public address by IP: refused
  b4  https to a host name (at the public address): taken
  c1  a registration with a revision and a bad interval: 400, nothing changes
  c2  a registration without a revision: the device reports none
  c3  pinned on the device (manual_override): no `webhook_rev` in the pushes,
      a reply's settings refused; unpinned, the key is back
  c4  the interval changed through the settings route (the web interface's
      way) and a restart: the pushes report no revision; the reply's settings
      go in again
  u1  (--untrusted, the DUT's USB-C cabled to the PC) the station's network
      marked untrusted: plain http to a private address refused, https by
      name taken
  t9  everything as found (one restart): the settings, the registration, the
      certificate sets, the Pi

  python tools/testbench/ha/push_reply_bench.py --dut localhost:18087
      --pi rpi001 --pi-ip 10.42.1.1 [--port 8447] [--untrusted --usb-ip 192.168.82.1]

Prints `PUSH REPLY PASS`. Written 2026-11-14.
"""
import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "lib"))
import pcbench  # noqa: E402

SET = "pushreply"
WORK = "/tmp/wican_pushreply"
NAME = "wican-pushreply.local"
PUBLIC_IP = "203.0.113.9"          # TEST-NET-3: never routed on the internet
HOOK = "/api/webhook/wican-pushreply"

RECEIVER_SRC = r'''
import gzip
import http.server
import json
import ssl
import sys
import threading

work, port, sport = sys.argv[1], int(sys.argv[2]), int(sys.argv[3])
lock = threading.Lock()
count = 0


class H(http.server.BaseHTTPRequestHandler):
    def do_POST(self):
        global count
        raw = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        if self.headers.get("Content-Encoding") == "gzip":
            raw = gzip.decompress(raw)
        try:
            status = json.loads(raw).get("status", {})
        except ValueError:
            status = {}
        rev = status.get("webhook_rev")
        with lock:
            count += 1
            # never the payload: the count, the listener, the path, the revision
            # and how much status came with it
            note = {"n": count, "tls": self.server.tls, "path": self.path,
                    "has_rev": rev is not None, "rev": rev,
                    "status_keys": len(status)}
            with open(work + "/pushes.log", "a") as f:
                f.write(json.dumps(note) + "\n")
            try:
                with open(work + "/answer.json") as f:
                    answer = json.load(f)
            except (OSError, ValueError):
                answer = {}
        body = answer.get("body", "").encode()
        self.send_response(answer.get("status", 204))
        if body:
            self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


plain = http.server.ThreadingHTTPServer(("0.0.0.0", port), H)
plain.tls = False
tls = http.server.ThreadingHTTPServer(("0.0.0.0", sport), H)
tls.tls = True
ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
ctx.load_cert_chain(work + "/srv.pem", work + "/srv.key")
tls.socket = ctx.wrap_socket(tls.socket, server_side=True)
threading.Thread(target=tls.serve_forever, daemon=True).start()
print("READY", flush=True)
plain.serve_forever()
'''

# A CA and a server certificate that names the host only (no address in it).
CERT_CMDS = (
    f"rm -rf {WORK} && mkdir -p {WORK} && cd {WORK} && "
    "openssl req -x509 -newkey rsa:2048 -nodes -days 30 "
    "-subj '/CN=wican pushreply bench CA' -keyout ca.key -out ca.pem 2>/dev/null && "
    f"openssl req -newkey rsa:2048 -nodes -subj '/CN={NAME}' "
    "-keyout srv.key -out srv.csr 2>/dev/null && "
    f"printf 'subjectAltName=DNS:{NAME}\\nbasicConstraints=CA:FALSE\\n"
    "extendedKeyUsage=serverAuth\\n' > ext.cnf && "
    "openssl x509 -req -in srv.csr -CA ca.pem -CAkey ca.key -CAcreateserial "
    "-days 30 -extfile ext.cnf -out srv.pem 2>/dev/null && cat ca.pem"
)


class Pi:
    """The bench Pi over ssh: the stand-in for Home Assistant."""

    def __init__(self, host):
        self.ssh = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", host]
        self.procs = []

    def run(self, cmd, text_in=None, timeout=90):
        r = subprocess.run(self.ssh + [cmd], input=text_in, capture_output=True,
                           text=True, timeout=timeout)
        return r.returncode, r.stdout

    def _spawn(self, cmd, ready, what):
        p = subprocess.Popen(self.ssh + [cmd], stdout=subprocess.PIPE,
                             stderr=subprocess.STDOUT, text=True)
        self.procs.append(p)
        line = p.stdout.readline()
        if ready not in line:
            raise pcbench.Bench(f"{what} did not start on the Pi: "
                                f"{line.strip()[:160]}")

    def start(self, port):
        self.run("pkill -f '[w]ican_pushreply/recv.py' ; "
                 f"pkill -f '[a]vahi-publish -a -R {NAME}' ; true")
        code, _ = self.run(f"cat > {WORK}/recv.py", text_in=RECEIVER_SRC)
        if code != 0:
            raise pcbench.Bench("the receiver's source did not reach the Pi")
        self.answer(204)
        self._spawn(f"python3 -u {WORK}/recv.py {WORK} {port} {port + 1}",
                    "READY", f"the receiver (ports {port}, {port + 1} taken?)")
        code, out = self.run(f"sudo -n ip addr add {PUBLIC_IP}/32 dev lo 2>&1; "
                             f"ip -br addr show lo")
        if PUBLIC_IP not in out:
            raise pcbench.Bench(f"the Pi did not take {PUBLIC_IP}: {out[:160]}")
        self._spawn(f"stdbuf -oL -eL avahi-publish -a -R {NAME} {PUBLIC_IP}",
                    "Established",
                    "the mDNS name")

    def answer(self, status, body=""):
        """What the receiver answers from now on."""
        doc = json.dumps({"status": status, "body": body})
        code, _ = self.run(f"cat > {WORK}/answer.tmp && "
                           f"mv {WORK}/answer.tmp {WORK}/answer.json",
                           text_in=doc)
        if code != 0:
            raise pcbench.Bench("the answer did not reach the Pi")

    def pushes(self):
        code, out = self.run(f"cat {WORK}/pushes.log 2>/dev/null")
        notes = []
        for ln in out.splitlines():
            try:
                notes.append(json.loads(ln))
            except ValueError:
                pass
        return notes

    def stop(self):
        for p in self.procs:
            p.terminate()
        self.procs = []
        self.run("pkill -f '[w]ican_pushreply/recv.py' ; "
                 f"pkill -f '[a]vahi-publish -a -R {NAME}' ; "
                 f"sudo -n ip addr del {PUBLIC_IP}/32 dev lo 2>/dev/null ; "
                 f"rm -rf {WORK} ; true")

    def clean(self):
        code, out = self.run(
            f"ip -br addr show lo | grep -c {PUBLIC_IP} ; "
            f"pgrep -fc '[w]ican_pushreply/recv.py|[a]vahi-publish -a -R {NAME}' ; "
            f"test -e {WORK} && echo there || echo gone")
        return out.split() == ["0", "0", "gone"], " ".join(out.split())


def upload_ca(dut, pem):
    """The CA as a certificate set (a raw PEM body; applies at once)."""
    req = urllib.request.Request(
        f"{dut.base}/api/certs/upload?set={SET}&type=ca", data=pem.encode(),
        method="POST", headers={"Content-Type": "application/x-pem-file"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code
    except (urllib.error.URLError, OSError) as e:
        raise pcbench.Bench(f"certificate upload: {e}")


def set_names(dut):
    return sorted(s.get("name") for s in dut.get("/api/certs").get("sets", []))


def reply(rev, url, interval, urls=None, enabled=True, pad=0):
    """The body of a 200 with settings, as Home Assistant sends it."""
    wh = {"rev": rev, "url": url, "enabled": enabled, "interval": interval}
    if urls:
        wh["urls"] = urls
    doc = {"webhook": wh}
    if pad:
        doc["pad"] = "x" * pad
    return json.dumps(doc)


def wait_for(fn, timeout, step=1.5):
    t0 = time.time()
    while time.time() - t0 < timeout:
        v = fn()
        if v:
            return v
        time.sleep(step)
    return fn()


class Bench:
    """The legs' shared moves."""

    def __init__(self, run, dut, pi):
        self.run, self.dut, self.pi = run, dut, pi

    def hook(self):
        return self.dut.get("/api/webhook")

    def settle(self, n=2, timeout=45):
        """Wait for n more pushes at the receiver; the new ones."""
        n0 = len(self.pi.pushes())
        wait_for(lambda: len(self.pi.pushes()) >= n0 + n, timeout)
        return self.pi.pushes()[n0:]

    def register(self, url, interval=5, **more):
        body = {"url": url, "enabled": True, "interval": interval}
        body.update(more)
        return self.dut.api("/api/webhook", "POST", body)

    def refused(self, leg, what, body, word, before):
        """A reply the device must not take: the verdict's word, an ignored
        count that moved, settings and revision as before, pushes delivered."""
        self.pi.answer(200, body)
        self.settle(2)
        st = wait_for(lambda: (lambda s: s if s["reply"]["last"] == word
                               else None)(self.hook()), 25)
        st = st or self.hook()
        ok = (st["reply"]["last"] == word
              and st["reply"]["ignored"] > before["reply"]["ignored"]
              and st["reply"]["applied"] == before["reply"]["applied"]
              and st["rev"] == before["rev"]
              and st["interval"] == before["interval"]
              and st["url"] == before["url"]
              and st["fail_count"] == before["fail_count"]
              and st["success_count"] > before["success_count"])
        self.run.check(
            f"{leg} {what}: refused ({word}), settings and revision as they "
            "were, the pushes delivered", ok,
            f"last={st['reply']['last']!r} ignored "
            f"{before['reply']['ignored']}->{st['reply']['ignored']} applied "
            f"{before['reply']['applied']}->{st['reply']['applied']} rev="
            f"{st['rev']!r} interval={st['interval']} delivered "
            f"+{st['success_count'] - before['success_count']} failed "
            f"+{st['fail_count'] - before['fail_count']}")
        return st

    def taken(self, leg, what, body, rev, interval, before):
        """A reply the device must take: the settings in use, the new revision
        in the answer of GET and in the next pushes."""
        self.pi.answer(200, body)
        st = wait_for(lambda: (lambda s: s if s["rev"] == rev else None)(
            self.hook()), 45)
        st = st or self.hook()
        new = self.settle(2)
        ok = (st["rev"] == rev and st["interval"] == interval
              and st["reply"]["applied"] == before["reply"]["applied"] + 1
              and st["reply"]["last"] in ("applied", "same")
              and new and all(p["rev"] == rev for p in new[-2:]))
        self.run.check(
            f"{leg} {what}: taken, in use, the next pushes report the new "
            "revision", ok,
            f"rev={st['rev']!r} interval={st['interval']} applied "
            f"{before['reply']['applied']}->{st['reply']['applied']} last="
            f"{st['reply']['last']!r} pushes report "
            f"{[p['rev'] for p in new[-2:]]}")
        return st


def legs_basic(b, base):
    """a1..a7: plain http to the Pi's private address, a trusted network."""
    run, dut, pi = b.run, b.dut, b.pi
    url = base + "-a"

    pi.answer(204)
    found = b.hook()
    code, r = b.register(url)
    new = b.settle(3)
    run.check("a1 registered without a revision: every push carries "
              "status.webhook_rev, empty",
              code in (200, 201) and isinstance(r, dict) and r.get("rev") == ""
              and len(new) >= 3 and all(p["has_rev"] and p["rev"] == ""
                                        for p in new),
              f"http {code}, {len(new)} pushes, revs "
              f"{[p['rev'] if p['has_rev'] else None for p in new]}")
    resync_keys = new[0]["status_keys"] if new else 0

    st = b.hook()
    run.check("a2 a 204: nothing changes",
              st["rev"] == "" and st["interval"] == 5 and st["status"] == "ok"
              and st["reply"] == found["reply"],
              f"rev={st['rev']!r} interval={st['interval']} reply={st['reply']}")

    mode = dut.settings("ha_webhooks").get("data_mode")
    n0 = len(pi.pushes())
    st = b.taken("a3", "a 200 with a valid webhook object",
                 reply("a3a3a3a3", url, 4), "a3a3a3a3", 4, st)
    after = [p for p in pi.pushes()[n0:] if p["rev"] == "a3a3a3a3"]
    run.check("a3 the diff goes on after it (no full snapshot for the same "
              "Home Assistant)",
              mode != "changed" or (after and resync_keys
                                    and after[0]["status_keys"] < resync_keys),
              f"data_mode={mode}: {resync_keys} status keys in the "
              f"registration's snapshot, "
              f"{after[0]['status_keys'] if after else '?'} in the push "
              "after the reply")

    w0 = dut.get("/api/status")["health"]["flash"]["writes"]
    b.settle(3)
    st2 = b.hook()
    w1 = dut.get("/api/status")["health"]["flash"]["writes"]
    run.check("a4 the same reply again: nothing changes, no flash write",
              st2["reply"]["applied"] == st["reply"]["applied"]
              and st2["reply"]["last"] == "same" and st2["rev"] == "a3a3a3a3"
              and st2["reply"]["ignored"] == st["reply"]["ignored"]
              and w1 == w0,
              f"applied={st2['reply']['applied']} last={st2['reply']['last']!r} "
              f"flash writes {w0}->{w1}")

    pi.answer(200, "")
    b.settle(2)
    pi.answer(200, "{}")
    b.settle(2)
    st3 = b.hook()
    run.check("a5 a 200 with no body, with {}: as a 204",
              st3["reply"]["last"] == "same"
              and st3["reply"]["ignored"] == st["reply"]["ignored"]
              and st3["rev"] == "a3a3a3a3" and st3["status"] == "ok",
              f"last={st3['reply']['last']!r} ignored={st3['reply']['ignored']}")

    st = st3
    st = b.refused("a6", "a body that is not JSON", "OK", "not_json", st)
    st = b.refused("a6", "a body over 2048 bytes",
                   reply("a6a6a6a6", url, 9, pad=3000), "too_long", st)
    st = b.refused("a6", "interval 0", reply("a6a6a6a6", url, 0), "invalid", st)
    st = b.refused("a6", "an ftp:// address",
                   reply("a6a6a6a6", "ftp://10.42.1.1/x", 9), "invalid", st)

    pi.answer(503, reply("a7a7a7a7", url, 9))
    wait_for(lambda: b.hook()["fail_count"] > st["fail_count"], 30)
    st7 = b.hook()
    run.check("a7 a 503 with a settings body: not read for settings, the "
              "push failed as before",
              st7["fail_count"] > st["fail_count"] and st7["rev"] == "a3a3a3a3"
              and st7["interval"] == 4
              and st7["reply"]["applied"] == st["reply"]["applied"]
              and st7["reply"]["ignored"] == st["reply"]["ignored"],
              f"failed +{st7['fail_count'] - st['fail_count']} rev="
              f"{st7['rev']!r} interval={st7['interval']} last_error="
              f"{st7['last_error']!r}")
    pi.answer(204)
    wait_for(lambda: b.hook()["status"] == "ok", 30)


def leg_restart(b):
    """r1: the certificate set selected through the settings route."""
    run, dut, pi = b.run, b.dut, b.pi

    dut.restart({"ha_webhooks": {"cert_set": SET}})
    st = b.hook()
    new = b.settle(2)
    run.check("r1 a restart and a save of another setting through the "
              "settings route: the settings taken from the reply and their "
              "revision are still there",
              st["rev"] == "a3a3a3a3" and st["interval"] == 4
              and dut.settings("ha_webhooks").get("cert_set") == SET
              and new and all(p["rev"] == "a3a3a3a3" for p in new),
              f"rev={st['rev']!r} interval={st['interval']} pushes report "
              f"{[p['rev'] for p in new]}")


def legs_trust(b, args):
    """b1..b4: from whom settings are taken (a trusted network)."""
    run, pi = b.run, b.pi
    plain_pub = f"http://{PUBLIC_IP}:{args.port}{HOOK}-b1"
    tls_priv = f"https://{args.pi_ip}:{args.port + 1}{HOOK}-b2"
    tls_pub = f"https://{PUBLIC_IP}:{args.port + 1}{HOOK}-b3"
    tls_name = f"https://{NAME}:{args.port + 1}{HOOK}-b4"

    pi.answer(204)
    code, r = b.register(plain_pub, rev="b1b1b1b1")
    new = b.settle(2)
    st = b.hook()
    run.check("b1 registered with a revision: stored, in the answer, in the "
              "pushes (plain http to a public address delivers)",
              code in (200, 201) and isinstance(r, dict)
              and r.get("rev") == "b1b1b1b1" and st["rev"] == "b1b1b1b1"
              and new and all(p["rev"] == "b1b1b1b1" and not p["tls"]
                              for p in new) and st["status"] == "ok",
              f"http {code}, rev={st['rev']!r} status={st['status']!r} "
              f"last_error={st['last_error']!r}")
    b.refused("b1", "plain http to a public address",
              reply("b1x", plain_pub, 9), "untrusted", st)

    pi.answer(204)
    b.register(tls_priv, rev="b2b2b2b2")
    b.settle(2)
    st = b.hook()
    run.check("b2 https to the private address by IP delivers (the chain "
              "through the set, the name not compared)",
              st["status"] == "ok" and st["rev"] == "b2b2b2b2",
              f"status={st['status']!r} last_error={st['last_error']!r}")
    b.taken("b2", "https to a private address by IP",
            reply("b2x", tls_priv, 6), "b2x", 6, st)

    pi.answer(204)
    b.register(tls_pub, rev="b3b3b3b3")
    b.settle(2)
    st = b.hook()
    run.check("b3 https to the public address by IP delivers",
              st["status"] == "ok" and st["rev"] == "b3b3b3b3",
              f"status={st['status']!r} last_error={st['last_error']!r}")
    b.refused("b3", "https to a public address by IP",
              reply("b3x", tls_pub, 9), "untrusted", st)

    pi.answer(204)
    b.register(tls_name, rev="b4b4b4b4")
    b.settle(2)
    st = b.hook()
    run.check("b4 https to the host name delivers (the name resolved over "
              "mDNS to the public address, chain and name verified)",
              st["status"] == "ok" and st["rev"] == "b4b4b4b4",
              f"status={st['status']!r} last_error={st['last_error']!r}")
    b.taken("b4", "https to a host name, at a public address",
            reply("b4x", tls_name, 7), "b4x", 7, st)


def legs_routes(b, base):
    """c1..c4: the registration's rules and the other routes."""
    run, dut, pi = b.run, b.dut, b.pi
    url = base + "-c"

    pi.answer(204)
    code, r = b.register(url, rev="c0c0c0c0")
    st = b.hook()
    code2, r2 = b.register(url, interval=0, rev="c1c1c1c1")
    st2 = b.hook()
    run.check("c1 a registration with a revision and interval 0: 400 "
              "interval, nothing changes",
              code in (200, 201) and code2 == 400 and "interval" in str(r2)
              and st2["rev"] == "c0c0c0c0" and st2["interval"] == st["interval"],
              f"http {code2} {str(r2)[:60]!r} rev={st2['rev']!r}")

    code, r = b.register(url, interval=6)
    new = b.settle(2)
    st = b.hook()
    run.check("c2 a registration without a revision: the device reports none",
              code == 200 and st["rev"] == "" and st["interval"] == 6
              and new and all(p["has_rev"] and p["rev"] == "" for p in new[-1:]),
              f"http {code} rev={st['rev']!r} the push reports "
              f"{[p['rev'] for p in new[-1:]]}")

    code, r = b.register(url, manual_override=True)
    new = b.settle(2)
    st = b.hook()
    run.check("c3 pinned on the device: the pushes carry no webhook_rev",
              code == 200 and st["manual_override"] is True
              and new and not new[-1]["has_rev"],
              f"http {code} override={st['manual_override']} has_rev="
              f"{[p['has_rev'] for p in new]}")
    b.refused("c3", "settings for a pinned webhook",
              reply("c3c3c3c3", url, 9), "pinned", st)
    pi.answer(204)
    code, r = b.register(url, manual_override=False)
    new = b.settle(2)
    run.check("c3 unpinned: the key is back",
              code == 200 and new and new[-1]["has_rev"],
              f"http {code} has_rev={[p['has_rev'] for p in new]}")

    st = b.taken("c4", "Home Assistant's settings", reply("c4c4c4c4", url, 4),
                 "c4c4c4c4", 4, b.hook())
    pi.answer(204)
    dut.restart({"ha_webhooks": {"interval_s": 9}})
    n0 = len(pi.pushes())               # the pushes of the new boot only
    new = wait_for(lambda: pi.pushes()[n0:], 60) or []
    stc = b.hook()
    run.check("c4 the interval changed through the settings route (the web "
              "interface's way): the pushes report no revision",
              stc["interval"] == 9 and stc["rev"] == ""
              and new and all(p["rev"] == "" for p in new),
              f"interval={stc['interval']} rev={stc['rev']!r} pushes report "
              f"{[p['rev'] for p in new]}")
    b.taken("c4", "the reply after it (Home Assistant's settings win)",
            reply("c4c4c4c4", url, 4), "c4c4c4c4", 4, stc)


def submit_and_wait(dut, watch, timeout=150):
    """Submit the staged settings on `dut` and wait for the new boot as seen
    through `watch` (the other way in, where the first one closes)."""
    b0 = dut.boot_count()
    if b0 is None:
        raise pcbench.Bench("no boot_count before a restart")
    dut.api("/api/settings/submit", "POST", {})
    time.sleep(3)
    return bool(wait_for(lambda: (lambda n: n is not None and n != b0)(
        watch.boot_count()), timeout, 1.0))


def legs_untrusted(b, args, base):
    """u1: the station's network marked untrusted. The API over the station's
    address is closed then (the network-trust gate), so this leg talks to the
    DUT over its USB network link and needs the DUT's USB-C on the PC. The
    link is proven BEFORE the station's way in is closed."""
    run, dut, pi = b.run, b.dut, b.pi
    usb = pcbench.Dut(f"{args.usb_ip}:80")
    found_usb = dut.settings("usb_host_manager")
    role = {k: found_usb[k] for k in ("enabled", "role", "device_class")}
    tls_name = f"https://{NAME}:{args.port + 1}{HOOK}-u2"
    url = base + "-u1"

    pi.answer(204)
    dut.restart({"usb_host_manager": {"enabled": True, "role": "device",
                                      "device_class": "ncm"}})
    up = wait_for(lambda: usb.api("/api/status")[0] == 200, 60)
    run.check("u0 the DUT answers over its USB network link (the way in "
              "while the station's network is untrusted)", bool(up),
              f"{args.usb_ip}: {usb.api('/api/status')[0]}")
    if not up:
        dut.restart({"usb_host_manager": role})     # the station still open
        return

    closed = False
    try:
        dut.stage("wifi_manager", {"sta_trusted": False})
        closed = submit_and_wait(dut, usb)
        dut.restarts += 1
        gate, _ = dut.api("/api/webhook")
        run.check("u1 the station's network marked untrusted: the API is "
                  "closed over the station, open over USB",
                  closed and gate == 403 and usb.api("/api/webhook")[0] == 200,
                  f"restarted={closed}, station http {gate}")
        ub = Bench(run, usb, pi)
        ub.register(url, rev="u1u1u1u1")
        ub.settle(2)
        st = ub.hook()
        run.check("u1 the pushes still go out over the untrusted network",
                  st["status"] == "ok" and st["rev"] == "u1u1u1u1",
                  f"status={st['status']!r} last_error={st['last_error']!r}")
        ub.refused("u1", "plain http to a private address, from an "
                   "untrusted network", reply("u1x", url, 9), "untrusted", st)
        pi.answer(204)
        ub.register(tls_name, rev="u2u2u2u2")
        ub.settle(2)
        ub.taken("u1", "https to a host name, from an untrusted network",
                 reply("u2x", tls_name, 7), "u2x", 7, ub.hook())
    finally:
        pi.answer(204)
        usb.stage("wifi_manager", {"sta_trusted": True})
        usb.stage("usb_host_manager", role)
        back = submit_and_wait(usb, dut)
        dut.restarts += 1
        run.check("u1 trusted again and the USB role as found: the API "
                  "answers over the station",
                  back
                  and dut.settings("wifi_manager").get("sta_trusted") is True
                  and dut.settings("usb_host_manager") == found_usb,
                  f"back={back}")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--dut", default="localhost:18087",
                    help="host:port of the DUT's API (a tunnel)")
    ap.add_argument("--pi", default="rpi001", help="the bench Pi (ssh host)")
    ap.add_argument("--pi-ip", default="10.42.1.1",
                    help="the Pi's address on the DUT's network")
    ap.add_argument("--port", type=int, default=8447,
                    help="the receiver's http port on the Pi (https: +1)")
    ap.add_argument("--untrusted", action="store_true",
                    help="add the untrusted-network leg (the DUT's USB-C on "
                         "this PC; two more restarts)")
    ap.add_argument("--usb-ip", default="192.168.82.1",
                    help="the DUT's address on its USB network link")
    args = ap.parse_args()

    run = pcbench.Run("PUSH REPLY")
    dut = pcbench.Dut(args.dut, own_tags=("ha_webhooks", "http_client_manager",
                                          "wifi_manager", "settings_manager"),
                      tasks=("ha_webhook",))
    pi = Pi(args.pi)
    b = Bench(run, dut, pi)
    base = f"http://{args.pi_ip}:{args.port}{HOOK}"

    # t0: as found
    st0 = dut.get("/api/status")
    found_settings = dut.settings("ha_webhooks")
    found_sets = set_names(dut)
    dut.as_found()
    run.check("t0 the DUT answers, this firmware reports a revision, the "
              "webhook's settings and the sets read",
              "rev" in found_settings and "reply" in dut.get("/api/webhook")
              and SET not in found_sets,
              f"url={found_settings.get('url')!r} interval_s="
              f"{found_settings.get('interval_s')} sets={found_sets}")

    try:
        # t1: the Pi's side and the CA on the DUT
        code, ca_pem = pi.run(CERT_CMDS)
        run.check("t1 a CA and a certificate that names the host only, on "
                  "the Pi", code == 0 and "BEGIN CERTIFICATE" in ca_pem,
                  f"ssh exit {code}")
        pi.start(args.port)
        up = upload_ca(dut, ca_pem)
        run.check("t1 the receiver, the public address and the name are up; "
                  "the CA uploaded as a certificate set",
                  up == 200 and SET in set_names(dut), f"http {up}")

        legs_basic(b, base)
        leg_restart(b)
        legs_trust(b, args)
        legs_routes(b, base)
        if args.untrusted:
            legs_untrusted(b, args, base)
        run.metric("push_reply_pushes_at_receiver", len(pi.pushes()))
    finally:
        # t9: as found (the settings carry the registration: one restart)
        try:
            pi.answer(204)
            dut.api("/api/webhook", "DELETE")
            time.sleep(7)                # a push that was under way
            dut.api("/api/webhook", "DELETE")
            if dut.settings("ha_webhooks") != found_settings:
                dut.restart({"ha_webhooks": found_settings})
            dut.api(f"/api/certs?set={SET}", "DELETE")
            after = dut.settings("ha_webhooks")
            run.check("t9 the webhook's settings as found (a restart)",
                      after == found_settings,
                      f"url={after.get('url')!r} rev={after.get('rev')!r} "
                      f"cert_set={after.get('cert_set')!r} interval_s="
                      f"{after.get('interval_s')}")
            run.check("t9 the certificate sets as found",
                      set_names(dut) == found_sets, f"{set_names(dut)}")
        finally:
            pi.stop()
            ok, what = pi.clean()
            run.check("t9 the Pi as found (no receiver, no name, no public "
                      "address, no files)", ok, what)

    dut.reset_check(run, st0)
    dut.passing_checks(run)
    return run.verdict()


if __name__ == "__main__":
    try:
        sys.exit(main())
    except pcbench.Bench as e:
        print(f"BENCH ERROR: {e}")
        print("PUSH REPLY FAIL: bench error")
        sys.exit(2)
