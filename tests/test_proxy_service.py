from __future__ import annotations

import json
import threading
import time

import pytest

from upool import paths
from upool.api import Api
from upool.proxy.cookies import parse_cookies
from upool.proxy.models import ProxyError, UpstreamResponse
from upool.proxy.service import ProxyService
from upool.proxy.store import AccountStore


@pytest.mark.parametrize("value", ["NaN", "Infinity", "-Infinity", "invalid"])
def test_invalid_retry_after_uses_the_finite_default(value):
    from upool.proxy.service import _retry_after
    assert _retry_after({"Retry-After": value}) == 60


class Engines:
    def __init__(self):
        self.calls = []
        self.closed = []
        self.replies = []
        self.adopted = []

    def snapshot(self):
        return []

    def complete(self, account, endpoint, payload):
        self.calls.append((account["id"], endpoint, payload))
        if self.replies:
            return self.replies.pop(0)
        return UpstreamResponse(200, {"Content-Type": "application/json"}, json.dumps({
            "id": "chatcmpl-test", "object": "chat.completion", "created": 1, "model": payload["model"],
            "choices": [{"index": 0, "message": {"role": "assistant", "content": "hello"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 4, "completion_tokens": 2, "total_tokens": 6}}).encode())

    def verify_chatgpt(self, account, cookies):
        return {"user_id": "user-one", "email": "one@example.test", "models": ["auto"]}

    def refresh_chatgpt(self, account):
        return self.verify_chatgpt(account, [])

    def adopt_account(self, source, target):
        self.adopted.append((source, target))

    def stop_account(self, account_id):
        self.closed.append(account_id)

    def delete_account_data(self, account_id):
        self.stop_account(account_id)

    def stop_all(self):
        pass

    def begin_grok(self, account):
        return {"state": "nonce", "url": "https://auth.x.ai/device", "user_code": "CODE"}

    def poll_grok(self, account, state):
        return {"status": "ok", "facts": {"user_id": "xai-user", "email": "grok@example.test", "models": ["grok-model"]}}

    def cancel_grok(self, account, state):
        pass


def add(service, account_id, provider="grok", model="grok-model"):
    account = service._new_account(provider, account_id)
    account.update(id=account_id, status="ready", models=[model])
    service.store.put(account)
    return account


def request(model="grok/grok-model", **extras):
    return {"model": model, "messages": [{"role": "user", "content": "Hello"}], **extras}


def test_cookie_forms_preserve_scope_and_http_only():
    token = "__Secure-next-auth.session-token"
    exported = [{"name": token, "value": "private", "domain": ".chatgpt.com", "path": "/", "secure": True,
                 "httpOnly": True, "sameSite": "lax", "expirationDate": time.time() + 1000}]
    cookies = parse_cookies(json.dumps(exported))
    assert cookies[0]["httpOnly"] and cookies[0]["sameSite"] == "Lax"
    assert cookies[0]["domain"] == ".chatgpt.com"
    netscape = f"# Netscape HTTP Cookie File\n#HttpOnly_.chatgpt.com\tTRUE\t/\tTRUE\t0\t{token}\tprivate"
    assert parse_cookies(netscape)[0]["httpOnly"]
    assert parse_cookies(f"Cookie: {token}=private; other=abc")[0]["value"] == "private"


@pytest.mark.parametrize("text", ["other=secret", '[{"name":"__Secure-next-auth.session-token","value":"private","domain":"evil.test"}]',
                                  '[{"name":"__Secure-next-auth.session-token","value":"private","expires":1}]'])
def test_invalid_cookie_exports_rejected_without_echo(text):
    with pytest.raises(ProxyError) as error:
        parse_cookies(text)
    assert "private" not in str(error.value) and "secret" not in str(error.value)


def test_store_key_is_stable_and_public_accounts_have_no_secrets():
    service = ProxyService(Engines())
    result = service.import_cookie("__Secure-next-auth.session-token=private", "Main")
    assert len(service.state()["accounts"]) == 1
    service.import_cookie("__Secure-next-auth.session-token=rotated", "Updated")
    assert len(service.state()["accounts"]) == 1
    assert service.state()["accounts"][0]["id"] == result["account_id"]
    public = json.dumps(service.state())
    assert "private" not in public and "rotated" not in public and "cookies" not in public
    assert service.client_key == AccountStore().key()
    assert service.engines.adopted


def test_round_robin_telemetry_and_profile_creation_without_switch():
    engines = Engines()
    service = ProxyService(engines)
    add(service, "one")
    add(service, "two")
    for _ in range(3):
        result = service.complete("chat/completions", request())
        assert json.loads(result.body)["model"] == "grok/grok-model"
    assert [entry[0] for entry in engines.calls] == ["one", "two", "one"]
    assert service.state()["stats"]["requests"] == 3
    assert service.state()["stats"]["input_tokens"] == 12
    api = Api()
    api._proxy = service
    config = paths.codex_config_file()
    assert not config.exists()
    saved = api.proxy_connect("codex", "grok/grok-model")
    assert saved["ok"]
    assert not config.exists()


@pytest.mark.parametrize(("app", "provider", "model", "expected"), [
    ("hermes", "chatgpt", "auto", {"upool_proxy": "chatgpt"}),
    ("hermes", "grok", "grok-model", {}),
    ("codex", "chatgpt", "auto", {}),
    ("opencode", "chatgpt", "auto", {}),
])
def test_proxy_profile_marks_only_hermes_chatgpt_without_applying_live_config(app, provider, model, expected):
    service = ProxyService(Engines())
    add(service, "one", provider, model)
    api = Api()
    api._proxy = service
    result = api.proxy_connect(app, f"{provider}/{model}")
    assert result["ok"]
    saved = api.store.get(app, result["data"]["id"])
    assert saved.extra == expected
    assert saved.api_key == service.client_key
    assert not paths.hermes_config_file().exists()


def test_failover_only_before_stream_and_retry_after_preserved():
    engines = Engines()
    engines.replies = [UpstreamResponse(429, {"Retry-After": "120"}, b"credential-bearing upstream error")]
    service = ProxyService(engines)
    add(service, "one")
    add(service, "two")
    service.complete("chat/completions", request())
    account = service.store.get("one")
    assert account["status"] == "cooldown"
    assert 115 < account["cooldown_until"] - time.time() <= 120
    assert [entry[0] for entry in engines.calls] == ["one", "two"]
    assert "credential-bearing" not in json.dumps(service.state())


def test_midstream_failure_releases_account_without_replay():
    def chunks():
        yield b'data: {"choices":[{"delta":{"content":"partial"}}]}\n\n'
        raise OSError("private detail")
    engines = Engines()
    engines.replies = [UpstreamResponse(200, {"Content-Type": "text/event-stream"}, chunks())]
    service = ProxyService(engines)
    add(service, "one")
    add(service, "two")
    result = service.complete("chat/completions", request(stream=True))
    iterator = iter(result.body)
    assert b"partial" in next(iterator)
    with pytest.raises(ProxyError, match="interrupted"):
        next(iterator)
    assert len(engines.calls) == 1 and not service._busy
    assert service.state()["stats"]["errors"] == 1


def test_cookie_tool_repair_and_responses_output():
    engines = Engines()
    def reply(content):
        return UpstreamResponse(200, {}, json.dumps({"id": "chatcmpl-test", "model": "auto", "choices": [
            {"index": 0, "message": {"role": "assistant", "content": content}, "finish_reason": "stop"}]}).encode())
    engines.replies = [reply("bad response"), reply('{"content":null,"tool_calls":[{"name":"read","arguments":{"path":"a"}}]}')]
    service = ProxyService(engines)
    add(service, "one", "chatgpt", "auto")
    payload = {"model": "chatgpt/auto", "input": "Read a", "tools": [{"type": "function", "name": "read",
               "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}]}
    response = json.loads(service.complete("responses", payload).body)
    assert response["output"][0]["type"] == "function_call"
    assert response["model"] == "chatgpt/auto"
    assert len(engines.calls) == 2
    assert all(entry[2]["stream"] is False for entry in engines.calls)


def test_grok_login_opaque_state_and_cancel():
    service = ProxyService(Engines())
    login = service.login_grok("Grok")
    with pytest.raises(ProxyError):
        service.login_status(login["account_id"], "wrong-state")
    result = service.login_status(login["account_id"], login["state"])
    assert result["status"] == "ok"
    assert service.state()["accounts"][0]["status"] == "ready"


def test_per_account_serialization():
    service = ProxyService(Engines())
    add(service, "one")
    first = service._acquire("grok", "grok-model", set())
    acquired = threading.Event()
    def waiter():
        account = service._acquire("grok", "grok-model", set())
        acquired.set()
        service._release(account)
    thread = threading.Thread(target=waiter)
    thread.start()
    assert not acquired.wait(0.05)
    service._release(first)
    assert acquired.wait(2)
    thread.join(2)


def stream_reply(*events):
    body = [("data: " + json.dumps(event) + "\n\n").encode() for event in events]
    body.append(b"data: [DONE]\n\n")
    return UpstreamResponse(200, {"Content-Type": "text/event-stream"}, iter(body))


def rows(service):
    with service.telemetry._connect() as db:
        return db.execute("SELECT status, model, input_tokens, output_tokens FROM requests ORDER BY timestamp").fetchall()


def test_stream_alias_and_usage_preserved_across_split_frames():
    engines = Engines()
    body = b'data: {"model":"grok-model","choices":[{"delta":{"content":"hello"},"finish_reason":null}]}\n\ndata: {"model":"grok-model","choices":[],"usage":{"prompt_tokens":7,"completion_tokens":3}}\n\ndata: [DONE]\n\n'
    engines.replies = [UpstreamResponse(200, {"Content-Type": "text/event-stream"}, (body[i:i + 5] for i in range(0, len(body), 5)))]
    service = ProxyService(engines)
    add(service, "one")
    result = service.complete("chat/completions", request(stream=True))
    output = b"".join(result.body)
    assert b'"model": "grok/grok-model"' in output
    assert b'"model": "grok-model"' not in output
    assert rows(service) == [(200, "grok/grok-model", 7, 3)]
    result.close()
    assert rows(service) == [(200, "grok/grok-model", 7, 3)]


def test_native_responses_stream_aliases_include_initial_and_final_events():
    engines = Engines()
    engines.replies = [stream_reply({"type": "response.created", "response": {"model": "grok-model"}}, {"type": "response.completed", "response": {"model": "grok-model", "usage": {"input_tokens": 8, "output_tokens": 2}}})]
    service = ProxyService(engines)
    add(service, "one")
    output = b"".join(service.complete("responses", {"model": "grok/grok-model", "input": "hi", "stream": True}).body)
    frames = [json.loads(frame.split(b"data: ")[1]) for frame in output.split(b"\n\n") if frame.startswith(b"data: {")]
    assert all(frame["response"]["model"] == "grok/grok-model" for frame in frames)
    assert rows(service) == [(200, "grok/grok-model", 8, 2)]


def test_chatgpt_responses_alias_in_all_stream_events():
    engines = Engines()
    engines.replies = [stream_reply({"id": "a", "model": "auto", "choices": [{"delta": {"content": "hi"}, "finish_reason": None}]}, {"model": "auto", "choices": [{"delta": {}, "finish_reason": "stop"}]})]
    service = ProxyService(engines)
    add(service, "one", "chatgpt", "auto")
    output = b"".join(service.complete("responses", {"model": "chatgpt/auto", "input": "hi", "stream": True}).body)
    assert b'"model": "auto"' not in output
    assert output.count(b'"model": "chatgpt/auto"') == 3
    assert not service._busy


@pytest.mark.parametrize("started", [False, True])
def test_close_marks_cancellation_and_releases_even_before_generator_start(started):
    engines = Engines()
    closed = []
    response = stream_reply({"model": "grok-model", "choices": [{"delta": {"content": "first"}, "finish_reason": None}]})
    response.close = lambda: closed.append(True)
    engines.replies = [response]
    service = ProxyService(engines)
    add(service, "one")
    result = service.complete("chat/completions", request(stream=True))
    if started:
        next(iter(result.body))
    result.close()
    result.body.close()
    result.close()
    assert not service._busy
    assert rows(service)[0][0] == 499
    assert closed == [True]


def test_first_stream_error_fails_over_without_output_and_uses_retry_after():
    engines = Engines()
    engines.replies = [stream_reply({"error": {"message": "private-cookie", "status": 429, "retry_after": 125}}), stream_reply({"model": "grok-model", "choices": [{"delta": {"content": "second"}, "finish_reason": "stop"}]})]
    service = ProxyService(engines)
    add(service, "one")
    add(service, "two")
    result = service.complete("chat/completions", request(stream=True))
    output = b"".join(result.body)
    assert b"second" in output and b"private-cookie" not in output
    assert [call[0] for call in engines.calls] == ["one", "two"]
    assert 120 < service.store.get("one")["cooldown_until"] - time.time() <= 125
    assert [row[0] for row in rows(service)] == [429, 200]


def test_late_stream_error_is_not_replayed_and_account_is_quarantined():
    engines = Engines()
    engines.replies = [stream_reply({"model": "grok-model", "choices": [{"delta": {"content": "started"}, "finish_reason": None}]}, {"error": {"status": 401, "message": "private-cookie"}})]
    service = ProxyService(engines)
    add(service, "one")
    add(service, "two")
    result = service.complete("chat/completions", request(stream=True))
    assert b"started" in next(iter(result.body))
    with pytest.raises(ProxyError) as exc:
        next(iter(result.body))
    assert exc.value.status == 401 and "private-cookie" not in str(exc.value)
    assert len(engines.calls) == 1
    assert service.store.get("one")["status"] == "reauth"
    assert rows(service)[0][0] == 401


def test_stream_without_terminal_marker_is_an_error():
    engines = Engines()
    engines.replies = [UpstreamResponse(200, {"Content-Type": "text/event-stream"}, iter([b'data: {"model":"grok-model","choices":[]}\n\n']))]
    service = ProxyService(engines)
    add(service, "one")
    result = service.complete("chat/completions", request(stream=True))
    with pytest.raises(ProxyError) as exc:
        b"".join(result.body)
    assert exc.value.code == "incomplete_stream"
    assert not service._busy and rows(service)[0][0] == 502


def test_bad_request_is_not_retried_across_accounts():
    engines = Engines()
    engines.replies = [UpstreamResponse(400, {}, b"private details")]
    service = ProxyService(engines)
    add(service, "one")
    add(service, "two")
    with pytest.raises(ProxyError) as exc:
        service.complete("chat/completions", request())
    assert exc.value.status == 400
    assert len(engines.calls) == 1 and not service._busy


def test_function_repair_happens_only_once_across_entire_pool():
    engines = Engines()
    engines.replies = [UpstreamResponse(200, {}, json.dumps({"choices": [{"message": {"content": "invalid"}}]}).encode()) for _ in range(4)]
    service = ProxyService(engines)
    add(service, "one", "chatgpt", "auto")
    add(service, "two", "chatgpt", "auto")
    payload = request("chatgpt/auto", tools=[{"type": "function", "function": {"name": "read", "parameters": {"type": "object"}}}])
    with pytest.raises(ProxyError) as exc:
        service.complete("chat/completions", payload)
    assert exc.value.code == "invalid_tool_output"
    assert [call[0] for call in engines.calls] == ["one", "one"]
    assert not service._busy


def test_access_denied_removes_only_failed_model():
    engines = Engines()
    engines.replies = [UpstreamResponse(403, {}, b"error")]
    service = ProxyService(engines)
    account = add(service, "one")
    account["models"].append("other-model")
    service.store.put(account)
    with pytest.raises(ProxyError):
        service.complete("chat/completions", request())
    account = service.store.get("one")
    assert account["status"] == "ready" and account["models"] == ["other-model"]


def test_usage_storage_failure_cannot_fail_or_retry_generation(monkeypatch):
    engines = Engines()
    service = ProxyService(engines)
    add(service, "one")
    add(service, "two")
    def fail(*args):
        raise OSError("full disk")
    monkeypatch.setattr(service.telemetry, "record", fail)
    assert json.loads(service.complete("chat/completions", request()).body)["choices"][0]["message"]["content"] == "hello"
    assert len(engines.calls) == 1 and not service._busy


def test_delete_reserves_account_before_engine_shutdown():
    engines = Engines()
    service = ProxyService(engines)
    add(service, "one")
    entered = threading.Event()
    release = threading.Event()
    def stop(account_id):
        entered.set()
        release.wait(2)
    engines.stop_account = stop
    thread = threading.Thread(target=service.delete_account, args=("one",))
    thread.start()
    assert entered.wait(1)
    assert "one" in service._busy
    with pytest.raises(ProxyError) as exc:
        service.refresh_account("one")
    assert exc.value.code == "account_busy"
    release.set()
    thread.join(2)
    assert not service._busy and not service.store.accounts()


def test_verification_counts_toward_browser_cap_and_preserves_concurrent_edits():
    engines = Engines()
    service = ProxyService(engines)
    add(service, "one", "chatgpt", "auto")
    add(service, "two", "chatgpt", "auto")
    a = service._acquire("chatgpt", "auto", set())
    b = service._acquire("chatgpt", "auto", set())
    with pytest.raises(ProxyError) as exc:
        service.import_cookie("__Secure-next-auth.session-token=new")
    assert exc.value.code == "browsers_busy"
    assert service._busy == {"one", "two"}
    service._release(a)
    service._release(b)
    account = service.store.get("one")
    account["user_id"] = "user-one"
    service.store.put(account)
    def verify(account, cookies):
        service.update_account("one", {"name": "Edited", "enabled": False})
        return {"user_id": "user-one", "models": ["auto"]}
    engines.verify_chatgpt = verify
    service.refresh_account("one")
    assert service.store.get("one")["name"] == "Edited"
    assert service.store.get("one")["enabled"] is False
    assert not service._busy


def test_no_tools_messages_normalized_at_actual_worker_boundary():
    from upool.proxy.worker import conversation_prompt
    engines = Engines()
    original = engines.complete
    observed = []
    def complete(account, endpoint, payload):
        observed.append(conversation_prompt(payload["messages"]))
        return original(account, endpoint, payload)
    engines.complete = complete
    service = ProxyService(engines)
    add(service, "one", "chatgpt", "auto")
    messages = [{"role": "user", "content": [{"type": "text", "text": "Read"}, {"type": "text", "text": "main.py"}]},
                {"role": "assistant", "content": None, "tool_calls": [{"id": "call_a", "type": "function", "function": {"name": "read_file", "arguments": '{"path":"main.py"}'}}]},
                {"role": "tool", "tool_call_id": "call_a", "content": [{"type": "text", "text": "file content"}]}]
    service.complete("chat/completions", request("chatgpt/auto", messages=messages))
    wire = engines.calls[0][2]["messages"]
    assert wire[0]["content"] == "Read\nmain.py"
    assert wire[1]["content"] == "" and wire[1]["tool_calls"][0]["id"] == "call_a"
    assert wire[2]["content"] == "file content"
    assert observed and not service._busy


def test_queued_acquisition_obeys_request_cancellation():
    service = ProxyService(Engines())
    add(service, "one")
    occupied = service._acquire("grok", "grok-model", set())
    cancelled = threading.Event()
    error = []
    def run():
        try:
            service.complete_cancellable("chat/completions", request(), cancelled)
        except ProxyError as exc:
            error.append(exc)
    thread = threading.Thread(target=run)
    thread.start()
    cancelled.set()
    thread.join(1)
    assert not thread.is_alive() and error[0].status == 499
    assert not service.engines.calls
    service._release(occupied)


def test_cancellation_stops_before_tool_repair():
    engines = Engines()
    cancelled = threading.Event()
    def complete(account, endpoint, payload, signal):
        signal.set()
        return UpstreamResponse(200, {}, b'{"choices":[{"message":{"content":"invalid"}}]}')
    engines.complete_cancellable = complete
    service = ProxyService(engines)
    add(service, "one", "chatgpt", "auto")
    payload = request("chatgpt/auto", tools=[{"type": "function", "function": {"name": "read_file"}}])
    with pytest.raises(ProxyError) as exc:
        service.complete_cancellable("chat/completions", payload, cancelled)
    assert exc.value.status == 499 and not service._busy
    assert rows(service)[0][0] == 499
    assert service.store.get("one")["error"] == ""


def test_shutdown_never_requires_runtime_snapshot_and_is_idempotent():
    engines = Engines()
    calls = []
    def invalid_snapshot():
        raise ValueError("manifest missing")
    engines.snapshot = invalid_snapshot
    engines.stop_all = lambda: calls.append("stop")
    service = ProxyService(engines)
    service.shutdown()
    service.shutdown()
    assert calls == ["stop"]
    assert service._shutdown and service._stopping


def test_shutdown_always_stops_engines_if_gateway_cleanup_raises(monkeypatch):
    engines = Engines()
    calls = []
    engines.stop_all = lambda: calls.append("stop")
    service = ProxyService(engines)
    def fail():
        raise OSError("closed socket")
    monkeypatch.setattr(service.gateway, "stop", fail)
    with pytest.raises(OSError):
        service.shutdown()
    assert calls == ["stop"]


@pytest.mark.parametrize("endpoint", ["chat/completions", "responses"])
def test_terminal_success_releases_account_before_yield_without_waiting_for_eof(endpoint):
    engines = Engines()
    closed = []
    terminal = b"data: [DONE]\n\n" if endpoint == "chat/completions" else b'event: response.completed\ndata: {"type":"response.completed","response":{"model":"grok-model","usage":{"input_tokens":4,"output_tokens":2}}}\n\n'
    def body():
        yield b'data: {"model":"grok-model","choices":[{"delta":{"content":"done"}}]}\n\n'
        yield terminal
        raise AssertionError("The completed upstream must not be read again")
    engines.replies = [UpstreamResponse(200, {"Content-Type": "text/event-stream"}, body(), lambda: closed.append(True))]
    service = ProxyService(engines)
    add(service, "one")
    payload = request(stream=True) if endpoint == "chat/completions" else {"model": "grok/grok-model", "input": "hi", "stream": True}
    result = service.complete(endpoint, payload)
    iterator = iter(result.body)
    assert b"done" in next(iterator)
    assert service._busy == {"one"}
    assert b"[DONE]" in next(iterator) if endpoint == "chat/completions" else b"response.completed" in next(iterator)
    assert not service._busy and closed == [True]
    assert rows(service)[0][0] == 200
    acquired = service._acquire("grok", "grok-model", set())
    service._release(acquired)
    result.close()
    iterator.close()
    assert rows(service)[0][0] == 200 and closed == [True]
