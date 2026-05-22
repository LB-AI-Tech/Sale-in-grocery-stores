#!/usr/bin/env python3
"""
COOP SK scraper — v1
====================
Strategy
--------
COOP SK has no public API.  Weekly promotions are PDF leaflets served at:

  https://www.coop.sk/files/pdf-file/flip_{ID}/pdf_{ID}.pdf

The current leaflet IDs are embedded in /sk/pdfflip/list as dFlip viewer
configuration attributes.

Text extraction uses pdfplumber.  The reliable parsing anchor is the phrase
"jednotková cena X,XX EUR/<unit>" which appears immediately after each
large-font promotional price on every product entry.  Working backwards from
that anchor yields the product name and price without any positional guessing.

Why not Playwright?
-------------------
The dFlip viewer renders one page at a time, requiring 14-28 page-flip
interactions (~60 s) to reach all 28 pages.  pdfplumber gives the same
quality text in ~2 s because the PDF already has a proper Unicode text layer
(confirmed: Slovak diacritics extracted correctly, no OCR needed).

Output
------
coop_products.json  —  list of product dicts in the shared server.py schema.
"""

import gzip, io, json, os, re, sys, urllib.request, urllib.error, ssl

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

try:
    import pdfplumber
except ImportError:
    print("✗ pdfplumber not installed.  Run: pip install pdfplumber")
    sys.exit(1)

OUTPUT_FILE = "coop_products.json"
BASE_URL    = "https://www.coop.sk"
LIST_PATH   = "/sk/pdfflip/list"
LETAK_URL   = BASE_URL + LIST_PATH    # canonical URL to link back to

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept":          "text/html,application/xhtml+xml,*/*;q=0.9",
    "Accept-Language": "sk-SK,sk;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Referer":         "https://www.coop.sk/",
    "Sec-Fetch-Dest":  "document",
    "Sec-Fetch-Mode":  "navigate",
    "Sec-Fetch-Site":  "same-origin",
}

_SSL_CTX = ssl.create_default_context()
_SSL_CTX.check_hostname = False
_SSL_CTX.verify_mode    = ssl.CERT_NONE

# Words to strip when cleaning product names (matched case-insensitively via .lower())
_NOISE_WORDS = {
    # Promotional / marketing
    "tradičná", "kvalita", "ilustračné", "foto", "dobrá", "naša", "novinka",
    "super", "výhodné", "teraz", "novom", "obale", "akcia",
    "nakúpte", "vyhrajte", "stovky", "cien", "autá", "vitamíny",
    "výhodnejšie", "zelovoc", "grile",
    "fantastická", "premium", "fantastický", "fantastické",
    # Loyalty-card / pricing labels
    "vernostnou", "kartou", "vernostnej", "karty", "vernostná",
    "podiel", "tomu", "ver",
    # Availability / legal
    "platí", "vypredania", "zásob", "predajniach", "dostupné", "voľné",
    "balené", "dostupná", "ponuka", "nájdete", "iba", "tovarov",
    "dní", "uvedená", "môže", "byť", "označená", "piktogramom",
    "ceny", "niektorých", "platia", "dátumu", "uvedeného", "najnižšia",
    # Unit-price header words and their PDF text fragments
    "jednotková", "cena", "jedn", "tková", "tka", "tk",
    # Prepositions / conjunctions that leak in around date lines
    "od", "do", "na", "buď", "alebo", "viac", "info",
    # Store format labels
    "farebné", "logá", "jednotlivých", "formátov", "predajní", "umiestnené",
    # RTL-artifact tokens (InDesign right-to-left text layer)
    "pooc", "atonedj", "uokčanz", "enjaderp", "eickudorp", "otmýt", "moravot",
    "erp", "dop", "ítalpen",
    # Discount word (never part of a product name)
    "zľava",
    # Common abbreviations that are not part of names
    "eur", "kg", "bal", "por", "ks",
    # "Various types / kinds" — appears on nearly every COOP product card
    "druhy", "rôzne",
    # Bottle-deposit labelling ("záloh obal")
    "záloh", "obal",
}


# ── HTTP helpers ──────────────────────────────────────────────────────────────

def _fetch_bytes(url: str, extra_headers: dict | None = None) -> bytes:
    headers = {**_HEADERS, **(extra_headers or {})}
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=60, context=_SSL_CTX) as resp:
        raw = resp.read()
        if "gzip" in resp.headers.get("Content-Encoding", ""):
            raw = gzip.decompress(raw)
    return raw


# ── Leaflet discovery ─────────────────────────────────────────────────────────

def _get_leaflets() -> list[dict]:
    """Parse /sk/pdfflip/list for dFlip source attributes → leaflet records."""
    html = _fetch_bytes(BASE_URL + LIST_PATH).decode("utf-8", "replace")

    # <div class="_df_thumb" id="df_intro_thumb_1311"
    #      source="https://…/pdf_1311.pdf"
    #      thumb="https://…/pdf_1311_thumb.jpg">Tempo</div>
    leaflets = re.findall(
        r'id="df_intro_thumb_(\d+)"\s+'
        r'source="([^"]+)"\s+'
        r'thumb="([^"]+)"[^>]*>\s*([^<]+)',
        html,
    )
    result = [
        {
            "id":        lid,
            "pdf_url":   src.strip(),
            "thumb_url": thumb.strip(),
            "name":      name.strip() or f"COOP {lid}",
        }
        for lid, src, thumb, name in leaflets
    ]
    return result


# ── Date extraction ────────────────────────────────────────────────────────────

def _extract_dates(pdf) -> tuple[str, str]:
    """
    Scan first 3 pages for "Platí od 14. 5. do 20. 5. 2026" patterns.
    Returns (valid_from, valid_until) as ISO date strings.
    """
    text = " ".join(
        (page.extract_text() or "")
        for page in pdf.pages[:3]
    )
    m = re.search(
        r'(?:Platí\s+od\s+)?(\d{1,2})[.\s]+(\d{1,2})[.\s]+(\d{4})'
        r'.{0,20}?'
        r'do\s+(\d{1,2})[.\s]+(\d{1,2})[.\s]+(\d{4})',
        text, re.I | re.S,
    )
    if m:
        d1, mo1, y1, d2, mo2, y2 = m.groups()
        return (
            f"{y1}-{int(mo1):02d}-{int(d1):02d}",
            f"{y2}-{int(mo2):02d}-{int(d2):02d}",
        )
    return "", ""


# ── Product name cleaning ──────────────────────────────────────────────────────

def _clean_name(raw: str) -> str:
    """Strip noise, collapse whitespace, return clean product name."""
    # Drop percentage / discount markers
    raw = re.sub(r'\d+\s*%', '', raw)
    # Drop web URLs
    raw = re.sub(r'www\.\S+', '', raw)
    # Drop tokens that are only punctuation / symbols (e.g. "!!", "!!", "/k")
    raw = re.sub(r'\b[!?.,;:/\\()]+\b', '', raw)
    raw = re.sub(r'(?<!\w)[!?]{1,}(?!\w)', '', raw)  # standalone ! and !!
    # Drop noise words (whole-word match); drop RTL-artifact words (start with é/ó/ú as
    # those accented vowels never begin real Slovak words in product names)
    tokens = raw.split()
    tokens = [
        t for t in tokens
        if t.lower().rstrip('.,!') not in _NOISE_WORDS
        and not re.match(r'^[éóí]', t, re.I)             # reversed-text artifacts (é/ó/í never start Slovak product words)
        and not re.search(r'[a-záäčďíľĺňôŕšťúýž][A-ZÁÄČĎÉÍĽĹŇÓÔŔŠŤÚÝŽ]', t)  # internal uppercase = scrambled
    ]
    # Limit to first 6 meaningful words to prevent column bleed
    tokens = tokens[:6]
    raw = " ".join(tokens)
    # Collapse repeated whitespace / punctuation at boundaries
    raw = re.sub(r'\s{2,}', ' ', raw).strip().strip('.,!?')
    # Capitalise first letter
    if raw:
        raw = raw[0].upper() + raw[1:]
    return raw


def _parse_price_token(tok: str) -> float | None:
    """
    Convert compact price tokens to euros:
      '059' → 0.59,  '269' → 2.69,  '1299' → 12.99
    Returns None if the value is outside a plausible grocery range.
    """
    if not re.fullmatch(r'\d{3,4}', tok):
        return None
    v = int(tok) / 100
    return v if 0.09 <= v <= 99.99 else None


# Units-of-measure that mark a QUANTITY, not a price (e.g. "150 g", "0,5 l")
_QUANTITY_UNITS = {
    "g", "kg", "l", "ml", "cl", "dl", "ks", "bal", "bal.", "por", "por.",
    "kus", "k", "x",
}


# ── Per-page parser ────────────────────────────────────────────────────────────

def _parse_page(page, leaflet: dict, valid_from: str, valid_until: str) -> list[dict]:
    """
    Extract products from one PDF page using WORD POSITIONS.

    Why positional?
    ---------------
    COOP letáky have 2-3 products per row.  Linear text extraction
    interleaves adjacent columns, so "150 g" from one product's description
    ends up next to the price token of another.

    Strategy
    --------
    1. extract_words() → list of {text, x0, top, …}
    2. Find every "jednotková cena" pair → these are reliable product anchors.
    3. For each anchor (jx, jy):
       a. Price  = last 3-4 digit token within ±220 pt horizontally and
                  0–140 pt above jy that is NOT followed by a quantity unit.
       b. Name   = words in the same column band, between price_y-80 pt and
                  jy, stripped of noise.
       c. Disc%  = any "N%" token in the same column within 180 pt above jy.
    """
    words = page.extract_words(keep_blank_chars=False, x_tolerance=5, y_tolerance=3)
    if not words:
        return []

    # Index words for fast neighbour lookup
    word_texts_lower = [w["text"].lower().rstrip(".,") for w in words]

    # ── 1. Locate every "jednotková" + "cena" pair ───────────────────────────
    anchors: list[dict] = []
    for i, w in enumerate(words):
        if word_texts_lower[i] != "jednotková":
            continue
        if i + 1 < len(words) and word_texts_lower[i + 1] == "cena":
            # Grab unit info: words i+2 … i+5
            unit_tail = " ".join(words[j]["text"] for j in range(i + 2, min(i + 6, len(words))))
            unit_m    = re.search(r"EUR/(\S+)", unit_tail, re.I)
            unit_str  = unit_m.group(1).rstrip(").,") if unit_m else ""
            anchors.append({"jx": w["x0"], "jy": w["top"], "unit": unit_str})

    products: list[dict] = []
    used_prices: set[tuple] = set()  # (price_val, approx_y) to avoid duplicates

    for anc in anchors:
        jx, jy = anc["jx"], anc["jy"]

        # ── 2a. Find price token ─────────────────────────────────────────────
        # Collect candidates: 3-4 digit tokens in the same column.
        # Key insight from layout analysis:
        #  - Prices can appear AT or slightly below the anchor (same text line),
        #    not just above — allow up to +10pt below jy.
        #  - Year numbers like 2026 sit in the window but must be excluded.
        #  - Package sizes (150g, 400g) are filtered by checking next token.
        #  - Real prices are within ~100pt of the anchor horizontally.
        price_candidates: list[tuple] = []  # (dist_to_anchor, price_val, word_idx, word_top)
        for idx, w in enumerate(words):
            if w["top"] > jy + 10:
                continue                          # too far below anchor
            if w["top"] < jy - 160:
                continue                          # too far above anchor
            if abs(w["x0"] - jx) > 140:
                continue                          # tighter column window (was 220)

            if not re.fullmatch(r"\d{3,4}", w["text"]):
                continue

            # Filter year numbers (2020-2035 → parse as 20.20-20.35 €, wrong)
            tok_int = int(w["text"])
            if 2010 <= tok_int <= 2035:
                continue

            # Filter quantity numbers (e.g. "150" in "150 g", "400" in "400 g")
            if idx + 1 < len(words):
                nxt = word_texts_lower[idx + 1]
                if nxt in _QUANTITY_UNITS:
                    continue

            v = _parse_price_token(w["text"])
            if v is not None:
                dist = abs(jy - w["top"])         # absolute vertical distance
                price_candidates.append((dist, v, idx, w["top"]))

        if not price_candidates:
            continue

        # Prefer the candidate closest to the anchor
        price_candidates.sort(key=lambda x: x[0])
        _, price, price_idx, price_y = price_candidates[0]

        # Skip if we already used this price at the same vertical position
        dedup_key = (round(price, 2), round(price_y / 10))
        if dedup_key in used_prices:
            continue
        used_prices.add(dedup_key)

        # ── 2b. Product name ─────────────────────────────────────────────────
        # Words in the same column band, between (price_y - 80) and (price_y + 15)
        name_band_words = [
            w for wi, w in enumerate(words)
            if (price_y - 120 <= w["top"] <= price_y + 20)
            and abs(w["x0"] - jx) < 105                           # tight column window
            and wi != price_idx
            and word_texts_lower[wi] not in _NOISE_WORDS
            and not re.fullmatch(r"\d+\s*[%€]?", w["text"])      # pure numbers/percent
            and not re.search(r"\d+[,.]\d+", w["text"])           # decimal (unit prices)
            and not w["text"].upper().startswith("EUR")            # EUR/kg etc.
            and "EU" not in w["text"].upper()                      # EU/k fragments
            and not w["text"].startswith("/")                      # /kg /k fragments
            and not re.fullmatch(r"\d{1,2}\.", w["text"])         # date fragments (14. 5.)
            and not re.fullmatch(r"[!?.,;:/\\()]+", w["text"])   # punctuation-only tokens
            and not w["text"].startswith(",")                      # ,9 decimal fragments
            and len(w["text"]) > 2                                 # skip 1-2 char tokens (jedn frags)
        ]
        name_band_words.sort(key=lambda w: (w["top"], w["x0"]))
        raw_name = " ".join(w["text"] for w in name_band_words)

        # ── 2c. Discount % ───────────────────────────────────────────────────
        disc_context_words = [
            w["text"] for w in words
            if (jy - 200 <= w["top"] <= jy + 10)
            and abs(w["x0"] - jx) < 160
        ]
        disc_m   = re.search(r"(\d+)\s*%", " ".join(disc_context_words))
        disc_pct = int(disc_m.group(1)) if disc_m else 0

        # ── Finalise ─────────────────────────────────────────────────────────
        name = _clean_name(raw_name)
        if not name or len(name) < 4:
            continue

        products.append({
            "store":          "COOP",
            "name":           name,
            "brand":          "",
            "price":          round(price, 2),
            "price_str":      f"{price:.2f} €".replace(".", ","),
            "original_price": "",
            "discount_pct":   disc_pct,
            "image":          "",
            "url":            LETAK_URL,
            "category":       normalize_category("", name),
            "type":           normalize_type("", name),
            "unit":           anc["unit"],
            "valid_from":     valid_from,
            "valid_until":    valid_until,
            "leaflet_id":     leaflet["id"],
            "leaflet_name":   leaflet["name"],
        })

    return products


# ── Leaflet scraper ────────────────────────────────────────────────────────────

def scrape_leaflet(leaflet: dict) -> list[dict]:
    print(f"  → COOP '{leaflet['name']}' (ID {leaflet['id']}): stahuje PDF …")
    pdf_bytes = _fetch_bytes(
        leaflet["pdf_url"],
        {"Accept": "application/pdf,*/*", "Referer": LETAK_URL},
    )
    print(f"    Veľkosť: {len(pdf_bytes)/1_048_576:.1f} MB")

    all_products: list[dict] = []

    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        valid_from, valid_until = _extract_dates(pdf)
        print(f"    Platnosť: {valid_from} – {valid_until}  |  Strany: {len(pdf.pages)}")

        for page_num, page in enumerate(pdf.pages):
            page_products = _parse_page(page, leaflet, valid_from, valid_until)
            all_products.extend(page_products)

        print(f"    Extrahovaných: {len(all_products)} produktov")

    return all_products


# ── Main scraping entry point ─────────────────────────────────────────────────

def scrape() -> list[dict]:
    print("  → COOP: načítavam zoznam letákov …")
    leaflets = _get_leaflets()
    if not leaflets:
        print("  ✗ COOP: žiadne letáky nenájdené na stránke")
        return []
    print(f"    Nájdené letáky: {[l['name'] + ' #' + l['id'] for l in leaflets]}")

    all_products: list[dict] = []
    for leaflet in leaflets:
        try:
            products = scrape_leaflet(leaflet)
            all_products.extend(products)
        except Exception as e:
            print(f"  ✗ COOP {leaflet['name']}: {e}")

    return all_products


def deduplicate(products: list[dict]) -> list[dict]:
    seen: set[tuple] = set()
    out:  list[dict] = []
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

        print("\nFirst 15 products:")
        for p in items[:15]:
            disc = f"  (-{p['discount_pct']}%)" if p["discount_pct"] else ""
            print(f"  [{p['category']:<22}] {p['name'][:52]:<52}  {p['price_str']}{disc}")
