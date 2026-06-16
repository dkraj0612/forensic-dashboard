import os
import re
import sys
import time
from urllib.parse import urljoin
from bs4 import BeautifulSoup

try:
    from curl_cffi import requests as tls_requests
except ImportError:
    print("[CRITICAL] Missing libraries. Run: pip install curl_cffi beautifulsoup4")
    sys.exit(1)

# ==========================================
#               CONFIGURATION
# ==========================================
# Add the exact Screener tickers you want to track here
WATCHLIST = [
    "RELIANCE", 
    "TCS", 
    "INFY", 
    "HDFCBANK",
    "LAXMIDENTL"
]

OUTPUT_DIR = "market_pulse_data"
# ==========================================

STOCKS_DIR = os.path.join(OUTPUT_DIR, "Stocks")
session = tls_requests.Session(impersonate="chrome124")

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Referer': 'https://www.screener.in/'
}

def sanitize_filename(text: str) -> str:
    clean = re.sub(r'[\\/*?:"<>|\'’]', "", text)
    return clean.replace(" ", "_").strip()[:100]

def extract_stock_data(ticker):
    print(f"\n>>> Scanning: {ticker}")
    url = f"https://www.screener.in/company/{ticker}/"
    
    try:
        resp = session.get(url, headers=HEADERS, timeout=15)
        if resp.status_code == 404:
            print(f"      [-] Ticker '{ticker}' not found on Screener.")
            return
        elif resp.status_code != 200:
            print(f"      [!] Server returned status {resp.status_code}.")
            return
    except Exception as e:
        print(f"      [!] Network error: {e}")
        return

    soup = BeautifulSoup(resp.text, 'html.parser')
    
    # We hunt for anything that looks like a document link
    valid_keywords = ['transcript', 'audio', 'presentation', 'report', 'notes', 'announcement']
    doc_count = 0
    
    save_dir = os.path.join(STOCKS_DIR, ticker, "Documents")
    os.makedirs(save_dir, exist_ok=True)

    for a in soup.find_all('a'):
        href = a.get('href', '')
        link_text = a.get_text(strip=True).lower()
        
        # Check if the link contains a document keyword OR is a PDF
        if not any(word in link_text for word in valid_keywords) and '.pdf' not in href.lower():
            continue
            
        # Clean up the name and format it
        clean_type = sanitize_filename(a.get_text(strip=True))
        if not clean_type:
            continue
            
        ext = ".mp3" if "audio" in link_text else ".pdf"
        filename = f"{ticker}_{clean_type}{ext}"
        save_path = os.path.join(save_dir, filename)
        
        # The Smart Filter: Only download if we don't already have it!
        if not os.path.exists(save_path):
            full_url = urljoin("https://www.screener.in", href)
            try:
                # We don't download random webpage links, only actual files
                if '.pdf' in full_url.lower() or 'concalls' in full_url.lower():
                    file_resp = session.get(full_url, headers=HEADERS, stream=True, timeout=30)
                    
                    if file_resp.status_code == 200:
                        with open(save_path, 'wb') as f:
                            for chunk in file_resp.iter_content(chunk_size=8192):
                                if chunk: f.write(chunk)
                        print(f"      [+] NEW FILE: Downloaded -> {filename}")
                        doc_count += 1
                        time.sleep(1) # Polite delay so we don't get banned
            except Exception as e:
                print(f"      [!] Failed to download {filename}: {e}")
                
    if doc_count == 0:
        print("      [-] No new documents today.")

def main():
    print(f"\n{'='*50}")
    print("=== WATCHLIST DOCUMENT SCANNER ===")
    print(f"{'='*50}")
    
    for ticker in WATCHLIST:
        extract_stock_data(ticker.upper())
        time.sleep(2) # Mandatory delay between stocks to mimic human browsing
        
    print("\n=== SCAN COMPLETE ===")

if __name__ == "__main__":
    main()
