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
SESSION_ID_COOKIE = os.getenv("SCREENER_COOKIE")
OUTPUT_DIR = "market_pulse_data"
# ==========================================

TODAY = datetime.today().strftime('%Y-%m-%d')
STOCKS_DIR = os.path.join(OUTPUT_DIR, "Stocks")
GLOBAL_DIR = os.path.join(OUTPUT_DIR, "Global_Data")

# Advanced Mapping: Uses partial paths instead of exact URLs to guarantee a match
TARGET_CATEGORIES = {
    "Concalls": {"type": "doc", "pattern": r'/concalls/$'},
    "Upcoming_Concalls": {"type": "table", "pattern": r'/concalls/upcoming'},
    "Annual_Reports": {"type": "doc", "pattern": r'reports'}, 
    "FII_Investment": {"type": "table", "pattern": r'fii'},
    "Upcoming_Results": {"type": "table", "pattern": r'results'},
    "Bulk_Deals": {"type": "table", "pattern": r'bulk-deals'},
    "Block_Deals": {"type": "table", "pattern": r'block-deals'},
    "SAST_Trades": {"type": "table", "pattern": r'sast-trades'},
    "Insider_Trades": {"type": "table", "pattern": r'insider-trades'},
    "Bonus_Issues": {"type": "table", "pattern": r'bonus'},
    "Right_Issues": {"type": "table", "pattern": r'rights'},
    "Stock_Splits": {"type": "table", "pattern": r'split'},
    "Buy_Backs": {"type": "table", "pattern": r'buy-back'},
    "Dividends": {"type": "table", "pattern": r'dividend'}
}

# Raw injection of the cookie to bypass Cloudflare POST-login blocks
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Language': 'en-US,en;q=0.9',
    'Referer': 'https://www.screener.in/',
    'Connection': 'keep-alive',
    'Cookie': f'sessionid={SESSION_ID_COOKIE}' if SESSION_ID_COOKIE else ''
}

session = tls_requests.Session(impersonate="chrome124")

def map_dashboard_links():
    """Scans the live dashboard to find the true, current URLs for all categories."""
    print("\n[*] Initializing dynamic endpoint mapper on Dashboard...")
    dashboard_url = "https://www.screener.in/market-pulse/"
    live_endpoints = {}
    
    try:
        resp = session.get(dashboard_url, headers=HEADERS, timeout=15)
        
        if "login" in resp.url.lower():
            print("      [CRITICAL] Dashboard redirected to login. Your SESSION_ID_COOKIE is expired or missing.")
            return {}
            
        soup = BeautifulSoup(resp.text, 'html.parser')
        
        for a in soup.find_all('a'):
            href = a.get('href', '')
            if not href:
                continue
                
            for cat_name, props in TARGET_CATEGORIES.items():
                if cat_name not in live_endpoints:
                    # If the link matches our safety regex pattern, store the full URL
                    if re.search(props['pattern'], href, re.IGNORECASE):
                        live_endpoints[cat_name] = {
                            "url": urljoin(dashboard_url, href),
                            "type": props["type"]
                        }
                        
        print(f"      [OK] Dynamically mapped {len(live_endpoints)} out of {len(TARGET_CATEGORIES)} active endpoints.")
        
        missing = set(TARGET_CATEGORIES.keys()) - set(live_endpoints.keys())
        if missing:
            print(f"      [!] Failed to locate endpoints for: {', '.join(missing)}")
            
        return live_endpoints
    except Exception as e:
        print(f"      [!] Network mapper failed: {e}")
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
        print(f"      [-] No structured data tables found on page.")
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
            if symbol == "GLOBAL":
                csv_path = os.path.join(GLOBAL_DIR, f"{category_name}_{TODAY}.csv")
                clean_group.to_csv(csv_path, index=False, encoding='utf-8-sig')
                print(f"      [OK] Logged Global Data -> Global_Data/{category_name}_{TODAY}.csv")
            else:
                stock_table_dir = os.path.join(STOCKS_DIR, symbol, "Tables")
                os.makedirs(stock_table_dir, exist_ok=True)
                csv_path = os.path.join(stock_table_dir, f"{category_name}.csv")
                
                if os.path.exists(csv_path):
                    try:
                        existing_df = pd.read_csv(csv_path)
                        combined_df = pd.concat([existing_df, clean_group], ignore_index=True).drop_duplicates()
                        combined_df.to_csv(csv_path, index=False, encoding='utf-8-sig')
                    except Exception:
                        clean_group.to_csv(csv_path, index=False, encoding='utf-8-sig')
                else:
                    clean_group.to_csv(csv_path, index=False, encoding='utf-8-sig')
                print(f"      [OK] Updated Stock Row -> Stocks/{symbol}/Tables/{category_name}.csv")

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
    if not SESSION_ID_COOKIE:
        print("[CRITICAL] SCREENER_COOKIE missing from GitHub environment.")
        sys.exit(1)

    os.makedirs(STOCKS_DIR, exist_ok=True)
    os.makedirs(GLOBAL_DIR, exist_ok=True)
    
    print(f"\n{'='*50}")
    print("=== DYNAMIC MARKET PULSE PIPELINE ===")
    print(f"{'='*50}")
    
    # Let the script find the exact URLs itself
    live_endpoints = map_dashboard_links()
    
    if not live_endpoints:
        print("[CRITICAL] Could not map any active endpoints. Exiting.")
        sys.exit(1)
        
    for category_name, payload in live_endpoints.items():
        print(f"\n>>> Processing Category Endpoint: {category_name}")
        url = payload["url"]
        data_type = payload["type"]
        
        try:
            resp = session.get(url, headers=HEADERS, timeout=15)
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
