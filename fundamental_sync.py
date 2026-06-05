
import os
import json
import logging
import sys
import time
from datetime import datetime
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# Configure Logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s | %(levelname)-8s | %(message)s', handlers=[logging.StreamHandler(sys.stdout)])
logger = logging.getLogger(__name__)

def sync_fundamentals():
    logger.info("--- STARTING WEEKLY FUNDAMENTAL SYNC ---")
    
    if not os.path.exists("active_watchlist.json"):
        logger.error("No active_watchlist.json found. Please ensure data_ingestion.py runs first.")
        return

    with open("active_watchlist.json", "r") as f:
        watchlist = json.load(f)

    session = requests.Session()
    retry = Retry(
        total=3, 
        backoff_factor=1.5, 
        status_forcelist=[429, 500, 502, 503, 504], 
        raise_on_status=False
    )
    adapter = HTTPAdapter(max_retries=retry, pool_connections=25, pool_maxsize=25)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}

    today_str = datetime.today().strftime('%Y-%m-%d')
    success_count = 0

    for ticker in watchlist:
        try:
            url = f"https://query2.finance.yahoo.com/v10/finance/quoteSummary/{ticker}.NS?modules=summaryDetail,financialData"
            res = session.get(url, headers=headers, timeout=10)
            
            # Default empty template
            fund_data = {
                "sync_date": today_str,
                "pe": "N/A", "forward_pe": "N/A", "roe": "N/A", 
                "de": "N/A", "margins": "N/A", "cash": "N/A"
            }
            
            if res.status_code == 200:
                data = res.json().get('quoteSummary', {}).get('result', [{}])[0]
                summary = data.get('summaryDetail', {})
                financials = data.get('financialData', {})
                
                fund_data["pe"] = summary.get('trailingPE', {}).get('fmt', 'N/A')
                fund_data["forward_pe"] = summary.get('forwardPE', {}).get('fmt', 'N/A')
                fund_data["margins"] = financials.get('profitMargins', {}).get('fmt', 'N/A')
                fund_data["roe"] = financials.get('returnOnEquity', {}).get('fmt', 'N/A')
                fund_data["de"] = financials.get('debtToEquity', {}).get('fmt', 'N/A')
                fund_data["cash"] = financials.get('totalCash', {}).get('fmt', 'N/A')

            # Ensure directory exists
            path = f"corporate_data/{ticker}"
            os.makedirs(path, exist_ok=True)
            
            # Write to disk securely
            with open(f"{path}/fundamentals.json", "w") as f:
                json.dump(fund_data, f, indent=4)
            
            success_count += 1
            
            # Anti-spam delay to prevent Yahoo from IP banning the GitHub runner
            time.sleep(0.5)

        except Exception as e:
            logger.warning(f"Failed to sync fundamentals for {ticker}: {e}")

    logger.info(f"--- WEEKLY SYNC COMPLETE: {success_count}/{len(watchlist)} stocks updated ---")

if __name__ == "__main__":
    sync_fundamentals()


