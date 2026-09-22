#!/usr/bin/env python3
"""Internal-DRAM static map from the linker map: which component owns which
.dram0.bss / .dram0.data bytes, symbol by symbol, so PSRAM candidates
(big buffers that no ISR / flash-cache-off path touches) can be picked.

  python tools/ram_map.py [--map build/wican-fw.map] [--min 256] [--top 60] [--component autopid]
"""
import argparse
import re
from collections import defaultdict

OUT_SEC_RE = re.compile(r"^(\.[A-Za-z0-9_.]+)\s+0x[0-9a-f]+\s+0x[0-9a-f]+")   # an output section header
IN_SEC_RE = re.compile(r"^ (\.(?:bss|data|sbss|sdata|tbss|tdata)(?:\.\S+)?|COMMON)(?:\s+(0x[0-9a-f]+)\s+(0x[0-9a-f]+)\s+(\S+))?$")
ADDR_RE = re.compile(r"^\s+(0x[0-9a-f]+)\s+(0x[0-9a-f]+)\s+(\S+)$")
WANT = {".dram0.bss", ".dram0.data"}


def component_of(obj):
    # esp-idf/autopid/libautopid.a(autopid.c.obj) -> autopid ;  .../esp_wifi/lib/esp32s3/libpp.a(...) -> libpp
    m = re.search(r"esp-idf/([^/]+)/lib", obj)
    if m:
        return m.group(1)
    m = re.search(r"/(lib[^/.]+)\.a", obj)
    if m:
        return m.group(1)
    return obj.split("/")[-1][:30]


def parse(path):
    rows = []  # (size, out_section, symbol, component, object)
    cur = None
    pending = None
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.rstrip("\r\n")
            m = OUT_SEC_RE.match(line)
            if m and not line.startswith(" "):
                cur = m.group(1)
                pending = None
                continue
            if cur not in WANT:
                continue
            m = IN_SEC_RE.match(line)
            if m:
                name, addr, size, obj = m.groups()
                sym = name.split(".", 2)[2] if name.count(".") >= 2 else name.lstrip(".")
                if size is not None:
                    rows.append((int(size, 16), cur, sym, component_of(obj), obj))
                    pending = None
                else:
                    pending = sym
                continue
            if pending is not None:
                m = ADDR_RE.match(line)
                if m:
                    addr, size, obj = m.groups()
                    rows.append((int(size, 16), cur, pending, component_of(obj), obj))
                pending = None
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--map", default="build/wican-fw.map")
    ap.add_argument("--min", type=int, default=256)
    ap.add_argument("--top", type=int, default=60)
    ap.add_argument("--component", default="")
    a = ap.parse_args()
    rows = parse(a.map)
    rows = [r for r in rows if r[0] > 0]
    per = defaultdict(int)
    for size, sec, sym, comp, obj in rows:
        per[comp] += size
    total = sum(per.values())
    print(f"internal static DRAM (.dram0.bss + .dram0.data) attributed: {total} B in {len(rows)} sections\n")
    print(f"{'component':28s} {'bytes':>7s}  share")
    for comp, size in sorted(per.items(), key=lambda kv: -kv[1])[:40]:
        print(f"{comp:28s} {size:7d}  {100.0 * size / total:4.1f}%")
    print()
    sel = [r for r in rows if r[0] >= a.min and (not a.component or r[3] == a.component)]
    sel.sort(reverse=True)
    print(f"{'bytes':>7s} {'section':11s} {'component':22s} symbol  (object)")
    for size, sec, sym, comp, obj in sel[:a.top]:
        print(f"{size:7d} {sec[7:]:11s} {comp:22s} {sym}  ({obj.split('(')[-1].rstrip(')')})")


if __name__ == "__main__":
    main()
