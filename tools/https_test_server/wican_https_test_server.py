#!/usr/bin/env python3
"""
WiCAN HTTPS Test Server (subfolder version)
Simulates endpoints for:
  - ABRP telemetry: /1/tlm/send (GET or POST with token & tlm)
  - Home Assistant event: /api/events/<event>
  - Home Assistant webhook: /api/webhook/<id>
  - Generic JSON echo: /generic
  - Metrics: /metrics
  - Runtime config introspection: /config
  - Dynamic fault injection: /inject_error (POST)

Usage:
  python tools/https_test_server/wican_https_test_server.py --host 0.0.0.0 --port 8443 --require-token --expected-token SECRET --verbose

Self-signed certificate auto-generated into test_certs/ (needs openssl) unless --plain-http.
"""
import argparse, json, os, random, ssl, subprocess, sys, threading, time, shutil
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse, parse_qs, unquote

LOCK = threading.Lock()
STATS = {'requests':0,'total_ok':0,'total_errors':0,'path_hits':{},'start_time':time.time()}
RUNTIME = {'fail_rate':0.0,'delay_ms':0}
CONFIG = {}
MAX_BODY = 64*1024
ABRP_KEYS = { 'utc','soc','soh','speed','lat','lon','elevation','is_charging','power','ext_temp','batt_temp','car_model','current','voltage' }

def log(msg):
    if CONFIG.get('verbose'): print(f"[HTTPS_TEST] {msg}")

def update_stats(path, ok):
    with LOCK:
        STATS['requests'] += 1
        STATS['path_hits'][path] = STATS['path_hits'].get(path,0)+1
        (STATS['total_ok'] if ok else STATS['total_errors'])
        if ok: STATS['total_ok']+=1
        else: STATS['total_errors']+=1

class RequestHandler(BaseHTTPRequestHandler):
    server_version = "WiCANHTTPS/1.0"
    def log_message(self, fmt, *args):
        if CONFIG.get('verbose'): super().log_message(fmt,*args)
    def _read_body(self):
        length = int(self.headers.get('Content-Length','0') or 0)
        if length>MAX_BODY:
            self._send_json(413, {'error':'payload too large','max':MAX_BODY}); return None
        return self.rfile.read(length) if length>0 else b''
    def _maybe_fail_or_delay(self):
        if RUNTIME['delay_ms']>0: time.sleep(RUNTIME['delay_ms']/1000.0)
        if random.random()<RUNTIME['fail_rate']:
            self._send_json(500, {'error':'injected failure'}); return True
        return False
    def _send_json(self, code, obj):
        body = json.dumps(obj).encode(); self.send_response(code)
        self.send_header('Content-Type','application/json'); self.send_header('Content-Length', str(len(body)))
        self.end_headers(); self.wfile.write(body)
    def _auth_failed(self): self._send_json(401, {'error':'unauthorized'})
    def _check_token(self, path, qtok, btoken, webhook):
        if not CONFIG.get('require_token'): return True
        if webhook: return True
        exp = CONFIG.get('expected_token'); cand = qtok or btoken
        return bool(exp and cand==exp)
    def _parse_json(self, raw):
        if not raw: return {}
        if 'application/json' in (self.headers.get('Content-Type','')):
            try: return json.loads(raw.decode() or '{}')
            except Exception: return {}
        return {}
    def do_GET(self):
        p = urlparse(self.path)
        if p.path=='/metrics':
            with LOCK: data=dict(STATS); data['uptime_s']=round(time.time()-STATS['start_time'],2)
            self._send_json(200,data); update_stats(p.path,True); return
        if p.path=='/config':
            cfg={k:v for k,v in CONFIG.items() if k!='expected_token'}; self._send_json(200,cfg); update_stats(p.path,True); return
        if p.path=='/1/tlm/send':
            qs=parse_qs(p.query); token=(qs.get('token') or [''])[0]; tlm_raw=(qs.get('tlm') or [''])[0]
            if self._maybe_fail_or_delay(): update_stats(p.path,False); return
            if not self._check_token(p.path, token, None, False): update_stats(p.path,False); return self._auth_failed()
            try: telemetry=json.loads(unquote(tlm_raw)) if tlm_raw else {}
            except Exception: telemetry={}
            self._send_json(200, {'status':'ok','received':telemetry,'utc':int(time.time())}); update_stats(p.path,True); return
        self._send_json(404, {'error':'not found'}); update_stats(p.path,False)
    def do_POST(self):
        p=urlparse(self.path); raw=self._read_body();
        if raw is None: update_stats(p.path,False); return
        if CONFIG.get('log_bodies'):
            try:
                preview = raw[:512].decode(errors='replace')
            except Exception:
                preview = str(raw[:64])
            log(f"BODY {p.path} ({len(raw)} bytes): {preview}{' ...' if len(raw)>512 else ''}")
        auth=self.headers.get('Authorization'); bearer=None
        if auth and auth.lower().startswith('bearer '): bearer=auth.split(' ',1)[1].strip()
        qs=parse_qs(p.query); qtok=(qs.get('token') or [''])[0]; webhook=p.path.startswith('/api/webhook/')
        if p.path=='/inject_error':
            body=self._parse_json(raw); changed={}
            if 'fail_rate' in body:
                try: fr=float(body['fail_rate']);
                except ValueError: fr=None
                if fr is not None and 0<=fr<=1: RUNTIME['fail_rate']=fr; changed['fail_rate']=fr
            if 'delay_ms' in body:
                try: d=int(body['delay_ms']);
                except ValueError: d=None
                if d is not None and 0<=d<=60000: RUNTIME['delay_ms']=d; changed['delay_ms']=d
            self._send_json(200, {'updated':changed,'current':RUNTIME}); update_stats(p.path,True); return
        if self._maybe_fail_or_delay(): update_stats(p.path,False); return
        if not self._check_token(p.path, qtok, bearer, webhook): update_stats(p.path,False); return self._auth_failed()
        if p.path=='/1/tlm/send':
            body=self._parse_json(raw)
            telemetry=body.get('tlm') if isinstance(body.get('tlm'),dict) else {k:v for k,v in body.items() if k in ABRP_KEYS}
            if 'utc' not in telemetry: telemetry['utc']=int(time.time())
            self._send_json(200, {'status':'ok','received':telemetry,'utc':int(time.time())}); update_stats(p.path,True); return
        if p.path.startswith('/api/events/'):
            event=p.path.split('/api/events/',1)[1] or 'unknown'; payload=self._parse_json(raw)
            self._send_json(200, {'event':event,'received':payload,'ts':int(time.time())}); update_stats(p.path,True); return
        if webhook:
            hook=p.path.split('/api/webhook/',1)[1]; payload=self._parse_json(raw)
            self._send_json(200, {'webhook':hook,'received':payload,'ts':int(time.time())}); update_stats(p.path,True); return
        if p.path=='/generic':
            payload=self._parse_json(raw)
            self._send_json(200, {'ok':True,'received':payload,'ts':int(time.time())}); update_stats(p.path,True); return
        self._send_json(404, {'error':'not found'}); update_stats(p.path,False)

def _gen_cert_with_cryptography(cert_path, key_path):
    try:
        from cryptography import x509
        from cryptography.x509.oid import NameOID
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
    except ImportError:
        print('[HTTPS_TEST] cryptography module not installed; run: pip install cryptography')
        return False
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'localhost')])
    cert = (x509.CertificateBuilder()
            .subject_name(subject)
            .issuer_name(subject)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(datetime.utcnow() - timedelta(minutes=1))
            .not_valid_after(datetime.utcnow() + timedelta(days=365))
            .add_extension(x509.SubjectAlternativeName([x509.DNSName('localhost')]), critical=False)
            .sign(key, hashes.SHA256()))
    with open(key_path, 'wb') as f:
        f.write(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL, serialization.NoEncryption()))
    with open(cert_path, 'wb') as f:
        f.write(cert.public_bytes(serialization.Encoding.PEM))
    print('[HTTPS_TEST] Generated cert/key using cryptography fallback')
    return True

def ensure_cert(cert_path, key_path):
    if os.path.exists(cert_path) and os.path.exists(key_path): return True
    print('[HTTPS_TEST] Generating self-signed certificate...'); os.makedirs(os.path.dirname(cert_path), exist_ok=True)
    # Allow overriding openssl path via env
    openssl_exe = os.environ.get('OPENSSL_EXE') or shutil.which('openssl')
    # Common install paths (Shining Light default)
    if not openssl_exe:
        possible = [r'C:\\Program Files\\OpenSSL-Win64\\bin\\openssl.exe', r'C:\\Program Files\\OpenSSL-Win32\\bin\\openssl.exe']
        for p in possible:
            if os.path.exists(p):
                openssl_exe = p; break
    if openssl_exe:
        cmd=[openssl_exe,'req','-x509','-newkey','rsa:2048','-sha256','-days','365','-nodes','-subj','/CN=localhost','-keyout',key_path,'-out',cert_path]
        try:
            subprocess.check_call(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            print(f'[HTTPS_TEST] Certificate generated with {openssl_exe}')
            return True
        except Exception as e:
            print(f'[HTTPS_TEST] OpenSSL invocation failed ({e}); attempting Python fallback...')
    else:
        print('[HTTPS_TEST] OpenSSL not found in PATH; attempting Python fallback...')
    return _gen_cert_with_cryptography(cert_path, key_path)

def run_server(a):
    CONFIG.update({'host':a.host,'port':a.port,'require_token':a.require_token,'expected_token':a.expected_token,'verbose':a.verbose,'log_bodies':a.log_bodies})
    RUNTIME['fail_rate']=a.fail_rate; RUNTIME['delay_ms']=a.delay_ms
    httpd=HTTPServer((a.host,a.port), RequestHandler); proto='HTTP'
    if not a.plain_http:
        if not ensure_cert(a.cert,a.key):
            print('[HTTPS_TEST] Cert generation failed and plain HTTP disabled'); sys.exit(2)
        ctx=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER); ctx.load_cert_chain(a.cert,a.key)
        httpd.socket=ctx.wrap_socket(httpd.socket, server_side=True); proto='HTTPS'
    print(f'[HTTPS_TEST] {proto} server on {a.host}:{a.port}'); print(f"[HTTPS_TEST] Require token={a.require_token} fail_rate={RUNTIME['fail_rate']} delay_ms={RUNTIME['delay_ms']}")
    try: httpd.serve_forever()
    except KeyboardInterrupt: print('\n[HTTPS_TEST] Stopping')
    finally: httpd.server_close()

def parse_args():
    script_dir = os.path.abspath(os.path.dirname(__file__))
    default_cert = os.path.join(script_dir, 'test_certs', 'server.crt')
    default_key  = os.path.join(script_dir, 'test_certs', 'server.key')
    p=argparse.ArgumentParser(description='WiCAN HTTPS Test Server')
    p.add_argument('--host',default='127.0.0.1'); p.add_argument('--port',type=int,default=8443)
    p.add_argument('--cert',default=default_cert)
    p.add_argument('--key',default=default_key)
    p.add_argument('--require-token',action='store_true')
    # Accept both kebab-case and snake_case for convenience
    p.add_argument('--expected-token','--expected_token',dest='expected_token',default=None,
                   help='Expected token value when --require-token is set')
    p.add_argument('--delay-ms',type=int,default=0); p.add_argument('--fail-rate',type=float,default=0.0)
    p.add_argument('--plain-http',action='store_true'); p.add_argument('--verbose',action='store_true')
    p.add_argument('--log-bodies',action='store_true', help='Log raw POST bodies (first 512 bytes)')
    return p.parse_args()

if __name__=='__main__': run_server(parse_args())