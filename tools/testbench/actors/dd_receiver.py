#!/usr/bin/env python3
"""Bench receiver for the data_destinations bench (runs ON rpi001).

One process, four listeners + an MQTT subscriber, everything recorded in
memory and served back to the orchestrator over the plain port:

  :8199  HTTP   any POST is recorded; GET /__dump?since=N returns the
                records (JSON list) from index N; GET /__mqtt the MQTT
                records; POST /__control {"abrp_reject": bool} flips the
                mock ABRP into its "status":"error" mode
  :8443  HTTPS  bench server certificate (~/wican-tls/server.crt, CN
                10.42.0.1 - the firmware skips the CN check for raw-IP
                hosts, the CHAIN must still verify against the bench CA)
  :8444  HTTPS  same certificate + REQUIRED client certificate signed by
                the bench CA (mutual TLS)
  :8445  HTTPS  the mock ABRP endpoint: /1/tlm/send takes the Iternio
                form (token=&tlm=<json>), api_key as ?api_key= or
                Authorization: APIKEY, answers {"status":"ok"} or
                {"status":"error","missing":[...]} - HTTP 200 either way,
                exactly like the real API

Records: {"i","t","port","path","method","query","headers":{...},
          "body","peer_cn"}; MQTT: {"i","t","topic","payload","retain"}.

  python3 dd_receiver.py [--tls-dir ~/wican-tls] [--mqtt localhost]
                         [--topic 'wican/#']
"""
import argparse
import http.server
import json
import os
import ssl
import sys
import threading
import time
import urllib.parse

RECORDS = []
MQTT = []
CTRL = {"abrp_reject": False}
LOCK = threading.Lock()


def record(port, handler, body, peer_cn=""):
    url = urllib.parse.urlsplit(handler.path)
    with LOCK:
        rec = {"i": len(RECORDS), "t": time.time(), "port": port, "path": url.path,
               "method": handler.command, "query": dict(urllib.parse.parse_qsl(url.query)),
               "headers": {k.lower(): v for k, v in handler.headers.items()},
               "body": body, "peer_cn": peer_cn}
        RECORDS.append(rec)
    return rec


def send_json(handler, obj, status=200):
    payload = json.dumps(obj).encode()
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(payload)))
    handler.end_headers()
    handler.wfile.write(payload)


def read_body(handler):
    n = int(handler.headers.get("Content-Length", 0))
    return handler.rfile.read(n).decode("utf-8", "replace") if n else ""


def peer_cn_of(handler):
    try:
        cert = handler.connection.getpeercert()
    except Exception:
        return ""
    for rdn in (cert or {}).get("subject", ()):
        for k, v in rdn:
            if k == "commonName":
                return v
    return ""


class Plain(http.server.BaseHTTPRequestHandler):
    port = 8199

    def do_GET(self):
        url = urllib.parse.urlsplit(self.path)
        q = dict(urllib.parse.parse_qsl(url.query))
        if url.path == "/__dump":
            since = int(q.get("since", 0))
            with LOCK:
                out = [r for r in RECORDS if r["i"] >= since]
            return send_json(self, out)
        if url.path == "/__mqtt":
            since = int(q.get("since", 0))
            with LOCK:
                out = [r for r in MQTT if r["i"] >= since]
            return send_json(self, out)
        if url.path == "/__control":
            return send_json(self, CTRL)
        record(self.port, self, "", peer_cn_of(self))
        send_json(self, {"ok": True})

    def do_POST(self):
        body = read_body(self)
        url = urllib.parse.urlsplit(self.path)
        if url.path == "/__control":
            try:
                CTRL.update(json.loads(body or "{}"))
            except Exception:
                pass
            return send_json(self, CTRL)
        record(self.port, self, body, peer_cn_of(self))
        send_json(self, {"ok": True})

    def log_message(self, *a):
        pass


class Tls(Plain):
    port = 8443


class Mtls(Plain):
    port = 8444


class Abrp(Plain):
    """Mock Iternio telemetry endpoint (GET or POST, form or query)."""
    port = 8445

    def _handle(self, body):
        url = urllib.parse.urlsplit(self.path)
        params = dict(urllib.parse.parse_qsl(url.query))
        if body:
            params.update(dict(urllib.parse.parse_qsl(body)))
        rec = record(self.port, self, body, peer_cn_of(self))
        auth = self.headers.get("Authorization", "")
        api_key = params.get("api_key") or (auth[7:] if auth.upper().startswith("APIKEY ") else "")
        missing = []
        if not params.get("token"):
            missing.append("token")
        tlm = None
        try:
            tlm = json.loads(params.get("tlm", "")) if params.get("tlm") else None
        except Exception:
            tlm = None
        if tlm is None:
            missing.append("tlm")
        elif "utc" not in tlm:
            missing.append("utc")
        if not api_key:
            missing.append("api_key")
        rec["abrp"] = {"token": params.get("token", ""), "api_key": api_key, "tlm": tlm,
                       "auth_header": auth}
        if url.path != "/1/tlm/send":
            return send_json(self, {"status": "error", "errors": ["unknown endpoint"]}, 404)
        if CTRL.get("abrp_reject"):
            return send_json(self, {"status": "error", "errors": ["bench reject"]})
        if missing:
            return send_json(self, {"status": "error", "missing": missing})
        send_json(self, {"status": "ok", "result": {}})

    def do_GET(self):
        self._handle("")

    def do_POST(self):
        self._handle(read_body(self))


def serve(handler, port, tls_dir=None, require_client=False):
    srv = http.server.ThreadingHTTPServer(("0.0.0.0", port), handler)
    if tls_dir:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(os.path.join(tls_dir, "server.crt"), os.path.join(tls_dir, "server.key"))
        if require_client:
            ctx.verify_mode = ssl.CERT_REQUIRED
            ctx.load_verify_locations(os.path.join(tls_dir, "ca.pem"))
        srv.socket = ctx.wrap_socket(srv.socket, server_side=True)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def mqtt_sub(host, topic):
    try:
        import paho.mqtt.client as mqtt
    except Exception as e:
        print("no paho:", e, flush=True)
        return
    try:
        c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2) if hasattr(mqtt, "CallbackAPIVersion") else mqtt.Client()
    except Exception:
        c = mqtt.Client()

    def on_connect(client, *a, **k):
        client.subscribe(topic)

    def on_message(client, userdata, msg):
        with LOCK:
            MQTT.append({"i": len(MQTT), "t": time.time(), "topic": msg.topic,
                         "payload": msg.payload.decode("utf-8", "replace"), "retain": bool(msg.retain)})

    c.on_connect = on_connect
    c.on_message = on_message
    while True:
        try:
            c.connect(host, 1883, 30)
            c.loop_forever(retry_first_connection=True)
        except Exception:
            time.sleep(2)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--tls-dir", default=os.path.expanduser("~/wican-tls"))
    ap.add_argument("--mqtt", default="localhost")
    ap.add_argument("--topic", default="wican/#")
    a = ap.parse_args()
    serve(Plain, 8199)
    serve(Tls, 8443, a.tls_dir)
    serve(Mtls, 8444, a.tls_dir, require_client=True)
    serve(Abrp, 8445, a.tls_dir)
    threading.Thread(target=mqtt_sub, args=(a.mqtt, a.topic), daemon=True).start()
    print("dd_receiver up: 8199 http, 8443 https, 8444 mtls, 8445 abrp; mqtt", a.mqtt, a.topic, flush=True)
    while True:
        time.sleep(3600)


if __name__ == "__main__":
    main()
