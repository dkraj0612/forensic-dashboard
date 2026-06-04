import os
import re
import json
import time
import sys
import logging
from datetime import datetime
import requests
import yfinance as yf
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logging.basicConfig(level=logging.INFO, format='%(asctime)s | %(levelname)-8s | %(message)s', handlers=[logging.StreamHandler(sys.stdout)])
logger = logging.getLogger(__name__)

def create_resilient_session() -> requests.Session:
    session = requests.Session()
    retry = Retry(total=4, backoff_factor=1.5, status_forcelist=[429, 500, 502, 503, 504], raise_on_status=False)
    adapter = HTTPAdapter(max_retries=retry, pool_connections=25, pool_maxsize=25)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session

def gather_omni_context(ticker: str, session: requests.Session) -> tuple[str, int]:
    context = f"--- ASSET: {ticker} ---\n\n"
    
    try:
        info = yf.Ticker(f"{ticker}.NS", session=session).info
        context += f"### [FUNDAMENTALS]\nP/E: {info.get('trailingPE')} | Margin: {info.get('profitMargins')}\n\n"
    except Exception: 
        pass

    valid_days_count = 0
    if os.path.exists("market_data"):
        all_dates = sorted([d for d in os.listdir("market_data") if re.match(r'\d{4}-\d{2}-\d{2}', d)])
        for d in all_dates:
            day_has_data = False
            for m_file in ["cash_market.md", "derivatives.md", "macro_flows.md", "surveillance.md"]:
                path = f"market_data/{d}/{m_file}"
                if os.path.exists(path):
                    with open(path, "r", encoding="utf-8") as f:
                        lines = [l for l in f.readlines() if ticker in l or l.startswith("#")]
                        if len(lines) > 2: 
                            context += f"[{d} - {m_file}]\n{''.join(lines)}\n"
                            day_has_data = True
            if day_has_data:
                valid_days_count += 1
                        
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
        return logger.critical("No GEMINI_API_KEY found.")
    
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={API_KEY}"
    db_path, verdicts = "master_forensic_db.json", {}
    
    if os.path.exists(db_path):
        try:
            with open(db_path, "r", encoding="utf-8") as f: 
                verdicts = json.load(f)
        except Exception: 
            pass

    today = datetime.today().strftime('%Y-%m-%d')
    start = time.time()

    prompt = """Analyze provided data. Output strictly valid JSON schema: 
    {"verdict": "BUY"|"HOLD"|"AVOID", "score": 0-100, "governance": {"risk_level": "Low"|"High", "details": "str"}, "market_momentum": {"trend": "Bullish"|"Bearish", "triggers": ["str"]}, "financial_health": {"score": 0-100, "revenue_quality": "str"}, "regulatory_surveillance": {"risk": "Low"|"High"}}"""

    for ticker in watchlist:
        if (time.time() - start) > 19800:
            logger.warning("5.5 hour limit reached. Halting AI loop gracefully.")
            break
            
        if ticker in verdicts and verdicts[ticker].get("last_updated") == today:
            logger.info(f"⏭️ SKIP: {ticker} already processed today.")
            continue

        ctx, days_count = gather_omni_context(ticker, session)
        
        if days_count < 30:
            logger.info(f"⚠️ DATA HOLD: {ticker} only has {days_count}/30 days of data. Skipping AI analysis.")
            verdicts[ticker] = {
                "verdict": "AWAITING DATA", 
                "score": 0, 
                "governance": {"risk_level": "Unknown", "details": f"Accumulating baseline: {days_count}/30 days available."},
                "market_momentum": {"trend": "Neutral", "triggers": []},
                "financial_health": {"score": 0, "revenue_quality": "Insufficient data for forensic analysis"},
                "regulatory_surveillance": {"risk": "Unknown", "framework": "Unknown"},
                "last_updated": today
            }
            with open(db_path, "w", encoding="utf-8") as f: 
                json.dump(verdicts, f, indent=4)
            continue
        
        logger.info(f"Evaluating: {ticker} ({days_count} days of context loaded)")
        payload = {"contents": [{"role": "user", "parts": [{"text": ctx}]}], "systemInstruction": {"parts": [{"text": prompt}]}, "generationConfig": {"temperature": 0.1, "responseMimeType": "application/json"}}
        
        for attempt in range(3):
            try:
                res = requests.post(url, json=payload, timeout=45)
                if res.status_code == 200:
                    clean = re.sub(r'^```json\s*|\s*
```$', '', res.json()['candidates'][0]['content']['parts'][0]['text'].strip(), flags=re.MULTILINE)
                    verdicts[ticker] = json.loads(clean)
                    verdicts[ticker]["last_updated"] = today
                    with open(db_path, "w", encoding="utf-8") as f: json.dump(verdicts, f, indent=4)
                    logger.info(f"✅ SAVED: {ticker} verdict generated.")
                    break
                elif res.status_code == 429:
                    time.sleep(10 * (attempt + 1))
            except Exception: 
                time.sleep(5)
                
        time.sleep(4.5)

def main():
    logger.info("--- STARTING AI ORCHESTRATION ---")
    try:
        with open("active_watchlist.json", "r") as f: 
            watchlist = json.load(f)
            
        if not isinstance(watchlist, list) or len(watchlist) == 0:
            raise ValueError("Watchlist is corrupted or empty.")
            
    except (FileNotFoundError, json.JSONDecodeError, ValueError) as e:
        logger.error(f"Consistency Failure: {e}")
        sys.exit(1)
        
    session = create_resilient_session()
    run_ai_analysis_sweep(watchlist, session)
    logger.info("--- AI ORCHESTRATION COMPLETE ---")

if __name__ == "__main__":
    main()
