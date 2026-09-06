#!/usr/bin/env python3
"""Build preview.html = the real web/index.html with the mock API shim
injected (--min: minified first, exactly as the firmware embeds it) — clickable in any browser with NO device (and publishable as
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
# MEATPI_COMPONENTS_PATH wins (the firmware build uses it too) — the
# sibling-clone guess below can point at an OLDER checkout and silently
# preview stale UI (bit us 2026-09-06)
_ENV = os.environ.get("MEATPI_COMPONENTS_PATH")
_CANDIDATES = ([os.path.join(_ENV, "web_ui_v2", "web", "index.html")] if _ENV else []) + [
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
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(INDEX)), "tools"))   # rjsmin for the chunks
    if "--min" in sys.argv:
        # test what ships: the same minifier the firmware build runs (gzip_asset.py)
        sys.path.insert(0, os.path.dirname(os.path.dirname(INDEX)))
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(INDEX)), "tools"))
        from gzip_asset import minify_html
        before = len(html)
        html = minify_html(html)
        print(f"minified: {before} -> {len(html)} chars")
    print("source:", os.path.normpath(INDEX)
          + ("" if _ENV else "   (set MEATPI_COMPONENTS_PATH to be sure this is the checkout you build from)"))

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
    # on-demand page chunks (web/*.js next to index.html) are inlined after
    # the app script: jsdom cannot load external scripts, and the stub in
    # PAGES.<name> uses the chunk when it is already present
    chunks = ""
    for name in ("scripts.js",):
        cp = os.path.join(os.path.dirname(INDEX), name)
        if os.path.exists(cp):
            js = open(cp, encoding="utf-8").read()
            if "--min" in sys.argv:
                import rjsmin
                js = rjsmin.jsmin(js, keep_bang_comments=True)
            chunks += "<script>" + js + "</script>\n"
    assert "</body>" in out, "no </body> to inline the chunks before"
    out = out.replace("</body>", chunks + "</body>", 1)
    dst = os.path.join(HERE, "preview.html")
    open(dst, "w", encoding="utf-8").write(out)
    print(f"preview.html written ({len(out)//1024} KB) -> {dst}")
    print("open directly in a browser, or publish as an artifact")


if __name__ == "__main__":
    main()
