from __future__ import annotations

import json

import pytest

from upool.proxy.cookies import parse_cookies
from upool.proxy.models import ProxyError


def test_netscape_domain_scope_is_preserved():
    cookies = parse_cookies(
        "#HttpOnly_chatgpt.com\tTRUE\t/\tTRUE\t0\t__Secure-next-auth.session-token\tfake-session\n"
        "auth.chatgpt.com\tFALSE\t/login\tFALSE\t0\tpreference\ttheme"
    )
    assert cookies[0]["domain"] == ".chatgpt.com"
    assert cookies[0]["httpOnly"] is True
    assert cookies[1]["domain"] == "auth.chatgpt.com"
    assert cookies[1]["secure"] is False
    assert cookies[1]["path"] == "/login"


@pytest.mark.parametrize("patch", [
    {"httpOnly": "false"}, {"secure": 1}, {"session": "true"}, {"value": 123},
])
def test_json_flags_and_values_are_not_silently_coerced(patch):
    cookie = {"name": "__Secure-next-auth.session-token", "value": "fake-session", **patch}
    with pytest.raises(ProxyError) as caught:
        parse_cookies(json.dumps([cookie]))
    assert caught.value.code == "invalid_cookie"


def test_chunked_sessions_are_accepted_but_unrelated_prefixes_are_not():
    assert len(parse_cookies("__Secure-authjs.session-token.0=fake0; __Secure-authjs.session-token.1=fake1")) == 2
    with pytest.raises(ProxyError):
        parse_cookies("__Secure-authjs.session-token-other=fake")


def test_expired_export_entries_do_not_reject_a_live_session(monkeypatch):
    monkeypatch.setattr("upool.proxy.cookies.time.time", lambda: 1000)
    live = {"name": "__Secure-authjs.session-token", "value": "live", "expires": 2000}
    old = {"name": "__Secure-next-auth.session-token", "value": "old", "expires": 999}
    expired_preference = {"name": "preference", "value": "old", "expires": 999}
    cookies = parse_cookies(json.dumps([old, live, expired_preference]))
    assert [cookie["value"] for cookie in cookies] == ["live"]
    with pytest.raises(ProxyError) as error:
        parse_cookies(json.dumps([old]))
    assert error.value.code == "auth_expired"
