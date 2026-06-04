
"use strict";

// ==========================================
// 1. STATE & ARCHITECTURE
// ==========================================
localforage.config({ 
    name: 'ForensicStudio', 
    storeName: 'studio_core_datastore' 
});

let isForensicMode = false;
let currentSessionId = Date.now().toString();

let savedSessions = {};

// Watchlist State Migration to Multi-Tab Array
// Format: [{ id: "wl_123", name: "Watchlist 1", stocks: [{symbol, addedPrice, currentPrice, ...}] }]
let watchlists = []; 
let activeWatchlistId = null;

let alphaLedger = [];
let forensicArchives = {};
let nseMasterList = []; 
let activeChartInstances = [];
let hasUnsyncedChanges = false;
let currentForensicTarget = null; 

// Global cache to prevent redundant API calls when iterating across tabs
let globalPriceCache = {};

// Prompts & Routing
const JSON_SCHEMA_ROUTER = `
[SYSTEM ROUTING RULES]:
Evaluate the user's input. IF the input is a valid company name or stock ticker, OUTPUT STRICTLY VALID JSON matching Schema A:
{ "type": "stock_analysis", "metadata": { "company_name": "", "ticker": "MUST_BE_EXACT_TICKER_SYMBOL", "classification": "", "analysis_date": "" }, "kpis": { "market_cap": "", "pe_ratio": "", "roe": "", "debt_to_equity": "", "final_verdict": "STRONG BUY / HOLD / AVOID" }, "governance": { "promoter_integrity": "", "red_flags": "" }, "financial_forensics": { "revenue_quality": "", "hidden_debt": "" }, "catalysts_and_sentiment": { "upcoming_triggers": "" } }

IF the input is a general question, concept, or non-stock text, OUTPUT STRICTLY VALID JSON matching Schema B:
{ "type": "general_query", "response": "Your detailed answer formatted in Markdown" }`;

let SMART_MASTER_PROMPT = `You are an elite Institutional Forensic Analyst. ` + JSON_SCHEMA_ROUTER;

const RADAR_PROMPT = `Analyze the provided watchlist data based on the latest news (last 24-48 hrs). OUTPUT STRICTLY VALID JSON. Schema: { "type": "radar", "radar_date": "Today's Date", "market_sentiment_summary": "1-2 sentence overview", "stock_updates": [ { "ticker": "STOCK_NAME", "news_summary": "2-3 crisp bullet points", "sentiment": "Positive / Negative / Neutral", "severity": "High / Medium / Low" } ] }`;

const NETWORK_PROXIES = [
    "https://corsproxy.io/?", 
    "https://api.allorigins.win/raw?url=",
    "https://api.codetabs.com/v1/proxy?quest="
];

const CRITICAL_NSE_FALLBACKS = [
    "RELIANCE,Reliance Industries", "TCS,Tata Consultancy", "INFY,Infosys", 
    "HDFCBANK,HDFC Bank", "ICICIBANK,ICICI Bank", "ITC,ITC Ltd", 
    "SBIN,State Bank of India", "BHARTIARTL,Bharti Airtel"
];

// ==========================================
// 2. UTILITIES & TOASTS
// ==========================================
function escapeHTML(str) { 
    return str ? str.replace(/[&<>'"]/g, tag => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;' }[tag])) : ""; 
}

function utoa(str) { 
    return btoa(unescape(encodeURIComponent(str))); 
}

function atou(str) { 
    return decodeURIComponent(escape(atob(str))); 
}

function showToast(message, type = 'info') {
    const container = document.getElementById('toast-container');
    const toast = document.createElement('div');
    
    let colors = 'bg-kite-panel border-kite-border text-zinc-300';
    let icon = 'ph-info';
    
    if (type === 'error') {
        colors = 'bg-rose-950 border-rose-900 text-rose-400';
        icon = 'ph-warning-circle';
    } else if (type === 'success') {
        colors = 'bg-emerald-950 border-emerald-900 text-emerald-400';
        icon = 'ph-check-circle';
    }

    toast.className = `flex items-center gap-3 px-4 py-3 rounded border shadow-2xl text-xs font-mono toast-enter ${colors}`;
    toast.innerHTML = `<i class="ph-bold ${icon} text-base"></i> <span>${escapeHTML(message)}</span>`;
    
    container.appendChild(toast);
    
    setTimeout(() => { 
        toast.classList.replace('toast-enter', 'toast-exit'); 
        setTimeout(() => toast.remove(), 300); 
    }, 3000);
}

function scrollToBottom() { 
    const c = document.getElementById('chat-container'); 
    c.scrollTop = c.scrollHeight; 
}

function triggerDirtyState(isDirty) {
    hasUnsyncedChanges = isDirty;
    const badge = document.getElementById('sync-badge');
    
    if (isDirty) {
        badge.className = "text-amber-500 flex items-center gap-1 pulse-dirty";
        badge.innerHTML = '<i class="ph-fill ph-warning"></i> Unsynced';
    } else {
        badge.className = "text-kite-green flex items-center gap-1";
        badge.innerHTML = '<i class="ph-fill ph-check-circle"></i> Synced';
    }
}

window.addEventListener('beforeunload', (e) => { 
    if (hasUnsyncedChanges) { 
        e.preventDefault(); 
        e.returnValue = ''; 
    } 
});

// ==========================================
// 3. CORE FETCH ENGINE & ARCHIVES
// ==========================================
async function fetchWithWaterfall(rawUrl) {
    for (let i = 0; i < NETWORK_PROXIES.length; i++) {
        try {
            let targetUrl = `${NETWORK_PROXIES[i]}${encodeURIComponent(rawUrl)}`;
            const res = await fetch(targetUrl);
            if (res.ok) {
                return await res.text();
            }
        } catch (e) { 
            console.warn(`Proxy ${i} failed.`); 
        }
    }
    throw new Error("Proxy Waterfall Exhausted.");
}

async function loadNseMasterList() {
    try {
        const cached = await localforage.getItem('nse_master_list');
        if (cached && cached.length > 0) { 
            nseMasterList = cached; 
            return; 
        }
        
        const csvText = await fetchWithWaterfall("https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv");
        
        if (csvText.includes("SYMBOL")) {
            const lines = csvText.split('\n');
            nseMasterList = lines.slice(1).map(l => { 
                const p = l.split(','); 
                return p[0] ? { symbol: p[0].trim(), name: p[1]?.trim() } : null; 
            }).filter(Boolean);
            
            await localforage.setItem('nse_master_list', nseMasterList);
            return;
        } 
        throw new Error("Invalid CSV Format");
    } catch (e) {
        console.warn("NSE Firewall block. Loading fallback cache.");
        nseMasterList = CRITICAL_NSE_FALLBACKS.map(s => { 
            const p = s.split(','); 
            return {symbol: p[0], name: p[1]}; 
        });
    }
}

async function resolveBSE6DigitToken(symbol) {
    try {
        const searchData = await fetchWithWaterfall(`https://query1.finance.yahoo.com/v1/finance/search?q=${symbol}.BO`);
        const match = searchData.match(/"symbol":"(\d+)\.BO"/);
        if (match) {
            return match[1];
        }
    } catch (e) { 
        return ""; 
    } 
    return "";
}

async function fetchStockDataFromGoogle(symbol) {
    const scriptUrl = 'https://script.google.com/macros/s/AKfycbx4X78LZ3cpk88sgJOwcsZrs1gy1JL-v7Co5_8F_3x3dASUfSW4esRH1PBseAWDOlnfcA/exec'; 
    try {
        const bseCode = await resolveBSE6DigitToken(symbol);
        const res = await fetch(`${scriptUrl}?ticker=${encodeURIComponent(symbol)}&bse=${encodeURIComponent(bseCode)}`);
        
        if(res.ok) {
            return await res.json();
        }
    } catch(e) { 
        console.warn("GAS scrape failed", e); 
    } 
    return null;
}

// ==========================================
// 4. SYSTEM INITIALIZATION & STATE MIGRATION
// ==========================================
async function initApp() {
    try {
        savedSessions = await localforage.getItem('sessions') || {};
        alphaLedger = await localforage.getItem('alpha_ledger') || [];
        forensicArchives = await localforage.getItem('forensic_archives') || {};
        
        // Multi-Tab Watchlist Migration Engine
        const storedWatchlists = await localforage.getItem('watchlists');
        
        if (storedWatchlists && storedWatchlists.length > 0) {
            watchlists = storedWatchlists;
        } else {
            // Check if there is a legacy flat array to migrate
            const oldFlatList = await localforage.getItem('watchlist');
            let initialStocks = [];
            
            if (oldFlatList && oldFlatList.length > 0) {
                initialStocks = oldFlatList.map(s => {
                    if (typeof s === 'string') return { symbol: s, addedAt: Date.now(), addedPrice: null };
                    return s;
                });
            }
            
            // Build the default Watchlist 1
            watchlists = [{
                id: 'wl_default_' + Date.now(),
                name: 'Watchlist 1',
                stocks: initialStocks
            }];
            await localforage.setItem('watchlists', watchlists);
        }
        
        activeWatchlistId = watchlists[0]?.id;
        
        await loadNseMasterList();
        updateModeUI();
        
        // Boot UI
        renderSessionsSidebar();
        renderWatchlistTabs();
        
        // Initial PnL Fetch
        await fetchWatchlistPrices();
        
        // Background Polling Engine (30s) updates prices silently
        setInterval(fetchWatchlistPrices, 30000);
        
        const sessionKeys = Object.keys(savedSessions).sort((a,b) => savedSessions[b].updatedAt - savedSessions[a].updatedAt);
        if (sessionKeys.length > 0) {
            loadSession(sessionKeys[0]);
        } else {
            document.getElementById('new-chat-btn').click();
        }
    } catch (e) { 
        console.error("DB Load Error", e); 
    }
}

// Factory Reset
document.getElementById('logout-btn').onclick = () => { 
    if(confirm("Are you sure you want to factory reset local memory? All unsynced data will be wiped.")) {
        localforage.clear().then(() => {
            location.reload();
        });
    }
};

// ==========================================
// 5. OMNI-MODEL API ENGINE
// ==========================================
async function fetchAIWithBackoff(contentsArray, sysInstruction) {
    const providerModelStr = localStorage.getItem('fs_api_model') || 'google|gemini-3.5-flash';
    const [provider, modelId] = providerModelStr.split('|');
    const apiKey = localStorage.getItem(`fs_api_key_${provider}`);
    
    if (!apiKey) {
        throw new Error(`API_KEY_MISSING`);
    }

    const maxRetries = 5; 
    const delays = [1000, 2000, 4000, 8000, 16000];
    
    for (let i = 0; i <= maxRetries; i++) {
        try {
            let url, headers = { 'Content-Type': 'application/json' }, bodyObj;

            if (provider === 'google') {
                url = `https://generativelanguage.googleapis.com/v1beta/models/${modelId}:generateContent?key=${apiKey}`;
                bodyObj = { 
                    contents: contentsArray.map(msg => ({ 
                        role: msg.role === 'ai' ? 'model' : 'user', 
                        parts: [{ text: msg.text }] 
                    })),
                    systemInstruction: { parts: [{ text: sysInstruction }] }, 
                    generationConfig: { temperature: 0.1 } 
                };
            } else if (provider === 'openai') {
                url = `https://api.openai.com/v1/chat/completions`;
                headers['Authorization'] = `Bearer ${apiKey}`;
                
                const messages = [{ role: 'system', content: sysInstruction }];
                contentsArray.forEach(m => {
                    messages.push({ role: m.role === 'ai' ? 'assistant' : 'user', content: m.text });
                });
                
                bodyObj = { 
                    model: modelId, 
                    messages: messages, 
                    temperature: 0.1 
                };
            } else if (provider === 'anthropic') {
                url = `https://api.anthropic.com/v1/messages`;
                headers['x-api-key'] = apiKey; 
                headers['anthropic-version'] = '2023-06-01'; 
                headers['anthropic-dangerous-direct-browser-access'] = 'true';
                
                const messages = contentsArray.map(m => ({ 
                    role: m.role === 'ai' ? 'assistant' : 'user', 
                    content: m.text 
                }));
                
                bodyObj = { 
                    model: modelId, 
                    system: sysInstruction, 
                    max_tokens: 4000, 
                    messages: messages, 
                    temperature: 0.1 
                };
            }

            const res = await fetch(url, { method: 'POST', headers, body: JSON.stringify(bodyObj) });
            
            if (res.status === 429) {
                const delay = parseInt(res.headers.get('retry-after') || delays[i]/1000) * 1000;
                await new Promise(r => setTimeout(r, delay)); 
                continue; 
            }
            
            const data = await res.json();
            
            if (!res.ok) {
                throw new Error("API Error");
            }

            if (provider === 'google') return data.candidates[0].content.parts[0].text;
            if (provider === 'openai') return data.choices[0].message.content;
            if (provider === 'anthropic') return data.content[0].text;

        } catch (error) { 
            if (i === maxRetries || error.message.includes("API_KEY_MISSING")) {
                throw error; 
            }
            await new Promise(r => setTimeout(r, delays[i])); 
        }
    }
}

// ==========================================
// 6. GHOST INJECTION & CHAT EXECUTION
// ==========================================
document.getElementById('search-form').onsubmit = async (e) => {
    e.preventDefault();
    const inputEl = document.getElementById('search-input');
    const q = inputEl.value.trim();
    if (!q) return;

    addMessageToDOM("user", q);
    inputEl.value = ""; 
    document.getElementById('run-btn').disabled = true;

    let contentsArray = [];
    let sysInstruction = "";
    const memoryContext = alphaLedger.length > 0 ? `\n[PAST MEMORY]: ${JSON.stringify(alphaLedger.slice(-10))}` : "";

    if (isForensicMode) {
        let activeTarget = currentForensicTarget || q.toUpperCase().split(' ')[0];
        
        let injectedDataText = "";
        const loaderId = showLoadingUI(`Extracting filings for ${activeTarget}...`);
        const fetchedData = await fetchStockDataFromGoogle(activeTarget);
        removeLoader(loaderId);

        if (fetchedData && !fetchedData.error) {
            forensicArchives[activeTarget] = fetchedData;
            await localforage.setItem('forensic_archives', forensicArchives);
            injectedDataText = `\n[DATA]: Base analysis strictly on this JSON:\n${JSON.stringify(fetchedData)}`;
        }
        
        contentsArray = [{ role: "user", text: `Target: ${q}${injectedDataText}` }];
        sysInstruction = SMART_MASTER_PROMPT + memoryContext;
    } else {
        const historyArr = savedSessions[currentSessionId]?.history || [];
        contentsArray = historyArr.slice(-4).map(msg => ({ role: msg.role, text: msg.text })); 
        contentsArray.push({ role: "user", text: q });
        sysInstruction = "You are a financial assistant. Use markdown." + memoryContext;
    }

    await saveSessionLocal("user", q);
    await triggerAI(contentsArray, sysInstruction);
};

document.getElementById('run-radar-btn').onclick = async () => {
    // Collect all symbols from all watchlists for a macro radar
    const allSymbols = new Set();
    watchlists.forEach(wl => wl.stocks.forEach(s => allSymbols.add(s.symbol)));
    
    if (allSymbols.size === 0) {
        return showToast("No assets in any Watchlist to scan.", "error");
    }
    
    isForensicMode = true; 
    updateModeUI();
    
    const symbolsArray = Array.from(allSymbols);
    const promptText = `Watchlist: ${symbolsArray.join(', ')}. Date: ${new Date().toISOString()}`;
    const uiText = `RUN RADAR: Scrape Data for ${symbolsArray.length} Stocks`;
    
    addMessageToDOM("user", `<span class="text-kite-orange font-bold flex items-center gap-2"><i class="ph-bold ph-broadcast animate-pulse"></i> ${escapeHTML(uiText)}</span>`, true);
    
    await saveSessionLocal("user", uiText);
    await triggerAI([{ role: "user", text: promptText }], RADAR_PROMPT);
};

async function triggerAI(contentsArray, sysInstruction) {
    const loaderId = showLoadingUI("Processing...");
    try {
        const responseText = await fetchAIWithBackoff(contentsArray, sysInstruction);
        removeLoader(loaderId);
        addMessageToDOM("ai", responseText);
        await saveSessionLocal("ai", responseText);
    } catch (err) {
        removeLoader(loaderId);
        if (err.message.includes("MISSING")) {
            document.getElementById('settings-btn').click();
        } else {
            addMessageToDOM("ai", `**Error:** ${escapeHTML(err.message)}`);
        }
    } finally { 
        document.getElementById('run-btn').disabled = false; 
    }
}

async function saveSessionLocal(role, text) {
    if (!savedSessions[currentSessionId]) {
        let title = "New Analysis";
        if (role === 'user') {
            title = text.replace(/<[^>]*>?/gm, '').substring(0, 25) + "...";
        }
        savedSessions[currentSessionId] = { 
            id: currentSessionId, 
            title: title, 
            history: [], 
            updatedAt: Date.now() 
        };
    }
    
    savedSessions[currentSessionId].history.push({ role, text });
    savedSessions[currentSessionId].updatedAt = Date.now();
    await localforage.setItem('sessions', savedSessions);
    triggerDirtyState(true); 
    renderSessionsSidebar();
    
    if (role === 'ai') {
        const json = extractJSON(text);
        if (json && json.parsed && json.parsed.metadata) {
            alphaLedger.push({ 
                date: new Date().toISOString().split('T')[0], 
                ticker: json.parsed.metadata.ticker, 
                verdict: json.parsed.kpis?.final_verdict 
            });
            await localforage.setItem('alpha_ledger', alphaLedger);
        }
    }
}

function loadSession(id) {
    if (!savedSessions[id]) return;
    currentSessionId = id;
    
    document.getElementById('chat-container').innerHTML = ""; 
    activeChartInstances = [];
    
    const session = savedSessions[id];
    session.history.forEach(m => {
        addMessageToDOM(m.role, m.text, m.text.includes('<span'));
    });
}

// ==========================================
// 7. UI MODES & RESPONSIVE SIDEBAR
// ==========================================
const mobileMenuBtn = document.getElementById('mobile-menu-btn');
const mobileCloseBtn = document.getElementById('mobile-close-sidebar');
const sidebarBackdrop = document.getElementById('sidebar-backdrop');
const globalSidebar = document.getElementById('global-sidebar');

function toggleMobileSidebar(show) {
    if (show) {
        globalSidebar.classList.remove('-translate-x-full');
        sidebarBackdrop.classList.remove('hidden');
        setTimeout(() => sidebarBackdrop.classList.remove('opacity-0'), 10);
    } else {
        globalSidebar.classList.add('-translate-x-full');
        sidebarBackdrop.classList.add('opacity-0');
        setTimeout(() => sidebarBackdrop.classList.add('hidden'), 300);
    }
}

if (mobileMenuBtn) mobileMenuBtn.onclick = () => toggleMobileSidebar(true);
if (mobileCloseBtn) mobileCloseBtn.onclick = () => toggleMobileSidebar(false);
if (sidebarBackdrop) sidebarBackdrop.onclick = () => toggleMobileSidebar(false);

function updateModeUI() {
    const genBtn = document.getElementById('mode-general');
    const forBtn = document.getElementById('mode-forensic');
    const searchInput = document.getElementById('search-input');
    
    if (isForensicMode) {
        forBtn.className = "px-2 md:px-3 py-1 rounded text-[10px] md:text-[11px] font-semibold transition-all bg-kite-border text-zinc-100";
        genBtn.className = "px-2 md:px-3 py-1 rounded text-[10px] md:text-[11px] font-semibold transition-all text-zinc-400 hover:text-zinc-200";
        searchInput.placeholder = "Analyze target asset (e.g. RELIANCE)...";
    } else {
        genBtn.className = "px-2 md:px-3 py-1 rounded text-[10px] md:text-[11px] font-semibold transition-all bg-kite-border text-zinc-100";
        forBtn.className = "px-2 md:px-3 py-1 rounded text-[10px] md:text-[11px] font-semibold transition-all text-zinc-400 hover:text-zinc-200";
        searchInput.placeholder = "Ask anything...";
    }
}

document.getElementById('mode-general').onclick = () => { 
    isForensicMode = false; 
    updateModeUI(); 
};

document.getElementById('mode-forensic').onclick = () => { 
    isForensicMode = true; 
    updateModeUI(); 
};

document.getElementById('new-chat-btn').onclick = () => {
    currentSessionId = Date.now().toString();
    activeChartInstances = []; 
    currentForensicTarget = null;
    updateWatchlistHeaderButton(); 
    
    document.getElementById('chat-container').innerHTML = `
        <div id="welcome-screen" class="flex flex-col items-center justify-center h-full text-center opacity-50 px-4">
            <i class="ph ph-terminal-window text-5xl md:text-6xl text-zinc-600 mb-2 md:mb-4"></i>
            <h2 class="text-[10px] md:text-xs font-mono tracking-widest text-zinc-400 uppercase">Omni-Engine Ready</h2>
        </div>
    `;
    renderSessionsSidebar();
    
    if (window.innerWidth < 768) toggleMobileSidebar(false);
};

function renderSessionsSidebar() {
    const list = document.getElementById('session-list'); 
    list.innerHTML = '';
    
    const sortedSessions = Object.values(savedSessions).sort((a,b) => b.updatedAt - a.updatedAt);
    
    sortedSessions.forEach(s => {
        const div = document.createElement('div');
        const isActive = s.id === currentSessionId;
        
        div.className = `flex justify-between items-center group px-3 py-2 rounded text-xs font-mono cursor-pointer transition-colors ${isActive ? 'bg-zinc-800 text-zinc-100' : 'text-zinc-400 hover:bg-zinc-900'}`;
        
        div.innerHTML = `
            <span class="truncate flex-1">${escapeHTML(s.title)}</span> 
            <button class="text-zinc-600 hover:text-rose-500 focus:outline-none md:opacity-0 md:group-hover:opacity-100 transition-opacity">
                <i class="ph-bold ph-trash"></i>
            </button>
        `;
        
        div.onclick = (e) => {
            if(e.target.closest('button')) { 
                delete savedSessions[s.id]; 
                localforage.setItem('sessions', savedSessions); 
                triggerDirtyState(true); 
                
                if(s.id === currentSessionId) {
                    document.getElementById('new-chat-btn').click(); 
                } else {
                    renderSessionsSidebar(); 
                }
                return; 
            }
            
            loadSession(s.id);
            renderSessionsSidebar();
            
            if (window.innerWidth < 768) toggleMobileSidebar(false);
        };
        
        list.appendChild(div);
    });
}

// ==========================================
// 8. WATCHLISTS, TABS & PNL LOGIC
// ==========================================

// Overlay Controllers
const watchlistScreen = document.getElementById('watchlist-screen');
const openWatchlistBtn = document.getElementById('open-watchlist-btn');
const closeWatchlistBtn = document.getElementById('close-watchlist-btn');

openWatchlistBtn.onclick = () => {
    renderWatchlistTabs();
    renderWatchlistGrid();
    watchlistScreen.classList.remove('hidden');
    setTimeout(() => {
        watchlistScreen.classList.remove('scale-95', 'opacity-0');
        watchlistScreen.classList.add('scale-100', 'opacity-100');
    }, 10);
};

function hideWatchlistScreen() {
    watchlistScreen.classList.remove('scale-100', 'opacity-100');
    watchlistScreen.classList.add('scale-95', 'opacity-0');
    setTimeout(() => {
        watchlistScreen.classList.add('hidden');
    }, 300);
}

closeWatchlistBtn.onclick = hideWatchlistScreen;

// Tab Management
window.createNewWatchlist = async () => {
    const newId = 'wl_' + Date.now();
    const newName = `Watchlist ${watchlists.length + 1}`;
    
    watchlists.push({ id: newId, name: newName, stocks: [] });
    activeWatchlistId = newId;
    
    await localforage.setItem('watchlists', watchlists);
    triggerDirtyState(true);
    
    renderWatchlistTabs();
    renderWatchlistGrid();
};

window.renameWatchlist = async (id, e) => {
    e.stopPropagation(); // Don't trigger tab selection
    const wl = watchlists.find(w => w.id === id);
    if (!wl) return;
    
    const newName = prompt("Rename Portfolio Ledger:", wl.name);
    if (newName && newName.trim() !== "") {
        wl.name = newName.trim();
        await localforage.setItem('watchlists', watchlists);
        triggerDirtyState(true);
        renderWatchlistTabs();
    }
};

window.deleteWatchlist = async (id, e) => {
    e.stopPropagation();
    if (watchlists.length <= 1) {
        return showToast("Cannot delete the final tracking ledger.", "warn");
    }
    
    if (confirm("Delete this entire portfolio ledger and all its assets?")) {
        watchlists = watchlists.filter(w => w.id !== id);
        if (activeWatchlistId === id) {
            activeWatchlistId = watchlists[0].id;
        }
        
        await localforage.setItem('watchlists', watchlists);
        triggerDirtyState(true);
        
        renderWatchlistTabs();
        renderWatchlistGrid();
    }
};

function renderWatchlistTabs() {
    const container = document.getElementById('watchlist-tabs-container');
    if (!container) return;
    container.innerHTML = '';
    
    watchlists.forEach(wl => {
        const isActive = wl.id === activeWatchlistId;
        const tab = document.createElement('div');
        
        // Tab Styling
        let baseStyle = "px-4 py-2 cursor-pointer text-xs font-bold uppercase tracking-widest border-b-2 transition-colors flex items-center gap-2 select-none group ";
        if (isActive) {
            baseStyle += "border-kite-blue text-white bg-kite-bg/30";
        } else {
            baseStyle += "border-transparent text-zinc-500 hover:text-zinc-300";
        }
        
        tab.className = baseStyle;
        
        tab.innerHTML = `
            <span class="truncate max-w-[100px] md:max-w-[150px]">${escapeHTML(wl.name)}</span>
            <button class="text-zinc-500 hover:text-white p-0.5 focus:outline-none md:opacity-0 md:group-hover:opacity-100 transition-opacity" onclick="renameWatchlist('${wl.id}', event)" title="Rename">
                <i class="ph-bold ph-pencil-simple"></i>
            </button>
            ${watchlists.length > 1 ? `
                <button class="text-zinc-500 hover:text-kite-red p-0.5 focus:outline-none md:opacity-0 md:group-hover:opacity-100 transition-opacity" onclick="deleteWatchlist('${wl.id}', event)" title="Delete">
                    <i class="ph-bold ph-trash"></i>
                </button>
            ` : ''}
        `;
        
        tab.onclick = () => { 
            activeWatchlistId = wl.id; 
            renderWatchlistTabs(); 
            renderWatchlistGrid(); 
        };
        
        container.appendChild(tab);
    });
    
    // Add New Tab Button
    const addBtn = document.createElement('div');
    addBtn.className = "px-3 py-2 cursor-pointer text-xs font-bold text-zinc-500 hover:text-white transition-colors flex items-center border-b-2 border-transparent select-none";
    addBtn.innerHTML = `<i class="ph-bold ph-plus text-base"></i>`;
    addBtn.onclick = createNewWatchlist;
    container.appendChild(addBtn);
}

// Watchlist Search & Add Logic
document.getElementById('new-stock-input').addEventListener('input', function() {
    const val = this.value.toUpperCase().trim();
    const dropdown = document.getElementById('autocomplete-dropdown');
    dropdown.innerHTML = ''; 
    
    if (!val) { 
        dropdown.classList.add('hidden'); 
        return; 
    }
    
    const matches = nseMasterList.filter(s => 
        s.symbol.includes(val) || (s.name && s.name.toUpperCase().includes(val))
    ).slice(0, 30); 
    
    if (matches.length === 0) { 
        dropdown.classList.add('hidden'); 
        return; 
    }

    matches.forEach(s => {
        const div = document.createElement('div'); 
        div.className = "px-4 py-2 hover:bg-zinc-800 cursor-pointer flex justify-between items-center border-b border-kite-border/50 text-xs transition-colors";
        div.innerHTML = `
            <span class="font-bold text-kite-blue font-mono">${s.symbol}</span> 
            <span class="text-[10px] text-zinc-500 truncate ml-4 flex-1 text-right">${s.name || ''}</span>
        `;
        
        div.onclick = async () => { 
            const activeWl = watchlists.find(w => w.id === activeWatchlistId);
            
            if (activeWl && !activeWl.stocks.some(w => w.symbol === s.symbol)) { 
                activeWl.stocks.push({ symbol: s.symbol, addedAt: Date.now(), addedPrice: null }); 
                await localforage.setItem('watchlists', watchlists); 
                triggerDirtyState(true); 
                
                // Immediately fetch to get the baseline addedPrice
                await fetchWatchlistPrices(); 
                showToast(`${s.symbol} injected into ${activeWl.name}.`, "success"); 
            } else {
                showToast("Asset already exists in this ledger.", "warn");
            }
            
            document.getElementById('new-stock-input').value = ''; 
            dropdown.classList.add('hidden'); 
        };
        
        dropdown.appendChild(div);
    }); 
    dropdown.classList.remove('hidden');
});

// Hide dropdown if clicked outside
document.addEventListener('click', (e) => {
    const dropdown = document.getElementById('autocomplete-dropdown');
    const input = document.getElementById('new-stock-input');
    if (dropdown && !dropdown.contains(e.target) && e.target !== input) {
        dropdown.classList.add('hidden');
    }
});

// Grid UI Logic
window.removeWatchlistItem = async (symbol, e) => {
    e.stopPropagation(); 
    const activeWl = watchlists.find(w => w.id === activeWatchlistId);
    if (!activeWl) return;
    
    activeWl.stocks = activeWl.stocks.filter(w => w.symbol !== symbol);
    await localforage.setItem('watchlists', watchlists);
    triggerDirtyState(true);
    
    if (currentForensicTarget === symbol) {
        currentForensicTarget = null;
        updateWatchlistHeaderButton();
    }
    
    renderWatchlistGrid();
};

function selectWatchlistItem(itemObj) {
    currentForensicTarget = itemObj.symbol;
    updateWatchlistHeaderButton();
    hideWatchlistScreen();

    // Auto-switch & Auto-fill Forensic Prompt
    if (!isForensicMode) document.getElementById('mode-forensic').click();
    
    const input = document.getElementById('search-input');
    input.value = `Execute full structural institutional financial forensics evaluation loop data scan against tracking profile target parameters on asset: ${itemObj.symbol}`;
    input.focus();
}

async function fetchWatchlistPrices() {
    // Collect all unique symbols across ALL watchlists
    const allSymbols = new Set();
    watchlists.forEach(wl => wl.stocks.forEach(s => allSymbols.add(s.symbol)));
    
    if (allSymbols.size === 0) {
        updateWatchlistHeaderButton();
        if (!watchlistScreen.classList.contains('hidden')) renderWatchlistGrid();
        return;
    }
    
    try {
        const queryStr = Array.from(allSymbols).map(s => s.includes('USD') || s.includes('BTC') ? s : `${s}.NS`).join(',');
        const queryUrl = `https://query1.finance.yahoo.com/v7/finance/quote?symbols=${queryStr}`;
        
        const data = await fetchWithWaterfall(queryUrl);
        const quotes = JSON.parse(data).quoteResponse.result;
        
        let needsDbUpdate = false;

        // Cache the live responses
        quotes.forEach(q => {
            const rawSym = q.symbol.replace('.NS', '');
            globalPriceCache[rawSym] = {
                price: q.regularMarketPrice,
                change: q.regularMarketChangePercent
            };
        });

        // Distribute cached prices mathematically across all watchlists
        watchlists.forEach(wl => {
            wl.stocks.forEach(w => {
                const cacheData = globalPriceCache[w.symbol];
                
                if (cacheData) {
                    w.currentPrice = cacheData.price;
                    w.dayChange = cacheData.change;
                    
                    // Stamp entry baseline on first successful fetch
                    if (w.addedPrice === null || w.addedPrice === undefined) {
                        w.addedPrice = w.currentPrice;
                        needsDbUpdate = true;
                    }
                    
                    // Calculate running PnL
                    if (w.addedPrice && w.addedPrice > 0) {
                        w.totalPnl = ((w.currentPrice - w.addedPrice) / w.addedPrice) * 100;
                    } else {
                        w.totalPnl = 0;
                    }
                }
            });
        });
        
        if (needsDbUpdate) {
            await localforage.setItem('watchlists', watchlists);
            triggerDirtyState(true);
        }

        updateWatchlistHeaderButton();
        
        // Re-render grid if overlay is active
        if (!watchlistScreen.classList.contains('hidden')) {
            renderWatchlistGrid();
        }

    } catch (err) {
        console.warn("Pricing fetch cascade failed", err);
    }
}

function updateWatchlistHeaderButton() {
    const symbolSpan = document.getElementById('header-target-symbol');
    const pnlSpan = document.getElementById('header-target-pnl');

    if (!currentForensicTarget) {
        symbolSpan.textContent = "No Target";
        symbolSpan.className = "text-[10px] md:text-xs font-bold text-zinc-400 truncate w-full text-left transition-colors group-hover:text-kite-blue";
        pnlSpan.classList.add('hidden');
        return;
    }

    // Hunt for the active target's data in the cache or watchlists
    let activeData = null;
    for (const wl of watchlists) {
        const found = wl.stocks.find(w => w.symbol === currentForensicTarget);
        if (found) { activeData = found; break; }
    }
    
    if (activeData) {
        symbolSpan.textContent = activeData.symbol;
        symbolSpan.className = "text-[10px] md:text-xs font-bold text-zinc-200 truncate w-full text-left transition-colors group-hover:text-kite-blue";
        
        const pColor = activeData.totalPnl >= 0 ? 'text-kite-green' : 'text-kite-red';
        const pSign = activeData.totalPnl > 0 ? '+' : '';
        const pStr = activeData.totalPnl !== undefined ? `${pSign}${activeData.totalPnl.toFixed(2)}%` : '--';
        const livePrc = activeData.currentPrice ? `₹${activeData.currentPrice.toFixed(2)}` : '--';

        pnlSpan.innerHTML = `${livePrc} <span class="mx-1 opacity-50">•</span> <span class="${pColor}">${pStr}</span>`;
        pnlSpan.classList.remove('hidden');
    } else {
        symbolSpan.textContent = currentForensicTarget;
        pnlSpan.classList.add('hidden');
    }
}

function renderWatchlistGrid() {
    const grid = document.getElementById('watchlist-grid');
    if (!grid) return;
    grid.innerHTML = '';

    const activeWl = watchlists.find(w => w.id === activeWatchlistId);

    if (!activeWl || activeWl.stocks.length === 0) {
        grid.innerHTML = `<div class="col-span-full text-center py-10 text-zinc-500 font-mono text-xs border border-dashed border-kite-border rounded-xl mx-4 mt-4 h-32 flex items-center justify-center">No assets bound to this tracking ledger. Use the search input above to inject targets.</div>`;
        return;
    }

    activeWl.stocks.forEach(w => {
        const priceStr = w.currentPrice ? `₹${w.currentPrice.toFixed(2)}` : 'Loading...';
        const addedPriceStr = w.addedPrice ? `₹${w.addedPrice.toFixed(2)}` : '--';
        
        const dColor = w.dayChange >= 0 ? 'text-kite-green' : 'text-kite-red';
        const dSign = w.dayChange > 0 ? '+' : '';
        const dStr = w.dayChange !== undefined ? `${dSign}${w.dayChange.toFixed(2)}%` : '--';

        const pColor = w.totalPnl >= 0 ? 'text-kite-green bg-kite-green/10 border-kite-green/20' : 'text-kite-red bg-kite-red/10 border-kite-red/20';
        const pSign = w.totalPnl > 0 ? '+' : '';
        const pStr = w.totalPnl !== undefined ? `${pSign}${w.totalPnl.toFixed(2)}%` : '--';
        
        const isActive = currentForensicTarget === w.symbol;
        const activeBorder = isActive ? 'border-kite-blue shadow-[0_0_15px_rgba(67,126,253,0.15)]' : 'border-kite-border hover:border-zinc-500';

        const card = document.createElement('div');
        card.className = `bg-kite-panel border rounded-xl p-4 flex flex-col cursor-pointer transition-all ${activeBorder}`;
        
        card.innerHTML = `
            <div class="flex justify-between items-start mb-4">
                <div class="flex flex-col">
                    <span class="font-bold text-sm md:text-base text-white tracking-tight">${w.symbol}</span>
                    <span class="text-[9px] font-mono text-zinc-500 uppercase tracking-widest mt-0.5">Entry: ${addedPriceStr}</span>
                </div>
                <button class="text-zinc-600 hover:text-kite-red p-1 rounded-md transition-colors focus:outline-none hover:bg-kite-red/10" onclick="removeWatchlistItem('${w.symbol}', event)">
                    <i class="ph-bold ph-x text-sm"></i>
                </button>
            </div>
            
            <div class="flex justify-between items-end mt-auto">
                <div class="flex flex-col">
                    <span class="text-sm font-mono text-zinc-200">${priceStr}</span>
                    <span class="text-[10px] font-mono ${dColor} mt-0.5">${dStr} <span class="text-zinc-600">(Day)</span></span>
                </div>
                <div class="px-2.5 py-1.5 rounded border text-xs font-bold font-mono ${pColor}">
                    ${pStr}
                </div>
            </div>
        `;

        card.onclick = () => selectWatchlistItem(w);
        grid.appendChild(card);
    });
}

// ==========================================
// 9. DOM RENDERERS & CHARTS
// ==========================================
function showLoadingUI(msg) {
    const id = 'loader-' + Date.now();
    const container = document.getElementById('chat-container');
    const div = document.createElement('div'); 
    
    div.id = id; 
    div.className = "flex justify-center my-4";
    div.innerHTML = `
        <div class="bg-kite-panel border border-kite-border px-4 py-2 rounded-full flex items-center gap-2 shadow-lg">
            <i class="ph-bold ph-circle-notch animate-spin text-kite-blue"></i>
            <span class="text-[10px] text-zinc-400 font-mono tracking-widest uppercase">${escapeHTML(msg)}</span>
        </div>
    `;
    
    container.appendChild(div); 
    scrollToBottom(); 
    return id;
}

function removeLoader(id) { 
    const loader = document.getElementById(id); 
    if(loader) loader.remove(); 
}

function addMessageToDOM(role, text, isRawHtml = false) {
    const welcomeScreen = document.getElementById('welcome-screen'); 
    if(welcomeScreen) welcomeScreen.style.display = 'none';
    
    const container = document.getElementById('chat-container');
    const div = document.createElement('div');
    
    if (role === 'user') {
        div.className = "p-3 md:p-4 rounded-xl w-full mx-auto bg-zinc-800/50 ml-auto max-w-[90%] md:max-w-[85%] border border-zinc-700/50";
        div.innerHTML = `<div class="text-xs font-mono text-zinc-200 break-words">${isRawHtml ? text : escapeHTML(text)}</div>`; 
        container.appendChild(div); 
        scrollToBottom(); 
    } else {
        div.className = "p-3 md:p-4 rounded-xl w-full mx-auto bg-kite-panel border border-kite-border shadow-lg max-w-4xl overflow-x-auto";
        const payload = formatAIText(text); 
        div.innerHTML = payload.html; 
        container.appendChild(div);
        
        if (payload.chartId) {
            mountTradingViewChart(payload.chartId, payload.ticker);
        }
        scrollToBottom();
    }
}

// Utilizing safe RegExp constructors
function extractJSON(text) { 
    try { 
        let cleaned = text.replace(new RegExp('`{3}json\\n?', 'g'), '').replace(new RegExp('`{3}\\n?', 'g'), '').trim(); 
        const start = cleaned.indexOf('{');
        const end = cleaned.lastIndexOf('}');
        
        if (start !== -1 && end !== -1) {
            return { parsed: JSON.parse(cleaned.substring(start, end + 1)) }; 
        }
    } catch (e) {
        console.warn("JSON Extraction Failed", e);
    } 
    return null; 
}

function formatAIText(text) {
    if (!text) return { html: '' };
    
    const jsonExtraction = extractJSON(text);
    
    if (jsonExtraction && jsonExtraction.parsed) {
        const parsed = jsonExtraction.parsed;
        
        if (parsed.type === 'general_query') {
            return { html: `<div class="prose prose-invert max-w-none text-xs break-words">${marked.parse(parsed.response)}</div>` };
        }
        if (parsed.type === 'radar' || parsed.stock_updates) {
            return { html: buildRadarHTML(parsed) };
        }
        if (parsed.type === 'stock_analysis' || parsed.kpis) {
            return buildForensicHTML(parsed);
        }
    } 
    
    return { html: `<div class="prose prose-invert max-w-none text-xs break-words">${marked.parse(text)}</div>` };
}

function buildForensicHTML(obj) {
    let html = '<div class="flex flex-col gap-3 font-sans">';
    let ticker = obj.metadata?.ticker;
    let chartId = `chart-${Date.now()}`;
    
    if (obj.metadata) {
        if (!currentForensicTarget) {
            currentForensicTarget = ticker;
            updateWatchlistHeaderButton();
        }
        
        let verdictStyle = "text-zinc-300 border-zinc-600";
        
        if (obj.kpis?.final_verdict?.includes('BUY')) {
            verdictStyle = "text-kite-green border-kite-green";
        } else if (obj.kpis?.final_verdict?.includes('AVOID')) {
            verdictStyle = "text-kite-red border-kite-red";
        }
        
        html += `
            <div class="flex flex-col sm:flex-row sm:justify-between sm:items-center border-b border-kite-border pb-2 gap-2">
                <div class="flex-1 min-w-0">
                    <h2 class="text-sm md:text-base font-bold text-white truncate">${escapeHTML(obj.metadata.company_name)}</h2>
                    <p class="text-[10px] font-mono text-kite-blue truncate">${escapeHTML(ticker)}</p>
                </div>
                <div class="px-3 py-1 border rounded text-[10px] font-bold uppercase tracking-widest text-center self-start sm:self-auto ${verdictStyle}">
                    ${escapeHTML(obj.kpis?.final_verdict || 'N/A')}
                </div>
            </div>
            <div id="${chartId}" class="tv-chart-container"></div>
        `;
    }
    
    if (obj.kpis) {
        html += `<div class="grid grid-cols-2 md:grid-cols-4 gap-2">`;
        for (const [key, value] of Object.entries(obj.kpis)) { 
            if (key !== 'final_verdict') {
                html += `
                    <div class="bg-kite-bg p-2 rounded border border-kite-border flex flex-col justify-center min-h-[60px]">
                        <div class="text-[9px] text-zinc-500 uppercase tracking-widest mb-1 font-bold break-words leading-tight">${escapeHTML(key.replace(/_/g, ' '))}</div>
                        <div class="text-xs font-mono text-zinc-200 break-words">${escapeHTML(String(value))}</div>
                    </div>
                `; 
            }
        }
        html += `</div>`;
    }
    
    const sections = ['governance', 'financial_forensics', 'catalysts_and_sentiment'];
    sections.forEach(section => {
        if (obj[section]) {
            html += `
                <div class="border border-kite-border rounded bg-kite-bg/30 p-3 space-y-2">
                    <h3 class="text-[10px] font-bold text-zinc-400 uppercase tracking-wider mb-2 break-words">${escapeHTML(section.replace(/_/g, ' '))}</h3>
            `;
            for (const [key, value] of Object.entries(obj[section])) { 
                html += `
                    <div class="border-l-2 border-zinc-700 pl-3">
                        <h4 class="text-[9px] font-mono text-zinc-500 uppercase break-words">${escapeHTML(key.replace(/_/g, ' '))}</h4>
                        <p class="text-[11px] text-zinc-300 whitespace-pre-wrap break-words leading-relaxed">${escapeHTML(String(value))}</p>
                    </div>
                `; 
            }
            html += `</div>`;
        }
    }); 
    
    return { html: html + '</div>', ticker, chartId };
}

function buildRadarHTML(obj) {
    let html = `
        <div class="space-y-3">
            <div class="border-b border-kite-border pb-2">
                <h2 class="text-sm font-bold text-kite-orange flex items-center gap-2"><i class="ph-bold ph-broadcast"></i> Radar: ${escapeHTML(obj.radar_date)}</h2>
                <p class="text-xs text-zinc-400 mt-1 break-words">${escapeHTML(obj.market_sentiment_summary)}</p>
            </div>
            <div class="grid grid-cols-1 md:grid-cols-2 gap-3">
    `;
    
    if (obj.stock_updates) {
        obj.stock_updates.forEach(update => { 
            const badgeStyle = update.sentiment === 'Positive' ? 'text-kite-green border-kite-green' : 'text-kite-red border-kite-red';
            html += `
                <div class="border border-kite-border bg-kite-bg/50 p-3 rounded">
                    <div class="flex justify-between items-start mb-2 gap-2">
                        <h3 class="font-bold text-zinc-100 text-xs truncate flex-1">${escapeHTML(update.ticker)}</h3>
                        <span class="text-[9px] uppercase font-bold px-1.5 py-0.5 rounded border shrink-0 ${badgeStyle}">
                            ${escapeHTML(update.sentiment)}
                        </span>
                    </div>
                    <p class="text-[11px] text-zinc-300 leading-relaxed break-words">${escapeHTML(update.news_summary)}</p>
                </div>
            `; 
        });
    }
    return html + '</div></div>';
}

async function mountTradingViewChart(id, ticker) {
    const container = document.getElementById(id); 
    if (!container) return;
    
    try {
        const yahooTicker = ticker.includes('USD') ? ticker : `${ticker}.NS`;
        const queryUrl = `https://query1.finance.yahoo.com/v8/finance/chart/${yahooTicker}?range=1y&interval=1d`;
        
        const dataText = await fetchWithWaterfall(queryUrl); 
        const result = JSON.parse(dataText).chart.result[0];
        const indicators = result.indicators.quote[0];
        
        const formattedData = result.timestamp.map((time, i) => ({ 
            time: time, 
            open: indicators.open[i], 
            high: indicators.high[i], 
            low: indicators.low[i], 
            close: indicators.close[i] 
        })).filter(d => d.open !== null);
        
        const chart = LightweightCharts.createChart(container, { 
            layout: { background: { color: 'transparent' }, textColor: '#a1a1aa' }, 
            grid: { vertLines: { color: '#2c2c2e' }, horzLines: { color: '#2c2c2e' } }, 
            timeScale: { borderColor: '#2c2c2e' } 
        });
        
        const series = chart.addCandlestickSeries({ 
            upColor: '#00b067', 
            downColor: '#eb5757', 
            borderVisible: false, 
            wickUpColor: '#00b067', 
            wickDownColor: '#eb5757' 
        });
        
        series.setData(formattedData);
        chart.timeScale().fitContent(); 
        
        activeChartInstances.push({ id: id, chart: chart }); 
        
        setTimeout(scrollToBottom, 100);
    } catch (e) { 
        container.innerHTML = `<div class="flex items-center justify-center h-full w-full"><span class="text-[10px] text-kite-red">Chart Error</span></div>`; 
    }
}

window.addEventListener('resize', () => {
    activeChartInstances.forEach(instance => { 
        const element = document.getElementById(instance.id); 
        if(element) {
            const newHeight = window.innerWidth >= 768 ? 350 : 280;
            instance.chart.resize(element.clientWidth, newHeight); 
        }
    });
});

// ==========================================
// 10. GIT VAULT & EXPORT BLOB
// ==========================================
document.getElementById('download-db-btn').onclick = () => {
    if (isForensicMode && currentForensicTarget && forensicArchives[currentForensicTarget]) {
        const blob = new Blob([JSON.stringify(forensicArchives[currentForensicTarget], null, 2)], { type: "application/json" });
        const link = document.createElement('a'); 
        link.href = URL.createObjectURL(blob); 
        link.download = `${currentForensicTarget}_Forensic.json`; 
        link.click(); 
        showToast("Exported JSON");
    } else {
        if (!nseMasterList.length) {
            return showToast("Master list empty", "error");
        }
        let csvContent = "SYMBOL,COMPANY\n" + nseMasterList.map(s => `${s.symbol},${s.name?.replace(/,/g, '')}`).join('\n');
        const blob = new Blob([csvContent], { type: 'text/csv' });
        const link = document.createElement('a'); 
        link.href = URL.createObjectURL(blob); 
        link.download = `EQUITY_MASTER.csv`; 
        link.click(); 
        showToast("Exported CSV");
    }
};

async function gitSync(action) {
    const token = localStorage.getItem('fs_git_token');
    const owner = localStorage.getItem('fs_git_user');
    const repo = localStorage.getItem('fs_git_repo');
    
    if (!token || !owner || !repo) { 
        document.getElementById('settings-btn').click(); 
        return showToast("Missing Vault Config", "error"); 
    }
    
    const url = `https://api.github.com/repos/${owner}/${repo}/contents/vault_backup.json`;
    const btn = document.getElementById(`git-${action}-btn`);
    const originalHtml = btn.innerHTML; 
    
    btn.innerHTML = `<i class="ph-bold ph-spinner animate-spin"></i>`;
    
    try {
        let sha = null;
        let content = null;
        
        const getRes = await fetch(url, { headers: { 'Authorization': `Bearer ${token}` } });
        
        if (getRes.ok) { 
            const data = await getRes.json(); 
            sha = data.sha; 
            content = data.content; 
        }
        
        if (action === 'push') {
            const uploadPayload = { 
                sessions: savedSessions, 
                watchlists: watchlists, 
                ledger: alphaLedger, 
                archives: forensicArchives 
            };
            
            const body = { 
                message: `Vault Sync`, 
                content: utoa(JSON.stringify(uploadPayload)) 
            };
            if (sha) body.sha = sha;
            
            const putRes = await fetch(url, { 
                method: 'PUT', 
                headers: { 'Authorization': `Bearer ${token}`, 'Content-Type': 'application/json' }, 
                body: JSON.stringify(body) 
            });
            
            if (!putRes.ok) throw new Error("Push Failed"); 
            
            triggerDirtyState(false); 
            showToast("Vault Pushed", "success");
        } else {
            if (!sha) throw new Error("No backup found"); 
            
            const payload = JSON.parse(atou(content));
            
            if (payload.sessions) savedSessions = payload.sessions; 
            if (payload.watchlists) watchlists = payload.watchlists; // Replaced flat watchlist logic
            if (payload.ledger) alphaLedger = payload.ledger; 
            if (payload.archives) forensicArchives = payload.archives;
            
            await localforage.setItem('sessions', savedSessions); 
            await localforage.setItem('watchlists', watchlists);
            
            renderSessionsSidebar(); 
            updateWatchlistHeaderButton();
            fetchWatchlistPrices(); 
            triggerDirtyState(false); 
            showToast("Vault Pulled", "success");
        }
    } catch (e) { 
        showToast(e.message, "error"); 
    } finally { 
        btn.innerHTML = originalHtml; 
    }
}

document.getElementById('git-push-btn').onclick = () => gitSync('push'); 
document.getElementById('git-pull-btn').onclick = () => gitSync('pull');

// ==========================================
// 11. SETTINGS DRAWER CONTROLLER
// ==========================================
const modal = document.getElementById('settings-modal');
const modelSel = document.getElementById('api-model-select');

document.getElementById('settings-btn').onclick = () => {
    document.getElementById('api-key-google').value = localStorage.getItem('fs_api_key_google') || "";
    document.getElementById('api-key-openai').value = localStorage.getItem('fs_api_key_openai') || "";
    document.getElementById('api-key-anthropic').value = localStorage.getItem('fs_api_key_anthropic') || "";
    
    modelSel.value = localStorage.getItem('fs_api_model') || "google|gemini-3.5-flash"; 
    modelSel.dispatchEvent(new Event('change'));
    
    document.getElementById('git-token-input').value = localStorage.getItem('fs_git_token') || ""; 
    document.getElementById('git-user-input').value = localStorage.getItem('fs_git_user') || ""; 
    document.getElementById('git-repo-input').value = localStorage.getItem('fs_git_repo') || "";
    
    modal.classList.remove('translate-x-full');
};

document.getElementById('close-settings-btn').onclick = () => {
    modal.classList.add('translate-x-full');
};

modelSel.addEventListener('change', () => { 
    document.querySelectorAll('.key-container').forEach(el => el.classList.add('hidden')); 
    document.getElementById(`key-container-${modelSel.value.split('|')[0]}`).classList.remove('hidden'); 
});

document.getElementById('save-settings-btn').onclick = () => {
    ['google','openai','anthropic'].forEach(provider => {
        localStorage.setItem(`fs_api_key_${provider}`, document.getElementById(`api-key-${provider}`).value);
    });
    
    localStorage.setItem('fs_api_model', modelSel.value); 
    localStorage.setItem('fs_git_token', document.getElementById('git-token-input').value); 
    localStorage.setItem('fs_git_user', document.getElementById('git-user-input').value); 
    localStorage.setItem('fs_git_repo', document.getElementById('git-repo-input').value); 
    
    modal.classList.add('translate-x-full'); 
    showToast("Config Saved", "success");
};

// Boot Sequence
initApp();


