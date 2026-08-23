@echo off
rem BenchBoard launcher - serves the WiCAN bench manifest
rem (benchboard.toml in this repo) on http://127.0.0.1:8777/.
rem BenchBoard itself lives in its own repo:
rem   https://github.com/meatpiHQ/benchboard
rem Clone it next to this checkout (..\benchboard) or set BENCHBOARD_PATH.
rem Needs Python 3.11+ (bare `python` may be the Store 3.10 and will refuse).
cd /d "%~dp0"
set PY=C:\Espressif\tools\python\v6.0.2\venv\Scripts\python.exe
if not exist "%PY%" set PY=python
set BB=%BENCHBOARD_PATH%
if "%BB%"=="" set BB=..\benchboard
if not exist "%BB%\benchboard.py" (
    echo BenchBoard not found at "%BB%" - clone
    echo   https://github.com/meatpiHQ/benchboard
    echo next to this repo, or set BENCHBOARD_PATH to your clone.
    exit /b 1
)
"%PY%" "%BB%\benchboard.py" %*
