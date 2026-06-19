
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

CSV_OUTPUT_PATH = os.path.join(FUNDAMENTALS_DIR, f"fundamental_exports_{TODAY_STR}.csv")
THESES_HTML_FILE = os.path.join(FUNDAMENTALS_DIR, f"ai_theses_{TODAY_STR}.html") # 🌟 AI MEMORY BANK (NOW HTML)
DAILY_METRICS_FILE = os.path.join(LOG_DIR, f"daily_metrics_{TODAY_STR}.json")

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

os.makedirs(FUNDAMENTALS_DIR, exist_ok=True)

# Initialize HTML file with beautiful Dark Mode CSS if it doesn't exist
if not os.path.exists(THESES_HTML_FILE):
    with open(THESES_HTML_FILE, 'w', encoding='utf-8') as f:
        f.write(f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>AI Fundamental Theses - {TODAY_STR}</title>
<style>
    body {{ font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; background-color: #0d1117; color: #c9d1d9; max-width: 900px; margin: 0 auto; padding: 20px; line-height: 1.6; }}
    h1 {{ color: #58a6ff; text-align: center; border-bottom: 1px solid #30363d; padding-bottom: 15px; margin-bottom: 30px; }}
    .thesis-card {{ background-color: #161b22; border: 1px solid #30363d; border-radius: 12px; padding: 25px; margin-bottom: 25px; box-shadow: 0 4px 12px rgba(0,0,0,0.5); }}
    .thesis-card h2 {{ color: #58a6ff; margin-top: 0; display: flex; align-items: center; gap: 10px; }}
    .timestamp {{ color: #8b949e; font-size: 0.85em; display: block; margin-bottom: 15px; border-bottom: 1px solid #21262d; padding-bottom: 10px; }}
    strong {{ color: #ffffff; font-weight: 600; }}
    p {{ margin-bottom: 15px; }}
</style>
</head>
<body>
<h1>📊 AI Fundamental Theses - {TODAY_STR}</h1>
""")

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
    """Checks if the Phase 1 Daily Sweeper completed successfully today."""
    print("[*] Validating Phase 1 Pipeline state...")
    if not os.path.exists(DAILY_METRICS_FILE):
        error_msg = "🚨 <b>SYSTEM ALERT</b> 🚨\n━━━━━━━━━━━━━━━━━━━━\n<b>Pipeline failed.</b> Did not run results analysis.\n\n<i>Reason: Today's sweeper metrics file is missing.</i>"
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
    print(f"      [~] Scraping deep fundamentals for {ticker}...")
    
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
    You are a Tier-1 Institutional Equity Analyst. 
    The company {ticker} just declared its earnings. I am providing you with the freshly scraped CSV data from Screener containing their complete fundamentals.
    
    RAW CSV DATA FOR {ticker}:
    -------------------------
    ABOUT & KEY POINTS: {csv_data.get('About_&_Key_Points')}
    TOP METRICS (Market Cap, P/E, Price): {csv_data.get('Top_Level_Metrics')}
    SCREENER SUMMARY (PROS/CONS): {csv_data.get('Pros_and_Cons_Summary')}
    CAGR METRICS: {csv_data.get('Four_CAGR_Boxes')}
    QUARTERLY RESULTS: {csv_data.get('Quarterly_Results')}
    BALANCE SHEET: {csv_data.get('Balance_Sheet')}
    CASH FLOWS: {csv_data.get('Cash_Flow')}
    DETAILED RATIOS: {csv_data.get('Detailed_Ratios')}
    SHAREHOLDING PATTERN: {csv_data.get('Shareholding_Pattern')}
    PEER COMPARISON: {csv_data.get('Peers')}
    -------------------------
    
    Based ONLY on this data, write a highly professional, aggressive 3-paragraph fundamental thesis.
    Paragraph 1: Executive Summary & Rating (Start with exactly one of: 🟢 BULLISH, 🔴 BEARISH, or ⚪ NEUTRAL). Analyze the YoY and QoQ growth from the Quarterly Results, incorporating their core business logic from the "About & Key Points" section.
    Paragraph 2: Financial Health & Margins. Break down their debt profile (from the Balance Sheet), operating cash generation (from Cash Flows), and current valuation (from Top Metrics like P/E and Market Cap).
    Paragraph 3: Red Flags & Shareholding. Note any promoter selling, working capital stress (debtor days), or trailing metrics compared to their peers.
    Use formatting (bolding, bullet points). Do not hallucinate data. Be concise.
    """
    
    # --- EXPONENTIAL BACKOFF ENGINE ---
    max_retries = 5
    base_delay = 5 
    
    for attempt in range(max_retries):
        try:
            response = ai_client.generate_content(prompt)
            ai_thesis_text = response.text.strip()
            
            # 🌟 HTML MEMORY BANK EXPORT 🌟
            # Convert AI Markdown to Clean HTML
            html_thesis = ai_thesis_text.replace('\n', '<br>') # Convert newlines to breaks
            html_thesis = re.sub(r'\*\*(.*?)\*\*', r'<strong>\1</strong>', html_thesis) # Convert bold
            
            with open(THESES_HTML_FILE, 'a', encoding='utf-8') as f:
                f.write(f"""
<div class="thesis-card">
    <h2>🏛️ {ticker}</h2>
    <span class="timestamp">Generated on {NOW.strftime('%A, %B %d, %Y at %I:%M %p')}</span>
    <p>{html_thesis}</p>
</div>
""")
            print(f"      [+] AI Thesis for {ticker} successfully archived to HTML Memory Bank.")

            # Send to Telegram
            send_to_telegram(ticker, ai_thesis_text)
            break # Success, exit the retry loop
            
        except Exception as api_error:
            error_str = str(api_error).lower()
            if '429' in error_str or 'quota' in error_str:
                wait_time = base_delay * (2 ** attempt) + random.uniform(1, 3)
                print(f"      [!] AI Rate Limit Hit for {ticker}. Retrying in {wait_time:.1f}s (Attempt {attempt+1}/{max_retries})...")
                time.sleep(wait_time)
            else:
                print(f"      [!] AI Thesis generation failed for {ticker}: {api_error}")
                break 
    else:
        print(f"      [!] Failed to generate AI Thesis for {ticker} after {max_retries} attempts.")

def send_to_telegram(ticker: str, text: str):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID: 
        return
        
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    session = tls_requests.Session(impersonate="chrome124")
    
    # 1. Safely convert Markdown bold (**text**) to HTML bold (<b>text</b>)
    formatted_text = re.sub(r'\*\*(.*?)\*\*', r'<b>\1</b>', text)
    
    # 2. SMART CHUNKING: Split by paragraph to NEVER break an HTML tag in half
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
    
    # 3. DISPATCH LOOP WITH CONTINUITY HEADERS
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
        
        # 4. BULLETPROOF RETRY SYSTEM FOR TELEGRAM
        for attempt in range(3):
            try:
                resp = session.post(url, json=payload, timeout=60)
                if resp.status_code == 200:
                    print(f"      [OK] Sent {ticker} deep dive [Part {i}/{total_chunks}]")
                    break
                elif resp.status_code == 429:
                    retry_after = int(resp.json().get('parameters', {}).get('retry_after', 5))
                    time.sleep(retry_after + 1)
                else:
                    time.sleep(3)
            except Exception as e:
                time.sleep(5)
                
        # 5. STRICT TELEGRAM RATE LIMIT DELAY (20 msgs / min)
        if i < total_chunks:
            time.sleep(3.1)

# =====================================================================
# MAIN EXECUTION
# =====================================================================
def main():
    print(f"\n{'='*60}\n=== LEVEL 2: FUNDAMENTAL DEEP DIVE ANALYZER ===\n{'='*60}")
    
    # 🚨 STEP 1: SAFETY CHECK
    verify_pipeline_success()
    
    # STEP 2: DISCOVERY
    tickers = get_recent_result_tickers()
    if not tickers:
        print("[*] No new results discovered in the file system. Exiting.")
        sys.exit(0)
        
    # STEP 3: SCRAPE DATA
    all_data = []
    for ticker in tickers:
        data = scrape_screener_fundamentals(ticker)
        if data: 
            all_data.append(data)
        time.sleep(2)
        
    # STEP 4: EXPORT AND ANALYZE
    if all_data:
        df = pd.DataFrame(all_data)
        df.to_csv(CSV_OUTPUT_PATH, index=False)
        print(f"\n[+] Master CSV Matrix successfully exported to: {CSV_OUTPUT_PATH}")
        
        print(f"\n[*] Commencing AI Deep Dive Analysis on {len(all_data)} stocks...")
        for row in all_data:
            analyze_and_dispatch(row['Ticker'], row)
            time.sleep(15) # Stay clear of Gemini base Rate Limits
            
    print("\n[+] Fundamental Analysis Pipeline Complete. All Theses committed to HTML Memory Bank.")

if __name__ == "__main__":
    main()

