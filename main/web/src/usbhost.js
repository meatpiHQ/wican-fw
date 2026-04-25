function usbHostDeviceTypeLabel(type)
{
    switch (type)
    {
        case 'espnetlink':
            return 'ESPNetLink';
        case 'usb_ethernet':
            return 'USB Ethernet';
        case 'none':
            return 'None';
        default:
            return type || 'Unknown';
    }
}

function usbHostDriverLabel(driver)
{
    switch (driver)
    {
        case 'rtl8152':
            return 'RTL8152';
        case 'asix':
            return 'ASIX';
        case 'cdc_ecm':
            return 'CDC-ECM';
        case 'cdc_ncm':
            return 'CDC-NCM';
        case 'rndis':
            return 'RNDIS';
        default:
            return driver ? String(driver) : '-';
    }
}

function usbHostIpModeLabel(mode)
{
    return mode === 'static' ? 'Static' : 'DHCP';
}

function usbHostHasValue(value)
{
    return value !== undefined && value !== null && value !== '';
}

function usbHostIsTruthy(value)
{
    if (typeof value === 'string')
    {
        const normalized = value.trim().toLowerCase();
        return normalized === 'true' || normalized === '1' || normalized === 'yes' || normalized === 'on';
    }

    return !!value;
}

function usbHostStateLabel(state)
{
    if (!state)
    {
        return 'Unknown';
    }

    return String(state)
        .split('_')
        .map(part => part ? (part.charAt(0).toUpperCase() + part.slice(1)) : '')
        .join(' ');
}

function usbHostSetText(id, value, fallback = '-')
{
    const el = document.getElementById(id);
    if (!el)
    {
        return;
    }

    if (value === undefined || value === null || value === '')
    {
        el.textContent = fallback;
        return;
    }

    el.textContent = String(value);
}

function usbHostSetBadge(id, active, activeText, inactiveText)
{
    const el = document.getElementById(id);
    if (!el)
    {
        return;
    }

    el.textContent = active ? activeText : inactiveText;
    el.classList.remove('status-connected', 'status-disconnected', 'status-neutral');
    el.classList.add(active ? 'status-connected' : 'status-disconnected');
}

function usbHostSetBadgeNeutral(id, text)
{
    const el = document.getElementById(id);
    if (!el)
    {
        return;
    }

    el.textContent = text;
    el.classList.remove('status-connected', 'status-disconnected');
    el.classList.add('status-neutral');
}

function usbHostSetSectionVisible(id, visible)
{
    const el = document.getElementById(id);
    if (!el)
    {
        return;
    }

    el.style.display = visible ? '' : 'none';
}

function usbHostPrettyJsonText(data, emptyMessage = 'No data available yet.')
{
    if (data === undefined || data === null || data === '')
    {
        return emptyMessage;
    }

    if (typeof data === 'string')
    {
        try
        {
            return JSON.stringify(JSON.parse(data), null, 2);
        }
        catch (_)
        {
            return data;
        }
    }

    try
    {
        return JSON.stringify(data, null, 2);
    }
    catch (_)
    {
        return String(data);
    }
}

async function usbHostFetchJson(url, options)
{
    const response = await fetch(url, options);
    const text = await response.text();
    let data = null;

    try
    {
        data = text ? JSON.parse(text) : null;
    }
    catch (_)
    {
        data = null;
    }

    if (!response.ok)
    {
        const message = (data && (data.error || data.message)) || text || response.statusText || 'Request failed';
        const error = new Error(message);
        error.status = response.status;
        error.body = data;
        throw error;
    }

    return data;
}

function usbHostGetState()
{
    if (!window.usbHostState)
    {
        window.usbHostState = {
            dirty: false,
            preferSavedApn: false,
            draft: null,
            status: null,
            config: null,
            resolvedDevice: 'none',
            espnetlink: {
                config: null,
                lte: null,
                gps: null
            }
        };
    }

    return window.usbHostState;
}

function usbHostNormalizeKey(value)
{
    if (value === undefined || value === null)
    {
        return '';
    }

    return String(value).trim().toLowerCase();
}

function usbHostNormalizeDeviceType(value)
{
    const normalized = usbHostNormalizeKey(value);

    switch (normalized)
    {
        case 'usb ethernet':
        case 'usb-ethernet':
        case 'ethernet':
            return 'usb_ethernet';
        default:
            return normalized;
    }
}

function usbHostHasLiveDevice(status)
{
    return !!(status && (
        usbHostIsTruthy(status.device_detected) ||
        usbHostIsTruthy(status.device_started) ||
        usbHostNormalizeKey(status.ethernet_driver) !== '' ||
        usbHostHasValue(status.ethernet_ifkey) ||
        usbHostHasValue(status.local_ip) ||
        usbHostHasValue(status.management_ip)
    ));
}

function usbHostResolvedDeviceType(status, config)
{
    if (!usbHostHasLiveDevice(status))
    {
        return 'none';
    }

    const liveDriver = usbHostNormalizeKey(status && status.ethernet_driver);
    if (liveDriver)
    {
        return liveDriver === 'rndis' ? 'espnetlink' : 'usb_ethernet';
    }

    const liveDeviceType = usbHostNormalizeDeviceType(status && status.active_device_type);
    if (liveDeviceType && liveDeviceType !== 'none')
    {
        return liveDeviceType;
    }

    const configuredDeviceType = usbHostNormalizeDeviceType(status && status.configured_device_type);
    if (configuredDeviceType && configuredDeviceType !== 'none' && status && status.device_started)
    {
        return configuredDeviceType;
    }

    return 'none';
}

function usbHostCloneConfigState()
{
    const state = usbHostGetState();
    return state.config ? JSON.parse(JSON.stringify(state.config)) : null;
}

function usbHostBuildConfigPayload(config)
{
    return {
        enabled: !!config.enabled,
        active_device_type: config.active_device_type || 'none',
        uplink_priority: config.uplink_priority || 'wifi_first',
        monitor_interval_ms: config.monitor_interval_ms,
        device_attach_delay_ms: config.device_attach_delay_ms,
        device_detach_delay_ms: config.device_detach_delay_ms,
        espnetlink: config.espnetlink || {},
        usb_ethernet: config.usb_ethernet || {}
    };
}

function usbHostExtractConfigApn(data)
{
    if (!data)
    {
        return '';
    }

    const payload = data.data && typeof data.data === 'object' ? data.data : data;
    if (payload.APN !== undefined && payload.APN !== null)
    {
        return String(payload.APN);
    }
    if (payload.apn !== undefined && payload.apn !== null)
    {
        return String(payload.apn);
    }

    return '';
}

function usbHostResolvedDraftApn(config)
{
    const state = usbHostGetState();
    const savedApn = ((config && config.espnetlink && config.espnetlink.apn) || '').trim();
    const currentApn = usbHostCurrentApn().trim();

    if (state.preferSavedApn && savedApn)
    {
        if (!currentApn || currentApn !== savedApn)
        {
            return savedApn;
        }

        state.preferSavedApn = false;
    }

    return currentApn || savedApn;
}

function usbHostSyncDraftFromConfig(force = false)
{
    const state = usbHostGetState();
    const { config } = state;
    const espnetlink = (config && config.espnetlink) || {};
    const usbEthernet = (config && config.usb_ethernet) || {};

    if (!config)
    {
        return;
    }

    if (!force && state.dirty && state.draft)
    {
        return;
    }

    state.draft = {
        enabled: !!config.enabled,
        uplinkPriority: config.uplink_priority || 'wifi_first',
        espnetlink: {
            apn: usbHostResolvedDraftApn(config),
            ipMode: espnetlink.ip_mode || 'dhcp',
            staticIp: espnetlink.static_ip || '',
            staticNetmask: espnetlink.static_netmask || '',
            staticGw: espnetlink.static_gw || '',
            managementIp: espnetlink.management_ip || ''
        },
        usbEthernet: {
            ipMode: usbEthernet.ip_mode || 'dhcp',
            staticIp: usbEthernet.static_ip || '',
            staticNetmask: usbEthernet.static_netmask || '',
            staticGw: usbEthernet.static_gw || ''
        }
    };
    state.dirty = false;
}

function usbHostCaptureDraft()
{
    const state = usbHostGetState();
    const enabledEl = document.getElementById('usb_host_enabled');
    const uplinkEl = document.getElementById('usb_host_uplink_priority');
    const apnEl = document.getElementById('usb_host_apn_value');
    const espModeEl = document.getElementById('usb_host_esp_ip_mode');
    const espStaticIpEl = document.getElementById('usb_host_esp_static_ip');
    const espStaticMaskEl = document.getElementById('usb_host_esp_static_netmask');
    const espStaticGwEl = document.getElementById('usb_host_esp_static_gw');
    const espMgmtEl = document.getElementById('usb_host_esp_management_ip');
    const ethModeEl = document.getElementById('usb_host_eth_ip_mode');
    const ethStaticIpEl = document.getElementById('usb_host_eth_static_ip');
    const ethStaticMaskEl = document.getElementById('usb_host_eth_static_netmask');
    const ethStaticGwEl = document.getElementById('usb_host_eth_static_gw');

    state.draft = {
        enabled: enabledEl ? enabledEl.value === 'true' : false,
        uplinkPriority: uplinkEl ? uplinkEl.value : 'wifi_first',
        espnetlink: {
            apn: apnEl ? apnEl.value.trim() : '',
            ipMode: espModeEl ? espModeEl.value : 'dhcp',
            staticIp: espStaticIpEl ? espStaticIpEl.value.trim() : '',
            staticNetmask: espStaticMaskEl ? espStaticMaskEl.value.trim() : '',
            staticGw: espStaticGwEl ? espStaticGwEl.value.trim() : '',
            managementIp: espMgmtEl ? espMgmtEl.value.trim() : ''
        },
        usbEthernet: {
            ipMode: ethModeEl ? ethModeEl.value : 'dhcp',
            staticIp: ethStaticIpEl ? ethStaticIpEl.value.trim() : '',
            staticNetmask: ethStaticMaskEl ? ethStaticMaskEl.value.trim() : '',
            staticGw: ethStaticGwEl ? ethStaticGwEl.value.trim() : ''
        }
    };

    return state.draft;
}

function usbHostUpdateIpModeVisibility()
{
    const state = usbHostGetState();
    const draft = state.draft || {};
    const espnetlink = draft.espnetlink || {};
    const usbEthernet = draft.usbEthernet || {};
    const espStaticVisible = espnetlink.ipMode === 'static';
    const usbStaticVisible = usbEthernet.ipMode === 'static';

    usbHostSetSectionVisible('usb_host_esp_static_ip_row', espStaticVisible);
    usbHostSetSectionVisible('usb_host_esp_static_netmask_row', espStaticVisible);
    usbHostSetSectionVisible('usb_host_esp_static_gw_row', espStaticVisible);

    usbHostSetSectionVisible('usb_host_eth_static_ip_row', usbStaticVisible);
    usbHostSetSectionVisible('usb_host_eth_static_netmask_row', usbStaticVisible);
    usbHostSetSectionVisible('usb_host_eth_static_gw_row', usbStaticVisible);
}

function usbHostMarkDirty()
{
    const state = usbHostGetState();
    usbHostCaptureDraft();
    usbHostUpdateIpModeVisibility();
    state.dirty = true;
    submit_enable();
}

function usbHostInitializeUi()
{
    if (!document.getElementById('usb_host_tab'))
    {
        return;
    }

    usbHostCaptureDraft();
    usbHostUpdateIpModeVisibility();
    usbHostSetBadgeNeutral('usb_host_status_cli', 'N/A');
    usbHostSetSectionVisible('usb_host_esp_profile_card', false);
    usbHostSetSectionVisible('usb_host_eth_profile_card', false);
    usbHostSetSectionVisible('usb_host_esp_status_panels', false);
    usbHostSetSectionVisible('usb_host_generic_panel', false);
}

function usbHostCurrentApn()
{
    const state = usbHostGetState();
    return usbHostExtractConfigApn(state.espnetlink && state.espnetlink.config);
}

function usbHostTextValue(value, fallback = '-')
{
    return value === undefined || value === null || value === '' ? fallback : String(value);
}

function usbHostStatusSummary(status, config)
{
    const resolvedDevice = usbHostResolvedDeviceType(status, config);
    const ethernetConnected = !!(status && (usbHostIsTruthy(status.ethernet_connected) || usbHostHasValue(status.local_ip)));
    const cliConnected = !!(status && (usbHostIsTruthy(status.cli_connected) || usbHostHasValue(status.management_ip)));

    if (!config || !config.enabled)
    {
        return 'USB Host is disabled';
    }

    if (!status)
    {
        return 'Waiting for USB Host status';
    }

    if (status.last_error)
    {
        return status.last_error;
    }

    if (!status.device_detected)
    {
        return 'No USB device detected';
    }

    if (resolvedDevice === 'espnetlink')
    {
        if (cliConnected && ethernetConnected)
        {
            return 'ESPNetLink connected';
        }
        if (cliConnected)
        {
            return 'ESPNetLink CLI connected';
        }
        return 'ESPNetLink detected';
    }

    if (resolvedDevice === 'usb_ethernet')
    {
        const driver = usbHostDriverLabel(status.ethernet_driver);
        if (ethernetConnected)
        {
            return driver + ' adapter connected';
        }
        return driver + ' adapter detected';
    }

    return usbHostStateLabel(status.state);
}

function usbHostRenderStatus(status)
{
    const state = usbHostGetState();
    const { config } = state;
    const resolvedDevice = usbHostResolvedDeviceType(status, config);
    const deviceDetected = usbHostHasLiveDevice(status);
    const ethernetConnected = !!(status && (
        usbHostIsTruthy(status.ethernet_connected) ||
        usbHostHasValue(status.local_ip) ||
        usbHostHasValue(status.gateway)
    ));

    state.resolvedDevice = resolvedDevice;

    const isEspnetlink = resolvedDevice === 'espnetlink';
    const cliApplicable = isEspnetlink && !!(status && usbHostIsTruthy(status.cli_enabled));
    const cliConnected = !!(status && (
        usbHostIsTruthy(status.cli_connected) ||
        usbHostHasValue(status.management_ip)
    ));

    usbHostSetText('usb_host_status_state', status ? usbHostStateLabel(status.state) : 'Unknown');
    usbHostSetText('usb_host_status_device', deviceDetected ? usbHostDeviceTypeLabel(resolvedDevice) : '');
    usbHostSetText('usb_host_status_driver', status ? usbHostDriverLabel(status.ethernet_driver) : '-');
    usbHostSetText('usb_host_status_ifkey', status ? status.ethernet_ifkey : '');
    usbHostSetBadge('usb_host_status_detected', deviceDetected, 'Detected', 'Not detected');
    usbHostSetBadge('usb_host_status_ethernet', ethernetConnected, 'Connected', 'Down');
    if (cliApplicable)
    {
        usbHostSetBadge('usb_host_status_cli', cliConnected, 'Connected', 'Disconnected');
    }
    else
    {
        usbHostSetBadgeNeutral('usb_host_status_cli', 'N/A');
    }
    usbHostSetText('usb_host_status_local_ip', status ? status.local_ip : '');
    usbHostSetText('usb_host_status_gateway', status ? status.gateway : '');
    usbHostSetText('usb_host_status_management_ip', (isEspnetlink && status) ? status.management_ip : '', isEspnetlink ? '-' : 'N/A');
    usbHostSetText('usb_host_status_summary', usbHostStatusSummary(status, config));
    usbHostSetText('usb_host_status_last_error', status ? status.last_error : '');

    usbHostSetText('usb_host_generic_driver', status ? usbHostDriverLabel(status.ethernet_driver) : '-');
    usbHostSetText('usb_host_generic_ifkey', status ? status.ethernet_ifkey : '');
    usbHostSetText('usb_host_generic_local_ip', status ? status.local_ip : '');
    usbHostSetText('usb_host_generic_gateway', status ? status.gateway : '');
}

function usbHostRenderConfig(config)
{
    if (!config)
    {
        return;
    }

    const state = usbHostGetState();
    usbHostSyncDraftFromConfig();

    const draft = state.draft || {};
    const esp = draft.espnetlink || {};
    const usbEthernet = draft.usbEthernet || {};
    const enabledEl = document.getElementById('usb_host_enabled');
    const uplinkEl = document.getElementById('usb_host_uplink_priority');
    const apnEl = document.getElementById('usb_host_apn_value');
    const espModeEl = document.getElementById('usb_host_esp_ip_mode');
    const espStaticIpEl = document.getElementById('usb_host_esp_static_ip');
    const espStaticMaskEl = document.getElementById('usb_host_esp_static_netmask');
    const espStaticGwEl = document.getElementById('usb_host_esp_static_gw');
    const espMgmtEl = document.getElementById('usb_host_esp_management_ip');
    const ethModeEl = document.getElementById('usb_host_eth_ip_mode');
    const ethStaticIpEl = document.getElementById('usb_host_eth_static_ip');
    const ethStaticMaskEl = document.getElementById('usb_host_eth_static_netmask');
    const ethStaticGwEl = document.getElementById('usb_host_eth_static_gw');

    if (enabledEl && document.activeElement !== enabledEl)
    {
        enabledEl.value = draft.enabled ? 'true' : 'false';
    }

    if (uplinkEl && document.activeElement !== uplinkEl)
    {
        uplinkEl.value = draft.uplinkPriority || 'wifi_first';
    }

    if (apnEl && document.activeElement !== apnEl)
    {
        apnEl.value = esp.apn || '';
    }

    if (espModeEl && document.activeElement !== espModeEl)
    {
        espModeEl.value = esp.ipMode || 'dhcp';
    }

    if (espStaticIpEl && document.activeElement !== espStaticIpEl)
    {
        espStaticIpEl.value = esp.staticIp || '';
    }

    if (espStaticMaskEl && document.activeElement !== espStaticMaskEl)
    {
        espStaticMaskEl.value = esp.staticNetmask || '';
    }

    if (espStaticGwEl && document.activeElement !== espStaticGwEl)
    {
        espStaticGwEl.value = esp.staticGw || '';
    }

    if (espMgmtEl && document.activeElement !== espMgmtEl)
    {
        espMgmtEl.value = esp.managementIp || '';
    }

    if (ethModeEl && document.activeElement !== ethModeEl)
    {
        ethModeEl.value = usbEthernet.ipMode || 'dhcp';
    }

    if (ethStaticIpEl && document.activeElement !== ethStaticIpEl)
    {
        ethStaticIpEl.value = usbEthernet.staticIp || '';
    }

    if (ethStaticMaskEl && document.activeElement !== ethStaticMaskEl)
    {
        ethStaticMaskEl.value = usbEthernet.staticNetmask || '';
    }

    if (ethStaticGwEl && document.activeElement !== ethStaticGwEl)
    {
        ethStaticGwEl.value = usbEthernet.staticGw || '';
    }

    usbHostUpdateIpModeVisibility();
}

function usbHostGpsFixText(gps)
{
    if (!gps)
    {
        return '-';
    }

    if (gps.valid)
    {
        return gps.fix_type ? 'Valid (' + gps.fix_type + ')' : 'Valid';
    }

    if (gps.fix_type !== undefined && gps.fix_type !== null && gps.fix_type !== '')
    {
        return 'No fix (' + gps.fix_type + ')';
    }

    return 'No fix';
}

function usbHostGpsLocationText(gps)
{
    if (!gps || !gps.valid)
    {
        return '-';
    }

    const { lat, lon } = gps;
    if (lat === undefined || lon === undefined || lat === null || lon === null)
    {
        return '-';
    }

    return String(lat) + ', ' + String(lon);
}

function usbHostGpsSpeedText(gps)
{
    if (!gps)
    {
        return '-';
    }

    if (gps.speed_kmph !== undefined && gps.speed_kmph !== null)
    {
        return String(gps.speed_kmph) + ' km/h';
    }

    if (gps.speed_knots !== undefined && gps.speed_knots !== null)
    {
        return String(gps.speed_knots) + ' kn';
    }

    return '-';
}

function usbHostLteOperatorText(lte)
{
    if (!lte)
    {
        return '-';
    }

    return usbHostTextValue(lte.operator || lte.operator_name || lte.operator_act);
}

function usbHostLteSignalText(lte)
{
    if (!lte)
    {
        return '-';
    }

    if (lte.rssi !== undefined && lte.rssi !== null)
    {
        const rawRssi = Math.max(0, Math.min(31, Number(lte.rssi)));
        const percent = Math.round((rawRssi / 31) * 100);

        if (rawRssi >= 25)
        {
            return String(percent) + '% (Excellent)';
        }
        if (rawRssi >= 20)
        {
            return String(percent) + '% (Good)';
        }
        if (rawRssi >= 14)
        {
            return String(percent) + '% (Fair)';
        }
        if (rawRssi >= 8)
        {
            return String(percent) + '% (Poor)';
        }

        return String(percent) + '% (Very poor)';
    }

    if (lte.rssi_dbm !== undefined && lte.rssi_dbm !== null)
    {
        const dbm = Number(lte.rssi_dbm);

        if (dbm >= -65)
        {
            return 'Excellent';
        }
        if (dbm >= -75)
        {
            return 'Good';
        }
        if (dbm >= -85)
        {
            return 'Fair';
        }
        if (dbm >= -95)
        {
            return 'Poor';
        }

        return 'Very poor';
    }

    return '-';
}

function usbHostRenderTelemetry()
{
    const state = usbHostGetState();
    const esp = state.espnetlink || {};
    const lteEl = document.getElementById('usb_host_lte_json');
    const gpsEl = document.getElementById('usb_host_gps_json');
    const lte = esp.lte || null;
    const gps = esp.gps || null;

    usbHostSetText('usb_host_lte_stage', lte ? usbHostTextValue(lte.stage) : '-');
    usbHostSetText('usb_host_lte_operator', usbHostLteOperatorText(lte));
    usbHostSetText('usb_host_lte_signal', usbHostLteSignalText(lte));
    usbHostSetText('usb_host_lte_network', lte ? usbHostTextValue(lte.network_type) : '-');
    usbHostSetText('usb_host_lte_ip', lte ? usbHostTextValue(lte.ip) : '-');

    usbHostSetText('usb_host_gps_fix', usbHostGpsFixText(gps));
    usbHostSetText('usb_host_gps_satellites', gps ? usbHostTextValue(gps.satellites) : '-');
    usbHostSetText('usb_host_gps_sats_in_view', gps ? usbHostTextValue(gps.sats_in_view) : '-');
    usbHostSetText('usb_host_gps_location', usbHostGpsLocationText(gps));
    usbHostSetText('usb_host_gps_speed', usbHostGpsSpeedText(gps));

    if (lteEl)
    {
        lteEl.textContent = usbHostPrettyJsonText(esp.lte, 'No LTE data available yet.');
    }

    if (gpsEl)
    {
        gpsEl.textContent = usbHostPrettyJsonText(esp.gps, 'No GPS data available yet.');
    }
}

function usbHostRenderDevicePanels(status, config)
{
    const resolvedDevice = usbHostResolvedDeviceType(status, config);
    const usbEthernet = (config && config.usb_ethernet) || {};
    const hasLiveDevice = usbHostHasLiveDevice(status);
    const showEspnetlinkSections = hasLiveDevice && resolvedDevice === 'espnetlink';
    const showUsbEthernetSections = hasLiveDevice && resolvedDevice === 'usb_ethernet';

    usbHostSetSectionVisible('usb_host_esp_profile_card', showEspnetlinkSections);
    usbHostSetSectionVisible('usb_host_eth_profile_card', showUsbEthernetSections);
    usbHostSetSectionVisible('usb_host_esp_status_panels', hasLiveDevice && resolvedDevice === 'espnetlink');
    usbHostSetSectionVisible('usb_host_generic_panel', hasLiveDevice && resolvedDevice === 'usb_ethernet');
    usbHostSetText('usb_host_generic_ip_mode', usbHostIpModeLabel(usbEthernet.ip_mode || 'dhcp'));
}

async function refreshUsbHostTab(showToast = false)
{
    const state = usbHostGetState();

    try
    {
        const [status, config] = await Promise.all([
            usbHostFetchJson('/usb_host/status'),
            usbHostFetchJson('/usb_host/config')
        ]);
        const resolvedDevice = usbHostResolvedDeviceType(status, config);

        state.status = status;
        state.config = config;
        state.resolvedDevice = resolvedDevice;

        const shouldFetchEspConfig = resolvedDevice === 'espnetlink' &&
            status &&
            usbHostIsTruthy(status.device_started);

        if (shouldFetchEspConfig)
        {
            const [espConfig, lteData, gpsData] = await Promise.all([
                usbHostFetchJson('/usb_host/espnetlink/config'),
                usbHostFetchJson('/usb_host/espnetlink/lte'),
                usbHostFetchJson('/usb_host/espnetlink/gps')
            ]);

            state.espnetlink = {
                config: espConfig,
                lte: lteData && lteData.data !== undefined ? lteData.data : lteData,
                gps: gpsData && gpsData.data !== undefined ? gpsData.data : gpsData
            };
        }
        else
        {
            state.espnetlink = {
                config: null,
                lte: null,
                gps: null
            };
        }

        usbHostSyncDraftFromConfig();
        usbHostRenderStatus(status);
        usbHostRenderConfig(config);
        usbHostRenderDevicePanels(status, config);
        usbHostRenderTelemetry();

        if (showToast)
        {
            showNotification('USB Host refreshed', 'blue', 2500);
        }
    }
    catch (error)
    {
        console.warn('USB Host refresh failed:', error);
        usbHostSetText('usb_host_status_state', 'Error', 'Error');
        usbHostSetText('usb_host_status_summary', 'USB Host refresh failed', 'USB Host refresh failed');
        usbHostSetText('usb_host_status_last_error', error.message, error.message);
        if (showToast)
        {
            showNotification('USB Host refresh failed: ' + error.message, 'red');
        }
    }
}

function usbHostStartAutoRefresh()
{
    if (window._usbHostTimer)
    {
        return;
    }

    window._usbHostTimer = setInterval(() => {
        const panel = document.getElementById('usb_host_tab');
        if (!panel || panel.style.display !== 'block')
        {
            usbHostStopAutoRefresh();
            return;
        }

        refreshUsbHostTab(false).catch((error) => console.warn('USB Host auto refresh failed:', error));
    }, 5000);
}

function usbHostStopAutoRefresh()
{
    if (!window._usbHostTimer)
    {
        return;
    }

    clearInterval(window._usbHostTimer);
    window._usbHostTimer = null;
}

async function usbHostSubmitConfig()
{
    const state = usbHostGetState();

    if (!state.config)
    {
        return true;
    }

    const draft = usbHostCaptureDraft();
    const config = usbHostCloneConfigState();
    if (!config)
    {
        return true;
    }

    config.enabled = !!draft.enabled;
    config.uplink_priority = draft.uplinkPriority;
    config.espnetlink = config.espnetlink || {};
    config.usb_ethernet = config.usb_ethernet || {};

    config.espnetlink.apn = draft.espnetlink.apn;
    config.espnetlink.ip_mode = draft.espnetlink.ipMode;
    config.espnetlink.static_ip = draft.espnetlink.staticIp;
    config.espnetlink.static_netmask = draft.espnetlink.staticNetmask;
    config.espnetlink.static_gw = draft.espnetlink.staticGw;
    config.espnetlink.management_ip = draft.espnetlink.managementIp;

    config.usb_ethernet.ip_mode = draft.usbEthernet.ipMode;
    config.usb_ethernet.static_ip = draft.usbEthernet.staticIp;
    config.usb_ethernet.static_netmask = draft.usbEthernet.staticNetmask;
    config.usb_ethernet.static_gw = draft.usbEthernet.staticGw;

    if (!config.active_device_type || config.active_device_type === 'none')
    {
        config.active_device_type = state.resolvedDevice !== 'none' ? state.resolvedDevice : 'espnetlink';
    }

    try
    {
        const response = await usbHostFetchJson('/usb_host/config', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify(usbHostBuildConfigPayload(config))
        });

        state.config = response;
        state.dirty = false;
        state.preferSavedApn = true;
        usbHostSyncDraftFromConfig(true);
        await refreshUsbHostTab(false);
        return true;
    }
    catch (error)
    {
        showNotification('Failed to save USB Host settings: ' + error.message, 'red');
        return false;
    }
}

if (document.readyState === 'loading')
{
    document.addEventListener('DOMContentLoaded', usbHostInitializeUi);
}
else
{
    usbHostInitializeUi();
}
