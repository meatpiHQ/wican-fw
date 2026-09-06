#!/usr/bin/env python3
"""Generate mock /api/settings data for the web-UI preview by parsing
every component's SETTINGS_* field table straight out of the C sources.

Output: mock_settings.json  { "<component>": {"values": {...}, "schema":
{"properties": {...}}} } — the same shapes GET /api/settings/<n> and
/<n>/schema serve, so the UI renders identically off-device.

Field-table macros parsed (settings_manager.h):
  SETTINGS_BOOL(name, def)             SETTINGS_INT(name, min, max, def)
  SETTINGS_STR(name, maxlen, def)      SETTINGS_STR_ENUM(name, "a,b", def)
  SETTINGS_*_REQ variants              SETTINGS_ARRAY(name, max, ITEMS,..)
  SETTINGS_ARRAY_ANY(name, max, SETTINGS_JSON([...]))
"""
import json
import os
import re
import sys

# MEATPI_COMPONENTS_PATH wins (same rule as the firmware build); the in-tree
# components/ folder is only a fallback for exotic checkouts
ROOT = os.environ.get("MEATPI_COMPONENTS_PATH") or os.path.join(os.path.dirname(__file__), "..", "..", "components")

RX_DESC = re.compile(r'\.name\s*=\s*"([a-z0-9_]+)"')
RX_BOOL = re.compile(r'SETTINGS_BOOL\s*\(\s*"([^"]+)"\s*,\s*(true|false)\s*\)')
RX_INT = re.compile(
    r'SETTINGS_INT(?:_REQ)?\s*\(\s*"([^"]+)"\s*,\s*(-?[\w* ()]+?)\s*,'
    r'\s*(-?[\w* ()]+?)\s*,\s*(-?[\w* ()]+?)\s*\)')
RX_STR = re.compile(
    r'SETTINGS_STR(?:_REQ)?\s*\(\s*"([^"]+)"\s*,\s*([\w() +-]+?)\s*,'
    r'(?:\s*[\w() +-]+?\s*,)?\s*"([^"]*)"\s*\)')
RX_ENUM = re.compile(
    r'SETTINGS_STR_ENUM\s*\(\s*"([^"]+)"\s*,\s*"([^"]+)"\s*,\s*"([^"]*)"\s*\)')
RX_ARRAY = re.compile(r'SETTINGS_ARRAY(?:_ANY)?\s*\(\s*"([^"]+)"')


def ceval(expr):
    """Best-effort constant eval for things like `16 * 1024` or macros."""
    expr = expr.strip()
    try:
        return int(eval(expr, {"__builtins__": {}}, {}))
    except Exception:
        return 0


def parse_file(path):
    src = open(path, encoding="utf-8", errors="replace").read()
    name_m = re.search(r'\.name\s*=\s*"([a-z0-9_]+)"', src)
    if not name_m:
        return None, None, None
    comp = name_m.group(1)
    props, values = {}, {}

    for m in RX_BOOL.finditer(src):
        props[m.group(1)] = {"type": "boolean"}
        values[m.group(1)] = m.group(2) == "true"
    for m in RX_ENUM.finditer(src):
        props[m.group(1)] = {"type": "string", "enum": m.group(2).split(",")}
        values[m.group(1)] = m.group(3)
    for m in RX_INT.finditer(src):
        k = m.group(1)
        if k in props:
            continue
        props[k] = {"type": "integer", "minimum": ceval(m.group(2)),
                    "maximum": ceval(m.group(3))}
        values[k] = ceval(m.group(4))
    for m in RX_STR.finditer(src):
        k = m.group(1)
        if k in props:
            continue
        props[k] = {"type": "string"}
        values[k] = m.group(3)
    for m in RX_ARRAY.finditer(src):
        k = m.group(1)
        if k in props:
            continue
        props[k] = {"type": "array", "items": {}}
        values[k] = []

    return comp, props, values


def main():
    out = {}
    for comp_dir in sorted(os.listdir(ROOT)):
        d = os.path.join(ROOT, comp_dir)
        if not os.path.isdir(d):
            continue
        for f in os.listdir(d):
            if not f.endswith("_settings.c"):
                continue
            comp, props, values = parse_file(os.path.join(d, f))
            if comp and props:
                out[comp] = {"values": values,
                             "schema": {"properties": props}}
    dst = os.path.join(os.path.dirname(__file__), "mock_settings.json")
    json.dump(out, open(dst, "w", encoding="utf-8"), indent=1)
    print(f"{len(out)} components -> {dst}")
    for c in sorted(out):
        print(f"  {c}: {len(out[c]['schema']['properties'])} fields")


if __name__ == "__main__":
    main()
