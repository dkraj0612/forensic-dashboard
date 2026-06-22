#!/usr/bin/env python3
""" Earnings Call Transcript Analyzer for Multibagger Discovery """
""" A comprehensive system for analyzing earnings call transcripts from microcap and small-cap companies to identify early-stage multibagger opportunities, particularly those serving as suppliers or partners to large-cap sector leaders. 
Author: Investment Research System Date: 2024 """

import os
import re
import json
import hashlib
import sqlite3
from pathlib import Path
from datetime import datetime, timedelta
from typing import Dict, List, Tuple, Optional, Any, Set
from dataclasses import dataclass, asdict
from collections import defaultdict, Counter
import logging
import concurrent.futures
from concurrent.futures import ProcessPoolExecutor, as_completed
import argparse

# NLP and ML imports
try:
    import pandas as pd
    import numpy as np
    from textblob import TextBlob
    import spacy
    from sklearn.feature_extraction.text import TfidfVectorizer, CountVectorizer
    from sklearn.decomposition import LatentDirichletAllocation
    import matplotlib.pyplot as plt
    import seaborn as sns
    from wordcloud import WordCloud
except ImportError as e:
    print(f"Warning: Some optional dependencies not available: {e}")
    print("Install with: pip install pandas numpy textblob spacy scikit-learn matplotlib seaborn wordcloud")

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', handlers=[logging.FileHandler("transcript_analyzer.log"), logging.StreamHandler()])
logger = logging.getLogger(__name__)

# ========================================== CONFIGURATION AND DATA CLASSES ==========================================

@dataclass
class TranscriptMetadata:
    file_path: str
    company_dir: str
    ticker: str
    date: Optional[datetime]
    date_confidence: float
    fiscal_quarter: Optional[str]
    fiscal_year: Optional[int]
    file_hash: str
    content_length: int
    processed_date: datetime

@dataclass
class CompanyMetrics:
    """Key metrics extracted from a transcript"""
    ticker: str
    date: datetime
    revenue_growth_yoy: Optional[float]
    revenue_growth_qoq: Optional[float]
    gross_margin: Optional[float]
    operating_margin: Optional[float]
    customer_count: Optional[int]
    employee_count: Optional[int]
    cash_position: Optional[float]
    debt_level: Optional[float]

@dataclass
class ManagementConfidence:
    """Management confidence scoring"""
    ticker: str
    date: datetime
    overall_score: float
    certainty_score: float
    specificity_score: float
    enthusiasm_score: float
    problem_acknowledgment_score: float
    guidance_confidence_score: float

@dataclass
class CompanyRelationship:
    """Relationship with other companies (customers, partners)"""
    ticker: str
    date: datetime
    related_company: str
    relationship_type: str # customer, partner, supplier, competitor
    strength_score: float
    context: str

@dataclass
class CompanyScores:
    """Overall company scoring for multibagger potential"""
    ticker: str
    latest_date: datetime
    multibagger_score: float
    growth_score: float
    confidence_score: float
    relationship_score: float
    financial_health_score: float
    momentum_score: float
    red_flags: List[str]
    green_flags: List[str]

class Config:
    """Configuration for the analyzer"""
    def __init__(self, config_path: Optional[str] = None):
        self.repo_root = os.getcwd()
        self.db_path = "transcript_analysis.db"
        self.output_dir = "analysis_output"
        self.parallel_workers = 4
        self.min_transcript_length = 1000
        self.date_confidence_threshold = 0.6
        # Analysis parameters
        self.min_growth_rate = 15.0 # Minimum YoY growth for microcaps
        self.high_growth_threshold = 50.0 # High growth threshold
        self.min_confidence_score = 0.5
        self.max_problem_acknowledgment = 0.8
        self.concentration_risk_threshold = 0.5
        # Sector leaders (can be expanded)
        self.sector_leaders = { 'NVIDIA', 'NVDA', 'TSMC', 'ASML', 'AAPL', 'APPLE', 'MSFT', 'MICROSOFT', 'GOOGL', 'GOOGLE', 'AMZN', 'AMAZON', 'META', 'TESLA', 'TSLA', 'AMD', 'INTC', 'INTEL', 'QUALCOMM', 'QCOM', 'BROADCOM', 'AVGO' }
        if config_path and os.path.exists(config_path):
            self.load_config(config_path)

    def load_config(self, path: str):
        """Load configuration from JSON file"""
        try:
            with open(path, 'r') as f:
                config_data = json.load(f)
                for key, value in config_data.items():
                    if hasattr(self, key):
                        setattr(self, key, value)
            logger.info(f"Loaded configuration from {path}")
        except Exception as e:
            logger.error(f"Error loading config: {e}")

# ========================================== DATABASE MANAGER ==========================================

class DatabaseManager:
    """Manages SQLite database for storing analysis results"""
    def __init__(self, db_path: str):
        self.db_path = db_path
        self.conn = None
        self.init_database()

    def init_database(self):
        """Initialize database schema"""
        self.conn = sqlite3.connect(self.db_path, check_same_thread=False)
        cursor = self.conn.cursor()
        # Transcripts table
        cursor.execute(''' CREATE TABLE IF NOT EXISTS transcripts ( id INTEGER PRIMARY KEY AUTOINCREMENT, file_path TEXT UNIQUE, ticker TEXT, date TEXT, date_confidence REAL, fiscal_quarter TEXT, fiscal_year INTEGER, file_hash TEXT, content_length INTEGER, processed_date TEXT ) ''')
        # Metrics table
        cursor.execute(''' CREATE TABLE IF NOT EXISTS metrics ( id INTEGER PRIMARY KEY AUTOINCREMENT, ticker TEXT, date TEXT, revenue_growth_yoy REAL, revenue_growth_qoq REAL, gross_margin REAL, operating_margin REAL, customer_count INTEGER, employee_count INTEGER, cash_position REAL, debt_level REAL, UNIQUE(ticker, date) ) ''')
        # Confidence scores table
        cursor.execute(''' CREATE TABLE IF NOT EXISTS confidence_scores ( id INTEGER PRIMARY KEY AUTOINCREMENT, ticker TEXT, date TEXT, overall_score REAL, certainty_score REAL, specificity_score REAL, enthusiasm_score REAL, problem_acknowledgment_score REAL, guidance_confidence_score REAL, UNIQUE(ticker, date) ) ''')
        # Relationships table
        cursor.execute(''' CREATE TABLE IF NOT EXISTS relationships ( id INTEGER PRIMARY KEY AUTOINCREMENT, ticker TEXT, date TEXT, related_company TEXT, relationship_type TEXT, strength_score REAL, context TEXT ) ''')
        # Company scores table
        cursor.execute(''' CREATE TABLE IF NOT EXISTS company_scores ( id INTEGER PRIMARY KEY AUTOINCREMENT, ticker TEXT, latest_date TEXT, multibagger_score REAL, growth_score REAL, confidence_score REAL, relationship_score REAL, financial_health_score REAL, momentum_score REAL, red_flags TEXT, green_flags TEXT ) ''')
        # Analysis cache table
        cursor.execute(''' CREATE TABLE IF NOT EXISTS analysis_cache ( file_hash TEXT PRIMARY KEY, analysis_type TEXT, result TEXT, created_date TEXT ) ''')
        self.conn.commit()
        logger.info("Database initialized")

    def save_transcript(self, metadata: TranscriptMetadata):
        """Save transcript metadata"""
        cursor = self.conn.cursor()
        cursor.execute(''' INSERT OR REPLACE INTO transcripts (file_path, ticker, date, date_confidence, fiscal_quarter, fiscal_year, file_hash, content_length, processed_date) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) ''', (metadata.file_path, metadata.ticker, metadata.date.isoformat() if metadata.date else None, metadata.date_confidence, metadata.fiscal_quarter, metadata.fiscal_year, metadata.file_hash, metadata.content_length, metadata.processed_date.isoformat()))
        self.conn.commit()

    def save_metrics(self, metrics: CompanyMetrics):
        """Save company metrics"""
        cursor = self.conn.cursor()
        cursor.execute(''' INSERT OR REPLACE INTO metrics (ticker, date, revenue_growth_yoy, revenue_growth_qoq, gross_margin, operating_margin, customer_count, employee_count, cash_position, debt_level) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) ''', (metrics.ticker, metrics.date.isoformat(), metrics.revenue_growth_yoy, metrics.revenue_growth_qoq, metrics.gross_margin, metrics.operating_margin, metrics.customer_count, metrics.employee_count, metrics.cash_position, metrics.debt_level))
        self.conn.commit()

    def save_confidence_score(self, confidence: ManagementConfidence):
        """Save management confidence scores"""
        cursor = self.conn.cursor()
        cursor.execute(''' INSERT OR REPLACE INTO confidence_scores (ticker, date, overall_score, certainty_score, specificity_score, enthusiasm_score, problem_acknowledgment_score, guidance_confidence_score) VALUES (?, ?, ?, ?, ?, ?, ?, ?) ''', (confidence.ticker, confidence.date.isoformat(), confidence.overall_score, confidence.certainty_score, confidence.specificity_score, confidence.enthusiasm_score, confidence.problem_acknowledgment_score, confidence.guidance_confidence_score))
        self.conn.commit()

    def save_relationship(self, relationship: CompanyRelationship):
        """Save relationship context"""
        cursor = self.conn.cursor()
        cursor.execute(''' INSERT INTO relationships (ticker, date, related_company, relationship_type, strength_score, context) VALUES (?, ?, ?, ?, ?, ?) ''', (relationship.ticker, relationship.date.isoformat(), relationship.related_company, relationship.relationship_type, relationship.strength_score, relationship.context))
        self.conn.commit()

    def save_company_scores(self, scores: CompanyScores):
        """Save overall company scores"""
        cursor = self.conn.cursor()
        cursor.execute(''' INSERT OR REPLACE INTO company_scores (ticker, latest_date, multibagger_score, growth_score, confidence_score, relationship_score, financial_health_score, momentum_score, red_flags, green_flags) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) ''', (scores.ticker, scores.latest_date.isoformat(), scores.multibagger_score, scores.growth_score, scores.confidence_score, scores.relationship_score, scores.financial_health_score, scores.momentum_score, json.dumps(scores.red_flags), json.dumps(scores.green_flags)))
        self.conn.commit()

    def get_processed_files(self) -> Set[str]:
        """Get set of already processed file hashes"""
        cursor = self.conn.cursor()
        cursor.execute('SELECT file_hash FROM transcripts')
        return {row[0] for row in cursor.fetchall()}

    def get_company_data(self, ticker: str) -> Dict[str, Any]:
        """Get all data for a company"""
        cursor = self.conn.cursor()
        # Get transcripts
        cursor.execute(''' SELECT date, fiscal_quarter, fiscal_year FROM transcripts WHERE ticker = ? ORDER BY date ''', (ticker,))
        transcripts = cursor.fetchall()
        # Get metrics
        cursor.execute(''' SELECT date, revenue_growth_yoy, revenue_growth_qoq, gross_margin, operating_margin FROM metrics WHERE ticker = ? ORDER BY date ''', (ticker,))
        metrics = cursor.fetchall()
        # Get confidence scores
        cursor.execute(''' SELECT date, overall_score, certainty_score, specificity_score FROM confidence_scores WHERE ticker = ? ORDER BY date ''', (ticker,))
        confidence = cursor.fetchall()
        # Get relationships
        cursor.execute(''' SELECT date, related_company, relationship_type, strength_score FROM relationships WHERE ticker = ? ORDER BY date DESC ''', (ticker,))
        relationships = cursor.fetchall()
        return {'transcripts': transcripts, 'metrics': metrics, 'confidence': confidence, 'relationships': relationships}

    def get_all_tickers(self) -> List[str]:
        """Get list of all analyzed tickers"""
        cursor = self.conn.cursor()
        cursor.execute('SELECT DISTINCT ticker FROM transcripts ORDER BY ticker')
        return [row[0] for row in cursor.fetchall()]

    def close(self):
        """Close database connection"""
        if self.conn:
            self.conn.close()

# ========================================== FILE DISCOVERY AND PARSING ==========================================

class RepositoryScanner:
    """Scans GitHub repository structure for transcript files"""
    def __init__(self, config: Config):
        self.config = config
        self.transcript_extensions = ['.txt', '.md', '.json']

    def scan(self) -> Dict[str, List[str]]:
        """Scan repository and organize files by company"""
        logger.info(f"Scanning repository: {self.config.repo_root}")
        companies = defaultdict(list)
        total_files = 0
        for root, dirs, files in os.walk(self.config.repo_root):
            # Skip hidden and common non-data directories
            dirs[:] = [d for d in dirs if not d.startswith('.') and d not in ('node_modules', 'venv', '__pycache__')]
            for file in files:
                if any(file.endswith(ext) for ext in self.transcript_extensions):
                    file_path = os.path.join(root, file)
                    # Determine company from directory structure
                    rel_path = os.path.relpath(root, self.config.repo_root)
                    company = self._extract_company_identifier(rel_path, file)
                    if company:
                        companies[company].append(file_path)
                        total_files += 1
        logger.info(f"Found {total_files} transcript files across {len(companies)} companies")
        return dict(companies)

    def _extract_company_identifier(self, rel_path: str, filename: str) -> Optional[str]:
        """Extract company identifier from directory or filename"""
        # Try to get ticker from directory name (first segment)
        parts = rel_path.split(os.sep)
        for part in parts:
            if 1 <= len(part) <= 5 and part.isupper():
                # Looks like a ticker (1-5 uppercase letters)
                if re.match(r'^[A-Z]{1,5}$', part):
                    return part
        # Check if it's a recognizable company name pattern
        if '-' in part and not part in ('.', '..'):
            return part.split('-')[0].upper()
        # Fallback to filename-based extraction
        ticker_match = re.search(r'^([A-Z]{1,5})[_\-]', filename)
        if ticker_match:
            return ticker_match.group(1)
        return None

    def read_file_content(self, file_path: str) -> str:
        """Read and return file content"""
        try:
            with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                content = f.read()
            return content
        except Exception as e:
            logger.error(f"Error reading {file_path}: {e}")
            return ""

    def compute_file_hash(self, file_path: str) -> str:
        """Compute MD5 hash of file"""
        return hashlib.md5(content.encode('utf-8')).hexdigest()

# ========================================== DATE EXTRACTION ==========================================

class DateExtractor:
    """Extracts dates from transcript content"""
    def __init__(self):
        # Comprehensive list of date patterns
        self.date_patterns = [
            r'(?i)(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2}(?:st|nd|rd|th)?,\s+\d{4}', # Month DD, YYYY
            r'(?i)\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\.?\s+\d{1,2}(?:st|nd|rd|th)?,\s+\d{4}', # Mon DD, YYYY
            r'\b\d{1,2}[\/\-]\d{1,2}[\/\-]\d{4}\b', # DD/MM/YYYY or MM/DD/YYYY
            r'\b\d{4}[\/\-]\d{1,2}[\/\-]\d{1,2}\b', # YYYY-MM-DD
        ]
        self.months = {'january': 1, 'february': 2, 'march': 3, 'april': 4, 'may': 5, 'june': 6, 'july': 7, 'august': 8, 'september': 9, 'october': 10, 'november': 11, 'december': 12, 'jan': 1, 'feb': 2, 'mar': 3, 'apr': 4, 'jun': 6, 'jul': 7, 'aug': 8, 'sep': 9, 'oct': 10, 'nov': 11, 'dec': 12}
        self.fiscal_quarters = {'q1': 'Q1', 'first quarter': 'Q1', 'q2': 'Q2', 'second quarter': 'Q2', 'q3': 'Q3', 'third quarter': 'Q3', 'q4': 'Q4', 'fourth quarter': 'Q4'}

    def extract_date(self, content: str, file_modified_date: Optional[datetime] = None) -> Tuple[Optional[datetime], float, Optional[str], Optional[int]]:
        """ Extract date from transcript content Returns: (date, confidence, fiscal_quarter, fiscal_year) """
        if not content:
            return None, 0.0, None, None

        search_content = content[:min(len(content), 1000)] # Search only first 1000 chars
        dates_found = []
        for pattern in self.date_patterns:
            for match in re.finditer(pattern, search_content):
                date_str = match.group(0)
                try:
                    date_obj = self._parse_date_match(match, date_str)
                    if date_obj:
                        # Check if date is reasonable (not in future, not too old)
                        if datetime(2010, 1, 1) <= date_obj <= datetime.now():
                            dates_found.append({'date': date_obj, 'position': match.start(), 'date_str': date_str})
                except Exception as e:
                    logger.debug(f"Date parsing error: {e}")
                    continue

        if not dates_found:
            # Fallback to file modified date if available
            if file_modified_date:
                return file_modified_date, 0.5, None, None
            return None, 0.0, None, None

        # Score dates by position (earlier is better) and frequency
        date_scores = {}
        for d in dates_found:
            date_obj = d['date']
            if date_obj not in date_scores:
                date_scores[date_obj] = {'count': 0, 'min_pos': float('inf')}
            date_scores[date_obj]['count'] += 1
            date_scores[date_obj]['min_pos'] = min(date_scores[date_obj]['min_pos'], d['position'])

        best_date = None
        best_score = -1.0
        for date_obj, stats in date_scores.items():
            # Calculate combined score
            frequency_score = min(stats['count'] / 3.0, 1.0) # Cap at 3 mentions
            position_score = max(0.0, 1.0 - (stats['min_pos'] / 1000.0))
            score = (frequency_score * 0.3) + (position_score * 0.7)
            if score > best_score:
                best_score = score
                best_date = date_obj

        # Extract fiscal quarter and year
        fiscal_quarter, fiscal_year = self._extract_fiscal_period(content)
        confidence = best_score
        return best_date, confidence, fiscal_quarter, fiscal_year

    def _parse_date_match(self, match: re.Match, date_str: str) -> Optional[datetime]:
        """Parse regex match into datetime object"""
        try:
            if date_str.count('/') == 2 or date_str.count('-') == 2:
                parts = date_str.replace('-', '/').split('/')
                if len(parts[0]) == 4:
                    # YYYY/MM/DD
                    return datetime(int(parts[0]), int(parts[1]), int(parts[2]))
                else:
                    # Assume MM/DD/YYYY or DD/MM/YYYY based on values
                    p1, p2, p3 = int(parts[0]), int(parts[1]), int(parts[2])
                    if p1 > 12:
                        return datetime(p3, p2, p1)
                    else:
                        return datetime(p3, p1, p2) # Default to MM/DD
            else:
                # Text date format
                for month_str, month_num in self.months.items():
                    if month_str in date_str.lower():
                        year = int(re.search(r'\d{4}', date_str).group())
                        day = int(re.search(r'\d{1,2}(?!\d{2})', date_str).group())
                        return datetime(year, month_num, day)
        except Exception as e:
            logger.debug(f"Parse error for {date_str}: {e}")
            return None

    def _extract_fiscal_period(self, content: str) -> Tuple[Optional[str], Optional[int]]:
        """Extract fiscal quarter and year from content"""
        search_content = content[:5000].lower()
        # Quarter
        quarter = None
        for q_str, q_val in self.fiscal_quarters.items():
            if q_str in search_content:
                quarter = q_val
                break
        # Year
        year = None
        year_match = re.search(r'(?:fiscal|fy)\s*(?:year)?\s*(\d{2,4})', search_content)
        if year_match:
            y_str = year_match.group(1)
            year = int(y_str) if len(y_str) == 4 else int(f"20{y_str}")
        return quarter, year

# ========================================== SENTIMENT ANALYSIS MODULE ==========================================

class SentimentAnalyzer:
    """Analyzes sentiment and tone in transcripts"""
    def __init__(self):
        # Financial sentiment lexicons
        self.positive_words = {'strong', 'growth', 'increase', 'improved', 'positive', 'excellent', 'outstanding', 'robust', 'accelerating', 'momentum', 'confident', 'optimistic', 'expansion', 'opportunity', 'success', 'winning', 'exceeded', 'beating', 'outperforming', 'solid', 'healthy'}
        self.negative_words = {'decline', 'decrease', 'weak', 'challenging', 'difficult', 'headwind', 'pressure', 'concern', 'issue', 'problem', 'disappointing', 'below', 'miss', 'underperforming', 'slowdown', 'deteriorating', 'uncertain', 'risk', 'threat', 'competitive', 'losing'}
