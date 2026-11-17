"""
render/tts.py — Text-to-speech voiceover
Chain: edge-tts → gTTS → silent (captions only)
"""
from __future__ import annotations
import asyncio
import re
from pathlib import Path
from typing import Optional

from src.utils import get_logger

log = get_logger("render.tts")

VOICE_MAP = {
    "english":  "en-IN-NeerjaNeural",
    "hinglish": "en-IN-NeerjaNeural",
    "hindi":    "hi-IN-SwaraNeural",
    "marathi":  "mr-IN-AarohiNeural",
}

GTTS_LANG_MAP = {
    "english": "en", "hinglish": "en", "hindi": "hi", "marathi": "mr",
}


def sanitise_for_tts(text: str, language: str) -> str:
    """Clean text for TTS — remove emojis, hashtags, URLs, special chars."""
    text = re.sub(r"http\S+", "", text)
    text = re.sub(r"#\w+", "", text)
    text = re.sub(r"@\w+", "", text)
    # Remove emojis
    text = re.sub(r"[^\w\s\u0900-\u097F.,!?।\-₹]", "", text)
    text = re.sub(r"\s+", " ", text).strip()

    # For Hinglish using en-IN voice: keep Roman script
    if language == "hinglish":
        # Remove Devanagari if any leaked in
        text = re.sub(r"[\u0900-\u097F]+", "", text)
        text = re.sub(r"\s+", " ", text).strip()

    return text


async def _edge_tts_generate(text: str, voice: str, output_path: Path) -> bool:
    try:
        import edge_tts
        communicate = edge_tts.Communicate(text, voice)
        await communicate.save(str(output_path))
        log.info(f"edge-tts generated: {output_path.name}")
        return True
    except Exception as e:
        log.warning(f"edge-tts failed: {e}")
        return False


def _gtts_generate(text: str, language: str, output_path: Path) -> bool:
    try:
        from gtts import gTTS
        lang = GTTS_LANG_MAP.get(language, "en")
        tts = gTTS(text=text, lang=lang, slow=False)
        tts.save(str(output_path))
        log.info(f"gTTS generated: {output_path.name}")
        return True
    except Exception as e:
        log.warning(f"gTTS failed: {e}")
        return False


def generate_voiceover(
    text: str,
    language: str,
    output_path: Path,
) -> tuple[bool, str]:
    """
    Generate voiceover MP3. Returns (success, method_used).
    method_used: 'edge_tts' | 'gtts' | 'silent'
    """
    if not text or not text.strip():
        log.info("No voiceover text — using silent mode")
        return False, "silent"

    clean_text = sanitise_for_tts(text, language)
    if not clean_text:
        return False, "silent"

    voice = VOICE_MAP.get(language, "en-IN-NeerjaNeural")

    # Try edge-tts
    mp3_path = output_path.with_suffix(".mp3")
    success = asyncio.run(_edge_tts_generate(clean_text, voice, mp3_path))
    if success:
        return True, "edge_tts"

    # Try gTTS
    success = _gtts_generate(clean_text, language, mp3_path)
    if success:
        return True, "gtts"

    log.warning("All TTS providers failed — reel will be captions only")
    return False, "silent"
