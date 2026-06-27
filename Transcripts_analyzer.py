#!/usr/bin/env python3
"""
DNA Evolution Transcript Analyzer - Fixed Edition
Sequential learning system that builds evolving intelligence from earnings transcripts.
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
    ticker: str
    file_path: str
    date: datetime
    quarter: str = ""
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
    ticker: str = ""
    version: int = 1
    baseline_quarter: str = ""
    latest_quarter: str = ""
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
    # FIX #9: renamed for clarity; updated after each prediction validation
    prediction_accuracy: float = 0.0
    metadata_created_date: datetime = field(default_factory=datetime.now)
    last_updated: Optional[datetime] = None

# ====== FILE SCANNER ======
class FileScanner:
    """Scans folders and sorts transcripts chronologically"""
    def __init__(self):
        self.transcript_extensions = {'.txt', '.md', '.csv'}

    def scan_and_sort(self, folder_path: str, ticker: str) -> Tuple[List[Tuple[str, datetime, str]], Dict[str, List]]:
        logger.info(f"Scanning folder: {folder_path}")
        transcripts = []
        file_stats = {'renames': [], 'duplicates': []}

        for root, dirs, files in os.walk(folder_path):
            dirs[:] = [d for d in dirs if not d.startswith('.')]
            for file in files:
                if any(file.endswith(ext) for ext in self.transcript_extensions):
                    file_path = os.path.join(root, file)
                    date, quarter = self._extract_date_from_file(file_path)

                    if not date or not quarter:
                        # FIX #7: warn explicitly instead of silently skipping
                        logger.warning(f"SKIPPED (no date/quarter found): {file}")
                        continue

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
                    file_stats['duplicates'].append(
                        os.path.basename(quarter_map[quarter][0]) + f" (older date for {quarter})"
                    )
                quarter_map[quarter] = (file_path, date, quarter)

        unique_transcripts = list(quarter_map.values())
        # Sort ascending by date so oldest is first — baseline is always the earliest quarter
        unique_transcripts.sort(key=lambda x: x[1])

        logger.info(f"Total unique transcripts: {len(unique_transcripts)} "
                    f"(oldest: {unique_transcripts[0][2]}, newest: {unique_transcripts[-1][2]})")
        return unique_transcripts, file_stats

    def _extract_date_from_file(self, file_path: str) -> Tuple[Optional[datetime], Optional[str]]:
        """Extract date and quarter from file content.
        FIX #7: reads the full file (not just first 5000 chars) so dates on later pages are found.
        """
        date = None
        quarter = None

        try:
            with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                # FIX #7: read full file; date often appears after the cover page
                content = f.read()

            # 1. Explicit quarter mentions
            q_match = re.search(
                r'\b(Q[1-4]|Quarter\s+[1-4])\s*(?:FY\s*|[-\s])?\s*(?:20)?(\d{2})\b',
                content, re.IGNORECASE
            )
            if q_match:
                q_raw = q_match.group(1).upper()
                q_num = re.sub(r'QUARTER\s*', 'Q', q_raw)
                year_suffix = q_match.group(2)
                quarter = f"{q_num} 20{year_suffix}"

            # 2. Date patterns
            patterns = [
                (r'(January|February|March|April|May|June|July|August|September|October|November|December)'
                 r'\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4})', 'mdy'),
                (r'(\d{1,2})\s+(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+(\d{4})', 'dmy_alpha'),
                (r'(\d{4})-(\d{2})-(\d{2})', 'ymd'),
                (r'(\d{1,2})[-/](\d{1,2})[-/](\d{4})', 'dmy_slash'),
            ]

            months = {
                'jan': 1, 'feb': 2, 'mar': 3, 'apr': 4, 'may': 5, 'jun': 6,
                'jul': 7, 'aug': 8, 'sep': 9, 'oct': 10, 'nov': 11, 'dec': 12,
                'january': 1, 'february': 2, 'march': 3, 'april': 4, 'june': 6,
                'july': 7, 'august': 8, 'september': 9, 'october': 10,
                'november': 11, 'december': 12
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
                    except Exception:
                        continue

        except Exception as e:
            logger.error(f"Error reading {file_path}: {e}")

        if date and not quarter:
            quarter = f"Q{(date.month - 1) // 3 + 1} {date.year}"

        if quarter and not date:
            try:
                q_num = int(quarter[1])
                y_num = int(quarter.split()[1])
                month_map = {1: 3, 2: 6, 3: 9, 4: 12}
                date = datetime(y_num, month_map[q_num], 28)
            except Exception:
                pass

        return date, quarter


# ====== TRANSCRIPT EXTRACTOR ======
class TranscriptExtractor:
    """
    Sentence-level extractor with:
    - Disqualifier filtering  (won't grab inflation/target/sector numbers)
    - Unit enforcement        (% must be present for growth/margin)
    - Proximity constraints   (number must sit within the same sentence as keyword)
    - Sanity bounds           (impossible values auto-rejected)
    - Source sentence stored  (every value is auditable)
    - Financial-domain tone   (no generic sentiment library)
    """

    # ── DISQUALIFIERS ────────────────────────────────────────────────────────
    # If any of these appear in the same sentence as a candidate number,
    # that sentence is rejected before the number is even read.

    # For revenue growth — words that mean the number is NOT company revenue growth
    _GROWTH_DISQUALIFIERS = {
        # macro / external context
        'inflation', 'cpi', 'wpi', 'repo rate', 'interest rate',
        'gdp', 'industry grew', 'sector grew', 'market grew',
        'industry growth', 'sector growth', 'market growth',
        'industry is growing', 'sector is growing',
        'overall market', 'addressable market', 'tam',
        # forward-looking / not actual
        'guidance', 'guided', 'target', 'targeting', 'aim', 'aiming',
        'expect', 'expected', 'anticipate', 'forecast', 'project',
        'aspire', 'aspiration', 'goal', 'objective',
        # composition / mix — not total growth
        'contribution', 'contributed', 'from new', 'from existing',
        'mix', 'constitutes', 'accounts for', 'represents',
        'of which', 'out of which', 'of our revenue',
        # competitor / peer
        'competitor', 'peers', 'peer group', 'industry peer',
        # basis points — not percentage
        'basis point', 'bps', 'bp improvement', 'bp expansion',
        # subsidiary / segment caveat
        'subsidiary', 'division revenue', 'segment revenue',
    }

    # For margin — words that mean the number is NOT the margin level
    _MARGIN_DISQUALIFIERS = {
        # movement not level
        'expanded by', 'contracted by', 'improved by', 'declined by',
        'increased by', 'decreased by', 'compressed by', 'widened by',
        'basis point', 'bps',
        # forward-looking
        'guidance', 'target', 'expect', 'aim', 'goal', 'aspire',
        # not our margin
        'industry margin', 'sector margin', 'competitor margin',
        # absolute numbers not ratios
        'crore', 'lakh', 'million', 'billion',
    }

    # ── SECTION WEIGHTS ───────────────────────────────────────────────────────
    # Management remarks carry more weight than analyst Q&A
    _QA_MARKERS = re.compile(
        r'(?:question|analyst|moderator|operator|participant|q&a|'
        r'thank you.*question|can you (?:please )?elaborate|'
        r'my question is|i have a question)',
        re.IGNORECASE
    )

    # ── HELPERS ───────────────────────────────────────────────────────────────

    def _split_sentences(self, text: str) -> List[str]:
        """
        Split on sentence-ending punctuation.
        Avoids splitting on decimals (18.4%) or abbreviations (Rs.).
        """
        # Normalise line breaks first
        text = re.sub(r'\r\n|\r', '\n', text)
        text = re.sub(r'\n{2,}', ' ', text)
        # Split: period/!/?  followed by whitespace or end, but NOT if
        # preceded by a digit (decimal guard) or single capital (abbreviation guard)
        sentences = re.split(r'(?<![0-9])(?<![A-Z])(?<=[.!?])\s+', text)
        return [s.strip() for s in sentences if len(s.strip()) > 15]

    def _split_management_qa(self, text: str) -> Tuple[str, str]:
        """
        Return (management_block, qa_block).
        Everything before the first Q&A marker is management remarks.
        """
        match = self._QA_MARKERS.search(text)
        if match:
            cut = match.start()
            return text[:cut], text[cut:]
        return text, ""

    def _has_disqualifier(self, sentence: str, disqualifiers: set) -> bool:
        s = sentence.lower()
        return any(d in s for d in disqualifiers)

    def _extract_pct_numbers(self, sentence: str) -> List[float]:
        """
        Extract numbers that are IMMEDIATELY followed by % or 'per cent' / 'percent'.
        Rejects numbers followed by 'bps' or 'basis points'.
        """
        # Pattern: digits, optional decimal, then optional whitespace, then %/percent
        matches = re.findall(
            r'(\d{1,3}(?:\.\d{1,2})?)\s*(?:%|per\s*cent\b)',
            sentence, re.IGNORECASE
        )
        results = []
        for m in matches:
            try:
                results.append(float(m))
            except Exception:
                pass
        return results

    def _is_yoy(self, sentence: str) -> bool:
        s = sentence.lower()
        return any(kw in s for kw in [
            'year-on-year', 'year on year', 'yoy', 'y-o-y',
            'year over year', 'compared to last year',
            'compared to previous year', 'versus last year',
            'versus same quarter last year', 'same period last year',
        ])

    def _has_subject(self, sentence: str, subjects: List[str]) -> bool:
        s = sentence.lower()
        return any(sub in s for sub in subjects)

    def _best_candidate(
        self,
        sentences: List[str],
        subjects: List[str],
        growth_verbs: List[str],
        disqualifiers: set,
        bounds: Tuple[float, float],
    ) -> Tuple[Optional[float], Optional[str], str]:
        """
        Core extraction engine.

        Returns (value, source_sentence, confidence).
        confidence: 'high' | 'medium' | 'low'

        Priority order:
          1. YoY-confirmed sentence with subject + verb
          2. Non-YoY sentence with subject + verb
          3. Subject-only sentence (no explicit growth verb)
        """
        tier1, tier2, tier3 = [], [], []

        for sentence in sentences:
            s_lower = sentence.lower()

            if self._has_disqualifier(sentence, disqualifiers):
                continue

            has_subject = self._has_subject(sentence, subjects)
            if not has_subject:
                continue

            has_verb = any(v in s_lower for v in growth_verbs)
            numbers  = self._extract_pct_numbers(sentence)
            valid    = [n for n in numbers if bounds[0] <= n <= bounds[1]]

            if not valid:
                continue

            # Prefer the number closest to the first growth verb in the sentence
            best_num = valid[0]

            if has_verb and self._is_yoy(sentence):
                tier1.append((best_num, sentence))
            elif has_verb:
                tier2.append((best_num, sentence))
            else:
                tier3.append((best_num, sentence))

        if tier1:
            v, s = tier1[0]
            return v, s, 'high'
        if tier2:
            v, s = tier2[0]
            return v, s, 'medium'
        if tier3:
            v, s = tier3[0]
            return v, s, 'low'
        return None, None, 'none'

    # ── PUBLIC EXTRACT ─────────────────────────────────────────────────────────

    def extract(self, file_path: str, ticker: str, quarter: str, date: datetime) -> QuarterData:
        try:
            with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                content = f.read()
        except Exception as e:
            logger.error(f"Cannot read {file_path}: {e}")
            return QuarterData(ticker=ticker, file_path=file_path, quarter=quarter, date=date)

        mgmt_block, qa_block = self._split_management_qa(content)
        mgmt_sentences = self._split_sentences(mgmt_block)
        all_sentences  = self._split_sentences(content)

        data = QuarterData(ticker=ticker, file_path=file_path, quarter=quarter, date=date)

        # Numeric metrics — prefer management block, fall back to full text
        rev_val, rev_src, rev_conf = self._extract_revenue_growth(mgmt_sentences)
        if rev_val is None:
            rev_val, rev_src, rev_conf = self._extract_revenue_growth(all_sentences)
        data.revenue_growth = rev_val

        mgn_val, mgn_src, mgn_type, mgn_conf = self._extract_margin(mgmt_sentences)
        if mgn_val is None:
            mgn_val, mgn_src, mgn_type, mgn_conf = self._extract_margin(all_sentences)
        data.margin = mgn_val

        data.customer_count = self._extract_customer_count(all_sentences)

        # Qualitative
        data.key_themes             = self._extract_themes(content)
        data.wins                   = self._extract_wins(all_sentences)
        data.challenges             = self._extract_challenges(all_sentences)
        data.product_updates        = self._extract_product_updates(all_sentences)
        data.forward_looking_guidance = self._extract_guidance(all_sentences)
        data.promises               = self._extract_promises(all_sentences, quarter)
        data.tone                   = self._assess_tone(content)
        data.specificity            = self._assess_specificity(content)
        data.evidence_key_quotes    = self._extract_quotes(content)

        # Log what was found and why
        self._log_extraction(ticker, quarter, rev_val, rev_src, rev_conf,
                             mgn_val, mgn_src, mgn_type, mgn_conf)
        return data

    # ── NUMERIC EXTRACTORS ─────────────────────────────────────────────────────

    def _extract_revenue_growth(
        self, sentences: List[str]
    ) -> Tuple[Optional[float], Optional[str], str]:
        """
        Rules:
        - Subject must contain revenue/sales/turnover (company-level)
        - Growth verb must be in the same sentence
        - % unit must be present immediately after the number
        - Disqualifiers (inflation, target, sector…) reject the sentence
        - Bounds: -50% to 300% (outside this = data error or not growth %)
        - YoY sentences ranked higher
        """
        subjects = [
            'revenue', 'revenues', 'net revenue', 'total revenue',
            'sales', 'net sales', 'total sales',
            'turnover', 'net turnover', 'total turnover',
            'top line', 'topline',
        ]
        verbs = [
            'grew', 'grow', 'grown', 'growth',
            'increased', 'increase', 'rose', 'risen',
            'up by', 'higher by', 'jumped', 'surged',
            'recorded a growth', 'clocked a growth',
            'registered a growth', 'posted a growth',
        ]
        return self._best_candidate(
            sentences, subjects, verbs,
            self._GROWTH_DISQUALIFIERS,
            bounds=(-50.0, 300.0),
        )

    def _extract_margin(
        self, sentences: List[str]
    ) -> Tuple[Optional[float], Optional[str], str, str]:
        """
        Extract margin level (not movement).
        Tries specific margin types in priority order:
        EBITDA → Gross → Operating → PAT/Net → generic margin
        Returns (value, source_sentence, margin_type, confidence)
        """
        margin_types = [
            ('EBITDA',     ['ebitda margin', 'ebitda margins']),
            ('Gross',      ['gross margin', 'gross margins']),
            ('Operating',  ['operating margin', 'operating margins', 'ebit margin']),
            ('PAT',        ['pat margin', 'net profit margin', 'net margin']),
            ('Generic',    ['margin', 'margins']),
        ]
        verbs = [
            'at', 'was', 'were', 'stood at', 'came in at',
            'came at', 'is', 'are', 'reported', 'recorded',
        ]

        for margin_type, subjects in margin_types:
            val, src, conf = self._best_candidate(
                sentences, subjects, verbs,
                self._MARGIN_DISQUALIFIERS,
                bounds=(0.0, 100.0),
            )
            if val is not None:
                return val, src, margin_type, conf

        return None, None, 'Unknown', 'none'

    def _extract_customer_count(self, sentences: List[str]) -> Optional[int]:
        """
        Extract customer count — must be an absolute number (not a %).
        Bounds: 1 to 100,000 (avoids scrip codes, dates, phone numbers).
        """
        subjects = [
            'customer', 'customers', 'client', 'clients',
            'active customer', 'paying customer',
        ]
        verbs = [
            'added', 'have', 'now have', 'total', 'count',
            'crossed', 'surpassed', 'reached', 'stand at',
        ]
        disqualifiers = {
            '%', 'percent', 'crore', 'lakh', 'million', 'billion',
            'revenue from', 'satisfaction', 'retention', 'churn',
        }

        for sentence in sentences:
            s_lower = sentence.lower()
            if not self._has_subject(sentence, subjects):
                continue
            if not any(v in s_lower for v in verbs):
                continue
            if self._has_disqualifier(sentence, disqualifiers):
                continue
            # Look for plain integers (not followed by %)
            nums = re.findall(r'\b(\d{1,6})\b', sentence)
            for n_str in nums:
                try:
                    n = int(n_str)
                    if 1 <= n <= 100_000:
                        return n
                except Exception:
                    continue
        return None

    # ── QUALITATIVE EXTRACTORS ────────────────────────────────────────────────

    def _extract_themes(self, text: str) -> List[str]:
        """Count keyword density per theme; require ≥ 3 hits to qualify."""
        theme_patterns = {
            'AI/ML':                  r'\b(?:AI|artificial intelligence|machine learning|generative AI|GenAI)\b',
            'International Expansion':r'\b(?:international|overseas|global expansion|new geography|new market)\b',
            'New Products':           r'\b(?:new product|product launch|new offering|unveiled|shipping now)\b',
            'Customer Acquisition':   r'\b(?:new customer|customer addition|customer acquisition|won.*client)\b',
            'Market Share':           r'\b(?:market share|gained share|share gain|competitive win)\b',
            'Profitability':          r'\b(?:profitab|margin expansion|operating leverage|cost efficiency)\b',
            'R&D':                    r'\b(?:research and development|R&D|innovation|patent|engineering)\b',
            'Partnerships':           r'\b(?:partnership|strategic alliance|collaboration|joint venture|MOU)\b',
            'M&A':                    r'\b(?:acqui(?:red|sition|re)|merger|inorganic)\b',
            'Cost Control':           r'\b(?:cost optim|cost reduction|cost control|streamlin|rationaliz)\b',
            'Export/PLI':             r'\b(?:PLI|production.linked|export|forex|foreign exchange)\b',
            'Capex/Expansion':        r'\b(?:capex|capital expenditure|greenfield|brownfield|new plant|new facility)\b',
        }
        themes = []
        for theme, pattern in theme_patterns.items():
            if len(re.findall(pattern, text, re.IGNORECASE)) >= 3:
                themes.append(theme)
        return themes[:8]

    def _extract_wins(self, sentences: List[str]) -> List[str]:
        """
        Wins must reference concrete business outcomes, not vague superlatives.
        """
        win_signals = re.compile(
            r'\b(?:record|highest ever|best ever|all.time high|'
            r'exceeded|surpassed|outperformed|beat.*estimate|'
            r'new.*(?:client|customer|contract|order)|'
            r'won.*(?:order|contract|deal|bid)|'
            r'secured.*(?:order|contract|deal)|'
            r'strong.*order book|order inflow)\b',
            re.IGNORECASE
        )
        wins = []
        for s in sentences:
            if win_signals.search(s) and len(s) > 30:
                wins.append(s.strip())
        return wins[:5]

    def _extract_challenges(self, sentences: List[str]) -> List[str]:
        """
        Challenges must reference business-specific pressure, not generic macro.
        """
        challenge_signals = re.compile(
            r'\b(?:headwind|supply.chain|raw material.*(?:cost|pressure)|'
            r'margin.*pressure|pricing pressure|demand.*slowdown|'
            r'order deferral|delayed.*order|capacity.*constraint|'
            r'labour.*shortage|attrition|customer.*churn|'
            r'competition.*intense|pricing.*competitive)\b',
            re.IGNORECASE
        )
        challenges = []
        for s in sentences:
            if challenge_signals.search(s) and len(s) > 30:
                challenges.append(s.strip())
        return challenges[:5]

    def _extract_product_updates(self, sentences: List[str]) -> List[str]:
        """Product/service launches with a named product or explicit launch verb."""
        launch_signals = re.compile(
            r'\b(?:launched|introduced|unveiled|released|started shipping|'
            r'went live|deployed|rolled out)\b',
            re.IGNORECASE
        )
        updates = []
        for s in sentences:
            if launch_signals.search(s) and len(s) > 30:
                updates.append(s.strip())
        return updates[:5]

    def _extract_guidance(self, sentences: List[str]) -> List[str]:
        """
        Forward guidance must contain a metric word (revenue/margin/growth/profit)
        AND a forward-looking verb. Rejects sentences about past performance.
        """
        fwd_verbs = re.compile(
            r'\b(?:expect|anticipate|forecast|guide|guidance|'
            r'project|target|aim|plan to|intend to|aspire)\b',
            re.IGNORECASE
        )
        metric_words = {'revenue', 'sales', 'growth', 'margin', 'profit',
                        'ebitda', 'pat', 'order', 'capex', 'volume'}
        guidance = []
        for s in sentences:
            if fwd_verbs.search(s) and any(m in s.lower() for m in metric_words):
                guidance.append(s.strip())
        return guidance[:7]

    def _extract_promises(self, sentences: List[str], quarter: str) -> List[Dict[str, str]]:
        """
        Promises must be time-bound or explicitly committed.
        A sentence is a promise only if it contains a commitment verb
        AND a timeframe OR a hard number target.
        """
        commit_verbs = re.compile(
            r'\b(?:committed to|committing to|will achieve|will deliver|'
            r'plan to|intend to|targeting|aiming to|goal of|'
            r'by end of|by Q[1-4]|by FY|in the next|over the next)\b',
            re.IGNORECASE
        )
        metric_words = {'revenue', 'margin', 'growth', 'ebitda', 'profit',
                        'order', 'customer', 'capacity', 'plant', 'headcount'}
        promises = []
        for s in sentences:
            if commit_verbs.search(s) and any(m in s.lower() for m in metric_words):
                promises.append({
                    'text':     s.strip(),
                    'made_in':  quarter,
                    'category': self._categorize_promise(s),
                })
        return promises[:5]

    def _categorize_promise(self, text: str) -> str:
        t = text.lower()
        if any(w in t for w in ['product', 'launch', 'release', 'ship']):      return 'product'
        if any(w in t for w in ['margin', 'profitab', 'ebitda', 'pat']):        return 'financial'
        if any(w in t for w in ['customer', 'client', 'order', 'contract']):    return 'customer'
        if any(w in t for w in ['hire', 'headcount', 'team', 'employee']):      return 'hiring'
        if any(w in t for w in ['plant', 'facility', 'capex', 'capacity']):     return 'infrastructure'
        return 'other'

    def _assess_tone(self, text: str) -> str:
        """
        Financial-domain tone detection — no generic sentiment library.

        Uses word lists specific to Indian concall language.
        Confidence buckets are based on RATIO of signals to total words
        so long transcripts don't auto-win.
        """
        t = text.lower()
        word_count = max(len(t.split()), 1)

        positive_signals = [
            'strong performance', 'robust growth', 'record revenue', 'all-time high',
            'beat our guidance', 'ahead of guidance', 'exceeded expectations',
            'strong order book', 'healthy pipeline', 'momentum continues',
            'market share gain', 'margin expansion', 'confident about',
            'exciting opportunity', 'positive outlook', 'well-positioned',
        ]
        cautious_signals = [
            'macro uncertainty', 'geopolitical', 'inflationary pressure',
            'demand moderation', 'cautiously optimistic', 'wait and watch',
            'visibility is limited', 'near-term headwind', 'challenging environment',
            'pricing pressure', 'competitive intensity', 'input cost',
            'monsoon uncertainty', 'rbi policy', 'interest rate headwind',
        ]
        defensive_signals = [
            'significantly below', 'missed our target', 'declined significantly',
            'severe pressure', 'deteriorating', 'loss of market share',
            'restructuring', 'impairment', 'write-off', 'provisions increased',
            'deeply concerned', 'unexpected headwind', 'guidance revision downward',
            'order cancellations', 'customer losses',
        ]

        pos_count  = sum(1 for s in positive_signals  if s in t)
        caut_count = sum(1 for s in cautious_signals  if s in t)
        def_count  = sum(1 for s in defensive_signals if s in t)

        # Normalise per 1000 words so transcript length doesn't bias
        norm = 1000 / word_count
        pos_score  = pos_count  * norm
        caut_score = caut_count * norm
        def_score  = def_count  * norm

        if def_score >= 0.5:                              return 'defensive'
        if caut_score > pos_score and caut_score >= 0.3: return 'cautious'
        if pos_score > caut_score and pos_score >= 0.3:  return 'positive'
        return 'neutral'

    def _assess_specificity(self, text: str) -> str:
        """How data-rich is the transcript?"""
        specific_patterns = [
            r'\d+(?:\.\d+)?\s*%',          # percentages
            r'(?:Rs\.?|INR|₹)\s*\d+',      # rupee amounts
            r'\d+\s*(?:crore|lakh|cr\b)',   # Indian currency units
            r'\bQ[1-4]\s*(?:FY)?\s*\d{2,4}\b',  # quarter references
            r'\d+\s+(?:customers|clients|employees|engineers|units)',
        ]
        count = sum(len(re.findall(p, text, re.IGNORECASE)) for p in specific_patterns)
        ratio = (count / max(len(text.split()), 1)) * 1000
        if ratio > 25: return 'high'
        if ratio > 10: return 'medium'
        return 'low'

    def _extract_quotes(self, text: str) -> List[Tuple[str, str]]:
        """
        Extract verbatim quotes attributed to named executives.
        Speaker name must appear before the colon/dash.
        """
        pattern = re.compile(
            r'((?:MD|CEO|CFO|CMD|Managing Director|Chief Executive|'
            r'Chief Financial|Chairman|Founder|Director)[^:\n]{0,40})'
            r'(?::|--|-)\s*([A-Z][^.!?]{40,300}[.!?])',
            re.IGNORECASE
        )
        quotes = []
        for speaker, quote in pattern.findall(text):
            quotes.append((speaker.strip(), quote.strip()))
        return quotes[:5]

    # ── LOGGING ───────────────────────────────────────────────────────────────

    def _log_extraction(self, ticker, quarter,
                        rev_val, rev_src, rev_conf,
                        mgn_val, mgn_src, mgn_type, mgn_conf):
        prefix = f"[{ticker} {quarter}]"
        if rev_val is not None:
            logger.info(f"{prefix} Revenue growth = {rev_val:.1f}%  "
                        f"(confidence: {rev_conf})  "
                        f"src: \"{(rev_src or '')[:80]}...\"")
        else:
            logger.warning(f"{prefix} Revenue growth NOT FOUND — will store None")

        if mgn_val is not None:
            logger.info(f"{prefix} {mgn_type} margin = {mgn_val:.1f}%  "
                        f"(confidence: {mgn_conf})  "
                        f"src: \"{(mgn_src or '')[:80]}...\"")
        else:
            logger.warning(f"{prefix} Margin NOT FOUND — will store None")


# ====== DNA BUILDER ======
class DNABuilder:
    """Builds and evolves Company DNA"""

    def build_baseline(self, ticker: str, first_quarter_data: QuarterData) -> CompanyDNA:
        logger.info(f"Building baseline DNA for {ticker} from {first_quarter_data.quarter}")
        dna = CompanyDNA(
            ticker=ticker,
            baseline_quarter=first_quarter_data.quarter,
            latest_quarter=first_quarter_data.quarter,
            timeline=[first_quarter_data],
        )
        for customer, context in first_quarter_data.customer_mentions.items():
            dna.customer_evolution[customer] = {
                'first_mentioned': first_quarter_data.quarter,
                'mentions': [first_quarter_data.quarter],
                'evolution': ['prospect'],
                'contexts': [context],
            }
        return dna

    def evolve_dna(self, dna: CompanyDNA, new_quarter_data: QuarterData) -> CompanyDNA:
        dna.latest_quarter = new_quarter_data.quarter
        dna.version += 1  # FIX: increment version each evolution
        dna.timeline.append(new_quarter_data)
        self._update_customer_tracking(dna, new_quarter_data)
        self._update_theme_tracking(dna, new_quarter_data)
        self._check_promises(dna, new_quarter_data)
        for promise in new_quarter_data.promises:
            dna.open_promises.append({
                **promise,
                'promised_in': new_quarter_data.quarter,
                'due_quarter': self._estimate_due_quarter(new_quarter_data.quarter, promise.get('text', '')),
            })
        dna.last_updated = datetime.now()
        return dna

    def _update_customer_tracking(self, dna: CompanyDNA, new_quarter_data: QuarterData):
        mentioned_this_quarter = set(new_quarter_data.customer_mentions.keys())
        for customer in list(dna.customer_evolution.keys()):
            if customer in mentioned_this_quarter:
                dna.customer_evolution[customer]['mentions'].append(new_quarter_data.quarter)
                context = new_quarter_data.customer_mentions[customer].lower()
                if 'production' in context or 'shipping' in context:     stage = 'production'
                elif 'design win' in context or 'select' in context:     stage = 'design_win'
                elif 'expand' in context or 'ramp' in context:           stage = 'expansion'
                else:                                                      stage = 'active'
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
                    'contexts': [new_quarter_data.customer_mentions[customer]],
                }

    def _update_theme_tracking(self, dna: CompanyDNA, new_quarter_data: QuarterData):
        mentioned_this_quarter = set(new_quarter_data.key_themes)
        for theme in list(dna.theme_lifecycle.keys()):
            if theme in mentioned_this_quarter:
                dna.theme_lifecycle[theme]['mentions'].append(new_quarter_data.quarter)
                dna.theme_lifecycle[theme]['status'] = 'active'
            else:
                if (len(dna.theme_lifecycle[theme]['mentions']) > 3
                        and new_quarter_data.quarter not in dna.theme_lifecycle[theme]['mentions']):
                    dna.theme_lifecycle[theme]['status'] = 'fading'

        for theme in mentioned_this_quarter:
            if theme not in dna.theme_lifecycle:
                dna.theme_lifecycle[theme] = {
                    'first_appeared': new_quarter_data.quarter,
                    'mentions': [new_quarter_data.quarter],
                    'status': 'new',
                }

    def _check_promises(self, dna: CompanyDNA, new_data: QuarterData):
        current_text = ' '.join([
            str(new_data.revenue_growth or ''),
            str(new_data.margin or ''),
            ' '.join(new_data.wins),
            ' '.join(getattr(new_data, 'product_updates', [])),
        ]).lower()

        still_open = []
        for promise in dna.open_promises:
            promise_keywords = self._extract_keywords(promise.get('text', ''))
            delivered = False
            evidence = ""

            if promise_keywords and any(kw in current_text for kw in promise_keywords):
                delivered = True
                evidence = f"Keywords found in {new_data.quarter} data"

            if promise.get('category') == 'financial' and new_data.margin is not None:
                if 'margin' in promise.get('text', '').lower():
                    target = self._extract_number(promise.get('text', ''))
                    if target is not None and new_data.margin >= target * 0.95:
                        delivered = True
                        evidence = f"Margin {new_data.margin}% vs target {target}%"

            if delivered:
                dna.fulfilled_promises.append({**promise, 'fulfilled_in': new_data.quarter, 'evidence': evidence})
            else:
                quarters_since = self._quarters_between(promise.get('promised_in', ''), new_data.quarter)
                if quarters_since > 4:
                    dna.broken_promises.append({**promise, 'broken_in': new_data.quarter,
                                                'quarters_overdue': quarters_since})
                else:
                    still_open.append(promise)

        dna.open_promises = still_open

    def _extract_keywords(self, text: str) -> List[str]:
        """
        FIX #4: use word-boundary regex so words have no surrounding spaces,
        then compare against stopwords correctly.
        """
        common = {'will', 'plan', 'to', 'the', 'a', 'an', 'by', 'in', 'on',
                  'of', 'and', 'or', 'we', 'our', 'is', 'are', 'for', 'that', 'this'}
        words = re.findall(r'\b\w+\b', text.lower())
        return [w for w in words if w not in common and len(w) > 3][:5]

    def _extract_number(self, text: str) -> Optional[float]:
        match = re.search(r'(\d+(?:\.\d+)?)', text)
        if match:
            try:
                return float(match.group(1))
            except Exception:
                return None
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
            q1_num  = int(q1_parts[0][1]);  q1_year = int(q1_parts[1])
            q2_num  = int(q2_parts[0][1]);  q2_year = int(q2_parts[1])
            return (q2_year - q1_year) * 4 + (q2_num - q1_num)
        except Exception:
            return 0


# ====== PATTERN LEARNER ======
class PatternLearner:
    """Learns patterns from DNA timeline"""

    def learn_patterns(self, dna: CompanyDNA) -> CompanyDNA:
        if len(dna.timeline) < 2:
            return dna
        self._learn_tone_patterns(dna)
        self._learn_promise_patterns(dna)
        self._learn_customer_patterns(dna)
        self._learn_growth_patterns(dna)
        self._learn_seasonal_patterns(dna)
        return dna

    def _learn_tone_patterns(self, dna: CompanyDNA):
        tone_transitions: Dict[str, Dict] = defaultdict(lambda: {'next_growth': [], 'next_tone': []})
        for i in range(len(dna.timeline) - 1):
            current = dna.timeline[i]
            next_q  = dna.timeline[i + 1]
            tone_transitions[current.tone]['next_growth'].append(next_q.revenue_growth)
            tone_transitions[current.tone]['next_tone'].append(next_q.tone)

        for tone, outcomes in tone_transitions.items():
            # FIX #3: use 'is not None' so 0% growth is counted
            valid_growths = [g for g in outcomes['next_growth'] if g is not None]
            if valid_growths:
                avg_growth = sum(valid_growths) / len(valid_growths)
                pattern = Pattern(
                    pattern_id=f"tone_{tone}_predicts_growth",
                    type="tone_growth",
                    rule=f"When tone is {tone}, next quarter growth averages {avg_growth:.1f}%",
                    observations=len(valid_growths),
                    accurate=len([g for g in valid_growths if g > 0]),
                    confidence=min(0.95, len(valid_growths) / 10),
                    last_updated=datetime.now().isoformat(),
                )
                dna.patterns[pattern.pattern_id] = pattern

    def _learn_promise_patterns(self, dna: CompanyDNA):
        """
        FIX #8: build a combined chronological list of all closed promises
        (fulfilled + broken) ordered by the quarter they were resolved,
        then take the most recent 5 to calculate the recent delivery rate.
        """
        all_closed: List[Dict] = []
        for p in dna.fulfilled_promises:
            all_closed.append({**p, '_delivered': True,
                                '_resolved_in': p.get('fulfilled_in', '')})
        for p in dna.broken_promises:
            all_closed.append({**p, '_delivered': False,
                                '_resolved_in': p.get('broken_in', '')})

        total = len(all_closed)
        if total < 3:
            return

        # Sort by resolution quarter so "recent" is actually recent
        def quarter_sort_key(p: Dict) -> Tuple[int, int]:
            parts = p['_resolved_in'].split()
            try:
                return int(parts[1]), int(parts[0][1])
            except Exception:
                return 0, 0

        all_closed.sort(key=quarter_sort_key)
        recent_window = all_closed[-5:]       # last 5 closed promises by time
        recent_delivered = sum(1 for p in recent_window if p['_delivered'])
        recent_rate = recent_delivered / len(recent_window)

        pattern = Pattern(
            pattern_id="promise_delivery",
            type="promise_delivery",
            rule=f"Management delivers {recent_rate*100:.0f}% of promises (recent window)",
            observations=total,
            accurate=len(dna.fulfilled_promises),
            confidence=min(0.95, total / 10),
            last_updated=datetime.now().isoformat(),
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
                    last_updated=datetime.now().isoformat(),
                )
                dna.patterns[pattern.pattern_id] = pattern

    def _learn_growth_patterns(self, dna: CompanyDNA):
        # FIX #3: is not None so 0% growth is included
        growth_rates = [q.revenue_growth for q in dna.timeline if q.revenue_growth is not None]
        if len(growth_rates) >= 3:
            recent = growth_rates[-3:]
            if all(recent[i] > recent[i - 1] for i in range(1, len(recent))):
                trajectory = "accelerating"
            elif all(recent[i] < recent[i - 1] for i in range(1, len(recent))):
                trajectory = "decelerating"
            elif max(recent) - min(recent) > 10:
                trajectory = "volatile"
            else:
                trajectory = "steady"

            pattern = Pattern(
                pattern_id="growth_trajectory",
                type="growth",
                rule=f"Growth trajectory: {trajectory} ({' -> '.join(f'{g:.0f}%' for g in recent)})",
                observations=len(recent),
                accurate=len(recent),
                confidence=min(0.90, len(growth_rates) / 8),
                last_updated=datetime.now().isoformat(),
            )
            dna.patterns['growth_trajectory'] = pattern

    def _learn_seasonal_patterns(self, dna: CompanyDNA):
        quarterly_growth: Dict[str, List[float]] = defaultdict(list)
        for q in dna.timeline:
            # FIX #3: is not None
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
                    last_updated=datetime.now().isoformat(),
                )
                dna.patterns[pattern.pattern_id] = pattern


# ====== DEVIATION DETECTOR ======
class DeviationDetector:
    """Detects deviations from learned patterns"""

    def detect_deviations(self, dna: CompanyDNA, new_data: QuarterData) -> List[Dict[str, Any]]:
        deviations: List[Dict] = []
        if len(dna.timeline) < 2:
            return deviations
        deviations.extend(self._check_growth_deviations(dna, new_data))
        deviations.extend(self._check_customer_deviations(dna, new_data))
        deviations.extend(self._check_theme_deviations(dna, new_data))
        deviations.extend(self._check_tone_deviations(dna, new_data))
        return deviations

    def _check_growth_deviations(self, dna: CompanyDNA, new_data: QuarterData) -> List[Dict]:
        # FIX #3: is not None
        if new_data.revenue_growth is None:
            return []
        historical = [q.revenue_growth for q in dna.timeline[:-1] if q.revenue_growth is not None]
        if not historical:
            return []
        avg = sum(historical) / len(historical)
        diff = new_data.revenue_growth - avg
        if abs(diff) > 10:
            severity = 'major'
        elif abs(diff) > 5:
            severity = 'moderate'
        else:
            return []
        return [{'type': 'growth_vs_average', 'severity': severity,
                 'description': f"Growth ({new_data.revenue_growth}%) vs historical avg ({avg:.1f}%) diff: {diff:+.1f}pp"}]

    def _check_customer_deviations(self, dna: CompanyDNA, new_data: QuarterData) -> List[Dict]:
        deviations = []
        for customer, data in dna.customer_evolution.items():
            if len(data['mentions']) >= 3 and customer not in new_data.customer_mentions:
                last_mentions = data['mentions'][-3:]
                # Check previous quarters only (exclude newly-added new_data from timeline check)
                prev_quarters = {q.quarter for q in dna.timeline[:-1]}
                if all(m in prev_quarters for m in last_mentions):
                    deviations.append({'type': 'customer_silence', 'severity': 'major',
                                       'icon': '🔴',
                                       'description': f"{customer} not mentioned (was in {len(data['mentions'])} consecutive quarters)"})
        return deviations

    def _check_theme_deviations(self, dna: CompanyDNA, new_data: QuarterData) -> List[Dict]:
        deviations = []
        for theme, data in dna.theme_lifecycle.items():
            if data['status'] == 'active' and len(data['mentions']) >= 4:
                if theme not in new_data.key_themes:
                    deviations.append({'type': 'theme_disappeared', 'severity': 'moderate',
                                       'icon': '🟠',
                                       'description': f"Theme '{theme}' absent (was in {len(data['mentions'])} quarters)"})

        existing_themes = set(dna.theme_lifecycle.keys())
        for theme in set(new_data.key_themes) - existing_themes:
            deviations.append({'type': 'new_theme', 'severity': 'minor', 'icon': '🟡',
                                'description': f"NEW theme appeared: '{theme}'"})
        return deviations

    def _check_tone_deviations(self, dna: CompanyDNA, new_data: QuarterData) -> List[Dict]:
        if len(dna.timeline) < 2:
            return []
        prev_tone = dna.timeline[-2].tone   # -1 is new_data, -2 is previous
        tone_score = {'positive': 1, 'neutral': 0, 'cautious': -1, 'defensive': -2}
        prev_score = tone_score.get(prev_tone, 0)
        new_score  = tone_score.get(new_data.tone, 0)
        if new_score < prev_score:
            severity = "major" if new_score <= -1 else "moderate"
            return [{'type': 'tone_shift', 'severity': severity,
                     'icon': '🔴' if severity == 'major' else '🟠',
                     'description': f"Tone shifted: {prev_tone.upper()} -> {new_data.tone.upper()}"}]
        return []


# ====== PREDICTION ENGINE ======
class PredictionEngine:
    """Makes predictions and validates them"""

    def make_predictions(self, dna: CompanyDNA) -> Optional[Prediction]:
        if len(dna.timeline) < 3:
            return None
        current_quarter = dna.timeline[-1]
        target_quarter  = self._get_next_quarter(current_quarter.quarter)
        predictions: Dict[str, Any] = {}
        confidence_scores: List[float] = []

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
        return Prediction(
            prediction_id=f"{dna.ticker}_{target_quarter}",
            made_on_quarter=current_quarter.quarter,
            target_quarter=target_quarter,
            predictions=predictions,
            confidence=overall_confidence,
        )

    def _predict_growth(self, dna: CompanyDNA) -> Tuple[Optional[Dict], float]:
        # FIX #3: is not None
        growth_history = [q.revenue_growth for q in dna.timeline if q.revenue_growth is not None]
        if len(growth_history) < 3:
            return None, 0.0
        recent = growth_history[-3:]
        if all(recent[i] > recent[i - 1] for i in range(1, len(recent))):
            avg_increase = (recent[-1] - recent[0]) / 2
            predicted    = recent[-1] + avg_increase
            confidence   = 0.70
        elif all(recent[i] < recent[i - 1] for i in range(1, len(recent))):
            avg_decrease = (recent[0] - recent[-1]) / 2
            predicted    = recent[-1] - avg_decrease
            confidence   = 0.70
        else:
            predicted  = sum(recent) / len(recent)
            confidence = 0.60
        return {'range': f"{predicted - 3:.0f}-{predicted + 3:.0f}%",
                'midpoint': predicted, 'basis': 'trend_analysis'}, confidence

    def _predict_tone(self, dna: CompanyDNA) -> Tuple[Optional[str], float]:
        if len(dna.timeline) < 2:
            return None, 0.0
        current_tone = dna.timeline[-1].tone
        pattern_key  = f"tone_{current_tone}_predicts_growth"
        if pattern_key in dna.patterns:
            return current_tone, dna.patterns[pattern_key].confidence
        return current_tone, 0.50

    def _predict_customers(self, dna: CompanyDNA) -> Tuple[Optional[List[str]], float]:
        recent_quarters = dna.timeline[-3:]
        freq: Dict[str, int] = defaultdict(int)
        for q in recent_quarters:
            for customer in getattr(q, 'customer_mentions', {}).keys():
                freq[customer] += 1
        likely = [c for c, f in freq.items() if f >= 2]
        if likely:
            return likely, (0.75 if len(recent_quarters) >= 3 else 0.60)
        return None, 0.0

    def validate_prediction(self, prediction: Prediction, actual_data: QuarterData) -> Prediction:
        prediction.actual_results = {}
        correct = 0
        total   = 0

        # FIX #3: is not None
        if 'revenue_growth' in prediction.predictions and actual_data.revenue_growth is not None:
            pred_range = prediction.predictions['revenue_growth']['range']
            low, high  = map(float, pred_range.replace('%', '').split('-'))
            prediction.actual_results['revenue_growth'] = actual_data.revenue_growth
            total += 1
            if low <= actual_data.revenue_growth <= high:
                correct += 1

        if 'tone' in prediction.predictions:
            prediction.actual_results['tone'] = actual_data.tone
            total += 1
            if prediction.predictions['tone'] == actual_data.tone:
                correct += 1

        if 'customers' in prediction.predictions:
            pred_set   = set(prediction.predictions['customers'])
            actual_set = set(actual_data.customer_mentions.keys())
            prediction.actual_results['customers'] = list(actual_set)
            total += 1
            if pred_set and len(pred_set.intersection(actual_set)) >= len(pred_set) * 0.7:
                correct += 1

        if total > 0:
            prediction.accuracy  = correct / total
            prediction.validated = True
        return prediction

    def _get_next_quarter(self, current_quarter: str) -> str:
        try:
            parts = current_quarter.split()
            q_num = int(parts[0][1])
            year  = int(parts[1])
            return f"Q1 {year + 1}" if q_num == 4 else f"Q{q_num + 1} {year}"
        except Exception:
            return "Q? ????"


# ====== DATABASE MANAGER ======
class DatabaseManager:
    """Manages dual storage: JSON files and SQLite database"""

    def __init__(self, output_dir: str, ticker: str):
        self.output_dir = Path(output_dir) / ticker
        self.dna_dir    = self.output_dir / 'dna'
        self.db_path    = self.output_dir / 'transcripts.db'
        self._init_database()

    def _init_database(self):
        self.dna_dir.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        cursor = self.conn.cursor()
        cursor.execute('''CREATE TABLE IF NOT EXISTS quarters (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            quarter         TEXT UNIQUE,
            date            TEXT,
            revenue_growth  REAL,
            margin          REAL,
            tone            TEXT,
            specificity     TEXT,
            data_json       TEXT
        )''')
        self.conn.commit()

    def save_dna(self, dna: CompanyDNA):
        dna_dict = asdict(dna)

        # Versioned snapshot
        json_path = self.dna_dir / f"DNA_{dna.latest_quarter.replace(' ', '_')}.json"
        with open(json_path, 'w', encoding='utf-8') as f:
            json.dump(dna_dict, f, indent=2, default=str)

        # Always-current pointer
        latest_path = self.dna_dir / "DNA_LATEST.json"
        with open(latest_path, 'w', encoding='utf-8') as f:
            json.dump(dna_dict, f, indent=2, default=str)

        # FIX #10: actually populate the SQLite table
        if dna.timeline:
            latest_q = dna.timeline[-1]
            cursor = self.conn.cursor()
            cursor.execute('''
                INSERT OR REPLACE INTO quarters
                    (quarter, date, revenue_growth, margin, tone, specificity, data_json)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            ''', (
                latest_q.quarter,
                latest_q.date.isoformat() if latest_q.date else None,
                latest_q.revenue_growth,
                latest_q.margin,
                latest_q.tone,
                latest_q.specificity,
                json.dumps(asdict(latest_q), default=str),
            ))
            self.conn.commit()

        logger.info(f"DNA v{dna.version} ({dna.latest_quarter}) saved")

    def close(self):
        if self.conn:
            self.conn.close()


# ====== AUDIT MANAGER ======
class AuditManager:
    """Audits and logs structural changes to the DNA state over time"""

    def __init__(self, output_dir: str, ticker: str):
        self.reports_dir = Path(output_dir) / ticker / 'reports'
        self.reports_dir.mkdir(parents=True, exist_ok=True)
        self.audit_path = self.reports_dir / '06_AUDIT_TRAIL.md'
        with open(self.audit_path, 'w', encoding='utf-8') as f:
            f.write(f"# 🛡️ {ticker} - DNA Evolution Audit Trail\n\n")
            f.write("Tracks how AI understanding of the company evolves quarter by quarter.\n\n")

    def audit_file_system(self, file_stats: dict):
        with open(self.audit_path, 'a', encoding='utf-8') as f:
            f.write("## 📁 File System Audit\n\n")
            if file_stats['renames']:
                f.write(f"- 🔄 **Renamed {len(file_stats['renames'])} files** to standard format.\n")
                for old, new in file_stats['renames']:
                    f.write(f"  - `{old}` → `{new}`\n")
            if file_stats['duplicates']:
                f.write(f"- 🗑️ **Removed {len(file_stats['duplicates'])} duplicate/inferior files.**\n")
                for dup in file_stats['duplicates']:
                    f.write(f"  - `{dup}`\n")
            if not file_stats['renames'] and not file_stats['duplicates']:
                f.write("- ✅ No file system changes needed.\n")
            f.write("\n---\n\n")

    def audit(self, quarter: str, prev_dna_state: dict, new_dna: CompanyDNA,
              deviations: List[Dict], is_baseline: bool = False):
        with open(self.audit_path, 'a', encoding='utf-8') as f:
            label = " *(BASELINE)*" if is_baseline else ""
            f.write(f"## 📅 Quarter Processed: {quarter}{label}\n")

            new_patterns = len(new_dna.patterns) - prev_dna_state['pattern_count']
            if new_patterns > 0:
                f.write(f"- 🧠 **Learned {new_patterns} new pattern(s)**\n")

            if deviations:
                f.write(f"- 🚨 **Detected {len(deviations)} deviation(s)** from historical behavior\n")
                for dev in deviations:
                    f.write(f"  - {dev.get('icon', '-')} {dev['description']}\n")
            else:
                label_text = "N/A (baseline)" if is_baseline else "0 deviations"
                f.write(f"- ✅ {label_text}\n")

            new_fulfilled = len(new_dna.fulfilled_promises) - prev_dna_state['fulfilled_count']
            new_broken    = len(new_dna.broken_promises)    - prev_dna_state['broken_count']
            if new_fulfilled > 0:
                f.write(f"- 🤝 Management fulfilled {new_fulfilled} commitment(s)\n")
            if new_broken > 0:
                f.write(f"- 💔 Management broke {new_broken} commitment(s)\n")

            f.write("\n---\n\n")


# ====== REPORT GENERATOR ======
class ReportGenerator:
    """Generates all markdown reports"""

    def __init__(self, output_dir: str, ticker: str):
        self.output_dir  = Path(output_dir) / ticker
        self.reports_dir = self.output_dir / 'reports'
        self.reports_dir.mkdir(parents=True, exist_ok=True)

    def generate_quarter_report(self, dna: CompanyDNA, quarter_idx: int,
                                 deviations: List[Dict], prediction: Optional[Prediction] = None):
        quarter_data = dna.timeline[quarter_idx]
        is_baseline  = (quarter_idx == 0)

        q_parts    = quarter_data.quarter.split()
        sortable_q = f"{q_parts[1]}_{q_parts[0]}" if len(q_parts) == 2 else quarter_data.quarter.replace(' ', '_')
        suffix     = 'baseline' if is_baseline else 'analysis'
        report_path = self.reports_dir / f"{sortable_q}_{suffix}.md"

        with open(report_path, 'w', encoding='utf-8') as f:
            title = 'Baseline Analysis' if is_baseline else 'Comprehensive Analysis'
            f.write(f"# {quarter_data.quarter} - {title}\n\n")
            f.write(f"**DNA Version:** v{dna.version}\n")
            f.write(f"**Tone:** {quarter_data.tone.upper()} | **Specificity:** {quarter_data.specificity.upper()}\n\n")
            f.write("---\n\n")

            # Prediction validation (skip for baseline)
            if not is_baseline:
                prev_predictions = [p for p in dna.predictions
                                    if p.target_quarter == quarter_data.quarter and p.validated]
                if prev_predictions:
                    f.write("## 🎯 Prediction Validation\n\n")
                    for pred in prev_predictions:
                        f.write(f"**Prediction made in {pred.made_on_quarter}:**\n\n")
                        if 'revenue_growth' in pred.predictions:
                            pred_range = pred.predictions['revenue_growth'].get('range', 'N/A')
                            actual     = (pred.actual_results or {}).get('revenue_growth', 'N/A')
                            icon = "✅" if (pred.accuracy or 0) >= 0.8 else "⚠️" if (pred.accuracy or 0) >= 0.5 else "❌"
                            f.write(f"{icon} **Growth:** predicted {pred_range} | actual {actual}\n")
                        if 'tone' in pred.predictions:
                            pred_tone   = pred.predictions['tone']
                            actual_tone = (pred.actual_results or {}).get('tone', 'N/A')
                            icon = "✅" if pred_tone == actual_tone else "❌"
                            f.write(f"{icon} **Tone:** predicted {pred_tone.upper()} | actual {str(actual_tone).upper()}\n")
                        f.write(f"\n**Accuracy:** {(pred.accuracy or 0)*100:.0f}%\n\n")
                    f.write("---\n\n")

            # Key metrics
            f.write("## 📊 Key Metrics\n\n")
            if quarter_data.revenue_growth is not None:
                f.write(f"**Revenue Growth:** {quarter_data.revenue_growth:.1f}%\n")
                if not is_baseline and quarter_idx > 0:
                    prev_growth     = dna.timeline[quarter_idx - 1].revenue_growth
                    baseline_growth = dna.timeline[0].revenue_growth
                    # FIX #3: is not None
                    if prev_growth is not None:
                        delta = quarter_data.revenue_growth - prev_growth
                        icon  = "🟢" if delta > 0 else "🔴" if delta < 0 else "⚪"
                        f.write(f"  - vs Previous Quarter: {icon} {delta:+.1f}pp\n")
                    if baseline_growth is not None:
                        delta = quarter_data.revenue_growth - baseline_growth
                        f.write(f"  - vs Baseline ({dna.timeline[0].quarter}): {'🟢' if delta>0 else '🔴'} {delta:+.1f}pp\n")
                    all_growth = [q.revenue_growth for q in dna.timeline[:quarter_idx]
                                  if q.revenue_growth is not None]
                    if all_growth:
                        avg   = sum(all_growth) / len(all_growth)
                        delta = quarter_data.revenue_growth - avg
                        icon  = "🟢" if delta > 2 else "🔴" if delta < -2 else "⚪"
                        f.write(f"  - vs Historical Average: {icon} {delta:+.1f}pp (avg: {avg:.1f}%)\n")
            f.write("\n")
            if quarter_data.margin is not None:
                f.write(f"**Gross Margin:** {quarter_data.margin:.1f}%\n\n")

            # Deviations
            if deviations:
                f.write("## 🚨 Pattern Deviations Detected\n\n")
                for severity_label in ['critical', 'major', 'moderate', 'minor']:
                    group = [d for d in deviations if d['severity'] == severity_label]
                    if group:
                        f.write(f"### {severity_label.upper()}\n\n")
                        for dev in group:
                            f.write(f"{dev.get('icon', '')} **{dev['type']}**: {dev['description']}\n")
                        f.write("\n")
                f.write("---\n\n")

            # Business narrative
            f.write("## 📝 Business Narrative\n\n")
            if quarter_data.key_themes:
                f.write("**Key Themes:**\n")
                for theme in quarter_data.key_themes:
                    if theme in dna.theme_lifecycle:
                        status   = dna.theme_lifecycle[theme]['status']
                        mentions = len(dna.theme_lifecycle[theme]['mentions'])
                        prefix   = "🆕" if status == 'new' else "🔄"
                        suffix_str = "" if status == 'new' else f" ({mentions} quarters)"
                        f.write(f"- {prefix} {theme}{suffix_str}\n")
                    else:
                        f.write(f"- {theme}\n")
                f.write("\n")

            if quarter_data.wins:
                f.write("**Wins & Achievements:**\n")
                for win in quarter_data.wins:
                    f.write(f"- ✅ {win}\n")
                f.write("\n")

            if quarter_data.challenges:
                f.write("**Challenges:**\n")
                for challenge in quarter_data.challenges:
                    f.write(f"- ⚠️ {challenge}\n")
                f.write("\n")

            if quarter_data.customer_mentions:
                f.write("**Customer Mentions:**\n")
                for customer, context in quarter_data.customer_mentions.items():
                    if customer in dna.customer_evolution:
                        mentions = len(dna.customer_evolution[customer].get('mentions', []))
                        new_tag  = dna.customer_evolution[customer].get('first_mentioned') == quarter_data.quarter
                        prefix   = "🆕" if new_tag else f"🔄 ({mentions} mentions)"
                        f.write(f"- {prefix} **{customer}**: {context[:150]}...\n")
                f.write("\n")

            if quarter_data.product_updates:
                f.write("**Product Updates:**\n")
                for update in quarter_data.product_updates:
                    f.write(f"- 🚀 {update}\n")
                f.write("\n")

            if quarter_data.forward_looking_guidance or quarter_data.promises:
                f.write("## 🔮 Forward-Looking Statements\n\n")
                for guidance in quarter_data.forward_looking_guidance:
                    f.write(f"- {guidance}\n")
                f.write("\n")
                if quarter_data.promises:
                    f.write("**Commitments Made:**\n")
                    for promise in quarter_data.promises:
                        f.write(f"- [{promise.get('category','OTHER').upper()}] {promise.get('text','')}\n")
                    f.write("\n")

            if quarter_data.evidence_key_quotes:
                f.write("## 🗣️ Key Quotes\n\n")
                for speaker, quote in quarter_data.evidence_key_quotes:
                    f.write(f'> **{speaker}:** "{quote}"\n\n')

            if prediction and not is_baseline:
                f.write("---\n\n## 🔮 Prediction for Next Quarter\n")
                f.write(f"**Target:** {prediction.target_quarter}\n")
                f.write(f"**Confidence:** {prediction.confidence*100:.0f}%\n\n")
                if 'revenue_growth' in prediction.predictions:
                    pd_ = prediction.predictions['revenue_growth']
                    if isinstance(pd_, dict):
                        f.write(f"**Growth Prediction:** {pd_.get('range','N/A')} (basis: {pd_.get('basis','N/A')})\n\n")
                if 'tone' in prediction.predictions:
                    f.write(f"**Tone Prediction:** {prediction.predictions['tone'].upper()}\n\n")
                if 'customers' in prediction.predictions:
                    f.write(f"**Expected Customers:** {', '.join(prediction.predictions['customers'])}\n\n")

        logger.info(f"Quarter report generated: {report_path}")

    def generate_master_timeline(self, dna: CompanyDNA):
        report_path = self.reports_dir / '00_MASTER_TIMELINE.md'
        with open(report_path, 'w', encoding='utf-8') as f:
            f.write(f"# 🧬 {dna.ticker} - Master Timeline\n\n")
            f.write(f"**DNA Version:** v{dna.version}\n")
            f.write(f"**Period:** {dna.baseline_quarter} → {dna.latest_quarter}\n")
            f.write(f"**Total Quarters:** {len(dna.timeline)}\n\n---\n\n")

            f.write("## Timeline\n\n")
            f.write("| Quarter | Growth | Margin | Tone | Themes | Customers |\n")
            f.write("|---------|--------|--------|------|--------|-----------|\n")
            for q in dna.timeline:
                g = f"{q.revenue_growth:.1f}%" if q.revenue_growth is not None else "N/A"
                m = f"{q.margin:.1f}%" if q.margin is not None else "N/A"
                f.write(f"| {q.quarter} | {g} | {m} | {q.tone} | {len(q.key_themes)} | {len(q.customer_mentions)} |\n")
            f.write("\n")

            f.write("## Growth Trajectory\n\n")
            growth_values = [q.revenue_growth for q in dna.timeline if q.revenue_growth is not None]
            if growth_values:
                max_g = max(growth_values) or 1
                for q in dna.timeline:
                    if q.revenue_growth is not None:
                        bar = "█" * int((q.revenue_growth / max_g) * 40)
                        f.write(f"{q.quarter:12s} | {bar} {q.revenue_growth:.1f}%\n")
            f.write("\n")

            if dna.patterns:
                f.write("## Learned Patterns\n\n")
                for name, pattern in dna.patterns.items():
                    # FIX #6: use pattern_id not non-existent 'name'
                    pid  = getattr(pattern, 'pattern_id', name)
                    rule = getattr(pattern, 'rule', 'N/A')
                    conf = getattr(pattern, 'confidence', 0)
                    acc  = getattr(pattern, 'accurate', 0)
                    obs  = getattr(pattern, 'observations', 0)
                    f.write(f"### {pid}\n")
                    f.write(f"- Rule: {rule}\n")
                    f.write(f"- Confidence: {conf*100:.0f}% ({acc}/{obs})\n\n")

        logger.info(f"Master timeline generated: {report_path}")

    def generate_prediction_tracker(self, dna: CompanyDNA):
        report_path = self.reports_dir / '02_PREDICTION_TRACKER.md'
        with open(report_path, 'w', encoding='utf-8') as f:
            f.write(f"# 🎯 {dna.ticker} - Prediction Accuracy Tracker\n\n")
            if not dna.predictions:
                f.write("No predictions made yet.\n")
                return
            validated = [p for p in dna.predictions if p.validated]
            if validated:
                total_acc = sum(p.accuracy or 0 for p in validated) / len(validated)
                f.write(f"**Overall Model Accuracy:** {total_acc*100:.0f}%\n")
                f.write(f"**Validated:** {len(validated)}/{len(dna.predictions)}\n\n---\n\n")
                f.write("| Made In | Target | Prediction | Actual | Accuracy |\n")
                f.write("|---------|--------|------------|--------|----------|\n")
                for pred in validated:
                    if 'revenue_growth' in pred.predictions:
                        pd_ = pred.predictions['revenue_growth']
                        pred_str = pd_.get('range', 'N/A') if isinstance(pd_, dict) else str(pd_)
                        actual_str = str((pred.actual_results or {}).get('revenue_growth', 'N/A'))
                    else:
                        pred_str = actual_str = "N/A"
                    acc  = pred.accuracy or 0
                    icon = "✅" if acc >= 0.8 else "⚠️" if acc >= 0.5 else "❌"
                    f.write(f"| {pred.made_on_quarter} | {pred.target_quarter} | {pred_str} | {actual_str} | {icon} {acc*100:.0f}% |\n")
        logger.info(f"Prediction tracker generated: {report_path}")

    def generate_investment_brief(self, dna: CompanyDNA):
        report_path = self.reports_dir / '05_INVESTMENT_BRIEF.md'
        with open(report_path, 'w', encoding='utf-8') as f:
            f.write(f"# 💼 {dna.ticker} - Investment Brief\n\n")
            f.write(f"**As of:** {dna.latest_quarter} | **Updated:** {datetime.now().strftime('%Y-%m-%d')}\n\n---\n\n")
            score     = 0
            reasoning = []

            if 'growth_trajectory' in dna.patterns:
                traj = getattr(dna.patterns['growth_trajectory'], 'rule', '')
                if 'accelerating' in traj:
                    score += 3; reasoning.append("✅ Growth accelerating")
                elif 'steady' in traj:
                    score += 1; reasoning.append("✅ Growth steady")
                elif 'decelerating' in traj:
                    score -= 2; reasoning.append("🔴 Growth decelerating")

            if 'promise_delivery' in dna.patterns:
                conf = getattr(dna.patterns['promise_delivery'], 'confidence', 0)
                if conf > 0.75:
                    score += 2; reasoning.append(f"✅ High management credibility ({conf*100:.0f}%)")
                elif conf < 0.50:
                    score -= 2; reasoning.append(f"🔴 Low management credibility ({conf*100:.0f}%)")

            active_customers = len([
                c for c, d in dna.customer_evolution.items()
                if len(d.get('mentions', [])) >= 3
            ])
            if active_customers >= 3:
                score += 2; reasoning.append(f"✅ {active_customers} stable sector relationships")

            latest = dna.timeline[-1] if dna.timeline else None
            if latest:
                if latest.tone == 'defensive':
                    score -= 2; reasoning.append("🔴 Management tone defensive")
                elif latest.tone == 'positive':
                    score += 1; reasoning.append("✅ Positive management tone")

            # FIX #9: update prediction_accuracy from validated predictions
            validated = [p for p in dna.predictions if p.validated]
            if validated:
                dna.prediction_accuracy = sum(p.accuracy or 0 for p in validated) / len(validated)
                f.write(f"**Model Prediction Accuracy:** {dna.prediction_accuracy*100:.0f}%\n\n")

            if score >= 4:   verdict, summary = "🟢 **STRONG BUY**", "Multiple positive factors. High conviction."
            elif score >= 2: verdict, summary = "🟢 **BUY**",         "More positives than negatives."
            elif score >= 0: verdict, summary = "🟡 **HOLD**",        "Mixed signals. Watch closely."
            else:            verdict, summary = "🔴 **AVOID/SELL**",  "Too many red flags."

            f.write(f"## {verdict}\n\n**Score:** {score}\n\n{summary}\n\n")
            f.write("### Reasoning\n\n")
            for r in reasoning:
                f.write(f"- {r}\n")
            f.write("\n---\n\n")

            if latest:
                f.write("### Latest Quarter\n\n")
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
                    for ch in latest.challenges[:3]: f.write(f"- {ch}\n")
                    f.write("\n")

        logger.info(f"Investment brief generated: {report_path}")


# ====== MAIN ORCHESTRATOR ======
class DNAEvolutionAnalyzer:
    """Main orchestrator"""

    def __init__(self, folder_path: str, ticker: str, output_dir: str = "analysis_output"):
        self.folder_path = folder_path
        self.ticker      = ticker
        self.output_dir  = output_dir
        logger.info("Initializing components...")
        self.scanner    = FileScanner()
        self.extractor  = TranscriptExtractor()
        self.builder    = DNABuilder()
        self.learner    = PatternLearner()
        self.db_manager = DatabaseManager(output_dir, ticker)
        self.report_gen = ReportGenerator(output_dir, ticker)
        self.pred_engine = PredictionEngine()
        self.deviation_detector = DeviationDetector()
        self.audit_manager = AuditManager(output_dir, ticker)

    def run(self) -> Optional[CompanyDNA]:
        logger.info("=" * 80)
        logger.info(f"DNA EVOLUTION ANALYZER — {self.ticker}")
        logger.info("=" * 80)

        # STEP 1: Scan and sort
        logger.info("[STEP 1] Scanning and sorting transcripts chronologically (oldest → newest)…")
        sorted_transcripts, file_stats = self.scanner.scan_and_sort(self.folder_path, self.ticker)
        self.audit_manager.audit_file_system(file_stats)

        if not sorted_transcripts:
            logger.error("No transcripts found! Check that files have readable dates/quarters.")
            return None

        logger.info(f"  → {len(sorted_transcripts)} transcripts found")
        for fp, dt, q in sorted_transcripts:
            logger.info(f"     {q}  ({dt.date()})  {os.path.basename(fp)}")

        # STEP 2: Build baseline from the OLDEST transcript
        logger.info("\n[STEP 2] Building baseline DNA from oldest transcript…")
        file_path, date, quarter = sorted_transcripts[0]
        # FIX #2: pass ticker to extractor
        first_data = self.extractor.extract(file_path, self.ticker, quarter, date)
        dna = self.builder.build_baseline(self.ticker, first_data)
        self.db_manager.save_dna(dna)

        # FIX #1: generate baseline report (was completely missing before)
        # FIX #5: audit baseline quarter too
        baseline_state = {'pattern_count': 0, 'fulfilled_count': 0, 'broken_count': 0}
        self.report_gen.generate_quarter_report(dna, 0, [], None)
        self.audit_manager.audit(quarter, baseline_state, dna, [], is_baseline=True)

        # STEP 3: Evolve DNA quarter by quarter
        logger.info("\n[STEP 3] Evolving DNA quarter by quarter…")
        for idx, (file_path, date, quarter) in enumerate(sorted_transcripts[1:], start=1):
            logger.info(f"\n--- [{idx}/{len(sorted_transcripts)-1}] {quarter} ---")

            quarter_data = self.extractor.extract(file_path, self.ticker, quarter, date)

            prev_state = {
                'pattern_count':   len(dna.patterns),
                'fulfilled_count': len(dna.fulfilled_promises),
                'broken_count':    len(dna.broken_promises),
            }

            # Validate last prediction before evolving
            if dna.predictions:
                self.pred_engine.validate_prediction(dna.predictions[-1], quarter_data)

            dna = self.builder.evolve_dna(dna, quarter_data)
            dna = self.learner.learn_patterns(dna)
            deviations = self.deviation_detector.detect_deviations(dna, quarter_data)

            if deviations:
                logger.info(f"  → {len(deviations)} deviations detected")

            prediction = self.pred_engine.make_predictions(dna)
            if prediction:
                dna.predictions.append(prediction)
                logger.info(f"  → Prediction made for {prediction.target_quarter}")

            self.db_manager.save_dna(dna)
            self.report_gen.generate_quarter_report(dna, idx, deviations, prediction)
            self.audit_manager.audit(quarter, prev_state, dna, deviations)

            if quarter_data.revenue_growth is not None:
                logger.info(f"  Growth: {quarter_data.revenue_growth:.1f}%")
            logger.info(f"  Tone:   {quarter_data.tone.upper()}")

        # STEP 4: Master reports
        logger.info("\n[STEP 4] Generating master reports…")
        self.report_gen.generate_master_timeline(dna)
        self.report_gen.generate_prediction_tracker(dna)
        self.report_gen.generate_investment_brief(dna)

        logger.info("\n" + "=" * 80)
        logger.info("ANALYSIS COMPLETE")
        logger.info(f"  DNA Files : {self.output_dir}/{self.ticker}/dna/")
        logger.info(f"  Reports   : {self.output_dir}/{self.ticker}/reports/")
        logger.info("=" * 80)

        self.db_manager.close()
        return dna


# ====== COMMAND LINE INTERFACE ======
def main():
    parser = argparse.ArgumentParser(
        description="DNA Evolution Transcript Analyzer",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Example:\n  python dna_analyzer.py --folder /path/to/transcripts --ticker ACME"
    )
    parser.add_argument('--folder', required=True, help='Folder containing transcript files (.txt or .md)')
    parser.add_argument('--ticker', required=True, help='Company ticker symbol')
    parser.add_argument('--out',    default='analysis_output', dest='output_dir',
                        help='Output directory (default: analysis_output)')
    args = parser.parse_args()

    if not os.path.exists(args.folder):
        logger.error(f"Folder not found: {args.folder}")
        return

    analyzer = DNAEvolutionAnalyzer(args.folder, args.ticker, args.output_dir)
    try:
        dna = analyzer.run()
        if dna:
            logger.info("\n" + "=" * 80)
            logger.info("INVESTMENT VERDICT")
            logger.info("=" * 80)
            latest = dna.timeline[-1] if dna.timeline else None
            if latest:
                logger.info(f"Latest Quarter : {latest.quarter}")
                if latest.revenue_growth is not None:
                    logger.info(f"Growth         : {latest.revenue_growth:.1f}%")
                logger.info(f"Tone           : {latest.tone.upper()}")
            if 'growth_trajectory' in dna.patterns:
                logger.info(f"Trajectory     : {dna.patterns['growth_trajectory'].rule}")
            logger.info("\nSee 05_INVESTMENT_BRIEF.md for full recommendation")
            logger.info("=" * 80)
    except KeyboardInterrupt:
        logger.info("Analysis interrupted by user")
    except Exception as e:
        logger.error(f"Analysis failed: {e}", exc_info=True)


if __name__ == '__main__':
    main()
