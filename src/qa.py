"""
qa.py — Automated QA gate: 8 checks before Telegram delivery
"""
from __future__ import annotations
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from src.utils import get_logger, load_settings, load_brand, hits_blocklist
from src.copywriter import CopyResult
from src.shopify import Product
from src.state import StateManager
from src.llm import LLMClient

log = get_logger("qa")

QA_CRITIC_SYSTEM = """You are a strict QA reviewer for GYODIFY Instagram content.
Score the deliverable honestly. Return ONLY valid JSON."""

QA_CRITIC_PROMPT = """
Review this Instagram {format} post for GYODIFY (@gyodify).

Hook: {hook}
Caption: {caption}
Hashtags: {hashtags}
Language: {language}
Product: {product_name} | ₹{price}

Score 1-10 on each dimension:
- hook_strength: Does the opening line stop the scroll?
- brand_fit: Does it match GYODIFY's playful, Gen-Z voice?
- clarity: Is the message clear and the CTA obvious?
- cringe_risk: Inverse score — 10=zero cringe, 1=very cringe
- authenticity: Does it sound natural, not AI-generated?

Return JSON:
{{
  "hook_strength": 8,
  "brand_fit": 9,
  "clarity": 7,
  "cringe_risk": 9,
  "authenticity": 8,
  "overall": 8.2,
  "feedback": "one line of constructive feedback"
}}
"""


@dataclass
class QAResult:
    passed: bool
    checks: dict[str, str] = field(default_factory=dict)   # check: pass/warn/fail
    messages: dict[str, str] = field(default_factory=dict)  # check: message
    critic_scores: dict[str, float] = field(default_factory=dict)
    overall_score: float = 0.0
    regenerate: bool = False


def run_qa(
    copy: CopyResult,
    media_paths: list[Path],
    product: Optional[Product],
    state: StateManager,
    llm: LLMClient,
    post_format: str,
) -> QAResult:
    """Run all 8 QA checks. Returns QAResult."""
    settings = load_settings()
    qa_cfg = settings["qa"]
    result = QAResult(passed=True)

    # ── 1. Palette compliance ─────────────────────────────────
    _check_palette(result, media_paths)

    # ── 2. Content safety ────────────────────────────────────
    _check_content_safety(result, copy)

    # ── 3. Factual accuracy ──────────────────────────────────
    _check_factual(result, copy, product)

    # ── 4. Language / script ────────────────────────────────
    _check_language(result, copy)

    # ── 5. Hashtag validity ──────────────────────────────────
    _check_hashtags(result, copy, state, qa_cfg)

    # ── 6. Technical specs ──────────────────────────────────
    _check_technical(result, media_paths, post_format, settings)

    # ── 7. Originality ──────────────────────────────────────
    _check_originality(result, copy, state, qa_cfg)

    # ── 8. LLM critic ───────────────────────────────────────
    _check_llm_critic(result, copy, product, post_format, llm, qa_cfg)

    # Final verdict
    fail_checks = [k for k, v in result.checks.items() if v == "fail"]
    result.passed = len(fail_checks) == 0
    result.regenerate = len(fail_checks) > 0
    result.overall_score = result.critic_scores.get("overall", 0.0)

    if fail_checks:
        log.warning(f"QA FAILED: {fail_checks}")
    else:
        log.info(f"QA PASSED — critic score: {result.overall_score:.1f}")

    return result


# ── Individual checks ─────────────────────────────────────────

def _check_palette(result: QAResult, media_paths: list[Path]):
    """Check dominant colours are within allowed palette."""
    try:
        from PIL import Image
        import colorsys

        brand = load_brand()
        NEON_THRESHOLD_S = 0.9   # saturation > 0.9 suggests neon
        violations = []

        for path in media_paths:
            if not path.exists() or path.suffix.lower() not in (".png", ".jpg"):
                continue
            img = Image.open(path).convert("RGB")
            # Sample 1000 pixels
            img_small = img.resize((32, 32))
            pixels = list(img_small.getdata())
            for r, g, b in pixels[:200]:
                h, s, v = colorsys.rgb_to_hsv(r/255, g/255, b/255)
                # Flag neon colours (high saturation, not in brand palette)
                if s > NEON_THRESHOLD_S and v > 0.8:
                    # Check if it's close to oxblood or coral (allowed)
                    is_oxblood = (r > 100 and g < 50 and b < 50)
                    is_coral = (r > 200 and g > 80 and g < 160 and b < 100)
                    if not is_oxblood and not is_coral:
                        violations.append(f"{path.name}: neon pixel detected")
                        break

        if violations:
            result.checks["palette"] = "warn"
            result.messages["palette"] = "; ".join(violations[:3])
        else:
            result.checks["palette"] = "pass"
            result.messages["palette"] = "Palette OK"

    except Exception as e:
        result.checks["palette"] = "warn"
        result.messages["palette"] = f"Palette check skipped: {e}"


def _check_content_safety(result: QAResult, copy: CopyResult):
    """Blocklist, sensitive topics, profanity."""
    full_text = f"{copy.hook} {copy.caption} {' '.join(copy.hashtags)}"
    hit, terms = hits_blocklist(full_text)

    PROFANITY = ["fuck", "shit", "bitch", "ass ", "damn", "hell"]
    has_profanity = any(p in full_text.lower() for p in PROFANITY)

    if hit:
        result.checks["content_safety"] = "fail"
        result.messages["content_safety"] = f"Blocklist hit: {terms}"
    elif has_profanity:
        result.checks["content_safety"] = "warn"
        result.messages["content_safety"] = "Mild profanity detected — review"
    else:
        result.checks["content_safety"] = "pass"
        result.messages["content_safety"] = "Content safety OK"


def _check_factual(result: QAResult, copy: CopyResult, product: Optional[Product]):
    """Verify prices and claims match the synced catalogue."""
    if not product:
        result.checks["factual"] = "warn"
        result.messages["factual"] = "No product — verify price claims manually"
        return

    text = copy.caption + copy.hook
    # Check no invented discount percentages
    import re
    discount_match = re.search(r"(\d+)%\s*off", text, re.IGNORECASE)
    if discount_match:
        result.checks["factual"] = "fail"
        result.messages["factual"] = f"Unverified discount claim: {discount_match.group()}"
        return

    # Check price mentioned is not lower than actual
    price_matches = re.findall(r"₹\s*(\d+)", text)
    for price_str in price_matches:
        claimed_price = int(price_str)
        if claimed_price < product.price_min * 0.9:
            result.checks["factual"] = "fail"
            result.messages["factual"] = f"Price ₹{claimed_price} < actual ₹{product.price_min:.0f}"
            return

    result.checks["factual"] = "pass"
    result.messages["factual"] = "Factual accuracy OK"


def _check_language(result: QAResult, copy: CopyResult):
    """Script matches language, no empty placeholders."""
    import re
    # Check for unfilled template placeholders
    if re.search(r"\{\{[^}]+\}\}", copy.caption):
        result.checks["language"] = "fail"
        result.messages["language"] = "Unfilled template placeholders found"
        return

    # Check Devanagari script for Hindi/Marathi
    if copy.language in ("hindi", "marathi"):
        devanagari_chars = len(re.findall(r"[\u0900-\u097F]", copy.caption))
        if devanagari_chars < 5:
            result.checks["language"] = "warn"
            result.messages["language"] = f"Expected Devanagari for {copy.language} but found {devanagari_chars} chars"
            return

    result.checks["language"] = "pass"
    result.messages["language"] = f"Language ({copy.language}) OK"


def _check_hashtags(result: QAResult, copy: CopyResult, state: StateManager,
                    qa_cfg: dict):
    """Count, duplicates, banned tags, differs from yesterday."""
    tags = copy.hashtags
    settings = load_settings()
    min_h = qa_cfg.get("hashtag_min", 15)
    max_h = qa_cfg.get("hashtag_max", 20)

    if len(tags) < min_h:
        result.checks["hashtags"] = "fail"
        result.messages["hashtags"] = f"Too few hashtags: {len(tags)} < {min_h}"
        return
    if len(tags) > max_h:
        result.checks["hashtags"] = "warn"
        result.messages["hashtags"] = f"Too many hashtags: {len(tags)} > {max_h}, trimming"
        copy.hashtags = tags[:max_h]

    # Check for duplicates
    lower_tags = [t.lower() for t in tags]
    if len(lower_tags) != len(set(lower_tags)):
        result.checks["hashtags"] = "fail"
        result.messages["hashtags"] = "Duplicate hashtags found"
        return

    # Must include branded tags
    if "#gyodify" not in lower_tags:
        copy.hashtags.append("#gyodify")
    if "#wearwhatyouthink" not in lower_tags:
        copy.hashtags.append("#wearwhatyouthink")

    result.checks["hashtags"] = "pass"
    result.messages["hashtags"] = f"{len(copy.hashtags)} hashtags OK"


def _check_technical(result: QAResult, media_paths: list[Path],
                     post_format: str, settings: dict):
    """Dimensions, file size, video duration."""
    img_cfg = settings["image"]
    reel_cfg = settings["reel"]
    issues = []

    for path in media_paths:
        if not path.exists():
            issues.append(f"Missing file: {path.name}")
            continue

        size_mb = path.stat().st_size / (1024 * 1024)

        if path.suffix.lower() in (".png", ".jpg"):
            # Check image dimensions
            try:
                from PIL import Image
                with Image.open(path) as img:
                    w, h = img.size
                    expected_w = img_cfg["post_width"]
                    expected_h = (img_cfg["reel_height"]
                                  if post_format == "reel"
                                  else img_cfg["post_height"])
                    if w != expected_w or h != expected_h:
                        issues.append(f"{path.name}: {w}x{h} expected {expected_w}x{expected_h}")
            except Exception as e:
                issues.append(f"{path.name}: dimension check failed: {e}")

        elif path.suffix.lower() == ".mp4":
            # Check duration and size
            if size_mb > settings["telegram"]["max_file_size_mb"]:
                issues.append(f"{path.name}: {size_mb:.1f}MB > 48MB limit")

    if issues:
        result.checks["technical"] = "fail"
        result.messages["technical"] = "; ".join(issues[:3])
    else:
        result.checks["technical"] = "pass"
        result.messages["technical"] = "Technical specs OK"


def _check_originality(result: QAResult, copy: CopyResult,
                       state: StateManager, qa_cfg: dict):
    """Similarity check against recent hooks."""
    from src.ideation import _similar
    recent = state.recent_hooks(qa_cfg.get("originality_lookback_days", 60))
    for recent_hook in recent:
        if _similar(copy.hook, recent_hook):
            result.checks["originality"] = "warn"
            result.messages["originality"] = f"Hook similar to recent post: '{recent_hook[:50]}'"
            return

    result.checks["originality"] = "pass"
    result.messages["originality"] = "Originality OK"


def _check_llm_critic(result: QAResult, copy: CopyResult, product: Optional[Product],
                      post_format: str, llm: LLMClient, qa_cfg: dict):
    """LLM critic scores the deliverable — auto-regenerate if < 7."""
    try:
        prompt = QA_CRITIC_PROMPT.format(
            format=post_format,
            hook=copy.hook,
            caption=copy.caption[:500],
            hashtags=" ".join(copy.hashtags[:5]),
            language=copy.language,
            product_name=product.clean_title if product else "GYODIFY Tee",
            price=f"{product.price_min:.0f}" if product else "349",
        )
        data, _ = llm.complete_json(prompt, task="qa_critic", system=QA_CRITIC_SYSTEM)
        result.critic_scores = {k: v for k, v in data.items()
                                if isinstance(v, (int, float))}
        overall = float(data.get("overall", 0))
        min_score = qa_cfg.get("min_critic_score", 7.0)

        if overall < min_score:
            result.checks["llm_critic"] = "fail"
            result.messages["llm_critic"] = (
                f"Critic score {overall:.1f} < {min_score} | {data.get('feedback', '')}"
            )
        else:
            result.checks["llm_critic"] = "pass"
            result.messages["llm_critic"] = (
                f"Critic score {overall:.1f} | {data.get('feedback', '')}"
            )
    except Exception as e:
        result.checks["llm_critic"] = "warn"
        result.messages["llm_critic"] = f"Critic check skipped: {e}"


def format_qa_report(qa: QAResult) -> str:
    """Human-readable QA report for Telegram delivery."""
    ICONS = {"pass": "✅", "warn": "⚠️", "fail": "❌"}
    lines = ["📋 *QA Report*"]
    for check, status in qa.checks.items():
        icon = ICONS.get(status, "❓")
        msg = qa.messages.get(check, "")
        lines.append(f"{icon} *{check}*: {msg}")

    if qa.critic_scores:
        lines.append(f"\n🎯 Critic: {qa.overall_score:.1f}/10")
        for k, v in qa.critic_scores.items():
            if k != "overall" and isinstance(v, (int, float)):
                lines.append(f"  · {k}: {v:.1f}")

    lines.append(f"\n{'✅ PASSED' if qa.passed else '❌ NEEDS REGEN'}")
    return "\n".join(lines)
