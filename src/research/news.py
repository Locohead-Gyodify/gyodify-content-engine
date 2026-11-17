"""
research/news.py — Google News RSS for fashion/Gen-Z/India signals
"""
from __future__ import annotations
import urllib.parse
from datetime import datetime

import feedparser
import requests

from src.utils import get_logger, load_sources
from src.research.trends import Signal, FASHION_KEYWORDS, _relevance_score

log = get_logger("research.news")

GNEWS_BASE = "https://news.google.com/rss/search?hl=en-IN&gl=IN&ceid=IN:en&q="

QUERIES = [
    "Indian fashion trends",
    "Gen Z India style",
    "streetwear India",
    "college fashion India",
    "affordable fashion India",
    "Indian festival fashion",
    "oversized tee trend India",
]

SAFETY_FILTER = [
    "death", "killed", "murder", "rape", "accident", "crash", "flood",
    "earthquake", "terror", "bomb", "riot", "violence", "tragedy",
    "suicide", "depression", "pandemic", "covid", "war", "protest",
]


def _is_safe(title: str, summary: str) -> bool:
    text = (title + " " + summary).lower()
    return not any(term in text for term in SAFETY_FILTER)


def fetch_news() -> list[Signal]:
    signals = []
    seen_titles: set[str] = set()

    for query in QUERIES:
        url = GNEWS_BASE + urllib.parse.quote(query)
        try:
            feed = feedparser.parse(url)
            for entry in feed.entries[:5]:
                title = entry.get("title", "")
                summary = entry.get("summary", "")
                if title in seen_titles:
                    continue
                seen_titles.add(title)
                if not _is_safe(title, summary):
                    continue
                score = _relevance_score(title, summary)
                signals.append(Signal(
                    source="google_news",
                    title=title,
                    summary=summary[:300],
                    url=entry.get("link", ""),
                    timestamp=datetime.now(),
                    score=score,
                    category="news",
                    region="IN",
                    extra={"query": query},
                ))
        except Exception as e:
            log.warning(f"News RSS failed for '{query}': {e}")

    log.info(f"Google News: {len(signals)} signals fetched")
    return signals
