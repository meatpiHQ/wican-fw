#!/bin/bash
# Sync components + tools to rpi001 (~/wican), overlaying the split
# meatpi-components repo (TESTBENCH.md §4 recipe; test.ps1 host does the
# same automatically — this is the standalone/BenchBoard entry point).
set -e
REPO="$(cd "$(dirname "$0")/../.." && pwd)"
COMPS="$REPO/../../wican-fw-dev/meatpi-components"
TAR="${TEMP:-/tmp}/wican_sync.tar"

cd "$REPO"
tar -cf "$TAR" --exclude "*/build" --exclude "*/managed_components" \
    components tools
if [ -d "$COMPS/components" ]; then
    tar -rf "$TAR" --exclude "*/build" -C "$COMPS" components
else
    echo "WARN: meatpi-components overlay not found at $COMPS (skipping)"
fi
scp -q "$TAR" rpi001:/tmp/wican_sync.tar
ssh rpi001 "mkdir -p ~/wican && cd ~/wican && tar -xf /tmp/wican_sync.tar"
echo "SYNC PI DONE"
