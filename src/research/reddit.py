"""
research/reddit.py — Reddit RSS feeds for meme/theme signals
Handles 403/429 from datacenter IPs gracefully
"""
from __future__ import annotations
import time
from datetime import datetime

import feedparser

from src.utils import get_logger, load_sources
from src.research.trends import Signal, _relevance_score
from src.research.news import _is_safe

log = get_logger("research.reddit")

REDDIT_RSS = "https://www.reddit.com/r/{subreddit}/hot.rss?limit={limit}"
HEADERS = {"User-Agent": "GYODIFY-ContentEngine/1.0 (content research bot)"}


def fetch_reddit() -> list[Signal]:
    sources = load_sources()
    signals = []
    blocked_count = 0

    for feed_cfg in sources.get("reddit_feeds", []):
        subreddit = feed_cfg["subreddit"]
        limit = feed_cfg.get("max_posts", 10)
        url = REDDIT_RSS.format(subreddit=subreddit, limit=limit)

        try:
            # feedparser doesn't support custom headers directly
            import requests
            resp = requests.get(url, headers=HEADERS, timeout=10)

            if resp.status_code in (403, 429):
                blocked_count += 1
                log.warning(f"Reddit r/{subreddit} blocked ({resp.status_code}) — skipping")
                time.sleep(2)
                continue

            resp.raise_for_status()
            feed = feedparser.parse(resp.text)

            for entry in feed.entries[:limit]:
                title = entry.get("title", "")
                summary = entry.get("summary", "")
                if not _is_safe(title, summary):
                    continue
                score = _relevance_score(title, summary)
                signals.append(Signal(
                    source=f"reddit_r_{subreddit}",
                    title=title,
                    summary=summary[:300],
                    url=entry.get("link", ""),
                    timestamp=datetime.now(),
                    score=score + 0.1,  # slight boost for social proof
                    category="social",
                    region="IN",
                    extra={"subreddit": subreddit},
                ))
            time.sleep(1)

        except Exception as e:
            log.warning(f"Reddit r/{subreddit} error: {e}")

    if blocked_count:
        log.warning(f"Reddit: {blocked_count} subreddits blocked by IP — using other sources")

    log.info(f"Reddit: {len(signals)} signals fetched ({blocked_count} blocked)")
    return signals
