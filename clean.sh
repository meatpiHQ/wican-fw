#!/usr/bin/env bash
# Fully clean firmware build dir(s): ./clean {v300|custom|all}

set -euo pipefail
cd "$(dirname "$0")"

usage() {
    echo "usage: ${0##*/} {v300|custom|all}" >&2
    exit 2
}
[ $# -eq 1 ] || usage

if [ -z "${IDF_PATH:-}" ]; then
    echo "error: IDF_PATH not set. source esp-idf/export.sh first" >&2
    exit 1
fi

# NOTE: the generated sdkconfig lives in each build dir, so this also wipes
# any menuconfig tweaks not yet promoted into the sdkconfig.defaults* files
# (idf.py -B <dir> save-defconfig first if you want to keep them).
clean_one() {
    local dir="build.$1"
    if [ -d "$dir" ]; then
        echo "=== cleaning $dir ==="
        idf.py -B "$dir" fullclean
        rmdir "$dir" 2>/dev/null || true
    else
        echo "=== $dir not present, skipping ==="
    fi
}

case "$1" in
    v300|custom)
        clean_one "$1"
        ;;
    all)
        clean_one v300
        clean_one custom
        ;;
    *)
        usage
        ;;
esac
