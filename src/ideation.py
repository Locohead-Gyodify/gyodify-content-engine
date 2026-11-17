"""
ideation.py — Generate and select content concepts via LLM
"""
from __future__ import annotations
import json
import random
from typing import Optional

from pydantic import BaseModel, field_validator

from src.utils import (get_logger, load_brand, load_settings, hits_blocklist,
                       load_themes)
from src.llm import LLMClient
from src.shopify import Product
from src.state import StateManager
from src.research.trends import Signal

log = get_logger("ideation")


class Concept(BaseModel):
    concept_id: str
    format: str                  # carousel | reel | post
    trend_hook: str
    angle: str
    product_handle: Optional[str]
    language_suggestion: str
    hook_line: str
    why_it_works: str
    risk_notes: str
    est_virality: float          # 1-10
    brand_fit: float             # 1-10
    freshness: float = 5.0       # filled in after scoring
    product_priority: float = 5.0
    final_score: float = 0.0

    @field_validator("format")
    @classmethod
    def validate_format(cls, v, info):
        # Force format to match the locked sequence position
        return v  # will be overridden by position


IDEATION_SYSTEM = """You are a creative director for GYODIFY — an Indian streetwear brand selling 
oversized graphic tees (₹349+, free shipping). The brand voice is playful, Gen-Z, witty, 
never cringe, never fake-urgent.

You generate Instagram content concepts for a specific post format.
Return ONLY valid JSON — no markdown, no explanation."""

IDEATION_PROMPT = """
Brand: GYODIFY | "Wear what you think." | Oversized graphic tees | ₹349+ | Free shipping India
Instagram: @gyodify
Post format today: {post_format}
Language today: {language}

Today's research signals:
{signals_text}

Featured products:
{products_text}

Recent post history (avoid repeating):
{history_text}

Content pillar mix (last 14 days): {pillar_mix}

Generate 5 content concepts for a {post_format} post. Return JSON array:
[{{
  "concept_id": "c1",
  "format": "{post_format}",
  "trend_hook": "the trend or insight driving this",
  "angle": "how it ties to GYODIFY",
  "product_handle": "product-handle-or-null",
  "language_suggestion": "{language}",
  "hook_line": "the opening line (max 10 words)",
  "why_it_works": "1 sentence",
  "risk_notes": "any brand risk — none/low/medium/high",
  "est_virality": 7.5,
  "brand_fit": 8.0
}}]
"""


def _score_concept(concept: Concept, state: StateManager,
                   products: list[Product]) -> float:
    """Score = 0.4*brand_fit + 0.3*virality + 0.2*freshness + 0.1*product_priority"""
    # Freshness: penalise recently used hooks
    recent = state.recent_hooks(60)
    freshness = 10.0
    for hook in recent:
        if _similar(concept.hook_line, hook):
            freshness -= 3.0
    concept.freshness = max(freshness, 1.0)

    # Product priority: new drops get boost
    product_priority = 5.0
    if concept.product_handle:
        prod = next((p for p in products if p.handle == concept.product_handle), None)
        if prod:
            if prod.is_new:
                product_priority = 10.0
            elif not state.recently_used_product(prod.handle):
                product_priority = 7.0
    concept.product_priority = product_priority

    score = (
        0.4 * concept.brand_fit
        + 0.3 * concept.est_virality
        + 0.2 * concept.freshness
        + 0.1 * concept.product_priority
    )
    concept.final_score = round(score, 2)
    return concept.final_score


def _similar(a: str, b: str) -> bool:
    """Simple similarity check — avoids exact repeats."""
    a_words = set(a.lower().split())
    b_words = set(b.lower().split())
    if not a_words or not b_words:
        return False
    overlap = len(a_words & b_words) / min(len(a_words), len(b_words))
    return overlap > 0.6


def _filter_concepts(concepts: list[Concept]) -> list[Concept]:
    """Remove concepts that hit the blocklist or have high risk."""
    clean = []
    for c in concepts:
        text = f"{c.trend_hook} {c.angle} {c.hook_line}"
        hit, terms = hits_blocklist(text)
        if hit:
            log.warning(f"Concept '{c.concept_id}' blocked: {terms}")
            continue
        if "high" in c.risk_notes.lower():
            log.warning(f"Concept '{c.concept_id}' high risk: {c.risk_notes}")
            continue
        clean.append(c)
    return clean


def generate_concepts(
    llm: LLMClient,
    state: StateManager,
    signals: list[Signal],
    products: list[Product],
    language: str,
) -> list[Concept]:
    """Generate 5 concepts via LLM, filter, score, and return ranked list."""
    post_format = state.current_format
    brand = load_brand()

    # Build signal text (top 8 by score)
    top_signals = sorted(signals, key=lambda s: s.score, reverse=True)[:8]
    signals_text = "\n".join(
        f"- [{s.source}] {s.title}" for s in top_signals
    ) or "- No live signals today (using evergreen themes)"

    # Products text
    products_text = "\n".join(
        f"- {p.clean_title} | ₹{p.price_min:.0f} | {p.url}"
        + (" [NEW DROP]" if p.is_new else "")
        for p in products[:5]
    )

    # Recent history
    recent_hooks = state.recent_hooks(14)
    history_text = "\n".join(f"- {h}" for h in recent_hooks[:5]) or "- None"

    # Pillar mix
    recent_pillars = state.recent_pillars(14)
    from collections import Counter
    pillar_counts = Counter(recent_pillars)
    pillar_mix = ", ".join(f"{k}: {v}" for k, v in pillar_counts.items()) or "none yet"

    prompt = IDEATION_PROMPT.format(
        post_format=post_format,
        language=language,
        signals_text=signals_text,
        products_text=products_text,
        history_text=history_text,
        pillar_mix=pillar_mix,
    )

    try:
        raw_concepts, provider = llm.complete_json(
            prompt, task="ideation", system=IDEATION_SYSTEM
        )
        log.info(f"Ideation: {len(raw_concepts)} concepts from {provider}")
    except Exception as e:
        log.error(f"Ideation LLM failed: {e} — using theme fallback")
        raw_concepts = _fallback_concepts(post_format, language, products)

    # Parse and validate
    concepts = []
    for raw in raw_concepts:
        try:
            c = Concept(**raw)
            c.format = post_format  # enforce
            concepts.append(c)
        except Exception as e:
            log.warning(f"Concept parse error: {e}")

    # Filter + score
    concepts = _filter_concepts(concepts)
    for c in concepts:
        _score_concept(c, state, products)

    concepts.sort(key=lambda c: c.final_score, reverse=True)
    log.info(f"Ideation: {len(concepts)} valid concepts after filtering")
    return concepts


def select_best_concept(concepts: list[Concept]) -> Optional[Concept]:
    if not concepts:
        return None
    return concepts[0]


def _fallback_concepts(format_: str, language: str, products: list[Product]) -> list[dict]:
    """Return 1 offline concept from themes bank."""
    from src.utils import load_themes
    import random
    themes = load_themes()
    all_themes = []
    for group in themes.values():
        all_themes.extend(group)
    hook = random.choice(all_themes) if all_themes else "Wear what you think."
    prod = products[0] if products else None
    return [{
        "concept_id": "offline_c1",
        "format": format_,
        "trend_hook": "evergreen relatable moment",
        "angle": "brand personality",
        "product_handle": prod.handle if prod else None,
        "language_suggestion": language,
        "hook_line": hook[:80],
        "why_it_works": "Evergreen relatable content always performs",
        "risk_notes": "none",
        "est_virality": 6.0,
        "brand_fit": 8.0,
    }]
