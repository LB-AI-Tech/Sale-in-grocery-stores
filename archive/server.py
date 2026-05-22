"""
server.py — LacnéNákupy local server
Serves index.html and proxies Kimbino API calls.

Usage:  python server.py
Then:   open http://localhost:8000
Stop:   Ctrl+C
"""

import http.server
import urllib.request
import urllib.parse
import urllib.error
import json
import gzip
import os

PORT = 8000

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "sk-SK,sk;q=0.9,en;q=0.8",
    "Accept-Encoding": "gzip, deflate",
    "Origin": "https://www.kimbino.sk",
    "Referer": "https://www.kimbino.sk/",
    "X-Requested-With": "XMLHttpRequest",
    "Connection": "keep-alive",
}

# We try these URL patterns in order until one returns valid JSON
URL_PATTERNS = [
    "https://www.kimbino.sk/detail/?countryId=sk&sef={sef}",
    "https://www.kimbino.sk/api/v1/detail/?countryId=sk&sef={sef}",
    "https://www.kimbino.sk/produkty/{sef}/?format=json",
    "https://www.kimbino.sk/sk/detail/?sef={sef}",
]


def fetch_url(url):
    """Fetch a URL and return decompressed bytes, or raise."""
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=15) as resp:
        raw = resp.read()
        encoding = resp.headers.get('Content-Encoding', '')
        status = resp.status
    if 'gzip' in encoding:
        raw = gzip.decompress(raw)
    return status, raw


class Handler(http.server.SimpleHTTPRequestHandler):

    def do_GET(self):
        if self.path.startswith('/api'):
            self.proxy_kimbino()
        else:
            super().do_GET()

    def proxy_kimbino(self):
        parsed = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed.query)
        sef = params.get('sef', [''])[0]

        if not sef:
            self.send_error(400, "Missing ?sef= parameter")
            return

        last_error = None

        for pattern in URL_PATTERNS:
            url = pattern.format(sef=urllib.parse.quote(sef))
            print(f"  → Trying: {url}")

            try:
                status, data = fetch_url(url)
                parsed_json = json.loads(data.decode('utf-8'))

                # Check it looks like product data
                if 'leaflets' in parsed_json or 'name' in parsed_json:
                    count = len(parsed_json.get('leaflets', []))
                    print(f"  ✓ OK — {count} produktov nájdených  [{url}]")
                    out = json.dumps(parsed_json, ensure_ascii=False).encode('utf-8')
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json; charset=utf-8")
                    self.send_header("Access-Control-Allow-Origin", "*")
                    self.send_header("Content-Length", str(len(out)))
                    self.end_headers()
                    self.wfile.write(out)
                    return
                else:
                    print(f"  ✗ Unexpected JSON structure: {str(parsed_json)[:100]}")

            except urllib.error.HTTPError as e:
                body = e.read()
                try:
                    if 'gzip' in (e.headers.get('Content-Encoding') or ''):
                        body = gzip.decompress(body)
                    body_text = body.decode('utf-8', errors='replace')[:150]
                except Exception:
                    body_text = repr(body[:80])
                print(f"  ✗ HTTP {e.code}: {body_text}")
                last_error = f"HTTP {e.code}"

            except json.JSONDecodeError as e:
                print(f"  ✗ Not JSON: {e}")
                last_error = "Not JSON"

            except Exception as e:
                print(f"  ✗ {type(e).__name__}: {e}")
                last_error = str(e)

        # All patterns failed
        print(f"  ✗ All URL patterns failed. Last error: {last_error}")
        error = json.dumps({"error": f"All API patterns failed: {last_error}"}).encode()
        self.send_response(502)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(error)

    def log_message(self, fmt, *args):
        msg = args[0] if args else ''
        if 'favicon' not in msg and '/api' not in msg:
            status = args[1] if len(args) > 1 else ''
            print(f"  ✓ [{status}] {msg.split()[0] if msg else ''}")


if __name__ == "__main__":
    os.chdir(os.path.dirname(os.path.abspath(__file__)))

    print(f"\n🛒  LacnéNákupy server beží na http://localhost:{PORT}")
    print(f"   Otvorte tento odkaz v prehliadači.")
    print(f"   Zastavenie: Ctrl+C\n")

    with http.server.HTTPServer(("", PORT), Handler) as httpd:
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\n\nServer zastavený.")
