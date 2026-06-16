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
    print("[CRITICAL] Missing dependencies. Run: pip install curl_cffi pandas lxml html5lib beautifulsoup4")
    sys.exit(1)

# ==========================================
#               CONFIGURATION
# ==========================================
# Securely pull authentication data from environment scopes
SESSION_ID_COOKIE = os.getenv("SCREENER_COOKIE")
OUTPUT_DIR = "market_pulse_data"
# ==========================================

TODAY = datetime.today().strftime('%Y-%m-%d')
STOCKS_DIR = os.path.join(OUTPUT_DIR, "Stocks")
GLOBAL_DIR = os.path.join(OUTPUT_DIR, "Global_Data")

TARGET_URLS = {
    "Concalls": ("doc", "https://www.screener.in/concalls/"),
    "Upcoming_Concalls": ("table", "https://www.screener.in/concalls/upcoming/"),
    "Annual_Reports": ("doc", "https://www.screener.in/reports/"),
    "FII_Investment": ("table", "https://www.screener.in/fii-fpi-investment/"),
    "Upcoming_Results": ("table", "https://www.screener.in/results/upcoming/"),
    "Bulk_Deals": ("table", "https://www.screener.in/bulk-deals/"),
    "Block_Deals": ("table", "https://www.screener.in/block-deals/"),
    "SAST_Trades": ("table", "https://www.screener.in/sast-trades/"),
    "Insider_Trades": ("table", "https://www.screener.in/insider-trades/"),
    "Bonus_Issues": ("table", "https://www.screener.in/corporate-actions/bonus/"),
    "Right_Issues": ("table", "https://www.screener.in/corporate-actions/rights/"),
    "Stock_Splits": ("table", "https://www.screener.in/corporate-actions/split/"),
    "Buy_Backs": ("table", "https://www.screener.in/corporate-actions/buy-back/"),
    "Dividends": ("table", "https://www.screener.in/corporate-actions/dividend/")
}

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
    'Accept-Language': 'en-US,en;q=0.9',
    'Referer': 'https://www.screener.in/',
    'Connection': 'keep-alive',
}

session = tls_requests.Session(impersonate="chrome124")

def verify_and_inject_auth():
    """Validates the structure of the input token environment layer."""
    global HEADERS
    if not SESSION_ID_COOKIE:
        print("[CRITICAL ERROR] The environment variable 'SCREENER_COOKIE' is entirely empty.")
        print("                 Ensure your GitHub Repository Secret is explicitly mapped in the workflow YAML file.")
        sys.exit(1)
        
    clean_cookie = SESSION_ID_COOKIE.strip()
    print(f"[SYSTEM] Validating pipeline token initialization (Length: {len(clean_cookie)} characters)...")
    
    # Inject directly into transport header map to bypass internal session manager rewrites
    HEADERS['Cookie'] = f"sessionid={clean_cookie}"

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
        print(f"      [-] No parsable structural data matrix located on page DOM.")
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
                print(f"      [OK] Logged Global Dataset Segment -> Global_Data/{category_name}_{TODAY}.csv")
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
                print(f"      [OK] Synchronized Ticker Registry Path -> Stocks/{symbol}/Tables/{category_name}.csv")

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
                    print(f"      [OK] Downloaded Document Attachment -> Stocks/{symbol}/Documents/{filename}")
                    doc_count += 1
                    time.sleep(1.5)
            except Exception as e:
                print(f"      [!] Transmission failure targeting {filename}: {e}")
                
    if doc_count == 0:
        print("      [-] No unresolved archival documents detected on current trace.")

def main():
    os.makedirs(STOCKS_DIR, exist_ok=True)
    os.makedirs(GLOBAL_DIR, exist_ok=True)
    
    print(f"\n{'='*50}")
    print("=== PIPELINE AUDIT AND TRACKING ENGINE ===")
    print(f"{'='*50}")
    
    verify_and_inject_auth()
    
    for category_name, (data_type, url) in TARGET_URLS.items():
        print(f"\n>>> Querying Category Endpoint: {category_name}")
        try:
            resp = session.get(url, headers=HEADERS, timeout=15)
            
            # CRITICAL AUDIT: Trace exact delivery endpoints to uncover silent routing drops
            print(f"      [DIAGNOSTIC] Response Code: {resp.status_code} | Target URL: {resp.url}")
            
            if "login" in resp.url.lower() or resp.status_code == 403:
                print(f"      [!] Access Denied on {category_name}. Session dropped by security gateway.")
                if "challenges.cloudflare.com" in resp.text or "sucuri" in resp.text.lower():
                    print("          -> Detected active JavaScript WAF Challenge Interstitial Page.")
                continue
                
            if data_type == "table":
                extract_tables(category_name, resp)
            elif data_type == "doc":
                extract_documents(resp)
                
        except Exception as e:
            print(f"      [!] Connection dropping at socket layer: {e}")
            
        time.sleep(2.5)
        
    print("\n=== ENGINE OPERATION COMPLETE ===")

if __name__ == "__main__":
    main()
