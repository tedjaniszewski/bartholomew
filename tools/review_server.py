"""
Serve the site locally with saving enabled for the place-name review tool.

    python3 tools/review_server.py            # then open http://localhost:8000/review.html
    python3 tools/review_server.py --port 8080

The review page saves each change to site/data/gazetteers/<map-id>.json
through this server; commit that file to publish the corrections. Only
listens on localhost.
"""

import argparse
import json
import re
import sys
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SITE = ROOT / "site"
sys.path.insert(0, str(ROOT / "tools" / "ocr"))
from build_gazetteer import write_gazetteer  # noqa: E402

GAZETTEER_PATH = re.compile(r"^/api/gazetteers/([a-z0-9-]+)\.json$")


class Handler(SimpleHTTPRequestHandler):
    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def do_GET(self):
        if self.path == "/api/ping":
            return self.reply(200, {"ok": True})
        super().do_GET()

    def do_PUT(self):
        match = GAZETTEER_PATH.match(self.path)
        if not match:
            return self.reply(404, {"error": "not found"})
        try:
            length = int(self.headers.get("Content-Length", 0))
            document = json.loads(self.rfile.read(length))
            if document.get("map") != match.group(1) or not isinstance(document.get("entries"), list):
                raise ValueError("not a gazetteer for this map")
        except (ValueError, json.JSONDecodeError) as error:
            return self.reply(400, {"error": str(error)})
        path = SITE / "data" / "gazetteers" / f"{match.group(1)}.json"
        write_gazetteer(path, document)
        self.reply(200, {"saved": len(document["entries"])})

    def reply(self, status, body):
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, format, *args):
        if self.command == "PUT" or (args and str(args[1])[0] in "45"):
            super().log_message(format, *args)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), partial(Handler, directory=str(SITE)))
    print(f"Review tool: http://localhost:{args.port}/review.html  (Ctrl+C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
