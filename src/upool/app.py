"""Desktop shell.

pywebview hands the OS webview (WebView2 on Windows) an HTML document and a
Python object. There is no HTTP layer between the UI and the backend - the JS
calls Python methods directly - so a provider switch is a function call, not a
request.
"""

from __future__ import annotations

import os
import sys

from . import paths
from .api import APP_VERSION, Api
from .server import UiServer

WINDOW_TITLE = "U-Pool"
DEFAULT_SIZE = (1180, 780)
MIN_SIZE = (940, 620)


def _truthy(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes", "on")


def run(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    debug = _truthy("UPOOL_DEBUG") or "--debug" in argv

    # Importing pywebview costs real time; keep it out of the module import path
    # so `python -m upool --version` and the test suite stay instant.
    import webview

    server: UiServer | None = None
    dev_url = os.environ.get("UPOOL_DEV_URL", "").strip()
    if dev_url:
        url = dev_url
    else:
        server = UiServer(paths.ui_dist_dir())
        url = server.start()

    api = Api()
    window = webview.create_window(
        WINDOW_TITLE,
        url=url,
        js_api=api,
        width=DEFAULT_SIZE[0],
        height=DEFAULT_SIZE[1],
        min_size=MIN_SIZE,
        background_color="#F7F7F8",
        text_select=False,
    )
    api.attach(window)

    try:
        webview.start(debug=debug, private_mode=False)
    finally:
        if server is not None:
            server.stop()
    return 0


def main() -> int:
    if "--version" in sys.argv[1:]:
        print(f"U-Pool {APP_VERSION}")
        return 0
    return run()
