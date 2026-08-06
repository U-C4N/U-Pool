"""One record per Cursor account in the pool.

``user_id`` is identity here, not ``email``. It is the half of the session cookie
before ``::``, it is known the moment a cookie is pasted, and it is what makes a
re-paste of a rotated cookie refresh the row that is already there instead of
adding a second one. An email is not known until ``/api/auth/me`` answers, and on
an expired token it never answers at all.

There is deliberately no ``period_end``. ``/api/auth/stripe`` returns one and the
card does not show it - a field stored for a display that was considered and
declined is a field that goes stale without anyone noticing.

Every usage figure is optional because it comes from undocumented endpoints that
will change without notice. A missing value renders as an em dash; it must never
be the reason a switch cannot happen, since the token is what switching needs and
the token comes from none of those calls.
"""

from __future__ import annotations

import base64
import binascii
import json
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any

from ..models import UPoolError, mask_secret, now_ms

# Which kind of credential a token is, read from its JWT ``type`` claim. A desktop
# sign-in stores a ``session`` token; a cookie exported from a browser is a ``web``
# token. Both authenticate the same cursor.com API - which is why a web cookie
# still fills a card with a name, a plan and a usage bar - but only a session token
# signs the desktop client in. Writing a web token into ``state.vscdb`` makes
# Cursor reject it and sign itself out, so the kind is surfaced to keep ``Use``
# from doing exactly that. Measured, not assumed: a browser cookie and a desktop
# session were compared side by side, and they differ in this claim.
KIND_SESSION = "session"
KIND_WEB = "web"
KIND_UNKNOWN = "unknown"


def token_kind(token: str) -> str:
    parts = token.split(".")
    if len(parts) != 3:
        return KIND_UNKNOWN
    segment = parts[1]
    try:
        payload = json.loads(base64.urlsafe_b64decode(segment + "=" * (-len(segment) % 4)))
    except (ValueError, binascii.Error):
        return KIND_UNKNOWN
    kind = payload.get("type") if isinstance(payload, dict) else None
    return kind if kind in (KIND_SESSION, KIND_WEB) else KIND_UNKNOWN

# What the last refresh concluded about the token. ``unknown`` is the honest
# answer before the first call and after a network failure - a Cursor outage must
# not mark a working account dead.
STATUS_OK = "ok"
STATUS_EXPIRED = "expired"
STATUS_UNKNOWN = "unknown"
STATUSES = (STATUS_OK, STATUS_EXPIRED, STATUS_UNKNOWN)

# Cursor meters a plan either in dollars or in requests, and the two endpoints
# that report them are different. The unit travels with the numbers so the bar
# can label itself without guessing from the magnitude.
UNIT_USD = "usd"
UNIT_REQUESTS = "requests"
UNITS = (UNIT_USD, UNIT_REQUESTS, "")

COOKIE_NAME = "WorkosCursorSessionToken"
# cursor.com still accepts the two NextAuth spellings, and a ``cookies.txt``
# exported from a browser carries whichever one that session was issued, so
# recognising all three is the parser's job rather than the user's.
COOKIE_NAMES = (COOKIE_NAME, "__Secure-next-auth.session-token", "next-auth.session-token")

# ``::`` percent-encoded. The dashboard sends it encoded, and an undocumented
# endpoint is not the place to find out whether the raw form is also accepted.
COOKIE_SEPARATOR = "%3A%3A"

_TEXT_FIELDS = (
    "id",
    "user_id",
    "email",
    "name",
    "token",
    "web_token",
    "plan",
    "plan_status",
    "usage_unit",
    "status",
)
_OPTIONAL_NUMBERS = ("usage_used", "usage_limit", "usage_percent")
_COUNTERS = ("last_checked", "added_at")


@dataclass
class CursorAccount:
    id: str = field(default_factory=lambda: uuid.uuid4().hex)

    # The two halves of the cookie. ``user_id`` identifies the row; ``token`` is
    # the whole credential, so it never leaves this process in a list response.
    user_id: str = ""
    token: str = ""

    # The browser cookie this account was pasted as, kept when `token` is upgraded
    # to a session token so the session can be re-minted when it dies ~60 days on.
    # A `web` token pasted straight in lives here too, banked by CursorStore.upsert.
    web_token: str = ""

    # Filled by /api/auth/me, absent until it answers.
    email: str = ""
    name: str = ""
    # Also from /api/auth/me, and stored for one reason: Cursor keeps the avatar
    # in the same profile blob as the display name, so a switch that wrote only
    # the name would drop the picture from the account menu. U-Pool's own cards
    # never render it - see :meth:`summary`.
    avatar: str = ""

    # Filled by /api/auth/stripe.
    plan: str = ""
    plan_status: str = ""

    # Filled by /api/usage-summary, or /api/usage for a request-quota plan.
    usage_used: float | None = None
    usage_limit: float | None = None
    usage_unit: str = ""
    usage_percent: float | None = None

    status: str = STATUS_UNKNOWN
    last_checked: int = 0
    added_at: int = field(default_factory=now_ms)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def redacted(self) -> dict[str, Any]:
        """Same record with both cookies masked, for logs and error messages."""
        data = self.to_dict()
        data["token"] = mask_secret(self.token)
        data["web_token"] = mask_secret(self.web_token)
        return data

    def summary(self, active: bool) -> dict[str, Any]:
        """What the UI is allowed to see: the record without its credential.

        ``api.py``'s module docstring already rules that no secret goes into a
        list response, and this one is stronger than a provider key - a session
        cookie is the whole account, not one endpoint's access to it. The UI only
        ever needs to know whether a token is there, which is ``has_token``.
        """
        data = self.to_dict()
        data.pop("token", None)
        # A second credential, handled exactly as `token` is: never sent, its
        # presence surfaced as a boolean the card uses to decide whether an
        # expired row can still be re-minted.
        web_token = data.pop("web_token", None)
        data["has_web_token"] = bool(web_token)
        # Held only to be written back into Cursor's own profile blob. Sending it
        # would put a remote image URL in front of the webview for every card, to
        # render something the card was never designed to show.
        data.pop("avatar", None)
        data["active"] = bool(active)
        data["has_token"] = bool(self.token)
        # Not the token, but what kind it is - the card disables Use on a browser
        # cookie, which shows usage but cannot sign the desktop app in.
        data["token_kind"] = token_kind(self.token)
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "CursorAccount":
        """Rebuild a record from ``cursor.json``, tolerating whatever is in it.

        The file is plain JSON the user can open and edit, and half of it is
        filled in by endpoints that are expected to change shape. So a value that
        will not convert falls back to the default rather than raising: one
        malformed row must not cost the whole pool.
        """
        known = set(cls.__dataclass_fields__)
        clean = {k: v for k, v in data.items() if k in known}
        clean.setdefault("id", uuid.uuid4().hex)
        for key in _TEXT_FIELDS:
            if key in clean:
                clean[key] = "" if clean[key] is None else str(clean[key])
        for key in _OPTIONAL_NUMBERS:
            if key in clean:
                clean[key] = as_optional_float(clean[key])
        for key in _COUNTERS:
            if key in clean:
                clean[key] = as_counter(clean[key])
        if clean.get("status") not in STATUSES:
            clean["status"] = STATUS_UNKNOWN
        if clean.get("usage_unit") not in UNITS:
            clean["usage_unit"] = ""
        return cls(**clean)


def as_optional_float(value: Any) -> float | None:
    """A usage figure, or ``None`` for anything that is not one.

    ``None`` is a real value here - "not measured yet" - so an unparseable entry
    collapses onto it rather than onto zero, which would read as a fresh quota.
    A bool is rejected for the same reason: ``float(True)`` is 1.0, and a card
    showing 1.0 of a limit nobody set is worse than a card showing nothing.
    """
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def as_counter(value: Any) -> int:
    """A millisecond timestamp. Zero means never, which is the safe default."""
    number = as_optional_float(value)
    return int(number) if number is not None else 0


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
