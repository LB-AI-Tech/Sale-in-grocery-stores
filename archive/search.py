"""
search.py — query the local database for discount results.

Usage:
    python search.py mlieko
    python search.py "olivový olej"
    python search.py milk          # English terms are mapped to Slovak
"""

import sqlite3
import sys
import json

DB_PATH = "leaflets.db"

# Simple English → Slovak synonym map
SYNONYMS = {
    "milk":       "mlieko",
    "bread":      "chlieb",
    "butter":     "maslo",
    "eggs":       "vajcia",
    "cheese":     "syr",
    "yogurt":     "jogurt",
    "yoghurt":    "jogurt",
    "ham":        "šunka",
    "chicken":    "kuracie mäso",
    "rice":       "ryža",
    "pasta":      "cestoviny",
    "olive oil":  "olivový olej",
    "bananas":    "banány",
    "apples":     "jablká",
    "tomatoes":   "paradajky",
    "beer":       "pivo",
    "coffee":     "káva",
    "tea":        "čaj",
    "water":      "minerálna voda",
    "chocolate":  "čokoláda",
    "ice cream":  "zmrzlina",
}


def search(query: str) -> list[dict]:
    # Translate if English
    resolved = SYNONYMS.get(query.lower(), query)

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    rows = conn.execute("""
        SELECT query, store, leaflet, valid_from, valid_until, page_url, scraped_at
        FROM discounts
        WHERE LOWER(query) LIKE LOWER(?)
        ORDER BY store
    """, (f"%{resolved}%",)).fetchall()

    conn.close()
    return [dict(r) for r in rows]


def pretty_print(results: list[dict], query: str):
    if not results:
        print(f'\nNo discounts found for "{query}". Try running: python scraper.py --query "{query}"')
        return

    print(f'\n🔍 Discounts for "{query}" ({len(results)} result(s)):\n')
    print(f"{'Store':<20} {'Valid from':<12} {'Until':<12} {'Leaflet'}")
    print("─" * 80)
    for r in results:
        print(
            f"{r['store']:<20} "
            f"{r['valid_from'] or '?':<12} "
            f"{r['valid_until'] or '?':<12} "
            f"{r['leaflet'][:40] if r['leaflet'] else ''}"
        )
    print()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python search.py <product>")
        sys.exit(1)

    query = " ".join(sys.argv[1:])
    results = search(query)
    pretty_print(results, query)
