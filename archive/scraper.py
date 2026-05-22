"""
scraper.py — Fetches products directly from Lidl's public API
Saves results to products.json for the website to use.

Usage:
    python scraper.py          # scrape current Lidl leaflet
    python scraper.py --next   # scrape next week's leaflet too

No extra libraries needed — uses only Python built-ins.
"""

import urllib.request
import urllib.error
import json
import gzip
import re
import os
from datetime import datetime

# ── Output file ──
OUTPUT_FILE = "products.json"

# ── Lidl API ──
# The flyer identifier changes weekly. We auto-detect the current one.
LIDL_FLYER_LIST = "https://www.lidl.sk/l/api/v4/folders?locale=sk-SK&region_id=1"
LIDL_FLYER_API  = "https://www.lidl.sk/l/api/v4/flyer?flyer_identifier={slug}&region_id=1"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, */*",
    "Accept-Language": "sk-SK,sk;q=0.9",
    "Accept-Encoding": "gzip, deflate",
    "Referer": "https://www.lidl.sk/",
}


def fetch(url):
    """Fetch URL, decompress if needed, return parsed JSON."""
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=15) as resp:
        raw = resp.read()
        enc = resp.headers.get("Content-Encoding", "")
    if "gzip" in enc:
        raw = gzip.decompress(raw)
    return json.loads(raw.decode("utf-8"))


def get_current_flyer_slug():
    """
    Find the current weekly leaflet slug automatically.
    Falls back to constructing it from today's date if API fails.
    """
    # Try to read it from the related flyers we already know about
    # The slug format is: online-letak-platny-od-DD-MM-YYYY
    # Lidl publishes new leaflets on Mondays
    from datetime import date, timedelta
    today = date.today()
    # Find last Monday
    monday = today - timedelta(days=today.weekday())
    slug = f"online-letak-platny-od-{monday.strftime('%d-%m-%Y')}"
    return slug


def extract_products(flyer_data, store="Lidl"):
    """
    Extract individual products from the Lidl flyer API response.
    Products are in flyer_data['products'] as a dict keyed by internal ID.
    """
    products_raw = flyer_data.get("products", {})
    flyer = flyer_data.get("flyer", {})

    valid_from = flyer.get("offerStartDate", "")
    valid_until = flyer.get("offerEndDate", "")
    leaflet_url = flyer.get("flyerUrlAbsolute", "https://www.lidl.sk/l/sk/letak/")
    leaflet_name = f"{flyer.get('name', 'Leták')} — {flyer.get('title', '')}"

    products = []
    seen_ids = set()

    for item in products_raw.values():
        product_id = item.get("productId", "")
        if product_id in seen_ids:
            continue
        seen_ids.add(product_id)

        price_raw = item.get("price", "")
        try:
            price = float(str(price_raw).replace(",", "."))
        except (ValueError, TypeError):
            price = 0.0

        if price <= 0:
            continue

        products.append({
            "store":       store,
            "leaflet":     leaflet_name,
            "name":        item.get("title", ""),
            "brand":       item.get("brand", ""),
            "price":       price,
            "price_str":   f"{price:.2f} €".replace(".", ","),
            "image":       item.get("image", ""),
            "url":         item.get("url", leaflet_url),
            "category":    item.get("categoryPrimary", "").split("/")[-1],
            "valid_from":  valid_from,
            "valid_until": valid_until,
            "scraped_at":  datetime.now().isoformat(),
        })

    return products


def scrape_lidl(slug=None):
    if not slug:
        slug = get_current_flyer_slug()

    url = LIDL_FLYER_API.format(slug=slug)
    print(f"  → Fetching Lidl: {url}")

    try:
        data = fetch(url)
        if not data.get("success"):
            print(f"  ✗ API returned success=false")
            return []

        products = extract_products(data)
        print(f"  ✓ Lidl: {len(products)} products ({slug})")

        # Also check for next week's leaflet
        related = data.get("relatedFlyers", [])
        next_slugs = [
            r["slug"] for r in related
            if "letak-platny-od" in r.get("slug", "") and r["slug"] != slug
        ]
        if next_slugs:
            print(f"  ℹ Next leaflet available: {next_slugs[0]}")

        return products

    except urllib.error.HTTPError as e:
        print(f"  ✗ HTTP {e.code} for Lidl ({slug})")
        return []
    except Exception as e:
        print(f"  ✗ Error: {e}")
        return []


def save(products):
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(products, f, ensure_ascii=False, indent=2)
    print(f"\n✅ Saved {len(products)} products to {OUTPUT_FILE}")


if __name__ == "__main__":
    import sys
    print("\n🛒 Slovak Grocery Leaflet Scraper\n")

    all_products = []

    # Lidl
    print("Scraping Lidl...")
    all_products += scrape_lidl()

    # More stores will be added here (Kaufland, Tesco, Billa...)

    save(all_products)
    print(f"\nDone! Run 'python server.py' and open http://localhost:8000")
