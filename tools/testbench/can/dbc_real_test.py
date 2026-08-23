#!/usr/bin/env python3
"""Real-world DBC regression: upload the fixtures (opendbc, see
fixtures/dbc/README.md) to the DUT and cross-check its parsed signal
tables FIELD-BY-FIELD (start/len/order/sign/factor/offset per signal,
keyed by message id + name) against an independent reference parser
built from the DBC spec. Catches parser drift that synthetic fixtures
can't (comment-line noise, orphan pseudo-messages, real formatting).

Pure HTTP — runs from the Pi or the PC, stdlib only:
  python3 dbc_real_test.py [dut_ip]
Cleans its uploads off the DUT afterwards. Prints DBC REAL PASS/FAIL.
"""
import json
import os
import re
import sys
import urllib.request
import urllib.error

DUT = sys.argv[1] if len(sys.argv) > 1 else "192.168.82.1"
BASE = "http://" + DUT
HERE = os.path.dirname(os.path.abspath(__file__))
FIXTURES = [
    ("bmw", "bmw_e9x_e8x.dbc"),
    ("tesla", "tesla_model3_party.dbc"),
    ("ford", "ford_lincoln_base_pt.dbc"),
    ("j1939", "j1939_database.dbc"),   # extended 29-bit ids
]

# device caps (autopid_private.h) — the reference applies the same
MSGS_CAP, SIGS_CAP = 400, 3000
NAME_LEN, UNIT_LEN = 32, 15

SG = re.compile(r'^\s*SG_\s+(\w+)\s*(M|m\d+)?\s*:\s*(\d+)\|(\d+)@([01])'
                r'([+-])\s*\(([^,]+),([^)]+)\)\s*\[([^|]*)\|([^\]]*)\]'
                r'\s*"([^"]*)"')
BO = re.compile(r'^BO_\s+(\d+)\s+(\w+)\s*:\s*(\d+)')

fails = []


def check(name, ok, detail=""):
    print(("PASS" if ok else "FAIL") + ": " + name
          + ("  ({})".format(detail) if detail else ""))
    if not ok:
        fails.append(name)


def api(path, method="GET", body=None, raw=None):
    data = raw if raw is not None else (
        json.dumps(body).encode() if body is not None else None)
    req = urllib.request.Request(BASE + path, data=data, method=method,
                                 headers={"Content-Type":
                                          "application/octet-stream"
                                          if raw is not None
                                          else "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            t = r.read().decode()
            return r.status, (json.loads(t)
                              if t.strip().startswith(("{", "[")) else t)
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


def ref_parse(path):
    """Reference implementation of the device's documented subset."""
    msgs, sigs = [], []
    cur = None
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            m = BO.match(line)
            if m:
                mid = int(m.group(1))
                if (mid & 0x40000000) or not m.group(2):
                    cur = None      # Vector orphan pseudo-message
                elif len(msgs) < MSGS_CAP:
                    msgs.append({"id": mid & 0x1FFFFFFF,
                                 "name": m.group(2)[:NAME_LEN]})
                    cur = len(msgs) - 1
                else:
                    cur = None      # over cap: keep what fits
                continue
            s = SG.match(line)
            if s and cur is not None and len(sigs) < SIGS_CAP:
                sigs.append({
                    "msg_id": msgs[cur]["id"],
                    "name": s.group(1)[:NAME_LEN],
                    "start": int(s.group(3)), "len": int(s.group(4)),
                    "order": "intel" if s.group(5) == "1"
                             else "motorola",
                    "signed": s.group(6) == "-",
                    "factor": float(s.group(7)),
                    "offset": float(s.group(8)),
                })
    return msgs, sigs


def fetch_all(db):
    items, off = [], 0
    while True:
        s, r = api("/api/autopid/dbc/signals?db={}&limit=100&offset={}"
                   .format(db, off))
        if s != 200:
            return None, 0
        items += r["items"]
        off += len(r["items"])
        if off >= r["total"] or not r["items"]:
            return items, r["total"]


def main():
    for i, (name, fname) in enumerate(FIXTURES, 1):
        print("fixture {}/{}: {} — upload + full signal diff…".format(
            i, len(FIXTURES), fname), flush=True)
        path = os.path.join(HERE, "..", "fixtures", "dbc", fname)

        if not os.path.exists(path):
            check(name + " fixture present", False, path)
            continue

        raw = open(path, "rb").read()
        code, r = api("/api/autopid/dbc?name=" + name, "POST", raw=raw)
        rm, rs = ref_parse(path)
        check(name + " upload + counts",
              code == 200 and r.get("messages") == len(rm)
              and r.get("signals") == len(rs),
              "dev={} ref=({},{})".format(
                  r if code != 200
                  else (r["messages"], r["signals"]), len(rm), len(rs)))
        if code != 200:
            continue

        dev, total = fetch_all(name)

        if dev is None:
            check(name + " signal fetch", False)
            continue

        ref_by_key = {}

        for x in rs:
            ref_by_key.setdefault((x["msg_id"], x["name"]), []).append(x)

        mism = 0
        unsup = {}

        for d in dev:
            if not d["supported"]:
                reason = d.get("reason", "?")
                unsup[reason] = unsup.get(reason, 0) + 1

            hit = any(
                c["start"] == d["start"] and c["len"] == d["len"] and
                c["order"] == d["order"] and c["signed"] == d["signed"]
                and abs(c["factor"] - d["factor"]) < 1e-9 and
                abs(c["offset"] - d["offset"]) < 1e-9
                for c in ref_by_key.get((d["id"], d["name"][:NAME_LEN]),
                                        []))

            if not hit:
                mism += 1

                if mism <= 3:
                    print("  MISMATCH:", d["name"], hex(d["id"]),
                          d["start"], d["len"], d["order"], d["signed"])

        sup = sum(1 for d in dev if d["supported"])

        check(name + " fields exact ({} signals)".format(total),
              mism == 0, "{} mismatches".format(mism))
        print("  {}: supported {}/{}, unsupported: {}".format(
            name, sup, total, unsup or "none"))
        print("PROGRESS {}/{}".format(i, len(FIXTURES) + 1), flush=True)

    # extended-id spot check: J1939 EEC1 is BO_ 2364540158 = 0x8CF004FE
    # (bit 31 = extended flag, source address 0xFE) — the device must
    # surface it under the MASKED 29-bit id
    want_id = 2364540158 & 0x1FFFFFFF
    code, r = api("/api/autopid/dbc/signals?db=j1939&q=EngineSpeed"
                  "&limit=5")
    hits = [x for x in r.get("items", [])
            if x["name"] == "EngineSpeed"] if code == 200 else []
    check("j1939 extended id masked (EEC1 0x%X)" % want_id,
          len(hits) == 1 and hits[0]["id"] == want_id
          and hits[0]["supported"],
          hits[0] if hits else r)

    # cleanup
    for name, _ in FIXTURES:
        api("/api/autopid/dbc?name=" + name, "DELETE")

    code, r = api("/api/autopid/dbc")
    leftover = [d["name"] for d in r.get("dbcs", [])
                if d["name"] in dict(FIXTURES)]
    check("cleanup", leftover == [], leftover)
    print("PROGRESS {}/{}".format(len(FIXTURES) + 1, len(FIXTURES) + 1),
          flush=True)

    if fails:
        print("DBC REAL FAIL:", ", ".join(fails))
        sys.exit(1)

    print("DBC REAL PASS")


if __name__ == "__main__":
    main()
