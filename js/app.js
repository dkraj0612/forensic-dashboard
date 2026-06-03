localforage.config({ name: 'ForensicStudio', storeName: 'analytics_cache' });

let masterForensicDatabase = null;
let activeChartInstance = null;
let activePriceStream = null;
let operatorId = "";

const navDashboard = document.getElementById('nav-dashboard');
const navTerminal = document.getElementById('nav-terminal');
const screenDashboard = document.getElementById('screen-dashboard');
const screenTerminal = document.getElementById('screen-terminal');

function switchScreen(screenName) {
    if (screenName === 'dashboard') {
        screenDashboard.classList.remove('hidden'); screenTerminal.classList.add('hidden');
        navDashboard.classList.replace('text-kite-muted', 'text-kite-blue');
        navDashboard.classList.replace('border-transparent', 'border-kite-blue');
        navTerminal.classList.replace('text-kite-blue', 'text-kite-muted');
        navTerminal.classList.replace('border-kite-blue', 'border-transparent');
    } else if (screenName === 'terminal') {
        screenTerminal.classList.remove('hidden'); screenDashboard.classList.add('hidden');
        navTerminal.classList.replace('text-kite-muted', 'text-kite-blue');
        navTerminal.classList.replace('border-transparent', 'border-kite-blue');
        navDashboard.classList.replace('text-kite-blue', 'text-kite-muted');
        navDashboard.classList.replace('border-kite-blue', 'border-transparent');
    }
}

navDashboard.onclick = () => switchScreen('dashboard');
navTerminal.onclick = () => switchScreen('terminal');

function setupAuthObserver() {
    if (typeof window.firebaseOnAuthChange !== 'function') {
        setTimeout(setupAuthObserver, 100); return;
    }
    window.firebaseOnAuthChange(window.auth, (user) => {
        if (user) {
            operatorId = user.email.split('@')[0].toUpperCase();
            document.getElementById("system-lock-overlay").classList.add("opacity-0", "pointer-events-none");
            setTimeout(() => document.getElementById("system-lock-overlay").classList.add("hidden"), 300);
            document.getElementById("logout-btn").innerText = operatorId.substring(0, 2);
            initDataPipeline();
        } else {
            document.getElementById("system-lock-overlay").classList.remove("hidden", "opacity-0", "pointer-events-none");
        }
    });
}

document.getElementById('auth-form').onsubmit = async (e) => {
    e.preventDefault();
    const emailInput = document.getElementById('auth-username').value.trim();
    const passwordInput = prompt(`Enter security credentials for ${emailInput}:`);
    const btn = document.getElementById('auth-submit-btn');
    if (!emailInput || !passwordInput) return alert("Credentials incomplete.");
    btn.innerText = "Authenticating..."; btn.disabled = true;
    try { await window.firebaseSignIn(window.auth, emailInput, passwordInput); } 
    catch (error) { alert("Authentication Refused: " + error.code); btn.innerText = "Login"; btn.disabled = false; }
};
document.getElementById('logout-btn').onclick = () => window.firebaseSignOut(window.auth);

async function initDataPipeline() {
    try {
        const response = await fetch(`./master_forensic_db.json?t=${Date.now()}`);
        if (!response.ok) throw new Error("Local matrix missing.");
        masterForensicDatabase = await response.json();
        await localforage.setItem('cached_master_db', masterForensicDatabase);
        renderViews();
    } catch (error) {
        masterForensicDatabase = await localforage.getItem('cached_master_db');
        if (masterForensicDatabase) renderViews();
        else document.getElementById("watchlist-table-body").innerHTML = `<tr><td colspan="6" class="p-4 text-kite-red text-center">Database empty.</td></tr>`;
    }
}

function renderViews() {
    buildFullDashboardWatchlist(masterForensicDatabase);
    buildTerminalSidebarWatchlist(masterForensicDatabase);
}

function getVerdictStyles(verdict) {
    if (!verdict) return { color: 'text-kite-muted', bg: 'bg-transparent' };
    const v = verdict.toUpperCase();
    if (v.includes('BUY')) return { color: 'text-kite-green', bg: 'bg-kite-green/10' };
    if (v.includes('AVOID')) return { color: 'text-kite-red', bg: 'bg-kite-red/10' };
    return { color: 'text-kite-blue', bg: 'bg-kite-blue/10' };
}

function buildFullDashboardWatchlist(database, filterText = "") {
    const tbody = document.getElementById("watchlist-table-body"); tbody.innerHTML = "";
    const tickers = Object.keys(database).filter(t => t.toLowerCase().includes(filterText.toLowerCase()));
    if (tickers.length === 0) return;

    tickers.forEach(ticker => {
        const data = database[ticker];
        const vStyle = getVerdictStyles(data.verdict);
        const tr = document.createElement("tr");
        tr.className = "border-b border-kite-border hover:bg-kite-nav transition-colors";
        tr.innerHTML = `
            <td class="py-4 font-mono"><div class="text-kite-blue font-semibold">${ticker}</div><div class="text-[10px] text-kite-muted mt-1 uppercase">${data.governance?.risk_level || "Standard Structure"}</div></td>
            <td class="py-4"><div class="text-kite-text font-medium">${data.score || "--"} / 100</div></td>
            <td class="py-4"><div class="text-kite-text text-xs">${data.market_momentum?.trend || "Neutral"}</div></td>
            <td class="py-4 text-xs text-kite-muted">${data.regulatory_surveillance?.framework || "Normal"}</td>
            <td class="py-4"><span class="px-2 py-1 rounded text-[10px] font-bold ${vStyle.bg} ${vStyle.color}">${data.verdict || 'HOLD'}</span></td>
            <td class="py-4 text-right"><button class="bg-kite-blue/10 text-kite-blue px-4 py-1.5 rounded text-xs transition-colors" data-ticker="${ticker}">Analyze Asset</button></td>
        `;
        tr.querySelector('button').onclick = () => { switchScreen('terminal'); renderDiagnosticTerminal(ticker, data); };
        tbody.appendChild(tr);
    });
}
document.getElementById('full-watchlist-search').addEventListener('input', (e) => { if(masterForensicDatabase) buildFullDashboardWatchlist(masterForensicDatabase, e.target.value); });

function buildTerminalSidebarWatchlist(database, filterText = "") {
    const container = document.getElementById("terminal-watchlist-container"); container.innerHTML = "";
    Object.keys(database).filter(t => t.toLowerCase().includes(filterText.toLowerCase())).forEach(ticker => {
        const data = database[ticker];
        const vStyle = getVerdictStyles(data.verdict);
        const item = document.createElement("div");
        item.className = "group flex justify-between items-center px-4 py-3 border-b border-kite-border hover:bg-kite-nav cursor-pointer transition-colors";
        item.innerHTML = `<div class="flex flex-col"><span class="text-sm font-medium ${vStyle.color}">${ticker}</span></div><div class="flex flex-col items-end"><span class="text-xs font-semibold ${vStyle.color}">${data.score || '--'}</span></div>`;
        item.onclick = () => renderDiagnosticTerminal(ticker, data);
        container.appendChild(item);
    });
}
document.getElementById('terminal-watchlist-search').addEventListener('input', (e) => { if(masterForensicDatabase) buildTerminalSidebarWatchlist(masterForensicDatabase, e.target.value); });

function renderDiagnosticTerminal(ticker, data) {
    document.getElementById("empty-state").classList.add("hidden");
    document.getElementById("active-content").classList.remove("hidden");
    
    document.getElementById("target-title").innerText = ticker;
    document.getElementById("target-score").innerText = data.score || "--";
    
    const vStyle = getVerdictStyles(data.verdict);
    const badge = document.getElementById("target-verdict");
    badge.innerText = data.verdict || "UNKNOWN";
    badge.className = `px-3 py-1.5 rounded text-xs font-bold tracking-wide ${vStyle.bg} ${vStyle.color}`;

    const grid = document.getElementById("analysis-grid"); grid.innerHTML = "";
    const modules = [
        { title: "Financial Health", val: data.financial_health?.revenue_quality },
        { title: "Corporate Governance", val: data.governance?.details },
        { title: "Shareholding Trends", val: data.shareholding_trends?.description },
        { title: "Momentum", val: `Trend: ${data.market_momentum?.trend}` },
        { title: "Surveillance Risk", val: `Risk Level: ${data.regulatory_surveillance?.risk}` },
        { title: "Catalysts", val: data.catalysts_and_sentiment?.description }
    ];
    modules.forEach(m => {
        if (!m.val) return; 
        const tile = document.createElement("div"); tile.className = "border border-kite-border bg-kite-nav rounded p-4";
        tile.innerHTML = `<h4 class="text-xs font-semibold text-kite-muted uppercase mb-3 border-b border-kite-border pb-2">${m.title}</h4><div class="prose">${marked.parse(m.val)}</div>`;
        grid.appendChild(tile);
    });

    renderYahooChartInstance(ticker);
}

// YAHOO HISTORICAL CHART LOADER
async function renderYahooChartInstance(ticker) {
    const container = document.getElementById("tv-chart-view-frame");
    container.innerHTML = `<div class="p-4 text-kite-blue text-xs font-mono flex items-center gap-2"><i class="ph ph-circle-notch animate-spin"></i> Fetching market data...</div>`;
    
    if (activePriceStream) clearInterval(activePriceStream);

    try {
        const yahooTicker = `${ticker}.NS`; 
        const url = `https://query1.finance.yahoo.com/v8/finance/chart/${yahooTicker}?interval=1d&range=90d`;
        const proxyUrl = `https://corsproxy.io/?url=${encodeURIComponent(url)}`;
        
        const response = await fetch(proxyUrl);
        if (!response.ok) throw new Error("API Blocked");
        const json = await response.json();
        
        const timestamps = json.chart.result[0].timestamp;
        const quotes = json.chart.result[0].indicators.quote[0];
        let formattedData = [];
        
        for (let i = 0; i < timestamps.length; i++) {
            if (quotes.open[i] !== null) { 
                formattedData.push({ time: new Date(timestamps[i] * 1000).toISOString().split('T')[0], open: quotes.open[i], high: quotes.high[i], low: quotes.low[i], close: quotes.close[i] });
            }
        }
        
        container.innerHTML = "";
        activeChartInstance = LightweightCharts.createChart(container, {
            layout: { background: { type: 'solid', color: 'transparent' }, textColor: '#9b9b9b' },
            grid: { vertLines: { color: '#2b2b2b' }, horzLines: { color: '#2b2b2b' } },
            timeScale: { borderColor: '#2b2b2b' }
        });
        const candleSeries = activeChartInstance.addCandlestickSeries({ upColor: '#4caf50', downColor: '#f44336', borderVisible: false });
        candleSeries.setData(formattedData);
        activeChartInstance.timeScale().fitContent();

        startLivePriceFeed(yahooTicker);
    } catch (error) {
        container.innerHTML = `<div class="p-4 text-kite-red text-xs">Market data unavailable for ${ticker}.</div>`;
    }
}

// LIVE PRICE TICKER FEED
function startLivePriceFeed(yahooTicker) {
    const livePriceEl = document.getElementById("live-price");
    const liveChangeEl = document.getElementById("live-change");
    
    const fetchLiveQuote = async () => {
        try {
            const url = `https://query1.finance.yahoo.com/v8/finance/chart/${yahooTicker}?interval=1m&range=1d`;
            const proxyUrl = `https://corsproxy.io/?url=${encodeURIComponent(url)}`;
            const res = await fetch(proxyUrl);
            const json = await res.json();
            
            const meta = json.chart.result[0].meta;
            const cmp = meta.regularMarketPrice;
            const prevClose = meta.chartPreviousClose;
            
            const change = cmp - prevClose;
            const percentChange = (change / prevClose) * 100;
            const colorClass = change >= 0 ? "text-kite-green" : "text-kite-red";
            const sign = change >= 0 ? "+" : "";
            
            livePriceEl.innerText = `₹${cmp.toFixed(2)}`;
            livePriceEl.className = `text-2xl font-bold font-mono ${colorClass}`;
            liveChangeEl.innerText = `${sign}${change.toFixed(2)} (${sign}${percentChange.toFixed(2)}%)`;
            liveChangeEl.className = `text-xs font-semibold mt-0.5 ${colorClass}`;
        } catch (e) {
            console.log("Silent background ping failed.");
        }
    };
    
    fetchLiveQuote();
    activePriceStream = setInterval(fetchLiveQuote, 10000);
}

window.addEventListener('resize', () => {
    if(activeChartInstance && document.getElementById('tv-chart-view-frame')) {
       activeChartInstance.resize(document.getElementById('tv-chart-view-frame').clientWidth, 400);
    }
});

let fetchSchedulerTimer = null;
let countdownTrackerTimer = null;
let secondsRemainingUntilFetch = 300; 

const schedulerIntervalSelect = document.getElementById('scheduler-interval');
const schedulerStatus = document.getElementById('scheduler-status');
const schedulerCountdown = document.getElementById('scheduler-countdown');
const schedulerIndicator = document.getElementById('scheduler-indicator');

document.getElementById('menu-dots-btn').addEventListener('click', (e) => {
    e.stopPropagation(); document.getElementById('options-dropdown').classList.toggle('hidden');
});
document.addEventListener('click', (e) => {
    const dropdown = document.getElementById('options-dropdown');
    if (!dropdown.classList.contains('hidden') && !dropdown.contains(e.target)) dropdown.classList.add('hidden');
});

async function executeBackgroundGitFetch() {
    schedulerStatus.innerText = "Fetching..."; schedulerStatus.className = "text-kite-blue font-medium";
    schedulerIndicator.className = "w-2 h-2 rounded-full bg-kite-blue animate-spin";
    try {
        const response = await fetch(`./master_forensic_db.json?t=${Date.now()}`);
        masterForensicDatabase = await response.json();
        await localforage.setItem('cached_master_db', masterForensicDatabase);
        buildFullDashboardWatchlist(masterForensicDatabase, document.getElementById('full-watchlist-search').value);
        buildTerminalSidebarWatchlist(masterForensicDatabase, document.getElementById('terminal-watchlist-search').value);
        schedulerStatus.innerText = "Synchronized"; schedulerStatus.className = "text-kite-green font-medium";
        schedulerIndicator.className = "w-2 h-2 rounded-full bg-kite-green animate-pulse";
    } catch (error) {
        schedulerStatus.innerText = "Sync Error"; schedulerStatus.className = "text-kite-red font-medium";
        schedulerIndicator.className = "w-2 h-2 rounded-full bg-kite-red";
    }
}

function manageCountdownDisplay() {
    if (secondsRemainingUntilFetch <= 0) return; 
    secondsRemainingUntilFetch--;
    const mins = Math.floor(secondsRemainingUntilFetch / 60); const secs = secondsRemainingUntilFetch % 60;
    schedulerCountdown.innerText = `${String(mins).padStart(2, '0')}:${String(secs).padStart(2, '0')}`;
}

function reinitializeSchedulerLoop() {
    clearInterval(fetchSchedulerTimer); clearInterval(countdownTrackerTimer);
    const configuredMinutes = parseInt(schedulerIntervalSelect.value);
    if (configuredMinutes === 0) {
        schedulerStatus.innerText = "Manual Only"; schedulerStatus.className = "text-kite-muted font-medium";
        schedulerCountdown.innerText = "--:--"; schedulerIndicator.className = "w-2 h-2 rounded-full bg-kite-muted"; return;
    }
    secondsRemainingUntilFetch = configuredMinutes * 60;
    schedulerStatus.innerText = "Monitoring"; schedulerStatus.className = "text-kite-green font-medium";
    schedulerIndicator.className = "w-2 h-2 rounded-full bg-kite-green animate-pulse";
    
    countdownTrackerTimer = setInterval(manageCountdownDisplay, 1000);
    fetchSchedulerTimer = setInterval(async () => {
        await executeBackgroundGitFetch(); secondsRemainingUntilFetch = configuredMinutes * 60; 
    }, configuredMinutes * 60 * 1000);
}

schedulerIntervalSelect.addEventListener('change', reinitializeSchedulerLoop);
document.getElementById('force-sync-btn').addEventListener('click', async (e) => {
    const btn = e.currentTarget; const originalText = btn.innerHTML;
    btn.disabled = true; btn.innerHTML = `<i class="ph ph-circle-notch animate-spin"></i> Processing...`;
    await executeBackgroundGitFetch();
    const configuredMinutes = parseInt(schedulerIntervalSelect.value);
    if (configuredMinutes > 0) secondsRemainingUntilFetch = configuredMinutes * 60; 
    setTimeout(() => { btn.disabled = false; btn.innerHTML = originalText; }, 600);
});

document.getElementById("system-lock-overlay").classList.add("hidden");
initDataPipeline();
reinitializeSchedulerLoop();
