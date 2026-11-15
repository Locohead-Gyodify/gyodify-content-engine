"""
render/carousel.py — Multi-slide carousel renderer
Renders each slide as a 1080x1350 PNG, returns list of paths
"""
from __future__ import annotations
from pathlib import Path
from typing import Optional

from src.utils import get_logger, load_brand, load_settings
from src.render.image import render_post
from src.copywriter import CopyResult

log = get_logger("render.carousel")


def _build_slide_context(
    slide_num: int,
    total_slides: int,
    slide_data: dict,
    base_context: dict,
    is_hook: bool = False,
    is_cta: bool = False,
) -> dict:
    return {
        **base_context,
        "slide_num": slide_num,
        "total_slides": total_slides,
        "slide_headline": slide_data.get("headline", ""),
        "slide_body": slide_data.get("body", ""),
        "is_hook_slide": is_hook,
        "is_cta_slide": is_cta,
        "swipe_cue": "Swipe →" if not is_cta else "",
    }


def render_carousel(
    copy: CopyResult,
    base_context: dict,
    output_dir: Path,
    template_name: str,
) -> list[Path]:
    """
    Render all carousel slides. Returns list of PNG paths.
    Slide 1 = hook, Last slide = CTA.
    """
    settings = load_settings()
    brand = load_brand()

    slides_data = copy.carousel_slides
    if not slides_data:
        # Build default slides from caption if LLM didn't return structured slides
        slides_data = _build_default_slides(copy)

    # Clamp to allowed range
    min_s = settings["carousel"]["min_slides"]
    max_s = settings["carousel"]["max_slides"]
    slides_data = slides_data[:max_s]
    if len(slides_data) < min_s:
        slides_data = _pad_slides(slides_data, min_s)

    total = len(slides_data)
    slide_paths = []

    for i, slide in enumerate(slides_data, start=1):
        is_hook = i == 1
        is_cta = i == total

        ctx = _build_slide_context(
            slide_num=i,
            total_slides=total,
            slide_data=slide,
            base_context=base_context,
            is_hook=is_hook,
            is_cta=is_cta,
        )

        out_path = output_dir / f"carousel_slide_{i:02d}.png"
        success = render_post(
            template_name=template_name,
            context=ctx,
            output_path=out_path,
        )

        if success:
            slide_paths.append(out_path)
            log.info(f"Carousel slide {i}/{total} rendered: {out_path.name}")
        else:
            log.error(f"Carousel slide {i} render failed")

    return slide_paths


def _build_default_slides(copy: CopyResult) -> list[dict]:
    """Build slide data from copy when LLM didn't return structured slides."""
    sentences = [s.strip() for s in copy.caption.replace("\n", " ").split(".") if s.strip()]
    slides = [{"headline": copy.hook, "body": ""}]  # slide 1: hook

    # Middle slides
    for i, sentence in enumerate(sentences[:4]):
        slides.append({"headline": "", "body": sentence + "."})

    # Last slide: CTA
    slides.append({
        "headline": "Shop now",
        "body": f"Link in bio · gyodify.shop\n{copy.cta}",
    })
    return slides


def _pad_slides(slides: list[dict], target: int) -> list[dict]:
    """Pad slide list to minimum count with empty slides."""
    while len(slides) < target:
        slides.insert(-1, {"headline": "", "body": ""})
    return slides
