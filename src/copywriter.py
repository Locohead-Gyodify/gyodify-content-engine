"""
copywriter.py — Generate hooks, captions, hashtags, scripts, alt text
"""
from __future__ import annotations
import random
from dataclasses import dataclass, field
from typing import Optional

from src.utils import (get_logger, load_hashtags, load_voice_examples,
                       load_settings, hits_blocklist)
from src.llm import LLMClient
from src.ideation import Concept
from src.shopify import Product

log = get_logger("copywriter")

COPY_SYSTEM = """You are a copywriter for GYODIFY — an Indian streetwear brand.
Brand voice: playful, Gen-Z, witty, never cringe. Short punchy lines.
Never: mean, political, religious, body-shaming, sexual, profane, fake urgency.
Never invent prices or discounts — only use verified product prices given to you.
Return ONLY valid JSON."""

COPY_PROMPT = """
Write Instagram copy for a {format} post.
Language: {language}
Hook: {hook_line}
Angle: {angle}
Product: {product_name} | {product_url} | ₹{price_min:.0f}
Brand tagline: "Wear what you think." | @gyodify
Trending angle: {trend_hook}

Voice examples for {language}:
{voice_examples}

Return JSON:
{{
  "hook": "opening line max 10 words",
  "caption": "full caption 80-150 words, with line breaks, 2-4 emojis, CTA at end",
  "cta": "tap the link in bio / tag a friend / shop now etc",
  "hashtags": ["#tag1", "#tag2", ...],
  "alt_text": "image description for accessibility",
  "reel_script": {{
    "voiceover": "spoken script for reel (if format=reel, else null)",
    "beats": ["beat 1 text", "beat 2 text", "beat 3 text"],
    "audio_vibe": "describe music vibe e.g. upbeat 110bpm playful"
  }},
  "carousel_slides": [
    {{"slide": 1, "headline": "...", "body": "..."}},
    ...
  ],
  "posting_time_ist": "9:00 AM",
  "posting_time_reason": "one sentence"
}}
"""

PROOFREAD_PROMPT = """
Proofread this {language} Instagram caption for:
1. Grammar and gender agreement errors
2. Natural tone (not stiff translation)  
3. Any phrases that sound awkward to a native speaker
4. Flag any uncertain phrases with [FLAG: reason]

Caption:
{caption}

Return JSON: {{"corrected": "...", "flags": ["..."], "changes_made": true/false}}
"""


@dataclass
class CopyResult:
    hook: str
    caption: str
    cta: str
    hashtags: list[str]
    alt_text: str
    reel_script: dict = field(default_factory=dict)
    carousel_slides: list[dict] = field(default_factory=list)
    posting_time_ist: str = "9:00 AM"
    posting_time_reason: str = ""
    language: str = "english"
    provider: str = "unknown"


def _build_hashtag_set(language: str, trend_tags: list[str] = None) -> list[str]:
    """Assemble hashtag set from pools + trend tags."""
    settings = load_settings()
    pools = load_hashtags()
    cfg = settings["hashtags"]

    selected = []
    # Always include branded
    selected.extend(pools.get("branded", []))

    # Sample from pools
    broad = random.sample(pools.get("broad_fashion", []),
                          min(cfg["broad_fashion"], len(pools.get("broad_fashion", []))))
    niche = random.sample(pools.get("niche", []),
                          min(cfg["niche"], len(pools.get("niche", []))))
    intent = random.sample(pools.get("intent_community", []),
                           min(cfg["intent_community"], len(pools.get("intent_community", []))))
    india = random.sample(pools.get("india_specific", []),
                          min(2, len(pools.get("india_specific", []))))

    selected.extend(broad + niche + intent + india)

    # Add trend tags (up to 3)
    if trend_tags:
        selected.extend(trend_tags[:cfg.get("trend_of_day", 3)])

    # Deduplicate, preserve order
    seen = set()
    result = []
    for tag in selected:
        if tag.lower() not in seen:
            seen.add(tag.lower())
            result.append(tag)

    # Enforce count range
    target = cfg["total_count"]
    return result[:target]


def _get_voice_examples(language: str, count: int = 3) -> str:
    examples = load_voice_examples()
    lang_key = {"english": "english", "hindi": "hindi",
                "marathi": "marathi", "hinglish": "hinglish"}.get(language, "hinglish")
    pool = examples.get(lang_key, examples.get("hinglish", []))
    sampled = random.sample(pool, min(count, len(pool)))
    return "\n---\n".join(sampled)


def generate_copy(
    llm: LLMClient,
    concept: Concept,
    product: Optional[Product],
    language: str,
) -> CopyResult:
    """Generate full copy for a concept."""
    prod_name = product.clean_title if product else "GYODIFY Oversized Tee"
    prod_url = product.url if product else "https://gyodify.shop"
    price_min = product.price_min if product else 349.0

    voice_examples = _get_voice_examples(language)

    prompt = COPY_PROMPT.format(
        format=concept.format,
        language=language,
        hook_line=concept.hook_line,
        angle=concept.angle,
        product_name=prod_name,
        product_url=prod_url,
        price_min=price_min,
        trend_hook=concept.trend_hook,
        voice_examples=voice_examples,
    )

    try:
        data, provider = llm.complete_json(prompt, task="copywriting", system=COPY_SYSTEM)
    except Exception as e:
        log.error(f"Copywriting LLM failed: {e}")
        return _fallback_copy(concept, product, language)

    # Build hashtags — merge LLM suggestions with curated pools
    llm_hashtags = data.get("hashtags", [])
    final_hashtags = _build_hashtag_set(language, trend_tags=llm_hashtags[:5])

    result = CopyResult(
        hook=data.get("hook", concept.hook_line),
        caption=data.get("caption", ""),
        cta=data.get("cta", "Link in bio"),
        hashtags=final_hashtags,
        alt_text=data.get("alt_text", ""),
        reel_script=data.get("reel_script", {}),
        carousel_slides=data.get("carousel_slides", []),
        posting_time_ist=data.get("posting_time_ist", "9:00 AM"),
        posting_time_reason=data.get("posting_time_reason", ""),
        language=language,
        provider=provider,
    )

    # Proofread Devanagari languages
    if language in ("hindi", "marathi"):
        result = _proofread_devanagari(llm, result, language)

    # Safety checks
    hit, terms = hits_blocklist(result.caption + " ".join(result.hashtags))
    if hit:
        log.warning(f"Caption hits blocklist after generation: {terms}")
        result.caption = result.caption  # QA gate will catch this

    log.info(f"Copy generated: {len(result.caption)} chars, {len(result.hashtags)} hashtags")
    return result


def _proofread_devanagari(llm: LLMClient, copy: CopyResult, language: str) -> CopyResult:
    """Run a native-speaker proofread pass for Hindi/Marathi."""
    prompt = PROOFREAD_PROMPT.format(language=language, caption=copy.caption)
    try:
        data, _ = llm.complete_json(prompt, task="copywriting")
        if data.get("changes_made"):
            copy.caption = data.get("corrected", copy.caption)
            flags = data.get("flags", [])
            if flags:
                log.info(f"Proofread flags ({language}): {flags}")
    except Exception as e:
        log.warning(f"Proofread failed for {language}: {e}")
    return copy


def _fallback_copy(concept: Concept, product: Optional[Product],
                   language: str) -> CopyResult:
    """Return minimal copy when LLM fails completely."""
    examples = load_voice_examples()
    lang_key = language if language in examples else "hinglish"
    pool = examples.get(lang_key, [])
    caption = random.choice(pool) if pool else "Wear what you think. Shop at gyodify.shop"
    hashtags = _build_hashtag_set(language)
    return CopyResult(
        hook=concept.hook_line[:80],
        caption=caption,
        cta="Link in bio",
        hashtags=hashtags,
        alt_text="GYODIFY oversized graphic tee",
        language=language,
        provider="offline",
    )


def format_telegram_caption(copy: CopyResult) -> str:
    """Format caption for Telegram delivery (monospace block for easy copy)."""
    tags = " ".join(copy.hashtags)
    return f"{copy.caption}\n\n{tags}"
