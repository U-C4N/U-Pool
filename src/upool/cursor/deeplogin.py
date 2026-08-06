"""Turning a browser cookie into a desktop session token.

The one reverse-engineered surface in the codebase, so it lives alone. A ``web``
cookie authenticates cursor.com but Cursor rejects it in ``state.vscdb`` and signs
itself out - only a ``session`` token signs the desktop in. Cursor's own
deep-login flow mints one, and this reproduces it, measured end to end on
2026-08-06:

    GET  cursor.com/loginDeepControl?challenge&uuid&mode=login   (cookie authorises)
            -> 307, stays on cursor.com, jar gains cursor-web-target-synced-user
    POST cursor.com/api/auth/loginDeepCallbackControl {uuid, challenge}
            -> 200 "OK"   (the jar cookie rides along)
    GET  api2.cursor.sh/auth/poll?uuid&verifier   -> {accessToken (session), authId}

The GET is not decorative: its redirect seeds the jar cookie the confirm POST
needs. One ``CookieJar`` spans the GET and the POST; the poll is a different host
and carries none.

Stdlib ``urllib`` only, following :mod:`upool.cursor.api`. No new dependency, and
- unlike a webview - no httpOnly cookie injection: the jar holds what the redirect
set, which is all the confirm needs.
"""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from http.cookiejar import CookieJar
from uuid import uuid4

from ..health import USER_AGENT
from ..models import UPoolError
from .models import COOKIE_NAME, COOKIE_SEPARATOR, KIND_SESSION, token_kind

DEEP_URL = "https://cursor.com/loginDeepControl"
CONFIRM_URL = "https://cursor.com/api/auth/loginDeepCallbackControl"
POLL_URL = "https://api2.cursor.sh/auth/poll"

DEFAULT_TIMEOUT = 20.0
# The confirm's redirect can take a moment to register before the poll sees the
# token; a browser polls the same way. Five tries at two seconds matched the
# measured latency with room to spare.
DEFAULT_ATTEMPTS = 5
DEFAULT_INTERVAL = 2.0


@dataclass(frozen=True)
class DeepLogin:
    """A minted session, and the account it proved to belong to."""

    user_id: str
    token: str


def _pkce_pair() -> tuple[str, str]:
    """A PKCE ``(verifier, challenge)``, exactly as the Cursor client derives it.

    The verifier is the secret redeemed on the poll URL; the challenge is its
    SHA-256, base64url without padding, and is all the server sees until then.
    """
    verifier = secrets.token_urlsafe(43)
    digest = hashlib.sha256(verifier.encode("utf-8")).digest()
    challenge = base64.urlsafe_b64encode(digest).decode("utf-8").rstrip("=")
    return verifier, challenge


def _open(request: urllib.request.Request, timeout: float):
    """One seam the whole module goes through, jar included.

    A module-level function rather than ``opener.open`` inline so a test can
    replace the network at one point without reaching into opener internals. The
    jar is built per call because an exchange is one short-lived handshake, not a
    session worth keeping.
    """
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(CookieJar()))
    return opener.open(request, timeout=timeout)


def exchange(
    user_id: str,
    web_token: str,
    *,
    timeout: float = DEFAULT_TIMEOUT,
    attempts: int = DEFAULT_ATTEMPTS,
    interval: float = DEFAULT_INTERVAL,
) -> DeepLogin:
    """Mint a session token for ``user_id`` from its ``web_token``.

    Raises :class:`UPoolError` on every failure and writes nothing - the caller
    decides what a failure means, and in :func:`upool.cursor.switch.use` it means
    the editor is never touched. The identity check at the end is the one that
    matters most: a switcher that silently minted a token for the wrong account
    would sign the user into someone else.
    """
    if not user_id or not web_token:
        raise UPoolError("This account has no browser cookie to sign in with - paste it again.")

    verifier, challenge = _pkce_pair()
    uuid = str(uuid4())
    cookie = f"{COOKIE_NAME}={user_id}{COOKIE_SEPARATOR}{web_token}"

    # Build the jar cookie the confirm needs. Its body is discarded; the redirect
    # it follows is the point.
    jar_request = urllib.request.Request(
        f"{DEEP_URL}?challenge={challenge}&uuid={uuid}&mode=login", method="GET"
    )
    jar_request.add_header("user-agent", USER_AGENT)
    jar_request.add_header("cookie", cookie)
    jar_request.add_header("accept", "text/html,application/xhtml+xml,*/*;q=0.8")
    try:
        with _open(jar_request, timeout):
            pass
    except urllib.error.HTTPError:
        # A non-2xx here is not fatal on its own - the confirm is what authorises -
        # so it is swallowed and the confirm gets its own say.
        pass
    except OSError as exc:
        raise UPoolError(f"Could not reach cursor.com to start sign-in: {exc}") from exc

    # The confirm. The site's own call is same-origin, so Origin/Referer are set -
    # they are the likely CSRF check.
    body = json.dumps({"uuid": uuid, "challenge": challenge}).encode("utf-8")
    confirm = urllib.request.Request(CONFIRM_URL, data=body, method="POST")
    confirm.add_header("user-agent", USER_AGENT)
    confirm.add_header("content-type", "application/json")
    confirm.add_header("cookie", cookie)
    confirm.add_header("origin", "https://cursor.com")
    confirm.add_header("referer", jar_request.full_url)
    try:
        with _open(confirm, timeout) as response:
            status = getattr(response, "status", 200)
            text = response.read(400).decode("utf-8", "replace").strip()
    except urllib.error.HTTPError as exc:
        detail = exc.read(400).decode("utf-8", "replace").strip()
        raise UPoolError(detail or f"Cursor refused the sign-in (HTTP {exc.code}).")
    except OSError as exc:
        raise UPoolError(f"Could not reach cursor.com to confirm sign-in: {exc}") from exc
    if status >= 300:
        raise UPoolError(text or f"Cursor refused the sign-in (HTTP {status}).")

    # Poll for the minted token. A browser does the same; the token appears once
    # the confirm's redirect has registered.
    for attempt in range(attempts):
        minted = _poll_once(uuid, verifier, timeout)
        if minted is not None:
            return _verify_identity(user_id, minted)
        if attempt < attempts - 1:
            time.sleep(interval)
    raise UPoolError("Cursor did not return a session token in time - try again.")


def _poll_once(uuid: str, verifier: str, timeout: float) -> dict | None:
    """One poll. ``None`` means "not ready yet", a raise means a hard failure.

    A malformed body is a hard failure, not a retry: the confirm has already
    succeeded, so JSON that will not parse is Cursor changing shape, which a retry
    cannot fix and which the user needs told.
    """
    request = urllib.request.Request(
        f"{POLL_URL}?uuid={uuid}&verifier={verifier}", method="GET"
    )
    request.add_header("user-agent", USER_AGENT)
    request.add_header("accept", "*/*")
    request.add_header("referer", "https://www.cursor.com/")
    try:
        with _open(request, timeout) as response:
            payload = response.read(8000)
    except urllib.error.HTTPError as exc:
        raise UPoolError(f"Cursor's sign-in poll answered HTTP {exc.code}.")
    except OSError as exc:
        raise UPoolError(f"Could not reach cursor.com to finish sign-in: {exc}") from exc

    if not payload:
        return None
    try:
        data = json.loads(payload)
    except ValueError as exc:
        raise UPoolError("Cursor's sign-in poll did not answer with JSON.") from exc
    if isinstance(data, dict) and data.get("accessToken"):
        return data
    return None


def _verify_identity(expected_user_id: str, payload: dict) -> DeepLogin:
    """The minted token, only if it is for the account we asked about.

    ``authId`` is ``provider|user_id``; the half after the pipe is the identity,
    the same shape :func:`upool.cursor.switch.live_account` reads. A mismatch is
    refused rather than returned - minting a token for the wrong account is the one
    failure a switcher must never paper over.
    """
    token = str(payload.get("accessToken") or "")
    auth_id = str(payload.get("authId") or "")
    minted_user_id = auth_id.split("|", 1)[-1] if "|" in auth_id else auth_id
    if not token:
        raise UPoolError("Cursor's sign-in poll returned no token.")
    if minted_user_id and minted_user_id != expected_user_id:
        raise UPoolError(
            "Cursor signed in a different account than the one asked for - "
            "not switching. Paste this account's cookie again."
        )
    if token_kind(token) != KIND_SESSION:
        # Measured every time in the probe, so a non-session token here means the
        # flow changed; writing it would sign the editor out, which is the whole
        # thing this feature exists to stop.
        raise UPoolError("Cursor returned a token that cannot sign the desktop app in.")
    return DeepLogin(user_id=expected_user_id, token=token)
