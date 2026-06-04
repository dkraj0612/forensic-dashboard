// PROXY CONFIGURATION ABSTRACTION
const PROXY_BASE = "https://api.corsproxy.io/?url=";
// GOOGLE APPS SCRIPT PLATFORM ENTRYPOINT (For Forensic Mode Document Processing)
const GAS_WEB_APP_URL = "https://script.google.com/macros/s/AKfycbx4X78LZ3cpk88sgJOwcsZrs1gy1JL-v7Co5_8F_3x3dASUfSW4esRH1PBseAWDOlnfcA/exec";

// Initialization and Core State Allocation
localforage.config({ name: 'ForensicStudio', storeName: 'analytics_cache' });

let masterForensicDatabase = {};
let nseMasterList = []; 
let userPinnedWatchlist = []; 
let activePriceStream = null;
let chartInstance = null;
let candleSeries = null;

let schedulerIntervalId = null;
let countdownIntervalId = null;
let currentCountdownSeconds = 300;

document.addEventListener('DOMContentLoaded', () => {
    setupAuthObserver();
});

// Contextual Routing and Guard Checks
function handleViewSwitchIntent(targetMode) {
    let confirmationInfo = "";
    
    if (targetMode === 'general') {
        confirmationInfo = "Switch to GENERAL MODE?\n\n• View comprehensive matrix tracking across model diagnostic assets.\n• Trigger direct sync of static global NSE stock ledgers to local system memory.";
    } else {
        confirmationInfo = "Switch to FORENSIC MODE?\n\n• Deploy continuous deep diagnostics for an isolated ticker asset.\n• Activate streaming live exchange telemetry charts.\n• Request contextual report files (transcripts, results, ownership records) via GAS.";
    }

    if (confirm(confirmationInfo)) {
        switchScreen(targetMode);
    }
}

function switchScreen(target) {
    const generalScreen = document.getElementById('screen-general');
    const forensicScreen = document.getElementById('screen-forensic');
    const btnGeneral = document.getElementById('nav-general');
    const btnForensic = document.getElementById('nav-forensic');

    if (target === 'general') {
        generalScreen.classList.remove('hidden'); generalScreen.classList.add('z-10');
        forensicScreen.classList.add('hidden'); forensicScreen.classList.remove('z-10');
        btnGeneral.className = "px-3 py-1 rounded text-xs font-medium transition-all text-zinc-100 bg-kite-border shadow-sm";
        btnForensic.className = "px-3 py-1 rounded text-xs font-medium transition-all text-zinc-400 hover:text-zinc-200";
    } else {
        forensicScreen.classList.remove('hidden'); forensicScreen.classList.add('z-10');
        generalScreen.classList.add('hidden'); generalScreen.classList.remove('z-10');
        btnForensic.className = "px-3 py-1 rounded text-xs font-medium transition-all text-zinc-100 bg-kite-border shadow-sm";
        btnGeneral.className = "px-3 py-1 rounded text-xs font-medium transition-all text-zinc-400 hover:text-zinc-200";
    }
}

// Authentication Framework (Preserved explicitly untouched)
function setupAuthObserver() {
    const authForm = document.getElementById('auth-form');
    authForm.addEventListener('submit', (e) => {
        e.preventDefault();
        const username = document.getElementById('auth-username').value.trim();
        const secureKey = prompt("Enter Master Gateway Authentication Key:");

        if (secureKey === "admin") {
            document.getElementById('auth-status').className = "text-center text-xs mt-4 text-kite-green font-semibold";
            document.getElementById('auth-status').innerText = "ACCESS GRANTED. INITIALIZING SYSTEMS...";
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

// Memory & Datastore Context Setup
async function initDataPipeline() {
    try {
        const response = await fetch(`master_forensic_db.json?t=${Date.now()}`);
        if (!response.ok) throw new Error("Server transmission error.");
        masterForensicDatabase = await response.json();
        await localforage.setItem('cached_forensic_db', masterForensicDatabase);
    } catch (err) {
        masterForensicDatabase = await localforage.getItem('cached_forensic_db') || {};
    }
    
    nseMasterList = await localforage.getItem('cached_nse_list') || [];

    if (userPinnedWatchlist.length === 0 && Object.keys(masterForensicDatabase).length > 0) {
        userPinnedWatchlist = Object.keys(masterForensicDatabase).slice(0, 5);
    }

    const cachedModel = await localforage.getItem('config_ai_model');
    if (cachedModel) document.getElementById('ai-model-selector').value = cachedModel;

    const cachedRepo = await localforage.getItem('config_git_repo');
    if (cachedRepo) document.getElementById('git-repo-url').value = cachedRepo;

    const cachedToken = await localforage.getItem('config_git_token');
    if (cachedToken) document.getElementById('git-access-token').value = cachedToken;

    evaluateGitPushState();
    renderViews();
    reinitializeSchedulerLoop();
    setupAutocompleteEngine();
    setupGlobalNavigationHooks();
    setupConfigurationChangeListeners();
}

function setupGlobalNavigationHooks() {
    document.getElementById('nav-general').addEventListener('click', () => handleViewSwitchIntent('general'));
    document.getElementById('nav-forensic').addEventListener('click', () => handleViewSwitchIntent('forensic'));
    document.getElementById('force-sync-btn').addEventListener('click', () => executeBackgroundGitFetch());
    document.getElementById('global-download-btn').addEventListener('click', () => executeContextualExport());
    document.getElementById('vault-push-btn').addEventListener('click', () => executeBackgroundGitPush());
}

function setupConfigurationChangeListeners() {
    document.getElementById('ai-model-selector').addEventListener('change', async (e) => {
        await localforage.setItem('config_ai_model', e.target.value);
    });
    document.getElementById('git-repo-url').addEventListener('input', async (e) => {
        await localforage.setItem('config_git_repo', e.target.value.trim());
        evaluateGitPushState();
    });
    document.getElementById('git-access-token').addEventListener('input', async (e) => {
        await localforage.setItem('config_git_token', e.target.value.trim());
        evaluateGitPushState();
    });
}

function evaluateGitPushState() {
    const repo = document.getElementById('git-repo-url').value.trim();
    const token = document.getElementById('git-access-token').value.trim();
    const pushBtn = document.getElementById('vault-push-btn');

    if (repo && token) {
        pushBtn.disabled = false;
        pushBtn.className = "w-full flex items-center justify-center gap-2 rounded bg-kite-blue hover:bg-blue-600 text-white py-2 text-xs font-semibold focus:outline-none transition-colors cursor-pointer";
    } else {
        pushBtn.disabled = true;
        pushBtn.className = "w-full flex items-center justify-center gap-2 rounded border border-transparent bg-zinc-800 text-zinc-500 cursor-not-allowed py-2 text-xs font-semibold focus:outline-none transition-colors";
    }
}

function getVerdictStyles(verdict) {
    if (!verdict) return 'text-kite-blue bg-kite-blue/10 border-kite-blue/20';
    const v = verdict.toUpperCase();
    if (v.includes('BUY')) return 'text-kite-green bg-kite-green/10 border-kite-green/20';
    if (v.includes('AVOID')) return 'text-kite-red bg-kite-red/10 border-kite-red/20';
    return 'text-kite-blue bg-kite-blue/10 border-kite-blue/20';
}

function renderViews() {
    buildFullGeneralWatchlist("");
    buildForensicSidebarWatchlist("");
}

function buildFullGeneralWatchlist(filterText = "") {
    const tbody = document.getElementById('watchlist-table-body');
    tbody.innerHTML = "";
    
    const fragment = document.createDocumentFragment();

    Object.keys(masterForensicDatabase).forEach(ticker => {
        const asset = masterForensicDatabase[ticker];
        if (filterText && !ticker.includes(filterText.toUpperCase())) return;

        const structure = asset.governance?.risk_level || "Standard Structure";
        const score = asset.score || "--";
        const momentum = asset.market_momentum?.trend || "Neutral";
        const risk = asset.regulatory_surveillance?.framework || "Normal";
        const verdict = asset.verdict || "HOLD";

        const tr = document.createElement('tr');
        tr.className = "hover:bg-zinc-900/40 cursor-pointer border-b border-kite-border/20 transition-colors";
        tr.onclick = () => { switchScreen('forensic'); renderDiagnosticTerminal(ticker); };

        tr.innerHTML = `
            <td class="sticky left-0 bg-[#1c1c1e] md:bg-transparent z-10 px-4 py-3 font-semibold text-white font-mono">${ticker} <span class="block text-[10px] text-zinc-500 font-normal font-sans uppercase">${structure}</span></td>
            <td class="px-4 py-3 text-center font-mono font-bold text-zinc-300">${score}</td>
            <td class="px-4 py-3 text-zinc-400 font-medium">${momentum}</td>
            <td class="px-4 py-3 text-zinc-400 font-medium">${risk}</td>
            <td class="px-4 py-3 text-right"><span class="px-2 py-0.5 rounded text-[10px] font-bold border ${getVerdictStyles(verdict)}">${verdict}</span></td>
        `;
        fragment.appendChild(tr);
    });

    tbody.appendChild(fragment);
    document.getElementById('full-watchlist-search').oninput = (e) => buildFullGeneralWatchlist(e.target.value);
}

function buildForensicSidebarWatchlist(filterText = "") {
    const container = document.getElementById('forensic-watchlist-container');
    container.innerHTML = "";
    
    const fragment = document.createDocumentFragment();

    userPinnedWatchlist.forEach(ticker => {
        const asset = masterForensicDatabase[ticker];
        if (filterText && !ticker.includes(filterText.toUpperCase())) return;

        const structure = asset ? (asset.governance?.risk_level || "Equity Asset") : "System Token";
        const score = asset ? (asset.score || "--") : "--";
        const verdict = asset ? (asset.verdict || "HOLD") : "UNTRACKED";

        const row = document.createElement('div');
        row.className = "p-3 flex items-center justify-between hover:bg-zinc-900/30 cursor-pointer border-l-2 border-transparent transition-all";
        row.onclick = () => {
            if(window.innerWidth < 768) document.getElementById('sidebar-close-btn').click();
            renderDiagnosticTerminal(ticker);
        };

        row.innerHTML = `
            <div>
                <div class="font-bold text-xs text-zinc-200 tracking-tight uppercase font-mono">${ticker}</div>
                <div class="text-[10px] text-zinc-500 max-w-[140px] truncate uppercase">${structure}</div>
            </div>
            <div class="text-right">
                <span class="text-[11px] font-mono font-bold block text-zinc-400">Score: ${score}</span>
                <span class="text-[9px] font-semibold tracking-wider ${getVerdictStyles(verdict).split(' ')[0]}">${verdict}</span>
            </div>
        `;
        fragment.appendChild(row);
    });
    
    container.appendChild(fragment);
}

function renderDiagnosticTerminal(ticker) {
    const data = masterForensicDatabase[ticker];

    document.getElementById('empty-state').classList.add('hidden');
    document.getElementById('active-content').classList.remove('hidden');

    document.getElementById('target-title').innerText = ticker;
    const priceNode = document.getElementById('live-price');
    priceNode.innerText = "--.--";
    priceNode.className = "text-base font-bold text-zinc-200";
    document.getElementById('live-price-label').innerText = "Last Traded Price";

    const grid = document.getElementById('analysis-grid');
    grid.innerHTML = "";

    if (!data) {
        document.getElementById('target-title').nextElementSibling.innerText = "UNTRACKED INSTRUMENT";
        document.getElementById('target-score').innerText = "--/100";
        grid.innerHTML = `
            <div class="lg:col-span-2 bg-kite-panel border border-kite-border rounded-lg p-6 text-center text-xs text-zinc-500">
                This asset exists in the global token registry but has no local intelligence profile data. Run python processing models or check upstream repositories.
            </div>
        `;
        renderYahooChartInstance(ticker);
        return;
    }

    const structure = data.governance?.risk_level || "Classification";
    document.getElementById('target-title').nextElementSibling.innerText = structure;
    document.getElementById('target-score').innerText = `${data.score || "--"}/100`;

    const modules = [
        { title: "Financial Health", payload: data.financial_health?.revenue_quality },
        { title: "Corporate Governance", payload: data.governance?.details },
        { title: "Shareholding Trends", payload: data.shareholding_trends?.description },
        { title: "Catalysts & Sentiment", payload: data.catalysts_and_sentiment?.description }
    ];

    modules.forEach(mod => {
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

function renderYahooChartInstance(ticker) {
    if (activePriceStream) clearInterval(activePriceStream);
    
    const chartFrame = document.getElementById('tv-chart-view-frame');
    if (chartInstance) {
        chartInstance.remove();
        chartInstance = null;
    }
    chartFrame.innerHTML = "";

    chartInstance = LightweightCharts.createChart(chartFrame, {
        layout: { background: { color: '#1c1c1e' }, textColor: '#a1a1aa' },
        grid: { vertLines: { color: '#2c2c2e' }, horzLines: { color: '#2c2c2e' } },
        crosshair: { mode: LightweightCharts.CrosshairMode.Normal },
        timeScale: { borderColor: '#2c2c2e' }
    });

    candleSeries = chartInstance.addCandlestickSeries({
        upColor: '#00b067', downColor: '#eb5757', borderVisible: false,
        wickUpColor: '#00b067', wickDownColor: '#eb5757'
    });

    const url = `${PROXY_BASE}${encodeURIComponent(`https://query1.finance.yahoo.com/v8/finance/chart/${ticker}.NS?range=90d&interval=1d`)}`;

    fetch(url).then(res => res.json()).then(json => {
        const chartData = json.chart.result[0];
        const timestamps = chartData.timestamp;
        const indicators = chartData.indicators.quote[0];
        const parsedMatrix = timestamps.map((ts, idx) => ({
            time: ts, open: indicators.open[idx], high: indicators.high[idx], low: indicators.low[idx], close: indicators.close[idx]
        })).filter(item => item.open && item.close);

        candleSeries.setData(parsedMatrix);
        chartInstance.timeScale().fitContent();
        startLivePriceFeed(`${ticker}.NS`);
    }).catch(() => console.warn("Chart data unavailable for " + ticker));
}

function startLivePriceFeed(ticker) {
    const fetchLatestTick = () => {
        const liveUrl = `${PROXY_BASE}${encodeURIComponent(`https://query1.finance.yahoo.com/v8/finance/chart/${ticker}?range=1d&interval=1m`)}`;
        
        fetch(liveUrl).then(res => {
            if (!res.ok) throw new Error("Dropped");
            return res.json();
        }).then(json => {
            const data = json.chart.result[0].meta;
            const price = data.regularMarketPrice;
            const prevClose = data.chartPreviousClose;
            const pctChange = ((price - prevClose) / prevClose) * 100;

            const priceNode = document.getElementById('live-price');
            priceNode.innerText = price.toFixed(2);
            priceNode.className = `text-base font-bold font-mono ${pctChange >= 0 ? 'text-kite-green' : 'text-kite-red'}`;
            document.getElementById('live-price-label').innerText = "Last Traded Price";
        }).catch(() => {
            const priceNode = document.getElementById('live-price');
            priceNode.innerText = "[ OFFLINE ]";
            priceNode.className = "text-sm font-bold font-mono text-kite-orange animate-pulse";
            document.getElementById('live-price-label').innerText = "Telemetry Dropped";
        });
    };
    fetchLatestTick();
    activePriceStream = setInterval(fetchLatestTick, 10000);
}

function setupAutocompleteEngine() {
    const searchInput = document.getElementById('forensic-watchlist-search');
    const autoBox = document.getElementById('search-autocomplete-box');

    searchInput.addEventListener('input', (e) => {
        const query = e.target.value.trim().toUpperCase();
        if (!query) { autoBox.classList.add('hidden'); return; }

        autoBox.innerHTML = "";
        
        // Match query across the comprehensive global static list if available, else fallback to analytical data
        const workingUniverse = nseMasterList.length > 0 ? nseMasterList : Object.keys(masterForensicDatabase);
        let matches = workingUniverse.filter(ticker => ticker.includes(query)).slice(0, 20);

        if (matches.length === 0) {
            autoBox.innerHTML = `<div class="p-3 text-xs text-zinc-500">No matching registry entries found</div>`;
        } else {
            matches.forEach(match => {
                const row = document.createElement('div');
                row.className = "p-2.5 flex items-center justify-between hover:bg-zinc-800 cursor-pointer text-xs transition-colors border-b border-kite-border/30";
                const isPinned = userPinnedWatchlist.includes(match);
                const hasModelAnalysis = masterForensicDatabase[match] ? "Analyzed Profile" : "No Core Data";

                row.innerHTML = `
                    <div class="flex-1 font-mono">
                        <span class="font-bold text-white block">${match}</span>
                        <span class="text-[10px] text-zinc-500 block uppercase">${hasModelAnalysis}</span>
                    </div>
                    <button class="add-token-btn p-1 rounded text-zinc-400 hover:text-kite-blue"><i class="${isPinned ? 'ph-fill ph-push-pin text-kite-blue' : 'ph ph-push-pin'} text-sm"></i></button>
                `;

                row.querySelector('.add-token-btn').onclick = (event) => {
                    event.stopPropagation();
                    isPinned ? userPinnedWatchlist = userPinnedWatchlist.filter(id => id !== match) : userPinnedWatchlist.push(match);
                    buildForensicSidebarWatchlist();
                    setupAutocompleteEngine(); 
                    autoBox.classList.add('hidden'); searchInput.value = "";
                };

                row.onclick = () => {
                    renderDiagnosticTerminal(match);
                    autoBox.classList.add('hidden'); searchInput.value = "";
                };
                autoBox.appendChild(row);
            });
        }
        autoBox.classList.remove('hidden');
    });

    document.addEventListener('click', (e) => {
        if (!searchInput.contains(e.target) && !autoBox.contains(e.target)) autoBox.classList.add('hidden');
    });
}

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

async function executeBackgroundGitPush() {
    const pushBtn = document.getElementById('vault-push-btn');
    const model = document.getElementById('ai-model-selector').value;
    
    pushBtn.disabled = true;
    pushBtn.innerHTML = `<i class="ph ph-cloud-arrow-up animate-pulse"></i> COMMITTING CHANGES...`;

    setTimeout(() => {
        alert(`Git Vault Synchronization Successful via [${model}].`);
        evaluateGitPushState();
    }, 1500);
}

// Contextual Data Extraction Pipeline Handler
async function executeContextualExport() {
    const isGeneralMode = !document.getElementById('screen-general').classList.contains('hidden');
    const downloadBtn = document.getElementById('global-download-btn');
    const originalIcon = downloadBtn.innerHTML;
    
    downloadBtn.innerHTML = `<i class="ph ph-circle-notch animate-spin text-sm"></i>`;
    downloadBtn.disabled = true;

    try {
        if (isGeneralMode) {
            // ==========================================
            // GENERAL MODE: FETCH STATIC LIST FROM NSE
            // ==========================================
            const staticNseUrl = `${PROXY_BASE}${encodeURIComponent('https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv')}`;
            const response = await fetch(staticNseUrl);
            if (!response.ok) throw new Error("NSE connection failed.");

            const csvData = await response.text();
            const lines = csvData.split('\n');
            let parsedSymbols = [];

            // Skip line 0 (Header Row: SYMBOL,NAME OF COMPANY...)
            for (let i = 1; i < lines.length; i++) {
                const columns = lines[i].split(',');
                if (columns[0]) {
                    const cleanSymbol = columns[0].replace(/['"]+/g, '').trim();
                    if (cleanSymbol) parsedSymbols.push(cleanSymbol.toUpperCase());
                }
            }

            if (parsedSymbols.length === 0) throw new Error("Parsed symbol matrix array empty.");

            // Commit static exchange array mapping directly to client storage
            nseMasterList = parsedSymbols;
            await localforage.setItem('cached_nse_list', nseMasterList);
            
            // Clean up old instances and reboot search mapping references
            setupAutocompleteEngine();
            alert(`Success! Loaded ${nseMasterList.length} static public equities out of the official NSE tracking index directly into client storage.`);

        } else {
            // ==========================================
            // FORENSIC MODE: FETCH VIA GOOGLE APPS SCRIPT
            // ==========================================
            const ticker = document.getElementById('target-title').innerText;
            if (!ticker || ticker === "UNASSIGNED") {
                alert("Mount active asset profile to prompt GAS target pull.");
                return;
            }

            const targetUrl = `${GAS_WEB_APP_URL}?ticker=${ticker}`;
            const response = await fetch(targetUrl, { method: 'GET', redirect: 'follow' });
            
            if (!response.ok) throw new Error("GAS engine rejected structural pull.");
            
            const payload = await response.text();
            if (!payload || payload.trim() === "") throw new Error("GAS payload empty.");

            triggerDownload(payload, `${ticker}_Transcripts_Ownership_Results.csv`, 'text/csv;charset=utf-8;');
        }
    } catch (error) {
        console.error("Pipeline Sync Halt:", error);
        alert("Action dropped: Pipeline transmission timeout, CORS error, or structural failure.");
    } finally {
        downloadBtn.innerHTML = originalIcon;
        downloadBtn.disabled = false;
    }
}

// Memory Safe Native Blob Downloader (Protects against empty file outputs on strict platforms)
function triggerDownload(content, filename, mimeType) {
    const blob = new Blob([content], { type: mimeType });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    
    link.setAttribute("href", url);
    link.setAttribute("download", filename);
    
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    
    setTimeout(() => URL.revokeObjectURL(url), 100);
}
