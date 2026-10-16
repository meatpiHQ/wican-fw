#!/usr/bin/env bash
# The PURE host suites without the bench Pi (2026-10-16, issues #985 to #989:
# the rig was offline): a suite's test_main.c and its pure sources compiled
# with the system gcc + Unity, ASan and UBSan on, no IDF build. Runs from
# WSL (or any Linux with gcc) against the Windows checkout; the IDF linux
# target (run_host_tests.sh on the Pi) stays the reference run.
#
#   bash tools/testbench/host_gcc_wsl.sh vpn_manager
#   bash tools/testbench/host_gcc_wsl.sh config_backup
#
# Only suites listed below are known to be IDF-free; add a case to extend.
# Paths: MEATPI_COMPONENTS_PATH (Windows or Linux form) names the live
# components tree, else the sibling checkout; IDF_PATH the ESP-IDF tree
# (Unity + esp_err.h), default C:\esp\v6.0.2\esp-idf; cJSON comes from this
# repo's managed_components.
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HERE/../.." && pwd)"

to_linux() {
    case "$1" in
        [A-Za-z]:*) local d="${1:0:1}"; echo "/mnt/${d,,}${1:2}" | tr '\\' '/' ;;
        *) echo "$1" ;;
    esac
}

COMP="$(to_linux "${MEATPI_COMPONENTS_PATH:-$REPO/../../components/meatpi-components/components}")"
IDF="$(to_linux "${IDF_PATH:-C:\\esp\\v6.0.2\\esp-idf}")"
CJSON="$REPO/managed_components/espressif__cjson/cJSON"
OUT="${TMPDIR:-/tmp}/wican_host_gcc"
mkdir -p "$OUT/inc"
: > "$OUT/inc/sdkconfig.h"
cat > "$OUT/main_shim.c" <<'EOF'
void app_main(void);
int main(void)
{
    app_main();
    return 0;
}
EOF

case "${1:-}" in
    vpn_manager)
        SRCS="$COMP/vpn_manager/host_test/main/test_main.c $COMP/vpn_manager/vpn_manager_check.c $COMP/ip4_routes/ip4_routes_core.c"
        INC="-I$COMP/vpn_manager/include -I$COMP/vpn_manager -I$COMP/ip4_routes/include -I$COMP/ip4_routes" ;;
    config_backup)
        SRCS="$COMP/config_backup/host_test/main/test_main.c $COMP/config_backup/config_backup_core.c $COMP/config_backup/config_backup_codec.c $CJSON/cJSON.c"
        INC="-I$COMP/config_backup -I$COMP/config_backup/include -I$CJSON" ;;
    *)
        echo "usage: $0 <vpn_manager|config_backup>"; exit 2 ;;
esac

gcc -std=gnu11 -Wall -Wextra -Wno-unused-parameter -Wno-missing-field-initializers -O1 -g \
    -fsanitize=address,undefined -fno-sanitize-recover=undefined \
    $INC -I"$IDF/components/unity/unity/src" -I"$IDF/components/esp_common/include" -I"$OUT/inc" \
    $SRCS "$IDF/components/unity/unity/src/unity.c" "$OUT/main_shim.c" -o "$OUT/$1.bin" -lm || exit 3
"$OUT/$1.bin"
