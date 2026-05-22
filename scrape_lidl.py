#!/usr/bin/env python3
"""
Lidl SK scraper  —  v1
=======================
Strategy (fastest first, most expensive last):

  Stage 1  Direct search API  — two targeted categories + full in-store sweep
           • Category 10068374  (Jedlo a nápoje)
           • Category 10077765  (Cenový líder)
           • &store=1 no-category sweep  — all ~300 in-store promotional items
             Uses version=v2.0.0 (what the website actually sends internally).
             Pagination: keep fetching while len(batch) == fetchsize (total=0 is
             always returned, so we can't use it).

  Stage 2  Playwright + network interception
           Navigate to the Cenový líder page, food deals page, and weekly-offers
           page in a headless browser.  Capture every /q/api/search JSON response
           the site makes (these use the real internal parameters) and harvest
           products from them.

  Stage 3  Schwarz leaflets API  — weekly non-food items
           Same slug-discovery logic as server.py's Source 2.  Provides ~60
           non-food items (tools, garden, clothing).

Output
------
  lidl_products.json  —  list of product dicts in the shared server.py schema.
"""

import json
import os
import re
import sys
import urllib.request
import urllib.error
import urllib.parse
import gzip
import ssl
from datetime import date, timedelta, datetime, timezone

# ── Fix Windows console encoding ─────────────────────────────────────────────
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass
os.environ["PYTHONIOENCODING"] = "utf-8"

# ── reuse server.py helpers ───────────────────────────────────────────────────
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    from server import normalize_category, normalize_type
    print("✓  normalize_category / normalize_type imported from server.py")
except ImportError:
    print("⚠  server.py not found — using stubs")
    def normalize_category(raw, name=""):  return "Ostatné"
    def normalize_type(raw, name=""):      return ""

OUTPUT_FILE = "lidl_products.json"

_SSL_CTX = ssl.create_default_context()
_SSL_CTX.check_hostname = False
_SSL_CTX.verify_mode    = ssl.CERT_NONE

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept":          "application/json, text/plain, */*",
    "Accept-Language": "sk-SK,sk;q=0.9",
    "Accept-Encoding": "gzip, deflate",
    "Referer":         "https://www.lidl.sk/",
}

_SKIP_HOSTS = (
    "google-analytics", "googletagmanager", "doubleclick", "facebook",
    "sentry", "hotjar", "cdn-cgi", "fonts.googleapis", "analytics",
    "datadog", "akamai", "cookielaw",
)

UNIT_RE = re.compile(r'(\d+[.,]?\d*\s*(?:g|kg|l|ml|cl|ks|bal\.?|por\.?|kus|pieces?))', re.I)


# ═══════════════════════════════════════════════════════════════════════════════
#  Shared helpers
# ═══════════════════════════════════════════════════════════════════════════════

def _fetch_json(url: str, extra_headers: dict | None = None) -> dict | list:
    headers = {**_HEADERS, **(extra_headers or {})}
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=25, context=_SSL_CTX) as resp:
        raw = resp.read()
        enc = resp.headers.get("Content-Encoding", "")
    if "gzip" in enc:
        raw = gzip.decompress(raw)
    return json.loads(raw.decode("utf-8"))


def _clean(s) -> str:
    return re.sub(r"\s+", " ", str(s or "")).strip()


def _parse_price(raw) -> float | None:
    if raw is None:
        return None
    try:
        v = float(str(raw).replace(",", ".").strip())
        return v if 0.05 <= v <= 500 else None
    except (TypeError, ValueError):
        return None


def _extract_unit(name: str, desc: str = "") -> str:
    for text in (desc, name):
        m = UNIT_RE.search(text)
        if m:
            return _clean(m.group(1))
    return ""


def _make_product(name, price_val, old_price, pct_disc, image, url,
                  category_raw, valid_from="", valid_until="", brand="") -> dict | None:
    name = _clean(name)
    if not name or len(name) < 3:
        return None
    price_val = _parse_price(price_val)
    if price_val is None or price_val <= 0:
        return None
    return {
        "store":          "Lidl",
        "name":           name,
        "brand":          _clean(brand),
        "price":          price_val,
        "price_str":      f"{price_val:.2f} €".replace(".", ","),
        "original_price": f"{float(old_price):.2f} €".replace(".", ",") if old_price else "",
        "discount_pct":   abs(int(pct_disc)) if pct_disc else 0,
        "image":          _clean(image),
        "url":            url or "https://www.lidl.sk",
        "category":       normalize_category(category_raw or "", name),
        "type":           normalize_type(category_raw or "", name),
        "unit":           _extract_unit(name),
        "valid_from":     valid_from or "",
        "valid_until":    valid_until or "",
    }


def _ts_to_date(ts) -> str:
    if not ts:
        return ""
    try:
        return datetime.fromtimestamp(float(ts), tz=timezone.utc).date().isoformat()
    except Exception:
        return ""


def _ingest_search_response(data: dict, products: list, seen: set,
                             label: str = "") -> int:
    """
    Parse a Lidl /q/api/search JSON response and append new products.
    Returns the count of new products added.
    """
    added = 0
    for item in data.get("items", []):
        gb = item.get("gridbox", {})
        d  = gb.get("data", {}) if isinstance(gb, dict) else {}
        if not d:
            continue

        price_obj = d.get("price", {}) or {}
        price_val = price_obj.get("price")
        if not price_val:
            continue
        try:
            price_val = float(price_val)
        except (TypeError, ValueError):
            continue
        if price_val <= 0:
            continue

        old_price = _parse_price(price_obj.get("oldPrice")) or 0
        disc_obj  = price_obj.get("discount", {}) or {}
        pct_disc  = disc_obj.get("percentageDiscount") or 0

        brand_obj  = d.get("brand") or {}
        brand_name = (brand_obj.get("brandName", "")
                      if isinstance(brand_obj, dict) else str(brand_obj or ""))

        name = d.get("fullTitle", "") or d.get("name", "")
        if not name:
            continue

        key = name[:50].lower()
        if key in seen:
            continue
        seen.add(key)

        url_path = d.get("canonicalPath", "")
        full_url = ("https://www.lidl.sk" + url_path) if url_path else "https://www.lidl.sk"

        cat = d.get("category") or ""

        p = _make_product(
            name         = name,
            price_val    = price_val,
            old_price    = old_price,
            pct_disc     = pct_disc,
            image        = d.get("image", ""),
            url          = full_url,
            category_raw = cat,
            valid_from   = _ts_to_date(d.get("storeStartDate")),
            valid_until  = _ts_to_date(d.get("storeEndDate")),
            brand        = brand_name,
        )
        if p:
            products.append(p)
            added += 1

    return added


# ═══════════════════════════════════════════════════════════════════════════════
#  Stage 1 — Direct search API
# ═══════════════════════════════════════════════════════════════════════════════

# The website uses version=v2.0.0 (discovered from SSR devalue data).
# version=2.1.0 also works from the outside but may return slightly different results.
_SEARCH_BASE_V2 = (
    "https://www.lidl.sk/q/api/search"
    "?assortment=SK&locale=sk_SK&version=v2.0.0"
)
_SEARCH_BASE_V1 = (
    "https://www.lidl.sk/q/api/search"
    "?assortment=SK&locale=sk_SK&version=2.1.0"
)


def _fetch_category(cat_id: str, products: list, seen: set, label: str) -> int:
    """Fetch all items for a given category ID. Returns count of new items."""
    total_new = 0
    for base in (_SEARCH_BASE_V2, _SEARCH_BASE_V1):
        try:
            url  = f"{base}&offset=0&fetchsize=1000&category.id={cat_id}"
            data = _fetch_json(url)
            n    = _ingest_search_response(data, products, seen, label)
            print(f"    category {cat_id} ({label}): {n} new products")
            total_new += n
            break   # success — don't retry with v1
        except Exception as e:
            print(f"    category {cat_id} ({label}) [{base[-5:]}]: {e}")
    return total_new


def _fetch_store_sweep(products: list, seen: set) -> int:
    """
    Fetch all in-store promotional items via &store=1 (no category filter).
    The API always returns total=0, so we paginate by continuing while
    the batch size equals fetchsize.
    Returns count of new items.
    """
    FETCHSIZE = 100
    total_new = 0
    offset    = 0

    # Try v2.0.0 first (what the site uses), fall back to 2.1.0
    for base in (_SEARCH_BASE_V2, _SEARCH_BASE_V1):
        worked    = False
        offset    = 0
        total_new = 0
        while True:
            url = f"{base}&offset={offset}&fetchsize={FETCHSIZE}&store=1"
            try:
                data  = _fetch_json(url)
                batch = data.get("items", [])
                n     = _ingest_search_response(data, products, seen, "store=1")
                total_new += n
                print(f"    store=1 offset={offset:>4}: {len(batch):>3} items  "
                      f"({n} new)  running total={len(products)}")
                worked = True
                if len(batch) < FETCHSIZE:
                    break
                offset += len(batch)
            except Exception as e:
                print(f"    store=1 offset={offset}: {e}")
                break

        if worked:
            break  # stop trying versions once one worked

    print(f"  ✓ store=1 sweep: {total_new} new products")
    return total_new


def try_direct_api() -> tuple[list, set]:
    """Stage 1: Direct API calls. Returns (products, seen_names)."""
    products: list[dict] = []
    seen:     set[str]   = set()

    print("\n── Stage 1: Direct search API ──")

    # Food & beverages
    _fetch_category("10068374", products, seen, "Jedlo a nápoje")

    # Cenový líder (often 0 items, but future-proof)
    _fetch_category("10077765", products, seen, "Cenový líder")

    # In-store sweep (all ~300 promotional items regardless of category)
    _fetch_store_sweep(products, seen)

    print(f"  ✓ Stage 1 total: {len(products)} products")
    return products, seen


# ═══════════════════════════════════════════════════════════════════════════════
#  Stage 2 — Playwright + network interception
# ═══════════════════════════════════════════════════════════════════════════════

# Pages to visit.  The browser will make /q/api/search calls with the exact
# parameters it uses internally — we capture these instead of guessing them.
_LIDL_PAGES = [
    # Cenový líder landing page  — most likely to show Olivový olej, Jacobs Krönung, mlieko
    "https://www.lidl.sk/p/cenovy-lider/",
    # Weekly special offers page
    "https://www.lidl.sk/aktualna-ponuka/",
    # Food & beverages category
    "https://www.lidl.sk/p/jedlo-a-napoje/",
]


def _parse_intercepted(data: dict, products: list, seen: set) -> int:
    """Parse a captured JSON response (any shape) into products."""
    added = 0
    # Standard /q/api/search shape
    if "items" in data:
        added += _ingest_search_response(data, products, seen, "intercepted")
    return added


def try_playwright(products: list, seen: set) -> int:
    """
    Stage 2: Open Lidl pages in headless Playwright, capture every
    /q/api/search response, and harvest products from them.
    Returns count of new products added.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("  ✗ playwright not installed — skipping stage 2")
        return 0

    print(f"\n── Stage 2: Playwright interception ({len(_LIDL_PAGES)} pages) ──")
    total_new = 0

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            locale="sk-SK",
            extra_http_headers={"Accept-Language": "sk-SK,sk;q=0.9"},
        )
        page = context.new_page()

        for page_url in _LIDL_PAGES:
            captured: list[tuple[str, dict]] = []

            def _on_response(resp, _url=page_url):
                try:
                    if any(s in resp.url for s in _SKIP_HOSTS):
                        return
                    if "/q/api/search" not in resp.url:
                        return
                    if "json" not in resp.headers.get("content-type", ""):
                        return
                    captured.append((resp.url, resp.json()))
                except Exception:
                    pass

            page.on("response", _on_response)

            print(f"  Loading {page_url} …")
            try:
                page.goto(page_url, wait_until="networkidle", timeout=60_000)
            except Exception:
                pass

            # Wait and scroll to trigger lazy loading
            page.wait_for_timeout(3_000)
            for _ in range(5):
                page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                page.wait_for_timeout(1_200)

            # Also try clicking a "Load more" or pagination button if present
            for btn_sel in (
                "button[data-testid*='load-more']",
                "button[class*='load-more']",
                "button[class*='loadmore']",
                ".btn-load-more",
                "[aria-label*='Načítať viac']",
                "[aria-label*='Zobraziť viac']",
            ):
                try:
                    btn = page.query_selector(btn_sel)
                    if btn and btn.is_visible():
                        btn.click()
                        page.wait_for_timeout(2_000)
                        print(f"    Clicked load-more: {btn_sel}")
                        break
                except Exception:
                    pass

            page.remove_listener("response", _on_response)

            print(f"  Captured {len(captured)} search API calls from {page_url.split('/')[-2] or page_url}")
            for cap_url, cap_data in captured:
                n = _parse_intercepted(cap_data, products, seen)
                total_new += n
                items_cnt = len(cap_data.get("items", []))
                print(f"    {items_cnt:>4} items  (+{n} new)  {cap_url[:120]}")

            # Also try to replicate any captured URL with higher fetchsize
            for cap_url, _ in captured:
                try:
                    parsed = urllib.parse.urlparse(cap_url)
                    params = dict(urllib.parse.parse_qsl(parsed.query))
                    orig_fetchsize = int(params.get("fetchsize", 0))
                    if orig_fetchsize > 0 and orig_fetchsize < 500:
                        params["fetchsize"] = "1000"
                        params["offset"]    = "0"
                        big_url = parsed._replace(
                            query=urllib.parse.urlencode(params)
                        ).geturl()
                        big_data = _fetch_json(big_url)
                        n = _ingest_search_response(big_data, products, seen,
                                                    "intercepted-expanded")
                        if n:
                            print(f"    Expanded fetch: +{n} more products  {big_url[:120]}")
                            total_new += n
                except Exception:
                    pass

        context.close()
        browser.close()

    print(f"  ✓ Stage 2 total new: {total_new}")
    return total_new


# ═══════════════════════════════════════════════════════════════════════════════
#  Stage 3 — Schwarz leaflets API (non-food weekly items)
# ═══════════════════════════════════════════════════════════════════════════════

def try_schwarz_flyer(products: list, seen: set) -> int:
    """
    Stage 3: Schwarz leaflets API for the current weekly flyer.
    Primary: Monday-based slug.
    Fallback: discover via cenovy-lider relatedFlyers.
    Returns count of new products added.
    """
    print("\n── Stage 3: Schwarz leaflets API ──")
    schwarz_hdrs = {
        "User-Agent": _HEADERS["User-Agent"],
        "Accept":     "application/json",
        "Referer":    "https://www.lidl.sk/",
    }

    today    = date.today()
    week_mon = today - timedelta(days=today.weekday())
    slug     = (f"online-letak-platny-od-"
                f"{week_mon.day:02d}-{week_mon.month:02d}-{week_mon.year}")

    meta = None
    try:
        meta = _fetch_json(
            f"https://endpoints.leaflets.schwarz/v4/flyer?flyer_identifier={slug}",
            extra_headers=schwarz_hdrs,
        )
        print(f"  ✓ Primary slug found: {slug}")
    except Exception as e:
        print(f"  ⚠  Primary slug ({slug}): {e}")

    # Fallback: discover via cenovy-lider relatedFlyers
    if meta is None:
        try:
            cl = _fetch_json(
                "https://endpoints.leaflets.schwarz/v4/flyer"
                "?flyer_identifier=cenovy-lider-zlacnuje-platne-od-02-02-2026",
                extra_headers=schwarz_hdrs,
            )
            today_str = str(today)
            for rf in cl.get("flyer", cl).get("relatedFlyers", []):
                rf_slug  = rf.get("slug", "")
                rf_start = rf.get("startDate", "")[:10]
                rf_end   = rf.get("endDate",   "")[:10]
                if ("online-letak" in rf_slug
                        and rf_start <= today_str <= rf_end):
                    meta = _fetch_json(
                        f"https://endpoints.leaflets.schwarz/v4/flyer"
                        f"?flyer_identifier={rf_slug}",
                        extra_headers=schwarz_hdrs,
                    )
                    print(f"  ↳ Discovered active flyer: {rf_slug}")
                    break
        except Exception as e2:
            print(f"  ⚠  Flyer discovery: {e2}")

    if not meta:
        print("  ✗ No Schwarz flyer found")
        return 0

    flyer   = meta.get("flyer", meta)
    prods   = flyer.get("products", {})
    v_from  = (flyer.get("offerStartDate", "") or "")[:10]
    v_until = (flyer.get("offerEndDate",   "") or "")[:10]

    added = 0
    items = prods.items() if isinstance(prods, dict) else enumerate(prods)
    for _, p in items:
        if not isinstance(p, dict):
            continue
        name      = _clean(p.get("title", "") or p.get("name", ""))
        price_raw = p.get("price", "")
        try:
            price_val = float(str(price_raw).replace(",", "."))
        except (ValueError, TypeError):
            continue
        if price_val <= 0 or not name:
            continue

        key = name[:50].lower()
        if key in seen:
            continue
        seen.add(key)

        url = p.get("url", "") or p.get("canonicalUrl", "")
        if url and not url.startswith("http"):
            url = "https://www.lidl.sk" + url

        cat = (p.get("categoryPrimary", "") or p.get("wonCategoryPrimary", ""))

        prod = _make_product(
            name         = name,
            price_val    = price_val,
            old_price    = 0,
            pct_disc     = 0,
            image        = p.get("image", ""),
            url          = url,
            category_raw = cat,
            valid_from   = v_from,
            valid_until  = v_until,
        )
        if prod:
            products.append(prod)
            added += 1

    print(f"  ✓ Schwarz flyer: {added} new non-food products "
          f"({v_from} – {v_until})")
    return added


# ═══════════════════════════════════════════════════════════════════════════════
#  Entry point
# ═══════════════════════════════════════════════════════════════════════════════

def scrape() -> list[dict]:
    products: list[dict] = []
    seen:     set[str]   = set()   # deduplicate by first 50 chars of name (lowercased)

    # Stage 1 — direct API (fast, no browser)
    products, seen = try_direct_api()

    # Stage 2 — Playwright interception (slower, may find additional items)
    try_playwright(products, seen)

    # Stage 3 — Schwarz leaflets API (non-food items)
    try_schwarz_flyer(products, seen)

    print(f"\n  ✓ Lidl total: {len(products)} unique products scraped")
    return products


def deduplicate(products: list[dict]) -> list[dict]:
    seen: set[tuple] = set()
    out:  list[dict] = []
    for p in products:
        key = (p["name"][:60].lower(), round(p["price"], 2))
        if key not in seen:
            seen.add(key)
            out.append(p)
    return out


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

        print("\nFirst 12 products:")
        for p in items[:12]:
            orig = f"  (bolo {p['original_price']})" if p.get("original_price") else ""
            print(f"  [{p['category']:<22}] {p['name'][:55]:<55}  {p['price_str']}{orig}")
