import os
import glob
import json
import re
import time
import sys
import logging
import google.generativeai as genai
from google.api_core.exceptions import ResourceExhausted
from typing import Dict, Any

# ==========================================
# 1. CONFIGURATION & LOGGING SETUP
# ==========================================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("pipeline_execution.log", encoding="utf-8")
    ]
)
logger = logging.getLogger("ConcallPipeline")

# --- Thresholds & Settings ---
MAX_RUNTIME_SECONDS = (4 * 3600) + (45 * 60)  # 4 Hours 45 Minutes
RATE_LIMIT_DELAY = 6.0       
FLASH_TOKEN_LIMIT = 150000   
LITE_TOKEN_LIMIT = 200000    

# ==========================================
# 2. CONTROLLERS & MANAGERS
# ==========================================
class DualModelController:
    """Manages API logic, token accounting, and dual-model failover."""
    
    def __init__(self, api_key: str):
        if not api_key:
            logger.critical("GEMINI_API_KEY environment variable is missing.")
            sys.exit(1)
            
        genai.configure(api_key=api_key)
        self.tokens_consumed = {"gemini-2.5-flash": 0, "gemini-2.5-flash-lite": 0}
        self.limits = {"gemini-2.5-flash": FLASH_TOKEN_LIMIT, "gemini-2.5-flash-lite": LITE_TOKEN_LIMIT}
        
        self.model_order = ["gemini-2.5-flash-lite", "gemini-2.5-flash"]
        self.current_model_index = 0
        self.active_model_name = self.model_order[self.current_model_index]
        self.active_model = genai.GenerativeModel(self.active_model_name)
        logger.info(f"Initialized Model Controller. Primary: {self.active_model_name}")

    def get_active_model(self) -> genai.GenerativeModel:
        return self.active_model

    def trigger_serverless_exit(self):
        """Exits cleanly to allow GitHub Actions to commit files, avoiding runner time waste."""
        logger.warning("🚨 ALL TOKEN POOLS EXHAUSTED or API QUOTA HIT.")
        logger.info("Triggering graceful exit. The pipeline will resume automatically on the next scheduled GitHub run.")
        sys.exit(0)

    def track_and_validate(self, usage_metadata) -> bool:
        if not usage_metadata:
            return True
            
        total_tokens = getattr(usage_metadata, "total_token_count", 0)
        self.tokens_consumed[self.active_model_name] += total_tokens
        
        current_used = self.tokens_consumed[self.active_model_name]
        limit = self.limits[self.active_model_name]
        
        logger.info(f"[{self.active_model_name}] Tokens Used: {total_tokens} | Total: {current_used}/{limit}")
        
        if current_used >= limit:
            logger.warning(f"⚠️ Limit breached for {self.active_model_name}.")
            self.current_model_index += 1
            
            if self.current_model_index < len(self.model_order):
                self.active_model_name = self.model_order[self.current_model_index]
                self.active_model = genai.GenerativeModel(self.active_model_name)
                logger.info(f"🔄 Shifted to fallback model: {self.active_model_name}")
            else:
                self.trigger_serverless_exit()
        return True

class PipelineStateManager:
    """Handles saving and loading the execution state to disk for a specific stock folder."""
    
    def __init__(self, folder_path: str):
        self.state_file_path = os.path.join(folder_path, "pipeline_progress_state.json")
        self.state = self._load_state()

    def _load_state(self) -> Dict[str, Any]:
        if os.path.exists(self.state_file_path):
            try:
                with open(self.state_file_path, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception as e:
                logger.error(f"State file read error: {e}. Creating fresh state.")
        return {"indexed_files": [], "completed_files": []}

    def save_state(self):
        try:
            with open(self.state_file_path, 'w', encoding='utf-8') as f:
                json.dump(self.state, f, indent=4)
        except Exception as e:
            logger.error(f"Error preserving state to disk: {e}")

# ==========================================
# 3. CORE LOGIC WRAPPERS
# ==========================================
def safe_api_call(controller: DualModelController, prompt: str, max_retries: int = 3):
    attempt = 0
    while attempt < max_retries:
        try:
            model = controller.get_active_model()
            response = model.generate_content(prompt)
            controller.track_and_validate(response.usage_metadata)
            return response
            
        except ResourceExhausted:
            logger.error(f"🛑 Rate Limit Hit (ResourceExhausted).")
            controller.trigger_serverless_exit()
            
        except Exception as e:
            logger.error(f"API Error: {e}")
            attempt += 1
            if attempt < max_retries:
                logger.info(f"Retrying ({attempt}/{max_retries}) in 30 seconds...")
                time.sleep(30)
            else:
                raise Exception(f"Failed to process prompt after {max_retries} attempts. Error: {e}")

# ==========================================
# 4. SINGLE STOCK PIPELINE ENGINE
# ==========================================
def process_stock_folder(folder_path: str, controller: DualModelController, global_start_time: float):
    """Processes all files within a single stock's subdirectory."""
    logger.info(f"\n" + "="*50)
    logger.info(f"📁 ENTERING DIRECTORY: {os.path.basename(folder_path)}")
    logger.info("="*50)

    state_mgr = PipelineStateManager(folder_path)
    state = state_mgr.state

    # --- PASS 1: Chronological Indexing ---
    all_files = glob.glob(os.path.join(folder_path, "*.md")) + glob.glob(os.path.join(folder_path, "*.txt"))
    raw_files = [f for f in all_files if not f.endswith("_Summary.md")]
    
    existing_indexed = {item["file_path"] for item in state["indexed_files"]}
    new_files = [f for f in raw_files if f not in existing_indexed]

    if new_files:
        logger.info(f"Indexing metadata for {len(new_files)} raw documents in {os.path.basename(folder_path)}...")
        for file_path in new_files:
            
            # Global Time Check
            if (time.time() - global_start_time) > MAX_RUNTIME_SECONDS:
                logger.warning("⏱️ GitHub Time Limit Approaching. Exiting cleanly.")
                state_mgr.save_state()
                sys.exit(0)

            with open(file_path, 'r', encoding='utf-8') as f:
                snippet = f.read(6000)

            prompt = f"Analyze this earnings call snippet. Return ONLY a raw JSON object with keys 'quarter' (e.g. 'Q1','Q4') and 'year' (4-digit int). No markdown.\n\nSnippet:\n{snippet}"
            
            try:
                response = safe_api_call(controller, prompt)
                clean_json = re.sub(r'```json|```', '', response.text).strip()
                meta_data = json.loads(clean_json)
                
                state["indexed_files"].append({
                    "file_path": file_path,
                    "quarter": meta_data["quarter"].upper(),
                    "year": int(meta_data["year"])
                })
                state_mgr.save_state()
                time.sleep(RATE_LIMIT_DELAY)
            except Exception as e:
                logger.error(f"Failed indexing {os.path.basename(file_path)}: {e}")
                continue

    q_map = {"Q1": 1, "Q2": 2, "Q3": 3, "Q4": 4}
    sorted_timeline = sorted(state["indexed_files"], key=lambda x: (x["year"], q_map.get(x["quarter"], 0)))

    # --- PASS 2: Sequential Audit & Summarization ---
    pending_files = [f for f in sorted_timeline if f["file_path"] not in state["completed_files"]]
    
    if not pending_files:
        logger.info(f"✔️ All documents in {os.path.basename(folder_path)} are fully processed. Moving on.")
        return

    logger.info(f"Commencing generation for {len(pending_files)} pending files...")
    
    for idx, file_info in enumerate(sorted_timeline):
        file_path = file_info["file_path"]
        
        if file_path in state["completed_files"]:
            continue
            
        # Global Time Check
        if (time.time() - global_start_time) > MAX_RUNTIME_SECONDS:
            logger.warning("⏱️ GitHub Time Limit Approaching. Halting cleanly to allow git commit.")
            state_mgr.save_state()
            sys.exit(0)
            
        current_label = f"{file_info['quarter']}_{file_info['year']}"
        output_summary_path = os.path.join(folder_path, f"{current_label}_Summary.md")
        
        # Load Previous Context
        prev_summary_content = ""
        if idx > 0:
            prev_info = sorted_timeline[idx - 1]
            prev_summary_path = os.path.join(folder_path, f"{prev_info['quarter']}_{prev_info['year']}_Summary.md")
            if os.path.exists(prev_summary_path):
                with open(prev_summary_path, 'r', encoding='utf-8') as pf:
                    prev_summary_content = pf.read()

        logger.info(f"Analyzing [ {current_label} ]")
        with open(file_path, 'r', encoding='utf-8') as f:
            current_transcript = f.read()

        prompt = f"""
        You are a highly analytical equity research tool. Synthesize a structured, clean One-Page Executive Summary 
        for the current earnings transcript: {current_label}.
        
        CONTEXT FROM PREVIOUS HISTORICAL SUMMARY:
        {prev_summary_content if prev_summary_content else "No historical summary available. This is the absolute oldest transcript. Initialize baseline targets here."}
        
        CURRENT TRANSCRIPT TO PROCESS:
        {current_transcript}
        
        OUTPUT FORMAT STRATEGY:
        Use clean Markdown headers. Output exactly these sections:
        ## Executive Overview
        ## Key Financial Metrics
        ## Business Strategy & Drivers
        ## Management Accountability & Consistency Audit
           - Using the historical context provided, explicitly audit whether management hit their stated targets, or if their narrative/tone shifted regarding headwinds. Be factual.
           - Extract all new forward-looking promises, guidance margins, or timelines stated in this current transcript to track next quarter.
        """

        try:
            response = safe_api_call(controller, prompt)
            
            with open(output_summary_path, 'w', encoding='utf-8') as out_f:
                out_f.write(response.text)
                
            logger.info(f"✅ Generated and saved: {current_label}_Summary.md")
            
            state["completed_files"].append(file_path)
            state_mgr.save_state()
            time.sleep(RATE_LIMIT_DELAY)
            
        except Exception as e:
            logger.error(f"Critical error on {current_label}: {e}")
            continue

# ==========================================
# 5. MASTER ORCHESTRATOR
# ==========================================
def process_all_stocks(root_directory: str):
    """Recursively finds all folders containing transcript files and processes them."""
    global_start_time = time.time()
    api_key = os.getenv("GEMINI_API_KEY")
    
    try:
        model_controller = DualModelController(api_key)
    except SystemExit:
        return
        
    if not os.path.exists(root_directory):
        logger.error(f"Root directory '{root_directory}' does not exist.")
        return
        
    # Recursively find all directories that actually contain .md or .txt files
    # (ignoring files that end with _Summary.md to ensure we only target raw transcripts)
    stock_folders = set()
    for root, dirs, files in os.walk(root_directory):
        for file in files:
            if (file.endswith(".md") or file.endswith(".txt")) and not file.endswith("_Summary.md"):
                stock_folders.add(root)

    # Sort alphabetically to maintain predictable order (e.g., ABB, RELIANCE, ZOMATO)
    stock_folders = sorted(list(stock_folders))
    
    if not stock_folders:
        logger.warning(f"No valid transcript files found inside {root_directory}")
        return

    logger.info(f"🔍 Discovered {len(stock_folders)} unique stock directories containing transcripts. Beginning master run.")

    for folder in stock_folders:
        process_stock_folder(folder, model_controller, global_start_time)
        
    logger.info("\n🎉 MASTER RUN COMPLETE. All folders processed successfully.")

if __name__ == "__main__":
    TARGET_DIR = "./ScreenerData"
    process_all_stocks(TARGET_DIR)
