#!/usr/bin/env python3
"""The way to the ECU simulator, whatever LAN the PC is on.

The simulator hands the PC a USB network link with fixed addresses: itself
192.168.8.1, the PC 192.168.8.2. 192.168.8.0/24 is also the factory LAN of
common routers (GL.iNet), and on such a LAN the PC has TWO interfaces in that
subnet: Windows then sends `http://192.168.8.1/...` to the router (the wired
interface has the better metric) and a bench reads the router's admin page
where it expects the simulator's JSON (2026-10-04, the day the bench PC moved
behind such a router).

install() makes every `urllib.request.urlopen()` of this process leave
through the simulator's own link when that collision exists: the request is
bound to the local address of the link (Windows sends a packet with a given
source address on the interface that owns it). Which local address that is
gets found by asking: the one behind which `/api/ecu/status` answers JSON.
Without a collision (one local address in the subnet, or none) nothing is
bound and nothing changes. No setting of the PC is touched.

  import sim_net; sim_net.install()          # bench_bus does it for its users
  python sim_net.py                          # what it found
"""
import http.client
import ipaddress
import json
import socket
import sys
import urllib.error
import urllib.request

SIM_HOST = "192.168.8.1"
PROBE_PATH = "/api/ecu/status"
PROBE_TIMEOUT_S = 3

_hosts = set()
_source = {}                    # simulator host -> local address, None = no bind
_installed = [False]


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


def install(host=SIM_HOST):
    """Idempotent. No I/O here: the link is looked for at the first request
    to `host`."""
    _hosts.add(host.replace("http://", "").split("/")[0].split(":")[0])
    if not _installed[0]:
        urllib.request.install_opener(urllib.request.build_opener(_SimHandler))
        _installed[0] = True


def main():
    host = sys.argv[1] if len(sys.argv) > 1 else SIM_HOST
    mine = local_addresses(host)
    print(f"local addresses in the subnet of {host}: {mine}")
    try:
        src = source_for(host)
    except urllib.error.URLError as e:
        print(e.reason)
        return 1
    print(f"requests to {host} leave from: {src or 'the default route'}")
    install(host)
    with urllib.request.urlopen(f"http://{host}{PROBE_PATH}",
                                timeout=PROBE_TIMEOUT_S) as r:
        print(r.read().decode("utf-8", errors="replace")[:200])
    return 0


if __name__ == "__main__":
    sys.exit(main())
