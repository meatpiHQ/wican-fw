#!/usr/bin/env python3
"""HTTPS to an address by IP: the pushes deliver and the log stays quiet.

The claim: a webhook (or a destination) at `https://<ip>:<port>` whose
certificate names a host works, the chain verified and the name not compared,
and it costs no log line per push. esp-tls prints a W line on every connection
that skips the name check (`skip_common_name=true: hostname matching and SNI
disabled ...`); with a push every 15 s the 16 KB log ring held little else and
the warning count climbed (seen 2026-11-14 with a Home Assistant on SSL, whose
integration registers its address by IP). http_client_manager keeps the tag
`esp-tls-mbedtls` to its errors with a log descriptor and says it once a boot
itself. The quieting has to outlive the wildcard level the settings apply at
boot and one sent at runtime (IDF drops every per-tag level on a wildcard set;
log_manager puts the descriptors below INFO back), and the tag can still be
raised by name.

The receiver is this bench's own: an HTTPS server on the Pi with a certificate
made for the run, signed by a CA made for the run and naming a host only, so
the DUT's connection by IP is the DuckDNS + Let's Encrypt shape. The DUT gets
the CA as a certificate set, selected for the webhook (two planned restarts:
`cert_set` is a setting).

Legs:
  t0  as found: the webhook's settings and registration, the certificate sets
  t1  the CA uploaded as the set `tlsbyip`, selected for the webhook (restart
      1), the webhook registered at https://<pi ip>:<port>/..., interval 5 s
  t2  pushes delivered: the chain verified through the set, the name not
      compared
  t3  no `esp-tls-mbedtls` W line came with them; http_client_manager's own
      line is there once
  t4  a wildcard level set at runtime (`PUT /api/logs/level` for `*`): still
      no such W line with the next pushes
  t5  the tag raised by name: the W line comes with the next pushes (the knob
      works, and it was the level that kept the line away)
  t6  a wildcard again: quiet again
  t7  everything as found (restart 2): the settings, the registration, the
      certificate sets; no reset nobody asked for, no E line of the own tags

  python tools/testbench/ha/tls_by_ip_bench.py --dut localhost:18084
      --pi rpi001 --pi-ip 10.42.1.1 [--port 8444]

Prints `TLS BY IP PASS`. Written 2026-11-14.
"""
import argparse
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "lib"))
import pcbench  # noqa: E402

SET = "tlsbyip"
PATH = "/api/webhook/wican-tlsbyip"
WORK = "/tmp/wican_tlsbyip"
TLS_TAG = "esp-tls-mbedtls"
OWN_LINE = "http_client_manager: https to an address by IP"

RECEIVER_SRC = r'''
import http.server
import ssl
import sys

port, cert, key, count_file = int(sys.argv[1]), sys.argv[2], sys.argv[3], sys.argv[4]
posts = 0


class H(http.server.BaseHTTPRequestHandler):
    def do_POST(self):
        global posts
        self.rfile.read(int(self.headers.get("Content-Length") or 0))
        posts += 1
        with open(count_file, "w") as f:
            f.write(str(posts))
        self.send_response(204)
        self.end_headers()

    def log_message(self, *a):
        pass


srv = http.server.ThreadingHTTPServer(("0.0.0.0", port), H)
ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
ctx.load_cert_chain(cert, key)
srv.socket = ctx.wrap_socket(srv.socket, server_side=True)
print("READY", flush=True)
srv.serve_forever()
'''

# A CA and a server certificate that names a host only (no address in it).
CERT_CMDS = (
    f"rm -rf {WORK} && mkdir -p {WORK} && cd {WORK} && "
    "openssl req -x509 -newkey rsa:2048 -nodes -days 30 "
    "-subj '/CN=wican tlsbyip bench CA' -keyout ca.key -out ca.pem 2>/dev/null && "
    "openssl req -newkey rsa:2048 -nodes -subj '/CN=tlsbyip.bench.example' "
    "-keyout srv.key -out srv.csr 2>/dev/null && "
    "printf 'subjectAltName=DNS:tlsbyip.bench.example\\nbasicConstraints=CA:FALSE\\n"
    "extendedKeyUsage=serverAuth\\n' > ext.cnf && "
    "openssl x509 -req -in srv.csr -CA ca.pem -CAkey ca.key -CAcreateserial "
    "-days 30 -extfile ext.cnf -out srv.pem 2>/dev/null && cat ca.pem"
)


class Pi:
    """The bench Pi over ssh: the certificates and the HTTPS receiver."""

    def __init__(self, host):
        self.ssh = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", host]
        self.proc = None

    def run(self, cmd, text_in=None, timeout=90):
        r = subprocess.run(self.ssh + [cmd], input=text_in, capture_output=True,
                           text=True, timeout=timeout)
        return r.returncode, r.stdout

    def start_receiver(self, port):
        self.run("pkill -f '[w]ican_tlsbyip/recv.py' ; true")
        code, _ = self.run(f"cat > {WORK}/recv.py", text_in=RECEIVER_SRC)
        if code != 0:
            raise pcbench.Bench("the receiver's source did not reach the Pi")
        self.proc = subprocess.Popen(
            self.ssh + [f"python3 -u {WORK}/recv.py {port} {WORK}/srv.pem "
                        f"{WORK}/srv.key {WORK}/count"],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        line = self.proc.stdout.readline()
        if "READY" not in line:
            raise pcbench.Bench(f"the HTTPS receiver did not start on the Pi "
                                f"(port {port} taken?): {line.strip()[:160]}")

    def posts(self):
        code, out = self.run(f"cat {WORK}/count 2>/dev/null || echo 0")
        return int(out.strip() or 0) if code == 0 and out.strip().isdigit() else 0

    def stop(self):
        if self.proc is not None:
            self.proc.terminate()
            self.proc = None
        self.run(f"pkill -f '[w]ican_tlsbyip/recv.py' ; rm -rf {WORK} ; true")


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


def tls_w_lines(dut):
    """This run's W lines of esp-tls-mbedtls in the log ring."""
    return {ln for ln in dut.ring_lines()
            if ln not in dut.ring0 and ln.startswith("W (")
            and f") {TLS_TAG}:" in ln}


def pushes(dut, n, wait):
    """Wait for n more delivered pushes. (delivered, failed, last state)."""
    st0 = dut.get("/api/webhook")
    st = st0
    t0 = time.time()
    while time.time() - t0 < wait:
        time.sleep(2)
        st = dut.get("/api/webhook")
        if st.get("success_count", 0) - st0.get("success_count", 0) >= n:
            break
    return (st.get("success_count", 0) - st0.get("success_count", 0),
            st.get("fail_count", 0) - st0.get("fail_count", 0), st)


def level(dut, tag, lvl):
    code, r = dut.api("/api/logs/level", "PUT", {"tag": tag, "level": lvl})
    return code == 200 and isinstance(r, dict) and r.get("ok") is True


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--dut", default="localhost:18084",
                    help="host:port of the DUT's API (a tunnel)")
    ap.add_argument("--pi", default="rpi001", help="the bench Pi (ssh host)")
    ap.add_argument("--pi-ip", default="10.42.1.1",
                    help="the Pi's address on the DUT's network")
    ap.add_argument("--port", type=int, default=8444,
                    help="the HTTPS receiver's port on the Pi")
    args = ap.parse_args()

    run = pcbench.Run("TLS BY IP")
    dut = pcbench.Dut(args.dut, own_tags=("ha_webhooks", "http_client_manager",
                                          "log_manager"))
    pi = Pi(args.pi)
    url = f"https://{args.pi_ip}:{args.port}{PATH}"

    # t0: as found
    st0 = dut.get("/api/status")
    found_settings = dut.settings("ha_webhooks")
    found_sets = set_names(dut)
    dut.as_found()
    run.check("t0 the DUT answers, the webhook's settings and the sets read",
              "cert_set" in found_settings and SET not in found_sets,
              f"url={found_settings.get('url')!r} cert_set="
              f"{found_settings.get('cert_set')!r} sets={found_sets}")

    try:
        # t1: the CA, the receiver, the set selected, the webhook registered
        code, ca_pem = pi.run(CERT_CMDS)
        run.check("t1 a CA and a certificate that names a host only, on the Pi",
                  code == 0 and "BEGIN CERTIFICATE" in ca_pem, f"ssh exit {code}")
        pi.start_receiver(args.port)
        up = upload_ca(dut, ca_pem)
        run.check("t1 the CA uploaded as a certificate set",
                  up == 200 and SET in set_names(dut), f"http {up}")
        dut.restart({"ha_webhooks": {"cert_set": SET}})
        code, r = dut.api("/api/webhook", "POST",
                          {"url": url, "enabled": True, "interval": 5})
        run.check("t1 the set selected (restart 1), the webhook registered by IP",
                  code in (200, 201) and isinstance(r, dict) and r.get("url") == url
                  and dut.settings("ha_webhooks").get("cert_set") == SET,
                  f"http {code} {str(r)[:100]}")

        # t2: the pushes deliver
        ok, bad, st = pushes(dut, 4, 60)
        run.check("t2 pushes delivered over https by IP (the chain verified "
                  "through the set, the name not compared)",
                  ok >= 4 and bad == 0 and st.get("status") == "ok",
                  f"+{ok} delivered, +{bad} failed, status={st.get('status')!r} "
                  f"last_error={st.get('last_error')!r}, the receiver counted "
                  f"{pi.posts()}")

        # t3: and the log stays quiet
        w = tls_w_lines(dut)
        run.check(f"t3 no {TLS_TAG} W line came with the pushes", not w,
                  " | ".join(sorted(w))[:300])
        own = [ln for ln in dut.ring_lines()
               if ln not in dut.ring0 and OWN_LINE in ln]
        run.check("t3 http_client_manager said it once", len(own) == 1,
                  f"{len(own)} line(s): {own[0][:120] if own else ''}")

        # t4: a wildcard level set at runtime does not bring the line back
        run.check("t4 wildcard level set (info)", level(dut, "*", "info"))
        ok, bad, st = pushes(dut, 3, 45)
        w = tls_w_lines(dut)
        run.check(f"t4 still no {TLS_TAG} W line after the wildcard",
                  ok >= 3 and not w,
                  f"+{ok} delivered; " + " | ".join(sorted(w))[:240])

        # t5: raised by name, the line is there: the knob works
        run.check(f"t5 {TLS_TAG} raised to warn by name",
                  level(dut, TLS_TAG, "warn"))
        ok, bad, st = pushes(dut, 2, 35)
        raised = tls_w_lines(dut)
        run.check("t5 the W line comes with the pushes while the tag is raised",
                  ok >= 2 and len(raised) >= 1
                  and any("skip_common_name" in ln for ln in raised),
                  f"+{ok} delivered, {len(raised)} W line(s)")

        # t6: a wildcard again: quiet again
        run.check("t6 wildcard level set (info) again", level(dut, "*", "info"))
        time.sleep(6)                    # a push that was under way
        before = tls_w_lines(dut)
        ok, bad, st = pushes(dut, 3, 45)
        new = tls_w_lines(dut) - before
        run.check("t6 quiet again after the wildcard", ok >= 3 and not new,
                  f"+{ok} delivered; " + " | ".join(sorted(new))[:240])
        run.metric("tls_by_ip_pushes_at_receiver", pi.posts())
    finally:
        # t7: as found (the settings carry the registration: one restart)
        try:
            dut.api("/api/webhook", "DELETE")
            if dut.settings("ha_webhooks") != found_settings:
                dut.restart({"ha_webhooks": found_settings})
            code, _ = dut.api(f"/api/certs?set={SET}", "DELETE")
            after = dut.settings("ha_webhooks")
            run.check("t7 the webhook's settings as found (restart 2)",
                      after == found_settings,
                      f"url={after.get('url')!r} cert_set={after.get('cert_set')!r} "
                      f"interval_s={after.get('interval_s')}")
            run.check("t7 the certificate sets as found",
                      set_names(dut) == found_sets, f"{set_names(dut)}")
        finally:
            pi.stop()

    dut.reset_check(run, st0)
    dut.passing_checks(run)
    return run.verdict()


if __name__ == "__main__":
    try:
        sys.exit(main())
    except pcbench.Bench as e:
        print(f"BENCH ERROR: {e}")
        print("TLS BY IP FAIL: bench error")
        sys.exit(2)
