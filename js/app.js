// Initialization and Core State Allocation
localforage.config({ name: 'ForensicStudio', storeName: 'analytics_cache' });

let masterForensicDatabase = {};
let userPinnedWatchlist = ['INFY', 'RELIANCE', 'TCS']; // Default initial configuration array
let activePriceStream = null;
let chartInstance = null;
let candleSeries = null;

let schedulerIntervalId = null;
let countdownIntervalId = null;
let currentCountdownSeconds = 300;

document.addEventListener('DOMContentLoaded', () => {
    setupAuthObserver();
});

// App Router Architecture Handling Transitions Contextually
function switchScreen(target) {
    const dash = document.getElementById('screen-dashboard');
    const term = document.getElementById('screen-terminal');
    const btnDash = document.getElementById('nav-dashboard');
    const btnTerm = document.getElementById('nav-terminal');

    if (target === 'dashboard') {
        dash.classList.remove('hidden');
        dash.classList.add('z-10');
        term.classList.add('hidden');
        term.classList.remove('z-10');
        
        btnDash.className = "px-3 py-1 rounded text-xs font-medium transition-all text-zinc-100 bg-kite-border shadow-sm";
        btnTerm.className = "px-3 py-1 rounded text-xs font-medium transition-all text-zinc-400 hover:text-zinc-200";
    } else {
        term.classList.remove('hidden');
        term.classList.add('z-10');
        dash.classList.add('hidden');
        dash.classList.remove('z-10');

        btnTerm.className = "px-3 py-1 rounded text-xs font-medium transition-all text-zinc-100 bg-kite-border shadow-sm";
        btnDash.className = "px-3 py-1 rounded text-xs font-medium transition-all text-zinc-400 hover:text-zinc-200";
    }
}

// Security Authentication Pipeline Observer Setup
function setupAuthObserver() {
    const authForm = document.getElementById('auth-form');
    authForm.addEventListener('submit', (e) => {
        e.preventDefault();
        const username = document.getElementById('auth-username').value.trim();
        const secureKey = prompt("Enter Master Gateway Authentication Key:");

        if (secureKey === "admin") {
            document.getElementById('auth-status').className = "text-center text-xs mt-4 text-kite-green font-semibold";
            document.getElementById('auth-status').innerText = "ACCESS GRANTED. INITIALIZING TELMETRY SYSTEMS...";
            
            setTimeout(() => {
                document.getElementById('system-lock-overlay').classList.add('hidden');
                document.getElementById('logout-btn').innerText = username.substring(0,2).toUpperCase();
                initDataPipeline();
            }, 800);
        } else {
            document.getElementById('auth-status').className = "text-center text-xs mt-4 text-kite-red font-semibold";
            document.getElementById('auth-status').innerText = "INVALID PIPELINE ACCESS SIGNATURE.";
        }
    });
}

// Data Storage Caching Operations
async function initDataPipeline() {
    try {
        const response = await fetch(`master_forensic_db.json?t=${Date.now()}`);
        if (!response.ok) throw new Error("Server transmission error.");
        masterForensicDatabase = await response.json();
        await localforage.setItem('cached_forensic_db', masterForensicDatabase);
    } catch (err) {
        console.warn("Pipeline link broken. Attempting IndexedDB extraction...", err);
        masterForensicDatabase = await localforage.getItem('cached_forensic_db');
        if (!masterForensicDatabase) {
            masterForensicDatabase = getFallbackDevelopmentDatabase();
        }
    }
    renderViews();
    reinitializeSchedulerLoop();
    setupAutocompleteEngine();
    setupGlobalNavigationHooks();
}

function setupGlobalNavigationHooks() {
    document.getElementById('nav-dashboard').addEventListener('click', () => switchScreen('dashboard'));
    document.getElementById('nav-terminal').addEventListener('click', () => switchScreen('terminal'));
    document.getElementById('force-sync-btn').addEventListener('click', () => executeBackgroundGitFetch());
    document.getElementById('global-download-btn').addEventListener('click', () => executeContextualExport());
}

// Dynamic DOM View Engine Compilation Rules
function getVerdictStyles(verdict) {
    switch (verdict?.toUpperCase()) {
        case 'BUY': return 'text-kite-green bg-kite-green/10 border-kite-green/20';
        case 'AVOID': return 'text-kite-red bg-kite-red/10 border-kite-red/20';
        default: return 'text-kite-blue bg-kite-blue/10 border-kite-blue/20';
    }
}

function renderViews() {
    buildFullDashboardWatchlist("");
    buildTerminalSidebarWatchlist("");
}

function buildFullDashboardWatchlist(filterText = "") {
    const tbody = document.getElementById('watchlist-table-body');
    tbody.innerHTML = "";

    Object.keys(masterForensicDatabase).forEach(key => {
        const asset = masterForensicDatabase[key];
        if (filterText && !key.toLowerCase().includes(filterText.toLowerCase()) && !asset.name.toLowerCase().includes(filterText.toLowerCase())) return;

        const tr = document.createElement('tr');
        tr.className = "hover:bg-zinc-900/40 cursor-pointer border-b border-kite-border/20 transition-colors";
        tr.onclick = () => {
            switchScreen('terminal');
            renderDiagnosticTerminal(key);
        };

        tr.innerHTML = `
            <td class="sticky left-0 bg-[#1c1c1e] md:bg-transparent z-10 px-4 py-3 font-semibold text-white">${key} <span class="block text-[10px] text-zinc-500 font-normal">${asset.name}</span></td>
            <td class="px-4 py-3 text-center font-mono font-bold text-zinc-300">${asset.health_score}</td>
            <td class="px-4 py-3 text-zinc-400 font-medium">${asset.momentum}</td>
            <td class="px-4 py-3 text-zinc-400 font-medium">${asset.risk}</td>
            <td class="px-4 py-3 text-right"><span class="px-2 py-0.5 rounded text-[10px] font-bold border ${getVerdictStyles(asset.verdict)}">${asset.verdict}</span></td>
        `;
        tbody.appendChild(tr);
    });

    document.getElementById('full-watchlist-search').oninput = (e) => buildFullDashboardWatchlist(e.target.value);
}

function buildTerminalSidebarWatchlist(filterText = "") {
    const container = document.getElementById('terminal-watchlist-container');
    container.innerHTML = "";

    userPinnedWatchlist.forEach(key => {
        const asset = masterForensicDatabase[key];
        if (!asset) return;
        if (filterText && !key.toLowerCase().includes(filterText.toLowerCase()) && !asset.name.toLowerCase().includes(filterText.toLowerCase())) return;

        const row = document.createElement('div');
        row.className = "p-3 flex items-center justify-between hover:bg-zinc-900/30 cursor-pointer border-l-2 border-transparent transition-all";
        row.onclick = () => {
            if(window.innerWidth < 768) document.getElementById('sidebar-close-btn').click();
            renderDiagnosticTerminal(key);
        };

        row.innerHTML = `
            <div>
                <div class="font-bold text-xs text-zinc-200 tracking-tight uppercase">${key}</div>
                <div class="text-[10px] text-zinc-500 max-w-[140px] truncate">${asset.name}</div>
            </div>
            <div class="text-right">
                <span class="text-[11px] font-mono font-bold block text-zinc-400">Score: ${asset.health_score}</span>
                <span class="text-[9px] font-semibold tracking-wider ${asset.verdict === 'BUY' ? 'text-kite-green' : asset.verdict === 'AVOID' ? 'text-kite-red' : 'text-kite-blue'}">${asset.verdict}</span>
            </div>
        `;
        container.appendChild(row);
    });
}

// Workspace Diagnostic Construction & API Routing Execution 
function renderDiagnosticTerminal(ticker) {
    const data = masterForensicDatabase[ticker];
    if (!data) return;

    document.getElementById('empty-state').classList.add('hidden');
    document.getElementById('active-content').classList.remove('hidden');

    document.getElementById('target-title').innerText = `${ticker} : ${data.name}`;
    document.getElementById('target-score').innerText = `${data.health_score}/100`;

    // Parse diagnostic properties mapping into analysis columns grid
    const grid = document.getElementById('analysis-grid');
    grid.innerHTML = "";

    const diagnosticModules = [
        { title: "Financial Forensic Matrix", payload: data.financial_health_markdown },
        { title: "Corporate Governance Integrity", payload: data.governance_markdown }
    ];

    diagnosticModules.forEach(mod => {
        if (!mod.payload) return;
        const card = document.createElement('div');
        card.className = "bg-kite-panel border border-kite-border rounded-lg p-4 shadow-sm";
        card.innerHTML = `
            <h3 class="text-xs font-bold text-white tracking-wider mb-3 uppercase border-l-2 border-kite-blue pl-2">${mod.title}</h3>
            <div class="prose max-w-none text-zinc-400">${marked.parse(mod.payload)}</div>
        `;
        grid.appendChild(card);
    });

    renderYahooChartInstance(ticker);
}

// Lightweight TradingView Visual Composition Logic
function renderYahooChartInstance(ticker) {
    if (activePriceStream) clearInterval(activePriceStream);
    
    const chartFrame = document.getElementById('tv-chart-view-frame');
    chartFrame.innerHTML = "";

    chartInstance = LightweightCharts.createChart(chartFrame, {
        layout: { background: { color: '#1c1c1e' }, textColor: '#a1a1aa' },
        grid: { vertLines: { color: '#2c2c2e' }, horzLines: { color: '#2c2c2e' } },
        crosshair: { mode: LightweightCharts.CrosshairMode.Normal },
        timeScale: { borderColor: '#2c2c2e' }
    });

    candleSeries = chartInstance.addCandlestickSeries({
        upColor: '#00b067', downColor: '#eb5757',
        borderUpColor: '#00b067', borderDownColor: '#eb5757',
        wickUpColor: '#00b067', wickDownColor: '#eb5757'
    });

    const proxyUrl = `https://api.corsproxy.io/?url=${encodeURIComponent(`https://query1.finance.yahoo.com/v8/finance/chart/${ticker}?range=90d&interval=1d`)}`;

    fetch(proxyUrl)
        .then(res => res.json())
        .then(json => {
            const chartData = json.chart.result[0];
            const timestamps = chartData.timestamp;
            const indicators = chartData.indicators.quote[0];

            const parsedMatrix = timestamps.map((ts, idx) => ({
                time: ts,
                open: indicators.open[idx] || indicators.close[idx],
                high: indicators.high[idx] || indicators.close[idx],
                low: indicators.low[idx] || indicators.close[idx],
                close: indicators.close[idx]
            })).filter(item => item.open && item.close);

            candleSeries.setData(parsedMatrix);
            chartInstance.timeScale().fitContent();

            startLivePriceFeed(ticker);
        })
        .catch(err => console.error("Historical tracking failure over active proxy execution loop:", err));
}

function startLivePriceFeed(ticker) {
    const fetchLatestTick = () => {
        const liveProxyUrl = `https://api.corsproxy.io/?url=${encodeURIComponent(`https://query1.finance.yahoo.com/v8/finance/chart/${ticker}?range=1d&interval=1m`)}`;
        fetch(liveProxyUrl)
            .then(res => res.json())
            .then(json => {
                const data = json.chart.result[0].meta;
                const price = data.regularMarketPrice;
                const prevClose = data.chartPreviousClose;
                const pctChange = ((price - prevClose) / prevClose) * 100;

                const priceNode = document.getElementById('live-price');
                priceNode.innerText = price.toFixed(2);
                priceNode.className = `text-base font-bold font-mono ${pctChange >= 0 ? 'text-kite-green' : 'text-kite-red'}`;
            })
            .catch(err => console.warn("Live monitoring telemetry packet dropped:", err));
    };

    fetchLatestTick();
    activePriceStream = setInterval(fetchLatestTick, 10000);
}

// Universal Autocomplete Search Engine Setup 
function setupAutocompleteEngine() {
    const searchInput = document.getElementById('terminal-watchlist-search');
    const autoBox = document.getElementById('search-autocomplete-box');

    searchInput.addEventListener('input', (e) => {
        const query = e.target.value.trim().toUpperCase();
        if (!query) {
            autoBox.classList.add('hidden');
            return;
        }

        autoBox.innerHTML = "";
        let matches = Object.keys(masterForensicDatabase).filter(key => key.includes(query) || masterForensicDatabase[key].name.toUpperCase().includes(query));

        if (matches.length === 0) {
            autoBox.innerHTML = `<div class="p-3 text-xs text-zinc-500">No matching pipeline tokens found</div>`;
        } else {
            matches.forEach(match => {
                const row = document.createElement('div');
                row.className = "p-2.5 flex items-center justify-between hover:bg-zinc-800 cursor-pointer text-xs transition-colors border-b border-kite-border/30";
                
                const isPinned = userPinnedWatchlist.includes(match);

                row.innerHTML = `
                    <div class="flex-1">
                        <span class="font-bold text-white block">${match}</span>
                        <span class="text-[10px] text-zinc-500 block truncate max-w-[180px]">${masterForensicDatabase[match].name}</span>
                    </div>
                    <div class="flex items-center gap-2">
                        <button class="add-token-btn p-1 rounded text-zinc-400 hover:text-kite-blue focus:outline-none" title="Pin instrument to Watchlist">
                            <i class="${isPinned ? 'ph-fill ph-push-pin text-kite-blue' : 'ph ph-push-pin'} text-sm"></i>
                        </button>
                    </div>
                `;

                row.querySelector('.add-token-btn').onclick = (event) => {
                    event.stopPropagation();
                    if (!isPinned) {
                        userPinnedWatchlist.push(match);
                    } else {
                        userPinnedWatchlist = userPinnedWatchlist.filter(id => id !== match);
                    }
                    buildTerminalSidebarWatchlist();
                    setupAutocompleteEngine(); 
                    autoBox.classList.add('hidden');
                    searchInput.value = "";
                };

                row.onclick = () => {
                    renderDiagnosticTerminal(match);
                    autoBox.classList.add('hidden');
                    searchInput.value = "";
                };

                autoBox.appendChild(row);
            });
        }
        autoBox.classList.remove('hidden');
    });

    document.addEventListener('click', (e) => {
        if (!searchInput.contains(e.target) && !autoBox.contains(e.target)) {
            autoBox.classList.add('hidden');
        }
    });
}

// Git Vault Automated Cron Execution Architecture
function reinitializeSchedulerLoop() {
    if (schedulerIntervalId) clearInterval(schedulerIntervalId);
    if (countdownIntervalId) clearInterval(countdownIntervalId);

    const intervalMinutes = parseInt(document.getElementById('scheduler-interval').value);
    currentCountdownSeconds = intervalMinutes * 60;

    countdownIntervalId = setInterval(manageCountdownDisplay, 1000);
    schedulerIntervalId = setInterval(executeBackgroundGitFetch, intervalMinutes * 60 * 1000);
    manageCountdownDisplay();
}

function manageCountdownDisplay() {
    currentCountdownSeconds--;
    if (currentCountdownSeconds < 0) return;

    const mins = Math.floor(currentCountdownSeconds / 60);
    const secs = currentCountdownSeconds % 60;
    document.getElementById('scheduler-countdown').innerText = `${mins.toString().padStart(2, '0')}:${secs.toString().padStart(2, '0')}`;
}

async function executeBackgroundGitFetch() {
    const indicator = document.getElementById('scheduler-indicator');
    const syncBtn = document.getElementById('force-sync-btn');
    
    indicator.classList.remove('hidden');
    syncBtn.disabled = true;
    syncBtn.innerHTML = `<i class="ph ph-arrows-clockwise animate-spin"></i> PIPELINE SYNCING...`;

    try {
        const response = await fetch(`master_forensic_db.json?t=${Date.now()}`);
        masterForensicDatabase = await response.json();
        await localforage.setItem('cached_forensic_db', masterForensicDatabase);
        renderViews();
    } catch (e) {
        console.error("Repository sync packet dropped:", e);
    } finally {
        setTimeout(() => {
            indicator.classList.add('hidden');
            syncBtn.disabled = false;
            syncBtn.innerHTML = `<i class="ph ph-arrows-clockwise"></i> FORCE VAULT PULL`;
            reinitializeSchedulerLoop();
        }, 1200);
    }
}

document.getElementById('scheduler-interval').addEventListener('change', reinitializeSchedulerLoop);

// Contextual Analytics Data Extraction Processor
function executeContextualExport() {
    const isDashboard = !document.getElementById('screen-dashboard').classList.contains('hidden');
    
    if (isDashboard) {
        let csvContent = "data:text/csv;charset=utf-8,Asset Token,Asset Name,Health Score,Momentum,Risk Vector,Verdict\n";
        Object.keys(masterForensicDatabase).forEach(key => {
            const asset = masterForensicDatabase[key];
            csvContent += `${key},"${asset.name}",${asset.health_score},"${asset.momentum}","${asset.risk}",${asset.verdict}\n`;
        });
        
        const encodedUri = encodeURI(csvContent);
        const link = document.createElement("a");
        link.setAttribute("href", encodedUri);
        link.setAttribute("download", `Forensic_Screener_Matrix_${Date.now()}.csv`);
        document.body.appendChild(link);
        link.click();
        document.body.removeChild(link);
    } else {
        const activeHeaders = document.getElementById('target-title').innerText.split(' : ');
        const ticker = activeHeaders[0];
        if (!ticker || ticker === "UNASSIGNED") return;

        const assetJson = JSON.stringify(masterForensicDatabase[ticker], null, 2);
        const dataStr = "data:text/json;charset=utf-8," + encodeURIComponent(assetJson);
        const link = document.createElement("a");
        link.setAttribute("href", dataStr);
        link.setAttribute("download", `Forensic_Analysis_${ticker}_${Date.now()}.json`);
        document.body.appendChild(link);
        link.click();
        document.body.removeChild(link);
    }
}

// Development Runtime Default Backup Configuration Objects
function getFallbackDevelopmentDatabase() {
    return {
        "INFY": {
            "name": "Infosys Limited",
            "health_score": 88,
            "momentum": "Strong Bullish Vector",
            "risk": "Low Operational Risk Profile",
            "verdict": "BUY",
            "financial_health_markdown": "### Financial Overview\n* Operating Margin: `24.2%` tracking flat\n* Free Cash Flow conversion over net profit sits at `102%`.\n* Revenue realization timelines are running optimal.",
            "governance_markdown": "### Audit Matrix Details\n* Auditor independence confirmations evaluated cleanly.\n* Internal accounting controls verified completely."
        },
        "RELIANCE": {
            "name": "Reliance Industries",
            "health_score": 79,
            "momentum": "Consolidating Range Structure",
            "risk": "Moderate Capital Overhead",
            "verdict": "HOLD",
            "financial_health_markdown": "### Financial Overview\n* Capital expenditure programs impacting current net realization yields.\n* Leverage metrics track completely within baseline parameters.",
            "governance_markdown": "### Audit Matrix Details\n* Related party transactions show no standard valuation mismatch structural faults."
        },
        "TCS": {
            "name": "Tata Consultancy Services",
            "health_score": 92,
            "momentum": "Bullish Trend Continuation",
            "risk": "Negligible Operational Risk",
            "verdict": "BUY",
            "financial_health_markdown": "### Financial Overview\n* Industry leading returns on equity scaling over `45%` parameters.\n* Order conversion pipeline metrics established historical highs.",
            "governance_markdown": "### Audit Matrix Details\n* Exceptional whistle-blower framework structural responses observed throughout periods."
        }
    };
}
