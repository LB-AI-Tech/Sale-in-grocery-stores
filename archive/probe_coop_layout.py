#!/usr/bin/env python3
"""Understand the exact PDF layout to tune the positional parser."""
import pdfplumber, re, io, urllib.request, ssl

ctx = ssl.create_default_context(); ctx.check_hostname=False; ctx.verify_mode=0

def fetch(url):
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 Chrome/124.0.0.0",
        "Accept": "application/pdf,*/*",
        "Referer": "https://www.coop.sk/",
    })
    with urllib.request.urlopen(req, timeout=60, context=ctx) as r:
        return r.read()

print("Downloading PDF 1311...")
pdf_bytes = fetch("https://www.coop.sk/files/pdf-file/flip_1311/pdf_1311.pdf")

with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
    # ── A. Page dimensions ────────────────────────────────────────────────────
    p0 = pdf.pages[0]
    print(f"\nPage dimensions: width={p0.width:.1f}  height={p0.height:.1f}")

    # ── B. x_tolerance impact on spaced chars ──────────────────────────────────
    print("\n=== x_tolerance comparison on page 2 ===")
    p2 = pdf.pages[1]
    for xt in [3, 8, 15, 25]:
        words = p2.extract_words(x_tolerance=xt, y_tolerance=3)
        long_words = [w for w in words if len(w['text']) > 3]
        print(f"  x_tol={xt:2d}: {len(words):3d} words, {len(long_words):3d} multi-char")
        # Show any "jednotková" or similar
        jc = [w for w in words if 'jednotk' in w['text'].lower()]
        if jc:
            print(f"           'jednotková' words: {[(w['text'], round(w['x0'])) for w in jc]}")

    # ── C. Detailed layout of page 1 (known good) ─────────────────────────────
    print("\n=== Page 1 word positions (x_tol=8) ===")
    p1 = pdf.pages[0]
    words1 = p1.extract_words(x_tolerance=8, y_tolerance=3)
    print(f"  {len(words1)} words total")
    print(f"  {'text':<30} {'x0':>6} {'top':>6} {'x1':>6}")
    for w in words1:
        print(f"  {w['text']:<30} {w['x0']:>6.1f} {w['top']:>6.1f} {w['x1']:>6.1f}")

    # ── D. Column structure: x-position distribution ──────────────────────────
    print("\n=== X-position histogram (page 1) ===")
    import collections
    xcounts = collections.Counter(round(w['x0'] / 50) * 50 for w in words1 if len(w['text']) > 3)
    for x, cnt in sorted(xcounts.items()):
        print(f"  x~{x:4.0f}: {'#'*min(cnt,40)} ({cnt})")

    # ── E. Check layout=True output ────────────────────────────────────────────
    print("\n=== extract_text(layout=True) page 1 ===")
    try:
        lt = p1.extract_text(layout=True) or ""
        print(repr(lt[:1000]))
    except Exception as e:
        print(f"  layout=True failed: {e}")

    # ── F. Page 2 word positions (the problematic page) ───────────────────────
    print("\n=== Page 2 word positions (x_tol=8) — looking for unit prices ===")
    words2 = p2.extract_words(x_tolerance=8, y_tolerance=3)
    # Show price-like and unit-price tokens
    for w in words2:
        if (re.fullmatch(r'\d{3,4}', w['text']) or
            re.match(r'\d+[,.]\d+', w['text']) or
            'EUR' in w['text'] or
            'jednotk' in w['text'].lower() or
            'cena' in w['text'].lower()):
            print(f"  {w['text']:<20} x0={w['x0']:>6.1f}  top={w['top']:>6.1f}")
