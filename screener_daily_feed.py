
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


import gc
import html

# Disable Telemetry to prevent unnecessary Hugging Face pings
os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"

def bootstrap_docling():
    """
    Boots up Docling with Low-RAM OCR for Scanned PDFs. 
    Waits up to ~2 hours if Hugging Face rate limits the IP.
    """
    from docling.document_converter import DocumentConverter, PdfFormatOption
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import PdfPipelineOptions

    # Enterprise low-RAM config for 100+ page scans
    pipeline_options = PdfPipelineOptions()
    pipeline_options.do_ocr = True                 # Actively read scanned images
    pipeline_options.generate_page_images = False  # NEVER save images to RAM

    # Wait delays: 1 min, 5 mins, 15 mins, 30 mins, 60 mins
    wait_times = [60, 300, 900, 1800, 3600] 
    
    for attempt, wait in enumerate(wait_times, 1):
        try:
            print(f"[*] Booting Docling AI Models (Attempt {attempt}/{len(wait_times)})...")
            converter = DocumentConverter(
                allowed_formats=[InputFormat.PDF],
                format_options={
                    InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options)
                }
            )
            print("[+] Docling successfully loaded into memory!")
            return converter
            
        except Exception as e:
            if "429" in str(e) or "Too Many Requests" in str(e):
                print(f"      [!] Hugging Face rate limit hit. Waiting {wait} seconds to try again...")
                time.sleep(wait)
            else:
                print(f"      [!] Critical Docling Boot Error: {e}")
                break
                
    print("[!] Failed to boot Docling after max retries. Pipeline will route to offline PyMuPDF.")
    return None

# Initialize Docling Globally
docling_converter = bootstrap_docling()


# =====================================================================
# SYSTEM CONFIGURATION & ZERO-DATA-LOSS GUARDRAILS
# =====================================================================
OUTPUT_DIR = "market_pulse_data"

# TELEGRAM & AI SECURITY
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

# INITIALIZE GEMINI MODELS GLOBALLY
if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)
    ai_model = genai.GenerativeModel('gemini-2.5-flash') 
    llm_model = genai.GenerativeModel('gemini-2.5-flash')
else:
    ai_model = None
    llm_model = None
    print("[!] Warning: GEMINI_API_KEY is not set. AI Analysis will be skipped.")


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
    """Multimodal fallback for direct PDF binary processing."""
    import os
    import google.generativeai as genai
    
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key or not pdf_bytes: 
        return "⚪ [NEUTRAL] Document parsed safely without AI."
        
    # BULLETPROOF FIX: Initialize the model locally inside the function
    genai.configure(api_key=api_key)
    local_llm_model = genai.GenerativeModel('gemini-2.5-flash')
        
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
        response = local_llm_model.generate_content([prompt, pdf_document])
        clean_summary = response.text.replace('**', '').replace('*', '').strip()
        
        if len(clean_summary) > 200: 
            clean_summary = clean_summary[:197] + "..."
            
        return clean_summary
        
    except Exception as e:
        print(f"      [!] AI Classification failed for {ticker}: {e}")
        return "⚪ [NEUTRAL] Document logged but objective AI classification timed out."



def send_telegram_summary():
    msg = "📊 <b>Market Sweeper AI Intelligence Report</b>\n"
    msg += f"📅 <b>Date:</b> {TODAY_STR}\n"
    msg += "━━━━━━━━━━━━━━━━━━━━\n"
    msg += "📦 <b>NSE Bhavcopies:</b>\n"
    msg += f"• Cash (SEC): {'✅ Downloaded' if PIPELINE_METRICS['bhavcopy_sec'] else '❌ Failed/Missing'}\n"
    msg += f"• F&O (FNO): {'✅ Downloaded' if PIPELINE_METRICS['bhavcopy_fno'] else '❌ Failed/Missing'}\n"
    msg += "━━━━━━━━━━━━━━━━━━━━\n"
    
    summaries = PIPELINE_METRICS.get("summaries", [])
    
    if not summaries:
        msg += "└ <i>No major financial documents were processed today.</i>\n"
    else:
        msg += f"🏢 <b>Objective Action Signals ({len(summaries)}):</b>\n\n"
        for summary in summaries:
            if len(msg) + len(summary) > 3900:
                msg += "• <b>CRITICAL:</b> Additional alerts truncated. Review filesystem logs.\n"
                break
            msg += f"{summary}\n\n"

    # --- AUDIT TRAIL LAYER (Permanent GitHub Record) ---
    audit_log_path = os.path.join(LOG_DIR, f"telegram_audit_{TODAY_STR}.md")
    with open(audit_log_path, 'w', encoding='utf-8') as f:
        f.write(msg)
    print(f"\n[+] Audit Trail Saved: Telegram payload committed to {audit_log_path}")

    # Console Mirror (Strips HTML tags for clean console viewing)
    print("\n" + "="*70)
    print("=== CONSOLE LOG STREAM BACKUP: DAILY MARKET INTELLIGENCE REPORT ===")
    print("="*70)
    clean_console_msg = msg.replace('<b>', '').replace('</b>', '').replace('<i>', '').replace('</i>', '')
    print(clean_console_msg)
    print("="*70 + "\n")

    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID: 
        return

    # FIXED: Restored clean URL format
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID, 
        "text": msg, 
        "parse_mode": "HTML", 
        "disable_web_page_preview": True
    }
    
    try:
        temp_session = tls_requests.Session(impersonate="chrome124")
        resp = temp_session.post(url, json=payload, timeout=60)
        
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
            resp = temp_session.get(sec_url, timeout=60)
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
            resp = temp_session.get(fno_url_udiff, timeout=60)
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
        resp = temp_session.get(url, timeout=60)
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
    
    # 1. Standard Date Patterns
    if any(pattern in text_lower for pattern in VALID_DATE_PATTERNS):
        return True
        
    # 2. Screener Intraday Patterns ("4m ago", "2h ago")
    if re.search(r'\b\d+\s*[mh]\s*ago\b', text_lower):
        return True
    if re.search(r'\b\d+\s*(mins?|hours?)\s*ago\b', text_lower):
        return True
        
    return False

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

def safe_ai_classification(text_content: str) -> str:
    """Safely calls Gemini API with exponential backoff for rate limits."""
    import os
    import random
    import time
    import google.generativeai as genai
    
    try:
        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key: 
            return "AI Skipped: No API Key Provided."
            
        # BULLETPROOF FIX: Initialize a completely fresh model directly inside the function
        genai.configure(api_key=api_key)
        fresh_model = genai.GenerativeModel('gemini-1.5-flash')
            
        max_retries = 3
        base_delay = 15 
        
        for attempt in range(max_retries):
            try:
                # Trim to 30,000 characters to stay within context windows safely
                response = fresh_model.generate_content(
                    f"Analyze this financial document. Provide a 2-3 sentence executive summary focusing on the key takeaways, material impacts, and any red flags. Format the summary cleanly.\n\nDocument text:\n{text_content[:30000]}"
                ) 
                return response.text.strip().replace('\n', ' ')
            except Exception as api_error:
                if '429' in str(api_error) or 'quota' in str(api_error).lower():
                    wait_time = base_delay * (2 ** attempt) + random.uniform(1, 5)
                    print(f"      [!] API Rate Limit Hit. Sleeping for {wait_time:.1f}s...")
                    time.sleep(wait_time)
                else:
                    return f"API Error: {str(api_error)}"
                    
        return "AI Skipped: Rate limit exceeded after maximum retries."
    except Exception as critical_error:
        return f"Critical Execution Error: {str(critical_error)}"


def extract_stock_data(ticker):
    local_session = get_session()
    url = f"https://www.screener.in/company/{ticker}/"
    downloaded_assets = [] 
    
    try:
        resp = local_session.get(url, timeout=60)
        
        # Guardrail: Expose Screener/Cloudflare Bans
        if resp.status_code == 429:
            print(f"\n[!!!] SCREENER BLOCKED YOU (429 Too Many Requests). Thread pausing...")
            time.sleep(10)
            return downloaded_assets
        if resp.status_code == 403:
            print(f"\n[!!!] CLOUDFLARE BLOCKED YOU (403 Bot Detected) on {ticker}.")
            return downloaded_assets
        if resp.status_code != 200: 
            return downloaded_assets
            
    except Exception as e: 
        return downloaded_assets

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
        
        ext = ".mp3" if is_audio else ".pdf" 
        filename = f"{ticker}_{matched_category}_{clean_type}{ext}"
        
        category_dir = os.path.join(STOCKS_DIR, ticker, matched_category)
        save_path = os.path.join(category_dir, filename)
        json_path = os.path.join(category_dir, f"{ticker}_{matched_category}_{clean_type}.json")
        full_url = urljoin("https://www.screener.in", href)

        if os.path.exists(json_path):
            continue

        if os.path.exists(save_path):
            print(f"      [~] FOUND UNPROCESSED FILE -> {filename} (Queueing for AI)")
            downloaded_assets.append({
                "ticker": ticker,
                "category": matched_category,
                "clean_type": clean_type,
                "file_path": save_path,
                "json_path": json_path,
                "url": full_url,
                "is_audio": is_audio
            })
            continue

        if not os.path.exists(save_path) and not os.path.exists(json_path):
            os.makedirs(category_dir, exist_ok=True)
            
            try:
                if '.pdf' in full_url.lower() or 'concalls' in full_url.lower() or 'announcements' in full_url.lower():
                    
                    file_resp = local_session.get(full_url, timeout=60)
                    
                    if file_resp.status_code == 200:
                        content_type = file_resp.headers.get("Content-Type", "").lower()
                        
                        if "text/html" in content_type or len(file_resp.content) < 1000:
                            continue
                        
                        with open(save_path, 'wb') as f: 
                            f.write(file_resp.content)
                            
                        print(f"      [+] DOWNLOADED RAW FILE -> {filename}")
                        
                        downloaded_assets.append({
                            "ticker": ticker,
                            "category": matched_category,
                            "clean_type": clean_type,
                            "file_path": save_path,
                            "json_path": json_path,
                            "url": full_url,
                            "is_audio": is_audio
                        })
                        
            except Exception as e:
                print(f"      [!] Processing failure on {filename}: {e}")
                
    return downloaded_assets

# =====================================================================
# HIGH-SPEED CONCURRENT MAIN EXECUTION LOOP
# ====================================================================
def main():
    import concurrent.futures
    import time
    import fitz  # PyMuPDF for lightning-fast page counting
    import gc    # Added for mandatory memory clearing
    import html  # Added for safe Telegram escaping
    
    os.makedirs(STOCKS_DIR, exist_ok=True)
    os.makedirs(LOG_DIR, exist_ok=True)
    
    print(f"\n{'='*60}")
    print("=== ENTERPRISE SWEEPER (DOCLING + CONCURRENCY + AI ENABLED) ===")
    print(f"{'='*60}")
    
    download_nse_bhavcopies()
    
    #all_nse_tickers = 
    all_nse_tickers = [
        "AARVI", "ADVENTHTL", "AEROFLEX", "AIIL", "AIRAN", "AMBIKCO", 
        "ANIKINDS", "ANMOL", "APOLSINHOT", "BALMLAWRIE", "BIMETAL", 
        "BLISSGVS", "CALSOFT", "CERA", "CRAFTSMAN", "DEEDEV", "DIACABS", 
        "DPSCLTD", "ENDURANCE", "ELLEN", "FAZE3Q", "GEEKAYWIRE", "GICRE", 
        "GODREJAGRO", "GOLDENTOBC", "GREAVESCOT", "HATSUN", "HERANBA", 
        "HILTON", "HINDCON", "HYBRIDFIN", "INCREDIBLE", "IPL", "KALYANI", 
        "KERNEX", "KIMS", "KILITCH", "KNAGRI", "KRISHANA", "KRISHIVAL", 
        "LFIC", "LLOYDSENT", "LYKALABS", "M&MFIN", "MAHABANK", "MBAPL", 
        "MEDICO", "MENNPIS", "MKPL", "NAGREEKEXP", "NDGL", "PGHL", 
        "RAMCOSYS", "RKEC", "ROSSELLIND", "RPPL", "SBIN", "SOLEX", 
        "SONACOMS", "SPCENET", "STEL", "SUNDARMFIN", "TARACHAND", "THEJO", 
        "VAIBHAVGBL", "VMSTMT", "WESTLIFE", "ZFSTEERING", "ZIMLAB", "ZODIAC"
    ]

    if not all_nse_tickers: 
        print("[!] Critical failure pulling master NSE stock vector lists. Pipeline killed.")
        sys.exit(1)
        
    completed_today = get_completed_today()
    remaining_tickers = [t for t in all_nse_tickers if t not in completed_today]
    
    print(f"\n[*] Commencing high-speed concurrent sweep of {len(remaining_tickers)} pending stocks...")
    
    files_to_process = []
    
    # === PHASE 1: HIGH-SPEED MULTI-THREADED SWEEP ===
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
        future_to_ticker = {executor.submit(extract_stock_data, ticker): ticker for ticker in remaining_tickers}
        
        for idx, future in enumerate(concurrent.futures.as_completed(future_to_ticker), 1):
            ticker = future_to_ticker[future]
            try:
                # Capture the downloaded assets from the thread result
                assets = future.result()
                if assets:
                    files_to_process.extend(assets)
                    
                mark_completed(ticker)
                sys.stdout.write(f"\r>>> Processed [{idx}/{len(remaining_tickers)}] Tickers (Latest: {ticker}) ...")
                sys.stdout.flush()
            except Exception as e:
                pass 

    # === PHASE 2: SEQUENTIAL DOCLING & AI ANALYSIS ===
    
    if files_to_process:
        print(f"\n\n=== PHASE 2: SEQUENTIAL AI ANALYSIS ({len(files_to_process)} Files) ===")
        
        # Process AI one-by-one so you never hit the 5/min Gemini limit
        for item in files_to_process:
            ticker = item['ticker']
            matched_category = item['category']
            is_audio = item['is_audio']
            save_path = item['file_path']
            full_url = item['url']
            clean_type = item['clean_type']
            json_path = item['json_path']

            # THE FIX: Define this variable here so both Audio and PDF blocks can safely use it
            clean_category_for_tg = matched_category.replace('_', ' ')

            # 1. Handle Audio Logic
            if is_audio:
                with STATE_LOCK:
                    # FIX: Safely converted to HTML so underscores in audio don't crash Telegram
                    PIPELINE_METRICS.setdefault("summaries", []).append(f"• <b>{ticker}</b> ({clean_category_for_tg}): Audio recording archived successfully.\n  └ <a href='{full_url}'>🎧 Listen to Audio</a>")
                
                json_metadata = {"ticker": ticker, "category": matched_category, "file_type": "audio", "source_url": full_url}
                with open(json_path, 'w', encoding='utf-8') as f: 
                    json.dump(json_metadata, f, indent=4)
                register_successful_metric(ticker, matched_category)
                continue
                
            # 2. Handle PDF / AI Logic
            print(f"      [~] Extracting AI insights for {ticker}...")
            
            try:
                # --- FAST 150-PAGE GUARDRAIL ---
                try:
                    doc = fitz.open(save_path)
                    page_count = doc.page_count
                    doc.close()
                except Exception:
                    page_count = 999 # Fail-safe, skip completely broken/corrupted PDFs
                
                md_path = save_path.replace(".pdf", ".md")
                ai_decision_string = "No AI Analysis performed."
                md_content = ""
                
                if page_count > 150:
                    print(f"      [!] Document too large ({page_count} pages). Bypassing Docling/AI.")
                    ai_decision_string = f"Skipped: Document exceeds 150 pages ({page_count} pages)."
                    md_content = f"# {ticker} - {matched_category}\n\n⚠️ **Document bypassed ({page_count} pages).**\n🔗 **[View Original Document]({full_url})**"
                    
                else:
                    docling_md_text = ""
                    
                    # --- DOCLING WITH OFFLINE FALLBACK ---
                    if docling_converter is not None:
                        print(f"      [~] Docling parsing layout & tables with OCR ({page_count} pages)...")
                        try:
                            conv_res = docling_converter.convert(save_path)
                            docling_md_text = conv_res.document.export_to_markdown()
                        except Exception as e:
                            print(f"      [!] Docling OCR failed on this specific file: {e}. Falling back...")
                    
                    if not docling_md_text:
                        print(f"      [~] Using Offline PyMuPDF to extract text...")
                        try:
                            doc = fitz.open(save_path)
                            for page in doc:
                                docling_md_text += page.get_text("text") + "\n\n"
                            doc.close()
                        except Exception as e:
                            print(f"      [!] PyMuPDF also failed. File is likely corrupted.")
                            continue
                    
                    # Call AI with the backoff wrapper, passing the perfect Markdown instead of raw bytes
                    ai_decision_string = safe_ai_classification(docling_md_text)
                    
                    # Generate and save Markdown using the parsed Docling text
                    if matched_category == "Concalls":
                        md_content = generate_enterprise_markdown(docling_md_text, ticker, matched_category, clean_type, full_url, ai_decision_string)
                    else:
                        md_content = convert_to_basic_markdown(docling_md_text, ticker, matched_category, clean_type, full_url, ai_decision_string)
                
                # HTML escape fix for Telegram
                ai_tg_safe = html.escape(ai_decision_string).replace('**', '')

                # THE FIX: Uses the clean_category_for_tg variable defined at the top of the loop
                with STATE_LOCK:
                    PIPELINE_METRICS.setdefault("summaries", []).append(
                        f"• <b>{ticker}</b> ({clean_category_for_tg}): {ai_tg_safe}\n  └ <a href='{full_url}'>📄 Source</a>"
                    )
                    
                if md_content:
                    with open(md_path, 'w', encoding='utf-8') as f: 
                        f.write(md_content)
                
                # Generate JSON metadata
                json_metadata = {
                    "ticker": ticker, "category": matched_category, "extraction_date": TODAY_STR,
                    "ai_summary": ai_decision_string, "source_url": full_url, "file_type": "pdf"
                }
                with open(json_path, 'w', encoding='utf-8') as f: 
                    json.dump(json_metadata, f, indent=4)
                
                register_successful_metric(ticker, matched_category)
                
                # Cleanup: Delete the raw PDF since we now have the Markdown
                if os.path.exists(save_path): 
                    os.remove(save_path)
                
                # STRICT RATE LIMIT GUARD: Wait 13 seconds before the next AI call (Only if AI actually ran)
                if page_count <= 150:
                    time.sleep(13)
                
            except Exception as e:
                print(f"      [!] AI Processing failure for {ticker}: {e}")
            finally:
                # MANDATORY RAM CLEAR (Prevents OOM Crashes on large Scanned PDFs)
                gc.collect()

    print("\n\n=== REAL-TIME SWEEP CONCLUDED SUCCESSFULLY ===")
    
    send_telegram_summary()


if __name__ == "__main__":
    main()

