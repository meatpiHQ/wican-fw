#!/usr/bin/env bash
# WiCAN host unit tests — builds and runs components/*/host_test on the
# IDF `linux` target and prints a compact PASS/FAIL summary (with per-suite
# test counts). An optional argument limits the run to one component:
#   run_host_tests.sh wifi_manager
# Runs on any Linux with gcc/cmake/ninja/libbsd-dev + an ESP-IDF checkout
# (~/esp-idf by default); on the bench Pi: scp/rsync the repo's components/ +
# tools/ to ~/wican and run
#   ssh rpi001 '~/wican/tools/testbench/run_host_tests.sh'
# See components/TESTBENCH.md §4.
set -u
cd "$(dirname "$0")/../.."
ONLY="${1:-*}"

# linux target needs only the IDF python env + system gcc/cmake/ninja —
# skip export.sh (it refuses without the cross toolchains installed).
export IDF_PATH="${IDF_PATH:-$HOME/esp-idf}"
export IDF_PYTHON_CHECK_CONSTRAINTS=no
export ESP_IDF_VERSION="${ESP_IDF_VERSION:-6.0.2}"
export IDF_PYTHON_ENV_PATH=$(ls -d "$HOME"/.espressif/python_env/* | head -1)
IDF_PY="$IDF_PYTHON_ENV_PATH/bin/python"

idf() { "$IDF_PY" "$IDF_PATH/tools/idf.py" "$@"; }

summary=""
rc=0

total=0
for ht in components/$ONLY/host_test; do
    [ -d "$ht" ] || { echo "no host_test matches '$ONLY'"; exit 1; }
    comp=$(basename "$(dirname "$ht")")
    echo "=== $comp ==="

    run=$(
        cd "$ht" || exit 1
        rm -rf sdkconfig
        [ -f build/CMakeCache.txt ] || rm -rf build
        idf --preview set-target linux > settarget.log 2>&1 \
            || { echo SET_TARGET_FAILED; tail -20 settarget.log; exit 2; }
        idf build > build.log 2>&1 || { echo BUILD_FAILED; tail -30 build.log; exit 2; }
        elf=$(ls build/*.elf 2>/dev/null | head -1)
        [ -n "$elf" ] || { echo NO_ELF; exit 2; }
        # The FreeRTOS POSIX port never exits after UNITY_END, so we
        # kill the process OURSELVES the moment Unity's summary line
        # appears (a fixed `timeout 30` burned 30 s on EVERY suite —
        # ~22 min of pure waiting across the 44 suites — and still
        # false-failed autopid on slow CI runners, 2026-07-27). Budget
        # only guards against a truly hung binary.
        budget=120
        "$elf" > run.log 2>&1 &
        pid=$!
        i=0
        while [ $i -lt $((budget * 2)) ]; do
            grep -qE '[0-9]+ Tests [0-9]+ Failures' run.log && break
            kill -0 "$pid" 2>/dev/null || break
            sleep 0.5
            i=$((i + 1))
        done
        kill "$pid" 2>/dev/null
        wait "$pid" 2>/dev/null
        out=$(cat run.log)
        echo "$out" | tail -15
        if ! echo "$out" | grep -qE '[0-9]+ Tests'; then
            echo "RUN TIMED OUT (no Unity summary within ${budget}s)"
        fi
        echo "$out" | grep -qE '[0-9]+ Tests 0 Failures' || exit 3
        exit 0
    )
    st=$?
    echo "$run"
    n=$(echo "$run" | grep -oE '[0-9]+ Tests' | tail -1 | cut -d' ' -f1)
    case $st in
        0) summary="$summary$comp: PASS (${n:-?} tests)\n"; total=$((total + ${n:-0})) ;;
        2) summary="$summary$comp: BUILD FAILED\n"; rc=1 ;;
        *) summary="$summary$comp: TEST FAILURES\n"; rc=1 ;;
    esac
done

echo "=== SUMMARY ==="
printf "%b" "$summary"
echo "TOTAL: $total tests"
exit $rc
