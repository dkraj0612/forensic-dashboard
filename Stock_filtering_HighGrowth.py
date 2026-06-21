import os
import io
import re
import time
import random
import sqlite3
import csv
import json
import logging
import requests
import concurrent.futures
import glob
import pandas as pd
from datetime import datetime, timedelta
from bs4 import BeautifulSoup
import fitz  # PyMuPDF for in-memory PDF extraction
import google.generativeai as genai # AI Structural Compiler

# ---------------------------------------------------------
# GLOBAL TTL CLOCK: Start the stopwatch at script boot
# ---------------------------------------------------------
SCRIPT_START_TIME = time.time()
MAX_RUNTIME_SEC = 5.45 * 3600  # Graceful abort at 5.45 hours

# Setup logging configuration
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# Configuration constants & Dynamic Date Stamping
RUN_DATE = time.strftime("%Y-%m-%d")
SCREENER_DATA_DIR = "ScreenerData"

DB_FILE = os.path.join(SCREENER_DATA_DIR, "screener_data.db")
CSV_FILE = os.path.join(SCREENER_DATA_DIR, f"qualified_stocks_analysis_{RUN_DATE}.csv")
TELEGRAM_LOG_FILE = os.path.join(SCREENER_DATA_DIR, f"telegram_analysis_log_{RUN_DATE}.txt")
MANIFEST_FILE = os.path.join(SCREENER_DATA_DIR, "manifest.json")
RUN_CACHE_FILE = os.path.join(SCREENER_DATA_DIR, f"run_cache_{RUN_DATE}.json") # Daily Checkpoint

NSE_URL = "https://archives.nseindia.com/content/equities/EQUITY_L.csv"
SCREENER_BASE_URL = "https://www.screener.in/company/{}/consolidated/"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}

# API Credentials
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)

def safe_request(url, retries=3):
    """Executes network requests with random jitter and exponential backoff to evade bot detection."""
    for attempt in range(retries):
        time.sleep(random.uniform(1.2, 2.5))
        try:
            response = requests.get(url, headers=HEADERS, timeout=15)
            if response.status_code == 200:
                return response
            elif response.status_code == 429:
                sleep_time = (attempt + 1) * 10
                logging.warning(f"Rate limited (429) on {url}. Sleeping for {sleep_time}s...")
                time.sleep(sleep_time)
            elif response.status_code == 404:
                return response
        except requests.exceptions.RequestException as e:
            logging.warning(f"Request failed for {url}: {e}. Retrying...")
            time.sleep(2)
    return None

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
        response = safe_request(NSE_URL)
        if not response:
            return []
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

def get_dynamic_table(soup, possible_ids):
    """Finds a table by trying multiple possible IDs (Fallbacks for SME/IPO stocks)."""
    for table_id in possible_ids:
        section = soup.find('section', id=table_id)
        if section and section.find('table'):
            return section.find('table')
    return None

def extract_row_values(soup, possible_ids, row_label):
    """Finds specific table rows and strictly aligns time periods by stripping 'TTM'."""
    table = get_dynamic_table(soup, possible_ids)
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

def vacuum_screener_data(soup):
    """
    Sweeps the entire Screener page and formats it vertically.
    Bypasses strict tbody HTML formatting to catch all tables (High/Low, Peers, CAGR).
    Uses ID-aware fallbacks for missing/renamed tables on new listings.
    """
    rows = []
    
    try:
        profile_div = soup.find('div', class_='company-profile')
        if profile_div:
            about_div = profile_div.find('div', class_='about')
            if about_div:
                rows.append(["--- ABOUT ---"])
                rows.append([about_div.get_text(separator=' ', strip=True)])
                rows.append([])
            
            key_points_div = profile_div.find('div', class_='company-profile-notes') 
            if not key_points_div: 
                key_points_div = profile_div.find('div', class_='key-points')
                
            if key_points_div:
                rows.append(["--- KEY POINTS ---"])
                rows.append([key_points_div.get_text(separator=' ', strip=True)])
                rows.append([])

        top_ratios = soup.find('ul', id='top-ratios')
        if top_ratios:
            rows.append(["--- STOCK INFO ---"])
            info_row_names = []
            info_row_vals = []
            for li in top_ratios.find_all('li'):
                name_span = li.find('span', class_='name')
                val_span = li.find('span', class_='value') 
                if name_span and val_span:
                    info_row_names.append(name_span.get_text(strip=True))
                    # Keeps the " / " for High/Low but safely strips out currency symbols
                    val_text = val_span.get_text(separator=' ', strip=True).replace('₹', '').replace('Cr.', '').replace('%', '').strip()
                    info_row_vals.append(val_text)
                    
                if len(info_row_names) == 4:
                    rows.append(info_row_names)
                    rows.append(info_row_vals)
                    rows.append([])
                    info_row_names = []
                    info_row_vals = []
            
            if info_row_names:
                rows.append(info_row_names)
                rows.append(info_row_vals)
                rows.append([])
                
        sections = [
            ("PEER COMPARISON", ["peers"]),
            ("QUARTERLY RESULTS", ["quarters", "half-years", "results"]),
            ("ANNUAL RESULTS", ["profit-loss"]),
            ("BALANCE SHEET", ["balance-sheet"]),
            ("CASH FLOW", ["cash-flow"]),
            ("RATIOS", ["ratios"]),
            ("SHAREHOLDING PATTERN", ["shareholding"])
        ]
        
        for title, possible_ids in sections:
            table = get_dynamic_table(soup, possible_ids)
            if table:
                rows.append([f"--- {title} ---"])
                for tr in table.find_all('tr'):
                    td_row = [td.get_text(separator=' ', strip=True) for td in tr.find_all(['td', 'th'])]
                    rows.append(td_row)
                rows.append([])
                    
        pl_section = soup.find('section', id='profit-loss')
        if pl_section:
            cagr_tables = pl_section.find_all('table', class_='ranges-table')
            if cagr_tables:
                rows.append(["--- CAGR BOXES ---"])
                for tbl in cagr_tables:
                    for tr in tbl.find_all('tr'):
                        rows.append([td.get_text(separator=' ', strip=True) for td in tr.find_all(['th', 'td'])])
                    rows.append([])

    except Exception as e:
        logging.warning(f"Error during vacuum phase: {e}")
        
    return rows

def filter_volatile_data(csv_rows):
    """Strips the volatile 'STOCK INFO', 'PEER', and 'CAGR' blocks from CSV representation."""
    filtered = []
    skip = False
    for row in csv_rows:
        if not row or (len(row) == 1 and str(row[0]).strip() == ""):
            continue
            
        first_col = str(row[0]).strip()
        
        if first_col.startswith("--- ") and first_col.endswith(" ---"):
            if first_col in ["--- STOCK INFO ---", "--- PEER COMPARISON ---", "--- CAGR BOXES ---"]:
                skip = True
                continue
            else:
                skip = False
                
        if not skip:
            filtered.append("|".join([str(item).strip() for item in row]))
            
    return filtered

# =====================================================================
# THE 3-TIER INTELLIGENT CONCALL PARSER
# =====================================================================

def get_layout_signature(ticker, sample_text, concall_dir):
    """Tier 1 & 3: Retrieves cached signature or queries AI for structural layout."""
    signature_file = os.path.join(concall_dir, "layout_signature.json")
    
    if os.path.exists(signature_file):
        try:
            with open(signature_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            pass

    if not GEMINI_API_KEY:
        logging.warning("No AI API Key found. Skipping intelligent parsing generation.")
        return None

    logging.info(f"Generating new AI Layout Signature for {ticker}...")
    prompt = f"""
    You are a data architect. Analyze this excerpt from an earnings call transcript and determine its layout physics.
    Return ONLY a JSON object (no markdown, no explanations) with this exact schema:
    {{
        "delimiter_type": "inline_metadata" | "standalone_line",
        "speaker_split_character": "character used to split speaker from dialogue, or null",
        "line_length_threshold_for_speaker": integer (max length of a line to be considered a speaker name),
        "roster_hints": ["List", "Of", "Names", "Found"]
    }}
    
    Transcript Sample:
    {sample_text[:4000]}
    """
    
    try:
        model = genai.GenerativeModel('gemini-2.5-flash-lite')
        
        # --- NEW API RATE LIMIT DEFENSE ---
        response = None
        max_retries = 4
        for attempt in range(max_retries):
            try:
                response = model.generate_content(prompt)
                break  # Success! Break out of the retry loop.
            except Exception as api_err:
                err_str = str(api_err).lower()
                if "429" in err_str or "quota" in err_str or "exhausted" in err_str:
                    # The free tier is locked to 15 RPM. A 60-second sleep guarantees the window resets.
                    sleep_time = 60 + (attempt * 10) 
                    logging.warning(f"Gemini API Free Tier Limit Hit for {ticker}. Pausing AI for {sleep_time}s... (Attempt {attempt+1}/{max_retries})")
                    time.sleep(sleep_time)
                else:
                    raise api_err # If it's a different error, raise it normally
                    
        if not response:
            logging.error(f"Failed to get AI response for {ticker} after {max_retries} rate limit retries.")
            return None
        # ----------------------------------
        
        match = re.search(r'\{.*\}', response.text, re.DOTALL)
        if match:
            clean_json = match.group(0)
            signature = json.loads(clean_json)
            with open(signature_file, 'w', encoding='utf-8') as f:
                json.dump(signature, f, indent=4)
            return signature
        else:
            logging.error(f"Failed to find valid JSON structure in AI response for {ticker}.")
            return None
            
    except Exception as e:
        logging.error(f"Failed to generate layout signature for {ticker}: {e}")
        return None

def parse_transcript_dynamically(full_text, signature):
    """Tier 2: The Adaptive Python Engine utilizing the AI's blueprint."""
    if not signature:
        return {"raw_text": full_text[:1000] + "... [UNPARSED]"}

    lines = full_text.split('\n')
    parsed_dialogue = []
    current_speaker = "Unknown"
    current_dialogue = []

    delim_type = signature.get("delimiter_type", "inline_metadata")
    split_char = signature.get("speaker_split_character")
    threshold = signature.get("line_length_threshold_for_speaker", 50)
    roster = signature.get("roster_hints", [])

    def is_roster_match(text):
        if not roster: return False
        text_clean = text.lower().strip()
        return any(hint.lower() in text_clean for hint in roster)

    for line in lines:
        line_str = line.strip()
        if not line_str: continue
        if "Page" in line_str and len(line_str) < 15: continue

        state_changed = False

        if delim_type == "standalone_line":
            if len(line_str) <= threshold and (line_str.isupper() or is_roster_match(line_str)):
                if current_dialogue:
                    parsed_dialogue.append({"speaker": current_speaker, "dialogue": " ".join(current_dialogue)})
                current_speaker = line_str
                current_dialogue = []
                state_changed = True
                
        elif delim_type == "inline_metadata" and split_char:
            if split_char in line_str and len(line_str.split(split_char)[0]) <= threshold:
                parts = line_str.split(split_char, 1)
                if current_dialogue:
                    parsed_dialogue.append({"speaker": current_speaker, "dialogue": " ".join(current_dialogue)})
                current_speaker = parts[0].strip()
                current_dialogue = [parts[1].strip()]
                state_changed = True

        if not state_changed:
            current_dialogue.append(line_str)

    if current_dialogue:
        parsed_dialogue.append({"speaker": current_speaker, "dialogue": " ".join(current_dialogue)})

    return {"transcript": parsed_dialogue}

def process_concalls(ticker, html_text, ticker_dir):
    """
    Downloads concall PDFs using pure Regex/JSON extraction with a strict 5-year boundary.
    Applies Option 2: Pre-Download Naming & Validation via Regex.
    """
    concall_dir = os.path.join(ticker_dir, "concalls")
    os.makedirs(concall_dir, exist_ok=True)

    transcript_links = {}
    target_date_boundary = datetime.now() - timedelta(days=5*365)
    
    # Pre-load existing URLs to avoid duplicate network downloads
    existing_urls = set()
    for existing_file in glob.glob(os.path.join(concall_dir, "*.json")):
        if "layout_signature" in existing_file: continue
        try:
            with open(existing_file, 'r', encoding='utf-8') as f:
                cached_data = json.load(f)
                if 'metadata' in cached_data and 'source_url' in cached_data['metadata']:
                    existing_urls.add(cached_data['metadata']['source_url'])
        except Exception:
            pass

    # Option 4 Phase A: The Backend JSON Pluck 
    json_match = re.search(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', html_text, re.DOTALL)
    if json_match:
        try:
            data = json.loads(json_match.group(1))
            
            def search_json_for_transcripts(obj, current_date="UnknownQuarter"):
                if isinstance(obj, dict):
                    if 'date' in obj:
                        current_date = str(obj['date']).replace(" ", "_")
                        # 5-Year Filter
                        try:
                            date_obj = datetime.strptime(current_date[:10], '%Y-%m-%d')
                            if date_obj < target_date_boundary:
                                return # Break execution for this deep branch, it's too old
                        except Exception:
                            pass
                            
                    if obj.get('name', '').lower() == 'transcript' and 'url' in obj:
                        transcript_links[current_date] = obj['url']
                        
                    for v in obj.values():
                        search_json_for_transcripts(v, current_date)
                elif isinstance(obj, list):
                    for item in obj:
                        search_json_for_transcripts(item, current_date)
                        
            search_json_for_transcripts(data)
        except Exception as e:
            logging.warning(f"JSON Parsing failed for {ticker}: {e}. Falling back to source regex.")

    # Option 4 Phase B: Raw Source Regex Fallback (If JSON fails)
    if not transcript_links:
        doc_match = re.search(r'id="documents"(.*?)</section>', html_text, re.IGNORECASE | re.DOTALL)
        if doc_match:
            doc_html = doc_match.group(1)
            blocks = re.split(r'<li|<div class="flex', doc_html)
            for idx, block in enumerate(blocks):
                if 'transcript' in block.lower():
                    urls = re.findall(r'href="([^"]+)"', block)
                    for link in urls:
                        if '.pdf' in link.lower() or 'concall' in link.lower() or 'transcript' in link.lower():
                            transcript_links[f"Fallback_Transcript_{idx}"] = link
                            break 

    expected_count = len(transcript_links)
    success_count = 0

    for fallback_date, pdf_url in transcript_links.items():
        if pdf_url.startswith("/"):
            pdf_url = "https://www.screener.in" + pdf_url

        # Check Cache to bypass network entirely
        if pdf_url in existing_urls:
            success_count += 1
            continue

        logging.info(f"Downloading transcript for {ticker}...")
        res = safe_request(pdf_url)
        if not res or res.status_code != 200: 
            continue
            
        # 3-Step Guarantee: MIME and Size constraints to catch hidden 404 pages
        if 'application/pdf' not in res.headers.get('Content-Type', '').lower():
            continue
        if len(res.content) < 5120: # 5KB check
            continue

        try:
            pdf_stream = io.BytesIO(res.content)
            
            with fitz.open(stream=pdf_stream, filetype="pdf") as doc:
                if len(doc) == 0: continue
                
                # Option 2 Naming Strategy: First-page Regex Extract
                first_page_text = doc[0].get_text("text")
                quarter_match = re.search(r'(Q[1-4]\s*FY\d{2,4})', first_page_text, re.IGNORECASE)
                
                if quarter_match:
                    quarter_str = quarter_match.group(1).replace(" ", "_").upper()
                else:
                    quarter_str = fallback_date.replace("-", "_")

                safe_title = "".join([c for c in quarter_str if c.isalnum() or c in ['_', '-']])[:100]
                json_filename = os.path.join(concall_dir, f"{ticker}_{safe_title}_Transcript.json")

                # Double-check cache with final determined name
                if os.path.exists(json_filename):
                    success_count += 1
                    continue

                full_text = ""
                sample_text = ""
                for page_num in range(len(doc)):
                    page_text = doc.load_page(page_num).get_text("text")
                    full_text += page_text + "\n"
                    if page_num < 2:  
                        sample_text += page_text + "\n"

                signature = get_layout_signature(ticker, sample_text, concall_dir)
                structured_data = parse_transcript_dynamically(full_text, signature)
                structured_data['metadata'] = {"ticker": ticker, "document_title": safe_title, "source_url": pdf_url}

                with open(json_filename, 'w', encoding='utf-8') as f:
                    json.dump(structured_data, f, indent=4)
                
                success_count += 1
                    
        except Exception as e:
            logging.error(f"PDF extraction failed for {ticker} ({pdf_url}): {e}")

    # Pass the extraction ledger back to main thread
    return {"expected": expected_count, "downloaded": success_count}

# =====================================================================
# FRAMEWORK DATA EXTRACTION
# =====================================================================

def scrape_stock(symbol):
    """Parses both the framework data and the extra contextual data from Screener."""
    url = SCREENER_BASE_URL.format(symbol)
    try:
        response = safe_request(url)
        if not response:
            return None
        if response.status_code == 404:
            url = f"https://www.screener.in/company/{symbol}/"
            response = safe_request(url)
            if not response or response.status_code != 200:
                return None
            
        soup = BeautifulSoup(response.text, 'html.parser')
        raw_vacuum_data = vacuum_screener_data(soup)
        
        safe_ticker = "".join([c for c in symbol if c.isalnum() or c in ['_', '-']]).rstrip()
        first_char = safe_ticker[0].upper() if safe_ticker and safe_ticker[0].isalpha() else "0-9"
        ticker_dir = os.path.join(SCREENER_DATA_DIR, "stocks", first_char, safe_ticker)
        
        # Concall Generation Pipeline generates manifest stats
        concall_stats = process_concalls(symbol, response.text, ticker_dir)
        
        def get_metric(possible_ids, name, length=3):
            arr = extract_row_values(soup, possible_ids, name)
            while len(arr) < length:
                arr.insert(0, 0.0)
            return arr

        sales_years = get_metric(["profit-loss"], "Sales", 3)
        sales_quarters = get_metric(["quarters", "half-years", "results"], "Sales", 2)
        net_profit_years = get_metric(["profit-loss"], "Net Profit", 2)
        opm_years = get_metric(["profit-loss"], "OPM", 2)
        op_years = get_metric(["profit-loss"], "Operating Profit", 1)
        interest_years = get_metric(["profit-loss"], "Interest", 1)
        
        share_cap = get_metric(["balance-sheet"], "Equity Capital", 2)
        borrowings = get_metric(["balance-sheet"], "Borrowings", 2)
        fixed_assets = get_metric(["balance-sheet"], "Fixed Assets", 2)
        cwip = get_metric(["balance-sheet"], "CWIP", 2)
        
        cfo = get_metric(["cash-flow"], "Cash from Operating Activity", 1)
        
        roce = get_metric(["ratios"], "ROCE", 2)
        ccc = get_metric(["ratios"], "Cash Conversion Cycle", 2)
        
        promoter = get_metric(["shareholding"], "Promoters", 2)
        fii = get_metric(["shareholding"], "FIIs", 2)
        dii = get_metric(["shareholding"], "DIIs", 2)

        other_income_years = get_metric(["profit-loss"], "Other Income", 1)
        debtor_days = get_metric(["ratios"], "Debtor Days", 2)
        payable_days = get_metric(["ratios"], "Creditor Days", 2)

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
            
            "market_cap": extract_top_ratio(soup, "Market Cap"),
            "stock_pe": extract_top_ratio(soup, "Stock P/E"),
            "dividend_yield": extract_top_ratio(soup, "Dividend Yield"),
            "other_income_y1": other_income_years[-1],
            "debtor_days_y1": debtor_days[-1], "debtor_days_y2": debtor_days[-2],
            "days_payable_y1": payable_days[-1], "days_payable_y2": payable_days[-2],
            
            "valuation_context": "",
            "earnings_quality_analysis": "",
            "pricing_power_analysis": "",
            "secondary_red_flags": "",
            
            "raw_vacuum_data": raw_vacuum_data,
            "concall_stats": concall_stats
        }
    except Exception as e:
        logging.warning(f"Error parsing {symbol}: {e}")
        return None

def evaluate_framework(d):
    """Executes the STRICT Dual-Track Logic with Null Validation and Math Safeguards."""
    gate_failures = []
    
    if d["sales_y1"] < 50.0: gate_failures.append(f"Nano-Cap Risk (Sales ₹{d['sales_y1']}Cr < ₹50Cr)")
    if d["interest_y1"] > 0:
        if d["operating_profit_y1"] <= 0: gate_failures.append("Bankruptcy Risk (Negative Operating Profit with Debt)")
        elif (d["operating_profit_y1"] / d["interest_y1"]) < 2.0: gate_failures.append("Bankruptcy Risk (Interest Coverage < 2.0)")
    if d["share_capital_y2"] > 0 and d["share_capital_y1"] > (d["share_capital_y2"] * 1.05): gate_failures.append("The Dilution Trap")
    if d["promoter_q1"] > 0 and d["promoter_q1"] < 40.0: gate_failures.append("No Skin in the Game")
    if d["market_cap"] > 5000: gate_failures.append("Market Cap exceeds 5000Cr")
    if d["operating_profit_y1"] > 0 and d["other_income_y1"] > d["operating_profit_y1"]: gate_failures.append("Other Income exceeds Core Profit")
        
    if len(gate_failures) > 0: return "REJECTED", f"Failed Survival Gate: {'; '.join(gate_failures)}"
        
    ta_triggers = 0
    ta_details = []
    
    if d["fixed_assets_y2"] > 0 and d["cwip_y2"] > (d["fixed_assets_y2"] * 0.10):
        if d["cwip_y1"] < (d["cwip_y2"] * 0.50) and d["fixed_assets_y1"] > d["fixed_assets_y2"]:
            ta_triggers += 1
            ta_details.append("Factory Go-Live")
            
    if d["ccc_y2"] != 0:
        if (d["ccc_y2"] > 0 and d["ccc_y1"] < (d["ccc_y2"] * 0.80)) or (d["ccc_y2"] < 0 and d["ccc_y1"] < (d["ccc_y2"] * 1.20)):
            ta_triggers += 1
            ta_details.append("Working Capital Squeeze")
            
    if d["opm_y2"] > 0 and d["sales_y2"] > 0:
        if d["opm_y1"] > (d["opm_y2"] * 1.20) and d["sales_y1"] >= (d["sales_y2"] * 0.95):
            ta_triggers += 1
            ta_details.append("Margin Turnaround")
            
    sm_q1 = d["promoter_q1"] + d["fii_q1"] + d["dii_q1"]
    sm_q2 = d["promoter_q2"] + d["fii_q2"] + d["dii_q2"]
    if sm_q2 > 0 and sm_q1 > sm_q2:
        ta_triggers += 1
        ta_details.append("Smart Money Creep")
        
    if d["days_payable_y2"] > 0 and d["days_payable_y1"] > (d["days_payable_y2"] * 1.15) and d["debtor_days_y1"] < d["debtor_days_y2"]:
        ta_triggers += 1
        ta_details.append("Supplier Squeeze")
        
    track_a_pass = ta_triggers >= 2

    tb_triggers = 0
    tb_details = []
    
    if d["roce_y1"] > 20.0 and d["roce_y2"] > 20.0:
        tb_triggers += 1
        tb_details.append("Elite ROCE")
        
    if d["sales_y3"] > 0 and d["sales_y2"] > 0:
        if d["sales_y1"] > (d["sales_y2"] * 1.15) and d["sales_y2"] > (d["sales_y3"] * 1.15):
            tb_triggers += 1
            tb_details.append("Top-Line Growth")
            
    sales_growth = (d["sales_y1"] / d["sales_y2"]) if d["sales_y2"] > 0 else 0
    profit_growth = (d["net_profit_y1"] / d["net_profit_y2"]) if d["net_profit_y2"] > 0 else 0
    if sales_growth > 0 and d["net_profit_y2"] > 0 and d["net_profit_y1"] > 0:
        if profit_growth > sales_growth:
            tb_triggers += 1
            tb_details.append("Operating Leverage")
            
    if d["net_profit_y1"] > 0 and d["cfo_y1"] > (d["net_profit_y1"] * 0.70):
        tb_triggers += 1
        tb_details.append("Immaculate Cash Conversion")
        
    if d["dividend_yield"] > 0.0:
        tb_triggers += 1
        tb_details.append("Dividend Validation")

    if d["stock_pe"] > 70: track_b_pass = False 
    else: track_b_pass = tb_triggers >= 3

    if track_a_pass and track_b_pass: return "HYPER-COMPOUNDER", f"Both Tracks. Fired: {', '.join(ta_details + tb_details)}"
    elif track_a_pass: return "EARLY INFLECTION", f"Track A. Fired: {', '.join(ta_details)}"
    elif track_b_pass: return "PROVEN COMPOUNDER", f"Track B. Fired: {', '.join(tb_details)}"
    else: return "STAGNANT", "Passed survival checks but failed growth thresholds."

def generate_qualitative_analysis(d):
    """Executes post-qualification analysis on the salvaged data to provide deep context."""
    pe = d["stock_pe"]
    mc = d["market_cap"]
    mc_tag = f"Large/Mid-Cap (₹{mc}Cr)" if mc > 5000 else f"Micro/Small-Cap (₹{mc}Cr)"
    
    if pe > 70: d["valuation_context"] = f"{mc_tag} priced for perfection (PE: {pe}). The easy multi-bagger money may have been made."
    elif 0 < pe <= 20: d["valuation_context"] = f"{mc_tag} trading at deep value (PE: {pe}). High margin of safety."
    elif pe == 0: d["valuation_context"] = f"{mc_tag} trading with zero/calculable PE."
    else: d["valuation_context"] = f"{mc_tag} trading at fair/standard multiple (PE: {pe})."

    op = d["operating_profit_y1"]
    other_inc = d["other_income_y1"]
    np = d["net_profit_y1"]
    
    if other_inc > op and op > 0: d["earnings_quality_analysis"] = f"CRITICAL WARNING: Other Income (₹{other_inc}Cr) exceeds Core Operating Profit (₹{op}Cr). Profits are highly engineered."
    elif other_inc > (np * 0.3): d["earnings_quality_analysis"] = "CAUTION: >30% of Net Profit stems from Other Income. Verify asset sales."
    elif d["dividend_yield"] > 0: d["earnings_quality_analysis"] = f"Elite Quality: Earnings are clean and validated by a {d['dividend_yield']}% hard-cash dividend."
    else: d["earnings_quality_analysis"] = "Standard Quality: Earnings driven by core operations. No dividend paid."

    dp_y1 = d["days_payable_y1"]
    dp_y2 = d["days_payable_y2"]
    dd_y1 = d["debtor_days_y1"]
    dd_y2 = d["debtor_days_y2"]
    
    if dp_y1 > (dp_y2 * 1.15) and dd_y1 < dd_y2: d["pricing_power_analysis"] = "EXTREME PRICING POWER: Forcing suppliers to wait longer for payment while forcing clients to pay cash faster."
    elif dd_y1 > (dd_y2 * 1.25): d["pricing_power_analysis"] = "WARNING: Receivables piling up. Company is giving away free credit to drive sales."
    else: d["pricing_power_analysis"] = "Neutral: Trade working capital is stable."

    flags = []
    if d["borrowings_y1"] > d["fixed_assets_y1"] and d["fixed_assets_y1"] > 0: flags.append("Debt exceeds hard physical assets")
    if d["promoter_q1"] < 50.0: flags.append(f"Promoter holding is passable but low ({d['promoter_q1']}%)")
    if d["cfo_y1"] < 0: flags.append("Passed survival, but currently burning operating cash flow")
        
    d["secondary_red_flags"] = " | ".join(flags) if flags else "No glaring secondary red flags detected."
    return d

def save_to_db(d):
    db_payload = {k: v for k, v in d.items() if k not in ["raw_vacuum_data", "concall_stats", "status"]}
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    columns = ', '.join(db_payload.keys())
    placeholders = ':' + ', :'.join(db_payload.keys())
    query = f"INSERT OR REPLACE INTO scraped_metrics ({columns}) VALUES ({placeholders})"
    cursor.execute(query, db_payload)
    conn.commit()
    conn.close()

def save_ticker_csv(d):
    ticker = d["ticker"]
    new_raw_data_rows = d.get("raw_vacuum_data", [])
    if not new_raw_data_rows: return 
    
    safe_ticker = "".join([c for c in ticker if c.isalnum() or c in ['_', '-']]).rstrip()
    first_char = safe_ticker[0].upper() if safe_ticker and safe_ticker[0].isalpha() else "0-9"
    ticker_dir = os.path.join(SCREENER_DATA_DIR, "stocks", first_char, safe_ticker)
    
    os.makedirs(ticker_dir, exist_ok=True)
    
    existing_files = glob.glob(os.path.join(ticker_dir, f"{safe_ticker}_*.csv"))
    if existing_files:
        latest_file = max(existing_files, key=os.path.getmtime)
        try:
            with open(latest_file, 'r', encoding='utf-8') as f:
                reader = csv.reader(f)
                old_raw_data_rows = list(reader)
            
            old_core_fundamentals = filter_volatile_data(old_raw_data_rows)
            new_core_fundamentals = filter_volatile_data(new_raw_data_rows)
            
            if old_core_fundamentals == new_core_fundamentals: return 
        except Exception as e: pass

    ticker_file = os.path.join(ticker_dir, f"{safe_ticker}_{RUN_DATE}.csv")
    with open(ticker_file, mode='w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        writer.writerows(new_raw_data_rows)

def process_worker(symbol):
    """The multithreaded worker function. Now features a TTL Time-Bomb Switch."""
    
    # 1. TIME BOMB CHECK
    if (time.time() - SCRIPT_START_TIME) > MAX_RUNTIME_SEC:
        return {"ticker": symbol, "status": "TIMEOUT"}
        
    data = scrape_stock(symbol)
    if not data: return None
        
    classification, reason = evaluate_framework(data)
    data["classification"] = classification
    data["qualification_reason"] = reason
    
    if classification in ["EARLY INFLECTION", "PROVEN COMPOUNDER", "HYPER-COMPOUNDER"]:
        data = generate_qualitative_analysis(data)
        
    data["status"] = "SUCCESS"
    return data

def send_telegram_alert(new_stocks, df_out):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID: return

    msg = f"📊 *Microcap Screener Weekly Update ({RUN_DATE})*\n\n"
    if not new_stocks: msg += "No new candidates passed the framework this week."
    else:
        msg += f"🚨 *{len(new_stocks)} NEW CANDIDATES DETECTED* 🚨\n\n"
        for stock in new_stocks:
            msg += f"🔥 *{stock['ticker']}* ({stock['classification']})\n_Reason:_ {stock['qualification_reason']}\n\n"

    try:
        with open(TELEGRAM_LOG_FILE, "a", encoding="utf-8") as f:
            f.write(f"\n--- Analysis Log Generated at {time.strftime('%Y-%m-%d %H:%M:%S')} ---\n")
            f.write(msg + "\n")
    except Exception: pass

    max_msg_length = 4000
    msg_chunks = [msg[i:i + max_msg_length] for i in range(0, len(msg), max_msg_length)]
    
    send_msg_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    for chunk in msg_chunks:
        try: requests.post(send_msg_url, data={"chat_id": TELEGRAM_CHAT_ID, "text": chunk, "parse_mode": "Markdown"})
        except Exception as e: logging.error(f"Failed to send Telegram msg: {e}")

    send_doc_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendDocument"
    csv_buffer = io.BytesIO()
    df_out.to_csv(csv_buffer, index=False)
    csv_buffer.name = f"Qualified_Microcaps_{RUN_DATE}.csv"
    csv_buffer.seek(0)

    try:
        requests.post(send_doc_url, data={"chat_id": TELEGRAM_CHAT_ID, "caption": f"📄 *Attached: Weekly Qualified List ({RUN_DATE})*", "parse_mode": "Markdown"}, files={"document": csv_buffer})
    except Exception as e: logging.error(f"Failed to send Telegram document: {e}")

def main():
    init_db()
    symbols = fetch_nse_symbols()
    
    if not symbols: return

    # 1. Load Local Tracking Ledgers
    previous_tickers = set()
    existing_csvs = glob.glob(os.path.join(SCREENER_DATA_DIR, "qualified_stocks_analysis_*.csv"))
    existing_csvs = [f for f in existing_csvs if not f.endswith(f"_{RUN_DATE}.csv")]
    if existing_csvs:
        latest_csv = max(existing_csvs, key=os.path.getmtime)
        try:
            prev_df = pd.read_csv(latest_csv)
            previous_tickers = set(prev_df['ticker'].tolist())
        except Exception: pass

    # 2. Daily Run Cache: Slice off everything we already processed today
    processed_today = set()
    if os.path.exists(RUN_CACHE_FILE):
        try:
            with open(RUN_CACHE_FILE, 'r') as f:
                processed_today = set(json.load(f))
        except Exception: pass

    symbols = [s for s in symbols if s not in processed_today]
    logging.info(f"Loaded {len(processed_today)} already processed symbols from today's cache. {len(symbols)} left to process.")

    # Load 5-Year Transcript Manifest
    concall_manifest = {}
    if os.path.exists(MANIFEST_FILE):
        try:
            with open(MANIFEST_FILE, 'r') as f:
                concall_manifest = json.load(f)
        except Exception: pass

    qualified_records = []
    new_candidates = []

    MAX_CONCURRENT_THREADS = 3
    logging.info(f"Initiating throttled multithreading with {MAX_CONCURRENT_THREADS} workers. Clock is ticking...")

    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_CONCURRENT_THREADS) as executor:
        future_to_symbol = {executor.submit(process_worker, sym): sym for sym in symbols}
        
        for idx, future in enumerate(concurrent.futures.as_completed(future_to_symbol)):
            symbol = future_to_symbol[future]
            try:
                data = future.result()
                
                # Check if the worker was aborted by the Time Bomb
                if data and data.get("status") == "TIMEOUT":
                    logging.warning(f"[{symbol}] Skipped due to 5.45-hour time limit. Checkpointing for next run.")
                    continue # Do NOT add to processed_today cache! Let it run next time.
                    
                # The stock successfully processed. Immediately write to Daily Ledger.
                processed_today.add(symbol)
                with open(RUN_CACHE_FILE, 'w') as f:
                    json.dump(list(processed_today), f)

                if data:
                    save_to_db(data)
                    save_ticker_csv(data)
                    
                    # Update Concall Tracking Manifest 
                    if "concall_stats" in data:
                        stats = data["concall_stats"]
                        concall_manifest[symbol] = stats
                        if stats["downloaded"] >= stats["expected"]:
                            concall_manifest[symbol]["status"] = "COMPLETED"
                        else:
                            concall_manifest[symbol]["status"] = "FAILED_RETRY_NEXT_RUN"

                    classif = data["classification"]
                    if classif in ["EARLY INFLECTION", "PROVEN COMPOUNDER", "HYPER-COMPOUNDER"]:
                        export_data = {k: v for k, v in data.items() if k not in ["raw_vacuum_data", "concall_stats", "status"]}
                        qualified_records.append(export_data)
                        logging.info(f">>> MATCH: {symbol} classified as {classif}")
                        
                        if symbol not in previous_tickers:
                            new_candidates.append(export_data)
                            
            except Exception as exc: logging.warning(f"{symbol} generated an exception: {exc}")
                
            if idx % 100 == 0 and idx > 0: logging.info(f"Progress: Processed {idx}/{len(symbols)} pending tickers...")

    # Write the Final Master Manifest File
    try:
        with open(MANIFEST_FILE, 'w', encoding='utf-8') as f:
            json.dump(concall_manifest, f, indent=4)
    except Exception as e:
        logging.error(f"Failed to save manifest file: {e}")

    if qualified_records:
        df_out = pd.DataFrame(qualified_records)
        front_cols = ['ticker', 'classification', 'market_cap', 'valuation_context', 'earnings_quality_analysis', 'pricing_power_analysis', 'secondary_red_flags', 'qualification_reason']
        back_cols = [c for c in df_out.columns if c not in front_cols]
        df_out = df_out[front_cols + back_cols]
        
        df_out.to_csv(CSV_FILE, index=False)
        logging.info(f"Analysis complete. {len(qualified_records)} candidate(s) saved to '{CSV_FILE}'.")
        send_telegram_alert(new_candidates, df_out)
    else: logging.info("Process completed. No listings matching the metrics were discovered.")

if __name__ == "__main__":
    main()
