# One-shot WiCAN Pro build (and optional flash) with the exact environment
# the firmware needs. Run from a NATIVE PowerShell window:
#
#   .\build.ps1                    # build only
#   .\build.ps1 -Flash COM12       # build + flash at 460800
#   .\build.ps1 -Clean             # idf.py fullclean first (after env/path
#                                  # changes - the CMake cache pins paths)
#   .\build.ps1 -Idf 6.0.2         # build with ESP-IDF v6.0.2 (C:\esp\v6.0.2) instead
#                                  # of the default 6.1 (the default since 6.0.0-beta.5,
#                                  # 2026-10-27); switching the IDF needs a fresh
#                                  # build\ (delete it: -Clean runs the OTHER idf's
#                                  # fullclean on a foreign cache)
#
# Why this script exists (each of these has burned a session):
#  - MEATPI_COMPONENTS_PATH must point at the live meatpi-components tree,
#    or CMake silently picks the STALE components/meatpi clone and the
#    build fails on newer components (espnetlink_link etc.).
#  - Git Bash / MSYS: idf.py prints "MSys/Mingw is no longer supported",
#    builds NOTHING and still exits 0 - a flash then re-writes the OLD
#    binary. This script refuses to run there.
#  - Flashing needs the IDF venv's esptool (v5 flags) at 460800 only, and
#    goes through build\flash_args so it always takes the image of THIS
#    build (the file name changes with every commit, old ones pile up).

param(
    [string]$Flash = "",
    [switch]$Clean,
    [ValidateSet('6.0.2', '6.1')][string]$Idf = '6.1'   # the ESP-IDF to build with (6.1 since 6.0.0-beta.5, 2026-10-27; 6.0.2 stays installed)
)

$ErrorActionPreference = "Stop"

if ($env:MSYSTEM) {
    Write-Error "Run this from native PowerShell, not Git Bash/MSYS (MSYSTEM=$env:MSYSTEM): idf.py builds nothing there while still exiting 0."
}

# ---- ESP-IDF environment (-Idf 6.0.2 or 6.1) ---------------------------------
# Both installs share the tool versions below (xtensa esp-15.2.0_20251204,
# ninja 1.12.1, cmake 4.0.3, ccache 4.12.1, esp-rom-elfs 20241011); what
# differs is the IDF tree and its Python environment. The v6.1 installer
# put its venv under tools\python\v6.1 (python 3.11), which v6.1's own
# export.ps1 does not find (it expects python_env\idf6.1_py3.10_env), so
# this script sets the paths itself for both, as it always did for 6.0.2.
$idfEnvs = @{
    '6.0.2' = @{ Path = 'C:\esp\v6.0.2\esp-idf'; Venv = 'C:\Espressif\tools\python\v6.0.2\venv'; Alt = 'C:\Espressif\tools\python_env\idf6.0_py3.10_env' }
    '6.1'   = @{ Path = 'C:\esp\v6.1\esp-idf';   Venv = 'C:\Espressif\tools\python\v6.1\venv';   Alt = '' }
}
$idfEnv = $idfEnvs[$Idf]
$env:IDF_PATH                     = $idfEnv.Path
$env:IDF_TOOLS_PATH               = 'C:\Espressif'
# The IDF venv, or its stand-in: on 2026-10-19 the 6.0.2 venv's base
# interpreter (C:\Espressif\tools\python\python.exe) vanished, so its launcher
# prints "No Python at ..." and runs nothing. A second environment with IDF's
# own requirements (idf_tools.py install-python-env, the Store python 3.10)
# was made beside it and is used while the launcher is dead.
$idfVenv = $idfEnv.Venv
$venvRuns = $false
try { $null = & "$idfVenv\Scripts\python.exe" -c 'pass' 2>$null; $venvRuns = ($LASTEXITCODE -eq 0) } catch { $venvRuns = $false }
if (-not $venvRuns) {
    $alt = $idfEnv.Alt
    if ($alt -and (Test-Path "$alt\Scripts\python.exe")) {
        Write-Host "build: the IDF $Idf venv does not run (its base interpreter is missing); using $alt" -ForegroundColor Yellow
        $idfVenv = $alt
    } else {
        Write-Error "build: no IDF $Idf python environment runs ($idfVenv is dead$(if ($alt) { " and $alt is absent" }))"
    }
}
$env:IDF_PYTHON_ENV_PATH          = $idfVenv
$env:IDF_PYTHON_CHECK_CONSTRAINTS = 'no'
$env:ESP_IDF_VERSION              = $Idf
$env:ESP_ROM_ELF_DIR              = 'C:\Espressif\tools\esp-rom-elfs\20241011\'
"build: ESP-IDF $Idf at $env:IDF_PATH (python $idfVenv)"
$env:PATH = "$idfVenv\Scripts;" +
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

# sanity: did ninja actually produce a fresh binary? The image name carries
# the git describe (build\wican-fw_obd_pro_<tag>.bin - root CMakeLists.txt),
# so resolve it from build\project_description.json; older images from
# earlier commits stay in build\ and are listed so nobody flashes one by hand.
$bin = python (Join-Path $PSScriptRoot 'tools\fw_bin.py')
if ($LASTEXITCODE -ne 0 -or -not $bin) { Write-Error "no current image in build\ (see tools\fw_bin.py output above)" }
$stale = Get-ChildItem (Join-Path $PSScriptRoot 'build') -Filter 'wican-fw*.bin' | Where-Object { $_.FullName -ne $bin }
foreach ($s in $stale) { Write-Warning "older image left in build\: $($s.Name) (flash_args does not use it; delete freely)" }
$age = (Get-Date) - (Get-Item $bin).LastWriteTime
"OK: build\$(Split-Path $bin -Leaf) ($([math]::Round((Get-Item $bin).Length/1MB,2)) MB, written $([math]::Round($age.TotalMinutes,1)) min ago)"
if ($age.TotalMinutes -gt 30) {
    Write-Warning "the binary is older than this run should allow - the build may not have rebuilt anything it should have (check build\.ninja_log)"
}

if ($Flash) {
    Set-Location (Join-Path $PSScriptRoot 'build')
    python -m esptool --chip esp32s3 -p $Flash -b 460800 --before default-reset --after hard-reset write-flash "@flash_args"
    if ($LASTEXITCODE -ne 0) { Write-Error "flash failed on $Flash" }
    "OK: flashed on $Flash"
}
