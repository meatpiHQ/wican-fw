#!/usr/bin/env python3
"""Where the ECU simulator is, and the way to it, whatever LAN the PC is on.

The simulator hands the PC a USB network link with fixed addresses. ECU sim
v2 (the bench since 2026-11-09) sits at http://10.8.8.1 on this bench (the
PC is 10.8.8.2); the first-generation simulator box used 192.168.8.1, which
is also the factory LAN of common routers (GL.iNet): on such a LAN the PC had
TWO interfaces in that subnet and Windows sent `http://192.168.8.1/...` to the
router, so a bench read the router's admin page where it expected JSON
(2026-10-04, the day the bench PC moved behind such a router).

Two jobs, no setting of the PC touched:

  sim_url()   the simulator's base URL: the WICAN_SIM_URL environment
              variable when set, else the first of CANDIDATES whose
              `/api/info` says it is the ECU simulator (10.8.8.1 first, then
              192.168.8.1), else CANDIDATES[0] so the failure names a host.
              Cached per process; "auto" / "" / None mean "find it".
  install()   makes every `urllib.request.urlopen()` of this process leave
              through the simulator's own link when the subnet collision
              exists: the request is bound to the local address of the link
              (Windows sends a packet with a given source address on the
              interface that owns it). Which local address that is gets
              found by asking: the one behind which `/api/ecu/status`
              answers JSON. Without a collision nothing is bound.

  import sim_net; sim_net.install()          # bench_bus does it for its users
  SIM = sim_net.sim_url(arg("--sim", "auto"))
  python sim_net.py                          # what it found
  python sim_net.py --url                    # the URL alone (for scripts)
"""
import http.client
import ipaddress
import json
import os
import socket
import sys
import urllib.error
import urllib.request

#: the product default (the first-generation box) kept for callers that
#: import it; the bench's ECU sim v2 answers at CANDIDATES[0]
SIM_HOST = "192.168.8.1"
CANDIDATES = ("http://10.8.8.1", "http://192.168.8.1")
ENV_URL = "WICAN_SIM_URL"
PROBE_PATH = "/api/ecu/status"
PROBE_TIMEOUT_S = 3
INFO_TIMEOUT_S = 2.5
AUTO = ("", "auto", None)

_hosts = set()
_source = {}                    # simulator host -> local address, None = no bind
_installed = [False]
_found = {}                     # "url" -> the resolved base URL


def _host_of(url):
    return url.replace("http://", "").replace("https://", "").split("/")[0].split(":")[0]


def local_addresses(host):
    """The PC's own IPv4 addresses in the /24 of `host`."""
    try:
        net = ipaddress.ip_network(host + "/24", strict=False)
        mine = socket.gethostbyname_ex(socket.gethostname())[2]
    except (OSError, ValueError):
        return []
    return [a for a in mine if ipaddress.ip_address(a) in net]


def answers_as_simulator(host, source, port=80):
    """True when the simulator's status route answers JSON from behind the
    local address `source`."""
    conn = http.client.HTTPConnection(host, port, timeout=PROBE_TIMEOUT_S,
                                      source_address=(source, 0))
    try:
        conn.request("GET", PROBE_PATH)
        r = conn.getresponse()
        body = r.read()
        return r.status == 200 and isinstance(json.loads(body), dict)
    except (OSError, ValueError, http.client.HTTPException):
        return False
    finally:
        conn.close()


def source_for(host):
    """The local address requests to `host` must leave from; None when no
    binding is needed. Raises URLError while a collision exists and the
    simulator answers behind none of the candidates (it reboots after every
    settings submit): the request must not fall through to the other device
    that owns the address."""
    if host in _source:
        return _source[host]
    mine = local_addresses(host)
    if len(mine) <= 1:
        _source[host] = None            # no collision: the plain route
        return None
    for a in mine:
        if answers_as_simulator(host, a):
            _source[host] = a
            print(f"sim_net: {host} is reached from {a} (another interface "
                  f"shares the subnet: {', '.join(x for x in mine if x != a)})",
                  flush=True)
            return a
    raise urllib.error.URLError(
        f"sim_net: the simulator at {host} answers behind none of "
        f"{', '.join(mine)}")


class _SimHandler(urllib.request.HTTPHandler):
    """Requests to a simulator host leave from the simulator's link."""

    def http_open(self, req):
        host = req.host.split(":")[0]
        if host in _hosts:
            src = source_for(host)
            if src is not None:
                return self.do_open(http.client.HTTPConnection, req,
                                    source_address=(src, 0))
        return super().http_open(req)


def install(host=None):
    """Idempotent. No I/O here: the link is looked for at the first request
    to a simulator host. Every candidate host is covered; `host` adds one."""
    for c in CANDIDATES:
        _hosts.add(_host_of(c))
    if host:
        _hosts.add(_host_of(host))
    env = os.environ.get(ENV_URL)
    if env:
        _hosts.add(_host_of(env))
    if not _installed[0]:
        urllib.request.install_opener(urllib.request.build_opener(_SimHandler))
        _installed[0] = True


def _info(base):
    """`/api/info` of `base` as a dict, or None (through the bound opener)."""
    install(base)
    try:
        with urllib.request.urlopen(base + "/api/info", timeout=INFO_TIMEOUT_S) as r:
            d = json.loads(r.read().decode("utf-8", errors="replace"))
        return d if isinstance(d, dict) else None
    except (OSError, ValueError, http.client.HTTPException):
        return None


def is_simulator(info):
    """The simulator's own `/api/info`: the product line names it (a router's
    page or a WiCAN under test does not)."""
    return isinstance(info, dict) and "ECU Simulator" in str(info.get("product", ""))


def sim_url(explicit=None):
    """The simulator's base URL (no trailing slash). See the module doc."""
    if explicit not in AUTO:
        return str(explicit).rstrip("/")
    if "url" in _found:
        return _found["url"]
    env = os.environ.get(ENV_URL)
    if env:
        _found["url"] = env.rstrip("/")
        return _found["url"]
    for c in CANDIDATES:
        if is_simulator(_info(c)):
            _found["url"] = c
            return c
    return CANDIDATES[0]            # not found: the failure will name this host


def sim_host(explicit=None):
    """The host part of sim_url(explicit) (for scripts that build URLs)."""
    return _host_of(sim_url(explicit))


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    want_url_only = "--url" in sys.argv
    if want_url_only:
        print(sim_url(args[0] if args else None))
        return 0
    base = sim_url(args[0] if args else None)
    host = _host_of(base)
    mine = local_addresses(host)
    print(f"simulator: {base}" + ("" if "url" in _found or args else " (NOT FOUND among "
                                   f"{', '.join(CANDIDATES)}; set {ENV_URL} or pass the URL)"))
    print(f"local addresses in the subnet of {host}: {mine}")
    try:
        src = source_for(host)
    except urllib.error.URLError as e:
        print(e.reason)
        return 1
    print(f"requests to {host} leave from: {src or 'the default route'}")
    install(base)
    info = _info(base)
    if info:
        print("info:", json.dumps({k: info.get(k) for k in ("product", "hw", "role",
                                                             "device_id", "fw_version")}))
    with urllib.request.urlopen(f"{base}{PROBE_PATH}", timeout=PROBE_TIMEOUT_S) as r:
        print(r.read().decode("utf-8", errors="replace")[:200])
    return 0


if __name__ == "__main__":
    sys.exit(main())
