from __future__ import annotations

import json
import socket
import ssl
import threading
import urllib.error
import urllib.parse
import urllib.request

import pytest

from upool.cursor import api
from upool.cursor.models import STATUS_EXPIRED, STATUS_OK, UNIT_REQUESTS, UNIT_USD, CursorAccount

# Trimmed copies of what cursor.com's own dashboard gets back. Every one of these
# shapes is undocumented, so the tests that matter most below are the ones where
# the shape is wrong.
ME = {
    "email": "umut@example.com",
    "email_verified": True,
    "name": "Umut Can",
    "sub": "auth0|user_01ABC",
    "picture": None,
}
STRIPE = {
    "membershipType": "pro",
    "paymentId": "cus_ABC",
    "daysRemainingOnTrial": 0,
    "subscriptionStatus": "active",
    "verifiedStudent": False,
}
SUMMARY = {
    "planUsage": {"usedCents": 1240, "limitCents": 2000},
    "startOfPeriod": "2026-08-01T00:00:00.000Z",
}
LEGACY = {
    "gpt-4": {
        "numRequests": 321,
        "numRequestsTotal": 321,
        "numTokens": 1_000_000,
        "maxRequestUsage": 500,
        "maxTokenUsage": None,
    },
    "gpt-3.5-turbo": {"numRequests": 40, "maxRequestUsage": None},
    "gpt-4-32k": {"numRequests": 2, "maxRequestUsage": 50},
    "startOfMonth": "2026-08-01T00:00:00.000Z",
}

PATHS = {
    "/api/auth/me": "me",
    "/api/auth/stripe": "stripe",
    "/api/usage-summary": "summary",
    "/api/usage": "usage",
}


def _which(url: str) -> str:
    """Which of the four calls this URL is. Exact paths: ``/api/usage`` is a
    prefix of ``/api/usage-summary`` and a substring match would confuse them."""
    return PATHS[urllib.parse.urlsplit(url).path]


class _Response:
    """Stand-in for the context manager ``urlopen`` returns."""

    def __init__(self, payload, status: int = 200, body: bytes | None = None) -> None:
        self._body = json.dumps(payload).encode("utf-8") if body is None else body
        self.status = status

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> "_Response":
        return self

    def __exit__(self, *_exc) -> None:
        return None


class _Net:
    """Stands in for ``urllib.request.urlopen``; the suite opens no socket.

    A route is a payload (answered as 200 JSON), an ``int`` (that HTTP status),
    a callable returning an exception (raised), or ``bytes`` (a 200 whose body is
    whatever that is).
    Calls are recorded so a test can assert what was *not* asked - the legacy
    usage endpoint is meant to stay unasked most of the time.
    """

    def __init__(self, **routes) -> None:
        self.routes = dict(routes)
        self._lock = threading.Lock()
        self.calls: list[str] = []
        self.cookies: list[str | None] = []
        self.agents: list[str | None] = []

    def __call__(self, request, timeout=None):
        url = request.full_url
        with self._lock:
            self.calls.append(url)
            self.cookies.append(request.get_header("Cookie"))
            self.agents.append(request.get_header("User-agent"))
        try:
            answer = self.routes[_which(url)]
        except KeyError:  # pragma: no cover - a test that forgot a route
            raise AssertionError(f"no route for {url}") from None
        if callable(answer):
            # A factory, so a test that fails every call raises a fresh exception
            # into each of the three threads rather than one shared object.
            raise answer()
        if isinstance(answer, int):
            raise urllib.error.HTTPError(url, answer, "no", {}, None)
        if isinstance(answer, bytes):
            return _Response(None, body=answer)
        return _Response(answer)

    def asked(self, name: str) -> list[str]:
        return [url for url in self.calls if _which(url) == name]


@pytest.fixture
def net(monkeypatch):
    stub = _Net(me=ME, stripe=STRIPE, summary=SUMMARY, usage=LEGACY)
    monkeypatch.setattr(urllib.request, "urlopen", stub)
    return stub


def account(**fields) -> CursorAccount:
    return CursorAccount(**{"user_id": "user_01ABC", "token": "eyJ.header.sig", **fields})


# ------------------------------------------------------------- the happy path


def test_a_complete_answer_fills_the_whole_card(net):
    holder = account()
    facts = api.fetch(holder)
    assert facts.account_id == holder.id
    assert facts.status == STATUS_OK
    assert facts.email == "umut@example.com"
    assert facts.name == "Umut Can"
    assert facts.plan == "pro"
    assert facts.plan_status == "active"
    assert facts.usage_used == 12.40
    assert facts.usage_limit == 20.00
    assert facts.usage_unit == UNIT_USD
    assert facts.usage_percent == 62.0
    assert facts.messages == []
    assert facts.checked_at > 0


def test_every_call_carries_the_session_cookie_and_identifies_itself(net):
    holder = account()
    api.fetch(holder)
    expected = f"WorkosCursorSessionToken={holder.user_id}%3A%3A{holder.token}"
    assert net.cookies == [expected] * 3
    assert set(net.agents) == {api.USER_AGENT}


def test_the_three_calls_are_made_once_each(net):
    api.fetch(account())
    assert [len(net.asked(name)) for name in ("me", "stripe", "summary")] == [1, 1, 1]


# ------------------------------------------------------------------ the token


def test_a_rejected_cookie_expires_the_account(net):
    net.routes.update(me=401, stripe=401, summary=401)
    facts = api.fetch(account())
    assert facts.status == STATUS_EXPIRED
    # Only the status. The stored email and name are what let a fresh cookie
    # revive the row in place, so a rejection must not carry blanks over them.
    assert facts.changes() == {"status": STATUS_EXPIRED}
    assert "expired" in facts.messages[0]


def test_one_endpoint_rejecting_does_not_expire_an_account_that_answered(net):
    # A 401 from a single endpoint is that endpoint's quirk. Something answered,
    # so the cookie is alive.
    net.routes.update(stripe=403)
    facts = api.fetch(account())
    assert facts.status == STATUS_OK
    assert facts.email == "umut@example.com"
    assert facts.plan is None


def test_an_account_without_a_cookie_is_never_marked_expired(net):
    facts = api.fetch(account(token=""))
    assert facts.status is None
    assert facts.changes() == {}
    assert "paste it again" in facts.messages[0]
    # Nothing was worth asking, so nothing was asked.
    assert net.calls == []


# ------------------------------------------- failures that must change nothing


@pytest.mark.parametrize(
    "failure",
    [
        lambda: urllib.error.URLError(socket.timeout("timed out")),
        lambda: TimeoutError("timed out"),
        lambda: urllib.error.URLError(ConnectionRefusedError(61, "refused")),
        lambda: urllib.error.URLError(ssl.SSLError(1, "certificate verify failed")),
        lambda: RuntimeError("something nobody predicted"),
    ],
)
def test_a_failure_that_is_not_a_rejection_leaves_the_status_alone(net, failure):
    net.routes.update(me=failure, stripe=failure, summary=failure, usage=failure)
    facts = api.fetch(account())
    assert facts.status is None, "an outage must not mark a working account dead"
    assert facts.changes() == {}
    assert facts.messages


def test_one_outage_is_one_sentence(net):
    net.routes.update(
        me=lambda: TimeoutError("t"),
        stripe=lambda: TimeoutError("t"),
        summary=lambda: TimeoutError("t"),
        usage=lambda: TimeoutError("t"),
    )
    assert api.fetch(account()).messages == ["cursor.com did not answer within 10s."]


def test_an_extractor_blowing_up_is_still_not_an_exception(net, monkeypatch):
    # The safety net, tested the only way it can be: every extractor below is
    # written not to need it, so one is made to fail on purpose.
    def explode(_payload):
        raise ValueError("a shape nobody wrote a guard for")

    monkeypatch.setattr(api, "_spend", explode)
    facts = api.fetch(account())
    assert "ValueError" in facts.messages[0]
    assert facts.checked_at > 0
    # Whatever was learned before it blew up is kept.
    assert facts.email == "umut@example.com"


def test_a_server_error_leaves_the_status_alone(net):
    net.routes.update(me=503, stripe=503, summary=503, usage=503)
    facts = api.fetch(account())
    assert facts.status is None
    assert "503" in facts.messages[0]


def test_a_body_that_is_not_json_is_not_a_dead_account(net):
    page = b"<html>maintenance</html>"
    net.routes.update(me=page, stripe=page, summary=page, usage=page)
    facts = api.fetch(account())
    assert facts.status is None
    assert any("did not answer with JSON" in message for message in facts.messages)


def test_partial_success_fills_partially(net):
    net.routes.update(stripe=500, summary=500, usage=500)
    facts = api.fetch(account())
    assert facts.status == STATUS_OK
    assert facts.email == "umut@example.com"
    assert facts.name == "Umut Can"
    assert facts.plan is None
    assert facts.usage_used is None and facts.usage_percent is None
    assert facts.changes() == {
        "email": "umut@example.com",
        "name": "Umut Can",
        "status": STATUS_OK,
    }


# --------------------------------------------------------------------- usage


def test_the_request_endpoint_is_not_asked_when_the_summary_answered(net):
    api.fetch(account())
    assert net.asked("usage") == []


def test_an_empty_usage_summary_falls_back_to_the_request_endpoint(net):
    net.routes.update(summary={})
    holder = account()
    facts = api.fetch(holder)
    asked = net.asked("usage")
    assert len(asked) == 1
    query = urllib.parse.parse_qs(urllib.parse.urlsplit(asked[0]).query)
    assert query == {"user": [holder.user_id]}
    assert facts.usage_used == 321
    assert facts.usage_limit == 500
    assert facts.usage_unit == UNIT_REQUESTS
    assert facts.usage_percent == 64.2


def test_the_request_endpoint_is_not_asked_when_the_cookie_was_rejected(net):
    net.routes.update(summary=401)
    api.fetch(account())
    assert net.asked("usage") == []


@pytest.mark.parametrize(
    "payload",
    [
        {"planUsage": []},
        {"usage": {"used": "not a number"}},
        [1, 2, 3],
        "a string where an object was",
        None,
        {"planUsage": {"used": None, "limit": True}},
    ],
)
def test_an_unexpected_usage_shape_costs_the_usage_and_nothing_else(net, payload):
    net.routes.update(summary=payload, usage=payload)
    facts = api.fetch(account())
    assert facts.usage_used is None
    assert facts.usage_limit is None
    assert facts.usage_percent is None
    assert facts.usage_unit is None
    # The card degrades; the identity it already had does not.
    assert facts.email == "umut@example.com"
    assert facts.status == STATUS_OK


@pytest.mark.parametrize(
    ("payload", "used", "limit"),
    [
        # Dollars stay dollars.
        ({"used": 12.4, "limit": 20}, 12.4, 20.0),
        ({"used": 12, "limit": 20}, 12.0, 20.0),
        # A key that says cents is believed whatever the magnitude.
        ({"usedCents": 40, "limitCents": 100}, 0.4, 1.0),
        # No such key, but a whole-dollar integer ceiling this big is cents.
        ({"used": 1240, "limit": 2000}, 12.4, 20.0),
        # What is left instead of what is allowed.
        ({"used": 12.4, "remaining": 7.6}, 12.4, 20.0),
        # Snake case, and a wrapper the root does not name the same way.
        ({"individual_usage": {"total_used": 5.0, "hard_limit": 20.0}}, 5.0, 20.0),
    ],
)
def test_spend_is_read_in_whatever_units_the_payload_used(net, payload, used, limit):
    net.routes.update(summary=payload)
    facts = api.fetch(account())
    assert (facts.usage_used, facts.usage_limit) == (used, limit)
    assert facts.usage_unit == UNIT_USD


def test_a_reported_percentage_is_enough_on_its_own(net):
    net.routes.update(summary={"totalPercentUsed": 41.5})
    facts = api.fetch(account())
    assert facts.usage_percent == 41.5
    # A percentage is usable, so the legacy endpoint is left alone - and with no
    # figures there is nothing to put a unit on.
    assert net.asked("usage") == []
    assert facts.usage_unit is None


def test_two_figures_beat_a_percentage_that_disagrees_with_them(net):
    net.routes.update(summary={"used": 5, "limit": 20, "percentUsed": 99})
    assert api.fetch(account()).usage_percent == 25.0


def test_the_request_bucket_with_the_largest_ceiling_wins(net):
    net.routes.update(summary={}, usage=LEGACY)
    facts = api.fetch(account())
    # The gpt-4 bucket, not the sum of all three and not the 50-request one.
    assert (facts.usage_used, facts.usage_limit) == (321, 500)


def test_request_counts_without_a_ceiling_still_show_something(net):
    net.routes.update(summary={}, usage={"gpt-4": {"numRequests": 12}})
    facts = api.fetch(account())
    assert facts.usage_used == 12
    assert facts.usage_limit is None
    assert facts.usage_percent is None
    assert facts.usage_unit == UNIT_REQUESTS


# ---------------------------------------------------- what the live API returns
#
# Everything above is a shape somebody reasoned their way to. These three are
# transcripts: the exact bodies cursor.com returned on 2026-08-06 for a signed-in
# account, trimmed of nothing that matters. They are the only fixtures here that
# are evidence rather than argument, and the first two nest their figures two
# levels down - which the extractor missed until this measurement was taken.

LIVE_SUMMARY = {
    "billingCycleStart": "2026-07-14T00:08:52.258Z",
    "billingCycleEnd": "2026-08-14T00:08:52.258Z",
    "membershipType": "free",
    "limitType": "user",
    "isUnlimited": False,
    "individualUsage": {
        "plan": {
            "enabled": True,
            "used": 14,
            "limit": 20,
            "remaining": 6,
            "breakdown": {"included": 20, "bonus": 0, "total": 20},
            "autoPercentUsed": 70,
            "apiPercentUsed": 0,
            "totalPercentUsed": 70,
        },
        "onDemand": {"enabled": False, "used": 99, "limit": None, "remaining": None},
    },
    "teamUsage": {},
}
LIVE_STRIPE = {
    "membershipType": "free",
    "paymentId": "cus_ABC",
    "subscriptionStatus": "unpaid",
    "verifiedStudent": True,
    "isTeamMember": False,
    "teamMembershipType": None,
    "individualMembershipType": "free",
    "isYearlyPlan": False,
}
LIVE_LEGACY = {
    "gpt-4": {
        "numRequests": 0,
        "numRequestsTotal": 0,
        "numTokens": 0,
        "maxTokenUsage": None,
        "maxRequestUsage": None,
    },
    "startOfMonth": "2026-07-14T00:08:52.258Z",
}


def test_the_live_summary_shape_is_read_two_levels_down(net):
    """``individualUsage.plan`` - the walk stopped at the wrapper before 0.8.0."""
    net.routes.update(summary=LIVE_SUMMARY)
    facts = api.fetch(account())
    assert (facts.usage_used, facts.usage_limit) == (14, 20)
    assert facts.usage_unit == UNIT_USD
    assert facts.usage_percent == 70.0


def test_the_on_demand_bucket_is_not_mistaken_for_the_plan(net):
    """``onDemand.used`` is a different number under the same key name.

    It is reachable only if ``onDemand`` joins the section list, which is why that
    list is a fixed set of names rather than a walk of the document.
    """
    net.routes.update(summary=LIVE_SUMMARY)
    assert api.fetch(account()).usage_used == 14


def test_a_free_account_with_no_quota_reports_zeroes_rather_than_nothing(net):
    """The measured account: a real answer that happens to be all zeroes.

    Reporting it as unknown would be wrong in the other direction - the endpoint
    did answer, and "0 of 0" is what it said.
    """
    summary = json.loads(json.dumps(LIVE_SUMMARY))
    summary["individualUsage"]["plan"].update(used=0, limit=0, remaining=0, totalPercentUsed=0)
    net.routes.update(summary=summary, usage=LIVE_LEGACY)
    facts = api.fetch(account())
    assert (facts.usage_used, facts.usage_limit) == (0, 0)
    assert facts.usage_unit == UNIT_USD


def test_the_live_stripe_shape_gives_the_plan_and_its_status(net):
    net.routes.update(stripe=LIVE_STRIPE)
    facts = api.fetch(account())
    assert (facts.plan, facts.plan_status) == ("free", "unpaid")


LIVE_ME = {
    "email": "umut@example.com",
    "email_verified": True,
    "name": "Umut Jan",
    "sub": "user_01JAXG",
    "created_at": "2024-10-23T20:16:56.465Z",
    "updated_at": "2026-08-05T20:58:05.887Z",
    "picture": "https://workoscdn.com/images/v1/PxPBcY71MEdD",
    "id": 109596761,
}


def test_the_avatar_comes_back_from_the_identity_call(net):
    """``picture`` is the URL Cursor already had in ``cachedScopedProfile``.

    Without it a switch writes the display name and drops the account menu's
    picture - which the first live round-trip did, and is why this is read at all.
    """
    net.routes.update(me=LIVE_ME)
    facts = api.fetch(account())
    assert facts.avatar == "https://workoscdn.com/images/v1/PxPBcY71MEdD"
    assert (facts.email, facts.name) == ("umut@example.com", "Umut Jan")


def test_an_identity_call_with_no_picture_reports_no_avatar(net):
    net.routes.update(me={"email": "a@example.com", "name": "A", "picture": None})
    assert api.fetch(account()).avatar is None


# ------------------------------------------------------------ identity + plan


@pytest.mark.parametrize(
    ("payload", "email", "name"),
    [
        ({"email": "a@b.c", "name": "A"}, "a@b.c", "A"),
        ({"user": {"email": "a@b.c", "displayName": "A"}}, "a@b.c", "A"),
        ({"emailAddress": "a@b.c", "full_name": "A"}, "a@b.c", "A"),
        # A field that turned into an object is skipped, not stringified.
        ({"email": "a@b.c", "name": {"first": "A"}}, "a@b.c", None),
        ({"email": "", "name": "  "}, None, None),
    ],
)
def test_identity_is_read_under_several_spellings(net, payload, email, name):
    net.routes.update(me=payload)
    facts = api.fetch(account())
    assert (facts.email, facts.name) == (email, name)


@pytest.mark.parametrize(
    ("payload", "plan", "status"),
    [
        ({"membershipType": "pro", "subscriptionStatus": "active"}, "pro", "active"),
        ({"membership_type": "business", "status": "trialing"}, "business", "trialing"),
        ({"subscription": {"plan": "ultra", "status": "past_due"}}, "ultra", "past_due"),
        # ``plan`` as an object falls through to the next spelling rather than
        # stopping the search on something unprintable.
        ({"plan": {"nickname": "pro"}, "tier": "pro"}, "pro", None),
    ],
)
def test_the_plan_is_read_under_several_spellings(net, payload, plan, status):
    net.routes.update(stripe=payload)
    facts = api.fetch(account())
    assert (facts.plan, facts.plan_status) == (plan, status)


# ------------------------------------------------------------------- the pool


def test_fetch_many_answers_once_per_account_in_order(net):
    accounts = [account(user_id=f"user_0{n}") for n in range(3)]
    results = api.fetch_many(accounts)
    assert [facts.account_id for facts in results] == [holder.id for holder in accounts]
    assert all(facts.status == STATUS_OK for facts in results)
    assert len(net.calls) == 9
    # Each account asked as itself, which is the bug a shared header would cause.
    assert sorted(set(net.cookies)) == sorted(
        f"WorkosCursorSessionToken={holder.user_id}%3A%3A{holder.token}" for holder in accounts
    )


def test_fetch_many_over_nothing_asks_nothing(net):
    assert api.fetch_many([]) == []
    assert net.calls == []


def test_fetch_many_keeps_going_when_one_account_is_dead(net):
    net.routes.update(me=401, stripe=401, summary=401)
    good, bad = account(), account(token="")
    results = api.fetch_many([good, bad])
    assert [facts.status for facts in results] == [STATUS_EXPIRED, None]
