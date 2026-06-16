import os
import re
import sys
import time
from datetime import datetime
from urllib.parse import urljoin
from bs4 import BeautifulSoup
from io import StringIO

try:
    from playwright.sync_api import sync_playwright
    import pandas as pd
except ImportError:
    print("[CRITICAL] Missing libraries. Run: pip install playwright pandas beautifulsoup4 lxml html5lib")
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

def sanitize_filename(text: str) -> str:
    clean = re.sub(r'[\\/*?:"<>|\'’]', "", text)
    return clean.replace(" ", "_").strip()[:100]

def extract_tables(category_name, page_content):
    soup = BeautifulSoup(page_content, 'html.parser')
    html_tables = soup.find_all('table')
    
    try:
        html_stream = StringIO(page_content)
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
            if symbol == "GLOBAL":
                csv_path = os.path.join(GLOBAL_DIR, f"{category_name}_{TODAY}.csv")
                clean_group.to_csv(csv_path, index=False, encoding='utf-8-sig')
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
        print(f"      [OK] Parsed and saved table data for {category_name}.")

def extract_documents(page_content, context):
    soup = BeautifulSoup(page_content, 'html.parser')
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
                # Use the browser's internal network context to download the file directly
                response = context.request.get(full_url)
                if response.ok:
                    with open(save_path, 'wb') as f:
                        f.write(response.body())
                    print(f"      [OK] Downloaded -> {symbol}/Documents/{filename}")
                    doc_count += 1
                    time.sleep(1) # Polite delay
            except Exception as e:
                print(f"      [!] Failed to download {filename}: {e}")
                
    if doc_count == 0:
        print("      [-] No new documents found.")

def run_scraper():
    if not USERNAME or not PASSWORD:
        print("[CRITICAL] Missing SCREENER_USERNAME or SCREENER_PASSWORD in GitHub secrets.")
        sys.exit(1)

    os.makedirs(STOCKS_DIR, exist_ok=True)
    os.makedirs(GLOBAL_DIR, exist_ok=True)
    
    print(f"\n{'='*50}")
    print("=== PLAYWRIGHT CLOUD BROWSER INITIALIZED ===")
    print(f"{'='*50}")

    with sync_playwright() as p:
        # Launch an invisible Chromium browser
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        )
        page = context.new_page()

        print("[*] Navigating to Login and bypassing firewalls...")
        page.goto("https://www.screener.in/login/")
        
        # Fill in credentials and log in
        page.fill("input[name='username']", USERNAME)
        page.fill("input[name='password']", PASSWORD)
        page.click("button[type='submit']")
        
        # Wait for the dashboard to load (ensures Cloudflare JS passes)
        page.wait_for_url("**/market-pulse/**", timeout=20000)
        print("      [OK] Successfully authenticated and loaded Market Pulse!")

        # 1. Dynamically map the dashboard links
        print("\n[*] Mapping live category endpoints...")
        dashboard_content = page.content()
        soup = BeautifulSoup(dashboard_content, 'html.parser')
        
        live_endpoints = {}
        for a in soup.find_all('a'):
            href = a.get('href', '')
            if not href: continue
                
            for cat_name, props in TARGET_CATEGORIES.items():
                if cat_name not in live_endpoints and re.search(props['pattern'], href, re.IGNORECASE):
                    live_endpoints[cat_name] = {
                        "url": urljoin("https://www.screener.in", href),
                        "type": props["type"]
                    }
        
        print(f"      [OK] Mapped {len(live_endpoints)} active targets.")
        
        # 2. Process each endpoint
        for category_name, payload in live_endpoints.items():
            print(f"\n>>> Processing Category: {category_name}")
            try:
                # Command the browser to visit the page
                page.goto(payload["url"])
                page.wait_for_load_state("domcontentloaded")
                time.sleep(2) # Give the data tables an extra second to render
                
                content = page.content()
                
                if "login" in page.url.lower():
                    print("      [!] Kicked back to login. Session dropped.")
                    continue

                if payload["type"] == "table":
                    extract_tables(category_name, content)
                elif payload["type"] == "doc":
                    extract_documents(content, context)
                    
            except Exception as e:
                print(f"      [!] Browser navigation error: {e}")
                
        browser.close()
        print("\n=== CLOUD BROWSER EXTRACTION COMPLETE ===")

if __name__ == "__main__":
    run_scraper()
