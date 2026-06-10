import os
import io
import csv
import zipfile
import logging
import sys
import time
import random
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional

from curl_cffi import requests as tls_requests

# Configure Logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s | %(levelname)-8s | %(message)s', handlers=[logging.StreamHandler(sys.stdout)])
logger = logging.getLogger(__name__)

class MarketPipelineConfig:
    def __init__(self):
        # SMART SHIFT: Look for the *last completed trading day* instead of today
        today = datetime.today()
        if today.weekday() == 0:  # If Monday, go back to Friday (3 days ago)
            self.trading_today = today - timedelta(days=3)
        elif today.weekday() == 6:  # If Sunday, go back to Friday (2 days ago)
            self.trading_today = today - timedelta(days=2)
        else:  # Tuesday-Saturday, just go back 1 day
            self.trading_today = today - timedelta(days=1)

        self.date_iso = self.trading_today.strftime('%Y-%m-%d')
        self.date_ddmmyyyy = self.trading_today.strftime('%d%m%Y')
        self.date_yymmdd = self.trading_today.strftime('%y%m%d')
        self.date_yyyymmdd = self.trading_today.strftime('%Y%m%d') 
        self.date_mmm = self.trading_today.strftime('%b').upper()
        self.date_yyyy = self.trading_today.strftime('%Y')
        self.date_ddmmmyyyy = self.trading_today.strftime('%d%b%Y').upper()

        # ORGANIZE YEAR-WISE
        self.base_market_dir = f"market_data/{self.date_yyyy}"
        self.base_corp_dir = "corporate_data"
        
        os.makedirs(f"{self.base_market_dir}/{self.date_iso}", exist_ok=True)

class OmniFetcher:
    """High-Speed Pure Cryptographic Engine (NSE Focused)"""
    def __init__(self):
        self.session = tls_requests.Session(impersonate="chrome124")
        
        logger.info("Priming NSE WAF Clearance Cookies...")
        headers = {
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Connection": "keep-alive"
        }
        try:
            self.session.get("https://www.nseindia.com", headers=headers, timeout=10)
            self.session.get("https://nsearchives.nseindia.com", headers=headers, timeout=10)
            self.session.get("https://archives.nseindia.com", headers=headers, timeout=10) # Added legacy archive priming
        except Exception:
            pass

    def get_content(self, url: str, timeout: int = 15) -> Optional[bytes]:
        try:
            headers = {"Referer": "https://www.nseindia.com/"}
            resp = self.session.get(url, headers=headers, timeout=timeout)
            if resp.status_code == 200:
                if resp.content.startswith(b'PK'): 
                    return resp.content
        except Exception: pass
        return None

    def get_json(self, url: str, timeout: int = 10) -> Optional[dict]:
        headers = {
            "Referer": "https://www.nseindia.com/", 
            "Accept": "application/json"
        }
        try:
            resp = self.session.get(url, headers=headers, timeout=timeout)
            if resp.status_code == 200:
                data = resp.json()
                if data: return data
        except Exception: pass
        return None

def is_valid_file(filepath: str) -> bool:
    return os.path.exists(filepath) and os.path.getsize(filepath) > 50

def to_md_table(data_list: List[Dict[str, Any]], custom_headers: Optional[List[str]] = None) -> str:
    if not data_list: return "*No data available.*\n"
    headers = custom_headers if custom_headers else list(data_list[0].keys())
    md = ["| " + " | ".join(headers) + " |", "| " + " | ".join(["---"] * len(headers)) + " |"]
    for row in data_list: 
        md.append("| " + " | ".join([str(row.get(h, '')).replace('|', '\\|').strip() for h in headers]) + " |")
    return "\n".join(md) + "\n"

def process_market_action(cfg: MarketPipelineConfig, fetcher: OmniFetcher):
    target = f"{cfg.base_market_dir}/{cfg.date_iso}/cash_market.md"
    if is_valid_file(target): 
        logger.info("    -> Cash Market already secured.")
        return
        
    prices = []
    urls_to_try = [
        f"https://nsearchives.nseindia.com/content/cm/BhavCopy_NSE_CM_0_0_0_{cfg.date_yyyymmdd}_F_0000.csv.zip",
        f"https://nsearchives.nseindia.com/content/historical/EQUITIES/{cfg.date_yyyy}/{cfg.date_mmm}/cm{cfg.date_ddmmmyyyy}bhav.csv.zip",
        f"https://archives.nseindia.com/content/historical/EQUITIES/{cfg.date_yyyy}/{cfg.date_mmm}/cm{cfg.date_ddmmmyyyy}bhav.csv.zip"
    ]
    
    content = None
    for url in urls_to_try:
        content = fetcher.get_content(url)
        if content: break

    if content:
        with zipfile.ZipFile(io.BytesIO(content)) as z:
            filename = [name for name in z.namelist() if name.endswith('.csv')][0]
            with z.open(filename) as f:
                for r in csv.DictReader(io.TextIOWrapper(f, encoding='utf-8')):
                    series = r.get('SERIES') or r.get('SctySrs')
                    if series in ['EQ', 'SM']:
                        prices.append({
                            "Ticker": r.get('SYMBOL') or r.get('TckrSymb'), 
                            "Close": r.get('CLOSE') or r.get('ClsPric'), 
                            "Volume": r.get('TOTTRDQTY') or r.get('TtlTradgVol')
                        })
    if prices:
        with open(target, "w", encoding="utf-8") as f: f.write(f"# Equity Pricing ({cfg.date_iso})\n\n{to_md_table(prices)}")
        logger.info(f"    ✅ Saved Cash Market ({len(prices)} rows)")
    else:
        logger.warning(f"    ⚠️ Cash Market Unavailable (File not yet uploaded by NSE?)")

def process_derivatives(cfg: MarketPipelineConfig, fetcher: OmniFetcher):
    target_fno = f"{cfg.base_market_dir}/{cfg.date_iso}/derivatives.md"
    if is_valid_file(target_fno): 
        logger.info("    -> Derivatives already secured.")
        return
        
    fno = []
    pr_date_str = cfg.trading_today.strftime('%d%m%y')
    
    # CARPET-BOMBING URLS: Hit all known current and legacy NSE endpoints
    urls_to_try = [
        f"https://nsearchives.nseindia.com/content/fo/BhavCopy_NSE_FO_0_0_0_{cfg.date_yyyymmdd}_F_0000.csv.zip",
        f"https://nsearchives.nseindia.com/content/historical/DERIVATIVES/{cfg.date_yyyy}/{cfg.date_mmm}/fo{cfg.date_ddmmmyyyy}bhav.csv.zip",
        f"https://archives.nseindia.com/content/historical/DERIVATIVES/{cfg.date_yyyy}/{cfg.date_mmm}/fo{cfg.date_ddmmmyyyy}bhav.csv.zip",
        f"https://nsearchives.nseindia.com/archives/equities/bhavcopy/pr/PR{pr_date_str}.zip",
        f"https://archives.nseindia.com/archives/equities/bhavcopy/pr/PR{pr_date_str}.zip"
    ]
    
    content = None
    for url in urls_to_try:
        content = fetcher.get_content(url)
        if content: break

    if content:
        try:
            with zipfile.ZipFile(io.BytesIO(content)) as z:
                target_filename = None
                for name in z.namelist():
                    if ('fo' in name.lower() or 'fo_' in name.lower() or 'bhavcopy_nse_fo' in name.lower()) and ('bhav' in name.lower() or 'csv' in name.lower()):
                        target_filename = name
                        break
                
                if not target_filename:
                    csvs = [n for n in z.namelist() if n.endswith('.csv')]
                    if len(csvs) == 1: target_filename = csvs[0]

                if target_filename:
                    file_data = z.read(target_filename)
                    if target_filename.endswith('.zip'):
                        with zipfile.ZipFile(io.BytesIO(file_data)) as inner_z:
                            inner_csv = [n for n in inner_z.namelist() if n.endswith('.csv')][0]
                            csv_text = io.TextIOWrapper(inner_z.open(inner_csv), encoding='utf-8')
                    else:
                        csv_text = io.TextIOWrapper(io.BytesIO(file_data), encoding='utf-8')
                        
                    for r in csv.DictReader(csv_text):
                        inst = r.get('INSTRUMENT') or r.get('FinInstrmTp')
                        if inst in ['FUTSTK', 'FUTIDX', 'FUT', 'IDX']: 
                            fno.append({
                                "Contract": r.get('SYMBOL') or r.get('TckrSymb'), 
                                "Expiry": r.get('EXPIRY_DT') or r.get('XpryDt'), 
                                "OI": r.get('OPEN_INT') or r.get('OpnIntrst')
                            })
        except: pass

    if fno:
        with open(target_fno, "w", encoding="utf-8") as f: f.write(f"# Futures Open Interest ({cfg.date_iso})\n\n{to_md_table(fno)}")
        logger.info(f"    ✅ Saved Derivatives ({len(fno)} rows)")
    else:
        logger.warning(f"    ⚠️ Derivatives Unavailable")

def process_corporate(cfg: MarketPipelineConfig, fetcher: OmniFetcher):
    flag_file = f"{cfg.base_market_dir}/{cfg.date_iso}/.corp_done"
    if os.path.exists(flag_file): return
        
    date_str = cfg.trading_today.strftime('%d-%m-%Y')
    url = f"https://www.nseindia.com/api/corporate-announcements?index=equities&from_date={date_str}&to_date={date_str}"
    
    data = fetcher.get_json(url)
    if not data:
        logger.warning("    ⚠️ Corp Events Unavailable")
        return
        
    hits = 0
    for item in data:
        symbol = item.get('symbol', 'UNKNOWN').strip()
        if symbol == 'UNKNOWN': continue
            
        headline = item.get('desc', '').strip()
        subject = item.get('sm_name', '').strip()
        attach = item.get('attchmntFile')
        
        if attach:
            pdf = attach if str(attach).startswith("http") else f"https://nsearchives.nseindia.com/corporate/{attach}"
        else:
            pdf = "No PDF"
            
        td = f"{cfg.base_corp_dir}/{symbol}/earnings" if "result" in subject.lower() else f"{cfg.base_corp_dir}/{symbol}/filings"
        os.makedirs(td, exist_ok=True)
        
        news_id = item.get('an_dt', '').replace(':', '').replace(' ', '_')
        if not news_id: news_id = str(random.randint(1000, 9999))
        
        fp = f"{td}/{cfg.date_iso}_{news_id}.md"
        if not os.path.exists(fp):
            with open(fp, "w", encoding="utf-8") as f: 
                f.write(f"# {headline}\n\n**Category:** {subject}\n**PDF Source:** {pdf}\n\n{item.get('sm_desc', '')}")
            hits += 1
            
    with open(flag_file, "w") as f: f.write("done")
    logger.info(f"    ✅ Saved {hits} Corp Events (via NSE API)")

def process_macro_flows(cfg: MarketPipelineConfig, fetcher: OmniFetcher):
    """Downloads FII/DII Trading Activity directly from NSE"""
    target = f"{cfg.base_market_dir}/{cfg.date_iso}/macro_flows.md"
    if os.path.exists(target): return
    
    url = "https://www.nseindia.com/api/fiidiiTradeReact"
    data = fetcher.get_json(url)
    if not data: return
    
    records = []
    for item in data:
        records.append({
            "Category": item.get("category"),
            "Buy Value": item.get("buyValue"),
            "Sell Value": item.get("sellValue"),
            "Net Value": item.get("netValue")
        })
        
    if records:
        with open(target, "w", encoding="utf-8") as f: f.write(f"# FII / DII Flows ({cfg.date_iso})\n\n{to_md_table(records)}")
        logger.info(f"    ✅ Saved Macro FII/DII Flows")

def process_regulatory(cfg: MarketPipelineConfig, fetcher: OmniFetcher):
    """Downloads Insider Trading from NSE (Instead of BSE to avoid Cloudflare)"""
    target = f"{cfg.base_market_dir}/{cfg.date_iso}/insider_trading.md"
    if os.path.exists(target): return
    
    url = "https://www.nseindia.com/api/corporates-pit?index=equities"
    data = fetcher.get_json(url)
    if not data or 'data' not in data: return
    
    records = []
    for item in data['data']:
        # Only log records matching the parsed date
        if cfg.date_ddmmmyyyy in item.get('date', '').upper() or cfg.date_iso in item.get('date', ''):
            records.append({
                "Ticker": item.get("symbol"),
                "Acquirer": item.get("acqName"),
                "Type": item.get("secType"),
                "Action": item.get("tdpTransactionType"),
                "Quantity": item.get("secAcq")
            })
            
    if records:
        with open(target, "w", encoding="utf-8") as f: f.write(f"# Insider Trading ({cfg.date_iso})\n\n{to_md_table(records)}")
        logger.info(f"    ✅ Saved Insider Trading ({len(records)} events)")

def main():
    logger.info("=== INITIALIZING DAILY NSE INGESTION ENGINE ===")
    cfg = MarketPipelineConfig()
    
    # Skip weekends for the execution itself
    if cfg.trading_today.weekday() >= 5:
        logger.info("Market Closed (Weekend). Exiting safely.")
        return
        
    logger.info(f"Fetching Daily Market Data for: {cfg.date_iso}")
    fetcher = OmniFetcher()
    
    process_market_action(cfg, fetcher)
    time.sleep(1) # Be gentle to NSE servers
    process_derivatives(cfg, fetcher)
    time.sleep(1)
    process_corporate(cfg, fetcher)
    time.sleep(1)
    process_macro_flows(cfg, fetcher)
    time.sleep(1)
    process_regulatory(cfg, fetcher)
    
    logger.info("=== DAILY INGESTION COMPLETE ===")

if __name__ == "__main__":
    main()
