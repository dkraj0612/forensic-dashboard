
import os
import sys
import time
import json
import random
import re
import datetime
import threading
from io import StringIO

try:
    from curl_cffi import requests as tls_requests
    from bs4 import BeautifulSoup
    import pandas as pd
    import google.generativeai as genai
except ImportError:
    print("[CRITICAL] Missing libraries. Run: pip install curl_cffi pandas beautifulsoup4 google-generativeai lxml")
    sys.exit(1)

# =====================================================================
# SYSTEM CONFIGURATION
# =====================================================================
OUTPUT_DIR = "market_pulse_data"
STOCKS_DIR = os.path.join(OUTPUT_DIR, "Stocks")
LOG_DIR = os.path.join(OUTPUT_DIR, "System_Logs")
FUNDAMENTALS_DIR = os.path.join(OUTPUT_DIR, "Fundamentals")

NOW = datetime.datetime.today()
TODAY_STR = NOW.strftime('%Y-%m-%d')
YESTERDAY_STR = (NOW - datetime.timedelta(days=1)).strftime('%Y-%m-%d')
TARGET_DATES = [TODAY_STR, YESTERDAY_STR]

# 🌟 AI MEMORY BANK (MARKDOWN FOR NATIVE GITHUB VIEWING)
THESES_MD_FILE = os.path.join(FUNDAMENTALS_DIR, f"ai_theses_{TODAY_STR}.md") 
DAILY_METRICS_FILE_TODAY = os.path.join(LOG_DIR, f"daily_metrics_{TODAY_STR}.json")
DAILY_METRICS_FILE_YEST = os.path.join(LOG_DIR, f"daily_metrics_{YESTERDAY_STR}.json")

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

os.makedirs(FUNDAMENTALS_DIR, exist_ok=True)

# Initialize Markdown file with a Master Header if it doesn't exist
if not os.path.exists(THESES_MD_FILE):
    with open(THESES_MD_FILE, 'w', encoding='utf-8') as f:
        f.write(f"# 📊 AI Fundamental Theses - {TODAY_STR}\n\n---\n\n")

# =====================================================================
# PHASE 1: PIPELINE STATE VALIDATION & DISCOVERY
# =====================================================================
def send_telegram_alert(message: str):
    """Sends a raw administrative alert to Telegram."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID: return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "HTML"}
    try: 
        tls_requests.post(url, json=payload, timeout=30)
    except Exception as e: 
        print(f"[!] Failed to send Telegram alert: {e}")

def verify_pipeline_success():
    """Checks if the Phase 1 Daily Sweeper completed successfully yesterday (or today)."""
    print("[*] Validating Phase 1 Pipeline state (checking for yesterday's run)...")
    
    if not (os.path.exists(DAILY_METRICS_FILE_YEST) or os.path.exists(DAILY_METRICS_FILE_TODAY)):
        error_msg = "🚨 <b>SYSTEM ALERT</b> 🚨\n━━━━━━━━━━━━━━━━━━━━\n<b>Pipeline failed.</b> Did not run fundamental analysis.\n\n<i>Reason: Yesterday's sweeper metrics file is missing.</i>"
        print("[!] FATAL: Daily metrics file missing. Phase 1 likely failed.")
        send_telegram_alert(error_msg)
        sys.exit(1) # Kill the script immediately
        
    print("[+] Phase 1 Pipeline success verified. Proceeding.")

def get_recent_result_tickers() -> list:
    """Scans the GitHub folder structure to find stocks that posted results recently."""
    print(f"[*] Scanning {STOCKS_DIR} for stocks with Results filed on {TARGET_DATES}...")
    target_tickers = set()
    
    if not os.path.exists(STOCKS_DIR):
        return []

    for ticker in os.listdir(STOCKS_DIR):
        results_dir = os.path.join(STOCKS_DIR, ticker, "Results")
        if os.path.isdir(results_dir):
            for file in os.listdir(results_dir):
                if file.endswith('.json'):
                    try:
                        with open(os.path.join(results_dir, file), 'r', encoding='utf-8') as f:
                            data = json.load(f)
                            if data.get("extraction_date") in TARGET_DATES:
                                target_tickers.add(ticker)
                    except Exception: 
                        pass
                        
    tickers_list = list(target_tickers)
    print(f"[+] Found {len(tickers_list)} stocks with recent Results: {tickers_list}")
    return tickers_list


# =====================================================================
# PHASE 2: DEEP SCREENER SCRAPER
# =====================================================================
def scrape_screener_fundamentals(ticker: str) -> dict:
    """Scrapes comprehensive tabular data from Screener.in for a given ticker."""
    print(f"\n      [~] Scraping deep fundamentals for {ticker}...")
    
    session = tls_requests.Session(impersonate="chrome124")
    session.headers.update({'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'})
    
    url = f"https://www.screener.in/company/{ticker}/consolidated/"
    try:
        resp = session.get(url, timeout=30)
        if resp.status_code == 404:
            url = f"https://www.screener.in/company/{ticker}/"
            resp = session.get(url, timeout=30)
        if resp.status_code != 200: 
            return {}
    except Exception: 
        return {}

    soup = BeautifulSoup(resp.text, 'html.parser')
    
    data = {
        'Ticker': ticker,
        'Extraction_Date': TODAY_STR,
        'About_&_Key_Points': "N/A",
        'Top_Level_Metrics': "N/A",
        'Pros_and_Cons_Summary': "N/A",
        'Quarterly_Results': "N/A",
        'Profit_Loss': "N/A",
        'Balance_Sheet': "N/A",
        'Cash_Flow': "N/A",
        'Detailed_Ratios': "N/A",
        'Four_CAGR_Boxes': "N/A",
        'Peers': "N/A",
        'Shareholding_Pattern': "N/A"
    }

    # 1. About & Key Points
    profile = soup.find('div', class_='company-profile')
    if profile:
        raw_text = profile.get_text(separator='\n', strip=True)
        clean_text = re.sub(r'(Website|BSE|NSE)\n*', '', raw_text)
        data['About_&_Key_Points'] = clean_text.strip()

    # 2. Top Level Metrics
    ratios_ul = soup.find('ul', id='top-ratios')
    if ratios_ul:
        ratios_list = []
        for li in ratios_ul.find_all('li'):
            name_elem = li.find('span', class_='name')
            val_elem = li.find('span', class_='value') or li.find('span', class_='number')
            name = name_elem.get_text(strip=True) if name_elem else ''
            val = val_elem.get_text(separator=' ', strip=True) if val_elem else ''
            if name and val:
                ratios_list.append(f"{name}: {val}")
        data['Top_Level_Metrics'] = "\n".join(ratios_list)

    # 3. Screener Summary (Pros & Cons Analysis)
    pros_cons_text = ""
    analysis_section = soup.find('section', id='analysis')
    if analysis_section:
        pros = analysis_section.find('div', class_='pros')
        if pros:
            pros_cons_text += "PROS:\n" + "\n".join([f"- {li.get_text(strip=True)}" for li in pros.find_all('li')]) + "\n\n"
        cons = analysis_section.find('div', class_='cons')
        if cons:
            pros_cons_text += "CONS:\n" + "\n".join([f"- {li.get_text(strip=True)}" for li in cons.find_all('li')])
    if pros_cons_text:
        data['Pros_and_Cons_Summary'] = pros_cons_text.strip()

    # Helper function to extract HTML tables safely into CSV strings
    def extract_table(section_id):
        sec = soup.find('section', id=section_id)
        if sec and sec.find('table'):
            try: 
                return pd.read_html(StringIO(str(sec.find('table'))))[0].to_csv(index=False).strip()
            except Exception: 
                pass
        return "N/A"

    # 4. Core Financial Grids
    data['Quarterly_Results'] = extract_table('quarters')
    data['Profit_Loss'] = extract_table('profit-loss')
    data['Balance_Sheet'] = extract_table('balance-sheet')
    data['Cash_Flow'] = extract_table('cash-flow')
    data['Detailed_Ratios'] = extract_table('ratios')
    data['Peers'] = extract_table('peers')
    data['Shareholding_Pattern'] = extract_table('shareholding')

    # 5. Four CAGR Boxes
    cagr_text = ""
    for box in soup.find_all('table', class_='ranges-table'):
        try:
            df = pd.read_html(StringIO(str(box)))[0]
            title = box.find('th').get_text(strip=True) if box.find('th') else "Metric"
            cagr_text += f"[{title}]\n{df.to_csv(index=False, header=False).strip()}\n\n"
        except Exception: 
            pass
    if cagr_text:
        data['Four_CAGR_Boxes'] = cagr_text.strip()

    return data


# =====================================================================
# PHASE 3 & 4: AI ANALYSIS AND TELEGRAM DISPATCH
# =====================================================================
def analyze_and_dispatch(ticker: str, csv_data: dict):
    if not GEMINI_API_KEY: 
        return
    
    print(f"      [~] Generating AI Fundamental Thesis for {ticker}...")
    genai.configure(api_key=GEMINI_API_KEY)
    ai_client = genai.GenerativeModel('gemini-2.5-flash')

    prompt = f"""
    You are an elite Quantitative Equity Analyst. 
   Analyze the provided financial data for ROLLT. Python has already calculated the exact QoQ and YoY percentage changes for you. Do not calculate your own math. Use the percentages provided.
Output a sharp, mobile-friendly bulleted report focusing on anomalies and momentum. No Markdown tables.

RAW DATA FOR ROLLT:
-------------------------
ABOUT & KEY POINTS: Incorporated in 1972, Rollatainers Ltd provides integrated packaging solutions. Has flexible laminates division (4000 MT/annum capacity) and cartons division (5 million cartons annually).
TOP METRICS: Market Cap: ₹ 95 Cr, Current Price: ₹ 3.80, Stock P/E: 4.5, Book Value: ₹ 0.37.

[PYTHON MATH-AUGMENTED GRIDS]

QUARTERLY RESULTS (In ₹ Cr):
Metric | Mar 2025 | Dec 2025 | Mar 2026 | [Python QoQ %] | [Python YoY %]
Sales | 12.50 | 0.00 | 0.00 | 0.00% | -100.00%
Expenses | 14.00 | 2.50 | 1.80 | -28.00% | -87.14%
Operating Profit | -1.50 | -2.50 | -1.80 | +28.00% | -20.00%
Other Income | 0.50 | 17.79 | 0.10 | -99.44% | -80.00%
Interest | 1.00 | 1.20 | 1.20 | 0.00% | +20.00%
Net Profit | -2.50 | 17.59 | -2.10 | -111.94% | +16.00%

BALANCE SHEET (In ₹ Cr):
Metric | Mar 2025 | Mar 2026 | [Python YoY %]
Borrowings | 31.00 | 35.00 | +12.90%
Trade Payables | 12.00 | 14.00 | +16.67%

CASH FLOWS (In ₹ Cr):
Metric | Mar 2025 | Mar 2026 | [Python YoY %]
Cash from Operating Activity | -5.00 | -12.00 | -140.00%

DETAILED RATIOS:
Metric | Mar 2025 | Mar 2026 | [Python YoY Change]
Debtor Days | 602 | 602 | 0 days

SHAREHOLDING PATTERN:
Metric | Dec 2025 | Mar 2026 | [Python QoQ Change]
Promoters | 50.96% | 50.96% | 0.00%
FIIs | 0.00% | 0.00% | 0.00%
-------------------------

    """
    
    # --- EXPONENTIAL BACKOFF ENGINE ---
    max_retries = 5
    base_delay = 5 
    
    for attempt in range(max_retries):
        try:
            response = ai_client.generate_content(prompt)
            
            # 🌟 BUG FIX: Catch silent Gemini Safety blocks 🌟
            try:
                ai_thesis_text = response.text.strip()
            except ValueError:
                ai_thesis_text = f"⚪ **NEUTRAL [AI BLOCKED]**\n\nGemini AI Safety Filters blocked the analysis for {ticker}. This usually happens if the raw CSV contains heavily flagged financial keywords (like 'Bankruptcy' or 'Fraud')."
            
            # 🌟 CONSOLE LOG MIRRORING 🌟
            print("\n" + "="*70)
            print(f"=== 🏛️ FUNDAMENTAL DEEP DIVE: {ticker} ===")
            print("="*70)
            print(ai_thesis_text)
            print("="*70 + "\n")

            # 🌟 GITHUB NATIVE MARKDOWN MEMORY BANK EXPORT 🌟
            md_thesis = "\n".join([f"> {line}" for line in ai_thesis_text.split('\n')])
            
            with open(THESES_MD_FILE, 'a', encoding='utf-8') as f:
                f.write(f"## 🏛️ {ticker}\n")
                f.write(f"*Generated on {NOW.strftime('%A, %B %d, %Y at %I:%M %p')}*\n\n")
                f.write(f"{md_thesis}\n\n---\n\n")
            
            print(f"      [+] AI Thesis for {ticker} successfully archived to Markdown Memory Bank.")

            # Send to Telegram
            send_to_telegram(ticker, ai_thesis_text)
            break 
            
        except Exception as api_error:
            error_str = str(api_error).lower()
            if '429' in error_str or 'quota' in error_str:
                wait_time = base_delay * (2 ** attempt) + random.uniform(1, 3)
                print(f"      [!] AI Rate Limit Hit for {ticker}. Retrying in {wait_time:.1f}s (Attempt {attempt+1}/{max_retries})...")
                time.sleep(wait_time)
            else:
                print(f"      [!] AI Thesis API Call failed for {ticker}: {api_error}")
                break 
    else:
        print(f"      [!] Failed to generate AI Thesis for {ticker} after {max_retries} attempts.")

def send_to_telegram(ticker: str, text: str):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID: 
        return
        
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    session = tls_requests.Session(impersonate="chrome124")
    
    formatted_text = re.sub(r'\*\*(.*?)\*\*', r'<b>\1</b>', text)
    paragraphs = formatted_text.split('\n\n')
    summary_chunks = []
    current_chunk = ""
    
    for p in paragraphs:
        if len(current_chunk) + len(p) > 3500:
            summary_chunks.append(current_chunk.strip())
            current_chunk = p + "\n\n"
        else:
            current_chunk += p + "\n\n"
            
    if current_chunk:
        summary_chunks.append(current_chunk.strip())
        
    total_chunks = len(summary_chunks)
    
    for i, chunk_text in enumerate(summary_chunks, 1):
        if i == 1:
            msg = f"🏛️ <b>FUNDAMENTAL DEEP DIVE: {ticker}</b>\n"
            if total_chunks > 1:
                msg += f"<i>[Part {i} of {total_chunks}]</i>\n"
            msg += "━━━━━━━━━━━━━━━━━━━━\n\n"
            msg += chunk_text
        else:
            msg = f"🏛️ <b>FUNDAMENTAL DEEP DIVE: {ticker}</b> <i>[Part {i} of {total_chunks}]</i>\n"
            msg += "━━━━━━━━━━━━━━━━━━━━\n\n"
            msg += chunk_text
            
        payload = {
            "chat_id": TELEGRAM_CHAT_ID, 
            "text": msg, 
            "parse_mode": "HTML", 
            "disable_web_page_preview": True
        }
        
        for attempt in range(3):
            try:
                resp = session.post(url, json=payload, timeout=60)
                if resp.status_code == 200:
                    print(f"      [OK] Sent {ticker} Telegram [Part {i}/{total_chunks}]")
                    break
                elif resp.status_code == 429:
                    retry_after = int(resp.json().get('parameters', {}).get('retry_after', 5))
                    time.sleep(retry_after + 1)
                else:
                    time.sleep(3)
            except Exception as e:
                time.sleep(5)
                
        if i < total_chunks:
            time.sleep(3.1)

# =====================================================================
# MAIN EXECUTION
# =====================================================================
def main():
    print(f"\n{'='*60}\n=== LEVEL 2: FUNDAMENTAL DEEP DIVE ANALYZER ===\n{'='*60}")
    
    verify_pipeline_success()
    
    tickers = get_recent_result_tickers()
    if not tickers:
        print("[*] No new results discovered in the file system. Exiting.")
        sys.exit(0)
        
    print(f"\n[*] Commencing extraction and AI Deep Dive Analysis on {len(tickers)} stocks...")
    
    for ticker in tickers:
        data = scrape_screener_fundamentals(ticker)
        
        if data:
            # 🌟 NEW: CREATE DEDICATED STOCK FOLDER (e.g., Fundamentals/TCS/)
            stock_folder = os.path.join(FUNDAMENTALS_DIR, ticker)
            os.makedirs(stock_folder, exist_ok=True)
            
            # 🌟 NEW: FORMAT DYNAMIC FILENAME (e.g., "june 2026 result.csv")
            month_year_str = NOW.strftime('%B %Y').lower() # Results in "june 2026"
            csv_filename = f"{month_year_str} result.csv"
            stock_csv_path = os.path.join(stock_folder, csv_filename)
            
            df = pd.DataFrame([data])
            df.to_csv(stock_csv_path, index=False)
            print(f"      [+] Exported {ticker} CSV to: Fundamentals/{ticker}/{csv_filename}")
            
            analyze_and_dispatch(ticker, data)
            time.sleep(15) 
        else:
            time.sleep(2) 
            
    print("\n[+] Fundamental Analysis Pipeline Complete. All Theses committed to Markdown Memory Bank.")

if __name__ == "__main__":
    main()

