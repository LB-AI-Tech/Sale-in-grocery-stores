#!/usr/bin/env python3
"""
kompaszliav.sk scraper  —  research/comparison tool
====================================================
Fetches all products from kompaszliav.sk by iterating:
  1. Alphabetical category pages (/produkty/a … /produkty/z + special letters)
  2. Key explicit category slugs (mlieko, maso, kava, …)

Output: kompas_products.json  — list of product dicts
        kompas_by_store.json  — products grouped by store name

Usage:
    python scrape_kompas.py
"""

import json
import os
import re
import sys
import time
import urllib.request
import urllib.error
import gzip
import ssl
from html.parser import HTMLParser

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass
os.environ["PYTHONIOENCODING"] = "utf-8"

BASE_URL   = "https://kompaszliav.sk"
OUTPUT_ALL  = "kompas_products.json"
OUTPUT_STORE = "kompas_by_store.json"

_SSL_CTX = ssl.create_default_context()
_SSL_CTX.check_hostname = False
_SSL_CTX.verify_mode    = ssl.CERT_NONE

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept":          "text/html,application/xhtml+xml,*/*",
    "Accept-Language": "sk-SK,sk;q=0.9,cs;q=0.8",
    "Accept-Encoding": "gzip, deflate",
}

# ── Alphabetical pages ────────────────────────────────────────────────────────
ALPHA_SLUGS = [
    "0-9", "a", "b", "c", "%C4%8D",  # č
    "d", "e", "f", "g", "h", "ch",
    "i", "j", "k", "l", "m", "n",
    "o", "p", "r", "s", "%C5%A1",    # š
    "t", "%C3%BA",                    # ú
    "v", "z", "%C5%BE",              # ž
]

# ── Explicit popular category slugs ──────────────────────────────────────────
CATEGORY_SLUGS = [
    # Mliečne výrobky
    "mlieko", "jogurt", "maslo", "smotana", "tvaroh", "syry",
    # Mäso + údeniny
    "maso", "kuracie-maso", "hovadzie-maso",
    "sunka", "salama", "klobasa", "varene-maso", "ryba",
    # Pečivo + obilniny
    "chlieb", "pecivo", "sladke-pecivo", "ryza", "cestoviny",
    # Nápoje
    "pivo", "vino", "alkohol", "kava", "caj", "nápoj",
    "limonada", "voda", "dzus",
    # Cukrovinky
    "cokolada", "sladkosti", "cukrovinky", "oblatka", "susienky",
    "chipsy", "slane-pecivo",
    # Tuky, kondimenty
    "olej", "olivovy-olej", "masť",
    # Zelenina + ovocie
    "ovocie", "zelenina",
    # Mrazené, hotové jedlá
    "mrazene", "hotove-jedlo", "pizza",
    # Vajcia
    "vajcia",
    # Drogéria
    "drogeria", "praci-prostriedok", "avivaz", "zubna-pasta",
    "sprchovaci-gel", "sampon", "toaletny-papier",
    # Ostatné potraviny
    "konzervy", "omacka", "korenie",
]


# ═══════════════════════════════════════════════════════════════════════════════
#  HTML fetcher
# ═══════════════════════════════════════════════════════════════════════════════

def _fetch_html(url: str, retries: int = 3, delay: float = 1.5) -> str:
    # Encode any non-ASCII chars in the path (slugs with Slovak diacritics)
    from urllib.parse import urlsplit, urlunsplit, quote
    parts = urlsplit(url)
    safe_path = quote(parts.path, safe="/%")
    url = urlunsplit((parts.scheme, parts.netloc, safe_path, parts.query, parts.fragment))

    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=_HEADERS)
            with urllib.request.urlopen(req, context=_SSL_CTX, timeout=20) as resp:
                raw = resp.read()
                enc = resp.headers.get("Content-Encoding", "")
                if enc == "gzip":
                    raw = gzip.decompress(raw)
                charset = "utf-8"
                ct = resp.headers.get("Content-Type", "")
                m = re.search(r"charset=([^\s;]+)", ct)
                if m:
                    charset = m.group(1)
                return raw.decode(charset, errors="replace")
        except urllib.error.HTTPError as e:
            if e.code == 429:
                wait = delay * (2 ** attempt)
                print(f"    429 – čakám {wait:.0f}s …", end=" ", flush=True)
                time.sleep(wait)
                print("pokračujem")
            elif e.code in (404, 410):
                return ""
            else:
                print(f"    HTTP {e.code} for {url}")
                return ""
        except Exception as e:
            if attempt < retries - 1:
                time.sleep(delay)
            else:
                print(f"    ✗ {url}: {e}")
                return ""
    return ""


# ═══════════════════════════════════════════════════════════════════════════════
#  HTML parser — extracts product cards
# ═══════════════════════════════════════════════════════════════════════════════

class ProductCardParser(HTMLParser):
    """
    Extracts product card <a class="product-card"> elements.

    Actual HTML structure:
      <a href="..." class="product-card ">
        <div class="product-card__header">
          <div class="store-wrapper">
            <img alt="logo - {StoreName}" title="{StoreName}">
          </div>
          <img src="{product-img}" alt="{ProductName}" title="{ProductName}">
        </div>
        <div class="product-card__content">
          <div class="product-card__info">
            <div class="product-card__info__inner-wrapper">
              <div class="product-store">{StoreName}</div>
              <div class="product-availability">{ValidFrom} - {ValidTo}</div>
            </div>
            <div><span class="product-price">{Price}</span></div>
          </div>
          <div class="product-card__monitoring-data">
            <span>{ProductName}</span>
          </div>
        </div>
      </a>
    """

    def __init__(self):
        super().__init__()
        self.products: list[dict] = []
        # parser state
        self._in_card      = False
        self._cur: dict | None = None
        # which semantic element are we inside?
        self._ctx: str     = ""          # "store"|"avail"|"price"|"name"
        self._img_idx      = 0           # img counter inside current card

    # ─────────────────────────────────────────────────────────────────────────
    @staticmethod
    def _classes(attrs_d: dict) -> set[str]:
        return set((attrs_d.get("class") or "").split())

    def handle_starttag(self, tag, attrs):
        attrs_d = dict(attrs)
        cls     = self._classes(attrs_d)

        # ── Enter card ──
        if tag == "a" and "product-card" in cls and not self._in_card:
            href = attrs_d.get("href", "")
            self._in_card = True
            self._cur     = {
                "link":        href if href.startswith("http") else BASE_URL + href,
                "store":       "",
                "name":        "",
                "valid_from":  "",
                "valid_until": "",
                "price_str":   "",
                "price":       0.0,
                "image":       "",
            }
            self._img_idx = 0
            return

        if not self._in_card or self._cur is None:
            return

        # ── Inside card: semantic divs ──
        if tag == "div":
            if "product-store" in cls:
                self._ctx = "store"
            elif "product-availability" in cls:
                self._ctx = "avail"
            elif "product-card__monitoring-data" in cls:
                self._ctx = "name"
            # other divs don't change context

        elif tag == "span":
            if "product-price" in cls:
                self._ctx = "price"

        elif tag == "img":
            src = attrs_d.get("src", "")
            alt = (attrs_d.get("alt") or attrs_d.get("title") or "").strip()
            if self._img_idx == 0:
                # first img = store logo  (alt = "logo - StoreName")
                store_name = re.sub(r"^logo\s*-\s*", "", alt, flags=re.I).strip()
                if store_name:
                    self._cur["store"] = store_name
            elif self._img_idx == 1:
                # second img = product image
                full = src if src.startswith("http") else BASE_URL + src
                self._cur["image"] = full
                if not self._cur["name"] and alt:
                    self._cur["name"] = alt   # prefill from alt; overwrite from monitoring-data
            self._img_idx += 1

    def handle_endtag(self, tag):
        # Product cards are never nested, so the first </a> always closes the card
        if self._in_card and tag == "a":
            self._in_card = False
            self._ctx     = ""
            self._finalise_card()
            self._cur = None
            return
        # Reset context when leaving a semantic element
        if tag in ("div", "span"):
            self._ctx = ""

    def handle_data(self, data):
        if not self._in_card or self._cur is None or not self._ctx:
            return
        text = data.strip()
        if not text:
            return
        ctx = self._ctx
        if ctx == "store":
            self._cur["store"] = text
        elif ctx == "avail":
            # "21.5. - 27.5.2026" or "21.5. - 27.5."
            m = re.match(
                r"(\d{1,2}\.\s*\d{1,2}\.(?:\s*\d{4})?)"
                r"\s*[-–]\s*"
                r"(\d{1,2}\.\s*\d{1,2}\.(?:\s*\d{4})?)",
                text,
            )
            if m:
                self._cur["valid_from"]  = m.group(1).strip()
                self._cur["valid_until"] = m.group(2).strip()
        elif ctx == "price":
            self._cur["price_str"] = text
        elif ctx == "name":
            self._cur["name"] = text   # authoritative name from monitoring-data

    # ─────────────────────────────────────────────────────────────────────────
    def _finalise_card(self):
        c = self._cur
        if not c:
            return
        name = c["name"].strip()
        price_str = c["price_str"].strip()
        try:
            price_val = float(
                price_str.replace("€", "").replace(",", ".").replace("\xa0", "").strip()
            )
        except ValueError:
            price_val = 0.0

        if not name or price_val <= 0:
            return

        self.products.append({
            "store":       c["store"],
            "name":        name,
            "price":       price_val,
            "price_str":   price_str,
            "valid_from":  c["valid_from"],
            "valid_until": c["valid_until"],
            "image":       c["image"],
            "link":        c["link"],
        })


def parse_products(html: str) -> list[dict]:
    parser = ProductCardParser()
    parser.feed(html)
    return parser.products


# ═══════════════════════════════════════════════════════════════════════════════
#  Deduplication
# ═══════════════════════════════════════════════════════════════════════════════

def _dedup_key(p: dict) -> str:
    return f"{p['store']}|{p['name'][:50].lower()}|{p['price']}"


# ═══════════════════════════════════════════════════════════════════════════════
#  Main scrape
# ═══════════════════════════════════════════════════════════════════════════════

def scrape() -> list[dict]:
    all_products: list[dict] = []
    seen: set[str] = set()

    def _ingest(prods: list[dict], source_label: str):
        added = 0
        for p in prods:
            k = _dedup_key(p)
            if k not in seen:
                seen.add(k)
                all_products.append(p)
                added += 1
        return added

    total_pages = 0

    # ── 1. Alphabetical pages ─────────────────────────────────────────────────
    print("\n═══ Fáza 1: Abecedné stránky ═══")
    for slug in ALPHA_SLUGS:
        url = f"{BASE_URL}/produkty/{slug}"
        print(f"  {url} … ", end="", flush=True)
        html = _fetch_html(url)
        if not html:
            print("prázdna")
            continue
        prods = parse_products(html)
        n = _ingest(prods, f"alpha/{slug}")
        total_pages += 1
        print(f"{n} nových  (celkom {len(all_products)})")
        time.sleep(0.4)   # polite throttle

    # ── 2. Category-slug pages ────────────────────────────────────────────────
    print("\n═══ Fáza 2: Kategórie ═══")
    seen_category_slugs: set[str] = set()
    for slug in CATEGORY_SLUGS:
        if slug in seen_category_slugs:
            continue
        seen_category_slugs.add(slug)
        url = f"{BASE_URL}/produkty/{slug}"
        print(f"  {url} … ", end="", flush=True)
        html = _fetch_html(url)
        if not html:
            print("prázdna/404")
            continue
        prods = parse_products(html)
        n = _ingest(prods, f"cat/{slug}")
        total_pages += 1
        print(f"{n} nových  (celkom {len(all_products)})")
        time.sleep(0.4)

    print(f"\n═══ Hotovo: {len(all_products)} produktov z {total_pages} stránok ═══")
    return all_products


# ═══════════════════════════════════════════════════════════════════════════════
#  Output
# ═══════════════════════════════════════════════════════════════════════════════

def save(products: list[dict]):
    # Flat list
    out_dir = os.path.dirname(os.path.abspath(__file__))
    path_all = os.path.join(out_dir, OUTPUT_ALL)
    with open(path_all, "w", encoding="utf-8") as f:
        json.dump(products, f, ensure_ascii=False, indent=2)
    print(f"  ✓ {path_all}  ({len(products)} produktov)")

    # Group by store
    by_store: dict[str, list[dict]] = {}
    for p in products:
        store = p["store"] or "Neznámy"
        by_store.setdefault(store, []).append(p)

    # Sort stores by product count descending
    by_store_sorted = dict(
        sorted(by_store.items(), key=lambda x: len(x[1]), reverse=True)
    )

    path_store = os.path.join(out_dir, OUTPUT_STORE)
    with open(path_store, "w", encoding="utf-8") as f:
        json.dump(by_store_sorted, f, ensure_ascii=False, indent=2)

    print(f"  ✓ {path_store}")
    print()
    print("  Produkty podľa obchodu:")
    for store, prods in by_store_sorted.items():
        print(f"    {store:<30}  {len(prods):>4} produktov")


if __name__ == "__main__":
    print("╔══════════════════════════════════════════════════╗")
    print("║  kompaszliav.sk — porovnávací scraper            ║")
    print("╚══════════════════════════════════════════════════╝")
    products = scrape()
    save(products)
