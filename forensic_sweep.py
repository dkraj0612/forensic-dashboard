import os
import io
import re
import csv
import json
import time
import sys
import zipfile
import logging
import traceback
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional

import requests
import pandas as pd
import yfinance as yf
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# --- LOGGING CONFIGURATION ---
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)-8s | %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

class MarketPipelineConfig:
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
        
        os.makedirs(f"{self.base_market_dir}/{self.date_iso}", exist_ok=True)
        os.makedirs(f"{self.base_market_dir}/adjustments", exist_ok=True)
        logger.info(f"Pipeline Configured for Target Date: {self.date_iso}")

def create_resilient_session() -> requests.Session:
    session = requests.Session()
    retry_strategy = Retry(total=4, backoff_factor=1.5, status_forcelist=[429, 500, 502, 503, 504], raise_on_status=False)
    adapter = HTTPAdapter(max_retries=retry_strategy, pool_connections=25, pool_maxsize=25)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Connection": "keep-alive"
    })
    return session

def get_nifty_total_market(session: requests.Session) -> List[str]:
    """Dynamically downloads the Nifty Total Market constituents (750 stocks)."""
    logger.info("Fetching live Nifty Total Market index constituents...")
    tickers = set()
    
    # 1. Establish cookies to bypass Cloudflare/Akamai bot protection
    try:
        session.get("https://www.niftyindices.com", timeout=10)
    except:
        pass

    # 2. Try the primary Nifty Total Market CSV
    try:
        url = "https://www.niftyindices.com/IndexConstituent/ind_niftytotalmarketlist.csv"
        res = session.get(url, headers={"Referer": "https://www.niftyindices.com/"}, timeout=15)
        if res.status_code == 200 and "Symbol" in res.text:
            reader = csv.DictReader(res.text.strip().split('\n'))
            for row in reader:
                sym = row.get('Symbol') or row.get('SYMBOL')
                if sym: tickers.add(sym.strip().upper())
    except Exception as e:
        logger.warning(f"Primary Total Market index fetch failed: {e}")

    # 3. BULLETPROOF FALLBACK: Combine Nifty 500 + Nifty Microcap 250
    # The Nifty Total Market is mathematically exactly these two indices combined.
    if len(tickers) < 500:
        logger.info("Primary URL blocked or missing. Executing fallback: Assembling Nifty 500 + Microcap 250...")
        fallback_urls = [
            "https://www.niftyindices.com/IndexConstituent/ind_nifty500list.csv",
            "https://www.niftyindices.com/IndexConstituent/ind_niftymicrocap250list.csv"
        ]
        for url in fallback_urls:
            try:
                res = session.get(url, headers={"Referer": "https://www.niftyindices.com/"}, timeout=15)
                if res.status_code == 200 and "Symbol" in res.text:
                    reader = csv.DictReader(res.text.strip().split('\n'))
                    for row in reader:
                        sym = row.get('Symbol') or row.get('SYMBOL')
                        if sym: tickers.add(sym.strip().upper())
            except Exception as e:
                logger.error(f"Fallback fetch failed for {url}: {e}")

    final_list = list(tickers)
    
    # Verify we successfully extracted a broad market list
    if len(final_list) > 200:
        logger.info(f"Successfully mapped {len(final_list)} assets for Nifty Total Market.")
        return final_list
        
    logger.error("All dynamic index fetches failed. Reverting to hardcoded safety watchlist.")
    return ["RELIANCE", "TCS", "INFY", "HDFCBANK", "TMPV"]

def to_md_table(data_list: List[Dict[str, Any]], custom_headers: Optional[List[str]] = None) -> str:
    if not data_list: return "*No transaction tracking parameters logged for this segment today.*\n"
    headers = custom_headers if custom_headers else list(data_list[0].keys())
    md_builder = ["| " + " | ".join(headers) + " |", "| " + " | ".join(["---"] * len(headers)) + " |"]
    for row in data_list:
        clean_row = [str(row.get(h, '')).replace('|', '\\|').strip() for h in headers]
        md_builder.append("| " + " | ".join(clean_row) + " |")
    return "\n".join(md_builder) + "\n"

def to_md_kv(title: str, data_dict: Dict[str, Any]) -> str:
    md = f"### {title}\n"
    if not data_dict: return md + "*Data context empty*\n"
    for k, v in data_dict.items(): md += f"* **{k}**: {v}\n"
    return md + "\n"

def is_data_secured(filepath: str) -> bool:
    return os.path.exists(filepath) and os.path.getsize(filepath) > 500

# --- CORE EXTRACTION STEPS (DATA SCRAPING) ---
def process_market_action(cfg: MarketPipelineConfig, session: requests.Session):
    target_file = f"{cfg.base_market_dir}/{cfg.date_iso}/cash_market.md"
    if is_data_secured(target_file):
        logger.info(f"Cash Market data already exists for {cfg.date_iso}. Skipping download.")
        return

    logger.info("Initiating Cash Market Extraction...")
    prices, indices = [], []

    try:
        url = f"https://nsearchives.nseindia.com/products/content/sec_bhavdata_full_{cfg.date_ddmmyyyy}.csv"
        res = session.get(url, headers={"Referer": "https://www.nseindia.com/"}, timeout=15)
        if res.status_code == 200 and "SYMBOL" in res.text:
            reader = csv.DictReader(res.text.strip().split('\n'))
            for r in reader:
                clean = {k.strip(): v.strip() for k, v in r.items() if k}
                if clean.get('SERIES') in ['EQ', 'SM']:
                    prices.append({"Ticker": clean.get('SYMBOL'), "Open": clean.get('OPEN_PRICE'), "High": clean.get('HIGH_PRICE'), "Low": clean.get('LOW_PRICE'), "Close": clean.get('CLOSE_PRICE'), "Volume": clean.get('TTL_TRD_QNTY'), "Delivery_Qty": clean.get('DELIV_QTY'), "Delivery_Pct": clean.get('DELIV_PER')})
            logger.info(f"Successfully extracted {len(prices)} equity records from Primary NSE endpoint.")
        else:
            logger.warning(f"Primary NSE endpoint returned unexpected status ({res.status_code}) or format. Triggering Plan B.")
    except Exception as e:
        logger.warning(f"Primary NSE endpoint failed: {e}. Triggering Plan B.")

    if not prices:
        try:
            fallback_url = f"https://www.bseindia.com/download/BhavCopy/Equity/EQ{cfg.date_yymmdd}_CSV.ZIP"
            logger.info(f"Attempting BSE Zip extraction from: {fallback_url}")
            res = session.get(fallback_url, headers={"Referer": "https://www.bseindia.com/"}, timeout=15)
            if res.status_code == 200:
                with zipfile.ZipFile(io.BytesIO(res.content)) as z:
                    for filename in z.namelist():
                        with z.open(filename) as f:
                            text_reader = csv.DictReader(io.TextIOWrapper(f, encoding='utf-8'))
                            for r in text_reader:
                                prices.append({"Ticker": r.get('SC_NAME', '').strip(), "Open": r.get('OPEN'), "High": r.get('HIGH'), "Low": r.get('LOW'), "Close": r.get('CLOSE'), "Volume": r.get('NO_OF_SHRS'), "Delivery_Qty": "N/A", "Delivery_Pct": "N/A"})
                logger.info(f"Successfully extracted {len(prices)} equity records via BSE Fallback.")
            else:
                logger.error(f"BSE Fallback failed with status code {res.status_code}")
        except Exception as e:
            logger.error(f"Critical Data Loss: Primary and Backup equity sources failed: {e}")

    try:
        idx_url = f"https://archives.nseindia.com/content/indices/ind_close_all_{cfg.date_ddmmyyyy}.csv"
        res = session.get(idx_url, headers={"Referer": "https://www.nseindia.com/"}, timeout=15)
        if res.status_code == 200 and "Index Name" in res.text:
            reader = csv.DictReader(res.text.strip().split('\n'))
            for r in reader:
                name = r.get('Index Name', '').strip()
                if name in ['Nifty 50', 'Nifty 500', 'Nifty Midcap 150', 'Nifty Smallcap 250']:
                    indices.append({"Index": name, "Open": r.get('Open Index Value'), "High": r.get('High Index Value'), "Low": r.get('Low Index Value'), "Close": r.get('Closing Index Value')})
            logger.info(f"Successfully extracted {len(indices)} core market indices.")
    except Exception as e:
        logger.warning(f"Failed to fetch market indices: {e}") 

    if prices or indices:
        md_content = f"# Cash Market Dashboard - {cfg.date_iso}\n\n## Broad Market Indices\n{to_md_table(indices, ['Index', 'Open', 'High', 'Low', 'Close'])}\n## Equity Price & Delivery Volume Action\n{to_md_table(prices, ['Ticker', 'Open', 'High', 'Low', 'Close', 'Volume', 'Delivery_Qty', 'Delivery_Pct'])}"
        with open(target_file, "w", encoding="utf-8") as f: f.write(md_content)
        logger.info(f"Cash Market Matrix written to {target_file}")

def process_derivatives(cfg: MarketPipelineConfig, session: requests.Session):
    target_file = f"{cfg.base_market_dir}/{cfg.date_iso}/derivatives.md"
    if is_data_secured(target_file):
        logger.info(f"Derivatives data already exists for {cfg.date_iso}. Skipping.")
        return

    logger.info("Initiating Derivatives (F&O) Extraction...")
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
                                fno_bhav.append({"Contract": r.get('SYMBOL'), "Expiry": r.get('EXPIRY_DT'), "Close": r.get('CLOSE'), "OI": r.get('OPEN_INT'), "Change_In_OI": r.get('CHG_IN_OI')})
            logger.info(f"Extracted {len(fno_bhav)} futures contracts from Bhavcopy.")
    except Exception as e: logger.error(f"Derivatives Bhavcopy extraction failed: {e}")

    try:
        oi_url = f"https://archives.nseindia.com/content/nsccl/fao_participant_oi_{cfg.date_ddmmyyyy}.csv"
        res = session.get(oi_url, headers={"Referer": "https://www.nseindia.com/"}, timeout=15)
        if res.status_code == 200:
            reader = csv.DictReader(res.text.strip().split('\n'))
            for r in reader: participant_oi.append({"Client": r.get('Client Type'), "Future_Long": r.get('Future Index Long'), "Future_Short": r.get('Future Index Short'), "Option_Call_Long": r.get('Option Index Call Long'), "Option_Put_Long": r.get('Option Index Put Long')})
            logger.info(f"Extracted Participant OI matrix with {len(participant_oi)} segments.")
    except Exception as e: logger.error(f"Participant OI extraction failed: {e}")

    try:
        res = session.get("https://nsearchives.nseindia.com/content/fo/fo_secban.csv", headers={"Referer": "https://www.nseindia.com/"}, timeout=15)
        if res.status_code == 200:
            lines = res.text.strip().split('\n')
            for line in lines[1:]:
                if line and ',' in line: ban_list.append({"Symbol": line.split(',')[1].strip()})
            logger.info(f"Found {len(ban_list)} stocks in F&O Ban list.")
    except Exception as e: logger.warning(f"Ban list extraction failed: {e}")

    if fno_bhav or participant_oi or ban_list:
        md_content = f"# Derivatives - {cfg.date_iso}\n\n## F&O Ban List\n{to_md_table(ban_list, ['Symbol'])}\n## Institutional Open Interest Layout\n{to_md_table(participant_oi, ['Client', 'Future_Long', 'Future_Short', 'Option_Call_Long', 'Option_Put_Long'])}\n## Active Index & Stock Futures Liquidity\n{to_md_table(fno_bhav[:500], ['Contract', 'Expiry', 'Close', 'OI', 'Change_In_OI'])}"
        with open(target_file, "w", encoding="utf-8") as f: f.write(md_content)

def process_macro_flows(cfg: MarketPipelineConfig, session: requests.Session):
    target_file = f"{cfg.base_market_dir}/{cfg.date_iso}/macro_flows.md"
    if is_data_secured(target_file): return

    logger.info("Initiating Macro Flows Extraction (FII/DII & Deals)...")
    fii_data, deals_data = [], []

    try:
        res = session.get("https://www.nseindia.com/api/fiidiiTradeReact", headers={"Referer": "https://www.nseindia.com/"}, timeout=15)
        if res.status_code == 200:
            for item in res.json(): fii_data.append({"Category": item.get('category'), "Buy_Value": item.get('buyValue'), "Sell_Value": item.get('sellValue'), "Net_Value": item.get('netValue')})
            logger.info(f"Extracted {len(fii_data)} FII/DII flow categories.")
    except Exception as e: logger.error(f"FII/DII extraction failed: {e}")

    for url, deal_type in [("https://archives.nseindia.com/content/equities/bulk.csv", "BULK"), ("https://archives.nseindia.com/content/equities/block.csv", "BLOCK")]:
        try:
            res = session.get(url, headers={"Referer": "https://www.nseindia.com/"}, timeout=15)
            if res.status_code == 200:
                reader = csv.DictReader(res.text.strip().split('\n'))
                count = 0
                for r in reader:
                    deals_data.append({"Type": deal_type, "Symbol": r.get('Symbol'), "Client": r.get('Client Name'), "Txn": r.get('Buy/Sell'), "Qty": r.get('Quantity Traded'), "Price": r.get('Trade Price / Wght. Avg. Price')})
                    count += 1
                logger.info(f"Extracted {count} {deal_type} deals.")
        except Exception as e: logger.warning(f"{deal_type} deal extraction failed: {e}")

    if fii_data or deals_data:
        md_content = f"# Institutional Flows - {cfg.date_iso}\n\n## Net Investments\n{to_md_table(fii_data)}\n## Deals\n{to_md_table(deals_data)}"
        with open(target_file, "w", encoding="utf-8") as f: f.write(md_content)

def process_surveillance(cfg: MarketPipelineConfig, session: requests.Session):
    target_file = f"{cfg.base_market_dir}/{cfg.date_iso}/surveillance.md"
    if is_data_secured(target_file): return

    logger.info("Initiating Surveillance Framework Extraction...")
    risk_list = []
    for url, list_type in [("https://archives.nseindia.com/content/circulars/surveillance/ASM_latest.csv", "ASM"), ("https://archives.nseindia.com/content/circulars/surveillance/GSM_latest.csv", "GSM")]:
        try:
            res = session.get(url, headers={"Referer": "https://www.nseindia.com/"}, timeout=15)
            if res.status_code == 200:
                reader = csv.DictReader(res.text.strip().split('\n'))
                for r in reader:
                    symbol = r.get('Symbol') or r.get('SYMBOL')
                    if symbol: risk_list.append({"Symbol": symbol.strip(), "Framework": list_type, "Risk_Stage": r.get('Current Stage', 'Active').strip()})
        except Exception as e: logger.warning(f"{list_type} surveillance extraction failed: {e}")

    if risk_list:
        logger.info(f"Flagged {len(risk_list)} assets under surveillance frameworks.")
        with open(target_file, "w", encoding="utf-8") as f: f.write(f"# Regulatory Risk Lists - {cfg.date_iso}\n\n{to_md_table(risk_list)}")

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
    except Exception as e: logger.error(f"Financial results extraction failed for {company}: {e}")

def process_corporate_events(cfg: MarketPipelineConfig, session: requests.Session):
    logger.info("Initiating Corporate Disclosures Extraction...")
    try:
        res = session.get("https://api.bseindia.com/BseIndiaAPI/api/AnnSubCategoryGetData/w", params={"pageno": 1, "strCat": "-1", "strPrevDate": cfg.date_ddmmyyyy, "strScrip": "", "strSearch": "", "strToDate": cfg.date_ddmmyyyy, "strType": "C"}, headers={"Referer": "https://www.bseindia.com/"}, timeout=15)
        if res.status_code != 200: 
            return
            
        items = res.json().get('Table', [])
        for item in items:
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
    except Exception as e: logger.error(f"Corporate disclosure workflow failed: {e}")

def process_shareholding_patterns(cfg: MarketPipelineConfig, session: requests.Session):
    logger.info("Scanning Rolling Shareholding Patterns (SHP)...")
    try:
        res = session.get("https://api.bseindia.com/BseIndiaAPI/api/ListofScripData/w?Group=A&Scripcode=&industry=&segment=Equity&status=Active", headers={"Referer": "https://www.bseindia.com/"}, timeout=20)
        if res.status_code != 200: return
        
        scrip_list = res.json()[:20] 
        for scrip in scrip_list:
            code, company = scrip.get('ScripCode'), scrip.get('ScripName', '').strip().replace(" ", "_")
            shp_res = session.get(f"https://api.bseindia.com/BseIndiaAPI/api/shpSecSummery_New/w?qtrid=&scripcode={code}", headers={"Referer": "https://www.bseindia.com/"}, timeout=10).json()
            if shp_res and shp_res.get('Data'):
                dfs = pd.read_html(io.StringIO(shp_res['Data']))
                if dfs:
                    target_file = f"{cfg.base_corp_dir}/{company}/shareholding_pattern.md"
                    os.makedirs(os.path.dirname(target_file), exist_ok=True)
                    with open(target_file, "w", encoding="utf-8") as f: f.write(f"# SHP\n\n{dfs[0].to_markdown(index=False)}")
            time.sleep(0.1)
    except Exception as e: logger.error(f"SHP compilation anomaly: {e}")

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
    if not adjustments: return
    
    lock_file = f"{cfg.base_market_dir}/adjustments/{cfg.date_iso}.lock"
    if os.path.exists(lock_file):
        logger.info("Healing protocol already executed today. Bypassing to prevent double-slicing.")
        return

    logger.info(f"Applying Structural Healing adjustments for {len(adjustments)} corporate actions...")
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
            
    with open(lock_file, "w") as f: f.write("LOCKED")
    logger.info("Historical data successfully adjusted for corporate actions.")

def process_price_adjustments(cfg: MarketPipelineConfig, session: requests.Session):
    logger.info("Scanning for Ex-Date Corporate Actions...")
    todays_adjustments = {}
    try:
        res = session.get("https://api.bseindia.com/BseIndiaAPI/api/CorporateAction/w", params={"scripcode": "", "DDLCA": "Split", "Fdate": "", "Tdate": ""}, headers={"Referer": "https://www.bseindia.com/"}, timeout=15)
        if res.status_code == 200:
            for item in res.json().get('Table', []):
                company = item.get('scrip_name', '').strip()
                if item.get('Ex_Date', '') in [cfg.date_ddmmyyyy, cfg.date_iso]:
                    ratio = calculate_ratio(item.get('Purpose', ''))
                    if ratio > 1.0: todays_adjustments[company] = ratio
        if todays_adjustments: 
            logger.info(f"Identified {len(todays_adjustments)} assets requiring historical price adjustments.")
            apply_one_pass_healing(cfg, todays_adjustments)
    except Exception as e: logger.warning(f"Corporate action tracker failed: {e}")

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
    context = f"--- TARGET ASSET: {ticker} ---\n\n"
    
    try:
        info = yf.Ticker(f"{ticker}.NS").info
        context += f"### [FUNDAMENTAL METRICS]\n"
        context += f"Trailing P/E: {info.get('trailingPE', 'N/A')} | Forward P/E: {info.get('forwardPE', 'N/A')}\n"
        context += f"Profit Margin: {info.get('profitMargins', 'N/A')} | Debt/Equity: {info.get('debtToEquity', 'N/A')}\n"
        context += f"Inst. Ownership: {info.get('heldPercentInstitutions', 0)*100}%\n\n"
    except Exception:
        pass

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

    corp_dir = f"corporate_data/{ticker}"
    if os.path.exists(corp_dir):
        context += "\n### [CORPORATE DISCLOSURES & FILINGS]\n"
        for root, _, files in os.walk(corp_dir):
            for file in files:
                if file.endswith(".md"):
                    with open(os.path.join(root, file), "r", encoding="utf-8") as f:
                        context += f"--- FILE: {file.upper()} ---\n{f.read()}\n\n"
                        
    return context

def run_ai_analysis_sweep(watchlist: List[str]):
    logger.info(f"Initializing Generative AI Evaluation for {len(watchlist)} assets...")
    API_KEY = os.environ.get("GEMINI_API_KEY")
    if not API_KEY:
        logger.critical("Process Aborted: GEMINI_API_KEY environment variable missing.")
        return
        
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={API_KEY}"
    
    # Load existing verdicts to prevent overwriting all data if the script fails midway
    master_verdicts = {}
    db_path = "master_forensic_db.json"
    if os.path.exists(db_path):
        try:
            with open(db_path, "r", encoding="utf-8") as f:
                master_verdicts = json.load(f)
        except Exception:
            pass

    for ticker in watchlist:
        logger.info(f"Compiling Omni-Context for target: {ticker}")
        context = gather_omni_context(ticker, days_to_look_back=30)
        
        payload = {
            "contents": [{"role": "user", "parts": [{"text": context}]}],
            "systemInstruction": {"parts": [{"text": get_omni_engine_prompt()}]},
            "generationConfig": { "temperature": 0.1, "responseMimeType": "application/json" }
        }
        
        for attempt in range(3):
            try:
                res = requests.post(url, headers={'Content-Type': 'application/json'}, json=payload, timeout=45)
                if res.status_code == 200:
                    raw_text = res.json()['candidates'][0]['content']['parts'][0]['text']
                    clean_json_str = re.sub(r'^```json\s*|\s*```$', '', raw_text.strip(), flags=re.MULTILINE)
                    master_verdicts[ticker] = json.loads(clean_json_str)
                    logger.info(f"SUCCESS: AI Verdict rendered and parsed for {ticker}")
                    break
                elif res.status_code == 429:
                    logger.warning(f"Rate limited by Gemini on {ticker}. Backing off... (Attempt {attempt+1})")
                    time.sleep(10 * (attempt + 1))
                else:
                    logger.error(f"Gemini API returned status {res.status_code}: {res.text}")
                    break
            except Exception as e:
                logger.error(f"Gemini connection failed for {ticker}: {e}")
                time.sleep(5 * (attempt + 1))
        
        # CRITICAL LIMIT: Gemini allows 15 RPM. 4.5s delay forces ~13 requests per minute.
        time.sleep(4.5)

    with open(db_path, "w", encoding="utf-8") as f:
        json.dump(master_verdicts, f, indent=4)
    logger.info(f"Matrix database written successfully to destination: {db_path}")

# --- UNIFIED EXECUTION GATEWAY ---
def main():
    logger.info("==================================================")
    logger.info("Initializing Unified Data Core Pipeline Execution")
    logger.info("==================================================")
    
    cfg = MarketPipelineConfig()
    session = create_resilient_session()
    
    # Generate watchlist dynamically from NiftyIndices
    active_watchlist = get_nifty_total_market(session)
    
    # Phase 1: Data Scraping & Preparation
    process_market_action(cfg, session)
    process_derivatives(cfg, session)
    process_macro_flows(cfg, session)
    process_surveillance(cfg, session)
    process_corporate_events(cfg, session)
    process_shareholding_patterns(cfg, session)
    process_price_adjustments(cfg, session)
    
    logger.info("Phase 1 Complete: All ingestion layers executed successfully.")
    
    # Phase 2: AI Orchestration
    run_ai_analysis_sweep(active_watchlist)
    
    logger.info("==================================================")
    logger.info("Master Pipeline Execution Terminated Successfully")
    logger.info("==================================================")

if __name__ == "__main__":
    main()
