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
from typing import Tuple, Dict, List
import textstat

# ---------------------------------------------------------
# GLOBAL SETTINGS
# ---------------------------------------------------------
SCRIPT_START_TIME = time.time()
MAX_RUNTIME_SEC = 5.45 * 3600  # Graceful abort at 5.45 hours

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

RUN_DATE = time.strftime("%Y-%m-%d")
SCREENER_DATA_DIR = "ScreenerData"
DB_FILE = os.path.join(SCREENER_DATA_DIR, "screener_data.db")
CSV_FILE = os.path.join(SCREENER_DATA_DIR, f"qualified_stocks_analysis_{RUN_DATE}.csv")
TELEGRAM_LOG_FILE = os.path.join(SCREENER_DATA_DIR, f"telegram_analysis_log_{RUN_DATE}.txt")
MANIFEST_FILE = os.path.join(SCREENER_DATA_DIR, "manifest.json")
RUN_CACHE_FILE = os.path.join(SCREENER_DATA_DIR, f"run_cache_{RUN_DATE}.json")

NSE_URL = "https://archives.nseindia.com/content/equities/EQUITY_L.csv"
SCREENER_BASE_URL = "https://www.screener.in/company/{}/consolidated/"
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"}

# ---------------------------------------------------------
# DETERMINISTIC TRANSCRIPT ENGINE
# ---------------------------------------------------------
class TranscriptIntelligence:
    def __init__(self):
        self.regex_map = {
            "Hype": re.compile(r'\b(multifold|exponential|game changer|value unlocking|multibagger|unprecedented|robust pipeline|paradigm shift|phenomenal|unmatched growth)\b', re.IGNORECASE),
            "Delivery": re.compile(r'\b(commissioned|commercial production|realized|cash flow|debt reduction|completed|on stream|disbursed|royalty paid|capacity utilization|ebitda accretive)\b', re.IGNORECASE),
            "Evasion": re.compile(r'\b(unseasonal|macro headwinds|temporary blip|will get back to you|operator activity|supply chain|cyclical downturn|take it offline|next quarter|details not handy)\b', re.IGNORECASE),
            "Governance_Stress": re.compile(r'\b(auditor resignation|pledge|related party|working capital stretch|debtor days|sebi|nclt|margin compression|promoter share|delay in filing|qualification)\b', re.IGNORECASE)
        }

    def _is_valid_speaker_name(self, text: str) -> bool:
        if len(text) > 100: return False
        lower_text = text.lower()
        if lower_text in ['moderator', 'operator', 'management', 'analyst', 'participant', 'speaker']: return True
        if lower_text.startswith(('gross margin', 'net profit', 'ebitda', 'revenue', 'cash flow', 'note:', 'source:')): return False
        orig_core = re.sub(r'^(Mr\.|Ms\.|Dr\.)\s*', '', text, flags=re.IGNORECASE).strip()
        orig_words = orig_core.split()
        if not orig_words: return False
        for w in orig_words:
            first_char = next((c for c in w if c.isalpha()), None)
            if first_char and first_char.islower() and w.lower() not in ['of', 'from', 'for', 'and', 'the', 'in', 'on']: return False
        if text.endswith('.') and not text.strip().split()[-1].lower() in ['ltd.', 'pvt.', 'inc.', 'mr.', 'ms.', 'dr.']: return False
        if text.endswith('?'): return False
        return True

    def _clean_text_stream(self, text: str) -> str:
        if not text: return ""
        text = re.sub(r'[−–—]', '-', text)
        text = "".join(ch for ch in text if ch.isprintable() or ch in ['\n', '\t', '\r'])
        return re.sub(r'[ \t]+', ' ', text)

    def extract_and_structure_transcript(self, pdf_bytes: bytes) -> Tuple[str, str, int]:
        raw_lines = []
        qa_started = False
        prep_segments, qa_segments = [], []
        qa_markers = ["open the floor for questions", "begin the q&a", "question-and-answer session", "questions and answers"]
        try:
            doc = fitz.open(stream=pdf_bytes, filetype="pdf")
            page_count = doc.page_count
            for page in doc:
                for line in page.get_text("text").split('\n'):
                    line_stripped = line.strip()
                    if not line_stripped: continue
                    lower_line = line_stripped.lower()
                    if "earnings conference call" in lower_line or ("limited" in lower_line and re.search(r'\bq[1-4]\b', lower_line)): continue
                    if re.match(r'^\d+\s*$', line_stripped) or re.match(r'^\d{2}\.\d{2}\.\d{4}$', line_stripped): continue
                    raw_lines.append(line_stripped)
            doc.close()
        except Exception: return "", "", 0

        normalized_lines = []
        inline_pattern = re.compile(r'(\b(?:(?:Mr\.|Ms\.|Dr\.)?\s*[A-Z][a-zA-Z\.\-]+\s+[A-Z][a-zA-Z\-]+(?:\s+[A-Z][a-zA-Z\.]+){0,2}|Moderator|Operator|Management|Analyst|Participant|Speaker)\s*:)')
        for line in raw_lines:
            parts = inline_pattern.split(line)
            if len(parts) > 1:
                for part in parts:
                    if part.strip(): normalized_lines.append(part.strip())
            else: normalized_lines.append(line)

        current_speaker = "PRE_SPEAKER_OVERFLOW_BUFFER"
        current_speech_accumulator = []

        def flush_speaker():
            nonlocal current_speech_accumulator, prep_segments, qa_segments
            if current_speech_accumulator:
                completed_speech = self._clean_text_stream(" ".join(current_speech_accumulator)).strip()
                if completed_speech:
                    formatted_block = f"### 👤 {current_speaker}\n{completed_speech}\n\n"
                    if qa_started: qa_segments.append(formatted_block)
                    else: prep_segments.append(formatted_block)
                current_speech_accumulator = []

        for line in normalized_lines:
            if not qa_started and any(marker in line.lower() for marker in qa_markers):
                flush_speaker()
                qa_started = True
            clean_line = re.sub(r'^[-–—•\s]+', '', line).strip()
            if not clean_line: continue
            is_speaker, speaker_name = False, ""
            if clean_line.endswith(':'):
                pot_speaker = clean_line[:-1].strip()
                if self._is_valid_speaker_name(pot_speaker): is_speaker, speaker_name = True, pot_speaker
            elif ':' in clean_line:
                parts = clean_line.split(':', 1)
                pot_speaker, spoken = parts[0].strip(), parts[1].strip()
                if self._is_valid_speaker_name(pot_speaker):
                    flush_speaker()
                    current_speaker = pot_speaker
                    if spoken: current_speech_accumulator.append(re.sub(r'^[-–—•\s]+', '', spoken).strip())
                    continue
            elif self._is_valid_speaker_name(clean_line):
                is_speaker, speaker_name = True, clean_line
            if is_speaker:
                flush_speaker()
                current_speaker = speaker_name
            else: current_speech_accumulator.append(line)
        flush_speaker()
        return "".join(prep_segments).strip(), "".join(qa_segments).strip(), page_count

    def calculate_metrics(self, text: str) -> Dict:
        words = text.split()
        word_count = len(words) if len(words) > 0 else 1
        counts = {k: len(reg.findall(text)) for k, reg in self.regex_map.items()}
        densities = {f"{k}_Density_Per_10k": (v / word_count) * 10000 for k, v in counts.items()}
        return {"Word_Count": word_count, "Gunning_Fog_Index": textstat.gunning_fog(text) if text.strip() else 0.0, **densities, **counts}

    def calculate_behavioral_signals(self, text_metrics: dict) -> List[str]:
        signals = []
        def get_f(val):
            try: return float(str(val).replace('%', '').replace(',', '').strip())
            except (ValueError, AttributeError, TypeError): return None
        hype = get_f(text_metrics.get("Hype_Density_Per_10k"))
        delivery = get_f(text_metrics.get("Delivery_Density_Per_10k"))
        evasion = get_f(text_metrics.get("Evasion_Density_Per_10k"))
        if hype and delivery and hype > 5.0 and delivery < 1.0: signals.append("🚨 **[BEHAVIORAL] 'Hype/Delivery Divergence':** Promoter using aggressive buzzwords.")
        if evasion and evasion > 3.0: signals.append("⚠️ **[EVASION] High Evasion Density:** Management used unusually high deflection terminology.")
        if not signals: signals.append("✅ **[STABILITY]** No severe behavioral manipulation thresholds breached.")
        return signals

quant_engine = TranscriptIntelligence()

# ---------------------------------------------------------
# INFRASTRUCTURE FUNCTIONS
# ---------------------------------------------------------

def safe_request(url, retries=3):
    for attempt in range(retries):
        time.sleep(random.uniform(1.2, 2.5))
        try:
            response = requests.get(url, headers=HEADERS, timeout=15)
            if response.status_code == 200: return response
            elif response.status_code == 429: time.sleep((attempt + 1) * 10)
        except requests.exceptions.RequestException: time.sleep(2)
    return None

def init_db():
    os.makedirs(SCREENER_DATA_DIR, exist_ok=True)
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS scraped_metrics (
            ticker TEXT PRIMARY KEY,
            scraped_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            classification TEXT, qualification_reason TEXT,
            sales_y1 REAL, sales_y2 REAL, sales_y3 REAL,
            sales_q1 REAL, sales_q2 REAL,
            net_profit_y1 REAL, net_profit_y2 REAL,
            opm_y1 REAL, opm_y2 REAL,
            operating_profit_y1 REAL, interest_y1 REAL,
            share_capital_y1 REAL, share_capital_y2 REAL,
            borrowings_y1 REAL, borrowings_y2 REAL,
            fixed_assets_y1 REAL, fixed_assets_y2 REAL,
            cwip_y1 REAL, cwip_y2 REAL,
            cfo_y1 REAL, roce_y1 REAL, roce_y2 REAL,
            ccc_y1 REAL, ccc_y2 REAL,
            promoter_q1 REAL, promoter_q2 REAL,
            fii_q1 REAL, fii_q2 REAL,
            dii_q1 REAL, dii_q2 REAL,
            market_cap REAL, stock_pe REAL, dividend_yield REAL,
            other_income_y1 REAL, debtor_days_y1 REAL, debtor_days_y2 REAL,
            days_payable_y1 REAL, days_payable_y2 REAL,
            valuation_context TEXT, earnings_quality_analysis TEXT,
            pricing_power_analysis TEXT, secondary_red_flags TEXT
        )
    """)
    conn.commit()
    conn.close()

def fetch_nse_symbols():
    try:
        response = safe_request(NSE_URL)
        if not response: return []
        df = pd.read_csv(io.StringIO(response.text))
        df = df[df[' SERIES'] == 'EQ']
        return df['SYMBOL'].dropna().unique().tolist()
    except Exception: return []

def get_dynamic_table(soup, possible_ids):
    for table_id in possible_ids:
        section = soup.find('section', id=table_id)
        if section and section.find('table'): return section.find('table')
    return None

def extract_row_values(soup, possible_ids, row_label):
    table = get_dynamic_table(soup, possible_ids)
    if not table: return []
    has_ttm = False
    thead = table.find("thead")
    if thead:
        headers = thead.find_all("th")
        if headers and "TTM" in headers[-1].text.upper(): has_ttm = True
    rows = table.find_all("tr")
    for row in rows:
        cells = row.find_all(["td", "th"])
        if cells and row_label.lower() in cells[0].text.lower():
            vals = [clean_value(c.text) for c in cells[1:]]
            if has_ttm and len(vals) > 0: vals = vals[:-1] 
            return vals
    return []

def extract_top_ratio(soup, metric_name):
    try:
        name_span = soup.find('span', class_='name', string=re.compile(metric_name, re.IGNORECASE))
        if name_span:
            li_parent = name_span.find_parent('li')
            if li_parent:
                val_span = li_parent.find('span', class_='number')
                if val_span: return clean_value(val_span.text)
    except Exception: pass
    return 0.0

def vacuum_screener_data(soup):
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
            if not key_points_div: key_points_div = profile_div.find('div', class_='key-points')
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
                    val_text = val_span.get_text(separator=' ', strip=True).replace('₹', '').replace('Cr.', '').replace('%', '').strip()
                    info_row_vals.append(val_text)
                if len(info_row_names) == 4:
                    rows.append(info_row_names); rows.append(info_row_vals); rows.append([])
                    info_row_names = []; info_row_vals = []
            if info_row_names: rows.append(info_row_names); rows.append(info_row_vals); rows.append([])
        sections = [("PEER COMPARISON", ["peers"]), ("QUARTERLY RESULTS", ["quarters", "half-years", "results"]), ("ANNUAL RESULTS", ["profit-loss"]), ("BALANCE SHEET", ["balance-sheet"]), ("CASH FLOW", ["cash-flow"]), ("RATIOS", ["ratios"]), ("SHAREHOLDING PATTERN", ["shareholding"])]
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
                    for tr in tbl.find_all('tr'): rows.append([td.get_text(separator=' ', strip=True) for td in tr.find_all(['th', 'td'])])
                    rows.append([])
    except Exception as e: logging.warning(f"Error during vacuum phase: {e}")
    return rows

def filter_volatile_data(csv_rows):
    filtered = []
    skip = False
    for row in csv_rows:
        if not row or (len(row) == 1 and str(row[0]).strip() == ""): continue
        first_col = str(row[0]).strip()
        if first_col.startswith("--- ") and first_col.endswith(" ---"):
            if first_col in ["--- STOCK INFO ---", "--- PEER COMPARISON ---", "--- CAGR BOXES ---"]: skip = True; continue
            else: skip = False
        if not skip: filtered.append("|".join([str(item).strip() for item in row]))
    return filtered

def process_concalls(ticker, html_text, ticker_dir):
    concall_dir = os.path.join(ticker_dir, "concalls")
    os.makedirs(concall_dir, exist_ok=True)
    transcript_links = {}
    target_date_boundary = datetime.now() - timedelta(days=5*365)
    
    # Check existing Markdown files
    existing_files = set([os.path.basename(f) for f in glob.glob(os.path.join(concall_dir, "*.md"))])

    json_match = re.search(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', html_text, re.DOTALL)
    if json_match:
        try:
            data = json.loads(json_match.group(1))
            def search_json(obj, current_date="Unknown"):
                if isinstance(obj, dict):
                    if 'date' in obj:
                        current_date = str(obj['date']).replace(" ", "_")
                        try:
                            if datetime.strptime(current_date[:10], '%Y-%m-%d') < target_date_boundary: return
                        except Exception: pass
                    if obj.get('name', '').lower() == 'transcript' and 'url' in obj: transcript_links[current_date] = obj['url']
                    for v in obj.values(): search_json(v, current_date)
                elif isinstance(obj, list):
                    for item in obj: search_json(item, current_date)
            search_json(data)
        except Exception: pass

    expected_count = len(transcript_links)
    success_count = 0

    for fallback_date, pdf_url in transcript_links.items():
        if pdf_url.startswith("/"): pdf_url = "https://www.screener.in" + pdf_url
        quarter_match = re.search(r'(Q[1-4]\s*FY\d{2,4})', fallback_date, re.IGNORECASE)
        quarter_str = quarter_match.group(1).replace(" ", "_").upper() if quarter_match else fallback_date.replace("-", "_")
        safe_title = "".join([c for c in quarter_str if c.isalnum() or c in ['_', '-']])[:100]
        md_filename = os.path.join(concall_dir, f"{ticker}_{safe_title}_Transcript.md")
        
        if os.path.basename(md_filename) in existing_files:
            success_count += 1; continue

        res = safe_request(pdf_url)
        if not res or res.status_code != 200 or 'application/pdf' not in res.headers.get('Content-Type', '').lower(): continue
        
        try:
            prep, qa, p_count = quant_engine.extract_and_structure_transcript(res.content)
            metrics = quant_engine.calculate_metrics(prep + qa)
            signals = quant_engine.calculate_behavioral_signals(metrics)
            with open(md_filename, 'w', encoding='utf-8') as f:
                f.write(f"# {ticker} - {safe_title}\n\n")
                f.write("## Behavioral Analysis\n" + "\n".join(signals) + "\n\n")
                f.write("## Preparation\n" + prep + "\n\n")
                f.write("## Q&A\n" + qa)
            success_count += 1
        except Exception as e: logging.error(f"Error parsing for {ticker}: {e}")

    return {"expected": expected_count, "downloaded": success_count}

def scrape_stock(symbol):
    url = SCREENER_BASE_URL.format(symbol)
    try:
        response = safe_request(url)
        if not response: return None
        if response.status_code == 404:
            url = f"https://www.screener.in/company/{symbol}/"
            response = safe_request(url)
            if not response or response.status_code != 200: return None
        soup = BeautifulSoup(response.text, 'html.parser')
        raw_vacuum_data = vacuum_screener_data(soup)
        
        safe_ticker = "".join([c for c in symbol if c.isalnum() or c in ['_', '-']]).rstrip()
        first_char = safe_ticker[0].upper() if safe_ticker and safe_ticker[0].isalpha() else "0-9"
        ticker_dir = os.path.join(SCREENER_DATA_DIR, "stocks", first_char, safe_ticker)
        
        concall_stats = process_concalls(symbol, response.text, ticker_dir)
        
        def get_metric(possible_ids, name, length=3):
            arr = extract_row_values(soup, possible_ids, name)
            while len(arr) < length: arr.insert(0, 0.0)
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
            "cfo_y1": cfo[-1], "roce_y1": roce[-1], "roce_y2": roce[-2],
            "ccc_y1": ccc[-1], "ccc_y2": ccc[-2],
            "promoter_q1": promoter[-1], "promoter_q2": promoter[-2],
            "fii_q1": fii[-1], "fii_q2": fii[-2], "dii_q1": dii[-1], "dii_q2": dii[-2],
            "market_cap": extract_top_ratio(soup, "Market Cap"),
            "stock_pe": extract_top_ratio(soup, "Stock P/E"),
            "dividend_yield": extract_top_ratio(soup, "Dividend Yield"),
            "other_income_y1": other_income_years[-1],
            "debtor_days_y1": debtor_days[-1], "debtor_days_y2": debtor_days[-2],
            "days_payable_y1": payable_days[-1], "days_payable_y2": payable_days[-2],
            "valuation_context": "", "earnings_quality_analysis": "",
            "pricing_power_analysis": "", "secondary_red_flags": "",
            "raw_vacuum_data": raw_vacuum_data,
            "concall_stats": concall_stats
        }
    except Exception as e:
        logging.warning(f"Error parsing {symbol}: {e}")
        return None

def evaluate_framework(d):
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
    ta_triggers = 0; ta_details = []
    if d["fixed_assets_y2"] > 0 and d["cwip_y2"] > (d["fixed_assets_y2"] * 0.10):
        if d["cwip_y1"] < (d["cwip_y2"] * 0.50) and d["fixed_assets_y1"] > d["fixed_assets_y2"]: ta_triggers += 1; ta_details.append("Factory Go-Live")
    if d["ccc_y2"] != 0:
        if (d["ccc_y2"] > 0 and d["ccc_y1"] < (d["ccc_y2"] * 0.80)) or (d["ccc_y2"] < 0 and d["ccc_y1"] < (d["ccc_y2"] * 1.20)): ta_triggers += 1; ta_details.append("Working Capital Squeeze")
    if d["opm_y2"] > 0 and d["sales_y2"] > 0:
        if d["opm_y1"] > (d["opm_y2"] * 1.20) and d["sales_y1"] >= (d["sales_y2"] * 0.95): ta_triggers += 1; ta_details.append("Margin Turnaround")
    sm_q1 = d["promoter_q1"] + d["fii_q1"] + d["dii_q1"]
    sm_q2 = d["promoter_q2"] + d["fii_q2"] + d["dii_q2"]
    if sm_q2 > 0 and sm_q1 > sm_q2: ta_triggers += 1; ta_details.append("Smart Money Creep")
    if d["days_payable_y2"] > 0 and d["days_payable_y1"] > (d["days_payable_y2"] * 1.15) and d["debtor_days_y1"] < d["debtor_days_y2"]: ta_triggers += 1; ta_details.append("Supplier Squeeze")
    track_a_pass = ta_triggers >= 2
    tb_triggers = 0; tb_details = []
    if d["roce_y1"] > 20.0 and d["roce_y2"] > 20.0: tb_triggers += 1; tb_details.append("Elite ROCE")
    if d["sales_y3"] > 0 and d["sales_y2"] > 0:
        if d["sales_y1"] > (d["sales_y2"] * 1.15) and d["sales_y2"] > (d["sales_y3"] * 1.15): tb_triggers += 1; tb_details.append("Top-Line Growth")
    sales_growth = (d["sales_y1"] / d["sales_y2"]) if d["sales_y2"] > 0 else 0
    profit_growth = (d["net_profit_y1"] / d["net_profit_y2"]) if d["net_profit_y2"] > 0 else 0
    if sales_growth > 0 and d["net_profit_y2"] > 0 and d["net_profit_y1"] > 0:
        if profit_growth > sales_growth: tb_triggers += 1; tb_details.append("Operating Leverage")
    if d["net_profit_y1"] > 0 and d["cfo_y1"] > (d["net_profit_y1"] * 0.70): tb_triggers += 1; tb_details.append("Immaculate Cash Conversion")
    if d["dividend_yield"] > 0.0: tb_triggers += 1; tb_details.append("Dividend Validation")
    if d["stock_pe"] > 70: track_b_pass = False 
    else: track_b_pass = tb_triggers >= 3
    if track_a_pass and track_b_pass: return "HYPER-COMPOUNDER", f"Both Tracks. Fired: {', '.join(ta_details + tb_details)}"
    elif track_a_pass: return "EARLY INFLECTION", f"Track A. Fired: {', '.join(ta_details)}"
    elif track_b_pass: return "PROVEN COMPOUNDER", f"Track B. Fired: {', '.join(tb_details)}"
    else: return "STAGNANT", "Passed survival checks but failed growth thresholds."

def generate_qualitative_analysis(d):
    pe = d["stock_pe"]; mc = d["market_cap"]
    mc_tag = f"Large/Mid-Cap (₹{mc}Cr)" if mc > 5000 else f"Micro/Small-Cap (₹{mc}Cr)"
    if pe > 70: d["valuation_context"] = f"{mc_tag} priced for perfection (PE: {pe})."
    elif 0 < pe <= 20: d["valuation_context"] = f"{mc_tag} trading at deep value (PE: {pe})."
    else: d["valuation_context"] = f"{mc_tag} trading at fair/standard multiple (PE: {pe})."
    op = d["operating_profit_y1"]; other_inc = d["other_income_y1"]; np = d["net_profit_y1"]
    if other_inc > op and op > 0: d["earnings_quality_analysis"] = "CRITICAL WARNING: Other Income exceeds Core Operating Profit."
    elif other_inc > (np * 0.3): d["earnings_quality_analysis"] = "CAUTION: >30% of Net Profit stems from Other Income."
    elif d["dividend_yield"] > 0: d["earnings_quality_analysis"] = f"Elite Quality: Earnings are clean and validated by a {d['dividend_yield']}% hard-cash dividend."
    else: d["earnings_quality_analysis"] = "Standard Quality: Earnings driven by core operations."
    dp_y1 = d["days_payable_y1"]; dp_y2 = d["days_payable_y2"]; dd_y1 = d["debtor_days_y1"]; dd_y2 = d["debtor_days_y2"]
    if dp_y1 > (dp_y2 * 1.15) and dd_y1 < dd_y2: d["pricing_power_analysis"] = "EXTREME PRICING POWER: Forcing suppliers to wait, forcing clients to pay cash faster."
    elif dd_y1 > (dd_y2 * 1.25): d["pricing_power_analysis"] = "WARNING: Receivables piling up. Free credit to drive sales."
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
        except Exception: pass
    ticker_file = os.path.join(ticker_dir, f"{safe_ticker}_{RUN_DATE}.csv")
    with open(ticker_file, mode='w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        writer.writerows(new_raw_data_rows)

def process_worker(symbol):
    if (time.time() - SCRIPT_START_TIME) > MAX_RUNTIME_SEC: return {"ticker": symbol, "status": "TIMEOUT"}
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
    previous_tickers = set()
    existing_csvs = glob.glob(os.path.join(SCREENER_DATA_DIR, "qualified_stocks_analysis_*.csv"))
    existing_csvs = [f for f in existing_csvs if not f.endswith(f"_{RUN_DATE}.csv")]
    if existing_csvs:
        latest_csv = max(existing_csvs, key=os.path.getmtime)
        try:
            prev_df = pd.read_csv(latest_csv)
            previous_tickers = set(prev_df['ticker'].tolist())
        except Exception: pass
    processed_today = set()
    if os.path.exists(RUN_CACHE_FILE):
        try:
            with open(RUN_CACHE_FILE, 'r') as f:
                processed_today = set(json.load(f))
        except Exception: pass
    symbols = [s for s in symbols if s not in processed_today]
    logging.info(f"Loaded {len(processed_today)} already processed symbols from today's cache. {len(symbols)} left to process.")
    concall_manifest = {}
    if os.path.exists(MANIFEST_FILE):
        try:
            with open(MANIFEST_FILE, 'r') as f:
                concall_manifest = json.load(f)
        except Exception: pass
    qualified_records = []
    new_candidates = []
    MAX_CONCURRENT_THREADS = 3
    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_CONCURRENT_THREADS) as executor:
        future_to_symbol = {executor.submit(process_worker, sym): sym for sym in symbols}
        for idx, future in enumerate(concurrent.futures.as_completed(future_to_symbol)):
            symbol = future_to_symbol[future]
            try:
                data = future.result()
                if data and data.get("status") == "TIMEOUT":
                    logging.warning(f"[{symbol}] Skipped due to 5.45-hour time limit.")
                    continue 
                processed_today.add(symbol)
                with open(RUN_CACHE_FILE, 'w') as f: json.dump(list(processed_today), f)
                if data:
                    save_to_db(data)
                    save_ticker_csv(data)
                    if "concall_stats" in data:
                        stats = data["concall_stats"]
                        concall_manifest[symbol] = stats
                        concall_manifest[symbol]["status"] = "COMPLETED" if stats["downloaded"] >= stats["expected"] else "FAILED_RETRY_NEXT_RUN"
                    classif = data["classification"]
                    if classif in ["EARLY INFLECTION", "PROVEN COMPOUNDER", "HYPER-COMPOUNDER"]:
                        export_data = {k: v for k, v in data.items() if k not in ["raw_vacuum_data", "concall_stats", "status"]}
                        qualified_records.append(export_data)
                        if symbol not in previous_tickers: new_candidates.append(export_data)
            except Exception as exc: logging.warning(f"{symbol} error: {exc}")
            if idx % 100 == 0 and idx > 0: logging.info(f"Progress: Processed {idx}/{len(symbols)} pending tickers...")
    try:
        with open(MANIFEST_FILE, 'w', encoding='utf-8') as f: json.dump(concall_manifest, f, indent=4)
    except Exception as e: logging.error(f"Failed to save manifest file: {e}")
    if qualified_records:
        df_out = pd.DataFrame(qualified_records)
        front_cols = ['ticker', 'classification', 'market_cap', 'valuation_context', 'earnings_quality_analysis', 'pricing_power_analysis', 'secondary_red_flags', 'qualification_reason']
        back_cols = [c for c in df_out.columns if c not in front_cols]
        df_out = df_out[front_cols + back_cols]
        df_out.to_csv(CSV_FILE, index=False)
        send_telegram_alert(new_candidates, df_out)

if __name__ == "__main__":
    main()
