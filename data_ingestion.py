
import os
import io
import csv
import json
import zipfile
import logging
import sys
import random
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

class BulletproofFetcher:
    """4-Layer Fallback Architecture to bypass Datacenter Firewalls"""
    def __init__(self):
        self.session = requests.Session()
        
        # Identity Shifting
        self.u_agents = [
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        ]
        self.session.headers.update({
            "User-Agent": random.choice(self.u_agents),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Connection": "keep-alive"
        })
        
        retry = Retry(total=3, backoff_factor=1.0, status_forcelist=[403, 429, 500, 502, 503, 504], raise_on_status=False)
        adapter = HTTPAdapter(max_retries=retry, pool_connections=25, pool_maxsize=25)
        self.session.mount("https://", adapter)
        self.session.mount("http://", adapter)

        # Proxy Waterfall Network
        self.proxies = [
            "",                                      # Layer 1: Direct Request
            "https://api.allorigins.win/raw?url=",   # Layer 2: AllOrigins Node
            "https://corsproxy.io/?url="             # Layer 3: CorsProxy Node
        ]

    def get_text(self, url: str, timeout: int = 15) -> Optional[str]:
        headers = {"Referer": "https://www.nseindia.com/"}
        for proxy in self.proxies:
            target = f"{proxy}{url}" if proxy else url
            try:
                if proxy: logger.info(f"Rerouting via Proxy: {proxy.split('/')[2]}")
                resp = self.session.get(target, headers=headers, timeout=timeout)
                
                # Check for success and avoid proxy error pages
                if resp.status_code == 200 and not resp.text.strip().lower().startswith("<!doctype html>"):
                    return resp.text
                if resp.status_code in [404, 400]:
                    logger.info(f"File not published yet (404): {url}")
                    return None
            except Exception as e:
                logger.debug(f"Text fetch attempt failed: {e}")
        logger.error(f"All network routes exhausted for text: {url}")
        return None

    def get_content(self, url: str, timeout: int = 15) -> Optional[bytes]:
        headers = {"Referer": "https://www.nseindia.com/"}
        for proxy in self.proxies:
            target = f"{proxy}{url}" if proxy else url
            try:
                if proxy: logger.info(f"Rerouting ZIP via Proxy: {proxy.split('/')[2]}")
                resp = self.session.get(target, headers=headers, timeout=timeout)
                if resp.status_code == 200:
                    return resp.content
            except Exception as e:
                logger.debug(f"Binary fetch attempt failed: {e}")
        logger.error(f"All network routes exhausted for binary: {url}")
        return None

    def get_json(self, url: str, params: dict = None, timeout: int = 15) -> Optional[dict]:
        headers = {"Referer": "https://www.bseindia.com/"}
        try:
            resp = self.session.get(url, params=params, headers=headers, timeout=timeout)
            if resp.status_code == 200:
                return resp.json()
        except Exception as e:
            logger.debug(f"JSON fetch failed: {e}")
        return None

def write_fallback_markdown(filepath: str, title: str):
    if not os.path.exists(filepath):
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(f"# {title}\n\n*No data published by exchange for this date.*")

def get_nifty_total_market(fetcher: BulletproofFetcher) -> List[str]:
    logger.info("Fetching live market index constituents...")
    tickers = set()

    def fetch_index(index_name):
        """Automatically tries both NSE naming conventions for any index."""
        for suffix in ["list.csv", "_list.csv"]:
            url = f"https://www.niftyindices.com/IndexConstituent/ind_{index_name}{suffix}"
            text = fetcher.get_text(url)
            if text and "Symbol" in text:
                return text
        return None

    # 1. Try the Primary Total Market Index (750 stocks)
    logger.info("Attempting primary: Nifty Total Market...")
    total_market_text = fetch_index("niftytotalmarket")
    if total_market_text:
        for row in csv.DictReader(total_market_text.strip().split('\n')):
            sym = row.get('Symbol') or row.get('SYMBOL')
            if sym: tickers.add(sym.strip().upper())

    # 2. Threshold Check & Aggressive Fallback
    # If Total Market fails or returns fewer than 700 stocks, assemble them manually.
    if len(tickers) < 700:
        logger.info(f"Primary fetch returned {len(tickers)} stocks. Triggering aggressive fallback assembly...")
        
        fallback_indices = ["nifty500", "niftymicrocap250", "niftysmallcap250"]
        for idx in fallback_indices:
            fallback_text = fetch_index(idx)
            if fallback_text:
                for row in csv.DictReader(fallback_text.strip().split('\n')):
                    sym = row.get('Symbol') or row.get('SYMBOL')
                    if sym: tickers.add(sym.strip().upper())

    final_list = list(tickers)
    logger.info(f"Successfully assembled {len(final_list)} unique tickers for the Watchlist.")
    
    # Absolute worst-case scenario safety net
    return final_list if len(final_list) > 200 else ["RELIANCE", "TCS", "INFY", "HDFCBANK", "ICICIBANK"]

def to_md_table(data_list: List[Dict[str, Any]], custom_headers: Optional[List[str]] = None) -> str:
    if not data_list: return "*No data available.*\n"
    headers = custom_headers if custom_headers else list(data_list[0].keys())
    md = ["| " + " | ".join(headers) + " |", "| " + " | ".join(["---"] * len(headers)) + " |"]
    for row in data_list: 
        md.append("| " + " | ".join([str(row.get(h, '')).replace('|', '\\|').strip() for h in headers]) + " |")
    return "\n".join(md) + "\n"

# --- CORE MODULES USING BULLETPROOF FETCHER ---

def process_market_action(cfg: MarketPipelineConfig, fetcher: BulletproofFetcher):
    target = f"{cfg.base_market_dir}/{cfg.date_iso}/cash_market.md"
    prices, indices = [], []
    
    # 1. Primary NSE Cash Market
    url = f"https://nsearchives.nseindia.com/products/content/sec_bhavdata_full_{cfg.date_ddmmyyyy}.csv"
    text = fetcher.get_text(url)
    if text:
        for r in csv.DictReader(text.strip().split('\n')):
            clean = {k.strip(): v.strip() for k, v in r.items() if k}
            if clean.get('SERIES') in ['EQ', 'SM']:
                prices.append({
                    "Ticker": clean.get('SYMBOL'), "Open": clean.get('OPEN_PRICE'), 
                    "High": clean.get('HIGH_PRICE'), "Low": clean.get('LOW_PRICE'), 
                    "Close": clean.get('CLOSE_PRICE'), 
                    "Volume": clean.get('TTL_TRD_QNTY') or clean.get('TOT_TRD_QTY', 'N/A'),
                    "Delivery_Qty": clean.get('DELIV_QTY', 'N/A'),
                    "Delivery_Pct": clean.get('DELIV_PER', 'N/A')
                })
    
    # 2. NSE Classic ZIP Fallback
    if not prices:
        zip_url = f"https://nsearchives.nseindia.com/content/historical/EQUITIES/{cfg.date_yyyy}/{cfg.date_mmm}/cm{cfg.date_ddmmmyyyy}bhav.csv.zip"
        content = fetcher.get_content(zip_url)
        if content:
            with zipfile.ZipFile(io.BytesIO(content)) as z:
                for filename in z.namelist():
                    with z.open(filename) as f:
                        for r in csv.DictReader(io.TextIOWrapper(f, encoding='utf-8')):
                            if r.get('SERIES') in ['EQ', 'SM']:
                                prices.append({
                                    "Ticker": r.get('SYMBOL'), "Open": r.get('OPEN'), 
                                    "High": r.get('HIGH'), "Low": r.get('LOW'), 
                                    "Close": r.get('CLOSE'), "Volume": r.get('TOTTRDQTY', 'N/A'),
                                    "Delivery_Qty": "N/A", "Delivery_Pct": "N/A"
                                })

    # 3. Ultimate BSE Fallback Backup
    if not prices:
        logger.info("NSE Cash completely blocked. Executing BSE Fallback routine...")
        bse_zip = f"https://www.bseindia.com/download/BhavCopy/Equity/EQ{cfg.date_yymmdd}_CSV.ZIP"
        content = fetcher.get_content(bse_zip)
        if content:
            with zipfile.ZipFile(io.BytesIO(content)) as z:
                for filename in z.namelist():
                    with z.open(filename) as f:
                        for r in csv.DictReader(io.TextIOWrapper(f, encoding='utf-8')):
                            prices.append({
                                "Ticker": r.get('SC_NAME', '').strip(), "Open": r.get('OPEN'), 
                                "High": r.get('HIGH'), "Low": r.get('LOW'), "Close": r.get('CLOSE'), 
                                "Volume": r.get('NO_OF_SHRS', 'N/A'), "Delivery_Qty": "N/A", "Delivery_Pct": "N/A"
                            })

    # Indices Backup
    idx_url = f"https://nsearchives.nseindia.com/content/indices/ind_close_all_{cfg.date_ddmmyyyy}.csv"
    text = fetcher.get_text(idx_url)
    if text:
        for r in csv.DictReader(text.strip().split('\n')):
            if r.get('Index Name', '').strip() in ['Nifty 50', 'Nifty 500', 'Nifty Midcap 150', 'Nifty Smallcap 250']:
                indices.append({
                    "Index": r.get('Index Name', '').strip(), "Open": r.get('Open Index Value'), 
                    "High": r.get('High Index Value'), "Low": r.get('Low Index Value'), 
                    "Close": r.get('Closing Index Value')
                })

    if prices or indices:
        with open(target, "w", encoding="utf-8") as f: 
            f.write(f"# Cash Market Analysis ({cfg.date_iso})\n\n## Broad Indices\n{to_md_table(indices)}\n## Equity Pricing\n{to_md_table(prices)}")
        logger.info(f"Cash market processed: {len(prices)} equities mapped with Delivery metrics.")
    else:
        write_fallback_markdown(target, "Cash Market Analysis")

def process_derivatives_and_options(cfg: MarketPipelineConfig, fetcher: BulletproofFetcher):
    target_fno = f"{cfg.base_market_dir}/{cfg.date_iso}/derivatives.md"
    target_opt = f"{cfg.base_market_dir}/{cfg.date_iso}/index_options.md"
    
    fno, oi, ban = [], [], []
    options_data = {
        "NIFTY": {"CE": 0, "PE": 0}, "BANKNIFTY": {"CE": 0, "PE": 0}, 
        "FINNIFTY": {"CE": 0, "PE": 0}, "MIDCPNIFTY": {"CE": 0, "PE": 0}
    }
    
    fo_url = f"https://nsearchives.nseindia.com/content/historical/DERIVATIVES/{cfg.date_yyyy}/{cfg.date_mmm}/fo{cfg.date_ddmmmyyyy}bhav.csv.zip"
    content = fetcher.get_content(fo_url)
    if content:
        with zipfile.ZipFile(io.BytesIO(content)) as z:
            for file_name in z.namelist():
                with z.open(file_name) as zf:
                    for r in csv.DictReader(io.TextIOWrapper(zf, encoding='utf-8')):
                        inst = r.get('INSTRUMENT')
                        sym = r.get('SYMBOL')
                        open_int = int(r.get('OPEN_INT', 0))
                        
                        if inst in ['FUTSTK', 'FUTIDX']: 
                            fno.append({
                                "Contract": sym, "Expiry": r.get('EXPIRY_DT'), 
                                "Close": r.get('CLOSE'), "OI": open_int
                            })
                        
                        if inst == 'OPTIDX' and sym in options_data:
                            opt_typ = r.get('OPTION_TYP')
                            if opt_typ in ['CE', 'PE']:
                                options_data[sym][opt_typ] += open_int
    
    oi_url = f"https://nsearchives.nseindia.com/content/nsccl/fao_participant_oi_{cfg.date_ddmmyyyy}.csv"
    text = fetcher.get_text(oi_url)
    if text:
        for r in csv.DictReader(text.strip().split('\n')): 
            oi.append({"Client": r.get('Client Type'), "Future_Long": r.get('Future Index Long'), "Future_Short": r.get('Future Index Short')})

    ban_url = "https://nsearchives.nseindia.com/content/fo/fo_secban.csv"
    text = fetcher.get_text(ban_url)
    if text:
        for line in text.strip().split('\n')[1:]:
            if ',' in line: ban.append({"Symbol": line.split(',')[1].strip()})

    if fno or oi or ban:
        with open(target_fno, "w", encoding="utf-8") as f: 
            f.write(f"# Derivatives Profile\n\n## Exchange Ban List\n{to_md_table(ban)}\n## Participant OI Flow\n{to_md_table(oi)}\n## Futures Open Interest\n{to_md_table(fno[:500])}")
        logger.info(f"Derivatives mapped. FNO records: {len(fno)}")
    else:
        write_fallback_markdown(target_fno, "Derivatives Profile")
        
    final_pcr_data = []
    for idx, data in options_data.items():
        if data["CE"] > 0 or data["PE"] > 0:
            pcr = round(data["PE"] / data["CE"], 3) if data["CE"] > 0 else 0
            final_pcr_data.append({"Index": idx, "Call_OI": data["CE"], "Put_OI": data["PE"], "PCR": pcr})
            
    if final_pcr_data:
        with open(target_opt, "w", encoding="utf-8") as f: 
            f.write(f"# Major Indices Options Chain (Locally Computed)\n\n{to_md_table(final_pcr_data)}")
        logger.info("Index Options PCR computed successfully offline.")
    else:
        write_fallback_markdown(target_opt, "Major Indices Options Chain")

def process_macro_flows(cfg: MarketPipelineConfig, fetcher: BulletproofFetcher):
    target = f"{cfg.base_market_dir}/{cfg.date_iso}/macro_flows.md"
    fii, deals = [], []
    
    fii_url = "https://www.nseindia.com/api/fiidiiTradeReact"
    text = fetcher.get_text(fii_url)
    if text:
        try:
            for i in json.loads(text): fii.append({"Category": i.get('category'), "Net_Value": i.get('netValue')})
        except: pass
        
    for url, t in [("https://nsearchives.nseindia.com/content/equities/bulk.csv", "BULK"), ("https://nsearchives.nseindia.com/content/equities/block.csv", "BLOCK")]:
        text = fetcher.get_text(url)
        if text:
            for r in csv.DictReader(text.strip().split('\n')): 
                deals.append({"Type": t, "Symbol": r.get('Symbol'), "Client": r.get('Client Name'), "Txn": r.get('Buy/Sell')})
            
    if fii or deals:
        with open(target, "w", encoding="utf-8") as f: 
            f.write(f"# Institutional Flows\n\n## FII/DII Net\n{to_md_table(fii)}\n## Dark Pool Deals (Bulk/Block)\n{to_md_table(deals)}")
        logger.info("Macro flows captured.")
    else:
        write_fallback_markdown(target, "Institutional Flows")

def process_bse_regulatory_data(cfg: MarketPipelineConfig, fetcher: BulletproofFetcher):
    target_pit = f"{cfg.base_market_dir}/{cfg.date_iso}/insider_trading.md"
    target_sast = f"{cfg.base_market_dir}/{cfg.date_iso}/promoter_pledges.md"
    
    pit_data, sast_data = [], []
    
    pit_url = f"https://api.bseindia.com/BseIndiaAPI/api/InsiderTrading/w?pageno=1&strCat=-1&strPrevDate={cfg.date_ddmmyyyy}&strScrip=&strSearch=&strToDate={cfg.date_ddmmyyyy}"
    data = fetcher.get_json(pit_url)
    if data:
        for item in data.get('Table', []):
            sym = item.get('SLONGNAME') or item.get('COMPANY_NAME')
            if sym:
                pit_data.append({
                    "Symbol": sym.strip(),
                    "Acquirer": item.get('NAME_OF_THE_ACQUIRER_DISPOSER') or item.get('ACQUIRER_NAME', 'Unknown'),
                    "Category": item.get('CATEGORY_OF_PERSON', 'Unknown'),
                    "Action": item.get('ACQUISITION_DISPOSAL_TRANSACTION_TYPE', 'Unknown'),
                    "Qty": item.get('NO_OF_SECURITIES') or item.get('NO_OF_SHARES', 0)
                })

    sast_url = f"https://api.bseindia.com/BseIndiaAPI/api/SastData/w?pageno=1&strCat=-1&strPrevDate={cfg.date_ddmmyyyy}&strScrip=&strSearch=&strToDate={cfg.date_ddmmyyyy}"
    data = fetcher.get_json(sast_url)
    if data:
        for item in data.get('Table', []):
            sym = item.get('COMPANY_NAME') or item.get('SLONGNAME')
            if sym:
                sast_data.append({
                    "Symbol": sym.strip(),
                    "Promoter": item.get('PROMOTER_NAME', 'Unknown'),
                    "Event": item.get('EVENT_TYPE', 'Unknown'),
                    "Shares": item.get('NO_OF_SHARES', 0),
                    "Percent": item.get('PERCENTAGE', 0)
                })

    if pit_data:
        with open(target_pit, "w", encoding="utf-8") as f: f.write(f"# Insider Trading Disclosures (PIT)\n\n{to_md_table(pit_data)}")
        logger.info(f"Insider trading records processed via BSE: {len(pit_data)}")
    else:
        write_fallback_markdown(target_pit, "Insider Trading Disclosures (PIT)")
        
    if sast_data:
        with open(target_sast, "w", encoding="utf-8") as f: f.write(f"# Promoter Pledged Shares (SAST)\n\n{to_md_table(sast_data)}")
        logger.info(f"Promoter pledge records processed via BSE: {len(sast_data)}")
    else:
        write_fallback_markdown(target_sast, "Promoter Pledged Shares (SAST)")

def process_corporate_events(cfg: MarketPipelineConfig, fetcher: BulletproofFetcher, watchlist: list):
    SEBI_MATERIAL_KEYWORDS = ["resignation", "appointment", "acquisition", "merger", "dividend", "financial result", "earnings", "fraud", "default", "auditor", "strike", "lockout", "penalty", "subpoena", "bankruptcy", "pledge"]
    ADMINISTRATIVE_NOISE = ["loss of share", "duplicate share", "trading window closure", "newspaper publication"]

    url = "https://api.bseindia.com/BseIndiaAPI/api/AnnSubCategoryGetData/w"
    params = {"pageno": 1, "strCat": "-1", "strPrevDate": cfg.date_ddmmyyyy, "strScrip": "", "strSearch": "", "strToDate": cfg.date_ddmmyyyy, "strType": "C"}
    
    data = fetcher.get_json(url, params=params)
    if not data: return
        
    material_hits = 0
    for item in data.get('Table', []):
        headline, cat = item.get('NEWSSUB', '').strip(), item.get('CATEGORYNAME', '').lower()
        headline_lower = headline.lower()
        company_clean = item.get('SLONGNAME', 'UNKNOWN').strip().replace(" ", "_").replace("/", "-")
        
        # Construct the direct PDF URL for the AI/User to reference
        attachment = item.get('ATTACHMENTNAME')
        pdf_link = f"https://www.bseindia.com/xml-data/corpfiling/AttachLive/{attachment}" if attachment else "No PDF Attached"
        
        is_material = any(w in headline_lower for w in SEBI_MATERIAL_KEYWORDS)
        if any(b in headline_lower for b in ADMINISTRATIVE_NOISE) and not is_material:
            continue 

        is_concall = "transcript" in headline_lower or "concall" in headline_lower
        
        if is_concall:
            td = f"{cfg.base_corp_dir}/{company_clean}/concalls"
            logger.info(f"🎤 Concall Transcript logged for: {company_clean}")
        elif "result" in cat:
            td = f"{cfg.base_corp_dir}/{company_clean}/earnings"
        else:
            td = f"{cfg.base_corp_dir}/{company_clean}/filings"
            
        os.makedirs(td, exist_ok=True)
        
        file_path = f"{td}/{cfg.date_iso}_{item.get('NEWSID')}.md"
        if not os.path.exists(file_path):
            with open(file_path, "w", encoding="utf-8") as f: 
                f.write(f"# {headline}\n\n**Category:** {item.get('CATEGORYNAME')}\n**PDF Source:** {pdf_link}\n\n{item.get('HEADLINE', '')}")
            material_hits += 1
            
    logger.info(f"Routed {material_hits} corporate filings via BSE.")

def main():
    logger.info("--- INITIALIZING BULLETPROOF INGESTION PIPELINE ---")
    cfg = MarketPipelineConfig()
    fetcher = BulletproofFetcher()
    
    try:
        watchlist = get_nifty_total_market(fetcher)
        with open("active_watchlist.json", "w") as f:
            json.dump(watchlist, f)
    except Exception as e:
        logger.error(f"Watchlist error: {e}")
        watchlist = []
        
    for module_name, module_func in [
        ("Cash Market", lambda: process_market_action(cfg, fetcher)),
        ("Derivatives & Options", lambda: process_derivatives_and_options(cfg, fetcher)),
        ("Macro Flows", lambda: process_macro_flows(cfg, fetcher)),
        ("Regulatory Data", lambda: process_bse_regulatory_data(cfg, fetcher)),
        ("Corporate Events", lambda: process_corporate_events(cfg, fetcher, watchlist))
    ]:
        try:
            logger.info(f"Triggering Module: {module_name}...")
            module_func()
        except Exception as e:
            logger.error(f"Module {module_name} crashed entirely: {e}")
            
    logger.info("--- PIPELINE EXECUTION COMPLETE ---")

if __name__ == "__main__":
    main()



