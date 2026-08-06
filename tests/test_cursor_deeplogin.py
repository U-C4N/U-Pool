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
# The brief's literal "sess" middle segment is not valid base64 JSON, so
# token_kind() reads it as unknown rather than session and the happy-path test
# fails against _verify_identity's real (unmocked) token_kind check. The middle
# segment here is base64url of {"type":"session"}, matching the jwt_of_type()
# helper's encoding in tests/test_cursor_switch.py.
SESSION_JWT = "eyJ0eXAiOiJKV1QifQ.eyJ0eXBlIjoic2Vzc2lvbiJ9.sig"


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
