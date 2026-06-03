import os
import io
import re
import csv
import json
import time
import sys
import zipfile
import traceback
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional

import requests
import pandas as pd
import yfinance as yf
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# --- SYSTEM CONFIGURATION ---
# Define your core tracking universe here (Without the .NS suffix for local mapping)
WATCHLIST = ["RELIANCE", "TCS", "INFY", "HDFCBANK", "TATAMOTORS"]

class MarketPipelineConfig:
    """Encapsulates system settings and handles localized execution state."""
    def __init__(self, target_date: Optional[datetime] = None):
        self.today = target_date or datetime.today()
        self.date_iso = self.today.strftime('%Y-%m-%d')
        self.date_ddmmyyyy = self.today.strftime('%d%m%Y')
        self.date_yymmdd = self.today.strftime('%y%m%d')
        self.date_mmm = self.today.strftime('%b').upper()
        self.date_yyyy = self.today.strftime('%Y')
        self.date_ddmmmyyyy = self.today.strftime('%d%b%Y').upper()
        
        self.base_market_dir = "market_data"
        self.base_corp_dir = "corporate_data"
        
        # Immediate directory validation
        os.makedirs(f"{self.base_market_dir}/{self.date_iso}", exist_ok=True)
        os.makedirs(f"{self.base_market_dir}/adjustments", exist_ok=True)

def create_resilient_session() -> requests.Session:
    """Builds an enterprise-grade pooled session with automated exponential backoff."""
    session = requests.Session()
    retry_strategy = Retry(
        total=4,
        backoff_factor=1.5,
        status_forcelist=[429, 500, 502, 503, 504],
        raise_on_status=False
    )
    adapter = HTTPAdapter(max_retries=retry_strategy, pool_connections=25, pool_maxsize=25)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
        "Connection": "keep-alive"
    })
    return session

# --- MARKDOWN TRANSFORMATION PARSERS ---
def to_md_table(data_list: List[Dict[str, Any]], custom_headers: Optional[List[str]] = None) -> str:
    if not data_list: 
        return "*No transaction tracking parameters logged for this segment today.*\n"
    headers = custom_headers if custom_headers else list(data_list[0].keys())
    
    md_builder = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join(["---"] * len(headers)) + " |"
    ]
    for row in data_list:
        clean_row = [str(row.get(h, '')).replace('|', '\\|').strip() for h in headers]
        md_builder.append("| " + " | ".join(clean_row) + " |")
    return "\n".join(md_builder) + "\n"

def to_md_kv(title: str, data_dict: Dict[str, Any]) -> str:
    md = f"### {title}\n"
    if not data_dict:
        return md + "*Data context empty*\n"
    for k, v in data_dict.items():
        md += f"* **{k}**: {v}\n"
    return md + "\n"

def is_data_secured(filepath: str) -> bool:
    return os.path.exists(filepath) and os.path.getsize(filepath) > 500

# --- CORE EXTRACTION STEPS (DATA SCRAPING) ---
def process_market_action(cfg: MarketPipelineConfig, session: requests.Session):
    target_file = f"{cfg.base_market_dir}/{cfg.date_iso}/cash_market.md"
    if is_data_secured(target_file):
        print(f"[{cfg.date_iso}] Cash Market data verified. Skipping.")
        return

    print("Extracting Active Cash Market Matrix...")
    prices: List[Dict[str, Any]] = []
    indices: List[Dict[str, Any]] = []

    try:
        url = f"https://nsearchives.nseindia.com/products/content/sec_bhavdata_full_{cfg.date_ddmmyyyy}.csv"
        res = session.get(url, headers={"Referer": "https://www.nseindia.com/"}, timeout=15)
        if res.status_code == 200 and "SYMBOL" in res.text:
            reader = csv.DictReader(res.text.strip().split('\n'))
            for r in reader:
                clean = {k.strip(): v.strip() for k, v in r.items() if k}
                if clean.get('SERIES') in ['EQ', 'SM']:
                    prices.append({
                        "Ticker": clean.get('SYMBOL'), "Open": clean.get('OPEN_PRICE'),
                        "High": clean.get('HIGH_PRICE'), "Low": clean.get('LOW_PRICE'),
                        "Close": clean.get('CLOSE_PRICE'), "Volume": clean.get('TTL_TRD_QNTY'),
                        "Delivery_Qty": clean.get('DELIV_QTY'), "Delivery_Pct": clean.get('DELIV_PER')
                    })
    except requests.RequestException as e:
        print(f"Primary NSE endpoint failed: {e}. Launching Backup Plan B...")

    if not prices:
        try:
            fallback_url = f"https://www.bseindia.com/download/BhavCopy/Equity/EQ{cfg.date_yymmdd}_CSV.ZIP"
            res = session.get(fallback_url, headers={"Referer": "https://www.bseindia.com/"}, timeout=15)
            if res.status_code == 200:
                with zipfile.ZipFile(io.BytesIO(res.content)) as z:
                    for filename in z.namelist():
                        with z.open(filename) as f:
                            text_reader = csv.DictReader(io.TextIOWrapper(f, encoding='utf-8'))
                            for r in text_reader:
                                prices.append({
                                    "Ticker": r.get('SC_NAME', '').strip(), "Open": r.get('OPEN'),
                                    "High": r.get('HIGH'), "Low": r.get('LOW'), "Close": r.get('CLOSE'),
                                    "Volume": r.get('NO_OF_SHRS'), "Delivery_Qty": "N/A", "Delivery_Pct": "N/A"
                                })
        except Exception as e:
            print(f"Critical Loss of Data Capability: {e}")

    try:
        idx_url = f"https://archives.nseindia.com/content/indices/ind_close_all_{cfg.date_ddmmyyyy}.csv"
        res = session.get(idx_url, headers={"Referer": "https://www.nseindia.com/"}, timeout=15)
        if res.status_code == 200 and "Index Name" in res.text:
            reader = csv.DictReader(res.text.strip().split('\n'))
            for r in reader:
                name = r.get('Index Name', '').strip()
                if name in ['Nifty 50', 'Nifty 500', 'Nifty Midcap 150', 'Nifty Smallcap 250']:
                    indices.append({
                        "Index": name, "Open": r.get('Open Index Value'), "High": r.get('High Index Value'),
                        "Low": r.get('Low Index Value'), "Close": r.get('Closing Index Value')
                    })
    except requests.RequestException:
        pass 

    if prices or indices:
        md_content = f"# Cash Market Dashboard - {cfg.date_iso}\n\n## Broad Market Indices\n"
        md_content += to_md_table(indices, ["Index", "Open", "High", "Low", "Close"])
        md_content += "\n## Equity Price & Delivery Volume Action\n"
        md_content += to_md_table(prices, ["Ticker", "Open", "High", "Low", "Close", "Volume", "Delivery_Qty", "Delivery_Pct"])
        with open(target_file, "w", encoding="utf-8") as f:
            f.write(md_content)

def process_derivatives(cfg: MarketPipelineConfig, session: requests.Session):
    target_file = f"{cfg.base_market_dir}/{cfg.date_iso}/derivatives.md"
    if is_data_secured(target_file): return

    print("Extracting Active Derivatives Profiles...")
    fno_bhav, participant_oi, ban_list = [], [], []

    try:
        bhav_url = f"https://archives.nseindia.com/content/historical/DERIVATIVES/{cfg.date_yyyy}/{cfg.date_mmm}/fo{cfg.date_ddmmmyyyy}bhav.csv.zip"
        res = session.get(bhav_url, headers={"Referer": "https://www.nseindia.com/"}, timeout=15)
        if res.status_code == 200:
            with zipfile.ZipFile(io.BytesIO(res.content)) as z:
                for file_name in z.namelist():
                    with z.open(file_name) as zf:
                        reader = csv.DictReader(io.TextIOWrapper(zf, encoding='utf-8'))
                        for r in reader:
                            if r.get('INSTRUMENT') in ['FUTSTK', 'FUTIDX']:
                                fno_bhav.append({
                                    "Contract": r.get('SYMBOL'), "Expiry": r.get('EXPIRY_DT'),
                                    "Close": r.get('CLOSE'), "OI": r.get('OPEN_INT'), "Change_In_OI": r.get('CHG_IN_OI')
                                })
    except Exception: pass

    try:
        oi_url = f"https://archives.nseindia.com/content/nsccl/fao_participant_oi_{cfg.date_ddmmyyyy}.csv"
        res = session.get(oi_url, headers={"Referer": "https://www.nseindia.com/"}, timeout=15)
        if res.status_code == 200:
            reader = csv.DictReader(res.text.strip().split('\n'))
            for r in reader:
                participant_oi.append({
                    "Client": r.get('Client Type'), "Future_Long": r.get('Future Index Long'),
                    "Future_Short": r.get('Future Index Short'), "Option_Call_Long": r.get('Option Index Call Long'),
                    "Option_Put_Long": r.get('Option Index Put Long')
                })
    except Exception: pass

    try:
        ban_url = "https://nsearchives.nseindia.com/content/fo/fo_secban.csv"
        res = session.get(ban_url, headers={"Referer": "https://www.nseindia.com/"}, timeout=15)
        if res.status_code == 200:
            lines = res.text.strip().split('\n')
            for line in lines[1:]:
                if line and ',' in line:
                    ban_list.append({"Symbol": line.split(',')[1].strip()})
    except Exception: pass

    if fno_bhav or participant_oi or ban_list:
        md_content = f"# Derivatives - {cfg.date_iso}\n\n## F&O Ban List\n"
        md_content += to_md_table(ban_list, ["Symbol"])
        md_content += "\n## Institutional Open Interest Layout\n"
        md_content += to_md_table(participant_oi, ["Client", "Future_Long", "Future_Short", "Option_Call_Long", "Option_Put_Long"])
        md_content += "\n## Active Index & Stock Futures Liquidity\n"
        md_content += to_md_table(fno_bhav[:500], ["Contract", "Expiry", "Close", "OI", "Change_In_OI"])
        with open(target_file, "w", encoding="utf-8") as f:
            f.write(md_content)

def process_macro_flows(cfg: MarketPipelineConfig, session: requests.Session):
    target_file = f"{cfg.base_market_dir}/{cfg.date_iso}/macro_flows.md"
    if is_data_secured(target_file): return

    print("Extracting Institutional Flow Architecture...")
    fii_data, deals_data = [], []

    try:
        res = session.get("https://www.nseindia.com/api/fiidiiTradeReact", headers={"Referer": "https://www.nseindia.com/"}, timeout=15)
        if res.status_code == 200:
            for item in res.json():
                fii_data.append({"Category": item.get('category'), "Buy_Value": item.get('buyValue'), "Sell_Value": item.get('sellValue'), "Net_Value": item.get('netValue')})
    except Exception: pass

    for url, deal_type in [("https://archives.nseindia.com/content/equities/bulk.csv", "BULK"), ("https://archives.nseindia.com/content/equities/block.csv", "BLOCK")]:
        try:
            res = session.get(url, headers={"Referer": "https://www.nseindia.com/"}, timeout=15)
            if res.status_code == 200:
                reader = csv.DictReader(res.text.strip().split('\n'))
                for r in reader:
                    deals_data.append({"Type": deal_type, "Symbol": r.get('Symbol'), "Client": r.get('Client Name'), "Txn": r.get('Buy/Sell'), "Qty": r.get('Quantity Traded'), "Price": r.get('Trade Price / Wght. Avg. Price')})
        except Exception: pass

    if fii_data or deals_data:
        md_content = f"# Institutional Flows - {cfg.date_iso}\n\n## Net Investments\n{to_md_table(fii_data)}\n## Deals\n{to_md_table(deals_data)}"
        with open(target_file, "w", encoding="utf-8") as f: f.write(md_content)

def process_surveillance(cfg: MarketPipelineConfig, session: requests.Session):
    target_file = f"{cfg.base_market_dir}/{cfg.date_iso}/surveillance.md"
    if is_data_secured(target_file): return

    print("Extracting Regulatory Surveillance Frameworks...")
    risk_list = []
    for url, list_type in [("https://archives.nseindia.com/content/circulars/surveillance/ASM_latest.csv", "ASM"), ("https://archives.nseindia.com/content/circulars/surveillance/GSM_latest.csv", "GSM")]:
        try:
            res = session.get(url, headers={"Referer": "https://www.nseindia.com/"}, timeout=15)
            if res.status_code == 200:
                reader = csv.DictReader(res.text.strip().split('\n'))
                for r in reader:
                    symbol = r.get('Symbol') or r.get('SYMBOL')
                    if symbol: risk_list.append({"Symbol": symbol.strip(), "Framework": list_type, "Risk_Stage": r.get('Current Stage', 'Active').strip()})
        except Exception: pass

    if risk_list:
        with open(target_file, "w", encoding="utf-8") as f:
            f.write(f"# Regulatory Risk Lists - {cfg.date_iso}\n\n{to_md_table(risk_list)}")

def fetch_financial_results(cfg: MarketPipelineConfig, session: requests.Session, scrip_code: str, company: str):
    if not scrip_code: return
    try:
        url = f"https://api.bseindia.com/BseIndiaAPI/api/TabResults/w?scripcode={scrip_code}&tabtype=RESULTS"
        res = session.get(url, headers={"Referer": "https://www.bseindia.com/"}, timeout=15)
        if res.status_code == 200:
            payload = res.json()
            if isinstance(payload, str): payload = json.loads(payload)
            if payload and isinstance(payload, list):
                target_dir = f"{cfg.base_corp_dir}/{company}/financials"
                os.makedirs(target_dir, exist_ok=True)
                md_content = f"# Financial Results - {cfg.date_iso}\n\n{to_md_kv('Metadata', {'Scrip': scrip_code, 'Company': company})}\n{to_md_table(payload, ['Quarter', 'Revenue', 'NetProfit', 'EPS'])}"
                with open(f"{target_dir}/{cfg.date_iso}_results.md", "w", encoding="utf-8") as f: f.write(md_content)
    except Exception: pass

def process_corporate_events(cfg: MarketPipelineConfig, session: requests.Session):
    print("Extracting Corporate Disclosures...")
    try:
        res = session.get("https://api.bseindia.com/BseIndiaAPI/api/AnnSubCategoryGetData/w", params={"pageno": 1, "strCat": "-1", "strPrevDate": cfg.date_ddmmyyyy, "strScrip": "", "strSearch": "", "strToDate": cfg.date_ddmmyyyy, "strType": "C"}, headers={"Referer": "https://www.bseindia.com/"}, timeout=15)
        if res.status_code != 200: return
        for item in res.json().get('Table', []):
            company = item.get('SLONGNAME', 'UNKNOWN').strip().replace(" ", "_").replace("/", "-")
            headline = item.get('NEWSSUB', '')
            category = item.get('CATEGORYNAME', '').lower()
            
            is_earnings = "result" in category or "financial" in headline.lower()
            is_concall = any(k in headline.lower() for k in ["transcript", "earnings call", "concall"])
            
            target_dir = f"{cfg.base_corp_dir}/{company}/" + ("earnings" if is_earnings else "concalls" if is_concall else "filings")
            os.makedirs(target_dir, exist_ok=True)
            if is_earnings: fetch_financial_results(cfg, session, item.get('SCRIP_CD'), company)
            
            with open(f"{target_dir}/{cfg.date_iso}_{item.get('NEWSID')}.md", "w", encoding="utf-8") as f:
                f.write(f"# Event\n* **Company**: {company}\n* **Date**: {cfg.date_iso}\n## Headline\n> {headline}\n\n## Details\n{item.get('HEADLINE', '')}\n")
    except Exception: pass

def process_shareholding_patterns(cfg: MarketPipelineConfig, session: requests.Session):
    print("Checking Rolling Shareholding Patterns...")
    try:
        res = session.get("https://api.bseindia.com/BseIndiaAPI/api/ListofScripData/w?Group=A&Scripcode=&industry=&segment=Equity&status=Active", headers={"Referer": "https://www.bseindia.com/"}, timeout=20)
        if res.status_code != 200: return
        for scrip in res.json()[:20]: # Limited to top 20 for API performance limits
            code, company = scrip.get('ScripCode'), scrip.get('ScripName', '').strip().replace(" ", "_")
            shp_res = session.get(f"https://api.bseindia.com/BseIndiaAPI/api/shpSecSummery_New/w?qtrid=&scripcode={code}", headers={"Referer": "https://www.bseindia.com/"}, timeout=10).json()
            if shp_res and shp_res.get('Data'):
                dfs = pd.read_html(io.StringIO(shp_res['Data']))
                if dfs:
                    target_file = f"{cfg.base_corp_dir}/{company}/shareholding_pattern.md"
                    os.makedirs(os.path.dirname(target_file), exist_ok=True)
                    with open(target_file, "w", encoding="utf-8") as f: f.write(f"# SHP\n\n{dfs[0].to_markdown(index=False)}")
            time.sleep(0.1)
    except Exception: pass

def calculate_ratio(purpose: str) -> float:
    p_low = purpose.lower()
    try:
        if 'bonus' in p_low:
            match = re.search(r'(\d+)\s*:\s*(\d+)', p_low)
            if match: return (float(match.group(2)) + float(match.group(1))) / float(match.group(2))
        elif 'split' in p_low or 'sub-div' in p_low:
            match = re.findall(r'rs\.?\s*(\d+)', p_low)
            if len(match) >= 2 and float(match[0]) > float(match[1]) > 0: return float(match[0]) / float(match[1])
    except ValueError: pass
    return 1.0

def apply_one_pass_healing(cfg: MarketPipelineConfig, adjustments: Dict[str, float]):
    """Walks the historical matrix exactly once, applying all valid adjustments simultaneously."""
    if not adjustments: return
    
    # CRITICAL BUG FIX: Idempotency Lock
    lock_file = f"{cfg.base_market_dir}/adjustments/{cfg.date_iso}.lock"
    if os.path.exists(lock_file):
        print("Healing protocol already executed today. Bypassing to prevent double-slicing.")
        return

    print(f"Initializing Structural Healing Protocol for {len(adjustments)} actions...")
    for date_folder in os.listdir(cfg.base_market_dir):
        file_path = os.path.join(cfg.base_market_dir, date_folder, "cash_market.md")
        if not os.path.exists(file_path): continue
            
        with open(file_path, "r", encoding="utf-8") as f: lines = f.readlines()
        modified = False
        for i, line in enumerate(lines):
            if not line.startswith("| "): continue
            matched_ticker = next((t for t in adjustments if line.startswith(f"| {t} |")), None)
                    
            if matched_ticker:
                ratio = adjustments[matched_ticker]
                parts = line.split(" | ")
                try:
                    for col in [1, 2, 3, 4]: parts[col] = f"{(float(parts[col]) / ratio):.2f}"
                    parts[5] = f"{int(float(parts[5].split(' |')[0].strip()) * ratio)} |\n"
                    lines[i] = " | ".join(parts)
                    modified = True
                except Exception: pass
                    
        if modified:
            with open(file_path, "w", encoding="utf-8") as f: f.writelines(lines)
            
    # Write lock file
    with open(lock_file, "w") as f: f.write("LOCKED")


def process_price_adjustments(cfg: MarketPipelineConfig, session: requests.Session):
    print("Executing Corporate Actions and Adjustments Protocol...")
    todays_adjustments = {}
    try:
        res = session.get("https://api.bseindia.com/BseIndiaAPI/api/CorporateAction/w", params={"scripcode": "", "DDLCA": "Split", "Fdate": "", "Tdate": ""}, headers={"Referer": "https://www.bseindia.com/"}, timeout=15)
        if res.status_code == 200:
            for item in res.json().get('Table', []):
                company = item.get('scrip_name', '').strip()
                if item.get('Ex_Date', '') in [cfg.date_ddmmyyyy, cfg.date_iso]:
                    ratio = calculate_ratio(item.get('Purpose', ''))
                    if ratio > 1.0: todays_adjustments[company] = ratio
        if todays_adjustments: apply_one_pass_healing(cfg, todays_adjustments)
    except Exception: pass

# --- AI ORCHESTRATION & ANALYSIS LAYER ---
def get_omni_engine_prompt():
    return """
    You are an expert Equity Research Analyst. Analyze the provided historical financial, corporate action, and market intelligence data.
    Output a strictly valid JSON object matching this structural schema exactly, without any external commentary:
    {
        "verdict": "STRONG BUY" | "BUY" | "HOLD" | "AVOID" | "STRONG AVOID",
        "score": 0-100,
        "governance": {"risk_level": "Low"|"Medium"|"High", "details": "string"},
        "shareholding_trends": {"description": "string", "institutional_stance": "Accumulating"|"Static"|"Liquidating"},
        "market_momentum": {"trend": "Bullish"|"Bearish"|"Neutral", "triggers": ["string"]},
        "financial_health": {"score": 0-100, "revenue_quality": "string"},
        "catalysts_and_sentiment": {"description": "string"},
        "regulatory_surveillance": {"framework": "Normal"|"ASM"|"GSM", "risk": "Low"|"Medium"|"High"}
    }
    """

def gather_omni_context(ticker: str, days_to_look_back: int = 30) -> str:
    """Merges scraped local markdown files with live yfinance fundamental metrics into one massive context prompt."""
    context = f"--- TARGET ASSET: {ticker} ---\n\n"
    
    # 1. Inject Live Fundamental Metrics via yfinance
    try:
        info = yf.Ticker(f"{ticker}.NS").info
        context += f"### [FUNDAMENTAL METRICS]\n"
        context += f"Trailing P/E: {info.get('trailingPE', 'N/A')} | Forward P/E: {info.get('forwardPE', 'N/A')}\n"
        context += f"Profit Margin: {info.get('profitMargins', 'N/A')} | Debt/Equity: {info.get('debtToEquity', 'N/A')}\n"
        context += f"Inst. Ownership: {info.get('heldPercentInstitutions', 0)*100}%\n\n"
    except Exception:
        pass

    # 2. Inject Scraped Market Data (Tokens Compressed)
    market_files = ["cash_market.md", "derivatives.md", "macro_flows.md", "surveillance.md"]
    if os.path.exists("market_data"):
        all_dates = sorted([d for d in os.listdir("market_data") if re.match(r'\d{4}-\d{2}-\d{2}', d)])
        target_dates = all_dates[-days_to_look_back:]

        for idx, d_folder in enumerate(target_dates):
            is_recent = (len(target_dates) - idx) <= 5
            day_context = ""
            for m_file in market_files:
                path = f"market_data/{d_folder}/{m_file}"
                if os.path.exists(path):
                    with open(path, "r", encoding="utf-8") as f:
                        lines = f.readlines()
                        relevant_lines = [l for l in lines if ticker in l or l.startswith("#") or l.startswith("|")]
                        if len(relevant_lines) > 3:
                            if is_recent:
                                day_context += f"--- {m_file.upper()} ---\n{''.join(relevant_lines)}\n"
                            else:
                                data_rows = [l for l in relevant_lines if not (l.startswith("#") or l.startswith("|"))]
                                if data_rows: day_context += f"- {m_file.upper()} Summary: {data_rows[0].strip()}\n"
            if day_context: context += f"\n### [MARKET DATA: {d_folder}]\n{day_context}"

    # 3. Inject Scraped Corporate Filings
    corp_dir = f"corporate_data/{ticker}"
    if os.path.exists(corp_dir):
        context += "\n### [CORPORATE DISCLOSURES & FILINGS]\n"
        for root, _, files in os.walk(corp_dir):
            for file in files:
                if file.endswith(".md"):
                    with open(os.path.join(root, file), "r", encoding="utf-8") as f:
                        context += f"--- FILE: {file.upper()} ---\n{f.read()}\n\n"
                        
    return context

def run_ai_analysis_sweep():
    print("\n[AI ORCHESTRATOR] Initializing Generative Evaluation...")
    API_KEY = os.environ.get("GEMINI_API_KEY")
    if not API_KEY:
        print("[CRITICAL] Process Aborted: GEMINI_API_KEY environment variable missing.")
        return
        
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={API_KEY}"
    master_verdicts = {}

    for ticker in WATCHLIST:
        print(f"[ENG] Compiling Omni-Context for target: {ticker}")
        context = gather_omni_context(ticker, days_to_look_back=30)
        
        payload = {
            "contents": [{"role": "user", "parts": [{"text": context}]}],
            "systemInstruction": {"parts": [{"text": get_omni_engine_prompt()}]},
            "generationConfig": { "temperature": 0.1, "responseMimeType": "application/json" }
        }
        
        # Resilient AI Polling
        for attempt in range(3):
            try:
                res = requests.post(url, headers={'Content-Type': 'application/json'}, json=payload, timeout=45)
                if res.status_code == 200:
                    raw_text = res.json()['candidates'][0]['content']['parts'][0]['text']
                    clean_json_str = re.sub(r'^```json\s*|\s*```$', '', raw_text.strip(), flags=re.MULTILINE)
                    master_verdicts[ticker] = json.loads(clean_json_str)
                    print(f"[SUCCESS] Verdict rendered for {ticker}")
                    break
                elif res.status_code == 429:
                    time.sleep(10 * (attempt + 1))
            except Exception as e:
                time.sleep(5 * (attempt + 1))
        
        time.sleep(2) # Prevent token-per-minute API threshold limits

    output_path = "master_forensic_db.json"
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(master_verdicts, f, indent=4)
    print(f"\n[COMPLETE] Matrix database written successfully to destination: {output_path}")

# --- UNIFIED EXECUTION GATEWAY ---
def main():
    print("Initializing Unified Data Core Pipeline Execution...")
    cfg = MarketPipelineConfig()
    session = create_resilient_session()
    
    # Phase 1: Data Scraping & Preparation
    process_market_action(cfg, session)
    process_derivatives(cfg, session)
    process_macro_flows(cfg, session)
    process_surveillance(cfg, session)
    process_corporate_events(cfg, session)
    process_shareholding_patterns(cfg, session)
    process_price_adjustments(cfg, session)
    print("All ingestion layers executed successfully.")
    
    # Phase 2: AI Orchestration
    run_ai_analysis_sweep()

if __name__ == "__main__":
    main()
