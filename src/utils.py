"""
utils.py — Logging, config loader, retry helpers
"""
import os
import json
import logging
import functools
from pathlib import Path
from typing import Any

import yaml
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

# ── Root paths ──────────────────────────────────────────────
ROOT = Path(__file__).parent.parent
CONFIG_DIR = ROOT / "config"
ASSETS_DIR = ROOT / "assets"
DATA_DIR = ROOT / "data"
OUTPUT_DIR = ROOT / "output"


# ── Logging ─────────────────────────────────────────────────
def get_logger(name: str) -> logging.Logger:
    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = logging.StreamHandler()
        fmt = logging.Formatter(
            "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            datefmt="%H:%M:%S"
        )
        handler.setFormatter(fmt)
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
    return logger

log = get_logger("gyodify")


# ── Config loader ────────────────────────────────────────────
_config_cache: dict[str, Any] = {}

def load_config(filename: str) -> dict:
    """Load a YAML config file from config/. Cached after first load."""
    if filename in _config_cache:
        return _config_cache[filename]
    path = CONFIG_DIR / filename
    if not path.exists():
        raise FileNotFoundError(f"Config not found: {path}")
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    _config_cache[filename] = data
    return data


def load_brand() -> dict:
    return load_config("brand.yaml")

def load_settings() -> dict:
    return load_config("settings.yaml")

def load_blocklist() -> dict:
    return load_config("blocklist.yaml")

def load_hashtags() -> dict:
    return load_config("hashtags.yaml")

def load_voice_examples() -> dict:
    return load_config("voice_examples.yaml")

def load_festivals() -> dict:
    return load_config("festivals.yaml")

def load_themes() -> dict:
    return load_config("themes.yaml")

def load_sources() -> dict:
    return load_config("sources.yaml")


# ── History / state ──────────────────────────────────────────
HISTORY_FILE = DATA_DIR / "history.json"

def load_history() -> dict:
    if HISTORY_FILE.exists():
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"current_position": 1, "last_run": None, "posts": [], "feedback": []}

def save_history(history: dict) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(history, f, indent=2, ensure_ascii=False)


# ── Output dir ───────────────────────────────────────────────
def get_output_dir(date_str: str) -> Path:
    out = OUTPUT_DIR / date_str
    out.mkdir(parents=True, exist_ok=True)
    return out


# ── Env helpers ──────────────────────────────────────────────
def require_env(key: str) -> str:
    val = os.environ.get(key)
    if not val:
        raise EnvironmentError(f"Required env var not set: {key}")
    return val

def optional_env(key: str, default: str = "") -> str:
    return os.environ.get(key, default)


# ── Retry decorator ──────────────────────────────────────────
def retry_on_rate_limit(max_attempts: int = 3):
    """Decorator: exponential backoff on 429 / transient errors."""
    return retry(
        stop=stop_after_attempt(max_attempts),
        wait=wait_exponential(multiplier=2, min=4, max=30),
        retry=retry_if_exception_type((Exception,)),
        reraise=True,
    )


# ── Blocklist checker ────────────────────────────────────────
def hits_blocklist(text: str) -> tuple[bool, list[str]]:
    """Return (hit, [matched terms]) for a given string."""
    bl = load_blocklist()
    text_lower = text.lower()
    hits = []
    for category in ["blocked_brands", "blocked_topics", "blocked_hashtags",
                      "licensed_graphic_patterns"]:
        for term in bl.get(category, []):
            if term.lower() in text_lower:
                hits.append(term)
    return bool(hits), hits


# ── Sanitise LLM output ──────────────────────────────────────
def safe_text(raw: str) -> str:
    """Strip any HTML/JS from LLM output before rendering into templates."""
    import html
    return html.escape(raw, quote=False)


# ── File size check ──────────────────────────────────────────
def file_size_mb(path: Path) -> float:
    return path.stat().st_size / (1024 * 1024)
