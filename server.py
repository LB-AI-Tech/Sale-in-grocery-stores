"""
server.py — LacnéNákupy local server
Usage:  python server.py
Then:   open http://localhost:8000
Stop:   Ctrl+C
"""

import http.server
import urllib.request
import urllib.error
import urllib.parse
import json
import gzip
import ssl
import os
import sys
import subprocess
from datetime import date, timedelta

PORT = 8000

SSL_CTX = ssl.create_default_context()
SSL_CTX.check_hostname = False
SSL_CTX.verify_mode = ssl.CERT_NONE

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "*/*",
    "Accept-Language": "sk-SK,sk;q=0.9",
    "Accept-Encoding": "gzip, deflate",
}


def fetch_json(url, extra_headers=None):
    headers = {**HEADERS, **(extra_headers or {})}
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=20, context=SSL_CTX) as resp:
        raw = resp.read()
        enc = resp.headers.get("Content-Encoding", "")
    if "gzip" in enc:
        raw = gzip.decompress(raw)
    return json.loads(raw.decode("utf-8"))


# ── Kaufland: Thursday-based weekly slug ─────────────────────────────────────
# Kaufland SK letak runs Thu–Wed; slug format: SK_sk_KDZ_8620_SK{iso_week:02d}-LFT

KAUFLAND_REGION = 8620

def get_kaufland_slug():
    today = date.today()
    days_since_thu = (today.weekday() - 3) % 7
    last_thu = today - timedelta(days=days_since_thu)
    iso_week = last_thu.isocalendar()[1]
    return f"SK_sk_KDZ_{KAUFLAND_REGION}_SK{iso_week:02d}-LFT"


_KAUFLAND_HEADERS = {
    "Referer": "https://kaufland.leaflets.schwarz/",
    "Origin":  "https://kaufland.leaflets.schwarz",
}

# ── Kaufland cache auto-refresh ───────────────────────────────────────────────
# Kaufland week runs Thursday → Wednesday.  The cache file written by
# scrape_kaufland.py is considered fresh for the whole current week.
# When stale, we launch the scraper as a background subprocess so the server
# never blocks waiting for a 60-second Playwright scrape.

_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
_KAUFLAND_CACHE = os.path.join(_BASE_DIR, "kaufland_products.json")
_kaufland_refresh_proc = None   # subprocess.Popen while scrape is running


def _kaufland_cache_is_stale() -> bool:
    """True if the cache is missing or was written before this week's Thursday."""
    if not os.path.exists(_KAUFLAND_CACHE):
        return True
    mtime      = date.fromtimestamp(os.path.getmtime(_KAUFLAND_CACHE))
    today      = date.today()
    week_start = today - timedelta(days=(today.weekday() - 3) % 7)  # last Thu
    return mtime < week_start


def _trigger_kaufland_refresh():
    """Start scrape_kaufland.py as a background subprocess (at most one at a time)."""
    global _kaufland_refresh_proc
    # Don't launch a second scrape while one is already running
    if _kaufland_refresh_proc is not None and _kaufland_refresh_proc.poll() is None:
        return
    script = os.path.join(_BASE_DIR, "scrape_kaufland.py")
    if not os.path.exists(script):
        print("  ⚠  scrape_kaufland.py not found — auto-refresh disabled")
        return
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    print("  ↻  Kaufland: spúšťam automatickú aktualizáciu letáku na pozadí …")
    _kaufland_refresh_proc = subprocess.Popen(
        [sys.executable, script],
        cwd=_BASE_DIR,
        env=env,
    )


# ── Lidl cache auto-refresh ───────────────────────────────────────────────────
# Lidl SK week runs Mon–Sun.  The cache is stale if missing or written before
# this week's Monday.

_LIDL_CACHE = os.path.join(_BASE_DIR, "lidl_products.json")
_lidl_refresh_proc = None   # subprocess.Popen while scrape is running


def _lidl_cache_is_stale() -> bool:
    """True if the cache is missing or was written before this week's Monday."""
    if not os.path.exists(_LIDL_CACHE):
        return True
    mtime      = date.fromtimestamp(os.path.getmtime(_LIDL_CACHE))
    today      = date.today()
    week_start = today - timedelta(days=today.weekday())  # last Monday
    return mtime < week_start


def _trigger_lidl_refresh():
    """Start scrape_lidl.py as a background subprocess (at most one at a time)."""
    global _lidl_refresh_proc
    if _lidl_refresh_proc is not None and _lidl_refresh_proc.poll() is None:
        return
    script = os.path.join(_BASE_DIR, "scrape_lidl.py")
    if not os.path.exists(script):
        print("  ⚠  scrape_lidl.py not found — Lidl auto-refresh disabled")
        return
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    print("  ↻  Lidl: spúšťam automatickú aktualizáciu letáku na pozadí …")
    _lidl_refresh_proc = subprocess.Popen(
        [sys.executable, script],
        cwd=_BASE_DIR,
        env=env,
    )


# ── Tesco cache auto-refresh ──────────────────────────────────────────────────
# Tesco SK promotions run weekly (typically Mon–Sun).
# The cache is stale if missing or written before this week's Monday.

_TESCO_CACHE = os.path.join(_BASE_DIR, "tesco_products.json")
_tesco_refresh_proc = None   # subprocess.Popen while scrape is running


def _tesco_cache_is_stale() -> bool:
    """True if the cache is missing or was written before this week's Monday."""
    if not os.path.exists(_TESCO_CACHE):
        return True
    mtime      = date.fromtimestamp(os.path.getmtime(_TESCO_CACHE))
    today      = date.today()
    week_start = today - timedelta(days=today.weekday())  # last Monday
    return mtime < week_start


def _trigger_tesco_refresh():
    """Start scrape_tesco.py as a background subprocess (at most one at a time)."""
    global _tesco_refresh_proc
    if _tesco_refresh_proc is not None and _tesco_refresh_proc.poll() is None:
        return
    script = os.path.join(_BASE_DIR, "scrape_tesco.py")
    if not os.path.exists(script):
        print("  ⚠  scrape_tesco.py not found — Tesco auto-refresh disabled")
        return
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    print("  ↻  Tesco: spúšťam automatickú aktualizáciu na pozadí …")
    _tesco_refresh_proc = subprocess.Popen(
        [sys.executable, script],
        cwd=_BASE_DIR,
        env=env,
    )


def _fetch_tesco_products() -> list:
    """Return Tesco products from tesco_products.json, refreshing if stale."""
    if _tesco_cache_is_stale():
        _trigger_tesco_refresh()
    if os.path.exists(_TESCO_CACHE):
        try:
            with open(_TESCO_CACHE, encoding="utf-8") as f:
                products = json.load(f)
            print(f"  ✓ Tesco: {len(products)} produktov (z cache)")
            return products
        except Exception as e:
            print(f"  ✗ Tesco cache: {e}")
    print("  ℹ Tesco: žiadne produkty — spustite scrape_tesco.py pre cache")
    return []


# ── COOP cache auto-refresh ───────────────────────────────────────────────────
# COOP SK leaflets are typically valid Wed–Tue.
# The cache is stale if missing or written before this week's Wednesday.

_COOP_CACHE = os.path.join(_BASE_DIR, "coop_products.json")
_coop_refresh_proc = None   # subprocess.Popen while scrape is running


def _coop_cache_is_stale() -> bool:
    """True if the cache is missing or was written before this week's Wednesday."""
    if not os.path.exists(_COOP_CACHE):
        return True
    mtime      = date.fromtimestamp(os.path.getmtime(_COOP_CACHE))
    today      = date.today()
    week_start = today - timedelta(days=(today.weekday() - 2) % 7)  # last Wed
    return mtime < week_start


def _trigger_coop_refresh():
    """Start scrape_coop.py as a background subprocess (at most one at a time)."""
    global _coop_refresh_proc
    if _coop_refresh_proc is not None and _coop_refresh_proc.poll() is None:
        return
    script = os.path.join(_BASE_DIR, "scrape_coop.py")
    if not os.path.exists(script):
        print("  ⚠  scrape_coop.py not found — COOP auto-refresh disabled")
        return
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    print("  ↻  COOP: spúšťam automatickú aktualizáciu na pozadí …")
    _coop_refresh_proc = subprocess.Popen(
        [sys.executable, "-X", "utf8", script],
        cwd=_BASE_DIR,
        env=env,
    )


def _fetch_coop_products() -> list:
    """Return COOP products from coop_products.json, refreshing if stale."""
    if _coop_cache_is_stale():
        _trigger_coop_refresh()
    if os.path.exists(_COOP_CACHE):
        try:
            with open(_COOP_CACHE, encoding="utf-8") as f:
                products = json.load(f)
            print(f"  ✓ COOP: {len(products)} produktov (z cache)")
            return products
        except Exception as e:
            print(f"  ✗ COOP cache: {e}")
    print("  ℹ COOP: žiadne produkty — spustite scrape_coop.py pre cache")
    return []


def _unwrap_list(data) -> list:
    """Extract the item list from a variety of API envelope shapes."""
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in ("items", "products", "offers", "data", "results",
                    "articles", "records", "hotspots"):
            val = data.get(key)
            if isinstance(val, list) and val:
                return val
    return []


def _parse_kaufland_price(raw) -> float | None:
    """Parse price from int (cents), float (euros), or string."""
    if raw is None:
        return None
    try:
        v = float(str(raw).replace(",", ".").strip())
        # Some APIs return prices in cents (integer > 100)
        if v > 100 and float(raw) == int(float(raw)):
            v /= 100
        return v if 0.10 <= v <= 500 else None
    except (ValueError, TypeError):
        return None


def _parse_kaufland_item(item: dict, valid_from: str = "",
                         valid_until: str = "") -> dict | None:
    """Normalise one raw Kaufland API product dict into the shared schema."""
    import re as _re
    name = (
        item.get("name") or item.get("title") or item.get("label") or
        item.get("productName") or item.get("description") or ""
    ).strip()
    if not name or len(name) < 3:
        return None

    price = _parse_kaufland_price(
        item.get("price") or item.get("promotionPrice") or
        item.get("currentPrice") or item.get("salePrice")
    )
    if price is None:
        return None

    orig = _parse_kaufland_price(
        item.get("normalPrice") or item.get("originalPrice") or
        item.get("regularPrice") or item.get("listPrice")
    )

    image = item.get("image") or item.get("imageUrl") or item.get("thumbnail") or ""
    if isinstance(image, dict):
        image = image.get("url") or image.get("src") or ""

    url_val = item.get("url") or item.get("link") or item.get("href") or ""
    if url_val and not url_val.startswith("http"):
        url_val = "https://www.kaufland.sk" + url_val

    desc = (item.get("description") or "") + " " + name
    unit_m = _re.search(r'(\d+[.,]?\d*\s*(?:g|kg|l|ml|cl|ks|bal\.?|por\.?))',
                        desc, _re.I)

    return {
        "store":          "Kaufland",
        "leaflet":        "Kaufland — aktuálny leták",
        "name":           name,
        "brand":          (item.get("brand") or item.get("brandName") or "").strip(),
        "price":          price,
        "price_str":      f"{price:.2f} €".replace(".", ","),
        "original_price": f"{orig:.2f} €".replace(".", ",") if orig and orig != price else "",
        "discount_pct":   0,
        "image":          image,
        "url":            url_val or "https://www.kaufland.sk",
        "category":       normalize_category(
                              item.get("category") or item.get("categoryName") or "", name),
        "type":           normalize_type(
                              item.get("category") or item.get("categoryName") or "", name),
        "valid_from":     item.get("validFrom") or item.get("startDate") or valid_from,
        "valid_until":    item.get("validUntil") or item.get("endDate") or valid_until,
        "unit":           unit_m.group(1) if unit_m else "",
    }


def _fetch_kaufland_products() -> tuple[list, dict]:
    """
    Returns (products, flyer_metadata).

    Priority:
      1. Schwarz leaflets REST API — probe several product sub-endpoints.
      2. kaufland_products.json   — cache generated by scrape_kaufland.py.
      3. Empty list               — only banner metadata is available.
    """
    slug = get_kaufland_slug()
    base = "https://endpoints.leaflets.schwarz/v4"

    # Always fetch flyer metadata — needed for the banner even if products fail
    meta_url = (
        f"{base}/flyer?flyer_identifier={slug}"
        f"&region_id={KAUFLAND_REGION}&region_code={KAUFLAND_REGION}"
    )
    try:
        meta  = fetch_json(meta_url, extra_headers=_KAUFLAND_HEADERS)
        flyer = meta.get("flyer", {})
    except Exception:
        flyer = {}

    flyer_id    = flyer.get("id") or slug
    valid_from  = flyer.get("offerStartDate", "")
    valid_until = flyer.get("offerEndDate", "")

    # Probe candidate product endpoints on the same Schwarz API
    for url in [
        f"{base}/flyer/{flyer_id}/offers",
        f"{base}/flyer/{flyer_id}/products",
        f"{base}/flyer/{flyer_id}/articles",
        f"{base}/offers?flyer_identifier={slug}&region_id={KAUFLAND_REGION}",
        f"{base}/products?flyer_identifier={slug}&region_id={KAUFLAND_REGION}",
    ]:
        try:
            data     = fetch_json(url, extra_headers=_KAUFLAND_HEADERS)
            raw_list = _unwrap_list(data)
            if not raw_list:
                continue
            products = [p for p in (
                _parse_kaufland_item(i, valid_from, valid_until) for i in raw_list
            ) if p]
            if products:
                print(f"  ✓ Kaufland products via API ({len(products)})")
                return products, flyer
        except Exception:
            pass

    # Fall back to the JSON cache produced by scrape_kaufland.py.
    # Trigger a background refresh whenever the cache is missing or stale.
    if _kaufland_cache_is_stale():
        _trigger_kaufland_refresh()

    cache = _KAUFLAND_CACHE
    if os.path.exists(cache):
        try:
            with open(cache, encoding="utf-8") as f:
                products = json.load(f)
            # Patch flyer dates onto cached products if they are missing
            for p in products:
                if not p.get("valid_from"):  p["valid_from"]  = valid_from
                if not p.get("valid_until"): p["valid_until"] = valid_until
            print(f"  ✓ Kaufland: {len(products)} produktov (z cache)")
            return products, flyer
        except Exception as e:
            print(f"  ✗ Kaufland cache: {e}")

    print("  ℹ Kaufland: žiadne produkty — spustite scrape_kaufland.py pre cache")
    return [], flyer


# ── Abbreviation expansion ────────────────────────────────────────────────────
# Maps lowercase dot-abbreviations (as seen in Slovak store product names) to
# their full Slovak forms. Applied word-by-word before keyword matching.

_ABBREVS = {
    # Dairy / mliečne
    "jog.": "jogurt", "jogh.": "jogurt",
    "ml.": "mlieko", "mliek.": "mlieko",
    "smot.": "smotana", "šľah.": "šľahačka",
    "mas.": "maslo", "tv.": "tvaroh", "tvar.": "tvaroh",
    # Meat / mäso
    "brav.": "bravčové", "br.": "bravčové",
    "kur.": "kuracie", "hydin.": "hydina",
    "hov.": "hovädzie", "morč.": "morčacie",
    "úden.": "údené", "ád.": "údené",
    # Fish / ryby
    "ryb.": "ryba", "loso.": "losos", "tun.": "tuniak",
    # Cheese / syr
    "syr.": "syr", "tav.": "tavený syr",
    # Flavours used as product qualifiers
    "jahod.": "jahoda", "čučor.": "čučoriedka",
    "malin.": "malina", "višn.": "višňa",
    "citrus.": "citrus", "citr.": "citrón",
    "vanilk.": "vanilka", "van.": "vanilka",
    "čok.": "čokoláda", "čokol.": "čokoláda", "choc.": "čokoláda",
    "zel.": "zelenina", "ovoc.": "ovocie",
    # Beverages
    "min.": "minerálna", "limon.": "limonáda",
    "piv.": "pivo", "káv.": "káva", "čaj.": "čaj",
    # Bakery
    "cel.": "celozrnný", "kvásk.": "kváskový",
    # Misc — words that carry no category meaning, strip them
    "napl.": "", "nápl.": "",
}


def _expand(text: str) -> str:
    """Replace known abbreviations in a space-tokenised string.
    Also handles compound dot-abbreviations like 'smot.jog.' and 'inst.káva'
    by splitting each token on '.' and looking up each sub-part."""
    result = []
    for word in text.split():
        w_lower = word.lower()
        # 1. Try the full token first
        if w_lower in _ABBREVS:
            val = _ABBREVS[w_lower]
            if val:
                result.append(val)
            continue
        # 2. Split on '.' for compound abbreviations ("smot.jog." → "smotana jogurt")
        parts = [p for p in w_lower.split('.') if p]
        if len(parts) > 1:
            expanded = [_ABBREVS.get(p + '.', p) for p in parts]
            joined = ' '.join(e for e in expanded if e)
            result.append(joined)
        else:
            result.append(word)
    return ' '.join(result)


# ── Direct raw-category overrides ─────────────────────────────────────────────
# A handful of store-specific raw values that unambiguously map to a label.
_RAW_CAT_EXACT = {
    "og":              "Ovocie & zelenina",   # Lidl internal code
    "semihard cheese": "Syry",                # Billa internal code
    "hard cheese":     "Syry",
    "soft cheese":     "Syry",
}


# ── Keyword rules ─────────────────────────────────────────────────────────────
# Checked against lowercased( expanded_raw_category + " " + expanded_name ).
# First match wins — more-specific / brand-level rules come first.

_CAT_RULES = [

    # ── Priority intercepts — specific product names that fire the WRONG rule
    #    before reaching the correct category in the generic block below.
    #    Always evaluated first; only the tightest possible keyword is used.
    ("Pekáreň", [
        "pinsa",      # "syr" in name → Syry (pos 5) beats Pekáreň (pos 6)
        "donut",      # "jablk" filling → Ovocie beats Pekáreň
        "xl puding",  # "jablko/granát" → Ovocie beats Pekáreň
        "makovka",    # "maslov" in "maslová" → Mliečne beats Pekáreň
    ]),
    ("Trvanlivé potraviny", [
        "kyselina citrónová",  # Dr.Oetker citric acid (citrón → Ovocie)
        "maggi dh cest",       # MAGGI pasta kits (paprika → Ovocie)
        "maggi am cest",       # MAGGI pasta kits (špenát → Ovocie)
        "vishu ",              # VISHU instant soups (mäso keyword → Mäso)
        "inst.rezan",          # generic instant noodle marker
    ]),
    ("Mäso & ryby", [
        "a la krab", "à la krab",  # "šalát" → Ovocie (pos 9) beats Mäso (pos 10)
    ]),
    ("Mrazené", [
        "mrazené", "mrazená", "mrazený", "mraz.",  # mäso/ryba keywords fire before Mrazené otherwise
    ]),
    ("Mliečne výrobky", [
        "liptov ",    # Liptov syr/nátierka: "syr " → Syry fires before Mliečne
        "leerdammer", # Leerdammer syr plátky: "syr " → Syry fires before Mliečne
    ]),
    ("Sladkosti & snacky", [
        "kofila",    # Orion Kofila: "káva" → Nápoje fires before Sladkosti
        "milka",     # "milk" substring → Mliečne fires before Sladkosti
    ]),

    # ── Pet food (very specific brands — must come first to avoid false matches)
    ("Krmivo pre zvieratá", [
        "pedigree", "darling gran", "friskies", "whiskas", "kitekat",
        "felix cat", "purina", "royal canin", "hills pet",
        "cesar hov", "cesar kur",   # "cesar " alone could be salad
        "krmivo pre", "cat food", "dog food",
    ]),

    # ── Household cleaning (brands before generic words)
    ("Dom & záhrada", [
        "domestos", "duck wc", "grilpur", "fixinela", "cif ",
        "finish sol", "finish osv",   # Finish dishwasher products
        "prací prášok", "prací gél", "prací prostriedok",
        "aviváž", "silan ", "ariel", "persil", "vizir", "tide ",
        "prostriedok na riad", "na riad", "umývač",
        "wc čistič", "čistič kúpeľ", "čistič podlah",
        "kuchynské utierky", "papierové utierky",
        "záhradn", "hnojivo", "kvetináč",
        "cleaning", "detergent", "household",
    ]),

    # ── Personal care (brands before generic)
    ("Drogéria & kozmetika", [
        "nivea", "dove ", "palmolive", "colgate", "oral-b",
        "gillette", "venus ", "braun ",
        "garnier", "loreal", "l'oréal", "pantene", "head & shoulders",
        "always ultra", "always ", "tampax", "pampers", "huggies",
        "harmony pap", "big soft ",   # paper products
        "šampón", "kondicionér", "sprchový gél",
        "tekuté mydlo", "toaletné mydlo",
        "zubná pasta", "zubná kefka", "ústna voda",
        "dezodorant", "antiperspirant", "deo sprej",
        "pleťový krém", "telové mlieko",
        "holiaci", "holenie",
        "vložky", "tampóny", "plienky",
        "toaletný papier", "papierové vreckovky",
        "shampoo", "cosmetic", "beauty", "hygiene",
    ]),

    # ── Beverages — brands first so "happy day pomaranč" → Nápoje not Ovocie
    ("Nápoje", [
        # Beer brands (SK/CZ most common)
        "krušovice", "budvar", "budweiser", "staropramen", "pilsner urquell",
        "zlatý bažant", "šariš", "topvar", "gambrinus", "kozel ",
        "heineken", "carlsberg", "stella artois", "corona ",
        "leffe", "hoegaarden", "paulaner",
        # Wine brands
        "hubert club", "vitis galéria", "topolčianky", "château",
        "frizzante", "prosecco", "sekt ", "lambrusco",
        # Spirit brands
        "nicolaus vodka", "ballantine", "jameson", "jack daniel",
        "jim beam", "beefeater", "captain morgan", "bacardi",
        "absolut vodka", "finlandia", "smirnoff", "grey goose",
        "johnnie walker", "chivas regal",
        "jägermeister", "becherovka", "fernet", "campari",
        "borovička", "slivovica",
        # Juice brands
        "happy day", "rauch ", "pfanner", "höllinger", "cappy ",
        "capri sun", "capri-sun", "relax džús",
        # Coffee brands
        "nescafé", "nescafe", "jacobs kaffee", "jacobs kav",
        "lavazza", "tchibo", "eduscho", "julius meinl",
        "dolce gusto", "tassimo", "senseo",
        # Soft drink brands
        "coca-cola", "pepsi ", "kofola", "fanta ", "sprite ",
        "red bull", "redbull", "monster energy", "hell energy",
        # Generic drink words
        "pivo ", "pivu", "pivné",
        "víno ", "vína ", "vínový", "šumivé víno",
        "vodka", "whisky", "whiskey", "likér", "rum ", "gin ",
        "džús", "šťava ovocná", "nektár",
        "minerálna voda", "perlivá voda",
        "limonáda", "tonik ", "isotonický",
        "sirup ", "mošt ", "smoothie",
        "čaj ", "zelený čaj", "ovocný čaj", "bylinný čaj",
        "káva", "espresso", "cappuccino", "instantná káva", "inst.káva",
        "kakao ", "horúca čokoláda",
        "nápoj", "beer", "wine", "juice", "beverage",
        # Additional brands / keywords
        "energeťák",               # Slovak slang for energy drink
        "magnesia",                # Magnesia mineral water brand
        "relax ", "relax džús",
        "demänovka",
        "urpiner",
        "radegast",
        "semtex ",
        "corgoň",
        "veltín", "veltínske",
        "liehovina",
        "pramenitá voda",
        "jana ",
        "ice hell",
        "oravská voda",
    ]),

    # ── Cheese — before Pekáreň/Mliečne so "syrový" doesn't leak into dairy
    ("Syry", [
        "semihard cheese",   # raw Billa category
        " syr ", "syr.", "syrov", "syrový", "syrové",   # space-guarded
        "eidam", "gouda", "camembert", "brie",
        "mozzarella", "mozarella", "mozarel",
        "čedar", "cheddar", "parmezan", "parmezán",
        "oštiepok", "parenica", "niva ", "hermelín",
        "emmental", "ementál", "ementáler", "gruyère", "raclette",
        "cottage ", "ricotta ", "feta ", "halloumi",
        "tavený syr", "tavenos", "tavenin",
        "hochland", "président", "castello",
        "bambino",     # soft processed cheese brand
        "encián",      # Slovak mold-ripened cheese
        "plesnivec",   # Slovak blue/mold cheese
        "madeland",    # Danish-style semi-hard cheese
        "karička",     # smoked processed cheese ring (≠ kariéka yogurt!)
        "bryndza",     # Slovak sheep-milk cheese
        "cheese",
    ]),

    # ── Bakery — before Mliečne so "závin s tvarohom" → Pekáreň, not Mliečne
    ("Pekáreň", [
        # Breads
        "chlieb", "chleba",
        # Rolls & buns
        "rohlík", "rožok", "žemľa", "žemle", "hamburgerov",
        "bageta", "bagetka", "baguette", "ciabatta",
        "pita chlieb", "bagel", "briošk",
        # Sweet bakery
        "vianočka", "závin ", "záviň",
        "buchty", "buchtiček", "buchtič",
        "makovník", "orechový závin", "tvarohový závin",
        "slimák ",     # swirl/snail-shaped filled pastry
        "zákus",       # zákusok (sing.) + zákusky (pl.) — pastries/cakes
        # Other pastry
        "croissant", "pletenka", "preclík",
        "pečivo", "kváskový chlieb", "kváskový",
        "štrúdľa", "lístkové cesto",
        # Toasts & wraps
        "toast", "tortilla",
        # Bakery brands common in SK
        "la lorraine", "penam", "minit",
        "bread", "bakery", "bun", "roll",
        # Additional bakery products
        "šišk",                    # šiška / šišky — doughnuts / fried pastry
        "taštičk",                 # filled pocket pastry
        "ražný", "ražná", "ražné", # rye breads/rolls
        "kapsa ",                  # filled pastry pocket
        "makovka",                 # poppy-seed roll
        "pleten", "pletenec",      # braided pastry/bread
    ]),

    # ── Dairy — before Sladkosti/Ovocie so "jog. jahoda" → Mliečne, not Ovocie
    ("Mliečne výrobky", [
        # Milk
        "mlieko", "plnotučné ml", "polotučné ml", "uht mlieko",
        # Yogurt — brands first, then generic
        "jogurt", "joghurt",
        "kariéka",       # Rajo's yogurt brand (≠ karička = cheese)
        "jogobella",     # Müller yogurt brand
        "pilos ",        # Lidl yogurt brand
        "mullermilch", "müllermilch",   # Müller milk drink
        "milsy",         # Bánovecký dairy brand
        "actimel", "activia", "danone fantasia", "danone actimel",
        "grécky typ", "gréc. typ",
        # Cream
        "smotana", "šľahačka", "šľahačkov",
        "smotana na šľahanie", "smotana na varenie", "kyslá smotana",
        # Butter / margarine
        "maslo ", "maslov",
        "hera 250", "rama ", "flora ",
        # Tvaroh / quark
        "tvaroh", "tvarohov",
        # Other dairy
        "kefír", "acidofiln", "acidko",
        # Dairy brands
        "rajo ", "zott ", "tami tatranské", "olma ", "meggle",
        "milk", "yogurt", "yoghurt", "cream", "butter", "dairy",
        # Additional dairy brands & products
        "madeta",                    # Czech dairy brand (Lipánek, etc.)
        "lipánek",                   # Madeta yogurt/dessert for kids
        "pribináček",                # Danone quark snack
        "tekovsk",                   # Tekovská tehla (processed cheese block)
        "grécky ",                   # Grécky biely / grécky typ yogurt
        "smotanový", "smotanová",    # smotanový dezert / smotanová nátierka
        "zakysaný", "zakysaná",      # zakysaná smotana (sour cream)
        "ovsánek",                   # Müller Ovsánek oat dairy drink
        "leerdammer",                # Leerdammer Original / tavená nátierka
        "lučina",                    # Lučina spreadable cheese / skyr
        "bánovecká",                 # Bánovecká nátierka (spreadable dairy)
        "babybel", "mini babybel",   # Mini Babybel wax-coated cheese
        "syrokrém",                  # spreadable cream cheese
        "smotanella",                # Smotanella cream
        "liptov ",                   # Liptov syry (dairy brand)
        "hera ",                     # Hera margarine (≠ hera 250 already present)
        "proteínový pud",            # protein pudding (dairy dessert)
        "monte ",                    # Monte hazelnut dairy dessert
        "pro aktiv",                 # Flora / Pro Aktiv margarine
        "ov nika",                   # OV NIKA dairy brand
        "ov dunajský",               # OV DUNAJSKÝ EMMENTÁLER
    ]),

    # ── Sweets, snacks, ice cream — BEFORE Ovocie so candy brand names
    #    containing fruit words (ORION BANÁNY, MILKA JAHODA) → Sladkosti
    ("Sladkosti & snacky", [
        # Chocolate brands
        "milka", "kinder ", "figaro", "merci ", "bounty", "snickers",
        "twix", "mars ", "kit kat", "kitkat", "ferrero", "nutella",
        "rocher",      # Ferrero Rocher
        "toblerone", "raffaello", "ritter sport",
        "orion ", "zora ",
        # Slovak/Czech confectionery brands
        "sedita", "horalky", "fidorka", "kávovky", "kávenky",
        "korunka", "kolonáda", "manner ", "corny ", "haribo",
        "nugeta", "yami ", "hašlerky", "halerky",
        "sfinx", "mentolky", "lentilky",
        # Chocolate (generic)
        "čokoláda", "čokol", " čoko",
        # Candy
        "cukrík", "bonbón", "bonboniér", "lízank", "žuvačk", "pralinky",
        # Chips & savoury snacks
        "chipsy", "lupienky", "krekry", "popcorn",
        "nachos", "tortilla chip", "pringles",
        "super ring",
        # Bars & biscuits
        "tyčink",      # tyčinka (sing.) and tyčinky (pl.) — snack sticks
        "sušienky", "keks",
        "oblátk", "oplatky", "wafer",   # oblátk covers both oblátka (sg.) and oblátky (pl.)
        # Nuts (snack context)
        "oriešky ", "arašid", "mandle ",
        # Ice cream
        "zmrzlin", "nanuk ", "magnum ", "häagen", "zmrzlinov",
        "chocolate", "candy", "sweet", "snack", "cookie", "biscuit",
        # Additional confectionery brands & products
        "lindt",       # Lindt Excellence / Lindor
        "tatranky",    # Opavia Tatranky wafer bars
        "toffifee",    # Storck Toffifee
        "margot",      # Orion Margot chocolate bar
        "kofila",      # Orion Kofila coffee-cream bar
        "brumík",      # Nestlé Brumík children's biscuit bar
        "jojo ",       # Jojo gummy / hard candy
        "kukuričky",   # Bohemia kukuričky (corn puffs)
        "lay's", "lays",  # Lay's crisps (with and without apostrophe)
    ]),

    # ── Fresh produce — after Sladkosti/Mliečne so flavour words (jahoda,
    #    banán, vanilka…) in candy/dairy names don't pull them here
    ("Ovocie & zelenina", [
        "ovocie", "zelenina",
        # Fruits
        "jablk", "hrušk", "banán", "pomaranč", "mandarínk", "citrus", "citrón",
        "jahod", "malin", "čučoriedk", "hrozno", "hrozienko", "slivk",
        "broskyn", "nektarink", "čerešň", "višňa",
        "ananás", "mango ", "kiwi", "avokád",
        "melón", "dyňa", "grapefruit", "borůvk",
        # Vegetables
        "paradajk", "cherry paradajk",
        "rajčiak",     # tomato (Slovak variant spelling)
        "uhorka", "paprik", "šalát ", "špenát", "kapust", "brokolica",
        "karfiol", "mrkva", "cibuľ", "cesnak", "zeler",
        "hrášok", "kukurica", "kel ", "repa ", "reďkovk",
        "baklažán", "cuketa", "pór ", "petržlen",
        "špargľ",      # asparagus
        "chren", "kaleráb", "rukola", "mangold",
        "datľov paradajk",
        # Billa bulk section label
        "bonvia",
        "fruit", "vegetable", "tomato", "apple", "banana", "strawberr",
        # Additional produce / nuts
        "pistáci",     # pistachios
        "kešu",        # cashews
        "zemiak",      # potatoes (zemiaky)
        "olivy",       # olives
        "bahlsen",     # Bahlsen nut/fruit snack mixes
    ]),

    # ── Meat & fish
    ("Mäso & ryby", [
        # Pork
        "bravčov", "bravč",
        "krkovičk", "krkovica",   # both diacritic and non-diacritic forms
        "panenka", "pliecko ",
        "karé ", "rebierk", "bôčik", "kolienk",
        "špekáčik", "mangalica",
        # Beef
        "hovädzi", "hoväd",
        "hovadzi",   # without diacritic ä (as in "HOVADZIE ZADNE")
        "guláš",
        # Chicken / poultry
        "kuraci", "kuracie",
        "kurč",      # kurča / kurčacie (without diacritic match gap)
        "kurc",      # kurcí etc.
        "hydinov", "hydina",
        # Duck
        "kačac",     # kačacie, kačacia, kačacím…
        # Turkey
        "morčaci",
        # Rabbit
        "králik",
        # Deli / cold cuts
        "klobás", "salám", "salámov",
        "salama",    # without diacritic (SALAMA DUNAJSKA etc.)
        "šunk", "slanin", "párky", "párok",
        "debrecínsk", "safalád", "frankfurtsk",
        "gašpark",   # gašparky — small smoked sausages
        "jaternic",  # jaternice — Czech-style offal sausage
        "prosciutto",
        "údeni", "údenin", "údenky",
        # Generic meat words
        "mäso ", "mäsov", "steak", "stehno",
        "rezň", "rezne",   # rezne = schnitzels (plural without háček variant)
        "kotlet",
        "prsia", "krídla", "mletý", "mleté", "pečienka",
        "minútka", "gyros", "kebab",
        # Fish
        "ryba ", "ryby ", "rybac", "rybí",
        "losos", "tuniak", "treska", "pstruh", "makrela",
        "sardink", "šproty", "jack mackerel",
        "morská", "plody mora", "krevety", "krab",
        "chicken", "beef", "pork", "fish", "salmon", "meat", "sausage",
        # Additional meat products
        "chorizo",     # Spanish-style sausage
        "salame ",     # Italian-style salami
        "bacon ",      # bacon (English loanword used on packaging)
        "tlačenk",     # tlačenka — head cheese / brawn
        "pražma",      # sea bream (morská ryba)
    ]),

    # ── Frozen
    ("Mrazené", [
        "mrazené", "mrazená", "mrazený",
        "frozen", "mraz.",
    ]),

    # ── Pantry / shelf-stable
    ("Trvanlivé potraviny", [
        # Pasta / rice / grains
        "cestovin", "špagety", "penne", "fusilli", "tagliatell",
        "ryža ", "basmati", "risotto",
        "múka ", "krupica", "ovsené vločky", "müsli", "cereáli",
        "granola", "corn flakes",
        # Oils / fats
        "olivový olej", "repkový olej", "slnečnicový olej",
        "borges", "raciol",
        # Condiments / sauces
        "ocot ", "kečup", "horčica", "majonéza",
        "sójová omáčka", "worcester", "tabasco", "pesto",
        "paradajkový pretlak", "paradajková pasta",
        # Seasonings / bouillon
        "vegeta", "podravka", "knorr", "maggi", "bujón", "vývar",
        "ochucovadlo", "korenie mix",
        # Canned / preserved
        "konzerv", "konzervovaný",
        "v olivovom oleji", "vo vlastnej šťave", "v majonéze",
        "rio mare", "treska v",
        "nakladané uhorky", "efko uhorky",
        "heinz fazuľa",
        # Sweet preserves
        "džem", "marmeláda", "lekvár", "med ", "javorový sirup",
        "gelfix",
        # Sugar / salt
        "cukor ", "kryštálový cukor", "soľ ",
        # Preserved meats / pâtés (Hamé etc.)
        "hamé", "paštéta", "májka al", "svačinka al",
        # Other shelf-stable
        "instant", "polievka v prášku",
        "ideál", "bask ryža",
        "pasta", "rice", "oil", "flour", "sauce", "soup", "canned",
    ]),
]


# ── Product type rules ────────────────────────────────────────────────────────
# More granular than category. First match wins.
# Used to power type-based search ("beer" → "pivo") and image placeholders.

_TYPE_RULES = [

    # ── Priority intercepts ────────────────────────────────────────────────────
    # Products whose name contains a word that would fire the wrong type first.
    ("čokoláda", ["milka"]),    # "milk" substring → mlieko fires before čokoláda
    ("donut",    ["šišk"]),     # "tvaroh" filling → tvaroh fires before donut
    ("koláč",    ["makovka"]),  # "maslo" → maslo (butter) fires before koláč

    # ── Beverages ──────────────────────────────────────────────────────────────
    ("pivo", [
        "zlatý bažant", "krušovice", "šariš", "corgoň", "topvar", "gambrinus",
        "kozel ", "heineken", "budvar", "budweiser", "staropramen",
        "pilsner urquell", "carlsberg", "stella artois", "leffe",
        "hoegaarden", "paulaner", "radegast", "urpiner",
        "pivo ", "pivu", "pivné", "beer", "lager", "ale ",
    ]),
    ("víno", [
        "víno ", "vína ", "vínový", "šumivé víno", "prosecco", "frizzante",
        "sekt ", "lambrusco", "hubert club", "vitis galéria",
        "topolčianky", "château", "wine",
    ]),
    ("liehovina", [
        "vodka", "whisky", "whiskey", "likér", "rum ", "gin ",
        "borovička", "slivovica", "becherovka", "fernet", "jägermeister",
        "campari", "demänovka", "nicolaus vodka", "ballantine", "jameson",
        "jack daniel", "jim beam", "captain morgan", "bacardi", "absolut",
        "liehovina", "spirits", "liquor",
    ]),
    ("energetický nápoj", [
        "red bull", "redbull", "monster energy", "hell energy", "semtex ",
        "ice hell", "energeťák", "energy drink",
    ]),
    ("minerálna voda", [
        "minerálna voda", "perlivá voda", "pramenitá voda",
        "magnesia", "jana ", "rajec ", "oravská voda", "zlatíčko",
    ]),
    ("džús", [
        "džús", "šťava ovocná", "nektár", "juice",
        "happy day", "rauch ", "pfanner", "höllinger", "cappy ",
        "capri sun", "capri-sun", "relax džús",
    ]),
    ("káva", [
        "káva", "coffee", "nescafé", "nescafe", "jacobs kaffee", "jacobs kav",
        "lavazza", "tchibo", "eduscho", "julius meinl",
        "dolce gusto", "tassimo", "espresso", "cappuccino", "instantná káva",
    ]),
    ("čaj", [
        "čaj ", "tea", "zelený čaj", "ovocný čaj", "bylinný čaj", "popradský",
    ]),
    ("kakao", ["kakao ", "horúca čokoláda"]),
    ("limonáda", [
        "coca-cola", "pepsi ", "kofola", "fanta ", "sprite ",
        "limonáda", "tonik ", "smoothie", "sirup ",
    ]),
    ("mošt", ["mošt "]),

    # ── Meat & deli ────────────────────────────────────────────────────────────
    ("kuracie", ["kuracie", "kuraci", "kurč", "kurc", "chicken"]),
    ("morčacie", ["morčaci", "turkey"]),
    ("kačacie", ["kačac", "duck"]),
    ("bravčové", [
        "bravčov", "bravč", "krkovičk", "krkovica",
        "karé ", "rebierk", "bôčik", "kolienk", "mangalica",
        "panenka", "pliecko ", "pork",
    ]),
    ("hovädzie", ["hovädzi", "hoväd", "hovadzi", "beef", "steak"]),
    ("jahňacie", ["jahňac", "lamb"]),
    ("šunka", ["šunk", "prosciutto", "ham"]),
    ("klobása", [
        "klobás", "salám", "salámov", "salama", "frankfurtsk",
        "debrecínsk", "safalád", "gašpark", "špekáčik",
        "chorizo", "salame ", "sausage",
    ]),
    ("slanina", ["slanin", "bacon "]),
    ("tlačenka", ["tlačenk"]),
    ("párky", ["párky", "párok", "hot dog"]),
    ("jaternica", ["jaternic"]),
    ("gyros", ["gyros", "kebab"]),
    ("ryba", [
        "losos", "salmon", "tuniak", "treska", "pstruh", "makrela",
        "sardink", "šproty", "jack mackerel", "pražma",
        "ryba ", "ryby ", "rybac", "rybí", "fish",
    ]),
    ("morské plody", ["krevety", "krab", "plody mora", "seafood"]),

    # ── Dairy ──────────────────────────────────────────────────────────────────
    ("mlieko", ["mlieko", "plnotučné ml", "polotučné ml", "milk"]),
    ("jogurt", [
        "jogurt", "joghurt", "kariéka", "jogobella", "pilos ",
        "actimel", "activia", "grécky typ", "gréc. typ",
        "danone fantasia", "pribináček", "yogurt", "yoghurt",
    ]),
    ("maslo", [
        "maslo ", "maslov", "hera ", "rama ", "flora ",
        "pro aktiv", "margarín", "butter",
    ]),
    ("smotana", ["smotana", "šľahačka", "kyslá smotana", "cream"]),
    ("tvaroh", ["tvaroh", "tvarohov"]),
    ("kefír", ["kefír", "acidofiln", "acidko"]),
    ("nátierka", [
        "nátierka", "lučina", "bánovecká", "syrokrém", "smotanella",
        "spreadable",
    ]),

    # ── Cheese ─────────────────────────────────────────────────────────────────
    ("syr", [
        "eidam", "gouda", "camembert", "brie", "mozzarella", "mozarella",
        "čedar", "cheddar", "parmezan", "parmezán", "emmental", "ementál",
        "gruyère", "raclette", "cottage ", "ricotta ", "feta ", "halloumi",
        "oštiepok", "parenica", "niva ", "hermelín", "encián", "plesnivec",
        "bryndza", "karička", "madeland", "leerdammer", "bambino",
        "hochland", "babybel", "liptov ", " syr ", "syr.", "syrov",
        "syrový", "syrové", "cheese",
    ]),

    # ── Bakery ─────────────────────────────────────────────────────────────────
    ("chlieb", ["chlieb", "chleba", "kváskový", "bread"]),
    ("rožok",  ["rožok", "rohlík", "žemľa", "žemle", "hamburgerov", "roll", "bun"]),
    ("bageta",    ["bageta", "bagetka", "baguette", "ciabatta"]),
    ("croissant", ["croissant", "briošk"]),
    ("závin",  ["závin ", "záviň", "štrúdľa", "strudel", "makovník"]),
    ("donut",  ["donut", "šišk"]),
    ("koláč",  ["zákus", "buchty", "pleten", "vianočka", "makovka"]),
    ("taštička", ["taštičk", "kapsa "]),
    ("toast",    ["toast"]),
    ("tortilla", ["tortilla", "pita chlieb"]),

    # ── Sweets & snacks ────────────────────────────────────────────────────────
    ("čokoláda", [
        "milka", "kinder ", "figaro", "merci ", "bounty", "snickers",
        "twix", "mars ", "kit kat", "kitkat", "ferrero", "nutella",
        "rocher", "toblerone", "raffaello", "ritter sport", "lindt",
        "orion ", "zora ", "margot", "kofila",
        "čokoláda", "čokol", " čoko", "chocolate",
    ]),
    ("zmrzlina", [
        "zmrzlin", "nanuk ", "magnum ", "häagen", "zmrzlinov",
        "nanuková torta", "veto ", "ice cream",
    ]),
    ("chipsy", [
        "chipsy", "lupienky", "pringles", "lay's", "lays",
        "pom-bär", "kukuričky", "zemiakové lupienky",
        "nachos", "tortilla chip", "crisps",
    ]),
    ("oblátka", [
        "oblátk", "oplatky", "wafer", "tatranky", "horalky",
        "fidorka", "manner ", "bez mila",
    ]),
    ("sušienka", ["sušienky", "keks", "cookie", "biscuit", "bahlsen", "piškóty"]),
    ("tyčinka",  ["tyčink", "corny ", "brumík"]),
    ("bonbón",   ["cukrík", "bonbón", "bonboniér", "lízank", "haribo", "jojo ", "candy"]),
    ("orechy",   ["oriešky ", "arašid", "mandle ", "pistáci", "kešu", "nuts"]),
    ("popcorn",  ["popcorn"]),

    # ── Produce ────────────────────────────────────────────────────────────────
    ("paradajky", ["paradajk", "cherry paradajk", "rajčiak", "tomato"]),
    ("paprika",   ["paprik", "pepper"]),
    ("šalát",     ["šalát ", "lettuce", "rukola", "mangold"]),
    ("špenát",    ["špenát", "spinach"]),
    ("uhorka",    ["uhorka", "cucumber"]),
    ("brokolica", ["brokolica", "karfiol", "broccoli", "cauliflower"]),
    ("kapusta",   ["kapust", "kel ", "cabbage"]),
    ("mrkva",     ["mrkva", "carrot"]),
    ("cibuľa",    ["cibuľ", "pór ", "onion"]),
    ("cesnak",    ["cesnak", "garlic"]),
    ("zemiaky",   ["zemiak", "potato"]),
    ("jablká",    ["jablk", "apple"]),
    ("banány",    ["banán", "banana"]),
    ("pomaranč",  ["pomaranč", "mandarínk", "citrón", "grapefruit", "orange"]),
    ("hrozno",    ["hrozno", "grape"]),
    ("jahody",    ["jahod", "strawberr"]),
    ("broskyne",  ["broskyn", "nektarink", "peach"]),
    ("čerešne",   ["čerešň", "višňa", "cherry"]),
    ("ananás",    ["ananás", "pineapple"]),
    ("mango",     ["mango ", "avokád", "kiwi"]),
    ("melón",     ["melón", "dyňa", "watermelon"]),
    ("olivy",     ["olivy", "olives"]),

    # ── Pantry ─────────────────────────────────────────────────────────────────
    ("cestoviny", ["cestovin", "špagety", "penne", "fusilli", "tagliatell", "pasta"]),
    ("ryža",   ["ryža ", "basmati", "risotto", "rice"]),
    ("múka",   ["múka ", "krupica", "flour"]),
    ("olej",   ["olivový olej", "repkový olej", "slnečnicový olej", "oil"]),
    ("kečup",  ["kečup", "ketchup"]),
    ("horčica", ["horčica", "mustard"]),
    ("majonéza", ["majonéza", "mayo"]),
    ("džem",   ["džem", "marmeláda", "lekvár", "jam"]),
    ("med",    ["med ", "honey"]),
    ("cukor",  ["cukor ", "sugar"]),
    ("soľ",    ["soľ ", "salt"]),
    ("ocot",   ["ocot ", "vinegar"]),
    ("paštéta", ["paštéta", "hamé"]),
    ("konzerva", ["konzerv", "konzervovaný", "canned"]),
    ("polievka", ["polievka", "soup", "vitana", "knorr", "maggi", "bujón"]),
    ("müsli",  ["müsli", "cereáli", "granola", "corn flakes", "ovsené vločky"]),
]


def normalize_type(raw_category: str, product_name: str = "") -> str:
    """
    Return a specific product type (more granular than category).
    E.g.  category=Nápoje  →  type=pivo / víno / káva / džús …
    Returns empty string when no type can be determined.
    """
    text = _expand(f"{raw_category} {product_name}").lower()
    for type_label, keywords in _TYPE_RULES:
        if any(kw in text for kw in keywords):
            return type_label
    return ""


def normalize_category(raw_category: str, product_name: str = "") -> str:
    """
    Map a raw store category + product name to a shared normalised label.
    Pipeline:
      1. Check exact raw-category overrides (e.g. Lidl "OG" → Ovocie)
      2. Expand abbreviations in both strings (e.g. "jog." → "jogurt")
      3. Walk keyword rules in priority order — first match wins
    """
    raw_lc = raw_category.strip().lower()
    if raw_lc in _RAW_CAT_EXACT:
        return _RAW_CAT_EXACT[raw_lc]

    text = _expand(f"{raw_category} {product_name}").lower()
    for label, keywords in _CAT_RULES:
        if any(kw in text for kw in keywords):
            return label
    return "Ostatné"


class Handler(http.server.SimpleHTTPRequestHandler):

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/api/lidl":
            self.handle_lidl(parsed)
        elif parsed.path == "/api/billa":
            self.handle_billa(parsed)
        elif parsed.path == "/api/kaufland":
            self.handle_kaufland(parsed)
        elif parsed.path == "/api/tesco":
            self.handle_tesco(parsed)
        elif parsed.path == "/api/coop":
            self.handle_coop(parsed)
        elif parsed.path == "/api/all":
            self.handle_all(parsed)
        else:
            super().do_GET()

    # ── Lidl ─────────────────────────────────────────────────────────────────

    @staticmethod
    def _fetch_lidl_products():
        """
        Fetches Lidl SK weekly promotional products.

        Priority:
          1. lidl_products.json cache  — produced by scrape_lidl.py (Playwright + API).
             Cache is refreshed in the background when stale (Monday-based, like Lidl
             week Mon–Sun).  Returns cached data immediately while the background
             scrape runs.
          2. Live API fallback  — Food & beverages (10068374), Cenový líder (10077765),
             and Schwarz leaflets flyer.  Used when the cache file does not yet exist
             (first run before scrape_lidl.py has completed).

        NOTE: Lidl SK's full weekly food leaflet (~100+ items) is not fully exposed
        through any structured API. scrape_lidl.py uses Playwright + in-store sweep
        (&store=1) to capture significantly more items than the live fallback alone.
        """
        from datetime import datetime, timezone

        # ── Try cache first ────────────────────────────────────────────────────
        if _lidl_cache_is_stale():
            _trigger_lidl_refresh()
        if os.path.exists(_LIDL_CACHE):
            try:
                with open(_LIDL_CACHE, encoding="utf-8") as f:
                    cached = json.load(f)
                if cached:
                    print(f"  ✓ Lidl: {len(cached)} produktov (z cache)")
                    return cached
            except Exception as e:
                print(f"  ✗ Lidl cache: {e}")

        # ── No cache yet — fall back to live API ───────────────────────────────
        print("  ℹ Lidl: cache nenájdená — používam live API (spustite scrape_lidl.py pre plné výsledky)")

        products   = []
        seen_names = set()

        def _make_product(name, price_val, old_price, pct_disc, image, url_path,
                          category_raw, ts_start, ts_end):
            name = (name or "").strip()
            if not name or not price_val or price_val <= 0:
                return None
            key = name[:50].lower()
            if key in seen_names:
                return None
            seen_names.add(key)

            valid_from = valid_until = ""
            try:
                if ts_start:
                    valid_from  = datetime.fromtimestamp(ts_start, tz=timezone.utc).date().isoformat()
                if ts_end:
                    valid_until = datetime.fromtimestamp(ts_end,   tz=timezone.utc).date().isoformat()
            except Exception:
                pass

            return {
                "store":          "Lidl",
                "leaflet":        "Lidl — aktuálne zľavy",
                "name":           name,
                "brand":          "",
                "price":          float(price_val),
                "price_str":      f"{price_val:.2f} €".replace(".", ","),
                "original_price": f"{old_price:.2f} €".replace(".", ",") if old_price else "",
                "discount_pct":   abs(int(pct_disc)) if pct_disc else 0,
                "image":          image or "",
                "url":            ("https://www.lidl.sk" + url_path) if url_path else "https://www.lidl.sk",
                "category":       normalize_category(category_raw or "", name),
                "type":           normalize_type(category_raw or "", name),
                "valid_from":     valid_from,
                "valid_until":    valid_until,
            }

        def _ingest_search_response(data):
            """Parse items from a Lidl /q/api/search response into products."""
            for item in data.get("items", []):
                d     = item.get("gridbox", {}).get("data", {})
                price = d.get("price", {})
                disc  = price.get("discount", {})

                price_val = price.get("price")
                if not price_val or float(price_val) <= 0:
                    continue

                old_price  = price.get("oldPrice") or 0.0
                pct_disc   = disc.get("percentageDiscount") or 0

                brand = d.get("brand") or {}
                brand_name = (brand.get("brandName", "")
                              if isinstance(brand, dict) else str(brand or ""))

                p = _make_product(
                    name        = d.get("fullTitle", ""),
                    price_val   = float(price_val),
                    old_price   = float(old_price) if old_price else 0,
                    pct_disc    = pct_disc,
                    image       = d.get("image", ""),
                    url_path    = d.get("canonicalPath", ""),
                    category_raw= d.get("category") or "",
                    ts_start    = d.get("storeStartDate"),
                    ts_end      = d.get("storeEndDate"),
                )
                if p:
                    p["brand"] = brand_name
                    products.append(p)

        _search_base = (
            "https://www.lidl.sk/q/api/search"
            "?offset=0&fetchsize=1000&locale=sk_SK&assortment=SK&version=2.1.0"
        )
        _search_hdrs = {"Referer": "https://www.lidl.sk/"}

        # ── Source 1a: Food & beverages (10068374) ─────────────────────────────
        try:
            _ingest_search_response(fetch_json(
                _search_base + "&category.id=10068374",
                extra_headers=_search_hdrs,
            ))
        except Exception as e:
            print(f"  ⚠  Lidl food API (10068374): {e}")

        # ── Source 1b: Cenový líder (10077765) ────────────────────────────────
        # Long-term price reductions (olivový olej, káva, mlieko, etc.) that appear
        # in the weekly leaflet but are tracked under a separate category.
        try:
            _ingest_search_response(fetch_json(
                _search_base + "&category.id=10077765",
                extra_headers=_search_hdrs,
            ))
        except Exception as e:
            print(f"  ⚠  Lidl cenový líder API (10077765): {e}")

        # ── Source 2: Schwarz leaflets API — weekly flyer non-food products ────
        # ~60 non-food items (tools, garden, clothing) with structured prices.
        # Tries Monday-based slug first; falls back to discovering the active
        # flyer via the stable Cenový líder flyer's relatedFlyers list.
        try:
            today    = date.today()
            schwarz_hdrs = {
                "User-Agent": HEADERS["User-Agent"],
                "Accept":     "application/json",
                "Referer":    "https://www.lidl.sk/",
            }

            # Primary: Monday-date slug (Lidl SK week runs Mon–Sun)
            week_mon   = today - timedelta(days=today.weekday())
            flyer_slug = (f"online-letak-platny-od-"
                          f"{week_mon.day:02d}-{week_mon.month:02d}-{week_mon.year}")
            meta = None
            try:
                meta = fetch_json(
                    f"https://endpoints.leaflets.schwarz/v4/flyer"
                    f"?flyer_identifier={flyer_slug}",
                    extra_headers=schwarz_hdrs,
                )
            except Exception:
                pass  # fall through to discovery

            # Fallback: discover active flyer via Cenový líder's relatedFlyers
            if meta is None:
                try:
                    cl = fetch_json(
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
                            meta = fetch_json(
                                f"https://endpoints.leaflets.schwarz/v4/flyer"
                                f"?flyer_identifier={rf_slug}",
                                extra_headers=schwarz_hdrs,
                            )
                            print(f"  ↳ Lidl: active flyer via discovery: {rf_slug}")
                            break
                except Exception as e2:
                    print(f"  ⚠  Lidl flyer discovery: {e2}")

            if meta:
                flyer   = meta.get("flyer", meta)
                prods   = flyer.get("products", {})
                v_from  = flyer.get("offerStartDate", "")
                v_until = flyer.get("offerEndDate",   "")

                for pid, p in (prods.items() if isinstance(prods, dict)
                               else enumerate(prods)):
                    price_raw = p.get("price", "")
                    try:
                        price_val = float(str(price_raw).replace(",", "."))
                    except (ValueError, TypeError):
                        continue
                    if price_val <= 0:
                        continue

                    name = p.get("title", "")
                    url  = p.get("url", "") or p.get("canonicalUrl", "")
                    if url and not url.startswith("http"):
                        url = "https://www.lidl.sk" + url
                    cat  = (p.get("categoryPrimary", "")
                            or p.get("wonCategoryPrimary", ""))

                    prod = _make_product(
                        name        = name,
                        price_val   = price_val,
                        old_price   = 0,
                        pct_disc    = 0,
                        image       = p.get("image", ""),
                        url_path    = None,
                        category_raw= cat,
                        ts_start    = None,
                        ts_end      = None,
                    )
                    if prod:
                        prod["url"]         = url
                        prod["valid_from"]  = v_from[:10] if v_from else ""
                        prod["valid_until"] = v_until[:10] if v_until else ""
                        products.append(prod)

        except Exception as e:
            print(f"  ⚠  Lidl Schwarz flyer API: {e}")

        return products

    def handle_lidl(self, parsed):
        print("  → Lidl search API (Jedlo a nápoje, in-store discounts)")
        try:
            products = self._fetch_lidl_products()
            out = json.dumps({
                "products": products,
                "count":    len(products),
            }, ensure_ascii=False).encode("utf-8")
            print(f"  ✓ Lidl: {len(products)} produktov")
            self.send_json(out)
        except urllib.error.HTTPError as e:
            self.send_error_json(f"Lidl API HTTP {e.code}")
        except Exception as e:
            self.send_error_json(f"Lidl: {e}")

    # ── Billa ─────────────────────────────────────────────────────────────────

    @staticmethod
    def _fetch_billa_products() -> list:
        """Fetch all current-week Billa promotion products (paginated).

        Uses the category-specific endpoint /categories/{kw}/products which
        returns only the current week's products directly, avoiding the need
        to scan all 3 000+ mixed-week products from the general endpoint.
        """
        today      = date.today()
        week_num   = today.isocalendar()[1]
        current_kw = f"kw-{week_num:02d}{today.year}"

        all_products = []
        seen_slugs: set = set()
        page = 1

        while True:
            url = (
                f"https://www.billa.sk/api/product-discovery/categories"
                f"/{current_kw}/products?limit=20&page={page}"
            )
            data    = fetch_json(url)
            results = data.get("results", [])
            if not results:
                break

            for item in results:
                # Deduplicate by slug
                slug = item.get("slug", "")
                if slug and slug in seen_slugs:
                    continue
                if slug:
                    seen_slugs.add(slug)

                price_obj     = item.get("price", {})
                price_cents   = price_obj.get("regular", {}).get("value", 0)
                crossed_cents = price_obj.get("crossed", 0)
                discount_pct  = price_obj.get("discountPercentage", 0)
                if price_cents <= 0:
                    continue

                price_eur   = price_cents / 100
                crossed_eur = crossed_cents / 100 if crossed_cents else None
                images      = item.get("images", [])

                all_products.append({
                    "store":          "Billa",
                    "leaflet":        f"Billa leták {current_kw.upper()}",
                    "name":           item.get("name", ""),
                    "brand":          item.get("brandMarketing", ""),
                    "price":          price_eur,
                    "price_str":      f"{price_eur:.2f} €".replace(".", ","),
                    "original_price": f"{crossed_eur:.2f} €".replace(".", ",") if crossed_eur else "",
                    "discount_pct":   abs(discount_pct) if discount_pct else 0,
                    "image":          images[0] if images else "",
                    "url":            f"https://www.billa.sk/produkt/{slug}" if slug else "https://www.billa.sk",
                    "category":       normalize_category(item.get("category", ""), item.get("name", "")),
                    "type":           normalize_type(item.get("category", ""), item.get("name", "")),
                    "valid_from":     "",
                    "valid_until":    "",
                })

            # Check if we've fetched all pages
            total = data.get("total", 0)
            if page * 20 >= total:
                break
            page += 1

        return all_products

    def handle_billa(self, parsed):
        print("  → Billa API: fetching current week products")
        try:
            products   = self._fetch_billa_products()
            today      = date.today()
            current_kw = f"kw-{today.isocalendar()[1]:02d}{today.year}"
            print(f"  ✓ Billa: {len(products)} produktov ({current_kw})")
            out = json.dumps({
                "products": products,
                "count":    len(products),
                "store":    "Billa",
                "week":     current_kw,
            }, ensure_ascii=False).encode("utf-8")
            self.send_json(out)
        except Exception as e:
            self.send_error_json(f"Billa: {e}")

    # ── Kaufland ─────────────────────────────────────────────────────────────

    def handle_kaufland(self, parsed):
        """Return Kaufland flyer metadata + any available products."""
        slug = get_kaufland_slug()
        print(f"  → Kaufland: {slug}")
        try:
            products, flyer = _fetch_kaufland_products()
            teasers   = flyer.get("teasers", {})
            thumbnail = (
                teasers.get("teaser_666x475") or
                teasers.get("teaser_322x230") or ""
            )
            letak_url = f"https://leaflets.kaufland.com/sk-SK/{slug}/view/flyer/page/1"
            out = json.dumps({
                "store":       "Kaufland",
                "slug":        slug,
                "name":        flyer.get("name", "Kaufland"),
                "valid_from":  flyer.get("offerStartDate", ""),
                "valid_until": flyer.get("offerEndDate", ""),
                "thumbnail":   thumbnail,
                "letak_url":   letak_url,
                "products":    products,
                "count":       len(products),
            }, ensure_ascii=False).encode("utf-8")
            print(f"  ✓ Kaufland: {len(products)} produktov  "
                  f"({flyer.get('offerStartDate','')} – {flyer.get('offerEndDate','')})")
            self.send_json(out)
        except urllib.error.HTTPError as e:
            self.send_error_json(f"Kaufland HTTP {e.code}: {slug}")
        except Exception as e:
            self.send_error_json(f"Kaufland: {e}")

    # ── Tesco ─────────────────────────────────────────────────────────────────

    def handle_tesco(self, parsed):
        """Return Tesco promo products from the local cache."""
        print("  → Tesco: aktuálne promo produkty")
        try:
            products = _fetch_tesco_products()
            out = json.dumps({
                "store":    "Tesco",
                "products": products,
                "count":    len(products),
            }, ensure_ascii=False).encode("utf-8")
            self.send_json(out)
        except Exception as e:
            self.send_error_json(f"Tesco: {e}")

    # ── COOP ─────────────────────────────────────────────────────────────────

    def handle_coop(self, parsed):
        """Return COOP promo products from the local PDF-parsed cache."""
        print("  → COOP: aktuálne letákové produkty")
        try:
            products = _fetch_coop_products()
            out = json.dumps({
                "store":    "COOP",
                "products": products,
                "count":    len(products),
            }, ensure_ascii=False).encode("utf-8")
            self.send_json(out)
        except Exception as e:
            self.send_error_json(f"COOP: {e}")

    # ── Combined: Lidl + Billa + Kaufland + Tesco + COOP ─────────────────────

    def handle_all(self, parsed):
        """Returns products from all active stores combined."""
        all_products = []
        errors       = []

        try:
            lidl = self._fetch_lidl_products()
            all_products.extend(lidl)
            print(f"  ✓ Lidl: {len(lidl)} produktov")
        except Exception as e:
            errors.append(f"Lidl: {e}")
            print(f"  ✗ Lidl: {e}")

        try:
            billa = self._fetch_billa_products()
            all_products.extend(billa)
            print(f"  ✓ Billa: {len(billa)} produktov")
        except Exception as e:
            errors.append(f"Billa: {e}")
            print(f"  ✗ Billa: {e}")

        try:
            kaufland, _ = _fetch_kaufland_products()
            all_products.extend(kaufland)
        except Exception as e:
            errors.append(f"Kaufland: {e}")
            print(f"  ✗ Kaufland: {e}")

        try:
            tesco = _fetch_tesco_products()
            all_products.extend(tesco)
        except Exception as e:
            errors.append(f"Tesco: {e}")
            print(f"  ✗ Tesco: {e}")

        try:
            coop = _fetch_coop_products()
            all_products.extend(coop)
        except Exception as e:
            errors.append(f"COOP: {e}")
            print(f"  ✗ COOP: {e}")

        out = json.dumps({
            "products": all_products,
            "count":    len(all_products),
            "errors":   errors,
        }, ensure_ascii=False).encode("utf-8")
        self.send_json(out)

    # ── HTTP helpers ──────────────────────────────────────────────────────────

    def send_json(self, data_bytes):
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(data_bytes)))
        self.end_headers()
        self.wfile.write(data_bytes)

    def send_error_json(self, message):
        print(f"  ✗ {message}")
        out = json.dumps({"error": message}).encode("utf-8")
        self.send_response(502)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(out)

    def log_message(self, fmt, *args):
        msg    = str(args[0]) if args else ""
        status = str(args[1]) if len(args) > 1 else ""
        if "favicon" not in msg and "api" not in msg and msg:
            print(f"  [{status}] {msg.split()[0] if msg else ''}")


if __name__ == "__main__":
    os.chdir(_BASE_DIR)

    # Kick off background scrapes if caches are stale/missing.
    if _lidl_cache_is_stale():
        _trigger_lidl_refresh()
    else:
        print(f"  ✓  Lidl cache je aktuálny ({_LIDL_CACHE})")

    if _kaufland_cache_is_stale():
        _trigger_kaufland_refresh()
    else:
        print(f"  ✓  Kaufland cache je aktuálny ({_KAUFLAND_CACHE})")

    if _tesco_cache_is_stale():
        _trigger_tesco_refresh()
    else:
        print(f"  ✓  Tesco cache je aktuálny ({_TESCO_CACHE})")

    if _coop_cache_is_stale():
        _trigger_coop_refresh()
    else:
        print(f"  ✓  COOP cache je aktuálny ({_COOP_CACHE})")

    print(f"\n🛒  LacnéNákupy server beží na http://localhost:{PORT}")
    print(f"   Otvorte tento odkaz v prehliadači.")
    print(f"   Zastavenie: Ctrl+C\n")
    with http.server.HTTPServer(("", PORT), Handler) as httpd:
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\n\nServer zastavený.")
