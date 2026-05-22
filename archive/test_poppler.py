from pdf2image import convert_from_path
import os

POPPLER_PATH = r"C:\poppler-25.12.0\Library\bin"

print("Checking if folder exists:")
print(os.path.exists(POPPLER_PATH))

print("\nContents of the bin folder:")
try:
    print(os.listdir(POPPLER_PATH))
except Exception as e:
    print("Error:", e)

# Try conversion with explicit path
try:
    images = convert_from_path(
        r"C:\Users\Lenovo\Desktop\letak-scraper\Kaufland-14-05-2026-20-05-2026-00.pdf", 
        dpi=200,
        poppler_path=POPPLER_PATH
    )
    print(f"\n✅ Success! Converted {len(images)} pages.")
except Exception as e:
    print("\n❌ Error:", str(e))