#!/usr/bin/env python3
"""
Kaufland.sk weekly-offers scraper  —  v2
=========================================
Strategy (fastest first, DOM fallback last):

  1. Schwarz leaflets API  — same service server.py already uses for metadata;
                             probe for a /products or /offers sub-endpoint.
  2. Network interception  — load the offers page in a headless browser,
                             capture every JSON response, pick the one that
                             looks like a product feed (duck-typed).
  3. DOM scraping fallback — if no API response is usable, parse the rendered
                             HTML with smarter selectors + heuristics.

Output
------
  kaufland_products.json  — list of product dicts in the same schema used by
                            server.py for Lidl/Billa, so they can be fed into
                            /api/all without any transformation.

Category normalisation
----------------------
  Reuses normalize_category() from server.py — no duplicate keyword tables.
"""

import json
import os
import re
import sys
import urllib.request
import urllib.error
from datetime import date, timedelta

# ── reuse server.py helpers ───────────────────────────────────────────────────
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    from server import normalize_category, normalize_type, get_kaufland_slug, KAUFLAND_REGION
    print("✓  Imported normalize_category / normalize_type + slug helper from server.py")
except ImportError:
    print("⚠  server.py not found — using stub normalize_category / normalize_type")
    KAUFLAND_REGION = 8620

    def get_kaufland_slug():
        today = date.today()
        days_since_thu = (today.weekday() - 3) % 7
        last_thu = today - timedelta(days=days_since_thu)
        iso_week = last_thu.isocalendar()[1]
        return f"SK_sk_KDZ_{KAUFLAND_REGION}_SK{iso_week:02d}-LFT"

    def normalize_category(raw, name=""):
        return "Ostatné"

    def normalize_type(raw, name=""):
        return ""

OUTPUT_FILE  = "kaufland_products.json"
# Primary: weekly flyer viewer.  Secondary: structured deals catalog.
OFFER_URL     = "https://predajne.kaufland.sk/aktualna-ponuka/prehlad.html"
OFFER_URL_ALT = "https://www.kaufland.sk/akcie/"

SCHWARZ_HEADERS = {
    "User-Agent":  "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer":     "https://kaufland.leaflets.schwarz/",
    "Origin":      "https://kaufland.leaflets.schwarz",
    "Accept":      "application/json",
}

UNIT_RE = re.compile(r'(\d+[.,]?\d*\s*(?:g|kg|l|ml|cl|ks|bal\.?|por\.?|kus))', re.I)

# Properly formatted shelf price: 1–3 digits + comma/dot + EXACTLY 2 decimal digits.
# This intentionally excludes plain integers (40, 50 = wash count) and
# per-unit amounts written without 2 decimals.
PRICE_RE = re.compile(r'(?<![=\d])(\d{1,3}[,.]\d{2})(?!\d)')

# Lines/text that signal promotional noise — skip when extracting product name
_JUNK_LINE = re.compile(
    r'mimoriadna|ponuka!|^\s*kaufland\s*$|akcia\s|leták|týždeň|platí|'
    r'park side|liv &|spice &|pultový|economy pack|powerful|dissolving',
    re.I,
)

# Lines that contain quantity/unit info — stop name collection here
_UNIT_LINE = re.compile(
    r'praní|prani|kúpanie|umývani|použití|opláchnut|'
    r'\bks\b|\bml\b|\bkg\b|\bl\b|\bg\b|'
    r'por\.|bal\.|baleni|litr|gram|\(=',
    re.I,
)

# Per-unit price context that must be ignored completely, e.g. "(=1 0,22)"
_PER_UNIT = re.compile(r'=\s*1\b|\(=|\(1\s*[,.]\d')

# Slovak/German/English promotional filler words that appear as price labels,
# NOT as product names. Checked as exact (stripped, lowercased) line match.
_FILLER_WORDS = {
    'iba', 'len', 'just', 'only', 'nur',          # "just X.XX €"
    'akcia', 'sale', 'angebot',                    # "on sale"
    'zľava', 'výpredaj', 'ponuka', 'mimoriadne',   # promo labels
    'kaufland', 'lidl', 'billa', 'tesco',          # store labels
    'nové', 'new', 'neu', 'top',                   # generic promo
}


# ═══════════════════════════════════════════════════════════════════════════════
#  Helpers
# ═══════════════════════════════════════════════════════════════════════════════

def parse_price(raw) -> float | None:
    """'3,49' / '3.49' / 3.49 / '349' → float, or None if nonsensical."""
    if raw is None:
        return None
    s = str(raw).replace(' ', '').replace(' ', '').replace(',', '.')
    s = re.sub(r'[^\d.]', '', s)
    # Remove trailing/leading dots
    s = s.strip('.')
    if not s:
        return None
    try:
        v = float(s)
        # Sane grocery price: 0.10 € – 500 €
        return v if 0.10 <= v <= 500 else None
    except ValueError:
        return None


def fmt_price(v: float) -> str:
    return f"{v:.2f} €"


def clean(s: str) -> str:
    return re.sub(r'\s+', ' ', str(s or '')).strip()


def extract_unit(text: str) -> str:
    m = UNIT_RE.search(text)
    return clean(m.group(1)) if m else ""


def fetch_json(url: str, headers: dict | None = None) -> dict | list:
    req = urllib.request.Request(url, headers=headers or SCHWARZ_HEADERS)
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode("utf-8"))


def normalise_product(name, price, orig_price=None, image='', url='',
                      unit='', valid_from='', valid_until='', brand='',
                      raw_cat='') -> dict:
    return {
        "name":           clean(name),
        "brand":          clean(brand),
        "store":          "Kaufland",
        "price":          price,
        "price_str":      fmt_price(price),
        "original_price": fmt_price(orig_price) if orig_price and orig_price != price else "",
        "category":       normalize_category(raw_cat, name),
        "type":           normalize_type(raw_cat, name),
        "image":          url if '.' not in (url or '') else image,  # fix swapped args
        "url":            url,
        "unit":           unit or extract_unit(name),
        "valid_from":     valid_from,
        "valid_until":    valid_until,
    }


# ═══════════════════════════════════════════════════════════════════════════════
#  Stage 1 — Schwarz leaflets REST API probe
# ═══════════════════════════════════════════════════════════════════════════════

def _parse_schwarz_offer(item: dict, valid_from='', valid_until='') -> dict | None:
    """Parse one offer object from the Schwarz API."""
    name = clean(
        item.get("name") or item.get("title") or item.get("label") or
        item.get("description") or ""
    )
    if not name or len(name) < 3:
        return None

    price = parse_price(
        item.get("price") or item.get("promotionPrice") or
        item.get("currentPrice") or item.get("salePrice")
    )
    if price is None:
        return None

    orig = parse_price(
        item.get("normalPrice") or item.get("originalPrice") or
        item.get("regularPrice") or item.get("listPrice")
    )

    image = item.get("image") or item.get("imageUrl") or item.get("thumbnail") or ""
    if isinstance(image, dict):
        image = image.get("url") or image.get("src") or ""

    url_val = item.get("url") or item.get("link") or ""
    if url_val and not url_val.startswith("http"):
        url_val = "https://www.kaufland.sk" + url_val

    return {
        "name":           name,
        "brand":          clean(item.get("brand") or item.get("brandName") or ""),
        "store":          "Kaufland",
        "price":          price,
        "price_str":      fmt_price(price),
        "original_price": fmt_price(orig) if orig and orig != price else "",
        "category":       normalize_category(
                              item.get("category") or item.get("categoryName") or "", name),
        "type":           normalize_type(
                              item.get("category") or item.get("categoryName") or "", name),
        "image":          image,
        "url":            url_val,
        "unit":           extract_unit(item.get("description", "") + " " + name),
        "valid_from":     item.get("validFrom")  or valid_from,
        "valid_until":    item.get("validUntil") or valid_until,
    }


def try_schwarz_api() -> list[dict]:
    """
    Probe the Schwarz leaflets REST API for individual product/offer data.
    Tries several endpoint patterns under the same base used by server.py.
    Returns a (possibly empty) list of normalised product dicts.
    """
    slug = get_kaufland_slug()
    base = "https://endpoints.leaflets.schwarz/v4"
    print(f"\n── Stage 1: Schwarz API  (slug={slug}) ──")

    # First, get flyer metadata to confirm connectivity and get dates/flyer_id
    meta_url = (
        f"{base}/flyer?flyer_identifier={slug}"
        f"&region_id={KAUFLAND_REGION}&region_code={KAUFLAND_REGION}"
    )
    try:
        meta = fetch_json(meta_url)
    except Exception as e:
        print(f"  ✗ Metadata fetch failed: {e}")
        return [], "", ""

    flyer       = meta.get("flyer", meta)
    flyer_id    = flyer.get("id") or flyer.get("flyerId") or slug
    valid_from  = flyer.get("offerStartDate") or flyer.get("validFrom")  or ""
    valid_until = flyer.get("offerEndDate")   or flyer.get("validUntil") or ""
    print(f"  ✓ Metadata OK  ({valid_from} – {valid_until})")

    # Probe candidate product/offer endpoints
    probes = [
        f"{base}/flyer/{flyer_id}/offers",
        f"{base}/flyer/{flyer_id}/products",
        f"{base}/flyer/{flyer_id}/articles",
        f"{base}/offers?flyer_identifier={slug}&region_id={KAUFLAND_REGION}",
        f"{base}/products?flyer_identifier={slug}&region_id={KAUFLAND_REGION}",
        # Schwarz sometimes exposes page-level hotspots
        f"{base}/flyer/{flyer_id}/hotspots",
        f"{base}/flyer/{flyer_id}/pages",
    ]

    for url in probes:
        try:
            data = fetch_json(url)
            items = _unwrap_list(data)
            if not items:
                continue
            products = [p for p in (_parse_schwarz_offer(i, valid_from, valid_until)
                                    for i in items) if p]
            if products:
                print(f"  ✓ Products endpoint: {url}")
                print(f"    → {len(products)} products")
                return products, valid_from, valid_until
            else:
                print(f"  – {url.split('/')[-1]}: response received but no parseable products")
        except urllib.error.HTTPError as e:
            print(f"  – {url.split('/')[-1]}: HTTP {e.code}")
        except Exception as e:
            print(f"  – {url.split('/')[-1]}: {e}")

    print("  ✗ No product endpoint found on Schwarz API")
    # Even though we found no products, expose the flyer dates for downstream stages
    return [], valid_from, valid_until


def _unwrap_list(data) -> list:
    """Extract the most likely product list from a known-key API envelope."""
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in ("items", "products", "offers", "data", "results",
                    "articles", "records", "hotspots", "pages",
                    # Kaufland-specific guesses
                    "promotions", "deals", "campaigns", "categories",
                    "assortment", "actions", "specials"):
            val = data.get(key)
            if isinstance(val, list) and val:
                return val
    return []


def _deep_find_products(data, _depth: int = 0) -> list:
    """
    Recursively search `data` for the largest list-of-dicts regardless of key names.
    Used when _unwrap_list() can't find the product list via known keys.
    """
    if _depth > 4:
        return []
    if isinstance(data, list):
        return data if data and isinstance(data[0], dict) else []
    if isinstance(data, dict):
        best: list = []
        for val in data.values():
            if isinstance(val, (list, dict)):
                candidate = _deep_find_products(val, _depth + 1)
                if len(candidate) > len(best):
                    best = candidate
        return best
    return []


def _print_json_structure(data, url: str) -> None:
    """Print a compact structural summary so we can see what the API returned."""
    if isinstance(data, dict):
        keys = list(data.keys())
        print(f"         ↳ dict  top-level keys: {keys[:12]}")
        for k, v in data.items():
            if isinstance(v, list) and v:
                first = v[0]
                if isinstance(first, dict):
                    print(f"         ↳ data['{k}'] = list({len(v)}) of dicts, "
                          f"first keys: {list(first.keys())[:8]}")
                else:
                    print(f"         ↳ data['{k}'] = list({len(v)}) of {type(first).__name__}")
    elif isinstance(data, list) and data:
        first = data[0]
        if isinstance(first, dict):
            print(f"         ↳ list({len(data)}) of dicts, first keys: {list(first.keys())[:8]}")
        else:
            print(f"         ↳ list({len(data)}) of {type(first).__name__}")


# ═══════════════════════════════════════════════════════════════════════════════
#  Stage 2 — Playwright + network interception
# ═══════════════════════════════════════════════════════════════════════════════

_SKIP_HOSTS = (
    "google-analytics", "googletagmanager", "doubleclick", "facebook",
    "sentry", "hotjar", "cdn-cgi", "fonts.googleapis", "analytics",
)


def _looks_like_products(data) -> list | None:
    """Return the item list if data could be a product feed, else None."""
    items = _unwrap_list(data)
    if len(items) < 3:
        return None
    if not isinstance(items[0], dict):
        return None
    sample = items[0]
    has_name  = any(k in sample for k in
                    ("name", "title", "label", "description", "productName",
                     "product_name", "articleName"))
    has_price = any(k in sample for k in
                    ("price", "currentPrice", "promotionPrice", "salePrice",
                     "normalPrice", "originalPrice", "discount_price",
                     "priceValue", "salePriceValue", "actionPrice"))
    if has_name and has_price:
        return items
    if (has_name or has_price) and len(items) >= 10:
        return items
    # Large list of dicts with unknown field names — accept it and let the
    # parser decide item-by-item. Kaufland uses non-standard field names.
    if len(items) >= 10:
        return items
    return None


def _parse_intercepted(item: dict) -> dict | None:
    name = clean(
        item.get("name") or item.get("title") or item.get("label") or
        item.get("productName") or item.get("description") or ""
    )
    if not name or len(name) < 4:
        return None

    price = parse_price(
        item.get("promotionPrice") or item.get("currentPrice") or
        item.get("salePrice")      or item.get("discount_price") or
        item.get("price")
    )
    if price is None:
        return None

    orig = parse_price(
        item.get("normalPrice") or item.get("originalPrice") or
        item.get("regularPrice") or item.get("listPrice")
    )

    image = item.get("image") or item.get("imageUrl") or item.get("thumbnail") or ""
    if isinstance(image, dict):
        image = image.get("url") or image.get("src") or ""

    url_val = item.get("url") or item.get("link") or item.get("href") or ""
    if url_val and not url_val.startswith("http"):
        url_val = "https://www.kaufland.sk" + url_val

    return {
        "name":           name,
        "brand":          clean(item.get("brand") or item.get("brandName") or ""),
        "store":          "Kaufland",
        "price":          price,
        "price_str":      fmt_price(price),
        "original_price": fmt_price(orig) if orig and orig != price else "",
        "category":       normalize_category(
                              item.get("category") or item.get("categoryName") or "", name),
        "type":           normalize_type(
                              item.get("category") or item.get("categoryName") or "", name),
        "image":          image,
        "url":            url_val,
        "unit":           extract_unit(
                              (item.get("description") or "") + " " + name),
        "valid_from":     item.get("validFrom")  or item.get("startDate") or "",
        "valid_until":    item.get("validUntil") or item.get("endDate")   or "",
    }


def _load_and_intercept(page, url: str) -> tuple[list, list]:
    """
    Navigate `page` to `url`, scroll to trigger lazy loading, and return
    (captured_json_responses, dom_products_if_needed).
    captured = list of (url, parsed_json).
    """
    captured: list[tuple[str, object]] = []

    def on_response(resp):
        if any(s in resp.url for s in _SKIP_HOSTS):
            return
        if "json" not in resp.headers.get("content-type", ""):
            return
        try:
            captured.append((resp.url, resp.json()))
        except Exception:
            pass

    page.on("response", on_response)
    print(f"  Loading {url} …")
    try:
        page.goto(url, wait_until="networkidle", timeout=60_000)
    except Exception:
        pass

    page.wait_for_timeout(3_000)
    for _ in range(4):
        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        page.wait_for_timeout(1_500)

    return captured


def try_network_interception() -> list[dict]:
    """
    Open the Kaufland offers page(s) in a headless browser, intercept every JSON
    response, pick the largest feed that looks like products.
    Falls back to DOM scraping if no API response is usable.
    Tries the alternative URL if the primary yields nothing.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("  ✗ playwright not installed — skipping stage 2")
        return []

    print(f"\n── Stage 2: Network interception ──")

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page(user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ))

        products = []

        for attempt_url in (OFFER_URL, OFFER_URL_ALT):
            captured = _load_and_intercept(page, attempt_url)

            print(f"  Captured {len(captured)} JSON responses — scanning …")
            for url, data in captured:
                standard = _unwrap_list(data)
                deep     = _deep_find_products(data) if not standard else standard
                size     = len(deep) if deep else 0
                print(f"    {size:>4} items  {url[:100]}")
                if data:   # always show structure — helps tune field names
                    _print_json_structure(data, url)

            # Pick the response whose parsed list has the most product-like items.
            # Try standard unwrap first, fall back to deep recursive search.
            best_url, best_items = "", []
            for url, data in captured:
                raw = _unwrap_list(data) or _deep_find_products(data)
                items = _looks_like_products(raw)
                if items and len(items) > len(best_items):
                    best_url, best_items = url, items

            if best_items:
                print(f"  ✓ Best feed: {best_url}")
                print(f"    → {len(best_items)} raw items")
                print(f"    → First item keys:   {list(best_items[0].keys())[:15]}")
                print(f"    → First item sample: { {k: best_items[0][k] for k in list(best_items[0].keys())[:5]} }")
                for item in best_items:
                    p = _parse_intercepted(item)
                    if p:
                        products.append(p)
                print(f"  ✓ {len(products)} valid products from API")
                if not products:
                    print(f"  ⚠  0 parsed — _parse_intercepted field names don't match Kaufland's schema")
                    print(f"     Kaufland uses these keys: {list(best_items[0].keys())}")
                else:
                    break   # success — no need to try alt URL

            # No API feed found — try DOM on this page before moving to alt URL
            print(f"\n── Stage 3: DOM scraping ({attempt_url}) ──")

            # Print selector diagnostics — show ALL selectors with their counts
            extra = ["article", "li[class]",
                     "div[class*='product-tile']", "div[class*='product']",
                     ".product-tile", ".offer-tile"]
            for sel in _DOM_SELECTORS + extra:
                try:
                    n = len(page.query_selector_all(sel))
                    flag = " ← too many (BEM children?)" if n > 500 else ""
                    if n > 0:
                        print(f"    {n:>5}  {sel}{flag}")
                except Exception:
                    pass

            dom_products = _dom_scrape(page)
            # Accept DOM results only if we got a meaningful number of products.
            # A single stray match (e.g. "Franz Josef 1.49 €") is not good enough.
            if len(dom_products) >= 5:
                products = dom_products
                break   # got enough from DOM — stop
            elif dom_products:
                print(f"  ⚠  Only {len(dom_products)} product(s) from DOM — trying alt URL")

        browser.close()

    return products


# ═══════════════════════════════════════════════════════════════════════════════
#  Stage 3 — DOM scraping (fallback, runs inside stage-2 browser session)
# ═══════════════════════════════════════════════════════════════════════════════

# Ordered from most specific to most generic.
# IMPORTANT: use exact-class selectors (.foo) instead of substring selectors
# (div[class*="foo"]) where possible. BEM naming means "product-tile__image",
# "product-tile__price", etc. ALL match div[class*="product-tile"] — that's
# why it returns 9 000+ sub-elements. .product-tile matches only the root.
_DOM_SELECTORS = [
    # Kaufland's actual BEM root class (discovered via JS diagnostic 2026-05)
    '.k-product-tile',
    # Legacy / alternative Kaufland class names
    '.product-tile',
    '.offer-tile',
    '.m-offer-tile',
    '.m-offer-tile__content',
    # Generic article/li product patterns
    'article[class*="offer"]',
    'article[class*="product"]',
    'li[class*="offer"]',
    'li[class*="product"]',
    '[data-testid*="offer"]',
    '[data-testid*="product"]',
    'article',
]


def _el_text(el, selector: str) -> str:
    """Query a sub-element and return its stripped inner text, or ''."""
    try:
        sub = el.query_selector(selector)
        return clean(sub.inner_text()) if sub else ""
    except Exception:
        return ""


def _is_filler(text: str) -> bool:
    """True if the text is a known promotional filler word, not a product name."""
    return text.strip().lower() in _FILLER_WORDS


def _extract_name(el, lines: list[str]) -> str:
    """
    Try specific sub-element selectors first; fall back to line-by-line parsing.
    Skips promotional noise and filler words, stops at unit/quantity lines.
    Minimum meaningful name length: 5 characters.
    """
    # 1. Sub-element approach — most reliable when class names are present
    for sel in ("h2", "h3", "h4",
                # BEM children (exact class first — most precise)
                ".product-tile__name", ".product-tile__title",
                ".offer-tile__name",   ".offer-tile__title",
                ".m-offer-tile__name", ".m-offer-tile__title",
                # Substring fallbacks
                "[class*='product-name']", "[class*='offer-title']",
                "[class*='item-name']",    "[class*='article-name']",
                "[class*='title']",        "[class*='name']"):
        candidate = _el_text(el, sel)
        if (len(candidate) >= 5
                and not _JUNK_LINE.search(candidate)
                and not _is_filler(candidate)):
            return candidate

    # 2. Line-by-line fallback
    parts = []
    for ln in lines:
        if _JUNK_LINE.search(ln):              # promo tag, store label
            continue
        if _is_filler(ln):                     # "iba", "len", store names…
            continue
        if _UNIT_LINE.search(ln):              # "40 – 50 praní" → stop
            break
        if re.search(r'-\d+\s*%', ln):         # "-15%" discount badge
            continue
        if not re.search(r'[a-záäčďéíľĺňóôŕšťúýžA-Z]', ln):  # no letters
            continue
        if re.match(r'^\d', ln):               # starts with digit → stop
            break
        parts.append(ln)
        if len(parts) >= 2:                    # at most 2 name lines
            break

    return ' '.join(parts)


def _extract_prices(el, lines: list[str]) -> tuple[float | None, float | None]:
    """
    Returns (sale_price, original_price).

    Priority:
      1. CSS-class-targeted sub-elements (sale price element, original price element)
      2. Line scanning restricted to properly-formatted prices (X,XX)
         while skipping per-unit reference prices like '(=1 0,22)'.
    """
    # ── 1. Sub-element approach ──────────────────────────────────────────────
    sale_price = None
    orig_price = None

    # Common Kaufland/generic sale-price class patterns
    for sel in (# BEM exact classes first
                ".product-tile__price--sale", ".product-tile__price--promo",
                ".product-tile__sale-price",  ".product-tile__promo-price",
                ".offer-tile__price",
                ".m-offer-tile__price",
                # Substring fallbacks
                "[class*='sale-price']", "[class*='promo-price']",
                "[class*='current-price']", "[class*='offer-price']",
                "[class*='action-price']", "[class*='aktions']",
                "strong[class*='price']", "b[class*='price']",
                "[class*='price--sale']", "[class*='price--promo']"):
        txt = _el_text(el, sel)
        if not txt:
            continue
        m = PRICE_RE.search(txt)
        if m:
            v = parse_price(m.group(1))
            if v and v >= 0.30:
                sale_price = v
                break

    # Common original/crossed-out price class patterns
    for sel in ("[class*='original-price']", "[class*='regular-price']",
                "[class*='normal-price']", "[class*='old-price']",
                "[class*='crossed']", "s[class*='price']", "del[class*='price']",
                "[class*='price--original']", "[class*='price--regular']"):
        txt = _el_text(el, sel)
        if not txt:
            continue
        m = PRICE_RE.search(txt)
        if m:
            v = parse_price(m.group(1))
            if v and v >= 0.30:
                orig_price = v
                break

    if sale_price is not None:
        return sale_price, orig_price

    # ── 2. Line-scanning fallback ────────────────────────────────────────────
    candidates: list[float] = []
    for ln in lines:
        # Skip per-unit reference price contexts entirely
        if _PER_UNIT.search(ln):
            continue
        # Skip lines with unit/quantity words
        if _UNIT_LINE.search(ln):
            continue
        # Skip discount percentage lines ("-15%")
        if re.search(r'-\d+\s*%', ln):
            continue
        # Skip date lines — "14.05. – 20.05." also matches X.XX format
        if re.search(r'\b\d{1,2}\.\s*\d{2}\.', ln):
            continue
        # Match ONLY properly formatted prices (exactly 2 decimal digits)
        for m in PRICE_RE.finditer(ln):
            v = parse_price(m.group(1))
            # Floor 0.30 € — excludes per-wash/per-unit amounts like 0,22
            if v and v >= 0.30:
                candidates.append(v)

    if not candidates:
        return None, None

    sale = min(candidates)
    # Original price = a value at least 5 % higher than sale on a separate hit
    orig = max(candidates) if len(candidates) > 1 and max(candidates) > sale * 1.05 else None
    return sale, orig


def _dom_scrape(page) -> list[dict]:
    products: list[dict] = []
    seen: set[tuple] = set()

    # ── Step 1: JS diagnostic — discover the actual container class names ──────
    print("  Running JS DOM diagnostic …")
    try:
        diag = page.evaluate(r"""
            () => {
                const priceRe = /(?<!\d)(\d{1,3}[,.]\d{2})(?!\d)/;

                // All elements that have product/offer/tile in their class
                const all = Array.from(document.querySelectorAll(
                    '[class*="product-tile"],[class*="offer-tile"],[class*="m-offer-tile"],article'
                ));

                // Unique individual class tokens (not full className strings)
                const tokens = {};
                all.forEach(el => {
                    (el.className || '').split(/\s+/).forEach(c => {
                        if (c) tokens[c] = (tokens[c] || 0) + 1;
                    });
                });

                // Walk up 5 levels from the first leaf element that holds a price
                const priceLeaf = Array.from(document.querySelectorAll('*')).find(el => {
                    return el.children.length === 0 && priceRe.test(el.textContent || '');
                });
                const ancestors = [];
                if (priceLeaf) {
                    let cur = priceLeaf.parentElement;
                    for (let i = 0; i < 7 && cur; i++, cur = cur.parentElement) {
                        ancestors.push({
                            tag: cur.tagName,
                            cls: (cur.className || '').slice(0, 80),
                            nChildren: cur.children.length
                        });
                    }
                }

                return {
                    total: all.length,
                    tokens: Object.entries(tokens)
                                  .sort((a, b) => b[1] - a[1])
                                  .slice(0, 25),
                    ancestors
                };
            }
        """)
        print(f"  Elements with product/offer/tile in class: {diag.get('total', 0)}")
        top_tokens = diag.get('tokens', [])
        print(f"  Top class tokens: {top_tokens[:12]}")
        ancs = diag.get('ancestors', [])
        if ancs:
            print("  Ancestors of first price leaf:")
            for a in ancs[:6]:
                print(f"    <{a['tag']}> cls='{a['cls']}'  children={a['nChildren']}")
    except Exception as e:
        print(f"  JS diagnostic error: {e}")
        diag = {}

    # ── Step 2: JS-based product extraction ───────────────────────────────────
    # Uses Kaufland's actual BEM sub-element classes for reliable extraction:
    #   .k-product-tile__title    → brand/first name line
    #   .k-product-tile__subtitle → product description line
    #   .k-price-tag              → contains "-%\nSalePrice\nOriginalPrice"
    #   .k-price-tag--k-card      → same structure but for Kaufland Card price
    # This avoids the raw innerText parsing issue where name+unit land on one line.
    print("  Running JS product extraction …")
    try:
        js_products = page.evaluate(r"""
            () => {
                const PRICE_RE = /(\d{1,3}[,.]\d{2})/g;
                const seen     = new Set();
                const products = [];

                const tiles = Array.from(document.querySelectorAll('.k-product-tile'));

                for (const tile of tiles) {
                    // ── Name: title element (brand) + subtitle (product)
                    const titleEl    = tile.querySelector('.k-product-tile__title');
                    const subtitleEl = tile.querySelector('.k-product-tile__subtitle');
                    const titleTxt   = titleEl    ? titleEl.innerText.trim()    : '';
                    const subTxt     = subtitleEl ? subtitleEl.innerText.trim() : '';

                    // Full name = "Brand ProductName" or just one if only one is present
                    let name = (titleTxt && subTxt)
                        ? titleTxt + ' ' + subTxt
                        : (titleTxt || subTxt);
                    name = name.replace(/\s+/g, ' ').trim();
                    if (!name || name.length < 3) continue;

                    // ── Prices: prefer Card price if available, fall back to regular
                    // Each .k-price-tag contains text like "-46%\n0,42\n0,69"
                    // Card price tag has additional class k-price-tag--k-card
                    function parsePriceTag(tagEl) {
                        if (!tagEl) return null;
                        const txt = tagEl.innerText || '';
                        const nums = [];
                        PRICE_RE.lastIndex = 0;
                        let m;
                        while ((m = PRICE_RE.exec(txt)) !== null) {
                            const v = parseFloat(m[1].replace(',', '.'));
                            if (v >= 0.05 && v <= 500) nums.push(v);
                        }
                        if (!nums.length) return null;
                        return { sale: Math.min(...nums), orig: nums.length > 1 ? Math.max(...nums) : null };
                    }

                    const cardTag    = tile.querySelector('.k-price-tag--k-card');
                    const regularTag = tile.querySelector('.k-price-tag:not(.k-price-tag--k-card)');

                    // Prefer Card price as the displayed price; use regular as "was" if no Card
                    let salePrice = null, origPrice = null;
                    if (cardTag) {
                        const cp = parsePriceTag(cardTag);
                        const rp = parsePriceTag(regularTag);
                        if (cp) {
                            salePrice = cp.sale;
                            // "was" price: the regular (non-card) sale price
                            origPrice = rp ? rp.sale : cp.orig;
                        }
                    } else if (regularTag) {
                        const rp = parsePriceTag(regularTag);
                        if (rp) { salePrice = rp.sale; origPrice = rp.orig; }
                    }

                    if (salePrice === null) continue;

                    // ── Dedup
                    const key = name.slice(0, 40).toLowerCase() + '|' + salePrice.toFixed(2);
                    if (seen.has(key)) continue;
                    seen.add(key);

                    // ── Image + URL
                    const imgEl = tile.querySelector('img');
                    const image = imgEl
                        ? (imgEl.getAttribute('src') || imgEl.getAttribute('data-src') || '')
                        : '';
                    const aEl = tile.querySelector('a[href]');
                    const url = aEl ? aEl.href : '';

                    // ── Discount percent from price tag text
                    const ptxt = (cardTag || regularTag || {innerText:''}).innerText || '';
                    const dm   = ptxt.match(/-(\d+)\s*%/);
                    const discPct = dm ? parseInt(dm[1]) : 0;

                    products.push({
                        name, price: salePrice, origPrice, image, url, discPct,
                        containerClass: (tile.className || '').split(' ')[0]
                    });
                }

                return products;
            }
        """)

        if js_products:
            print(f"  JS extraction: {len(js_products)} products")
            # Show which container class names were actually used
            cls_counts: dict[str, int] = {}
            for p in js_products:
                c = p.get('containerClass', '')
                cls_counts[c] = cls_counts.get(c, 0) + 1
            print(f"  Container classes used: {dict(list(cls_counts.items())[:6])}")

            for item in js_products:
                name = clean(item.get('name', ''))
                price = item.get('price')
                if not name or price is None:
                    continue

                href = item.get('url', '')
                if href and not href.startswith('http'):
                    href = 'https://www.kaufland.sk' + href

                orig_price = item.get('origPrice')
                disc_pct   = item.get('discPct', 0)
                key = (name[:60].lower(), round(price, 2))
                if key in seen:
                    continue
                seen.add(key)

                products.append({
                    "name":           name,
                    "brand":          "",
                    "store":          "Kaufland",
                    "price":          price,
                    "price_str":      fmt_price(price),
                    "original_price": fmt_price(orig_price) if orig_price and orig_price > price else "",
                    "discount_pct":   disc_pct,
                    "category":       normalize_category("", name),
                    "type":           normalize_type("", name),
                    "image":          item.get('image', ''),
                    "url":            href,
                    "unit":           extract_unit(name),
                    "valid_from":     "",
                    "valid_until":    "",
                })
        else:
            print("  JS extraction returned 0 products")

    except Exception as e:
        print(f"  JS extraction error: {e}")

    if products:
        print(f"  ✓ {len(products)} products extracted (JS method)")
        return products

    # ── Step 3: broad-selector fallback (mirrors original extract_kaufland_ocr) ──
    # No cap — iterate over everything; deduplication via `seen` handles repeats.
    print("  Falling back to broad CSS selector (no cap) …")
    broad = page.query_selector_all(
        "article, div[class*='offer'], div[class*='product']"
    )
    print(f"  Broad selector: {len(broad)} elements")

    for el in broad:
        try:
            text = el.inner_text().strip()
            if len(text) < 20 or len(text) > 2000:
                continue
            lines = [ln.strip() for ln in text.splitlines() if ln.strip()]

            name = _extract_name(el, lines)
            if not name or len(name) < 5 or _is_filler(name):
                continue

            price, orig = _extract_prices(el, lines)
            if price is None:
                continue

            key = (name[:60].lower(), round(price, 2))
            if key in seen:
                continue
            seen.add(key)

            img_el = el.query_selector("img")
            image  = (img_el.get_attribute("src") or
                      img_el.get_attribute("data-src") or "") if img_el else ""

            a_el = el.query_selector("a[href]")
            href = a_el.get_attribute("href") if a_el else ""
            if href and not href.startswith("http"):
                href = "https://www.kaufland.sk" + href

            products.append({
                "name":           name,
                "brand":          "",
                "store":          "Kaufland",
                "price":          price,
                "price_str":      fmt_price(price),
                "original_price": fmt_price(orig) if orig else "",
                "category":       normalize_category("", name),
                "type":           normalize_type("", name),
                "image":          image,
                "url":            href,
                "unit":           extract_unit(text),
                "valid_from":     "",
                "valid_until":    "",
            })
        except Exception:
            continue

    print(f"  ✓ {len(products)} products extracted (broad selector fallback)")
    return products


# ═══════════════════════════════════════════════════════════════════════════════
#  Entry point
# ═══════════════════════════════════════════════════════════════════════════════

def scrape() -> list[dict]:
    # Stage 1 — lightweight REST probe (no browser)
    # Always returns (products, valid_from, valid_until) — dates come from Schwarz
    # flyer metadata even when the products endpoint is 404.
    result = try_schwarz_api()
    if isinstance(result, tuple):
        products, flyer_vf, flyer_vu = result
    else:
        products, flyer_vf, flyer_vu = result, "", ""   # legacy compat

    if products:
        return products

    # Stage 2 + 3 — browser-based (Playwright required)
    products = try_network_interception()

    # Backfill validity dates from Schwarz flyer metadata (Stage 1 metadata is
    # always retrieved even when no product endpoint is available).
    if flyer_vf or flyer_vu:
        for p in products:
            if not p.get("valid_from"):
                p["valid_from"]  = flyer_vf
            if not p.get("valid_until"):
                p["valid_until"] = flyer_vu
        print(f"  ↳ Dátumy z Schwarz metadát aplikované: {flyer_vf} – {flyer_vu}")

    return products


def deduplicate(products: list[dict]) -> list[dict]:
    seen: set[tuple] = set()
    out: list[dict] = []
    for p in products:
        key = (p["name"][:60].lower(), round(p["price"], 2))
        if key not in seen:
            seen.add(key)
            out.append(p)
    return out


if __name__ == "__main__":
    products = deduplicate(scrape())

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(products, f, ensure_ascii=False, indent=2)

    print(f"\n✅  {len(products)} unique products saved → {OUTPUT_FILE}")

    if products:
        # Category breakdown
        from collections import Counter
        counts = Counter(p["category"] for p in products)
        print("\nCategory breakdown:")
        for cat, n in counts.most_common():
            print(f"  {cat:<26} {n:>4}")

        print("\nFirst 8 products:")
        for p in products[:8]:
            orig = f"  (bolo {p['original_price']})" if p["original_price"] else ""
            print(f"  [{p['category']:<22}] {p['name'][:52]:<52}  {p['price_str']}{orig}")
