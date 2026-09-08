# One-shot WiCAN Pro build (and optional flash) with the exact environment
# the firmware needs. Run from a NATIVE PowerShell window:
#
#   .\build.ps1                    # build only
#   .\build.ps1 -Flash COM12       # build + flash at 460800
#   .\build.ps1 -Clean             # idf.py fullclean first (after env/path
#                                  # changes - the CMake cache pins paths)
#
# Why this script exists (each of these has burned a session):
#  - MEATPI_COMPONENTS_PATH must point at the live meatpi-components tree,
#    or CMake silently picks the STALE components/meatpi clone and the
#    build fails on newer components (espnetlink_link etc.).
#  - Git Bash / MSYS: idf.py prints "MSys/Mingw is no longer supported",
#    builds NOTHING and still exits 0 - a flash then re-writes the OLD
#    binary. This script refuses to run there.
#  - Flashing needs the IDF venv's esptool (v5 flags) at 460800 only.

param(
    [string]$Flash = "",
    [switch]$Clean
)

$ErrorActionPreference = "Stop"

if ($env:MSYSTEM) {
    Write-Error "Run this from native PowerShell, not Git Bash/MSYS (MSYSTEM=$env:MSYSTEM): idf.py builds nothing there while still exiting 0."
}

# ---- ESP-IDF v6.0.2 environment -------------------------------------------
$env:IDF_PATH                     = 'C:\esp\v6.0.2\esp-idf'
$env:IDF_TOOLS_PATH               = 'C:\Espressif'
$env:IDF_PYTHON_ENV_PATH          = 'C:\Espressif\tools\python\v6.0.2\venv'
$env:IDF_PYTHON_CHECK_CONSTRAINTS = 'no'
$env:ESP_IDF_VERSION              = '6.0.2'
$env:ESP_ROM_ELF_DIR              = 'C:\Espressif\tools\esp-rom-elfs\20241011\'
$env:PATH = 'C:\Espressif\tools\python\v6.0.2\venv\Scripts;' +
            'C:\Espressif\tools\ninja\1.12.1;C:\Espressif\tools\cmake\4.0.3\bin;' +
            'C:\Espressif\tools\xtensa-esp-elf\esp-15.2.0_20251204\xtensa-esp-elf\bin;' +
            'C:\Espressif\tools\ccache\4.12.1\ccache-4.12.1-windows-x86_64;' +
            'C:\Espressif\tools\idf-git\2.34.2\cmd;' + $env:PATH

# ---- the live components tree (REQUIRED) ----------------------------------
$env:MEATPI_COMPONENTS_PATH = 'C:\Users\Ali\Desktop\Projects\project_wican\wican-fw-dev\components\meatpi-components\components'
if (-not (Test-Path (Join-Path $env:MEATPI_COMPONENTS_PATH 'espnetlink_link'))) {
    Write-Error "MEATPI_COMPONENTS_PATH looks wrong: $env:MEATPI_COMPONENTS_PATH (no espnetlink_link in it)"
}

Set-Location $PSScriptRoot

if ($Clean) {
    python "$env:IDF_PATH\tools\idf.py" fullclean
    if ($LASTEXITCODE -ne 0) { Write-Error "fullclean failed" }
}

python "$env:IDF_PATH\tools\idf.py" build
if ($LASTEXITCODE -ne 0) { Write-Error "build failed (exit $LASTEXITCODE)" }

# sanity: did ninja actually produce a fresh binary?
$bin = Join-Path $PSScriptRoot 'build\wican-fw.bin'
$age = (Get-Date) - (Get-Item $bin).LastWriteTime
"OK: build\wican-fw.bin ($([math]::Round((Get-Item $bin).Length/1MB,2)) MB, written $([math]::Round($age.TotalMinutes,1)) min ago)"
if ($age.TotalMinutes -gt 30) {
    Write-Warning "the binary is older than this run should allow - the build may not have rebuilt anything it should have (check build\.ninja_log)"
}

if ($Flash) {
    Set-Location (Join-Path $PSScriptRoot 'build')
    python -m esptool --chip esp32s3 -p $Flash -b 460800 --before default-reset --after hard-reset write-flash "@flash_args"
    if ($LASTEXITCODE -ne 0) { Write-Error "flash failed on $Flash" }
    "OK: flashed on $Flash"
}
