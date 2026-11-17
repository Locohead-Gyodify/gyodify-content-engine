"""
shopify.py — Shopify catalogue sync, image cache, kids filter
"""
from __future__ import annotations
import json
import re
import time
from pathlib import Path
from typing import Optional

import requests
from pydantic import BaseModel, field_validator

from src.utils import get_logger, load_settings, ROOT, load_history, save_history

log = get_logger("shopify")

CACHE_DIR = ROOT / ".cache" / "products"
STORE_URL = "https://gyodify.shop"

KIDS_PATTERNS = re.compile(
    r"\b(kids?|boys?|girls?|baby|toddler|infant|"
    r"[0-9]+-[0-9]+m\b|[0-9]+\s*yrs?|children)\b",
    re.IGNORECASE,
)
# "Baby Tee" is a women's cropped-fit — keep it
BABY_TEE_WHITELIST = re.compile(r"baby[\s-]tee", re.IGNORECASE)


class ProductVariant(BaseModel):
    id: int
    title: str
    price: str
    available: bool


class Product(BaseModel):
    id: int
    handle: str
    title: str
    clean_title: str = ""
    product_type: str
    tags: list[str]
    url: str
    image_urls: list[str]
    local_image_path: Optional[str] = None
    price_min: float
    price_max: float
    variants: list[ProductVariant]
    available: bool
    is_new: bool = False
    licensed_flag: bool = False

    @field_validator("clean_title", mode="before")
    @classmethod
    def strip_seo_suffix(cls, v, info):
        # Strip everything after | in title (SEO suffixes)
        raw = info.data.get("title", v or "")
        return raw.split("|")[0].strip()


def _is_kids_product(product: dict) -> bool:
    """Return True if the product should be excluded (kids)."""
    title = product.get("title", "")
    tags = " ".join(product.get("tags", []))
    combined = f"{title} {tags}"

    # Whitelist: baby tee is women's style
    if BABY_TEE_WHITELIST.search(title):
        return False

    # Check collection (handle contains 'kids')
    handle = product.get("handle", "")
    if "kids" in handle or "children" in handle:
        return True

    return bool(KIDS_PATTERNS.search(combined))


def _parse_product(raw: dict, known_handles: set) -> Optional[Product]:
    """Parse a raw Shopify product dict into a Product model."""
    settings = load_settings()

    if settings["products"]["exclude_kids"] and _is_kids_product(raw):
        log.debug(f"Skipping kids product: {raw.get('title')}")
        return None

    variants = raw.get("variants", [])
    if not variants:
        return None

    prices = [float(v.get("price", 0)) for v in variants]
    image_urls = [
        f"{img.get('src', '')}?width={settings['products']['image_width']}"
        for img in raw.get("images", [])
        if img.get("src")
    ]

    handle = raw.get("handle", "")
    is_new = handle not in known_handles

    # Check for licensed graphics
    from src.utils import hits_blocklist
    title_tags = raw.get("title", "") + " ".join(raw.get("tags", []))
    licensed, _ = hits_blocklist(title_tags)

    return Product(
        id=raw["id"],
        handle=handle,
        title=raw.get("title", ""),
        clean_title=raw.get("title", ""),
        product_type=raw.get("product_type", ""),
        tags=raw.get("tags", []),
        url=f"{STORE_URL}/products/{handle}",
        image_urls=image_urls,
        price_min=min(prices) if prices else 0,
        price_max=max(prices) if prices else 0,
        variants=[
            ProductVariant(
                id=v["id"],
                title=v.get("title", ""),
                price=v.get("price", "0"),
                available=v.get("available", False),
            )
            for v in variants
        ],
        available=any(v.get("available", False) for v in variants),
        is_new=is_new,
        licensed_flag=licensed,
    )


def fetch_catalogue() -> list[Product]:
    """Fetch all products from Shopify public API, paginated."""
    settings = load_settings()
    delay = settings["products"]["request_delay_seconds"]
    max_pages = settings["products"]["max_pages"]

    # Load known handles from history for new-drop detection
    history = load_history()
    known_handles = set(
        p.get("product") for p in history.get("posts", []) if p.get("product")
    )

    products = []
    page = 1
    while page <= max_pages:
        url = f"{STORE_URL}/products.json?limit=250&page={page}"
        try:
            resp = requests.get(
                url,
                headers={"User-Agent": "GYODIFY-ContentEngine/1.0"},
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json().get("products", [])
            if not data:
                break
            for raw in data:
                prod = _parse_product(raw, known_handles)
                if prod:
                    products.append(prod)
            log.info(f"Shopify page {page}: {len(data)} raw → {len(products)} total kept")
            page += 1
            time.sleep(delay)
        except requests.RequestException as e:
            log.error(f"Shopify fetch error page {page}: {e}")
            break

    # Cache product list
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_file = CACHE_DIR / "products_cache.json"
    cached = [p.model_dump() for p in products]
    with open(cache_file, "w", encoding="utf-8") as f:
        json.dump(cached, f, indent=2, ensure_ascii=False)

    log.info(f"Shopify sync complete: {len(products)} products")
    return products


def download_product_image(product: Product) -> Optional[str]:
    """Download first product image to cache. Returns local path."""
    if not product.image_urls:
        return None

    img_dir = CACHE_DIR / "images"
    img_dir.mkdir(parents=True, exist_ok=True)
    local_path = img_dir / f"{product.handle}.jpg"

    if local_path.exists():
        return str(local_path)

    url = product.image_urls[0]
    try:
        resp = requests.get(url, timeout=20, stream=True)
        resp.raise_for_status()
        with open(local_path, "wb") as f:
            for chunk in resp.iter_content(8192):
                f.write(chunk)
        log.info(f"Downloaded image: {product.handle}")
        return str(local_path)
    except Exception as e:
        log.warning(f"Image download failed for {product.handle}: {e}")
        return None


def select_products_for_post(
    products: list[Product],
    state,
    count: int = 3,
) -> list[Product]:
    """
    Pick products to feature, weighted by:
    - New drops (highest priority)
    - Not recently used
    - Available
    - Not licensed-flagged
    """
    available = [p for p in products if p.available and not p.licensed_flag]
    if not available:
        available = [p for p in products if p.available]

    # Score each product
    scored = []
    for p in available:
        score = 0
        if p.is_new:
            score += 10
        if not state.recently_used_product(p.handle):
            score += 5
        if p.price_min <= 449:
            score += 2  # highlight affordable range
        scored.append((score, p))

    scored.sort(key=lambda x: x[0], reverse=True)
    selected = [p for _, p in scored[:count]]

    # Flag licensed products in Telegram note
    for p in selected:
        if p.licensed_flag:
            log.warning(f"Licensed-looking product selected: {p.title} — flag for review")

    return selected


def load_cached_catalogue() -> list[Product]:
    """Load from cache when network is unavailable."""
    cache_file = CACHE_DIR / "products_cache.json"
    if not cache_file.exists():
        return []
    with open(cache_file, "r", encoding="utf-8") as f:
        data = json.load(f)
    return [Product(**d) for d in data]
