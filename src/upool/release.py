"""What the GitHub releases page is offering.

Read-only and stdlib-only, in the same spirit as :mod:`upool.health`: one GET
against the releases API, every failure turned into a readable
:class:`~upool.models.UPoolError`. Nothing here touches the disk or the running
install - that is :mod:`upool.updater`'s job.
"""

from __future__ import annotations

import json
import re
import socket
import ssl
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from typing import Any

from . import __version__
from .models import UPoolError

GITHUB_REPO = "U-C4N/U-Pool"
LATEST_URL = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
RELEASES_PAGE = f"https://github.com/{GITHUB_REPO}/releases/latest"
# The Windows bundle produced by ``scripts/build.py``, e.g. U-Pool-0.5.0-win64.zip.
ASSET_RE = re.compile(r"^U-Pool-.*win.*\.zip$", re.I)
CHECKSUM_ASSET = "SHA256SUMS.txt"

USER_AGENT = f"U-Pool/{__version__} (+self-update)"
DEFAULT_TIMEOUT = 8.0

# Hosts a release download is allowed to come from. GitHub redirects the asset
# URL to its CDN, so both have to be accepted - and nothing else.
ALLOWED_HOSTS = ("github.com", "githubusercontent.com")

_NUMBER = re.compile(r"^(\d+)(.*)$")


@dataclass(frozen=True)
class Release:
    tag: str
    version: str
    notes: str
    html_url: str
    published_at: str
    asset_name: str = ""
    asset_url: str = ""
    asset_size: int = 0
    # Empty when GitHub published no digest for the asset; the caller then has to
    # say what it could and could not verify rather than implying a signature.
    asset_sha256: str = ""
    checksums_url: str = ""

    @property
    def has_asset(self) -> bool:
        return bool(self.asset_url)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self) | {"has_asset": self.has_asset}


def parse_version(text: str) -> tuple[int, ...]:
    """Sortable key for a version string. Never raises.

    A trailing non-numeric suffix marks a pre-release and sorts *below* the bare
    version, with its own number breaking the tie: ``0.5.0-rc1`` < ``0.5.0-rc2``
    < ``0.5.0``. Anything unparsable becomes ``(-1,)``, which loses every
    comparison.
    """
    cleaned = str(text).strip().lstrip("vV")
    if not cleaned:
        return (-1,)
    head, _, tail = cleaned.partition("-")
    parts: list[int] = []
    for chunk in head.split("."):
        match = _NUMBER.match(chunk)
        if match is None:
            return (-1,)
        parts.append(int(match.group(1)))
        if match.group(2):
            tail = match.group(2) + tail
            break
    if not parts:
        return (-1,)
    # Pad so 0.5 and 0.5.0 compare equal, then append the pre-release marker and
    # its ordinal - rc2 has to beat rc1 without either beating the release.
    while len(parts) < 3:
        parts.append(0)
    if tail:
        digits = re.search(r"\d+", tail)
        parts.extend((-1, int(digits.group()) if digits else 0))
    else:
        parts.extend((0, 0))
    return tuple(parts)


def is_newer(candidate: str, current: str) -> bool:
    theirs = parse_version(candidate)
    ours = parse_version(current)
    if theirs == (-1,) or ours == (-1,):
        return False
    return theirs > ours


def is_allowed_url(url: str) -> bool:
    """True for an https URL on a host GitHub actually serves releases from."""
    try:
        parsed = urllib.parse.urlparse(url)
    except Exception:  # noqa: BLE001 - a URL we cannot even parse is not allowed
        return False
    if parsed.scheme != "https" or not parsed.hostname:
        return False
    host = parsed.hostname.lower()
    return any(host == allowed or host.endswith("." + allowed) for allowed in ALLOWED_HOSTS)


def _pick_asset(assets: list[dict[str, Any]]) -> dict[str, Any] | None:
    for asset in assets:
        if ASSET_RE.match(str(asset.get("name") or "")):
            return asset
    return None


def _digest(asset: dict[str, Any]) -> str:
    raw = str(asset.get("digest") or "")
    prefix = "sha256:"
    return raw[len(prefix) :].lower() if raw.lower().startswith(prefix) else ""


def _request(url: str, timeout: float) -> Any:
    request = urllib.request.Request(url, method="GET")
    request.add_header("user-agent", USER_AGENT)
    request.add_header("accept", "application/vnd.github+json")
    request.add_header("x-github-api-version", "2022-11-28")
    return urllib.request.urlopen(request, timeout=timeout)


def fetch_latest(timeout: float = DEFAULT_TIMEOUT) -> Release:
    """The newest published release, whether or not it is newer than us."""
    try:
        with _request(LATEST_URL, timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise UPoolError("There is no published release to compare against yet.") from exc
        if exc.code in (403, 429):
            remaining = exc.headers.get("x-ratelimit-remaining") if exc.headers else None
            if remaining == "0":
                raise UPoolError(
                    "GitHub's rate limit for this network is used up. Try again later."
                ) from exc
        raise UPoolError(f"GitHub answered HTTP {exc.code} when asked for the latest release.") from exc
    except urllib.error.URLError as exc:
        reason = exc.reason
        if isinstance(reason, ssl.SSLError):
            raise UPoolError(f"TLS failed talking to GitHub: {reason.reason or reason}") from exc
        if isinstance(reason, socket.timeout):
            raise UPoolError(f"GitHub did not answer within {timeout:.0f}s.") from exc
        raise UPoolError(f"Could not reach GitHub: {reason}") from exc
    except (socket.timeout, TimeoutError) as exc:
        raise UPoolError(f"GitHub did not answer within {timeout:.0f}s.") from exc
    except (ValueError, UnicodeDecodeError) as exc:
        raise UPoolError("GitHub's answer was not valid JSON.") from exc
    except Exception as exc:  # noqa: BLE001 - a check must never crash the UI
        raise UPoolError(f"Update check failed: {type(exc).__name__}: {exc}") from exc

    if not isinstance(payload, dict):
        raise UPoolError("GitHub's answer was not in the expected shape.")

    tag = str(payload.get("tag_name") or "")
    if not tag:
        raise UPoolError("The latest release has no tag, so there is nothing to compare.")

    assets = [a for a in (payload.get("assets") or []) if isinstance(a, dict)]
    asset = _pick_asset(assets) or {}
    checksums = next(
        (a for a in assets if str(a.get("name") or "").lower() == CHECKSUM_ASSET.lower()),
        {},
    )
    return Release(
        tag=tag,
        version=tag.lstrip("vV"),
        notes=str(payload.get("body") or "").strip(),
        html_url=str(payload.get("html_url") or RELEASES_PAGE),
        published_at=str(payload.get("published_at") or ""),
        asset_name=str(asset.get("name") or ""),
        asset_url=str(asset.get("browser_download_url") or ""),
        asset_size=int(asset.get("size") or 0),
        asset_sha256=_digest(asset),
        checksums_url=str(checksums.get("browser_download_url") or ""),
    )
