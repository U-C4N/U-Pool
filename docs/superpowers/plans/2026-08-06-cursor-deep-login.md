# Cursor deep-login exchange — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn a pasted Cursor browser cookie (a `web` token) into a desktop `session` token on `Use`, via Cursor's own deep-login flow, so every pooled account can sign the editor in.

**Architecture:** A new stdlib-only `upool/cursor/deeplogin.py` performs the three-call PKCE exchange (GET seeds a cookie jar → POST confirm → poll mints a session token). `CursorAccount` gains a `web_token` field that keeps the source cookie so an expired session can be re-minted silently. `switch.use()` converts before it touches the editor, so a network failure leaves Cursor running and nothing written.

**Tech Stack:** Python 3.11+, stdlib `urllib.request` + `http.cookiejar` (no new dependency), pytest. TypeScript/React for the one card change.

## Global Constraints

- Python targets 3.11+ with `from __future__ import annotations`. — every new `.py` file.
- No new dependencies. HTTP is stdlib `urllib`, following `upool/cursor/api.py` and `upool/health.py`.
- **Never run a write path against the real home.** Verify ad-hoc code through `python scripts/sandbox.py`, never a bare `python -c`. Tests run `python -m pytest --basetemp=<writable dir> -q`.
- The autouse `sandbox` fixture in `tests/conftest.py` already redirects every write target. No new write target is introduced by this plan (`cursor.json` is already redirected via `UPOOL_FAKE_HOME`).
- Docstrings explain *why*, not *what*; comments are sparse full sentences; prose uses ` - ` as an aside separator. Match the surrounding files.
- A credential never enters a list response: `summary()` must not carry `web_token`, only the boolean `has_web_token`.
- Measured protocol constants (verbatim from the 2026-08-06 probe): confirm endpoint `https://cursor.com/api/auth/loginDeepCallbackControl`; deep URL `https://cursor.com/loginDeepControl?challenge={c}&uuid={u}&mode=login`; poll `https://api2.cursor.sh/auth/poll?uuid={u}&verifier={v}`; `verifier = secrets.token_urlsafe(43)`, `challenge = base64.urlsafe_b64encode(sha256(verifier)).rstrip("=")`; poll returns `{"accessToken": <jwt>, "authId": "<provider>|<user_id>"}`.

---

## File Structure

- **Create** `src/upool/cursor/deeplogin.py` — the exchange. Imports only stdlib plus `models` constants and `UPoolError`. Never imports `CursorAccount`.
- **Modify** `src/upool/cursor/models.py` — add `web_token` field; add `cookie_header_for(user_id, token)` and route `cookie_header` through it; `_TEXT_FIELDS`, `redacted()`, `summary()`.
- **Modify** `src/upool/cursor/store.py` — `upsert` banks a pasted `web` token into `web_token`; new `upgrade_token`; `FACT_FIELDS` unchanged (a refresh never learns `web_token`).
- **Modify** `src/upool/cursor/switch.py` — add `_ensure_session_token`; delete `_refuse_unswitchable`; rewire `use()`.
- **Modify** `ui/lib/types.ts` — `CursorAccountSummary.has_web_token: boolean`.
- **Modify** `ui/lib/mock.ts` — provide `has_web_token` on mock accounts.
- **Modify** `ui/components/CursorCard.tsx` — rewrite the `blocked` ladder.
- **Create** `tests/test_cursor_deeplogin.py` — the module's tests.
- **Modify** `tests/test_cursor_store.py`, `tests/test_cursor_switch.py` — field round-trip, upgrade, conversion, ordering.

---

### Task 1: `web_token` field on `CursorAccount`

**Files:**
- Modify: `src/upool/cursor/models.py` (field ~line 99-125; `_TEXT_FIELDS` line 80-90; `redacted` line 130-134; `summary` line 136-155; `cookie_header` line 207-218)
- Test: `tests/test_cursor_store.py`

**Interfaces:**
- Produces: `CursorAccount.web_token: str` (default `""`); `models.cookie_header_for(user_id: str, token: str) -> str`; `summary()` gains key `has_web_token: bool` and never contains `web_token`; `redacted()` masks `web_token`.

- [ ] **Step 1: Write the failing tests**

In `tests/test_cursor_store.py`, add (match the file's existing import of `CursorAccount`):

```python
def test_web_token_round_trips_through_the_document():
    account = CursorAccount(user_id="user_1", token="sess", web_token="web-cookie")
    assert CursorAccount.from_dict(account.to_dict()).web_token == "web-cookie"


def test_the_summary_hides_the_web_token_but_says_it_exists():
    account = CursorAccount(user_id="user_1", token="sess", web_token="web-cookie")
    summary = account.summary(active=False)
    assert "web_token" not in summary
    assert summary["has_web_token"] is True
    assert CursorAccount(user_id="u", token="t").summary(active=False)["has_web_token"] is False


def test_the_redacted_record_masks_the_web_token():
    account = CursorAccount(user_id="user_1", token="sess", web_token="web-cookie-secret")
    assert "web-cookie-secret" not in repr(account.redacted())
```

- [ ] **Step 2: Run to verify they fail**

Run: `python -m pytest tests/test_cursor_store.py -k "web_token or hides_the_web" --basetemp=.pytest-tmp -q`
Expected: FAIL — `TypeError: unexpected keyword argument 'web_token'`.

- [ ] **Step 3: Add the field**

In `models.py`, after the `token: str = ""` line (line 102) add:

```python
    # The browser cookie this account was pasted as, kept when `token` is upgraded
    # to a session token so the session can be re-minted when it dies ~60 days on.
    # A `web` token pasted straight in lives here too, banked by CursorStore.upsert.
    web_token: str = ""
```

- [ ] **Step 4: Teach the field's helpers about it**

Add `"web_token"` to `_TEXT_FIELDS` (after `"token",`, line 85):

```python
    "token",
    "web_token",
```

In `redacted()` (line 130-134), mask it alongside `token`:

```python
    def redacted(self) -> dict[str, Any]:
        """Same record with both cookies masked, for logs and error messages."""
        data = self.to_dict()
        data["token"] = mask_secret(self.token)
        data["web_token"] = mask_secret(self.web_token)
        return data
```

In `summary()` (line 136-155), pop it and add the boolean, next to the `token` pop:

```python
        data.pop("token", None)
        # A second credential, handled exactly as `token` is: never sent, its
        # presence surfaced as a boolean the card uses to decide whether an
        # expired row can still be re-minted.
        web_token = data.pop("web_token", None)
        data["has_web_token"] = bool(web_token)
```

- [ ] **Step 5: Add `cookie_header_for` and route `cookie_header` through it**

Replace `cookie_header` (line 207-218) with:

```python
def cookie_header_for(user_id: str, token: str) -> str:
    """The ``Cookie:`` value for a request as ``user_id`` holding ``token``.

    The one spelling of the header, kept as one function so the encoding of the
    separator and the choice of cookie name live in a single place - the two
    things a request gets wrong silently. Takes the two halves rather than an
    account so the deep-login exchange, which holds a ``web_token`` that is not in
    ``account.token``, can build the same header without reaching for the record.
    """
    if not user_id or not token:
        raise UPoolError("This account has no session cookie stored - paste it again.")
    return f"{COOKIE_NAME}={user_id}{COOKIE_SEPARATOR}{token}"


def cookie_header(account: CursorAccount) -> str:
    """The ``Cookie:`` value for a request made as ``account``."""
    return cookie_header_for(account.user_id, account.token)
```

- [ ] **Step 6: Run to verify they pass**

Run: `python -m pytest tests/test_cursor_store.py --basetemp=.pytest-tmp -q`
Expected: PASS (existing store tests plus the three new ones).

- [ ] **Step 7: Commit**

```bash
git add src/upool/cursor/models.py tests/test_cursor_store.py
git commit -m "Keep the browser cookie beside the session token it mints."
```

---

### Task 2: `deeplogin.py` — the exchange

**Files:**
- Create: `src/upool/cursor/deeplogin.py`
- Test: `tests/test_cursor_deeplogin.py`

**Interfaces:**
- Consumes: `models.COOKIE_NAME`, `models.COOKIE_SEPARATOR`, `models.token_kind`, `models.UPoolError` (re-exported via `..models`).
- Produces: `deeplogin.DeepLogin` (frozen dataclass with `user_id: str`, `token: str`); `deeplogin.exchange(user_id: str, web_token: str, *, timeout: float = 20.0, attempts: int = 5, interval: float = 2.0) -> DeepLogin`; `deeplogin._pkce_pair() -> tuple[str, str]` (verifier, challenge).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_cursor_deeplogin.py`. The stub models the three calls: a GET that returns HTML (and would set a jar cookie), a POST confirm, and a poll GET. `urlopen` is monkeypatched; the opener the module builds also routes through it because `HTTPCookieProcessor` calls the same `urlopen`. To keep the test independent of opener internals, monkeypatch at `deeplogin`'s module boundary — the module exposes `_open(request, timeout)` that both the jar-GET/POST and the poll go through.

```python
from __future__ import annotations

import base64
import hashlib
import io
import json

import pytest

import upool.cursor.deeplogin as deeplogin
from upool.models import UPoolError

USER = "user_01ABC"
WEB = "eyJ0eXAiOiJKV1QifQ.web.sig"
SESSION_JWT = "eyJ0eXAiOiJKV1QifQ.sess.sig"


class _Resp:
    def __init__(self, status: int, body: bytes) -> None:
        self.status = status
        self._body = body

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def read(self, _n: int | None = None) -> bytes:
        return self._body

    def geturl(self) -> str:
        return "https://cursor.com/loginDeepControl"


def _router(**answers):
    """Build a fake `_open` that answers by URL substring.

    answers: keys 'deep' (the GET), 'confirm' (the POST), 'poll' -> _Resp.
    A missing key means that call is never expected; hitting it fails loudly.
    """
    def _open(request, timeout=None):
        url = request.full_url
        if "loginDeepControl" in url and request.get_method() == "GET":
            return answers["deep"]
        if "loginDeepCallbackControl" in url:
            return answers["confirm"]
        if "auth/poll" in url:
            poll = answers["poll"]
            return poll.pop(0) if isinstance(poll, list) else poll
        raise AssertionError(f"unexpected call: {request.get_method()} {url}")
    return _open


@pytest.fixture
def net(monkeypatch):
    def _install(**answers):
        monkeypatch.setattr(deeplogin, "_open", _router(**answers))
    return _install


def _session_poll(user_id: str = USER):
    return _Resp(200, json.dumps({"accessToken": SESSION_JWT, "authId": f"google-oauth2|{user_id}"}).encode())


def test_the_pkce_challenge_is_the_hash_of_the_verifier():
    verifier, challenge = deeplogin._pkce_pair()
    expected = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    assert challenge == expected


def test_a_web_cookie_becomes_a_session_token(net):
    net(deep=_Resp(200, b"<html>"), confirm=_Resp(200, b"OK"), poll=_session_poll())
    result = deeplogin.exchange(USER, WEB)
    assert result.user_id == USER
    assert result.token == SESSION_JWT


def test_the_confirm_rejection_message_reaches_the_caller(net):
    net(deep=_Resp(200, b"<html>"), confirm=_Resp(400, b"Select a team to continue."))
    with pytest.raises(UPoolError, match="Select a team to continue."):
        deeplogin.exchange(USER, WEB)


def test_a_poll_that_never_yields_a_token_is_an_error(net):
    net(deep=_Resp(200, b"<html>"), confirm=_Resp(200, b"OK"), poll=_Resp(200, b"{}"))
    with pytest.raises(UPoolError):
        deeplogin.exchange(USER, WEB, attempts=2, interval=0)


def test_an_unparseable_poll_body_is_an_error_not_a_crash(net):
    net(deep=_Resp(200, b"<html>"), confirm=_Resp(200, b"OK"), poll=_Resp(200, b"<not json>"))
    with pytest.raises(UPoolError):
        deeplogin.exchange(USER, WEB, attempts=1, interval=0)


def test_a_token_minted_for_a_different_account_is_refused(net):
    net(deep=_Resp(200, b"<html>"), confirm=_Resp(200, b"OK"), poll=_session_poll("user_99SOMEONEELSE"))
    with pytest.raises(UPoolError, match="different account"):
        deeplogin.exchange(USER, WEB)
```

- [ ] **Step 2: Run to verify they fail**

Run: `python -m pytest tests/test_cursor_deeplogin.py --basetemp=.pytest-tmp -q`
Expected: FAIL — `ModuleNotFoundError: upool.cursor.deeplogin`.

- [ ] **Step 3: Write the module**

Create `src/upool/cursor/deeplogin.py`:

```python
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
```

- [ ] **Step 4: Run to verify they pass**

Run: `python -m pytest tests/test_cursor_deeplogin.py --basetemp=.pytest-tmp -q`
Expected: PASS (six tests).

- [ ] **Step 5: Commit**

```bash
git add src/upool/cursor/deeplogin.py tests/test_cursor_deeplogin.py
git commit -m "Add the deep-login exchange: a web cookie becomes a session token."
```

---

### Task 3: Store banks and upgrades the cookie

**Files:**
- Modify: `src/upool/cursor/store.py` (`upsert` line 136-186)
- Test: `tests/test_cursor_store.py`

**Interfaces:**
- Consumes: `CursorAccount.web_token` (Task 1); `models.token_kind`, `models.KIND_WEB`.
- Produces: `CursorStore.upgrade_token(account_id: str, session_token: str) -> CursorAccount`. `upsert` now sets `web_token` when the pasted token is a `web` one.

- [ ] **Step 1: Write the failing tests**

In `tests/test_cursor_store.py` (reuse the file's existing `store` fixture / construction pattern — inspect a nearby test for how a `CursorStore` is built under the sandbox):

```python
def test_a_pasted_web_cookie_is_banked_as_the_web_token(store):
    web = "eyJ0eXAiOiJKV1QifQ." + _b64({"type": "web"}) + ".sig"   # helper below
    account, _ = store.upsert(CursorAccount(user_id="user_1", token=web))
    assert account.web_token == web


def test_a_pasted_session_token_leaves_the_web_token_empty(store):
    sess = "eyJ0eXAiOiJKV1QifQ." + _b64({"type": "session"}) + ".sig"
    account, _ = store.upsert(CursorAccount(user_id="user_1", token=sess))
    assert account.web_token == ""


def test_upgrade_token_records_the_session_and_keeps_the_cookie(store):
    web = "eyJ0eXAiOiJKV1QifQ." + _b64({"type": "web"}) + ".sig"
    original, _ = store.upsert(CursorAccount(user_id="user_1", token=web))
    upgraded = store.upgrade_token(original.id, "new-session-token")
    assert upgraded.token == "new-session-token"
    assert upgraded.web_token == web          # the cookie survives, to re-mint later
    assert upgraded.status == "ok"
```

Add this helper near the top of the test file (a `web`/`session` JWT the real `token_kind` will read):

```python
import base64 as _base64
import json as _json


def _b64(claims: dict) -> str:
    raw = _base64.urlsafe_b64encode(_json.dumps(claims).encode()).decode().rstrip("=")
    return raw
```

- [ ] **Step 2: Run to verify they fail**

Run: `python -m pytest tests/test_cursor_store.py -k "web_token or upgrade or banked or session_token_leaves" --basetemp=.pytest-tmp -q`
Expected: FAIL — `AttributeError: 'CursorStore' object has no attribute 'upgrade_token'` and the banked assertion.

- [ ] **Step 3: Bank a pasted web token in `upsert`**

In `store.py`, add the import (line 27):

```python
from .models import KIND_WEB, STATUS_OK, STATUS_UNKNOWN, CursorAccount, as_counter, token_kind
```

(`STATUS_OK` is used in Step 4; add it now.) In `upsert`, both branches set `web_token` when the pasted token is `web`. In the existing-row branch, after `account.token = parsed.token` (line 159) add:

```python
                if token_kind(parsed.token) == KIND_WEB:
                    account.web_token = parsed.token
```

In the new-row branch (line 178-183), build with it:

```python
            account = CursorAccount(
                user_id=parsed.user_id,
                token=parsed.token,
                web_token=parsed.token if token_kind(parsed.token) == KIND_WEB else "",
                email=parsed.email,
                name=parsed.name,
            )
```

- [ ] **Step 4: Add `upgrade_token`**

After `upsert` (before `merge_facts`, line 188), add:

```python
    def upgrade_token(self, account_id: str, session_token: str) -> CursorAccount:
        """Record a session token minted from this row's web cookie.

        ``web_token`` is left untouched: it is the only thing that can re-mint when
        the session dies ~60 days on, so it outlives the token it produced.
        ``status`` becomes ``ok`` - a row that just proved it can sign in is not
        expired, whatever a prior refresh concluded - and the usage figures are
        left alone, since they were true when they were measured.
        """
        with self._lock:
            accounts = self.data()["accounts"]
            for index, existing in enumerate(accounts):
                if existing.get("id") != account_id:
                    continue
                account = CursorAccount.from_dict(existing)
                account.token = session_token
                account.status = STATUS_OK
                accounts[index] = account.to_dict()
                self._save()
                return account
        raise UPoolError(f"No Cursor account with id {account_id}.")
```

- [ ] **Step 5: Run to verify they pass**

Run: `python -m pytest tests/test_cursor_store.py --basetemp=.pytest-tmp -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/upool/cursor/store.py tests/test_cursor_store.py
git commit -m "Bank a pasted web cookie, and record the session it upgrades to."
```

---

### Task 4: `switch.use()` converts before it touches the editor

**Files:**
- Modify: `src/upool/cursor/switch.py` (imports line 34-39; `use` line 76-78; delete `_refuse_unswitchable` line 171-203)
- Test: `tests/test_cursor_switch.py`

**Interfaces:**
- Consumes: `deeplogin.exchange` (Task 2); `store.upgrade_token` (Task 3); `models.cookie_header_for`, `KIND_SESSION`, `KIND_WEB`, `STATUS_EXPIRED`, `token_kind`.
- Produces: `switch._ensure_session_token(account: CursorAccount, store: CursorStore) -> CursorAccount`. `_refuse_unswitchable` is removed.

- [ ] **Step 1: Write/adjust the failing tests**

The existing `test_a_browser_cookie_is_refused_before_the_editor_is_touched` (line 211) and `test_an_expired_account_is_refused_by_name` (line 177) encode the *old* refusal and must change. Replace them and add the ordering test. Use the file's `measured`, `db`, `pool`, `record` fixtures.

```python
def test_a_web_cookie_is_converted_before_the_editor_is_touched(measured, db, pool, monkeypatch):
    calls = record(monkeypatch, running=True)
    web = _web_jwt()                      # helper below; token_kind -> "web"
    account, _ = pool.upsert(CursorAccount(user_id="user_1", token=web))
    minted = deeplogin.DeepLogin(user_id="user_1", token=_session_jwt())
    monkeypatch.setattr(switch.deeplogin, "exchange", lambda uid, wt, **k: minted)

    switch.use(account.id, pool)

    assert pool.get(account.id).token == minted.token   # upgraded and persisted
    assert "close" in calls                             # editor was closed *after* conversion


def test_a_failed_exchange_leaves_the_editor_untouched(measured, db, pool, monkeypatch):
    calls = record(monkeypatch, running=True)
    account, _ = pool.upsert(CursorAccount(user_id="user_1", token=_web_jwt()))

    def _boom(uid, wt, **k):
        raise UPoolError("cursor.com said no")

    monkeypatch.setattr(switch.deeplogin, "exchange", _boom)

    with pytest.raises(UPoolError, match="cursor.com said no"):
        switch.use(account.id, pool)
    assert "close" not in calls          # the load-bearing order: no conversion, no close


def test_a_live_session_token_needs_no_network(measured, db, pool, monkeypatch):
    calls = record(monkeypatch, running=False)
    account, _ = pool.upsert(CursorAccount(user_id="user_1", token=_session_jwt()))

    def _forbidden(*a, **k):
        raise AssertionError("exchange must not be called for a session token")

    monkeypatch.setattr(switch.deeplogin, "exchange", _forbidden)
    switch.use(account.id, pool)          # does not raise, makes no call


def test_an_expired_session_with_no_cookie_is_refused(measured, db, pool, monkeypatch):
    record(monkeypatch, running=True)
    account, _ = pool.upsert(CursorAccount(user_id="user_1", token=_session_jwt()))
    # Force it expired with no web_token behind it.
    account.status = "expired"
    pool.replace(account)                 # or the file's equivalent; see nearby tests
    with pytest.raises(UPoolError):
        switch.use(account.id, pool)


def test_an_expired_web_row_is_re_minted(measured, db, pool, monkeypatch):
    calls = record(monkeypatch, running=True)
    account, _ = pool.upsert(CursorAccount(user_id="user_1", token=_web_jwt()))
    minted = deeplogin.DeepLogin(user_id="user_1", token=_session_jwt())
    monkeypatch.setattr(switch.deeplogin, "exchange", lambda uid, wt, **k: minted)
    switch.use(account.id, pool)
    assert pool.get(account.id).token == minted.token
```

Add JWT helpers near the top of the test file (mirror the ones the file already uses for `_web`/`_session`; the existing tests at lines 211-239 already build such tokens — reuse their construction rather than inventing a new one):

```python
def _web_jwt() -> str:
    return _jwt({"type": "web"})          # _jwt already exists in this file if the
def _session_jwt() -> str:                # web/session tests use one; otherwise add:
    return _jwt({"type": "session"})
```

If no `_jwt` helper exists, add:

```python
import base64, json

def _jwt(claims: dict) -> str:
    seg = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
    return f"eyJ0eXAiOiJKV1QifQ.{seg}.sig"
```

Ensure the test file imports the module and error: `from upool.cursor import deeplogin`, `from upool.models import UPoolError`, `import pytest`.

- [ ] **Step 2: Run to verify they fail**

Run: `python -m pytest tests/test_cursor_switch.py --basetemp=.pytest-tmp -q`
Expected: FAIL — `_ensure_session_token` missing / `switch.deeplogin` missing / old refusal tests gone.

- [ ] **Step 3: Wire the conversion into `use`**

In `switch.py` imports (line 34-39), add:

```python
from ..models import UPoolError
from . import deeplogin, process, vscdb
from .models import (
    KIND_SESSION,
    KIND_WEB,
    STATUS_EXPIRED,
    CursorAccount,
    token_kind,
)
from .store import CursorStore
```

In `use` (line 76-78), replace the refusal with the conversion:

```python
    store = pool if pool is not None else CursorStore()
    account = store.get(account_id)
    account = _ensure_session_token(account, store)
```

- [ ] **Step 4: Replace `_refuse_unswitchable` with `_ensure_session_token`**

Delete `_refuse_unswitchable` (line 171-203) and put in its place:

```python
def _ensure_session_token(account: CursorAccount, store: CursorStore) -> CursorAccount:
    """The account with a token Cursor will accept, minting one if it must.

    Placed at the very top of :func:`use`, before the editor is asked to close,
    because it is the one step here that reaches the network - and the module's
    whole discipline is that everything that can fail fails before Cursor is
    touched. A conversion that cannot reach cursor.com leaves the editor running
    and the pool unchanged; the user retries, with nothing to undo.

    Three cases, in this order:

    1. A live session token is used as-is, with no network call - the common path,
       a hand-pasted session or one adopted from the live editor.
    2. Any cookie that can be exchanged - a stored ``web_token``, or a ``token``
       that is itself a ``web`` cookie - is turned into a session token and
       persisted. A dead cookie fails inside :func:`deeplogin.exchange` with
       cursor.com's own message.
    3. Neither - an expired bare session token with no cookie behind it - is
       refused. Nothing can revive it; only a fresh paste can.
    """
    kind = token_kind(account.token)
    if kind == KIND_SESSION and account.status != STATUS_EXPIRED:
        return account

    source_cookie = account.web_token or (account.token if kind == KIND_WEB else "")
    if source_cookie:
        # exchange validates user_id and the cookie itself and raises a clear
        # message on either being empty, so there is no separate guard here.
        minted = deeplogin.exchange(account.user_id, source_cookie)
        return store.upgrade_token(account.id, minted.token)

    raise UPoolError(
        f"{_label(account)}'s session has expired - paste a fresh cookie for it first."
    )
```

(`_label` already exists at line 206-212 and stays.)

- [ ] **Step 5: Run the switch tests**

Run: `python -m pytest tests/test_cursor_switch.py --basetemp=.pytest-tmp -q`
Expected: PASS. If `pool.replace` / status-forcing in Step 1 does not match the file's helpers, adjust to the file's actual store API (inspect how other tests mutate a stored account's status).

- [ ] **Step 6: Run the whole suite**

Run: `python -m pytest --basetemp=.pytest-tmp -q`
Expected: PASS. Watch for `test_api.py` expectations about `token_kind`/refusal that referenced the old behavior; update any that asserted a web cookie is unusable.

- [ ] **Step 7: Commit**

```bash
git add src/upool/cursor/switch.py tests/test_cursor_switch.py
git commit -m "Convert a web cookie to a session on Use, before the editor is touched."
```

---

### Task 5: The card stops blocking a browser cookie

**Files:**
- Modify: `ui/lib/types.ts` (`CursorAccountSummary` ~line 266-291)
- Modify: `ui/lib/mock.ts` (mock account objects)
- Modify: `ui/components/CursorCard.tsx` (`blocked` line 163-171)

**Interfaces:**
- Consumes: `has_web_token` from the Python `summary()` (Task 1).
- Produces: a `blocked` ladder that enables `Use` on a `web` cookie and on an expired row that still has a cookie.

- [ ] **Step 1: Add `has_web_token` to the type**

In `ui/lib/types.ts`, in `CursorAccountSummary`, beside `has_token` / `token_kind`:

```ts
  has_token: boolean;
  has_web_token: boolean;
  token_kind: CursorTokenKind;
```

- [ ] **Step 2: Provide it in the mock**

In `ui/lib/mock.ts`, add `has_web_token` to every mock account literal. For a browser-cookie mock set it `true`; otherwise `false`. (Search the file for `has_token:` and add the sibling on each.)

- [ ] **Step 3: Rewrite the `blocked` ladder**

In `ui/components/CursorCard.tsx`, replace `blocked` (line 163-171) with:

```tsx
  // Mirror switch._ensure_session_token: a browser cookie is now converted on
  // Use, not refused, so it no longer blocks. Only a truly dead row does - an
  // expired session with no cookie behind it to re-mint from.
  const blocked = !supported
    ? "U-Pool could not find Cursor on this machine."
    : !account.has_token
      ? "No session cookie is stored for this account."
      : expired && account.token_kind === "session" && !account.has_web_token
        ? "This account's session has expired — paste a fresh cookie for it."
        : "";
```

- [ ] **Step 4: Typecheck, lint, build**

Run: `cd ui && npm run typecheck && npm run lint && npm run build`
Expected: all pass; `ui/out` regenerated. Fix any spot the removed `token_kind === "web"` message left dangling (e.g. an unused import or copy constant).

- [ ] **Step 5: Commit**

```bash
git add ui/lib/types.ts ui/lib/mock.ts ui/components/CursorCard.tsx
git commit -m "Let the card offer Use on a browser cookie; block only a dead row."
```

---

### Task 6: Manual end-to-end verification (author-run)

**Files:** none — this is the honest step the tests cannot cover.

- [ ] **Step 1: Build and run**

```bash
cd ui && npm run build && cd ..
python -m upool
```

- [ ] **Step 2: Convert a real cookie**

Paste a real `web` cookie for a spare Cursor account, close Cursor, press `Use` on that card, reopen Cursor.

- [ ] **Step 3: Confirm the outcome**

Cursor is signed in as that account; `cursor.json` shows the row's `token` is now a `session` token (`type: session`) and `web_token` still holds the original cookie. A second `Use` on it makes no network call. Report the result — this plan does not claim it works until this step is run.

---

## Self-Review

**Spec coverage:**
- §1 measured protocol → Task 2 (`deeplogin.py`, constants verbatim). ✓
- §2 PKCE → Task 2 `_pkce_pair` + invariant test. ✓
- §3 module, identity check, failure taxonomy → Task 2. ✓ (interface refined: `exchange(user_id, web_token)` instead of a pre-built header string — noted in the plan header; one-spelling preserved via `COOKIE_NAME`/`COOKIE_SEPARATOR`, and the identity check gets `expected_user_id` for free.)
- §4 `web_token`, `_TEXT_FIELDS`, `redacted`, `summary`+`has_web_token` → Task 1. ✓
- §5 `_ensure_session_token` three cases, `_refuse_unswitchable` removed, conversion before `process.close` → Task 4. ✓
- §6 `upsert` banks, `upgrade_token` → Task 3. ✓
- §7 card `blocked` rewrite, `has_web_token` in type/mock → Task 5. ✓
- §8 server text surfaced verbatim → Task 2 confirm/`_poll` raise carries the body; asserted in `test_the_confirm_rejection_message_reaches_the_caller`. ✓
- §9 tests → Tasks 1-4 each carry theirs. ✓
- §10 no claim without manual run → Task 6. ✓

**Placeholder scan:** No TBD/TODO. Two steps say "inspect the file's fixture/helper" (Task 3 `store`, Task 4 status-forcing) — these are real instructions to match an existing pattern, not placeholders, and each names the exact thing to look for. Acceptable.

**Type consistency:** `exchange(user_id, web_token, *, timeout, attempts, interval)` used identically in Task 2 (def), Task 4 (monkeypatch `lambda uid, wt, **k`). `DeepLogin(user_id, token)` consistent. `upgrade_token(account_id, session_token)` consistent Task 3 ↔ Task 4. `has_web_token` consistent Python summary ↔ TS type ↔ mock ↔ card. `cookie_header_for(user_id, token)` consistent Task 1 ↔ Task 4.
