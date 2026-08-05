"""What cursor.com will tell us about an account we hold a cookie for.

These are the endpoints cursor.com's own dashboard calls. They are undocumented,
they carry no version, and they have already changed shape once between the
request-quota era and the dollar-budget one. So this module is written to the
rule from the design: **a shape change must degrade the card, never break the
switch.** Nothing is indexed, every field is optional, every failure is a
sentence rather than an exception, and :func:`fetch` cannot raise - the token is
what a switch needs, and the token comes from none of these calls.

That rule is why the return value is :class:`AccountFacts` rather than a
:class:`~upool.cursor.models.CursorAccount`: a refresh reports only what it
learned, and the caller merges. A card that showed a plan and a usage bar
yesterday keeps both through today's outage instead of being overwritten with
blanks.

Concurrency is on two levels, deliberately. ``fetch_many`` parallelises over
accounts, and ``fetch`` *also* runs its three GETs side by side rather than
leaving them sequential inside a worker - because ``fetch`` is what the single
card's Refresh button reaches, and there the user is watching one account.
Sequential, three timeouts of ten seconds are thirty seconds of spinner for one
card; concurrent, they are ten. The cost is at most eight by three sockets during
a refresh-all, which is I/O the whole way down.

Stdlib ``urllib`` only, following :mod:`upool.health`. No new dependency.
"""

from __future__ import annotations

import json
import socket
import ssl
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any

from ..health import USER_AGENT
from ..models import UPoolError, now_ms
from .models import (
    STATUS_EXPIRED,
    STATUS_OK,
    UNIT_REQUESTS,
    UNIT_USD,
    CursorAccount,
    as_optional_float,
    cookie_header,
)

DEFAULT_TIMEOUT = 10.0
# One account's three calls, and up to eight accounts at once - the same width
# ``health.check_many`` uses, for the same reason: past that the bottleneck stops
# being us.
CALL_WORKERS = 3
ACCOUNT_WORKERS = 8

# A constant, never a setting. The credential in the header is the whole account,
# and a configurable host is a configurable place to send it to.
BASE_URL = "https://cursor.com"
ME_URL = f"{BASE_URL}/api/auth/me"
STRIPE_URL = f"{BASE_URL}/api/auth/stripe"
USAGE_SUMMARY_URL = f"{BASE_URL}/api/usage-summary"
USAGE_URL = f"{BASE_URL}/api/usage"

ME_LABEL = "account endpoint"
STRIPE_LABEL = "plan endpoint"
SUMMARY_LABEL = "usage endpoint"
USAGE_LABEL = "request-usage endpoint"

REJECTED = (401, 403)
REJECTED_MESSAGE = "Cursor rejected the session cookie - it has expired or been signed out."

# Fields a refresh can fill in. Anything not listed here is ours rather than
# cursor.com's, and stays out of the merge.
LEARNABLE = (
    "email",
    "name",
    "plan",
    "plan_status",
    "usage_used",
    "usage_limit",
    "usage_unit",
    "usage_percent",
    "status",
)


@dataclass
class AccountFacts:
    """What one refresh learned. ``None`` means "did not find out", not "empty".

    The distinction is the whole point: a stored plan must survive a call that
    failed, so only the fields that came back are allowed into the record. See
    :meth:`changes`.
    """

    account_id: str = ""

    # ``None`` here means the calls reached no conclusion about the cookie - a
    # timeout, a 500, an account with nothing stored - and the caller keeps
    # whatever status it had. ``"unknown"`` is a conclusion; this is the absence
    # of one.
    status: str | None = None

    email: str | None = None
    name: str | None = None
    plan: str | None = None
    plan_status: str | None = None
    usage_used: float | None = None
    usage_limit: float | None = None
    usage_unit: str | None = None
    usage_percent: float | None = None

    # When the answers were obtained, which is not when the caller gets round to
    # storing them - a refresh-all can be ten seconds wide.
    checked_at: int = 0

    # One readable sentence per thing that went wrong, for the card's tooltip.
    messages: list[str] = field(default_factory=list)

    def changes(self) -> dict[str, Any]:
        """The subset a caller may copy onto its record.

        Here rather than in the store because the "``None`` means unanswered"
        rule is this module's, and a merge that re-derives it by hand is a merge
        that will one day blank a field on a bad day for cursor.com.
        """
        return {name: getattr(self, name) for name in LEARNABLE if getattr(self, name) is not None}


# ----------------------------------------------------------------- one request


@dataclass
class _Answer:
    """One GET, reduced to the three things the extractors ask of it."""

    data: Any = None
    code: int | None = None
    problem: str = ""

    @property
    def ok(self) -> bool:
        return self.data is not None

    @property
    def rejected(self) -> bool:
        return self.code in REJECTED


def _get(url: str, cookie: str, timeout: float, label: str) -> _Answer:
    """A GET as this account, turned into an :class:`_Answer` whatever happens.

    The failure taxonomy is the design's: ``401``/``403`` is the token being
    dead, and everything else - refused connection, TLS, timeout, 5xx, a body
    that is not JSON - is *us* failing to find out, which must leave the stored
    status alone. A Cursor outage marking a working account expired would be a
    worse bug than showing no usage at all.
    """
    request = urllib.request.Request(url, method="GET")
    request.add_header("user-agent", USER_AGENT)
    request.add_header("accept", "application/json")
    request.add_header("cookie", cookie)

    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read()
            code = getattr(response, "status", None)
    except urllib.error.HTTPError as exc:
        if exc.code in REJECTED:
            return _Answer(code=exc.code, problem=REJECTED_MESSAGE)
        return _Answer(code=exc.code, problem=f"Cursor's {label} answered HTTP {exc.code}.")
    except urllib.error.URLError as exc:
        reason = exc.reason
        if isinstance(reason, ssl.SSLError):
            # ``reason`` is set by the ssl module on the errors it raises and is
            # absent on one built any other way, so it is read defensively - an
            # AttributeError from inside an except block is the one place a
            # "never raises" promise breaks without a test noticing.
            detail = getattr(reason, "reason", "") or reason
            return _Answer(problem=f"TLS failed talking to cursor.com: {detail}")
        if isinstance(reason, socket.timeout):
            return _Answer(problem=f"cursor.com did not answer within {timeout:.0f}s.")
        return _Answer(problem=f"Could not reach cursor.com: {reason}")
    except (socket.timeout, TimeoutError):
        return _Answer(problem=f"cursor.com did not answer within {timeout:.0f}s.")
    except Exception as exc:  # noqa: BLE001 - a refresh must never crash the UI
        return _Answer(problem=f"The {label} could not be read: {type(exc).__name__}: {exc}")

    try:
        payload = json.loads(body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        # HTML from a captive portal or a maintenance page reaches here.
        return _Answer(code=code, problem=f"Cursor's {label} did not answer with JSON.")
    return _Answer(data=payload, code=code)


# ------------------------------------------------------------------ extraction
#
# Everything below digs. No payload is indexed, no key is assumed present, and
# each field is probed under every spelling that is plausible for an endpoint
# nobody documented - camelCase and snake_case, at the root and inside the
# wrappers a dashboard payload tends to grow.

# Wrappers worth looking inside. A fixed list rather than a walk of the whole
# document, because ``used`` inside a ``teamUsage`` block is somebody else's
# number and a walk would happily find it.
IDENTITY_SECTIONS = ("user", "profile", "account", "data")
PLAN_SECTIONS = ("subscription", "membership", "plan", "data")
USAGE_SECTIONS = (
    "planUsage",
    "plan_usage",
    # The live payload nests the figures two deep as ``individualUsage.plan``, so
    # both halves have to be listed - without the second the walk stops on the
    # wrapper and the card reports nothing on an account that has numbers.
    "plan",
    "usage",
    "individualUsage",
    "individual_usage",
    "currentPeriod",
    "current_period",
    "summary",
    "activeSubscription",
    "data",
)

EMAIL_KEYS = ("email", "emailAddress", "email_address")
NAME_KEYS = ("name", "displayName", "display_name", "fullName", "full_name", "nickname")
PLAN_KEYS = ("membershipType", "membership_type", "planName", "plan_name", "plan", "tier", "type")
PLAN_STATUS_KEYS = ("subscriptionStatus", "subscription_status", "status", "state")

USED_KEYS = (
    "used",
    "usedCents",
    "used_cents",
    "usageCents",
    "usage_cents",
    "totalUsed",
    "total_used",
    "amountUsed",
    "spendCents",
    "currentUsage",
)
LIMIT_KEYS = (
    "limit",
    "limitCents",
    "limit_cents",
    "hardLimit",
    "hard_limit",
    "spendLimit",
    "spend_limit",
    "monthlyLimit",
    "maxAmount",
    "budget",
    "quota",
    "totalLimit",
)
REMAINING_KEYS = ("remaining", "remainingCents", "remaining_cents", "amountRemaining")
PERCENT_KEYS = ("totalPercentUsed", "percentUsed", "percent_used", "usagePercent", "percent")

REQUESTS_USED_KEYS = ("numRequests", "num_requests", "numRequestsTotal", "requests", "used")
REQUESTS_LIMIT_KEYS = (
    "maxRequestUsage",
    "max_request_usage",
    "maxRequests",
    "requestLimit",
    "limit",
)

# A key that says so is the only certain evidence a figure is in cents.
CENTS_HINT = "cent"
# Failing that: an integer limit this large that divides into whole dollars is a
# cents figure. Cursor's dollar budgets are $20 and $40, so 2000 and 4000 are
# cents and 20 and 40 are dollars; the two ranges do not overlap anywhere a plan
# actually sits. A genuine $1000-a-month budget quoted in dollars would be read
# as $10 - wrong on a card, and still only a card.
CENTS_FLOOR = 1000.0


def _sections(payload: Any, *names: str) -> list[dict[str, Any]]:
    """The payload itself plus any of ``names`` nested up to two levels inside."""
    if not isinstance(payload, dict):
        return []
    found: list[dict[str, Any]] = [payload]
    frontier = [payload]
    for _ in range(2):
        deeper: list[dict[str, Any]] = []
        for section in frontier:
            for name in names:
                child = section.get(name)
                if isinstance(child, dict) and not any(child is seen for seen in found):
                    found.append(child)
                    deeper.append(child)
        frontier = deeper
    return found


def _text_in(sections: list[dict[str, Any]], *names: str) -> str | None:
    """First non-empty string under any of ``names``, searching each section.

    Non-strings are skipped rather than converted, so a payload where ``plan``
    became an object falls through to ``tier`` instead of stopping on it.
    """
    for section in sections:
        for name in names:
            value = section.get(name)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return None


def _number_in(sections: list[dict[str, Any]], *names: str) -> tuple[float | None, str]:
    """First readable number under any of ``names``, with the key that gave it.

    The key comes back because the units heuristic below needs to know whether
    the payload called it cents.
    """
    for section in sections:
        for name in names:
            if name in section:
                number = as_optional_float(section[name])
                if number is not None:
                    return number, name
    return None, ""


def _identity(payload: Any) -> tuple[str | None, str | None]:
    sections = _sections(payload, *IDENTITY_SECTIONS)
    return _text_in(sections, *EMAIL_KEYS), _text_in(sections, *NAME_KEYS)


def _plan(payload: Any) -> tuple[str | None, str | None]:
    sections = _sections(payload, *PLAN_SECTIONS)
    return _text_in(sections, *PLAN_KEYS), _text_in(sections, *PLAN_STATUS_KEYS)


def _to_dollars(
    used: float | None, limit: float | None, used_key: str, limit_key: str
) -> tuple[float | None, float | None]:
    """Normalise a spend pair to dollars, guessing only when it has to.

    Both figures move together: they come out of the same object under the same
    convention, so a payload that names one of them in cents has named the other
    in cents too, whatever it called it.
    """
    named_cents = CENTS_HINT in used_key.lower() or CENTS_HINT in limit_key.lower()
    looks_like_cents = (
        limit is not None
        and limit >= CENTS_FLOOR
        and float(limit).is_integer()
        and limit % 100 == 0
        and (used is None or float(used).is_integer())
    )
    if not (named_cents or looks_like_cents):
        return used, limit
    return (
        None if used is None else used / 100,
        None if limit is None else limit / 100,
    )


def _spend(payload: Any) -> tuple[float | None, float | None, float | None]:
    """Dollars used, dollars allowed and a reported percentage, any of them None."""
    sections = _sections(payload, *USAGE_SECTIONS)
    used, used_key = _number_in(sections, *USED_KEYS)
    limit, limit_key = _number_in(sections, *LIMIT_KEYS)
    if limit is None and used is not None:
        # Some shapes report what is left instead of what is allowed.
        remaining, remaining_key = _number_in(sections, *REMAINING_KEYS)
        if remaining is not None:
            limit, limit_key = used + remaining, remaining_key
    used, limit = _to_dollars(used, limit, used_key, limit_key)
    percent, _ = _number_in(sections, *PERCENT_KEYS)
    return used, limit, percent


def _requests(payload: Any) -> tuple[float | None, float | None]:
    """Requests used and allowed, from the legacy per-model buckets.

    The payload is one object per model plus some loose keys, and each model
    carries its own ceiling. The bucket with the largest ``maxRequestUsage`` is
    the plan's headline quota and the one the dashboard draws. Nothing is summed:
    the buckets meter separate allowances, so their total is a number that is not
    any account's limit.
    """
    if not isinstance(payload, dict):
        return None, None
    # The root too, in case the shape ever flattens onto it.
    buckets = [payload] + [value for value in payload.values() if isinstance(value, dict)]

    best: tuple[float, float | None] | None = None
    loose: float | None = None
    for bucket in buckets:
        used, _ = _number_in([bucket], *REQUESTS_USED_KEYS)
        limit, _ = _number_in([bucket], *REQUESTS_LIMIT_KEYS)
        if limit is not None and limit > 0:
            if best is None or limit > best[0]:
                best = (limit, used)
        elif used is not None and (loose is None or used > loose):
            loose = used
    if best is not None:
        return best[1], best[0]
    return loose, None


# ------------------------------------------------------------------- the fetch


def _messages(answers: list[_Answer]) -> list[str]:
    """Every distinct problem, in the order it happened.

    Three calls failing the same way is one thing wrong, and the card has room
    for one sentence about it.
    """
    seen: list[str] = []
    for answer in answers:
        if answer.problem and answer.problem not in seen:
            seen.append(answer.problem)
    return seen


def fetch(account: CursorAccount, timeout: float = DEFAULT_TIMEOUT) -> AccountFacts:
    """Ask cursor.com about one account. Never raises.

    Partial success fills partially: ``/api/auth/me`` answering while the usage
    call times out gives a name, an email and no usage figures.

    The catch-all is the last line of that promise rather than the first - every
    call and every extractor below already handles its own failures. It is here
    because a refresh runs on a background thread over payloads nobody
    documented, and an exception escaping it would take the whole pool's refresh
    down over one account's odd answer.
    """
    facts = AccountFacts(account_id=account.id)
    try:
        _refresh(account, facts, timeout)
    except UPoolError as exc:
        # No cookie stored at all. Deliberately not ``expired``: this record was
        # never signed in, and saying it expired would hide that from the user.
        facts.messages.append(str(exc))
    except Exception as exc:  # noqa: BLE001 - a refresh must never crash the UI
        facts.messages.append(f"The refresh failed: {type(exc).__name__}: {exc}")
    facts.checked_at = now_ms()
    return facts


def _refresh(account: CursorAccount, facts: AccountFacts, timeout: float) -> None:
    """Fill ``facts`` in place from the four endpoints."""
    cookie = cookie_header(account)
    calls = ((ME_URL, ME_LABEL), (STRIPE_URL, STRIPE_LABEL), (USAGE_SUMMARY_URL, SUMMARY_LABEL))
    with ThreadPoolExecutor(max_workers=CALL_WORKERS, thread_name_prefix="upool-cursor") as pool:
        me, plan, summary = pool.map(lambda call: _get(call[0], cookie, timeout, call[1]), calls)
    answers = [me, plan, summary]

    if me.ok:
        facts.email, facts.name = _identity(me.data)
    if plan.ok:
        facts.plan, facts.plan_status = _plan(plan.data)

    used = limit = percent = None
    if summary.ok:
        used, limit, percent = _spend(summary.data)
    if used is not None or limit is not None:
        facts.usage_unit = UNIT_USD
    elif percent is None and not summary.rejected:
        # The dollar endpoint knows nothing about this account, which is what a
        # request-quota plan looks like. A rejected cookie is not asked twice.
        query = urllib.parse.urlencode({"user": account.user_id})
        legacy = _get(f"{USAGE_URL}?{query}", cookie, timeout, USAGE_LABEL)
        answers.append(legacy)
        if legacy.ok:
            used, limit = _requests(legacy.data)
            if used is not None or limit is not None:
                facts.usage_unit = UNIT_REQUESTS

    # A percentage the payload reported is used only when there is nothing to
    # compute one from: a bar that disagrees with the two figures printed beside
    # it reads as broken, whichever of the two is right.
    if used is not None and limit is not None and limit > 0:
        percent = round(used / limit * 100, 1)
    facts.usage_used, facts.usage_limit, facts.usage_percent = used, limit, percent

    # One endpoint answering proves the cookie works, so a 401 from another is a
    # quirk of that endpoint rather than a dead account. Expired is concluded
    # only when nothing at all got through and something said no.
    if any(answer.ok for answer in answers):
        facts.status = STATUS_OK
    elif any(answer.rejected for answer in answers):
        facts.status = STATUS_EXPIRED

    facts.messages = _messages(answers)
    facts.checked_at = now_ms()


def fetch_many(
    accounts: list[CursorAccount], timeout: float = DEFAULT_TIMEOUT
) -> list[AccountFacts]:
    """One :class:`AccountFacts` per account, in the order given."""
    if not accounts:
        return []
    workers = min(ACCOUNT_WORKERS, len(accounts))
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="upool-cursor") as pool:
        return list(pool.map(lambda account: fetch(account, timeout), accounts))
