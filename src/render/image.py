"""
render/image.py — HTML/CSS template → 1080x1350 PNG via Playwright
Falls back to Pillow if Chromium unavailable.
"""
from __future__ import annotations
import base64
import json
from pathlib import Path
from typing import Optional

from src.utils import get_logger, load_brand, load_settings, ASSETS_DIR
from src.render.fonts import get_google_fonts_css, font_face_css

log = get_logger("render.image")

TEMPLATES_DIR = Path(__file__).parent.parent.parent / "templates"


def _logo_base64(dark: bool = True) -> str:
    """Return base64-encoded logo for embedding in HTML."""
    key = "primary" if dark else "white"
    brand = load_brand()
    logo_path = Path(brand["logo"][key])
    if not logo_path.is_absolute():
        logo_path = Path(__file__).parent.parent.parent / logo_path
    if logo_path.exists():
        with open(logo_path, "rb") as f:
            return base64.b64encode(f.read()).decode()
    return ""


def _product_image_base64(image_path: Optional[str]) -> str:
    """Embed product image as base64 for Playwright rendering."""
    if not image_path or not Path(image_path).exists():
        return ""
    with open(image_path, "rb") as f:
        data = base64.b64encode(f.read()).decode()
    ext = Path(image_path).suffix.lower().lstrip(".")
    mime = {"jpg": "jpeg", "jpeg": "jpeg", "png": "png", "webp": "webp"}.get(ext, "jpeg")
    return f"data:image/{mime};base64,{data}"


def render_post(
    template_name: str,
    context: dict,
    output_path: Path,
    width: int = 1080,
    height: int = 1350,
) -> bool:
    """
    Render an HTML template to PNG.
    Tries Playwright first, falls back to Pillow.
    Returns True on success.
    """
    html = _build_html(template_name, context, width, height)
    if not html:
        log.error(f"Failed to build HTML for template: {template_name}")
        return False

    # Save HTML for debugging
    html_debug = output_path.with_suffix(".html")
    with open(html_debug, "w", encoding="utf-8") as f:
        f.write(html)

    success = _render_playwright(html, output_path, width, height)
    if not success:
        log.warning("Playwright failed — trying Pillow fallback")
        success = _render_pillow_fallback(context, output_path, width, height)

    return success


def _build_html(template_name: str, context: dict,
                width: int, height: int) -> Optional[str]:
    """Load HTML template and inject context data."""
    template_path = TEMPLATES_DIR / f"{template_name}.html"
    if not template_path.exists():
        log.error(f"Template not found: {template_path}")
        return None

    with open(template_path, "r", encoding="utf-8") as f:
        template = f.read()

    # Inject context as JSON + font CSS
    brand = load_brand()
    pos = context.get("position", 1)
    palette = brand["feed_positions"].get(pos, brand["feed_positions"][1])

    logo_dark = _logo_base64(dark=True)
    logo_white = _logo_base64(dark=False)
    product_img = _product_image_base64(context.get("product_image_path"))

    # Build injection script
    inject = f"""
<script>
window.GYODIFY = {json.dumps({
    "palette": palette,
    "context": context,
    "brand": {
        "name": brand["brand"]["name"],
        "tagline": brand["brand"]["tagline"],
        "url": brand["brand"]["url"],
    },
    "logos": {{"dark": logo_dark, "white": logo_white}},
    "productImage": product_img,
})};
</script>
"""
    # Inject Google Fonts + local fallback
    font_css = f"""
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="{get_google_fonts_css()}" rel="stylesheet">
<style>{font_face_css()}</style>
"""
    html = template.replace("<!--GYODIFY_INJECT-->", inject + font_css)
    return html


def _render_playwright(html: str, output_path: Path,
                       width: int, height: int) -> bool:
    """Render HTML to PNG using Playwright (headless Chromium)."""
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            browser = p.chromium.launch(args=["--no-sandbox", "--disable-dev-shm-usage"])
            context = browser.new_context(
                viewport={"width": width, "height": height},
                device_scale_factor=load_settings()["image"]["device_scale_factor"],
            )
            page = context.new_page()
            page.set_content(html, wait_until="networkidle", timeout=30000)
            # Wait for fonts to load
            page.wait_for_timeout(1500)
            page.screenshot(
                path=str(output_path),
                full_page=False,
                clip={"x": 0, "y": 0, "width": width, "height": height},
            )
            browser.close()

        log.info(f"Playwright rendered: {output_path.name} ({output_path.stat().st_size // 1024}KB)")
        return True

    except Exception as e:
        log.error(f"Playwright render failed: {e}")
        return False


def _render_pillow_fallback(context: dict, output_path: Path,
                             width: int, height: int) -> bool:
    """
    Minimal Pillow fallback — renders a clean branded card
    when Playwright is unavailable.
    """
    try:
        from PIL import Image, ImageDraw, ImageFont
        from src.render.fonts import FONTS_DIR

        brand = load_brand()
        pos = context.get("position", 1)
        palette = brand["feed_positions"].get(pos, brand["feed_positions"][1])

        img = Image.new("RGB", (width, height), color=palette["bg"])
        draw = ImageDraw.Draw(img)

        # Load fonts with fallback
        def load_font(filename, size):
            path = FONTS_DIR / filename
            try:
                return ImageFont.truetype(str(path), size)
            except Exception:
                return ImageFont.load_default()

        font_headline = load_font("SpaceGrotesk-Bold.ttf", 72)
        font_body = load_font("Inter-Regular.ttf", 36)
        font_small = load_font("Inter-Regular.ttf", 28)

        # Oxblood accent bar at top
        draw.rectangle([(0, 0), (width, 12)], fill=palette["accent"])

        # Brand name
        draw.text((60, 60), "GYODIFY", font=font_headline, fill=palette["accent"])

        # Hook text (wrapped)
        hook = context.get("hook", brand["brand"]["tagline"])
        _draw_wrapped(draw, hook, font_headline, palette["fg"],
                      x=60, y=200, max_width=width - 120)

        # Caption preview
        body = context.get("caption", "")[:200]
        _draw_wrapped(draw, body, font_body, palette["fg"],
                      x=60, y=height - 300, max_width=width - 120)

        # Price + URL bar at bottom
        draw.rectangle([(0, height - 100), (width, height)], fill=palette["accent"])
        price_text = f"From ₹{context.get('price_min', 349):.0f} · Free shipping · gyodify.shop"
        draw.text((60, height - 72), price_text, font=font_small, fill="#FFFFFF")

        img.save(str(output_path), format="PNG", optimize=True)
        log.info(f"Pillow fallback rendered: {output_path.name}")
        return True

    except Exception as e:
        log.error(f"Pillow fallback failed: {e}")
        return False


def _draw_wrapped(draw, text: str, font, fill,
                  x: int, y: int, max_width: int, line_height: int = 90):
    """Draw word-wrapped text."""
    words = text.split()
    lines = []
    current = []
    for word in words:
        test_line = " ".join(current + [word])
        try:
            bbox = draw.textbbox((0, 0), test_line, font=font)
            w = bbox[2] - bbox[0]
        except AttributeError:
            w, _ = draw.textsize(test_line, font=font)
        if w <= max_width:
            current.append(word)
        else:
            if current:
                lines.append(" ".join(current))
            current = [word]
    if current:
        lines.append(" ".join(current))

    for i, line in enumerate(lines[:6]):
        draw.text((x, y + i * line_height), line, font=font, fill=fill)
