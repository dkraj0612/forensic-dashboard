import os
import io
import re
import time
import random
import sqlite3
import csv
import logging
import requests
from bs4 import BeautifulSoup
import pandas as pd

# Setup logging configuration
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# Configuration constants
DB_FILE = "screener_data.db"
CSV_FILE = "qualified_stocks_analysis.csv"
NSE_URL = "https://archives.nseindia.com/content/equities/EQUITY_L.csv"
SCREENER_BASE_URL = "https://www.screener.in/company/{}/consolidated/"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}

def init_db():
    """Initializes the SQLite database schema including the salvaged top-card data."""
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
    """Finds specific table rows inside the P&L, Balance Sheet, and Ratios tables."""
    table = soup.find(id=table_id)
    if not table:
        return []
    
    rows = table.find_all("tr")
    for row in rows:
        cells = row.find_all(["td", "th"])
        if cells and row_label.lower() in cells[0].text.lower():
            return [clean_value(c.text) for c in cells[1:]]
    return []

def extract_top_ratio(soup, metric_name):
    """Extracts unstructured top-card metrics (Market Cap, P/E, Div Yield) using regex."""
    try:
        name_span = soup.find('span', class_='name', string=re.compile(metric_name, re.IGNORECASE))
        if name_span:
            val_span = name_span.find_next_sibling('span', class_='number')
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
        
        share_cap = get_metric("balance-sheet", "Share Capital", 2)
        borrowings = get_metric("balance-sheet", "Borrowings", 2)
        fixed_assets = get_metric("balance-sheet", "Fixed Assets", 2)
        cwip = get_metric("balance-sheet", "Capital Work in Progress", 2)
        
        cfo = get_metric("cash-flow", "Cash from Operating Activity", 1)
        
        roce = get_metric("ratios", "ROCE", 2)
        ccc = get_metric("ratios", "Cash Conversion Cycle", 2)
        
        promoter = get_metric("shareholding", "Promoters", 2)
        fii = get_metric("shareholding", "FIIs", 2)
        dii = get_metric("shareholding", "DIIs", 2)

        # Extra Salvaged Data
        other_income_years = get_metric("profit-loss", "Other Income", 1)
        debtor_days = get_metric("ratios", "Debtor Days", 2)
        payable_days = get_metric("ratios", "Payable Days", 2)

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
    """Executes the STRICT Dual-Track Logic EXACTLY as specified in the prompt."""
    
    # 1. PHASE 1: THE UNIVERSAL SURVIVAL GATE
    gate_failures = []
    if d["interest_y1"] > 0 and (d["operating_profit_y1"] / d["interest_y1"]) < 2.0:
        gate_failures.append("Bankruptcy Risk")
    if d["share_capital_y1"] > (d["share_capital_y2"] * 1.05):
        gate_failures.append("The Dilution Trap")
    if d["promoter_q1"] < 40.0:
        gate_failures.append("No Skin in the Game")
        
    if len(gate_failures) > 0:
        return "REJECTED", f"Failed Survival Gate: {'; '.join(gate_failures)}"
        
    # 2. PHASE 2 / TRACK A: THE EARLY INFLECTION ENGINE
    ta_triggers = 0
    ta_details = []
    
    if d["cwip_y2"] > (d["fixed_assets_y2"] * 0.10) and d["cwip_y1"] < (d["cwip_y2"] * 0.50) and d["fixed_assets_y1"] > d["fixed_assets_y2"]:
        ta_triggers += 1
        ta_details.append("Factory Go-Live Trigger")
    if d["ccc_y2"] > 0 and d["ccc_y1"] < (d["ccc_y2"] * 0.80):
        ta_triggers += 1
        ta_details.append("Working Capital Squeeze Trigger")
    if d["opm_y1"] > (d["opm_y2"] * 1.20) and d["sales_y1"] >= (d["sales_y2"] * 0.95):
        ta_triggers += 1
        ta_details.append("Margin Turnaround Trigger")
        
    sm_q1 = d["promoter_q1"] + d["fii_q1"] + d["dii_q1"]
    sm_q2 = d["promoter_q2"] + d["fii_q2"] + d["dii_q2"]
    if sm_q1 > sm_q2:
        ta_triggers += 1
        ta_details.append("Smart Money Creep Trigger")
        
    track_a_pass = ta_triggers >= 2

    # 3. PHASE 2 / TRACK B: THE PROVEN COMPOUNDER ENGINE
    tb_triggers = 0
    tb_details = []
    
    if d["roce_y1"] > 20.0 and d["roce_y2"] > 20.0:
        tb_triggers += 1
        tb_details.append("Elite Capital Efficiency Trigger")
    if d["sales_y1"] > (d["sales_y2"] * 1.15) and d["sales_y2"] > (d["sales_y3"] * 1.15):
        tb_triggers += 1
        tb_details.append("Sustained Top-Line Growth Trigger")
        
    sales_growth = (d["sales_y1"] / d["sales_y2"]) if d["sales_y2"] > 0 else 0
    profit_growth = (d["net_profit_y1"] / d["net_profit_y2"]) if d["net_profit_y2"] > 0 else 0
    if profit_growth > sales_growth and sales_growth > 0:
        tb_triggers += 1
        tb_details.append("Operating Leverage Trigger")
        
    if d["cfo_y1"] > (d["net_profit_y1"] * 0.70):
        tb_triggers += 1
        tb_details.append("Immaculate Cash Conversion Trigger")
        
    track_b_pass = tb_triggers >= 3

    # Route classification logic
    if track_a_pass and track_b_pass:
        return "HYPER-COMPOUNDER", f"Passed Both Tracks. Fired: {', '.join(ta_details + tb_details)}"
    elif track_a_pass:
        return "EARLY INFLECTION", f"Passed Track A. Fired: {', '.join(ta_details)}"
    elif track_b_pass:
        return "PROVEN COMPOUNDER", f"Passed Track B. Fired: {', '.join(tb_details)}"
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

def main():
    init_db()
    symbols = fetch_nse_symbols()
    
    if not symbols:
        logging.error("No valid symbols found. Exiting.")
        return

    qualified_records = []

    for idx, symbol in enumerate(symbols):
        # Introducing 'Human Jitter' (2-4 seconds) to avoid Screener Cloudflare bans during CI/CD runs
        time.sleep(random.uniform(2.1, 4.1)) 
        
        if idx % 100 == 0 and idx > 0:
            logging.info(f"Progress: Processed {idx}/{len(symbols)} tickers...")

        data = scrape_stock(symbol)
        if not data:
            continue
            
        classification, reason = evaluate_framework(data)
        data["classification"] = classification
        data["qualification_reason"] = reason
        
        # If the stock passes the gate, run the deep-dive textual analysis
        if classification in ["EARLY INFLECTION", "PROVEN COMPOUNDER", "HYPER-COMPOUNDER"]:
            data = generate_qualitative_analysis(data)
            qualified_records.append(data)
            logging.info(f">>> MATCH: {symbol} classified as {classification}")
            
        # Write ALL data (Rejected and Passed) to the local database
        save_to_db(data)

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
    else:
        logging.info("Process completed. No listings matching the metrics were discovered.")

if __name__ == "__main__":
    main()
