"""
research/trends.py — Google Trends RSS (India)
"""
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

import feedparser

from src.utils import get_logger, load_sources

log = get_logger("research.trends")

TRENDS_RSS = "https://trends.google.com/trends/trendingsearches/daily/rss?geo=IN"


@dataclass
class Signal:
    source: str
    title: str
    summary: str
    url: str = ""
    timestamp: Optional[datetime] = None
    score: float = 0.0
    category: str = "general"
    region: str = "IN"
    extra: dict = field(default_factory=dict)


FASHION_KEYWORDS = [
    "fashion", "outfit", "tee", "shirt", "style", "wear",
    "streetwear", "clothing", "drip", "fit", "ootd",
    "monsoon", "winter", "summer", "festival", "diwali",
    "holi", "college", "campus", "trend", "look",
]


def _relevance_score(title: str, summary: str) -> float:
    """Score 0-1 based on fashion/gen-z relevance."""
    text = (title + " " + summary).lower()
    hits = sum(1 for kw in FASHION_KEYWORDS if kw in text)
    return min(hits / 3.0, 1.0)


def fetch_trending() -> list[Signal]:
    """Fetch India daily trending searches from Google Trends RSS."""
    signals = []
    try:
        feed = feedparser.parse(TRENDS_RSS)
        for entry in feed.entries[:20]:
            title = entry.get("title", "")
            summary = entry.get("summary", "")
            score = _relevance_score(title, summary)

            signals.append(Signal(
                source="google_trends",
                title=title,
                summary=summary,
                url=entry.get("link", ""),
                timestamp=datetime.now(),
                score=score,
                category="trending",
                region="IN",
            ))
        log.info(f"Google Trends: {len(signals)} signals fetched")
    except Exception as e:
        log.warning(f"Google Trends fetch failed: {e}")

    return signals
