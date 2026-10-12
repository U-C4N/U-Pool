"""Transport objects shared without importing a running service."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Iterable

from ..models import UPoolError


class ProxyError(UPoolError):
    def __init__(self, message: str, status: int = 502, code: str = "upstream_error") -> None:
        super().__init__(message)
        self.status = status
        self.code = code


@dataclass
class UpstreamResponse:
    status: int
    headers: dict[str, str]
    body: bytes | Iterable[bytes]
    close: Callable[[], None] | None = None
