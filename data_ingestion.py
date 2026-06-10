import os
import io
import csv
import zipfile
import logging
import sys
from datetime import datetime, timedelta
from typing import Optional

from curl_cffi import requests as tls_requests

# Configure Logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s | %(levelname)-8s | %(message)s', handlers=[logging.StreamHandler(sys.stdout)])
logger = logging.getLogger(__name__)

class MarketPipelineConfig:
    def __init__(self):
        today = datetime.today()
        self.trading_today = today - timedelta(days=1)
        self.date_iso = self.trading_today.strftime('%Y-%m-%d')
        self.date_yyyymmdd = self.trading_today.strftime('%Y%m%d') 
        self.date_mmm = self.trading_today.strftime('%b').upper()
        self.date_yyyy = self.trading_today.strftime('%Y')
        self.date_ddmmmyyyy = self.trading_today.strftime('%d%b%Y').upper()
        self.base_market_dir = f"market_data/{self.date_yyyy}/{self.date_iso}"
        os.makedirs(self.base_market_dir, exist_ok=True)

class OmniFetcher:
    def __init__(self):
        self.session = tls_requests.Session(impersonate="chrome124")
        
    def get_full_bhavcopy(self, cfg: MarketPipelineConfig) -> Optional[bytes]:
        # Prioritize the full historical Bhavcopy URL structure
        # The PR archive is only a fallback for summaries, so we de-prioritize it
        urls = [
            f"https://nsearchives.nseindia.com/content/historical/DERIVATIVES/{cfg.date_yyyy}/{cfg.date_mmm}/fo{cfg.date_ddmmmyyyy}bhav.csv.zip",
            f"https://archives.nseindia.com/content/historical/DERIVATIVES/{cfg.date_yyyy}/{cfg.date_mmm}/fo{cfg.date_ddmmmyyyy}bhav.csv.zip"
        ]
        
        for url in urls:
            try:
                resp = self.session.get(url, timeout=45)
                # Validation: If the content is tiny, it's likely an error/summary page
                if resp.status_code == 200 and len(resp.content) > 100000: # Ensure at least ~100KB
                    logger.info(f"    -> Successfully fetched full Bhavcopy: {url}")
                    return resp.content
            except: continue
        return None

def process_derivatives(cfg: MarketPipelineConfig, fetcher: OmniFetcher):
    target_fno = f"{cfg.base_market_dir}/derivatives.md"
    content = fetcher.get_full_bhavcopy(cfg)
    
    if not content:
        logger.error("    ⚠️ CRITICAL: Could not find valid full Bhavcopy. Skipping to avoid partial data.")
        return

    fno_rows = []
    with zipfile.ZipFile(io.BytesIO(content)) as z:
        for name in z.namelist():
            if 'bhav' in name.lower() and name.endswith('.csv'):
                with z.open(name) as f:
                    reader = csv.DictReader(io.TextIOWrapper(f, encoding='utf-8'))
                    for r in reader:
                        # Extracting specific columns ensuring they exist
                        inst = r.get('INSTRUMENT', '')
                        if inst:
                            fno_rows.append(r)
    
    if len(fno_rows) > 500: # Safety check: Full files usually have 10k+ rows
        with open(target_fno, "w", encoding="utf-8") as f:
            f.write(f"# Derivatives Data ({cfg.date_iso}) - {len(fno_rows)} rows\n\n")
            writer = csv.writer(f)
            writer.writerow(fno_rows[0].keys())
            for row in fno_rows: writer.writerow(row.values())
        logger.info(f"    ✅ Successfully ingested {len(fno_rows)} rows.")
    else:
        logger.warning(f"    ⚠️ File fetched but row count ({len(fno_rows)}) is suspicious. Data skipped.")

if __name__ == "__main__":
    process_derivatives(MarketPipelineConfig(), OmniFetcher())
