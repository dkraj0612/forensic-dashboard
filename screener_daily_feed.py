import os
import re
import sys
import time
import json
import random
import zipfile
import datetime
import logging
import threading
import concurrent.futures
from urllib.parse import urljoin
from bs4 import BeautifulSoup
from io import BytesIO, StringIO
from typing import List, Dict, Tuple

try:
    from curl_cffi import requests as tls_requests
    import pandas as pd
    import fitz  # PyMuPDF
    import textstat
    import google.generativeai as genai
except ImportError:
    print("[CRITICAL] Missing libraries. Run: pip install curl_cffi pandas beautifulsoup4 pymupdf textstat google-generativeai")
    sys.exit(1)

# =====================================================================
# SYSTEM CONFIGURATION & ZERO-DATA-LOSS GUARDRAILS
# =====================================================================
OUTPUT_DIR = "market_pulse_data"

# TELEGRAM & AI SECURITY
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

# Thread locks and Thread-Local Storage for true parallel networking
STATE_LOCK = threading.Lock()
thread_local = threading.local()

def get_session():
    """Generates a dedicated, isolated browser session for each individual thread."""
    if not hasattr(thread_local, "session"):
        thread_local.session = tls_requests.Session(impersonate="chrome124")
        thread_local.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Referer': 'https://www.nseindia.com/'
        })
    return thread_local.session

# Initialize Gemini Client natively
if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)
    llm_model = genai.GenerativeModel('gemini-1.5-flash')
else:
    llm_model = None
    print("[!] GEMINI_API_KEY missing from environment variables. AI analysis bypassed.")

NOW = datetime.datetime.today()
TODAY_STR = NOW.strftime('%Y-%m-%d')
STOCKS_DIR = os.path.join(OUTPUT_DIR, "Stocks")
LOG_DIR = os.path.join(OUTPUT_DIR, "System_Logs")
BHAV_DIR = os.path.join(OUTPUT_DIR, "Market_Bhavcopies")
PROGRESS_FILE = os.path.join(LOG_DIR, f"completed_tickers_{TODAY_STR}.txt")
METRICS_FILE = os.path.join(LOG_DIR, f"daily_metrics_{TODAY_STR}.json")

DDMMYYYY = NOW.strftime('%d%m%Y')
DD_MMM_YYYY = NOW.strftime('%d%b%Y').upper()
MMM = NOW.strftime('%b').upper()
YYYY = NOW.strftime('%Y')

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')

def generate_lookback_patterns():
    weekday = NOW.weekday()
    days_to_check = 4 if weekday == 0 else (3 if weekday == 6 else 2)
    patterns = ["today", "1 day ago", "2 days ago", "3 days ago"]
    for i in range(days_to_check):
        target_date = NOW - datetime.timedelta(days=i)
        day = target_date.strftime('%d')
        day_strip = target_date.strftime('%e').strip()
        mon_short = target_date.strftime('%b')
        year = target_date.strftime('%Y')
        patterns.extend([
            f"{day} {mon_short}", 
            f"{day_strip} {mon_short}", 
            f"{mon_short} {day}", 
            f"{mon_short} {day_strip}", 
            f"{day}-{mon_short}-{year}"
        ])
    return list(set(p.lower() for p in patterns))

VALID_DATE_PATTERNS = generate_lookback_patterns()

TARGET_CATEGORIES = {
    "Results": [r'financial result', r'quarterly result', r'audited result', r'unaudited result', r'results'],
    "Concalls": [r'transcript', r'audio', r'concall', r'earnings call', r'call transcript'],
    "Dividend": [r'dividend', r'interim dividend', r'final dividend', r'book closure for dividend'],
    "Bonus": [r'bonus', r'bonus issue', r'allotment of bonus'],
    "Order_Book": [r'order', r'contract', r'award', r'letter of intent', r'loi', r'tender'],
    "Fund_Raise": [r'fund raising', r'qip', r'preferential issue', r'rights issue', r'qualified institutional'],
    "New_Projects": [r'new project', r'commissioning', r'capacity expansion', r'commercial production'],
    "Major_Deals": [r'block deal', r'bulk deal', r'strategic partnership', r'acquisition of', r'joint venture'],
    "Business_Updates": [r'business update', r'monthly update', r'sales volume', r'provisional figures'],
    "SAST": [r'sast', r'substantial acquisition', r'reg.*29', r'disclosure under regulation'],
    "SHP": [r'shareholding pattern', r'shp', r'shareholding statement'],
    "Insider_Trades": [r'insider', r'reg.*7', r'insider trade', r'prohibition of insider'],
    "Buyback_Split": [r'buyback', r'buy back', r'stock split', r'sub-division', r'sub division'],
    "Pledge_Action": [r'pledge', r'revocation of pledge', r'encumbrance'],
    "Regulatory_Risk": [r'usfda', r'form 483', r'sebi', r'default', r'nclt', r'insolvency', r'tax', r'search and seizure'],
    "Management_Change": [r'resignation', r'cessation', r'appointment of director', r'change in management', r'cfo'],
    "Credit_Rating": [r'credit rating', r'crisil', r'icra', r'care rating', r'rating upgrade', r'rating downgrade']
}

# =====================================================================
# STATE TRACKING ENGINE
# =====================================================================
def load_metrics():
    if os.path.exists(METRICS_FILE):
        try:
            with open(METRICS_FILE, 'r') as f:
                data = json.load(f)
                data['category_counts'] = {k: set(v) for k, v in data.get('category_counts', {}).items()}
                data['total_unique_stocks'] = set(data.get('total_unique_stocks', []))
                data['summaries'] = data.get('summaries', [])
                return data
        except Exception:
            pass
            
    return {
        "bhavcopy_sec": False, 
        "bhavcopy_fno": False,
        "category_counts": {cat: set() for cat in TARGET_CATEGORIES.keys()},
        "total_unique_stocks": set(),
        "summaries": []
    }

def save_metrics():
    data_to_save = {
        "bhavcopy_sec": PIPELINE_METRICS["bhavcopy_sec"],
        "bhavcopy_fno": PIPELINE_METRICS["bhavcopy_fno"],
        "category_counts": {k: list(v) for k, v in PIPELINE_METRICS["category_counts"].items()},
        "total_unique_stocks": list(PIPELINE_METRICS["total_unique_stocks"]),
        "summaries": PIPELINE_METRICS.get("summaries", [])
    }
    with open(METRICS_FILE, 'w') as f: 
        json.dump(data_to_save, f)

PIPELINE_METRICS = load_metrics()

# =====================================================================
# MULTIMODAL AI SELECTION & ANALYSIS ENGINE
# =====================================================================
def generate_ai_summary(ticker: str, category: str, pdf_bytes: bytes) -> str:
    if not llm_model or not pdf_bytes: 
        return "⚪ [NEUTRAL] Document parsed safely without AI."
        
    prompt = f"""
    You are a strict quantitative trading algorithm analyzing an NSE India corporate filing ({category}) for {ticker}.
    
    Step 1: Classify the objective directional market impact of this document. Choose EXACTLY ONE:
    🟢 [BULLISH] (Strong positive growth, insider buying, major orders, dividend hikes, new client wins)
    🔴 [BEARISH] (Profit drops, revenue contractions, insider selling, governance stress, auditor resignation)
    📦 [ORDER BOOK] (Specific layout tracking material contract wins, infrastructure awards, or order book sizing)
    🤝 [NEW CLIENTS] (Strategic business partnerships, global client onboarding, commercial alliances)
    ⚠️ [RISK] (Auditor resignations, tax raids, FDA warnings, credit downgrades, promoter pledging)
    📊 [METRICS] (Monthly sales volumes, provisional business updates, loan growth)
    ⚪ [NEUTRAL] (Routine compliance filings, calendar updates, expected standard operational outcomes)
    
    Step 2: Extract the single most critical numerical fact or corporate action justifying this classification.
    
    Output format MUST be strictly:
    [CLASSIFICATION] One short, punchy justification sentence. Do not include introductory text.
    """
    try:
        pdf_document = {"mime_type": "application/pdf", "data": pdf_bytes}
        response = llm_model.generate_content([prompt, pdf_document])
        clean_summary = response.text.replace('**', '').replace('*', '').strip()
        
        if len(clean_summary) > 200: 
            clean_summary = clean_summary[:197] + "..."
            
        return clean_summary
        
    except Exception as e:
        print(f"      [!] AI Classification failed for {ticker}: {e}")
        return "⚪ [NEUTRAL] Document logged but objective AI classification timed out."

def send_telegram_summary():
    msg = "📊 *Market Sweeper AI Intelligence Report*\n"
    msg += f"📅 *Date:* {TODAY_STR}\n"
    msg += "━━━━━━━━━━━━━━━━━━━━\n"
    msg += "📦 *NSE Bhavcopies:*\n"
    msg += f"• Cash (SEC): {'✅ Downloaded' if PIPELINE_METRICS['bhavcopy_sec'] else '❌ Failed/Missing'}\n"
    msg += f"• F&O (FNO): {'✅ Downloaded' if PIPELINE_METRICS['bhavcopy_fno'] else '❌ Failed/Missing'}\n"
    msg += "━━━━━━━━━━━━━━━━━━━━\n"
    
    summaries = PIPELINE_METRICS.get("summaries", [])
    
    if not summaries:
        msg += "└ `No major financial documents were processed today.`\n"
    else:
        msg += f"🏢 *Objective Action Signals ({len(summaries)}):*\n\n"
        for summary in summaries:
            if len(msg) + len(summary) > 3900:
                msg += "• *CRITICAL:* Additional alerts truncated. Review filesystem logs.\n"
                break
            msg += f"{summary}\n\n"

    # --- AUDIT TRAIL LAYER (Permanent GitHub Record) ---
    audit_log_path = os.path.join(LOG_DIR, f"telegram_audit_{TODAY_STR}.md")
    with open(audit_log_path, 'w', encoding='utf-8') as f:
        f.write(msg)
    print(f"\n[+] Audit Trail Saved: Telegram payload committed to {audit_log_path}")

    # Console Mirror
    print("\n" + "="*70)
    print("=== CONSOLE LOG STREAM BACKUP: DAILY MARKET INTELLIGENCE REPORT ===")
    print("="*70)
    print(msg.replace('*', '').replace('`', ''))
    print("="*70 + "\n")

    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID: 
        return

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID, 
        "text": msg, 
        "parse_mode": "Markdown", 
        "disable_web_page_preview": True
    }
    
    try:
        temp_session = tls_requests.Session(impersonate="chrome124")
        resp = temp_session.post(url, json=payload, timeout=15)
        
        if resp.status_code == 200: 
            print("      [OK] Telegram summary notification dispatched securely.")
        else: 
            print(f"      [!] Telegram dispatch rejected (Status {resp.status_code}): {resp.text}")
            
    except Exception as e: 
        print(f"      [!] Socket termination during Telegram pipeline transmission: {e}")

# =====================================================================
# INTEGRATED NLP ENGINE & ROBUST TEXT EXTRACTOR
# =====================================================================
class TranscriptIntelligence:
    def __init__(self):
        self.regex_map = {
            "Hype": re.compile(r'\b(multifold|exponential|game changer|value unlocking|multibagger|unprecedented|robust pipeline|paradigm shift|phenomenal|unmatched growth)\b', re.IGNORECASE),
            "Delivery": re.compile(r'\b(commissioned|commercial production|realized|cash flow|debt reduction|completed|on stream|disbursed|royalty paid|capacity utilization|ebitda accretive)\b', re.IGNORECASE),
            "Evasion": re.compile(r'\b(unseasonal|macro headwinds|temporary blip|will get back to you|operator activity|supply chain|cyclical downturn|take it offline|next quarter|details not handy)\b', re.IGNORECASE),
            "Governance_Stress": re.compile(r'\b(auditor resignation|pledge|related party|working capital stretch|debtor days|sebi|nclt|margin compression|promoter share|delay in filing|qualification)\b', re.IGNORECASE)
        }

    def _clean_text_stream(self, text: str) -> str:
        if not text: 
            return ""
        text = re.sub(r'[−–—]', '-', text)
        text = "".join(ch for ch in text if ch.isprintable() or ch in ['\n', '\t', '\r'])
        return re.sub(r'[ \t]+', ' ', text)

    def extract_and_structure_transcript(self, pdf_bytes: bytes) -> Tuple[str, str, int]:
        raw_lines = []
        try:
            doc = fitz.open(stream=pdf_bytes, filetype="pdf")
            page_count = doc.page_count
            for page in doc:
                for line in page.get_text("text").split('\n'):
                    line_stripped = line.strip()
                    if line_stripped: 
                        raw_lines.append(line_stripped)
            doc.close()
        except Exception: 
            return "", "", 0

        current_speech_accumulator = [self._clean_text_stream(line) for line in raw_lines]
        
        prep_text = " ".join(current_speech_accumulator[:100]).strip()
        qa_text = " ".join(current_speech_accumulator[100:]).strip()
        
        return prep_text, qa_text, page_count

    def calculate_metrics(self, text: str) -> Dict:
        words = text.split()
        word_count = len(words) if len(words) > 0 else 1
        
        counts = {k: len(reg.findall(text)) for k, reg in self.regex_map.items()}
        densities = {f"{k}_Density_Per_10k": (v / word_count) * 10000 for k, v in counts.items()}
        
        return {
            "Word_Count": word_count, 
            "Gunning_Fog_Index": textstat.gunning_fog(text) if text.strip() else 0.0, 
            **densities, 
            **counts
        }

    def calculate_behavioral_signals(self, text_metrics: dict) -> List[str]:
        signals = []
        
        def get_f(val):
            try: 
                return float(str(val).replace('%', '').replace(',', '').strip())
            except (ValueError, AttributeError, TypeError): 
                return None
                
        hype = get_f(text_metrics.get("Hype_Density_Per_10k"))
        delivery = get_f(text_metrics.get("Delivery_Density_Per_10k"))
        
        if hype and delivery and hype > 5.0 and delivery < 1.0: 
            signals.append("🚨 **[BEHAVIORAL] 'Hype/Delivery Divergence':** Promoter using aggressive buzzwords (>5.0 density) with minimal operational delivery terminology.")
            
        if not signals: 
            signals.append("✅ **[STABILITY]** No severe behavioral manipulation thresholds breached in the transcript language.")
            
        return signals

quant_engine = TranscriptIntelligence()

def convert_to_basic_markdown(pdf_bytes: bytes, ticker: str, category: str, clean_type: str, source_url: str, ai_summary: str) -> str:
    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        md_lines = [
            f"# {ticker} - {category} - {clean_type}", 
            f"**Extraction Date:** {TODAY_STR}",
            f"**Source URL:** [View Original Document]({source_url})\n---",
            f"### 🤖 AI Intelligence Summary\n> {ai_summary}\n---"
        ]
        
        total_text_length = 0
        for page_num in range(len(doc)):
            text = doc.load_page(page_num).get_text("text")
            total_text_length += len(text.strip())
            if text.strip(): 
                md_lines.extend([f"## Page {page_num + 1}", text.strip(), "---\n"])
        doc.close()
        
        if total_text_length < 150:
            md_lines.append("> ⚠️ **SCANNED IMAGE DETECTED:** This document appears to be a scanned image or handwritten filing. Standard Python text extraction bypassed.")
            
        return "\n\n".join(md_lines)
        
    except Exception as e: 
        return f"# {ticker} - {category} - {clean_type}\n**Source URL:** [View Original Document]({source_url})\n\n> ❌ **PARSING ERROR:** The document was corrupted. Error: {e}"

def generate_enterprise_markdown(pdf_bytes: bytes, ticker: str, category: str, clean_type: str, source_url: str, ai_summary: str) -> str:
    prep_text, qa_text, page_count = quant_engine.extract_and_structure_transcript(pdf_bytes)
    combined_text = f"{prep_text} {qa_text}"
    
    if not combined_text.strip() or page_count <= 3:
        return convert_to_basic_markdown(pdf_bytes, ticker, category, clean_type, source_url, ai_summary)

    total_metrics = quant_engine.calculate_metrics(combined_text)
    warning_flags = quant_engine.calculate_behavioral_signals(total_metrics)

    return f"""---
metadata:
  company_name: "{ticker}"
  call_date: "{TODAY_STR}"
  reporting_period: "{clean_type}"
  source_url: "{source_url}"
telemetry_matrix:
  obfuscation_fog_index: {total_metrics['Gunning_Fog_Index']:.2f}
  total_word_volume: {total_metrics['Word_Count']}
---

# Concall NLP Analysis: {ticker}
**Source URL:** [Listen/Read Original]({source_url})

---
### 🤖 AI Intelligence Summary
> {ai_summary}

---
## 1. Behavioral Warning Flags
{chr(10).join([f"* {flag}" for flag in warning_flags])}

---
## SECTION A: PREPARED STATEMENTS
{prep_text[:5000]}... *(Truncated for storage. See Source URL)*
"""

# =====================================================================
# NSE DATA PIPELINE ENGINE
# =====================================================================
def download_nse_bhavcopies():
    os.makedirs(BHAV_DIR, exist_ok=True)
    temp_session = tls_requests.Session(impersonate="chrome124")
    
    sec_url = f"https://nsearchives.nseindia.com/products/content/sec_bhavdata_full_{DDMMYYYY}.csv"
    sec_save_path = os.path.join(BHAV_DIR, f"SEC_BHAV_{TODAY_STR}.csv")
    
    if not os.path.exists(sec_save_path):
        try:
            print("      [~] Requesting SEC Bhavdata...")
            resp = temp_session.get(sec_url, timeout=20)
            if resp.status_code == 200:
                with open(sec_save_path, 'wb') as f: 
                    f.write(resp.content)
                with STATE_LOCK: 
                    PIPELINE_METRICS["bhavcopy_sec"] = True
                    save_metrics()
        except Exception: 
            pass
    else:
        with STATE_LOCK: 
            PIPELINE_METRICS["bhavcopy_sec"] = True

    YYYYMMDD = NOW.strftime('%Y%m%d')
    fno_url_udiff = f"https://nsearchives.nseindia.com/content/fo/BhavCopy_NSE_FO_0_0_0_{YYYYMMDD}_F_0000.csv.zip"
    fno_save_path = os.path.join(BHAV_DIR, f"FNO_BHAV_{TODAY_STR}.csv")
    
    if not os.path.exists(fno_save_path):
        try:
            print("      [~] Requesting FNO Bhavdata Archive...")
            resp = temp_session.get(fno_url_udiff, timeout=20)
            if resp.status_code == 200:
                with zipfile.ZipFile(BytesIO(resp.content)) as z:
                    csv_filename = z.namelist()[0]
                    with z.open(csv_filename) as f:
                        df = pd.read_csv(f)
                        instrument_col = 'FinInstrmTp' if 'FinInstrmTp' in df.columns else 'INSTRUMENT'
                        if instrument_col in df.columns: 
                            df = df[df[instrument_col] != 'OPTSTK']
                        df.to_csv(fno_save_path, index=False)
                        
                with STATE_LOCK: 
                    PIPELINE_METRICS["bhavcopy_fno"] = True
                    save_metrics()
        except Exception: 
            pass
    else:
        with STATE_LOCK: 
            PIPELINE_METRICS["bhavcopy_fno"] = True

def get_dynamic_nse_list():
    url = "https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv"
    try:
        temp_session = tls_requests.Session(impersonate="chrome124")
        resp = temp_session.get(url, timeout=20)
        if resp.status_code == 200:
            return pd.read_csv(StringIO(resp.text))['SYMBOL'].dropna().astype(str).str.strip().unique().tolist()
        return []
    except Exception: 
        return []

def get_completed_today():
    if not os.path.exists(PROGRESS_FILE): 
        return set()
    with open(PROGRESS_FILE, 'r') as f: 
        return set(line.strip() for line in f.readlines())

def mark_completed(ticker):
    with STATE_LOCK:
        with open(PROGRESS_FILE, 'a') as f: 
            f.write(f"{ticker}\n")

def sanitize_filename(text: str) -> str:
    clean = re.sub(r'[\\/*?:"<>|\'’]', "", text)
    return clean.replace(" ", "_").strip()[:100]

def is_within_temporal_window(element_text: str) -> bool:
    text_lower = element_text.lower()
    return any(pattern in text_lower for pattern in VALID_DATE_PATTERNS)

def identify_category(link_text: str) -> str:
    text_lower = link_text.lower()
    for category, patterns in TARGET_CATEGORIES.items():
        for pattern in patterns:
            if re.search(pattern, text_lower): 
                return category
    return None

def register_successful_metric(ticker: str, category: str):
    with STATE_LOCK:
        PIPELINE_METRICS["category_counts"][category].add(ticker)
        PIPELINE_METRICS["total_unique_stocks"].add(ticker)
        save_metrics()

def extract_stock_data(ticker):
    local_session = get_session()
    url = f"https://www.screener.in/company/{ticker}/"
    
    try:
        resp = local_session.get(url, timeout=6)
        if resp.status_code != 200: 
            return
    except Exception: 
        return

    soup = BeautifulSoup(resp.text, 'html.parser')

    for a in soup.find_all('a'):
        href = a.get('href', '')
        link_text = a.get_text(strip=True)
        
        if not href or not link_text: 
            continue

        parent = a.find_parent(['li', 'tr', 'div'])
        context_text = parent.get_text(" ", strip=True) if parent else link_text

        matched_category = identify_category(link_text)
        if not matched_category or not is_within_temporal_window(context_text): 
            continue

        clean_type = sanitize_filename(link_text)
        is_audio = "audio" in link_text.lower()
        
        ext = ".mp3" if is_audio else ".md"
        filename = f"{ticker}_{matched_category}_{clean_type}{ext}"
        
        category_dir = os.path.join(STOCKS_DIR, ticker, matched_category)
        save_path = os.path.join(category_dir, filename)
        json_path = save_path.replace('.md', '.json').replace('.mp3', '.json')

        if not os.path.exists(save_path) and not os.path.exists(json_path):
            os.makedirs(category_dir, exist_ok=True)
            
            try:
                full_url = urljoin("https://www.screener.in", href)
                if '.pdf' in full_url.lower() or 'concalls' in full_url.lower() or 'announcements' in full_url.lower():
                    
                    file_resp = local_session.get(full_url, timeout=30)
                    
                    if file_resp.status_code == 200:
                        content_type = file_resp.headers.get("Content-Type", "").lower()
                        
                        if "text/html" in content_type or len(file_resp.content) < 1000:
                            continue
                        
                        ai_decision_string = "⚪ [NEUTRAL] Audio File Logged."
                        
                        if is_audio:
                            # Audio storage & burn cycle
                            with open(save_path, 'wb') as f: 
                                f.write(file_resp.content)
                                
                            with STATE_LOCK:
                                PIPELINE_METRICS.setdefault("summaries", []).append(f"• *{ticker}* ({matched_category}): Audio recording archived successfully.\n  └ [🎧 Listen to Audio]({full_url})")
                                
                            if os.path.exists(save_path): 
                                os.remove(save_path)
                        else:
                            # PDF In-Memory AI Extraction
                            print(f"      [~] Extracting AI insights for {ticker}...")
                            ai_decision_string = generate_ai_summary(ticker, matched_category, file_resp.content)
                            
                            with STATE_LOCK:
                                PIPELINE_METRICS.setdefault("summaries", []).append(f"• *{ticker}* ({matched_category}): {ai_decision_string}\n  └ [📄 Source]({full_url})")
                            
                            # Save Markdown with AI Injection
                            md_content = None
                            if matched_category == "Concalls":
                                md_content = generate_enterprise_markdown(file_resp.content, ticker, matched_category, clean_type, full_url, ai_decision_string)
                            else:
                                md_content = convert_to_basic_markdown(file_resp.content, ticker, matched_category, clean_type, full_url, ai_decision_string)
                            
                            with open(save_path, 'w', encoding='utf-8') as f: 
                                f.write(md_content)
                        
                        # --- THE JSON STRUCTURED DATA EXPORT ---
                        json_metadata = {
                            "ticker": ticker,
                            "category": matched_category,
                            "extraction_date": TODAY_STR,
                            "ai_classification": ai_decision_string.split("]")[0] + "]" if "]" in ai_decision_string else "UNKNOWN",
                            "ai_summary": ai_decision_string,
                            "source_url": full_url,
                            "file_type": "audio" if is_audio else "pdf"
                        }
                        
                        with open(json_path, 'w', encoding='utf-8') as f:
                            json.dump(json_metadata, f, indent=4)
                            
                        print(f"      [+] SAVED -> {ticker} (JSON & Audit Files)")
                        register_successful_metric(ticker, matched_category)
                                
            except Exception as e:
                print(f"      [!] Processing failure on {filename}: {e}")

# =====================================================================
# HIGH-SPEED CONCURRENT MAIN EXECUTION LOOP
# =====================================================================
def main():
    os.makedirs(STOCKS_DIR, exist_ok=True)
    os.makedirs(LOG_DIR, exist_ok=True)
    
    print(f"\n{'='*60}")
    print("=== ENTERPRISE SWEEPER (NLP + CONCURRENCY + AI ENABLED) ===")
    print(f"{'='*60}")
    
    download_nse_bhavcopies()
    
    all_nse_tickers = get_dynamic_nse_list()
    
    if not all_nse_tickers: 
        print("[!] Critical failure pulling master NSE stock vector lists. Pipeline killed.")
        sys.exit(1)
        
    completed_today = get_completed_today()
    remaining_tickers = [t for t in all_nse_tickers if t not in completed_today]
    
    print(f"\n[*] Commencing high-speed concurrent sweep of {len(remaining_tickers)} pending stocks...")
    
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        future_to_ticker = {executor.submit(extract_stock_data, ticker): ticker for ticker in remaining_tickers}
        
        for idx, future in enumerate(concurrent.futures.as_completed(future_to_ticker), 1):
            ticker = future_to_ticker[future]
            try:
                future.result()
                mark_completed(ticker)
                sys.stdout.write(f"\r>>> Processed [{idx}/{len(remaining_tickers)}] Tickers (Latest: {ticker}) ...")
                sys.stdout.flush()
            except Exception:
                pass 

    print("\n\n=== REAL-TIME SWEEP CONCLUDED SUCCESSFULLY ===")
    
    send_telegram_summary()

if __name__ == "__main__":
    main()
