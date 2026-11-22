"""Development only: serve the UI and the same Api over HTTP on localhost, so the
interface can be checked in an ordinary browser. The app itself uses pywebview."""

from __future__ import annotations

import json
import os
import sys
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from forest.app.api import Api

UI = Path(__file__).resolve().parent / "ui"
API = Api()


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=str(UI), **kw)

    def log_message(self, *a):  # quiet
        pass

    def do_POST(self):
        if not self.path.startswith("/api/"):
            self.send_error(404)
            return
        name = self.path[5:]
        fn = getattr(API, name, None)
        if name.startswith("_") or not callable(fn):
            self.send_error(404)
            return
        args = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"[]")
        try:
            body = json.dumps(fn(*args)).encode()
            self.send_response(200)
        except Exception as exc:  # noqa: BLE001
            body = json.dumps({"ok": False, "error": repr(exc)}).encode()
            self.send_response(500)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)


def main() -> None:
    # PORT (set by preview tools that pick a free port) wins over the command-line default.
    port = int(os.environ.get("PORT") or (sys.argv[1] if len(sys.argv) > 1 else 8765))
    print(f"Forest Studio dev UI on http://127.0.0.1:{port}", flush=True)
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()


if __name__ == "__main__":
    main()
