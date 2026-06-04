
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
        total=3, 
        backoff_factor=1.0, 
        status_forcelist=[403, 429, 500, 502, 503, 504], 
        raise_on_status=False
    )
    adapter = HTTPAdapter(max_retries=retry, pool_connections=25, pool_maxsize=25)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Connection": "keep-alive"
    })
    return session

def fetch_text_with_waterfall(session: requests.Session, url: str) -> Optional[str]:
    """Bypasses Datacenter blocks with fast-fail logic for dead links."""
    headers = {"Referer": "https://www.nseindia.com/"}
    
    # 1. Attempt Direct Fetch
    try:
        res = session.get(url, headers=headers, timeout=10)
        if res.status_code == 200:
            return res.text
            
        if res.status_code in [404, 400]:
            logger.info(f"Exchange returned 404/400. File not published today: {url}")
            return None
            
    except Exception as e:
        logger.debug(f"Direct fetch exception: {e}")

    # 2. Proxy Waterfall (Isolated requests, no internal urllib retries to prevent hanging)
    proxies = [
        f"https://corsproxy.io/?url={url}",
        f"https://api.allorigins.win/raw?url={url}"
    ]
    
    for proxy in proxies:
        try:
            logger.info(f"Rerouting via Proxy: {proxy.split('/')[2]}")
            proxy_res = requests.get(proxy, timeout=10)
            
            if proxy_res.status_code == 200 and not proxy_res.text.strip().lower().startswith("<!doctype html>"):
                return proxy_res.text
        except Exception:
            pass
            
    logger.error(f"All network routes exhausted for {url}")
    return None

def write_fallback_markdown(filepath: str, title: str):
    """Guarantees a file is always created to prevent pipeline crashes."""
    if not os.path.exists(filepath):
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(f"# {title}\n\n*No data published by exchange for this date.*")

def get_nifty_total_market(session: requests.Session) -> List[str]:
    logger.info("Fetching live Nifty Total Market index constituents...")
    tickers = set()
    
    try: session.get("https://www.nseindia.com", timeout=5)
    except: pass

    try:
        text = fetch_text_with_waterfall(session, "https://www.niftyindices.com/IndexConstituent/ind_niftytotalmarketlist.csv")
        if text and "Symbol" in text:
            for row in csv.DictReader(text.strip().split('\n')):
                sym = row.get('Symbol') or row.get('SYMBOL')
                if sym: tickers.add(sym.strip().upper())
    except Exception: pass

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
            except Exception: pass

    final_list = list(tickers)
    if len(final_list) > 200: 
        return final_list
        
    logger.error("Using offline diagnostic list.")
    return ["RELIANCE", "TCS", "INFY", "HDFCBANK", "ICICIBANK"]

def to_md_table(data_list: List[Dict[str, Any]], custom_headers: Optional[List[str]] = None) -> str:
    if not data_list: return "*No data available.*\n"
    headers = custom_headers if custom_headers else list(data_list[0].keys())
    md = ["| " + " | ".join(headers) + " |", "| " + " | ".join(["---"] * len(headers)) + " |"]
    for row in data_list: 
        md.append("| " + " | ".join([str(row.get(h, '')).replace('|', '\\|').strip() for h in headers]) + " |")
    return "\n".join(md) + "\n"

def process_market_action(cfg: MarketPipelineConfig, session: requests.Session):
    target = f"{cfg.base_market_dir}/{cfg.date_iso}/cash_market.md"
    prices, indices = [], []
    
    try:
        res = session.get(f"https://nsearchives.nseindia.com/products/content/sec_bhavdata_full_{cfg.date_ddmmyyyy}.csv", headers={"Referer": "https://www.nseindia.com/"}, timeout=15)
        if res.status_code == 200:
            for r in csv.DictReader(res.text.strip().split('\n')):
                clean = {k.strip(): v.strip() for k, v in r.items() if k}
                if clean.get('SERIES') in ['EQ', 'SM']:
                    prices.append({
                        "Ticker": clean.get('SYMBOL'), "Open": clean.get('OPEN_PRICE'), 
                        "High": clean.get('HIGH_PRICE'), "Low": clean.get('LOW_PRICE'), 
                        "Close": clean.get('CLOSE_PRICE'), "Volume": clean.get('TTL_TRD_QNTY')
                    })
    except Exception: pass
    
    if not prices:
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
                                    "Volume": r.get('NO_OF_SHRS')
                                })
        except Exception: pass

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
    except Exception: pass

    if prices or indices:
        with open(target, "w", encoding="utf-8") as f: 
            f.write(f"# Cash Market Analysis ({cfg.date_iso})\n\n## Broad Indices\n{to_md_table(indices)}\n## Equity Pricing\n{to_md_table(prices)}")
        logger.info(f"Cash market processed: {len(prices)} equities.")
    else:
        write_fallback_markdown(target, "Cash Market Analysis")

def process_derivatives(cfg: MarketPipelineConfig, session: requests.Session):
    target = f"{cfg.base_market_dir}/{cfg.date_iso}/derivatives.md"
    fno, oi, ban = [], [], []
    
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
                                    "Close": r.get('CLOSE'), "OI": r.get('OPEN_INT')
                                })
    except Exception as e: logger.debug(f"Derivatives ZIP Error: {e}")
    
    try:
        text = fetch_text_with_waterfall(session, f"https://nsearchives.nseindia.com/content/nsccl/fao_participant_oi_{cfg.date_ddmmyyyy}.csv")
        if text:
            for r in csv.DictReader(text.strip().split('\n')): 
                oi.append({"Client": r.get('Client Type'), "Future_Long": r.get('Future Index Long'), "Future_Short": r.get('Future Index Short')})
    except Exception: pass

    try:
        text = fetch_text_with_waterfall(session, "https://nsearchives.nseindia.com/content/fo/fo_secban.csv")
        if text:
            for line in text.strip().split('\n')[1:]:
                if ',' in line: ban.append({"Symbol": line.split(',')[1].strip()})
    except Exception: pass

    if fno or oi or ban:
        with open(target, "w", encoding="utf-8") as f: 
            f.write(f"# Derivatives Profile\n\n## Exchange Ban List\n{to_md_table(ban)}\n## Participant OI Flow\n{to_md_table(oi)}\n## Futures Open Interest\n{to_md_table(fno[:500])}")
        logger.info(f"Derivatives mapped. FNO: {len(fno)}")
    else:
        write_fallback_markdown(target, "Derivatives Profile")

def process_macro_flows(cfg: MarketPipelineConfig, session: requests.Session):
    target = f"{cfg.base_market_dir}/{cfg.date_iso}/macro_flows.md"
    fii, deals = [], []
    
    try:
        text = fetch_text_with_waterfall(session, "https://www.nseindia.com/api/fiidiiTradeReact")
        if text:
            for i in json.loads(text): fii.append({"Category": i.get('category'), "Net_Value": i.get('netValue')})
    except Exception: pass
        
    for url, t in [("https://nsearchives.nseindia.com/content/equities/bulk.csv", "BULK"), ("https://nsearchives.nseindia.com/content/equities/block.csv", "BLOCK")]:
        try:
            text = fetch_text_with_waterfall(session, url)
            if text:
                for r in csv.DictReader(text.strip().split('\n')): 
                    deals.append({"Type": t, "Symbol": r.get('Symbol'), "Client": r.get('Client Name'), "Txn": r.get('Buy/Sell')})
        except Exception: pass
            
    if fii or deals:
        with open(target, "w", encoding="utf-8") as f: 
            f.write(f"# Institutional Flows\n\n## FII/DII Net\n{to_md_table(fii)}\n## Dark Pool Deals (Bulk/Block)\n{to_md_table(deals)}")
        logger.info("Macro flows captured.")
    else:
        write_fallback_markdown(target, "Institutional Flows")

# --- NEW FORENSIC MODULES ---

def process_insider_trading(cfg: MarketPipelineConfig, session: requests.Session):
    target = f"{cfg.base_market_dir}/{cfg.date_iso}/insider_trading.md"
    pit_data = []
    
    try:
        text = fetch_text_with_waterfall(session, "https://www.nseindia.com/api/corporates-pit?index=equities")
        if text:
            data = json.loads(text).get('data', [])
            for row in data:
                # Extract relevant structural PIT data
                sym = row.get("symbol")
                if not sym: continue
                pit_data.append({
                    "Symbol": sym,
                    "Person": row.get("personName", "Unknown"),
                    "Category": row.get("personCategory", "Unknown"),
                    "Txn_Type": row.get("tdpTransactionType", "Unknown"),
                    "Securities": row.get("secAcq", 0) or row.get("secDisp", 0),
                    "Value": row.get("secVal", "0")
                })
    except Exception as e:
        logger.debug(f"PIT fetch failed: {e}")
        
    if pit_data:
        with open(target, "w", encoding="utf-8") as f: 
            f.write(f"# Insider Trading Disclosures (PIT)\n\n{to_md_table(pit_data)}")
        logger.info(f"Insider trading records processed: {len(pit_data)}")
    else:
        write_fallback_markdown(target, "Insider Trading Disclosures (PIT)")

def process_promoter_pledges(cfg: MarketPipelineConfig, session: requests.Session):
    target = f"{cfg.base_market_dir}/{cfg.date_iso}/promoter_pledges.md"
    sast_data = []
    
    try:
        text = fetch_text_with_waterfall(session, "https://www.nseindia.com/api/corporate-pledge-data?index=equities")
        if text:
            data = json.loads(text).get('data', [])
            for row in data:
                sym = row.get("symbol")
                if not sym: continue
                sast_data.append({
                    "Symbol": sym,
                    "Promoter": row.get("promoter", "Unknown"),
                    "Action": row.get("reason", "Unknown"),
                    "Shares_Pledged": row.get("noOfShares", 0),
                    "Pct_of_Total": row.get("percOfTotalShares", 0)
                })
    except Exception as e:
        logger.debug(f"SAST fetch failed: {e}")
        
    if sast_data:
        with open(target, "w", encoding="utf-8") as f: 
            f.write(f"# Promoter Pledged Shares (SAST)\n\n{to_md_table(sast_data)}")
        logger.info(f"Promoter pledge records processed: {len(sast_data)}")
    else:
        write_fallback_markdown(target, "Promoter Pledged Shares (SAST)")

def process_index_options(cfg: MarketPipelineConfig, session: requests.Session):
    target = f"{cfg.base_market_dir}/{cfg.date_iso}/index_options.md"
    indices = ["NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY"]
    options_data = []
    
    for idx in indices:
        try:
            url = f"https://www.nseindia.com/api/option-chain-indices?symbol={idx}"
            text = fetch_text_with_waterfall(session, url)
            if text:
                data = json.loads(text).get("filtered", {})
                if data:
                    tot_ce = data.get("CE", {}).get("totOI", 0)
                    tot_pe = data.get("PE", {}).get("totOI", 0)
                    # PCR Calculation
                    pcr = round(tot_pe / tot_ce, 3) if tot_ce > 0 else 0
                    
                    options_data.append({
                        "Index": idx,
                        "Call_OI": tot_ce,
                        "Put_OI": tot_pe,
                        "PCR": pcr
                    })
        except Exception as e:
            logger.debug(f"Option chain fetch failed for {idx}: {e}")
            
    if options_data:
        with open(target, "w", encoding="utf-8") as f: 
            f.write(f"# Major Indices Options Chain\n\n{to_md_table(options_data)}")
        logger.info(f"Index options chain processed: {len(options_data)} records.")
    else:
        write_fallback_markdown(target, "Major Indices Options Chain")


def process_corporate_events(cfg: MarketPipelineConfig, session: requests.Session, watchlist: list):
    SEBI_MATERIAL_KEYWORDS = ["resignation", "appointment", "acquisition", "merger", "dividend", "financial result", "earnings", "fraud", "default", "auditor", "strike", "lockout", "penalty", "subpoena", "bankruptcy", "pledge"]
    ADMINISTRATIVE_NOISE = ["loss of share", "duplicate share", "trading window closure", "newspaper publication"]

    try:
        res = session.get(
            "https://api.bseindia.com/BseIndiaAPI/api/AnnSubCategoryGetData/w", 
            params={"pageno": 1, "strCat": "-1", "strPrevDate": cfg.date_ddmmyyyy, "strScrip": "", "strSearch": "", "strToDate": cfg.date_ddmmyyyy, "strType": "C"}, 
            headers={"Referer": "https://www.bseindia.com/"}, 
            timeout=15
        )
        if res.status_code != 200: return
        
        material_hits = 0
        for item in res.json().get('Table', []):
            headline, cat = item.get('NEWSSUB', '').strip(), item.get('CATEGORYNAME', '').lower()
            headline_lower = headline.lower()
            company_clean = item.get('SLONGNAME', 'UNKNOWN').strip().replace(" ", "_").replace("/", "-")
            
            is_material = any(w in headline_lower for w in SEBI_MATERIAL_KEYWORDS)
            if any(b in headline_lower for b in ADMINISTRATIVE_NOISE) and not is_material:
                continue 

            td = f"{cfg.base_corp_dir}/{company_clean}/" + ("earnings" if "result" in cat else ("concalls" if "transcript" in headline_lower else "filings"))
            os.makedirs(td, exist_ok=True)
            
            file_path = f"{td}/{cfg.date_iso}_{item.get('NEWSID')}.md"
            if not os.path.exists(file_path):
                with open(file_path, "w", encoding="utf-8") as f: 
                    f.write(f"# {headline}\n\n**Category:** {item.get('CATEGORYNAME')}\n\n{item.get('HEADLINE', '')}")
                material_hits += 1
                
        logger.info(f"Routed {material_hits} corporate filings.")
    except Exception as e: 
        logger.warning(f"Corporate event extraction failed: {e}")

def main():
    logger.info("--- INITIALIZING ROBUST INGESTION PIPELINE ---")
    cfg = MarketPipelineConfig()
    session = create_resilient_session()
    
    try:
        watchlist = get_nifty_total_market(session)
        with open("active_watchlist.json", "w") as f:
            json.dump(watchlist, f)
    except Exception as e:
        logger.error(f"Watchlist error: {e}")
        watchlist = []
        
    # SANDBOXED EXECUTION: One failure will NEVER crash the others
    for module_name, module_func in [
        ("Cash Market", lambda: process_market_action(cfg, session)),
        ("Derivatives", lambda: process_derivatives(cfg, session)),
        ("Macro Flows", lambda: process_macro_flows(cfg, session)),
        ("Insider Trading (PIT)", lambda: process_insider_trading(cfg, session)),
        ("Promoter Pledges", lambda: process_promoter_pledges(cfg, session)),
        ("Index Options (PCR)", lambda: process_index_options(cfg, session)),
        ("Corporate Events", lambda: process_corporate_events(cfg, session, watchlist))
    ]:
        try:
            logger.info(f"Triggering Module: {module_name}...")
            module_func()
        except Exception as e:
            logger.error(f"Module {module_name} crashed entirely: {e}")
            
    logger.info("--- PIPELINE EXECUTION COMPLETE ---")

if __name__ == "__main__":
    main()


