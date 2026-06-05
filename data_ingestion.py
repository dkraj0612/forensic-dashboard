
import os
import io
import csv
import json
import zipfile
import logging
import sys
import random
import time
from datetime import datetime
from typing import List, Dict, Any, Optional

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from playwright.sync_api import sync_playwright

# Configure Logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s | %(levelname)-8s | %(message)s', handlers=[logging.StreamHandler(sys.stdout)])
logger = logging.getLogger(__name__)

class MarketPipelineConfig:
    def __init__(self):
        self.today = datetime.today()
        self.date_iso = self.today.strftime('%Y-%m-%d')
        self.date_ddmmyyyy = self.today.strftime('%d%m%Y')
        self.date_yymmdd = self.today.strftime('%y%m%d')
        self.date_yyyymmdd = self.today.strftime('%Y%m%d') 
        self.date_mmm = self.today.strftime('%b').upper()
        self.date_yyyy = self.today.strftime('%Y')
        self.date_ddmmmyyyy = self.today.strftime('%d%b%Y').upper()
        
        self.base_market_dir = "market_data"
        self.base_corp_dir = "corporate_data"
        
        os.makedirs(f"{self.base_market_dir}/{self.date_iso}", exist_ok=True)
        os.makedirs(f"{self.base_market_dir}/adjustments", exist_ok=True)

class OmniFetcher:
    """The Ultimate Fetcher: 3-Layer Proxies + Playwright DOM-Level WAF Bypass"""
    def __init__(self):
        # --- 1. LIGHTWEIGHT REQUESTS ENGINE ---
        self.session = requests.Session()
        self.u_agents = [
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
        ]
        self.session.headers.update({
            "User-Agent": random.choice(self.u_agents),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Encoding": "gzip, deflate", # Removed br to avoid compression issues
            "Connection": "keep-alive"
        })
        
        retry = Retry(total=2, backoff_factor=1.0, status_forcelist=[403, 429, 500, 502, 503, 504], raise_on_status=False)
        adapter = HTTPAdapter(max_retries=retry, pool_connections=25, pool_maxsize=25)
        self.session.mount("https://", adapter)
        self.session.mount("http://", adapter)

        self.proxies = [
            "",                                      # Layer 1: Direct
            "https://api.allorigins.win/raw?url=",   # Layer 2: AllOrigins
            "https://corsproxy.io/?url="             # Layer 3: CorsProxy
        ]

        # --- 2. HEAVY PLAYWRIGHT ENGINE ---
        self.pw = None
        self.browser = None
        self.pw_context = None
        self.page = None

    def _init_playwright(self):
        """Launches the WAF-Bypass Browser Context"""
        if self.pw_context is None:
            logger.warning("Engaging Playwright Ghost-Human Browser...")
            self.pw = sync_playwright().start()
            
            # WAF BYPASS: Disable HTTP/2 & Automation Flags to defeat Akamai/Cloudflare
            self.browser = self.pw.chromium.launch(
                headless=True, 
                args=[
                    "--disable-http2", 
                    "--disable-blink-features=AutomationControlled",
                    "--no-sandbox"
                ]
            )
            
            self.pw_context = self.browser.new_context(
                user_agent=random.choice(self.u_agents),
                viewport={"width": 1920, "height": 1080},
                ignore_https_errors=True
            )
            self.page = self.pw_context.new_page()
            
            logger.info("Priming cookies and solving firewall challenges...")
            try:
                self.page.goto("https://www.nseindia.com", wait_until="domcontentloaded", timeout=45000)
                time.sleep(3) # Allow scripts to run
                self.page.goto("https://www.bseindia.com", wait_until="domcontentloaded", timeout=45000)
                time.sleep(3)
            except Exception as e:
                logger.debug(f"Priming took too long: {e}")

    def prime_bse_cookies(self):
        try:
            self.session.get("https://www.bseindia.com", timeout=15)
        except Exception: pass

    def get_text(self, url: str, timeout: int = 25) -> Optional[str]:
        headers = {"Referer": "https://www.nseindia.com/"}
        
        # Phase 1: Proxy Waterfall
        for proxy in self.proxies:
            target = f"{proxy}{url}" if proxy else url
            try:
                if proxy: logger.info(f"Rerouting text via Proxy: {proxy.split('/')[2]}")
                resp = self.session.get(target, headers=headers, timeout=timeout)
                if resp.status_code == 200 and not resp.text.strip().lower().startswith("<!doctype html>"):
                    return resp.text
            except Exception: pass
            
        # Phase 2: Playwright Fallback
        self._init_playwright()
        try:
            resp = self.page.request.get(url, headers=headers, timeout=timeout*1000)
            if resp.ok: return resp.text()
        except Exception as e: logger.error(f"Playwright text fetch failed: {e}")
        
        return None

    def get_content(self, url: str, timeout: int = 45) -> Optional[bytes]:
        headers = {"Referer": "https://www.nseindia.com/"}
        
        # Phase 1: Proxy Waterfall
        try:
            resp = self.session.get(url, headers=headers, timeout=timeout)
            if resp.status_code == 200 and resp.content.startswith(b'PK'): return resp.content
        except Exception: pass
            
        try:
            logger.info("Rerouting ZIP via CorsProxy...")
            resp = self.session.get(f"https://corsproxy.io/?url={url}", headers=headers, timeout=timeout)
            if resp.status_code == 200 and resp.content.startswith(b'PK'): return resp.content
        except Exception: pass
            
        # Phase 2: Playwright Fallback (HTTP/2 Disabled)
        self._init_playwright()
        try:
            logger.info("Physical Browser Download Triggered (HTTP/2 Bypassed)...")
            resp = self.page.request.get(url, headers={"Referer": "https://www.nseindia.com/", "Accept": "*/*"}, timeout=60000)
            body = resp.body()
            if body.startswith(b'PK'): 
                return body
            else:
                logger.error("Playwright downloaded file, but it is not a valid ZIP.")
        except Exception as e: logger.error(f"Playwright ZIP fetch failed: {e}")
        
        logger.error(f"Ultimate binary download failed for: {url}")
        return None

    def get_json(self, url: str, params: dict = None, timeout: int = 25) -> Optional[dict]:
        headers = {"Referer": "https://www.bseindia.com/", "Accept": "application/json"}
        
        # Phase 1: Standard Requests
        try:
            resp = self.session.get(url, params=params, headers=headers, timeout=timeout)
            if resp.status_code == 200:
                data = resp.json()
                if data: return data
        except Exception: pass
            
        # Phase 2: Playwright DOM-Level Fetch
        self._init_playwright()
        if params:
            qs = "&".join([f"{k}={v}" for k, v in params.items()])
            url = f"{url}?{qs}"
            
        try:
            logger.info("Executing DOM-level JSON fetch to bypass Cloudflare HTML block...")
            # We execute the Javascript INSIDE the already-verified browser tab. WAF cannot block this.
            json_data = self.page.evaluate(f"""async () => {{
                const res = await fetch('{url}', {{ headers: {{ 'Accept': 'application/json' }} }});
                return await res.json();
            }}""")
            
            # Verify we didn't just parse an empty WAF string
            if json_data: 
                return json_data
            else:
                logger.error("Playwright DOM fetch succeeded, but returned empty JSON.")
        except Exception as e: 
            logger.error(f"Playwright DOM fetch failed: {e}")
        
        return None
        
    def close(self):
        if self.pw_context:
            self.page.close()
            self.pw_context.close()
            self.browser.close()
            self.pw.stop()

# --- TELEGRAM AND HUNTER HELPERS ---
def send_telegram_alert(message: str):
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id: return
    try:
        requests.post(f"https://api.telegram.org/bot{token}/sendMessage", json={"chat_id": chat_id, "text": message, "parse_mode": "Markdown"})
    except Exception as e:
        logger.error(f"Failed to send Telegram alert: {e}")

def is_valid_file(filepath: str) -> bool:
    """Checks if file exists AND has content (not empty)."""
    return os.path.exists(filepath) and os.path.getsize(filepath) > 50

def to_md_table(data_list: List[Dict[str, Any]], custom_headers: Optional[List[str]] = None) -> str:
    if not data_list: return "*No data available.*\n"
    headers = custom_headers if custom_headers else list(data_list[0].keys())
    md = ["| " + " | ".join(headers) + " |", "| " + " | ".join(["---"] * len(headers)) + " |"]
    for row in data_list: 
        md.append("| " + " | ".join([str(row.get(h, '')).replace('|', '\\|').strip() for h in headers]) + " |")
    return "\n".join(md) + "\n"

# --- CORE MODULES ---
def get_nifty_total_market(fetcher: OmniFetcher) -> List[str]:
    if is_valid_file("active_watchlist.json"):
        with open("active_watchlist.json", "r") as f:
            data = json.load(f)
            if len(data) > 500:
                logger.info(f"Watchlist already exists with {len(data)} stocks. Skipping fetch.")
                return data

    logger.info("Fetching live market index constituents...")
    tickers = set()

    def fetch_index(index_name):
        for suffix in ["list.csv", "_list.csv"]:
            text = fetcher.get_text(f"https://www.niftyindices.com/IndexConstituent/ind_{index_name}{suffix}")
            if text and "Symbol" in text: return text
        return None

    tm_text = fetch_index("niftytotalmarket")
    if tm_text:
        for r in csv.DictReader(tm_text.strip().split('\n')):
            sym = r.get('Symbol') or r.get('SYMBOL')
            if sym: tickers.add(sym.strip().upper())

    if len(tickers) < 700:
        logger.info(f"Primary fetch got {len(tickers)}. Falling back to multi-index assembly...")
        for idx in ["nifty500", "niftymicrocap250", "niftysmallcap250"]:
            fallback = fetch_index(idx)
            if fallback:
                for r in csv.DictReader(fallback.strip().split('\n')):
                    sym = r.get('Symbol') or r.get('SYMBOL')
                    if sym: tickers.add(sym.strip().upper())

    final_list = list(tickers)
    if len(final_list) > 200:
        with open("active_watchlist.json", "w") as f: json.dump(final_list, f)
    return final_list

def process_market_action(cfg: MarketPipelineConfig, fetcher: OmniFetcher) -> str:
    target = f"{cfg.base_market_dir}/{cfg.date_iso}/cash_market.md"
    if is_valid_file(target): return "✅ Skipped (Already Secure)"

    prices, indices = [], []
    text = fetcher.get_text(f"https://nsearchives.nseindia.com/products/content/sec_bhavdata_full_{cfg.date_ddmmyyyy}.csv")
    if text:
        for r in csv.DictReader(text.strip().split('\n')):
            clean = {k.strip(): v.strip() for k, v in r.items() if k}
            if clean.get('SERIES') in ['EQ', 'SM']:
                prices.append({
                    "Ticker": clean.get('SYMBOL'), "Open": clean.get('OPEN_PRICE'), "High": clean.get('HIGH_PRICE'), 
                    "Low": clean.get('LOW_PRICE'), "Close": clean.get('CLOSE_PRICE'), 
                    "Volume": clean.get('TTL_TRD_QNTY') or clean.get('TOT_TRD_QTY', 'N/A'),
                    "Delivery_Qty": clean.get('DELIV_QTY', 'N/A'), "Delivery_Pct": clean.get('DELIV_PER', 'N/A')
                })
    
    if not prices:
        content = fetcher.get_content(f"https://nsearchives.nseindia.com/content/historical/EQUITIES/{cfg.date_yyyy}/{cfg.date_mmm}/cm{cfg.date_ddmmmyyyy}bhav.csv.zip")
        if content:
            with zipfile.ZipFile(io.BytesIO(content)) as z:
                for filename in z.namelist():
                    with z.open(filename) as f:
                        for r in csv.DictReader(io.TextIOWrapper(f, encoding='utf-8')):
                            if r.get('SERIES') in ['EQ', 'SM']:
                                prices.append({
                                    "Ticker": r.get('SYMBOL'), "Open": r.get('OPEN'), "High": r.get('HIGH'), "Low": r.get('LOW'), 
                                    "Close": r.get('CLOSE'), "Volume": r.get('TOTTRDQTY', 'N/A'), "Delivery_Qty": "N/A", "Delivery_Pct": "N/A"
                                })

    idx_text = fetcher.get_text(f"https://nsearchives.nseindia.com/content/indices/ind_close_all_{cfg.date_ddmmyyyy}.csv")
    if idx_text:
        for r in csv.DictReader(idx_text.strip().split('\n')):
            if r.get('Index Name', '').strip() in ['Nifty 50', 'Nifty 500', 'Nifty Midcap 150', 'Nifty Smallcap 250']:
                indices.append({"Index": r.get('Index Name', '').strip(), "Open": r.get('Open Index Value'), "High": r.get('High Index Value'), "Low": r.get('Low Index Value'), "Close": r.get('Closing Index Value')})

    if prices:
        with open(target, "w", encoding="utf-8") as f: 
            f.write(f"# Cash Market Analysis ({cfg.date_iso})\n\n## Broad Indices\n{to_md_table(indices)}\n## Equity Pricing\n{to_md_table(prices)}")
        return f"✅ Downloaded ({len(prices)} equities)"
    return "❌ Failed (Timeout)"

def process_derivatives(cfg: MarketPipelineConfig, fetcher: OmniFetcher) -> str:
    target_fno = f"{cfg.base_market_dir}/{cfg.date_iso}/derivatives.md"
    target_opt = f"{cfg.base_market_dir}/{cfg.date_iso}/index_options.md"
    
    if is_valid_file(target_fno) and is_valid_file(target_opt):
        return "✅ Skipped (Already Secure)"

    fno, oi, ban = [], [], []
    options_data = {"NIFTY": {"CE": 0, "PE": 0}, "BANKNIFTY": {"CE": 0, "PE": 0}, "FINNIFTY": {"CE": 0, "PE": 0}}
    
    content = fetcher.get_content(f"https://nsearchives.nseindia.com/content/historical/DERIVATIVES/{cfg.date_yyyy}/{cfg.date_mmm}/fo{cfg.date_ddmmmyyyy}bhav.csv.zip")
    if content:
        with zipfile.ZipFile(io.BytesIO(content)) as z:
            for file_name in z.namelist():
                with z.open(file_name) as zf:
                    for r in csv.DictReader(io.TextIOWrapper(zf, encoding='utf-8')):
                        inst, sym = r.get('INSTRUMENT'), r.get('SYMBOL')
                        try:
                            oi_val = r.get('OPEN_INT', '0')
                            open_int = int(float(oi_val)) if oi_val.strip() else 0
                        except ValueError: open_int = 0
                        
                        if inst in ['FUTSTK', 'FUTIDX']: 
                            fno.append({"Contract": sym, "Expiry": r.get('EXPIRY_DT'), "Close": r.get('CLOSE'), "OI": open_int})
                        if inst == 'OPTIDX' and sym in options_data:
                            opt_typ = r.get('OPTION_TYP')
                            if opt_typ in ['CE', 'PE']: options_data[sym][opt_typ] += open_int
    
    text_oi = fetcher.get_text(f"https://nsearchives.nseindia.com/content/nsccl/fao_participant_oi_{cfg.date_ddmmyyyy}.csv")
    if text_oi:
        for r in csv.DictReader(text_oi.strip().split('\n')): 
            if r.get('Client Type'): oi.append({"Client": r.get('Client Type'), "Future_Long": r.get('Future Index Long'), "Future_Short": r.get('Future Index Short')})

    text_ban = fetcher.get_text("https://nsearchives.nseindia.com/content/fo/fo_secban.csv")
    if text_ban:
        for line in text_ban.strip().split('\n')[1:]:
            if ',' in line and line.strip(): ban.append({"Symbol": line.split(',')[1].strip()})

    if fno:
        with open(target_fno, "w", encoding="utf-8") as f: 
            f.write(f"# Derivatives Profile\n\n## Exchange Ban List\n{to_md_table(ban)}\n## Participant OI Flow\n{to_md_table(oi)}\n## Futures Open Interest\n{to_md_table(fno[:500])}")
        
        final_pcr = [{"Index": idx, "Call_OI": d["CE"], "Put_OI": d["PE"], "PCR": round(d["PE"]/d["CE"], 3) if d["CE"]>0 else 0} for idx, d in options_data.items() if d["CE"]>0 or d["PE"]>0]
        if final_pcr:
            with open(target_opt, "w", encoding="utf-8") as f: f.write(f"# Major Indices Options Chain\n\n{to_md_table(final_pcr)}")
        return f"✅ Downloaded ({len(fno)} FNO records)"
    return "❌ Failed (Timeout)"

def process_macro_flows(cfg: MarketPipelineConfig, fetcher: OmniFetcher) -> str:
    target = f"{cfg.base_market_dir}/{cfg.date_iso}/macro_flows.md"
    if is_valid_file(target): return "✅ Skipped (Already Secure)"
    
    fii, deals = [], []
    try:
        fetcher.session.get("https://www.nseindia.com", timeout=15)
        text_fii = fetcher.get_text("https://www.nseindia.com/api/fiidiiTradeReact")
        if text_fii:
            for i in json.loads(text_fii): fii.append({"Category": i.get('category'), "Net_Value": i.get('netValue')})
    except: pass
        
    for url, t in [("https://nsearchives.nseindia.com/content/equities/bulk.csv", "BULK"), ("https://nsearchives.nseindia.com/content/equities/block.csv", "BLOCK")]:
        text = fetcher.get_text(url)
        if text:
            for r in csv.DictReader(text.strip().split('\n')): 
                if r.get('Symbol'): deals.append({"Type": t, "Symbol": r.get('Symbol'), "Client": r.get('Client Name'), "Txn": r.get('Buy/Sell')})
            
    if fii or deals:
        with open(target, "w", encoding="utf-8") as f: 
            f.write(f"# Institutional Flows\n\n## FII/DII Net\n{to_md_table(fii)}\n## Dark Pool Deals (Bulk/Block)\n{to_md_table(deals)}")
        return f"✅ Downloaded ({len(deals)} deals)"
    return "❌ Failed (Timeout)"

def process_regulatory(cfg: MarketPipelineConfig, fetcher: OmniFetcher) -> str:
    t_pit = f"{cfg.base_market_dir}/{cfg.date_iso}/insider_trading.md"
    t_sast = f"{cfg.base_market_dir}/{cfg.date_iso}/promoter_pledges.md"
    if is_valid_file(t_pit) and is_valid_file(t_sast): return "✅ Skipped (Already Secure)"

    pit_data, sast_data = [], []
    fetcher.prime_bse_cookies()
    params = {"pageno": 1, "strCat": "-1", "strPrevDate": cfg.date_yyyymmdd, "strScrip": "", "strSearch": "", "strToDate": cfg.date_yyyymmdd}
    
    pit_json = fetcher.get_json("https://api.bseindia.com/BseIndiaAPI/api/InsiderTrading/w", params=params)
    if pit_json:
        for i in pit_json.get('Table', []):
            sym = i.get('SLONGNAME') or i.get('COMPANY_NAME')
            if sym: pit_data.append({"Symbol": sym.strip(), "Acquirer": i.get('ACQUIRER_NAME', 'Unknown'), "Category": i.get('CATEGORY_OF_PERSON', 'Unknown'), "Action": i.get('ACQUISITION_DISPOSAL_TRANSACTION_TYPE', 'Unknown'), "Qty": i.get('NO_OF_SECURITIES', 0)})

    sast_json = fetcher.get_json("https://api.bseindia.com/BseIndiaAPI/api/SastData/w", params=params)
    if sast_json:
        for i in sast_json.get('Table', []):
            sym = i.get('COMPANY_NAME') or i.get('SLONGNAME')
            if sym: sast_data.append({"Symbol": sym.strip(), "Promoter": i.get('PROMOTER_NAME', 'Unknown'), "Event": i.get('EVENT_TYPE', 'Unknown'), "Shares": i.get('NO_OF_SHARES', 0), "Percent": i.get('PERCENTAGE', 0)})

    if pit_data or sast_data:
        if pit_data:
            with open(t_pit, "w", encoding="utf-8") as f: f.write(f"# Insider Trading (PIT)\n\n{to_md_table(pit_data)}")
        if sast_data:
            with open(t_sast, "w", encoding="utf-8") as f: f.write(f"# Promoter Pledges (SAST)\n\n{to_md_table(sast_data)}")
        return f"✅ Downloaded (PIT: {len(pit_data)}, SAST: {len(sast_data)})"
    return "❌ Failed (BSE No Data)"

def process_corporate(cfg: MarketPipelineConfig, fetcher: OmniFetcher) -> str:
    flag_file = f"{cfg.base_market_dir}/{cfg.date_iso}/.corp_done"
    if os.path.exists(flag_file): return "✅ Skipped (Already Secure)"

    fetcher.prime_bse_cookies()
    params = {"pageno": 1, "strCat": "-1", "strPrevDate": cfg.date_yyyymmdd, "strScrip": "", "strSearch": "", "strToDate": cfg.date_yyyymmdd, "strType": "C"}
    data = fetcher.get_json("https://api.bseindia.com/BseIndiaAPI/api/AnnSubCategoryGetData/w", params=params)
    if not data: return "❌ Failed (BSE Issue)"
        
    hits = 0
    for item in data.get('Table', []):
        headline, cat = item.get('NEWSSUB', '').strip(), item.get('CATEGORYNAME', '').lower()
        company = item.get('SLONGNAME', 'UNKNOWN').strip().replace(" ", "_").replace("/", "-")
        attach = item.get('ATTACHMENTNAME')
        pdf = f"https://www.bseindia.com/xml-data/corpfiling/AttachLive/{attach}" if attach else "No PDF"
        
        is_mat = any(w in headline.lower() for w in ["resignation", "appointment", "acquisition", "merger", "dividend", "financial result", "earnings", "fraud", "default", "auditor", "strike", "lockout", "penalty", "subpoena", "bankruptcy", "pledge"])
        if any(b in headline.lower() for b in ["loss of share", "duplicate share", "trading window closure"]) and not is_mat: continue 

        td = f"{cfg.base_corp_dir}/{company}/" + ("concalls" if "transcript" in headline.lower() or "concall" in headline.lower() else "earnings" if "result" in cat else "filings")
        os.makedirs(td, exist_ok=True)
        
        fp = f"{td}/{cfg.date_iso}_{item.get('NEWSID')}.md"
        if not os.path.exists(fp):
            with open(fp, "w", encoding="utf-8") as f: f.write(f"# {headline}\n\n**Category:** {item.get('CATEGORYNAME')}\n**PDF Source:** {pdf}\n\n{item.get('HEADLINE', '')}")
            hits += 1
            
    with open(flag_file, "w") as f: f.write("done")
    return f"✅ Downloaded ({hits} material events)"

def main():
    logger.info("--- OMNI-FETCHER PERSISTENT HUNTER ACTIVATED ---")
    cfg = MarketPipelineConfig()
    fetcher = OmniFetcher()
    
    try:
        watchlist = get_nifty_total_market(fetcher)
        wl_status = f"✅ Watchlist ({len(watchlist)} stocks)"
    except Exception as e:
        wl_status = f"❌ Watchlist Failed: {e}"
        
    status = {"Cash Market": "Wait", "Derivatives": "Wait", "Macro Flows": "Wait", "Regulatory Data": "Wait", "Corporate Events": "Wait"}
    
    status["Cash Market"] = process_market_action(cfg, fetcher)
    status["Derivatives"] = process_derivatives(cfg, fetcher)
    status["Macro Flows"] = process_macro_flows(cfg, fetcher)
    status["Regulatory Data"] = process_regulatory(cfg, fetcher)
    status["Corporate Events"] = process_corporate(cfg, fetcher)
    
    fetcher.close()
    
    report = f"📊 *Market Hunter Report: {cfg.date_iso}*\n\n"
    report += f"📋 Watchlist: {wl_status}\n"
    for k, v in status.items():
        report += f"{'✅' if '✅' in v else '❌'} {k}: {v.replace('✅ ', '').replace('❌ ', '')}\n"
        
    if any("❌ Failed" in v for v in status.values()):
        report += "\n⚠️ *Status:* Some modules failed. Pipeline will hunt again next hour."
    else:
        report += "\n🎯 *Status:* ALL DATA SECURED. Ready for AI Analysis."
        
    send_telegram_alert(report)
    logger.info("--- HUNTER CYCLE COMPLETE ---")

if __name__ == "__main__":
    main()

