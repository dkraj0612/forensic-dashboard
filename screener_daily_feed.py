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

# Print confirmation to GitHub Logs securely
if not SESSION_ID_COOKIE:
    print("[ERROR] SCREENER_COOKIE environment variable is empty or missing.")
    sys.exit(1)
else:
    print(f"[SYSTEM] Cookie successfully injected into pipeline. Length: {len(SESSION_ID_COOKIE)} chars.")

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

# Bulletproof Header Structuring: Forcing the cookie at the root request layer
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
    'Accept-Language': 'en-US,en;q=0.9',
    'Referer': 'https://www.screener.in/',
    'Connection': 'keep-alive',
    'Cookie': f'sessionid={SESSION_ID_COOKIE}'
}

session = tls_requests.Session(impersonate="chrome124")

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
        print(f"      [-] No structured data tables found on this page.")
        return

    for idx, df in enumerate(pandas_tables):
        if df.empty:
            continue
        if idx >= len(html_tables):
            break
            
        html_table = html_tables[idx]
        html_rows = html_table.find_all('tr')
        if html_table.find('th'):
            html_rows = [r for r in html_rows if not r.find('th')]
        else:
            html_rows = html_rows[1:]
            
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
    os.makedirs(STOCKS_DIR, exist_ok=True)
    os.makedirs(GLOBAL_DIR, exist_ok=True)
    
    print(f"\n{'='*50}")
    print("=== DIRECT HEADER INJECTION PIPELINE ===")
    print(f"{'='*50}")
    
    for category_name, (data_type, url) in TARGET_URLS.items():
        print(f"\n>>> Processing Category Endpoint: {category_name}")
        try:
            # Passing HEADERS containing the forced Cookie string directly
            resp = session.get(url, headers=HEADERS, timeout=15)
            
            # If redirected to login, the cookie is dead or blocked by location firewalls
            if resp.status_code != 200 or "login" in resp.url.lower():
                print(f"      [!] Session verification failure on {category_name}. Server rejected token header.")
                continue
                
            if data_type == "table":
                extract_tables(category_name, resp)
            elif data_type == "doc":
                extract_documents(resp)
                
        except Exception as e:
            print(f"      [!] Transport layer error: {e}")
            
        time.sleep(2)
        
    print("\n=== SYSTEM PROCESSING COMPLETE ===")

if __name__ == "__main__":
    main()
