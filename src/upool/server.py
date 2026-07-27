"""Loopback static file server for the built UI.

The Next.js export references its assets with absolute paths (``/_next/...``),
which ``file://`` cannot resolve. A threaded stdlib server on 127.0.0.1 costs a
couple of milliseconds at startup and makes the bundle work unmodified.
"""

from __future__ import annotations

import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


class _Handler(SimpleHTTPRequestHandler):
    def end_headers(self) -> None:
        # The bundle changes on every rebuild; caching it only creates confusion.
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def send_response(self, code, message=None):  # noqa: D102 - stdlib override
        super().send_response(code, message)

    def log_message(self, *_args) -> None:
        """Silence the default stderr access log."""

    def do_GET(self) -> None:  # noqa: N802 - stdlib override
        path = self.translate_path(self.path)
        # Single-page routes have no file on disk; hand back the shell.
        if not Path(path).exists() and "." not in Path(path).name:
            self.path = "/index.html"
        super().do_GET()


class UiServer:
    def __init__(self, root: Path) -> None:
        self.root = root
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> str:
        if not self.root.exists():
            raise FileNotFoundError(
                f"UI bundle not found at {self.root}. Run 'npm run build' inside ui/ first."
            )
        handler = partial(_Handler, directory=str(self.root))
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        host, port = self._server.server_address[:2]
        return f"http://{host}:{port}/"

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None
