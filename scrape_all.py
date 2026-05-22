#!/usr/bin/env python3
"""
scrape_all.py — run all three store scrapers in parallel and report results.

Usage
-----
  python scrape_all.py           # refresh all stores
  python scrape_all.py kaufland  # refresh one store only
  python scrape_all.py tesco coop

Lidl and Billa are fetched live by server.py on every request and don't
have a standalone scraper — they don't need to be refreshed here.
"""

import json
import os
import subprocess
import sys
import time
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

SCRAPERS = {
    "kaufland": {
        "script":  "scrape_kaufland.py",
        "output":  "kaufland_products.json",
        "utf8":    False,
    },
    "tesco": {
        "script":  "scrape_tesco.py",
        "output":  "tesco_products.json",
        "utf8":    False,
    },
    "coop": {
        "script":  "scrape_coop.py",
        "output":  "coop_products.json",
        "utf8":    True,   # needs -X utf8 on Windows
    },
}


def _product_count(json_path: str) -> int:
    """Return the number of products in a JSON cache file, or -1 on error."""
    try:
        with open(json_path, encoding="utf-8") as f:
            return len(json.load(f))
    except Exception:
        return -1


def run_scrapers(targets: list[str]) -> None:
    procs: dict[str, subprocess.Popen] = {}
    start_times: dict[str, float] = {}

    print(f"\n{'='*60}")
    print(f"  LacnéNákupy — scrape_all.py")
    print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  Stores: {', '.join(t.capitalize() for t in targets)}")
    print(f"{'='*60}\n")

    # Launch all scrapers in parallel
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"

    for name in targets:
        cfg    = SCRAPERS[name]
        script = os.path.join(BASE_DIR, cfg["script"])
        if not os.path.exists(script):
            print(f"  ⚠  {name}: {cfg['script']} not found — skipping")
            continue

        cmd = [sys.executable]
        if cfg["utf8"]:
            cmd.append("-X")
            cmd.append("utf8")
        cmd.append(script)

        print(f"  ↻  {name.capitalize()}: starting …")
        procs[name] = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            env=env,
            cwd=BASE_DIR,
        )
        start_times[name] = time.monotonic()

    if not procs:
        print("  Nothing to run.")
        return

    print()

    # Wait for all to finish and collect output
    outputs: dict[str, str] = {}
    exit_codes: dict[str, int] = {}

    for name, proc in procs.items():
        stdout, _ = proc.communicate()
        outputs[name]    = stdout.decode("utf-8", "replace")
        exit_codes[name] = proc.returncode

    # Print a summary per scraper
    total_ok = 0
    total_products = 0

    for name in targets:
        if name not in exit_codes:
            continue   # was skipped

        elapsed = time.monotonic() - start_times[name]
        ok      = exit_codes[name] == 0
        count   = _product_count(os.path.join(BASE_DIR, SCRAPERS[name]["output"]))

        status = "✅" if ok else "❌"
        count_str = f"{count} produktov" if count >= 0 else "chyba pri čítaní JSON"

        print(f"  {status}  {name.capitalize():<12} {count_str:<22}  {elapsed:.0f}s")

        if ok:
            total_ok += 1
            if count > 0:
                total_products += count
        else:
            # Print the last 10 lines of the failed scraper's output
            lines = [l for l in outputs[name].splitlines() if l.strip()]
            tail  = lines[-10:] if len(lines) > 10 else lines
            print(f"\n  --- {name} output (tail) ---")
            for line in tail:
                print(f"  {line}")
            print()

    print(f"\n{'='*60}")
    if total_ok == len(procs):
        print(f"  Všetky scrapery hotové.  Celkom: {total_products} produktov.")
        print(f"  Reštartujte server.py pre načítanie nových dát.")
    else:
        failed = [n for n in procs if exit_codes.get(n, 1) != 0]
        print(f"  {total_ok}/{len(procs)} hotových.  Zlyhalo: {', '.join(failed)}")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    # Optionally pass store names as CLI args; default = all
    requested = [a.lower() for a in sys.argv[1:] if a.lower() in SCRAPERS]
    targets   = requested if requested else list(SCRAPERS.keys())

    unknown = [a for a in sys.argv[1:] if a.lower() not in SCRAPERS]
    if unknown:
        print(f"Unknown store(s): {', '.join(unknown)}")
        print(f"Valid options: {', '.join(SCRAPERS.keys())}")
        sys.exit(1)

    run_scrapers(targets)
