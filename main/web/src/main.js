// Check OUR fork's releases for OTA updates (never the stock meatpiHQ repo, whose
// images would overwrite this custom build). Compares the running firmware against
// the latest v* release tag and, when newer, links straight to that release's OTA
// app image (the *.bin you POST to /upload/ota.bin).
const FW_UPDATE_REPO = 'cdufresne81/nc-flash-wican-fw';
const FW_RELEASES_URL = `https://github.com/${FW_UPDATE_REPO}/releases`;
// Check once per page load. checkFirmwareUpdate() is called from the shared
// /check_status onload handler;
// without this guard the rate-limited (60/hr) GitHub releases API would be
// re-hit each time, for a result that can't change within a page's lifetime.
let fwUpdateChecked = false;
async function checkFirmwareUpdate() {
        if (fwUpdateChecked) return;
        fwUpdateChecked = true;
        try {
            // Prefer the git-describe tag (git_version, e.g. "v1.2.3"): it carries the
            // full semver including patch and matches our release tag format exactly.
            // Only trust it when it actually looks like a vX.Y[.Z] tag -- dev builds
            // report a bare SHA (e.g. "b79549b-dirty") that must not be mis-parsed as a
            // version. Otherwise fall back to the major.minor fw_version display.
            const gitRaw = document.getElementById('git_version')?.textContent?.trim();
            const fwRaw = document.getElementById('fw_version')?.textContent?.trim();
            const currentRaw = (gitRaw && /v?\d+\.\d+/i.test(gitRaw)) ? gitRaw : fwRaw;
            if (!currentRaw) return;

            // Helpers: extract numeric version and compare a.b.c parts
            const extractVersion = (str) => {
                if (!str) return null;
                const m = String(str).match(/(\d+)(?:\.(\d+))?(?:\.(\d+))?/);
                return m ? [m[1], m[2] || '0', m[3] || '0'].join('.') : null;
            };
            const cmpVersions = (a, b) => {
                const ap = a.split('.').map(n => parseInt(n, 10) || 0);
                const bp = b.split('.').map(n => parseInt(n, 10) || 0);
                const len = Math.max(ap.length, bp.length);
                for (let i = 0; i < len; i++) {
                    const ai = ap[i] || 0;
                    const bi = bp[i] || 0;
                    if (ai > bi) return 1;
                    if (ai < bi) return -1;
                }
                return 0;
            };

            const currentVersion = extractVersion(currentRaw);
            if (!currentVersion) return;

            const response = await fetch(`https://api.github.com/repos/${FW_UPDATE_REPO}/releases`);
            if (!response.ok) return;
            const releases = await response.json();
            if (!Array.isArray(releases)) return;

            // Pick the highest published v* release (skip drafts/prereleases). Don't
            // rely on API ordering -- compare semver across all candidates.
            let latest = null;
            let latestVersion = null;
            for (const rel of releases) {
                if (!rel || rel.draft || rel.prerelease) continue;
                const tag = rel.tag_name || rel.name || '';
                if (!/^v?\d+\.\d+/i.test(tag)) continue;
                const ver = extractVersion(tag);
                if (!ver) continue;
                if (!latestVersion || cmpVersions(ver, latestVersion) === 1) {
                    latest = rel;
                    latestVersion = ver;
                }
            }
            if (!latest) return;

            // Only notify if latest > current
            if (cmpVersions(latestVersion, currentVersion) === 1) {
                const notice = document.getElementById('firmware-update-notice');
                if (notice) {
                    // Prefer a direct link to the OTA app image asset (the obd_pro *.bin
                    // flashable via /upload/ota.bin), not the bootloader/partition-table/
                    // ota_data bins or the source archives. Fall back to the release page.
                    const assets = Array.isArray(latest.assets) ? latest.assets : [];
                    const otaAsset = assets.find(a => {
                        const name = (a && a.name) || '';
                        return /\.bin$/i.test(name) &&
                            /obd[_-]?pro/i.test(name) &&
                            !/bootloader|partition|ota[_-]?data/i.test(name);
                    });
                    const url = (otaAsset && otaAsset.browser_download_url)
                        || latest.html_url || FW_RELEASES_URL;
                    const versionText = ` <span style='color:#b45309'>(v${latestVersion})</span>`;
                    notice.innerHTML = `<span style=\"font-weight: 600;\">New firmware available!</span><br><a id=\"firmware-update-link\" href=\"${url}\" target=\"_blank\" style=\"color: #2563eb; text-decoration: underline;\">Download</a>${versionText}`;
                    notice.style.display = 'block';
                }
            }
        } catch (e) {
            // Silent fail to avoid impacting UI if GitHub is unreachable
        }
    }
    // document.addEventListener('DOMContentLoaded', checkFirmwareUpdate);
    document.addEventListener('DOMContentLoaded', (event) => {
        document.getElementById("submit_button").disabled = true;
        setRTCTime();
    });
    function setRTCTime() {
        const now = new Date();
        
        function decToBcd(val) {
            return Math.floor(val / 10) * 16 + (val % 10);
        }
        
        const rtcData = {
            command: "set_rtc_time",
            hour: decToBcd(now.getUTCHours()),
            min: decToBcd(now.getUTCMinutes()),
            sec: decToBcd(now.getUTCSeconds()),
            year: decToBcd(now.getUTCFullYear() % 100),
            month: decToBcd(now.getUTCMonth() + 1),
            day: decToBcd(now.getUTCDate()),
            weekday: decToBcd(now.getUTCDay()) 
        };
        
        fetch('/system_commands', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify(rtcData, null, 0)
        })
        .then(response => response.text())
        .then(data => {
            console.log('RTC time set successfully (UTC):', data);
        })
        .catch(error => {
            console.error('Error setting RTC time:', error);
        });
    }

    function showNotification(message, color = "red", duration = 5000) {
        const notification = document.getElementById("notification");
        
        switch (color) {
            case "red":
                notification.style.backgroundColor = "#fee2e2";
                notification.style.borderColor = "#ef4444";
                notification.style.color = "#991b1b";
                break;
            case "green":
                notification.style.backgroundColor = "#dcfce7";
                notification.style.borderColor = "#22c55e";
                notification.style.color = "#166534";
                break;
            case "blue":
                notification.style.backgroundColor = "#dbeafe";
                notification.style.borderColor = "#3b82f6";
                notification.style.color = "#1e40af";
                break;
            case "yellow":
                notification.style.backgroundColor = "#fef9c3";
                notification.style.borderColor = "#eab308";
                notification.style.color = "#854d0e";
                break;
            default:
                notification.style.backgroundColor = "#fee2e2";
                notification.style.borderColor = "#ef4444";
                notification.style.color = "#991b1b";
        }

        notification.innerHTML = message;
        notification.classList.add("show");
        
        if (window.notificationTimeout) {
            clearTimeout(window.notificationTimeout);
        }
        
        window.notificationTimeout = setTimeout(() => {
            notification.classList.remove("show");
        }, duration);
    }

    async function downloadRestartHistoryJson() {
        try {
            const response = await fetch('/restart_tracker/history');
            if (!response.ok) {
                throw new Error(`HTTP ${response.status}`);
            }

            const data = await response.json();
            const json = JSON.stringify(data, null, 2);
            const blob = new Blob([json], { type: 'application/json' });
            const url = URL.createObjectURL(blob);
            const link = document.createElement('a');
            const timestamp = new Date().toISOString().replace(/[:.]/g, '-');

            link.href = url;
            link.download = `restart-history-${timestamp}.json`;
            document.body.appendChild(link);
            link.click();
            link.remove();
            URL.revokeObjectURL(url);

            showNotification('Restart history downloaded.', 'blue', 3000);
        } catch (error) {
            console.error('Failed to download restart history:', error);
            showNotification(`Unable to fetch restart history JSON. ${error.message}`, 'red');
        }
    }

    function formatRestartTrackerValue(value, fallback = 'N/A') {
        if (value === undefined || value === null || value === '') {
            return fallback;
        }

        return String(value).replace(/_/g, ' ');
    }

    function formatRestartTrackerLocalTime(unixTimestamp, fallback = 'N/A') {
        if (unixTimestamp === undefined || unixTimestamp === null || Number(unixTimestamp) <= 0) {
            return fallback;
        }

        const timestampMs = Number(unixTimestamp) * 1000;
        const date = new Date(timestampMs);
        if (Number.isNaN(date.getTime())) {
            return fallback;
        }

        return date.toLocaleString();
    }

    function toggleStandardPIDOptions() {
        const standardPidsSelect = document.getElementById("standard_pids");
        const ecuProtocolSelect = document.getElementById("ecu_protocol");
        const availablePidsSelect = document.getElementById("available_pids");
        const scanPidButton = document.getElementById("scan_pids_button");
        
        const isEnabled = standardPidsSelect.value === "enable";
        ecuProtocolSelect.disabled = !isEnabled;
        availablePidsSelect.disabled = !isEnabled;
        scanPidButton.disabled = !isEnabled;
    }

    function toggleSmartConnectConfig() {
        const wifiMode = document.getElementById("wifi_mode").value;
        const smartConnectConfig = document.getElementById("smartconnect_config");
        const stationConfigSection = document.getElementById("station_config_section");
        const protocolSelect = document.getElementById("protocol");
        const addFbBtn = document.getElementById("add_fallback_button");
        const fbRows = document.querySelectorAll('#fallback_rows input, #fallback_rows select, #fallback_rows button');
        
        if (wifiMode === "SmartConnect") {
            smartConnectConfig.style.display = "block";
            stationConfigSection.style.display = "none";
            protocolSelect.disabled = true; 
            
            toggleDriveConfig(); 
            
            if (addFbBtn) addFbBtn.disabled = true;
            fbRows.forEach(el => el.disabled = true);
        } else {
            smartConnectConfig.style.display = "none";
            stationConfigSection.style.display = "block";
            protocolSelect.disabled = false;

            const bleStatus = document.getElementById("ble_status");
            const blePasskey = document.getElementById("ble_pass_value");
            bleStatus.disabled = false;
            blePasskey.disabled = false;
            if (addFbBtn) addFbBtn.disabled = false;
            fbRows.forEach(el => el.disabled = false);
        }
        try { toggleApStationWarning(); } catch(_) {}

        // Trigger validation when switching modes
        submit_enable();
    }

    function toggleApStationWarning() {
        const wifiModeEl = document.getElementById("wifi_mode");
        const apAutoDisableEl = document.getElementById("ap_auto_disable");
        const div = document.getElementById("apstation_warning_div");
        if (!wifiModeEl || !apAutoDisableEl || !div) return;

        const shouldShow = (wifiModeEl.value === "APStation") && (apAutoDisableEl.value === "disable");
        div.style.display = shouldShow ? "block" : "none";
    }

    function toggleDriveConfig() {
        const wifiMode = document.getElementById("wifi_mode").value;
        const driveConnectionType = document.getElementById("drive_connection_type").value;
        const driveWifiConfig = document.getElementById("drive_wifi_config");
        const driveWifiPassword = document.getElementById("drive_wifi_password");
        const driveWifiSecurity = document.getElementById("drive_wifi_security");
        const bleStatus = document.getElementById("ble_status");
        const blePasskey = document.getElementById("ble_pass_value");
        
        // Only apply SmartConnect logic when in SmartConnect mode
        if (wifiMode === "SmartConnect") {
            if (driveConnectionType === "wifi") {
                // Show WiFi config, force disable BLE
                driveWifiConfig.style.display = "table-row";
                driveWifiPassword.style.display = "table-row";
                driveWifiSecurity.style.display = "table-row";
                bleStatus.value = "disable";
                bleStatus.selectedIndex = 1; // Select "Disable" option
                bleStatus.disabled = true;
                blePasskey.disabled = true;
            } else if (driveConnectionType === "ble") {
                // Hide WiFi config, force enable BLE
                driveWifiConfig.style.display = "none";
                driveWifiPassword.style.display = "none";
                driveWifiSecurity.style.display = "none";
                bleStatus.value = "enable";
                bleStatus.selectedIndex = 0; // Select "Enable" option
                bleStatus.disabled = true;
                blePasskey.disabled = false;
            }
        }
        // Trigger validation when changing drive connection type
        submit_enable();
    }

    function renderFallbackNetworks(list) {
        const container = document.getElementById('fallback_rows');
        if (!container) return;
        container.innerHTML = '';
        const limited = Array.isArray(list) ? list.slice(0,5) : [];
        limited.forEach(item => addFallbackNetworkRow(item));
        updateAddFallbackButtonState();
    }

    function addFallbackNetworkRow(data = {}) {
        const container = document.getElementById('fallback_rows');
        if (!container) return;
        const current = container.querySelectorAll('.fallback-row').length;
        if (current >= 5) return;

        const row = document.createElement('div');
        row.className = 'fallback-row';
        row.style.display = 'grid';
        row.style.gridTemplateColumns = '1fr 1fr 120px auto';
        row.style.gap = '8px';
        row.style.margin = '6px 0';

        row.innerHTML = `
            <input type="text" class="fb-ssid" placeholder="SSID" value="${(data.ssid||'').replace(/"/g,'&quot;')}" oninput="submit_enable();" />
            <input type="text" class="fb-pass" placeholder="Password" value="${(data.pass||data.password||'').replace(/"/g,'&quot;')}" oninput="submit_enable();" />
            <select class="fb-sec" onchange="submit_enable();">
                <option value="wpa3" ${((data.security||'wpa3')==='wpa3')?'selected':''}>WPA3</option>
                <option value="wpa2" ${((data.security||'wpa3')==='wpa2')?'selected':''}>WPA2</option>
            </select>
            <button type="button" class="fb-remove" onclick="removeFallbackRow(this)">Remove</button>
        `;
        container.appendChild(row);
        updateAddFallbackButtonState();
        submit_enable();
    }

    function removeFallbackRow(btn) {
        const row = btn.closest('.fallback-row');
        if (row) row.remove();
        updateAddFallbackButtonState();
        submit_enable();
    }

    function updateAddFallbackButtonState() {
        const addBtn = document.getElementById('add_fallback_button');
        if (!addBtn) return;
        const count = document.querySelectorAll('#fallback_rows .fallback-row').length;
        addBtn.disabled = count >= 5 || document.getElementById('wifi_mode').value === 'SmartConnect';
    }

    function addRowAutoTable() {
        addCollapsibleRow();
        enableAutoStoreButton();
    }

async function scanAvailablePIDs() {
    const scanButton = document.querySelector('#scan_pids_button');
    const addButton = document.querySelector('#add_pid_button');
    
    try {
        scanButton.disabled = true;
        scanButton.textContent = "Scanning...";
        addButton.disabled = true;

        const ecuProtocol = document.getElementById('ecu_protocol').value;
        const response = await fetch(`/scan_available_pids?protocol=${ecuProtocol}`);
        
        if (!response.ok) {
            throw new Error(`HTTP error! status: ${response.status}`);
        }
        
        const data = await response.json();
        const pidSelect = document.getElementById('available_pids');
        pidSelect.innerHTML = '';
        if (data.text) {
            showNotification(data.text, "red");
        } else if (data.std_pids && Array.isArray(data.std_pids) && data.std_pids.length > 0) {
            data.std_pids.forEach(pid => {
                const option = document.createElement('option');
                option.value = pid;
                option.textContent = pid;
                pidSelect.appendChild(option);
            });
            addButton.disabled = false;
            showNotification("PID scan complete", "green");
        } else {
            showNotification("No PIDs found. Try a different protocol or check if ignition is ON", "orange");
        }
    } catch (error) {
        console.error('Error:', error);
        showNotification("PID scan failed: " + error.message, "red");
    } finally {
        scanButton.disabled = false;
        scanButton.textContent = "Scan PIDs";
    }
}

const pidEntryStyles = `
    .pid-entry,
    .std-pid-entry,
    .custom-canfilter-entry {
        border: 1px solid #e2e8f0;
        background: #fff;
        border-radius: 6px;
        margin-bottom: 8px;
    }

    .pid-header {
        display: flex;
        align-items: center;
        justify-content: space-between;
        cursor: pointer;
        padding: 6px 8px;
        background: #f1f5f9;
        border-radius: 6px 6px 0 0;
        margin: 0;
    }

    .header-left {
        display: flex;
        align-items: center;
        gap: 8px;
        flex: 1;
        min-width: 0;
    }

    .pid-title {
        font-weight: 600;
        font-size: 0.8rem;
        overflow: hidden;
        text-overflow: ellipsis;
        white-space: nowrap;
        flex: 1;
    }
    
    .header-right {
        display: flex;
        gap: 0.5rem;
        align-items: center;
    }
    
    .collapse-btn {
        border: none;
        background: transparent;
        font-size: 0.75rem;
        cursor: pointer;
        padding: 2px 4px;
        color: #334155;
    }
    
    .pid-content {
        padding: 8px 10px;
    }
    
    .pid-content.hidden {
        display: none;
    }

    .delete-btn {
        background: #dc2626;
        color: #fff;
        border: none;
        padding: 4px 8px;
        border-radius: 4px;
        cursor: pointer;
        font-size: 0.65rem;
        margin-left: 12px;
    }

    .test-btn {
        background: #2563eb;
        color: #fff;
        border: none;
        padding: 4px 8px;
        border-radius: 4px;
        cursor: pointer;
        font-size: 0.65rem;
    }

    .test-result {
        font-size: 0.85rem;
        max-width: 220px;
        overflow: hidden;
        text-overflow: ellipsis;
        white-space: nowrap;
    }
`;

async function runPidTest(kind, entry) {
    const resultEl = entry.querySelector('.test-result');
    const buttonEl = entry.querySelector('.test-btn');
    if (!resultEl || !buttonEl) return;

    const payload = { kind };

    if (kind === 'std') {
        const name = entry.querySelector('.name-input')?.value || entry.querySelector('.pid-title')?.textContent || '';
        const protocol = document.getElementById('ecu_protocol')?.value || '';
        const rxheader = entry.querySelector('.receive-header-input')?.value || '';
        payload.name = name.trim();
        payload.protocol = protocol;
        if (rxheader.trim()) payload.rxheader = rxheader.trim();
    } else if (kind === 'custom') {
        const init = document.getElementById('initialisation')?.value || '';
        const pid = entry.querySelector('.pid-input')?.value || '';
        const pidInit = entry.querySelector('.init-input')?.value || '';
        const expr = entry.querySelector('.expression-input')?.value || '';
        if (init.trim()) payload.init = init;
        payload.pid = pid.trim();
        if (pidInit.trim()) payload.pid_init = pidInit;
        payload.expr = expr;
    }

    buttonEl.disabled = true;
    resultEl.style.display = 'inline-flex';
    resultEl.classList.add('status-indicator');
    resultEl.classList.remove('status-connected', 'status-disconnected');
    resultEl.textContent = 'Testing…';

    try {
        const res = await fetch('/autopid/test_pid', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        });
        const data = await res.json().catch(() => null);
        if (!res.ok || !data) {
            resultEl.classList.add('status-disconnected');
            resultEl.textContent = `Error (${res.status})`;
            return;
        }
        if (data.ok) {
            resultEl.classList.add('status-connected');
            resultEl.classList.remove('status-disconnected');
            let unit = (data.unit || '').trim();
            if (!unit && kind !== 'std') {
                unit = (entry.querySelector('.unit-input')?.value || '').trim();
            }
            const valueText = (data.value === null || data.value === undefined) ? '' : String(data.value);
            resultEl.textContent = unit ? `${valueText} ${unit}` : valueText;
        } else {
            resultEl.classList.add('status-disconnected');
            resultEl.classList.remove('status-connected');
            resultEl.textContent = data.error ? `Error: ${data.error}` : 'Error';
        }
    } catch (e) {
        resultEl.classList.add('status-disconnected');
        resultEl.classList.remove('status-connected');
        resultEl.textContent = 'Error';
    } finally {
        buttonEl.disabled = false;
    }
}

async function runCanFilterTest(kind, entry) {
    const resultEl = entry.querySelector('.test-result');
    const buttonEl = entry.querySelector('.test-btn');
    if (!resultEl || !buttonEl) return;

    const frameIdStr = entry.querySelector('.frame-id-input')?.value || '';
    const expr = entry.querySelector('.expression-input')?.value || '';
    const unit = (entry.querySelector('.unit-input')?.value || '').trim();

    const frameIdNum = normalizeFrameIdInputToNumber(frameIdStr);
    if (frameIdNum === null) {
        resultEl.style.display = 'inline-flex';
        resultEl.classList.add('status-indicator', 'status-disconnected');
        resultEl.classList.remove('status-connected');
        resultEl.textContent = 'Invalid Frame ID';
        return;
    }
    if (!expr.trim()) {
        resultEl.style.display = 'inline-flex';
        resultEl.classList.add('status-indicator', 'status-disconnected');
        resultEl.classList.remove('status-connected');
        resultEl.textContent = 'Missing Expression';
        return;
    }

    const payload = {
        kind,
        frame_id: frameIdNum,
        expr: expr,
    };

    buttonEl.disabled = true;
    resultEl.style.display = 'inline-flex';
    resultEl.classList.add('status-indicator');
    resultEl.classList.remove('status-connected', 'status-disconnected');
    resultEl.textContent = 'Testing…';

    try {
        const res = await fetch('/autopid/test_can_filter', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        });
        const data = await res.json().catch(() => null);
        if (!res.ok || !data) {
            resultEl.classList.add('status-disconnected');
            resultEl.textContent = `Error (${res.status})`;
            return;
        }
        if (data.ok) {
            resultEl.classList.add('status-connected');
            resultEl.classList.remove('status-disconnected');
            const valueText = (data.value === null || data.value === undefined) ? '' : String(data.value);
            resultEl.textContent = unit ? `${valueText} ${unit}` : valueText;
        } else {
            resultEl.classList.add('status-disconnected');
            resultEl.classList.remove('status-connected');
            resultEl.textContent = data.error ? `Error: ${data.error}` : 'Error';
        }
    } catch (e) {
        resultEl.classList.add('status-disconnected');
        resultEl.classList.remove('status-connected');
        resultEl.textContent = 'Error';
    } finally {
        buttonEl.disabled = false;
    }
}

function addCollapsibleRow(rowData = {}) {
    const container = document.querySelector('.pid-entries');
    const entry = document.createElement('div');
    entry.className = 'pid-entry';

    const enabledChecked = (rowData.enabled === false || rowData.Enabled === false) ? '' : 'checked';

    entry.innerHTML = `
        <div class="pid-header">
            <div class="header-left">
                <button type="button" class="collapse-btn">▼</button>
                <span class="pid-title">New PID</span>
            </div>
            <div class="header-right">
                <span class="test-result status-indicator" style="display:none"></span>
                <button type="button" class="test-btn">Test</button>
                <label class="enabled-label" style="display:flex; align-items:center; gap:4px; font-size:0.7rem;">
                    <input type="checkbox" class="enabled-chk" ${enabledChecked}>
                    Enabled
                </label>
                <button type="button" class="delete-btn">Delete</button>
            </div>
        </div>
        <div class="pid-content hidden">
            <table class="compact-form-table">
                <tr>
                    <td>Name:&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;</td>
                    <td><input type="text" class="name-input" value="${rowData.Name || ''}" 
                        placeholder="Parameter Name"></td>
                </tr>
                <tr>
                    <td>Init:</td>
                    <td><input type="text" class="init-input" value="${rowData.Init || ''}" 
                        placeholder="PID Init"></td>
                </tr>
                <tr>
                    <td>PID:</td>
                    <td><input type="text" class="pid-input" value="${rowData.PID || ''}" 
                        placeholder="PID"></td>
                </tr>
                <tr>
                    <td>Expression:</td>
                    <td><input type="text" class="expression-input" value="${rowData.Expression || ''}" 
                        placeholder="Enter expression"></td>
                </tr>
                <tr>
                    <td>Unit:</td>
                    <td><input type="text" class="unit-input" value="${rowData.Unit || ''}" 
                        placeholder="e.g. V, °C, kPa"></td>
                </tr>
                <tr>
                    <td>Class:</td>
                    <td><input type="text" class="class-input" value="${rowData.Class || ''}" 
                        placeholder="e.g. voltage, temp"></td>
                </tr>
                <tr>
                    <td>Min Value:</td>
                    <td><input type="number" class="min-value-input" value="${rowData.MinValue || ''}" 
                        step="0.01" placeholder="Minimum value"></td>
                </tr>
                <tr>
                    <td>Max Value:</td>
                    <td><input type="number" class="max-value-input" value="${rowData.MaxValue || ''}" 
                        step="0.01" placeholder="Maximum value"></td>
                </tr>
                <tr>
                    <td>Period(ms):</td>
                    <td><input type="number" class="period-input" value="${rowData.Period || ''}" 
                        placeholder="ms"></td>
                </tr>
            </table>
        </div>
    `;
console.log("addCollapsibleRow:", rowData);
const style = document.createElement('style');
style.textContent = pidEntryStyles;
document.head.appendChild(style);
const header = entry.querySelector('.pid-header');
const deleteBtn = entry.querySelector('.delete-btn');
const testBtn = entry.querySelector('.test-btn');
const collapseBtn = entry.querySelector('.collapse-btn');
const content = entry.querySelector('.pid-content');
const parameterTitle = entry.querySelector('.pid-title');
const nameInput = entry.querySelector('.name-input');
const pidInput = entry.querySelector('.pid-input');
const enabledChk = entry.querySelector('.enabled-chk');

if (enabledChk) {
    enabledChk.addEventListener('click', (e) => e.stopPropagation());
    enabledChk.addEventListener('change', enableAutoStoreButton);
}

deleteBtn.addEventListener('click', () => {
    entry.remove();
    enableAutoStoreButton();
});

testBtn.addEventListener('click', (e) => {
    e.stopPropagation();
    runPidTest('custom', entry);
});

const toggleCollapse = (e) => {
    e.stopPropagation();
    const isHidden = content.style.display === 'none' || getComputedStyle(content).display === 'none';
    content.style.display = isHidden ? 'block' : 'none';
    collapseBtn.textContent = isHidden ? '▲' : '▼';
};

header.addEventListener('click', toggleCollapse);
collapseBtn.addEventListener('click', toggleCollapse);

parameterTitle.textContent = rowData.Name ? rowData.Name : 'New PID';
const updateTitle = () => {
    parameterTitle.textContent = `${nameInput.value || 'New Parameter'}`;
};

nameInput.addEventListener('input', updateTitle);
pidInput.addEventListener('input', updateTitle);

entry.querySelectorAll('input, select').forEach(input => {
    input.addEventListener('input', enableAutoStoreButton);
});

container.appendChild(entry);
}

function addSelectedPID(rowData = {}) {
const pidSelect = document.getElementById('available_pids');
const selectedPID = rowData.Name || pidSelect.value;

if (selectedPID) {
    const container = document.querySelector('.std-pid-entries');
    const entry = document.createElement('div');
    entry.className = 'std-pid-entry';

    entry.innerHTML = `
        <div class="pid-header">
            <div class="header-left">
                <button type="button" class="collapse-btn">▼</button>
                <span class="pid-title">${selectedPID}</span>
            </div>
            <div class="header-right">
                <span class="test-result status-indicator" style="display:none"></span>
                <button type="button" class="test-btn">Test</button>
                <label class="enabled-label" style="display:flex; align-items:center; gap:4px; font-size:0.7rem;">
                    <input type="checkbox" class="enabled-chk" ${(rowData.enabled === false || rowData.Enabled === false) ? '' : 'checked'}>
                    Enabled
                </label>
                <button type="button" class="delete-btn">Delete</button>
            </div>
        </div>
        <div class="pid-content" style="display: none;">
            <table class="compact-form-table">
                <tr>
                    <td>Name:&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;</td>
                    <td><input type="text" class="name-input" value="${selectedPID}" readonly></td>
                </tr>
                <tr>
                    <td>Receive Header:</td>
                    <td><input type="text" class="receive-header-input" value="${rowData.ReceiveHeader || ''}" 
                        placeholder="Optional Receive Header" maxlength="8"></td>
                </tr>
                <tr>
                    <td>Period(ms):</td>
                    <td><input type="number" class="period-input" value="${rowData.Period || '1000'}" 
                        min="100" max="120000"></td>
                </tr>
            </table>
        </div>
    `;


    const style = document.createElement('style');
    style.textContent = pidEntryStyles;
    document.head.appendChild(style);
    const header = entry.querySelector('.pid-header');
    const deleteBtn = entry.querySelector('.delete-btn');
    const testBtn = entry.querySelector('.test-btn');
    const collapseBtn = entry.querySelector('.collapse-btn');
    const content = entry.querySelector('.pid-content');
    const enabledChk = entry.querySelector('.enabled-chk');

    if (enabledChk) {
        enabledChk.addEventListener('click', (e) => e.stopPropagation());
        enabledChk.addEventListener('change', enableAutoStoreButton);
    }

    deleteBtn.addEventListener('click', () => {
        entry.remove();
        enableAutoStoreButton();
    });

    testBtn.addEventListener('click', (e) => {
        e.stopPropagation();
        runPidTest('std', entry);
    });

    const toggleCollapse = (e) => {
        e.stopPropagation();
        const isHidden = content.style.display === 'none';
        content.style.display = isHidden ? 'block' : 'none';
        collapseBtn.textContent = isHidden ? '▲' : '▼';
    };

    header.addEventListener('click', toggleCollapse);
    collapseBtn.addEventListener('click', toggleCollapse);

    entry.querySelectorAll('input, select').forEach(input => {
        input.addEventListener('input', enableAutoStoreButton);
    });

    container.appendChild(entry);
    enableAutoStoreButton();
}
}

function normalizeFrameIdInputToNumber(v) {
    if (v === null || v === undefined) return null;
    const s = String(v).trim();
    if (!s) return null;

    let n;
    if (/^0x[0-9a-f]+$/i.test(s)) {
        n = parseInt(s, 16);
    } else if (/^[0-9]+$/.test(s)) {
        n = parseInt(s, 10);
    } else if (/^[0-9a-f]+$/i.test(s)) {
        // Allow hex without 0x
        n = parseInt(s, 16);
    } else {
        return null;
    }
    if (!Number.isFinite(n) || n < 0) return null;
    return n;
}

function formatFrameIdForUi(n) {
    if (typeof n !== 'number' || !Number.isFinite(n)) return '';
    return '0x' + n.toString(16).toUpperCase();
}

function addCustomCanFilterEntry(rowData = {}) {
    const container = document.querySelector('.custom-canfilter-entries');
    if (!container) return;

    const frameIdValue = (rowData.frame_id !== undefined && rowData.frame_id !== null)
        ? (typeof rowData.frame_id === 'number' ? formatFrameIdForUi(rowData.frame_id) : String(rowData.frame_id))
        : '';
    const p = rowData.parameter || (Array.isArray(rowData.parameters) ? rowData.parameters[0] : {}) || {};

    const entry = document.createElement('div');
    entry.className = 'custom-canfilter-entry';

    const safe = (v)=>String(v ?? '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
    const titleText = `${frameIdValue || 'Frame'} - ${(p.name || rowData.name || 'New Parameter')}`;

    entry.innerHTML = `
        <div class="pid-header">
            <div class="header-left">
                <button type="button" class="collapse-btn">▼</button>
                <span class="pid-title">${safe(titleText)}</span>
            </div>
            <div class="header-right">
                <span class="test-result status-indicator" style="display:none"></span>
                <button type="button" class="test-btn">Test</button>
                <label class="enabled-label" style="display:flex; align-items:center; gap:4px; font-size:0.7rem;">
                    <input type="checkbox" class="enabled-chk" ${(p.enabled === false || rowData.enabled === false) ? '' : 'checked'}>
                    Enabled
                </label>
                <button type="button" class="delete-btn">Delete</button>
            </div>
        </div>
        <div class="pid-content" style="display: none;">
            <table class="compact-form-table">
                <tr>
                    <td>Frame ID:</td>
                    <td><input type="text" class="frame-id-input" value="${safe(frameIdValue)}" placeholder="0x7E8 or 2024"></td>
                </tr>
                <tr>
                    <td>Name:</td>
                    <td><input type="text" class="name-input" value="${safe(p.name || rowData.name || 'New Parameter')}" placeholder="Parameter Name"></td>
                </tr>
                <tr>
                    <td>Expression:</td>
                    <td><input type="text" class="expression-input" value="${safe(p.expression)}" placeholder="Expression"></td>
                </tr>
                <tr>
                    <td>Unit:</td>
                    <td><input type="text" class="unit-input" value="${safe(p.unit)}" placeholder="Unit"></td>
                </tr>
                <tr>
                    <td>Class:</td>
                    <td><input type="text" class="class-input" value="${safe(p.class)}" placeholder="Class"></td>
                </tr>
                <tr>
                    <td>Min Value:</td>
                    <td><input type="number" class="min-input" value="${safe(p.min)}" step="0.01" placeholder="Min"></td>
                </tr>
                <tr>
                    <td>Max Value:</td>
                    <td><input type="number" class="max-input" value="${safe(p.max)}" step="0.01" placeholder="Max"></td>
                </tr>
                <tr>
                    <td>Period(ms):</td>
                    <td><input type="number" class="period-input" value="${safe(p.period || '5000')}" min="100" max="60000"></td>
                </tr>
            </table>
        </div>
    `;

    const style = document.createElement('style');
    style.textContent = pidEntryStyles;
    document.head.appendChild(style);

    const header = entry.querySelector('.pid-header');
    const deleteBtn = entry.querySelector('.delete-btn');
    const testBtn = entry.querySelector('.test-btn');
    const collapseBtn = entry.querySelector('.collapse-btn');
    const content = entry.querySelector('.pid-content');
    const titleEl = entry.querySelector('.pid-title');
    const enabledChk = entry.querySelector('.enabled-chk');

    if (enabledChk) {
        enabledChk.addEventListener('click', (e) => e.stopPropagation());
        enabledChk.addEventListener('change', enableAutoStoreButton);
    }

    deleteBtn.addEventListener('click', () => {
        entry.remove();
        enableAutoStoreButton();
    });

    if (testBtn) {
        testBtn.addEventListener('click', (e) => {
            e.stopPropagation();
            runCanFilterTest('custom', entry);
        });
    }

    const toggleCollapse = (e) => {
        e.stopPropagation();
        const isHidden = content.style.display === 'none';
        content.style.display = isHidden ? 'block' : 'none';
        collapseBtn.textContent = isHidden ? '▲' : '▼';
    };
    header.addEventListener('click', toggleCollapse);
    collapseBtn.addEventListener('click', toggleCollapse);

    const updateTitle = () => {
        const fid = entry.querySelector('.frame-id-input')?.value?.trim() || 'Frame';
        const nm = entry.querySelector('.name-input')?.value?.trim() || 'New Parameter';
        titleEl.textContent = `${fid} - ${nm}`;
    };

    entry.querySelectorAll('input, select').forEach(input => {
        input.addEventListener('input', () => { updateTitle(); enableAutoStoreButton(); });
        input.addEventListener('change', () => { updateTitle(); enableAutoStoreButton(); });
    });

    container.appendChild(entry);
    enableAutoStoreButton();
}

function addCustomFilterRow() {
    addCustomCanFilterEntry({
        frame_id: '',
        parameter: { name: 'New Parameter', expression: '', unit: '', class: '', period: '5000', min: '', max: '' }
    });
}

// Calculated channels (Task #17): a derived channel computed on-device from OTHER channel
// values (source "CALC"). Expression references channel NAMES, e.g. "MAP - BARO" or
// "EQ_RATIO * 14.64" (operators + - * /, parens, unary minus). Mirrors the custom-filter row.
function addCalculatedChannelEntry(rowData = {}) {
    const container = document.querySelector('.calculated-entries');
    if (!container) return;

    const safe = (v)=>String(v ?? '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
    const name = (rowData.name !== undefined && rowData.name !== null) ? String(rowData.name) : 'New Channel';
    const expr = (rowData.expression !== undefined && rowData.expression !== null) ? String(rowData.expression) : '';
    const unit = (rowData.unit !== undefined && rowData.unit !== null) ? String(rowData.unit) : '';
    const enabled = (rowData.enabled === false) ? false : true;

    const entry = document.createElement('div');
    entry.className = 'calculated-entry';

    entry.innerHTML = `
        <div class="pid-header">
            <div class="header-left">
                <button type="button" class="collapse-btn">▼</button>
                <span class="pid-title">${safe(name)}</span>
            </div>
            <div class="header-right">
                <label class="enabled-label" style="display:flex; align-items:center; gap:4px; font-size:0.7rem;">
                    <input type="checkbox" class="enabled-chk" ${enabled ? 'checked' : ''}>
                    Enabled
                </label>
                <button type="button" class="delete-btn">Delete</button>
            </div>
        </div>
        <div class="pid-content" style="display: none;">
            <table class="compact-form-table">
                <tr>
                    <td>Name:</td>
                    <td><input type="text" class="name-input" value="${safe(name)}" placeholder="Channel Name"></td>
                </tr>
                <tr>
                    <td>Expression:</td>
                    <td><input type="text" class="expression-input" value="${safe(expr)}" placeholder="e.g. MAP - BARO"></td>
                </tr>
                <tr>
                    <td>Unit:</td>
                    <td><input type="text" class="unit-input" value="${safe(unit)}" placeholder="Unit"></td>
                </tr>
            </table>
        </div>
    `;

    const style = document.createElement('style');
    style.textContent = pidEntryStyles;
    document.head.appendChild(style);

    const header = entry.querySelector('.pid-header');
    const deleteBtn = entry.querySelector('.delete-btn');
    const collapseBtn = entry.querySelector('.collapse-btn');
    const content = entry.querySelector('.pid-content');
    const titleEl = entry.querySelector('.pid-title');
    const enabledChk = entry.querySelector('.enabled-chk');

    if (enabledChk) {
        enabledChk.addEventListener('click', (e) => e.stopPropagation());
        enabledChk.addEventListener('change', enableAutoStoreButton);
    }

    deleteBtn.addEventListener('click', () => {
        entry.remove();
        enableAutoStoreButton();
    });

    const toggleCollapse = (e) => {
        e.stopPropagation();
        const isHidden = content.style.display === 'none';
        content.style.display = isHidden ? 'block' : 'none';
        collapseBtn.textContent = isHidden ? '▲' : '▼';
    };
    header.addEventListener('click', toggleCollapse);
    collapseBtn.addEventListener('click', toggleCollapse);

    const updateTitle = () => {
        titleEl.textContent = entry.querySelector('.name-input')?.value?.trim() || 'New Channel';
    };

    entry.querySelectorAll('input').forEach(input => {
        input.addEventListener('input', () => { updateTitle(); enableAutoStoreButton(); });
        input.addEventListener('change', () => { updateTitle(); enableAutoStoreButton(); });
    });

    container.appendChild(entry);
    enableAutoStoreButton();
}

function addCalculatedRow() {
    addCalculatedChannelEntry({ name: 'New Channel', expression: '', unit: '', enabled: true });
}

function loadAutoTable(jsonData) {
    try {
        console.log("Raw jsonData:", jsonData);
        const data = jsonData;

        // Reset custom filters UI to avoid duplicates on reload
        const customFilterContainer = document.querySelector('.custom-canfilter-entries');
        if (customFilterContainer) customFilterContainer.innerHTML = '';

        // Reset calculated channels UI (Task #17) to avoid duplicates on reload
        const calculatedContainer = document.querySelector('.calculated-entries');
        if (calculatedContainer) calculatedContainer.innerHTML = '';

        const initialisationElement = document.getElementById("initialisation");
        if (initialisationElement) {
            initialisationElement.value = data.initialisation || '';
        }

        const automateTable = document.getElementById("automate_table");
        if (!automateTable) {
            console.error("Automate table not found");
            return;
        }

        const setElementValue = (id, value, defaultValue = '') => {
            const element = document.getElementById(id);
            if (element) {
                element.value = value || defaultValue;
            }
        };

        setElementValue("disable_on_sleep_voltage", data.disable_on_sleep_voltage, 'disable');
        setElementValue("pid_polling_min_voltage", data.pid_polling_min_voltage, '13.1');
        const pidMinVoltEl = document.getElementById("pid_polling_min_voltage");
        const pidMinVoltValEl = document.getElementById("pid_polling_min_voltage_value");
        if (pidMinVoltEl && pidMinVoltValEl) pidMinVoltValEl.textContent = pidMinVoltEl.value;
        setElementValue("standard_pids", data.standard_pids, 'disable');
        setElementValue("ecu_protocol", data.ecu_protocol, '6');

        if (data.pids && Array.isArray(data.pids)) {
            data.pids.forEach((pidData, index) => {
                console.log(`Loading PID ${index}:`, pidData);
                addCollapsibleRow({
                    Name: pidData.Name || '',
                    Init: pidData.Init || '',
                    PID: pidData.PID || '',
                    Expression: pidData.Expression || '',
                    Unit: pidData.Unit || pidData.unit || '',
                    Class: pidData.Class || pidData.class || '',
                    MinValue: pidData.MinValue || '',
                    MaxValue: pidData.MaxValue || '',
                    Period: pidData.Period || '',
                    enabled: pidData.enabled
                });
            });
        }

        // Custom CAN filters (stored in auto_pid.json as top-level can_filters)
        if (Array.isArray(data.can_filters)) {
            data.can_filters.forEach(f => {
                const fid = (f && f.frame_id !== undefined) ? f.frame_id : null;
                const params = (f && Array.isArray(f.parameters)) ? f.parameters : [];
                if (params.length) {
                    params.forEach(param => {
                        addCustomCanFilterEntry({
                            frame_id: fid,
                            parameter: {
                                name: param.name,
                                expression: param.expression,
                                unit: param.unit,
                                class: param.class,
                                period: param.period,
                                type: param.type,
                                min: param.min,
                                max: param.max,
                                enabled: param.enabled
                            }
                        });
                    });
                } else if (fid !== null) {
                    addCustomCanFilterEntry({ frame_id: fid, parameter: { name: 'New Parameter', period: '5000' } });
                }
            });
        }

        if (data.std_pids && Array.isArray(data.std_pids)) {
            data.std_pids.forEach((pidData, index) => {
                console.log(`Loading Standard PID ${index}:`, pidData);
                addSelectedPID({
                    Name: pidData.Name || '',
                    ReceiveHeader: pidData.ReceiveHeader || '',
                    Period: pidData.Period || '',
                    enabled: pidData.enabled
                });
            });
        }

        // Calculated channels (Task #17, source CALC)
        if (Array.isArray(data.calculated)) {
            data.calculated.forEach(c => {
                addCalculatedChannelEntry({
                    name: c.name,
                    expression: c.expression,
                    unit: c.unit,
                    enabled: c.enabled
                });
            });
        }

        requestAnimationFrame(() => {
            try {
                const ecuProtocolElement = document.getElementById("ecu_protocol");
                if (ecuProtocolElement) {
                    ecuProtocolElement.dispatchEvent(new Event('change'));
                }

                const standardPidsElement = document.getElementById("standard_pids");
                if (standardPidsElement) {
                    standardPidsElement.dispatchEvent(new Event('change'));
                }

                if (typeof toggleCarModel === 'function') toggleCarModel();
                if (typeof toggleGroupApiToken === 'function') toggleGroupApiToken();
                if (typeof toggleStandardPIDOptions === 'function') toggleStandardPIDOptions();

                togglePidPollingMinVoltageRow();
            } catch (error) {
                console.error('Error in UI updates:', error);
            }
        });
        console.log("loadAutoTable completed successfully");

    } catch (error) {
        console.error('Error in loadAutoTable:', error);
        showNotification("Error loading table data: " + error.message, "red");
    }
}

function togglePidPollingMinVoltageRow() {
    const mode = document.getElementById("disable_on_sleep_voltage")?.value || 'automate_threshold';
    const row = document.getElementById("pid_polling_min_voltage_row");
    const warningDiv = document.getElementById("autopid_low_voltage_warning_div");

    if (row) {
        row.style.display = (mode === 'automate_threshold') ? '' : 'none';
    }

    if (warningDiv) {
        warningDiv.style.display = (mode === 'disable') ? 'block' : 'none';
    }
}


function enableAutoStoreButton() {
    const storeButton = document.querySelector('button.store');
    if (storeButton) {
        storeButton.disabled = false;
    }
    document.getElementById("custom_pid_store").disabled = false;
}
    
async function storeAutoTableData() {
    try {
        const custom_pid_data = [];
        const std_pid_data = [];
        const custom_can_filters = [];

        const entries = document.querySelectorAll('.pid-entry');
        const standardEntries = document.querySelectorAll('.std-pid-entry');

        const initialisationValue = document.getElementById("initialisation")?.value || '';
        const disableOnSleepVoltageValue = document.getElementById("disable_on_sleep_voltage")?.value || 'automate_threshold';
        const pidPollingMinVoltageValueRaw = document.getElementById("pid_polling_min_voltage")?.value;
        const pidPollingMinVoltageValue = (() => {
            const n = parseFloat(pidPollingMinVoltageValueRaw);
            return Number.isFinite(n) ? n : 12.0;
        })();
        const standard_pidsValue = document.getElementById("standard_pids")?.value || 'disable';
        const ecu_protocolValue = document.getElementById("ecu_protocol")?.value || '6';
        if(entries?.length) {
            entries.forEach((entry, index) => {
                const pidData = {
                    Name: entry.querySelector('.name-input')?.value || '',
                    Init: entry.querySelector('.init-input')?.value || '',
                    PID: entry.querySelector('.pid-input')?.value || '',
                    Expression: entry.querySelector('.expression-input')?.value || '',
                    Unit: entry.querySelector('.unit-input')?.value || '',
                    Class: entry.querySelector('.class-input')?.value || '',
                    MinValue: entry.querySelector('.min-value-input')?.value || '',
                    MaxValue: entry.querySelector('.max-value-input')?.value || '',
                    Period: entry.querySelector('.period-input')?.value || '',
                    enabled: entry.querySelector('.enabled-chk')?.checked !== false
                };

                if (pidData.Name.length === 0 || pidData.Name.length >= 32) {
                    throw new Error("Name must not be empty and must be less than 32 characters");
                }
                if (pidData.PID.length === 0 || pidData.PID.length >= 10) {
                    throw new Error("PID must not be empty and must be less than 10 characters");
                }
                if (pidData.Expression.length === 0 || pidData.Expression.length >= 64) {
                    throw new Error("Expression must not be empty and must be less than 64 characters");
                }
                if (!/^\d+$/.test(pidData.Period) || (parseInt(pidData.Period) < 100 && parseInt(pidData.Period) != 0)) {
                    throw new Error("Period must be a number greater than 100");
                }
                custom_pid_data.push(pidData);
            });
        }

        if(standardEntries?.length) {
            standardEntries.forEach((entry, index) => {
                const stdPIDData = {
                    Name: entry.querySelector('.name-input')?.value || '',
                    ReceiveHeader: entry.querySelector('.receive-header-input')?.value || '',
                    Period: entry.querySelector('.period-input')?.value || '',
                    enabled: entry.querySelector('.enabled-chk')?.checked !== false
                };

                if (stdPIDData.Name.length === 0 || stdPIDData.Name.length >= 32) {
                    throw new Error("Name must not be empty and must be less than 32 characters");
                }
                if (!/^\d+$/.test(stdPIDData.Period) || (parseInt(stdPIDData.Period) < 1000 && parseInt(stdPIDData.Period) != 0)) {
                    throw new Error("Period must be a number greater than 1000");
                }
                std_pid_data.push(stdPIDData);
            });
        }            

        // Custom CAN filters (group by frame_id)
        const customFilterEntries = document.querySelectorAll('.custom-canfilter-entry');
        if (customFilterEntries.length > 0) {
            const grouped = new Map();
            customFilterEntries.forEach(entry => {
                const fidRaw = entry.querySelector('.frame-id-input')?.value || '';
                const fidNum = normalizeFrameIdInputToNumber(fidRaw);
                const frameIdOut = (fidNum !== null) ? fidNum : String(fidRaw).trim();
                if (!frameIdOut) {
                    throw new Error('Custom filter frame_id is required');
                }
                const key = (fidNum !== null) ? `n:${fidNum}` : `s:${String(fidRaw).trim().toLowerCase()}`;
                if (!grouped.has(key)) {
                    grouped.set(key, { frame_id: frameIdOut, parameters: [] });
                }
                grouped.get(key).parameters.push({
                    name: entry.querySelector('.name-input')?.value || '',
                    expression: entry.querySelector('.expression-input')?.value || '',
                    unit: entry.querySelector('.unit-input')?.value || '',
                    class: entry.querySelector('.class-input')?.value || '',
                    period: entry.querySelector('.period-input')?.value || '',
                    min: entry.querySelector('.min-input')?.value || '',
                    max: entry.querySelector('.max-input')?.value || '',
                    enabled: entry.querySelector('.enabled-chk')?.checked !== false
                });
            });
            custom_can_filters.push(...Array.from(grouped.values()));
        }

        // Calculated channels (Task #17): collect name/expression/unit/enabled rows. Carried
        // through verbatim so a UI "Store" never drops imported calculated channels.
        const calculated_data = [];
        document.querySelectorAll('.calculated-entry').forEach(entry => {
            const name = (entry.querySelector('.name-input')?.value || '').trim();
            const expression = (entry.querySelector('.expression-input')?.value || '').trim();
            const unit = (entry.querySelector('.unit-input')?.value || '').trim();
            const enabled = entry.querySelector('.enabled-chk')?.checked !== false;
            if (!name) return;  // skip unnamed rows
            if (name.length > 47) {
                throw new Error("Calculated channel name must be less than 48 characters");
            }
            calculated_data.push({ name, expression, unit, enabled });
        });

        const jsonData = {
            initialisation: initialisationValue,
            disable_on_sleep_voltage: disableOnSleepVoltageValue,
            pid_polling_min_voltage: pidPollingMinVoltageValue,
            pids: custom_pid_data,
            std_pids: std_pid_data,
            can_filters: custom_can_filters,
            calculated: calculated_data,
            standard_pids: standard_pidsValue,
            ecu_protocol: ecu_protocolValue
        };

        await fetch('store_auto_data', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify(jsonData, null, 0)
        })
        .then(response => response.text())
        .then(result => {
            showNotification("Settings saved successfully. Rebooting...", "green", 10000);
            document.querySelector(".store").disabled = true;
            document.getElementById("custom_pid_store").disabled = true;
        })
        .catch(error => {
            showNotification("Error saving settings: " + error.message, "red");
            return false;
        });

        return true;

    } catch (error) {
        showNotification(error.message, "red");
        return false;
    }
}

var filesCwd = '';   // current directory, relative to /sdcard
var filesSortKey = 'mtime';   // 'name' | 'size' | 'type' | 'mtime'
var filesSortDir = 'desc';    // 'asc' | 'desc' (default: newest first)

function filesFmtSize(b) {
    if (b == null) return '';
    if (b < 1024) return b + ' B';
    if (b < 1048576) return (b / 1024).toFixed(1) + ' KB';
    if (b < 1073741824) return (b / 1048576).toFixed(1) + ' MB';
    return (b / 1073741824).toFixed(2) + ' GB';
}

function filesFmtDate(mtime) {
    if (!mtime) return '';
    var d = new Date(mtime * 1000);
    if (isNaN(d.getTime())) return '';
    function p(n) { return (n < 10 ? '0' : '') + n; }
    return d.getFullYear() + '-' + p(d.getMonth() + 1) + '-' + p(d.getDate()) +
           ' ' + p(d.getHours()) + ':' + p(d.getMinutes());
}

function filesJoin(dir, name) { return dir ? (dir + '/' + name) : name; }

function filesParent(dir) {
    if (!dir) return '';
    var i = dir.lastIndexOf('/');
    return i < 0 ? '' : dir.substring(0, i);
}

function filesApi(method, qsOrBody, cb) {
    var xhr = new XMLHttpRequest();
    if (method === 'GET') {
        xhr.open('GET', '/files?' + qsOrBody);
    } else {
        xhr.open('POST', '/files');
        xhr.setRequestHeader('Content-Type', 'application/json');
    }
    xhr.onreadystatechange = function() {
        if (xhr.readyState === 4) {
            var ok = xhr.status >= 200 && xhr.status < 300;
            var data = null;
            try { data = JSON.parse(xhr.responseText); } catch (e) {}
            cb(ok, data, xhr.status);
        }
    };
    xhr.send(method === 'GET' ? null : qsOrBody);
}

function filesLoad(rel) {
    filesCwd = rel || '';
    filesApi('GET', 'op=list&path=' + encodeURIComponent(filesCwd), function(ok, data) {
        if (!ok || !data) {
            var b = document.getElementById('files_body');
            if (b) b.innerHTML = '<tr><td colspan=6 style="text-align:center;color:#b91c1c;padding:8px">Failed to load folder</td></tr>';
            return;
        }
        filesRender(data);
    });
    filesApi('GET', 'op=df', function(ok, data) {
        var el = document.getElementById('files_df');
        if (!el) return;
        el.textContent = (ok && data && data.total) ? ('SD: ' + filesFmtSize(data.free) + ' free of ' + filesFmtSize(data.total)) : '';
    });
}

// Update the sortable header labels (Name/Size/Modified/Type) with the active arrow.
function filesUpdateSortHeaders() {
    var keys = ['name', 'size', 'mtime', 'type'];
    var labels = { name: 'Name', size: 'Size', mtime: 'Modified', type: 'Type' };
    keys.forEach(function(k) {
        var th = document.getElementById('files_th_' + k);
        if (!th) return;
        th.textContent = labels[k] + (filesSortKey === k ? (filesSortDir === 'asc' ? ' ▲' : ' ▼') : '');
    });
}

// Header click handler: toggle direction on the active column, else switch column.
function filesSortBy(key) {
    if (filesSortKey === key) {
        filesSortDir = (filesSortDir === 'asc') ? 'desc' : 'asc';
    } else {
        filesSortKey = key;
        filesSortDir = (key === 'mtime') ? 'desc' : 'asc';
    }
    // Preserve the current checkbox selection across the sort re-render.
    if (filesLastData) filesRender(filesLastData, filesSelectedPaths());
}

var filesLastData = null;

function filesRender(data, keepSel) {
    filesLastData = data;
    // The DOM checkboxes are the sole source of truth for selection. keepSel is an
    // optional array of rel-paths whose checkbox should stay ticked across a re-render
    // (used by sort, which rebuilds the rows); a fresh folder load passes nothing.
    var keep = {};
    if (keepSel) { for (var ki = 0; ki < keepSel.length; ki++) { keep[keepSel[ki]] = true; } }
    document.getElementById('files_path').textContent = '/sdcard' + (data.path ? '/' + data.path : '');
    var body = document.getElementById('files_body');
    body.innerHTML = '';
    var selAll = document.getElementById('files_selall');
    if (selAll) selAll.checked = false;
    filesUpdateSortHeaders();
    if (data.sd_mounted === false) {
        body.innerHTML = '<tr><td colspan=6 style="text-align:center;color:#b91c1c;padding:8px">SD card not mounted</td></tr>';
        return;
    }
    var cell = 'border:1px solid #e2e8f0;padding:6px';
    if (filesCwd) {
        var up = document.createElement('tr');
        var uc = document.createElement('td'); uc.colSpan = 6; uc.style.cssText = cell;
        var ua = document.createElement('a'); ua.href = '#'; ua.textContent = '.. (up one level)';
        ua.onclick = function(e) { e.preventDefault(); filesLoad(filesParent(filesCwd)); };
        uc.appendChild(ua); up.appendChild(uc); body.appendChild(up);
    }
    var entries = (data.entries || []).slice();
    var dir = (filesSortDir === 'asc') ? 1 : -1;
    function cmp(a, b) {
        var r = 0;
        if (filesSortKey === 'size') {
            r = (a.size || 0) - (b.size || 0);
        } else if (filesSortKey === 'mtime') {
            r = (a.mtime || 0) - (b.mtime || 0);
        } else if (filesSortKey === 'type') {
            r = (a.type || '').localeCompare(b.type || '');
        } else {
            r = a.name.localeCompare(b.name);
        }
        if (r === 0) r = a.name.localeCompare(b.name);
        return r * dir;
    }
    entries.sort(function(a, b) {
        // Keep directories grouped first; sort chosen key within each group.
        if ((a.type === 'dir') !== (b.type === 'dir')) return a.type === 'dir' ? -1 : 1;
        return cmp(a, b);
    });
    entries.forEach(function(en) {
        var isDir = en.type === 'dir';
        var rel = filesJoin(filesCwd, en.name);
        var tr = document.createElement('tr');

        var selTd = document.createElement('td'); selTd.style.cssText = cell;
        if (!isDir) {
            var cb = document.createElement('input'); cb.type = 'checkbox';
            cb.className = 'files_sel_cb'; cb.value = rel; cb.checked = !!keep[rel];
            selTd.appendChild(cb);
        }
        tr.appendChild(selTd);

        var nameTd = document.createElement('td'); nameTd.style.cssText = cell;
        if (isDir) {
            var a = document.createElement('a'); a.href = '#'; a.textContent = en.name + '/';
            a.onclick = function(e) { e.preventDefault(); filesLoad(rel); };
            nameTd.appendChild(a);
        } else {
            nameTd.textContent = en.name + (en.active ? '  (active log)' : '');
        }
        tr.appendChild(nameTd);

        var sizeTd = document.createElement('td'); sizeTd.style.cssText = cell;
        sizeTd.textContent = isDir ? '' : filesFmtSize(en.size); tr.appendChild(sizeTd);

        var dateTd = document.createElement('td'); dateTd.style.cssText = cell;
        dateTd.textContent = filesFmtDate(en.mtime); tr.appendChild(dateTd);

        var typeTd = document.createElement('td'); typeTd.style.cssText = cell;
        typeTd.textContent = isDir ? 'folder' : 'file'; tr.appendChild(typeTd);

        var actTd = document.createElement('td'); actTd.style.cssText = cell;
        if (!isDir) {
            var dl = document.createElement('button'); dl.textContent = 'Download'; dl.style.marginRight = '4px';
            dl.onclick = function() { filesDownload(rel, en.name); };
            actTd.appendChild(dl);
        }
        if (!en.locked) {
            var rn = document.createElement('button'); rn.textContent = 'Rename'; rn.style.marginRight = '4px';
            rn.onclick = function() { filesRename(rel, en.name); };
            actTd.appendChild(rn);
            var del = document.createElement('button'); del.textContent = 'Delete';
            del.onclick = function() { filesDelete(rel, en.name, isDir); };
            actTd.appendChild(del);
        }
        tr.appendChild(actTd);

        body.appendChild(tr);
    });
    if (!entries.length) {
        var er = document.createElement('tr'); var ec = document.createElement('td');
        ec.colSpan = 6; ec.style.cssText = 'text-align:center;color:#555;padding:8px'; ec.textContent = '(empty)';
        er.appendChild(ec); body.appendChild(er);
    }
}

// Header "select all" checkbox: toggle every currently-listed file checkbox.
function filesToggleAll(cb) {
    var boxes = document.getElementsByClassName('files_sel_cb');
    for (var i = 0; i < boxes.length; i++) {
        boxes[i].checked = cb.checked;
    }
}

// Collect the relative paths of all currently-checked file rows.
function filesSelectedPaths() {
    var out = [];
    var boxes = document.getElementsByClassName('files_sel_cb');
    for (var i = 0; i < boxes.length; i++) {
        if (boxes[i].checked) out.push(boxes[i].value);
    }
    return out;
}

function filesDownloadSelected() {
    var paths = filesSelectedPaths();
    if (!paths.length) { alert('No files selected.'); return; }
    // No zip endpoint: trigger each single-file download with a small stagger.
    var i = 0;
    function next() {
        if (i >= paths.length) return;
        var rel = paths[i++];
        var name = rel.indexOf('/') >= 0 ? rel.substring(rel.lastIndexOf('/') + 1) : rel;
        filesDownload(rel, name);
        setTimeout(next, 400);
    }
    next();
}

function filesDeleteSelected() {
    var paths = filesSelectedPaths();
    if (!paths.length) { alert('No files selected.'); return; }
    if (!confirm('Delete ' + paths.length + ' selected file(s)?')) return;
    var remaining = paths.length;
    var failures = [];
    paths.forEach(function(rel) {
        filesApi('POST', JSON.stringify({ op: 'delete', path: rel }), function(ok, data, st) {
            if (!ok) failures.push(rel + ': ' + ((data && data.error) || st));
            if (--remaining === 0) {
                if (failures.length) alert('Some deletes failed:\n' + failures.join('\n'));
                filesLoad(filesCwd);
            }
        });
    });
}

function filesDownload(rel, name) {
    var a = document.createElement('a');
    a.href = '/files?op=download&path=' + encodeURIComponent(rel);
    a.download = name;
    document.body.appendChild(a); a.click(); document.body.removeChild(a);
}

function filesMkdir() {
    var name = prompt('New folder name:');
    if (!name) return;
    filesApi('POST', JSON.stringify({ op: 'mkdir', path: filesCwd, name: name }), function(ok, data, st) {
        if (!ok) alert('Create failed: ' + ((data && data.error) || st));
        filesLoad(filesCwd);
    });
}

function filesRename(rel, oldName) {
    var name = prompt('Rename "' + oldName + '" to:', oldName);
    if (!name || name === oldName) return;
    filesApi('POST', JSON.stringify({ op: 'rename', path: rel, name: name }), function(ok, data, st) {
        if (!ok) alert('Rename failed: ' + ((data && data.error) || st));
        filesLoad(filesCwd);
    });
}

function filesDelete(rel, name, isDir) {
    if (!confirm('Delete ' + (isDir ? 'folder (and ALL its contents)' : 'file') + ' "' + name + '"?')) return;
    filesApi('POST', JSON.stringify({ op: 'delete', path: rel }), function(ok, data, st) {
        if (!ok) alert('Delete failed: ' + ((data && data.error) || st));
        filesLoad(filesCwd);
    });
}

function openTab(evt, tabName) {
    var i, tabcontent, tablinks;
    tabcontent = document.getElementsByClassName("tabcontent");
    for(i = 0; i < tabcontent.length; i++) {
        tabcontent[i].style.display = "none";
    }
    tablinks = document.getElementsByClassName("tablinks");
    for(i = 0; i < tablinks.length; i++) {
        tablinks[i].className = tablinks[i].className.replace(" active", "");
    }
    // Stop the CSV status poll on every tab switch (restarted below only for the logger tab).
    if (typeof csv_status_poll_stop === 'function') csv_status_poll_stop();
    document.getElementById(tabName).style.display = "block";
    evt.currentTarget.className += " active";

    if (tabName === 'automate') {
        try { ensureAutomateSubTabInitialized(); } catch(_) {}
    }
    
    if (tabName === 'files_tab') {
        filesCwd = '';
        filesLoad('');
    } else if (tabName === 'logger') {
        csv_status_poll_start();
    } else if (tabName === 'console_tab') {
        csv_status_poll_start();
        consoleRefresh();
    }
}

function openAutomateSubTab(evt, tabName) {
    var i, tabcontent, tablinks;
    tabcontent = document.getElementsByClassName("automate-subtabcontent");
    for (i = 0; i < tabcontent.length; i++) {
        tabcontent[i].style.display = "none";
    }
    tablinks = document.getElementsByClassName("automate-subtablinks");
    for (i = 0; i < tablinks.length; i++) {
        tablinks[i].className = tablinks[i].className.replace(" active", "");
    }
    var panel = document.getElementById(tabName);
    if (panel) panel.style.display = "block";
    if (evt && evt.currentTarget) evt.currentTarget.className += " active";
}

function ensureAutomateSubTabInitialized() {
    var defaultButton = document.getElementById('automateSubDefaultOpen');
    if (!defaultButton) return;
    if (defaultButton.className.indexOf('active') !== -1) return;
    defaultButton.click();
}
function sta_enable() {}

// Helper function to get DOM elements efficiently
function getElements() {
    return {
        wifiMode: document.getElementById("wifi_mode"),
        wifiScanButton: document.getElementById("wifi_scan_button"),
        ssidValue: document.getElementById("ssid_value"),
        passValue: document.getElementById("pass_value"),
        staSecurity: document.getElementById("sta_security"),
        bleStatus: document.getElementById("ble_status"),
        apAutoDisable: document.getElementById("ap_auto_disable"),
        bleWarningDiv: document.getElementById("ble_warning_div"),
        battAlert: document.getElementById("batt_alert"),
        battAlertDiv: document.getElementById("batt_alert_div"),
        submitButton: document.getElementById("submit_button"),
        apPassValue: document.getElementById("ap_pass_value"),
        tcpPortValue: document.getElementById("tcp_port_value"),
        battAlertPort: document.getElementById("batt_alert_port"),
        blePassValue: document.getElementById("ble_pass_value"),
        sleepVolt: document.getElementById("sleep_volt"),
        sleepStatus: document.getElementById("sleep_status"),
        sleepDisableAgree: document.getElementById("sleep_disable_agree"),
        protocol: document.getElementById("protocol"),
        portType: document.getElementById("port_type"),
        periodicWakeup: document.getElementById("periodic_wakeup"),
        wakeupEveryRow: document.getElementById("wakeup_every_row"),
        sta_ble_info: document.getElementById("sta_ble_info")
    };
}

// Helper function to validate field length
function validateLength(value, min, max, fieldName) {
    const length = value.length;
    return length >= min && length <= max;
}

// Helper function to validate port number
function validatePort(value) {
    const port = parseInt(value);
    return port >= 1 && port <= 65535;
}

// Helper function to disable submit button with error message
function disableSubmitWithError(message, duration = 5000) {
    showNotification(message, "red", duration);
    return false;
}

function submit_enable() {
    console.log("submit_enable");
    const elements = getElements();
    const wifiMode = elements.wifiMode.value;
    
    // Configure WiFi mode-specific settings
    configureWifiModeSettings(elements, wifiMode);
    
    // Handle BLE status and warnings
    handleBleStatus(elements);
    
    // Validate form and enable/disable submit button
    const isValid = validateForm(elements, wifiMode);
    elements.submitButton.disabled = !isValid;
    
    // Configure protocol-specific settings
    configureProtocolSettings(elements);
    
    // Configure sleep and battery alert settings
    configureSleepSettings(elements);
    
    // Configure MQTT and battery alert visibility
    configureMqttAndBatteryAlerts(elements);
    
    // Configure periodic wakeup settings
    configurePeriodicWakeup(elements);
}

function configureWifiModeSettings(elements, wifiMode) {
    const isAP = wifiMode === "AP";
    const isAPStation = wifiMode === "APStation";
    const isSmartConnect = wifiMode === "SmartConnect";
    const isBLEStation = wifiMode === "BLEStation";
    const isStation = wifiMode === "Station";
    const usesAP = isAP || isAPStation;
    const apChValue = document.getElementById("ap_ch_value");
    
    // Set station fields
    elements.ssidValue.disabled = isAP;
    elements.passValue.disabled = isAP;
    elements.staSecurity.disabled = isAP;
    elements.wifiScanButton.disabled = isAP;

    // Set AP fields (Station-only/BLE+Station do not run AP)
    if (apChValue) apChValue.disabled = !usesAP;
    if (elements.apPassValue) elements.apPassValue.disabled = !usesAP;

    // Auto-disable AP only applies to AP+Station
    elements.apAutoDisable.disabled = !isAPStation;
    
    // Set BLE settings based on mode
    if (isAP) {
        elements.bleStatus.disabled = false;
        elements.blePassValue.disabled = false;
        elements.sta_ble_info.style.display = "none";
    } else if (isBLEStation) {
        elements.bleStatus.disabled = true;
        elements.bleStatus.value = "enable";
        elements.bleStatus.selectedIndex = 0;
        elements.blePassValue.disabled = false;
        elements.sta_ble_info.style.display = "block";
    } else if (isStation) {
        elements.sta_ble_info.style.display = "block";
    }
    else if (isSmartConnect) {
        elements.bleStatus.disabled = true;
        elements.blePassValue.disabled = true;
    } else {
        elements.bleStatus.disabled = true;
        elements.bleStatus.value = "disable";
        elements.bleStatus.selectedIndex = 1;
        elements.blePassValue.disabled = true;
        elements.sta_ble_info.style.display = "none";
    }
}

function handleBleStatus(elements) {
    const isBleEnabled = elements.bleStatus.value === "enable";
    const isBLEStation = elements.wifiMode.value === "BLEStation";

    elements.bleWarningDiv.style.display = (isBleEnabled && !isBLEStation) ? "block" : "none";
    // Enable BLE passkey input only when BLE is enabled
    elements.blePassValue.disabled = !isBleEnabled;
    
    if (isBleEnabled && !window.bleAlertShown) {
        elements.battAlert.value = "disable";
        elements.battAlertDiv.style.display = "none";
        elements.battAlert.disabled = true;
        window.bleAlertShown = true;
    } else if (!isBleEnabled) {
        elements.battAlert.disabled = true;
    }
}

function validateForm(elements, wifiMode) {
    const usesAP = wifiMode === "AP" || wifiMode === "APStation";
    const usesStation = wifiMode !== "AP";

    // Password validation
    if (usesAP) {
        const apPassLen = elements.apPassValue.value.length;
        if (apPassLen < 8 || apPassLen > 63) {
            return disableSubmitWithError("AP password length, min=8 max=63", 5000);
        }
        if (elements.apPassValue.value === "@meatpi#") {
            return disableSubmitWithError("AP password MUST be changed from default", 50000);
        }
    }

    if (usesStation) {
        const passLen = elements.passValue.value.length;
        if (passLen < 8 || passLen > 63) {
            return disableSubmitWithError("Station password length, min=8 max=63", 5000);
        }

        // SSID validation
        if (!validateLength(elements.ssidValue.value, 1, 32)) {
            return disableSubmitWithError("Station SSID length, min=1 max=32", 5000);
        }
    }

    
    // Port validation
    if (!validatePort(elements.tcpPortValue.value)) {
        return disableSubmitWithError("TCP Port value, min=1 max=65535", 5000);
    }
    if (!validatePort(elements.battAlertPort.value)) {
        return disableSubmitWithError("Battery Alert Port value, min=1 max=65535", 5000);
    }
    
    // BLE passkey validation - only validate if BLE is enabled
    const isBleEnabled = elements.bleStatus.value === "enable";
    if (isBleEnabled) {
        const blePass = elements.blePassValue.value;
        if (blePass.length !== 6 || blePass.charAt(0) === "0") {
            return disableSubmitWithError("BLE Passkey: 6 digits required, first digit cannot be 0", 5000);
        }

        if (blePass === "123456") {
            return disableSubmitWithError("BLE Passkey MUST be changed from default", 50000);
        }
    }
    
    // Sleep voltage validation
    const sleepVolt = parseFloat(elements.sleepVolt.value);
    if (sleepVolt < 12 || sleepVolt > 15) {
        return disableSubmitWithError("Sleep Voltage Value, min=12.0 max=15.0", 5000);
    }
    
    // Sleep disable agreement validation
    if (elements.sleepStatus.value === "disable" && elements.sleepDisableAgree.value === "no") {
        return disableSubmitWithError("You must agree to disable sleep mode", 5000);
    }
    
    // SmartConnect validation
    if (wifiMode === "SmartConnect") {
        return validateSmartConnect();
    }
    
    return true;
}

function validateSmartConnect() {
    const homeSSID = document.getElementById("home_ssid").value.trim();
    const homePassword = document.getElementById("home_password").value.trim();
    const driveConnectionType = document.getElementById("drive_connection_type").value;
    const driveSSID = document.getElementById("drive_ssid").value.trim();
    const drivePassword = document.getElementById("drive_password").value.trim();
    
    if (!homeSSID || !homePassword) {
        return disableSubmitWithError("SmartConnect: Home SSID and Password are required", 5000);
    }
    
    if (driveConnectionType === "wifi" && (!driveSSID || !drivePassword)) {
        return disableSubmitWithError("SmartConnect: Drive SSID and Password are required when WiFi is selected", 5000);
    }
    
    return true;
}

function configureProtocolSettings(elements) {
    elements.tcpPortValue.disabled = false;
    elements.portType.selectedIndex = 0;
    elements.portType.disabled = false;
}

function configureSleepSettings(elements) {
    const sleepEnabled = elements.sleepStatus.value === "enable";
    const bleEnabled = elements.bleStatus.value === "enable";
    
    if (sleepEnabled) {
        if (!bleEnabled) {
            elements.battAlert.disabled = true;
        }
    } else {
        elements.battAlert.disabled = true;
        elements.battAlert.selectedIndex = 0;
    }
}

function configureMqttAndBatteryAlerts(elements) {
    // Battery alert div is always hidden in current logic
    elements.battAlertDiv.style.display = "none";
}

function configurePeriodicWakeup(elements) {
    const sleepDisabled = elements.sleepStatus.value === "disable";
    
    if (sleepDisabled) {
        elements.periodicWakeup.disabled = true;
        elements.periodicWakeup.value = "disable";
        elements.wakeupEveryRow.style.display = "none";
    } else {
        elements.periodicWakeup.disabled = false;
        const wakeupEnabled = elements.periodicWakeup.value === "enable";
        elements.wakeupEveryRow.style.display = wakeupEnabled ? "table-row" : "none";
    }
}
document.getElementById("defaultOpen").click();
function checkStatus() {
    const xhttp = new XMLHttpRequest();
    xhttp.onload = function() {
        var obj = JSON.parse(this.responseText);
        if(obj.wifi_mode == "APStation") {
            document.getElementById("wifi_mode_current").innerHTML = "AP+Station";
        } else if(obj.wifi_mode == "BLEStation") {
            document.getElementById("wifi_mode_current").innerHTML = "BLE+Station";
        } else if(obj.wifi_mode == "Station") {
            document.getElementById("wifi_mode_current").innerHTML = "Station";
        } else if(obj.wifi_mode == "AP") {
            document.getElementById("wifi_mode_current").innerHTML = "AP";
        } else if(obj.wifi_mode == "SmartConnect") {
            document.getElementById("wifi_mode_current").innerHTML = "SmartConnect";
        } else {
            document.getElementById("wifi_mode_current").innerHTML = obj.wifi_mode || "N/A";
        }

        document.getElementById("sta_status").innerHTML = obj.sta_status;
        document.getElementById("ap_channel_status").innerHTML = obj.ap_ch;
        document.getElementById("sta_ip").innerHTML = obj.sta_ip;

        const dnsMainEl = document.getElementById("dns_main_status");
        if (dnsMainEl) dnsMainEl.innerHTML = (obj.dns_main || "N/A");
        const dnsBackupEl = document.getElementById("dns_backup_status");
        if (dnsBackupEl) dnsBackupEl.innerHTML = (obj.dns_backup || "N/A");
        const timeSyncedEl = document.getElementById("time_synced_status");
        if (timeSyncedEl) timeSyncedEl.innerHTML = (obj.time_synced ? "Yes" : "No");

        document.getElementById("mdns").innerHTML = obj.mdns;
        document.getElementById("can_bitrate_status").innerHTML = obj.can_datarate;
        if(obj.can_mode == "normal") {
            document.getElementById("can_mode_status").innerHTML = "Normal";
        } else if(obj.can_mode == "silent") {
            document.getElementById("can_mode_status").innerHTML = "Silent";
        }
        if(obj.port_type == "tcp") {
            document.getElementById("port_type_status").innerHTML = "TCP";
        } else if(obj.port_type == "udp") {
            document.getElementById("port_type_status").innerHTML = "UDP";
        }
        document.getElementById("port_status").innerHTML = obj.port;
        document.getElementById("fw_version").innerHTML = obj.fw_version;
        document.getElementById("hw_version").innerHTML = obj.hw_version;
        document.getElementById("git_version").innerHTML = obj.git_version;
        document.getElementById("protocol").value = obj.protocol;
        if(obj.protocol != "auto_pid") {
            document.getElementById("autopid_warning_div").style.display = "block";
        }else {
            document.getElementById("autopid_warning_div").style.display = "none";
        }
        if(obj.subnet_overlap == "yes" && obj.ap_auto_disable != "enable") {
            document.getElementById("apconfig_warning_div").style.display = "block";
        } else {
            document.getElementById("apconfig_warning_div").style.display = "none";
        }
        document.getElementById("batt_voltage").innerHTML = obj.batt_voltage;
        if(document.getElementById("batt_alert").value == "enable") {
            document.getElementById("batt_alert_div").style.display = "none";
        } else if(document.getElementById("batt_alert").value == "disable") {
            document.getElementById("batt_alert_div").style.display = "none";
        }
        document.getElementById("obd_chip_status").innerHTML = obj.obd_chip_status || "N/A";
        document.getElementById("uptime").innerHTML = obj.uptime || "N/A";
        const restartLastResetEl = document.getElementById("restart_last_reset_reason");
        if (restartLastResetEl) restartLastResetEl.innerHTML = formatRestartTrackerValue(obj.restart_last_reset_reason);
        const restartLastPlannedEl = document.getElementById("restart_last_planned_reason");
        if (restartLastPlannedEl) restartLastPlannedEl.innerHTML = formatRestartTrackerValue(obj.restart_last_planned_reason);
        const restartLastSourceEl = document.getElementById("restart_last_source");
        if (restartLastSourceEl) restartLastSourceEl.innerHTML = formatRestartTrackerValue(obj.restart_last_source);
        const restartLastBootEl = document.getElementById("restart_last_boot_time");
        if (restartLastBootEl) restartLastBootEl.innerHTML = formatRestartTrackerLocalTime(obj.restart_last_boot_timestamp_unix);
        const restartBootCountEl = document.getElementById("restart_boot_count");
        if (restartBootCountEl) restartBootCountEl.innerHTML = (obj.restart_boot_count ?? 0);
        const restartUnexpectedCountEl = document.getElementById("restart_unexpected_reset_count");
        if (restartUnexpectedCountEl) restartUnexpectedCountEl.innerHTML = (obj.restart_unexpected_reset_count ?? 0);
        checkFirmwareUpdate();
    };
    xhttp.open("GET", "/check_status");
    xhttp.send();
}


function loadautoPID() {
    console.log("Loading auto PID data..."); 
    const xhttp = new XMLHttpRequest();
    xhttp.onload = function() {
        console.log("Server response:", this.responseText);
        if(this.responseText !== "NONE") {
            const data = JSON.parse(this.responseText);
            loadAutoTable(data);
            document.getElementById("custom_pid_store").disabled = true;
        } else {
            console.log("No PID data found on server");
            togglePidPollingMinVoltageRow();
        }
    };
    xhttp.onerror = function(error) {
        console.error("Error loading auto PID:", error);
    };
    xhttp.open("GET", "/load_auto_pid");
    xhttp.send();
}

// Logger Settings (Task #5, trimmed in #5 datalogger-trim): one master "Logging"
// toggle drives the single real config key csv_log (a hidden select in the DOM).
function applyLoggerXor() {
    var masterEl = document.getElementById("logging_master");
    var cs = document.getElementById("csv_log");
    if (!masterEl || !cs) { return; }
    var csvShow = (masterEl.value === "enable");
    cs.value = csvShow ? "enable" : "disable";
    // CSV runtime Start/Stop button + live status line: shown only when logging is on.
    var csvRow = document.getElementById("csv_runtime_row");
    var csvStatRow = document.getElementById("csv_status_row");
    if (csvRow) csvRow.style.display = csvShow ? "" : "none";
    if (csvStatRow) csvStatRow.style.display = csvShow ? "" : "none";
    // Wide CSV (Task #11) controls, progressive: Grid Mode shown when CSV is active; Grid Rate
    // only when Fixed-rate. (Format toggle removed in Task #16 -- output is always Wide.)
    var gmEl = document.getElementById("csv_grid_mode");
    var gmRow = document.getElementById("csv_grid_mode_row");
    var hzRow = document.getElementById("csv_grid_hz_row");
    if (gmRow) gmRow.style.display = csvShow ? "" : "none";
    if (hzRow) hzRow.style.display = (csvShow && gmEl && gmEl.value === "fixed") ? "" : "none";
    var reRow = document.getElementById("csv_require_engine_row");
    if (reRow) reRow.style.display = csvShow ? "" : "none";
}

async function postConfig() {
    var obj = {};
    document.getElementById("submit_button").disabled = true;
    await new Promise(resolve => setTimeout(resolve, 1000));
    const storeResult = await storeAutoTableData();
    if (!storeResult) {
        document.getElementById("submit_button").disabled = false;
        return;
    }
    
    await new Promise(resolve => setTimeout(resolve, 1000));

    obj["wifi_mode"] = document.getElementById("wifi_mode").value;
    obj["ap_ch"] = document.getElementById("ap_ch_value").value;
    obj["ap_auto_disable"] = document.getElementById("ap_auto_disable").value;

    // Optional custom AP SSID
    {
        const enEl = document.getElementById("ap_ssid_enable");
        const valEl = document.getElementById("ap_ssid_value");
        const enabled = !!(enEl && enEl.checked);
        const ssid = (valEl && typeof valEl.value === 'string') ? valEl.value.trim() : "";

        if (enabled) {
            // Enforce min length client-side (firmware enforces too)
            if (ssid.length < 3 || ssid.length > 32) {
                showNotification("AP SSID must be 3–32 characters", "red");
                document.getElementById("submit_button").disabled = false;
                return;
            }
        }

        obj["ap_ssid_en"] = enabled ? "enable" : "disable";
        obj["ap_ssid"] = ssid;
    }
    obj["sta_ssid"] = document.getElementById("ssid_value").value;
    obj["sta_pass"] = document.getElementById("pass_value").value;
    obj["sta_security"] = document.getElementById("sta_security").value;
    obj["home_ssid"] = document.getElementById("home_ssid").value;
    obj["home_password"] = document.getElementById("home_password").value;
    obj["home_security"] = document.getElementById("home_security").value;
    obj["drive_ssid"] = document.getElementById("drive_ssid").value;
    obj["drive_password"] = document.getElementById("drive_password").value;
    obj["drive_security"] = document.getElementById("drive_security").value;
    obj["home_protocol"] = document.getElementById("home_protocol").value;
    obj["drive_protocol"] = document.getElementById("drive_protocol").value;
    obj["drive_connection_type"] = document.getElementById("drive_connection_type").value;
    obj["drive_mode_timeout"] = document.getElementById("drive_mode_timeout").value;
    obj["can_datarate"] = document.getElementById("can_datarate").value;
    obj["can_mode"] = document.getElementById("can_mode").value;
    obj["port_type"] = document.getElementById("port_type").value;
    obj["port"] = document.getElementById("tcp_port_value").value;
    obj["ap_pass"] = document.getElementById("ap_pass_value").value;
    obj["protocol"] = document.getElementById("protocol").value;
    obj["ble_pass"] = document.getElementById("ble_pass_value").value;
    obj["ble_status"] = document.getElementById("ble_status").value;
    obj["ble_power"] = document.getElementById("ble_power").value; // BLE TX power (dBm)
    obj["sleep_status"] = document.getElementById("sleep_status").value;
    obj["sleep_disable_agree"] = document.getElementById("sleep_disable_agree").value;
    obj["periodic_wakeup"] = document.getElementById("periodic_wakeup").value;
    obj["sleep_volt"] = document.getElementById("sleep_volt").value;
    obj["engine_volt"] = document.getElementById("engine_volt").value;
    obj["sleep_time"] = document.getElementById("sleep_time").value;
    obj["wakeup_interval"] = document.getElementById("wakeup_interval").value;
    obj["batt_alert"] = document.getElementById("batt_alert").value;
    obj["batt_alert_ssid"] = document.getElementById("batt_alert_ssid").value;
    obj["batt_alert_pass"] = document.getElementById("batt_alert_pass").value;
    obj["batt_alert_volt"] = document.getElementById("batt_alert_volt").value;
    obj["batt_alert_protocol"] = document.getElementById("batt_alert_protocol").value;
    let mqtt_txt = "mqtt://";
    let mqtt_url_val = mqtt_txt.concat(document.getElementById("batt_alert_url").value);
    obj["batt_alert_url"] = mqtt_url_val;
    obj["batt_alert_port"] = document.getElementById("batt_alert_port").value;
    obj["batt_alert_topic"] = document.getElementById("batt_alert_topic").value;
    obj["batt_alert_time"] = document.getElementById("batt_alert_time").value;
    obj["batt_mqtt_user"] = document.getElementById("batt_mqtt_user").value;
    obj["batt_mqtt_pass"] = document.getElementById("batt_mqtt_pass").value;
    applyLoggerXor();   // compose the real csv_log key from the master widget
    obj["csv_log"] = document.getElementById("csv_log").value;
    obj["log_filesystem"] = document.getElementById("log_filesystem").value;
    obj["log_storage"] = document.getElementById("log_storage").value;
    obj["csv_grid_mode"] = document.getElementById("csv_grid_mode").value;
    obj["csv_grid_hz"] = document.getElementById("csv_grid_hz").value;
    obj["csv_require_engine"] = document.getElementById("csv_require_engine").value;
    obj["log_period"] = loadedLogPeriod;   // preserve persisted datalog period (no UI element after trim)
    obj["imu_threshold"] = document.getElementById("imu_threshold").value;

    // Collect fallback networks (max 5)
    try {
        const rows = document.querySelectorAll('#fallback_rows .fallback-row');
        const fallbacks = [];
        rows.forEach(r => {
            const ssid = r.querySelector('.fb-ssid').value.trim();
            const pass = r.querySelector('.fb-pass').value;
            const sec = r.querySelector('.fb-sec').value;
            if (ssid) {
                fallbacks.push({ ssid, pass, security: sec });
            }
        });
        obj["sta_fallbacks"] = fallbacks.slice(0, 5);
    } catch (e) {
        console.warn('fallback networks parse error', e);
        document.getElementById("submit_button").disabled = false;
    }

    var configJSON = JSON.stringify(obj, null, 0);
    
    // Send main configuration first
    const xhttp = new XMLHttpRequest();


    xhttp.open("POST", "/store_config");
    xhttp.setRequestHeader("Content-Type", "application/json");
    xhttp.onreadystatechange = function() {
        if (xhttp.readyState === 4 && xhttp.status >= 200 && xhttp.status < 300) {
            // POST was successful, reload after 8 seconds
            setTimeout(function() {
                window.location.reload();
            }, 8000);
        }
    };
    xhttp.send(configJSON);
}

function toggleApSsid() {
    const enEl = document.getElementById("ap_ssid_enable");
    const valEl = document.getElementById("ap_ssid_value");
    if (!enEl || !valEl) return;
    valEl.disabled = !enEl.checked;
    if (!enEl.checked) {
        // Keep value, but clear any browser validation UI
        try { valEl.setCustomValidity(""); } catch(_) {}
    } else {
        validateApSsid();
    }
}

function validateApSsid() {
    const enEl = document.getElementById("ap_ssid_enable");
    const valEl = document.getElementById("ap_ssid_value");
    if (!enEl || !valEl) return;
    if (!enEl.checked) {
        try { valEl.setCustomValidity(""); } catch(_) {}
        return;
    }
    const ssid = (valEl.value || "").trim();
    if (ssid.length < 3 || ssid.length > 32) {
        try { valEl.setCustomValidity("AP SSID must be 3–32 characters"); } catch(_) {}
    } else {
        try { valEl.setCustomValidity(""); } catch(_) {}
    }
}

function otaClick() {
    const fileInput = document.getElementById("ota_file");
    const submitButton = document.getElementById("ota_submit_button");
    const otaForm = document.getElementById("ota_form");

    const progressRow = document.getElementById("ota_progress_row");
    const progressFill = document.getElementById("ota_progress_fill");
    const progressText = document.getElementById("ota_progress_text");

    const setProgressVisible = (visible) => {
        if (progressRow) {
            progressRow.style.display = visible ? "" : "none";
        }
    };

    const setProgress = (percent, text) => {
        if (progressFill) {
            const clamped = Math.max(0, Math.min(100, Number(percent) || 0));
            progressFill.style.width = clamped + "%";
        }
        if (progressText) {
            progressText.textContent = text;
        }
    };

    const setProgressState = (state) => {
        if (!progressFill) return;
        progressFill.classList.remove("is-success", "is-error");
        if (state === "success") progressFill.classList.add("is-success");
        if (state === "error") progressFill.classList.add("is-error");
    };

    if (!fileInput || fileInput.files.length === 0) {
        showNotification("No files selected!", "red");
        alert("No files selected!");
        return;
    }

    if (!otaForm) {
        showNotification("OTA form not found", "red");
        if (submitButton) submitButton.disabled = false;
        return;
    }

    if (submitButton) submitButton.disabled = true;
    setProgressVisible(true);
    setProgressState("normal");
    setProgress(0, "Starting upload...");
    showNotification("Uploading firmware...", "green");

    const formData = new FormData(otaForm);
    const xhr = new XMLHttpRequest();
    const uploadUrl = otaForm.getAttribute("action") || "/upload/ota.bin";
    xhr.open("POST", uploadUrl);

    // Large uploads + slow links can take time; timeout mainly protects against a dead connection.
    xhr.timeout = 10 * 60 * 1000;

    let uploadCompleted = false;
    let totalBytes = 0;
    let loadedBytes = 0;
    let lastProgressAt = Date.now();
    let stallTimer = null;

    const cleanupTimers = () => {
        if (stallTimer) {
            clearInterval(stallTimer);
            stallTimer = null;
        }
    };

    const failAndUnlock = (message) => {
        cleanupTimers();
        setProgressState("error");
        showNotification(message, "red");
        // Keep whatever progress we have (helps indicate where it died).
        const percent = totalBytes > 0 ? Math.round((loadedBytes / totalBytes) * 100) : 0;
        setProgress(percent, message);
        if (submitButton) submitButton.disabled = false;
    };

    const startPostUploadWait = () => {
        cleanupTimers();
        setProgressState("success");
        setProgress(100, "Upload complete. Waiting 15 seconds...");
        showNotification("Upload complete. Waiting 15 seconds...", "green");

        let remaining = 15;
        const timer = setInterval(function () {
            remaining -= 1;
            if (remaining <= 0) {
                clearInterval(timer);
                setProgress(100, "Reconnecting...");
                // Device usually reboots after OTA; reload after delay.
                window.location.reload();
                return;
            }
            setProgress(100, `Upload complete. Waiting ${remaining} seconds...`);
        }, 1000);
    };

    xhr.upload.onprogress = function (event) {
        if (!event.lengthComputable) {
            setProgress(0, "Uploading...");
            return;
        }
        const percent = Math.round((event.loaded / event.total) * 100);
        totalBytes = event.total;
        loadedBytes = event.loaded;
        lastProgressAt = Date.now();
        if (percent >= 100 || (totalBytes > 0 && loadedBytes >= totalBytes)) uploadCompleted = true;
        setProgress(percent, `Upload: ${percent}%`);
    };

    xhr.upload.onload = function () {
        // Upload data fully handed off to the network stack/server.
        uploadCompleted = true;
        setProgress(100, "Upload sent. Finalizing...");
    };

    // If Wi-Fi drops mid-upload, browsers can sometimes hang without calling onerror immediately.
    // Light stall detection: if progress doesn't change for 15s during an active upload, warn/fail.
    stallTimer = setInterval(function () {
        if (uploadCompleted) return;
        if (totalBytes > 0 && loadedBytes > 0 && loadedBytes < totalBytes) {
            const stalledForMs = Date.now() - lastProgressAt;
            if (stalledForMs > 15000) {
                const offlineHint = (typeof navigator !== 'undefined' && navigator.onLine === false)
                    ? " (browser is offline)"
                    : "";
                try { xhr.abort(); } catch (e) { /* ignore */ }
                failAndUnlock("Update failed: connection lost during upload" + offlineHint + ". Reconnect and try again.");
            }
        }
    }, 1000);

    xhr.onerror = function () {
        // If the device reboots right after receiving the image, the browser may see a disconnect.
        if (uploadCompleted) {
            startPostUploadWait();
            return;
        }
        const offlineHint = (typeof navigator !== 'undefined' && navigator.onLine === false)
            ? " (browser is offline)"
            : "";
        failAndUnlock("Update failed: network error" + offlineHint + ". Reconnect and try again.");
    };

    xhr.onabort = function () {
        if (uploadCompleted) {
            startPostUploadWait();
            return;
        }
        failAndUnlock("Update aborted. If Wi-Fi dropped or the device was unplugged, reconnect and try again.");
    };

    xhr.ontimeout = function () {
        if (uploadCompleted) {
            startPostUploadWait();
            return;
        }
        failAndUnlock("Update timed out. Check Wi-Fi/device connection and try again.");
    };

    xhr.onreadystatechange = function () {
        if (xhr.readyState !== 4) return;

        if ((xhr.status >= 200 && xhr.status < 300) || (xhr.status === 0 && uploadCompleted)) {
            startPostUploadWait();
        } else {
            failAndUnlock(`Update failed (HTTP ${xhr.status}). Try again after reconnecting.`);
        }
    };

    xhr.send(formData);
}

function reboot() {
    const xhttp = new XMLHttpRequest();
    document.getElementById("reboot_button").disabled = true;
    showNotification("Rebooting please reconnect...", "yellow");
    xhttp.open("POST", "/system_reboot");
    xhttp.send("reboot");
}

function send_system_command(command) {
    const xhttp = new XMLHttpRequest();
    const data = {
        "command": command
    };
    xhttp.open("POST", "/system_commands");
    xhttp.send(JSON.stringify(data, null, 0));
}

async function downloadCfg() {
    const endpoints = [
        '/load_config',
        '/load_auto_pid'
    ];
    
    const delay = 500; 
    let combinedData = {};
    let hasErrors = false;
    
    try {
        for (let i = 0; i < endpoints.length; i++) {
            const endpoint = endpoints[i];
            
            try {
                const response = await fetch(endpoint);
                
                if (!response.ok) {
                    throw new Error(`HTTP error! status: ${response.status}`);
                }
                
                const data = await response.json();
                const key = endpoint.replace('/load_', '');
                combinedData[key] = data;
            } catch (fetchError) {
                hasErrors = true;
            }
            
            if (i < endpoints.length - 1) {
                await new Promise(resolve => setTimeout(resolve, delay));
            }
        }
        
        if (Object.keys(combinedData).length === 0) {
            throw new Error('No data was successfully fetched from any endpoint');
        }
        
        const dataStr = JSON.stringify(combinedData, null, 0);
        const blob = new Blob([dataStr], { type: 'application/json' });
        const url = window.URL.createObjectURL(blob);
        
        const link = document.createElement('a');
        link.href = url;
        link.download = `config_${new Date().toISOString().split('T')[0]}.json`;
        
        document.body.appendChild(link);
        link.click();
        
        document.body.removeChild(link);
        window.URL.revokeObjectURL(url);
        
        return true;
        
    } catch (error) {
        alert('Failed to download configuration');
        return false;
    }
}

async function uploadCfg() {
    const fileInput = document.getElementById('fileInput');
    const file = fileInput.files[0];
    if (!file) return;

    const endpointMap = {
        'config': '/store_config',
        'auto_pid': '/store_auto_data'
    };

    const delay = 200;

    try {
        const reader = new FileReader();
        
        reader.onload = async function(e) {
            try {
                const jsonData = JSON.parse(e.target.result);
                let hasErrors = false;

                for (const [key, endpoint] of Object.entries(endpointMap)) {
                    if (jsonData[key]) {
                        try {
                            const response = await fetch(endpoint, {
                                method: 'POST',
                                headers: {
                                    'Content-Type': 'application/json',
                                },
                                body: JSON.stringify(jsonData[key], null, 0)
                            });

                            if (!response.ok) {
                                hasErrors = true;
                                throw new Error(`HTTP error! status: ${response.status}`);
                            }

                            await new Promise(resolve => setTimeout(resolve, delay));

                        } catch (fetchError) {
                            hasErrors = true;
                        }
                    }
                }

                if (hasErrors) {
                    alert('Some configurations failed to upload');
                } else {
                    alert('Configuration uploaded successfully, Rebooting...');
                }
                
                fileInput.value = '';

            } catch (parseError) {
                alert('Failed to parse configuration file');
            }
        };

        reader.onerror = function() {
            alert('Error reading file');
        };

        reader.readAsText(file);

    } catch (error) {
        alert('Upload failed');
    }
}

var loadedLogPeriod = "10";   // last persisted datalog period; re-sent by postConfig (no UI element after trim)

async function Load() {
    const xhttp = new XMLHttpRequest();
xhttp.onload = async function() {
        var obj = JSON.parse(this.responseText);
        // Set WiFi mode by value (more robust than selectedIndex)
        const wifiModeEl = document.getElementById("wifi_mode");
        if (wifiModeEl) {
            const modeFromCfg = obj.wifi_mode || "AP";
            const hasOption = Array.from(wifiModeEl.options || []).some(o => o && o.value === modeFromCfg);
            if (hasOption) {
                wifiModeEl.value = modeFromCfg;
            }
        }

        // Load SmartConnect configuration
        document.getElementById("home_ssid").value = obj.home_ssid || "";
        document.getElementById("home_password").value = obj.home_password || "";
        document.getElementById("home_security").value = obj.home_security || "wpa3";
        document.getElementById("home_protocol").value = obj.home_protocol || "elm327";
        document.getElementById("drive_ssid").value = obj.drive_ssid || "";
        document.getElementById("drive_password").value = obj.drive_password || "";
        document.getElementById("drive_security").value = obj.drive_security || "wpa3";
        document.getElementById("drive_protocol").value = obj.drive_protocol || "elm327";
        document.getElementById("drive_connection_type").value = obj.drive_connection_type || "wifi";
        // Load drive mode timeout value and update display
        document.getElementById("drive_mode_timeout").value = obj.drive_mode_timeout || "60";
        document.getElementById("drive_mode_timeout_value").textContent = obj.drive_mode_timeout || "60";

        
        if(obj.ap_auto_disable == "enable") {
            document.getElementById("ap_auto_disable").selectedIndex = "0";
        } else {
            document.getElementById("ap_auto_disable").selectedIndex = "1";
        }

        try { toggleApStationWarning(); } catch(_) {}

        // Custom AP SSID (optional, default disabled)
        {
            const enEl = document.getElementById("ap_ssid_enable");
            const valEl = document.getElementById("ap_ssid_value");
            if (enEl) {
                enEl.checked = (obj.ap_ssid_en === "enable");
            }
            if (valEl) {
                valEl.value = obj.ap_ssid || "";
            }
            try { toggleApSsid(); } catch(_) {}
        }

        var ch = parseInt(obj.ap_ch);
        ch = ch - 1;
        document.getElementById("ap_ch_value").selectedIndex = ch.toString();
        document.getElementById("ssid_value").value = obj.sta_ssid;
        document.getElementById("pass_value").value = obj.sta_pass;
        document.getElementById("sta_security").value = obj.sta_security || "wpa3";			
        if(obj.can_datarate == "5K") {
            document.getElementById("can_datarate").selectedIndex = "0";
        } else if(obj.can_datarate == "10K") {
            document.getElementById("can_datarate").selectedIndex = "1";
        } else if(obj.can_datarate == "20K") {
            document.getElementById("can_datarate").selectedIndex = "2";
        } else if(obj.can_datarate == "25K") {
            document.getElementById("can_datarate").selectedIndex = "3";
        } else if(obj.can_datarate == "50K") {
            document.getElementById("can_datarate").selectedIndex = "4";
        } else if(obj.can_datarate == "100K") {
            document.getElementById("can_datarate").selectedIndex = "5";
        } else if(obj.can_datarate == "125K") {
            document.getElementById("can_datarate").selectedIndex = "6";
        } else if(obj.can_datarate == "250K") {
            document.getElementById("can_datarate").selectedIndex = "7";
        } else if(obj.can_datarate == "500K") {
            document.getElementById("can_datarate").selectedIndex = "8";
        } else if(obj.can_datarate == "800K") {
            document.getElementById("can_datarate").selectedIndex = "9";
        } else if(obj.can_datarate == "1000K") {
            document.getElementById("can_datarate").selectedIndex = "10";
        } else if(obj.can_datarate == "auto") {
            document.getElementById("can_datarate").selectedIndex = "11";
        }
        if(obj.can_mode == "normal") {
            document.getElementById("can_mode").selectedIndex = "0";
        } else if(obj.can_mode == "silent") {
            document.getElementById("can_mode").selectedIndex = "1";
        }
        if(obj.port_type == "tcp") {
            document.getElementById("port_type").selectedIndex = "0";
        } else if(obj.port_type == "udp") {
            document.getElementById("port_type").selectedIndex = "1";
        }
        if(obj.ble_status == "enable") {
            document.getElementById("ble_status").selectedIndex = 0;
        } else if(obj.ble_status == "disable") {
            document.getElementById("ble_status").selectedIndex = 1;
        }
        if(obj.sleep_status == "enable") {
            document.getElementById("sleep_status").selectedIndex = "0";
        } else if(obj.sleep_status == "disable") {
            document.getElementById("sleep_status").selectedIndex = "1";
        }

        if(obj.sleep_disable_agree == "yes") {
            document.getElementById("sleep_disable_agree").selectedIndex = "1";
        } else {
            document.getElementById("sleep_disable_agree").selectedIndex = "0";
        }
        toggleSleepWarning();
        if(obj.periodic_wakeup == "enable") {
            document.getElementById("periodic_wakeup").selectedIndex = "0";
        } else if(obj.periodic_wakeup == "disable") {
            document.getElementById("periodic_wakeup").selectedIndex = "1";
        }

        document.getElementById("batt_mqtt_user").value = obj.batt_mqtt_user;
        document.getElementById("batt_mqtt_pass").value = obj.batt_mqtt_pass;
        if(obj.batt_alert_time == "1") {
            document.getElementById("batt_alert_time").selectedIndex = "0";
        } else if(obj.batt_alert_time == "6") {
            document.getElementById("batt_alert_time").selectedIndex = "1";
        } else if(obj.batt_alert_time == "12") {
            document.getElementById("batt_alert_time").selectedIndex = "2";
        } else if(obj.batt_alert_time == "24") {
            document.getElementById("batt_alert_time").selectedIndex = "3";
        }
        if(document.getElementById("batt_alert").value == "enable") {
            document.getElementById("batt_alert_div").style.display = "none";
        } else if(document.getElementById("batt_alert").value == "disable") {
            document.getElementById("batt_alert_div").style.display = "none";
        }

        // --- Restored settings population (regression fix: commit d372fc9 over-cut this block,
        //     causing every Submit to persist stock HTML defaults). MQTT-gateway lines intentionally
        //     omitted (feature removed by the trim); protocol is populated by checkStatus(). ---
        // Datalogger master + wide-CSV grid controls (firmware defaults are fixed/10).
        var _cs_on = (obj.csv_log === "enable");
        document.getElementById("csv_log").value = _cs_on ? "enable" : "disable";
        document.getElementById("logging_master").value = _cs_on ? "enable" : "disable";
        document.getElementById("csv_grid_mode").value = (obj.csv_grid_mode === "event") ? "event" : "fixed";
        var _hz = parseInt(obj.csv_grid_hz, 10);
        document.getElementById("csv_grid_hz").value = (_hz >= 1 && _hz <= 50) ? _hz : 10;
        document.getElementById("csv_require_engine").value = (obj.csv_require_engine === "disable") ? "disable" : "enable";
        applyLoggerXor();

        // SD card is the only storage option after the trim ("internal" was removed);
        // the select has a single option, so pin it to index 0.
        document.getElementById("log_storage").selectedIndex = 0;

        // IMU wake threshold (raw LSB; displayed in mg at 3.9 mg/LSB).
        document.getElementById("imu_threshold").value = obj.imu_threshold || "8";
        document.getElementById("imu_threshold_value").textContent = ((obj.imu_threshold || 8) * 3.9).toFixed(1) + ' mg';

        const blePowerVal = ("ble_power" in obj) ? obj.ble_power : 9;
        document.getElementById("ble_power").value = blePowerVal;
        document.getElementById("ble_power_value").textContent = blePowerVal;

        document.getElementById("tcp_port_value").value = obj.port;
        document.getElementById("ap_pass_value").value = obj.ap_pass;
        document.getElementById("ble_pass_value").value = obj.ble_pass;
        document.getElementById("sleep_volt").value = obj.sleep_volt;
        document.getElementById("sleep_volt_value").textContent = obj.sleep_volt;
        if (obj.engine_volt !== undefined) {   // null-guard for old configs missing the key
            document.getElementById("engine_volt").value = obj.engine_volt;
            document.getElementById("engine_volt_value").textContent = obj.engine_volt;
        }
        document.getElementById("sleep_time").value = obj.sleep_time;
        document.getElementById('sleep_time_value').textContent = obj.sleep_time;
        document.getElementById("wakeup_interval").value = obj.wakeup_interval;
        document.getElementById('wakeup_interval_value').textContent = obj.wakeup_interval;
        document.getElementById("batt_alert").value = "disable";
        document.getElementById("batt_alert_ssid").value = obj.batt_alert_ssid;
        document.getElementById("batt_alert_pass").value = obj.batt_alert_pass;
        document.getElementById("batt_alert_volt").value = obj.batt_alert_volt;
        document.getElementById("batt_alert_protocol").value = obj.batt_alert_protocol;
        document.getElementById("batt_alert_url").value = obj.batt_alert_url.slice(7);
        document.getElementById("batt_alert_port").value = obj.batt_alert_port;
        document.getElementById("batt_alert_topic").value = obj.batt_alert_topic;
        // Preserve the persisted datalog period (no UI element after the trim; feeds
        // poll_log_init/fast_log_init). Re-sent verbatim by postConfig so Submit never pins it to 10.
        if (obj.log_period !== undefined && obj.log_period !== null) {
            loadedLogPeriod = obj.log_period;
        }
        loadautoPID();

        // Load fallback networks if present
        try {
            const fb = Array.isArray(obj.sta_fallbacks) ? obj.sta_fallbacks : [];
            renderFallbackNetworks(fb);
        } catch(e) {
            renderFallbackNetworks([]);
        }

        // Apply mode-dependent enable/disable rules after values are loaded
        try { toggleSmartConnectConfig(); } catch(_) {}
        try { toggleApStationWarning(); } catch(_) {}
        try { submit_enable(); } catch(_) {}

        document.querySelector(".store").disabled = true;
        document.getElementById("submit_button").disabled = true;
    };
    checkStatus();
    xhttp.open("GET", "/load_config");
    xhttp.send();

    
    // Initialize SmartConnect configuration visibility
    toggleSmartConnectConfig();

    // Initialize AP+Station warning visibility
    try { toggleApStationWarning(); } catch(_) {}

    // Initialize Automate low-voltage defaults before auto_pid.json is loaded.
    try { togglePidPollingMinVoltageRow(); } catch(_) {}

    // Initialize AP SSID input state
    try { toggleApSsid(); } catch(_) {}
    
    // Initialize lucide icons
    if (typeof lucide !== 'undefined' && lucide.createIcons) {
        lucide.createIcons();
    }
}

// ---- CSV datalogger runtime Start/Stop (POST /csv_logger) + live status poll ----
// The button is a runtime action (NOT part of the config form / Submit). Firmware status
// is the source of truth: after each POST and on every poll we reconcile the button label
// from /csv_status (manual_override || session_active), robust to cross-core latency and
// to start/stop happening from another client or from ignition.
function csv_notify(m, c) {
    if (typeof showNotification === 'function') showNotification(m, c); else console.log(m);
}
function csv_log_button_en(b) {
    // b==1 => idle/Start, b==0 => active/Stop (mirror mon_button_en; no sibling inputs)
    var btn = document.getElementById('csv_log_button');
    if (btn) btn.value = (b == 1) ? 'Start' : 'Stop';
}
function csv_status_render(j) {
    // Firmware status is the source of truth (robust to cross-core latency + external
    // start/stop). "on" => button shows Stop; otherwise Start.
    var on = !!(j && (j.manual_mode === 'on' || j.session_active));
    csv_log_button_en(on ? 0 : 1);
    console_status_render(j, on);
    var line = document.getElementById('csv_status_line');
    if (!line) return;
    if (j && j.session_active) {
        line.textContent = 'logging • ' + (j.file || '') + ' • ' + (j.rows_written || 0) + ' rows';
    } else if (j && j.manual_mode === 'on' && j.sd_mounted === false) {
        line.textContent = 'waiting for SD card…';
    } else if (j && j.manual_mode === 'on') {
        line.textContent = 'armed — waiting for data…';
    } else if (j && j.manual_mode === 'off') {
        line.textContent = 'stopped';
    } else {
        line.textContent = 'idle';
    }
}
function csv_status_tick() {
    if (window._csvStatusInFlight) return;
    window._csvStatusInFlight = true;
    fetch('/csv_status').then(function(r) { return r.json(); })
        .then(function(j) { csv_status_render(j); })
        .catch(function() {})
        .finally(function() { window._csvStatusInFlight = false; });
}
function csv_status_poll_start() {
    if (window._csvStatusTimer) return;
    csv_status_tick();
    window._csvStatusTimer = setInterval(csv_status_tick, 1500);
}
function csv_status_poll_stop() {
    if (window._csvStatusTimer) { clearInterval(window._csvStatusTimer); window._csvStatusTimer = null; }
}
function csv_log_control() {
    var btn = document.getElementById('csv_log_button');
    if (!btn) return;
    var op = (btn.value === 'Start') ? 'start' : 'stop';
    fetch('/csv_logger?op=' + op, { method: 'POST' })
        .then(function(r) { if (!r.ok) throw new Error('HTTP ' + r.status); return r.json(); })
        .then(function(j) {
            csv_status_render(j);
            if (op === 'start') {
                csv_notify(j.session_active ? 'Datalogging started' : 'Datalogging armed — waiting for data',
                           j.session_active ? 'green' : 'orange');
            } else {
                csv_notify('Datalogging stopped', 'blue');
            }
            csv_status_poll_start();
        })
        .catch(function(e) { csv_notify('CSV control failed: ' + e.message, 'red'); });
}

function isNameUnique(name) {
    return canData.every((item) => item["Name"] !== name);
}

function toggleSleepWarning() {
    const sleepStatus = document.getElementById("sleep_status").value;
    const sleepWarningDiv = document.getElementById("sleep_warning_div");
    const agreementSelect = document.getElementById("sleep_disable_agree");
    
    if (sleepStatus === "disable") {
        sleepWarningDiv.style.display = "block";
        agreementSelect.value = "no";
    } else {
        sleepWarningDiv.style.display = "none";
    }
}

async function scanWifiNetworks() {
    const scanButton = document.getElementById('wifi_scan_button');
    const networksList = document.getElementById('wifi_networks_list');
    const networksRow = document.getElementById('wifi_networks_row');
    
    try {
        scanButton.disabled = true;
        scanButton.textContent = "Scanning...";
        
        const response = await fetch('/wifi_scan');
        if (!response.ok) {
            throw new Error(`HTTP error! status: ${response.status}`);
        }
        
        const data = await response.text();

        // Clear existing options
        networksList.innerHTML = '<option value="">Select a network...</option>';

        if (data && data !== "NONE") {
            try {
                const scanResult = JSON.parse(data);

                // Check for error response from server
                if (scanResult.error) {
                    throw new Error(scanResult.error);
                }

                if (scanResult.networks && Array.isArray(scanResult.networks)) {
                    // Filter out networks with empty SSID and sort by signal strength
                    const validNetworks = scanResult.networks
                        .filter(network => network.ssid && network.ssid.trim() !== '')
                        .sort((a, b) => b.rssi - a.rssi); // Sort by signal strength (strongest first)
                    
                    // Remove duplicates (same SSID, keep the strongest signal)
                    const uniqueNetworks = [];
                    const seenSSIDs = new Set();
                    
                    validNetworks.forEach(network => {
                        if (!seenSSIDs.has(network.ssid)) {
                            seenSSIDs.add(network.ssid);
                            uniqueNetworks.push(network);
                        }
                    });
                    
                    if (uniqueNetworks.length > 0) {
                        uniqueNetworks.forEach(network => {
                            const option = document.createElement('option');
                            option.value = network.ssid;
                            
                            // Create a nice display name with signal strength and security
                            const signalBars = getSignalQuality(network.rssi);
                            const security = getSecurityType(network.auth_mode);
                            option.textContent = `${network.ssid} (${signalBars}, ${network.rssi} dBm, ${security})`;
                            
                            networksList.appendChild(option);
                        });
                        networksRow.style.display = 'table-row';
                        showNotification(`Found ${uniqueNetworks.length} WiFi networks`, "green");
                    } else {
                        showNotification("No valid networks found", "yellow");
                    }
                } else {
                    throw new Error("Invalid scan data format");
                }
            } catch (parseError) {
                console.error('Parse error:', parseError);
                showNotification("WiFi Scan failed " + parseError, "red");
            }
        } else {
            showNotification("No networks found", "yellow");
        }
    } catch (error) {
        console.error('WiFi scan error:', error);
        showNotification("WiFi scan failed: " + error.message, "red");
    } finally {
        scanButton.disabled = false;
        scanButton.textContent = "Scan";
    }
}

function getSignalQuality(rssi) {
    // Convert RSSI to signal quality text
    if (rssi >= -50) return "Excellent"; // Excellent
    if (rssi >= -60) return "Good";      // Good  
    if (rssi >= -70) return "Fair";      // Fair
    return "Weak";                       // Weak
}

function getSecurityType(authMode) {
    // Simplify auth mode display
    switch(authMode) {
        case "OPEN": return "Open";
        case "WPA_PSK": return "WPA";
        case "WPA2_PSK": return "WPA2";
        case "WPA_WPA2_PSK": return "WPA/WPA2";
        case "WPA2_WPA3_PSK": return "WPA2/WPA3";
        case "WPA3_PSK": return "WPA3";
        default: return authMode;
    }
}

function selectWifiNetwork() {
    const networksList = document.getElementById('wifi_networks_list');
    const ssidInput = document.getElementById('ssid_value');
    
    if (networksList.value) {
        ssidInput.value = networksList.value;
        submit_enable(); // Trigger form validation
        enableAutoStoreButton(); // Enable store button if it exists
    }
}


// ---- Field Console (issue #5): one-tap trip landing surface ----
function console_status_render(j, on) {
    var dot = document.getElementById('console_rec_dot');
    var state = document.getElementById('console_rec_state');
    var file = document.getElementById('console_rec_file');
    var btn = document.getElementById('console_rec_btn');
    if (!dot || !state || !btn) return;
    var live = !!(j && j.session_active);
    var armed = !!(j && j.manual_mode === 'on' && !live);
    dot.className = 'rec-dot' + (live ? ' live' : (armed ? ' armed' : ''));
    state.textContent = live ? 'Recording' : (armed ? 'Armed' : 'Idle');
    if (file) file.textContent = live ? (j.file || '') : (armed ? 'waiting for data\u2026' : '\u00a0');
    btn.textContent = on ? 'Stop Trip' : 'Start Trip';
    btn.className = 'console-rec-btn' + (on ? ' stop' : '');
    var rows = document.getElementById('console_rec_rows');
    var dropped = document.getElementById('console_rec_dropped');
    var cols = document.getElementById('console_rec_cols');
    if (rows) rows.textContent = (j && j.rows_written) || 0;
    if (dropped) dropped.textContent = (j && j.rows_dropped) || 0;
    if (cols) cols.textContent = (j && j.columns) || 0;
    var sdDot = document.getElementById('console_dot_sd');
    var sdChip = document.getElementById('console_chip_sd');
    if (sdDot && j && typeof j.sd_mounted === 'boolean') {
        sdDot.className = 'chip-dot ' + (j.sd_mounted ? 'ok' : 'bad');
        if (sdChip) sdChip.textContent = j.sd_mounted ? 'mounted' : 'missing';
    }
}

function consoleRecClick() {
    var btn = document.getElementById('console_rec_btn');
    if (!btn) return;
    var op = btn.classList.contains('stop') ? 'stop' : 'start';
    fetch('/csv_logger?op=' + op, { method: 'POST' })
        .then(function(r) { if (!r.ok) throw new Error('HTTP ' + r.status); return r.json(); })
        .then(function(j) {
            csv_status_render(j);
            if (op === 'start') {
                csv_notify(j.session_active ? 'Trip recording started' : 'Trip armed \u2014 waiting for data',
                           j.session_active ? 'green' : 'orange');
            } else {
                csv_notify('Trip stopped', 'blue');
                setTimeout(consoleLoadTrips, 800);
            }
            csv_status_poll_start();
        })
        .catch(function(e) { csv_notify('Trip control failed: ' + e.message, 'red'); });
}

function consoleFmtSize(b) {
    b = Number(b) || 0;
    if (b >= 1048576) return (b / 1048576).toFixed(1) + ' MB';
    if (b >= 1024) return (b / 1024).toFixed(0) + ' KB';
    return b + ' B';
}

function consoleFmtDate(mtime) {
    if (!mtime) return '';
    var d = new Date(mtime * 1000);
    function p(n) { return (n < 10 ? '0' : '') + n; }
    return d.getFullYear() + '-' + p(d.getMonth() + 1) + '-' + p(d.getDate()) + ' ' + p(d.getHours()) + ':' + p(d.getMinutes());
}

function consoleLoadTrips() {
    fetch('/csv_list')
        .then(function(r) { return r.json(); })
        .then(function(j) {
            var box = document.getElementById('console_trips');
            if (!box) return;
            var files = (j && Array.isArray(j.files)) ? j.files.slice(0, 12) : [];
            if (!files.length) {
                box.innerHTML = '<div class="console-empty">No trips yet \u2014 press Start Trip to record one.</div>';
                return;
            }
            box.innerHTML = files.map(function(f) {
                var name = String(f.name || '');
                var meta = consoleFmtSize(f.size) + (f.mtime ? ' \u2022 ' + consoleFmtDate(f.mtime) : '');
                return '<div class="console-trip"><div><div class="t-name">' + name + '</div>' +
                       '<div class="t-meta">' + meta + '</div></div>' +
                       '<a class="t-dl" href="/download_csv?file=' + encodeURIComponent(name) + '" download>' +
                       '<button class="console-mini-btn" type="button">Download</button></a></div>';
            }).join('');
        })
        .catch(function() {});
}

function consoleLoadChips() {
    fetch('/check_status')
        .then(function(r) { return r.json(); })
        .then(function(d) {
            var wifiDot = document.getElementById('console_dot_wifi');
            var wifiChip = document.getElementById('console_chip_wifi');
            var proto = document.getElementById('console_chip_proto');
            var fw = document.getElementById('console_chip_fw');
            var staUp = (d && d.sta_status === 'Connected');
            if (wifiDot) wifiDot.className = 'chip-dot ' + (staUp ? 'ok' : 'bad');
            if (wifiChip) wifiChip.textContent = staUp ? (d.sta_ip || 'connected') : 'AP only';
            if (proto) proto.textContent = (d && d.protocol) || '\u2013';
            if (fw) fw.textContent = (d && (d.git_version || d.fw_version)) || '\u2013';
        })
        .catch(function() {});
}

function consoleRefresh() {
    consoleLoadTrips();
    consoleLoadChips();
}

document.getElementById("defaultOpen").click();
