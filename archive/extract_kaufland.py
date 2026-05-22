import pdfplumber
import re
import json
import csv
from pathlib import Path
from typing import List, Dict

def extract_kaufland_flyer(pdf_path: str) -> List[Dict]:
    products = []
    validity = "14. 5. 2026 - 20. 5. 2026"  # from the cover

    with pdfplumber.open(pdf_path) as pdf:
        for page_num, page in enumerate(pdf.pages, 1):
            # Better text extraction for layouts
            text = page.extract_text(x_tolerance=2, y_tolerance=2, layout=True) or ""
            
            lines = [line.strip() for line in text.split('\n') if line.strip()]

            for line in lines:
                # Skip very short lines
                if len(line) < 8:
                    continue

                # Find price patterns (new price is usually the biggest / red one)
                price_matches = re.findall(r'(\d+[\.,]?\d*)\s*€?', line)
                percent_match = re.search(r'[-]?\d+%', line)

                if price_matches:
                    # Try to extract product name (text before prices)
                    name_part = re.split(r'\d+[\.,]?\d*', line)[0].strip()
                    name = clean_name(name_part)

                    if not name or len(name) < 3:
                        continue

                    product = {
                        "page": page_num,
                        "name": name,
                        "unit": extract_unit(line),
                        "discount_percent": percent_match.group(0) if percent_match else "",
                        "prices": price_matches,
                        "current_price": price_matches[-1] if price_matches else "",  # usually last is current
                        "original_price": price_matches[0] if len(price_matches) > 1 else "",
                        "raw_line": line[:400],
                        "validity": validity,
                        "category": guess_category(line, page_num)
                    }
                    products.append(product)

            # Also try tables (some pages have them)
            tables = page.extract_tables()
            for table in tables:
                for row in table:
                    if not row:
                        continue
                    row_text = " | ".join(str(cell).strip() for cell in row if cell)
                    if any(c.isdigit() for c in row_text):
                        # Process similar way as lines
                        price_matches = re.findall(r'(\d+[\.,]?\d*)', row_text)
                        if price_matches and len(row_text) > 10:
                            name = clean_name(row_text.split(str(price_matches[0]))[0])
                            products.append({
                                "page": page_num,
                                "name": name,
                                "unit": extract_unit(row_text),
                                "current_price": price_matches[-1],
                                "original_price": price_matches[0] if len(price_matches) > 1 else "",
                                "raw_line": row_text[:400],
                                "validity": validity,
                                "category": guess_category(row_text, page_num)
                            })

    # Remove obvious duplicates
    seen = set()
    unique = []
    for p in products:
        key = (p["name"][:80], p["current_price"])
        if key not in seen:
            seen.add(key)
            unique.append(p)
    
    return unique


def clean_name(text: str) -> str:
    text = re.sub(r'[-–]\d+%|Card|KUPÓN|SUPER VÝHODNE|AKCIOVÁ PONUKA', '', text, flags=re.I)
    text = re.sub(r'\s+', ' ', text.strip())
    return text.strip()[:180]


def extract_unit(text: str) -> str:
    unit_match = re.search(r'(\d+[\.,]?\d*)\s*(kg|g|l|ml|ks|bal|por|kus|100\s*g|500\s*g|1\s*kg)', text, re.I)
    return unit_match.group(0) if unit_match else ""


def guess_category(text: str, page: int) -> str:
    text_lower = text.lower()
    if page <= 7: return "Ovocie a Zelenina"
    if "pečivo" in text_lower or "chlieb" in text_lower: return "Pečivo"
    if "mäso" in text_lower or "hydina" in text_lower or "kuracie" in text_lower: return "Mäso"
    if "sy" in text_lower: return "Mliečne výrobky / Syry"
    if "nápoj" in text_lower or "pivo" in text_lower: return "Nápoje"
    return "Ostatné"


# ====================== RUN ======================
if __name__ == "__main__":
    pdf_file = "Kaufland-14-05-2026-20-05-2026-00.pdf"
    
    if not Path(pdf_file).exists():
        print("PDF file not found!")
    else:
        print("Extracting from Kaufland leták...")
        data = extract_kaufland_flyer(pdf_file)
        
        print(f"\n✅ Extracted {len(data)} offers.")

        # Save JSON
        with open("kaufland_offers.json", "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

        # Save CSV
        with open("kaufland_offers.csv", "w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=data[0].keys() if data else [])
            writer.writeheader()
            writer.writerows(data)

        # Preview
        for item in data[:8]:
            print(f"{item['name'][:80]:<70} | {item['current_price']} € | {item['discount_percent']}")