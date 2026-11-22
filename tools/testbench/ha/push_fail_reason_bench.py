#!/usr/bin/env python3
"""A push to Home Assistant that fails says WHY, per address.

The claim: when the webhook's address does not take a push, `GET /api/webhook`
carries one entry per address that failed (`failures`: slot, a reason word, a
code for support, the time), the W line of the cycle names them, and the web
interface's Home Assistant card says it in plain words. Before 2026-11-22 the
unit said `status: failed` and `last_error: esp_err=ESP_ERR_HTTP_CONNECT` for
every kind of failure; a field report of that day (a far end that closed the
TLS handshake, `-0x7280` in the device log only) took the log and two rounds
of questions to read.

The far ends are this bench's own, on the Pi: one small server per way a push
can be turned away, with certificates made for the run under a root made for
the run. The unit meets them by the Pi's name (`rpi001.local`, so its hello
carries a host name), first with its built-in certificate bundle, then with
the run's root as a certificate set (two planned restarts: `cert_set` is a
setting).

Legs:
  t0   as found: the webhook's settings and registration, the certificate sets
  t1   the far ends up on the Pi
  a..  with the built-in bundle, one address each:
         dns            a name that does not exist
         refused        a port nothing listens on
         unreachable    an address nobody has
         tls_closed     a far end that closes after the hello (the report)
         tls_refused    a far end that answers the hello with an alert
         not_https      https to a port that speaks plain http
         cert_untrusted a certificate under a root the bundle does not hold
         no_reply       a server that takes the request and never answers
         http 400 / 404 / 502   an answer that is not a 2xx
  b..  with the run's root as the certificate set (restart 1):
         cert           a certificate for another name, and one under another
                        root: the certificate check says no to both and the
                        unit cannot tell them apart (esp-tls of IDF 6.1 does
                        not hand over the check's flags after a failed
                        handshake), so they are one reason
         dates          a certificate that ran out yesterday and one that
                        starts tomorrow: recorded, not judged. The firmware
                        does not check a certificate's dates
                        (CONFIG_MBEDTLS_HAVE_TIME_DATE is off, IDF's
                        default), so both are taken today; with the check
                        on both must fail with the reason `cert`
         delivered      the good far end: `failures` empty, status ok
  c..  two addresses:
         the failover delivers: status ok, the first address's reason kept
         both fail: two entries in the order tried, the W line names both,
         and (unless --no-page) the unit's own page in Chrome shows a line
         per address and the bubble with the code (push_fail_page.mjs)
  t9   everything as found (restart 2): the settings, the registration, the
       sets; no reset nobody asked for, no E line of the own tags

  python tools/testbench/ha/push_fail_reason_bench.py --dut localhost:18086
      --pi rpi001 [--name rpi001.local] [--pi-ip 10.42.1.1] [--port 8470]

Prints `PUSH FAIL REASON PASS`. Written 2026-11-22.
"""
import argparse
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "lib"))
import pcbench  # noqa: E402

SET = "pushfail"
WORK = "/tmp/wican_pushfail"
HOOK = "/api/webhook/wican-pushfail"

# The far ends: stock python3 with its `cryptography` (the Pi's own, 38.x).
SERVER_SRC = r'''
import datetime
import http.server
import os
import socket
import ssl
import sys
import threading
import time

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

work, name, base = sys.argv[1], sys.argv[2], int(sys.argv[3])
NOW = datetime.datetime.utcnow()
DAY = datetime.timedelta(days=1)


def key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def cn(text):
    return x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, text)])


def cert(subject, pub, issuer, issuer_key, ca, san=None, start=None, end=None):
    b = (x509.CertificateBuilder().subject_name(subject).issuer_name(issuer).public_key(pub)
         .serial_number(x509.random_serial_number())
         .not_valid_before(start or NOW - DAY).not_valid_after(end or NOW + 30 * DAY)
         .add_extension(x509.BasicConstraints(ca=ca, path_length=None), critical=True))
    if san:
        b = (b.add_extension(x509.SubjectAlternativeName([x509.DNSName(san)]), critical=False)
             .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False))
    return b.sign(issuer_key, hashes.SHA256())


def write(path, data):
    with open(os.path.join(work, path), "wb") as f:
        f.write(data)


def leaf(tag, root, root_key, san, start=None, end=None):
    k = key()
    c = cert(cn(san), k.public_key(), root.subject, root_key, False, san=san, start=start, end=end)
    write(tag + ".pem", c.public_bytes(serialization.Encoding.PEM))
    write(tag + ".key", k.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL,
                                        serialization.NoEncryption()))
    return os.path.join(work, tag + ".pem"), os.path.join(work, tag + ".key")


root_key = key()
root = cert(cn("wican pushfail bench root"), root_key.public_key(), cn("wican pushfail bench root"), root_key, True)
write("ca.pem", root.public_bytes(serialization.Encoding.PEM))
other_key = key()
other = cert(cn("another root"), other_key.public_key(), cn("another root"), other_key, True)
CERTS = {
    "good": leaf("good", root, root_key, name),
    "cert_name": leaf("cert_name", root, root_key, "another-host.bench.example"),
    "cert_expired": leaf("cert_expired", root, root_key, name, start=NOW - 30 * DAY, end=NOW - DAY),
    "cert_future": leaf("cert_future", root, root_key, name, start=NOW + DAY, end=NOW + 30 * DAY),
    "cert_stranger": leaf("cert_stranger", other, other_key, name),
}


class Hook(http.server.BaseHTTPRequestHandler):
    """A webhook's far end: 204, or the status its path asks for (/s502)."""
    count_file = None

    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length") or 0))
        tail = self.path.rsplit("/", 1)[-1]
        status = int(tail[1:]) if tail[:1] == "s" and tail[1:].isdigit() else 204
        if status == 204 and self.count_file:
            with open(self.count_file, "a") as f:
                f.write("x")
        self.send_response(status)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, *a):
        pass


def http_server(port, pair=None, count=None):
    handler = type("H", (Hook,), {"count_file": count})
    srv = http.server.ThreadingHTTPServer(("0.0.0.0", port), handler)
    if pair:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(*pair)
        srv.socket = ctx.wrap_socket(srv.socket, server_side=True)
    threading.Thread(target=srv.serve_forever, daemon=True).start()


def raw_server(port, serve):
    s = socket.socket()
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(("0.0.0.0", port))
    s.listen(8)

    def loop():
        while True:
            conn, _ = s.accept()
            threading.Thread(target=serve, args=(conn,), daemon=True).start()
    threading.Thread(target=loop, daemon=True).start()


def closes_after_hello(conn):
    try:
        conn.settimeout(5)
        conn.recv(4096)
    except OSError:
        pass
    conn.close()


REFUSE = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
REFUSE.maximum_version = ssl.TLSVersion.TLSv1_2
REFUSE.set_ciphers("DHE-RSA-AES256-GCM-SHA384")   # a suite the unit does not offer
REFUSE.load_cert_chain(*CERTS["good"])


def refuses_hello(conn):
    try:
        conn.settimeout(5)
        REFUSE.wrap_socket(conn, server_side=True)      # fails, OpenSSL sends the alert
    except (OSError, ssl.SSLError):
        pass
    conn.close()


def never_answers(conn):
    try:
        conn.settimeout(5)
        conn.recv(4096)
        time.sleep(25)
    except OSError:
        pass
    conn.close()


http_server(base + 1, CERTS["good"], os.path.join(work, "count"))
raw_server(base + 2, closes_after_hello)
raw_server(base + 3, refuses_hello)
http_server(base + 4)                                   # plain http: not_https, and the statuses
http_server(base + 5, CERTS["cert_name"])
http_server(base + 6, CERTS["cert_expired"])
http_server(base + 7, CERTS["cert_future"])
raw_server(base + 8, never_answers)
http_server(base + 9, CERTS["cert_stranger"])
print("READY", ssl.OPENSSL_VERSION, flush=True)
while True:
    time.sleep(3600)
'''


class Pi:
    """The bench Pi over ssh: the far ends."""

    def __init__(self, host):
        self.ssh = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", host]
        self.proc = None

    def run(self, cmd, text_in=None, timeout=90):
        r = subprocess.run(self.ssh + [cmd], input=text_in, capture_output=True, text=True, timeout=timeout)
        return r.returncode, r.stdout

    def start(self, name, port):
        self.run(f"pkill -f '[w]ican_pushfail/server.py' ; rm -rf {WORK} ; mkdir -p {WORK}")
        code, _ = self.run(f"cat > {WORK}/server.py", text_in=SERVER_SRC)
        if code != 0:
            raise pcbench.Bench("the far ends' source did not reach the Pi")
        self.proc = subprocess.Popen(self.ssh + [f"python3 -u {WORK}/server.py {WORK} {name} {port}"],
                                     stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        lines = []
        for _ in range(40):
            line = self.proc.stdout.readline()
            if not line or "READY" in line:
                break
            lines.append(line.strip())
        if "READY" not in line:
            raise pcbench.Bench("the far ends did not start on the Pi (python3 with cryptography? the ports "
                                f"{port + 1} to {port + 9} free?): " + " | ".join(lines[-3:])[:300])
        return line.strip()

    def delivered(self):
        code, out = self.run(f"wc -c < {WORK}/count 2>/dev/null || echo 0")
        return int(out.strip() or 0) if out.strip().isdigit() else 0

    def stop(self):
        if self.proc is not None:
            self.proc.terminate()
            self.proc = None
        self.run(f"pkill -f '[w]ican_pushfail/server.py' ; rm -rf {WORK} ; true")


def upload_ca(dut, pem):
    req = urllib.request.Request(f"{dut.base}/api/certs/upload?set={SET}&type=ca", data=pem.encode(),
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


def cycle(dut, urls, wait=70):
    """Register `urls` (one or two), let two push cycles pass (the second one
    is certainly the new registration's), return the webhook's state."""
    body = {"url": urls[0], "enabled": True, "interval": 5}
    if len(urls) > 1:
        body["urls"] = list(urls)
    code, r = dut.api("/api/webhook", "POST", body)
    if code not in (200, 201):
        raise pcbench.Bench(f"registration: HTTP {code} {str(r)[:120]}")
    s0 = dut.get("/api/webhook")
    st = s0
    t0 = time.time()
    while time.time() - t0 < wait:
        time.sleep(2)
        st = dut.get("/api/webhook")
        done = (st.get("success_count", 0) - s0.get("success_count", 0)
                + st.get("fail_count", 0) - s0.get("fail_count", 0))
        if done >= 2:
            break
    return st


def new_w_lines(dut, seen):
    lines = [ln for ln in dut.ring_lines() if ln not in seen]
    seen.update(lines)
    return [ln for ln in lines if ln.startswith("W (") and "ha_webhooks: webhook POST failed" in ln]


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--dut", default="localhost:18086", help="host:port of the DUT's API (a tunnel)")
    ap.add_argument("--pi", default="rpi001", help="the bench Pi (ssh host)")
    ap.add_argument("--name", default="rpi001.local", help="the Pi's name as the unit resolves it")
    ap.add_argument("--pi-ip", default="10.42.1.1", help="the Pi's address on the DUT's network")
    ap.add_argument("--nobody", default="10.42.1.250", help="an address on the DUT's network nobody has")
    ap.add_argument("--port", type=int, default=8470, help="the far ends take port+1 to port+9 on the Pi")
    ap.add_argument("--no-page", action="store_true", help="skip the unit's own page in Chrome")
    ap.add_argument("--out", default="", help="where the page leg saves its screenshot")
    args = ap.parse_args()

    run = pcbench.Run("PUSH FAIL REASON")
    dut = pcbench.Dut(args.dut, own_tags=("ha_webhooks", "http_client_manager"))
    pi = Pi(args.pi)
    P = args.port

    def at(offset, scheme="https", path=HOOK, host=None):
        return f"{scheme}://{host or args.name}:{P + offset}{path}"

    # t0: as found
    st0 = dut.get("/api/status")
    found_settings = dut.settings("ha_webhooks")
    found_sets = set_names(dut)
    dut.as_found()
    run.check("t0 the DUT answers, the webhook's settings and the sets read",
              "cert_set" in found_settings and SET not in found_sets,
              f"url={found_settings.get('url')!r} cert_set={found_settings.get('cert_set')!r} sets={found_sets}")
    first = dut.get("/api/webhook")
    run.check("t0 the webhook's answer has `failures`, a list", isinstance(first.get("failures"), list),
              f"{first.get('failures')!r}")
    seen = set(dut.ring_lines())

    def leg(tag, urls, reasons, codes, last_error, status="failed", http=None):
        """One registration, two cycles, the entries compared. `reasons` and
        `codes` (regular expressions) per failed address, in the order tried."""
        st = cycle(dut, urls)
        f = st.get("failures") if isinstance(st.get("failures"), list) else []
        got = [(e.get("slot"), e.get("reason"), e.get("code")) for e in f]
        ok = (st.get("status") == status and len(f) == len(reasons)
              and all(e.get("reason") == r for e, r in zip(f, reasons))
              and all(re.fullmatch(c, str(e.get("code") or "")) for e, c in zip(f, codes))
              and all(re.match(r"^20\d\d-\d\d-\d\dT\d\d:\d\d:\d\dZ$", str(e.get("time") or "")) for e in f)
              and re.fullmatch(last_error, str(st.get("last_error") or "")) is not None)
        if http is not None:
            ok = ok and len(f) == 1 and f[0].get("http") == http
        else:
            ok = ok and all("http" not in e for e in f)
        run.check(f"{tag}: {', '.join(reasons) or 'no failure'}", ok,
                  f"status={st.get('status')!r} failures={got} last_error={st.get('last_error')!r}")
        return st, f

    conn = "esp_err=ESP_ERR_HTTP_CONNECT"
    try:
        if found_settings.get("cert_set"):
            dut.restart({"ha_webhooks": {"cert_set": ""}})     # the bundle for the first legs
        # t1: the far ends
        ready = pi.start(args.name, P)
        code, ca_pem = pi.run(f"cat {WORK}/ca.pem")
        run.check("t1 the far ends are up on the Pi, the run's root read",
                  "READY" in ready and "BEGIN CERTIFICATE" in ca_pem, ready[:60])

        # a: one address, the built-in bundle
        leg("a1 a name that does not exist", [f"https://wican-pushfail-nohost.invalid{HOOK}"],
            ["dns"], [r"esp-tls 0x8001( errno \d+)?"], conn)
        leg("a2 a port nothing listens on", [at(0)], ["no_answer"], [r"esp-tls 0x800[46]( errno \d+)?"], conn)
        leg("a3 an address nobody has", [at(1, host=args.nobody)], ["no_answer"],
            [r"esp-tls 0x800[46]( errno \d+)?"], conn)
        leg("a4 the far end closes after the hello (the field report)", [at(2)], ["tls_closed"],
            [r"tls -0x7280"], conn)
        w = new_w_lines(dut, seen)
        run.check("a4 the cycle's W line names the address, the reason and the code",
                  any("(1 tls_closed [tls -0x7280])" in ln and conn in ln for ln in w), (w[-1:] or [""])[0][:170])
        leg("a5 the far end answers the hello with an alert", [at(3)], ["tls_refused"], [r"tls -0x7780"], conn)
        leg("a6 https to a port that speaks plain http", [at(4)], ["not_https"], [r"tls -0x7200"], conn)
        leg("a7 a certificate under a root the bundle does not hold", [at(1)], ["cert_untrusted"],
            [r"tls -0x(2700|3000)( flags 0x[0-9A-F]+)?"], conn)
        leg("a8 a server that takes the request and never answers", [at(8, scheme="http")], ["no_reply"],
            [r".+"], r".+")
        leg("a9 an answer of 400", [at(4, scheme="http", path=HOOK + "/s400")], ["http"], [r"http 400"],
            r"http=400", http=400)
        leg("a10 an answer of 404", [at(4, scheme="http", path=HOOK + "/s404")], ["http"], [r"http 404"],
            r"http=404", http=404)
        leg("a11 an answer of 502", [at(4, scheme="http", path=HOOK + "/s502")], ["http"], [r"http 502"],
            r"http=502", http=502)

        # b: the run's root as the certificate set
        up = upload_ca(dut, ca_pem)
        dut.restart({"ha_webhooks": {"cert_set": SET}})
        run.check("b0 the run's root uploaded and selected for the webhook (restart 1)",
                  up == 200 and dut.settings("ha_webhooks").get("cert_set") == SET, f"http {up}")
        seen.update(dut.ring_lines())
        cert_code = r"tls -0x2700( flags 0x[0-9A-F]+)?"
        leg("b1 a certificate for another name", [at(5)], ["cert"], [cert_code], conn)
        leg("b2 a certificate under another root", [at(9)], ["cert"], [cert_code], conn)
        # the dates: what the unit does is recorded; the two must agree, and
        # a refusal has to carry the reason
        seen_dates = []
        for tag, off in (("expired", 6), ("not valid yet", 7)):
            st = cycle(dut, [at(off)])
            f = st.get("failures") or []
            seen_dates.append((tag, st.get("status"), [(e.get("reason"), e.get("code")) for e in f]))
        checked = [s == "failed" for _, s, _ in seen_dates]
        run.check("b3 a certificate past its dates and one before them: the unit treats both alike, "
                  "and a refusal says `cert`",
                  checked[0] == checked[1]
                  and all((s == "ok" and not f) or (s == "failed" and [r for r, _ in f] == ["cert"])
                          for _, s, f in seen_dates),
                  ("the dates are CHECKED: both refused" if all(checked) else
                   "the dates are NOT checked in this build: both certificates are taken") + f" {seen_dates}")
        run.metric("cert_dates_checked", int(all(checked)))
        n0 = pi.delivered()
        st, f = leg("b4 the good far end", [at(1)], [], [], r"", status="ok")
        run.check("b4 the pushes arrived there", pi.delivered() - n0 >= 2, f"+{pi.delivered() - n0}")

        # c: two addresses
        n0 = pi.delivered()
        st, f = leg("c1 the failover delivers, the first address's reason is kept", [at(0), at(1)],
                    ["no_answer"], [r"esp-tls 0x800[46]( errno \d+)?"], r"", status="ok")
        run.check("c1 the entry is the first address's, the pushes arrived at the second",
                  len(f) == 1 and f[0].get("slot") == 1 and pi.delivered() - n0 >= 2,
                  f"slot={f[0].get('slot') if f else None} +{pi.delivered() - n0}")
        seen.update(dut.ring_lines())
        st, f = leg("c2 both addresses fail", [at(0), at(2)], ["no_answer", "tls_closed"],
                    [r"esp-tls 0x800[46]( errno \d+)?", r"tls -0x7280"], conn)
        run.check("c2 the entries are in the order tried, slot 1 then slot 2",
                  [e.get("slot") for e in f] == [1, 2], f"{[e.get('slot') for e in f]}")
        w = new_w_lines(dut, seen)
        run.check("c2 the cycle's W line names both",
                  any(re.search(r"\(1 no_answer \[[^\]]+\], 2 tls_closed \[tls -0x7280\]\)", ln) for ln in w),
                  (w[-1:] or [""])[0][:190])
        if not args.no_page:
            cmd = ["node", os.path.join(HERE, "push_fail_page.mjs"), dut.base]
            if args.out:
                cmd += ["--out", args.out]
            p = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
            for ln in (p.stdout + p.stderr).splitlines():
                print("   " + ln)
            run.check("c3 the unit's own page: a line per failed address, the code in the bubble",
                      "PUSH FAIL PAGE PASS" in p.stdout, f"node exit {p.returncode}")
    finally:
        # t9: as found (the settings carry the registration: one restart)
        try:
            dut.api("/api/webhook", "DELETE")
            if dut.settings("ha_webhooks") != found_settings:
                dut.restart({"ha_webhooks": found_settings})
            dut.api(f"/api/certs?set={SET}", "DELETE")
            after = dut.settings("ha_webhooks")
            run.check("t9 the webhook's settings as found (restart 2)", after == found_settings,
                      f"url={after.get('url')!r} cert_set={after.get('cert_set')!r} "
                      f"interval_s={after.get('interval_s')}")
            run.check("t9 the certificate sets as found", set_names(dut) == found_sets, f"{set_names(dut)}")
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
        print("PUSH FAIL REASON FAIL: bench error")
        sys.exit(2)
