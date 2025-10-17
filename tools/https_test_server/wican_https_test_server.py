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
import argparse, json, os, random, ssl, subprocess, sys, threading, time, shutil, base64
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse, parse_qs, parse_qsl, unquote


LOCK = threading.Lock()
STATS = {'requests':0,'total_ok':0,'total_errors':0,'path_hits':{},'start_time':time.time()}
RUNTIME = {'fail_rate':0.0,'delay_ms':0}
CONFIG = {}
MAX_BODY = 64*1024
ABRP_KEYS = { 'utc','soc','soh','speed','lat','lon','elevation','is_charging','power','ext_temp','batt_temp','car_model','current','voltage' }

def log(msg):
    if CONFIG.get('verbose'): print(f"[HTTPS_TEST] {msg}")

def log_request_url(method, path, query_string='', headers=None, body=None):
    if not CONFIG.get('verbose'): return
    
    # Construct the full URL
    scheme = 'https' if not CONFIG.get('plain_http', False) else 'http'
    host = CONFIG.get('host', '127.0.0.1')
    port = CONFIG.get('port', 8443)
    
    # Use localhost for 127.0.0.1 and 0.0.0.0 for better curl compatibility
    if host in ['127.0.0.1', '0.0.0.0']:
        host = 'localhost'
    
    url = f"{scheme}://{host}:{port}{path}"
    if query_string:
        url += f"?{query_string}"
    
    log(f"Received {method}: {url}")
    
    # Log curl command for easy reproduction
    curl_cmd = f"curl -X {method}"
    if not CONFIG.get('plain_http', False):
        curl_cmd += " -k"  # ignore SSL certificate issues
    
    # Add headers
    if headers:
        for header, value in headers.items():
            if header.lower() not in ['host', 'content-length', 'connection']:
                curl_cmd += f" -H \"{header}: {value}\""
    
    # Add body for POST requests
    if method == 'POST' and body:
        try:
            body_str = body.decode('utf-8') if isinstance(body, bytes) else str(body)
            if len(body_str) > 200:  # Truncate long bodies
                body_str = body_str[:200] + "..."
            curl_cmd += f" -d '{body_str}'"
        except:
            curl_cmd += " -d '[binary data]'"
    
    curl_cmd += f" \"{url}\""
    log(f"Curl equivalent: {curl_cmd}")

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
    def _auth_failed(self, reason='unauthorized'):
        self._send_json(401, {'error': reason})

    def _decode_basic(self, auth_header):
        # Returns (user, pass) or (None, None)
        try:
            if not auth_header or not auth_header.lower().startswith('basic '):
                return (None, None)
            b64 = auth_header.split(' ', 1)[1].strip()
            raw = base64.b64decode(b64).decode('utf-8')
            if ':' in raw:
                u, p = raw.split(':', 1)
                return (u, p)
        except Exception:
            return (None, None)
        return (None, None)

    def _check_auth(self, path, query_dict, headers_dict, body_bytes, webhook):
        # Bearer or token-in-query/body
        auth_hdr = headers_dict.get('Authorization') or headers_dict.get('authorization')
        bearer = None
        if auth_hdr and auth_hdr.lower().startswith('bearer '):
            bearer = auth_hdr.split(' ', 1)[1].strip()

        qtok = (query_dict.get('token') or [''])[0]
        form_token = ''
        # Parse form body only once if needed
        content_type = headers_dict.get('Content-Type', headers_dict.get('content-type', ''))
        if 'application/x-www-form-urlencoded' in content_type and body_bytes:
            try:
                form_token = dict(parse_qsl(body_bytes.decode('utf-8'))).get('token', '')
            except Exception:
                form_token = ''

        # Basic
        basic_user, basic_pass = self._decode_basic(auth_hdr)

        # API key header
        api_key_header_name = CONFIG.get('api_key_header_name')
        api_key_header_expected = CONFIG.get('expected_api_key_header')
        header_ok = True
        if api_key_header_name and api_key_header_expected is not None:
            # header names are case-insensitive
            hdr_val = None
            for k, v in headers_dict.items():
                if k.lower() == api_key_header_name.lower():
                    hdr_val = v; break
            header_ok = (hdr_val == api_key_header_expected)

        # API key query
        api_key_query_name = CONFIG.get('api_key_query_name')
        api_key_query_expected = CONFIG.get('expected_api_key_query')
        query_ok = True
        if api_key_query_name and api_key_query_expected is not None:
            qv = (query_dict.get(api_key_query_name) or [''])
            qv = qv[0] if qv else ''
            query_ok = (qv == api_key_query_expected)

        # Token requirement
        token_ok = True
        if CONFIG.get('require_token') and not webhook:
            exp = CONFIG.get('expected_token')
            cand = qtok or bearer or form_token
            token_ok = bool(exp and cand == exp)

        # Basic requirement
        basic_ok = True
        if CONFIG.get('require_basic'):
            bu = CONFIG.get('basic_user'); bp = CONFIG.get('basic_pass')
            basic_ok = bool(basic_user == bu and basic_pass == bp)

        # Final decision: all configured checks must pass
        if not token_ok:
            self._auth_failed('missing_or_invalid_token'); return False
        if not header_ok:
            self._auth_failed('missing_or_invalid_api_key_header'); return False
        if not query_ok:
            self._auth_failed('missing_or_invalid_api_key_query'); return False
        if not basic_ok:
            self._auth_failed('missing_or_invalid_basic_auth'); return False
        return True
    def _parse_json(self, raw):
        if not raw: return {}
        if 'application/json' in (self.headers.get('Content-Type','')):
            try: return json.loads(raw.decode() or '{}')
            except Exception: return {}
        return {}
    def _parse_form_data(self, raw):
        if not raw: return {}
        if 'application/x-www-form-urlencoded' in (self.headers.get('Content-Type','')):
            try: 
                form_str = raw.decode('utf-8')
                return dict(parse_qsl(form_str))
            except Exception: return {}
        return {}
    def do_GET(self):
        p = urlparse(self.path)
        log_request_url('GET', p.path, p.query, dict(self.headers))
        if p.path=='/metrics':
            with LOCK: data=dict(STATS); data['uptime_s']=round(time.time()-STATS['start_time'],2)
            self._send_json(200,data); update_stats(p.path,True); return
        if p.path=='/config':
            cfg={k:v for k,v in CONFIG.items() if k!='expected_token'}; self._send_json(200,cfg); update_stats(p.path,True); return
        if p.path=='/1/tlm/send':
            qs=parse_qs(p.query); tlm_raw=(qs.get('tlm') or [''])[0]
            if self._maybe_fail_or_delay(): update_stats(p.path,False); return
            if not self._check_auth(p.path, qs, dict(self.headers), b'', False): update_stats(p.path,False); return
            try: telemetry=json.loads(unquote(tlm_raw)) if tlm_raw else {}
            except Exception: telemetry={}
            self._send_json(200, {'status':'ok','received':telemetry,'utc':int(time.time())}); update_stats(p.path,True); return
        self._send_json(404, {'error':'not found'}); update_stats(p.path,False)
    def do_POST(self):
        p = urlparse(self.path)
        raw = self._read_body()
        if raw is None:
            update_stats(p.path, False)
            return
        log_request_url('POST', p.path, p.query, dict(self.headers), raw)
        if CONFIG.get('log_bodies') or CONFIG.get('log_bodies_full'):
            full = CONFIG.get('log_bodies_full')
            body_text = ''
            try:
                # Attempt pretty JSON if content-type indicates JSON
                ctype = (self.headers.get('Content-Type','') or '').lower()
                if 'application/json' in ctype:
                    try:
                        parsed = json.loads(raw.decode('utf-8', errors='replace') or '{}')
                        body_text = json.dumps(parsed, indent=2, sort_keys=True)
                    except Exception:
                        body_text = raw.decode('utf-8', errors='replace')
                else:
                    body_text = raw.decode('utf-8', errors='replace')
            except Exception:
                body_text = str(raw[:64])

            if not full and len(body_text) > 512:
                display = body_text[:512] + ' ...'
            else:
                display = body_text
            log(f"BODY {p.path} ({len(raw)} bytes){' FULL' if full else ''}:\n{display}")

        qs = parse_qs(p.query)
        webhook = p.path.startswith('/api/webhook/')

        # Parse form data for token extraction (for ABRP tlm)
        form_data = self._parse_form_data(raw)

        if p.path == '/inject_error':
            body = self._parse_json(raw)
            changed = {}
            if 'fail_rate' in body:
                try:
                    fr = float(body['fail_rate'])
                except ValueError:
                    fr = None
                if fr is not None and 0 <= fr <= 1:
                    RUNTIME['fail_rate'] = fr
                    changed['fail_rate'] = fr
            if 'delay_ms' in body:
                try:
                    d = int(body['delay_ms'])
                except ValueError:
                    d = None
                if d is not None and 0 <= d <= 60000:
                    RUNTIME['delay_ms'] = d
                    changed['delay_ms'] = d
            self._send_json(200, {'updated': changed, 'current': RUNTIME})
            update_stats(p.path, True)
            return

        if self._maybe_fail_or_delay():
            update_stats(p.path, False)
            return

        # Unified auth checks
        if not self._check_auth(p.path, qs, dict(self.headers), raw, webhook):
            update_stats(p.path, False)
            return

        if p.path == '/1/tlm/send':
            telemetry = {}
            # Handle form data (ABRP format)
            if form_data and 'tlm' in form_data:
                try:
                    # Parse URL-encoded telemetry JSON
                    tlm_json = unquote(form_data['tlm'])
                    telemetry = json.loads(tlm_json)
                    log(f"Parsed form telemetry: {telemetry}")
                except Exception as e:
                    log(f"Failed to parse form telemetry: {e}")
                    telemetry = {}
            else:
                # Handle JSON body (legacy format)
                body = self._parse_json(raw)
                telemetry = body.get('tlm') if isinstance(body.get('tlm'), dict) else {k: v for k, v in body.items() if k in ABRP_KEYS}

            if 'utc' not in telemetry:
                telemetry['utc'] = int(time.time())
            self._send_json(200, {'status': 'ok', 'received': telemetry, 'utc': int(time.time())})
            update_stats(p.path, True)
            return

        if p.path.startswith('/api/events/'):
            event = p.path.split('/api/events/', 1)[1] or 'unknown'
            payload = self._parse_json(raw)
            self._send_json(200, {'event': event, 'received': payload, 'ts': int(time.time())})
            update_stats(p.path, True)
            return

        if webhook:
            hook = p.path.split('/api/webhook/', 1)[1]
            payload = self._parse_json(raw)
            self._send_json(200, {'webhook': hook, 'received': payload, 'ts': int(time.time())})
            update_stats(p.path, True)
            return

        if p.path == '/generic':
            payload = self._parse_json(raw)
            self._send_json(200, {'ok': True, 'received': payload, 'ts': int(time.time())})
            update_stats(p.path, True)
            return

        self._send_json(404, {'error': 'not found'})
        update_stats(p.path, False)

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
    CONFIG.update({
        'host': a.host,
        'port': a.port,
        'plain_http': a.plain_http,
        'require_token': a.require_token,
        'expected_token': a.expected_token,
        'require_basic': a.require_basic,
        'basic_user': a.basic_user,
        'basic_pass': a.basic_pass,
        'api_key_header_name': a.api_key_header_name,
        'expected_api_key_header': a.expected_api_key_header,
        'api_key_query_name': a.api_key_query_name,
        'expected_api_key_query': a.expected_api_key_query,
        'verbose': a.verbose,
        'log_bodies': a.log_bodies,
        'log_bodies_full': a.log_bodies_full,
    })
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
    # Basic auth controls
    p.add_argument('--require-basic', action='store_true', help='Require HTTP Basic auth')
    p.add_argument('--basic-user', default=None, help='Expected Basic auth username')
    p.add_argument('--basic-pass', default=None, help='Expected Basic auth password')
    # API key controls
    p.add_argument('--api-key-header-name', default=None, help='Header name to check for API key (e.g., x-api-key)')
    p.add_argument('--expected-api-key-header', default=None, help='Expected API key header value')
    p.add_argument('--api-key-query-name', default=None, help='Query parameter name for API key (e.g., api_key)')
    p.add_argument('--expected-api-key-query', default=None, help='Expected API key query value')
    p.add_argument('--delay-ms',type=int,default=0); p.add_argument('--fail-rate',type=float,default=0.0)
    p.add_argument('--plain-http',action='store_true'); p.add_argument('--verbose',action='store_true')
    p.add_argument('--log-bodies',action='store_true', help='Log raw POST bodies (first 512 bytes, pretty-prints JSON)')
    p.add_argument('--log-bodies-full',action='store_true', help='Log complete POST bodies without truncation (pretty-prints JSON)')
    return p.parse_args()

if __name__=='__main__': run_server(parse_args())