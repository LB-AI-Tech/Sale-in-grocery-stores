from playwright.sync_api import sync_playwright
import json
import re
import csv
from datetime import datetime
from collections import defaultdict

CATEGORIES = {
    "Ovocie a Zelenina": ["jahod", "čučoriedk", "paprika", "šalát", "melón", "uhorka", "paradajk", "jablko", "marhuľ", "mango", "hrušky", "banán"],
    "Mäso": ["kurac", "bravčov", "hydina", "stehn", "rebier", "šunka", "saláma", "klobás", "prsn", "krkovič"],
    "Pečivo": ["chlieb", "rožok", "donut", "vianočka", "pečivo", "koláč", "bageta", "korbáčik"],
    "Mliečne výrobky": ["sy", "jogurt", "tvaroh", "smotana", "maslo", "eidam", "mozzarella", "bryndza", "mlieko", "korbáčik", "chedar"],
    "Nápoje": ["pivo", "víno", "šťava", "kola", "limonad", "staropramen", "krusovice", "becherovka", "urquell"],
    "Trvanlivé / Snacks": ["cestovin", "ryža", "olej", "konzerva", "lupienky", "čokolád", "tuniak"],
}

def get_category(name: str) -> str:
    name_lower = name.lower()
    for cat, keywords in CATEGORIES.items():
        if any(kw in name_lower for kw in keywords):
            return cat
    return "Ostatné"


def is_valid_grocery(name: str, price_str: str) -> bool:
    if len(name) < 8:
        return False
    name_lower = name.lower()
    bad = ["aktuálny týždeň", "ponuka platná", "mimoriadna ponuka", "platnosť", "z lásky", "partner", "hokej", 
           "park side", "liv & bo", "kaufland", "pultový predaj", "spice & soul", "panvica", "pokrievka", "miska", "kuchynské", "skrinka", "kompresor"]
    
    if any(b in name_lower for b in bad):
        return False
    
    try:
        price = float(re.sub(r'[^\d.,]', '', price_str).replace(',', '.'))
        if price < 0.2 or price > 30:
            return False
    except:
        return False
    return True


def extract_unit(full_text: str) -> str:
    match = re.search(r'(\d+[.,]?\d*\s*(g|kg|l|ml|ks|balenie|kus|por|bal))', full_text, re.I)
    return match.group(1) if match else ""


def scrape_kaufland_flyer():
    products = []
    seen = defaultdict(bool)
    
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        
        print("🌐 Opening Kaufland weekly flyer...")
        page.goto("https://predajne.kaufland.sk/aktualna-ponuka/prehlad.html", wait_until="networkidle")
        page.wait_for_timeout(8000)

        for _ in range(5):
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            page.wait_for_timeout(2500)

        items = page.query_selector_all("article, div[class*='offer'], div[class*='product']")

        print(f"Found {len(items)} items → processing...")

        for item in items:
            try:
                full_text = item.inner_text()
                
                # Improved name extraction - take longer text
                name_match = re.search(r'^([^\d\n]{10,140})', full_text.strip())
                name = name_match.group(1).strip() if name_match else ""

                prices = re.findall(r'(\d+[.,]?\d*)', full_text)
                if not name or not prices:
                    continue

                current_price = prices[-1]

                if not is_valid_grocery(name, current_price):
                    continue

                key = (name[:70].lower(), current_price)
                if seen[key]:
                    continue
                seen[key] = True

                unit = extract_unit(full_text)
                category = get_category(name)

                product = {
                    "name": name[:170],
                    "category": category,
                    "current_price": current_price,
                    "original_price": prices[0] if len(prices) > 1 else "",
                    "unit": unit,
                    "timestamp": datetime.now().isoformat()
                }
                products.append(product)

                if len(products) % 50 == 0 or len(products) < 20:
                    print(f"✓ {category:<18} | {name[:58]:<58} | {current_price} €   {unit}")

            except:
                continue

        browser.close()

    print(f"\n✅ Final count: {len(products)} unique products")

    with open("kaufland_flyer_final.json", "w", encoding="utf-8") as f:
        json.dump(products, f, ensure_ascii=False, indent=2)

    with open("kaufland_flyer_final.csv", "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["name", "category", "current_price", "original_price", "unit", "timestamp"])
        writer.writeheader()
        writer.writerows(products)

    return products


if __name__ == "__main__":
    scrape_kaufland_flyer()