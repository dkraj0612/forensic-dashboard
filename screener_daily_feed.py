import os
import re
import sys
import time
from datetime import datetime
from urllib.parse import urljoin
from bs4 import BeautifulSoup

try:
    from curl_cffi import requests as tls_requests
    import pandas as pd
except ImportError:
    print("[CRITICAL] Missing libraries. Run: pip install curl_cffi pandas lxml html5lib beautifulsoup4")
    sys.exit(1)

# ==========================================
#               CONFIGURATION
# ==========================================
# 1. Paste the exact URL of the dashboard page from your screenshot:
DASHBOARD_URL = "https://www.screener.in/market-pulse/"  # <--- UPDATE THIS TO THE REAL URL

# 2. You MUST paste your 'sessionid' cookie here so the script can log in.
SESSION_ID_COOKIE = "1f4z98mwvz8ekft5cr5czpdsxoba9n7i"

# 3. Output Location
OUTPUT_DIR = r"C:\Users\Test\Downloads\corporate_data\market_pulse_daily"
# ==========================================

TODAY = datetime.today().strftime('%Y-%m-%d')
TABLES_DIR = os.path.join(OUTPUT_DIR, "Data_Tables")
DOCS_DIR = os.path.join(OUTPUT_DIR, "Documents")

TARGET_CATEGORIES = [
    "Announcements", "Industries Overview", "Concalls", "Upcoming Concalls", 
    "Annual Reports", "FII Investment", "Upcoming Results", "Bulk Deals", 
    "Block Deals", "SAST Trades", "Insider Trades", "Bonus", "Right", 
    "Split", "Buy Back", "Dividend"
]

session = tls_requests.Session(impersonate="chrome124")
if SESSION_ID_COOKIE:
    session.cookies.set("sessionid", SESSION_ID_COOKIE, domain=".screener.in")

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Referer': 'https://www.screener.in/'
}

def sanitize_filename(text: str) -> str:
    clean = re.sub(r'[\\/*?:"<>|\'’]', "", text)
    return clean.replace(" ", "_").strip()[:100]

def download_pdf(url, save_path):
    try:
        resp = session.get(url, headers=HEADERS, stream=True, timeout=30)
        if resp.status_code == 200:
            with open(save_path, 'wb') as f:
                for chunk in resp.iter_content(chunk_size=8192):
                    if chunk: f.write(chunk)
            return True
    except Exception:
        pass
    return False

def process_category(category_name, url):
    print(f"\n  >>> Analyzing: {category_name} ({url})")
    try:
        resp = session.get(url, headers=HEADERS, timeout=15)
        if resp.status_code != 200 or "login" in resp.url.lower():
            print("      [!] Access Denied. Ensure your sessionid cookie is valid.")
            return
    except Exception as e:
        print(f"      [!] Network error: {e}")
        return

    soup = BeautifulSoup(resp.text, 'html.parser')
    safe_name = sanitize_filename(category_name)

    # 1. EXTRACT DATA TABLES (Bulk Deals, Dividends, etc.)
    try:
        # Pandas magically finds all HTML tables and converts them to DataFrames
        tables = pd.read_html(resp.text)
        for idx, df in enumerate(tables):
            if not df.empty:
                csv_filename = f"{safe_name}_{TODAY}_Table_{idx+1}.csv"
                csv_path = os.path.join(TABLES_DIR, csv_filename)
                
                # utf-8-sig ensures Excel reads Rupees/Special characters correctly
                df.to_csv(csv_path, index=False, encoding='utf-8-sig')
                print(f"      [OK] Extracted Data Table -> {csv_filename}")
    except ValueError:
        pass # No tables found on this specific page

    # 2. EXTRACT DOCUMENTS (Concalls, Reports) & ANNOUNCEMENTS
    doc_count = 0
    link_log = []
    
    for a in soup.find_all('a'):
        href = a.get('href', '')
        link_text = a.get_text(strip=True)
        
        if not href or not link_text:
            continue
            
        full_url = urljoin("https://www.screener.in", href)
        
        # Check for PDFs
        if '.pdf' in href.lower() or 'transcript' in link_text.lower():
            pdf_filename = f"{safe_name}_{TODAY}_{sanitize_filename(link_text)}.pdf"
            pdf_path = os.path.join(DOCS_DIR, pdf_filename)
            
            if not os.path.exists(pdf_path):
                if download_pdf(full_url, pdf_path):
                    print(f"      [OK] Downloaded Document -> {pdf_filename}")
                    doc_count += 1
                    time.sleep(1) # Polite delay
                    
        # Check for External BSE/NSE Links (Announcements)
        elif 'bseindia.com' in href or 'nseindia.com' in href:
            link_log.append({"Event Date": TODAY, "Title": link_text, "URL": full_url})
            
    # 3. SAVE EXTERNAL LINK LOG
    if link_log:
        log_df = pd.DataFrame(link_log)
        log_csv = os.path.join(TABLES_DIR, f"{safe_name}_{TODAY}_Links.csv")
        log_df.to_csv(log_csv, index=False, encoding='utf-8-sig')
        print(f"      [OK] Compiled {len(link_log)} external announcements into Log file.")

def main():
    os.makedirs(TABLES_DIR, exist_ok=True)
    os.makedirs(DOCS_DIR, exist_ok=True)
    
    print(f"\n{'='*50}")
    print("=== MARKET PULSE DYNAMIC EXTRACTOR ===")
    print(f"{'='*50}")
    
    if not SESSION_ID_COOKIE:
        print("[!] WARNING: No session cookie provided. This dashboard likely requires it.")
        
    print(f"[*] Accessing Dashboard: {DASHBOARD_URL}")
    try:
        resp = session.get(DASHBOARD_URL, headers=HEADERS, timeout=15)
        if "login" in resp.url.lower():
            print("\n[CRITICAL] Redirected to Login page. Your session cookie is missing or expired.")
            sys.exit(1)
    except Exception as e:
        print(f"[CRITICAL] Could not reach dashboard: {e}")
        sys.exit(1)
        
    soup = BeautifulSoup(resp.text, 'html.parser')
    
    # Dynamically find the category links straight from the dashboard's HTML
    category_links = {}
    for a in soup.find_all('a'):
        text = a.get_text(separator=" ", strip=True).strip()
        for target in TARGET_CATEGORIES:
            # If the link matches our target (e.g., "Bulk Deals"), grab its underlying URL
            if target.lower() in text.lower() and a.get('href'):
                category_links[target] = urljoin(DASHBOARD_URL, a.get('href'))
                
    if not category_links:
        print("[!] Could not find any target categories. Check the Dashboard URL.")
        sys.exit(1)
        
    print(f"[*] Successfully mapped {len(category_links)} categories from the dashboard. Beginning extraction...")
    
    for name, url in category_links.items():
        process_category(name, url)
        time.sleep(2) # Prevent rate-limiting
        
    print("\n=== DAILY MARKET PULSE EXTRACTION COMPLETE ===")

if __name__ == "__main__":
    main()
