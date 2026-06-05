
import os
import re
import json
import time
import sys
import logging
from datetime import datetime, timedelta
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# Configure Logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s | %(levelname)-8s | %(message)s', handlers=[logging.StreamHandler(sys.stdout)])
logger = logging.getLogger(__name__)

def create_resilient_session() -> requests.Session:
    session = requests.Session()
    retry = Retry(
        total=5, 
        backoff_factor=1.5, 
        status_forcelist=[429, 500, 502, 503, 504], 
        raise_on_status=False
    )
    adapter = HTTPAdapter(max_retries=retry, pool_connections=25, pool_maxsize=25)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session

def is_fundamental_fresh(ticker: str) -> bool:
    """Gatekeeper: Ensures fundamentals exist and are not older than 14 days."""
    path = f"corporate_data/{ticker}/fundamentals.json"
    if not os.path.exists(path):
        return False
    
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
            sync_date_str = data.get('sync_date')
            if not sync_date_str: return False
            
            sync_date = datetime.strptime(sync_date_str, '%Y-%m-%d')
            # 14-day freshness window check
            return datetime.today() - sync_date < timedelta(days=14)
    except Exception:
        return False

def gather_omni_context(ticker: str) -> tuple[str, int]:
    context = f"--- ASSET FORENSIC PROFILE: {ticker} ---\n\n"
    historical_data = ""
    valid_days_count = 0
    
    # 1. LOAD FUNDAMENTALS FROM DISK (No Live Yahoo API Calls here!)
    fund_path = f"corporate_data/{ticker}/fundamentals.json"
    if os.path.exists(fund_path):
        try:
            with open(fund_path, "r", encoding="utf-8") as f:
                f_data = json.load(f)
                context += f"### [LATEST FUNDAMENTALS]\n"
                context += f"Trailing P/E: {f_data.get('pe', 'N/A')} | Forward P/E: {f_data.get('forward_pe', 'N/A')}\n"
                context += f"Profit Margin: {f_data.get('margins', 'N/A')} | ROE: {f_data.get('roe', 'N/A')}\n"
                context += f"Debt-to-Equity: {f_data.get('de', 'N/A')} | Total Cash: {f_data.get('cash', 'N/A')}\n\n"
        except Exception as e:
            logger.debug(f"Error reading fundamentals for {ticker}: {e}")

    # 2. PROCESS HISTORICAL MATRIX FOLDERS (FULL LIST RETAINED)
    if os.path.exists("market_data"):
        all_dates = sorted([d for d in os.listdir("market_data") if re.match(r'\d{4}-\d{2}-\d{2}', d)])
        
        for d in all_dates:
            day_has_data = False
            for m_file in ["cash_market.md", "derivatives.md", "macro_flows.md", "insider_trading.md", "promoter_pledges.md", "index_options.md"]:
                path = f"market_data/{d}/{m_file}"
                if os.path.exists(path):
                    with open(path, "r", encoding="utf-8") as f:
                        lines = [l for l in f.readlines() if ticker in l or l.startswith("#") or "NIFTY" in l]
                        if len(lines) > 2: 
                            historical_data += f"[{d} - {m_file}]\n{''.join(lines)}\n"
                            day_has_data = True
            if day_has_data:
                valid_days_count += 1
                
    # If we don't have enough baseline data, abort instantly to save Gemini API calls
    if valid_days_count < 10:
        return context, valid_days_count
        
    context += historical_data
                        
    # 3. INJECT SPECIFIC CORPORATE FILINGS
    corp = f"corporate_data/{ticker}"
    if os.path.exists(corp):
        for r, _, files in os.walk(corp):
            for file in files:
                if file.endswith(".md"):
                    with open(os.path.join(r, file), "r", encoding="utf-8") as f: 
                        context += f"[{file}]\n{f.read()}\n"
                        
    return context, valid_days_count

def run_ai_analysis_sweep(watchlist: list, session: requests.Session):
    API_KEY = os.environ.get("GEMINI_API_KEY")
    if not API_KEY: 
        return logger.critical("System Error: No GEMINI_API_KEY environment variable found. Halting execution.")
    
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={API_KEY}"
    db_path = "master_forensic_db.json"
    verdicts = {}
    
    if os.path.exists(db_path):
        try:
            with open(db_path, "r", encoding="utf-8") as f: 
                verdicts = json.load(f)
        except Exception: 
            pass

    today = datetime.today().strftime('%Y-%m-%d')
    start_time = time.time()

    system_prompt = """You are an elite Institutional Forensic Analyst. You are analyzing raw exchange data and corporate filings. 
    You MUST output your response STRICTLY as a valid JSON object matching the exact structure below. Do NOT output any markdown wrappers, conversational text, or explanations outside the JSON object.
    
    { 
        "type": "stock_analysis", 
        "metadata": { 
            "company_name": "Full legal name", 
            "ticker": "The ticker symbol", 
            "classification": "Sector / Industry", 
            "analysis_date": "Today's Date" 
        }, 
        "kpis": { 
            "market_cap": "e.g., ₹50,000 Cr", 
            "pe_ratio": "Value", 
            "roe": "Value", 
            "debt_to_equity": "Value", 
            "final_verdict": "STRONG BUY / HOLD / AVOID" 
        }, 
        "governance": { 
            "promoter_integrity": "1-2 sentence analysis", 
            "red_flags": "List any regulatory or ASM/GSM risks" 
        }, 
        "financial_forensics": { 
            "revenue_quality": "1-2 sentence analysis", 
            "hidden_debt": "Note on leverage metrics" 
        }, 
        "catalysts_and_sentiment": { 
            "upcoming_triggers": "Short term market drivers based on data" 
        } 
    }"""

    for ticker in watchlist:
        # Graceful Github Actions / CI Timeout Limit (5.5 Hours)
        if (time.time() - start_time) > 19800:
            logger.warning("5.5 hour execution limit reached. Halting AI loop gracefully to prevent CI runner termination.")
            break

        # GATEKEEPER: Ensure fundamentals are fresh before spending Gemini API tokens
        if not is_fundamental_fresh(ticker):
            logger.warning(f"⚠️ GATEKEEPER HOLD: {ticker} fundamentals are missing or older than 14 days. Skipping analysis.")
            continue
            
        if ticker in verdicts and verdicts[ticker].get("metadata", {}).get("analysis_date") == today:
            logger.info(f"⏭️ SKIP: {ticker} has already been processed and logged today.")
            continue

        ctx, days_count = gather_omni_context(ticker)
        
        # Immediate skip if data is insufficient (happens in milliseconds now)
        if days_count < 10: 
            logger.info(f"⚠️ DATA HOLD: {ticker} only has {days_count} days of market data. Skipping deep analysis.")
            continue
        
        logger.info(f"Evaluating: {ticker} ({days_count} historical records loaded)")
        
        payload = {
            "contents": [{"role": "user", "parts": [{"text": ctx}]}], 
            "systemInstruction": {"parts": [{"text": system_prompt}]}, 
            "generationConfig": {"temperature": 0.1, "responseMimeType": "application/json"}
        }
        
        for attempt in range(4):
            try:
                res = session.post(url, json=payload, timeout=45)
                
                if res.status_code == 200:
                    raw_text = res.json()['candidates'][0]['content']['parts'][0]['text'].strip()
                    
                    # Safe Regex multiline stripping
                    clean_json_str = re.sub(r'^`{3}(?:json)?\s*|\s*`{3}$', '', raw_text, flags=re.MULTILINE | re.IGNORECASE)
                    
                    parsed_json = json.loads(clean_json_str)
                    parsed_json["metadata"]["analysis_date"] = today 
                    
                    verdicts[ticker] = parsed_json
                    
                    # Commit to disk immediately
                    with open(db_path, "w", encoding="utf-8") as f: 
                        json.dump(verdicts, f, indent=4)
                        
                    logger.info(f"✅ SAVED: {ticker} forensic verdict generated.")
                    break
                    
                elif res.status_code == 429:
                    logger.warning(f"Rate limited by Gemini. Pausing execution... (Attempt {attempt+1})")
                    time.sleep(15 * (attempt + 1))
                else:
                    logger.error(f"API HTTP Error {res.status_code}: {res.text}")
                    time.sleep(5)
                    
            except Exception as parse_error: 
                logger.error(f"Data mapping execution failure for {ticker}: {parse_error}")
                time.sleep(5)
                
        # Mandatory anti-spam cooldown between requests to preserve API quota
        time.sleep(4.5)

def main():
    logger.info("--- STARTING BATCH AI ORCHESTRATION ---")
    try:
        if not os.path.exists("active_watchlist.json"):
            logger.warning("No active_watchlist.json found. Creating generic fallback list.")
            watchlist = ["RELIANCE", "TCS", "INFY", "HDFCBANK", "ICICIBANK"]
        else:
            with open("active_watchlist.json", "r") as f: 
                watchlist = json.load(f)
            
        if not isinstance(watchlist, list) or len(watchlist) == 0:
            raise ValueError("Target array is corrupted or empty.")
            
    except (json.JSONDecodeError, ValueError) as e:
        logger.error(f"Execution Collision Fault: {e}")
        sys.exit(1)
        
    session = create_resilient_session()
    run_ai_analysis_sweep(watchlist, session)
    
    logger.info("--- BATCH AI ORCHESTRATION COMPLETE ---")

if __name__ == "__main__":
    main()


