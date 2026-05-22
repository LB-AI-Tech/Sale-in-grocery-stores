#!/usr/bin/env python3
"""
Tesco SK scraper — v1
=====================
Strategy
--------
Tesco's `/api/graphql` endpoint is POST-blocked by Akamai, but the
promotional-products pages are server-side rendered with the first 12 items
from each category embedded in ``__NEXT_DATA__`` → ``__APOLLO_STATE__``.

We fetch:
  • The main promo page  →  category list + 12 "all-categories" items
  • Each of the 16 category sub-pages  →  up to 12 items per category

Total: ~144 unique products covering every food & non-food category.
A weekly Tesco SK flyer typically contains 80–200 items, so this gives
good coverage of all major deals.

Output
------
tesco_products.json  —  list of product dicts in the shared server.py schema.
"""

import gzip, json, os, re, sys, urllib.request, urllib.error, ssl
from datetime import datetime, timezone, date

# ── reuse normalize_category from server.py ──────────────────────────────────
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    from server import normalize_category, normalize_type
    print("✓  normalize_category / normalize_type imported from server.py")
except ImportError:
    print("⚠  server.py not found — using stubs")
    def normalize_category(raw, name=""):
        return "Ostatné"
    def normalize_type(raw, name=""):
        return ""

OUTPUT_FILE = "tesco_products.json"
BASE_URL    = "https://www.tesco.sk"
PROMO_PATH  = "/akciove-ponuky/akciove-produkty/tesco-obchod"

# Akamai requires these headers — without Sec-Fetch-* the response is 403
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept":               "text/html,application/xhtml+xml,*/*;q=0.9",
    "Accept-Language":      "sk-SK,sk;q=0.9",
    "Accept-Encoding":      "gzip, deflate, br",
    "Referer":              "https://www.tesco.sk/",
    "Connection":           "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest":       "document",
    "Sec-Fetch-Mode":       "navigate",
    "Sec-Fetch-Site":       "same-origin",
    "Sec-Fetch-User":       "?1",
}

_SSL_CTX = ssl.create_default_context()
_SSL_CTX.check_hostname = False
_SSL_CTX.verify_mode    = ssl.CERT_NONE

_UNIT_RE = re.compile(
    r'(\d+[.,]?\d*\s*(?:g|kg|l|ml|cl|ks|bal\.?|por\.?|kus|pieces?))',
    re.I,
)


# ── HTTP helper ───────────────────────────────────────────────────────────────

def _fetch_html(path: str) -> str:
    """GET a tesco.sk page with the Akamai-required headers."""
    url = BASE_URL + path
    req = urllib.request.Request(url, headers=_HEADERS)
    with urllib.request.urlopen(req, timeout=25, context=_SSL_CTX) as resp:
        raw = resp.read()
        if "gzip" in resp.headers.get("Content-Encoding", ""):
            raw = gzip.decompress(raw)
        return raw.decode("utf-8", "replace")


def _extract_apollo(html: str) -> dict:
    """Pull ``__APOLLO_STATE__`` out of the embedded ``__NEXT_DATA__`` JSON."""
    m = re.search(r'id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.S)
    if not m:
        return {}
    try:
        nd = json.loads(m.group(1))
        return nd["props"]["pageProps"].get("__APOLLO_STATE__", {})
    except Exception:
        return {}


def _apollo_total(apollo: dict) -> int:
    """Return totalItems from the first promotions() query in ROOT_QUERY."""
    rq = apollo.get("ROOT_QUERY", {})
    for k, v in rq.items():
        if k.startswith("promotions(") and isinstance(v, dict):
            return v.get("totalItems", 0)
    return 0


# ── Product parser ────────────────────────────────────────────────────────────

def _clean(s) -> str:
    return re.sub(r"\s+", " ", str(s or "")).strip()


def _parse_price(raw) -> float | None:
    if raw is None:
        return None
    try:
        v = float(raw)
        return v if 0.10 <= v <= 500 else None
    except (TypeError, ValueError):
        return None


def _extract_unit(desc: str, name: str) -> str:
    for text in (desc, name):
        m = _UNIT_RE.search(text)
        if m:
            return _clean(m.group(1))
    return ""


def _parse_period(item: dict, tesco_category: str) -> dict | None:
    """
    Convert one PromotionPeriod Apollo cache entry into our schema.

    Price logic
    -----------
    • If ccPrice is set  →  sale_price = ccPrice,  original = normalRetailPrice
    • Else               →  sale_price = normalRetailPrice  (regular deal)
    The 30-day baseline price (thirtydayPrice) is used as the "was" price
    if it's higher than the normal retail price.
    """
    # Name: prefer the human-facing displayedProductName, fall back to promo name
    name = _clean(item.get("displayedProductName") or item.get("promoOfferName") or "")
    if not name or len(name) < 3:
        return None

    desc = _clean(item.get("displayedDescription") or "")

    retail   = _parse_price(item.get("normalRetailPrice"))
    cc_price = _parse_price(item.get("ccPrice"))
    thirty   = _parse_price(item.get("thirtydayPrice"))

    # Determine displayed price and "was" price
    if cc_price is not None:
        sale_price = cc_price
        orig_price = retail
    else:
        sale_price = retail
        # Use 30-day price as "was" if higher
        orig_price = thirty if (thirty and retail and thirty > retail * 1.02) else None

    if sale_price is None:
        return None

    # Image: pngUrl already a full URL
    image = _clean(item.get("pngUrl") or "")

    # URL
    url_val = _clean(item.get("addToBasketURL") or "")
    if url_val and not url_val.startswith("http"):
        url_val = BASE_URL + url_val

    # Dates
    def _date(raw):
        if not raw:
            return ""
        try:
            return datetime.fromisoformat(raw.replace("Z", "+00:00")).date().isoformat()
        except Exception:
            return ""

    return {
        "store":          "Tesco",
        "name":           name,
        "brand":          "",
        "price":          round(sale_price, 2),
        "price_str":      f"{sale_price:.2f} €".replace(".", ","),
        "original_price": f"{orig_price:.2f} €".replace(".", ",") if orig_price else "",
        "discount_pct":   0,
        "image":          image,
        "url":            url_val,
        "category":       normalize_category(tesco_category, name),
        "type":           normalize_type(tesco_category, name),
        "unit":           _extract_unit(desc, name),
        "valid_from":     _date(item.get("promoStart")),
        "valid_until":    _date(item.get("promoEnd")),
    }


# ── Main scraping logic ───────────────────────────────────────────────────────

def scrape() -> list[dict]:
    products: dict[int, dict] = {}   # keyed by PromotionPeriod.id to deduplicate

    # ── Main page: get category list + initial batch ─────────────────────────
    print(f"  → Tesco: loading main promo page …")
    try:
        html    = _fetch_html(PROMO_PATH)
        apollo  = _extract_apollo(html)
        total   = _apollo_total(apollo)
        print(f"    totalItems (all categories): {total}")
    except Exception as e:
        print(f"  ✗ Tesco main page: {e}")
        return []

    # Harvest categories
    categories = {}   # slug → name
    for k, v in apollo.items():
        if k.startswith("ProductCategory:"):
            slug = v.get("slug", "")
            name = v.get("name", "")
            if slug:
                categories[slug] = name

    print(f"    {len(categories)} categories found")

    # Harvest initial PromotionPeriod items (no category context yet)
    for k, v in apollo.items():
        if k.startswith("PromotionPeriod:"):
            parsed = _parse_period(v, "")
            if parsed:
                products[v["id"]] = parsed

    print(f"    {len(products)} items from main page")

    # ── Category sub-pages ───────────────────────────────────────────────────
    for slug, cat_name in categories.items():
        path = f"{PROMO_PATH}/{slug}"
        try:
            cat_html   = _fetch_html(path)
            cat_apollo = _extract_apollo(cat_html)
            cat_total  = _apollo_total(cat_apollo)

            new_count = 0
            for k, v in cat_apollo.items():
                if k.startswith("PromotionPeriod:") and v["id"] not in products:
                    parsed = _parse_period(v, cat_name)
                    if parsed:
                        products[v["id"]] = parsed
                        new_count += 1

            got = len([k for k in cat_apollo if k.startswith("PromotionPeriod:")])
            print(f"    {slug:<38}  got={got:>3}/{cat_total:<3}  new={new_count:>3}  total={len(products)}")
        except Exception as e:
            print(f"    {slug:<38}  ERROR: {e}")

    result = list(products.values())
    print(f"\n  ✓ Tesco: {len(result)} unique products scraped")
    return result


def deduplicate(products: list[dict]) -> list[dict]:
    seen: set[tuple] = set()
    out: list[dict] = []
    for p in products:
        key = (p["name"][:60].lower(), round(p["price"], 2))
        if key not in seen:
            seen.add(key)
            out.append(p)
    return out


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    items = deduplicate(scrape())

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=2)

    print(f"\n✅  {len(items)} unique products → {OUTPUT_FILE}")

    if items:
        from collections import Counter
        counts = Counter(p["category"] for p in items)
        print("\nCategory breakdown:")
        for cat, n in counts.most_common():
            print(f"  {cat:<30} {n:>4}")

        print("\nFirst 10 products:")
        for p in items[:10]:
            orig = f"  (bolo {p['original_price']})" if p["original_price"] else ""
            print(f"  [{p['category']:<22}] {p['name'][:55]:<55}  {p['price_str']}{orig}")
