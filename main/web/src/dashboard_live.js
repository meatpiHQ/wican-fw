let autoRefreshInterval;
let isConnected = false;
let parameterInfo = {};

const iconMapping = {
    'temperature': 'thermometer',
    'pressure': 'gauge',
    'speed': 'gauge',
    'power': 'zap',
    'battery': 'battery',
    'voltage': 'zap',
    'current': 'trending-up',
    'distance': 'route',
    'volume_storage': 'fuel',
    'power_factor': 'bar-chart-3',
    'fluid': 'droplets',
    'air': 'wind',
    'rotation': 'disc',
    'gear': 'settings',
    'tire': 'circle',
    'none': 'bar-chart-3',
    'degC': 'thermometer',
    '°C': 'thermometer',
    'kPa': 'gauge',
    'psi': 'gauge',
    'rpm': 'disc',
    '%': 'percent',
    'V': 'zap',
    'A': 'trending-up',
    'W': 'power',
    'kW': 'zap',
    'kwh': 'battery',
    'kWh': 'battery',
    'km': 'route',
    'km/h': 'gauge',
    'mph': 'gauge',
    'g/s': 'wind',
    'L': 'fuel',
    'gal': 'fuel',
    'bar': 'gauge',
    'Encoded': 'hash',
    'none': 'bar-chart-3'
};

const parameterSections = {
    'Battery & Charging': {
        icon: 'battery',
        keywords: ['SOC', 'SOH', 'HV_V', 'HV_A', 'HV_W', 'RANGE', 'KWH_CHARGED', 'CHARGING', 'CHARGER_CONNECTED', 'CHARGING_DC', 'AC_C_C', 'AC_C_V', 'LV_V', 'BATT_CAPACITY', 'HV_CAPACITY'],
        classes: ['battery', 'voltage', 'current', 'power'],
        units: ['V', 'A', 'W', 'kW', 'kWh', 'kwh', '%']
    },
    'Vehicle Status': {
        icon: 'car',
        keywords: ['SPEED', 'ODOMETER', 'GEAR', 'THROTTLE', 'READY', 'PARK_BRAKE', 'FUEL', 'TANK', 'LEVEL', 'AC_P'],
        classes: ['speed', 'distance', 'gear', 'volume_storage'],
        units: ['km/h', 'mph', 'km', 'L', 'gal', '%']
    },
    'Engine Parameters': {
        icon: 'settings',
        keywords: ['ENGINE_RPM', 'ENGINE_OIL_PRES', 'ENGINE_OIL_TEMP', 'PCM_Oil_Life', 'OILCH_DIS', 'FUEL_PRESSURE', 'PCM_Fuel_Rate', 'MAF', 'STFT', 'PCM_Knock_Count', 'PCM_Desired_Boost', 'PCM_Wastegate', 'PCM_Gear', 'PCM_Transmission_Temp', 'PCM_Learned_Octane_Ratio'],
        classes: ['rotation', 'pressure', 'fluid', 'air'],
        units: ['rpm', 'kPa', 'psi', 'bar', 'g/s']
    },
    'Temperatures': {
        icon: 'thermometer',
        keywords: ['BATT_TEMP', 'HV_T_A', 'HV_T_MAX', 'HV_T_MIN', 'HV_T_I', 'HV_T_1', 'HV_T_2', 'HV_T_3', 'HV_T_4', 'HV_T_5', 'COOLANT_TMP', 'INTAKE_AIR_TMP', 'T_CAB', 'TMP_A'],
        classes: ['temperature'],
        units: ['degC', '°C']
    },
    'Tire Pressures': {
        icon: 'circle',
        keywords: ['TYRE_P_FL', 'TYRE_P_FR', 'TYRE_P_RL', 'TYRE_P_RR'],
        classes: ['tire', 'pressure'],
        units: ['kPa', 'psi', 'bar']
    },
    'Battery Cell Analysis': {
        icon: 'battery',
        keywords: ['HV_C_V_MAX', 'HV_C_V_MAX_NO', 'HV_C_V_MIN', 'HV_C_V_MIN_NO', 'HV_C_D_MAX', 'HV_C_D_MAX_NO', 'HV_C_D_MIN', 'HV_C_D_MIN_NO'],
        classes: ['battery'],
        units: ['V']
    },
    'Power & Performance': {
        icon: 'zap',
        keywords: ['POWER_MAX', 'REGEN_MAX', 'HV_AV'],
        classes: ['power', 'power_factor'],
        units: ['W', 'kW', '%']
    }
};

document.addEventListener('DOMContentLoaded', function() {
    lucide.createIcons();
    loadParameterInfo();
    fetchLiveData();
    startAutoRefresh();
    
// Ensure jQuery and dependencies are loaded
$(document).ready(function() {
    // Initialize date range picker when jQuery is ready
    setTimeout(function() {
        if ($.fn.daterangepicker) {
            $('#dateRange').daterangepicker({
                startDate: moment().subtract(7, 'days'),
                endDate: moment(),
                ranges: {
                    'Today': [moment(), moment()],
                    'Yesterday': [moment().subtract(1, 'days'), moment().subtract(1, 'days')],
                    'Last 7 Days': [moment().subtract(6, 'days'), moment()],
                    'Last 30 Days': [moment().subtract(29, 'days'), moment()],
                    'This Month': [moment().startOf('month'), moment().endOf('month')],
                    'Last Month': [moment().subtract(1, 'month').startOf('month'), moment().subtract(1, 'month').endOf('month')]
                },
                locale: {
                    format: 'MM/DD/YYYY',
                    separator: ' - ',
                    applyLabel: 'Apply',
                    cancelLabel: 'Cancel',
                    fromLabel: 'From',
                    toLabel: 'To',
                    customRangeLabel: 'Custom',
                    weekLabel: 'W',
                    daysOfWeek: ['Su', 'Mo', 'Tu', 'We', 'Th', 'Fr', 'Sa'],
                    monthNames: ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December']
                },
                autoApply: false,
                alwaysShowCalendars: true,
                showDropdowns: true,
                linkedCalendars: false,
                opens: 'right',
                drops: 'down',
                buttonClasses: 'btn btn-sm',
                applyButtonClasses: 'btn-primary',
                cancelClass: 'btn-secondary'
            });
            
            // Set initial display value
            $('#dateRange').val(moment().subtract(7, 'days').format('MM/DD/YYYY') + ' - ' + moment().format('MM/DD/YYYY'));
            
            // Date range change handler
            $('#dateRange').on('apply.daterangepicker', function(ev, picker) {
                console.log('Date range selected:', picker.startDate.format('YYYY-MM-DD'), 'to', picker.endDate.format('YYYY-MM-DD'));
                $(this).val(picker.startDate.format('MM/DD/YYYY') + ' - ' + picker.endDate.format('MM/DD/YYYY'));
                
                // Trigger data reload with new date range
                if (typeof loadLoggerData === 'function') {
                    loadLoggerData(picker.startDate, picker.endDate);
                }
            });
            
            // Cancel handler
            $('#dateRange').on('cancel.daterangepicker', function(ev, picker) {
                // Keep the previous value
            });
            
            // Make calendar icon clickable
            $('.input-group-text').on('click', function() {
                $('#dateRange').click();
            });
            
            // Recreate icons for the calendar icon in the input group
            lucide.createIcons();
        } else {
            console.error('DateRangePicker plugin not loaded');
        }
    }, 100); // Small delay to ensure all scripts are loaded
});
});

document.getElementById('live-data-tab').addEventListener('shown.bs.tab', function () {
    fetchLiveData();
    startAutoRefresh();
});

document.getElementById('logger-data-tab').addEventListener('shown.bs.tab', function () {
    stopAutoRefresh();
    if (typeof initializeLoggerDashboard === 'function') {
        initializeLoggerDashboard();
    } else {
        // Initialize logger dashboard components if the function doesn't exist
        console.log('Initializing logger dashboard...');
        // The daterangepicker is already initialized in DOMContentLoaded
        // You can add additional logger initialization here if needed
    }
});

document.getElementById('autoRefreshToggle').addEventListener('change', function() {
    if (this.checked) {
        startAutoRefresh();
    } else {
        stopAutoRefresh();
    }
});

async function loadParameterInfo() {
    try {
        // Load standard PID info
        console.log('Loading standard PID info...');
        try {
            const stdPidResponse = await fetch('/std_pid_info');
            if (stdPidResponse.ok) {
                const stdPids = await stdPidResponse.json();
                if (Array.isArray(stdPids)) {
                    stdPids.forEach(param => {
                        if (param && param.name) {
                            parameterInfo[param.name] = {
                                unit: param.unit || '',
                                class: param.class || 'none'
                            };
                        }
                    });
                    console.log('Standard PID info loaded:', Object.keys(parameterInfo).length, 'parameters');
                }
            }
        } catch (stdError) {
            console.warn('Failed to load standard PID info:', stdError.message);
        }
        
        // Load auto PID info with JSON repair attempt
        console.log('Loading auto PID info...');
        try {
            const autoPidResponse = await fetch('/load_auto_pid_car_data');
            if (autoPidResponse.ok) {
                let responseText = await autoPidResponse.text();
                console.log('Auto PID response length:', responseText.length);
                
                // Parse JSON directly without repair attempts
                try {
                    const autoPidData = JSON.parse(responseText);
                    processAutoPidData(autoPidData);
                } catch (jsonError) {
                    console.warn('JSON parse failed:', jsonError.message);
                    throw new Error('Failed to parse Auto PID data');
                }
            }
        } catch (autoError) {
            console.warn('Failed to load auto PID info:', autoError.message);
        }
        
    } catch (error) {
        console.error('Error in loadParameterInfo:', error);
    }
    
    // Ensure we have some basic parameter info even if loading fails
    if (Object.keys(parameterInfo).length === 0) {
        console.log('Using fallback parameter info');
        addFallbackParameterInfo();
    }
    
    console.log('Total parameters loaded:', Object.keys(parameterInfo).length);
}

function processAutoPidData(autoPidData) {
    if (autoPidData && autoPidData.cars && Array.isArray(autoPidData.cars)) {
        autoPidData.cars.forEach(car => {
            if (car.pids && Array.isArray(car.pids)) {
                car.pids.forEach(pid => {
                    if (pid.parameters && Array.isArray(pid.parameters)) {
                        pid.parameters.forEach(param => {
                            if (param && param.name) {
                                parameterInfo[param.name] = {
                                    unit: param.unit || '',
                                    class: param.class || 'none'
                                };
                            }
                        });
                    }
                });
            }
        });
        console.log('Auto PID info processed successfully');
    }
}

function addFallbackParameterInfo() {
    const fallbackParams = {
        // Battery & EV parameters
        'SOC': { unit: '%', class: 'battery' },
        'SOH': { unit: '%', class: 'battery' },
        'HV_V': { unit: 'V', class: 'voltage' },
        'HV_A': { unit: 'A', class: 'current' },
        'HV_W': { unit: 'W', class: 'power' },
        'RANGE': { unit: 'km', class: 'distance' },
        'CHARGING': { unit: 'none', class: 'none' },
        'CHARGER_CONNECTED': { unit: 'none', class: 'none' },
        
        // Vehicle parameters  
        'SPEED': { unit: 'km/h', class: 'speed' },
        'ENGINE_RPM': { unit: 'rpm', class: 'rotation' },
        'ODOMETER': { unit: 'km', class: 'distance' },
        'GEAR': { unit: 'none', class: 'gear' },
        'THROTTLE': { unit: '%', class: 'none' },
        'READY': { unit: 'none', class: 'none' },
        'PARK_BRAKE': { unit: 'none', class: 'none' },
        'FUEL': { unit: '%', class: 'volume_storage' },
        
        // Temperature parameters
        'BATT_TEMP': { unit: '°C', class: 'temperature' },
        'COOLANT_TMP': { unit: '°C', class: 'temperature' },
        'INTAKE_AIR_TMP': { unit: '°C', class: 'temperature' },
        
        // Pressure parameters
        'ENGINE_OIL_PRES': { unit: 'kPa', class: 'pressure' },
        'FUEL_PRESSURE': { unit: 'kPa', class: 'pressure' },
        'TYRE_P_FL': { unit: 'kPa', class: 'pressure' },
        'TYRE_P_FR': { unit: 'kPa', class: 'pressure' },
        'TYRE_P_RL': { unit: 'kPa', class: 'pressure' },
        'TYRE_P_RR': { unit: 'kPa', class: 'pressure' }
    };
    
    Object.assign(parameterInfo, fallbackParams);
}

function getParameterIcon(paramName) {
    const cleanParamName = paramName.replace(/^\w+-/, '');
    const info = parameterInfo[paramName] || parameterInfo[cleanParamName];
    
    if (info) {
        if (info.class && iconMapping[info.class]) {
            return iconMapping[info.class];
        }
        if (info.unit && iconMapping[info.unit]) {
            return iconMapping[info.unit];
        }
    }
    
    return iconMapping['none'];
}

function getParameterUnit(paramName) {
    const cleanParamName = paramName.replace(/^\w+-/, '');
    const info = parameterInfo[paramName] || parameterInfo[cleanParamName];
    return info ? info.unit : '';
}

function getBinaryStatus(value) {
    if (value === true || value === 1 || value === '1') return 'active';
    if (value === false || value === 0 || value === '0') return 'inactive';
    return 'unknown';
}

function isBinaryParameter(paramName, value) {
    const binaryKeywords = ['CHARGING', 'CONNECTED', 'READY', 'BRAKE', 'DC'];
    const numericKeywords = ['SPEED', 'RPM', 'TEMP', 'PRESSURE', 'VOLTAGE', 'CURRENT', 'POWER', 'SOC', 'RANGE', 'ODOMETER', 'THROTTLE', 'FUEL', 'GEAR'];
    
    if (numericKeywords.some(keyword => paramName.toUpperCase().includes(keyword))) {
        return false;
    }
    
    if (binaryKeywords.some(keyword => paramName.toUpperCase().includes(keyword))) {
        return true;
    }
    
    if (typeof value === 'boolean') {
        return true;
    }
    
    if (typeof value === 'number' && (value === 0 || value === 1)) {
        const info = parameterInfo[paramName] || parameterInfo[paramName.replace(/^\w+-/, '')];
        if (info && info.unit && info.unit !== 'none' && info.unit !== '') {
            return false;
        }
        return true;
    }
    
    return false;
}

function organizeParameters(data) {
    const sections = {};
    const unassigned = {};
    
    Object.keys(parameterSections).forEach(sectionName => {
        sections[sectionName] = {};
    });
    
    Object.entries(data).forEach(([key, value]) => {
        let assigned = false;
        const cleanParamName = key.replace(/^\w+-/, '');
        const info = parameterInfo[key] || parameterInfo[cleanParamName];
        
        // Special handling for fuel-related parameters
        if (key.toLowerCase().includes('fuel') || key.toLowerCase().includes('tank') || key.toLowerCase().includes('level')) {
            if (key.toLowerCase().includes('fuel') || key.toLowerCase().includes('tank')) {
                sections['Vehicle Status'][key] = value;
                assigned = true;
            }
        }
        
        if (!assigned) {
            for (const [sectionName, sectionConfig] of Object.entries(parameterSections)) {
                if (sectionConfig.keywords.some(keyword => key.toUpperCase().includes(keyword.toUpperCase()))) {
                    sections[sectionName][key] = value;
                    assigned = true;
                    break;
                }
                
                if (!assigned && info) {
                    if (info.class && sectionConfig.classes && sectionConfig.classes.includes(info.class)) {
                        sections[sectionName][key] = value;
                        assigned = true;
                        break;
                    }
                    
                    if (!assigned && info.unit && sectionConfig.units && sectionConfig.units.includes(info.unit)) {
                        // Skip % unit matching for Battery & Charging if it's a fuel parameter
                        if (sectionName === 'Battery & Charging' && info.unit === '%' && 
                            (key.toLowerCase().includes('fuel') || key.toLowerCase().includes('tank'))) {
                            continue;
                        }
                        
                        if (sectionName === 'Tire Pressures') {
                            if (key.toLowerCase().includes('tyre') || key.toLowerCase().includes('tire')) {
                                sections[sectionName][key] = value;
                                assigned = true;
                                break;
                            }
                        } else {
                            sections[sectionName][key] = value;
                            assigned = true;
                            break;
                        }
                    }
                }
            }
        }
        
        if (!assigned) {
            unassigned[key] = value;
        }
    });
    
    if (Object.keys(unassigned).length > 0) {
        sections['Other Parameters'] = unassigned;
        parameterSections['Other Parameters'] = { icon: 'bar-chart-3', keywords: [], classes: [], units: [] };
    }
    
    Object.keys(sections).forEach(sectionName => {
        if (Object.keys(sections[sectionName]).length === 0) {
            delete sections[sectionName];
        }
    });
    
    return sections;
}

function fetchLiveData() {
    const refreshIcon = document.getElementById('refreshIcon');
    
    fetch('/autopid_data')
        .then(response => {
            if (!response.ok) {
                throw new Error(`HTTP error! status: ${response.status}`);
            }
            return response.json();
        })
        .then(data => {
            updateConnectionStatus(true);
            displayLiveData(data);
            updateLastUpdateTime();
        })
        .catch(error => {
            console.error('Error fetching live data:', error);
            updateConnectionStatus(false);
            showNoDataMessage();
        })
        .finally(() => {
            document.getElementById('liveDataLoading').style.display = 'none';
        });
}

function displayLiveData(data) {
    const container = document.getElementById('liveDataContainer');
    const noDataMsg = document.getElementById('noDataMessage');
    
    if (!data || Object.keys(data).length === 0) {
        showNoDataMessage();
        return;
    }
    
    noDataMsg.style.display = 'none';
    
    // First time or after connection loss, build the entire structure
    if (container.innerHTML === '' || container.style.display === 'none') {
        container.style.display = 'block';
        const organizedData = organizeParameters(data);
        
        Object.entries(organizedData).forEach(([sectionName, sectionData], sectionIndex) => {
            const sectionElement = createSection(sectionName, sectionData, sectionIndex);
            container.appendChild(sectionElement);
        });
        
        lucide.createIcons();
    } else {
        // Just update values for existing elements
        updateMetricValues(data);
    }
}

function createSection(sectionName, sectionData, sectionIndex) {
    const section = document.createElement('div');
    section.className = 'section-card';
    section.style.animationDelay = `${sectionIndex * 0.05}s`;
    
    const sectionConfig = parameterSections[sectionName] || { icon: 'bar-chart-3' };
    
    section.innerHTML = `
        <div class="section-header">
            <i data-lucide="${sectionConfig.icon}" class="section-icon"></i>
            <h3 class="section-title">${sectionName}</h3>
        </div>
        <div class="metrics-grid" id="section-${sectionName.replace(/\s+/g, '-')}">
        </div>
    `;
    
    const metricsGrid = section.querySelector('.metrics-grid');
    
    Object.entries(sectionData).forEach(([key, value], index) => {
        const card = createMetricCard(key, value, index);
        metricsGrid.appendChild(card);
    });
    
    return section;
}

function updateMetricValues(data) {
    Object.entries(data).forEach(([paramName, paramValue]) => {
        // Find the element for this parameter
        const elements = document.querySelectorAll(`.metric-card[data-param="${paramName}"]`);
        
        if (elements.length > 0) {
            elements.forEach(card => {
                const isBinary = isBinaryParameter(paramName, paramValue);
                
                if (isBinary) {
                    const status = getBinaryStatus(paramValue);
                    const statusIndicator = card.querySelector('.status-indicator');
                    if (statusIndicator) {
                        statusIndicator.className = `status-indicator status-${status}`;
                        const statusIcon = statusIndicator.querySelector('.status-icon');
                        const statusText = statusIndicator.querySelector('.status-text');
                        
                        if (statusIcon) {
                            // Update icon without recreating
                            const newIconName = status === 'active' ? 'check-circle' : 
                                            status === 'inactive' ? 'x-circle' : 'alert-triangle';
                            
                            if (statusIcon.getAttribute('icon') !== newIconName) {
                                statusIcon.setAttribute('icon', newIconName);
                                lucide.replace(statusIcon);
                            }
                        }
                        
                        if (statusText) {
                            statusText.textContent = status.charAt(0).toUpperCase() + status.slice(1);
                        }
                    }
                } else {
                    const valueElement = card.querySelector('.metric-value');
                    if (valueElement) {
                        // Keep the unit, just update the value text
                        const unitElement = valueElement.querySelector('.metric-unit');
                        const unitHtml = unitElement ? unitElement.outerHTML : '';
                        valueElement.innerHTML = paramValue + (unitHtml || '');
                    }
                }
            });
        }
    });
}

function createMetricCard(paramName, paramValue, index) {
    const card = document.createElement('div');
    card.className = 'metric-card';
    card.style.animationDelay = `${index * 0.02}s`;
    card.setAttribute('data-param', paramName); // Add data attribute for easy lookup
    
    const displayName = paramName.replace(/^\w+-/, '').replace(/([A-Z])/g, ' $1').trim();
    const iconName = getParameterIcon(paramName);
    const unit = getParameterUnit(paramName);
    const isBinary = isBinaryParameter(paramName, paramValue);
    
    if (isBinary) {
        const status = getBinaryStatus(paramValue);
        card.innerHTML = `
            <div class="metric-header">
                <p class="metric-title">${displayName}</p>
                <i data-lucide="${iconName}" class="metric-icon"></i>
            </div>
            <div class="status-indicator status-${status}">
                <i data-lucide="${status === 'active' ? 'check-circle' : status === 'inactive' ? 'x-circle' : 'alert-triangle'}" class="status-icon"></i>
                <span class="status-text">${status.charAt(0).toUpperCase() + status.slice(1)}</span>
            </div>
        `;
    } else {
        card.innerHTML = `
            <div class="metric-header">
                <p class="metric-title">${displayName}</p>
                <i data-lucide="${iconName}" class="metric-icon"></i>
            </div>
            <p class="metric-value">
                ${paramValue}
                ${unit ? `<span class="metric-unit">${unit}</span>` : ''}
            </p>
        `;
    }
    
    return card;
}

function updateConnectionStatus(connected) {
    isConnected = connected;
    const connectionDot = document.getElementById('connectionDot');
    const connectionText = document.getElementById('connectionText');
    
    if (connected) {
        connectionDot.className = 'connection-dot connection-connected';
        connectionText.textContent = 'Connected';
    } else {
        connectionDot.className = 'connection-dot connection-disconnected';
        connectionText.textContent = 'Disconnected';
    }
}

function showNoDataMessage() {
    document.getElementById('liveDataContainer').style.display = 'none';
    document.getElementById('noDataMessage').style.display = 'block';
}

function updateLastUpdateTime() {
    const now = new Date();
    document.getElementById('lastUpdate').textContent = 
        `Last updated: ${now.toLocaleTimeString()}`;
}

function startAutoRefresh() {
    if (autoRefreshInterval) {
        clearInterval(autoRefreshInterval);
    }
    
    if (document.getElementById('autoRefreshToggle').checked) {
        autoRefreshInterval = setInterval(() => {
            if (document.getElementById('live-data').classList.contains('active')) {
                fetchLiveData();
            }
        }, 5000);
    }
}

function stopAutoRefresh() {
    if (autoRefreshInterval) {
        clearInterval(autoRefreshInterval);
        autoRefreshInterval = null;
    }
}

window.addEventListener('unhandledrejection', function(event) {
    console.error('Unhandled promise rejection:', event.reason);
    updateConnectionStatus(false);
});

let performanceMetrics = {
    lastFetchTime: 0,
    averageFetchTime: 0,
    fetchCount: 0
};

function trackPerformance(startTime) {
    const fetchTime = Date.now() - startTime;
    performanceMetrics.fetchCount++;
    performanceMetrics.lastFetchTime = fetchTime;
    performanceMetrics.averageFetchTime = 
        (performanceMetrics.averageFetchTime * (performanceMetrics.fetchCount - 1) + fetchTime) / performanceMetrics.fetchCount;
    
    if (fetchTime > 2000) {
        console.warn(`Slow fetch detected: ${fetchTime}ms`);
    }
}

const originalFetch = window.fetch;
window.fetch = function(...args) {
    const startTime = Date.now();
    return originalFetch.apply(this, args).then(response => {
        trackPerformance(startTime);
        return response;
    });
};

setInterval(() => {
    if (performanceMetrics.fetchCount > 0) {
        const connectionText = document.getElementById('connectionText');
        if (isConnected) {
            connectionText.textContent = `Connected (${performanceMetrics.lastFetchTime}ms)`;
        }
    }
}, 1000);

window.addEventListener('beforeunload', function() {
    stopAutoRefresh();
});

document.addEventListener('visibilitychange', function() {
    if (document.hidden) {
        stopAutoRefresh();
    } else if (document.getElementById('autoRefreshToggle').checked) {
        startAutoRefresh();
    }
});

function handleMobileLayout() {
    const isMobile = window.innerWidth < 768;
    const metricsGrids = document.querySelectorAll('.metrics-grid');
    
    metricsGrids.forEach(grid => {
        if (isMobile) {
            grid.style.gridTemplateColumns = '1fr';
        } else {
            grid.style.gridTemplateColumns = 'repeat(auto-fill, minmax(260px, 1fr))';
        }
    });
}

window.addEventListener('resize', handleMobileLayout);

document.addEventListener('DOMContentLoaded', function() {
    setTimeout(handleMobileLayout, 100);
});

// Add CSS for spinning animation
const style = document.createElement('style');
style.textContent = `
    @keyframes spin {
        from { transform: rotate(0deg); }
        to { transform: rotate(360deg); }
    }
    .spin {
        animation: spin 1s linear infinite;
    }
`;
document.head.appendChild(style);
