#!/usr/bin/env python3
""" 
DNA Evolution Transcript Analyzer - World Class Edition 
Sequential learning system that builds evolving intelligence from earnings transcripts. 
Each transcript teaches the system about the company, enabling pattern recognition, deviation 
detection, and predictive modeling.
"""

import os
import re
import json
import sqlite3
import hashlib
from pathlib import Path
from datetime import datetime, timedelta
from typing import Dict, List, Tuple, Optional, Any, Set
from dataclasses import dataclass, field, asdict
from collections import defaultdict, Counter
import logging
import argparse

try:
    import pandas as pd
    import numpy as np
    from textblob import TextBlob
except ImportError:
    print("Installing required packages: pip install pandas numpy textblob")

# ====== LOGGING SETUP ======
logging.basicConfig(
    level=logging.INFO, 
    format='%(asctime)s - %(levelname)s - %(message)s', 
    handlers=[logging.FileHandler("dna_analyzer.log"), logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

# ====== DATA STRUCTURES ======

@dataclass
class QuarterData:
    """Complete data for a single quarter"""
    version: int = 1
    ticker: str = ""
    file_path: str
    quarter: str = ""
    date: datetime = field(default_factory=datetime.now)
    revenue_growth: Optional[float] = None
    margin: Optional[float] = None
    customer_count: Optional[int] = None
    customer_mentions: Dict = field(default_factory=dict)
    key_themes: List[str] = field(default_factory=list)
    narrative_key_themes: List[str] = field(default_factory=list)
    wins: List[str] = field(default_factory=list)
    challenges: List[str] = field(default_factory=list)
    product_updates: List[str] = field(default_factory=list)
    forward_looking_guidance: List[str] = field(default_factory=list)
    promises: List[Dict[str, str]] = field(default_factory=list)
    tone: str = "neutral"
    specificity: str = "medium"
    evidence_key_quotes: List[Tuple[str, str]] = field(default_factory=list)
    last_updated: str = ""

@dataclass
class Pattern:
    """A learned pattern"""
    pattern_id: str
    type: str
    rule: str = ""
    observations: int = 0
    accurate: int = 0
    confidence: float = 0.0
    examples: List[str] = field(default_factory=list)
    last_updated: str = ""

@dataclass
class Prediction:
    """A prediction made for future quarter"""
    prediction_id: str
    made_on_quarter: str
    target_quarter: str
    predictions: Dict[str, Any]
    confidence: float
    validation_date: Optional[datetime] = None
    actual_results: Optional[Dict[str, Any]] = None
    accuracy: Optional[float] = None
    validated: bool = False

@dataclass
class CompanyDNA:
    """The evolving intelligence about a company"""
    version: int = 1
    ticker: str = ""
    baseline_quarter: str
    latest_quarter: str
    timeline: List[QuarterData] = field(default_factory=list)
    open_promises: List[Dict] = field(default_factory=list)
    promises_tracking: Dict[str, Dict] = field(default_factory=dict)
    fulfilled_promises: List[Dict] = field(default_factory=list)
    broken_promises: List[Dict] = field(default_factory=list)
    failed_promises: List[Dict] = field(default_factory=list)
    patterns: Dict[str, Any] = field(default_factory=dict)
    predictions: List[Prediction] = field(default_factory=list)
    customer_evolution: Dict = field(default_factory=dict)
    theme_lifecycle: Dict = field(default_factory=dict)
    model_accuracy_prediction_accuracy: float = 0.0
    metadata_created_date: datetime = field(default_factory=datetime.now)
    last_updated: Optional[datetime] = None

# ====== FILE SCANNER ======
class FileScanner:
    """Scans folders and sorts transcripts chronologically"""
    def __init__(self):
        self.transcript_extensions = {'.txt', '.md', '.csv'}

    def scan_and_sort(self, folder_path: str, ticker: str) -> Tuple[List[Tuple[str, datetime, str]], Dict[str, List]]:
        """Scan folder, rename files to standard format, track changes, and return chronologically sorted transcripts"""
        logger.info(f"Scanning folder: {folder_path}")
        transcripts = []
        file_stats = {'renames': [], 'duplicates': []}
        
        for root, dirs, files in os.walk(folder_path):
            dirs[:] = [d for d in dirs if not d.startswith('.')]
            for file in files:
                if any(file.endswith(ext) for ext in self.transcript_extensions):
                    file_path = os.path.join(root, file)
                    date, quarter = self._extract_date_from_file(file_path)
                    
                    if date and quarter:
                        q_parts = quarter.split()
                        sortable_q = f"{q_parts[1]}_{q_parts[0]}" if len(q_parts) == 2 else quarter.replace(' ', '_')
                        ext = os.path.splitext(file)[1]
                        
                        new_filename = f"{ticker}_{sortable_q}{ext}"
                        new_file_path = os.path.join(root, new_filename)
                        
                        if new_file_path != file_path:
                            try:
                                if os.path.exists(new_file_path):
                                    size_old = os.path.getsize(new_file_path)
                                    size_new = os.path.getsize(file_path)
                                    if size_new > size_old:
                                        os.remove(new_file_path)
                                        os.rename(file_path, new_file_path)
                                        file_stats['renames'].append((file, new_filename))
                                        file_stats['duplicates'].append(os.path.basename(new_file_path) + " (smaller old version)")
                                        logger.info(f"Replaced duplicate with larger file: {new_filename}")
                                    else:
                                        os.remove(file_path)
                                        file_stats['duplicates'].append(file)
                                        logger.info(f"Deleted inferior duplicate: {file}")
                                        continue 
                                else:
                                    os.rename(file_path, new_file_path)
                                    file_stats['renames'].append((file, new_filename))
                                    logger.info(f"Renamed {file} -> {new_filename}")
                            except Exception as e:
                                logger.error(f"Failed to rename {file}: {e}")
                                new_file_path = file_path 
                        
                        transcripts.append((new_file_path, date, quarter))
                        
        if not transcripts:
            return [], file_stats
            
        quarter_map = {}
        for file_path, date, quarter in transcripts:
            if quarter not in quarter_map or date > quarter_map[quarter][1]:
                if quarter in quarter_map:
                    file_stats['duplicates'].append(os.path.basename(quarter_map[quarter][0]) + f" (older date for {quarter})")
                quarter_map[quarter] = (file_path, date, quarter)
                
        unique_transcripts = list(quarter_map.values())
        unique_transcripts.sort(key=lambda x: x[1])
        
        logger.info(f"Total unique transcripts found: {len(unique_transcripts)} (filtered out duplicates)")
        return unique_transcripts, file_stats

    def _extract_date_from_file(self, file_path: str) -> Tuple[Optional[datetime], Optional[str]]:
        """Extract date and quarter from file content"""
        date = None
        quarter = None
        
        try:
            with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                content = f.read(5000) 
            
            # 1. Look for explicit Quarter mentions in the text
            q_match = re.search(r'\b(Q[1-4]|Quarter [1-4])\s*(?:FY|-| )?\s*(?:20)?(\d{2})\b', content, re.IGNORECASE)
            if q_match:
                q_num = q_match.group(1).upper().replace('UARTER ', '')
                year_suffix = q_match.group(2)
                quarter = f"{q_num} 20{year_suffix}"
                
            # 2. Look for dates
            patterns = [
                (r'(January|February|March|April|May|June|July|August|September|October|November|December)\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4})', 'mdy'),
                (r'(\d{1,2})\s+(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+(\d{4})', 'dmy_alpha'),
                (r'(\d{4})-(\d{2})-(\d{2})', 'ymd'),
                (r'(\d{1,2})[-/](\d{1,2})[-/](\d{4})', 'dmy_slash'),
            ]
            
            months = {
                'jan': 1, 'feb': 2, 'mar': 3, 'apr': 4, 'may': 5, 'jun': 6,
                'jul': 7, 'aug': 8, 'sep': 9, 'oct': 10, 'nov': 11, 'dec': 12,
                'january': 1, 'february': 2, 'march': 3, 'april': 4, 'june': 6, 'july': 7,
                'august': 8, 'september': 9, 'october': 10, 'november': 11, 'december': 12
            }
            
            for pattern, date_type in patterns:
                match = re.search(pattern, content, re.IGNORECASE)
                if match:
                    try:
                        if date_type == 'mdy':
                            m_str, d, y = match.groups()
                            temp_date = datetime(int(y), months[m_str.lower()[:3]], int(d))
                        elif date_type == 'dmy_alpha':
                            d, m_str, y = match.groups()
                            temp_date = datetime(int(y), months[m_str.lower()[:3]], int(d))
                        elif date_type == 'ymd':
                            y, m, d = match.groups()
                            temp_date = datetime(int(y), int(m), int(d))
                        elif date_type == 'dmy_slash':
                            d, m, y = match.groups()
                            temp_date = datetime(int(y), int(m), int(d))
                            
                        if datetime(2010, 1, 1) <= temp_date <= datetime.now() + timedelta(days=365):
                            date = temp_date
                            break
                    except:
                        continue

        except Exception as e:
            logger.error(f"Error reading content of {file_path}: {e}")

        # Derive missing values
        if date and not quarter:
            quarter = f"Q{(date.month-1)//3+1} {date.year}"
            
        if quarter and not date:
            try:
                q_num = int(quarter[1])
                y_num = int(quarter.split()[1])
                month_map = {1: 3, 2: 6, 3: 9, 4: 12}
                date = datetime(y_num, month_map[q_num], 28)
            except:
                pass
                
        return date, quarter

# ====== TRANSCRIPT EXTRACTOR ======
class TranscriptExtractor:
    """EXTRACTS ALL DATA FROM TRANSCRIPT"""
    def __init__(self):
        self.sector_leaders = {
            'NVIDIA', 'NVDA', 'TSMC', 'ASML', 'APPLE', 'AAPL', 'MICROSOFT', 'MSFT', 
            'GOOGLE', 'GOOGL', 'AMAZON', 'AMZN', 'META', 'TESLA', 'TSLA', 'AMD', 
            'INTEL', 'INTC', 'QUALCOMM', 'QCOM', 'BROADCOM', 'AVGO', 'ORACLE', 
            'ORCL', 'SALESFORCE', 'CRM', 'ADOBE', 'ADBE'
        }

    def extract(self, file_path: str, quarter: str, date: datetime) -> QuarterData:
        """Extract all data from transcript"""
        try:
            with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                content = f.read()
                
            data = QuarterData(ticker="UNKNOWN", file_path=file_path, quarter=quarter, date=date)
            data.revenue_growth = self._extract_revenue_growth(content)
            data.margin = self._extract_margin(content)
            data.customer_count = self._extract_customer_count(content)
            data.key_themes = self._extract_themes(content)
            data.wins = self._extract_wins(content)
            data.challenges = self._extract_challenges(content)
            data.product_updates = self._extract_product_updates(content)
            data.forward_looking_guidance = self._extract_guidance(content)
            data.promises = self._extract_promises(content, quarter)
            data.tone = self._assess_tone(content)
            data.specificity = self._assess_specificity(content)
            data.evidence_key_quotes = self._extract_quotes(content)
            return data
            
        except Exception as e:
            logger.error(f"Error extracting data: {e}")
            return QuarterData(ticker="UNKNOWN", file_path=file_path, quarter=quarter, date=date)

    def _extract_revenue_growth(self, text: str) -> Optional[float]:
        patterns = [
            r'revenue.*?grew.*?(\d+(?:\.\d+)?)',
            r'revenue.*?increased.*?(\d+(?:\.\d+)?)',
            r'sales.*?up.*?(\d+(?:\.\d+)?)'
        ]
        for pattern in patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                try:
                    growth = float(match.group(1))
                    if growth < 500: return growth
                except: continue
        return None

    def _extract_margin(self, text: str) -> Optional[float]:
        patterns = [r'gross margin.*?(\d+(?:\.\d+)?)\%', r'margin.*?(\d+(?:\.\d+)?)\%']
        for pattern in patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                try:
                    margin = float(match.group(1))
                    if margin < 100: return margin
                except: pass
        return None

    def _extract_customer_count(self, text: str) -> Optional[int]:
        patterns = [r'customers.*?(\d+)', r'customer base.*?(\d+)']
        for pattern in patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                try:
                    count = int(match.group(1))
                    if count < 1000000000: return count
                except: continue
        return None

    def _extract_themes(self, text: str) -> List[str]:
        theme_patterns = {
            'AI/ML': r' (?:AI|artificial intelligence|machine learning|generative AI) ',
            'International Expansion': r' (?:international|global|overseas|expansion|geographic) ',
            'New Products': r' (?:new product|launch|release|introduce|unveil|shipping) ',
            'Customer Acquisition': r' (?:customer acquisition|new customer|growth) ',
            'Market Share': r' (?:market share|competitive position|market leadership) ',
            'Profitability': r' (?:profitability|profitable|margins|operating leverage) ',
            'R&D': r' (?:research|development|innovation|engineering) ',
            'Partnerships': r' (?:partnership|collaboration|strategic alliance|joint venture) ',
            'M&A': r' (?:acquisition|merger|M&A|acquiring|acquire) ',
            'Cost Control': r' (?:cost control|efficiency|optimization|streamline) '
        }
        themes = []
        for theme, pattern in theme_patterns.items():
            count = len(re.findall(pattern, text, re.IGNORECASE))
            if count >= 3: 
                themes.append(theme)
        return themes[:7]

    def _extract_wins(self, text: str) -> List[str]:
        patterns = [
            r'(?:excited to|proud to|pleased to).*?(?:announce|report|share).*?(?:\.|\n)',
            r'(?:record|best ever|highest|strongest|unprecedented).*?(?:\.|\n)',
            r'(?:exceeded|surpassed|beat|outperformed).*?(?:\.|\n)'
        ]
        wins = []
        for pattern in patterns:
            matches = re.findall(pattern, text, re.IGNORECASE)
            for match in matches:
                cleaned = match.strip()
                if len(cleaned) > 20 and any(word in cleaned.lower() for word in ['customer', 'client', 'partner', 'contract', 'design win', 'collaboration', 'award', 'leader']):
                    wins.append(cleaned)
        return wins[:5]

    def _extract_challenges(self, text: str) -> List[str]:
        patterns = [
            r'(?:challenge|headwind|difficulty|pressure).*?(?:faced|facing|addressing|dealing with|due to).*?(?:\.|\n)',
            r'(?:impacted by|offset by|partially offset by).*?(?:\.|\n)'
        ]
        challenges = []
        for pattern in patterns:
            matches = re.findall(pattern, text, re.IGNORECASE)
            for match in matches:
                cleaned = match.strip()
                if len(cleaned) > 20 and any(word in cleaned.lower() for word in ['cost', 'supply', 'demand', 'economic']):
                    challenges.append(cleaned)
        return challenges[:5]

    def _extract_product_updates(self, text: str) -> List[str]:
        updates = []
        patterns = [
            r'(?:launched|released|introduced|unveiled|shipping) ([^.!?]{20,200}[.!?])',
            r'(?:new product|new service|new offering|new solution) ([^.!?]{20,200}[.!?])',
        ]
        for pattern in patterns:
            matches = re.findall(pattern, text, re.IGNORECASE)
            for match in matches[:3]:
                updates.append(match.strip())
        return updates[:5]

    def _extract_guidance(self, text: str) -> List[str]:
        guidance = []
        patterns = [
            r'(?:expect|anticipate|forecast|project|guide|guidance) ([^.!?]{30,250}[.!?])',
            r'(?:Q[1-4]|next quarter|fiscal year|FY) ([^.!?]{30,250}[.!?])',
        ]
        for pattern in patterns:
            matches = re.findall(pattern, text, re.IGNORECASE)
            for match in matches[:4]:
                if any(word in match.lower() for word in ['revenue', 'growth', 'margin', 'earnings', 'profit']):
                    guidance.append(match.strip())
        return guidance[:7]

    def _extract_promises(self, text: str, quarter: str) -> List[Dict[str, str]]:
        promises = []
        patterns = [
            r'(?:by (?:Q[1-4]|end of|the end of))([^.!?]{20,200}[.!?])',
            r'(?:will|plan to|committed to|committing to) ([^.!?]{20,200}[.!?])',
            r'(?:targeting|aiming for|goal is) ([^.!?]{20,200}[.!?])',
        ]
        for pattern in patterns:
            matches = re.findall(pattern, text, re.IGNORECASE)
            for match in matches[:3]:
                promise_text = match.strip()
                if len(promise_text) > 20:
                    promises.append({
                        'text': promise_text,
                        'made_in': quarter,
                        'category': self._categorize_promise(promise_text)
                    })
        return promises[:5]

    def _categorize_promise(self, text: str) -> str:
        text_lower = text.lower()
        if any(word in text_lower for word in ['product', 'launch', 'release']):
            return 'product'
        elif any(word in text_lower for word in ['margin', 'profitability', 'profit']):
            return 'financial'
        elif any(word in text_lower for word in ['customer', 'client', 'user']):
            return 'customer'
        elif any(word in text_lower for word in ['hire', 'team', 'headcount']):
            return 'hiring'
        elif any(word in text_lower for word in ['facility', 'expansion', 'capacity']):
            return 'infrastructure'
        else:
            return 'other'

    def _assess_tone(self, text: str) -> str:
        try:
            blob = TextBlob(text[:10000])
            polarity = blob.sentiment.polarity
            
            defensive_words = ['uncertain', 'challenging', 'difficult', 'cautious', 'headwind', 'pressure']
            defensive_count = sum(text.lower().count(word) for word in defensive_words)
            
            if polarity > 0.15 and defensive_count < 5:
                return "positive"
            elif polarity < -0.05 or defensive_count > 10:
                return "cautious"
            elif defensive_count > 15:
                return "defensive"
            else:
                return "neutral"
        except:
            return "neutral"

    def _assess_specificity(self, text: str) -> str:
        specific_patterns = [
            r'\n\d+\%\b',
            r'\$\d+[MBK]?\b',
            r'\bQ[1-4]\s+\d{4}\b',
            r'\d+\s+(?:customers|clients|engineers|employees)',
        ]
        specific_count = sum(len(re.findall(p, text)) for p in specific_patterns)
        words = len(text.split())
        
        if words == 0: return "low"
        ratio = (specific_count / words) * 1000
        
        if ratio > 30: return "high"
        elif ratio > 15: return "medium"
        else: return "low"

    def _extract_quotes(self, text: str) -> List[Tuple[str, str]]:
        quotes = []
        patterns = [
            r'(CEO|Chief Executive|Founder|CFO|Chief Financial).*?(?:\:-|:)\s*([^\.!?]{50,350}[\.!?])',
        ]
        for pattern in patterns:
            matches = re.findall(pattern, text, re.IGNORECASE)
            for speaker, quote in matches[:5]:
                quotes.append((speaker.strip(), quote.strip()))
        return quotes[:5]

# ====== DNA BUILDER ======
class DNABuilder:
    """BUILDS AND EVOLVES COMPANY DNA"""
    def build_baseline(self, ticker: str, first_quarter_data: QuarterData) -> CompanyDNA:
        logger.info(f"Building baseline DNA for {ticker} from {first_quarter_data.quarter}")
        dna = CompanyDNA(
            ticker=ticker,
            baseline_quarter=first_quarter_data.quarter,
            latest_quarter=first_quarter_data.quarter,
            timeline=[first_quarter_data]
        )
        
        # Initialize customer tracking
        for customer, context in first_quarter_data.customer_mentions.items():
            dna.customer_evolution[customer] = {
                'first_mentioned': first_quarter_data.quarter,
                'mentions': [first_quarter_data.quarter],
                'evolution': ['prospect'],
                'contexts': [context]
            }
        return dna

    def evolve_dna(self, dna: CompanyDNA, new_quarter_data: QuarterData) -> CompanyDNA:
        dna.latest_quarter = new_quarter_data.quarter
        dna.timeline.append(new_quarter_data)
        
        self._update_customer_tracking(dna, new_quarter_data)
        self._update_theme_tracking(dna, new_quarter_data)
        self._check_promises(dna, new_quarter_data)
        
        for promise in new_quarter_data.promises:
            dna.open_promises.append({
                **promise,
                'promised_in': new_quarter_data.quarter,
                'due_quarter': self._estimate_due_quarter(new_quarter_data.quarter, promise.get('text', ''))
            })
            
        dna.last_updated = datetime.now()
        return dna

    def _update_customer_tracking(self, dna: CompanyDNA, new_quarter_data: QuarterData):
        mentioned_this_quarter = set(new_quarter_data.customer_mentions.keys())
        
        for customer in list(dna.customer_evolution.keys()):
            if customer in mentioned_this_quarter:
                dna.customer_evolution[customer]['mentions'].append(new_quarter_data.quarter)
                context = new_quarter_data.customer_mentions[customer].lower()
                
                if 'production' in context or 'shipping' in context: stage = 'production'
                elif 'design win' in context or 'select' in context: stage = 'design_win'
                elif 'expand' in context or 'ramp' in context: stage = 'expansion'
                else: stage = 'active'
                    
                if isinstance(dna.customer_evolution[customer]['evolution'], list):
                    dna.customer_evolution[customer]['evolution'].append(stage)
                
            else:
                dna.customer_evolution[customer]['status'] = 'fading'
                
        for customer in mentioned_this_quarter:
            if customer not in dna.customer_evolution:
                dna.customer_evolution[customer] = {
                    'first_mentioned': new_quarter_data.quarter,
                    'mentions': [new_quarter_data.quarter],
                    'evolution': ['new'],
                    'contexts': [new_quarter_data.customer_mentions[customer]]
                }

    def _update_theme_tracking(self, dna: CompanyDNA, new_quarter_data: QuarterData):
        mentioned_this_quarter = set(new_quarter_data.key_themes)
        
        for theme in list(dna.theme_lifecycle.keys()):
            if theme in mentioned_this_quarter:
                dna.theme_lifecycle[theme]['mentions'].append(new_quarter_data.quarter)
                dna.theme_lifecycle[theme]['status'] = 'active'
            else:
                if len(dna.theme_lifecycle[theme]['mentions']) > 3 and new_quarter_data.quarter not in dna.theme_lifecycle[theme]['mentions']:
                    dna.theme_lifecycle[theme]['status'] = 'fading'
                    
        for theme in mentioned_this_quarter:
            if theme not in dna.theme_lifecycle:
                dna.theme_lifecycle[theme] = {
                    'first_appeared': new_quarter_data.quarter,
                    'mentions': [new_quarter_data.quarter],
                    'status': 'new'
                }

    def _check_promises(self, dna: CompanyDNA, new_data: QuarterData):
        current_text = ' '.join([
            str(new_data.revenue_growth or ''),
            str(new_data.margin or ''),
            ' '.join(new_data.wins),
            ' '.join(getattr(new_data, 'product_updates', []))
        ]).lower()
        
        still_open = []
        
        for promise in dna.open_promises:
            promise_keywords = self._extract_keywords(promise.get('text', ''))
            delivered = False
            evidence = ""
            
            if any(kw in current_text for kw in promise_keywords):
                delivered = True
                evidence = f"Keywords found in {new_data.quarter} data"
                
            if promise.get('category') == 'financial' and new_data.margin:
                if 'margin' in promise.get('text', '').lower():
                    target = self._extract_number(promise.get('text', ''))
                    if target and new_data.margin >= target * 0.95:
                        delivered = True
                        evidence = f"Margin {new_data.margin}% vs target {target}%"
                        
            if delivered:
                dna.fulfilled_promises.append({
                    **promise,
                    'fulfilled_in': new_data.quarter,
                    'evidence': evidence
                })
            else:
                quarters_since = self._quarters_between(promise.get('promised_in', ''), new_data.quarter)
                if quarters_since > 4: 
                    dna.broken_promises.append({
                        **promise,
                        'broken_in': new_data.quarter,
                        'quarters_overdue': quarters_since
                    })
                else:
                    still_open.append(promise)
                    
        dna.open_promises = still_open

    def _extract_keywords(self, text: str) -> List[str]:
        common = {'will', 'plan', 'to', 'the', 'a', 'an', 'by', 'in', 'on', 'of', 'and', 'or'}
        words = re.findall(r' \w+ ', text.lower())
        return [w for w in words if w not in common and len(w) > 3][:5]

    def _extract_number(self, text: str) -> Optional[float]:
        match = re.search(r'(\d+(?:\.\d+)?)', text)
        if match:
            try: return float(match.group(1))
            except: return None
        return None

    def _estimate_due_quarter(self, current_quarter: str, promise_text: str) -> str:
        quarter_match = re.search(r'Q([1-4])', promise_text)
        year_match = re.search(r'20\d{2}', promise_text)
        if quarter_match and year_match:
            return f"Q{quarter_match.group(1)} {year_match.group(0)}"
        return "estimated +2Q"

    def _quarters_between(self, q1: str, q2: str) -> int:
        try:
            q1_parts = q1.split()
            q2_parts = q2.split()
            q1_num = int(q1_parts[0][1])
            q1_year = int(q1_parts[1])
            q2_num = int(q2_parts[0][1])
            q2_year = int(q2_parts[1])
            return (q2_year - q1_year) * 4 + (q2_num - q1_num)
        except: return 0

# ====== PATTERN LEARNER ======
class PatternLearner:
    """Learns patterns from DNA timeline"""
    def learn_patterns(self, dna: CompanyDNA) -> CompanyDNA:
        if len(dna.timeline) < 2: return dna
        self._learn_tone_patterns(dna)
        self._learn_promise_patterns(dna)
        self._learn_customer_patterns(dna)
        self._learn_growth_patterns(dna)
        self._learn_seasonal_patterns(dna)
        return dna

    def _learn_tone_patterns(self, dna: CompanyDNA):
        tone_transitions = defaultdict(lambda: {'next_growth': [], 'next_tone': []})
        for i in range(len(dna.timeline)-1):
            current = dna.timeline[i]
            next_q = dna.timeline[i+1]
            tone_transitions[current.tone]['next_growth'].append(next_q.revenue_growth)
            tone_transitions[current.tone]['next_tone'].append(next_q.tone)
            
        for tone, outcomes in tone_transitions.items():
            if outcomes['next_growth']:
                valid_growths = [g for g in outcomes['next_growth'] if g is not None]
                if valid_growths:
                    avg_growth = sum(valid_growths) / len(valid_growths)
                    pattern = Pattern(
                        pattern_id=f"tone_{tone}_predicts_growth",
                        type="tone_growth",
                        rule=f"When tone is {tone}, next quarter growth is {avg_growth}",
                        observations=len(valid_growths),
                        accurate=len([g for g in valid_growths if g > 0]),
                        confidence=min(0.95, len(valid_growths) / 10),
                        last_updated=datetime.now().isoformat()
                    )
                    dna.patterns[f"tone_{tone}_predicts_growth"] = pattern

    def _learn_promise_patterns(self, dna: CompanyDNA):
        total = len(dna.fulfilled_promises) + len(dna.broken_promises)
        if total < 3: return
        recent_fulfilled = len([p for p in dna.fulfilled_promises[-3:]])
        recent_total = len(dna.fulfilled_promises[-3:]) + len(dna.broken_promises[-3:])
        recent_rate = recent_fulfilled / recent_total if recent_total > 0 else 0
        
        pattern = Pattern(
            pattern_id="promise_delivery",
            type="promise_delivery",
            rule=f"Management delivers {recent_rate*100:.0f}% of promises",
            observations=total,
            accurate=recent_fulfilled,
            confidence=min(0.95, total / 10),
            last_updated=datetime.now().isoformat()
        )
        dna.patterns['promise_delivery'] = pattern

    def _learn_customer_patterns(self, dna: CompanyDNA):
        for customer, data in dna.customer_evolution.items():
            if len(data['mentions']) >= 3:
                consecutive = len(data['mentions'])
                pattern = Pattern(
                    pattern_id=f"customer_{customer}",
                    type="customer",
                    rule=f"{customer} mentioned in {consecutive} consecutive quarters",
                    observations=consecutive,
                    accurate=consecutive,
                    confidence=min(0.95, consecutive / 8),
                    examples=data.get('contexts', [])[:2],
                    last_updated=datetime.now().isoformat()
                )
                dna.patterns[f"customer_{customer}"] = pattern

    def _learn_growth_patterns(self, dna: CompanyDNA):
        growth_rates = [q.revenue_growth for q in dna.timeline if q.revenue_growth is not None]
        if len(growth_rates) >= 3:
            recent = growth_rates[-3:]
            if all(recent[i] > recent[i-1] for i in range(1, len(recent))): trajectory = "accelerating"
            elif all(recent[i] < recent[i-1] for i in range(1, len(recent))): trajectory = "decelerating"
            elif max(recent) - min(recent) > 10: trajectory = "volatile"
            else: trajectory = "steady"
                
            pattern = Pattern(
                pattern_id="growth_trajectory",
                type="growth",
                rule=f"Growth trajectory: {trajectory} ({' -> '.join([f'{g:.0f}%' for g in recent])})",
                observations=len(recent),
                accurate=len(recent),
                confidence=min(0.90, len(growth_rates) / 8),
                last_updated=datetime.now().isoformat()
            )
            dna.patterns['growth_trajectory'] = pattern

    def _learn_seasonal_patterns(self, dna: CompanyDNA):
        quarterly_growth = defaultdict(list)
        for q in dna.timeline:
            if q.revenue_growth is not None:
                quarter_num = q.quarter.split()[0]
                quarterly_growth[quarter_num].append(q.revenue_growth)
                
        for quarter, growths in quarterly_growth.items():
            if len(growths) >= 2:
                avg = sum(growths) / len(growths)
                pattern = Pattern(
                    pattern_id=f"seasonal_{quarter}",
                    type="seasonal",
                    rule=f"{quarter} typically shows {avg:.1f}% growth (n={len(growths)})",
                    observations=len(growths),
                    accurate=len(growths),
                    confidence=min(0.80, len(growths) / 4),
                    last_updated=datetime.now().isoformat()
                )
                dna.patterns[f"seasonal_{quarter}"] = pattern

# ====== DEVIATION DETECTOR ======
class DeviationDetector:
    """Detects deviations from learned patterns"""
    def detect_deviations(self, dna: CompanyDNA, new_data: QuarterData) -> List[Dict[str, Any]]:
        deviations = []
        if len(dna.timeline) < 2: return deviations
        deviations.extend(self._check_growth_deviations(dna, new_data))
        deviations.extend(self._check_customer_deviations(dna, new_data))
        deviations.extend(self._check_theme_deviations(dna, new_data))
        deviations.extend(self._check_tone_deviations(dna, new_data))
        return deviations

    def _check_growth_deviations(self, dna: CompanyDNA, new_data: QuarterData) -> List[Dict]:
        deviations = []
        if new_data.revenue_growth is None: return deviations
        historical_growth = [q.revenue_growth for q in dna.timeline[:-1] if q.revenue_growth is not None]
        if not historical_growth: return deviations
            
        avg = sum(historical_growth) / len(historical_growth)
        diff = new_data.revenue_growth - avg
        
        if abs(diff) > 10: severity = 'major'
        elif abs(diff) > 5: severity = 'moderate'
        else: return deviations
            
        deviations.append({
            'type': 'growth_vs_average',
            'severity': severity,
            'description': f"Growth ({new_data.revenue_growth}%) vs historical avg ({avg:.1f}%) diff: {diff:+.1f}pp"
        })
        return deviations

    def _check_customer_deviations(self, dna: CompanyDNA, new_data: QuarterData) -> List[Dict]:
        deviations = []
        for customer, data in dna.customer_evolution.items():
            if len(data['mentions']) >= 3:
                if customer not in new_data.customer_mentions:
                    last_mentions = data['mentions'][-3:]
                    if all(customer in getattr(q, 'customer_mentions', {}) for q in dna.timeline if q.quarter in last_mentions):
                        deviations.append({
                            'type': 'customer_silence',
                            'severity': 'major',
                            'description': f"{customer} not mentioned (was in {len(data['mentions'])} consecutive quarters)",
                            'icon': 'ð´'
                        })
        return deviations

    def _check_theme_deviations(self, dna: CompanyDNA, new_data: QuarterData) -> List[Dict]:
        deviations = []
        for theme, data in dna.theme_lifecycle.items():
            if data['status'] == 'active' and len(data['mentions']) >= 4:
                if theme not in new_data.key_themes:
                    deviations.append({
                        'type': 'theme_disappeared',
                        'severity': 'moderate',
                        'description': f"Theme '{theme}' absent (was in {len(data['mentions'])} quarters)",
                        'icon': 'ð '
                    })
                    
        existing_themes = set(dna.theme_lifecycle.keys())
        new_themes = set(new_data.key_themes) - existing_themes
        for theme in new_themes:
            deviations.append({
                'type': 'new_theme',
                'severity': 'minor',
                'description': f"NEW theme appeared: '{theme}'",
                'icon': 'ð¡'
            })
        return deviations

    def _check_tone_deviations(self, dna: CompanyDNA, new_data: QuarterData) -> List[Dict]:
        deviations = []
        if len(dna.timeline) >= 2:
            prev_tone = dna.timeline[-1].tone
            tone_progression = {'positive': 1, 'neutral': 0, 'cautious': -1, 'defensive': -2}
            
            prev_score = tone_progression.get(prev_tone, 0)
            new_score = tone_progression.get(new_data.tone, 0)
            
            if new_score < prev_score:
                severity = "major" if new_score <= -1 else "moderate"
                deviations.append({
                    'type': 'tone_shift',
                    'severity': severity,
                    'description': f"Tone shifted: {prev_tone.upper()} -> {new_data.tone.upper()}",
                    'icon': 'ð´' if severity == 'major' else 'ð '
                })
        return deviations

# ====== PREDICTION ENGINE ======
class PredictionEngine:
    """Makes predictions and validates them"""
    def make_predictions(self, dna: CompanyDNA) -> Optional[Prediction]:
        if len(dna.timeline) < 3: return None
            
        current_quarter = dna.timeline[-1]
        target_quarter = self._get_next_quarter(current_quarter.quarter)
        
        predictions = {}
        confidence_scores = []
        
        pred_growth, conf = self._predict_growth(dna)
        if pred_growth is not None:
            predictions['revenue_growth'] = pred_growth
            confidence_scores.append(conf)
            
        pred_tone, tone_conf = self._predict_tone(dna)
        if pred_tone:
            predictions['tone'] = pred_tone
            confidence_scores.append(tone_conf)
            
        pred_customers, cust_conf = self._predict_customers(dna)
        if pred_customers:
            predictions['customers'] = pred_customers
            confidence_scores.append(cust_conf)
            
        overall_confidence = sum(confidence_scores) / len(confidence_scores) if confidence_scores else 0.0
        
        prediction = Prediction(
            prediction_id=f"{dna.ticker}_{target_quarter}",
            made_on_quarter=current_quarter.quarter,
            target_quarter=target_quarter,
            predictions=predictions,
            confidence=overall_confidence
        )
        return prediction

    def _predict_growth(self, dna: CompanyDNA) -> Tuple[Optional[Dict], float]:
        """Predict next quarter growth"""
        growth_history = [q.revenue_growth for q in dna.timeline if q.revenue_growth]
        
        if len(growth_history) < 3:
            return None, 0.0
            
        recent = growth_history[-3:]
        
        # Simple trend-based prediction
        if len(recent) >= 3:
            # Check if accelerating/decelerating
            if all(recent[i] > recent[i-1] for i in range(1, len(recent))):
                # Accelerating - predict continuation
                avg_increase = (recent[-1] - recent[0]) / 2
                predicted = recent[-1] + avg_increase
                confidence = 0.70
            elif all(recent[i] < recent[i-1] for i in range(1, len(recent))):
                # Decelerating - predict continuation
                avg_decrease = (recent[0] - recent[-1]) / 2
                predicted = recent[-1] - avg_decrease
                confidence = 0.70
            else:
                # Use average
                predicted = sum(recent) / len(recent)
                confidence = 0.60
                
            # Create range
            range_low = predicted - 3
            range_high = predicted + 3
            
            return {
                'range': f"{range_low:.0f}-{range_high:.0f}%",
                'midpoint': predicted,
                'basis': 'trend_analysis'
            }, confidence
            
        return None, 0.0
        
    def _predict_tone(self, dna: CompanyDNA) -> Tuple[Optional[str], float]:
        """Predict next quarter tone"""
        if len(dna.timeline) < 2:
            return None, 0.0
            
        current_tone = dna.timeline[-1].tone
        
        # Check pattern
        if f"tone_{current_tone}" in dna.patterns:
            pattern = dna.patterns[f"tone_{current_tone}"]
            return current_tone, pattern.confidence
            
        # Default: assume continuation
        return current_tone, 0.50
        
    def _predict_customers(self, dna: CompanyDNA) -> Tuple[Optional[List[str]], float]:
        """Predict which customers will be mentioned"""
        # Find customers mentioned in last 3 quarters
        recent_quarters = dna.timeline[-3:]
        customer_frequency = defaultdict(int)
        
        for q in recent_quarters:
            for customer in getattr(q, 'customer_mentions', {}).keys():
                customer_frequency[customer] += 1
                
        # Predict customers mentioned 2+ times in last 3 quarters
        likely_customers = [c for c, freq in customer_frequency.items() if freq >= 2]
        
        if likely_customers:
            confidence = 0.75 if len(recent_quarters) >= 3 else 0.60
            return likely_customers, confidence
            
        return None, 0.0

    def validate_prediction(self, prediction: Prediction, actual_data: QuarterData) -> Prediction:
        """Validate prediction against actual data"""
        prediction.actual_results = {}
        correct = 0
        total = 0
        
        # Validate growth
        if 'revenue_growth' in prediction.predictions and actual_data.revenue_growth:
            pred_range = prediction.predictions['revenue_growth']['range']
            low, high = map(float, pred_range.replace('%', '').split('-'))
            
            actual = actual_data.revenue_growth
            prediction.actual_results['revenue_growth'] = actual
            
            total += 1
            if low <= actual <= high:
                correct += 1
                
        # Validate tone
        if 'tone' in prediction.predictions:
            pred_tone = prediction.predictions['tone']
            actual_tone = actual_data.tone
            prediction.actual_results['tone'] = actual_tone
            
            total += 1
            if pred_tone == actual_tone:
                correct += 1
                
        # Validate customers
        if 'customers' in prediction.predictions:
            pred_customers = set(prediction.predictions['customers'])
            actual_customers = set(actual_data.customer_mentions.keys())
            
            prediction.actual_results['customers'] = list(actual_customers)
            
            total += 1
            overlap = len(pred_customers.intersection(actual_customers))
            if overlap >= len(pred_customers) * 0.7:  # 70% match correct
                correct += 1
                
        # Calculate accuracy
        if total > 0:
            prediction.accuracy = correct / total
            prediction.validated = True
            
        return prediction

    def _get_next_quarter(self, current_quarter: str) -> str:
        try:
            parts = current_quarter.split(' ')
            q_num = int(parts[0][1])
            year = int(parts[1])
            if q_num == 4: return f"Q1 {year + 1}"
            else: return f"Q{q_num + 1} {year}"
        except: return "Q? ????"

# ====== DATABASE MANAGER ======
class DatabaseManager:
    """Manages dual storage: JSON files and SQLite database"""
    def __init__(self, output_dir: str, ticker: str):
        self.output_dir = Path(output_dir) / ticker
        self.dna_dir = self.output_dir / 'dna'
        self.db_path = self.output_dir / 'transcripts.db'
        self._init_database()

    def _init_database(self):
        self.dna_dir.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        cursor = self.conn.cursor()
        cursor.execute('''CREATE TABLE IF NOT EXISTS quarters (
            id INTEGER PRIMARY KEY AUTOINCREMENT, quarter TEXT UNIQUE, 
            date TEXT, revenue_growth REAL, margin REAL, tone TEXT, 
            specificity TEXT, data_json TEXT)''')
        self.conn.commit()

    def save_dna(self, dna: CompanyDNA):
        dna_dict = asdict(dna)
        json_path = self.dna_dir / f"DNA_{dna.latest_quarter.replace(' ', '_')}.json"
        
        with open(json_path, "w", encoding='utf-8') as f:
            json.dump(dna_dict, f, indent=2, default=str)
            
        latest_path = self.dna_dir / "DNA_LATEST.json"
        with open(latest_path, "w", encoding='utf-8') as f:
            json.dump(dna_dict, f, indent=2, default=str)
            
        logger.info(f"DNA v{dna.latest_quarter} saved to {json_path}")
        
    def close(self):
        if self.conn: self.conn.close()

# ====== AUDIT MANAGER ======
class AuditManager:
    """Audits and logs structural changes to the DNA state over time."""
    def __init__(self, output_dir: str, ticker: str):
        self.reports_dir = Path(output_dir) / ticker / 'reports'
        self.reports_dir.mkdir(parents=True, exist_ok=True)
        self.audit_path = self.reports_dir / '06_AUDIT_TRAIL.md'
        
        # Initialize file
        with open(self.audit_path, 'w', encoding='utf-8') as f:
            f.write(f"# ð¡ï¸ {ticker} - DNA Evolution Audit Trail\n\n")
            f.write("This document tracks exactly how the AI's understanding of the company evolves quarter over quarter.\n\n")
            

    def audit_file_system(self, file_stats: dict):
        with open(self.audit_path, 'a', encoding='utf-8') as f:
            f.write("## ð File System Audit\n\n")
            if file_stats['renames']:
                f.write(f"- ð **Renamed {len(file_stats['renames'])} raw files** to standard format.\n")
                for old, new in file_stats['renames']:
                    f.write(f"  - `{old}` -> `{new}`\n")
            if file_stats['duplicates']:
                f.write(f"- ðï¸ **Removed {len(file_stats['duplicates'])} duplicate/inferior files.**\n")
                for dup in file_stats['duplicates']:
                    f.write(f"  - `{dup}`\n")
            if not file_stats['renames'] and not file_stats['duplicates']:
                f.write("- â No file system changes needed. All files correctly formatted.\n")
            f.write("\n---\n\n")
            
    def audit(self, quarter: str, prev_dna_state: dict, new_dna: CompanyDNA, deviations: List[Dict]):
        with open(self.audit_path, 'a', encoding='utf-8') as f:
            f.write(f"## ð Quarter Processed: {quarter}\n")
            
            # 1. Pattern Changes
            new_patterns = len(new_dna.patterns) - prev_dna_state['pattern_count']
            if new_patterns > 0:
                f.write(f"- ð§  **Learned {new_patterns} new pattern(s)**\n")
                
            # 2. Deviations
            if deviations:
                f.write(f"- ð¨ **Detected {len(deviations)} deviation(s)** from historical behavior\n")
                for dev in deviations:
                    f.write(f"  - {dev.get('icon', '-')} {dev['description']}\n")
            else:
                f.write("- â Maintained consistent historical patterns (0 deviations)\n")
                
            # 3. Promises Tracking
            new_fulfilled = len(new_dna.fulfilled_promises) - prev_dna_state['fulfilled_count']
            new_broken = len(new_dna.broken_promises) - prev_dna_state['broken_count']
            
            if new_fulfilled > 0:
                f.write(f"- ð¤ Management fulfilled {new_fulfilled} previous commitment(s)\n")
            if new_broken > 0:
                f.write(f"- ð Management broke {new_broken} previous commitment(s)\n")
                
            f.write("\n---\n\n")

# ====== REPORT GENERATOR ======
class ReportGenerator:
    """Generates all markdown reports"""
    def __init__(self, output_dir: str, ticker: str):
        self.output_dir = Path(output_dir) / ticker
        self.reports_dir = self.output_dir / 'reports'
        self.reports_dir.mkdir(parents=True, exist_ok=True)

    def generate_quarter_report(self, dna: CompanyDNA, quarter_idx: int, deviations: List[Dict], prediction: Optional[Prediction] = None):
        """Generate comprehensive quarter report"""
        quarter_data = dna.timeline[quarter_idx]
        is_baseline = (quarter_idx == 0)
        
        # Format from "Q1 2022" to "2022_Q1" for proper alphabetical sorting in file explorers
        q_parts = quarter_data.quarter.split()
        if len(q_parts) == 2:
            sortable_q = f"{q_parts[1]}_{q_parts[0]}"
        else:
            sortable_q = quarter_data.quarter.replace(' ', '_')
            
        report_path = self.reports_dir / f"{sortable_q}_{'baseline' if is_baseline else 'analysis'}.md"
        
        with open(report_path, 'w', encoding='utf-8') as f:
            f.write(f"# {quarter_data.quarter} - {'Baseline Analysis' if is_baseline else 'Comprehensive Analysis'}\n\n")
            f.write(f"**DNA Version:** v{getattr(dna, 'version', 1)}\n")
            f.write(f"**Tone:** {quarter_data.tone.upper()} | **Specificity:** {quarter_data.specificity.upper()}\n\n")
            f.write("---\n\n")
            
            # Validate previous prediction if exists
            if quarter_idx > 0:
                prev_predictions = [p for p in dna.predictions if p.target_quarter == quarter_data.quarter]
                if prev_predictions:
                    f.write("## ð¯ Prediction Validation\n\n")
                    for pred in prev_predictions:
                        if getattr(pred, 'validated', False):
                            f.write(f"**Prediction made in {pred.made_on_quarter}:**\n\n")
                            
                            if 'revenue_growth' in pred.predictions:
                                pred_range = pred.predictions['revenue_growth'].get('range', 'N/A')
                                actual = getattr(pred, 'actual_results', {}).get('revenue_growth', 'N/A')
                                icon = "â" if getattr(pred, 'accuracy', 0) >= 0.8 else "â ï¸" if getattr(pred, 'accuracy', 0) >= 0.5 else "â"
                                f.write(f"{icon} **Growth Prediction:** {pred_range} | **Actual:** {actual}\n")
                                
                            if 'tone' in pred.predictions:
                                pred_tone = pred.predictions['tone']
                                actual_tone = getattr(pred, 'actual_results', {}).get('tone', 'N/A')
                                icon = "â" if pred_tone == actual_tone else "â"
                                f.write(f"{icon} **Tone Prediction:** {pred_tone.upper()} | **Actual:** {str(actual_tone).upper()}\n")
                                
                            f.write(f"\n**Overall Accuracy:** {getattr(pred, 'accuracy', 0)*100:.0f}%\n\n")
                    f.write("---\n\n")
                    
            f.write("## ð Key Metrics\n\n")
            if quarter_data.revenue_growth is not None:
                f.write(f"**Revenue Growth:** {quarter_data.revenue_growth:.1f}%\n")
                if not is_baseline and quarter_idx > 0:
                    prev_growth = dna.timeline[quarter_idx-1].revenue_growth
                    if prev_growth:
                        delta = quarter_data.revenue_growth - prev_growth
                        icon = "ð¢" if delta > 0 else "ð´" if delta < 0 else "âª"
                        f.write(f"  - vs Previous Quarter: {icon} {delta:+.1f}pp\n")
                        
                    baseline_growth = dna.timeline[0].revenue_growth
                    if baseline_growth:
                        delta = quarter_data.revenue_growth - baseline_growth
                        icon = "ð¢" if delta > 0 else "ð´"
                        f.write(f"  - vs Baseline ({dna.timeline[0].quarter}): {icon} {delta:+.1f}pp\n")
                        
                    all_growth = [q.revenue_growth for q in dna.timeline[:quarter_idx] if q.revenue_growth]
                    if all_growth:
                        avg = sum(all_growth) / len(all_growth)
                        delta = quarter_data.revenue_growth - avg
                        icon = "ð¢" if delta > 2 else "ð´" if delta < -2 else "âª"
                        f.write(f"  - vs Historical Average: {icon} {delta:+.1f}pp (avg: {avg:.1f}%)\n")
            f.write("\n")
            
            if quarter_data.margin is not None:
                f.write(f"**Gross Margin:** {quarter_data.margin:.1f}%\n\n")
                
            if deviations:
                f.write("## ð¨ Pattern Deviations Detected\n\n")
                critical = [d for d in deviations if d['severity'] == 'critical']
                major = [d for d in deviations if d['severity'] == 'major']
                moderate = [d for d in deviations if d['severity'] == 'moderate']
                minor = [d for d in deviations if d['severity'] == 'minor']
                
                for group, label in [(critical, 'CRITICAL'), (major, 'MAJOR'), (moderate, 'MODERATE'), (minor, 'MINOR')]:
                    if group:
                        f.write(f"### {label}\n\n")
                        for dev in group:
                            f.write(f"{dev.get('icon', '')} **{dev['type']}**: {dev['description']}\n")
                        f.write("\n")
                f.write("---\n\n")
                
            f.write("## ð Business Narrative\n\n")
            if quarter_data.key_themes:
                f.write("**Key Themes:**\n")
                for theme in quarter_data.key_themes:
                    if theme in dna.theme_lifecycle:
                        status = dna.theme_lifecycle[theme]['status']
                        mentions = len(dna.theme_lifecycle[theme]['mentions'])
                        if status == 'new': f.write(f"- ð {theme}\n")
                        else: f.write(f"- ð {theme} ({mentions} quarters)\n")
                    else:
                        f.write(f"- {theme}\n")
                f.write("\n")
                
            if quarter_data.wins:
                f.write("**Wins & Achievements:**\n")
                for win in quarter_data.wins: f.write(f"- â {win}\n")
                f.write("\n")
                
            if quarter_data.challenges:
                f.write("**Challenges Discussed:**\n")
                for challenge in quarter_data.challenges: f.write(f"- â ï¸ {challenge}\n")
                f.write("\n")
                
            if quarter_data.customer_mentions:
                f.write("**Customer Mentions:**\n")
                for customer, context in quarter_data.customer_mentions.items():
                    if customer in dna.customer_evolution:
                        mentions = len(dna.customer_evolution[customer].get('mentions', []))
                        if dna.customer_evolution[customer].get('first_mentioned') == quarter_data.quarter:
                            f.write(f"- ð **{customer}**: {context[:150]}...\n")
                        else:
                            f.write(f"- ð **{customer}** ({mentions} mentions): {context[:150]}...\n")
                f.write("\n")
                
            if quarter_data.product_updates:
                f.write("**Product Updates:**\n")
                for update in quarter_data.product_updates: f.write(f"- ð {update}\n")
                f.write("\n")
                
            if quarter_data.forward_looking_guidance or quarter_data.promises:
                f.write("## ð® Forward-Looking Statements\n\n")
                if quarter_data.forward_looking_guidance:
                    f.write("**Guidance:**\n")
                    for guidance in quarter_data.forward_looking_guidance: f.write(f"- {guidance}\n")
                    f.write("\n")
                    
                if quarter_data.promises:
                    f.write("**Commitments Made:**\n")
                    for promise in quarter_data.promises:
                        f.write(f"- [{promise.get('category', 'OTHER').upper()}] {promise.get('text', '')}\n")
                    f.write("\n")
                    
            if quarter_data.evidence_key_quotes:
                f.write("## ð£ï¸ Key Quotes\n\n")
                for speaker, quote in quarter_data.evidence_key_quotes:
                    f.write(f"> **{speaker}:** \"{quote}\"\n\n")
                    
            if prediction and not is_baseline:
                f.write("---\n\n")
                f.write("## ð® Prediction for Next Quarter\n")
                f.write(f"**Target:** {prediction.target_quarter}\n")
                f.write(f"**Overall Confidence:** {prediction.confidence*100:.0f}%\n\n")
                
                if 'revenue_growth' in prediction.predictions:
                    pred_data = prediction.predictions['revenue_growth']
                    if isinstance(pred_data, dict):
                        f.write(f"**Growth Prediction:** {pred_data.get('range', 'N/A')}\n")
                        f.write(f"- Basis: {pred_data.get('basis', 'N/A')}\n\n")
                    
                if 'tone' in prediction.predictions:
                    f.write(f"**Tone Prediction:** {prediction.predictions['tone'].upper()}\n\n")
                    
                if 'customers' in prediction.predictions:
                    f.write(f"**Expected Customer Mentions:** {', '.join(prediction.predictions['customers'])}\n\n")
                    
        logger.info(f"Quarter report generated: {report_path}")

    def generate_master_timeline(self, dna: CompanyDNA):
        """Generate master timeline report"""
        report_path = self.reports_dir / '00_MASTER_TIMELINE.md'
        
        with open(report_path, 'w', encoding='utf-8') as f:
            f.write(f"# ð§¬ {getattr(dna, 'ticker', 'UNKNOWN')} - Master Timeline\n\n")
            f.write(f"**DNA Version:** v{getattr(dna, 'version', 1)}\n")
            f.write(f"**Period:** {dna.baseline_quarter} -> {dna.latest_quarter}\n")
            f.write(f"**Total Quarters:** {len(dna.timeline)}\n\n")
            f.write("---\n\n")
            
            f.write("## Timeline table\n\n")
            f.write("| Quarter | Growth | Margin | Tone | Themes | Customers |\n")
            f.write("|---------|--------|--------|------|--------|-----------|\n")
            
            for q in dna.timeline:
                growth_str = f"{q.revenue_growth:.1f}%" if q.revenue_growth else "N/A"
                margin_str = f"{q.margin:.1f}%" if q.margin else "N/A"
                themes_str = f"{len(q.key_themes)}"
                customers_str = f"{len(q.customer_mentions)}"
                f.write(f"| {q.quarter} | {growth_str} | {margin_str} | {q.tone} | {themes_str} | {customers_str} |\n")
            f.write("\n")
            
            f.write("## Growth trajectory chart (text-based)\n\n")
            growth_values = [q.revenue_growth for q in dna.timeline if q.revenue_growth]
            if growth_values:
                max_growth = max(growth_values)
                for q in dna.timeline:
                    if q.revenue_growth:
                        bar_length = int((q.revenue_growth / max_growth) * 40) if max_growth > 0 else 0
                        bar = "â" * bar_length
                        f.write(f"{q.quarter:12s} | {bar} {q.revenue_growth:.1f}%\n")
            f.write("\n")
            
            if dna.patterns:
                f.write("## Learned Patterns Summary\n\n")
                for name, pattern in dna.patterns.items():
                    f.write(f"### {getattr(pattern, 'name', name)}\n")
                    f.write(f"- Rule: {getattr(pattern, 'rule', 'N/A')}\n")
                    f.write(f"- Confidence: {getattr(pattern, 'confidence', 0)*100:.0f}% ({getattr(pattern, 'accurate', 0)}/{getattr(pattern, 'observations', 0)})\n\n")
                    
        logger.info(f"Master timeline generated: {report_path}")

    def generate_prediction_tracker(self, dna: CompanyDNA):
        """Generate prediction accuracy tracker"""
        report_path = self.reports_dir / '02_PREDICTION_TRACKER.md'
        
        with open(report_path, 'w', encoding='utf-8') as f:
            f.write(f"# ð¯ {getattr(dna, 'ticker', 'UNKNOWN')} - Prediction Accuracy Tracker\n\n")
            
            if not dna.predictions:
                f.write("No predictions made yet.\n")
                return
                
            validated = [p for p in dna.predictions if getattr(p, 'validated', False)]
            
            if validated:
                total_accuracy = sum(getattr(p, 'accuracy', 0) for p in validated) / len(validated)
                f.write(f"**Overall Model Accuracy:** {total_accuracy*100:.0f}%\n")
                f.write(f"**Validated Predictions:** {len(validated)}/{len(dna.predictions)}\n\n")
                f.write("---\n\n")
                
                f.write("## Prediction History\n\n")
                f.write("| Made In | Target | Prediction | Actual | Accuracy |\n")
                f.write("|---------|--------|------------|--------|----------|\n")
                
                for pred in validated:
                    if 'revenue_growth' in pred.predictions:
                        pred_str = pred.predictions['revenue_growth'].get('range', 'N/A') if isinstance(pred.predictions['revenue_growth'], dict) else str(pred.predictions['revenue_growth'])
                        actual_str = f"{getattr(pred, 'actual_results', {}).get('revenue_growth', 'N/A')}"
                    else:
                        pred_str = "N/A"
                        actual_str = "N/A"
                        
                    accuracy = getattr(pred, 'accuracy', 0)
                    icon = "â" if accuracy >= 0.8 else "â ï¸" if accuracy >= 0.5 else "â"
                    f.write(f"| {pred.made_on_quarter} | {pred.target_quarter} | {pred_str} | {actual_str} | {icon} {accuracy*100:.0f}% |\n")
                    
        logger.info(f"Prediction tracker generated: {report_path}")

    def generate_investment_brief(self, dna: CompanyDNA):
        """Generate current investment recommendation"""
        report_path = self.reports_dir / '05_INVESTMENT_BRIEF.md'
        
        with open(report_path, 'w', encoding='utf-8') as f:
            f.write(f"# ð¼ {getattr(dna, 'ticker', 'UNKNOWN')} - Investment Brief\n\n")
            f.write(f"**As of:** {dna.latest_quarter}\n")
            f.write(f"**Last Updated:** {datetime.now().strftime('%Y-%m-%d')}\n\n")
            f.write("---\n\n")
            
            score = 0
            reasoning = []
            
            # Growth trajectory
            if 'growth_trajectory' in dna.patterns:
                traj = getattr(dna.patterns['growth_trajectory'], 'rule', '')
                if 'accelerating' in traj:
                    score += 3
                    reasoning.append("â Growth accelerating (most important factor)")
                elif 'steady' in traj:
                    score += 1
                    reasoning.append("â Growth steady")
                elif 'decelerating' in traj:
                    score -= 2
                    reasoning.append("ð´ Growth decelerating")
                    
            # Promise delivery
            if 'promise_delivery' in dna.patterns:
                pattern = dna.patterns['promise_delivery']
                conf = getattr(pattern, 'confidence', 0)
                if conf > 0.75:
                    score += 2
                    reasoning.append(f"â High management credibility ({conf*100:.0f}%)")
                elif conf < 0.50:
                    score -= 2
                    reasoning.append(f"ð´ Low management credibility ({conf*100:.0f}%)")
                    
            # Customer momentum
            active_customers = len([c for c, d in dna.customer_evolution.items() if len(d.get('mentions', [])) >= 3])
            if active_customers >= 3:
                score += 2
                reasoning.append(f"â Multiple sector leader relationships ({active_customers})")
                
            # Tone
            latest = dna.timeline[-1] if dna.timeline else None
            if latest:
                if latest.tone == 'defensive':
                    score -= 2
                    reasoning.append("ð´ Management tone defensive")
                elif latest.tone == 'positive':
                    score += 1
                    reasoning.append("â Positive management tone")
                    
            # Generate recommendation
            if score >= 4:
                verdict = "ð¢ **STRONG BUY**"
                summary = "Multiple positive factors align. High conviction opportunity."
            elif score >= 2:
                verdict = "ð¢ **BUY**"
                summary = "More positives than negatives. Good risk/reward."
            elif score >= 0:
                verdict = "ð¡ **HOLD**"
                summary = "Mixed signals. Watch closely before adding."
            else:
                verdict = "ð´ **AVOID/SELL**"
                summary = "Too many red flags. Risk outweighs potential."
                
            f.write(f"## {verdict}\n\n")
            f.write(f"**Score:** {score}\n\n")
            f.write(f"{summary}\n\n")
            
            f.write("### Reasoning:\n\n")
            for reason in reasoning:
                f.write(f"- {reason}\n")
            f.write("\n---\n\n")
            
            f.write("### Latest Quarter Summary\n\n")
            if latest:
                f.write(f"**Quarter:** {latest.quarter}\n")
                if latest.revenue_growth is not None:
                    f.write(f"**Growth:** {latest.revenue_growth:.1f}%\n")
                f.write(f"**Tone:** {latest.tone.upper()}\n")
                f.write(f"**Themes:** {', '.join(latest.key_themes[:5])}\n\n")
                
                if latest.wins:
                    f.write("**Key Wins:**\n")
                    for win in latest.wins[:3]: f.write(f"- {win}\n")
                    f.write("\n")
                    
                if latest.challenges:
                    f.write("**Challenges:**\n")
                    for challenge in latest.challenges[:3]: f.write(f"- {challenge}\n")
                    f.write("\n")
                    
        logger.info(f"Investment brief generated: {report_path}")

# ====== MAIN ORCHESTRATOR ======
class DNAEvolutionAnalyzer:
    """MAIN ORCHESTRATOR - runs the complete DNA evolution analysis"""
    def __init__(self, folder_path: str, ticker: str, output_dir: str = "analysis_output"):
        self.folder_path = folder_path
        self.ticker = ticker
        self.output_dir = output_dir
        
        logger.info("Initializing components...")
        self.scanner = FileScanner()
        self.extractor = TranscriptExtractor()
        self.builder = DNABuilder()
        self.learner = PatternLearner()
        self.db_manager = DatabaseManager(output_dir, ticker)
        self.report_gen = ReportGenerator(output_dir, ticker)
        self.prediction_engine = PredictionEngine()
        self.deviation_detector = DeviationDetector()
        self.audit_manager = AuditManager(output_dir, ticker)

    def run(self):
        logger.info(f"{'='*80}")
        logger.info(f"DNA EVOLUTION ANALYZER - Started for {self.ticker}")
        logger.info(f"{'='*80}")
        
        logger.info("[STEP 1] Scanning and sorting transcripts chronologically...")
        sorted_transcripts, file_stats = self.scanner.scan_and_sort(self.folder_path, self.ticker)
        
        # Log file system changes immediately to the audit trail
        self.audit_manager.audit_file_system(file_stats)
        
        if not sorted_transcripts:
            logger.error("No transcripts found!")
            return None
            
        logger.info("[STEP 2] Building baseline DNA from oldest transcript...")
        file_path, date, quarter = sorted_transcripts[0]
        first_data = self.extractor.extract(file_path, quarter, date)
        dna = self.builder.build_baseline(self.ticker, first_data)
        self.db_manager.save_dna(dna)
        
        logger.info("[STEP 3] Evolving DNA with each subsequent transcript...")
        for idx, (file_path, date, quarter) in enumerate(sorted_transcripts[1:], start=1):
            logger.info(f"--- Processing Quarter {idx+1}/{len(sorted_transcripts)}: {quarter} ---")
            quarter_data = self.extractor.extract(file_path, quarter, date)
            
            # Take snapshot for Audit Manager
            prev_state = {
                'pattern_count': len(dna.patterns),
                'fulfilled_count': len(dna.fulfilled_promises),
                'broken_count': len(dna.broken_promises)
            }
            
            if dna.predictions:
                self.prediction_engine.validate_prediction(dna.predictions[-1], quarter_data)
                
            dna = self.builder.evolve_dna(dna, quarter_data)
            dna = self.learner.learn_patterns(dna)
            deviations = self.deviation_detector.detect_deviations(dna, quarter_data)
            
            if deviations:
                logger.info(f"  -> Detected {len(deviations)} pattern deviations")
                
            prediction = self.prediction_engine.make_predictions(dna)
            if prediction:
                dna.predictions.append(prediction)
                logger.info(f"  -> Made prediction for {prediction.target_quarter}")
                
            self.db_manager.save_dna(dna)
            self.report_gen.generate_quarter_report(dna, idx, deviations, prediction)
            self.audit_manager.audit(quarter, prev_state, dna, deviations)
            
            # Log progress
            if quarter_data.revenue_growth:
                logger.info(f"Growth: {quarter_data.revenue_growth:.1f}%")
            logger.info(f"Tone: {quarter_data.tone.upper()}")
            
        logger.info("[STEP 4] Generating master reports...")
        self.report_gen.generate_master_timeline(dna)
        self.report_gen.generate_prediction_tracker(dna)
        self.report_gen.generate_investment_brief(dna)
        
        logger.info(f"{'='*80}")
        logger.info("[ANALYSIS COMPLETE]")
        logger.info(f"DNA Files: {self.output_dir}/{self.ticker}/dna/")
        logger.info(f"Reports: {self.output_dir}/{self.ticker}/reports/")
        logger.info(f"{'='*80}")
        
        self.db_manager.close()
        return dna

# ====== COMMAND LINE INTERFACE ======
def main():
    parser = argparse.ArgumentParser(
        description="DNA Evolution Transcript Analyzer - World Class Edition",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='''Examples:
  Analyze a folder of transcripts for ACME
  python dna_analyzer.py --folder /path/to/ACME/transcripts --ticker ACME'''
    )
    
    parser.add_argument('--folder', type=str, required=True, help='Folder containing transcript files (.txt or .md)')
    parser.add_argument('--ticker', type=str, required=True, help='Company ticker symbol')
    parser.add_argument('--out', type=str, default='analysis_output', dest='output_dir', help='Output directory (default: analysis_output)')
    
    args = parser.parse_args()
    
    if not os.path.exists(args.folder):
        logger.error(f"Folder not found: {args.folder}")
        return
        
    analyzer = DNAEvolutionAnalyzer(args.folder, args.ticker, args.output_dir)
    try:
        dna = analyzer.run()
        
        # Print final investment verdict
        if dna:
            logger.info("\n" + "="*80)
            logger.info("INVESTMENT VERDICT")
            logger.info("="*80)
            latest = dna.timeline[-1] if dna.timeline else None
            if latest:
                logger.info(f"Latest Quarter: {latest.quarter}")
                if latest.revenue_growth:
                    logger.info(f"Growth: {latest.revenue_growth:.1f}%")
                logger.info(f"Tone: {latest.tone.upper()}")
            if 'growth_trajectory' in dna.patterns:
                logger.info(f"Trajectory: {dna.patterns['growth_trajectory'].rule}")
            
            logger.info("\nSee 05_INVESTMENT_BRIEF.md for detailed recommendation")
            logger.info("="*80)
            
    except KeyboardInterrupt:
        logger.info("\n\nAnalysis interrupted by user")
    except Exception as e:
        logger.error(f"\n\nAnalysis failed: {e}", exc_info=True)

if __name__ == '__main__':
    main()
