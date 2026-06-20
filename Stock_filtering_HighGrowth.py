import os
import io
import re
import time
import random
import sqlite3
import csv
import logging
import requests
import concurrent.futures
import glob
from bs4 import BeautifulSoup
import pandas as pd

# Setup logging configuration
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# Configuration constants & Dynamic Date Stamping
RUN_DATE = time.strftime("%Y-%m-%d")
SCREENER_DATA_DIR = "ScreenerData"

DB_FILE = os.path.join(SCREENER_DATA_DIR, "screener_data.db")
CSV_FILE = os.path.join(SCREENER_DATA_DIR, f"qualified_stocks_analysis_{RUN_DATE}.csv")
TELEGRAM_LOG_FILE = os.path.join(SCREENER_DATA_DIR, f"telegram_analysis_log_{RUN_DATE}.txt")

NSE_URL = "https://archives.nseindia.com/content/equities/EQUITY_L.csv"
SCREENER_BASE_URL = "https://www.screener.in/company/{}/consolidated/"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}

# Telegram Credentials (passed securely via GitHub Secrets/Environment)
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

def init_db():
    """Initializes the master directory and SQLite database schema including the salvaged top-card data."""
    os.makedirs(SCREENER_DATA_DIR, exist_ok=True)
    
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS scraped_metrics (
            ticker TEXT PRIMARY KEY,
            scraped_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            classification TEXT,
            qualification_reason TEXT,
            
            -- Core Framework Data
            sales_y1 REAL, sales_y2 REAL, sales_y3 REAL,
            sales_q1 REAL, sales_q2 REAL,
            net_profit_y1 REAL, net_profit_y2 REAL,
            opm_y1 REAL, opm_y2 REAL,
            operating_profit_y1 REAL, interest_y1 REAL,
            share_capital_y1 REAL, share_capital_y2 REAL,
            borrowings_y1 REAL, borrowings_y2 REAL,
            fixed_assets_y1 REAL, fixed_assets_y2 REAL,
            cwip_y1 REAL, cwip_y2 REAL,
            cfo_y1 REAL,
            roce_y1 REAL, roce_y2 REAL,
            ccc_y1 REAL, ccc_y2 REAL,
            promoter_q1 REAL, promoter_q2 REAL,
            fii_q1 REAL, fii_q2 REAL,
            dii_q1 REAL, dii_q2 REAL,
            
            -- Salvaged Extra Data (For Querying & Context)
            market_cap REAL,
            stock_pe REAL,
            dividend_yield REAL,
            other_income_y1 REAL,
            debtor_days_y1 REAL, debtor_days_y2 REAL,
            days_payable_y1 REAL, days_payable_y2 REAL,
            
            -- Qualitative Analysis Notes
            valuation_context TEXT,
            earnings_quality_analysis TEXT,
            pricing_power_analysis TEXT,
            secondary_red_flags TEXT
        )
    """)
    conn.commit()
    conn.close()

def fetch_nse_symbols():
    """Fetches NSE symbols and filters out illiquid/suspended listings to save scraping time."""
    logging.info("Fetching dynamic stock list from NSE...")
    try:
        response = requests.get(NSE_URL, headers=HEADERS, timeout=15)
        response.raise_for_status()
        df = pd.read_csv(io.StringIO(response.text))
        
        # Filter for standard EQ series (ignores most ETFs, NCDs, and suspended stocks)
        df = df[df[' SERIES'] == 'EQ']
        symbols = df['SYMBOL'].dropna().unique().tolist()
        logging.info(f"Retrieved {len(symbols)} active EQ symbols from NSE.")
        return symbols
    except Exception as e:
        logging.error(f"Failed to fetch NSE symbols: {e}")
        return []

def clean_value(val_str):
    """Normalizes raw text string metrics into pure floats."""
    if not val_str or val_str.strip() == "" or val_str == "–" or val_str == "-":
        return 0.0
    val_str = val_str.replace(",", "").replace("%", "").replace("₹", "").replace("Cr.", "").strip()
    try:
        return float(val_str)
    except ValueError:
        return 0.0

def extract_row_values(soup, table_id, row_label):
    """Finds specific table rows and strictly aligns time periods by stripping 'TTM'."""
    table = soup.find(id=table_id)
    if not table:
        return []
    
    # Detect if Screener added a 'TTM' column to this specific table
    has_ttm = False
    thead = table.find("thead")
    if thead:
        headers = thead.find_all("th")
        if headers and "TTM" in headers[-1].text.upper():
            has_ttm = True

    rows = table.find_all("tr")
    for row in rows:
        cells = row.find_all(["td", "th"])
        if cells and row_label.lower() in cells[0].text.lower():
            vals = [clean_value(c.text) for c in cells[1:]]
            
            # If the table has TTM, drop the last column to align with Balance Sheet FYs
            if has_ttm and len(vals) > 0:
                vals = vals[:-1] 
                
            return vals
    return []

def extract_top_ratio(soup, metric_name):
    """Extracts unstructured top-card metrics by bypassing nested span obfuscation."""
    try:
        name_span = soup.find('span', class_='name', string=re.compile(metric_name, re.IGNORECASE))
        if name_span:
            # Go up to the parent 'li' to bypass the new DOM nesting
            li_parent = name_span.find_parent('li')
            if li_parent:
                val_span = li_parent.find('span', class_='number')
                if val_span:
                    return clean_value(val_span.text)
    except Exception:
        pass
    return 0.0

def scrape_stock(symbol):
    """Parses both the framework data and the extra contextual data from Screener."""
    url = SCREENER_BASE_URL.format(symbol)
    try:
        response = requests.get(url, headers=HEADERS, timeout=10)
        if response.status_code == 404:
            url = f"https://www.screener.in/company/{symbol}/"
            response = requests.get(url, headers=HEADERS, timeout=10)
            if response.status_code != 200:
                return None
        elif response.status_code != 200:
            return None
            
        soup = BeautifulSoup(response.text, 'html.parser')
        
        def get_metric(table, name, length=3):
            arr = extract_row_values(soup, table, name)
            while len(arr) < length:
                arr.insert(0, 0.0)
            return arr

        # Data Dictionary: Core Framework
        sales_years = get_metric("profit-loss", "Sales", 3)
        sales_quarters = get_metric("quarters", "Sales", 2)
        net_profit_years = get_metric("profit-loss", "Net Profit", 2)
        opm_years = get_metric("profit-loss", "OPM", 2)
        op_years = get_metric("profit-loss", "Operating Profit", 1)
        interest_years = get_metric("profit-loss", "Interest", 1)
        
        # Corrected Data Dictionary mappings to match Screener exact labels
        share_cap = get_metric("balance-sheet", "Equity Capital", 2)
        borrowings = get_metric("balance-sheet", "Borrowings", 2)
        fixed_assets = get_metric("balance-sheet", "Fixed Assets", 2)
        cwip = get_metric("balance-sheet", "CWIP", 2)
        
        cfo = get_metric("cash-flow", "Cash from Operating Activity", 1)
        
        roce = get_metric("ratios", "ROCE", 2)
        ccc = get_metric("ratios", "Cash Conversion Cycle", 2)
        
        promoter = get_metric("shareholding", "Promoters", 2)
        fii = get_metric("shareholding", "FIIs", 2)
        dii = get_metric("shareholding", "DIIs", 2)

        # Extra Salvaged Data
        other_income_years = get_metric("profit-loss", "Other Income", 1)
        debtor_days = get_metric("ratios", "Debtor Days", 2)
        payable_days = get_metric("ratios", "Creditor Days", 2)

        return {
            "ticker": symbol,
            "sales_y1": sales_years[-1], "sales_y2": sales_years[-2], "sales_y3": sales_years[-3],
            "sales_q1": sales_quarters[-1], "sales_q2": sales_quarters[-2],
            "net_profit_y1": net_profit_years[-1], "net_profit_y2": net_profit_years[-2],
            "opm_y1": opm_years[-1], "opm_y2": opm_years[-2],
            "operating_profit_y1": op_years[-1], "interest_y1": interest_years[-1],
            "share_capital_y1": share_cap[-1], "share_capital_y2": share_cap[-2],
            "borrowings_y1": borrowings[-1], "borrowings_y2": borrowings[-2],
            "fixed_assets_y1": fixed_assets[-1], "fixed_assets_y2": fixed_assets[-2],
            "cwip_y1": cwip[-1], "cwip_y2": cwip[-2],
            "cfo_y1": cfo[-1],
            "roce_y1": roce[-1], "roce_y2": roce[-2],
            "ccc_y1": ccc[-1], "ccc_y2": ccc[-2],
            "promoter_q1": promoter[-1], "promoter_q2": promoter[-2],
            "fii_q1": fii[-1], "fii_q2": fii[-2],
            "dii_q1": dii[-1], "dii_q2": dii[-2],
            
            # Additional Context Attributes
            "market_cap": extract_top_ratio(soup, "Market Cap"),
            "stock_pe": extract_top_ratio(soup, "Stock P/E"),
            "dividend_yield": extract_top_ratio(soup, "Dividend Yield"),
            "other_income_y1": other_income_years[-1],
            "debtor_days_y1": debtor_days[-1], "debtor_days_y2": debtor_days[-2],
            "days_payable_y1": payable_days[-1], "days_payable_y2": payable_days[-2],
            
            # Placeholders for generated text
            "valuation_context": "",
            "earnings_quality_analysis": "",
            "pricing_power_analysis": "",
            "secondary_red_flags": ""
        }
    except Exception as e:
        logging.warning(f"Error parsing {symbol}: {e}")
        return None

def evaluate_framework(d):
    """Executes the STRICT Dual-Track Logic with Null Validation and Math Safeguards."""
    
    # 1. PHASE 1: THE UNIVERSAL SURVIVAL GATE
    gate_failures = []
    
    # SAFEGUARD: The Penny Stock / Nano-Cap Trap
    if d["sales_y1"] < 50.0:
        gate_failures.append(f"Nano-Cap Risk (Sales ₹{d['sales_y1']}Cr < ₹50Cr)")
        
    # SAFEGUARD: Bankruptcy Risk (Requires operating profit to be positive)
    if d["interest_y1"] > 0:
        if d["operating_profit_y1"] <= 0:
            gate_failures.append("Bankruptcy Risk (Negative Operating Profit with Debt)")
        elif (d["operating_profit_y1"] / d["interest_y1"]) < 2.0:
            gate_failures.append("Bankruptcy Risk (Interest Coverage < 2.0)")

    # SAFEGUARD: The Dilution Trap (Strict Null Check)
    if d["share_capital_y2"] > 0 and d["share_capital_y1"] > (d["share_capital_y2"] * 1.05):
        gate_failures.append("The Dilution Trap")
        
    # SAFEGUARD: Skin in the game (Strict Null Check)
    if d["promoter_q1"] > 0 and d["promoter_q1"] < 40.0:
        gate_failures.append("No Skin in the Game")
        
    # SAFEGUARD: Market Cap Ceiling
    if d["market_cap"] > 5000:
        gate_failures.append("Market Cap exceeds 5000Cr")
        
    # SAFEGUARD: Fake Profits 
    if d["operating_profit_y1"] > 0 and d["other_income_y1"] > d["operating_profit_y1"]:
        gate_failures.append("Other Income exceeds Core Profit")
        
    if len(gate_failures) > 0:
        return "REJECTED", f"Failed Survival Gate: {'; '.join(gate_failures)}"
        
    # 2. PHASE 2 / TRACK A: THE EARLY INFLECTION ENGINE
    ta_triggers = 0
    ta_details = []
    
    # SAFEGUARD: Factory Go-Live (Prevents zero-division)
    if d["fixed_assets_y2"] > 0 and d["cwip_y2"] > (d["fixed_assets_y2"] * 0.10):
        if d["cwip_y1"] < (d["cwip_y2"] * 0.50) and d["fixed_assets_y1"] > d["fixed_assets_y2"]:
            ta_triggers += 1
            ta_details.append("Factory Go-Live")
            
    # SAFEGUARD: Working Capital Squeeze (Correctly handles FMCG negative cash cycles)
    if d["ccc_y2"] != 0:
        if (d["ccc_y2"] > 0 and d["ccc_y1"] < (d["ccc_y2"] * 0.80)) or (d["ccc_y2"] < 0 and d["ccc_y1"] < (d["ccc_y2"] * 1.20)):
            ta_triggers += 1
            ta_details.append("Working Capital Squeeze")
            
    # SAFEGUARD: Margin Turnaround (Requires historical reporting)
    if d["opm_y2"] > 0 and d["sales_y2"] > 0:
        if d["opm_y1"] > (d["opm_y2"] * 1.20) and d["sales_y1"] >= (d["sales_y2"] * 0.95):
            ta_triggers += 1
            ta_details.append("Margin Turnaround")
            
    # SAFEGUARD: Smart Money Creep (Requires history to calculate a "creep")
    sm_q1 = d["promoter_q1"] + d["fii_q1"] + d["dii_q1"]
    sm_q2 = d["promoter_q2"] + d["fii_q2"] + d["dii_q2"]
    if sm_q2 > 0 and sm_q1 > sm_q2:
        ta_triggers += 1
        ta_details.append("Smart Money Creep")
        
    # SAFEGUARD: Supplier Squeeze (Pricing Power check)
    if d["days_payable_y2"] > 0 and d["days_payable_y1"] > (d["days_payable_y2"] * 1.15) and d["debtor_days_y1"] < d["debtor_days_y2"]:
        ta_triggers += 1
        ta_details.append("Supplier Squeeze")
        
    track_a_pass = ta_triggers >= 2

    # 3. PHASE 2 / TRACK B: THE PROVEN COMPOUNDER ENGINE
    tb_triggers = 0
    tb_details = []
    
    if d["roce_y1"] > 20.0 and d["roce_y2"] > 20.0:
        tb_triggers += 1
        tb_details.append("Elite ROCE")
        
    # SAFEGUARD: Sustained Top-Line Growth (Strict Null Check)
    if d["sales_y3"] > 0 and d["sales_y2"] > 0:
        if d["sales_y1"] > (d["sales_y2"] * 1.15) and d["sales_y2"] > (d["sales_y3"] * 1.15):
            tb_triggers += 1
            tb_details.append("Top-Line Growth")
            
    # SAFEGUARD: Operating Leverage (Ensures Net Profit was actually positive last year to avoid negative math anomalies)
    sales_growth = (d["sales_y1"] / d["sales_y2"]) if d["sales_y2"] > 0 else 0
    profit_growth = (d["net_profit_y1"] / d["net_profit_y2"]) if d["net_profit_y2"] > 0 else 0
    if sales_growth > 0 and d["net_profit_y2"] > 0 and d["net_profit_y1"] > 0:
        if profit_growth > sales_growth:
            tb_triggers += 1
            tb_details.append("Operating Leverage")
            
    # SAFEGUARD: Immaculate Cash Conversion (Ensures Net Profit is positive to avoid false flags on negative CFO)
    if d["net_profit_y1"] > 0 and d["cfo_y1"] > (d["net_profit_y1"] * 0.70):
        tb_triggers += 1
        tb_details.append("Immaculate Cash Conversion")
        
    if d["dividend_yield"] > 0.0:
        tb_triggers += 1
        tb_details.append("Dividend Validation")

    # SAFEGUARD: Valuation Check (Downgrades if too expensive)
    if d["stock_pe"] > 70:
        track_b_pass = False 
    else:
        track_b_pass = tb_triggers >= 3

    # 4. ROUTING CLASSIFICATION
    if track_a_pass and track_b_pass:
        return "HYPER-COMPOUNDER", f"Both Tracks. Fired: {', '.join(ta_details + tb_details)}"
    elif track_a_pass:
        return "EARLY INFLECTION", f"Track A. Fired: {', '.join(ta_details)}"
    elif track_b_pass:
        return "PROVEN COMPOUNDER", f"Track B. Fired: {', '.join(tb_details)}"
    else:
        return "STAGNANT", "Passed survival checks but failed growth thresholds."

def generate_qualitative_analysis(d):
    """Executes post-qualification analysis on the salvaged data to provide deep context."""
    
    # 1. Valuation Context
    pe = d["stock_pe"]
    mc = d["market_cap"]
    mc_tag = f"Large/Mid-Cap (₹{mc}Cr)" if mc > 5000 else f"Micro/Small-Cap (₹{mc}Cr)"
    
    if pe > 70:
        d["valuation_context"] = f"{mc_tag} priced for perfection (PE: {pe}). The easy multi-bagger money may have been made."
    elif 0 < pe <= 20:
        d["valuation_context"] = f"{mc_tag} trading at deep value (PE: {pe}). High margin of safety."
    elif pe == 0:
        d["valuation_context"] = f"{mc_tag} trading with zero/calculable PE."
    else:
        d["valuation_context"] = f"{mc_tag} trading at fair/standard multiple (PE: {pe})."

    # 2. Quality of Earnings (Other Income Check)
    op = d["operating_profit_y1"]
    other_inc = d["other_income_y1"]
    np = d["net_profit_y1"]
    
    if other_inc > op and op > 0:
        d["earnings_quality_analysis"] = f"CRITICAL WARNING: Other Income (₹{other_inc}Cr) exceeds Core Operating Profit (₹{op}Cr). Profits are highly engineered."
    elif other_inc > (np * 0.3):
        d["earnings_quality_analysis"] = "CAUTION: >30% of Net Profit stems from Other Income. Verify asset sales."
    elif d["dividend_yield"] > 0:
        d["earnings_quality_analysis"] = f"Elite Quality: Earnings are clean and validated by a {d['dividend_yield']}% hard-cash dividend."
    else:
        d["earnings_quality_analysis"] = "Standard Quality: Earnings driven by core operations. No dividend paid."

    # 3. Pricing Power (Supplier Squeeze Check)
    dp_y1 = d["days_payable_y1"]
    dp_y2 = d["days_payable_y2"]
    dd_y1 = d["debtor_days_y1"]
    dd_y2 = d["debtor_days_y2"]
    
    if dp_y1 > (dp_y2 * 1.15) and dd_y1 < dd_y2:
        d["pricing_power_analysis"] = "EXTREME PRICING POWER: Forcing suppliers to wait longer for payment while forcing clients to pay cash faster."
    elif dd_y1 > (dd_y2 * 1.25):
        d["pricing_power_analysis"] = "WARNING: Receivables piling up. Company is giving away free credit to drive sales."
    else:
        d["pricing_power_analysis"] = "Neutral: Trade working capital is stable."

    # 4. Secondary Red Flags
    flags = []
    if d["borrowings_y1"] > d["fixed_assets_y1"] and d["fixed_assets_y1"] > 0:
        flags.append("Debt exceeds hard physical assets")
    if d["promoter_q1"] < 50.0:
        flags.append(f"Promoter holding is passable but low ({d['promoter_q1']}%)")
    if d["cfo_y1"] < 0:
        flags.append("Passed survival, but currently burning operating cash flow")
        
    d["secondary_red_flags"] = " | ".join(flags) if flags else "No glaring secondary red flags detected."
    return d

def save_to_db(d):
    """Saves ALL extracted data (core + context) to the persistent SQLite database."""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    # Constructing a dynamic SQL insert based on dict keys
    columns = ', '.join(d.keys())
    placeholders = ':' + ', :'.join(d.keys())
    query = f"INSERT OR REPLACE INTO scraped_metrics ({columns}) VALUES ({placeholders})"
    cursor.execute(query, d)
    conn.commit()
    conn.close()

def save_ticker_csv(d):
    """Saves the individual stock's scraped data into its dedicated directory with a timestamp."""
    ticker = d["ticker"]
    # Safely strip out special characters so we don't crash the OS folder creation
    safe_ticker = "".join([c for c in ticker if c.isalnum() or c in ['_', '-']]).rstrip()
    ticker_dir = os.path.join(SCREENER_DATA_DIR, safe_ticker)
    
    # Automatically creates ScreenerData/TICKER/ if it doesn't exist
    os.makedirs(ticker_dir, exist_ok=True)
    
    ticker_file = os.path.join(ticker_dir, f"{safe_ticker}_{RUN_DATE}.csv")
    df = pd.DataFrame([d])
    df.to_csv(ticker_file, index=False)

def send_telegram_alert(new_stocks, df_out):
    """Handles Telegram chunking and seamlessly logs the history for future AI review."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        logging.warning("Telegram credentials not found. Skipping alert.")
        return

    # Build the core message text
    msg = f"📊 *Microcap Screener Weekly Update ({RUN_DATE})*\n\n"
    if not new_stocks:
        msg += "No new candidates passed the framework this week."
    else:
        msg += f"🚨 *{len(new_stocks)} NEW CANDIDATES DETECTED* 🚨\n\n"
        for stock in new_stocks:
            ticker = stock['ticker']
            classif = stock['classification']
            reason = stock['qualification_reason']
            msg += f"🔥 *{ticker}* ({classif})\n_Reason:_ {reason}\n\n"

    # Save the generated telegram analysis output directly into the Git repository context
    try:
        with open(TELEGRAM_LOG_FILE, "a", encoding="utf-8") as f:
            f.write(f"\n--- Analysis Log Generated at {time.strftime('%Y-%m-%d %H:%M:%S')} ---\n")
            f.write(msg + "\n")
    except Exception as e:
        logging.warning(f"Could not save telegram log to local directory: {e}")

    # Telegram Chunking Logic: Max character limit for sendMessage is 4096. 
    # We chunk at 4000 to be perfectly safe.
    max_msg_length = 4000
    msg_chunks = [msg[i:i + max_msg_length] for i in range(0, len(msg), max_msg_length)]
    
    send_msg_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    for chunk in msg_chunks:
        try:
            response = requests.post(
                send_msg_url,
                data={"chat_id": TELEGRAM_CHAT_ID, "text": chunk, "parse_mode": "Markdown"}
            )
            response.raise_for_status()
        except Exception as e:
            logging.error(f"Failed to send Telegram message chunk: {e}")

    # Send Document endpoint (Captions are limited to 1024 characters)
    send_doc_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendDocument"
    
    csv_buffer = io.BytesIO()
    df_out.to_csv(csv_buffer, index=False)
    csv_buffer.name = f"Qualified_Microcaps_{RUN_DATE}.csv"
    csv_buffer.seek(0)

    try:
        response = requests.post(
            send_doc_url, 
            data={"chat_id": TELEGRAM_CHAT_ID, "caption": f"📄 *Attached: Weekly Qualified List ({RUN_DATE})*", "parse_mode": "Markdown"}, 
            files={"document": csv_buffer}
        )
        response.raise_for_status()
        logging.info("Telegram alert and CSV payload sent successfully.")
    except Exception as e:
        logging.error(f"Failed to send Telegram document: {e}")

def process_worker(symbol):
    """The multithreaded worker function. Keeps the sleep inside the thread for desynchronized jitter."""
    # Desynchronized Human Jitter (1.5 to 3.5 seconds)
    # Because each thread sleeps independently, requests are safely staggered.
    time.sleep(random.uniform(1.5, 3.5)) 
    
    data = scrape_stock(symbol)
    if not data:
        return None
        
    classification, reason = evaluate_framework(data)
    data["classification"] = classification
    data["qualification_reason"] = reason
    
    # Run the deep-dive textual analysis if it passes
    if classification in ["EARLY INFLECTION", "PROVEN COMPOUNDER", "HYPER-COMPOUNDER"]:
        data = generate_qualitative_analysis(data)
        
    return data

def main():
    init_db()
    symbols = fetch_nse_symbols()
    
    if not symbols:
        logging.error("No valid symbols found. Exiting.")
        return

    # Dynamic Historical Audit Checking
    # Use glob to scan the folder and automatically locate the most recent past CSV run
    previous_tickers = set()
    existing_csvs = glob.glob(os.path.join(SCREENER_DATA_DIR, "qualified_stocks_analysis_*.csv"))
    
    # Exclude today's file to prevent a self-matching collision if re-run on the same day
    existing_csvs = [f for f in existing_csvs if not f.endswith(f"_{RUN_DATE}.csv")]
    
    if existing_csvs:
        latest_csv = max(existing_csvs, key=os.path.getmtime)
        try:
            prev_df = pd.read_csv(latest_csv)
            previous_tickers = set(prev_df['ticker'].tolist())
            logging.info(f"Loaded {len(previous_tickers)} previous candidates from {os.path.basename(latest_csv)} for historical comparison.")
        except Exception as e:
            logging.warning(f"Failed to load previous CSV ({latest_csv}): {e}")

    qualified_records = []
    new_candidates = []

    # Configure the Thread Pool (Throttled carefully at 4 workers)
    MAX_CONCURRENT_THREADS = 4
    logging.info(f"Initiating throttled multithreading with {MAX_CONCURRENT_THREADS} workers...")

    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_CONCURRENT_THREADS) as executor:
        # Submit all tasks to the thread pool
        future_to_symbol = {executor.submit(process_worker, sym): sym for sym in symbols}
        
        # Process results as they complete
        for idx, future in enumerate(concurrent.futures.as_completed(future_to_symbol)):
            symbol = future_to_symbol[future]
            try:
                data = future.result()
                if data:
                    # Write to database and file system sequentially in the main thread 
                    # to prevent SQLite locking errors and racing folder creation
                    save_to_db(data)
                    save_ticker_csv(data)
                    
                    classif = data["classification"]
                    if classif in ["EARLY INFLECTION", "PROVEN COMPOUNDER", "HYPER-COMPOUNDER"]:
                        qualified_records.append(data)
                        logging.info(f">>> MATCH: {symbol} classified as {classif}")
                        
                        # Check if it's a net-new addition this week
                        if symbol not in previous_tickers:
                            new_candidates.append(data)
                            
            except Exception as exc:
                logging.warning(f"{symbol} generated an exception: {exc}")
                
            if idx % 100 == 0 and idx > 0:
                logging.info(f"Progress: Processed {idx}/{len(symbols)} tickers...")

    # Output ONLY verified candidate records to the global tracking CSV file
    if qualified_records:
        df_out = pd.DataFrame(qualified_records)
        
        # Organize the CSV layout so textual context columns are front-and-center
        front_cols = [
            'ticker', 'classification', 'market_cap', 'valuation_context',
            'earnings_quality_analysis', 'pricing_power_analysis',
            'secondary_red_flags', 'qualification_reason'
        ]
        back_cols = [c for c in df_out.columns if c not in front_cols]
        df_out = df_out[front_cols + back_cols]
        
        df_out.to_csv(CSV_FILE, index=False)
        logging.info(f"Analysis complete. {len(qualified_records)} candidate(s) saved to '{CSV_FILE}'.")
        
        # Trigger the Telegram Push
        send_telegram_alert(new_candidates, df_out)
    else:
        logging.info("Process completed. No listings matching the metrics were discovered.")

if __name__ == "__main__":
    main()
