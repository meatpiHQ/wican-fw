# Build the bench_ap instrument firmware (ESP32-P4-Function-EV, chip rev v0.1).
#
#   .\build.ps1              # build into C:\Users\Ali\idf_tmp\bap
#   .\build.ps1 -Flash       # then push to rpi001 and flash (flash_from_pi.py)
#
# The traps this script exists for (README, "Build and flash"):
#  - esp_wifi_remote picks its Kconfig by ESP_IDF_VERSION and wants "6.0";
#    with "6.0.2" it includes nothing and WIFI_INIT_CONFIG_DEFAULT fails.
#  - the project directory is deep enough to hit Windows' object-path limit:
#    the build directory must be short.
#  - the P4 is RISC-V: the firmware's build.ps1 puts the xtensa toolchain on
#    the PATH, this one the riscv32 one.
#  - Git Bash / MSYS: idf.py builds nothing there while still exiting 0.
param(
    [switch]$Flash,
    [string]$BuildDir = 'C:\Users\Ali\idf_tmp\bap'
)

$ErrorActionPreference = "Stop"

if ($env:MSYSTEM) {
    Write-Error "Run this from native PowerShell, not Git Bash/MSYS (MSYSTEM=$env:MSYSTEM)."
}

$env:IDF_PATH                     = 'C:\esp\v6.0.2\esp-idf'
$env:IDF_TOOLS_PATH               = 'C:\Espressif'
$env:IDF_PYTHON_ENV_PATH          = 'C:\Espressif\tools\python\v6.0.2\venv'
$env:IDF_PYTHON_CHECK_CONSTRAINTS = 'no'
$env:ESP_IDF_VERSION              = '6.0'
$env:ESP_ROM_ELF_DIR              = 'C:\Espressif\tools\esp-rom-elfs\20241011\'
$env:PATH = 'C:\Espressif\tools\python\v6.0.2\venv\Scripts;' +
            'C:\Espressif\tools\ninja\1.12.1;C:\Espressif\tools\cmake\4.0.3\bin;' +
            'C:\Espressif\tools\riscv32-esp-elf\esp-15.2.0_20251204\riscv32-esp-elf\bin;' +
            'C:\Espressif\tools\ccache\4.12.1\ccache-4.12.1-windows-x86_64;' +
            'C:\Espressif\tools\idf-git\2.34.2\cmd;' + $env:PATH

Set-Location $PSScriptRoot

python "$env:IDF_PATH\tools\idf.py" -B $BuildDir build
if ($LASTEXITCODE -ne 0) { Write-Error "build failed (exit $LASTEXITCODE)" }

$bin = Join-Path $BuildDir 'bench_ap.bin'
if (-not (Test-Path $bin)) { Write-Error "no $bin after the build" }
Write-Host ("OK: {0} ({1} bytes, {2})" -f $bin, (Get-Item $bin).Length, (Get-Item $bin).LastWriteTime)

if ($Flash) {
    python (Join-Path $PSScriptRoot 'flash_from_pi.py') --build $BuildDir
    if ($LASTEXITCODE -ne 0) { Write-Error "flash failed (exit $LASTEXITCODE)" }
}
