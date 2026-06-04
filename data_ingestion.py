
import os
import io
import csv
import json
import zipfile
import logging
import sys
import time
from datetime import datetime
from typing import List, Dict, Any, Optional

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# Configure Logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s | %(levelname)-8s | %(message)s', handlers=[logging.StreamHandler(sys.stdout)])
logger = logging.getLogger(__name__)

class MarketPipelineConfig:
    def __init__(self):
        self.today = datetime.today()
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

def create_resilient_session() -> requests.Session:
    session = requests.Session()
    retry = Retry(
        total=5, 
        backoff_factor=1.5, 
        status_forcelist=[403, 429, 500, 502, 503, 504], 
        raise_on_status=False
    )
    adapter = HTTPAdapter(max_retries=retry, pool_connections=25, pool_maxsize=25)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Connection": "keep-alive"
    })
    return session

def fetch_text_with_waterfall(session: requests.Session, url: str) -> Optional[str]:
    """Bypasses Datacenter blocks by routing through external proxy endpoints if direct fetch fails."""
    headers = {"Referer": "https://www.nseindia.com/"}
    
    # 1. Attempt Direct Fetch
    try:
        res = session.get(url, headers=headers, timeout=12)
        if res.status_code == 200:
            return res.text
        logger.warning(f"Direct fetch blocked (HTTP {res.status_code}): {url}")
    except Exception as e:
        logger.warning(f"Direct fetch exception: {e}")

    # 2. Proxy Waterfall (Masks GitHub Actions IP)
    proxies = [
        f"https://api.allorigins.win/raw?url={url}",
        f"https://corsproxy.io/?url={url}"
    ]
    
    for proxy in proxies:
        try:
            logger.info(f"Rerouting via Proxy: {proxy}")
            res = session.get(proxy, timeout=15)
            # Ensure it didn't return an HTML error page instead of raw CSV/JSON data
            if res.status_code == 200 and not res.text.strip().lower().startswith("<!doctype html>"):
                return res.text
        except Exception:
            pass
            
    logger.error(f"FATAL: All proxy routing failed for {url}")
    return None

def get_nifty_total_market(session: requests.Session) -> List[str]:
    logger.info("Fetching live Nifty Total Market index constituents...")
    tickers = set()
    
    # Prime cookies for NSE endpoints
    try: session.get("https://www.nseindia.com", timeout=10)
    except: pass
    try: session.get("https://www.niftyindices.com", timeout=10)
    except: pass

    # Attempt 1: Nifty Total Market
    try:
        text = fetch_text_with_waterfall(session, "https://www.niftyindices.com/IndexConstituent/ind_niftytotalmarketlist.csv")
        if text and "Symbol" in text:
            for row in csv.DictReader(text.strip().split('\n')):
                sym = row.get('Symbol') or row.get('SYMBOL')
                if sym: tickers.add(sym.strip().upper())
    except Exception as e: 
        logger.warning(f"Primary index fetch failed: {e}")

    # Fallback: Assemble Nifty 500 + Microcap 250
    if len(tickers) < 500:
        logger.info("Executing fallback: Assembling Nifty 500 + Microcap 250...")
        for url in [
            "https://www.niftyindices.com/IndexConstituent/ind_nifty500list.csv", 
            "https://www.niftyindices.com/IndexConstituent/ind_niftymicrocap250list.csv"
        ]:
            try:
                text = fetch_text_with_waterfall(session, url)
                if text:
                    for row in csv.DictReader(text.strip().split('\n')):
                        sym = row.get('Symbol') or row.get('SYMBOL')
                        if sym: tickers.add(sym.strip().upper())
            except Exception: 
                pass

    final_list = list(tickers)
    if len(final_list) > 200: 
        return final_list
        
    logger.error("All exchange fetches failed. Using hardcoded offline diagnostic list.")
    return ["RELIANCE", "TCS", "INFY", "HDFCBANK", "ICICIBANK", "SBIN", "ITC"]

def to_md_table(data_list: List[Dict[str, Any]], custom_headers: Optional[List[str]] = None) -> str:
    if not data_list: 
        return "*No data logged.*\n"
    headers = custom_headers if custom_headers else list(data_list[0].keys())
    md = ["| " + " | ".join(headers) + " |", "| " + " | ".join(["---"] * len(headers)) + " |"]
    for row in data_list: 
        md.append("| " + " | ".join([str(row.get(h, '')).replace('|', '\\|').strip() for h in headers]) + " |")
    return "\n".join(md) + "\n"

def process_market_action(cfg: MarketPipelineConfig, session: requests.Session):
    target = f"{cfg.base_market_dir}/{cfg.date_iso}/cash_market.md"
    if os.path.exists(target): 
        return
        
    prices, indices = [], []
    
    # Extract Cash Market Bhavcopy (NSE directly to BSE Fallback due to file size)
    try:
        res = session.get(f"https://nsearchives.nseindia.com/products/content/sec_bhavdata_full_{cfg.date_ddmmyyyy}.csv", headers={"Referer": "https://www.nseindia.com/"}, timeout=15)
        if res.status_code == 200:
            for r in csv.DictReader(res.text.strip().split('\n')):
                clean = {k.strip(): v.strip() for k, v in r.items() if k}
                if clean.get('SERIES') in ['EQ', 'SM']:
                    prices.append({
                        "Ticker": clean.get('SYMBOL'), "Open": clean.get('OPEN_PRICE'), 
                        "High": clean.get('HIGH_PRICE'), "Low": clean.get('LOW_PRICE'), 
                        "Close": clean.get('CLOSE_PRICE'), "Volume": clean.get('TTL_TRD_QNTY'), 
                        "Delivery_Qty": clean.get('DELIV_QTY'), "Delivery_Pct": clean.get('DELIV_PER')
                    })
    except Exception: 
        pass
    
    # Fallback to BSE if NSE fails (Massive savior for Actions Runners)
    if not prices:
        logger.info("NSE Cash blocked. Executing BSE Fallback routine...")
        try:
            res = session.get(f"https://www.bseindia.com/download/BhavCopy/Equity/EQ{cfg.date_yymmdd}_CSV.ZIP", headers={"Referer": "https://www.bseindia.com/"}, timeout=15)
            if res.status_code == 200:
                with zipfile.ZipFile(io.BytesIO(res.content)) as z:
                    for filename in z.namelist():
                        with z.open(filename) as f:
                            for r in csv.DictReader(io.TextIOWrapper(f, encoding='utf-8')):
                                prices.append({
                                    "Ticker": r.get('SC_NAME', '').strip(), "Open": r.get('OPEN'), 
                                    "High": r.get('HIGH'), "Low": r.get('LOW'), "Close": r.get('CLOSE'), 
                                    "Volume": r.get('NO_OF_SHRS'), "Delivery_Qty": "N/A", "Delivery_Pct": "N/A"
                                })
        except Exception: 
            pass

    # Extract Index Closing Values via Waterfall
    try:
        text = fetch_text_with_waterfall(session, f"https://nsearchives.nseindia.com/content/indices/ind_close_all_{cfg.date_ddmmyyyy}.csv")
        if text:
            for r in csv.DictReader(text.strip().split('\n')):
                if r.get('Index Name', '').strip() in ['Nifty 50', 'Nifty 500', 'Nifty Midcap 150', 'Nifty Smallcap 250']:
                    indices.append({
                        "Index": r.get('Index Name', '').strip(), "Open": r.get('Open Index Value'), 
                        "High": r.get('High Index Value'), "Low": r.get('Low Index Value'), 
                        "Close": r.get('Closing Index Value')
                    })
    except Exception: 
        pass

    if prices or indices:
        with open(target, "w", encoding="utf-8") as f: 
            f.write(f"# Cash Market Analysis ({cfg.date_iso})\n\n## Broad Indices\n{to_md_table(indices)}\n## Equity Pricing\n{to_md_table(prices)}")
        logger.info(f"Cash market payload processed: {len(prices)} individual equities mapped.")
    else:
        logger.error("FATAL: Failed to capture both NSE and BSE Cash Market data.")

def process_derivatives(cfg: MarketPipelineConfig, session: requests.Session):
    target = f"{cfg.base_market_dir}/{cfg.date_iso}/derivatives.md"
    if os.path.exists(target): 
        return
        
    fno, oi, ban = [], [], []
    
    # Futures & Options Matrix (ZIP - Direct only to prevent proxy corruption)
    try:
        fo_url = f"https://nsearchives.nseindia.com/content/historical/DERIVATIVES/{cfg.date_yyyy}/{cfg.date_mmm}/fo{cfg.date_ddmmmyyyy}bhav.csv.zip"
        res = session.get(fo_url, headers={"Referer": "https://www.nseindia.com/"}, timeout=15)
        if res.status_code == 200:
            with zipfile.ZipFile(io.BytesIO(res.content)) as z:
                for file_name in z.namelist():
                    with z.open(file_name) as zf:
                        for r in csv.DictReader(io.TextIOWrapper(zf, encoding='utf-8')):
                            if r.get('INSTRUMENT') in ['FUTSTK', 'FUTIDX']: 
                                fno.append({
                                    "Contract": r.get('SYMBOL'), "Expiry": r.get('EXPIRY_DT'), 
                                    "Close": r.get('CLOSE'), "OI": r.get('OPEN_INT'), "Change_In_OI": r.get('CHG_IN_OI')
                                })
        else:
            logger.warning(f"Derivatives ZIP blocked directly (HTTP {res.status_code}).")
    except Exception as e: 
        logger.warning(f"Derivatives ZIP Error: {e}")
    
    # Client Level OI Participant Data
    try:
        text = fetch_text_with_waterfall(session, f"https://nsearchives.nseindia.com/content/nsccl/fao_participant_oi_{cfg.date_ddmmyyyy}.csv")
        if text:
            for r in csv.DictReader(text.strip().split('\n')): 
                oi.append({
                    "Client": r.get('Client Type'), "Future_Long": r.get('Future Index Long'), 
                    "Future_Short": r.get('Future Index Short')
                })
    except Exception: 
        pass

    # Regulatory F&O Ban List
    try:
        text = fetch_text_with_waterfall(session, "https://nsearchives.nseindia.com/content/fo/fo_secban.csv")
        if text:
            for line in text.strip().split('\n')[1:]:
                if ',' in line: 
                    ban.append({"Symbol": line.split(',')[1].strip()})
    except Exception: 
        pass

    if fno or oi or ban:
        with open(target, "w", encoding="utf-8") as f: 
            f.write(f"# Derivatives Profile\n\n## Exchange Ban List\n{to_md_table(ban)}\n## Participant OI Flow\n{to_md_table(oi)}\n## Futures Open Interest\n{to_md_table(fno[:500])}")
        logger.info(f"Derivatives mapped. FNO: {len(fno)} | Ban List: {len(ban)}")
    else:
        logger.warning("Warning: Complete Derivatives payload failed to capture.")

def process_macro_flows(cfg: MarketPipelineConfig, session: requests.Session):
    target = f"{cfg.base_market_dir}/{cfg.date_iso}/macro_flows.md"
    if os.path.exists(target): 
        return
        
    fii, deals = [], []
    
    # FII / DII Institutional Flows
    try:
        text = fetch_text_with_waterfall(session, "https://www.nseindia.com/api/fiidiiTradeReact")
        if text:
            for i in json.loads(text): 
                fii.append({"Category": i.get('category'), "Net_Value": i.get('netValue')})
    except Exception: 
        pass
        
    # Bulk & Block Deals
    for url, t in [("https://nsearchives.nseindia.com/content/equities/bulk.csv", "BULK"), ("https://nsearchives.nseindia.com/content/equities/block.csv", "BLOCK")]:
        try:
            text = fetch_text_with_waterfall(session, url)
            if text:
                for r in csv.DictReader(text.strip().split('\n')): 
                    deals.append({"Type": t, "Symbol": r.get('Symbol'), "Client": r.get('Client Name'), "Txn": r.get('Buy/Sell')})
        except Exception: 
            pass
            
    if fii or deals:
        with open(target, "w", encoding="utf-8") as f: 
            f.write(f"# Institutional Flows\n\n## FII/DII Net\n{to_md_table(fii)}\n## Dark Pool Deals (Bulk/Block)\n{to_md_table(deals)}")
        logger.info("Institutional Capital Flows captured.")

def process_surveillance(cfg: MarketPipelineConfig, session: requests.Session):
    target = f"{cfg.base_market_dir}/{cfg.date_iso}/surveillance.md"
    if os.path.exists(target): 
        return
        
    risk = []
    
    # Regulatory Surveillance Grids (ASM / GSM)
    for url, t in [("https://nsearchives.nseindia.com/content/circulars/surveillance/ASM_latest.csv", "ASM Framework"), ("https://nsearchives.nseindia.com/content/circulars/surveillance/GSM_latest.csv", "GSM Framework")]:
        try:
            text = fetch_text_with_waterfall(session, url)
            if text:
                for r in csv.DictReader(text.strip().split('\n')):
                    s = r.get('Symbol') or r.get('SYMBOL')
                    if s: risk.append({"Symbol": s.strip(), "Framework": t})
        except Exception: 
            pass
            
    if risk:
        with open(target, "w", encoding="utf-8") as f: 
            f.write(f"# SEBI Surveillance Matrix\n\n{to_md_table(risk)}")
        logger.info(f"Regulatory surveillance mapped: {len(risk)} assets flagged.")

def process_corporate_events(cfg: MarketPipelineConfig, session: requests.Session, watchlist: list):
    watchlist_set = set(watchlist)

    SEBI_MATERIAL_KEYWORDS = [
        "resignation", "appointment", "acquisition", "merger", "amalgamation", 
        "dividend", "financial result", "earnings", "rating", "fraud", "default", 
        "auditor", "strike", "lockout", "capacity addition", "order", "contract", 
        "penalty", "subpoena", "bankruptcy", "insolvency", "delisting", "pledge", "revocation"
    ]

    ADMINISTRATIVE_NOISE = [
        "loss of share", "duplicate share", "trading window closure", 
        "newspaper publication", "newspaper advertisement", "compliance certificate"
    ]

    try:
        # BSE API is typically extremely open to Actions, no proxy needed
        res = session.get(
            "https://api.bseindia.com/BseIndiaAPI/api/AnnSubCategoryGetData/w", 
            params={"pageno": 1, "strCat": "-1", "strPrevDate": cfg.date_ddmmyyyy, "strScrip": "", "strSearch": "", "strToDate": cfg.date_ddmmyyyy, "strType": "C"}, 
            headers={"Referer": "https://www.bseindia.com/"}, 
            timeout=15
        )
        if res.status_code != 200: 
            logger.warning("BSE Corp Events API unreachable.")
            return
        
        material_hits = 0
        
        for item in res.json().get('Table', []):
            headline = item.get('NEWSSUB', '').strip()
            headline_lower = headline.lower()
            cat = item.get('CATEGORYNAME', '').lower()
            company_clean = item.get('SLONGNAME', 'UNKNOWN').strip().replace(" ", "_").replace("/", "-")
            
            is_material = any(w in headline_lower for w in SEBI_MATERIAL_KEYWORDS)
            is_noise = any(b in headline_lower for b in ADMINISTRATIVE_NOISE)

            if is_noise and not is_material:
                continue 

            if "result" in cat:
                td = f"{cfg.base_corp_dir}/{company_clean}/earnings"
            elif "transcript" in headline_lower or "concall" in headline_lower:
                td = f"{cfg.base_corp_dir}/{company_clean}/concalls"
            else:
                td = f"{cfg.base_corp_dir}/{company_clean}/filings"
                
            os.makedirs(td, exist_ok=True)
            
            file_path = f"{td}/{cfg.date_iso}_{item.get('NEWSID')}.md"
            if not os.path.exists(file_path):
                with open(file_path, "w", encoding="utf-8") as f: 
                    f.write(f"# {headline}\n\n**Category:** {item.get('CATEGORYNAME')}\n\n{item.get('HEADLINE', '')}")
                material_hits += 1
                
        logger.info(f"Extracted and routed {material_hits} material corporate filings.")
                
    except Exception as e: 
        logger.warning(f"Corporate event extraction failed: {e}")

def main():
    logger.info("--- INITIALIZING ROBUST MARKET INGESTION AUTOMATION PIPELINE ---")
    cfg = MarketPipelineConfig()
    session = create_resilient_session()
    
    watchlist = get_nifty_total_market(session)
    with open("active_watchlist.json", "w") as f:
        json.dump(watchlist, f)
        
    process_market_action(cfg, session)
    process_derivatives(cfg, session)
    process_macro_flows(cfg, session)
    process_surveillance(cfg, session)
    process_corporate_events(cfg, session, watchlist)
    
    logger.info("--- PIPELINE EXECUTION COMPLETE. DATA AWAITING AI PROCESSING ---")

if __name__ == "__main__":
    main()


