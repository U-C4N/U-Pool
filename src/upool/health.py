"""Endpoint reachability and latency probe.

Deliberately stdlib-only: adding an HTTP client would cost more startup time
than this whole feature is worth. Probes are plain GETs against a cheap
endpoint - never a completion request, so testing a provider costs no tokens.
"""

from __future__ import annotations

import socket
import ssl
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from typing import Any

from . import __version__, adapters
from .models import Provider

DEFAULT_TIMEOUT = 10.0
USER_AGENT = f"U-Pool/{__version__} (+health-check)"

STATUS_OK = "ok"
STATUS_AUTH = "auth"
STATUS_WARN = "warn"
STATUS_ERROR = "error"
STATUS_UNREACHABLE = "unreachable"
STATUS_SKIPPED = "skipped"


@dataclass
class HealthResult:
    provider_id: str
    status: str
    latency_ms: int | None
    http_status: int | None
    message: str

    @property
    def reachable(self) -> bool:
        return self.status in (STATUS_OK, STATUS_AUTH, STATUS_WARN)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["reachable"] = self.reachable
        return data


def _classify(code: int, latency_ms: int) -> tuple[str, str]:
    if 200 <= code < 300:
        return STATUS_OK, f"Reachable ({latency_ms}ms)"
    if code in (401, 403):
        return STATUS_AUTH, f"Reachable, key rejected ({code})"
    if code == 404:
        return STATUS_WARN, f"Reachable, no /models endpoint ({latency_ms}ms)"
    if 400 <= code < 500:
        return STATUS_WARN, f"Reachable, HTTP {code} ({latency_ms}ms)"
    return STATUS_ERROR, f"Server error, HTTP {code}"


def check(provider: Provider, timeout: float = DEFAULT_TIMEOUT) -> HealthResult:
    target = adapters.get(provider.app).health_target(provider)
    if target is None:
        return HealthResult(
            provider_id=provider.id,
            status=STATUS_SKIPPED,
            latency_ms=None,
            http_status=None,
            message="Official login - nothing to probe.",
        )

    url, headers = target
    request = urllib.request.Request(url, method="GET")
    request.add_header("user-agent", USER_AGENT)
    for key, value in headers.items():
        request.add_header(key, value)

    started = time.perf_counter()
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            latency_ms = int((time.perf_counter() - started) * 1000)
            status, message = _classify(response.status, latency_ms)
            return HealthResult(provider.id, status, latency_ms, response.status, message)
    except urllib.error.HTTPError as exc:
        # An HTTP error still proves the endpoint answered.
        latency_ms = int((time.perf_counter() - started) * 1000)
        status, message = _classify(exc.code, latency_ms)
        return HealthResult(provider.id, status, latency_ms, exc.code, message)
    except urllib.error.URLError as exc:
        reason = exc.reason
        if isinstance(reason, ssl.SSLError):
            message = f"TLS failed: {reason.reason or reason}"
        elif isinstance(reason, socket.timeout):
            message = f"Timed out after {timeout:.0f}s"
        else:
            message = f"Unreachable: {reason}"
        return HealthResult(provider.id, STATUS_UNREACHABLE, None, None, message)
    except (socket.timeout, TimeoutError):
        return HealthResult(
            provider.id, STATUS_UNREACHABLE, None, None, f"Timed out after {timeout:.0f}s"
        )
    except Exception as exc:  # never let a probe crash the UI
        return HealthResult(provider.id, STATUS_ERROR, None, None, f"{type(exc).__name__}: {exc}")


def check_many(providers: list[Provider], timeout: float = DEFAULT_TIMEOUT) -> list[HealthResult]:
    if not providers:
        return []
    workers = min(8, len(providers))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        return list(pool.map(lambda p: check(p, timeout), providers))
