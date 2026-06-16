import os
import re
import sys
import time
import random
import datetime
from urllib.parse import urljoin
from bs4 import BeautifulSoup
from io import StringIO

try:
    from curl_cffi import requests as tls_requests
    import pandas as pd
except ImportError:
    print("[CRITICAL] Missing libraries. Run: pip install curl_cffi pandas beautifulsoup4")
    sys.exit(1)

# ==========================================
#               CONFIGURATION
# ==========================================
OUTPUT_DIR = "market_pulse_data"
# ==========================================

NOW = datetime.datetime.today()
TODAY_STR = NOW.strftime('%Y-%m-%d')
STOCKS_DIR = os.path.join(OUTPUT_DIR, "Stocks")
LOG_DIR = os.path.join(OUTPUT_DIR, "System_Logs")
PROGRESS_FILE = os.path.join(LOG_DIR, f"completed_tickers_{TODAY_STR}.txt")

def generate_lookback_patterns():
    """
    Dynamically builds a list of valid date strings to scan based on the day of the week.
    Ensures zero data loss across weekends and post-market filing hours.
    """
    weekday = NOW.weekday()  # 0=Monday, 1=Tuesday, ..., 5=Saturday, 6=Sunday
    
    if weekday == 0:      # Monday: Look back across Mon, Sun, Sat, Fri (4 days)
        days_to_check = 4
    elif weekday == 6:    # Sunday: Look back across Sun, Sat, Fri (3 days)
        days_to_check = 3
    else:                 # Tue-Sat: Look back 2 days to catch late-night entries safely
        days_to_check = 2
        
    patterns = ["today", "1 day ago", "2 days ago", "3 days ago"]
    
    for i in range(days_to_check):
        target_date = NOW - datetime.timedelta(days=i)
        day = target_date.strftime('%d')
        day_strip = target_date.strftime('%e').strip()
        mon_short = target_date.strftime('%b')
        year = target_date.strftime('%Y')
        
        patterns.extend([
            f"{day} {mon_short}",
            f"{day_strip} {mon_short}",
            f"{mon_short} {day}",
            f"{mon_short} {day_strip}",
            f"{day}-{mon_short}-{year}"
        ])
    
    # Return unique patterns, lowercase for unified matching
    return list(set(p.lower() for p in patterns))

VALID_DATE_PATTERNS = generate_lookback_patterns()

# Strict Target Categories and Keyword Maps
TARGET_CATEGORIES = {
    "SAST": [r'sast', r'substantial acquisition', r'reg.*29', r'disclosure under regulation'],
    "SHP": [r'shareholding pattern', r'shp', r'shareholding statement'],
    "Insider_Trades": [r'insider', r'reg.*7', r'insider trade', r'prohibition of insider'],
    "Concalls": [r'transcript', r'audio', r'concall', r'earnings call', r'call transcript'],
    "Results": [r'financial result', r'quarterly result', r'audited result', r'unaudited result', r'results'],
    "Dividend": [r'dividend', r'interim dividend', r'final dividend', r'book closure for dividend'],
    "Bonus": [r'bonus', r'bonus issue', r'allotment of bonus']
}

session = tls_requests.Session(impersonate="chrome124")
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Referer': 'https://www.screener.in/'
}

def get_dynamic_nse_list():
    """Fetches the live master list of active symbols directly from the NSE."""
    print("\n[*] Initializing data pipeline. Pulling live NSE equity master...")
    url = "https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv"
    try:
        resp = session.get(url, headers=HEADERS, timeout=20)
        if resp.status_code == 200:
            df = pd.read_csv(StringIO(resp.text))
            tickers = df['SYMBOL'].dropna().astype(str).str.strip().unique().tolist()
            print(f"      [OK] Successfully loaded {len(tickers)} active NSE companies.")
            return tickers
        print(f"      [!] NSE server rejected request (Status {resp.status_code}).")
        return []
    except Exception as e:
        print(f"      [!] Connection to NSE failed: {e}")
        return []

def get_completed_today():
    if not os.path.exists(PROGRESS_FILE): return set()
    with open(PROGRESS_FILE, 'r') as f:
        return set(line.strip() for line in f.readlines())

def mark_completed(ticker):
    with open(PROGRESS_FILE, 'a') as f:
        f.write(f"{ticker}\n")

def sanitize_filename(text: str) -> str:
    clean = re.sub(r'[\\/*?:"<>|\'’]', "", text)
    return clean.replace(" ", "_").strip()[:100]

def is_within_temporal_window(element_text: str) -> bool:
    """Verifies if the announcement text context belongs strictly within our valid lookback window."""
    text_lower = element_text.lower()
    return any(pattern in text_lower for pattern in VALID_DATE_PATTERNS)

def identify_category(link_text: str) -> str:
    """Matches the document text against targeted categories using regex."""
    text_lower = link_text.lower()
    for category, patterns in TARGET_CATEGORIES.items():
        for pattern in patterns:
            if re.search(pattern, text_lower):
                return category
    return None

def extract_stock_data(ticker):
    """Scans public pages for matching targeted files published within the temporal window."""
    url = f"https://www.screener.in/company/{ticker}/"
    try:
        resp = session.get(url, headers=HEADERS, timeout=15)
        if resp.status_code == 404: return
        if resp.status_code != 200:
            print(f"      [!] {ticker} returned status {resp.status_code}.")
            return
    except Exception as e:
        print(f"      [!] Network issue on {ticker}: {e}")
        return

    soup = BeautifulSoup(resp.text, 'html.parser')
    downloads_found = 0

    for a in soup.find_all('a'):
        href = a.get('href', '')
        link_text = a.get_text(strip=True)
        
        if not href or not link_text:
            continue

        # Inspect the closest structural container to parse historical context metadata
        parent_container = a.find_parent(['li', 'tr', 'div'])
        context_text = parent_container.get_text(" ", strip=True) if parent_container else link_text

        # Constraint 1: Must match one of the chosen target categories
        matched_category = identify_category(link_text)
        if not matched_category:
            continue

        # Constraint 2: Must be published within our safe temporal window (catches weekends)
        if not is_within_temporal_window(context_text):
            continue

        clean_type = sanitize_filename(link_text)
        ext = ".mp3" if "audio" in link_text.lower() else ".pdf"
        filename = f"{ticker}_{matched_category}_{clean_type}{ext}"
        
        category_dir = os.path.join(STOCKS_DIR, ticker, matched_category)
        save_path = os.path.join(category_dir, filename)

        # Delta Check: Only download if the file does not exist locally
        if not os.path.exists(save_path):
            os.makedirs(category_dir, exist_ok=True)
            full_url = urljoin("https://www.screener.in", href)
            
            try:
                if '.pdf' in full_url.lower() or 'concalls' in full_url.lower() or 'announcements' in full_url.lower():
                    file_resp = session.get(full_url, headers=HEADERS, stream=True, timeout=30)
                    
                    if file_resp.status_code == 200:
                        with open(save_path, 'wb') as f:
                            for chunk in file_resp.iter_content(chunk_size=8192):
                                if chunk: f.write(chunk)
                        print(f"      [+] VALID DISCLOSURE DOWNLOADED -> {filename}")
                        downloads_found += 1
                        time.sleep(random.uniform(1.0, 2.0))
            except Exception as e:
                print(f"      [!] Download failure on {filename}: {e}")

def main():
    os.makedirs(STOCKS_DIR, exist_ok=True)
    os.makedirs(LOG_DIR, exist_ok=True)
    
    print(f"\n{'='*60}")
    print("=== TEMPORALLY ADAPTIVE ALL-NSE SWEEPER ===")
    print(f"{'='*60}")
    print(f"[*] Day of Week Detection: {NOW.strftime('%A')} (Index: {NOW.weekday()})")
    print(f"[*] Target Categories: {', '.join(TARGET_CATEGORIES.keys())}")
    print(f"[*] Active Date Matching Profiles Applied:\n    {VALID_DATE_PATTERNS[:10]}...")
    print("-" * 60)
    
    all_nse_tickers = get_dynamic_nse_list()
    if not all_nse_tickers:
        print("[CRITICAL] Aborting pipeline: Could not parse NSE master list.")
        sys.exit(1)
        
    completed_today = get_completed_today()
    remaining_tickers = [t for t in all_nse_tickers if t not in completed_today]
    
    print(f"[*] Total Tickers: {len(all_nse_tickers)} | Handled Today: {len(completed_today)}")
    print(f"[*] Remaining sweep queue: {len(remaining_tickers)}")
    print("-" * 60)
    
    for idx, ticker in enumerate(remaining_tickers, 1):
        sys.stdout.write(f"\r>>> Processing [{idx}/{len(remaining_tickers)}] Ticker: {ticker} ...")
        sys.stdout.flush()
        
        extract_stock_data(ticker)
        mark_completed(ticker)
        
        time.sleep(random.uniform(1.2, 2.8))

    print("\n\n=== REAL-TIME SWEEP CONCLUDED SUCCESSFULLY ===")

if __name__ == "__main__":
    main()
