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
    [Parameter(Mandatory = $true, Position = 0)][ValidateSet('check', 'host', 'target', 'hil', 'live', 'perf', 'usbeth', 'espnetlink', 'blesec', 'blehttp', 'blej2534', 'blecanraw', 'blephy', 'blethru', 'sleep', 'sleepmatrix', 'conserve', 'autobaud', 'profilee2e', 'slcan', 'j2534auto', 'j2534std', 'wwh', 'eutruck', 'j1939', 'crashnote', 'vpnpause', 'vpnroutes', 'garage', 'espnleasy', 'usbgps', 'usbdetach', 'abrp', 'backup', 'fwupdate', 'espnlota', 'logclock', 'sdformat', 'stackaudit', 'dwc2', 'logsinks', 'release', 'all', 'list')]
    [string]$What,
    [Parameter(Position = 1)][string]$Component,
    [ValidateSet('6.0.2', '6.1')][string]$Idf = '6.1',  # the ESP-IDF the build stages use (6.1 since 6.0.0-beta.5, 2026-10-27; 6.0.2 stays installed; build.ps1 -Idf has the same table)
    [string]$Port = 'COM10',  # UART0 external USB-serial = console/flash
                              # ('auto' = whatever the preflight detects);
                              # works in EVERY USB-connector mode. COM numbers
                              # drift on every re-plug, so the preflight falls
                              # back BY PnP NAME: the CH344 quad-UART channel B
                              # (the rig's WiCAN UART0: 2 Mbaud console, 460800
                              # flash), then CH342 A (the DUT's own connector
                              # cabled to the PC, device role; gone when it
                              # hosts the USB-Ethernet adapter / a dongle).
    [string]$BenchHost = 'rpi001',
    [string]$DutIp = '',      # '' = resolve the DUT on the Pi: its hotspot DHCP
                              # lease (hostname wican_<DeviceId>), then mDNS;
                              # pass an address to pin it
    [string]$DeviceId = '68ee8f5a653d',   # the bench unit (2026-09-19)
    [string]$FwRepo,          # wican-fw checkout; default: WICAN_FW_PATH env,
                              # then known sibling locations (see below)
    [switch]$Flash,
    [switch]$NoReport,
    [switch]$SkipBenchCheck,  # bypass the physical-bench preflight
    [switch]$NoConsole,       # PC bench stages: do not open the DUT's console
                              # port beside the run (Start-ConsoleTail)
    [switch]$Factory,         # backup stage: run b9 too, a REAL factory reset of
                              # the DUT (the full wipe since 2026-10-26) and the
                              # undo from its SD copy; the reset DUT is reached
                              # on its own AP through the Pi (a second forward
                              # and the bench's --join-ap)
    [switch]$SdFormat         # sdformat stage: really format the card WiCAN
                              # cannot read (it erases the card)
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
# (later roots override, same precedence as the build's overlays).
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

# The bench's python: the IDF v6.0.2 venv (esptool v5 spelling, the bench
# modules). 2026-10-19: its base interpreter vanished from
# C:\Espressif\tools\python (the venv launcher prints "No Python at ..." and
# exits without running anything), so the first interpreter that runs is
# used: the v5.5.3 venv (3.10; requests, pyserial and websocket present;
# esptool 4.x, so the flash stages want the v6 venv back), then python on
# PATH. The bench scripts themselves need the standard library only.
function Resolve-BenchPython {
    $cands = @('C:\Espressif\tools\python\v6.0.2\venv\Scripts\python.exe',
               'C:\Espressif\tools\python_env\idf6.0_py3.10_env\Scripts\python.exe',  # the stand-in made 2026-10-19 (esptool 5)
               'C:\Espressif\tools\python\v5.5.3\venv\Scripts\python.exe',
               'python')
    foreach ($c in $cands) {
        try {
            $v = & $c -c 'import sys; print(sys.version.split()[0])' 2>$null
            if ($LASTEXITCODE -eq 0 -and $v) {
                if ($c -notlike '*v6.0.2*') { Write-Host "bench: python = $c ($v); the IDF v6.0.2 venv does not run (its base interpreter is missing)" -ForegroundColor Yellow }
                return $c
            }
        } catch {}
    }
    throw 'no python interpreter runs on this PC'
}
$script:BenchPy = Resolve-BenchPython

function Save-StageLog { param([string]$Name, $Text)
    if (-not (Test-Path $script:logDir)) { New-Item -ItemType Directory -Force $script:logDir | Out-Null }
    $safe = $Name -replace '[^\w.-]', '_'
    ($Text | Out-String) | Set-Content -Encoding utf8 (Join-Path $script:logDir "$safe.log")
}

# The DUT's console beside a PC bench stage. A reset nobody asked for says why
# on the console only (the panic handler does not write into the log ring),
# and a bench that runs over HTTP has nobody watching it: the EU truck bench's
# first run (2026-10-03) lost the device to such a reset, and the reason with
# it. DTR/RTS stay off, so the open does not reset the DUT. No console port,
# or a port somebody else holds: no capture, the stage runs all the same.
# -NoConsole leaves the port alone.
function Start-ConsoleTail { param([string]$Name)
    try {
        if ($NoConsole -or -not ($script:Port -match '^COM\d+$')) { return $null }
        if (-not (Test-Path $script:logDir)) { New-Item -ItemType Directory -Force $script:logDir | Out-Null }
        $safe = $Name -replace '[^\w.-]', '_'
        $log = Join-Path $script:logDir "console_$safe.log"
        $stop = "$log.stop"
        if (Test-Path $stop) { Remove-Item $stop -Force -Confirm:$false }
        $p = Start-Process -FilePath $script:BenchPy -PassThru -WindowStyle Hidden `
            -ArgumentList @('-u', "`"$repo\tools\testbench\lib\console_tail.py`"", $script:Port, "`"$log`"", '--stop-file', "`"$stop`"") `
            -RedirectStandardOutput "$log.keys"
        return [pscustomobject]@{ Proc = $p; Log = $log; Stop = $stop; Keys = "$log.keys" }
    } catch {
        Write-Host "console: no capture ($($_.Exception.Message))" -ForegroundColor Yellow
        return $null
    }
}

function Stop-ConsoleTail { param($Tail)
    if ($null -eq $Tail) { return }
    try {
        New-Item -ItemType File -Path $Tail.Stop -Force | Out-Null
        if (-not $Tail.Proc.WaitForExit(6000)) { Stop-Process -Id $Tail.Proc.Id -Force -Confirm:$false }
        Remove-Item $Tail.Stop -Force -Confirm:$false -ErrorAction SilentlyContinue
        $keys = @(Get-Content $Tail.Keys -ErrorAction SilentlyContinue)
        $crash = @($keys | Where-Object { $_ -match 'Guru Meditation|wdt timeout|Task watchdog|Backtrace:|abort\(\) was called|assert failed|stack overflow|CORRUPT HEAP' })
        if ($crash.Count -gt 0) {
            Write-Host "console: the DUT crashed during this stage (the whole console: $($Tail.Log)):" -ForegroundColor Red
            $crash | Select-Object -First 16 | ForEach-Object { Write-Host "  $_" }
        }
        # The crash park (2026-10-05): three runs in a row that crash within ten
        # minutes of their start, and the firmware sleeps instead of starting a
        # fourth time. A bench over HTTP only sees a DUT that stopped answering,
        # and the DUT prints its one line and is silent from then on: say it here.
        $parkAt = -1
        for ($i = 0; $i -lt $keys.Count; $i++) { if ($keys[$i] -match 'WICAN PARK') { $parkAt = $i } }
        if ($parkAt -ge 0) {
            $back = @($keys | Select-Object -Skip ($parkAt + 1) | Where-Object { $_ -match 'restart_tracker: boot' }).Count -gt 0
            if ($back) {
                Write-Host "console: the DUT PARKED ITSELF during this stage (three crashes in a row) and started again later:" -ForegroundColor Red
            } else {
                Write-Host "console: THE DUT PARKED ITSELF during this stage and is still parked: three runs in a row crashed before any had been up ten minutes, so the firmware sleeps instead of starting again (LED breathing red, about 47 mA, no network)." -ForegroundColor Red
            }
            Write-Host "  $($keys[$parkAt])"
            $keys | Where-Object { $_ -match 'previous run crashed' } | Select-Object -Last 3 | ForEach-Object { Write-Host "  $_" }
            if (-not $back) {
                Write-Host "  bring it back:  python tools\testbench\lib\owon_psu.py cycle   (or hold its button until the LED turns sky blue: one try, and the history keeps every note)" -ForegroundColor Red
            }
            Write-Host "  then the cause: GET /api/restart/report (the crash that parked it, kept in flash), python tools\crash_decode.py --url http://<device>; TESTING.md, 'When the DUT disappears in the middle of a test'" -ForegroundColor Red
        }
        Remove-Item $Tail.Keys -Force -Confirm:$false -ErrorAction SilentlyContinue
    } catch {
        Write-Host "console: capture not closed cleanly ($($_.Exception.Message))" -ForegroundColor Yellow
    }
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
#     COM7/COM6, needed for usb_obd/port-B legs) OR hosting a device (the
#     USB-Ethernet adapter / a dongle, needed for the usbeth leg). Never both.
#   - console/flash always works via the UART0 external adapter (COM10).
#   - live/perf need the composed main ON the DUT + the Pi fixtures.
# The selected kind FAILS FAST on hard requirements; optional legs consult
# $script:bench and SKIP themselves instead of failing.
function Invoke-BenchCheck {
    $b = [pscustomobject]@{
        Ports    = @([System.IO.Ports.SerialPort]::GetPortNames())
        Serial   = $null     # usable console/flash port for target/hil
        Ch342    = @()       # CH342 COM names => connector is in DEVICE role
        Ch344B   = @()       # CH344 quad-UART channel B = the rig's WiCAN UART0
        Ftdi     = @()       # FTDI 'USB Serial Port' (the OWON PSU on this rig)
        EcuSim   = @()       # 'MeatPi USB-CAN Device' = the ECU simulator's CDC
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

    # every serial fixture by WHAT it is: tools/testbench/detect_ports.py, the
    # one detector the manual benches share (COM numbers drift on each re-plug:
    # CH342 7/6 today, 2029/30 historically; the CH344 quad-UART went COM12-15
    # -> COM252-255 on 2026-09-19). Enumeration only: nothing is opened, because
    # opening the WiCAN console resets the DUT. The run refreshes
    # tools/testbench/bench_ports.json for the scripts that read it.
    $det = $null
    try {
        $raw = & python "$repo\tools\testbench\detect_ports.py" --json 2>$null
        if ($raw) { $det = ($raw | Out-String | ConvertFrom-Json) }
    } catch {}
    if ($det) {
        $r = $det.roles
        if ($r.wican_console) { $b.Ch344B = @($r.wican_console.port) }
        if ($r.ch342_console) { $b.Ch342 = @($r.ch342_console.port) }
        if ($r.ch342_obd)     { $b.Ch342 += @($r.ch342_obd.port) }
        if ($r.psu)           { $b.Ftdi = @($r.psu.port) }
        elseif ($det.candidates.psu) { $b.Ftdi = @($det.candidates.psu | ForEach-Object { $_.port }) }
        if ($r.ecu_sim_cdc)   { $b.EcuSim = @($r.ecu_sim_cdc.port) }
    } else {
        # no python/pyserial: the same classification by PnP name, inline
        try {
            $pnp = @(Get-CimInstance Win32_PnPEntity -ErrorAction Stop |
                Where-Object { $_.Name -match '\(COM\d+\)' })
            $com = { param($e) ($e.Name -replace '.*\((COM\d+)\).*', '$1') }
            $b.Ch342  = @($pnp | Where-Object { $_.Name -match 'CH342' } | ForEach-Object { & $com $_ })
            $b.Ch344B = @($pnp | Where-Object { $_.Name -match 'SERIAL-B.*CH344' } | ForEach-Object { & $com $_ })
            $b.Ftdi   = @($pnp | Where-Object { $_.Name -match '^USB Serial Port' } | ForEach-Object { & $com $_ })
            $b.EcuSim = @($pnp | Where-Object { $_.Name -match 'MeatPi USB-CAN' } | ForEach-Object { & $com $_ })
        } catch {}
    }

    # console/flash port: the requested -Port, else the UART0 adapter, else the
    # CH344 channel B, else CH342 A
    foreach ($cand in (@($Port, 'COM10') + $b.Ch344B + $b.Ch342)) {
        if ($b.Ports -contains $cand) { $b.Serial = $cand; break }
    }
    if ($b.Serial -and $b.Serial -ne $Port -and $Port -ne 'auto') {
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
    $how = ''
    if ($b.Serial -and $b.Ch344B -contains $b.Serial) { $how = ' (CH344 B)' }
    elseif ($b.Serial -and $b.Ch342 -contains $b.Serial) { $how = ' (CH342 A)' }
    $serialTxt = if ($b.Serial) { "$($b.Serial)$how" } else { 'NONE' }
    $dutTxt = if ($b.DutOnline) { "$($b.DutVersion) @ $DutIp" } else { "OFFLINE @ $DutIp" }
    $ftdiTxt = if ($b.Ftdi) { $b.Ftdi -join '/' } else { 'none' }
    $ecuTxt = if ($b.EcuSim) { $b.EcuSim -join '/' } else { 'none' }
    Write-Host "bench: serial=$serialTxt  pi=$($b.PiOk)  dut=$dutTxt  broker=$($b.Broker)  ncm=$($b.UsbNcm)" -ForegroundColor Cyan
    Write-Host "bench: $wiring; ftdi(psu)=$ftdiTxt  ecu_sim_cdc=$ecuTxt" -ForegroundColor Cyan
    $script:conditions += "preflight: $wiring; serial=$serialTxt; dut=$(if ($b.DutOnline) { $b.DutVersion } else { 'offline' }) @ $DutIp; broker=$($b.Broker); ncm=$($b.UsbNcm); ftdi=$ftdiTxt; ecu_sim_cdc=$ecuTxt"

    # hard requirements per kind
    if (-not $b.PiOk) { throw "bench: $BenchHost unreachable - every kind needs the Pi" }
    if ($What -in @('target', 'hil', 'all') -and -not $b.Serial) {
        throw "bench: no console/flash COM port (tried $Port, COM10, CH344 channel B, CH342). Plug the UART0 adapter / the CH344 or cable the connector to the PC."
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

# The DUT's hotspot lease moves; resolve it by device id on the Pi: the
# newest dnsmasq lease with hostname wican_<id> (the STA side, on the
# hotspot - what every bench leg wants), then the mDNS _wican._tcp record
# (avahi-browse; `getent hosts` returns ONE of the DUT's addresses at random)
# minus the DUT's own AP subnet 192.168.0.x, which avahi also advertises
# and which the Pi can reach only through the radio parked on that AP.
# Falls back to the historical bench address so URLs stay well-formed
# when the Pi is down (the preflight then reports pi=False).
function Resolve-DutIp {
    # no double quotes in the remote command: PowerShell 5.1 mangles them on the way to ssh
    $remote = ('ip=$(sudo sh -c ''cat /var/lib/NetworkManager/dnsmasq-*.leases 2>/dev/null'' | awk -v h=wican_{0} ''$4==h{{print $3}}'' | tail -1); ' +
               'if [ x$ip = x ]; then ip=$(timeout 8 avahi-browse -rtp _wican._tcp 2>/dev/null | awk -F'';'' -v h=wican_{0}.local ''length($1)==1 && $7==h && $8 !~ /^192.168.0./ {{print $8}}'' | head -1); fi; ' +
               'echo DUT_IP=$ip') -f $DeviceId
    $out = & ssh -o BatchMode=yes -o ConnectTimeout=8 $BenchHost $remote 2>$null
    if (($out | Out-String) -match 'DUT_IP=(\d+\.\d+\.\d+\.\d+)') {
        Write-Host "bench: DUT wican_$DeviceId resolved to $($Matches[1]) via $BenchHost" -ForegroundColor DarkGray
        return $Matches[1]
    }
    Write-Host "bench: could not resolve wican_$DeviceId on $BenchHost - using 10.42.0.62 (pass -DutIp to pin)" -ForegroundColor Yellow
    return '10.42.0.62'
}

# Legs that drive the DUT over the USB-NCM mgmt link call this first and
# SKIP (yellow) when the link is down, same fixture-absent pattern as
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
    # -Idf picks the install (2026-10-27: 6.1 beside 6.0.2; build.ps1 carries
    # the same table): both share the tool versions below, only the IDF tree
    # and its python environment differ. The 6.0.2 venv's base interpreter
    # vanished on 2026-10-19, so its stand-in is taken when the venv is dead.
    $idfEnvs = @{
        '6.0.2' = @{ Path = 'C:\esp\v6.0.2\esp-idf'; Venv = 'C:\Espressif\tools\python\v6.0.2\venv'; Alt = 'C:\Espressif\tools\python_env\idf6.0_py3.10_env' }
        '6.1'   = @{ Path = 'C:\esp\v6.1\esp-idf';   Venv = 'C:\Espressif\tools\python\v6.1\venv';   Alt = '' }
    }
    $e = $idfEnvs[$Idf]
    $venv = $e.Venv
    $runs = $false
    try { $null = & "$venv\Scripts\python.exe" -c 'pass' 2>$null; $runs = ($LASTEXITCODE -eq 0) } catch { $runs = $false }
    if (-not $runs -and $e.Alt -and (Test-Path "$($e.Alt)\Scripts\python.exe")) { $venv = $e.Alt }
    $env:IDF_PATH = $e.Path
    $env:IDF_TOOLS_PATH = 'C:\Espressif'
    $env:IDF_PYTHON_ENV_PATH = $venv
    $env:IDF_PYTHON_CHECK_CONSTRAINTS = 'no'
    $env:ESP_IDF_VERSION = $Idf
    # gdbinit generation needs this or ninja aborts BEFORE the app image
    # (the stale-bin trap, CHECKLIST 2026-07-05)
    $env:ESP_ROM_ELF_DIR = 'C:\Espressif\tools\esp-rom-elfs\20241011\'
    # ccache MUST be on PATH: build dirs configured from a ccache-enabled
    # shell bake `ccache gcc` into build.ninja, without it every compile
    # dies with "CreateProcess failed" (the 2026-07-05 `all` build failures)
    $env:PATH = "$venv\Scripts;" +
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

# Test apps inherit the MAIN firmware's sdkconfig.defaults and partition
# table through ..\..\.. of their directory (standard 7). Since the
# components live in their own repository that is the components repo's
# root, where neither file exists and CMake stops. Stage copies for the
# build and take them away again (2026-10-05: no target app had been built
# since the split).
function Add-TargetAppDefaults { param([string]$AppDir)
    $root = (Resolve-Path "$AppDir\..\..\..").Path
    $staged = @()
    foreach ($f in 'sdkconfig.defaults', 'wican_pro_partitions_table.csv') {
        if (-not (Test-Path "$root\$f")) {
            Copy-Item "$repo\$f" "$root\$f"
            $staged += "$root\$f"
        }
    }
    return , $staged
}

function Invoke-TargetApp { param([string]$Comp)
    $appDir = Get-CompDir "$Comp\test_apps"
    $staged = Add-TargetAppDefaults $appDir
    try { Invoke-BuildFlash $appDir }
    finally { foreach ($f in $staged) { Remove-Item $f -ErrorAction SilentlyContinue } }
    if ($Comp -eq 'restart_tracker') {
        # the app crashes on purpose, step by step: its own checker judges
        # every crash note against IDF's panic text of the same crash
        $out = & python "$repo\tools\testbench\system\rt_target_check.py" $Port "$env:TEMP\wican_$Comp.log" 2>&1 | ForEach-Object { Write-Host $_; "$_" }
        Save-StageLog 'target_restart_tracker' $out
        $verdict = $out | Select-String -Pattern '^RT TARGET PASS' | Select-Object -Last 1
        if (-not $verdict) { throw "restart_tracker target run FAILED (console: $env:TEMP\wican_$Comp.log)" }
        $script:note = $verdict.Line
        return
    }
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
    # silently on overflow: 2026-07-22 lesson), the ephemeral job tasks
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
        $py = $script:BenchPy
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
        $py = $script:BenchPy
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
        $py = $script:BenchPy
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
        $py = $script:BenchPy
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
        $py = $script:BenchPy
        $out = & $py -u "$repo\tools\testbench\ble\ble_cli_wedge_test.py" --usb 192.168.82.1 --bench $BenchHost | ForEach-Object { Write-Host $_; $_ }
        Save-StageLog 'live_blecli' $out
        if (-not ($out | Select-String -Quiet 'BLE CLI WEDGE PASS')) { throw 'no BLE CLI WEDGE PASS (UB500 on the Pi? BLE toggle failed?)' }
        $script:note = 'BLE CLI WEDGE PASS'
    }
    # LAST: reboots the DUT twice (settings flips); runs on the PC (USB
    # mgmt link + ssh probes from the Pi)
    Invoke-Stage 'live wifi_gate (network-trust lockdown)' {
        if (Skip-IfNoUsbNcm) { return }
        $py = $script:BenchPy
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
    $out = Invoke-Bench "cd ~/wican && python3 -u tools/testbench/usb/espnetlink_bench.py $DutIp"
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
    Write-Host '  check  - the bench preflight alone (~10 s): serial fixtures by PnP name, DUT address, Pi, broker'
    Write-Host '  hil    - WiFi hardware-in-the-loop pytest (add -Flash the first time)'
    Write-Host '  live   - cli_ws + ws_obd + dbc_real + stack_audit + usb-eth (auto-skip) vs the composed main'
    Write-Host '  stackaudit - static frame-vs-stack audit (tools/stack_audit.py) + live task watermarks/heap floors'
    Write-Host '  dwc2       - DWC2 kill-vs-ISR race hammer (espnetlink dongle on the USB host port)'
    Write-Host '  perf   - mqtt rtt/drain, ws latency/obd_poll, ble A/B'
    Write-Host '  usbeth - USB-Ethernet bench (needs the adapter on the USB connector)'
    Write-Host '  espnetlink - LTE dongle bench: RNDIS data path + CDC-ACM modem console'
    Write-Host '  blesec     - BLE bonding + security (gate / pair / persist) on the Pi UB500'
    Write-Host '  blehttp    - the HTTP API over BLE: storage (/data + /sd), settings, UDS, errors (BLE API PASS + BLE STORAGE PASS)'
    Write-Host '  blej2534   - J2534 PassThru over BLE: CAN, filters, ISO15765, reflash gate, second tester refused'
    Write-Host '  blecanraw  - binary CAN records over BLE (can <-raw-> ble bridge): live bus, answered requests, 29-bit'
    Write-Host '  blephy     - BLE 5 settings: 1M vs 2M PHY throughput/RTT, legacy + extended advertising sets'
    Write-Host '  blethru    - BLE throughput with the ECU simulator as the central (notify/write/read/tunnel vs the esp-idf reference)'
    Write-Host '  autobaud   - CAN listen-before-talk, auto bit rate and the autopid bus guard: PCAN keeps the bus alive and counts error frames (~9 min)'
    Write-Host '  profilee2e - a published vehicle profile end to end (2026-10-21): the Leaf ZE1 profile imported the way the web UI imports it (index shift, renames, passive filters) and polled against an ECU played FROM THE PROFILE on the PCAN (every reply and every filter frame with bytes the bench chose); the values the device publishes must be what the profile''s expressions mean, every request answered, the filters decoded, the rows re-read, restore (2 planned restarts, ~5 min; by hand --profiles <checkout>\vehicle_profiles.json tests a profile before it is pushed)'
    Write-Host '  slcan      - slcan over TCP 3333 (2026-10-16): first the trap (the server on with no connection row: client connected, nothing arrives, the W line and bridge:null say why), then the bridged path: python-can on the PC and on rpi001, slcand + SocketCAN, the /ws/can monitor beside the TCP client, exactly-N with PCAN as the generator, TX both ways, restore (2 to 3 planned restarts, ~3 min)'
    Write-Host '  j2534auto  - a tester states the bit rate on a silent bus (2026-10-17): with Automatic and a bus that only answers when asked, a J2534 CONNECT at a rate the node does not know is refused and said so (tx_refused, the W lines), a CONNECT at 500k is taken (adopted, writes and the ISO15765 reply flow, frames then prove it), after a restart an slcan client''s S6 does the same through the bridge, restore (3 planned restarts, ~4 min)'
    Write-Host '  j2534std   - a standard J2534 tool through the PassThru server (2026-10-19): udsoncan''s byte stream through the DLL replayed byte-exact (the 4-byte id first, the pad flag per message, TxDone and START_OF_MESSAGE indications, a multi-frame VIN, two ECUs on one channel, functional 7DF through a PASS filter, the reflash gate behind the id), then the installed wican_j2534.dll driven by udsoncan under the 32-bit Python; restore (2 planned restarts, ~1 min)'
    Write-Host '  vpnpause   - the VPN pauses on networks marked Pause VPN (issue #976): the bench AP marked / unmarked, away on the dongle AP, home again; wg-bench on the Pi, the console for the away leg (~12 min)'
    Write-Host '  vpnroutes  - the split tunnel (issue #975): a request to a routed network leaves through wg-bench on the Pi, one to any other address through the station, judged at the receiver by the source address (~6 min)'
    Write-Host '  espnleasy  - the ESPNetLink easy-setup surfaces on the DUT (2026-10-13): the usb summary on /api/status against /api/espnetlink, the sim_pin key (a no-op PUT, no restart), the UI doors in the served page, the GPS hint counting away from home (ap off on the P4 for 100 s, home again) (~6 min; needs the P4 daemon)'
    Write-Host '  garage     - the garage story with the ESPNetLink (2026-10-12): home WiFi on the P4, away on the dongle LTE with the VPN to betty, home again, flaps, a dongle reboot away, sleep/wake both ways; the data path judged at a receiver on betty by the source address of every POST; the default legs in usb_rndis with VPN variant A (~35 min; wifi_modem, variant B, g9 and g11 by hand)'
    Write-Host '  usbgps     - a USB GPS receiver is read by default (issue #978): the console switch off, the receiver read as NMEA from boot, the fix when the sky allows, gps_ in autopid and the HA push, the USB GPS receiver switch off = ignored, back on; then the USB page card, the switches and the Status row in Chrome (3 to 4 planned restarts, ~6 min)'
    Write-Host '  sdformat   - the SD card format from the API (2026-10-25): the slot as found, an unreadable card named by what it holds with a quiet log (W line, no E line), the refusals (no phrase, a wrong phrase, a mounted card), the format behind the phrase and the mount after it with a file round trip, the boot after, then the real page in Chrome; the format legs need a card WiCAN cannot read in the slot and the switch -SdFormat (it erases the card), PARTIAL otherwise (0 to 1 planned restart, ~3 min)'
    Write-Host '  usbdetach  - a warm restart in the USB device role drops off the bus first (2026-10-17): the DUT cabled to this PC as a USB NCM device (one planned restart when it was host), warm restarts with the link up: gone from the PC within 5 s of each request (2.2 to 2.4 s measured), back, the link answers, software resets only; the internal heap before and after; the DUT stays in the device role (restore: python tools\testbench\usb\usb_detach_bench.py --only d4 --restore-host, a PSU power cycle inside) (~6 min)'
    Write-Host '  abrp       - the ABRP rows against the real Iternio API (2026-10-13): the key checked from the PC, host routes on the Pi for the run, the key as a query parameter / as the Authorization header / a wrong key, the counters over the window, the Test button, get_telemetry as the witness with a user token in internal/abrp_credentials.json (PARTIAL without one: the 401 refusals only), restore (2 planned restarts, ~5 min)'
    Write-Host '  backup     - the configuration backup that carries everything (issue #979): the file plain / redacted / encrypted after the key, the fs deny list, the round trips from a file and from the copy on the flash, the refusals, the sweep (8 planned restarts, ~7 min)'
    Write-Host '  fwupdate   - firmware updates pulled from a manifest (2026-10-18): rpi001 serves a release list and patched copies of the current build over HTTPS with the bench CA; up to date + the ETag, an update offered and its notes, the install through the pipe and the verify milestone, a wrong sha256, a foreign image (list and upload), the layout verdicts (wipe with the backup step, serial, unknown), channel visibility, stepping and yanked releases, the rollback after three crashes typed over /ws/cli, the legacy push back, mutual exclusion with an upload (6 to 8 planned restarts, 3 crashes on purpose, ~20 min; the DUT must run this build)'
    Write-Host '  release    - THE RELEASE TEST (2026-10-21, internal/TASK_release_test.md): the fast pre-release gate on the exact release image. Flashes the build''s flash set over the console port, then 13 budgeted steps: boot health, UI and API, a settings round trip, OBD (autopid values, ELM over ws_obd), CAN frames, MQTT, the HA webhook sta_ip bench (the PSRAM-stack canary), the updater''s check, an OTA upload of the same image, the writers, BLE, the closing stack/heap/faults/E-line audit and the restore; writes test-reports\release\<image sha256>.json, which release.py reads before it publishes (3 planned restarts, cap 15 min, ~9 min; the simulator on the bus)'
    Write-Host '  espnlota   - the ESPNetLink dongle updated through the WiCAN PRO (2026-10-19): rpi001 serves the dongle release list and patched copies of the dongle build; the row and its notes, the install over USB (download to PSRAM, the checks, the push, the dongle restart, the new version read back), the explicit downgrade, a wrong sha256 and the WiCAN image listed for the dongle (nothing pushed), no_memory, the restore; --unplug cuts the USB link mid-push over /ws/cli (1 planned WiCAN restart for the settings, the dongle restarts 3 to 5 times, ~12 min; the dongle on the connector in usb_rndis mode, the DUT on this build)'
    Write-Host '  wwh        - OBD over UDS (ISO 27145 / SAE J1979-2): a PCAN vehicle is detected, polled, its codes read and cleared; the car switch both ways (~10 min)'
    Write-Host '  crashnote  - the crash note, the stored crash report and the crash-loop brake, two stages: (1) the DUT is crashed on purpose over its console (abort, an invalid store twice, the interrupt watchdog); the next boot must say where: its note against the console panic text, the decoded function names, one small NVS write for a crash that is news and none for the same crash again, the report word for word after a PSU power cycle (CRASH NOTE PASS); (2) three quick crashes in a row park the device: console line, HTTP silent, supply current against sleep mode, the timed exit, the next crash parks at once, a power cycle is a fresh start (CRASH PARK PASS)'
    Write-Host '  eutruck    - end to end on an EU truck: the PCAN adapter plays WWH-OBD ECUs and a J1939 network at once; from an OBD-II car through the wizard path to the truck, the car made current on the truck bus, and back; then the tables own protocol commands on the truck bus (~6 min)'
    Write-Host '  j1939      - the J1939 listener on native CAN: a PCAN truck is read value for value (BAM, DM1, 250k and 500k), the DUT never transmits; then exactly-N conservation up to line rate; then AutoPID on the truck (detection, PGN rows, broker, an ELM app beside them, DM1 in the DTC report); then active mode (address claim and contention, requests incl. RTS/CTS as destination, NACKs, a bus flood, ? rows, DM2 and the clear, a diagnostics hold, listen mode) (~30 min)'
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
    if ($What -ne 'list' -and -not $DutIp) { $DutIp = Resolve-DutIp }
    if ($What -ne 'list' -and -not $SkipBenchCheck) {
        Invoke-Stage 'bench preflight' { Invoke-BenchCheck }
        if ($script:anyFail) {
            $benchOk = $false
            Write-Host 'bench preflight FAILED - fix the bench (or -SkipBenchCheck to override)' -ForegroundColor Red
        }
    }
    if ($benchOk) { switch ($What) {
        'list'   { Show-Inventory }
        'check'  { Write-Host 'bench: preflight only - the quick bench check, no test kind ran' -ForegroundColor DarkGray }
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
                $py = $script:BenchPy
                $out = & $py -u "$repo\tools\testbench\ble\ble_security_test.py" --usb 192.168.82.1 --bench $BenchHost --passkey 421337 --setup 2>&1 | ForEach-Object { Write-Host $_; $_ }
                Save-StageLog 'live_blesec' $out
                # always restore the bench even if the test threw
                & $py -u "$repo\tools\testbench\ble\ble_security_test.py" --usb 192.168.82.1 --restore 2>&1 | Out-Null
                if (-not ($out | Select-String -Quiet 'BLE SECURITY PASS')) { throw 'no BLE SECURITY PASS (UB500 present? bleak on the Pi?)' }
                $script:note = 'BLE SECURITY PASS'
            }
        }
        'blehttp' {
            # The HTTP API over BLE (ble_http): storage, settings, UDS, errors,
            # disconnect mid-upload, through the FFF3/FFF4 tunnel. Runs ON the
            # Pi (UB500 + bleak) via the ble_pi_run.py wrapper; 2 planned
            # reboots (configure + restore); the restore stage always runs.
            Invoke-Stage 'live blehttp (HTTP API over BLE incl. storage)' {
                $py = $script:BenchPy
                $out = & $py -u "$repo\tools\testbench\ble\ble_pi_run.py" ble_http_pi.py --dut $DutIp --stage configure 2>&1 | ForEach-Object { Write-Host $_; $_ }
                $out += & $py -u "$repo\tools\testbench\ble\ble_pi_run.py" ble_http_pi.py --stage ble 2>&1 | ForEach-Object { Write-Host $_; $_ }
                $out += & $py -u "$repo\tools\testbench\ble\ble_pi_run.py" ble_http_pi.py --stage restore 2>&1 | ForEach-Object { Write-Host $_; $_ }
                Save-StageLog 'live_blehttp' $out
                foreach ($m in ($out | Select-String '^METRIC (\S+)=(\S+) (\S+)')) { Add-Metric $m.Matches[0].Groups[1].Value ([double]$m.Matches[0].Groups[2].Value) $m.Matches[0].Groups[3].Value }
                if (-not ($out | Select-String -Quiet 'BLE STORAGE PASS')) { throw 'no BLE STORAGE PASS (see the storage legs in the log)' }
                if (-not ($out | Select-String -Quiet '^BLE API PASS')) { throw 'no BLE API PASS (UB500 present? link gate? see the log)' }
                $script:note = 'BLE API PASS + BLE STORAGE PASS'
            }
        }
        'blej2534' {
            # J2534 PassThru over BLE (ble_j2534): HELLO/OPEN, CAN + filters,
            # ISO15765 + the reflash gate, a second tester refused, link drop
            # releases the server. Runs ON the Pi; 2 planned reboots.
            Invoke-Stage 'live blej2534 (J2534 PassThru over BLE)' {
                $py = $script:BenchPy
                $out = & $py -u "$repo\tools\testbench\ble\ble_pi_run.py" ble_j2534_pi.py --dut $DutIp --stage configure 2>&1 | ForEach-Object { Write-Host $_; $_ }
                $out += & $py -u "$repo\tools\testbench\ble\ble_pi_run.py" ble_j2534_pi.py --stage ble 2>&1 | ForEach-Object { Write-Host $_; $_ }
                $out += & $py -u "$repo\tools\testbench\ble\ble_pi_run.py" ble_j2534_pi.py --stage restore 2>&1 | ForEach-Object { Write-Host $_; $_ }
                Save-StageLog 'live_blej2534' $out
                foreach ($m in ($out | Select-String '^METRIC (\S+)=(\S+) (\S+)')) { Add-Metric $m.Matches[0].Groups[1].Value ([double]$m.Matches[0].Groups[2].Value) $m.Matches[0].Groups[3].Value }
                if (-not ($out | Select-String -Quiet '^BLE J2534 PASS')) { throw 'no BLE J2534 PASS (j2534_server enabled? ECU sim on the bus? see the log)' }
                $script:note = 'BLE J2534 PASS'
            }
        }
        'blecanraw' {
            # Binary CAN over BLE: the can <-raw-> ble bridge on the data pipe
            # (live bus records, 20 answered requests, a 29-bit write). Runs
            # ON the Pi; 2 planned reboots.
            Invoke-Stage 'live blecanraw (binary CAN records over BLE)' {
                $py = $script:BenchPy
                $out = & $py -u "$repo\tools\testbench\ble\ble_pi_run.py" ble_can_raw_pi.py --dut $DutIp --stage configure 2>&1 | ForEach-Object { Write-Host $_; $_ }
                $out += & $py -u "$repo\tools\testbench\ble\ble_pi_run.py" ble_can_raw_pi.py --stage ble 2>&1 | ForEach-Object { Write-Host $_; $_ }
                $out += & $py -u "$repo\tools\testbench\ble\ble_pi_run.py" ble_can_raw_pi.py --stage restore 2>&1 | ForEach-Object { Write-Host $_; $_ }
                Save-StageLog 'live_blecanraw' $out
                foreach ($m in ($out | Select-String '^METRIC (\S+)=(\S+) (\S+)')) { Add-Metric $m.Matches[0].Groups[1].Value ([double]$m.Matches[0].Groups[2].Value) $m.Matches[0].Groups[3].Value }
                if (-not ($out | Select-String -Quiet '^BLE CAN RAW PASS')) { throw 'no BLE CAN RAW PASS (can_manager enabled? ECU sim on the bus? see the log)' }
                $script:note = 'BLE CAN RAW PASS'
            }
        }
        'blephy' {
            # BLE 5 as settings (ble_manager v4 `phy` / `advertising`). Pi
            # side: settings + reboots, the advertising sets under btmon, a
            # 1M link check, restore. The PHY transfer legs run from THIS
            # PC's Bluetooth adapter (ble_phy_pc.py): the Pi's UB500 cannot
            # hold a 2M link with the S3, the Intel adapter can. 3 planned
            # reboots; the PC pairs once (the script provides the passkey
            # through a WinRT custom pairing ceremony).
            Invoke-Stage 'live blephy (BLE 5 PHY + advertising sets)' {
                $py = $script:BenchPy
                $pypc = 'C:\Espressif\tools\python\v5.5.3\venv\Scripts\python.exe'   # has bleak (WinRT)
                $out = & $py -u "$repo\tools\testbench\ble\ble_pi_run.py" ble_phy_bench_pi.py --dut $DutIp --stage configure 2>&1 | ForEach-Object { Write-Host $_; $_ }
                $out += & $py -u "$repo\tools\testbench\ble\ble_pi_run.py" ble_phy_bench_pi.py --stage adv 2>&1 | ForEach-Object { Write-Host $_; $_ }
                $out += & $pypc -u "$repo\tools\testbench\ble\ble_phy_pc.py" --phy 1m 2>&1 | ForEach-Object { Write-Host $_; $_ }
                $out += & $py -u "$repo\tools\testbench\ble\ble_pi_run.py" ble_phy_bench_pi.py --stage switch 2>&1 | ForEach-Object { Write-Host $_; $_ }
                $out += & $py -u "$repo\tools\testbench\ble\ble_pi_run.py" ble_phy_bench_pi.py --stage adv 2>&1 | ForEach-Object { Write-Host $_; $_ }
                $out += & $pypc -u "$repo\tools\testbench\ble\ble_phy_pc.py" --phy 2m 2>&1 | ForEach-Object { Write-Host $_; $_ }
                $out += & $py -u "$repo\tools\testbench\ble\ble_pi_run.py" ble_phy_bench_pi.py --stage restore 2>&1 | ForEach-Object { Write-Host $_; $_ }
                Save-StageLog 'live_blephy' $out
                foreach ($m in ($out | Select-String '^METRIC (\S+)=(\S+) (\S+)')) { Add-Metric $m.Matches[0].Groups[1].Value ([double]$m.Matches[0].Groups[2].Value) $m.Matches[0].Groups[3].Value }
                $stagesOk = @($out | Select-String '^STAGE \w+ OK').Count -eq 5
                $legsOk = ($out | Select-String -Quiet '^PC PHY LEG 1m PASS') -and ($out | Select-String -Quiet '^PC PHY LEG 2m PASS')
                if (-not ($stagesOk -and $legsOk)) { throw 'no BLE PHY PASS (a Pi stage failed or a PC leg did not negotiate its PHY: see the log)' }
                Write-Host 'BLE PHY PASS'
                $script:note = 'BLE PHY PASS'
            }
        }
        'blethru' {
            # BLE throughput with the ECU simulator as the CENTRAL
            # (ble_central_bench over USB-NCM 192.168.8.1): throughput_app
            # link tuning, notify/write/read + tunnel legs on 1M then 2M.
            # The DUT is read over WiFi before/after each link. ~10 min.
            Invoke-Stage 'live blethru (simulator central: BLE throughput)' {
                $py = $script:BenchPy
                $out = & $py -u "$repo\tools\testbench\ble\ble_sim_central_bench.py" --dut $DutIp 2>&1 | ForEach-Object { Write-Host $_; $_ }
                Save-StageLog 'live_blethru' $out
                foreach ($m in ($out | Select-String '^METRIC (\S+)=(\S+) (\S+)')) { Add-Metric $m.Matches[0].Groups[1].Value ([double]$m.Matches[0].Groups[2].Value) $m.Matches[0].Groups[3].Value }
                if (-not ($out | Select-String -Quiet '^BLE THROUGHPUT PASS')) { throw 'no BLE THROUGHPUT PASS (simulator on USB-NCM with ble_central_bench? DUT BLE on? see the log)' }
                $script:note = 'BLE THROUGHPUT PASS'
            }
        }
        'sleep'  {
            # Sleep-mode HIL (OWON P4305 on COM2016 + Pi; ~10 min).
            Invoke-Stage 'sleep bench' {
                $out = & $script:BenchPy -u "$repo\tools\testbench\sleep\sleep_bench_test.py" 2>&1 | ForEach-Object { Write-Host $_; $_ }
                Save-StageLog 'sleep_bench' $out
                if (-not ($out | Select-String -Quiet 'SLEEP BENCH PASS')) { throw 'no SLEEP BENCH PASS (PSU on COM2016? sleep enabled?)' }
            }
        }
        'sleepmatrix' {
            # Full sleep robustness matrix (~90 min; SLEEP_MATRIX.md).
            Invoke-Stage 'sleep matrix' {
                $out = & $script:BenchPy -u "$repo\tools\testbench\sleep\sleep_matrix_test.py" 2>&1 | ForEach-Object { Write-Host $_; $_ }
                Save-StageLog 'sleep_matrix' $out
                if (-not ($out | Select-String -Quiet 'SLEEP MATRIX PASS')) { throw 'no SLEEP MATRIX PASS' }
            }
        }
        'dwc2' {
            # DWC2 kill-vs-ISR race hammer (~10 min; needs the espnetlink
            # dongle on the USB host port + usb_host_manager/usb_acm_cli
            # enabled).
            Invoke-Stage 'DWC2 hammer' {
                $out = & $script:BenchPy -u "$repo\tools\testbench\usb\dwc2_hammer_test.py" 2>&1 | ForEach-Object { Write-Host $_; $_ }
                Save-StageLog 'dwc2_hammer' $out
                if (-not ($out | Select-String -Quiet 'DWC2 HAMMER PASS')) { throw 'no DWC2 HAMMER PASS (dongle plugged? host mode enabled?)' }
            }
        }
        'conserve' {
            # Counter-conservation benches (standard §7 performance-truth).
            Invoke-Stage 'CAN conservation' {
                $py = $script:BenchPy
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
                $out = & $script:BenchPy -u "$repo\tools\testbench\usb\usb_ncm_conservation_test.py" 2>&1 | ForEach-Object { Write-Host $_; $_ }
                Save-StageLog 'usb_ncm_conservation' $out
                if (-not ($out | Select-String -Quiet 'USB NCM CONSERVATION PASS')) { throw 'no USB NCM CONSERVATION PASS' }
            }
            Invoke-Stage 'WiFi conservation' {
                $out = & $script:BenchPy -u "$repo\tools\testbench\wifi\wifi_conservation_test.py" 2>&1 | ForEach-Object { Write-Host $_; $_ }
                Save-StageLog 'wifi_conservation' $out
                if (-not ($out | Select-String -Quiet 'WIFI CONSERVATION PASS')) { throw 'no WIFI CONSERVATION PASS (iperf on the Pi?)' }
            }
        }
        'autobaud' {
            # CAN listen-before-talk + the autopid bus guard
            # (TASK_j1939_wwh.md phase 2, ~20 min, 17 DUT restarts). Runs on
            # the PC: the PCAN adapter on the DUT's bus is the judge, the ECU
            # simulator (USB-NCM, 192.168.8.1) is the ACK source. The bench
            # polls the DUT's HTTP several times a second, so it gets a
            # tunnel of its own through the Pi instead of ssh + curl.
            Invoke-Stage 'CAN auto-baud + bus guard' {
                $py = $script:BenchPy
                $tunnel = Start-Process -FilePath 'ssh' -PassThru -WindowStyle Hidden -ArgumentList @('-N', '-o', 'BatchMode=yes', '-o', 'ExitOnForwardFailure=yes', '-L', "18081:${DutIp}:80", $BenchHost)
                $con = Start-ConsoleTail 'can_autobaud'
                try {
                    Start-Sleep -Seconds 3
                    if ($tunnel.HasExited) { throw "no tunnel to the DUT through $BenchHost (port 18081 busy?)" }
                    $out = & $py -u "$repo\tools\testbench\can\can_autobaud_bench.py" 'localhost:18081' 2>&1 | ForEach-Object { Write-Host $_; $_ }
                    Save-StageLog 'can_autobaud' $out
                    if (-not ($out | Select-String -Quiet 'CAN AUTOBAUD PASS')) { throw 'no CAN AUTOBAUD PASS (PCAN wired? simulator on 192.168.8.1?)' }
                } finally {
                    if (-not $tunnel.HasExited) { Stop-Process -Id $tunnel.Id -Force -Confirm:$false }
                    Stop-ConsoleTail $con
                }
            }
        }
        'profilee2e' {
            # A published vehicle profile end to end (2026-10-21): the Leaf ZE1
            # profile is imported the way the web UI imports it and polled
            # against an ECU played FROM THE PROFILE on the PCAN (every reply
            # and every passive-filter frame carry bytes the bench chose); the
            # values the device publishes must be what the profile's
            # expressions mean in the legacy frame of reference the published
            # file uses. The ECU simulator stays on the bus: it ACKs the
            # actor's frames and answers the standard rows found on the DUT.
            # By hand with an unpushed profile: --profiles <checkout>\vehicle_profiles.json
            Invoke-Stage 'vehicle profile end to end' {
                $py = $script:BenchPy
                $tunnel = Start-Process -FilePath 'ssh' -PassThru -WindowStyle Hidden -ArgumentList @('-N', '-o', 'BatchMode=yes', '-o', 'ExitOnForwardFailure=yes', '-L', "18081:${DutIp}:80", $BenchHost)
                $con = Start-ConsoleTail 'autopid_profile_e2e'
                try {
                    Start-Sleep -Seconds 3
                    if ($tunnel.HasExited) { throw "no tunnel to the DUT through $BenchHost (port 18081 busy?)" }
                    $out = & $py -u "$repo\tools\testbench\obd\autopid_profile_e2e_bench.py" '--dut' 'localhost:18081' 2>&1 | ForEach-Object { Write-Host $_; $_ }
                    Save-StageLog 'autopid_profile_e2e' $out
                    if (-not ($out | Select-String -Quiet '^AUTOPID PROFILE E2E PASS')) { throw 'no AUTOPID PROFILE E2E PASS (PCAN wired? the simulator on the bus? the profile published on main?)' }
                } finally {
                    if (-not $tunnel.HasExited) { Stop-Process -Id $tunnel.Id -Force -Confirm:$false }
                    Stop-ConsoleTail $con
                }
            }
        }
        'slcan' {
            # slcan over TCP end to end (2026-10-16, a Discord report: "SLCAN
            # works on the CAN monitor, not over python-can; the log says
            # client connected, nothing arrives"). First the trap: the slcan0
            # server on with no connection row (the client connects, the bus
            # is read, nothing arrives; the W line and /api/sockets bridge:null
            # say why). Then the bridged path: python-can on the PC through
            # the tunnel and on rpi001 straight at the DUT, slcand + SocketCAN,
            # the /ws/can monitor beside the TCP client, exactly-N with PCAN
            # as the generator, TX both ways, restore. The slcan port rides a
            # second forward of the same tunnel.
            Invoke-Stage 'slcan over TCP' {
                $py = $script:BenchPy
                $tunnel = Start-Process -FilePath 'ssh' -PassThru -WindowStyle Hidden -ArgumentList @('-N', '-o', 'BatchMode=yes', '-o', 'ExitOnForwardFailure=yes', '-L', "18081:${DutIp}:80", '-L', "18333:${DutIp}:3333", $BenchHost)
                $con = Start-ConsoleTail 'slcan'
                try {
                    Start-Sleep -Seconds 3
                    if ($tunnel.HasExited) { throw "no tunnel to the DUT through $BenchHost (ports 18081/18333 busy?)" }
                    $out = & $py -u "$repo\tools\testbench\can\slcan_bridge_test.py" --dut 'localhost:18081' --slcan 'localhost:18333' --dut-ip $DutIp --pi $BenchHost 2>&1 | ForEach-Object { Write-Host $_; $_ }
                    Save-StageLog 'slcan' $out
                    if (-not ($out | Select-String -Quiet '^SLCAN BRIDGE PASS')) { throw 'no SLCAN BRIDGE PASS (PCAN wired? the DUT on the bench AP?)' }
                } finally {
                    if (-not $tunnel.HasExited) { Stop-Process -Id $tunnel.Id -Force -Confirm:$false }
                    Stop-ConsoleTail $con
                }
            }
        }
        'j2534auto' {
            # A tester states the bit rate on a silent bus (2026-10-17, the
            # second half of a Discord report: "J2534 driver works,
            # communication established, no comm to the canbus"). With
            # Automatic on a bus that speaks only when asked (the bench
            # simulator, a gatewayed OBD port) nothing was ever proven and
            # every write was refused. Now a J2534 CONNECT's baud and an
            # slcan client's S command are taken when the bus is silent.
            # The tunnel carries the web API, the J2534 port and the slcan
            # port; PCAN is the witness and, for the proof by frames, the
            # generator.
            Invoke-Stage 'J2534 and slcan state the bit rate' {
                $py = $script:BenchPy
                $tunnel = Start-Process -FilePath 'ssh' -PassThru -WindowStyle Hidden -ArgumentList @('-N', '-o', 'BatchMode=yes', '-o', 'ExitOnForwardFailure=yes', '-L', "18081:${DutIp}:80", '-L', "18534:${DutIp}:6809", '-L', "18333:${DutIp}:3333", $BenchHost)
                $con = Start-ConsoleTail 'j2534_auto'
                try {
                    Start-Sleep -Seconds 3
                    if ($tunnel.HasExited) { throw "no tunnel to the DUT through $BenchHost (ports 18081/18534/18333 busy?)" }
                    $out = & $py -u "$repo\tools\testbench\usb\j2534_auto_baud_bench.py" --dut 'localhost:18081' --j2534 'localhost:18534' --slcan 'localhost:18333' 2>&1 | ForEach-Object { Write-Host $_; $_ }
                    Save-StageLog 'j2534_auto' $out
                    if (-not ($out | Select-String -Quiet '^J2534 AUTO BAUD PASS')) { throw 'no J2534 AUTO BAUD PASS (PCAN wired? the simulator answering 7DF? the DUT on the bench AP?)' }
                } finally {
                    if (-not $tunnel.HasExited) { Stop-Process -Id $tunnel.Id -Force -Confirm:$false }
                    Stop-ConsoleTail $con
                }
            }
        }
        'j2534std' {
            # A standard J2534 tool through the PassThru server (2026-10-19,
            # the second field report: udsoncan timed out on every request
            # because the device took an ISO15765 message's data as the bare
            # payload while SAE J2534-1 puts the CAN id first). The tool's
            # bytes replayed byte-exact over the wire protocol, then the
            # installed DLL driven by udsoncan itself under the 32-bit
            # Python. The tunnel carries the web API and the J2534 port;
            # PCAN is the witness on the bus.
            Invoke-Stage 'A standard J2534 tool through the server' {
                $py = $script:BenchPy
                $tunnel = Start-Process -FilePath 'ssh' -PassThru -WindowStyle Hidden -ArgumentList @('-N', '-o', 'BatchMode=yes', '-o', 'ExitOnForwardFailure=yes', '-L', "18081:${DutIp}:80", '-L', "18534:${DutIp}:6809", $BenchHost)
                $con = Start-ConsoleTail 'j2534_std'
                try {
                    Start-Sleep -Seconds 3
                    if ($tunnel.HasExited) { throw "no tunnel to the DUT through $BenchHost (ports 18081/18534 busy?)" }
                    $out = & $py -u "$repo\tools\testbench\usb\j2534_std_tool_bench.py" --dut 'localhost:18081' --j2534 'localhost:18534' 2>&1 | ForEach-Object { Write-Host $_; $_ }
                    Save-StageLog 'j2534_std' $out
                    if (-not ($out | Select-String -Quiet '^J2534 STD TOOL PASS')) { throw 'no J2534 STD TOOL PASS (PCAN wired? the simulator answering 7E0? the DUT on the bench AP?)' }
                } finally {
                    if (-not $tunnel.HasExited) { Stop-Process -Id $tunnel.Id -Force -Confirm:$false }
                    Stop-ConsoleTail $con
                }
            }
        }
        'backup' {
            # The configuration backup that carries everything (issue #979,
            # 2026-10-11): the file (plain, redacted, encrypted once the key
            # file was downloaded), the fs deny list, the round trips from a
            # file and from the copy on the flash (and the card when one is
            # in), the refusals, the sweep's refresh after an upload and its
            # idle quiet. Eight planned restarts; no console, no PCAN.
            Invoke-Stage 'Configuration backup' {
                $py = $script:BenchPy
                $apTunnel = $null
                $tunnel = Start-Process -FilePath 'ssh' -PassThru -WindowStyle Hidden -ArgumentList @('-N', '-o', 'BatchMode=yes', '-o', 'ExitOnForwardFailure=yes', '-L', "18081:${DutIp}:80", $BenchHost)
                try {
                    Start-Sleep -Seconds 3
                    if ($tunnel.HasExited) { throw "no tunnel to the DUT through $BenchHost (port 18081 busy?)" }
                    $benchArgs = @('--dut', 'localhost:18081', '--pi', $BenchHost)
                    if ($Factory) {
                        # b9: the reset DUT answers on its own AP only (the bench DUT's USB link
                        # to the Pi drops once the factory default hands the port to the
                        # ESPNetLink dongle): a second forward to the AP address; the bench
                        # starts the Pi's join right after the reset (--join-ap) and leaves it
                        $apTunnel = Start-Process -FilePath 'ssh' -PassThru -WindowStyle Hidden -ArgumentList @('-N', '-o', 'BatchMode=yes', '-o', 'ExitOnForwardFailure=yes', '-L', '18082:192.168.0.10:80', $BenchHost)
                        $benchArgs += @('--factory', '--reset-dut', 'localhost:18082', '--join-ap')
                    }
                    $out = & $py -u "$repo\tools\testbench\system\config_backup_bench.py" @benchArgs 2>&1 | ForEach-Object { Write-Host $_; $_ }
                    Save-StageLog 'config_backup' $out
                    if (-not ($out | Select-String -Quiet 'CONFIG BACKUP PASS')) { throw 'no CONFIG BACKUP PASS (the DUT on the bench AP? a card in for the SD leg?)' }
                } finally {
                    if (-not $tunnel.HasExited) { Stop-Process -Id $tunnel.Id -Force -Confirm:$false }
                    if ($apTunnel -and -not $apTunnel.HasExited) { Stop-Process -Id $apTunnel.Id -Force -Confirm:$false }
                }
            }
        }
        'fwupdate' {
            # Firmware updates pulled from a manifest (2026-10-18,
            # internal/TASK_fw_update.md 9.2): rpi001 serves the release list
            # and patched copies of the current build (fw_server.py), the
            # bench walks the legs over HTTP through the tunnel and types the
            # three crashes of the rollback leg over /ws/cli on the same port.
            # No console, no PCAN; the DUT must run THIS build (the bench
            # refuses otherwise: flash it first).
            Invoke-Stage 'Firmware update from a manifest' {
                $py = $script:BenchPy
                $tunnel = Start-Process -FilePath 'ssh' -PassThru -WindowStyle Hidden -ArgumentList @('-N', '-o', 'BatchMode=yes', '-o', 'ExitOnForwardFailure=yes', '-L', "18081:${DutIp}:80", $BenchHost)
                try {
                    Start-Sleep -Seconds 3
                    if ($tunnel.HasExited) { throw "no tunnel to the DUT through $BenchHost (port 18081 busy?)" }
                    $out = & $py -u "$repo\tools\testbench\system\fw_update_bench.py" --dut 'localhost:18081' --pi $BenchHost 2>&1 | ForEach-Object { Write-Host $_; $_ }
                    Save-StageLog 'fw_update' $out
                    foreach ($m in ($out | Select-String -Pattern '^METRIC ' )) { Add-Metric 'fw_update' $m.Line }
                    if (-not ($out | Select-String -Quiet '^FW UPDATE( PARTIAL)? PASS')) { throw 'no FW UPDATE PASS (the DUT on this build and on the bench AP? the Pi serving 8446 with the bench CA? the cli <-> ws_cli bridge for the rollback leg?)' }
                } finally {
                    if (-not $tunnel.HasExited) { Stop-Process -Id $tunnel.Id -Force -Confirm:$false }
                }
            }
        }
        'release' {
            # The Release Test (2026-10-21, internal/TASK_release_test.md): the
            # fast pre-release gate on the exact release image. The bench
            # flashes the build's flash set over the console port itself and
            # tails the console after the flash (so no Start-ConsoleTail here),
            # walks its steps over HTTP through the tunnel and the Pi, and
            # writes test-reports/release/<image sha256>.json, the record
            # release.py check / publish read before an image goes out.
            Invoke-Stage 'Release test (the fast pre-release gate)' {
                $py = $script:BenchPy
                Sync-BenchTools
                $tunnel = Start-Process -FilePath 'ssh' -PassThru -WindowStyle Hidden -ArgumentList @('-N', '-o', 'BatchMode=yes', '-o', 'ExitOnForwardFailure=yes', '-L', "18081:${DutIp}:80", $BenchHost)
                try {
                    Start-Sleep -Seconds 3
                    if ($tunnel.HasExited) { throw "no tunnel to the DUT through $BenchHost (port 18081 busy?)" }
                    $out = & $py -u "$repo\tools\testbench\release\release_test.py" --dut 'localhost:18081' --dut-ip $DutIp --pi $BenchHost --console $script:Port 2>&1 | ForEach-Object { Write-Host $_; $_ }
                    Save-StageLog 'release_test' $out
                    foreach ($m in ($out | Select-String -Pattern '^METRIC ' )) { Add-Metric 'release' $m.Line }
                    if (-not ($out | Select-String -Quiet '^RELEASE TEST PASS')) { throw 'no RELEASE TEST PASS (the DUT on the bench AP, the console port free, the Pi with its broker, the simulator answering on the bus?)' }
                    $script:note = ($out | Select-String '^record: ' | Select-Object -Last 1).Line
                } finally {
                    if (-not $tunnel.HasExited) { Stop-Process -Id $tunnel.Id -Force -Confirm:$false }
                }
            }
        }
        'espnlota' {
            # The ESPNetLink dongle updated through the WiCAN PRO (2026-10-19,
            # internal/TASK_fw_update.md 9.3): rpi001 serves the dongle's
            # release list and patched copies of the dongle build; the bench
            # drives the relay over HTTP through the tunnel with the dongle on
            # the connector in usb_rndis mode. No console, no PCAN; the opt-in
            # unplug leg types `usb vbus` over /ws/cli. The DUT must run THIS
            # build (flash it first).
            Invoke-Stage 'ESPNetLink update through the WiCAN PRO' {
                $py = $script:BenchPy
                $tunnel = Start-Process -FilePath 'ssh' -PassThru -WindowStyle Hidden -ArgumentList @('-N', '-o', 'BatchMode=yes', '-o', 'ExitOnForwardFailure=yes', '-L', "18081:${DutIp}:80", $BenchHost)
                try {
                    Start-Sleep -Seconds 3
                    if ($tunnel.HasExited) { throw "no tunnel to the DUT through $BenchHost (port 18081 busy?)" }
                    $out = & $py -u "$repo\tools\testbench\wifi\espnetlink_ota_bench.py" --dut 'localhost:18081' --pi $BenchHost 2>&1 | ForEach-Object { Write-Host $_; $_ }
                    Save-StageLog 'espnetlink_ota' $out
                    foreach ($m in ($out | Select-String -Pattern '^METRIC ' )) { Add-Metric 'espnetlink_ota' $m.Line }
                    if (-not ($out | Select-String -Quiet '^ESPNL OTA( PARTIAL)? PASS')) { throw 'no ESPNL OTA PASS (the dongle on the connector in usb_rndis mode? the DUT on this build? the Pi serving 8446 with the bench CA? a dongle build in the dongle repo?)' }
                } finally {
                    if (-not $tunnel.HasExited) { Stop-Process -Id $tunnel.Id -Force -Confirm:$false }
                }
            }
        }
        'vpnpause' {
            # Pause the VPN on marked networks (issue #976, 2026-10-11): the
            # bench AP marked = paused, unmarked = connected over wg-bench on
            # the Pi, the hotspot down = paused on the dongle's AP (judged on
            # the console the stage holds), the hotspot up = connected again.
            Invoke-Stage 'VPN pause on marked networks' {
                $py = $script:BenchPy
                $tunnel = Start-Process -FilePath 'ssh' -PassThru -WindowStyle Hidden -ArgumentList @('-N', '-o', 'BatchMode=yes', '-o', 'ExitOnForwardFailure=yes', '-L', "18081:${DutIp}:80", $BenchHost)
                $con = Start-ConsoleTail 'vpn_pause'
                try {
                    Start-Sleep -Seconds 3
                    if ($tunnel.HasExited) { throw "no tunnel to the DUT through $BenchHost (port 18081 busy?)" }
                    $bargs = @('--dut', 'localhost:18081', '--pi', $BenchHost)
                    if ($null -ne $con) { $bargs += @('--console-log', $con.Log) } else { $bargs += @('--console', 'none') }
                    $out = & $py -u "$repo\tools\testbench\vpn\vpn_pause_bench.py" @bargs 2>&1 | ForEach-Object { Write-Host $_; $_ }
                    Save-StageLog 'vpn_pause' $out
                    if (-not ($out | Select-String -Quiet 'VPN PAUSE PASS')) { throw 'no VPN PAUSE PASS (wg-bench on the Pi? the dongle paired? the DUT on the bench AP?)' }
                } finally {
                    if (-not $tunnel.HasExited) { Stop-Process -Id $tunnel.Id -Force -Confirm:$false }
                    Stop-ConsoleTail $con
                }
            }
        }
        'vpnroutes' {
            # The split tunnel (issue #975, 2026-10-11): vpn_manager `routes`
            # through ip4_routes. The Pi's wg-bench carries an alias network
            # behind the tunnel; a destinations test to it must arrive from the
            # tunnel address, one to the hotspot address from the station's,
            # and with default_route both from the tunnel.
            Invoke-Stage 'VPN split-tunnel routes' {
                $py = $script:BenchPy
                $tunnel = Start-Process -FilePath 'ssh' -PassThru -WindowStyle Hidden -ArgumentList @('-N', '-o', 'BatchMode=yes', '-o', 'ExitOnForwardFailure=yes', '-L', "18081:${DutIp}:80", $BenchHost)
                $con = Start-ConsoleTail 'vpn_routes'
                try {
                    Start-Sleep -Seconds 3
                    if ($tunnel.HasExited) { throw "no tunnel to the DUT through $BenchHost (port 18081 busy?)" }
                    $out = & $py -u "$repo\tools\testbench\vpn\vpn_routes_bench.py" '--dut' 'localhost:18081' '--pi' $BenchHost 2>&1 | ForEach-Object { Write-Host $_; $_ }
                    Save-StageLog 'vpn_routes' $out
                    if (-not ($out | Select-String -Quiet 'VPN ROUTES PASS')) { throw 'no VPN ROUTES PASS (wg-bench on the Pi? dd_receiver started? the DUT on the bench AP?)' }
                } finally {
                    if (-not $tunnel.HasExited) { Stop-Process -Id $tunnel.Id -Force -Confirm:$false }
                    Stop-ConsoleTail $con
                }
            }
        }
        'garage' {
            # The garage story with the ESPNetLink (2026-10-12): the home
            # WiFi is the P4 bench AP, away is the dongle's LTE with the VPN
            # to betty, then home again; the data path is judged at a
            # receiver on betty by the source address of every POST. The
            # bench opens its own tunnel to the DUT and probes the console
            # itself (optional: only g10 needs it), so the stage holds
            # neither. The default legs in usb_rndis with VPN variant A; the
            # other runs are by hand (--mode wifi_modem, --vpn B, --only g9
            # or g11).
            Invoke-Stage 'ESPNetLink garage story' {
                $py = $script:BenchPy
                $out = & $py -u "$repo\tools\testbench\wifi\espnetlink_garage_bench.py" '--dut-ip' $DutIp '--pi' $BenchHost 2>&1 | ForEach-Object { Write-Host $_; $_ }
                Save-StageLog 'espnetlink_garage' $out
                if (-not ($out | Select-String -Quiet 'ESPNL GARAGE (PARTIAL )?PASS')) { throw 'no ESPNL GARAGE PASS (the P4 daemon up? betty reachable? the dongle on the connector? the DUT on the bench AP?)' }
            }
        }
        'espnleasy' {
            # The easy-setup surfaces (2026-10-13): the bench opens its own
            # tunnel to the DUT through the Pi and drives the P4 bench AP for
            # the away leg (e3), so the stage holds neither; --no-away by
            # hand when the daemon is down.
            Invoke-Stage 'ESPNetLink easy setup' {
                $py = $script:BenchPy
                $out = & $py -u "$repo\tools\testbench\usb\espnetlink_easy_bench.py" '--dut-ip' $DutIp '--pi' $BenchHost 2>&1 | ForEach-Object { Write-Host $_; $_ }
                Save-StageLog 'espnetlink_easy' $out
                if (-not ($out | Select-String -Quiet 'ESPNL EASY BENCH (PARTIAL )?PASS')) { throw 'no ESPNL EASY BENCH PASS (the P4 daemon up? the dongle on the connector? the DUT on the bench AP?)' }
            }
        }
        'abrp' {
            # The ABRP rows of data_destinations against the REAL Iternio API
            # (2026-10-13): the api_key checked from the PC, host routes on
            # the Pi to Iternio's addresses for the run (the bench subnet has
            # no internet path by itself), three rows every 5 s (the key as a
            # query parameter, as the Authorization header, a wrong key), the
            # counters over the window, the Test button, Iternio's
            # get_telemetry as the witness when internal/abrp_credentials.json
            # holds a user token (without one the refusal path only: the bench
            # says PARTIAL PASS), the originals restored. Nothing secret is
            # printed; the credentials file is gitignored.
            Invoke-Stage 'ABRP live' {
                $py = $script:BenchPy
                $tunnel = Start-Process -FilePath 'ssh' -PassThru -WindowStyle Hidden -ArgumentList @('-N', '-o', 'BatchMode=yes', '-o', 'ExitOnForwardFailure=yes', '-L', "18081:${DutIp}:80", $BenchHost)
                try {
                    Start-Sleep -Seconds 3
                    if ($tunnel.HasExited) { throw "no tunnel to the DUT through $BenchHost (port 18081 busy?)" }
                    $out = & $py -u "$repo\tools\testbench\system\abrp_live_bench.py" '--dut' 'localhost:18081' '--pi' $BenchHost 2>&1 | ForEach-Object { Write-Host $_; $_ }
                    Save-StageLog 'abrp_live' $out
                    if (-not ($out | Select-String -Quiet 'ABRP LIVE (PARTIAL )?PASS')) { throw 'no ABRP LIVE PASS (internal/abrp_credentials.json present? the DUT on the bench AP? the Pi reaches the LAN router?)' }
                    if ($out | Select-String -Quiet 'ABRP LIVE PARTIAL PASS') { Write-Host 'bench: PARTIAL, no user token in internal/abrp_credentials.json: the refusal path only' -ForegroundColor Yellow }
                } finally {
                    if (-not $tunnel.HasExited) { Stop-Process -Id $tunnel.Id -Force -Confirm:$false }
                }
            }
        }
        'logclock' {
            # Log timestamps that survive the clock's history (issue #984,
            # 2026-10-13): an SNTP actor on the Pi (UDP 123, a transient
            # systemd unit) steps the DUT's clock forwards and backwards
            # while the logger writes; the journal, the markers, the
            # re-based rows, the relative formats, the unsynced boot after
            # a PSU power cycle (`rtc --forget`), the re-base tool. Two
            # planned restarts for the settings plus one for the format
            # change, one power cycle. Needs the OWON PSU on this PC.
            Invoke-Stage 'Log clock' {
                $py = $script:BenchPy
                $tunnel = Start-Process -FilePath 'ssh' -PassThru -WindowStyle Hidden -ArgumentList @('-N', '-o', 'BatchMode=yes', '-o', 'ExitOnForwardFailure=yes', '-L', "18081:${DutIp}:80", $BenchHost)
                try {
                    Start-Sleep -Seconds 3
                    if ($tunnel.HasExited) { throw "no tunnel to the DUT through $BenchHost (port 18081 busy?)" }
                    $out = & $py -u "$repo\tools\testbench\system\log_clock_bench.py" '--dut' 'localhost:18081' '--pi' $BenchHost 2>&1 | ForEach-Object { Write-Host $_; $_ }
                    Save-StageLog 'log_clock' $out
                    if (-not ($out | Select-String -Quiet 'LOG CLOCK (PARTIAL )?PASS')) { throw 'no LOG CLOCK PASS (the DUT on the bench AP? the Pi can bind UDP 123? the OWON on this PC?)' }
                    if ($out | Select-String -Quiet 'LOG CLOCK PARTIAL PASS') { Write-Host 'bench: PARTIAL (a leg skipped)' -ForegroundColor Yellow }
                } finally {
                    if (-not $tunnel.HasExited) { Stop-Process -Id $tunnel.Id -Force -Confirm:$false }
                }
            }
        }
        'sdformat' {
            # The SD card format from the API (2026-10-25): the slot's state
            # as found, an unreadable card reported by what it holds with no E
            # line, the refusals, the format behind the typed phrase and the
            # mount after it (a file round trip), the boot after, then the real
            # page in Chrome through the same tunnel. The format legs run only
            # with -SdFormat (the card is erased) and a card WiCAN cannot read
            # in the slot; PARTIAL otherwise.
            Invoke-Stage 'SD card format' {
                $py = $script:BenchPy
                $tunnel = Start-Process -FilePath 'ssh' -PassThru -WindowStyle Hidden -ArgumentList @('-N', '-o', 'BatchMode=yes', '-o', 'ExitOnForwardFailure=yes', '-L', "18081:${DutIp}:80", $BenchHost)
                try {
                    Start-Sleep -Seconds 3
                    if ($tunnel.HasExited) { throw "no tunnel to the DUT through $BenchHost (port 18081 busy?)" }
                    $benchArgs = @('--dut', 'localhost:18081')
                    if ($SdFormat) { $benchArgs += '--format' }
                    $out = & $py -u "$repo\tools\testbench\system\sdcard_format_bench.py" @benchArgs 2>&1 | ForEach-Object { Write-Host $_; $_ }
                    Save-StageLog 'sdcard_format' $out
                    if (-not ($out | Select-String -Quiet 'SD FORMAT (PARTIAL )?PASS')) { throw 'no SD FORMAT PASS (the DUT on the bench AP? a card in the slot?)' }
                    if ($out | Select-String -Quiet 'SD FORMAT PARTIAL PASS') { Write-Host 'bench: PARTIAL (the format legs need an unreadable card and -SdFormat)' -ForegroundColor Yellow }
                    # the real page in Chrome through the same tunnel, against what the device says
                    $ui = & node "$repo\tools\testbench\system\sdcard_ui_check.mjs" 'http://127.0.0.1:18081' 2>&1 | ForEach-Object { Write-Host $_; $_ }
                    Save-StageLog 'sdcard_ui' $ui
                    if (-not ($ui | Select-String -Quiet 'SD CARD UI PASS')) { throw 'no SD CARD UI PASS' }
                } finally {
                    if (-not $tunnel.HasExited) { Stop-Process -Id $tunnel.Id -Force -Confirm:$false }
                }
            }
        }
        'usbgps' {
            # A USB GPS receiver is read by default (issue #978, 2026-10-11):
            # the console switch off (a fresh install), the receiver on the
            # connector read as NMEA from boot, the fix when the sky allows,
            # the gps_ parameters and the HA push's gps block, the USB GPS
            # receiver switch off = ignored, back on; then the USB page's
            # card, the two switches and the Status row in Chrome through
            # the same tunnel.
            Invoke-Stage 'USB GPS receiver' {
                $py = $script:BenchPy
                $tunnel = Start-Process -FilePath 'ssh' -PassThru -WindowStyle Hidden -ArgumentList @('-N', '-o', 'BatchMode=yes', '-o', 'ExitOnForwardFailure=yes', '-L', "18081:${DutIp}:80", $BenchHost)
                try {
                    Start-Sleep -Seconds 3
                    if ($tunnel.HasExited) { throw "no tunnel to the DUT through $BenchHost (port 18081 busy?)" }
                    $out = & $py -u "$repo\tools\testbench\usb\usb_gps_bench.py" '--dut' 'localhost:18081' '--pi' $BenchHost 2>&1 | ForEach-Object { Write-Host $_; $_ }
                    Save-StageLog 'usb_gps' $out
                    if (-not ($out | Select-String -Quiet 'USB GPS PASS')) { throw 'no USB GPS PASS (the u-blox on the connector? the DUT on the bench AP?)' }
                    $ui = & node "$repo\tools\testbench\usb\usb_gps_ui_check.mjs" 'http://127.0.0.1:18081' 2>&1 | ForEach-Object { Write-Host $_; $_ }
                    Save-StageLog 'usb_gps_ui' $ui
                    if (-not ($ui | Select-String -Quiet 'USB GPS UI PASS')) { throw 'no USB GPS UI PASS' }
                } finally {
                    if (-not $tunnel.HasExited) { Stop-Process -Id $tunnel.Id -Force -Confirm:$false }
                }
            }
        }
        'usbdetach' {
            # A warm restart in the USB device role drops off the bus first
            # (2026-10-17, from the ESPNetLink bench of 2026-10-16): the DUT's
            # USB-C cabled to this PC, role device / class ncm (one planned
            # restart when it was host), the PC's view of the device through
            # PnP, the warm restarts with the link up: the path to the DUT gone
            # within 5 s of each request, back, the link answers, software resets
            # only; the internal heap before and after. Leaves the DUT in the
            # device role: the connector mux returns to the CH342 at power-on
            # only (TESTING.md), so the restore is the bench's --restore-host
            # by hand (role host + a PSU power cycle).
            Invoke-Stage 'USB restart detach' {
                $py = $script:BenchPy
                $tunnel = Start-Process -FilePath 'ssh' -PassThru -WindowStyle Hidden -ArgumentList @('-N', '-o', 'BatchMode=yes', '-o', 'ExitOnForwardFailure=yes', '-L', "18081:${DutIp}:80", $BenchHost)
                $con = Start-ConsoleTail 'usbdetach'
                try {
                    Start-Sleep -Seconds 3
                    if ($tunnel.HasExited) { throw "no tunnel to the DUT through $BenchHost (port 18081 busy?)" }
                    $out = & $py -u "$repo\tools\testbench\usb\usb_detach_bench.py" '--dut' 'localhost:18081' 2>&1 | ForEach-Object { Write-Host $_; $_ }
                    Save-StageLog 'usb_detach' $out
                    if (-not ($out | Select-String -Quiet 'USB DETACH PASS')) { throw 'no USB DETACH PASS (the DUT cabled to this PC? the DUT on the bench AP?)' }
                } finally {
                    Stop-ConsoleTail $con
                    if (-not $tunnel.HasExited) { Stop-Process -Id $tunnel.Id -Force -Confirm:$false }
                }
            }
        }
        'crashnote' {
            # The crash note, the stored crash report and the crash-loop
            # brake on the production firmware (TASK_crash_note.md,
            # TASK_crash_loop_brake.md; ~10 min in two stages, each ending
            # with a PSU power cycle that leaves the DUT as found).
            # Stage 1 (~4 min, 4 crashes on purpose, 1 planned restart): the
            # bench types `restart_tracker --panic`, `--panic=fault` (twice)
            # and `--panic=wdt` on the DUT's console and judges what the next
            # boot says about each: the note against the console's own panic
            # text, the decoded function names, the log line, the ONE small
            # NVS write that stores a crash that is news and the zero writes
            # of the same crash again, the report word for word after a power
            # cycle. Stage 2 (~6 min, 4 crashes, a forced sleep): three quick
            # crashes in a row park the device (console line, HTTP silent,
            # supply current against the firmware's own sleep mode), the park
            # ends by the bench's timer, the next quick crash parks at once,
            # a power cycle is a fresh start. Both hold the console
            # themselves (they type there), so no console tail beside them.
            $py = $script:BenchPy
            foreach ($stage in @(
                    @{ Name = 'crash note'; Script = 'crash_note_bench.py'; Log = 'crash_note'; Verdict = 'CRASH NOTE PASS' },
                    @{ Name = 'crash park'; Script = 'crash_park_bench.py'; Log = 'crash_park'; Verdict = 'CRASH PARK PASS' })) {
                Invoke-Stage $stage.Name {
                    $tunnel = Start-Process -FilePath 'ssh' -PassThru -WindowStyle Hidden -ArgumentList @('-N', '-o', 'BatchMode=yes', '-o', 'ExitOnForwardFailure=yes', '-L', "18081:${DutIp}:80", $BenchHost)
                    try {
                        Start-Sleep -Seconds 3
                        if ($tunnel.HasExited) { throw "no tunnel to the DUT through $BenchHost (port 18081 busy?)" }
                        $out = & $py -u "$repo\tools\testbench\system\$($stage.Script)" 'localhost:18081' 2>&1 | ForEach-Object { Write-Host $_; $_ }
                        Save-StageLog $stage.Log $out
                        if (-not ($out | Select-String -Quiet "^$($stage.Verdict)")) { throw "no $($stage.Verdict)" }
                    } finally {
                        if (-not $tunnel.HasExited) { Stop-Process -Id $tunnel.Id -Force -Confirm:$false }
                    }
                }
            }
        }
        'eutruck' {
            # End to end on an EU truck (TASK_j1939_wwh.md phase 7, ~6 min,
            # 10 DUT restarts): the PCAN adapter plays WWH-OBD ECUs AND a
            # J1939 network in one process (actors/pcan_wwh_ecu.py --truck),
            # the DUT walks the wizard's path from an OBD-II car to the truck,
            # the stored car is made current on the truck's bus (the bus guard
            # looks again), and back; the ECU simulator is the ACK source and
            # the car. Second stage (leg x5): the tables' own protocol
            # commands and resets on the truck's bus, judged at the truck.
            Invoke-Stage 'EU truck end to end' {
                $py = $script:BenchPy
                $actorLogs = Join-Path $repo 'test-reports\logs'
                $tunnel = Start-Process -FilePath 'ssh' -PassThru -WindowStyle Hidden -ArgumentList @('-N', '-o', 'BatchMode=yes', '-o', 'ExitOnForwardFailure=yes', '-L', "18081:${DutIp}:80", $BenchHost)
                $con = Start-ConsoleTail 'eu_truck_e2e'
                try {
                    Start-Sleep -Seconds 3
                    if ($tunnel.HasExited) { throw "no tunnel to the DUT through $BenchHost (port 18081 busy?)" }
                    $out = & $py -u "$repo\tools\testbench\obd\eu_truck_e2e_bench.py" 'localhost:18081' '--logdir' $actorLogs 2>&1 | ForEach-Object { Write-Host $_; $_ }
                    Save-StageLog 'eu_truck_e2e' $out
                    if (-not ($out | Select-String -Quiet '^EU TRUCK E2E PASS')) { throw 'no EU TRUCK E2E PASS (PCAN wired? simulator on 192.168.8.1?)' }
                } finally {
                    if (-not $tunnel.HasExited) { Stop-Process -Id $tunnel.Id -Force -Confirm:$false }
                    Stop-ConsoleTail $con
                }
            }
            Invoke-Stage 'EU truck: the tables own protocols' {
                $py = $script:BenchPy
                $actorLogs = Join-Path $repo 'test-reports\logs'
                $tunnel = Start-Process -FilePath 'ssh' -PassThru -WindowStyle Hidden -ArgumentList @('-N', '-o', 'BatchMode=yes', '-o', 'ExitOnForwardFailure=yes', '-L', "18081:${DutIp}:80", $BenchHost)
                $con = Start-ConsoleTail 'eu_truck_tables'
                try {
                    Start-Sleep -Seconds 3
                    if ($tunnel.HasExited) { throw "no tunnel to the DUT through $BenchHost (port 18081 busy?)" }
                    $out = & $py -u "$repo\tools\testbench\obd\eu_truck_e2e_bench.py" 'localhost:18081' '--only' 'x5' '--logdir' $actorLogs 2>&1 | ForEach-Object { Write-Host $_; $_ }
                    Save-StageLog 'eu_truck_tables' $out
                    if (-not ($out | Select-String -Quiet '^EU TRUCK E2E PARTIAL PASS')) { throw 'no EU TRUCK E2E PARTIAL PASS for leg x5' }
                } finally {
                    if (-not $tunnel.HasExited) { Stop-Process -Id $tunnel.Id -Force -Confirm:$false }
                    Stop-ConsoleTail $con
                }
            }
        }
        'wwh' {
            # OBD over UDS as an autopid dialect (TASK_j1939_wwh.md phase 3,
            # ~10 min, about 14 DUT restarts). Runs on the PC: the PCAN
            # adapter is the vehicle (actors/pcan_wwh_ecu.py) and its log is
            # the wire's side of every check; the ECU simulator (USB-NCM,
            # 192.168.8.1) is the ACK source and the OBD-II car of the last
            # leg. The actor's logs land beside the stage log.
            Invoke-Stage 'WWH-OBD (ISO 27145 / J1979-2)' {
                $py = $script:BenchPy
                $actorLogs = Join-Path $repo 'test-reports\logs'
                $tunnel = Start-Process -FilePath 'ssh' -PassThru -WindowStyle Hidden -ArgumentList @('-N', '-o', 'BatchMode=yes', '-o', 'ExitOnForwardFailure=yes', '-L', "18081:${DutIp}:80", $BenchHost)
                $con = Start-ConsoleTail 'wwh_obd'
                try {
                    Start-Sleep -Seconds 3
                    if ($tunnel.HasExited) { throw "no tunnel to the DUT through $BenchHost (port 18081 busy?)" }
                    $out = & $py -u "$repo\tools\testbench\obd\wwh_obd_bench.py" 'localhost:18081' '--logdir' $actorLogs 2>&1 | ForEach-Object { Write-Host $_; $_ }
                    Save-StageLog 'wwh_obd' $out
                    if (-not ($out | Select-String -Quiet 'WWH OBD PASS')) { throw 'no WWH OBD PASS (PCAN wired? simulator on 192.168.8.1?)' }
                } finally {
                    if (-not $tunnel.HasExited) { Stop-Process -Id $tunnel.Id -Force -Confirm:$false }
                    Stop-ConsoleTail $con
                }
            }
        }
        'j1939' {
            # The J1939 listener on the native CAN bus (TASK_j1939_wwh.md
            # phase 4) and AutoPID on a J1939 vehicle (phase 5). Runs on the
            # PC: the PCAN adapter is the truck (actors/pcan_j1939_truck.py),
            # the ECU simulator (USB-NCM, 192.168.8.1) only acknowledges.
            # Three stages: the listener bench (~3 min, 4 DUT restarts),
            # counter conservation at 250k and 500k up to line rate (~9 min,
            # 3 restarts), then AutoPID on the truck (~10 min, 7 restarts;
            # the obd0 ELM bridge is tunnelled too for its app leg).
            # ($actorLogs, not $logdir: this block runs in the script's scope
            # and PowerShell names are case-insensitive; `$logdir = ...` here
            # replaced $script:logDir, and this kind's stage logs landed
            # outside the run's folder until 2026-10-04.)
            $py = $script:BenchPy
            $actorLogs = Join-Path $repo 'test-reports\logs'
            Invoke-Stage 'J1939 listener' {
                $tunnel = Start-Process -FilePath 'ssh' -PassThru -WindowStyle Hidden -ArgumentList @('-N', '-o', 'BatchMode=yes', '-o', 'ExitOnForwardFailure=yes', '-L', "18081:${DutIp}:80", $BenchHost)
                $con = Start-ConsoleTail 'j1939_listener'
                try {
                    Start-Sleep -Seconds 3
                    if ($tunnel.HasExited) { throw "no tunnel to the DUT through $BenchHost (port 18081 busy?)" }
                    $out = & $py -u "$repo\tools\testbench\can\j1939_bench.py" 'localhost:18081' '--logdir' $actorLogs 2>&1 | ForEach-Object { Write-Host $_; $_ }
                    Save-StageLog 'j1939_listener' $out
                    if (-not ($out | Select-String -Quiet '^J1939 PASS')) { throw 'no J1939 PASS (PCAN wired? simulator on 192.168.8.1?)' }
                } finally {
                    if (-not $tunnel.HasExited) { Stop-Process -Id $tunnel.Id -Force -Confirm:$false }
                    Stop-ConsoleTail $con
                }
            }
            Invoke-Stage 'J1939 conservation' {
                $tunnel = Start-Process -FilePath 'ssh' -PassThru -WindowStyle Hidden -ArgumentList @('-N', '-o', 'BatchMode=yes', '-o', 'ExitOnForwardFailure=yes', '-L', "18081:${DutIp}:80", $BenchHost)
                $con = Start-ConsoleTail 'j1939_conservation'
                try {
                    Start-Sleep -Seconds 3
                    if ($tunnel.HasExited) { throw "no tunnel to the DUT through $BenchHost (port 18081 busy?)" }
                    $out = & $py -u "$repo\tools\testbench\can\j1939_conservation_test.py" 'localhost:18081' 2>&1 | ForEach-Object { Write-Host $_; $_ }
                    Save-StageLog 'j1939_conservation' $out
                    if (-not ($out | Select-String -Quiet '^J1939 CONSERVATION PASS')) { throw 'no J1939 CONSERVATION PASS' }
                } finally {
                    if (-not $tunnel.HasExited) { Stop-Process -Id $tunnel.Id -Force -Confirm:$false }
                    Stop-ConsoleTail $con
                }
            }
            Invoke-Stage 'AutoPID J1939' {
                $tunnel = Start-Process -FilePath 'ssh' -PassThru -WindowStyle Hidden -ArgumentList @('-N', '-o', 'BatchMode=yes', '-o', 'ExitOnForwardFailure=yes', '-L', "18081:${DutIp}:80", '-L', "18082:${DutIp}:35000", $BenchHost)
                $con = Start-ConsoleTail 'autopid_j1939'
                try {
                    Start-Sleep -Seconds 3
                    if ($tunnel.HasExited) { throw "no tunnel to the DUT through $BenchHost (ports 18081/18082 busy?)" }
                    $out = & $py -u "$repo\tools\testbench\obd\autopid_j1939_bench.py" 'localhost:18081' '--elm' 'localhost:18082' '--logdir' $actorLogs 2>&1 | ForEach-Object { Write-Host $_; $_ }
                    Save-StageLog 'autopid_j1939' $out
                    if (-not ($out | Select-String -Quiet '^AUTOPID J1939 PASS')) { throw 'no AUTOPID J1939 PASS' }
                } finally {
                    if (-not $tunnel.HasExited) { Stop-Process -Id $tunnel.Id -Force -Confirm:$false }
                    Stop-ConsoleTail $con
                }
            }
            # phase 6: the node talks (address claim, requests, DM2 / the
            # clear, the transport protocol as a destination); 18083 is the
            # J2534 TCP port, a bare tester connection = the diagnostics hold
            Invoke-Stage 'J1939 active' {
                $tunnel = Start-Process -FilePath 'ssh' -PassThru -WindowStyle Hidden -ArgumentList @('-N', '-o', 'BatchMode=yes', '-o', 'ExitOnForwardFailure=yes', '-L', "18081:${DutIp}:80", '-L', "18083:${DutIp}:6809", $BenchHost)
                $con = Start-ConsoleTail 'j1939_active'
                try {
                    Start-Sleep -Seconds 3
                    if ($tunnel.HasExited) { throw "no tunnel to the DUT through $BenchHost (ports 18081/18083 busy?)" }
                    $out = & $py -u "$repo\tools\testbench\can\j1939_active_bench.py" 'localhost:18081' '--diag' 'localhost:18083' '--logdir' $actorLogs 2>&1 | ForEach-Object { Write-Host $_; $_ }
                    Save-StageLog 'j1939_active' $out
                    if (-not ($out | Select-String -Quiet '^J1939 ACTIVE PASS')) { throw 'no J1939 ACTIVE PASS' }
                } finally {
                    if (-not $tunnel.HasExited) { Stop-Process -Id $tunnel.Id -Force -Confirm:$false }
                    Stop-ConsoleTail $con
                }
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
