# WiCAN test runner - one entry point for every test kind (see TESTING.md).
#
#   .\test.ps1 list                        # show every runnable suite/check
#   .\test.ps1 host                        # all host unit suites on rpi001
#   .\test.ps1 host wifi_manager           # one component's host suite
#   .\test.ps1 target settings_manager     # build+flash+verify a self-contained test app
#   .\test.ps1 hil                         # WiFi HIL pytest (bench Pi + DUT)
#   .\test.ps1 hil -Flash                  # rebuild+flash the HIL app first
#   .\test.ps1 live                        # live checks vs the composed main (cli_ws, ws_live)
#   .\test.ps1 perf                        # performance battery (mqtt/ws/ble) vs the composed main
#   .\test.ps1 all                         # host + every target app + hil
#
# Every run (except `list`) writes a dated summary report to test-reports\
# (results table + performance numbers + DUT conditions); raw per-stage logs
# land in test-reports\logs\<run>\ (gitignored).
#
param(
    [Parameter(Mandatory = $true, Position = 0)][ValidateSet('host', 'target', 'hil', 'live', 'perf', 'usbeth', 'espnetlink', 'blesec', 'sleep', 'sleepmatrix', 'conserve', 'stackaudit', 'dwc2', 'logsinks', 'all', 'list')]
    [string]$What,
    [Parameter(Position = 1)][string]$Component,
    [string]$Port = 'COM10',  # UART0 external USB-serial = console/flash;
                              # works in EVERY USB-connector mode. COM7/COM6 =
                              # CH342 A/B — only exist while the connector is
                              # cabled to the PC (device role); gone when it
                              # hosts the USB-Ethernet adapter / a dongle.
    [string]$BenchHost = 'rpi001',
    [string]$DutIp = '10.42.0.62',
    [string]$DeviceId = '14c19f44e349',
    [string]$FwRepo,          # wican-fw checkout; default: WICAN_FW_PATH env,
                              # then known sibling locations (see below)
    [switch]$Flash,
    [switch]$NoReport,
    [switch]$SkipBenchCheck   # bypass the physical-bench preflight
)

# 'Continue', not 'Stop': PS 5.1 turns redirected native stderr into
# terminating errors under Stop, which swallows tool output; failures are
# detected via $LASTEXITCODE checks instead.
$ErrorActionPreference = 'Continue'
# ---- repo roots (Phase 3 split, 2026-07-26) --------------------------------
# $repo   = THIS repo (the wican-fw checkout): testbench, reports.
# $fwRepo = the wican-fw firmware checkout (build/flash subject + report
#           header). The wican-fw shim passes -FwRepo explicitly; direct
#           runs resolve via WICAN_FW_PATH, then known sibling locations.
$repo = $PSScriptRoot
$script:fwRepo = $null
# in-tree first: the bench now ships inside the firmware repo itself
foreach ($cand in @($FwRepo, $env:WICAN_FW_PATH, $repo,
                    "$repo\..\wican-fw")) {
    if ($cand -and (Test-Path "$cand\main\main.c") -and (Test-Path "$cand\CMakeLists.txt")) {
        $script:fwRepo = (Resolve-Path $cand).Path; break
    }
}
if (-not $script:fwRepo) {
    throw 'wican-fw checkout not found - pass -FwRepo <path> or set WICAN_FW_PATH'
}
$fwRepo = $script:fwRepo
# benches locate fw-side tools (wdl_dump.py, stack_audit.py) through this
$env:WICAN_FW_PATH = $fwRepo

# Component sources live in the meatpi-components repos (2026-07-22 split).
# One resolution for host-sync overlays, target apps and the HIL suite.
function Get-ComponentRoots {
    $roots = @()
    if ($env:MEATPI_COMPONENTS_PATH) {
        $roots += (Split-Path $env:MEATPI_COMPONENTS_PATH -Parent)
    } else {
        foreach ($cand in @("$fwRepo\components\meatpi",
                            "$fwRepo\..\meatpi-components",
                            "$repo\..\meatpi-components")) {
            if (Test-Path "$cand\components") { $roots += (Resolve-Path $cand).Path; break }
        }
    }
    # optional component-overlay roots (later roots override), the same
    # neutral hook the firmware build exposes
    if ($env:MEATPI_COMPONENT_OVERLAYS) {
        foreach ($cand in @($env:MEATPI_COMPONENT_OVERLAYS)) {
            if (Test-Path "$cand") { $roots += (Resolve-Path "$cand\..").Path }
        }
    }
    return $roots
}

# Absolute dir of components/<relPath> across the component roots
# (later roots override — same precedence as the build's overlays).
function Get-CompDir { param([string]$RelPath)
    $hit = $null
    foreach ($root in Get-ComponentRoots) {
        if (Test-Path "$root\components\$RelPath") { $hit = "$root\components\$RelPath" }
    }
    if (-not $hit) { throw "components\$RelPath not found in any component root" }
    return $hit
}

$targets = @('settings_manager', 'filesystem', 'http_server_manager', 'wifi_manager',
              'log_manager', 'dev_status_manager', 'restart_tracker', 'obd_chip',
              'api_http', 'socket_manager', 'bridge_manager', 'ble_manager')

# ---------------------------------------------------------------- reporting
$script:stages = @()
$script:metrics = @()
$script:conditions = @()
$script:anyFail = $false
$script:runName = (Get-Date -Format 'yyyy-MM-dd_HHmm') + "_$What" + $(if ($Component) { "_$Component" } else { '' })
$script:logDir = Join-Path $repo "test-reports\logs\$($script:runName)"

function Save-StageLog { param([string]$Name, $Text)
    if (-not (Test-Path $script:logDir)) { New-Item -ItemType Directory -Force $script:logDir | Out-Null }
    $safe = $Name -replace '[^\w.-]', '_'
    ($Text | Out-String) | Set-Content -Encoding utf8 (Join-Path $script:logDir "$safe.log")
}

function Add-Metric { param([string]$Label, [string]$Line)
    $script:metrics += [pscustomobject]@{ Label = $Label; Line = $Line }
}

# Runs one report row. The body streams its output to the console; it can set
# $script:note for the report's notes column. A throw = FAIL (run continues -
# the report records it).
function Invoke-Stage { param([string]$Name, [scriptblock]$Body)
    Write-Host "== $Name ==" -ForegroundColor Cyan
    $sw = [Diagnostics.Stopwatch]::StartNew()
    $script:note = ''
    $script:skipStage = $false   # a body sets this when its FIXTURE is absent
    $status = 'PASS'
    try { & $Body | Out-Host }
    catch { $status = 'FAIL'; $script:note = $_.Exception.Message; $script:anyFail = $true }
    if ($script:skipStage -and $status -eq 'PASS') { $status = 'SKIP' }
    $sw.Stop()
    $secs = [math]::Round($sw.Elapsed.TotalSeconds, 1)
    $script:stages += [pscustomobject]@{ Stage = $Name; Result = $status; Seconds = $secs; Notes = "$($script:note)" }
    $color = 'Green'
    if ($status -eq 'FAIL') { $color = 'Red' } elseif ($status -eq 'SKIP') { $color = 'Yellow' }
    Write-Host "[$status] $Name (${secs}s)" -ForegroundColor $color
}

# ---------------------------------------------------------------- bench preflight
# Quick physical-bench check BEFORE any run (~10 s). The bench has moving
# parts that tests silently depend on:
#   - the DUT's USB connector is EITHER cabled to this PC (CH342 device role,
#     COM7/COM6 — needed for usb_obd/port-B legs) OR hosting a device (the
#     USB-Ethernet adapter / a dongle — needed for the usbeth leg). Never both.
#   - console/flash always works via the UART0 external adapter (COM10).
#   - live/perf need the composed main ON the DUT + the Pi fixtures.
# The selected kind FAILS FAST on hard requirements; optional legs consult
# $script:bench and SKIP themselves instead of failing.
function Invoke-BenchCheck {
    $b = [pscustomobject]@{
        Ports    = @([System.IO.Ports.SerialPort]::GetPortNames())
        Serial   = $null     # usable console/flash port for target/hil
        Ch342    = @()       # CH342 COM names => connector is in DEVICE role
        PiOk     = $false
        DutOnline = $false
        DutVersion = ''
        UsbEth   = $false    # connector hosts a USB-Ethernet adapter (asix/...)
        Espnetlink = $false  # connector hosts the espnetlink LTE dongle (rndis + ACM)
        UsbDriver = ''
        UsbDevicePresent = $false
        Broker   = $false    # mosquitto :1883 on the Pi (perf mqtt legs)
        UsbNcm   = $false    # DUT mgmt link 192.168.82.1 (NCM device role)
    }
    $script:bench = $b

    # CH342 by PnP name (COM numbers drift: 7/6 today, 2029/30 historically)
    try {
        $b.Ch342 = @(Get-CimInstance Win32_PnPEntity -ErrorAction Stop |
            Where-Object { $_.Name -match 'CH342' -and $_.Name -match '\(COM\d+\)' } |
            ForEach-Object { ($_.Name -replace '.*\((COM\d+)\).*', '$1') })
    } catch {}

    # console/flash port: the requested -Port, else the UART0 adapter, else CH342 A
    foreach ($cand in (@($Port, 'COM10') + $b.Ch342)) {
        if ($b.Ports -contains $cand) { $b.Serial = $cand; break }
    }
    if ($b.Serial -and $b.Serial -ne $Port) {
        Write-Host "bench: -Port $Port not present, using $($b.Serial)" -ForegroundColor Yellow
        $script:Port = $b.Serial
    }

    # one ssh round-trip: Pi alive, DUT status, USB state, broker port
    $probe = & ssh -o BatchMode=yes -o ConnectTimeout=8 $BenchHost `
        "echo PI_OK; curl -s -m 5 http://$DutIp/api/status; echo; echo --USB--; curl -s -m 5 http://$DutIp/api/usb; echo; echo --ACM--; curl -s -m 5 http://$DutIp/api/usb/acm; echo; echo --BROKER--; (ss -tln 2>/dev/null || netstat -tln) | grep -c ':1883 '" 2>$null
    $text = ($probe | Out-String)
    if ($text -match 'PI_OK') { $b.PiOk = $true }
    if ($text -match '"uptime"') {
        $b.DutOnline = $true
        if ($text -match '"version":"([^"]+)"') { $b.DutVersion = $Matches[1] }
    }
    if ($text -match '"device_present":true') { $b.UsbDevicePresent = $true }
    if ($text -match '"driver":"([a-z0-9_]+)"') { $b.UsbDriver = $Matches[1] }
    # RNDIS + an ACM console => the espnetlink LTE dongle; other eth drivers => a USB-Ethernet adapter
    if ($b.UsbDriver -eq 'rndis' -and $text -match '--ACM--\s*\{[^}]*"connected":\s*true') { $b.Espnetlink = $true }
    elseif ($text -match '"eth_connected":true[^}]*"driver"' -and $b.UsbDriver -ne 'rndis') { $b.UsbEth = $true }
    if ($text -match '--BROKER--\s*[1-9]') { $b.Broker = $true }

    # USB-NCM mgmt link: several live legs drive the DUT through
    # 192.168.82.1. Probe it in 2 s here so an absent link becomes clean
    # stage SKIPs, not silent minutes of connect timeouts (looked like a
    # hang and got Ctrl-C'd, 2026-07-26).
    try {
        $tcp = New-Object Net.Sockets.TcpClient
        if ($tcp.ConnectAsync('192.168.82.1', 80).Wait(2000) -and $tcp.Connected) { $b.UsbNcm = $true }
        $tcp.Close()
    } catch {}

    # summary
    $wiring = 'connector state UNKNOWN'
    if ($b.Ch342.Count -gt 0) { $wiring = "connector -> PC (CH342 $($b.Ch342 -join '/'))" }
    elseif ($b.Espnetlink) { $wiring = 'connector -> espnetlink LTE dongle (RNDIS + ACM console)' }
    elseif ($b.UsbEth) { $wiring = 'connector -> USB-Ethernet adapter (host mode)' }
    elseif ($b.UsbDevicePresent) { $wiring = 'connector -> a USB device (host mode, not eth)' }
    Write-Host "bench: serial=$(if ($b.Serial) { $b.Serial } else { 'NONE' })  pi=$($b.PiOk)  dut=$(if ($b.DutOnline) { $b.DutVersion } else { 'OFFLINE' })  broker=$($b.Broker)  ncm=$($b.UsbNcm)" -ForegroundColor Cyan
    Write-Host "bench: $wiring" -ForegroundColor Cyan
    $script:conditions += "preflight: $wiring; serial=$($b.Serial); dut=$(if ($b.DutOnline) { $b.DutVersion } else { 'offline' }); broker=$($b.Broker); ncm=$($b.UsbNcm)"

    # hard requirements per kind
    if (-not $b.PiOk) { throw "bench: $BenchHost unreachable - every kind needs the Pi" }
    if ($What -in @('target', 'hil', 'all') -and -not $b.Serial) {
        throw "bench: no console/flash COM port (tried $Port, COM10, CH342). Plug the UART0 adapter or cable the connector to the PC."
    }
    if ($What -in @('live', 'perf', 'usbeth', 'espnetlink') -and -not $b.DutOnline) {
        throw "bench: DUT $DutIp offline via the Pi (composed main flashed? hotspot up?)"
    }
    if ($What -eq 'perf' -and -not $b.Broker) {
        Write-Host 'bench: WARNING mosquitto not listening on the Pi - mqtt legs will fail' -ForegroundColor Yellow
    }
    if ($What -eq 'usbeth' -and -not $b.UsbEth) {
        throw "bench: USB-Ethernet adapter not detected on the DUT ($wiring). Plug it into the WiCAN USB connector + cable to Pi eth0."
    }
    if ($What -eq 'espnetlink' -and -not $b.Espnetlink) {
        throw "bench: espnetlink not detected ($wiring). Plug the LTE dongle into the WiCAN USB connector + enable usb_host_manager & usb_acm_cli."
    }
}

# Legs that drive the DUT over the USB-NCM mgmt link call this first and
# SKIP (yellow) when the link is down — same fixture-absent pattern as
# usbeth/espnetlink. Returns $true when the stage should bail.
function Skip-IfNoUsbNcm {
    if ($script:bench -and -not $script:bench.UsbNcm) {
        $script:skipStage = $true
        $script:note = "USB-NCM 192.168.82.1 not up - usb_host_manager {enabled:true, role:'device', device_class:'ncm'} + submit"
        return $true
    }
    return $false
}

function Get-DutConditions {
    # Snapshot what the DUT was running - performance numbers are meaningless
    # without the conditions they were measured under.
    $st = & ssh -o BatchMode=yes $BenchHost "curl -s -m 5 http://$DutIp/api/status"
    if (-not $st) { $script:conditions += 'DUT /api/status unreachable'; return }
    Save-StageLog 'dut_api_status' $st
    try {
        $j = ($st | Out-String | ConvertFrom-Json)
        $bits = @(); foreach ($p in $j.bits.PSObject.Properties) { if ($p.Value) { $bits += $p.Name } }
        $script:conditions += "status bits: $($bits -join ', ')"
        $script:conditions += "fw $($j.version) on $($j.partition), uptime $($j.uptime)"
        if ($j.memory) { $script:conditions += "internal free $($j.memory.internal.free), largest $($j.memory.internal.largest_block)" }
    } catch { $script:conditions += 'status parse failed (raw in logs)' }
    $wifi = & ssh -o BatchMode=yes $BenchHost "curl -s -m 5 http://$DutIp/api/settings/wifi_manager"
    if ($wifi) { $script:conditions += "wifi: $wifi" }
    $ble = & ssh -o BatchMode=yes $BenchHost "curl -s -m 5 http://$DutIp/api/settings/ble_manager"
    if ($ble) { $script:conditions += "ble: $ble" }
}

function Write-Report {
    if ($NoReport -or $What -eq 'list' -or $script:stages.Count -eq 0) { return }
    $dir = Join-Path $repo 'test-reports'
    if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Force $dir | Out-Null }
    $file = Join-Path $dir "$($script:runName).md"
    $branch = (& git -C $fwRepo rev-parse --abbrev-ref HEAD)
    $commit = (& git -C $fwRepo rev-parse --short HEAD)
    $dirty = ''; if (& git -C $fwRepo status --porcelain) { $dirty = ' +uncommitted' }
    $failed = @($script:stages | Where-Object { $_.Result -ne 'PASS' }).Count
    $overall = 'PASS'; if ($failed -gt 0) { $overall = "FAIL ($failed stage(s))" }

    $md = @()
    $md += "# WiCAN test report - $(Get-Date -Format 'yyyy-MM-dd HH:mm') - ``$What$(if ($Component) { " $Component" })`` - **$overall**"
    $md += ''
    $md += "- firmware: ``$branch`` @ ``$commit``$dirty"
    $md += "- bench: $BenchHost, DUT $DutIp ($Port), device id $DeviceId"
    foreach ($c in $script:conditions) { $md += "- DUT: $c" }
    $md += ''
    $md += '| stage | result | secs | notes |'
    $md += '|---|---|---|---|'
    foreach ($s in $script:stages) {
        $n = "$($s.Notes)" -replace '\|', '\|' -replace "`r?`n", ' '
        $md += "| $($s.Stage) | $($s.Result) | $($s.Seconds) | $n |"
    }
    if ($script:metrics.Count -gt 0) {
        $md += ''
        $md += '## Performance numbers'
        $md += ''
        foreach ($m in $script:metrics) { $md += "- **$($m.Label)**: ``$($m.Line)``" }
    }
    $md += ''
    $md += "Raw stage logs: ``test-reports/logs/$($script:runName)/`` (local only)."
    $md -join "`n" | Set-Content -Encoding utf8 $file
    Write-Host "report: $file" -ForegroundColor Cyan
}

# ---------------------------------------------------------------- idf env
function Use-IdfEnv {
    $env:IDF_PATH = 'C:\esp\v6.0.2\esp-idf'
    $env:IDF_TOOLS_PATH = 'C:\Espressif'
    $env:IDF_PYTHON_ENV_PATH = 'C:\Espressif\tools\python\v6.0.2\venv'
    $env:IDF_PYTHON_CHECK_CONSTRAINTS = 'no'
    $env:ESP_IDF_VERSION = '6.0.2'
    # gdbinit generation needs this or ninja aborts BEFORE the app image
    # (the stale-bin trap, CHECKLIST 2026-07-05)
    $env:ESP_ROM_ELF_DIR = 'C:\Espressif\tools\esp-rom-elfs\20241011\'
    # ccache MUST be on PATH: build dirs configured from a ccache-enabled
    # shell bake `ccache gcc` into build.ninja — without it every compile
    # dies with "CreateProcess failed" (the 2026-07-05 `all` build failures)
    $env:PATH = 'C:\Espressif\tools\python\v6.0.2\venv\Scripts;' +
                'C:\Espressif\tools\ninja\1.12.1;C:\Espressif\tools\cmake\4.0.3\bin;' +
                'C:\Espressif\tools\xtensa-esp-elf\esp-15.2.0_20251204\xtensa-esp-elf\bin;' +
                'C:\Espressif\tools\ccache\4.12.1\ccache-4.12.1-windows-x86_64;' +
                'C:\Espressif\tools\idf-git\2.34.2\cmd;' + $env:PATH
}

function Invoke-Idf { param([string]$Dir, [string[]]$IdfArgs)
    Push-Location $Dir
    try { & python "$env:IDF_PATH\tools\idf.py" @IdfArgs; if ($LASTEXITCODE -ne 0) { throw "idf.py $IdfArgs failed" } }
    finally { Pop-Location }
}

function Invoke-BuildFlash { param([string]$AppDir)
    Use-IdfEnv
    # upstream espressif/mqtt bug: mqtt5_client.c is registered UNCONDITIONALLY
    # (and again conditionally) -> compile error whenever MQTT_PROTOCOL_5 is
    # off. Every managed copy in this repo is patched; a fresh re-download
    # (new managed_components) regresses - self-heal here (2026-07-06).
    $mqttCmake = "$AppDir\managed_components\espressif__mqtt\CMakeLists.txt"
    if (Test-Path $mqttCmake) {
        $content = Get-Content $mqttCmake -Raw
        if ($content -match [regex]::Escape('idf_component_register(SRCS "mqtt5_client.c" "${srcs}"')) {
            $content = $content.Replace('idf_component_register(SRCS "mqtt5_client.c" "${srcs}"',
                                        'idf_component_register(SRCS "${srcs}"')
            Set-Content -Path $mqttCmake -Value $content -Encoding utf8 -NoNewline
            Write-Host "patched upstream mqtt5 SRCS bug in $mqttCmake" -ForegroundColor Yellow
        }
    }
    if (-not (Test-Path "$AppDir\build\flash_args")) { Invoke-Idf $AppDir @('set-target', 'esp32s3') }
    & ninja -C "$AppDir\build"
    if ($LASTEXITCODE -ne 0) { Invoke-Idf $AppDir @('build') }  # first build via idf.py
    Push-Location "$AppDir\build"
    try {
        & python -m esptool --chip esp32s3 -p $Port -b 460800 --before default-reset --after hard-reset write-flash "@flash_args"
        if ($LASTEXITCODE -ne 0) { throw "flash failed" }
    } finally { Pop-Location }
}

# ---------------------------------------------------------------- bench sync
$script:benchSynced = $false
function Sync-BenchTools {
    if ($script:benchSynced) { return }
    $tar = Join-Path $env:TEMP 'wican_sync.tar'
    # stale-tar guard: a failed pack/copy must FAIL the sync, not silently
    # ship the previous archive (burned 2026-07-08: edited test never ran)
    if (Test-Path $tar) { Remove-Item $tar -Force }
    # Pin Windows bsdtar explicitly: when this script is launched from a
    # Git-Bash shell, GNU tar shadows it on PATH and treats the "C:\..."
    # temp path as a REMOTE host ("C:"), silently producing no local file.
    $tarExe = Join-Path $env:SystemRoot 'System32\tar.exe'
    if (-not (Test-Path $tarExe)) { $tarExe = 'tar' } # pre-1809 fallback
    # components live in the meatpi-components repos since the 2026-07-22
    # split; the dev tooling lives HERE since the 2026-07-26 Phase 3 move.
    # OVERLAY each repo's components/ into the same archive path, else the
    # Pi keeps running the pre-split sources as phantom suites (caught
    # 2026-07-26: Pi autopid was 4 days stale).
    $compRoots = Get-ComponentRoots
    Push-Location $repo
    try {
        & $tarExe -cf $tar --exclude '*/build' --exclude '*/build/*' `
            --exclude '*/managed_components' --exclude '*/managed_components/*' `
            --exclude '*/sdkconfig' tools
        foreach ($cr in $compRoots) {
            & $tarExe -rf $tar --exclude '*/build' --exclude '*/build/*' `
                --exclude '*/managed_components' --exclude '*/managed_components/*' `
                --exclude '*/sdkconfig' -C $cr components
        }
        # verify the ARTIFACT, not tar's exit code: bsdtar returns 1 on
        # benign warnings while still producing a valid archive. A missing
        # or trivially small file is the real failure (the old tar was
        # deleted above, so we can't silently ship a stale one).
        if (-not (Test-Path $tar) -or (Get-Item $tar).Length -lt 1MB) {
            throw 'bench sync: tar produced no usable archive'
        }
    } finally { Pop-Location }
    & scp -q $tar "${BenchHost}:/tmp/wican_sync.tar"
    if ($LASTEXITCODE -ne 0) { throw 'bench sync: scp failed' }
    & ssh -o BatchMode=yes $BenchHost "mkdir -p ~/wican && cd ~/wican && tar -xf /tmp/wican_sync.tar && sed -i 's/\r$//' tools/testbench/run_host_tests.sh && chmod +x tools/testbench/run_host_tests.sh"
    if ($LASTEXITCODE -ne 0) { throw 'bench sync failed' }
    $script:benchSynced = $true
}

# Run a command on the Pi, stream its lines to the console AND return them.
function Invoke-Bench { param([string]$Cmd)
    Sync-BenchTools
    return & ssh -o BatchMode=yes $BenchHost $Cmd | ForEach-Object { Write-Host $_; $_ }
}

# ---------------------------------------------------------------- test kinds
function Invoke-HostSuites { param([string]$Only)
    $filter = ''; if ($Only) { $filter = " $Only" }
    $out = Invoke-Bench "~/wican/tools/testbench/run_host_tests.sh$filter"
    $rc = $LASTEXITCODE
    Save-StageLog "host_suites$(if ($Only) { "_$Only" })" $out
    # per-suite rows straight from the script's summary block
    $inSummary = $false
    $totalNote = ''
    foreach ($line in $out) {
        if ($line -match '^=== SUMMARY ===') { $inSummary = $true; continue }
        if ($inSummary -and $line -match '^TOTAL: (.+)$') { $totalNote = $Matches[1]; continue }
        if ($inSummary -and $line -match '^(\S+): (.+)$') {
            $suite = $Matches[1]; $verdict = $Matches[2]   # -notmatch below clobbers $Matches
            $r = 'PASS'; if ($verdict -notmatch '^PASS') { $r = 'FAIL'; $script:anyFail = $true }
            $script:stages += [pscustomobject]@{ Stage = "host $suite"; Result = $r; Seconds = ''; Notes = $verdict }
        }
    }
    if ($rc -ne 0) { throw "host suites FAILED ($totalNote)" }
    $script:note = $totalNote
}

function Invoke-TargetApp { param([string]$Comp)
    Invoke-BuildFlash (Get-CompDir "$Comp\test_apps")
    if ($Comp -eq 'ble_manager') {
        # app idles after BLE READY; the pass verdict comes from the Pi's
        # BLE controller driving it (tools/testbench/ble/ble_bench.py)
        & python "$repo\tools\testbench\lib\serial_capture.py" $Port 115200 60 "$env:TEMP\wican_$Comp.log" 'BLE READY'
        if ($LASTEXITCODE -ne 0) { throw "ble_manager app never printed BLE READY (log: $env:TEMP\wican_$Comp.log)" }
        $out = Invoke-Bench "for d in `$(bluetoothctl devices | awk '{print `$2}'); do bluetoothctl remove `$d > /dev/null 2>&1; done; cd ~/wican && sudo PYTHONPATH=/home/meatpi/.local/lib/python3.11/site-packages timeout 120 python3 -u tools/testbench/ble/ble_bench.py"
        Save-StageLog 'target_ble_manager_bench' $out
        if (-not ($out | Select-String -Quiet 'BLE BENCH PASS')) { throw 'ble_bench.py did not print BLE BENCH PASS' }
        $script:note = 'BLE BENCH PASS (driven from rpi001)'
        return
    }
    & python "$repo\tools\testbench\lib\serial_capture.py" $Port 115200 90 "$env:TEMP\wican_$Comp.log"
    if ($LASTEXITCODE -ne 0) { throw "$Comp target test FAILED (log: $env:TEMP\wican_$Comp.log)" }
    $script:note = 'TEST DONE, no crash signatures'
}

function Invoke-HilSuite {
    Use-IdfEnv
    $hilDir = Get-CompDir 'wifi_manager\test_apps_hil'
    if ($Flash) { Invoke-BuildFlash $hilDir }
    # run from THIS repo root so pytest.ini + conftest.py (the HIL
    # fixtures) apply regardless of where the suite dir lives
    Push-Location $repo
    try {
        & python -m pytest $hilDir -v --dut-port $Port --bench-host $BenchHost
        if ($LASTEXITCODE -ne 0) { throw 'HIL suite FAILED' }
    } finally { Pop-Location }
    $script:note = 'pytest green'
}

# Live checks: the COMPOSED main firmware must be on the DUT, reachable at
# $DutIp, with the required channels/bridges enabled in settings (TESTING.md).
function Invoke-LiveChecks {
    Get-DutConditions
    Invoke-Stage 'live cli_ws (CLI over WebSocket)' {
        $out = Invoke-Bench "cd ~/wican && python3 -u tools/testbench/wifi/cli_ws_test.py $DutIp"
        Save-StageLog 'live_cli_ws' $out
        if (-not ($out | Select-String -Quiet 'CLI WS PASS')) { throw 'no CLI WS PASS (ws_cli channel + cli<->ws_cli bridge enabled?)' }
        $script:note = 'CLI WS PASS'
    }
    Invoke-Stage 'live ws_obd (OBD over WebSocket)' {
        $out = Invoke-Bench "cd ~/wican && python3 -u tools/testbench/wifi/ws_live_test.py $DutIp"
        Save-StageLog 'live_ws_obd' $out
        if (-not ($out | Select-String -Quiet 'WS LIVE PASS')) { throw 'no WS LIVE PASS (ws_obd channel + obd<->ws_obd bridge enabled?)' }
        $script:note = 'WS LIVE PASS'
    }
    Invoke-Stage 'live dbc_real (real-DBC parser cross-check)' {
        $out = Invoke-Bench "cd ~/wican && python3 -u tools/testbench/can/dbc_real_test.py $DutIp"
        Save-StageLog 'live_dbc_real' $out
        if (-not ($out | Select-String -Quiet 'DBC REAL PASS')) { throw 'no DBC REAL PASS (fixtures/dbc synced? /api/autopid/dbc reachable?)' }
        $script:note = 'DBC REAL PASS'
    }
    # stack + memory audit: every task's stack_hw (PSRAM stacks corrupt
    # silently on overflow — 2026-07-22 lesson), the ephemeral job tasks
    # via their exit log line, and internal/PSRAM heap floors.
    Invoke-Stage 'live stack_audit (task watermarks + heap floors)' {
        $out = Invoke-Bench "cd ~/wican && python3 -u tools/testbench/system/stack_audit_test.py $DutIp"
        Save-StageLog 'live_stack_audit' $out
        if (-not ($out | Select-String -Quiet 'STACK AUDIT PASS')) { throw 'no STACK AUDIT PASS (see watermark table in the log)' }
        $script:note = ($out | Select-String 'STACK AUDIT PASS' | Select-Object -Last 1).Line
    }
    # event_manager integration: discovery + validation + timer->mqtt.publish
    # (template render) + cooldown. Runs ON the Pi (broker + DUT reachable).
    Invoke-Stage 'live event_manager (rules/timers/templates/cooldown)' {
        $out = Invoke-Bench "cd ~/wican && python3 -u tools/testbench/system/event_manager_bench.py $DutIp $DeviceId"
        Save-StageLog 'live_event_manager' $out
        if (-not ($out | Select-String -Quiet 'EVENT MGR PASS')) { throw 'no EVENT MGR PASS (mqtt enabled + broker reachable?)' }
        $script:note = 'EVENT MGR PASS'
    }
    # event_manager worker pool: a slow http.post must not stall the dispatcher
    Invoke-Stage 'live em_worker (non-blocking action pool)' {
        if (Skip-IfNoUsbNcm) { return }
        $py = 'C:\Espressif\tools\python\v6.0.2\venv\Scripts\python.exe'
        $out = & $py -u "$repo\tools\testbench\system\em_worker_bench.py" 192.168.82.1 $BenchHost | ForEach-Object { Write-Host $_; $_ }
        Save-StageLog 'live_em_worker' $out
        if (-not ($out | Select-String -Quiet 'EM WORKER PASS')) { throw 'no EM WORKER PASS' }
        $script:note = 'EM WORKER PASS'
    }
    # HA integration: mock HA receiver on the Pi, register via /api/webhook,
    # assert {status+device_id, autopid_data, config} telemetry + failover +
    # stats. Runs on the PC (USB mgmt + ssh receiver on the Pi AP).
    Invoke-Stage 'live ha_webhook (Home Assistant telemetry)' {
        if (Skip-IfNoUsbNcm) { return }
        $py = 'C:\Espressif\tools\python\v6.0.2\venv\Scripts\python.exe'
        $out = & $py -u "$repo\tools\testbench\ha\ha_webhook_bench.py" 192.168.82.1 $BenchHost | ForEach-Object { Write-Host $_; $_ }
        Save-StageLog 'live_ha_webhook' $out
        if (-not ($out | Select-String -Quiet 'HA WEBHOOK PASS')) { throw 'no HA WEBHOOK PASS (autopid enabled? Pi AP 10.42.0.1 reachable from DUT STA?)' }
        $script:note = 'HA WEBHOOK PASS'
    }
    # data_logger two-stream matrix (formats x params/CAN, rotation,
    # retention, gate) + the CAN-rate benchmark. Runs on the PC (USB
    # mgmt + ws_cli + PCAN); CAN legs self-skip without a PCAN.
    Invoke-Stage 'live datalog (data_logger streams + formats)' {
        if (Skip-IfNoUsbNcm) { return }
        $py = 'C:\Espressif\tools\python\v6.0.2\venv\Scripts\python.exe'
        $out = & $py -u "$repo\tools\testbench\system\data_logger_bench.py" 192.168.82.1 | ForEach-Object { Write-Host $_; $_ }
        Save-StageLog 'live_datalog' $out
        if (-not ($out | Select-String -Quiet 'DATALOG PASS')) { throw 'no DATALOG PASS (SD card in? PCAN plugged? ws_cli enabled?)' }
        foreach ($m in ($out | Select-String '^METRIC (.+) = (.+)$')) {
            Add-Metric "DL $($m.Matches[0].Groups[1].Value)" $m.Matches[0].Groups[2].Value
        }
        $script:note = 'DATALOG PASS'
    }
    # bus-off recovery fault injection: PCAN 50k storm + DUT id-0 TX
    # forces a REAL bus-off (TEC 256), asserts the automatic recovery
    # (can_core_recovery). Self-skips without a PCAN.
    Invoke-Stage 'live canrec (bus-off auto-recovery)' {
        if (Skip-IfNoUsbNcm) { return }
        $py = 'C:\Espressif\tools\python\v6.0.2\venv\Scripts\python.exe'
        $out = & $py -u "$repo\tools\testbench\can\can_recovery_bench.py" 192.168.82.1 | ForEach-Object { Write-Host $_; $_ }
        Save-StageLog 'live_canrec' $out
        if ($out | Select-String -Quiet 'SKIP: python-can') { $script:note = 'SKIP (no python-can)'; return }
        if (-not ($out | Select-String -Quiet 'CANREC PASS')) { throw 'no CANREC PASS (PCAN plugged? can_manager enabled?)' }
        foreach ($m in ($out | Select-String '^METRIC (.+) = (.+)$')) {
            Add-Metric "CANREC $($m.Matches[0].Groups[1].Value)" $m.Matches[0].Groups[2].Value
        }
        $script:note = 'CANREC PASS'
    }
    # external log sinks: TCP tail / UDP collector / ws_log / SD file
    # conservation with the `logsinks emit` known generator. Runs ON the
    # Pi (it binds the collector). Reboots the DUT 3x, restores settings.
    Invoke-Stage 'live logsinks (external log sinks conservation)' {
        $out = Invoke-Bench "cd ~/wican && python3 -u tools/testbench/system/log_sinks_bench_test.py $DutIp"
        Save-StageLog 'live_logsinks' $out
        if (-not ($out | Select-String -Quiet 'LOG SINKS BENCH PASS')) { throw 'no LOG SINKS BENCH PASS (SD card in? ws_cli enabled?)' }
        $script:note = 'LOG SINKS BENCH PASS'
    }
    # auto-SKIP when the USB connector isn't hosting that device
    Invoke-Stage 'live usb-eth' { Invoke-UsbEthBench }
    Invoke-Stage 'live espnetlink' { Invoke-EspnetlinkBench }
    # BLE CLI wedge regression: enables BLE (WiFi stays on), drives the
    # CLI characteristics from the Pi's UB500 while GATT reads run
    # concurrently, restores BLE off. Reboots the DUT twice.
    Invoke-Stage 'live blecli (BLE CLI wedge regression)' {
        if (Skip-IfNoUsbNcm) { return }
        $py = 'C:\Espressif\tools\python\v6.0.2\venv\Scripts\python.exe'
        $out = & $py -u "$repo\tools\testbench\ble\ble_cli_wedge_test.py" --usb 192.168.82.1 --bench $BenchHost | ForEach-Object { Write-Host $_; $_ }
        Save-StageLog 'live_blecli' $out
        if (-not ($out | Select-String -Quiet 'BLE CLI WEDGE PASS')) { throw 'no BLE CLI WEDGE PASS (UB500 on the Pi? BLE toggle failed?)' }
        $script:note = 'BLE CLI WEDGE PASS'
    }
    # LAST: reboots the DUT twice (settings flips); runs on the PC (USB
    # mgmt link + ssh probes from the Pi)
    Invoke-Stage 'live wifi_gate (network-trust lockdown)' {
        if (Skip-IfNoUsbNcm) { return }
        $py = 'C:\Espressif\tools\python\v6.0.2\venv\Scripts\python.exe'
        $out = & $py -u "$repo\tools\testbench\wifi\wifi_gate_live_test.py" 192.168.82.1 $DutIp $BenchHost | ForEach-Object { Write-Host $_; $_ }
        Save-StageLog 'live_wifi_gate' $out
        if (-not ($out | Select-String -Quiet 'WIFI GATE PASS')) { throw 'no WIFI GATE PASS (USB link up? sta_trusted schema present?)' }
        $script:note = 'WIFI GATE PASS'
    }
}

# USB-Ethernet bench: needs the adapter physically on the WiCAN USB connector
# (host mode) + Pi eth-bench profile. SKIPs itself when the fixture is absent
# (e.g. the connector is cabled to the PC for usb_obd work).
function Invoke-UsbEthBench {
    if ($script:bench -and -not $script:bench.UsbEth) {
        $script:skipStage = $true
        $script:note = 'USB-Ethernet adapter not present (connector not in host-eth mode)'
        return
    }
    $out = Invoke-Bench "cd ~/wican && python3 -u tools/testbench/usb/usb_eth_bench.py $DutIp"
    Save-StageLog 'usbeth_bench' $out
    if (-not ($out | Select-String -Quiet 'USB ETH TARGET PASS')) { throw 'no USB ETH TARGET PASS' }
    $script:note = 'USB ETH TARGET PASS'
}

# ESPNetLink (LTE dongle) bench: RNDIS data path + CDC-ACM modem console.
# SKIPs itself when the dongle isn't present.
function Invoke-EspnetlinkBench {
    if ($script:bench -and -not $script:bench.Espnetlink) {
        $script:skipStage = $true
        $script:note = 'espnetlink not present (no RNDIS + ACM console)'
        return
    }
    $out = Invoke-Bench "cd ~/wican && python3 -u tools/testbench/usb/espnetlink_bench.py"
    Save-StageLog 'espnetlink_bench' $out
    if (-not ($out | Select-String -Quiet 'ESPNETLINK TARGET PASS')) { throw 'no ESPNETLINK TARGET PASS' }
    $script:note = 'ESPNETLINK TARGET PASS (RNDIS data + ACM modem status)'
}

# Performance battery vs the composed main. WiFi-path benches run FIRST -
# a BLE client connect makes interface_manager suspend WiFi, so ble_ab goes
# last. Every scenario's summary line lands in the report's numbers section.
function Invoke-PerfBattery {
    Get-DutConditions
    Invoke-Stage 'perf mqtt rtt' {
        $out = Invoke-Bench "cd ~/wican && python3 -u tools/testbench/wifi/mqtt_bench.py --device-id $DeviceId --scenario rtt --n 200 --size 512"
        Save-StageLog 'perf_mqtt_rtt' $out
        $line = $out | Select-String '^rtt size=' | Select-Object -Last 1
        if (-not $line) { throw 'no rtt summary (mqtt enabled + broker on the Pi?)' }
        Add-Metric 'MQTT RTT 512B' $line.Line
        $script:note = $line.Line
    }
    Invoke-Stage 'perf mqtt drain 4000B paced' {
        # size cap is BENCH_MAX_SIZE=4000 (main_bench.c) - a larger size is
        # silently ignored by the device
        $out = Invoke-Bench "cd ~/wican && python3 -u tools/testbench/wifi/mqtt_bench.py --device-id $DeviceId --scenario throughput --n 300 --size 4000 --gap-ms 4"
        Save-StageLog 'perf_mqtt_drain' $out
        $line = $out | Select-String '^throughput size=' | Select-Object -Last 1
        if (-not $line) { throw 'no throughput summary' }
        if ($line.Line -match 'broker rx 0 msgs') { throw "device never published: $($line.Line)" }
        Add-Metric 'MQTT drain 4000B gap 4ms' $line.Line
        $script:note = $line.Line
    }
    Invoke-Stage 'perf ws latency (echo via /ws/can)' {
        $out = Invoke-Bench "cd ~/wican && python3 -u tools/testbench/wifi/ws_bench.py --host $DutIp --scenario latency --ws-path /ws/can"
        Save-StageLog 'perf_ws_latency' $out
        $line = $out | Select-String '^latency n=' | Select-Object -Last 1
        if (-not $line) { throw 'no latency summary (ws_can<->obd0 bridge enabled?)' }
        Add-Metric 'WS latency (ws_can<->obd0)' $line.Line
        $script:note = $line.Line
    }
    Invoke-Stage 'perf ws obd_poll' {
        $out = Invoke-Bench "cd ~/wican && python3 -u tools/testbench/wifi/ws_bench.py --host $DutIp --scenario obd_poll --secs 30"
        Save-StageLog 'perf_ws_obd_poll' $out
        $line = $out | Select-String '^obd_poll n=' | Select-Object -Last 1
        if (-not $line) { throw 'no obd_poll summary' }
        Add-Metric 'WS OBD poll 30s' $line.Line
        $script:note = $line.Line
    }
    Invoke-Stage 'perf ble A/B (suspends WiFi while connected)' {
        $out = Invoke-Bench "for d in `$(bluetoothctl devices | awk '{print `$2}'); do bluetoothctl remove `$d > /dev/null 2>&1; done; cd ~/wican && sudo PYTHONPATH=/home/meatpi/.local/lib/python3.11/site-packages timeout 180 python3 -u tools/testbench/ble/ble_ab.py --dut $DutIp"
        Save-StageLog 'perf_ble_ab' $out
        $line = $out | Select-String 'BLE AB DONE' | Select-Object -Last 1
        if (-not $line) { throw 'no BLE AB DONE summary' }
        Add-Metric 'BLE A/B (RTT + TX/RX pipes)' $line.Line
        $script:note = 'BLE AB DONE (numbers in perf section)'
    }
}

function Show-Inventory {
    Write-Host "host suites (.\test.ps1 host [component]):" -ForegroundColor Cyan
    $seen = @{}
    foreach ($root in Get-ComponentRoots) {
        Get-ChildItem "$root\components\*\host_test\CMakeLists.txt" -ErrorAction SilentlyContinue | ForEach-Object {
            $seen[$_.Directory.Parent.Name] = $true }
    }
    $seen.Keys | Sort-Object | ForEach-Object { Write-Host "  $_" }
    Write-Host "target apps (.\test.ps1 target <component>):" -ForegroundColor Cyan
    foreach ($t in $targets) { Write-Host "  $t" }
    Write-Host 'other kinds:' -ForegroundColor Cyan
    Write-Host '  hil    - WiFi hardware-in-the-loop pytest (add -Flash the first time)'
    Write-Host '  live   - cli_ws + ws_obd + dbc_real + stack_audit + usb-eth (auto-skip) vs the composed main'
    Write-Host '  stackaudit - static frame-vs-stack audit (tools/stack_audit.py) + live task watermarks/heap floors'
    Write-Host '  dwc2       - DWC2 kill-vs-ISR race hammer (espnetlink dongle on the USB host port)'
    Write-Host '  perf   - mqtt rtt/drain, ws latency/obd_poll, ble A/B'
    Write-Host '  usbeth - USB-Ethernet bench (needs the adapter on the USB connector)'
    Write-Host '  espnetlink - LTE dongle bench: RNDIS data path + CDC-ACM modem console'
    Write-Host '  all    - host + every target app + hil'
    Write-Host ''
    Write-Host 'Every kind starts with a BENCH PREFLIGHT (skip: -SkipBenchCheck): COM'
    Write-Host 'ports (CH342 = connector->PC vs host mode), Pi, DUT, fixtures. Legs'
    Write-Host 'whose physical fixture is absent SKIP instead of failing.'
    Write-Host 'manual-only (see TESTING.md): system_bench.py, ble_blast.py, socket_bench.py, obd_*_check.py, vpn_bench_target.py'
}

# ---------------------------------------------------------------- dispatch
Use-IdfEnv
try {
    $benchOk = $true
    if ($What -ne 'list' -and -not $SkipBenchCheck) {
        Invoke-Stage 'bench preflight' { Invoke-BenchCheck }
        if ($script:anyFail) {
            $benchOk = $false
            Write-Host 'bench preflight FAILED - fix the bench (or -SkipBenchCheck to override)' -ForegroundColor Red
        }
    }
    if ($benchOk) { switch ($What) {
        'list'   { Show-Inventory }
        'host'   { Invoke-Stage "host suites$(if ($Component) { " ($Component)" })" { Invoke-HostSuites $Component } }
        'target' {
            if (-not $Component) { throw "usage: .\test.ps1 target <component>  (one of: $($targets -join ', '))" }
            Invoke-Stage "target $Component" { Invoke-TargetApp $Component }
        }
        'hil'    { Invoke-Stage 'wifi HIL suite' { Invoke-HilSuite } }
        'live'   { Invoke-LiveChecks }
        'perf'   { Invoke-PerfBattery }
        'usbeth' { Invoke-Stage 'usb-eth bench' { Invoke-UsbEthBench } }
        'stackaudit' {
            # static half: compiler frame sizes vs task stacks (local build)
            Invoke-Stage 'static stack audit (tools/stack_audit.py)' {
                # the analyzer stays in wican-fw (public tool) and walks the
                # fw build tree relative to itself
                $out = & python "$fwRepo\tools\stack_audit.py" 2>&1 | ForEach-Object { Write-Host $_; $_ }
                Save-StageLog 'stack_audit_static' $out
                if (-not ($out | Select-String -Quiet '== candidate overflows')) { throw 'stack_audit.py produced no report (build with .su files present?)' }
                $script:note = ($out | Select-String 'candidate' | Select-Object -Last 1).Line
            }
            # runtime half: live watermarks + heap floors on the DUT
            Invoke-Stage 'live stack_audit (task watermarks + heap floors)' {
                $out = Invoke-Bench "cd ~/wican && python3 -u tools/testbench/system/stack_audit_test.py $DutIp"
                Save-StageLog 'live_stack_audit' $out
                if (-not ($out | Select-String -Quiet 'STACK AUDIT PASS')) { throw 'no STACK AUDIT PASS (see watermark table in the log)' }
                $script:note = ($out | Select-String 'STACK AUDIT PASS' | Select-Object -Last 1).Line
            }
        }
        'espnetlink' { Invoke-Stage 'espnetlink bench' { Invoke-EspnetlinkBench } }
        'logsinks' {
            # External log sinks (log_sinks component): TCP tail / UDP
            # collector / ws_log / SD file conservation vs the `logsinks
            # emit` known generator. Runs ON the Pi; reboots the DUT 3x,
            # restores settings.
            Invoke-Stage 'live logsinks (external log sinks conservation)' {
                $out = Invoke-Bench "cd ~/wican && python3 -u tools/testbench/system/log_sinks_bench_test.py $DutIp"
                Save-StageLog 'live_logsinks' $out
                if (-not ($out | Select-String -Quiet 'LOG SINKS BENCH PASS')) { throw 'no LOG SINKS BENCH PASS (SD card in? ws_cli enabled?)' }
                $script:note = 'LOG SINKS BENCH PASS'
            }
        }
        'blesec' {
            # BLE bonding + security end-to-end. Needs BLE off the RAM cliff,
            # so it switches the DUT to WiFi-off/BLE-on, runs the pair/gate/
            # persist scenarios on the Pi's UB500, then restores apsta+BLE-off.
            # Reboots the DUT ~3x; leaves it back on WiFi. Runs on the PC.
            Invoke-Stage 'live blesec (BLE bonding + security)' {
                $py = 'C:\Espressif\tools\python\v6.0.2\venv\Scripts\python.exe'
                $out = & $py -u "$repo\tools\testbench\ble\ble_security_test.py" --usb 192.168.82.1 --bench $BenchHost --passkey 421337 --setup 2>&1 | ForEach-Object { Write-Host $_; $_ }
                Save-StageLog 'live_blesec' $out
                # always restore the bench even if the test threw
                & $py -u "$repo\tools\testbench\ble\ble_security_test.py" --usb 192.168.82.1 --restore 2>&1 | Out-Null
                if (-not ($out | Select-String -Quiet 'BLE SECURITY PASS')) { throw 'no BLE SECURITY PASS (UB500 present? bleak on the Pi?)' }
                $script:note = 'BLE SECURITY PASS'
            }
        }
        'sleep'  {
            # Sleep-mode HIL (OWON P4305 on COM2016 + Pi; ~10 min).
            Invoke-Stage 'sleep bench' {
                $out = & 'C:\Espressif\tools\python\v6.0.2\venv\Scripts\python.exe' -u "$repo\tools\testbench\sleep\sleep_bench_test.py" 2>&1 | ForEach-Object { Write-Host $_; $_ }
                Save-StageLog 'sleep_bench' $out
                if (-not ($out | Select-String -Quiet 'SLEEP BENCH PASS')) { throw 'no SLEEP BENCH PASS (PSU on COM2016? sleep enabled?)' }
            }
        }
        'sleepmatrix' {
            # Full sleep robustness matrix (~90 min; SLEEP_MATRIX.md).
            Invoke-Stage 'sleep matrix' {
                $out = & 'C:\Espressif\tools\python\v6.0.2\venv\Scripts\python.exe' -u "$repo\tools\testbench\sleep\sleep_matrix_test.py" 2>&1 | ForEach-Object { Write-Host $_; $_ }
                Save-StageLog 'sleep_matrix' $out
                if (-not ($out | Select-String -Quiet 'SLEEP MATRIX PASS')) { throw 'no SLEEP MATRIX PASS' }
            }
        }
        'dwc2' {
            # DWC2 kill-vs-ISR race hammer (~10 min; needs the espnetlink
            # dongle on the USB host port + usb_host_manager/usb_acm_cli
            # enabled).
            Invoke-Stage 'DWC2 hammer' {
                $out = & 'C:\Espressif\tools\python\v6.0.2\venv\Scripts\python.exe' -u "$repo\tools\testbench\usb\dwc2_hammer_test.py" 2>&1 | ForEach-Object { Write-Host $_; $_ }
                Save-StageLog 'dwc2_hammer' $out
                if (-not ($out | Select-String -Quiet 'DWC2 HAMMER PASS')) { throw 'no DWC2 HAMMER PASS (dongle plugged? host mode enabled?)' }
            }
        }
        'conserve' {
            # Counter-conservation benches (standard §7 performance-truth).
            Invoke-Stage 'CAN conservation' {
                $py = 'C:\Espressif\tools\python\v6.0.2\venv\Scripts\python.exe'
                $out = & $py -u "$repo\tools\testbench\can\can_conservation_test.py" 2>&1 | ForEach-Object { Write-Host $_; $_ }
                Save-StageLog 'can_conservation' $out
                if (-not ($out | Select-String -Quiet 'CAN CONSERVATION PASS')) { throw 'no CAN CONSERVATION PASS (PCAN wired?)' }
            }
            Invoke-Stage 'USB-NCM conservation' {
                # needs the DUT in role=device/class=ncm (TESTING.md);
                # SKIPs when the NCM link is absent
                if (-not (Test-Connection -ComputerName 192.168.82.1 -Count 1 -Quiet -ErrorAction SilentlyContinue)) {
                    $script:note = 'SKIP (no NCM link at 192.168.82.1)'
                    Write-Host 'SKIP: DUT not in NCM device role' -ForegroundColor Yellow
                    return
                }
                $out = & 'C:\Espressif\tools\python\v6.0.2\venv\Scripts\python.exe' -u "$repo\tools\testbench\usb\usb_ncm_conservation_test.py" 2>&1 | ForEach-Object { Write-Host $_; $_ }
                Save-StageLog 'usb_ncm_conservation' $out
                if (-not ($out | Select-String -Quiet 'USB NCM CONSERVATION PASS')) { throw 'no USB NCM CONSERVATION PASS' }
            }
            Invoke-Stage 'WiFi conservation' {
                $out = & 'C:\Espressif\tools\python\v6.0.2\venv\Scripts\python.exe' -u "$repo\tools\testbench\wifi\wifi_conservation_test.py" 2>&1 | ForEach-Object { Write-Host $_; $_ }
                Save-StageLog 'wifi_conservation' $out
                if (-not ($out | Select-String -Quiet 'WIFI CONSERVATION PASS')) { throw 'no WIFI CONSERVATION PASS (iperf on the Pi?)' }
            }
        }
        'all'    {
            # NOTE: live/perf are separate runs - after `all` the DUT is left
            # with the last test app, not the composed main (TESTING.md).
            Invoke-Stage 'host suites' { Invoke-HostSuites $null }
            foreach ($c in $targets) { Invoke-Stage "target $c" { Invoke-TargetApp $c } }
            # the target loop leaves the ble_manager TEST APP on the DUT -
            # HIL must reflash its own app or it times out on HIL READY
            # (the 2026-07-05 `all` HIL failure)
            $script:Flash = $true
            Invoke-Stage 'wifi HIL suite' { Invoke-HilSuite }
        }
    } }
} finally {
    Write-Report
}
if ($script:anyFail) { Write-Host 'RESULT: FAIL' -ForegroundColor Red; exit 1 }
if ($What -ne 'list') { Write-Host 'RESULT: PASS' -ForegroundColor Green }
