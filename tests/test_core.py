"""
Core tests — run with: pytest tests/ -v
"""
import json
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock


# ── Utils ──────────────────────────────────────────────────
def test_hits_blocklist_basic():
    from src.utils import hits_blocklist
    hit, terms = hits_blocklist("I love Marvel characters on my tee")
    assert hit is True
    assert "marvel" in [t.lower() for t in terms]

def test_hits_blocklist_clean():
    from src.utils import hits_blocklist
    hit, _ = hits_blocklist("Oversized graphic tee, free shipping India")
    assert hit is False

def test_safe_text_escapes_html():
    from src.utils import safe_text
    result = safe_text("<script>alert('xss')</script>")
    assert "<script>" not in result


# ── State ──────────────────────────────────────────────────
def test_state_position_cycle(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    from src.state import StateManager, SEQUENCE, POSITION_FORMAT
    state = StateManager()
    state.history["current_position"] = 1
    assert state.current_format == "carousel"
    assert state.next_position() == 2

def test_state_position_wraps():
    from src.state import StateManager
    state = StateManager()
    state.history["current_position"] = 6
    assert state.next_position() == 1

def test_state_language_rotation():
    from src.state import StateManager
    state = StateManager()
    state.history["posts"] = [
        {"language": "hinglish"},
        {"language": "hinglish"},
    ]
    # Should not pick hinglish again (streak = 2 = max)
    picked = state.pick_language()
    assert picked != "hinglish"


# ── Shopify ────────────────────────────────────────────────
def test_kids_filter_excludes():
    from src.shopify import _is_kids_product
    assert _is_kids_product({"title": "Kids Hoodie 3-4 Yrs", "tags": [], "handle": "kids-hoodie"})

def test_kids_filter_baby_tee_whitelisted():
    from src.shopify import _is_kids_product
    # Baby Tee is women's style — must NOT be excluded
    assert not _is_kids_product({"title": "Baby Tee - White", "tags": [], "handle": "baby-tee-white"})

def test_kids_filter_allows_normal():
    from src.shopify import _is_kids_product
    assert not _is_kids_product({"title": "Oversized Graphic Tee", "tags": ["streetwear"], "handle": "oversized-tee"})


# ── Hashtags ───────────────────────────────────────────────
def test_hashtag_set_includes_branded():
    from src.copywriter import _build_hashtag_set
    tags = _build_hashtag_set("english")
    lower = [t.lower() for t in tags]
    assert "#gyodify" in lower
    assert "#wearwhatyouthink" in lower

def test_hashtag_set_count_in_range():
    from src.copywriter import _build_hashtag_set
    from src.utils import load_settings
    tags = _build_hashtag_set("hinglish")
    cfg = load_settings()["hashtags"]
    assert cfg["total_count"] - 2 <= len(tags) <= cfg["total_count"] + 2

def test_hashtag_no_duplicates():
    from src.copywriter import _build_hashtag_set
    tags = _build_hashtag_set("marathi")
    lower = [t.lower() for t in tags]
    assert len(lower) == len(set(lower))


# ── Caption validation ─────────────────────────────────────
def test_caption_no_placeholder_text():
    from src.qa import _check_content_safety
    from src.copywriter import CopyResult
    copy = CopyResult(
        hook="Loud tees. Quiet prices.",
        caption="{{hook}} is unfilled placeholder",
        cta="Link in bio",
        hashtags=["#gyodify"],
        alt_text="",
        language="english",
        provider="test",
    )
    # language check should catch {{...}}
    from src.qa import QAResult
    result = QAResult(passed=True)
    from src.qa import _check_language
    _check_language(result, copy)
    assert result.checks.get("language") == "fail"

def test_no_blocklist_in_caption():
    from src.qa import _check_content_safety, QAResult
    from src.copywriter import CopyResult
    copy = CopyResult(
        hook="Check this Marvel tee",
        caption="Featuring Marvel's Spider-Man in our latest drop",
        cta="Shop now",
        hashtags=["#gyodify"],
        alt_text="",
        language="english",
        provider="test",
    )
    result = QAResult(passed=True)
    _check_content_safety(result, copy)
    assert result.checks.get("content_safety") == "fail"


# ── LLM JSON repair ────────────────────────────────────────
def test_llm_response_json_parsing():
    from src.llm import LLMResponse
    resp = LLMResponse(text='```json\n{"key": "value"}\n```', provider="test")
    data = resp.as_json()
    assert data["key"] == "value"

def test_llm_response_json_no_fence():
    from src.llm import LLMResponse
    resp = LLMResponse(text='{"score": 8.5}', provider="test")
    data = resp.as_json()
    assert data["score"] == 8.5


# ── QA gate ────────────────────────────────────────────────
def test_qa_factual_no_fake_discount():
    from src.qa import _check_factual, QAResult
    from src.copywriter import CopyResult
    from src.shopify import Product, ProductVariant
    copy = CopyResult(
        hook="50% off everything!", caption="FLAT 50% off all tees today only!",
        cta="", hashtags=[], alt_text="", language="english", provider="test",
    )
    prod = Product(
        id=1, handle="test-tee", title="Test Tee", product_type="Tee",
        tags=[], url="https://gyodify.shop/products/test-tee",
        image_urls=[], price_min=349.0, price_max=449.0,
        variants=[ProductVariant(id=1, title="S", price="349", available=True)],
        available=True,
    )
    result = QAResult(passed=True)
    _check_factual(result, copy, prod)
    assert result.checks.get("factual") == "fail"


# ── Calendar ───────────────────────────────────────────────
def test_festival_in_lead_window():
    from src.research.calendar import fetch_calendar_signals
    from datetime import date
    # Provide a date where Diwali 2027-10-29 is 7 days away
    test_date = date(2027, 10, 22)
    signals = fetch_calendar_signals(today=test_date)
    festival_names = [s.extra.get("festival_name") for s in signals if s.category == "festival"]
    assert "Diwali" in festival_names

def test_no_festival_outside_window():
    from src.research.calendar import fetch_calendar_signals
    from datetime import date
    # Far from any festival
    test_date = date(2027, 7, 1)
    signals = fetch_calendar_signals(today=test_date)
    # Should have season signals but not Diwali
    diwali = [s for s in signals if s.extra.get("festival_name") == "Diwali"]
    assert len(diwali) == 0


# ── Telegram payload ───────────────────────────────────────
def test_feedback_keyboard_structure():
    from src.telegram_bot import _build_feedback_keyboard
    kb = _build_feedback_keyboard("2026-11-14")
    assert "inline_keyboard" in kb
    buttons = kb["inline_keyboard"][0]
    assert len(buttons) == 4
    types = [b["callback_data"].split("|")[1] for b in buttons]
    assert "posted_well" in types
    assert "regenerate" in types

def test_format_qa_report():
    from src.qa import QAResult, format_qa_report
    qa = QAResult(
        passed=True,
        checks={"palette": "pass", "content_safety": "pass"},
        messages={"palette": "OK", "content_safety": "OK"},
        overall_score=8.4,
    )
    report = format_qa_report(qa)
    assert "✅" in report
    assert "PASSED" in report
