document.addEventListener('DOMContentLoaded', async function() {
    // Initialize SQLite
    const SQL = await initSqlJs({
        locateFile: file => `sql-wasm.wasm`
    });
    
    let dbIndex = null;
    let dbInstance = null;
    let currentDb = null;
    let parameterMap = {};
    let chart = null;
    let loadedDatabases = {}; // Define loadedDatabases here at the top level
    
    // Initialize date range picker
    $('#dateRange').daterangepicker({
        timePicker: true,
        timePicker24Hour: true,
        startDate: moment().subtract(7, 'days'),
        endDate: moment().add(1, 'day'), // Include a day in the future to ensure today's data
        locale: {
            format: 'YYYY-MM-DD HH:mm'
        }
    }, function(start, end) {
        fetchRelevantDatabases(start.toISOString(), end.toISOString());
    });
    
    // Parameter search functionality
    const paramSearch = document.getElementById('paramSearch');
    if (paramSearch) {
        paramSearch.addEventListener('input', function() {
            filterParameters(this.value);
        });
    }

    // Fetch database index first
    await fetchDatabaseIndex();
    
    // Fetch initial relevant databases based on default date range
    const dateRangePicker = $('#dateRange').data('daterangepicker');
    if (dateRangePicker) {
        const startDate = dateRangePicker.startDate.toISOString();
        const endDate = dateRangePicker.endDate.toISOString();
        await fetchRelevantDatabases(startDate, endDate);
    }

    /**
     * Fetch the database index from the server
     */
    async function fetchDatabaseIndex() {
        try {
            const response = await fetch('/obd_logs');
            dbIndex = await response.json();
            
            displayDatabaseInfo();
            return dbIndex;
        } catch (error) {
            console.error('Error fetching database index:', error);
            const dbLoadingElement = document.getElementById('dbLoading');
            if (dbLoadingElement) {
                dbLoadingElement.innerHTML = 
                    `<div class="alert alert-danger">Failed to load database index: ${error.message}</div>`;
            }
        }
    }
    
    /**
     * Display database information
     */
    function displayDatabaseInfo() {
        if (!dbIndex) return;
        
        const dbInfoElement = document.getElementById('databaseInfo');
        if (!dbInfoElement) return;
        
        dbInfoElement.innerHTML = '';
        
        const currentDbInfo = document.createElement('div');
        currentDbInfo.innerHTML = `
            <h6>Current Database: ${dbIndex.current_db}</h6>
            <p>Available Databases: ${dbIndex.databases.length}</p>
        `;
        dbInfoElement.appendChild(currentDbInfo);
        
        const dbLoadingElement = document.getElementById('dbLoading');
        if (dbLoadingElement) {
            dbLoadingElement.style.display = 'none';
        }
    }
    

    /**
     * Fetch databases relevant to the selected date range
     */
    async function fetchRelevantDatabases(startDate, endDate) {
        if (!dbIndex) return;
        
        // Clear previous database data
        parameterMap = {};
        loadedDatabases = {}; // Clear previously loaded databases
        
        // Create an array to track all databases we need to load
        let databasesToLoad = [];
        
        // First, check if current_db exists and add it
        if (dbIndex.current_db) {
            // Check if current_db is already in the databases array
            const currentDbInList = dbIndex.databases.find(db => db.filename === dbIndex.current_db);
            
            if (currentDbInList) {
                databasesToLoad.push(currentDbInList);
            } else {
                // Current DB is not in the list, create a placeholder entry
                console.log("Current DB not found in databases list, adding it manually:", dbIndex.current_db);
                databasesToLoad.push({
                    filename: dbIndex.current_db,
                    created: new Date().toISOString(), // Assume it's recent
                    status: "current"
                });
            }
        }
        
        // Then add all other databases that fall within the date range
        const dateRangeDbs = dbIndex.databases.filter(db => {
            // Skip if it's already in our list (avoid duplicates)
            if (databasesToLoad.some(loadDb => loadDb.filename === db.filename)) {
                return false;
            }
            // Check if it falls within the date range
            return db.created <= endDate && (db.ended >= startDate || !db.ended);
        });
        
        databasesToLoad = databasesToLoad.concat(dateRangeDbs);
        
        if (databasesToLoad.length === 0) {
            alert('No databases found for the selected date range');
            return;
        }
        
        // Sort by creation date (newest first)
        databasesToLoad.sort((a, b) => new Date(b.created) - new Date(a.created));
        
        console.log(`Loading ${databasesToLoad.length} databases for the selected time period:`, 
                    databasesToLoad.map(db => db.filename).join(', '));
        
        // Update UI
        const parameterListElement = document.getElementById('parameterList');
        if (parameterListElement) {
            parameterListElement.innerHTML = `<div class="alert alert-info">Loading ${databasesToLoad.length} database files...</div>`;
        }
        
        // Store combined data from all databases
        window.combinedParameters = {};
        
        // Load each database sequentially and store its data
        for (const db of databasesToLoad) {
            currentDb = db;
            await loadAndStoreDatabaseFile(db.filename);
        }
        
        // After loading all databases, update the parameter list
        updateParameterList();
        
        // Enable the checkboxes for chart display
        const checkboxes = document.querySelectorAll('.param-checkbox');
        checkboxes.forEach(checkbox => {
            checkbox.addEventListener('change', function() {
                updateChart();
            });
        });
    }
    
    /**
     * Load a database file and store its data in memory
     */
    async function loadAndStoreDatabaseFile(filename) {
        try {
            const paramLoadingElement = document.getElementById('paramLoading');
            if (paramLoadingElement) {
                paramLoadingElement.style.display = 'block';
            }
            
            console.log("Loading database file:", filename);
            const response = await fetch(`/obd_logs/${filename}`);
            const arrayBuffer = await response.arrayBuffer();
            
            // Create a new database instance
            const db = new SQL.Database(new Uint8Array(arrayBuffer));
            
            // Store the database instance
            loadedDatabases[filename] = {
                instance: db,
                parameters: {},
                // We'll load parameter data on demand when selected
            };
            
            // Extract parameter metadata
            const paramResults = db.exec("SELECT Id, Name, Type, Data FROM param_info ORDER BY Name");
            
            if (paramResults.length > 0 && paramResults[0].values.length > 0) {
                paramResults[0].values.forEach(param => {
                    const [id, name, type, dataJSON] = param;
                    
                    // Store parameter metadata
                    loadedDatabases[filename].parameters[id] = {
                        name,
                        type,
                        data: dataJSON ? JSON.parse(dataJSON) : {}
                    };
                    
                    // Add to our combined parameters dictionary
                    if (!window.combinedParameters[id]) {
                        window.combinedParameters[id] = { 
                            name, 
                            type, 
                            data: dataJSON ? JSON.parse(dataJSON) : {},
                            databases: [filename]
                        };
                    } else {
                        // Parameter already exists, just add this database to its list
                        if (!window.combinedParameters[id].databases.includes(filename)) {
                            window.combinedParameters[id].databases.push(filename);
                        }
                    }
                });
            }
            
            console.log(`Loaded ${Object.keys(loadedDatabases[filename].parameters).length} parameters from ${filename}`);
            
        } catch (error) {
            console.error('Error loading database file:', error);
            const paramLoadingElement = document.getElementById('paramLoading');
            if (paramLoadingElement) {
                paramLoadingElement.innerHTML = 
                    `<div class="alert alert-danger">Failed to load database: ${error.message}</div>`;
            }
        }
    }
    
    /**
     * Update the parameter list in the UI
     */
    function updateParameterList() {
        const paramListElement = document.getElementById('parameterList');
        const paramLoadingElement = document.getElementById('paramLoading');
        
        if (!paramListElement) return;
        
        if (Object.keys(window.combinedParameters).length > 0) {
            paramListElement.innerHTML = '';
            
            // Sort parameters by name
            const sortedParams = Object.entries(window.combinedParameters)
                .sort((a, b) => a[1].name.localeCompare(b[1].name));
            
            sortedParams.forEach(([id, paramInfo]) => {
                const paramItem = document.createElement('div');
                paramItem.className = 'form-check';
                paramItem.innerHTML = `
                    <input class="form-check-input param-checkbox" type="checkbox" value="${id}" id="param-${id}">
                    <label class="form-check-label" for="param-${id}">
                        ${paramInfo.name} (${paramInfo.type})
                    </label>
                `;
                paramListElement.appendChild(paramItem);
            });
            
            if (paramLoadingElement) {
                paramLoadingElement.style.display = 'none';
            }
        } else {
            paramListElement.innerHTML = 
                '<div class="alert alert-warning">No parameters found in the selected databases</div>';
            if (paramLoadingElement) {
                paramLoadingElement.style.display = 'none';
            }
        }
    }
    
    /**
     * Filter parameters based on search input
     */
    function filterParameters(searchTerm) {
        const paramItems = document.querySelectorAll('#parameterList .form-check');
        searchTerm = searchTerm.toLowerCase();
        
        paramItems.forEach(item => {
            const label = item.querySelector('label');
            if (label) {
                const labelText = label.textContent.toLowerCase();
                if (labelText.includes(searchTerm)) {
                    item.style.display = 'block';
                } else {
                    item.style.display = 'none';
                }
            }
        });
    }
    
    /**
     * Update the chart with selected parameters
     */
/**
 * Update the chart with selected parameters - each parameter gets its own chart
 */
async function updateChart() {
    // Get selected parameters
    const checkboxes = document.querySelectorAll('.param-checkbox:checked');
    const chartContainer = document.getElementById('chart-container');
    
    if (!chartContainer) return;
    
    // Clear existing charts
    chartContainer.innerHTML = '';
    
    if (!checkboxes || checkboxes.length === 0) {
        chartContainer.innerHTML = '<div class="alert alert-info">Select parameters to display</div>';
        
        const chartLegend = document.getElementById('chartLegend');
        if (chartLegend) {
            chartLegend.innerHTML = '';
        }
        
        return;
    }
    
    const selectedParams = Array.from(checkboxes).map(checkbox => parseInt(checkbox.value));
    
    try {
        // Create a chart for each parameter
        for (const paramId of selectedParams) {
            // Create container for this parameter's chart
            const paramChartContainer = document.createElement('div');
            paramChartContainer.className = 'parameter-chart';
            paramChartContainer.style.height = '300px';
            paramChartContainer.style.marginBottom = '30px';
            paramChartContainer.style.position = 'relative';
            
            // Add a title for this chart
            const chartTitle = document.createElement('h5');
            chartTitle.textContent = window.combinedParameters[paramId]?.name || `Parameter ${paramId}`;
            chartTitle.style.textAlign = 'center';
            
            // Create canvas for the chart
            const canvas = document.createElement('canvas');
            
            // Add elements to the container
            paramChartContainer.appendChild(chartTitle);
            paramChartContainer.appendChild(canvas);
            chartContainer.appendChild(paramChartContainer);
            
            // Load data for this parameter
            const data = [];
            const color = getRandomColor(paramId);
            
            // Extract data from all relevant databases that are already loaded
            for (const [filename, dbData] of Object.entries(loadedDatabases)) {
                if (window.combinedParameters[paramId]?.databases.includes(filename)) {
                    const query = "SELECT timestamp, value FROM param_data WHERE param_id = ? AND timestamp > 0 ORDER BY timestamp";
                    
                    try {
                        const stmt = dbData.instance.prepare(query);
                        stmt.bind([paramId]);
                        
                        while (stmt.step()) {
                            const row = stmt.get();
                            const [timestamp, value] = row;
                            
                            // Convert timestamp (seconds) to milliseconds
                            const time = timestamp * 1000;
                            
                            data.push({
                                x: moment(time).toDate(),
                                y: value
                            });
                        }
                    } catch (queryError) {
                        console.error(`Error querying database ${filename} for parameter ${paramId}:`, queryError);
                    }
                }
            }
            
            // Sort data points by time
            data.sort((a, b) => a.x - b.x);
            console.log(`Parameter ${window.combinedParameters[paramId]?.name}: ${data.length} data points`);
            
            // Create chart for this parameter
            new Chart(canvas, {
                type: 'line',
                data: {
                    datasets: [{
                        label: window.combinedParameters[paramId]?.name || `Parameter ${paramId}`,
                        data: data,
                        borderColor: color,
                        backgroundColor: color + '20', // Add transparency for fill
                        fill: true,
                        tension: 0.1,
                        pointRadius: 0
                    }]
                },
                options: {
                    scales: {
                        x: {
                            type: 'time',
                            time: {
                                unit: 'minute',
                                displayFormats: {
                                    millisecond: 'HH:mm:ss.SSS',
                                    second: 'HH:mm:ss',
                                    minute: 'HH:mm',
                                    hour: 'MMM D, HH:mm',
                                    day: 'MMM D, YYYY',
                                },
                                tooltipFormat: 'MMM D, YYYY HH:mm:ss'
                            },
                            title: {
                                display: true,
                                text: 'Time'
                            },
                            ticks: {
                                autoSkip: true,
                                maxRotation: 45,
                                minRotation: 0
                            }
                        },
                        y: {
                            title: {
                                display: true,
                                text: 'Value'
                            },
                            beginAtZero: false
                        }
                    },
                    plugins: {
                        legend: {
                            display: false  // No need for legend with single parameter
                        },
                        tooltip: {
                            mode: 'index',
                            intersect: false
                        }
                    },
                    responsive: true,
                    maintainAspectRatio: false
                }
            });
        }
        
        // No need for separate legend since each chart is labeled
        const chartLegend = document.getElementById('chartLegend');
        if (chartLegend) {
            chartLegend.innerHTML = '';
        }
        
    } catch (error) {
        console.error('Error updating charts:', error);
        if (chartContainer) {
            chartContainer.innerHTML = 
                `<div class="alert alert-danger">Error displaying charts: ${error.message}</div>`;
        }
    }
}
    
    /**
     * Update the chart legend
     */
    function updateChartLegend(dataByParam) {
        const legendElement = document.getElementById('chartLegend');
        if (!legendElement) return;
        
        legendElement.innerHTML = '';
        
        Object.values(dataByParam).forEach(dataset => {
            const legendItem = document.createElement('div');
            legendItem.className = 'legend-item';
            
            const colorBox = document.createElement('span');
            colorBox.className = 'color-box';
            colorBox.style.backgroundColor = dataset.borderColor;
            
            legendItem.appendChild(colorBox);
            legendItem.appendChild(document.createTextNode(dataset.label));
            
            legendElement.appendChild(legendItem);
        });
    }
    
    /**
     * Generate a random color for chart lines
     */
    function getRandomColor() {
        const colors = [
            '#4dc9f6', '#f67019', '#f53794', '#537bc4', '#acc236',
            '#166a8f', '#00a950', '#58595b', '#8549ba', '#e6194b',
            '#3cb44b', '#ffe119', '#4363d8', '#f58231', '#911eb4',
            '#46f0f0', '#f032e6', '#bcf60c', '#fabebe', '#008080'
        ];
        
        // If we have more parameters than colors, generate a random one
        if (Object.keys(parameterMap).length > colors.length) {
            return `#${Math.floor(Math.random()*16777215).toString(16).padStart(6, '0')}`;
        }
        
        // Otherwise, use our predefined colors with good contrast
        return colors[Object.keys(parameterMap).length % colors.length];
    }
    
    /**
     * Download all database data for the selected time range
     */
    async function downloadDatabaseData() {
        if (!dbIndex) return;
        
        const dateRangePicker = $('#dateRange').data('daterangepicker');
        if (!dateRangePicker) return;
        
        const startDate = dateRangePicker.startDate.toISOString();
        const endDate = dateRangePicker.endDate.toISOString();
        
        const relevantDbs = dbIndex.databases.filter(db => {
            return db.created <= endDate && (db.ended >= startDate || !db.ended);
        });
        
        if (relevantDbs.length === 0) {
            alert('No databases found for the selected date range');
            return;
        }
        
        // Create a zip file with all relevant databases
        const zip = new JSZip();
        
        for (const db of relevantDbs) {
            try {
                const response = await fetch(`/obd_logs/${db.filename}`);
                const blob = await response.blob();
                zip.file(db.filename, blob);
            } catch (error) {
                console.error(`Error downloading ${db.filename}:`, error);
            }
        }
        
        // Generate and download the zip file
        const content = await zip.generateAsync({type: 'blob'});
        const link = document.createElement('a');
        link.href = URL.createObjectURL(content);
        link.download = `obd_logs_${moment().format('YYYYMMDD_HHmmss')}.zip`;
        link.click();
    }
    
    /**
     * Combine data from multiple databases
     */
    async function combineMultipleDatabases() {
        const startDate = $('#dateRange').data('daterangepicker').startDate.toISOString();
        const endDate = $('#dateRange').data('daterangepicker').endDate.toISOString();
        
        const relevantDbs = dbIndex.databases.filter(db => {
            return db.created <= endDate && (db.ended >= startDate || !db.ended);
        });
        
        if (relevantDbs.length <= 1) {
            // If only one database or none, just use the current method
            return;
        }
        
        // For multiple databases, we would need to:
        // 1. Load each database
        // 2. Extract data for selected parameters
        // 3. Combine the data
        // 4. Update the chart
        
        // This is a complex operation that would require significant memory
        // and processing. For now, we'll just alert the user about the limitation.
        alert(`Currently displaying data from ${currentDb.filename}. For cross-database analysis, please download the data and use external tools.`);
    }
    
    // Add this function to combine data from multiple databases
    async function loadMultipleDatabases(dbList, limit = 3) {
        // Limit to most recent databases for performance
        const recentDbs = dbList.slice(0, limit);
        console.log(`Loading ${recentDbs.length} most recent databases`);
        
        for (const db of recentDbs) {
            await loadDatabaseFile(db.filename);
            // After loading each database, update the parameter list and chart
        }
    }
    
    /**
     * Add download button after database info is loaded
     */
    function addDownloadButton() {
        const dbInfoElement = document.getElementById('databaseInfo');
        if (!dbInfoElement || !dbIndex) return;
        
        // Check if button already exists
        if (document.getElementById('downloadDbButton')) return;
        
        const downloadButton = document.createElement('button');
        downloadButton.id = 'downloadDbButton';
        downloadButton.className = 'btn btn-sm mt-2'; // Removed color class
        downloadButton.style.backgroundColor = '#24478f';
        downloadButton.style.color = 'white'; // Setting text color to white for better contrast
        downloadButton.textContent = 'Download Selected Database Files';
        downloadButton.addEventListener('click', downloadDatabaseData);
        
        dbInfoElement.appendChild(downloadButton);
    }
    
    // Add the download button after database info is loaded
    setTimeout(addDownloadButton, 1500);
    
    // JSZip is required for the download functionality
    if (!window.JSZip) {
        const script = document.createElement('script');
        script.src = 'jszip.min.js';
        document.head.appendChild(script);
    }
});
