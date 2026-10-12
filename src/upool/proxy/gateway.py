"""Authenticated loopback transport for the account pool."""

from __future__ import annotations

import hmac
import json
import select
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import urlsplit

from .models import ProxyError
from .protocol import validate_request

MAX_BODY_BYTES = 4 * 1024 * 1024


class _Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False


class GatewayServer:
    def __init__(self, service: Any) -> None:
        self.service = service
        self._server: _Server | None = None
        self._thread: threading.Thread | None = None
        self._lock = threading.RLock()
        self._active: dict[int, tuple[Any, socket.socket]] = {}

    @property
    def running(self) -> bool:
        return self._server is not None

    def start(self, port: int = 8318) -> str:
        if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
            raise ProxyError("Port must be between 1 and 65535.", 400, "invalid_port")
        with self._lock:
            if self._server is not None:
                if self._server.server_port != port:
                    raise ProxyError("Stop the gateway before changing its port.", 409, "gateway_running")
                return f"http://127.0.0.1:{port}/v1"
            handler = self._handler()
            try:
                server = _Server(("127.0.0.1", port), handler)
            except OSError as exc:
                raise ProxyError("The configured API port is unavailable.", 409, "port_unavailable") from exc
            self._server = server
            self._thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.1}, daemon=True)
            self._thread.start()
        return f"http://127.0.0.1:{port}/v1"

    def stop(self) -> None:
        with self._lock:
            server, thread = self._server, self._thread
            self._server = self._thread = None
            active = list(self._active.values())
        for cleanup, connection in active:
            cleanup()
            try:
                connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        if server is not None:
            server.shutdown()
            server.server_close()
        if thread is not None:
            thread.join(timeout=5)

    def _handler(self) -> type[BaseHTTPRequestHandler]:
        gateway = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"
            server_version = "U-Pool"
            sys_version = ""

            def log_message(self, *_args: Any) -> None:
                pass

            def setup(self) -> None:
                super().setup()
                self.connection.settimeout(30)

            def _json(self, status: int, payload: dict) -> None:
                body = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("Connection", "close")
                self.end_headers()
                self.close_connection = True
                self.wfile.write(body)

            def _error(self, error: ProxyError) -> None:
                self._discard_request_body()
                self._json(error.status, {"error": {"message": str(error), "type": "invalid_request_error" if error.status < 500 else "server_error", "code": error.code}})

            def _discard_request_body(self) -> None:
                # Closing a Windows socket with queued body bytes can reset the
                # connection before the caller receives its authentication error.
                # Discard bytes without parsing them; cap both work and elapsed time.
                if getattr(self, "_body_consumed", False):
                    return
                lengths = self.headers.get_all("Content-Length", [])
                if not lengths and not self.headers.get("Transfer-Encoding"):
                    return
                remaining = MAX_BODY_BYTES
                if len(lengths) == 1 and lengths[0].isdigit() and len(lengths[0]) <= 12:
                    remaining = min(int(lengths[0]), MAX_BODY_BYTES)
                deadline = time.monotonic() + 0.2
                original_timeout = self.connection.gettimeout()
                try:
                    while remaining:
                        wait = deadline - time.monotonic()
                        if wait <= 0:
                            break
                        self.connection.settimeout(wait)
                        block = self.rfile.read1(min(remaining, 64 * 1024))
                        if not block:
                            break
                        remaining -= len(block)
                except (OSError, ValueError):
                    pass
                finally:
                    self.connection.settimeout(original_timeout)
                    self._body_consumed = True

            def _authorize(self) -> None:
                host = urlsplit("//" + self.headers.get("Host", "")).hostname
                if host not in {"127.0.0.1", "localhost"}:
                    raise ProxyError("Only loopback hosts are accepted.", 403, "invalid_host")
                token = self.headers.get("Authorization", "")
                if not gateway.service.client_key or not hmac.compare_digest(token.encode(), ("Bearer " + gateway.service.client_key).encode()):
                    raise ProxyError("Invalid API key.", 401, "invalid_api_key")

            def do_GET(self) -> None:
                try:
                    self._authorize()
                    if self.path != "/v1/models":
                        raise ProxyError("Unknown endpoint.", 404, "not_found")
                    self._json(200, {"object": "list", "data": gateway.service.list_models()})
                except ProxyError as exc:
                    self._error(exc)
                except (BrokenPipeError, ConnectionResetError, TimeoutError):
                    pass
                except Exception:
                    self._error(ProxyError("The gateway could not complete the request."))

            def do_POST(self) -> None:
                self._body_consumed = False
                response = None
                iterator = None
                cleanup = None
                cancelled = threading.Event()
                finished = threading.Event()
                emitted = False
                endpoint = ""
                try:
                    self._authorize()
                    if self.path not in {"/v1/chat/completions", "/v1/responses"}:
                        raise ProxyError("Unknown endpoint.", 404, "not_found")
                    endpoint = self.path[4:]
                    if self.headers.get("Transfer-Encoding"):
                        raise ProxyError("Transfer-Encoding is unsupported; send Content-Length.", 400, "invalid_request")
                    lengths = self.headers.get_all("Content-Length", [])
                    if len(lengths) != 1 or not lengths[0].isdigit():
                        raise ProxyError("A valid Content-Length is required.", 411, "invalid_request")
                    length = int(lengths[0])
                    if not 0 < length <= MAX_BODY_BYTES:
                        raise ProxyError("Request body exceeds the 4 MiB limit.", 413, "body_too_large")
                    if self.headers.get_content_type() != "application/json":
                        raise ProxyError("Content-Type must be application/json.", 415, "invalid_request")
                    body = self.rfile.read(length)
                    self._body_consumed = True
                    if len(body) != length:
                        raise ProxyError("Incomplete request body.", 400, "invalid_request")
                    try:
                        payload = json.loads(body, parse_constant=_invalid_constant)
                    except (ValueError, UnicodeError, RecursionError) as exc:
                        raise ProxyError("Request body must be valid JSON.", 400, "invalid_request") from exc
                    payload = validate_request(endpoint, payload)
                    cleanup_lock = threading.Lock()
                    cleaned = False

                    def close_upstream() -> None:
                        nonlocal cleaned
                        with cleanup_lock:
                            if cleaned:
                                return
                            cleaned = True
                        cancelled.set()
                        if response is not None:
                            _close_response(response)

                    cleanup = close_upstream
                    with gateway._lock:
                        gateway._active[id(self)] = (cleanup, self.connection)
                    # A browser can stop reading while the worker is blocked on its
                    # next token. Detect EOF independently so cancellation reaches it.
                    def watch_disconnect() -> None:
                        while not finished.wait(0.1):
                            try:
                                readable, _, _ = select.select([self.connection], [], [], 0)
                                if readable and not self.connection.recv(1, socket.MSG_PEEK):
                                    cleanup()
                                    return
                            except OSError:
                                cleanup()
                                return

                    threading.Thread(target=watch_disconnect, daemon=True).start()
                    cancellable = getattr(gateway.service, "complete_cancellable", None)
                    response = cancellable(endpoint, payload, cancelled) if callable(cancellable) else gateway.service.complete(endpoint, payload)
                    if cancelled.is_set():
                        _close_response(response)
                        raise ProxyError("The client cancelled the request.", 499, "request_cancelled")
                    if isinstance(response.body, bytes):
                        first = response.body
                    else:
                        iterator = iter(response.body)
                        first = next(iterator, b"")
                    self.send_response(response.status)
                    # Only public response metadata crosses the worker boundary.
                    headers = {key.lower(): value for key, value in response.headers.items()}
                    for key in ("content-type", "retry-after", "x-request-id"):
                        value = headers.get(key)
                        if value and "\r" not in value and "\n" not in value:
                            self.send_header(key, value)
                    if "content-type" not in headers:
                        self.send_header("Content-Type", "text/event-stream" if payload.get("stream") else "application/json")
                    self.send_header("Cache-Control", "no-store")
                    self.send_header("Connection", "close")
                    if iterator is None:
                        self.send_header("Content-Length", str(len(first)))
                    else:
                        self.send_header("X-Accel-Buffering", "no")
                    self.end_headers()
                    emitted = True
                    self.close_connection = True
                    self.wfile.write(first)
                    self.wfile.flush()
                    if iterator is not None:
                        for chunk in iterator:
                            self.wfile.write(chunk)
                            self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, TimeoutError):
                    pass
                except Exception as exc:
                    error = exc if isinstance(exc, ProxyError) else ProxyError("The gateway could not complete the request.")
                    try:
                        if not emitted:
                            self._error(error)
                        elif iterator is not None:
                            data = {"error": {"code": error.code, "message": str(error), "type": "server_error"}}
                            prefix = ""
                            if endpoint == "responses":
                                data = {"type": "error", "code": error.code, "message": str(error)}
                                prefix = "event: error\n"
                            self.wfile.write((prefix + "data: " + json.dumps(data) + "\n\n").encode())
                            self.wfile.flush()
                    except (OSError, ValueError):
                        pass
                finally:
                    finished.set()
                    if cleanup is not None:
                        cleanup()
                    if iterator is not None and hasattr(iterator, "close"):
                        try:
                            iterator.close()
                        except Exception:
                            pass
                    with gateway._lock:
                        gateway._active.pop(id(self), None)

        return Handler


def _invalid_constant(value: str) -> None:
    raise ValueError(value)


def _close_response(response: Any) -> None:
    callback = getattr(response, "close", None)
    if callable(callback):
        try:
            callback()
        except Exception:
            pass
