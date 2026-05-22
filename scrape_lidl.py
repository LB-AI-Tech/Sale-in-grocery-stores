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
#  Stage 4 — kompaszliav.sk supplement (leaflet food items via their OCR)
# ═══════════════════════════════════════════════════════════════════════════════

_KOMPAS_BASE = "https://kompaszliav.sk"

# Category slugs to fetch from kompaszliav.sk filtered to Lidl.
# These cover weekly food promotions that aren't in Lidl's online catalog.
_KOMPAS_FOOD_SLUGS = [
    "mlieko", "bezlaktozove-mlieko", "jogurt", "maslo", "smotana", "tvaroh",
    "maso", "sunka", "salama", "klobasa", "ryba",
    "chlieb", "pecivo", "sladke-pecivo",
    "ryza", "cestoviny",
    "ovocie", "zelenina",
    "kava", "jacobs", "instantna-kava", "caj", "pivo", "vino", "limonada", "voda",
    "cokolada", "oblatka", "susienky", "chipsy",
    "olej", "olivovy-olej",
    "vajcia", "mrazene", "hotove-jedlo",
]

_KOMPAS_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept":          "text/html,*/*",
    "Accept-Language": "sk-SK,sk;q=0.9",
    "Accept-Encoding": "gzip, deflate",
    "Referer":         "https://kompaszliav.sk/",
}

_KOMPAS_PRICE_RE = re.compile(r"(\d[\d\s]*[,\.]\d{2})\s*€")
_KOMPAS_DATE_RE  = re.compile(
    r"(\d{1,2}\.\s*\d{1,2}\.(?:\s*\d{4})?)"
    r"\s*[-–]\s*"
    r"(\d{1,2}\.\s*\d{1,2}\.(?:\s*\d{4})?)"
)


def _kompas_fetch_html(url: str) -> str:
    from urllib.parse import urlsplit, urlunsplit, quote
    parts = urlsplit(url)
    url   = urlunsplit((parts.scheme, parts.netloc,
                        quote(parts.path, safe="/%"), parts.query, parts.fragment))
    try:
        req = urllib.request.Request(url, headers=_KOMPAS_HEADERS)
        with urllib.request.urlopen(req, context=_SSL_CTX, timeout=20) as r:
            raw = r.read()
            if r.headers.get("Content-Encoding", "") == "gzip":
                raw = gzip.decompress(raw)
            return raw.decode("utf-8", errors="replace")
    except Exception:
        return ""


def _kompas_parse_products(html: str) -> list[dict]:
    """
    Parse kompaszliav.sk product-card HTML for Lidl products only.

    Card structure:
      <a class="product-card" href="...">
        <div class="product-card__header">
          <div class="store-wrapper"><img alt="logo - {Store}"></div>
          <img alt="{Name}" src="{image}">
        </div>
        <div class="product-card__content">
          <div class="product-store">{Store}</div>
          <div class="product-availability">{dates}</div>
          <span class="product-price">{price}</span>
          <div class="product-card__monitoring-data"><span>{Name}</span></div>
        </div>
      </a>
    """
    from html.parser import HTMLParser

    class _Parser(HTMLParser):
        def __init__(self):
            super().__init__()
            self.products: list[dict] = []
            self._in_card = False
            self._cur: dict | None = None
            self._ctx = ""
            self._img_idx = 0

        @staticmethod
        def _cls(attrs_d):
            return set((attrs_d.get("class") or "").split())

        def handle_starttag(self, tag, attrs):
            a = dict(attrs)
            c = self._cls(a)
            if tag == "a" and "product-card" in c and not self._in_card:
                href = a.get("href", "")
                self._in_card = True
                self._cur = {"link": href, "store": "", "name": "", "valid_from": "",
                             "valid_until": "", "price_str": "", "image": ""}
                self._img_idx = 0
                return
            if not self._in_card or self._cur is None:
                return
            if tag == "div":
                if "product-store" in c:              self._ctx = "store"
                elif "product-availability" in c:     self._ctx = "avail"
                elif "product-card__monitoring-data" in c: self._ctx = "name"
            elif tag == "span" and "product-price" in c: self._ctx = "price"
            elif tag == "img":
                src = a.get("src", "")
                alt = (a.get("alt") or a.get("title") or "").strip()
                if self._img_idx == 0:
                    store = re.sub(r"^logo\s*-\s*", "", alt, flags=re.I).strip()
                    if store: self._cur["store"] = store
                elif self._img_idx == 1:
                    full = src if src.startswith("http") else _KOMPAS_BASE + src
                    self._cur["image"] = full
                    if not self._cur["name"] and alt:
                        self._cur["name"] = alt
                self._img_idx += 1

        def handle_endtag(self, tag):
            if self._in_card and tag == "a":
                self._in_card = False
                self._ctx = ""
                self._save()
                self._cur = None
            elif tag in ("div", "span"):
                self._ctx = ""

        def handle_data(self, data):
            if not self._in_card or not self._cur or not self._ctx:
                return
            t = data.strip()
            if not t: return
            if self._ctx == "store":   self._cur["store"] = t
            elif self._ctx == "avail":
                m = _KOMPAS_DATE_RE.match(t)
                if m:
                    self._cur["valid_from"]  = m.group(1).strip()
                    self._cur["valid_until"] = m.group(2).strip()
            elif self._ctx == "price": self._cur["price_str"] = t
            elif self._ctx == "name":  self._cur["name"] = t

        def _save(self):
            c = self._cur
            if not c: return
            name = c["name"].strip()
            if not name: return
            try:
                pv = float(c["price_str"].replace("€","").replace(",",".").replace("\xa0","").strip())
            except ValueError:
                return
            if pv <= 0: return
            self.products.append({
                "store":       c["store"],
                "name":        name,
                "price":       pv,
                "price_str":   c["price_str"],
                "valid_from":  c["valid_from"],
                "valid_until": c["valid_until"],
                "image":       c["image"],
                "link":        c["link"],
            })

    p = _Parser()
    p.feed(html)
    return [x for x in p.products if x["store"].lower() in ("lidl", "")]


def _kompas_to_product(item: dict) -> dict | None:
    """Convert a kompaszliav.sk product dict to our standard schema."""
    # Strip leading promotional markers kompaszliav uses (e.g. "** Jacobs Krönung")
    raw_name = re.sub(r"^[\s*#!]+", "", item["name"])
    name = _clean(raw_name)
    if not name:
        return None

    # Parse dates: kompaszliav uses "18.5." or "18.5.2026" format
    def _iso(s: str) -> str:
        s = s.strip().replace(" ", "")
        # "18.5.2026" or "18.5."
        m = re.match(r"(\d{1,2})\.(\d{1,2})\.(\d{4})?", s)
        if not m:
            return ""
        d, mo, yr = m.groups()
        yr = yr or str(date.today().year)
        return f"{yr}-{int(mo):02d}-{int(d):02d}"

    vf  = _iso(item.get("valid_from",  ""))
    vu  = _iso(item.get("valid_until", ""))

    image = item.get("image", "")
    url   = item.get("link",  "https://kompaszliav.sk/predajcovia/lidl")

    return _make_product(
        name         = name,
        price_val    = item["price"],
        old_price    = 0,
        pct_disc     = 0,
        image        = image,
        url          = url,
        category_raw = "",
        valid_from   = vf,
        valid_until  = vu,
    )


def try_kompas_supplement(products: list, seen: set) -> int:
    """
    Stage 4: Scrape kompaszliav.sk for Lidl food category pages.
    Adds products that are in the physical weekly leaflet (OCR-extracted)
    but not in Lidl's online catalog.
    Returns count of newly added products.
    """
    print("\n── Stage 4: kompaszliav.sk food supplement ──")
    import time as _time

    added  = 0
    errors = 0

    for slug in _KOMPAS_FOOD_SLUGS:
        url  = f"{_KOMPAS_BASE}/produkty/{slug}?store=lidl"
        html = _kompas_fetch_html(url)
        if not html:
            errors += 1
            continue

        items = _kompas_parse_products(html)
        for item in items:
            prod = _kompas_to_product(item)
            if not prod:
                continue
            key = prod["name"][:50].lower()
            if key in seen:
                continue
            seen.add(key)
            products.append(prod)
            added += 1

        _time.sleep(0.3)   # polite throttle

    print(f"  ✓ kompaszliav.sk: {added} nových potravinových produktov"
          f"  ({errors} kategórií zlyhalo)")
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

    # Stage 4 — kompaszliav.sk supplement (leaflet food items via their OCR)
    # This catches Cenový líder food items (Olivový olej, Bezlaktózové mlieko,
    # Jacobs Krönung, …) and fresh-counter items that are not in Lidl's online
    # catalog at all.  kompaszliav.sk extracts them via OCR from the Schwarz
    # leaflet page images.
    try_kompas_supplement(products, seen)

    print(f"\n  ✓ Lidl total: {len(products)} unique products scraped")
    return products


# ═══════════════════════════════════════════════════════════════════════════════
#  Stage 4 — Parse Cenový líder products from flyer keyWords
# ═══════════════════════════════════════════════════════════════════════════════
# The weekly flyer's Cenový líder pages contain OCR text with product names
# and superscript price digits (⁰¹²³⁴⁵⁶⁷⁸⁹).
#
# Limitation: Lidl's online search returns "no results" for Cenový líder food
# items (Jacobs Krönung, Olivový olej, Bezlaktózové mlieko) — they exist only
# in the physical in-store leaflet, not in Lidl's online catalog.
#
# What we CAN reliably extract:
#   - Sub-€1 items: superscript IS the full price (e.g. ⁸⁹ → 0.89 €)
#   - Names + discount percentages for all items (even where price is unknown)
# What we CANNOT extract:
#   - Multi-euro items where only the cents digit is in the superscript (e.g. Jacobs
#     Krönung 7.49 € shows only ⁴⁹ in OCR — the integer "7" is lost)

_SUPERSCRIPT_MAP = str.maketrans("⁰¹²³⁴⁵⁶⁷⁸⁹", "0123456789")

def _decode_superscripts(text: str) -> str:
    """Replace superscript digit chars with regular ASCII equivalents."""
    return text.translate(_SUPERSCRIPT_MAP)


def _parse_kw_products(keywords_by_page: list[tuple[int, str]]) -> list[dict]:
    """
    Extract (name, price, discount_pct, valid_from, valid_until) tuples from
    a list of (page_number, keyword_text) pairs.

    Pattern in keyWords:
      -34 Jacobs Krönung Mletá Káva ⁴⁹  -18 Bezlaktózové Mlieko 15 Tuku ⁸⁹
      Discount numbers are negative integers in normal digits.
      Prices are superscript cents — full price when < 1 €, decimal only otherwise.
    """
    results = []
    disc_re  = re.compile(r'-\d{1,3}')   # e.g. -34, -18
    sup_re   = re.compile(r'[⁰¹²³⁴⁵⁶⁷⁸⁹]+')  # one or more superscript digits
    # Words to skip as "noise" from the OCR boilerplate text
    _NOISE = {
        'ponuka', 'tovaru', 'letáku', 'platí', 'vypredania', 'chyby', 'tlači',
        'ceny', 'sú', 'firma', 'lidl', 'vyhradzuje', 'právo', 'zmien',
        'baleniach', 'variantoch', 'ponúkaného', 'odber', 'možný', 'obvyklom',
        'región', 'dlhodobo', 'zlacnené', 'prehľad', 'zlacnených', 'vybraných',
        'produktov', 'ktoré', 'boli', 'priebehu', 'týždňov', 'období',
        'potvrdzuje', 'výskum', 'agentúry', 'nms', 'market', 'research',
        'slovakia', 'vykonaný', 'dňoch', 'reprezentatívnej', 'vzorke',
        'cenový', 'líder', 'znižuje', 'bežné', 'supercena', 'ušetrite',
        'iba', 'kus', 'ďalších', 'teraz', 'platnosti', 'akciových', 'cien',
        'zistených', 'zberom', 'dňa', 'odporúčanej', 'maloobchodnej',
        'predajnej', 'cene', 'nms', 'porovnaní', 'produktom', 'štandardne',
        'ponúkanej', 'veľkosti', 'jednotkovej', 'jednotka',
    }

    for _page_no, kw_raw in keywords_by_page:
        kw = _decode_superscripts(kw_raw).strip()
        # Split on discount markers to get segments like "34 Jacobs Krönung Mletá Káva 49"
        # We work on the full text and look for patterns: [discount] Name [price_cents]
        segments = re.split(r'(?=-\d{1,3}\s)', kw)
        for seg in segments:
            seg = seg.strip()
            if not seg:
                continue
            # Extract discount
            dm = disc_re.match(seg)
            if dm:
                discount = abs(int(dm.group()))
                rest = seg[dm.end():].strip()
            else:
                discount = 0
                rest = seg

            # Extract trailing price (2-3 digits at end of the name segment,
            # before the next product name or end of text)
            # After decoding superscripts, price looks like normal digits
            # We look for a 1-3 digit number at the end
            price_m = re.search(r'\s(\d{1,3})\s*$', rest)
            if price_m:
                cents_str = price_m.group(1)
                name_raw  = rest[:price_m.start()].strip()
                cents     = int(cents_str)
                # Heuristic: if cents value ≤ 99, it might be the full price in cents
                # (sub-€1 item) or the decimal part of a multi-euro price.
                # We can't tell for sure — we store what we have.
                price_full = cents / 100  # treat as full price initially
            else:
                name_raw  = rest.strip()
                price_full = None
                cents      = None

            # Clean name: remove noise words and short tokens
            name_words = [w for w in name_raw.split()
                          if w.lower() not in _NOISE and len(w) >= 2
                          and not w.isdigit()]
            name = ' '.join(name_words).strip()
            if len(name) < 5:
                continue

            results.append({
                'name':         name,
                'price':        price_full,
                'discount_pct': discount,
                'cents_only':   cents,   # just the decimal cents (may be partial)
            })

    return results


def try_cenovy_lider_keywords(products: list, seen: set) -> int:
    """
    Stage 4: Parse weekly flyer's Cenový líder pages (keyWords) to extract
    food items not available in the search API.

    NOTE: Lidl's online search returns zero results for these products — they
    exist only in the physical in-store leaflet.  We extract what we can:
    - Sub-€1 items: superscript IS the full price (⁸⁹ → 0.89 €)
    - Multi-€ items: only the cents part is captured (⁴⁹ → ?.49 €) — skipped

    Returns count of new products added.
    """
    schwarz_hdrs = {
        "User-Agent": _HEADERS["User-Agent"],
        "Accept":     "application/json",
        "Referer":    "https://www.lidl.sk/",
    }

    print("\n── Stage 4: Cenový líder keyWords (sub-€1 items) ──")

    valid_from = valid_until = ""
    weekly_kw_pages: list[tuple[int, str]] = []

    try:
        today    = date.today()
        week_mon = today - timedelta(days=today.weekday())
        slug     = (f"online-letak-platny-od-"
                    f"{week_mon.day:02d}-{week_mon.month:02d}-{week_mon.year}")
        wf = _fetch_json(
            f"https://endpoints.leaflets.schwarz/v4/flyer?flyer_identifier={slug}",
            extra_headers=schwarz_hdrs,
        )
        wf_flyer = wf.get("flyer", wf)
        valid_from  = (wf_flyer.get("offerStartDate", "") or "")[:10]
        valid_until = (wf_flyer.get("offerEndDate",   "") or "")[:10]

        for i, p in enumerate(wf_flyer.get("pages", [])):
            kw_raw = p.get("keyWords", "")
            # Only process pages that are Cenový líder / long-term reduction sections
            if not kw_raw:
                continue
            try:
                kw = kw_raw.encode("latin-1").decode("utf-8")
            except Exception:
                kw = kw_raw
            if "Dlhodobo Zlacnené" in kw or "Cenový Líder" in kw:
                weekly_kw_pages.append((i + 1, kw))

        print(f"  Weekly flyer ({slug}): {len(weekly_kw_pages)} Cenový líder pages")
    except Exception as e:
        print(f"  ⚠  Weekly flyer fetch: {e}")
        return 0

    if not weekly_kw_pages:
        print("  ✗ No Cenový líder pages found in weekly flyer")
        return 0

    kw_products = _parse_kw_products(weekly_kw_pages)
    added = 0

    for kp in kw_products:
        name      = kp["name"]
        price_val = kp["price"]
        cents     = kp["cents_only"]

        # Only add items where we can reliably determine the price:
        # - price is between 0.15 € and 0.99 € (clearly sub-€1)
        # - name is meaningful (≥ 4 chars after cleaning)
        if price_val is None or price_val < 0.15 or price_val > 0.99:
            continue
        if len(name) < 4:
            continue

        key = name[:50].lower()
        if key in seen:
            continue
        seen.add(key)

        p = _make_product(
            name         = name,
            price_val    = price_val,
            old_price    = 0,
            pct_disc     = kp["discount_pct"],
            image        = "",
            url          = "https://www.lidl.sk",
            category_raw = "",
            valid_from   = valid_from,
            valid_until  = valid_until,
        )
        if p:
            products.append(p)
            added += 1
            print(f"    + {name[:55]:<55}  {price_val:.2f} €"
                  f"  (disc {kp['discount_pct']}%)")

    print(f"  ✓ Stage 4: {added} new sub-€1 products from keyWords")
    if added == 0:
        print("  ℹ  Note: multi-euro Cenový líder items (Jacobs Krönung, Olivový olej, etc.)")
        print("     are NOT in Lidl's online catalog — they cannot be scraped automatically.")
    return added


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
