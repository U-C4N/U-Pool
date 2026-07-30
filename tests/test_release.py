from __future__ import annotations

import pytest

from upool import release
from upool.models import UPoolError

# A trimmed copy of the shape GitHub's releases/latest actually returns.
PAYLOAD = {
    "tag_name": "v0.6.0",
    "html_url": "https://github.com/U-C4N/U-Pool/releases/tag/v0.6.0",
    "published_at": "2026-08-01T09:00:00Z",
    "body": "Release notes.",
    "assets": [
        {"name": "SHA256SUMS.txt", "browser_download_url": "https://github.com/s.txt", "size": 90},
        {
            "name": "U-Pool-0.6.0-win64.zip",
            "browser_download_url": "https://github.com/U-C4N/U-Pool/releases/download/v0.6.0/U-Pool-0.6.0-win64.zip",
            "size": 51_200_000,
            "digest": "sha256:" + "ab" * 32,
        },
        {"name": "U-Pool-0.6.0-macos.tar.gz", "browser_download_url": "https://github.com/m", "size": 1},
    ],
}


@pytest.mark.parametrize(
    ("candidate", "current", "expected"),
    [
        ("0.5.0", "0.4.0", True),
        ("v0.5.0", "0.4.0", True),
        ("0.5.0", "v0.5.0", False),
        ("0.4.0", "0.5.0", False),
        ("0.4.10", "0.4.9", True),
        ("0.5", "0.5.0", False),
        ("1.0.0", "0.99.99", True),
        # A pre-release is older than the release it leads up to, and never wins
        # against the version already installed.
        ("0.5.0-rc1", "0.5.0", False),
        ("0.5.0", "0.5.0-rc1", True),
        ("0.5.0-rc2", "0.5.0-rc1", True),
        ("0.6.0-beta", "0.5.0", True),
        # Garbage on either side loses rather than raising.
        ("", "0.4.0", False),
        ("nightly", "0.4.0", False),
        ("0.4.0", "who knows", False),
    ],
)
def test_is_newer(candidate, current, expected):
    assert release.is_newer(candidate, current) is expected


def test_parse_version_never_raises():
    for text in ("", "v", "...", "1.2.3.4.5", "0x10", None):  # type: ignore[arg-type]
        release.parse_version(text)  # no assertion: not raising is the point


def test_pick_asset_takes_the_windows_zip():
    asset = release._pick_asset(PAYLOAD["assets"])
    assert asset is not None
    assert asset["name"] == "U-Pool-0.6.0-win64.zip"


def test_pick_asset_returns_none_for_a_source_only_release():
    assert release._pick_asset([{"name": "U-Pool-0.6.0.tar.gz"}]) is None


def test_fetch_latest_reads_the_asset_and_digest(monkeypatch):
    monkeypatch.setattr(release, "_request", lambda url, timeout: _Response(PAYLOAD))
    rel = release.fetch_latest()
    assert rel.tag == "v0.6.0"
    assert rel.version == "0.6.0"
    assert rel.has_asset
    assert rel.asset_name == "U-Pool-0.6.0-win64.zip"
    assert rel.asset_size == 51_200_000
    assert rel.asset_sha256 == "ab" * 32
    assert rel.checksums_url == "https://github.com/s.txt"
    assert rel.to_dict()["has_asset"] is True


def test_fetch_latest_survives_a_release_without_assets(monkeypatch):
    monkeypatch.setattr(release, "_request", lambda url, timeout: _Response({"tag_name": "v0.6.0"}))
    rel = release.fetch_latest()
    assert rel.has_asset is False
    assert rel.asset_sha256 == ""


def test_fetch_latest_turns_a_broken_answer_into_a_readable_error(monkeypatch):
    monkeypatch.setattr(release, "_request", lambda url, timeout: _Response("not an object"))
    with pytest.raises(UPoolError, match="expected shape"):
        release.fetch_latest()


def test_fetch_latest_needs_a_tag(monkeypatch):
    monkeypatch.setattr(release, "_request", lambda url, timeout: _Response({"body": "hi"}))
    with pytest.raises(UPoolError, match="no tag"):
        release.fetch_latest()


@pytest.mark.parametrize(
    ("url", "allowed"),
    [
        ("https://github.com/U-C4N/U-Pool/releases/download/v1/a.zip", True),
        ("https://objects.githubusercontent.com/blob/a.zip", True),
        ("https://githubusercontent.com/a.zip", True),
        # http, and hosts that merely end in the right letters, are not GitHub.
        ("http://github.com/a.zip", False),
        ("https://notgithub.com/a.zip", False),
        ("https://github.com.evil.test/a.zip", False),
        ("file:///C:/a.zip", False),
        ("", False),
    ],
)
def test_is_allowed_url(url, allowed):
    assert release.is_allowed_url(url) is allowed


class _Response:
    """Stand-in for the context manager ``urlopen`` returns."""

    def __init__(self, payload) -> None:
        import json

        self._body = json.dumps(payload).encode("utf-8")

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> "_Response":
        return self

    def __exit__(self, *_exc) -> None:
        return None
