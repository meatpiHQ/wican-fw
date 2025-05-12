// Global variables
let wsConnection  = null;
let realtimeChart = null;
let historyChart = null;
let activeParams = new Set();
let paramData = {};
let paramInfo = {};
let dataHistory = {};
let maxDataPoints = 100;

// At the top of the file, add:
console.log("Dashboard.js loaded");

// Initialize the dashboard
document.addEventListener('DOMContentLoaded', function() {
    console.log("DOM content loaded event fired");
    try {
        console.log("Initializing WebSocket");
        initWebSocket();
        console.log("Setting up charts");
        setupCharts();
        console.log("Setting up event listeners");
        setupEventListeners();
        console.log("Setting up tabs");
        setupTabs();
        console.log("Dashboard initialization complete");
    } catch (error) {
        console.error("Error during dashboard initialization:", error);
    }
});

// Initialize WebSocket connection
function initWebSocket() {
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const wsUrl = `${protocol}//${window.location.host}/obd_logger_ws`;
    
    wsConnection  = new WebSocket(wsUrl);
    
    wsConnection .onopen = function() {
        document.getElementById('connectionStatus').textContent = 'Connected';
        document.getElementById('connectionStatus').className = 'status connected';
        console.log('WebSocket connection established');
        
        // Load parameter information
        fetchParamInfo();
    };
    
    wsConnection .onclose = function() {
        document.getElementById('connectionStatus').textContent = 'Disconnected';
        document.getElementById('connectionStatus').className = 'status disconnected';
        console.log('WebSocket connection closed');
        
        // Try to reconnect after 5 seconds
        setTimeout(initWebSocket, 5000);
    };
    
    wsConnection .onerror = function(error) {
        console.error('WebSocket error:', error);
    };
    
    wsConnection .onmessage = function(event) {
        handleWebSocketMessage(event.data);
    };
}

// Handle incoming WebSocket messages
function handleWebSocketMessage(message) {
    // Check if it's a response to a query
    if (message.startsWith('DateTime|param_id') || 
        message.startsWith('Id|Name') ||
        message.includes('----')) {
        // This is likely a query result
        document.getElementById('queryResult').textContent = message;
        
        // If it's parameter info, process it
        if (message.includes('Id|Name|Type|Data')) {
            processParamInfoResponse(message);
        }
        
        // If it's parameter data, process it for history chart
        if (message.includes('DateTime|value|Name|Type|Data')) {
            processHistoryDataResponse(message);
        }
    } else {
        console.log('Received message:', message);
    }
}

// Process parameter info response
function processParamInfoResponse(response) {
    const lines = response.split('\n');
    if (lines.length < 3) return;
    
    // Skip header and separator lines
    for (let i = 2; i < lines.length; i++) {
        const parts = lines[i].split('|');
        if (parts.length >= 4) {
            const id = parseInt(parts[0]);
            const name = parts[1];
            const type = parts[2];
            let metadata = {};
            
            try {
                metadata = JSON.parse(parts[3] || '{}');
            } catch (e) {
                console.error('Error parsing metadata:', e);
            }
            
            paramInfo[name] = {
                id: id,
                type: type,
                metadata: metadata
            };
            
            // Add to parameter select dropdowns
            addParamToSelects(name);
        }
    }
    
    console.log('Parameter info loaded:', paramInfo);
}

// Process history data response
function processHistoryDataResponse(response) {
    const lines = response.split('\n');
    if (lines.length < 3) return;
    
    const selectedParam = document.getElementById('historyParamSelect').value;
    dataHistory[selectedParam] = [];
    
    // Skip header and separator lines
    for (let i = 2; i < lines.length; i++) {
        const parts = lines[i].split('|');
        if (parts.length >= 3) {
            const timestamp = parts[0];
            const value = parseFloat(parts[1]);
            
            dataHistory[selectedParam].push({
                x: new Date(timestamp),
                y: value
            });
        }
    }
    
    // Update history chart
    updateHistoryChart();
}

// Add parameter to select dropdowns
function addParamToSelects(paramName) {
    const paramSelect = document.getElementById('paramSelect');
    const historyParamSelect = document.getElementById('historyParamSelect');
    
    // Add to realtime select if not already there
    if (!Array.from(paramSelect.options).some(option => option.value === paramName)) {
        const option = document.createElement('option');
        option.value = paramName;
        option.textContent = paramName;
        paramSelect.appendChild(option);
    }
    
    // Add to history select if not already there
    if (!Array.from(historyParamSelect.options).some(option => option.value === paramName)) {
        const option = document.createElement('option');
        option.value = paramName;
        option.textContent = paramName;
        historyParamSelect.appendChild(option);
    }
}

// Fetch parameter information
function fetchParamInfo() {
    console.log("Fetching parameter information");
    if (wsConnection && wsConnection.readyState === WebSocket.OPEN) {
        wsConnection.send('SELECT * FROM param_info;');
    } else {
        console.log("WebSocket not connected, can't fetch parameter info");
    }
}

// Setup Chart.js charts
function setupCharts() {
    // Setup realtime chart
    const realtimeCtx = document.getElementById('realtimeChart').getContext('2d');
    realtimeChart = new Chart(realtimeCtx, {
        type: 'line',
        data: {
            datasets: []
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            scales: {
                x: {
                    type: 'time',
                    time: {
                        unit: 'second',
                        displayFormats: {
                            second: 'HH:mm:ss'
                        }
                    },
                    title: {
                        display: true,
                        text: 'Time'
                    }
                },
                y: {
                    beginAtZero: false,
                    title: {
                        display: true,
                        text: 'Value'
                    }
                }
            },
            plugins: {
                legend: {
                    position: 'top',
                },
                tooltip: {
                    mode: 'index',
                    intersect: false,
                }
            },
            animation: {
                duration: 0
            }
        }
    });
    
    // Setup history chart
    const historyCtx = document.getElementById('historyChart').getContext('2d');
    historyChart = new Chart(historyCtx, {
        type: 'line',
        data: {
            datasets: []
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            scales: {
                x: {
                    type: 'time',
                    time: {
                        unit: 'minute',
                        displayFormats: {
                            minute: 'HH:mm'
                        }
                    },
                    title: {
                        display: true,
                        text: 'Time'
                    }
                },
                y: {
                    beginAtZero: false,
                    title: {
                        display: true,
                        text: 'Value'
                    }
                }
            },
            plugins: {
                legend: {
                    position: 'top',
                },
                tooltip: {
                    mode: 'index',
                    intersect: false,
                }
            }
        }
    });
}

// Setup event listeners
function setupEventListeners() {
    // Add parameter button
    document.getElementById('addParam').addEventListener('click', function() {
        console.log("Add Parameter button clicked");
        const paramSelect = document.getElementById('paramSelect');
        const selectedParam = paramSelect.value;
        console.log("Selected parameter:", selectedParam);
        
        if (selectedParam && !activeParams.has(selectedParam)) {
            console.log("Adding parameter to active set");
            activeParams.add(selectedParam);
            paramData[selectedParam] = [];
            
            // Add to chart
            console.log("Adding parameter to chart");
            addParamToChart(selectedParam);
            
            // Add parameter card to grid
            console.log("Adding parameter card");
            addParamCard(selectedParam);
            
            // Start polling for this parameter
            console.log("Starting to poll for parameter");
            startPollingParam(selectedParam);
        } else {
            console.log("Parameter not added: empty or already active");
        }
    });
    
    // Clear parameters button
    document.getElementById('clearParams').addEventListener('click', function() {
        activeParams.clear();
        paramData = {};
        
        // Clear chart
        realtimeChart.data.datasets = [];
        realtimeChart.update();
        
        // Clear parameter grid
        document.getElementById('paramGrid').innerHTML = '';
    });
    
    // Load history button
    document.getElementById('loadHistory').addEventListener('click', function() {
        const selectedParam = document.getElementById('historyParamSelect').value;
        const startTime = document.getElementById('startTime').value;
        const endTime = document.getElementById('endTime').value;
        
        if (selectedParam) {
            loadHistoryData(selectedParam, startTime, endTime);
        }
    });
    
    // Execute query button
    document.getElementById('executeQuery').addEventListener('click', function() {
        const query = document.getElementById('queryConsole').value;
        if (query.trim()) {
            executeQuery(query);
        }
    });
}

// Setup tabs
function setupTabs() {
    const tabs = document.querySelectorAll('.tab');
    const tabContents = document.querySelectorAll('.tab-content');
    
    tabs.forEach(tab => {
        tab.addEventListener('click', function() {
            const tabName = this.getAttribute('data-tab');
            
            // Remove active class from all tabs and contents
            tabs.forEach(t => t.classList.remove('active'));
            tabContents.forEach(c => c.classList.remove('active'));
            
            // Add active class to clicked tab and corresponding content
            this.classList.add('active');
            document.getElementById(tabName).classList.add('active');
        });
    });
}

// Add parameter to realtime chart
function addParamToChart(paramName) {
    // Generate a random color
    const r = Math.floor(Math.random() * 255);
    const g = Math.floor(Math.random() * 255);
    const b = Math.floor(Math.random() * 255);
    const color = `rgb(${r}, ${g}, ${b})`;
    
    // Create new dataset
    const newDataset = {
        label: paramName,
        data: [],
        borderColor: color,
        backgroundColor: `rgba(${r}, ${g}, ${b}, 0.1)`,
        borderWidth: 2,
        pointRadius: 0,
        lineTension: 0.2,
        fill: false
    };
    
    realtimeChart.data.datasets.push(newDataset);
    realtimeChart.update();
}

// Add parameter card to grid
function addParamCard(paramName) {
    const paramGrid = document.getElementById('paramGrid');
    
    const card = document.createElement('div');
    card.className = 'param-card';
    card.id = `card-${paramName}`;
    
    const nameElem = document.createElement('div');
    nameElem.className = 'param-name';
    nameElem.textContent = paramName;
    
    const valueElem = document.createElement('div');
    valueElem.className = 'param-value';
    valueElem.id = `value-${paramName}`;
    valueElem.textContent = '0';
    
    const unitElem = document.createElement('div');
    unitElem.className = 'param-unit';
    
    // Get unit from metadata if available
    if (paramInfo[paramName] && paramInfo[paramName].metadata && paramInfo[paramName].metadata.unit) {
        unitElem.textContent = paramInfo[paramName].metadata.unit;
    } else {
        unitElem.textContent = '';
    }
    
    card.appendChild(nameElem);
    card.appendChild(valueElem);
    card.appendChild(unitElem);
    
    paramGrid.appendChild(card);
}

// Start polling for parameter data
function startPollingParam(paramName) {
    // Initial query
    queryParamData(paramName);
    
    // Set up interval for continuous polling
    const intervalId = setInterval(() => {
        if (activeParams.has(paramName)) {
            queryParamData(paramName);
        } else {
            clearInterval(intervalId);
        }
    }, 1000); // Poll every second
}

// Query parameter data
function queryParamData(paramName) {
    console.log("Querying data for parameter:", paramName);
    if (wsConnection && wsConnection.readyState === WebSocket.OPEN) {
        const paramId = paramInfo[paramName]?.id;
        console.log("Parameter ID:", paramId);
        if (paramId) {
            // Query the most recent value for this parameter
            const query = `
                SELECT pd.DateTime, pd.value, pi.Name, pi.Type, pi.Data 
                FROM param_data pd 
                JOIN param_info pi ON pd.param_id = pi.Id 
                WHERE pd.param_id = ${paramId} 
                ORDER BY pd.DateTime DESC 
                LIMIT 1;
            `;
            console.log("Sending query:", query);
            wsConnection.send(query);
            
            // Also set up a listener for the response
            const originalOnMessage = wsConnection.onmessage;
            wsConnection.onmessage = function(event) {
                const message = event.data;
                console.log("Received response:", message);
                
                // Check if this is the response to our query
                if (message.includes('DateTime|value|Name|Type|Data')) {
                    const lines = message.split('\n');
                    if (lines.length >= 3) {
                        const parts = lines[2].split('|');
                        if (parts.length >= 3) {
                            const timestamp = new Date(parts[0]);
                            const value = parseFloat(parts[1]);
                            console.log("Parsed data:", timestamp, value);
                            
                            // Update parameter data
                            updateParamData(paramName, timestamp, value);
                        }
                    }
                }
                
                // Restore original message handler
                wsConnection.onmessage = originalOnMessage;
                // Also process this message with the original handler
                originalOnMessage(event);
            };
        }
    } else {
        console.log("WebSocket not connected or parameter ID not found");
    }
}

// Update parameter data
function updateParamData(paramName, timestamp, value) {
    // Add to data array
    if (!paramData[paramName]) {
        paramData[paramName] = [];
    }
    
    paramData[paramName].push({
        x: timestamp,
        y: value
    });
    
    // Limit array size
    if (paramData[paramName].length > maxDataPoints) {
        paramData[paramName].shift();
    }
    
    // Update chart
    updateRealtimeChart();
    
    // Update parameter card
    const valueElem = document.getElementById(`value-${paramName}`);
    if (valueElem) {
        valueElem.textContent = value.toFixed(2);
    }
}

// Update realtime chart
function updateRealtimeChart() {
    // Update each dataset
    realtimeChart.data.datasets.forEach((dataset, index) => {
        const paramName = dataset.label;
        if (paramData[paramName]) {
            dataset.data = paramData[paramName];
        }
    });
    
    realtimeChart.update();
}

// Load history data
function loadHistoryData(paramName, startTime, endTime) {
    if (wsConnection  && wsConnection .readyState === WebSocket.OPEN) {
        const paramId = paramInfo[paramName]?.id;
        if (paramId) {
            let query = `
                SELECT pd.DateTime, pd.value, pi.Name, pi.Type, pi.Data 
                FROM param_data pd 
                JOIN param_info pi ON pd.param_id = pi.Id 
                WHERE pd.param_id = ${paramId} 
            `;
            
            // Add time filters if provided
            if (startTime) {
                query += ` AND pd.DateTime >= '${startTime.replace('T', ' ')}'`;
            }
            
            if (endTime) {
                query += ` AND pd.DateTime <= '${endTime.replace('T', ' ')}'`;
            }
            
            query += ` ORDER BY pd.DateTime ASC LIMIT 1000;`;
            
            wsConnection .send(query);
        }
    }
}

// Update history chart
function updateHistoryChart() {
    const selectedParam = document.getElementById('historyParamSelect').value;
    
    // Clear existing datasets
    historyChart.data.datasets = [];
    
    if (dataHistory[selectedParam] && dataHistory[selectedParam].length > 0) {
        // Generate a random color
        const r = Math.floor(Math.random() * 255);
        const g = Math.floor(Math.random() * 255);
        const b = Math.floor(Math.random() * 255);
        const color = `rgb(${r}, ${g}, ${b})`;
        
        // Create new dataset
        const newDataset = {
            label: selectedParam,
            data: dataHistory[selectedParam],
            borderColor: color,
            backgroundColor: `rgba(${r}, ${g}, ${b}, 0.1)`,
            borderWidth: 2,
            pointRadius: 1,
            lineTension: 0.2,
            fill: false
        };
        
        historyChart.data.datasets.push(newDataset);
    }
    
    historyChart.update();
}

// Execute custom SQL query
function executeQuery(query) {
    if (wsConnection  && wsConnection .readyState === WebSocket.OPEN) {
        document.getElementById('queryResult').textContent = 'Executing query...';
        wsConnection .send(query);
    } else {
        document.getElementById('queryResult').textContent = 'WebSocket not connected';
    }
}

// Helper function to format date for SQL
function formatDateForSQL(date) {
    return date.toISOString().replace('T', ' ').substring(0, 19);
}

// Helper function to get a random color
function getRandomColor() {
    const letters = '0123456789ABCDEF';
    let color = '#';
    for (let i = 0; i < 6; i++) {
        color += letters[Math.floor(Math.random() * 16)];
    }
    return color;
}

// Periodically check connection status and reconnect if needed
setInterval(() => {
    if (!wsConnection  || wsConnection .readyState === WebSocket.CLOSED) {
        console.log('WebSocket disconnected, attempting to reconnect...');
        initWebSocket();
    }
}, 5000);
