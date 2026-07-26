#!/usr/bin/env python3
"""Build preview.html = the real web/index.html with the mock API shim
injected — clickable in any browser with NO device (and publishable as
a claude.ai artifact). Run extract_mocks.py first (or let this call it).
"""
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
# components live in the meatpi-components repo since 2026-07 (same
# search order as the root CMakeLists); the old in-repo path is the
# fallback for exotic checkouts
_CANDIDATES = [
    os.path.join(HERE, "..", "..", "..", "..", "wican-fw-dev",
                 "meatpi-components", "components", "web_ui_v2", "web",
                 "index.html"),
    os.path.join(HERE, "..", "..", "components", "meatpi", "components",
                 "web_ui_v2", "web", "index.html"),
    os.path.join(HERE, "..", "..", "components", "web_ui_v2", "web",
                 "index.html"),
]
INDEX = next((p for p in _CANDIDATES if os.path.exists(p)),
             _CANDIDATES[0])


def main():
    mocks_path = os.path.join(HERE, "mock_settings.json")
    if not os.path.exists(mocks_path) or "--fresh" in sys.argv:
        subprocess.run([sys.executable, os.path.join(HERE, "extract_mocks.py")],
                       check=True)
    mocks = open(mocks_path, encoding="utf-8").read()
    shim = open(os.path.join(HERE, "mock_api.js"), encoding="utf-8").read()
    html = open(INDEX, encoding="utf-8").read()

    inject = ("<script>window.__MOCK_SETTINGS__=" + mocks + ";</script>\n"
              "<script>" + shim + "</script>\n"
              "<script>document.addEventListener('DOMContentLoaded',()=>{"
              "const b=document.createElement('div');"
              "b.style.cssText='position:fixed;bottom:10px;right:10px;z-index:9999;"
              "background:#c1810b;color:#fff;font:600 11px system-ui;"
              "padding:4px 10px;border-radius:6px;opacity:.92';"
              "b.textContent='PREVIEW — mock data, no device';"
              "document.body.append(b);});</script>\n")

    anchor = '<meta charset="utf-8">'
    assert anchor in html, "injection anchor missing"
    out = html.replace(anchor, anchor + "\n" + inject, 1)
    dst = os.path.join(HERE, "preview.html")
    open(dst, "w", encoding="utf-8").write(out)
    print(f"preview.html written ({len(out)//1024} KB) -> {dst}")
    print("open directly in a browser, or publish as an artifact")


if __name__ == "__main__":
    main()
