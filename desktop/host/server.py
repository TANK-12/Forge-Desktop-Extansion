"""The cross-platform host's own little web server (Relay task 130).

The desktop page and the settings window load from here instead of file://,
so the host can hand each page its start-up data before any of its scripts
run (window.FORGE_DESKTOP, as particles.py's user script does on Linux) and
serve the theme, the wallpaper and the settings without writing into the
app's folder. Only this computer can reach it (127.0.0.1), every path starts
with a random token, and only the app's own page files are served.

The desktop pages talk back through pywebview's bridge (fast, both ways);
the settings window uses the small JSON API here, so it works in any
browser too.
"""

from __future__ import annotations

import json
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Protocol
from urllib.parse import parse_qs, urlsplit

TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".woff2": "font/woff2",
}
# the folders under the app root the pages may load from
SERVED = ("desktop", "shared", "hub")
BOOT_MARK = "<head>"
MAX_BODY = 1_000_000


class Api(Protocol):
    def boot(self, screen: int, part: str) -> dict: ...
    def theme_js(self) -> str: ...
    def wallpaper(self) -> Path | None: ...
    def settings_info(self) -> dict: ...
    def set_setting(self, key: str, value) -> dict: ...
    def action(self, name: str) -> dict: ...


def boot_html(page: str, boot: dict) -> str:
    """The page with its start-up data as the first script in <head>."""
    # no "<" at all inside the script: a saved note holding "<!--<script>"
    # or "</script>" must not end or upset it
    data = json.dumps(boot).replace("<", "\\u003c")
    tag = f"<script>window.FORGE_DESKTOP = {data};</script>"
    if BOOT_MARK not in page:
        return tag + page
    return page.replace(BOOT_MARK, BOOT_MARK + tag, 1)


def safe_file(root: Path, rel: str) -> Path | None:
    """`rel` under one of the served folders of `root`, a file of a served
    type, never outside root (no .., no links out)."""
    parts = [p for p in rel.split("/") if p]
    if not parts or parts[0] not in SERVED or any(p in ("..", ".") for p in parts):
        return None
    path = (root / Path(*parts)).resolve()
    try:
        path.relative_to(root.resolve())
    except ValueError:
        return None
    if path.suffix.lower() not in TYPES or not path.is_file():
        return None
    return path


class Server:
    def __init__(self, root: Path, api: Api, token: str | None = None):
        self.root = Path(root)
        self.api = api
        self.token = token or secrets.token_urlsafe(18)
        self.httpd: ThreadingHTTPServer | None = None

    @property
    def port(self) -> int:
        return self.httpd.server_address[1] if self.httpd else 0

    def url(self, rel: str) -> str:
        return f"http://127.0.0.1:{self.port}/{self.token}/{rel.lstrip('/')}"

    def start(self) -> "Server":
        server = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format, *args):  # quiet: no console
                return None

            def do_GET(self):
                server.answer(self, "GET")

            def do_POST(self):
                server.answer(self, "POST")

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.httpd.daemon_threads = True
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        return self

    def stop(self) -> None:
        if self.httpd:
            self.httpd.shutdown()
            self.httpd.server_close()
            self.httpd = None

    # ------------------------------------------------------------------
    def answer(self, h: BaseHTTPRequestHandler, method: str) -> None:
        try:
            status, ctype, body = self.route(h, method)
        except Exception as e:  # one bad ask never stops the server
            status, ctype, body = 500, TYPES[".json"], _json({"error": str(e)})
        h.send_response(status)
        h.send_header("Content-Type", ctype)
        h.send_header("Content-Length", str(len(body)))
        h.send_header("Cache-Control", "no-store")
        h.send_header("X-Content-Type-Options", "nosniff")
        h.end_headers()
        h.wfile.write(body)

    def route(self, h: BaseHTTPRequestHandler, method: str):
        missing = (404, "text/plain", b"not here")
        # another site in a browser can't reach in by a name that points here
        host = (h.headers.get("Host") or "").split(":")[0]
        if host not in ("127.0.0.1", "localhost"):
            return (403, "text/plain", b"no")
        url = urlsplit(h.path)
        head, _, rel = url.path.lstrip("/").partition("/")
        if not secrets.compare_digest(head, self.token):
            return missing
        query = parse_qs(url.query)
        if method == "POST":
            return self.post(h, rel)
        if rel == "hub/theme.js":
            return 200, TYPES[".js"], self.api.theme_js().encode()
        if rel == "wallpaper":
            pic = self.api.wallpaper()
            if pic is None or not pic.is_file():
                return missing
            return 200, TYPES.get(pic.suffix.lower(), "image/jpeg"), pic.read_bytes()
        if rel == "api/settings":
            return 200, TYPES[".json"], _json(self.api.settings_info())
        path = safe_file(self.root, rel)
        if path is None:
            return missing
        body = path.read_bytes()
        if rel == "desktop/particles.html":
            screen = _int(query.get("screen", ["0"])[0])
            part = query.get("part", ["all"])[0]
            if part not in ("all", "scenery", "widgets"):
                part = "all"
            boot = self.api.boot(screen, part)
            body = boot_html(body.decode("utf-8"), boot).encode("utf-8")
        return 200, TYPES[path.suffix.lower()], body

    def post(self, h: BaseHTTPRequestHandler, rel: str):
        if "application/json" not in (h.headers.get("Content-Type") or ""):
            return 415, "text/plain", b"json only"
        size = _int(h.headers.get("Content-Length") or "0")
        if size <= 0 or size > MAX_BODY:
            return 413, "text/plain", b"too big"
        try:
            msg = json.loads(h.rfile.read(size))
        except ValueError:
            return 400, "text/plain", b"not json"
        if not isinstance(msg, dict):
            return 400, "text/plain", b"not an object"
        if rel == "api/set" and isinstance(msg.get("key"), str):
            return (
                200,
                TYPES[".json"],
                _json(self.api.set_setting(msg["key"], msg.get("value"))),
            )
        if rel == "api/do" and isinstance(msg.get("action"), str):
            return 200, TYPES[".json"], _json(self.api.action(msg["action"]))
        return 404, "text/plain", b"not here"


def _json(data) -> bytes:
    return json.dumps(data).encode("utf-8")


def _int(text: str) -> int:
    try:
        return int(text)
    except (TypeError, ValueError):
        return 0
