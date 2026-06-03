import os
import re
import json
import time
import requests
import yfinance as yf

# Your core watchlist (Add or remove NSE tickers as needed)
WATCHLIST = ["RELIANCE.NS", "TCS.NS", "INFY.NS", "HDFCBANK.NS", "TATAMOTORS.NS"]

def get_omni_prompt():
    return """
    You are an expert Equity Research Analyst. Analyze the provided financial data for this stock.
    Output a strictly valid JSON object matching this schema exactly, without any external markdown or commentary:
    {
        "verdict": "STRONG BUY" | "BUY" | "HOLD" | "AVOID" | "STRONG AVOID",
        "score": 0-100,
        "governance": {"risk_level": "Low"|"Medium"|"High", "details": "string"},
        "shareholding_trends": {"description": "string", "institutional_stance": "Accumulating"|"Static"|"Liquidating"},
        "market_momentum": {"trend": "Bullish"|"Bearish"|"Neutral", "triggers": ["string"]},
        "financial_health": {"score": 0-100, "revenue_quality": "string"},
        "catalysts_and_sentiment": {"description": "string"},
        "regulatory_surveillance": {"framework": "Normal"|"ASM"|"GSM", "risk": "Low"|"Medium"|"High"}
    }
    """

def run_pipeline():
    print("[SYSTEM] Booting Automated Forensic Sweep...")
    API_KEY = os.environ.get("GEMINI_API_KEY")
    
    if not API_KEY:
        print("[CRITICAL] GEMINI_API_KEY secret is missing from GitHub Actions.")
        return

    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={API_KEY}"
    master_db = {}

    for ticker in WATCHLIST:
        clean_ticker = ticker.replace(".NS", "")
        print(f"--> Downloading fundamentals for {clean_ticker}...")
        
        try:
            # 1. Download free fundamental data via Yahoo Finance
            stock = yf.Ticker(ticker)
            info = stock.info
            
            # Extract key metrics to feed the AI
            financial_context = f"""
            Target Asset: {clean_ticker}
            Sector: {info.get('sector', 'N/A')}
            Market Cap: {info.get('marketCap', 'N/A')}
            Trailing P/E: {info.get('trailingPE', 'N/A')}
            Forward P/E: {info.get('forwardPE', 'N/A')}
            Profit Margin: {info.get('profitMargins', 'N/A')}
            Operating Margin: {info.get('operatingMargins', 'N/A')}
            Return on Equity: {info.get('returnOnEquity', 'N/A')}
            Total Debt / Equity: {info.get('debtToEquity', 'N/A')}
            Current Price: {info.get('currentPrice', 'N/A')}
            52 Week High: {info.get('fiftyTwoWeekHigh', 'N/A')}
            52 Week Low: {info.get('fiftyTwoWeekLow', 'N/A')}
            Institutional Ownership %: {info.get('heldPercentInstitutions', 0) * 100}%
            """
            
            # 2. Ask Gemini to analyze the data
            payload = {
                "contents": [{"role": "user", "parts": [{"text": financial_context}]}],
                "systemInstruction": {"parts": [{"text": get_omni_prompt()}]},
                "generationConfig": { "temperature": 0.2, "responseMimeType": "application/json" }
            }
            
            res = requests.post(url, headers={'Content-Type': 'application/json'}, json=payload)
            response_data = res.json()
            
            # 3. Clean and parse the AI's JSON output
            raw_text = response_data['candidates'][0]['content']['parts'][0]['text']
            clean_json_str = re.sub(r'^```json\s*|\s*```$', '', raw_text.strip(), flags=re.MULTILINE)
            
            master_db[clean_ticker] = json.loads(clean_json_str)
            print(f"[SUCCESS] AI Verdict rendered for {clean_ticker}")
            
        except Exception as e:
            print(f"[ERROR] Failed to process {clean_ticker}: {e}")
            
        time.sleep(3) # Prevent hitting API rate limits

    # 4. Save the final database directly to the repository folder
    with open("master_forensic_db.json", "w", encoding="utf-8") as f:
        json.dump(master_db, f, indent=4)
    print("\n[COMPLETE] master_forensic_db.json updated successfully.")

if __name__ == "__main__":
    run_pipeline()
