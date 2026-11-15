"""
state.py — Position counter, rotation enforcement, history tracking
"""
from __future__ import annotations
import json
from datetime import date, datetime
from pathlib import Path
from typing import Optional

from src.utils import load_history, save_history, load_brand, load_settings, get_logger

log = get_logger("state")

# ── Sequence constants ───────────────────────────────────────
SEQUENCE = [1, 2, 3, 4, 5, 6]   # Carousel, Reel, Post × 2

POSITION_FORMAT = {
    1: "carousel", 2: "reel", 3: "post",
    4: "carousel", 5: "reel", 6: "post",
}
POSITION_TEMPLATE = {
    1: "carousel_cream", 2: "reel_sage",    3: "post_dusty",
    4: "carousel_peach", 5: "reel_midnight", 6: "post_espresso",
}


class StateManager:
    def __init__(self):
        self.history = load_history()
        self.settings = load_settings()
        self.brand = load_brand()

    # ── Position ─────────────────────────────────────────────
    @property
    def current_position(self) -> int:
        return int(self.history.get("current_position", 1))

    @property
    def current_format(self) -> str:
        return POSITION_FORMAT[self.current_position]

    @property
    def current_template(self) -> str:
        return POSITION_TEMPLATE[self.current_position]

    @property
    def current_palette(self) -> dict:
        return self.brand["feed_positions"][self.current_position]

    def next_position(self) -> int:
        """What position comes after current (wraps 6 → 1)."""
        cur = self.current_position
        idx = SEQUENCE.index(cur)
        return SEQUENCE[(idx + 1) % len(SEQUENCE)]

    def advance_position(self) -> None:
        """Advance counter — called only after owner taps 👍 or 😐."""
        self.history["current_position"] = self.next_position()
        save_history(self.history)
        log.info(f"Position advanced to {self.history['current_position']}")

    # ── Language rotation ────────────────────────────────────
    def pick_language(self, force: Optional[str] = None,
                      is_festival: bool = False,
                      festival_language: Optional[str] = None) -> str:
        """Pick language respecting weights and streak rules."""
        import random

        if force:
            return force

        lang_cfg = self.settings["languages"]

        # Force Marathi for Maharashtra-specific festivals
        if is_festival and festival_language and lang_cfg.get("force_marathi_festivals"):
            if festival_language == "marathi":
                return "marathi"
            return festival_language

        # Build weighted list
        weights = lang_cfg["weights"]
        pool = []
        for lang, w in weights.items():
            pool.extend([lang] * w)

        # Enforce streak rule — never same language 3 days running
        max_streak = lang_cfg.get("max_same_streak", 2)
        recent = [p.get("language") for p in self.history.get("posts", [])[-max_streak:]]
        if len(set(recent)) == 1 and len(recent) >= max_streak:
            blocked = recent[0]
            pool = [l for l in pool if l != blocked]
            if not pool:
                pool = list(weights.keys())

        return random.choice(pool)

    # ── Recency checks ───────────────────────────────────────
    def recently_used_product(self, product_handle: str) -> bool:
        """True if this product appeared in the last N days."""
        cooldown = self.settings["products"]["product_repeat_cooldown_days"]
        recent_handles = [
            p.get("product") for p in self.history.get("posts", [])[-cooldown:]
        ]
        return product_handle in recent_handles

    def recent_hooks(self, days: int = 60) -> list[str]:
        """Return hooks used in the last N posts for originality check."""
        posts = self.history.get("posts", [])[-days:]
        return [p.get("hook", "") for p in posts if p.get("hook")]

    def recent_pillars(self, days: int = 14) -> list[str]:
        """Return content pillars used in last N posts for variety."""
        posts = self.history.get("posts", [])[-days:]
        return [p.get("pillar", "") for p in posts if p.get("pillar")]

    # ── Record a run ─────────────────────────────────────────
    def record_post(self, entry: dict) -> None:
        """Add a post record to history. Does NOT advance position."""
        entry["date"] = entry.get("date", date.today().isoformat())
        entry["position"] = self.current_position
        entry["format"] = self.current_format
        entry["template"] = self.current_template
        self.history.setdefault("posts", []).append(entry)
        self.history["last_run"] = datetime.now().isoformat()
        save_history(self.history)
        log.info(f"Recorded post: pos={entry['position']}, pillar={entry.get('pillar')}")

    # ── Feedback handling ────────────────────────────────────
    def apply_feedback(self, feedback: str, post_date: str) -> None:
        """
        feedback: 'posted_well' | 'posted_average' | 'skipped' | 'regenerate'
        Only 'posted_well' and 'posted_average' advance the position.
        """
        for post in self.history.get("posts", []):
            if post.get("date") == post_date:
                post["feedback"] = feedback
                break

        if feedback in ("posted_well", "posted_average"):
            self.advance_position()
        elif feedback == "regenerate":
            log.info("Regenerate requested — position stays, fresh copy on next run")
        elif feedback == "skipped":
            log.info("Post skipped — position stays")

        save_history(self.history)

    # ── Log manual metrics ───────────────────────────────────
    def log_metrics(self, post_date: str, metrics: dict) -> None:
        """Store owner-entered performance metrics (/stats command)."""
        for post in self.history.get("posts", []):
            if post.get("date") == post_date:
                post["metrics"] = metrics
                log.info(f"Metrics logged for {post_date}: {metrics}")
                break
        save_history(self.history)

    # ── Weekly insights input ────────────────────────────────
    def get_insights_data(self, days: int = 30) -> dict:
        """Aggregate data for the weekly insights workflow."""
        posts = [
            p for p in self.history.get("posts", [])
            if p.get("feedback") in ("posted_well", "posted_average")
        ][-days:]

        from collections import Counter
        return {
            "total_posts": len(posts),
            "formats": Counter(p.get("format") for p in posts),
            "pillars": Counter(p.get("pillar") for p in posts),
            "languages": Counter(p.get("language") for p in posts),
            "avg_qa_score": (
                sum(p.get("qa_score", 0) for p in posts) / len(posts)
                if posts else 0
            ),
            "top_feedback": Counter(p.get("feedback") for p in posts),
        }
