import os
import re
import sys
import time
from bs4 import BeautifulSoup

try:
    from curl_cffi import requests as tls_requests
except ImportError:
    print("[CRITICAL] You must install curl_cffi: pip install curl_cffi")
    sys.exit(1)

session = tls_requests.Session(impersonate="chrome124")

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
    'Accept-Language': 'en-US,en;q=0.9',
    'Referer': 'https://www.screener.in/'
}

# --- FEED CONFIGURATION ---
# The folder where these daily files will be saved
OUTPUT_DIR = r"C:\Users\Test\Downloads\corporate_data\daily_filings"

# How many pages of the timeline do you want to scan? (Usually 1 page = ~100 announcements)
PAGES_TO_SCAN = 5

# Define EXACTLY what you want to extract from the firehose. 
# If any of these words appear in the announcement title, it downloads the PDF.
TARGET_KEYWORDS = [
    "Transcript", 
    "Shareholding Pattern", 
    "Credit Rating",
    "Investor Presentation"
]
# ---------------------------

def sanitize_filename(text: str) -> str:
    """Strips illegal Windows characters and cleans up the string."""
    clean_text = re.sub(r'[\\/*?:"<>|\'’]', "", text)
    clean_text = clean_text.replace(" ", "_").replace(",", "")
    return clean_text[:120]

def download_file(url, save_path):
    try:
        resp = session.get(url, headers=HEADERS, stream=True, timeout=30)
        if resp.status_code == 200:
            with open(save_path, 'wb') as f:
                for chunk in resp.iter_content(chunk_size=8192):
                    if chunk: f.write(chunk)
            return True
        return False
    except Exception as e:
        print(f"      [!] Failed to download: {e}")
        return False

def scan_filings_page(page_number):
    url = f"https://www.screener.in/filings/?page={page_number}"
    print(f"\n[*] Scanning Market Feed: Page {page_number} ...")
    
    try:
        resp = session.get(url, headers=HEADERS, timeout=15)
        if "Cloudflare" in resp.text or resp.status_code != 200:
            print(f"[!] Access denied or error on page {page_number}")
            return
    except Exception as e:
        print(f"[!] Network error: {e}")
        return

    soup = BeautifulSoup(resp.text, 'html.parser')
    
    # Track how many targets we found on this page
    found_targets = 0

    # In the Screener feed, each announcement is usually inside a list item <li> or table row <tr>
    # We will look for EVERY link on the page that points to an external document
    for a_tag in soup.find_all('a'):
        href = a_tag.get('href', '')
        title = a_tag.get_text(strip=True)
        
        # Identify if this link is an actual announcement attachment
        if 'bseindia.com' in href or 'nseindia.com' in href or href.endswith('.pdf'):
            
            # Check if the title matches our specific TARGET_KEYWORDS
            if not any(keyword.lower() in title.lower() for keyword in TARGET_KEYWORDS):
                continue # Skip if it's just a regular board meeting notice, etc.
                
            # If it matches, we need to figure out which company it belongs to.
            # We traverse up to the parent container (the row) and look for the company link.
            parent_container = a_tag.find_parent(['li', 'tr', 'div'])
            symbol = "UNKNOWN_SYMBOL"
            
            if parent_container:
                for comp_link in parent_container.find_all('a'):
                    comp_href = comp_link.get('href', '')
                    if '/company/' in comp_href:
                        # Extracts "TCS" from "/company/TCS/" or "/company/TCS/consolidated/"
                        match = re.search(r'/company/([^/]+)/', comp_href)
                        if match:
                            symbol = match.group(1).upper()
                            break
            
            found_targets += 1
            clean_title = sanitize_filename(title)
            
            # Add today's date so you know when you downloaded it
            today_str = datetime.today().strftime('%Y-%m-%d')
            filename = f"{symbol}_{today_str}_{clean_title}.pdf"
            save_path = os.path.join(OUTPUT_DIR, filename)
            
            # Check if we already grabbed this file in a previous run
            if os.path.exists(save_path):
                continue
                
            print(f"    ↳ Match Found: [{symbol}] {title[:60]}...")
            if download_file(href, save_path):
                print(f"      [OK] Saved to disk.")
                
            time.sleep(1.5) # Protect against rate-limits from BSE/NSE servers

    print(f"[*] Page {page_number} complete. Found {found_targets} targeted filings.")

def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    
    print(f"\n{'='*50}")
    print("=== DAILY MARKET FILINGS MONITOR ===")
    print(f"{'='*50}")
    print(f"[*] Target Location: {OUTPUT_DIR}")
    print(f"[*] Tracking Keywords: {', '.join(TARGET_KEYWORDS)}")
    
    for page in range(1, PAGES_TO_SCAN + 1):
        scan_filings_page(page)
        # Sleep between page loads so Screener doesn't ban our IP
        time.sleep(3)
        
    print("\n=== MARKET FEED SCAN COMPLETE ===")

if __name__ == "__main__":
    main()
