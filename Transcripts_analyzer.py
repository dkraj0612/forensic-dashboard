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

    def compute_file_hash(self, content: str) -> str:
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

# ========================================== NLP ANALYSIS MODULES ==========================================

class SentimentAnalyzer:
    """Analyzes sentiment and tone in transcripts"""
    def __init__(self):
        # Financial sentiment lexicons
        self.positive_words = {'strong', 'growth', 'increase', 'improved', 'positive', 'excellent', 'outstanding', 'robust', 'accelerating', 'momentum', 'confident', 'optimistic', 'expansion', 'opportunity', 'success', 'winning', 'exceeded', 'beating', 'outperforming', 'solid', 'healthy'}
        self.negative_words = {'decline', 'decrease', 'weak', 'challenging', 'difficult', 'headwind', 'pressure', 'concern', 'issue', 'problem', 'disappointing', 'below', 'miss', 'underperforming', 'slowdown', 'deteriorating', 'uncertain', 'risk', 'threat', 'competitive', 'losing'}
        self.uncertainty_words = {'may', 'might', 'could', 'possibly', 'potentially', 'perhaps', 'uncertain', 'unclear', 'depends', 'subject to', 'assuming', 'if', 'hope', 'try', 'attempt'}
        self.certainty_words = {'will', 'expect', 'confident', 'committed', 'definitely', 'certain', 'clearly', 'absolutely', 'guaranteed', 'assured', 'determined'}

    def analyze_sentiment(self, text: str) -> Dict[str, float]:
        """Analyze overall sentiment of text"""
        text_lower = text.lower()
        words = re.findall(r'\b\w+\b', text_lower)
        if not words:
            return {'polarity': 0.0, 'subjectivity': 0.0, 'positive_ratio': 0.0, 'negative_ratio': 0.0}
            
        # Count sentiment words
        positive_count = sum(1 for word in words if word in self.positive_words)
        negative_count = sum(1 for word in words if word in self.negative_words)
        total_sentiment_words = positive_count + negative_count
        
        # Calculate ratios
        positive_ratio = positive_count / len(words) if words else 0
        negative_ratio = negative_count / len(words) if words else 0
        
        # Calculate polarity (-1 to 1)
        if total_sentiment_words > 0:
            polarity = (positive_count - negative_count) / total_sentiment_words
        else:
            polarity = 0.0
            
        # Use TextBlob for additional sentiment analysis
        try:
            blob = TextBlob(text[:5000]) # Analyze first 5000 chars for performance
            textblob_sentiment = blob.sentiment.polarity
            textblob_subjectivity = blob.sentiment.subjectivity
            # Combine custom and TextBlob sentiment
            combined_polarity = (polarity + textblob_sentiment) / 2
            return {'polarity': combined_polarity, 'subjectivity': textblob_subjectivity, 'positive_ratio': positive_ratio, 'negative_ratio': negative_ratio}
        except:
            return {'polarity': polarity, 'subjectivity': 0.5, 'positive_ratio': positive_ratio, 'negative_ratio': negative_ratio}

class ConfidenceAnalyzer:
    """Analyzes management confidence from linguistic patterns"""
    def __init__(self):
        self.sentiment_analyzer = SentimentAnalyzer()
        # Specificity indicators
        self.specific_patterns = [
            r'\b\d+(?:\.\d+)?(?:%| percent)\b', # Percentages
            r'\$\d+(?:\.\d+)?[mbk]?\b', # Dollar amounts
            r'\b\d+ (?:customers|clients|users)\b', # Specific counts
            r'\b[qQ][1-4](?: |\/|-)\d{2,4}\b', # Specific quarters
            r'\b(?:by|in)\s+(?:january|february|march|april|may|june|july|august|september|october|november|december)\b', # Specific months
            r'\b(?:guidance|projection)\s+(?:is|of)\s+\d+\b' # Numeric guidance
        ]

    def analyze_comprehensive_confidence(self, text: str) -> ManagementConfidence:
        """Comprehensive confidence analysis"""
        # Overall sentiment
        sentiment = self.sentiment_analyzer.analyze_sentiment(text)
        
        # Certainty analysis
        certainty_score = self._analyze_certainty(text)
        
        # Specificity score
        specificity_score = self._calculate_specificity(text)
        
        # Enthusiasm score (based on positive sentiment and assertiveness)
        enthusiasm_score = max(0, min(1, (sentiment['positive_ratio'] * 5) + (certainty_score * 0.5)))
        
        # Problem acknowledgment (balanced is good)
        problem_score = self._analyze_problem_acknowledgment(text)
        
        # Guidance confidence
        guidance_score = self._analyze_guidance_confidence(text)
        
        # Overall confidence score (weighted combination)
        overall_score = (certainty_score * 0.25) + (specificity_score * 0.25) + (enthusiasm_score * 0.15) + (problem_score * 0.15) + (guidance_score * 0.20)
        
        return ManagementConfidence(
            ticker="", # Will be filled by caller
            date=datetime.now(), # Will be filled by caller
            overall_score=overall_score,
            certainty_score=certainty_score,
            specificity_score=specificity_score,
            enthusiasm_score=enthusiasm_score,
            problem_acknowledgment_score=problem_score,
            guidance_confidence_score=guidance_score
        )

    def _analyze_certainty(self, text: str) -> float:
        """Analyze certainty level in language"""
        text_lower = text.lower()
        words = re.findall(r'\b\w+\b', text_lower)
        if not words: return 0.5
        certainty_count = sum(1 for word in words if word in self.sentiment_analyzer.certainty_words)
        uncertainty_count = sum(1 for word in words if word in self.sentiment_analyzer.uncertainty_words)
        total = certainty_count + uncertainty_count
        if total == 0: return 0.5
        certainty_score = certainty_count / total
        return certainty_score

    def _calculate_specificity(self, text: str) -> float:
        """Calculate how specific the language is"""
        specific_count = 0
        for pattern in self.specific_patterns:
            specific_count += len(re.findall(pattern, text, re.IGNORECASE))
        
        # Normalize by text length (per 1000 words)
        words = len(re.findall(r'\b\w+\b', text))
        if words < 100: return 0.5
        
        specificity_density = (specific_count / words) * 1000
        # Score between 0 and 1 (more than 50 specific items per 1000 words is very high score)
        score = min(1.0, specificity_density / 50)
        return score

    def _analyze_problem_acknowledgment(self, text: str) -> float:
        """Analyze how management addresses challenges"""
        problem_words = {'challenge', 'issue', 'problem', 'difficulty', 'headwind', 'obstacle'}
        solution_words = {'address', 'solve', 'mitigate', 'overcome', 'improve', 'fix', 'plan'}
        
        text_lower = text.lower()
        problem_count = sum(text_lower.count(word) for word in problem_words)
        solution_count = sum(text_lower.count(word) for word in solution_words)
        
        if problem_count == 0:
            # No problems mentioned might indicate avoidance
            return 0.6
        
        # Good ratio is about 1:1 to 1:2 (problems to solutions)
        if solution_count == 0:
            return 0.3 # Problems mentioned but no solutions
        
        ratio = solution_count / problem_count
        # Optimal score (1.0) around 1-2 solutions per problem
        if 1.0 <= ratio <= 2.0: return 1.0
        elif 0.5 <= ratio < 1.0: return 0.7
        elif ratio > 2.0: return 0.8 # Might be over-explaining
        else: return 0.4

    def _analyze_guidance_confidence(self, text: str) -> float:
        """Analyze confidence in forward guidance"""
        guidance_patterns = [
            r'(?i)expect\s+(?:to|that)',
            r'(?i)anticipate',
            r'(?i)forecast',
            r'(?i)project',
            r'(?i)guide',
            r'(?i)guidance',
            r'(?i)outlook'
        ]
        
        guidance_count = sum(len(re.findall(pattern, text)) for pattern in guidance_patterns)
        if guidance_count == 0: return 0.5 # No guidance provided
        
        # Check for hedging in guidance
        hedging_patterns = [
            r'(?i)subject to',
            r'(?i)assuming',
            r'(?i)depending on',
            r'(?i)if',
            r'(?i)hope'
        ]
        
        hedging_count = sum(len(re.findall(pattern, text)) for pattern in hedging_patterns)
        # More guidance with less hedging is higher confidence
        if guidance_count == 0: return 0.5
        hedging_ratio = hedging_count / guidance_count
        
        score = max(0.0, min(1.0, 1 - (hedging_ratio / 2)))
        return score

class EntityExtractor:
    """Extracts named entities and relationships from text"""
    def __init__(self):
        try:
            self.nlp = spacy.load("en_core_web_sm")
        except:
            logger.warning("spaCy model not found. Install with: python -m spacy download en_core_web_sm")
            self.nlp = None
        
        # Common company name patterns
        self.company_patterns = [
            r'\b([A-Z][a-zA-Z\s]+?)(?:Inc\.|Corp\.|Corporation|Ltd\.|Limited|LLC|LP)\b',
            r'\b([A-Z]{2,5})\b' # Tickers and abbreviations
        ]
        
        # Relationship indicators
        self.customer_indicators = {'customer', 'client', 'buyer', 'purchaser', 'consumer'}
        self.partner_indicators = {'partner', 'collaboration', 'joint venture', 'alliance', 'relationship'}
        self.supplier_indicators = {'supplier', 'vendor', 'provider', 'source'}
        self.competitor_indicators = {'competitor', 'rival', 'competing against'}

    def extract_company_entities_and_relationships(self, text: str, sector_leaders: Set[str]) -> List[CompanyRelationship]:
        """Extract company entities and relationships"""
        relationships = []
        
        # Use spaCy if available
        if self.nlp:
            doc = self.nlp(text[:100000]) # Limit for performance
            entities = [ent.text for ent in doc.ents if ent.label_ == 'ORG']
        else:
            # Fallback pattern-based extraction
            entities = []
            for pattern in self.company_patterns:
                entities.extend(re.findall(pattern, text))
        
        # Analyze context for each entity found
        for entity in set(entities):
            # Check if it's a sector leader
            company_name = entity.strip()
            is_sector_leader = any(leader in company_name.upper() for leader in sector_leaders)
            
            if is_sector_leader:
                # Find mentions in text and extract context window
                for match in re.finditer(re.escape(company_name), text, re.IGNORECASE):
                    start = max(0, match.start() - 200)
                    end = min(len(text), match.end() + 200)
                    context = text[start:end]
                    
                    # Classify relationship type and strength from context
                    rel_type, strength = self._classify_relationship(context)
                    
                    relationships.append(CompanyRelationship(
                        ticker="", # Will be filled by caller
                        date=datetime.now(), # Will be filled by caller
                        related_company=company_name,
                        relationship_type=rel_type,
                        strength_score=strength,
                        context=context.strip()
                    ))
        return relationships

    def _classify_relationship(self, context: str) -> Tuple[str, float]:
        """Classify relationship type and strength from context"""
        context_lower = context.lower()
        
        # Score each relationship type based on indicators
        customer_score = sum(1 for ind in self.customer_indicators if ind in context_lower)
        partner_score = sum(1 for ind in self.partner_indicators if ind in context_lower)
        supplier_score = sum(1 for ind in self.supplier_indicators if ind in context_lower)
        competitor_score = sum(1 for ind in self.competitor_indicators if ind in context_lower)
        
        scores = {
            'customer': customer_score,
            'partner': partner_score,
            'supplier': supplier_score,
            'competitor': competitor_score
        }
        
        max_score_type = max(scores.items(), key=lambda x: x[1])
        
        if max_score_type[1] == 0:
            # Mentioned without clear relationship indicator
            return 'mention', 0.3
        
        rel_type = max_score_type[0]
        base_strength = min(1.0, 0.5 + (max_score_type[1] * 0.1))
        
        # Look for growth/expansion language
        if any(word in context_lower for word in ['grow', 'expand', 'increase', 'ramp']):
            base_strength = min(1.0, base_strength + 0.2)
        
        # Look for specific product/project mentions
        if re.search(r'\b(?:project|product|platform|design win)\b', context_lower):
            base_strength = min(1.0, base_strength + 0.2)
        
        return rel_type, base_strength

class MetricsExtractor:
    """Extracts financial and operational metrics from text"""
    def __init__(self):
        # Metric patterns
        self.growth_patterns = [
            r'(?:revenue|sales)\s+(?:grew|increased|up)\s+(?:by\s+)?(\d+(?:\.\d+)?)\s*%',
            r'(?:y/y|yoy|year-over-year)\s+(?:growth|increase)\s+(?:of\s+)?(\d+(?:\.\d+)?)\s*%',
            r'(?:growth|increase)\s+(?:of\s+)?(\d+(?:\.\d+)?)\s*%\s+(?:year-over-year|y/y|yoy)'
        ]
        self.margin_patterns = [
            r'(?:gross margin)\s+(?:was|is|at|of)\s+(\d+(?:\.\d+)?)\s*%',
            r'margin\s+(?:expanded|improved)\s+(?:to\s+)?(\d+(?:\.\d+)?)\s*%'
        ]
        self.customer_patterns = [
            r'(\d+)\s+(?:customers|clients|users)',
            r'(?:customer base)\s+(?:of\s+)?(\d+)'
        ]
        self.cash_patterns = [
            r'(?:cash and cash equivalents|cash position)\s+(?:was|of|at)\s+\$?(\d+(?:\.\d+)?)\s*(m|b|million|billion)'
        ]

    def extract_metrics(self, text: str) -> CompanyMetrics:
        """Extract all available metrics from text"""
        revenue_growth = self._extract_max_value(text, self.growth_patterns)
        gross_margin = self._extract_max_value(text, self.margin_patterns)
        customer_count = self._extract_customer_count(text)
        cash_position = self._extract_cash(text)
        
        # Calculate YoY and QoQ if possible (simplified logic)
        revenue_growth_yoy = revenue_growth
        revenue_growth_qoq = revenue_growth / 4 if revenue_growth else None # Heuristic fallback
        
        return CompanyMetrics(
            ticker="", # Will be filled by caller
            date=datetime.now(), # Will be filled by caller
            revenue_growth_yoy=revenue_growth_yoy,
            revenue_growth_qoq=revenue_growth_qoq,
            gross_margin=gross_margin,
            operating_margin=gross_margin * 0.5 if gross_margin else None, # Heuristic
            customer_count=customer_count,
            employee_count=None,
            cash_position=cash_position,
            debt_level=None
        )

    def _extract_max_value(self, text: str, patterns: List[str]) -> Optional[float]:
        """Extract max value from a set of regex patterns"""
        values = []
        for pattern in patterns:
            for match in re.finditer(pattern, text, re.IGNORECASE):
                try:
                    val = float(match.group(1))
                    if val < 1000: # Sanity check for percentages
                        values.append(val)
                except:
                    pass
        return max(values) if values else None

    def _extract_customer_count(self, text: str) -> Optional[int]:
        """Extract customer count from text"""
        values = []
        for pattern in self.customer_patterns:
            for match in re.finditer(pattern, text, re.IGNORECASE):
                try:
                    val_str = match.group(1).replace(',', '')
                    val = int(val_str)
                    if val < 1000000000: # Sanity check
                        values.append(val)
                except:
                    pass
        return max(values) if values else None

    def _extract_cash(self, text: str) -> Optional[float]:
        """Extract cash position from text"""
        for pattern in self.cash_patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                try:
                    amount = float(match.group(1))
                    multiplier = match.group(2).lower()
                    if multiplier in ['m', 'million']:
                        return amount * 1_000_000
                    elif multiplier in ['b', 'billion']:
                        return amount * 1_000_000_000
                except:
                    pass
        return None

# ========================================== SCORING AND RANKING MODULE ==========================================

class Scorer:
    """Calculates overall multibagger score for a company"""
    def __init__(self, config: Config, db: DatabaseManager):
        self.config = config
        self.db = db
        
    def score_company(self, ticker: str) -> Optional[CompanyScores]:
        """Calculate comprehensive scores for a company"""
        # Get all company data
        data = self.db.get_company_data(ticker)
        if not data['transcripts']:
            return None
            
        # Calculate component scores
        growth_score = self._calculate_growth_score(data['metrics'])
        confidence_score = self._calculate_confidence_score(data['confidence'])
        relationship_score = self._calculate_relationship_score(data['relationships'])
        financial_health_score = self._calculate_financial_health_score(data['metrics'])
        momentum_score = self._calculate_momentum_score(data['confidence'], data['metrics'])
        
        # Identify red and green flags
        red_flags = self._identify_red_flags(data)
        green_flags = self._identify_green_flags(data)
        
        # Calculate overall score (weighted combination)
        multibagger_score = (
            growth_score * 0.30 +
            confidence_score * 0.20 +
            relationship_score * 0.20 +
            financial_health_score * 0.15 +
            momentum_score * 0.15
        )
        
        # Get latest date
        latest_date = datetime.fromisoformat(data['transcripts'][-1][0]) if data['transcripts'] else datetime.now()
        
        return CompanyScores(
            ticker=ticker,
            latest_date=latest_date,
            multibagger_score=multibagger_score,
            growth_score=growth_score,
            confidence_score=confidence_score,
            relationship_score=relationship_score,
            financial_health_score=financial_health_score,
            momentum_score=momentum_score,
            red_flags=red_flags,
            green_flags=green_flags
        )

    def _calculate_growth_score(self, metrics: List[Tuple]) -> float:
        """Score based on revenue growth"""
        if not metrics: return 0.0
        recent_metrics = metrics[-4:] # Look at up to 4 quarters
        
        growth_rates = [m[2] for m in recent_metrics if m[2] is not None] # revenue_growth_yoy
        if not growth_rates: return 0.0
        
        avg_growth = sum(growth_rates) / len(growth_rates)
        
        # Score based on thresholds
        if avg_growth < self.config.min_growth_rate: return 0.0
        elif avg_growth >= self.config.high_growth_threshold: return 1.0
        else:
            # Linear interpolation between min and high threshold
            range_size = self.config.high_growth_threshold - self.config.min_growth_rate
            score = (avg_growth - self.config.min_growth_rate) / range_size
            
            # Bonus for accelerating growth
            if len(growth_rates) >= 2 and growth_rates[-1] > growth_rates[-2]:
                score += 0.1
            
            return min(1.0, max(0.0, score))

    def _calculate_confidence_score(self, confidence: List[Tuple]) -> float:
        """Score based on management confidence trends"""
        if not confidence: return 0.5
        recent_scores = confidence[-2:] # Look at last 2 transcripts
        
        avg_confidence = sum(c[1] for c in recent_scores) / len(recent_scores) # overall_score
        
        # Check for trend
        if len(confidence) >= 2 and confidence[-1][1] > confidence[-2][1]:
            avg_confidence += 0.1 # Bonus for improving confidence
            
        return min(1.0, max(0.0, avg_confidence))

    def _calculate_relationship_score(self, relationships: List[Tuple]) -> float:
        """Score based on strategic partnerships and customers"""
        if not relationships: return 0.0
        
        score = 0.0
        # Give points for high quality customer/partner relationships
        for rel in relationships:
            rel_type = rel[3]
            strength = rel[4]
            
            if rel_type == 'customer':
                score += 0.2 * strength
            elif rel_type == 'partner':
                score += 0.15 * strength
            elif rel_type == 'supplier':
                score += 0.1 * strength
                
        # Bonus if multiple strong relationships exist
        if len(relationships) >= 3:
            score += 0.2
            
        return min(1.0, score)

    def _calculate_financial_health_score(self, metrics: List[Tuple]) -> float:
        """Score based on margins and cash"""
        if not metrics: return 0.5
        
        score = 0.5 # Neutral start
        recent_metric = metrics[-1]
        
        gross_margin = recent_metric[4]
        operating_margin = recent_metric[5]
        
        if gross_margin and gross_margin > 50:
            score += 0.2
        if operating_margin and operating_margin > 10:
            score += 0.2
        elif operating_margin and operating_margin < 0:
            score -= 0.2
            
        return min(1.0, max(0.0, score))

    def _calculate_momentum_score(self, confidence: List[Tuple], metrics: List[Tuple]) -> float:
        """Score based on improving metrics across multiple dimensions"""
        score = 0.5
        
        # Check metric momentum
        if len(metrics) >= 2:
            if metrics[-1][2] and metrics[-2][2] and metrics[-1][2] > metrics[-2][2]: # Growth accelerating
                score += 0.15
            if metrics[-1][4] and metrics[-2][4] and metrics[-1][4] > metrics[-2][4]: # Gross margin expanding
                score += 0.15
                
        # Check confidence momentum
        if len(confidence) >= 2 and confidence[-1][1] > confidence[-2][1]:
            score += 0.2
            
        return min(1.0, max(0.0, score))

    def _identify_red_flags(self, data: Dict) -> List[str]:
        """Identify potential warning signs"""
        flags = []
        metrics = data['metrics']
        confidence = data['confidence']
        
        if not metrics:
            flags.append("Insufficient financial metrics found")
            return flags
            
        # Check growth
        recent_growth = [m[2] for m in metrics[-2:] if m[2] is not None]
        if recent_growth and recent_growth[-1] < 10.0:
            flags.append("Low recent growth rate (<10%)")
        if len(recent_growth) >= 2 and recent_growth[-1] < recent_growth[-2]:
            flags.append("Decelerating growth trend")
            
        # Check confidence
        if confidence and confidence[-1][1] < 0.4:
            flags.append("Low management confidence score")
        if len(confidence) >= 2 and confidence[-1][1] < confidence[-2][1]:
            flags.append("Declining management confidence")
            
        return flags

    def _identify_green_flags(self, data: Dict) -> List[str]:
        """Identify positive signals"""
        flags = []
        metrics = data['metrics']
        relationships = data['relationships']
        
        # Check relationships
        customers = [r for r in relationships if r[3] == 'customer' and r[4] > 0.6]
        if customers:
            flags.append("Strong relationships with sector leaders")
            
        # Check margins
        if metrics and metrics[-1][4] and metrics[-1][4] > 60.0:
            flags.append("High gross margin profile (>60%)")
            
        # Check growth acceleration
        recent_growth = [m[2] for m in metrics[-3:] if m[2] is not None]
        if len(recent_growth) >= 2 and recent_growth[-1] > recent_growth[-2]:
            flags.append("Accelerating growth trajectory")
            
        return flags
