"""Repo-root pytest fixtures for WiCAN HIL bench tests.

Wires tools/testbench (Bench = Pi AP control, Dut = DUT serial) into any
pytest file under components/*/test_apps*/ (see components/TESTBENCH.md).

Run from the dev host (DUT on a local COM port, bench over SSH):
    pytest components/wifi_manager/test_apps_hil -v --dut-port COM7
Run on the Pi itself (DUT plugged into the Pi, bench local):
    pytest components/wifi_manager/test_apps_hil -v \
        --dut-port /dev/ttyACM0 --bench-local
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent / "tools" / "testbench" / "lib"))

from bench import Bench  # noqa: E402
from dut import Dut      # noqa: E402


def pytest_addoption(parser):
    parser.addoption("--dut-port", default="COM7",
                     help="DUT serial port (COMx or /dev/tty*)")
    parser.addoption("--dut-baud", type=int, default=115200)
    parser.addoption("--bench-host", default="rpi001",
                     help="SSH host alias of the bench Pi")
    parser.addoption("--bench-local", action="store_true",
                     help="run bench commands locally (pytest runs on the Pi)")


@pytest.fixture(scope="session")
def bench(request):
    host = None if request.config.getoption("--bench-local") \
        else request.config.getoption("--bench-host")
    b = Bench(host)
    b.cleanup()          # never start with stale wican-* APs
    yield b
    b.cleanup()


@pytest.fixture(scope="session")
def dut(request):
    port = request.config.getoption("--dut-port")
    try:
        d = Dut(port, request.config.getoption("--dut-baud"))
    except Exception as e:  # busy port = every test "fails"; say so plainly
        pytest.exit(f"cannot open DUT serial {port}: {e}\n"
                    f"Close any serial monitor / flasher using the port "
                    f"(VS Code monitor, idf.py monitor, another test run) "
                    f"and retry.", returncode=2)
    yield d
    d.close()
