# WiCAN HTTPS Test Server

Local test server to emulate external HTTPS/HTTP endpoints the firmware posts to (ABRP, Home Assistant events & webhooks, generic JSON collectors).

## Features
- Endpoints:
  - `GET /metrics` – runtime stats
  - `GET /config` – current server config (no secret token value)
  - `GET /1/tlm/send` – ABRP style (token & URL encoded `tlm` JSON in query)
  - `POST /1/tlm/send` – ABRP POST variant (token may be in query; body JSON either flat ABRP keys or `{ "tlm": { ... } }`)
  - `POST /api/events/<event>` – Home Assistant style event (Bearer token header if enforced)
  - `POST /api/webhook/<id>` – Home Assistant webhook (token bypassed)
  - `POST /generic` – generic JSON echo
  - `POST /inject_error` – adjust `fail_rate` / `delay_ms` at runtime

- Token enforcement (`--require-token` + `--expected-token` or `--expected_token`)
- Random failure injection (`--fail-rate` 0..1)
- Artificial response delay (`--delay-ms`)
- TLS with self‑signed cert (auto generated) or plain HTTP fallback (`--plain-http`)
- Fallback Python `cryptography` cert generation if OpenSSL missing
- Verbose logging (`--verbose`)

## Installation / Prerequisites
Python 3.9+.

Optional for faster startup (if OpenSSL not installed):
```
pip install cryptography
```

On Windows, to enable OpenSSL auto-cert: install OpenSSL (e.g. `winget install ShiningLight.OpenSSL.Light`).

## Running (TLS)
From repo root (or directly inside folder):
```
cd tools/https_test_server
python wican_https_test_server.py --host 0.0.0.0 --port 8443 --require-token --expected-token SECRET --verbose
```
Output example:
```
[HTTPS_TEST] Generating self-signed certificate...
[HTTPS_TEST] Certificate generated with C:\\Program Files\\OpenSSL-Win64\\bin\\openssl.exe
[HTTPS_TEST] HTTPS server on 0.0.0.0:8443
[HTTPS_TEST] Require token=True fail_rate=0.0 delay_ms=0
```

## Running (Plain HTTP)
```
python wican_https_test_server.py --host 0.0.0.0 --port 8080 --plain-http --verbose
```

## Command Line Options
| Option | Description |
|--------|-------------|
| `--host` | Bind address (`0.0.0.0` to accept LAN) |
| `--port` | Listen port (default 8443) |
| `--cert`, `--key` | Paths for cert/key (auto if absent) |
| `--plain-http` | Disable TLS (serve HTTP) |
| `--require-token` | Enforce token auth for non-webhook endpoints |
| `--expected-token / --expected_token` | Token value expected (paired with `--require-token`) |
| `--delay-ms` | Artificial delay before responses |
| `--fail-rate` | Probability (0..1) of injected 500 error |
| `--verbose` | Extra logging & http.server logs |

## Endpoints & Auth Rules
| Endpoint Pattern | Method(s) | Token Required* | Notes |
|------------------|----------|-----------------|-------|
| `/1/tlm/send` | GET/POST | Yes (if enforced) | ABRP style telemetry `token` query + `tlm` JSON |
| `/api/events/<event>` | POST | Yes (if enforced) | Bearer token in `Authorization` header |
| `/api/webhook/<id>` | POST | No | Token ignored/bypassed |
| `/generic` | POST | Yes (if enforced) | Any JSON accepted |
| `/metrics` | GET | No | Stats JSON |
| `/config` | GET | No | Running config |
| `/inject_error` | POST | No | Adjust delay/fail rate |

*If `--require-token` is not used, all endpoints accept requests without token.

## Example cURL Requests
Metrics:
```
curl -k https://localhost:8443/metrics
```
ABRP GET style telemetry:
```
curl -k "https://localhost:8443/1/tlm/send?token=SECRET&tlm=%7B%22soc%22:55.2,%22speed%22:10%7D"
```
Generic POST:
```
curl -k -H "Authorization: Bearer SECRET" -H "Content-Type: application/json" \
     -d '{"speed":72.3,"soc":54.1}' https://localhost:8443/generic
```
Home Assistant event simulation:
```
curl -k -H "Authorization: Bearer SECRET" -H "Content-Type: application/json" \
     -d '{"soc":54.1}' https://localhost:8443/api/events/wican_update
```
Webhook (no token):
```
curl -k -H "Content-Type: application/json" -d '{"ping":1}' https://localhost:8443/api/webhook/test123
```
Adjust fault injection:
```
curl -k -H "Content-Type: application/json" -d '{"fail_rate":0.2,"delay_ms":300}' https://localhost:8443/inject_error
```

## Using With WiCAN Firmware
Firmware Destination examples:
- ABRP simulation: `https://<PC_IP>:8443/1/tlm/send`
- Generic: `https://<PC_IP>:8443/generic`
- HA event: `https://<PC_IP>:8443/api/events/wican_update`
- Webhook: `https://<PC_IP>:8443/api/webhook/myhook`

Set API Token field in firmware to `SECRET` (or your chosen value) for token‑required endpoints.

If using self‑signed cert:
- Enable "skip CN" / insecure trust option on the device.
- Or export `test_certs/server.crt` and add to trust store.

## WSL / Dual Environment Notes
- If server runs in Windows PowerShell and you curl from WSL, use Windows IP (e.g. from `ipconfig`) instead of localhost if connection refused.
- Windows Firewall may block inbound LAN requests; create an inbound rule for `python.exe` on the chosen port.

## Troubleshooting
| Symptom | Cause | Fix |
|---------|-------|-----|
| OpenSSL failed & no fallback | cryptography not installed | `pip install cryptography` or install OpenSSL |
| Connection refused | Server not running or firewall | Start server; add firewall rule; confirm with `netstat -ano` |
| 401 unauthorized | Missing/incorrect token | Add `?token=SECRET` (ABRP GET) or Bearer header |
| Random 500 errors | Injected failure rate | Reduce via `/inject_error` or start with `--fail-rate 0` |

## License
GPL (inherits project license).
