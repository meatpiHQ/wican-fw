#!/usr/bin/env python3
"""The firmware release server of the bench (runs ON rpi001 as a transient
unit; 2026-10-18, internal/TASK_fw_update.md 9.2): serves a manifest tree
the way raw.githubusercontent.com and a GitHub release asset do, with the
knobs the fw_update bench turns.

    python3 fw_server.py --root ~/wican-http/fw [--port 8446]
                         [--tls-dir ~/wican-tls | --plain] [--redirect-images]

Behaviour:
  GET <path>                 a file under --root; `ETag: "<sha256 prefix>"`,
                             `If-None-Match` answers 304 (the device's cached
                             manifest view stands)
  GET /dl/<name>             with --redirect-images (or the knob): 302 to
                             /img/<name>?<a 1.2 KB signed-looking query>, the
                             shape of a GitHub release asset; else the file
  GET /img/<name>            the file (the hop's target)
  POST /__ctl?truncate=N&delay_ms=M&redirect=0|1
                             the knobs, live: `truncate` serves only N bytes
                             of an image body (the Content-Length still says
                             the whole file: a broken download), `delay_ms`
                             sleeps that long per 64 KB of an image body (a
                             slow download for the abort and mutual exclusion
                             legs), `redirect` flips the 302
  GET /__dump                the request log as JSON: [{t, method, path,
                             status, inm, bytes}] (the bench reads what the
                             device asked, If-None-Match included)
  POST /__reset              forget the log

One line per request on stdout (`journalctl -u fw_bench`).
"""
import argparse
import hashlib
import http.server
import json
import os
import ssl
import sys
import threading
import time
import urllib.parse

KNOBS = {"truncate": 0, "delay_ms": 0, "redirect": False}
LOG = []
LOCK = threading.Lock()
ROOT = "."
IMAGE_EXT = (".bin", ".zip")


def is_image(path):
    return path.lower().endswith(IMAGE_EXT)


def fake_signature():
    # the shape of a GitHub release asset's redirect target: ~1.2 KB of query
    return ("X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Credential=releaseassetproduction%2F20261018%2Fus-east-1%2Fs3%2Faws4_request"
            "&X-Amz-Date=20261018T090000Z&X-Amz-Expires=300&X-Amz-Signature=" + "ab" * 32
            + "&X-Amz-SignedHeaders=host&response-content-disposition=attachment%3B%20filename%3Dfirmware.bin"
            + "&response-content-type=application%2Foctet-stream&actor_id=0&key_id=0&repo_id=1&token=" + "cd" * 400)


class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "fw_bench/1"

    def log_message(self, fmt, *args):  # one line per request, our own shape
        return

    def _note(self, status, nbytes=0):
        rec = {"t": round(time.time(), 3), "method": self.command, "path": self.path,
               "status": status, "inm": self.headers.get("If-None-Match", ""), "bytes": nbytes}
        with LOCK:
            LOG.append(rec)
        print(json.dumps(rec), flush=True)

    def _send_json(self, obj, status=200):
        body = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
        self._note(status, len(body))

    def do_POST(self):
        u = urllib.parse.urlsplit(self.path)
        if u.path == "/__ctl":
            q = urllib.parse.parse_qs(u.query)
            if "truncate" in q:
                KNOBS["truncate"] = int(q["truncate"][0])
            if "delay_ms" in q:
                KNOBS["delay_ms"] = int(q["delay_ms"][0])
            if "redirect" in q:
                KNOBS["redirect"] = q["redirect"][0] in ("1", "true")
            return self._send_json({"ok": True, "knobs": KNOBS})
        if u.path == "/__reset":
            with LOCK:
                LOG.clear()
            return self._send_json({"ok": True})
        self._send_json({"error": "not found"}, 404)

    def do_GET(self):
        u = urllib.parse.urlsplit(self.path)
        if u.path == "/__dump":
            with LOCK:
                recs = list(LOG)
            return self._send_json({"knobs": KNOBS, "requests": recs})
        if u.path.startswith("/dl/") and KNOBS["redirect"]:
            name = u.path[4:]
            host = self.headers.get("Host", "")
            scheme = "https" if isinstance(self.connection, ssl.SSLSocket) else "http"
            loc = "%s://%s/img/%s?%s" % (scheme, host, name, fake_signature())
            self.send_response(302)
            self.send_header("Location", loc)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return self._note(302)
        rel = u.path
        if rel.startswith("/dl/") or rel.startswith("/img/"):
            rel = "/" + rel.split("/", 2)[2]
        rel = urllib.parse.unquote(rel).lstrip("/")
        full = os.path.normpath(os.path.join(ROOT, rel))
        if not full.startswith(os.path.normpath(ROOT)) or not os.path.isfile(full):
            body = b"not found\n"
            self.send_response(404)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return self._note(404, len(body))
        with open(full, "rb") as f:
            data = f.read()
        etag = '"' + hashlib.sha256(data).hexdigest()[:40] + '"'
        if self.headers.get("If-None-Match", "") == etag:
            self.send_response(304)
            self.send_header("ETag", etag)
            self.end_headers()
            return self._note(304)
        image = is_image(full)
        ctype = ("application/octet-stream" if image else
                 "application/json" if full.endswith(".json") else
                 "text/markdown; charset=utf-8" if full.endswith(".md") else "text/plain")
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("ETag", etag)
        self.send_header("Cache-Control", "max-age=300")
        self.end_headers()
        sent = 0
        limit = KNOBS["truncate"] if (image and KNOBS["truncate"] > 0) else len(data)
        try:
            while sent < limit:
                chunk = data[sent:min(sent + 65536, limit)]
                self.wfile.write(chunk)
                self.wfile.flush()
                sent += len(chunk)
                if image and KNOBS["delay_ms"] > 0:
                    time.sleep(KNOBS["delay_ms"] / 1000.0)
            if sent < len(data):
                # a broken download: the connection closes short of the Content-Length
                self.close_connection = True
        except (BrokenPipeError, ConnectionResetError, ssl.SSLError):
            self.close_connection = True
        self._note(200, sent)


class Server(http.server.ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def main():
    global ROOT
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--root", default=os.path.expanduser("~/wican-http/fw"))
    ap.add_argument("--port", type=int, default=8446)
    ap.add_argument("--bind", default="0.0.0.0")
    ap.add_argument("--tls-dir", default=os.path.expanduser("~/wican-tls"))
    ap.add_argument("--plain", action="store_true", help="http, no TLS")
    ap.add_argument("--redirect-images", action="store_true", help="302 for /dl/<name> from the start")
    a = ap.parse_args()
    ROOT = a.root
    KNOBS["redirect"] = a.redirect_images
    os.makedirs(ROOT, exist_ok=True)
    srv = Server((a.bind, a.port), Handler)
    scheme = "http"
    if not a.plain:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(os.path.join(a.tls_dir, "server.crt"), os.path.join(a.tls_dir, "server.key"))
        srv.socket = ctx.wrap_socket(srv.socket, server_side=True)
        scheme = "https"
    print("FW SERVER READY %s://%s:%d root %s redirect %s" % (scheme, a.bind, a.port, ROOT, KNOBS["redirect"]), flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
