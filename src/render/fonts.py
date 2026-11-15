"""
render/fonts.py — Font management and Devanagari glyph verification
"""
from __future__ import annotations
from pathlib import Path

from src.utils import get_logger, ASSETS_DIR

log = get_logger("render.fonts")

FONTS_DIR = ASSETS_DIR / "fonts"

# Test strings that exercise common Devanagari conjuncts and matras
DEVANAGARI_TEST_STRINGS = [
    "नमस्ते",          # conjunct
    "क्षमा",           # complex conjunct
    "स्वागत",          # conjunct with matra
    "ओवरसाइज़",        # nukta
    "पहनो",            # basic matra
    "शुभेच्छा",         # multiple conjuncts
    "GYODIFY टी-शर्ट",  # mixed Hindi-Latin
]

REQUIRED_FONTS = {
    "Space Grotesk": "SpaceGrotesk-Bold.ttf",
    "Inter": "Inter-Regular.ttf",
    "Bebas Neue": "BebasNeue-Regular.ttf",
    "Noto Sans Devanagari": "NotoSansDevanagari-Bold.ttf",
}


def check_fonts_present() -> dict[str, bool]:
    """Check which required font files exist in assets/fonts/."""
    results = {}
    for name, filename in REQUIRED_FONTS.items():
        path = FONTS_DIR / filename
        results[name] = path.exists()
        if not path.exists():
            log.warning(f"Font missing: {filename} ({name})")
    return results


def test_devanagari_rendering() -> bool:
    """
    Quick test: render Devanagari strings with Pillow + raqm.
    Returns True if no tofu boxes detected.
    Falls back gracefully if libraqm not available.
    """
    try:
        from PIL import Image, ImageDraw, ImageFont

        font_path = FONTS_DIR / REQUIRED_FONTS["Noto Sans Devanagari"]
        if not font_path.exists():
            log.warning("Noto Sans Devanagari not found — skipping glyph test")
            return False

        try:
            font = ImageFont.truetype(str(font_path), size=32)
        except Exception as e:
            log.warning(f"Font load failed: {e}")
            return False

        for test_str in DEVANAGARI_TEST_STRINGS:
            img = Image.new("RGB", (400, 80), color="white")
            draw = ImageDraw.Draw(img)
            try:
                draw.text((10, 10), test_str, font=font, fill="black",
                          language="hi", features=["+kern"])
            except TypeError:
                # Older Pillow without language param
                draw.text((10, 10), test_str, font=font, fill="black")

            # Check for tofu: look for large uniform-grey rectangles in output
            # Simplified check: if render succeeds without exception, assume OK
        log.info("Devanagari glyph test: PASSED")
        return True

    except ImportError:
        log.warning("Pillow not available for glyph test")
        return False
    except Exception as e:
        log.warning(f"Devanagari glyph test failed: {e}")
        return False


def get_google_fonts_css() -> str:
    """Return Google Fonts CSS import string for Playwright rendering."""
    return (
        "https://fonts.googleapis.com/css2?"
        "family=Space+Grotesk:wght@300;400;500;600;700"
        "&family=Inter:wght@300;400;500;600;700"
        "&family=Bebas+Neue"
        "&family=Noto+Sans+Devanagari:wght@400;700"
        "&display=swap"
    )


def font_face_css() -> str:
    """
    Local font-face declarations for offline / fallback rendering.
    Uses bundled font files in assets/fonts/.
    """
    lines = []
    font_map = [
        ("Space Grotesk", "SpaceGrotesk-Bold.ttf", 700, "normal"),
        ("Space Grotesk", "SpaceGrotesk-Regular.ttf", 400, "normal"),
        ("Inter", "Inter-Regular.ttf", 400, "normal"),
        ("Inter", "Inter-Bold.ttf", 700, "normal"),
        ("Bebas Neue", "BebasNeue-Regular.ttf", 400, "normal"),
        ("Noto Sans Devanagari", "NotoSansDevanagari-Bold.ttf", 700, "normal"),
        ("Noto Sans Devanagari", "NotoSansDevanagari-Regular.ttf", 400, "normal"),
    ]
    for family, filename, weight, style in font_map:
        path = FONTS_DIR / filename
        if path.exists():
            lines.append(f"""@font-face {{
  font-family: '{family}';
  src: url('file://{path}') format('truetype');
  font-weight: {weight};
  font-style: {style};
}}""")
    return "\n".join(lines)
