import os
import re
import sys
import time
from datetime import datetime
from urllib.parse import urljoin
from bs4 import BeautifulSoup
from io import StringIO

try:
    from curl_cffi import requests as tls_requests
    import pandas as pd
except ImportError:
    print("[CRITICAL] Missing libraries. Run: pip install curl_cffi pandas lxml html5lib beautifulsoup4")
    sys.exit(1)

# ==========================================
#               CONFIGURATION
# ==========================================
USERNAME = os.getenv("SCREENER_USERNAME")
PASSWORD = os.getenv("SCREENER_PASSWORD")
OUTPUT_DIR = "market_pulse_data"
# ==========================================

TODAY = datetime.today().strftime('%Y-%m-%d')
STOCKS_DIR = os.path.join(OUTPUT_DIR, "Stocks")
GLOBAL_DIR = os.path.join(OUTPUT_DIR, "Global_Data")

# We map the exact text from your screenshot to the type of data we want to extract.
# Announcements and Industries Overview are INTENTIONALLY excluded here.
TARGET_CATEGORIES = {
    "Concalls": "doc",
    "Upcoming Concalls": "table",
    "Annual Reports": "doc",
    "FII Investment": "table",
    "Upcoming Results": "table",
    "Bulk Deals": "table",
    "Block Deals": "table",
    "SAST Trades": "table",
    "Insider Trades": "table",
    "Bonus": "table",
    "Right": "table",
    "Split": "table",
    "Buy Back": "table",
    "Dividend": "table"
}

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
    'Accept-Language': 'en-US,en;q=0.9',
}

session = tls_requests.Session(impersonate="chrome124")

def authenticate_session():
    """Logs into Screener natively via the cloud runner environment."""
    print("[*] Initiating Native Cloud Authentication...")
    login_url = "https://www.screener.in/login/"
    
    try:
        get_resp = session.get(login_url, headers=HEADERS, timeout=15)
        soup = BeautifulSoup(get_resp.text, 'html.parser')
        csrf_tag = soup.find('input', {'name': 'csrfmiddlewaretoken'})
        
        if not csrf_tag:
            print("      [!] Could not locate CSRF token on login page.")
            return False
            
        payload = {
            'csrfmiddlewaretoken': csrf_tag.get('value'),
            'username': USERNAME,
            'password': PASSWORD,
            'next': '/market-pulse/'
        }
        
        login_headers = HEADERS.copy()
        login_headers['Referer'] = login_url
        login_headers['Origin'] = "https://www.screener.in"
        
        post_resp = session.post(login_url, data=payload, headers=login_headers, timeout=15)
        
        if "login" not in post_resp.url.lower():
            print("      [OK] Successfully authenticated credentials.")
            return True
        else:
            print("      [!] Authentication rejected. Check password/username secrets.")
            return False
    except Exception as e:
        print(f"      [!] Login execution failed: {e}")
        return False

def map_dashboard_links():
    """Visits the Market Pulse dashboard and dynamically extracts the actual live URLs."""
    print("\n[*] Mapping live category endpoints from Dashboard...")
    dashboard_url = "https://www.screener.in/market-pulse/"
    live_endpoints = {}
    
    try:
        resp = session.get(dashboard_url, headers=HEADERS, timeout=15)
        soup = BeautifulSoup(resp.text, 'html.parser')
        
        for a in soup.find_all('a'):
            text = a.get_text(separator=" ", strip=True).strip()
            
            for cat_name, data_type in TARGET_CATEGORIES.items():
                # If the exact dashboard link text matches our target list, grab the hidden href
                if cat_name.lower() in text.lower() and a.get('href'):
                    # Prevent overwriting if already mapped
                    if cat_name not in live_endpoints:
                        live_endpoints[cat_name] = {
                            "url": urljoin(dashboard_url, a.get('href')),
                            "type": data_type
                        }
                        
        print(f"      [OK] Mapped {len(live_endpoints)} out of {len(TARGET_CATEGORIES)} target categories.")
        return live_endpoints
    except Exception as e:
        print(f"      [!] Failed to map dashboard: {e}")
        return {}

def sanitize_filename(text: str) -> str:
    clean = re.sub(r'[\\/*?:"<>|\'’]', "", text)
    return clean.replace(" ", "_").strip()[:100]

def extract_tables(category_name, resp):
    soup = BeautifulSoup(resp.text, 'html.parser')
    html_tables = soup.find_all('table')
    
    try:
        html_stream = StringIO(resp.text)
        pandas_tables = pd.read_html(html_stream)
    except ValueError:
        print(f"      [-] No structural data tables found on page.")
        return

    for idx, df in enumerate(pandas_tables):
        if df.empty or idx >= len(html_tables):
            continue
            
        html_table = html_tables[idx]
        html_rows = html_table.find_all('tr')
        html_rows = [r for r in html_rows if not r.find('th')] if html_table.find('th') else html_rows[1:]
            
        symbols = []
        for tr in html_rows:
            symbol = "GLOBAL"
            comp_link = tr.find('a', href=re.compile(r'/company/'))
            if comp_link:
                match = re.search(r'/company/([^/]+)/', comp_link.get('href'))
                if match:
                    symbol = match.group(1).upper()
            symbols.append(symbol)
            
        if len(symbols) != len(df):
            while len(symbols) < len(df):
                symbols.append("GLOBAL")
            symbols = symbols[:len(df)]
            
        df['Ticker_Symbol'] = symbols
        df['Extraction_Date'] = TODAY
        
        for symbol, group in df.groupby('Ticker_Symbol'):
            clean_group = group.drop(columns=['Ticker_Symbol'])
            safe_cat_name = sanitize_filename(category_name)
            
            if symbol == "GLOBAL":
                csv_path = os.path.join(GLOBAL_DIR, f"{safe_cat_name}_{TODAY}.csv")
                clean_group.to_csv(csv_path, index=False, encoding='utf-8-sig')
                print(f"      [OK] Logged Global Data -> Global_Data/{safe_cat_name}_{TODAY}.csv")
            else:
                stock_table_dir = os.path.join(STOCKS_DIR, symbol, "Tables")
                os.makedirs(stock_table_dir, exist_ok=True)
                csv_path = os.path.join(stock_table_dir, f"{safe_cat_name}.csv")
                
                if os.path.exists(csv_path):
                    try:
                        existing_df = pd.read_csv(csv_path)
                        combined_df = pd.concat([existing_df, clean_group], ignore_index=True).drop_duplicates()
                        combined_df.to_csv(csv_path, index=False, encoding='utf-8-sig')
                    except Exception:
                        clean_group.to_csv(csv_path, index=False, encoding='utf-8-sig')
                else:
                    clean_group.to_csv(csv_path, index=False, encoding='utf-8-sig')
                print(f"      [OK] Updated Stock Row -> Stocks/{symbol}/Tables/{safe_cat_name}.csv")

def extract_documents(resp):
    soup = BeautifulSoup(resp.text, 'html.parser')
    valid_doc_keywords = ['transcript', 'audio', 'presentation', 'summary', 'report', 'notes']
    doc_count = 0
    
    for a in soup.find_all('a'):
        href = a.get('href', '')
        link_text = a.get_text(strip=True).lower()
        if not any(word in link_text for word in valid_doc_keywords) and '.pdf' not in href.lower():
            continue
            
        parent_row = a.find_parent(['li', 'tr'])
        symbol = "GLOBAL"
        if parent_row:
            comp_link = parent_row.find('a', href=re.compile(r'/company/'))
            if comp_link:
                match = re.search(r'/company/([^/]+)/', comp_link.get('href'))
                if match:
                    symbol = match.group(1).upper()
                    
        clean_type = sanitize_filename(a.get_text(strip=True))
        ext = ".mp3" if "audio" in link_text else ".pdf"
        filename = f"{symbol}_{TODAY}_{clean_type}{ext}"
        
        save_dir = os.path.join(GLOBAL_DIR, "Documents") if symbol == "GLOBAL" else os.path.join(STOCKS_DIR, symbol, "Documents")
        os.makedirs(save_dir, exist_ok=True)
        save_path = os.path.join(save_dir, filename)
        
        if not os.path.exists(save_path):
            full_url = urljoin("https://www.screener.in", href)
            try:
                file_resp = session.get(full_url, headers=HEADERS, stream=True, timeout=30)
                if file_resp.status_code == 200:
                    with open(save_path, 'wb') as f:
                        for chunk in file_resp.iter_content(chunk_size=8192):
                            if chunk: f.write(chunk)
                    print(f"      [OK] Downloaded Document -> Stocks/{symbol}/Documents/{filename}")
                    doc_count += 1
                    time.sleep(1.5)
            except Exception as e:
                print(f"      [!] Failed to download {filename}: {e}")
                
    if doc_count == 0:
        print("      [-] No new documents found.")

def main():
    if not USERNAME or not PASSWORD:
        print("[CRITICAL] SCREENER_USERNAME and/or SCREENER_PASSWORD missing from GitHub environment.")
        sys.exit(1)

    os.makedirs(STOCKS_DIR, exist_ok=True)
    os.makedirs(GLOBAL_DIR, exist_ok=True)
    
    print(f"\n{'='*50}")
    print("=== LIVE NATIVE CREDENTIALS RUN ===")
    print(f"{'='*50}")
    
    if not authenticate_session():
        print("[CRITICAL] Aborting extraction due to authentication failure.")
        sys.exit(1)
        
    # Step 1: Map the actual, real URLs from the dashboard
    live_endpoints = map_dashboard_links()
    
    if not live_endpoints:
        print("[CRITICAL] Could not map any endpoints. Exiting.")
        sys.exit(1)
    
    # Step 2: Loop through the successfully mapped URLs
    for category_name, payload in live_endpoints.items():
        print(f"\n>>> Processing Category Endpoint: {category_name}")
        url = payload["url"]
        data_type = payload["type"]
        
        try:
            resp = session.get(url, headers=HEADERS, timeout=15)
            
            # Diagnostic to ensure we aren't getting 404s anymore
            print(f"      [DIAGNOSTIC] Status: {resp.status_code} | Landed URL: {resp.url}")
            
            if resp.status_code != 200 or "login" in resp.url.lower():
                print(f"      [!] Verification failure on {category_name}.")
                continue
                
            if data_type == "table":
                extract_tables(category_name, resp)
            elif data_type == "doc":
                extract_documents(resp)
                
        except Exception as e:
            print(f"      [!] Socket transport error: {e}")
            
        time.sleep(2.5)
        
    print("\n=== SYSTEM PROCESSING COMPLETE ===")

if __name__ == "__main__":
    main()
