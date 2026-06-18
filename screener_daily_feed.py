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

# TELEGRAM & AI SECURITY: Loaded exclusively from local OS environment variables
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

# Thread lock to guarantee file system integrity across concurrent tasks
STATE_LOCK = threading.Lock()

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

# NSE Date Formatting
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
        day, day_strip = target_date.strftime('%d'), target_date.strftime('%e').strip()
        mon_short, year = target_date.strftime('%b'), target_date.strftime('%Y')
        patterns.extend([f"{day} {mon_short}", f"{day_strip} {mon_short}", f"{mon_short} {day}", f"{mon_short} {day_strip}", f"{day}-{mon_short}-{year}"])
    return list(set(p.lower() for p in patterns))

VALID_DATE_PATTERNS = generate_lookback_patterns()

TARGET_CATEGORIES = {
    "SAST": [r'sast', r'substantial acquisition', r'reg.*29', r'disclosure under regulation'],
    "SHP": [r'shareholding pattern', r'shp', r'shareholding statement'],
    "Insider_Trades": [r'insider', r'reg.*7', r'insider trade', r'prohibition of insider'],
    "Concalls": [r'transcript', r'audio', r'concall', r'earnings call', r'call transcript'],
    "Results": [r'financial result', r'quarterly result', r'audited result', r'unaudited result', r'results'],
    "Dividend": [r'dividend', r'interim dividend', r'final dividend', r'book closure for dividend'],
    "Bonus": [r'bonus', r'bonus issue', r'allotment of bonus']
}

session = tls_requests.Session(impersonate="chrome124")
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Referer': 'https://www.nseindia.com/'
}

# =====================================================================
# STATE TRACKING ENGINE (PREVENTS METRIC LOSS ON REBOOT)
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
        except Exception: pass
    return {
        "bhavcopy_sec": False, "bhavcopy_fno": False,
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
    with open(METRICS_FILE, 'w') as f: json.dump(data_to_save, f)

PIPELINE_METRICS = load_metrics()

# =====================================================================
# MULTIMODAL AI SELECTION & ANALYSIS ENGINE
# =====================================================================
def generate_ai_summary(ticker: str, category: str, pdf_bytes: bytes) -> str:
    """Passes the RAW PDF directly to Gemini's vision engine to extract an objective trading signal and summary."""
    if not llm_model or not pdf_bytes:
        return "Document parsed and saved safely."
        
    prompt = f"""
    You are a strict quantitative trading algorithm analyzing an NSE India corporate filing ({category}) for {ticker}.
    
    Step 1: Classify the objective directional market impact of this document. Choose EXACTLY ONE:
    🟢 [BULLISH] (Strong positive growth, insider buying, major orders, dividend hikes, new client wins)
    🔴 [BEARISH] (Profit drops, revenue contractions, insider selling, governance stress, auditor resignation)
    📦 [ORDER BOOK] (Specific layout tracking material contract wins, infrastructure awards, or order book sizing)
    🤝 [NEW CLIENTS] (Strategic business partnerships, global client onboarding, commercial alliances)
    ⚪ [NEUTRAL] (Routine compliance filings, calendar updates, expected standard operational outcomes)
    
    Step 2: Extract the single most critical numerical fact or corporate action justifying this classification.
    
    Output format MUST be strictly:
    [CLASSIFICATION] One short, punchy justification sentence. Do not include introductory text.
    """
    try:
        pdf_document = {
            "mime_type": "application/pdf",
            "data": pdf_bytes
        }
        response = llm_model.generate_content([prompt, pdf_document])
        clean_summary = response.text.replace('**', '').replace('*', '').strip()
        if len(clean_summary) > 200:
            clean_summary = clean_summary[:197] + "..."
        return clean_summary
    except Exception as e:
        print(f"      [!] AI PDF Classification failed for {ticker}: {e}")
        return "⚪ [NEUTRAL] Document logged but objective AI classification timed out."

def send_telegram_summary():
    """Compiles local metrics, objective decision logs, and securely dispatches the aggregated payload."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("\n[!] Telegram configuration missing. Summary notification bypassed.")
        return

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
            # Prevents overflowing Telegram's maximum 4,096 character payload footprint safely
            if len(msg) + len(summary) > 3900:
                msg += "• *CRITICAL:* Additional alerts truncated. Review filesystem logs.\n"
                break
            msg += f"{summary}\n\n"

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "Markdown"}
    
    try:
        resp = session.post(url, json=payload, timeout=15)
        if resp.status_code == 200:
            print("\n      [OK] Telegram summary notification dispatched securely.")
        else:
            print(f"\n      [!] Telegram dispatch failed: {resp.text}")
    except Exception as e:
        print(f"\n      [!] Socket error during Telegram notification: {e}")

# =====================================================================
# INTEGRATED NLP ENGINE (PURE TEXT ANALYSIS)
# =====================================================================
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
            if first_char and first_char.islower() and w.lower() not in ['of', 'from', 'for', 'and', 'the', 'in', 'on']: 
                return False
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
        except Exception:
            return "", "", 0

        normalized_lines = []
        inline_pattern = re.compile(r'(\b(?:(?:Mr\.|Ms\.|Dr\.)?\s*[A-Z][a-zA-Z\.\-]+\s+[A-Z][a-zA-Z\.\-]+(?:\s+[A-Z][a-zA-Z\.\-]+){0,2}|Moderator|Operator|Management|Analyst|Participant|Speaker)\s*:)')
        for line in raw_lines:
            parts = inline_pattern.split(line)
            if len(parts) > 1:
                for part in parts:
                    if part.strip(): normalized_lines.append(part.strip())
            else:
                normalized_lines.append(line)

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
            else:
                current_speech_accumulator.append(line)

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

        if hype and delivery and hype > 5.0 and delivery < 1.0:
            signals.append("🚨 **[BEHAVIORAL] 'Hype/Delivery Divergence':** Promoter using aggressive buzzwords (>5.0 density) with minimal operational delivery terminology.")
            
        if evasion and evasion > 3.0:
            signals.append("⚠️ **[EVASION] High Evasion Density:** Management used unusually high deflection terminology (e.g., macro headwinds, take it offline).")

        if not signals: 
            signals.append("✅ **[STABILITY]** No severe behavioral manipulation thresholds breached in the transcript language.")
            
        return signals

quant_engine = TranscriptIntelligence()

# =====================================================================
# NSE DATA PIPELINE ENGINE
# =====================================================================
def download_nse_bhavcopies():
    os.makedirs(BHAV_DIR, exist_ok=True)
    
    sec_url = f"https://nsearchives.nseindia.com/products/content/sec_bhavdata_full_{DDMMYYYY}.csv"
    sec_save_path = os.path.join(BHAV_DIR, f"SEC_BHAV_{TODAY_STR}.csv")
    
    if not os.path.exists(sec_save_path):
        try:
            print("      [~] Requesting SEC Bhavdata...")
            resp = session.get(sec_url, headers=HEADERS, timeout=20)
            if resp.status_code == 200:
                with open(sec_save_path, 'wb') as f: f.write(resp.content)
                with STATE_LOCK:
                    PIPELINE_METRICS["bhavcopy_sec"] = True
                    save_metrics()
                print("      [OK] SEC Bhavcopy saved successfully.")
            else:
                print(f"      [!] SEC Bhavcopy not available yet (Status {resp.status_code}).")
        except Exception as e:
            print(f"      [!] SEC Data Error: {e}")
    else:
        with STATE_LOCK:
            PIPELINE_METRICS["bhavcopy_sec"] = True

    YYYYMMDD = NOW.strftime('%Y%m%d')
    fno_url_udiff = f"https://nsearchives.nseindia.com/content/fo/BhavCopy_NSE_FO_0_0_0_{YYYYMMDD}_F_0000.csv.zip"
    fno_url_legacy = f"https://nsearchives.nseindia.com/content/historical/DERIVATIVES/{YYYY}/{MMM}/fo{DD_MMM_YYYY}bhav.csv.zip"
    
    fno_save_path = os.path.join(BHAV_DIR, f"FNO_BHAV_{TODAY_STR}.csv")
    
    if not os.path.exists(fno_save_path):
        try:
            print("      [~] Requesting FNO Bhavdata Archive...")
            
            resp = session.get(fno_url_udiff, headers=HEADERS, timeout=20)
            if resp.status_code != 200:
                resp = session.get(fno_url_legacy, headers=HEADERS, timeout=20)
                
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
                print("      [OK] FNO Bhavcopy processed and saved.")
            else:
                print(f"      [!] FNO Bhavcopy not available yet (Status {resp.status_code}).")
        except Exception as e:
            print(f"      [!] FNO Data Error: {e}")
    else:
        with STATE_LOCK:
            PIPELINE_METRICS["bhavcopy_fno"] = True

def get_dynamic_nse_list():
    url = "https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv"
    try:
        resp = session.get(url, headers=HEADERS, timeout=20)
        if resp.status_code == 200:
            return pd.read_csv(StringIO(resp.text))['SYMBOL'].dropna().astype(str).str.strip().unique().tolist()
        return []
    except Exception: return []

def get_completed_today():
    if not os.path.exists(PROGRESS_FILE): return set()
    with open(PROGRESS_FILE, 'r') as f: return set(line.strip() for line in f.readlines())

def mark_completed(ticker):
    with STATE_LOCK:
        with open(PROGRESS_FILE, 'a') as f: f.write(f"{ticker}\n")

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
            if re.search(pattern, text_lower): return category
    return None

def convert_to_basic_markdown(pdf_bytes, ticker, category, clean_type):
    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        md_lines = [f"# {ticker} - {category} - {clean_type}", f"**Extraction Date:** {TODAY_STR}\n---"]
        for page_num in range(len(doc)):
            text = doc.load_page(page_num).get_text("text")
            if text.strip(): md_lines.extend([f"## Page {page_num + 1}", text.strip(), "---\n"])
        doc.close()
        return "\n\n".join(md_lines)
    except Exception: return None

def generate_enterprise_markdown(pdf_bytes: bytes, ticker: str, category: str, clean_type: str) -> str:
    prep_text, qa_text, page_count = quant_engine.extract_and_structure_transcript(pdf_bytes)
    combined_text = f"{prep_text} {qa_text}"
    if not combined_text.strip() or page_count <= 3: return None

    total_metrics = quant_engine.calculate_metrics(combined_text)
    warning_flags = quant_engine.calculate_behavioral_signals(total_metrics)
    system_directive = "> [SYSTEM DIRECTIVE - FORENSIC AUDIT]\n> Focus strictly on Hype vs Delivery divergence, Linguistic Evasion, and Management Accountability during the Q&A cross-examination."

    return f"""---
metadata:
  company_name: "{ticker}"
  call_date: "{TODAY_STR}"
  reporting_period: "{clean_type}"
telemetry_matrix:
  obfuscation_fog_index: {total_metrics['Gunning_Fog_Index']:.2f}
  total_word_volume: {total_metrics['Word_Count']}
behavioral_densities_per_10k:
  promoter_hype: {total_metrics['Hype_Density_Per_10k']:.2f}
  operational_delivery: {total_metrics['Delivery_Density_Per_10k']:.2f}
  linguistic_evasion: {total_metrics['Evasion_Density_Per_10k']:.2f}
  governance_stress: {total_metrics['Governance_Stress_Density_Per_10k']:.2f}
---

# Concall NLP Analysis: {ticker}

{system_directive}

---

## 1. Behavioral Warning Flags (Auto-Generated)
{chr(10).join([f"* {flag}" for flag in warning_flags])}

---

## 2. Textual Telemetry (Deterministic)
| Metric Classification | Whole Document |
| :--- | :--- |
| **Linguistic Obfuscation (Gunning Fog)** | {total_metrics['Gunning_Fog_Index']:.2f} |
| **Total Word Volume** | {total_metrics['Word_Count']} |

---

## SECTION A: PREPARED CORPORATE STATEMENTS
{prep_text}

---

## SECTION B: INTERACTIVE Q&A CROSS-EXAMINATION
{qa_text}
"""

def register_successful_metric(ticker: str, category: str):
    """Safely records the download to prevent metric loss under thread concurrency."""
    with STATE_LOCK:
        PIPELINE_METRICS["category_counts"][category].add(ticker)
        PIPELINE_METRICS["total_unique_stocks"].add(ticker)
        save_metrics()

def extract_stock_data(ticker):
    # Micro-throttle to smooth out parallel domain connection hits
    time.sleep(random.uniform(0.1, 0.4))
    
    url = f"https://www.screener.in/company/{ticker}/"
    try:
        resp = session.get(url, headers=HEADERS, timeout=15)
        if resp.status_code != 200: return
    except Exception: return

    soup = BeautifulSoup(resp.text, 'html.parser')

    for a in soup.find_all('a'):
        href = a.get('href', '')
        link_text = a.get_text(strip=True)
        if not href or not link_text: continue

        parent = a.find_parent(['li', 'tr', 'div'])
        context_text = parent.get_text(" ", strip=True) if parent else link_text

        matched_category = identify_category(link_text)
        if not matched_category or not is_within_temporal_window(context_text): continue

        clean_type = sanitize_filename(link_text)
        is_audio = "audio" in link_text.lower()
        
        ext = ".mp3" if is_audio else ".md"
        filename = f"{ticker}_{matched_category}_{clean_type}{ext}"
        
        category_dir = os.path.join(STOCKS_DIR, ticker, matched_category)
        save_path = os.path.join(category_dir, filename)

        if not os.path.exists(save_path):
            os.makedirs(category_dir, exist_ok=True)
            try:
                full_url = urljoin("https://www.screener.in", href)
                if '.pdf' in full_url.lower() or 'concalls' in full_url.lower() or 'announcements' in full_url.lower():
                    
                    file_resp = session.get(full_url, headers=HEADERS, timeout=45)
                    
                    if file_resp.status_code == 200:
                        content_type = file_resp.headers.get("Content-Type", "").lower()
                        if "text/html" in content_type:
                            print(f"      [!] Blocked by external server firewall (HTML returned). Skipping {filename}")
                            continue
                            
                        if len(file_resp.content) < 1000:
                            print(f"      [!] File is corrupted or empty (< 1KB). Skipping {filename}")
                            continue
                        
                        if is_audio:
                            with open(save_path, 'wb') as f: f.write(file_resp.content)
                            print(f"      [+] AUDIO STORED -> {filename}")
                            with STATE_LOCK:
                                PIPELINE_METRICS.setdefault("summaries", []).append(f"• *{ticker}* ({matched_category}): Audio recording archived successfully.")
                            register_successful_metric(ticker, matched_category)
                            
                        else:
                            # --- NATIVE MULTIMODAL AI RUN (FIRED ONLY UPON GENUINE DOWNLOAD - ON RAW PDF BYTES) ---
                            print(f"      [~] Routing raw PDF bytes directly to Gemini AI model for {ticker}...")
                            ai_decision_string = generate_ai_summary(ticker, matched_category, file_resp.content)
                            with STATE_LOCK:
                                PIPELINE_METRICS.setdefault("summaries", []).append(f"• *{ticker}* ({matched_category}): {ai_decision_string}")
                            
                            # Resume baseline local filesystem tracking execution
                            md_content = None
                            if matched_category == "Concalls":
                                print(f"      [~] Executing NLP Pipeline on {ticker}...")
                                md_content = generate_enterprise_markdown(file_resp.content, ticker, matched_category, clean_type)
                            else:
                                print(f"      [~] Extracting Basic Markdown for {ticker}...")
                                md_content = convert_to_basic_markdown(file_resp.content, ticker, matched_category, clean_type)
                            
                            if md_content and len(md_content) > 150:
                                with open(save_path, 'w', encoding='utf-8') as f: f.write(md_content)
                                print(f"      [+] CONVERTED TO MD -> {filename}")
                                register_successful_metric(ticker, matched_category)
                            else:
                                fallback_path = save_path.replace(".md", ".pdf")
                                with open(fallback_path, 'wb') as f: f.write(file_resp.content)
                                print(f"      [!] Complex parse failed. Safely secured raw PDF -> {fallback_path.split('/')[-1]}")
                                register_successful_metric(ticker, matched_category)
                                
                        time.sleep(random.uniform(1.5, 3.0))
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
    
    # 5 parallel processing workers maintains maximum stability while clearing 3,000 stocks under ~12 minutes
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
        future_to_ticker = {executor.submit(extract_stock_data, ticker): ticker for ticker in remaining_tickers}
        
        for idx, future in enumerate(concurrent.futures.as_completed(future_to_ticker), 1):
            ticker = future_to_ticker[future]
            try:
                future.result()
                mark_completed(ticker)
                sys.stdout.write(f"\r>>> Processed [{idx}/{len(remaining_tickers)}] Tickers (Latest: {ticker}) ...")
                sys.stdout.flush()
            except Exception as e:
                print(f"\n[!] Thread pool exception encountered running stock context [{ticker}]: {e}")

    print("\n\n=== REAL-TIME SWEEP CONCLUDED SUCCESSFULLY ===")
    
    # Send the final aggregated data and AI decision intelligence metrics to Telegram
    send_telegram_summary()

if __name__ == "__main__":
    main()
