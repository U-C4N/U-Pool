from __future__ import annotations

import asyncio
import json
import threading
import socket
import urllib.error
import urllib.request

import pytest

from upool.proxy.worker import BrowserWorker, WorkerFailure, WorkerServer, cdp_cookies, conversation_prompt


def test_cookie_properties_survive_cdp_import():
    cookie = {"name": "__Secure-next-auth.session-token", "value": "fake", "domain": ".chatgpt.com",
              "path": "/", "httpOnly": True, "secure": True, "sameSite": "Lax", "expires": 1900000000}
    assert cdp_cookies([cookie]) == [cookie]
    session = dict(cookie, expires=-1)
    assert "expires" not in cdp_cookies([session])[0]
    with pytest.raises(WorkerFailure):
        cdp_cookies([dict(cookie, domain="chatgpt.com.attacker.invalid")])
    with pytest.raises(WorkerFailure):
        cdp_cookies([dict(cookie, url="https://attacker.invalid/")])


def test_conversation_contains_complete_history_and_rejects_nontext():
    messages = [{"role": "system", "content": "Use tools"}, {"role": "user", "content": "hello"},
                {"role": "assistant", "content": "hi"}, {"role": "user", "content": "continue"}]
    prompt = conversation_prompt(messages)
    assert json.loads(prompt.split("\n\n", 1)[1]) == messages
    with pytest.raises(WorkerFailure):
        conversation_prompt([{"role": "user", "content": [{"type": "image"}]}])


def test_worker_routes_require_bearer_even_health():
    loop = asyncio.new_event_loop()
    thread = threading.Thread(target=loop.run_forever, daemon=True)
    thread.start()
    config = {"port": 0, "api_key": "fake-test-key"}
    worker = BrowserWorker(config)
    server = WorkerServer(config, worker, loop)
    serving = threading.Thread(target=server.serve_forever, daemon=True)
    serving.start()
    url = f"http://127.0.0.1:{server.server_port}/health"
    try:
        with pytest.raises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(url)
        assert error.value.code == 401
        request = urllib.request.Request(url, headers={"Authorization": "Bearer fake-test-key"})
        with urllib.request.urlopen(request) as response:
            assert json.load(response) == {"status": "ready"}
    finally:
        server.shutdown()
        server.server_close()
        serving.join(timeout=2)
        loop.call_soon_threadsafe(loop.stop)
        thread.join(timeout=2)
        loop.close()


def test_crashed_browser_is_runtime_failure_not_expired_auth():
    from types import SimpleNamespace
    async def scenario():
        worker = BrowserWorker({})
        worker.chrome = SimpleNamespace(poll=lambda: 1)
        with pytest.raises(WorkerFailure) as error:
            await worker._ensure()
        assert error.value.status == 503
    asyncio.run(scenario())


@pytest.mark.parametrize("close_fails", [False, True])
def test_failed_authentication_discards_partially_connected_driver(close_fails):
    class FailedDriver:
        is_connected = False
        closed = False
        async def connect(self):
            self.is_connected = True  # Websocket opened before auth failed.
            raise RuntimeError("private authentication detail")
        async def close(self):
            self.closed = True
            self.is_connected = False
            if close_fails:
                raise RuntimeError("private close detail")
    async def scenario():
        worker = BrowserWorker({})
        driver = FailedDriver()
        worker.driver = driver
        with pytest.raises(WorkerFailure) as error:
            await worker._ensure()
        assert error.value.status == 401
        assert "private" not in str(error.value)
        assert driver.closed and worker.driver is None
    asyncio.run(scenario())


def test_cancelled_connect_discards_partially_opened_driver():
    class WaitingDriver:
        is_connected = False
        closed = False
        async def connect(self):
            self.is_connected = True
            await asyncio.Event().wait()
        async def close(self):
            self.closed = True
    async def scenario():
        worker = BrowserWorker({})
        driver = WaitingDriver()
        worker.driver = driver
        task = asyncio.create_task(worker._ensure())
        await asyncio.sleep(0)
        assert driver.is_connected
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert driver.closed and worker.driver is None
    asyncio.run(scenario())


def _browser_cookie_import_fixture(monkeypatch, tmp_path, *, storage_error=False,
                                   target_error=False, missing_target=False):
    import io
    import sys
    import types
    from upool.proxy import worker as worker_module
    endpoints, commands = [], []
    module = _completion_types(monkeypatch)
    class ImportDriver:
        def __init__(self, **kwargs):
            self.configuration = kwargs
            self._completion = object()
            self._target_id = None
            self._owns_target = False
    module.CDPDriver = ImportDriver
    registry = types.ModuleType("chatgpt_web2api.tab_registry")
    monkeypatch.setitem(sys.modules, "chatgpt_web2api.tab_registry", registry)
    sys.modules["chatgpt_web2api"].tab_registry = registry
    def discover(url, **kwargs):
        endpoints.append(url)
        assert url.endswith("/json/version"), "Never select a dying page from /json/list"
        return io.BytesIO(json.dumps({"webSocketDebuggerUrl":
                                     "ws://127.0.0.1:45678/devtools/browser/managed"}).encode())
    monkeypatch.setattr(worker_module.urllib.request, "urlopen", discover)
    class BrowserSocket:
        def __init__(self):
            self.replies = []
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            pass
        async def send(self, raw):
            command = json.loads(raw)
            commands.append(command)
            # The old owned page is still being destroyed. Browser-level
            # import is independent of its websocket and target-list order.
            self.replies.append({"method": "Target.targetDestroyed",
                                 "params": {"targetId": "closing-old-page"}})
            response = {"id": command["id"], "result": {}}
            if command["method"] == "Storage.setCookies" and storage_error:
                response = {"id": command["id"], "error": {"message": "private cookie value"}}
            if command["method"] == "Target.createTarget":
                response["result"] = {} if missing_target else {"targetId": "known-new-page"}
                if target_error:
                    response = {"id": command["id"], "error": {"message": "private target detail"}}
            self.replies.append(response)
        async def recv(self):
            return json.dumps(self.replies.pop(0))
    sockets = []
    def connect(url, **kwargs):
        assert url == "ws://127.0.0.1:45678/devtools/browser/managed"
        socket = BrowserSocket()
        sockets.append(socket)
        return socket
    websockets = types.ModuleType("websockets")
    websockets.connect = connect
    monkeypatch.setitem(sys.modules, "websockets", websockets)
    original_sleep = asyncio.sleep
    async def quick_sleep(delay):
        await original_sleep(0 if delay == 2 else delay)
    monkeypatch.setattr(worker_module.asyncio, "sleep", quick_sleep)
    worker = BrowserWorker({"cdp_port": 45678, "state": str(tmp_path), "account_id": "isolated"})
    async def facts():
        assert worker.driver._target_id == "known-new-page"
        assert worker.driver._owns_target is True
        assert worker.driver.configuration["tab_mode"] == "owned"
        return {"user_id": "fake-own-account", "models": ["auto"]}
    monkeypatch.setattr(worker, "_facts", facts)
    return worker, endpoints, commands


def test_browser_cookie_import_ignores_closing_pages_and_owns_exact_created_target(monkeypatch, tmp_path):
    worker, endpoints, commands = _browser_cookie_import_fixture(monkeypatch, tmp_path)
    class ClosingDriver:
        closed = False
        async def close(self):
            self.closed = True
    previous = ClosingDriver()
    worker.driver = previous
    cookie = {"name": "__Secure-next-auth.session-token", "value": "fake-only",
              "domain": ".chatgpt.com", "path": "/", "secure": True, "httpOnly": True,
              "sameSite": "Lax", "expires": 1900000000}
    async def scenario():
        assert await worker.inject([cookie]) == {"user_id": "fake-own-account", "models": ["auto"]}
        assert previous.closed
        assert endpoints == ["http://127.0.0.1:45678/json/version"]
        assert commands == [
            {"id": 1, "method": "Storage.setCookies", "params": {"cookies": [cookie]}},
            {"id": 2, "method": "Target.createTarget",
             "params": {"url": "https://chatgpt.com/?model=auto"}},
        ]
    asyncio.run(scenario())


@pytest.mark.parametrize("settings,status,methods", [
    ({"storage_error": True}, 400, ["Storage.setCookies"]),
    ({"target_error": True}, 503, ["Storage.setCookies", "Target.createTarget"]),
    ({"missing_target": True}, 503, ["Storage.setCookies", "Target.createTarget"]),
])
def test_browser_cookie_import_failures_are_redacted_without_adopting_arbitrary_page(
        monkeypatch, tmp_path, settings, status, methods):
    worker, endpoints, commands = _browser_cookie_import_fixture(monkeypatch, tmp_path, **settings)
    async def scenario():
        with pytest.raises(WorkerFailure) as error:
            await worker.inject([{"name": "session", "value": "fake-only", "domain": ".chatgpt.com"}])
        assert error.value.status == status
        assert "private" not in str(error.value) and "fake-only" not in str(error.value)
        assert [command["method"] for command in commands] == methods
        assert worker.driver is None
    asyncio.run(scenario())


def test_invalid_cookie_domain_is_rejected_before_browser_commit(monkeypatch, tmp_path):
    worker, endpoints, commands = _browser_cookie_import_fixture(monkeypatch, tmp_path)
    async def scenario():
        with pytest.raises(WorkerFailure):
            await worker.inject([{"name": "session", "value": "fake", "domain": "attacker.invalid"}])
        assert endpoints == commands == []
    asyncio.run(scenario())


def test_each_worker_request_navigates_to_new_conversation(monkeypatch):
    from types import SimpleNamespace
    import queue
    import sys
    import types
    module = types.ModuleType("chatgpt_web2api.cdp_driver")
    module.AuthExpiredError = type("AuthExpiredError", (Exception,), {})
    module.RateLimitError = type("RateLimitError", (Exception,), {})
    monkeypatch.setitem(sys.modules, "chatgpt_web2api", types.ModuleType("chatgpt_web2api"))
    monkeypatch.setitem(sys.modules, "chatgpt_web2api.cdp_driver", module)
    calls = []
    class FakeDriver:
        is_connected = True
        async def navigate_new_chat(self):
            calls.append("new")
        async def send_and_stream(self, prompt, **kwargs):
            yield SimpleNamespace(delta="ok", finish_reason="stop")
    async def scenario():
        worker = BrowserWorker({})
        worker.driver = FakeDriver()
        worker._models = ["auto"]
        async def forbidden_selection(slug):
            pytest.fail("The explicit auto alias must not select a concrete model")
        monkeypatch.setattr(worker, "_select_model", forbidden_selection)
        payload = {"model": "auto", "messages": [{"role": "user", "content": "fake"}]}
        for _ in range(2):
            output = queue.Queue()
            await worker.complete(payload, output)
            assert [output.get_nowait()[0] for _ in range(output.qsize())] == ["ready", "chunk", "done"]
    asyncio.run(scenario())
    assert calls == ["new", "new"]


def test_worker_cancels_generation_when_client_closes_before_first_token():
    started, stopped = threading.Event(), threading.Event()
    class WaitingWorker:
        async def complete(self, payload, output):
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                stopped.set()
    loop = asyncio.new_event_loop()
    thread = threading.Thread(target=loop.run_forever, daemon=True)
    thread.start()
    server = WorkerServer({"port": 0, "api_key": "fake"}, WaitingWorker(), loop)
    serving = threading.Thread(target=server.serve_forever, daemon=True)
    serving.start()
    try:
        client = socket.create_connection(("127.0.0.1", server.server_port))
        client.sendall(b"POST /v1/chat/completions HTTP/1.1\r\nHost: localhost\r\nAuthorization: Bearer fake\r\nContent-Length: 2\r\n\r\n{}")
        assert started.wait(timeout=2)
        client.shutdown(socket.SHUT_RDWR)
        client.close()
        assert stopped.wait(timeout=2)
    finally:
        server.shutdown()
        server.server_close()
        serving.join(timeout=2)
        loop.call_soon_threadsafe(loop.stop)
        thread.join(timeout=2)
        loop.close()


class PickerDriver:
    """A browser-shaped picker whose controls respond only to native events."""
    is_connected = True

    def __init__(self, items, *, accept_selection=True, close_on_select=True):
        self.items = [dict(item, x=40, y=100 + index * 30) for index, item in enumerate(items)]
        self.accept_selection = accept_selection
        self.close_on_select = close_on_select
        self.menu_open = False
        self.events = []

    async def _js_strict(self, expression, **kwargs):
        return json.dumps({"origin": "https://chatgpt.com", "composer": True,
                           "trigger": {"x": 10, "y": 20}, "menu_open": self.menu_open,
                           "items": self.items if self.menu_open else []})

    async def _cdp(self, method, parameters):
        self.events.append((method, parameters))
        if method == "Input.dispatchKeyEvent" and parameters["type"] == "keyDown":
            self.menu_open = False
        if method != "Input.dispatchMouseEvent" or parameters["type"] != "mouseReleased":
            return {}
        if (parameters["x"], parameters["y"]) == (10, 20):
            self.menu_open = not self.menu_open
        elif self.menu_open:
            selected = next((item for item in self.items if
                             (item["x"], item["y"]) == (parameters["x"], parameters["y"])), None)
            if selected is not None and self.accept_selection:
                for item in self.items:
                    item["checked"] = item is selected
            if self.close_on_select:
                self.menu_open = False
        return {}


def picker_item(label, *, slug=None, checked=False, disabled=False):
    return {"labels": [label], "slugs": [slug] if slug else [],
            "checked": checked, "disabled": disabled}


@pytest.mark.parametrize("include_auto", [False, True])
def test_catalog_preserves_model_titles_and_ignores_malformed_entries(include_auto):
    class CatalogDriver:
        is_connected = True
        async def get_models(self):
            return [{"slug": "gpt-5-5", "title": "GPT-5.5"}, "invalid", {"slug": ""},
                    {"slug": "gpt-5-5", "title": "GPT-5.5"}] + ([{"slug": "auto"}] if include_auto else [])
    async def scenario():
        worker = BrowserWorker({})
        worker.driver = CatalogDriver()
        assert await worker._catalog() == ["gpt-5-5", "auto"]
        assert worker._model_titles == {"gpt-5-5": "GPT-5.5"}
    asyncio.run(scenario())


@pytest.mark.parametrize("close_on_select", [True, False])
def test_exact_model_selection_uses_native_clicks_and_verifies_checked_state(close_on_select):
    async def scenario():
        worker = BrowserWorker({})
        driver = PickerDriver([picker_item("GPT-6", checked=True), picker_item("GPT-5.5"),
                               picker_item("GPT-5.5 Thinking")], close_on_select=close_on_select)
        worker.driver = driver
        worker._model_titles = {"gpt-5-5": "GPT-5.5"}
        assert await worker._select_model("gpt-5-5")
        assert driver.items[1]["checked"]
        assert not driver.menu_open
        mouse = [parameters for method, parameters in driver.events if method == "Input.dispatchMouseEvent"]
        assert [event["type"] for event in mouse] == ["mouseMoved", "mousePressed", "mouseReleased"] * (
            3 if close_on_select else 2)
        assert {(event["x"], event["y"]) for event in mouse} == {(10, 20), (40, 130)}
    asyncio.run(scenario())


@pytest.mark.parametrize("items", [
    [picker_item("GPT-5.5 Thinking")],
    [picker_item("GPT-5.5Leaving on October14")],
    [picker_item("GPT-5.5", disabled=True)],
    [picker_item("GPT-5.5", slug="gpt-5.5-wm")],
    [picker_item("GPT-5.5"), picker_item("GPT-5.5")],
])
def test_model_picker_rejects_wrong_variants_disabled_and_ambiguous_matches(items):
    async def scenario():
        worker = BrowserWorker({})
        driver = PickerDriver(items)
        worker.driver = driver
        worker._model_titles = {"gpt-5-5": "GPT-5.5"}
        assert not await worker._select_model("gpt-5-5")
        clicks = [parameters for method, parameters in driver.events
                  if method == "Input.dispatchMouseEvent" and parameters["type"] == "mouseReleased"]
        assert [(event["x"], event["y"]) for event in clicks] == [(10, 20)]
        assert not driver.menu_open
    asyncio.run(scenario())


def test_shared_legacy_and_work_titles_require_exact_slug_metadata():
    worker = BrowserWorker({})
    worker._model_titles = {"gpt-5-5": "GPT-5.5", "gpt-5.5-wm": "GPT-5.5"}
    state = {"items": [picker_item("GPT-5.5", checked=True)]}
    assert worker._matching_model_item(state, "gpt-5-5") is None
    state["items"][0]["slugs"] = ["gpt-5-5"]
    assert worker._matching_model_item(state, "gpt-5-5") is state["items"][0]


def test_native_click_without_checked_state_is_not_selection_success(monkeypatch):
    monkeypatch.setattr("upool.proxy.worker._MODEL_PICKER_TIMEOUT", 0.05)
    async def scenario():
        worker = BrowserWorker({})
        worker.driver = PickerDriver([picker_item("GPT-5.5")], accept_selection=False)
        worker.driver._current_model = "gpt-5-5"  # An upstream cache is not browser proof.
        worker._model_titles = {"gpt-5-5": "GPT-5.5"}
        assert not await worker._select_model("gpt-5-5")
        assert not worker.driver.menu_open
    asyncio.run(scenario())


def test_generation_navigates_before_verified_selection_and_refuses_failed_selection(monkeypatch):
    import queue
    import sys
    import types
    from types import SimpleNamespace
    module = types.ModuleType("chatgpt_web2api.cdp_driver")
    module.AuthExpiredError = type("AuthExpiredError", (Exception,), {})
    module.RateLimitError = type("RateLimitError", (Exception,), {})
    monkeypatch.setitem(sys.modules, "chatgpt_web2api", types.ModuleType("chatgpt_web2api"))
    monkeypatch.setitem(sys.modules, "chatgpt_web2api.cdp_driver", module)
    calls = []
    class Driver:
        is_connected = True
        async def navigate_new_chat(self):
            calls.append("new")
        async def send_and_stream(self, prompt, **kwargs):
            calls.append("send")
            yield SimpleNamespace(delta="ok", finish_reason="stop")
    async def scenario():
        worker = BrowserWorker({})
        worker.driver = Driver()
        worker._models = ["gpt-5-5"]
        async def selected(slug):
            calls.append("select")
            return True
        monkeypatch.setattr(worker, "_select_model", selected)
        payload = {"model": "gpt-5-5", "messages": [{"role": "user", "content": "fake"}]}
        await worker.complete(payload, queue.Queue())
        assert calls == ["new", "select", "send"]
        calls.clear()
        async def refused(slug):
            calls.append("select")
            return False
        monkeypatch.setattr(worker, "_select_model", refused)
        output = queue.Queue()
        await worker.complete(payload, output)
        assert calls == ["new", "select"]
        kind, failure = output.get_nowait()
        assert kind == "error" and failure.status == 503
        assert output.empty()
    asyncio.run(scenario())


_DOM_FIXTURE = r"""
class Element {
  constructor(attrs = {}, text = '', children = []) {
    this.attrs = attrs; this.text = text; this.children = children;
    for (const child of children) child.parent = this;
  }
  get textContent() { return this.text + this.children.map(child => child.textContent).join(''); }
  contains(node) { return node === this || this.children.some(child => child.contains(node)); }
  matches(selector) {
    if (selector === '.markdown') return this.attrs.class === 'markdown';
    const match = selector.match(/^\[([^=\]]+)(?:="([^"]*)")?\]$/);
    return !!match && Object.hasOwn(this.attrs, match[1]) &&
      (match[2] === undefined || this.attrs[match[1]] === match[2]);
  }
  querySelectorAll(selector) {
    const matches = [];
    const selectors = selector.split(',').map(value => value.trim());
    const visit = node => {
      if (selectors.some(value => node.matches(value))) matches.push(node);
      for (const child of node.children) visit(child);
    };
    for (const child of this.children) visit(child);
    return matches;
  }
  querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
  closest(selector) {
    for (let node = this; node; node = node.parent) if (node.matches(selector)) return node;
    return null;
  }
  compareDocumentPosition(other) { return order.indexOf(other) < order.indexOf(this) ? 2 : 4; }
}
const markdown = () => new Element({'data-markdown-text-style': ''}, 'ANSWER');
const oldMessage = () => new Element({'data-message-author-role': 'assistant'}, '',
  [new Element({class: 'markdown'}, 'ANSWER')]);
const user = new Element({'data-user-message-bubble': ''}, 'PRIVATE USER');
let answer = new Element({'data-chatgpt-selection-message-id': 'current'}, '', [markdown()]);
if (variant === 'mixed') answer = new Element({'data-chatgpt-selection-message-id': 'current'}, '',
  [new Element({'data-message-author-role': 'assistant'}, '', [markdown()])]);
const assistantUnit = new Element({'data-content-search-unit-key': 'assistant'}, '', [
  new Element({'data-conversation-role': 'assistant'}, 'ASSISTANT LABEL'), answer,
  new Element({button: 'copy'}, 'ACTION CONTROL')]);
if (variant === 'ambiguous') assistantUnit.children.push(
  new Element({'data-chatgpt-selection-message-id': 'other'}, 'WRONG ANSWER'));
for (const child of assistantUnit.children) child.parent = assistantUnit;
const modernTurn = new Element({'data-turn-key': 'current'}, '', [
  new Element({'data-content-search-unit-key': 'user'}, '', [user]), assistantUnit]);
const document = new Element({}, '', variant === 'legacy' ? [oldMessage(),
  new Element({'data-message-author-role': 'user'}, 'PRIVATE USER')] : [modernTurn]);
const order = [];
const visit = node => { order.push(node); for (const child of node.children) visit(child); };
visit(document);
"""


def _evaluate_message_queries(variant, expressions):
    import shutil
    import subprocess
    from upool.proxy.worker import _message_query
    node = shutil.which("node")
    if node is None:
        pytest.skip("DOM adapter fixtures use the UI development Node runtime")
    script = ("const variant = " + json.dumps(variant) + ";\n" + _DOM_FIXTURE +
              "\nconst expressions = " + json.dumps([_message_query(expr) for expr in expressions]) +
              ";console.log(JSON.stringify(expressions.map(expr => eval(expr))));")
    return subprocess.run([node, "-e", script], capture_output=True, text=True, timeout=10)


@pytest.mark.parametrize("variant", ["modern", "legacy", "mixed"])
def test_message_adapter_selects_answer_without_role_label_user_or_action_text(variant):
    expressions = [
        'document.querySelectorAll(\'[data-message-author-role="assistant"]\').length',
        'document.querySelectorAll(\'[data-message-author-role="user"]\').length',
        # Exact whitespace produced by the pinned acknowledgment expression.
        'document.querySelectorAll(    \'[data-message-author-role="user"]\').length',
        "(function(){var msgs = document.querySelectorAll('[data-message-author-role=\"assistant\"]');"
        "var last = msgs[msgs.length - 1]; var md = last.querySelector('.markdown');"
        "return {text: last.textContent, markdown: md ? md.textContent : ''};})()",
    ]
    result = _evaluate_message_queries(variant, expressions)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == [1, 1, 1, {"text": "ANSWER", "markdown": "ANSWER"}]


def test_message_adapter_rejects_two_assistant_candidates_in_one_content_unit():
    result = _evaluate_message_queries("ambiguous", [
        'document.querySelectorAll(\'[data-message-author-role="assistant"]\').length'])
    assert result.returncode != 0
    assert "Ambiguous ChatGPT message structure" in result.stderr


def test_message_adapter_does_not_rewrite_unrelated_javascript():
    from upool.proxy.worker import _message_query
    expression = "(async()=>{return fetch('/api/auth/session');})()"
    assert _message_query(expression) == expression
    assert _message_query("window.location.href") == "window.location.href"


_PHASE2_SNAPSHOT_FIXTURE = (
    "(function(){var msgs = document.querySelectorAll('[data-message-author-role=\"assistant\"]');"
    "var last = msgs[msgs.length - 1];var md = last.querySelector('.markdown');"
    "var text = md ? md.textContent : '';var mdText = text;"
    "return JSON.stringify({text: text, md_text: mdText, html_len:60, child_count:1, "
    "has_action:false, is_thinking:false});})()")


@pytest.mark.parametrize("variant,modern", [("modern", True), ("mixed", True), ("legacy", False)])
def test_completion_snapshot_marks_only_actual_modern_message_content(variant, modern):
    result = _evaluate_message_queries(variant, [_PHASE2_SNAPSHOT_FIXTURE])
    assert result.returncode == 0, result.stderr
    state = json.loads(json.loads(result.stdout)[0])
    assert state["upool_modern_message"] is modern
    assert state["text"] == state["md_text"] == "ANSWER"


def _completion_types(monkeypatch):
    import sys
    import types
    from types import SimpleNamespace
    module = types.ModuleType("chatgpt_web2api.cdp_driver")
    class GenerationStuckError(Exception):
        def __init__(self, phase, seconds):
            self.phase, self.seconds = phase, seconds
    module.GenerationStuckError = GenerationStuckError
    module.AuthExpiredError = type("AuthExpiredError", (Exception,), {})
    module.RateLimitError = type("RateLimitError", (Exception,), {})
    module.StreamChunk = lambda **kwargs: SimpleNamespace(delta=kwargs.get("delta", ""),
                                                         finish_reason=kwargs.get("finish_reason"))
    monkeypatch.setitem(sys.modules, "chatgpt_web2api", types.ModuleType("chatgpt_web2api"))
    monkeypatch.setitem(sys.modules, "chatgpt_web2api.cdp_driver", module)
    return module


class _WaitingDetector:
    had_non_text_content = False
    def __init__(self, first=None):
        self.first = first
        self.closed = False
        self.arguments = None
    async def stream_until_complete(self, **kwargs):
        from types import SimpleNamespace
        self.arguments = kwargs
        try:
            if self.first is not None:
                yield SimpleNamespace(delta=self.first)
            await asyncio.Event().wait()
        finally:
            self.closed = True


def test_anchored_completion_returns_real_terminal_text_with_no_rendered_dom(monkeypatch):
    from types import SimpleNamespace
    from upool.proxy.worker import _AnchoredCompletion
    _completion_types(monkeypatch)
    anchor = object()
    detector = _WaitingDetector()
    class Driver:
        async def _get_live_conversation_id_best_effort(self):
            return "own-new-conversation"
        async def _fetch_text_for_turn(self, conversation_id, received_anchor):
            assert conversation_id == "own-new-conversation" and received_anchor is anchor
            return SimpleNamespace(status="matched", text="READY")
    async def scenario():
        completion = _AnchoredCompletion(Driver(), detector)
        chunks = [chunk.delta async for chunk in completion.stream_until_complete(
            initial_count=0, timeout=2, turn_anchor=anchor, model="gpt-6-thinking")]
        assert chunks == ["READY"]
        assert completion.last_dom_text == "READY"
        assert detector.closed
        assert detector.arguments["turn_anchor"] is anchor
    asyncio.run(scenario())


@pytest.mark.parametrize("status,text", [("not_ready", ""), ("ambiguous", "STALE"),
                                        ("degraded_not_fresh", "STALE"),
                                        ("fetch_failed", "STALE"), ("non_text", ""),
                                        ("matched", "")])
def test_anchored_completion_never_accepts_stale_ambiguous_or_empty_results(monkeypatch, status, text):
    from types import SimpleNamespace
    from upool.proxy.worker import _AnchoredCompletion
    module = _completion_types(monkeypatch)
    detector = _WaitingDetector()
    class Driver:
        async def _get_live_conversation_id_best_effort(self):
            return "own-new-conversation"
        async def _fetch_text_for_turn(self, conversation_id, anchor):
            return SimpleNamespace(status=status, text=text)
    async def scenario():
        completion = _AnchoredCompletion(Driver(), detector)
        with pytest.raises(module.GenerationStuckError):
            async for _ in completion.stream_until_complete(initial_count=0, timeout=0.05,
                                                             turn_anchor=object()):
                pytest.fail("An unverified backend result must not emit content")
        assert completion.last_dom_text == "" and detector.closed
    asyncio.run(scenario())


def test_anchored_completion_only_appends_matching_backend_suffix(monkeypatch):
    from types import SimpleNamespace
    from upool.proxy.worker import _AnchoredCompletion
    _completion_types(monkeypatch)
    detector = _WaitingDetector(first="HELLO")
    class Driver:
        async def _get_live_conversation_id_best_effort(self):
            return "own-new-conversation"
        async def _fetch_text_for_turn(self, conversation_id, anchor):
            return SimpleNamespace(status="matched", text="HELLO WORLD")
    async def scenario():
        completion = _AnchoredCompletion(Driver(), detector)
        assert [chunk.delta async for chunk in completion.stream_until_complete(
            initial_count=0, timeout=2, turn_anchor=object())] == ["HELLO", " WORLD"]
        assert completion.last_dom_text == "HELLO WORLD" and detector.closed
    asyncio.run(scenario())


@pytest.mark.parametrize("rendered,canonical", [
    ("print('ok')", "```python\nprint('ok')\n```"),
    ("TitleFirst itemSecond item", "**Title**\n\n- First item\n- Second item"),
])
def test_modern_dom_formatting_is_buffered_until_canonical_anchored_answer(monkeypatch, rendered, canonical):
    from types import SimpleNamespace
    from upool.proxy.worker import _compatible_driver_type
    _completion_types(monkeypatch)
    anchor = object()
    class FormattingDetector(_WaitingDetector):
        def __init__(self, driver):
            super().__init__()
            self.driver = driver
        async def stream_until_complete(self, **kwargs):
            try:
                state = json.loads(await self.driver._js_strict(_PHASE2_SNAPSHOT_FIXTURE))
                # A real MarkdownRoot renders text without source Markdown.
                yield SimpleNamespace(delta=state["text"])
                await asyncio.Event().wait()
            finally:
                self.closed = True
    class BaseDriver:
        def __init__(self):
            self._completion = FormattingDetector(self)
        async def _js_strict(self, expression, timeout=15):
            assert "upool_modern_message: __upoolModernMessageQuery" in expression
            return json.dumps({"text": rendered, "upool_modern_message": True})
        async def _get_live_conversation_id_best_effort(self):
            return "own-code-conversation"
        async def _fetch_text_for_turn(self, conversation_id, received_anchor):
            assert conversation_id == "own-code-conversation" and received_anchor is anchor
            return SimpleNamespace(status="matched", text=canonical)
    async def scenario():
        driver = _compatible_driver_type(BaseDriver)()
        assert [chunk.delta async for chunk in driver._completion.stream_until_complete(
            initial_count=0, timeout=2, turn_anchor=anchor)] == [canonical]
        assert driver._completion.last_dom_text == canonical
        assert driver._completion.detector.closed
    asyncio.run(scenario())


def test_modern_flag_is_reset_before_a_following_legacy_request(monkeypatch):
    from types import SimpleNamespace
    from upool.proxy.worker import _AnchoredCompletion
    _completion_types(monkeypatch)
    detector = _WaitingDetector(first="LEGACY")
    class Driver:
        async def _get_live_conversation_id_best_effort(self):
            return "own-new-conversation"
        async def _fetch_text_for_turn(self, conversation_id, anchor):
            return SimpleNamespace(status="matched", text="LEGACY ANSWER")
    async def scenario():
        driver = Driver()
        completion = _AnchoredCompletion(driver, detector)
        driver._upool_modern_messages = True  # Previous request's modern state.
        assert [chunk.delta async for chunk in completion.stream_until_complete(
            initial_count=0, timeout=2, turn_anchor=object())] == ["LEGACY", " ANSWER"]
        assert driver._upool_modern_messages is False
    asyncio.run(scenario())


def test_modern_dom_completion_preserves_empty_public_baseline_for_driver_reconciliation(monkeypatch):
    from types import SimpleNamespace
    from upool.proxy.worker import _AnchoredCompletion
    _completion_types(monkeypatch)
    class Driver:
        async def _get_live_conversation_id_best_effort(self):
            return "own-new-conversation"
        async def _fetch_text_for_turn(self, conversation_id, anchor):
            return SimpleNamespace(status="not_ready", text="")
    class FinishedDetector:
        had_non_text_content = False
        async def stream_until_complete(self, **kwargs):
            driver._upool_modern_messages = True
            yield SimpleNamespace(delta="CODE WITHOUT FENCES")
    async def scenario():
        completion = _AnchoredCompletion(driver, FinishedDetector())
        assert [chunk.delta async for chunk in completion.stream_until_complete(
            initial_count=0, timeout=2, turn_anchor=object())] == []
        # The pinned driver final reconciliation then emits the WHOLE raw
        # backend text, because none of the rendered DOM answer was public.
        assert completion.last_dom_text == ""
    driver = Driver()
    asyncio.run(scenario())


def test_anchored_completion_rejects_backend_rewrite_after_public_output(monkeypatch):
    from types import SimpleNamespace
    from upool.proxy.worker import _AnchoredCompletion
    _completion_types(monkeypatch)
    detector = _WaitingDetector(first="PUBLIC ANSWER")
    class Driver:
        async def _get_live_conversation_id_best_effort(self):
            return "own-new-conversation"
        async def _fetch_text_for_turn(self, conversation_id, anchor):
            return SimpleNamespace(status="matched", text="DIFFERENT ANSWER")
    async def scenario():
        completion = _AnchoredCompletion(Driver(), detector)
        chunks = []
        with pytest.raises(WorkerFailure, match="already streamed"):
            async for chunk in completion.stream_until_complete(initial_count=0, timeout=2,
                                                                 turn_anchor=object()):
                chunks.append(chunk.delta)
        assert chunks == ["PUBLIC ANSWER"] and detector.closed
    asyncio.run(scenario())


def test_anchored_completion_preserves_auth_expiry_and_cleans_pending_detector(monkeypatch):
    from upool.proxy.worker import _AnchoredCompletion
    module = _completion_types(monkeypatch)
    detector = _WaitingDetector()
    class Driver:
        async def _get_live_conversation_id_best_effort(self):
            return "own-new-conversation"
        async def _fetch_text_for_turn(self, conversation_id, anchor):
            raise module.AuthExpiredError()
    async def scenario():
        completion = _AnchoredCompletion(Driver(), detector)
        with pytest.raises(module.AuthExpiredError):
            async for _ in completion.stream_until_complete(initial_count=0, timeout=2,
                                                             turn_anchor=object()):
                pytest.fail("Authentication errors must not become success")
        assert detector.closed
    asyncio.run(scenario())


def test_anchored_completion_deadline_bounds_stalled_backend_fetch(monkeypatch):
    import time
    from upool.proxy.worker import _AnchoredCompletion
    module = _completion_types(monkeypatch)
    detector = _WaitingDetector()
    class Driver:
        async def _get_live_conversation_id_best_effort(self):
            return "own-new-conversation"
        async def _fetch_text_for_turn(self, conversation_id, anchor):
            await asyncio.Event().wait()
    async def scenario():
        started = time.monotonic()
        completion = _AnchoredCompletion(Driver(), detector)
        with pytest.raises(module.GenerationStuckError):
            async for _ in completion.stream_until_complete(initial_count=0, timeout=0.1,
                                                             turn_anchor=object()):
                pass
        assert time.monotonic() - started < 0.5 and detector.closed
    asyncio.run(scenario())


def test_anchored_completion_cancellation_closes_observation_and_backend_tasks(monkeypatch):
    from upool.proxy.worker import _AnchoredCompletion
    _completion_types(monkeypatch)
    detector = _WaitingDetector()
    reading = asyncio.Event()
    class Driver:
        async def _get_live_conversation_id_best_effort(self):
            return "own-new-conversation"
        async def _fetch_text_for_turn(self, conversation_id, anchor):
            reading.set()
            await asyncio.Event().wait()
    async def scenario():
        completion = _AnchoredCompletion(Driver(), detector)
        stream = completion.stream_until_complete(initial_count=0, timeout=10, turn_anchor=object())
        task = asyncio.create_task(anext(stream))
        await asyncio.wait_for(reading.wait(), timeout=2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await stream.aclose()
        assert detector.closed
        assert [task for task in asyncio.all_tasks() if task is not asyncio.current_task()] == []
    asyncio.run(scenario())


def test_compatible_driver_changes_only_owned_instance_queries(monkeypatch):
    from upool.proxy.worker import _AnchoredCompletion, _compatible_driver_type
    class BaseDriver:
        def __init__(self):
            self._completion = object()
            self.queries = []
        async def _js_strict(self, expression, timeout=15):
            self.queries.append((expression, timeout))
            return "answer"
    async def scenario():
        legacy = BaseDriver()
        owned = _compatible_driver_type(BaseDriver)()
        expression = 'document.querySelectorAll(\'[data-message-author-role="assistant"]\').length'
        assert await owned._js_strict(expression, timeout=7) == "answer"
        assert isinstance(owned._completion, _AnchoredCompletion)
        assert owned.queries[0][1] == 7 and "__upoolMessageNodes" in owned.queries[0][0]
        await legacy._js_strict(expression)
        assert legacy.queries == [(expression, 15)]
    asyncio.run(scenario())


def _fresh_state(**overrides):
    return dict({"origin": "https://chatgpt.com", "pathname": "/", "main": True,
                 "previous": 0, "composer": True}, **overrides)


_PROJECTION_FIXTURE = """(async function() {
  var r = await fetch('/backend-api/conversation/' + __D.conv_id, {
    headers: {'Authorization': 'Bearer ' + __D.token}
  });
  if (!r.ok) return JSON.stringify({__status: r.status});
  return JSON.stringify(await r.json());
})()"""


def _projection_driver(monkeypatch):
    import sys
    import types
    from upool.proxy.worker import _compatible_driver_type
    module = types.ModuleType("chatgpt_web2api.backend_projection")
    module.CONVERSATION_PROJECTION_JS = _PROJECTION_FIXTURE
    monkeypatch.setitem(sys.modules, "chatgpt_web2api.backend_projection", module)
    class BaseDriver:
        def __init__(self):
            self._completion = object()
            self.calls = []
            self.status = 200
            self.retry_after = "121"
            self.block = False
            self.reading = asyncio.Event()
            self.cancelled = False
        async def _js_with_data_strict(self, expression, data, timeout=15):
            self.calls.append((expression, data, timeout))
            self.reading.set()
            if self.block:
                try:
                    await asyncio.Event().wait()
                finally:
                    self.cancelled = True
            if self.status != 200:
                return json.dumps({"__status": self.status, "__retry_after": self.retry_after,
                                   "ignored_body": "PRIVATE-PROMPT-TOKEN-ERROR-BODY"})
            return json.dumps({"nodes": {}, "current_node": None})
        async def _js_strict(self, expression, timeout=15):
            return json.dumps(_fresh_state())
        async def navigate_new_chat(self):
            pass
        async def _refresh_token(self):
            pass
    return _compatible_driver_type(BaseDriver)()


@pytest.mark.parametrize("value,expected", [
    ("121", 121), ("1.1", 2), ("0", 0), ("-1", 0),
    ("Thu, 01 Jan 1970 00:18:41 GMT", 121),
    (None, 60), ("", 60), ("invalid", 60), ("nan", 60), ("inf", 60),
])
def test_worker_retry_after_handles_numeric_date_and_unknown_values(monkeypatch, value, expected):
    from upool.proxy.worker import _worker_retry_after
    monkeypatch.setattr("upool.proxy.worker.time.time", lambda: 1000)
    assert _worker_retry_after(value) == expected


@pytest.mark.parametrize("status,public_status", [(429, 429), (400, 400), (413, 400),
                                                  (422, 400), (403, 403), (500, 503)])
def test_projection_hard_failures_preserve_status_without_provider_body(monkeypatch, status, public_status):
    driver = _projection_driver(monkeypatch)
    driver.status = status
    async def scenario():
        with pytest.raises(WorkerFailure) as failure:
            await driver._js_with_data_strict(_PROJECTION_FIXTURE,
                                             {"conv_id": "own-conversation", "token": "fake-private-token"})
        assert failure.value.status == public_status
        assert failure.value.retry_after == (121 if status == 429 else None)
        assert "PRIVATE" not in str(failure.value) and "own-conversation" not in str(failure.value)
        assert driver._upool_backend_failure is failure.value
        expression, _, timeout = driver.calls[0]
        assert expression.count("fetch(") == 1 and "r.headers.get('Retry-After')" in expression
        assert "AbortSignal.timeout(4500)" in expression and timeout == 5
    asyncio.run(scenario())


@pytest.mark.parametrize("status", [401, 404])
def test_projection_auth_and_transient_missing_status_keep_pinned_semantics(monkeypatch, status):
    driver = _projection_driver(monkeypatch)
    driver.status = status
    async def scenario():
        raw = await driver._js_with_data_strict(_PROJECTION_FIXTURE, {"conv_id": "own-conversation"})
        assert json.loads(raw)["__status"] == status
        assert driver._upool_backend_failure is None
    asyncio.run(scenario())


def test_concurrent_text_end_observers_share_one_get_and_retry_only_after_interval(monkeypatch):
    driver = _projection_driver(monkeypatch)
    monkeypatch.setattr("upool.proxy.worker._TURN_POLL_INTERVAL", 0.02)
    async def scenario():
        data = {"conv_id": "own-conversation"}
        text, end = await asyncio.gather(driver._js_with_data_strict(_PROJECTION_FIXTURE, data),
                                        driver._js_with_data_strict(_PROJECTION_FIXTURE, data))
        assert text == end and len(driver.calls) == 1
        await driver._js_with_data_strict(_PROJECTION_FIXTURE, {"conv_id": "different-new-conversation"})
        assert len(driver.calls) == 2
        await asyncio.sleep(0.03)
        await driver._js_with_data_strict(_PROJECTION_FIXTURE, {"conv_id": "different-new-conversation"})
        assert len(driver.calls) == 3
    asyncio.run(scenario())


@pytest.mark.parametrize("reset", ["navigate_new_chat", "_refresh_token"])
def test_new_generation_or_successful_reauth_clears_prior_hard_failure(monkeypatch, reset):
    driver = _projection_driver(monkeypatch)
    driver.status = 429
    async def scenario():
        with pytest.raises(WorkerFailure):
            await driver._js_with_data_strict(_PROJECTION_FIXTURE, {"conv_id": "own-conversation"})
        driver.status = 200
        await getattr(driver, reset)()
        assert driver._upool_backend_failure is None and driver._upool_projection_cache is None
        assert json.loads(await driver._js_with_data_strict(
            _PROJECTION_FIXTURE, {"conv_id": "own-conversation"}))["nodes"] == {}
        assert len(driver.calls) == 2
    asyncio.run(scenario())


def test_cancelled_projection_releases_lock_and_does_not_cache_incomplete_read(monkeypatch):
    driver = _projection_driver(monkeypatch)
    driver.block = True
    async def scenario():
        task = asyncio.create_task(driver._js_with_data_strict(_PROJECTION_FIXTURE, {"conv_id": "own"}))
        await asyncio.wait_for(driver.reading.wait(), timeout=1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert driver.cancelled and driver._upool_projection_cache is None
        driver.block = False
        raw = await asyncio.wait_for(driver._js_with_data_strict(_PROJECTION_FIXTURE, {"conv_id": "own"}), 1)
        assert json.loads(raw)["nodes"] == {} and len(driver.calls) == 2
        assert [task for task in asyncio.all_tasks() if task is not asyncio.current_task()] == []
    asyncio.run(scenario())


def test_projection_timeout_bounds_both_transport_and_lock_cleanup(monkeypatch):
    driver = _projection_driver(monkeypatch)
    driver.block = True
    monkeypatch.setattr("upool.proxy.worker._PROJECTION_TIMEOUT", 0.02)
    async def scenario():
        with pytest.raises(asyncio.TimeoutError):
            await driver._js_with_data_strict(_PROJECTION_FIXTURE, {"conv_id": "own"})
        assert driver.cancelled and not driver._upool_projection_lock.locked()
        assert driver._upool_projection_cache is None
    asyncio.run(scenario())


def test_completion_surfaces_hard_error_swallowed_by_pinned_end_observer(monkeypatch):
    from upool.proxy.worker import _AnchoredCompletion
    _completion_types(monkeypatch)
    driver = _projection_driver(monkeypatch)
    driver.status = 429
    closed = False
    class CatchingDetector:
        had_non_text_content = False
        async def stream_until_complete(self, **kwargs):
            nonlocal closed
            try:
                try:
                    await driver._js_with_data_strict(_PROJECTION_FIXTURE, {"conv_id": "own"})
                except Exception:
                    pass  # The pinned detector degrades generic read errors.
                await asyncio.Event().wait()
                yield
            finally:
                closed = True
    async def no_conversation():
        return ""
    driver._get_live_conversation_id_best_effort = no_conversation
    async def scenario():
        completion = _AnchoredCompletion(driver, CatchingDetector())
        with pytest.raises(WorkerFailure) as failure:
            async for _ in completion.stream_until_complete(initial_count=0, timeout=2,
                                                             turn_anchor=object()):
                pytest.fail("Quota must fail before any public output")
        assert failure.value.status == 429 and failure.value.retry_after == 121 and closed
        assert len(driver.calls) == 1
        assert [task for task in asyncio.all_tasks() if task is not asyncio.current_task()] == []
    asyncio.run(scenario())


@pytest.mark.parametrize("yields", [False, True])
def test_completion_rechecks_latched_error_before_pinned_yield_or_normal_end(monkeypatch, yields):
    from types import SimpleNamespace
    from upool.proxy.worker import _AnchoredCompletion
    _completion_types(monkeypatch)
    failure = WorkerFailure("ChatGPT usage limit reached.", 429, 90)
    class Driver:
        async def _get_live_conversation_id_best_effort(self):
            return ""
    driver = Driver()
    closed = False
    class CatchingDetector:
        had_non_text_content = False
        async def stream_until_complete(self, **kwargs):
            nonlocal closed
            try:
                await asyncio.sleep(0)
                driver._upool_backend_failure = failure
                if yields:
                    yield SimpleNamespace(delta="UNVERIFIED OUTPUT")
            finally:
                closed = True
    async def scenario():
        completion = _AnchoredCompletion(driver, CatchingDetector())
        with pytest.raises(WorkerFailure) as error:
            async for _ in completion.stream_until_complete(initial_count=0, timeout=2,
                                                             turn_anchor=object()):
                pytest.fail("A late quota error must prevent output and normal termination")
        assert error.value is failure and closed
        assert [task for task in asyncio.all_tasks() if task is not asyncio.current_task()] == []
    asyncio.run(scenario())


def _submit_event(driver, method, **params):
    driver._cdp_event_handlers[method]({"method": method, "params": params})


def _submit_request(driver, request_id="owned-post", *, method="POST",
                    url="https://chatgpt.com/backend-api/f/conversation"):
    _submit_event(driver, "Network.requestWillBeSent", requestId=request_id,
                  request={"method": method, "url": url, "postData": "PRIVATE BODY NEVER READ"})


def _submit_response(driver, request_id="owned-post", *, status=429, delay="120",
                     url="https://chatgpt.com/backend-api/f/conversation"):
    _submit_event(driver, "Network.responseReceived", requestId=request_id,
                  response={"status": status, "url": url,
                            "headers": {"ReTrY-AfTeR": delay, "Set-Cookie": "PRIVATE COOKIE NEVER READ"}})


@pytest.mark.parametrize("status,delay,expected", [
    (429, "120", 120), (429, "0", 0), (429, None, 60), (429, "invalid", 60),
    (429, "Thu, 01 Jan 1970 00:18:41 GMT", 121), (400, None, None), (413, None, None),
    (404, None, None),
])
@pytest.mark.parametrize("response_first", [False, True])
def test_owned_post_status_works_before_conversation_exists_and_waits_for_verified_capture(
        monkeypatch, status, delay, expected, response_first):
    from types import SimpleNamespace
    from upool.proxy.worker import _SubmitStatus
    monkeypatch.setattr("upool.proxy.worker.time.time", lambda: 1000)
    async def scenario():
        previous_calls = []
        previous = lambda event: previous_calls.append(event["method"])
        driver = SimpleNamespace(_cdp_event_handlers={"Network.requestWillBeSent": previous},
                                 _upool_backend_failure=None)
        scope = SimpleNamespace(_closed=False, future=asyncio.get_running_loop().create_future())
        observer = _SubmitStatus(driver, scope)
        try:
            _submit_request(driver)
            if response_first:
                _submit_response(driver, status=status, delay=delay)
                assert driver._upool_backend_failure is None  # Same route is insufficient.
            scope.future.set_result(SimpleNamespace(uuid="validated-own-uuid", request_id="owned-post"))
            if not response_first:
                _submit_response(driver, status=status, delay=delay)
            await asyncio.sleep(0)
            failure = driver._upool_backend_failure
            public_status = 429 if status == 429 else 503 if status == 404 else 400
            assert failure.status == public_status and failure.retry_after == expected
            assert "PRIVATE" not in str(failure)
            assert not hasattr(driver, "_current_conv_id")
            assert previous_calls == ["Network.requestWillBeSent"]
        finally:
            observer.close()
        assert driver._cdp_event_handlers == {"Network.requestWillBeSent": previous}
        assert not observer.candidates and not observer.failures
    asyncio.run(scenario())


@pytest.mark.parametrize("method,url,captured_id,response_id,response_url", [
    ("GET", "https://chatgpt.com/backend-api/f/conversation", "owned-post", "owned-post", None),
    ("POST", "https://chatgpt.com.attacker.invalid/backend-api/f/conversation", "owned-post", "owned-post", None),
    ("POST", "http://chatgpt.com/backend-api/f/conversation", "owned-post", "owned-post", None),
    ("POST", "https://chatgpt.com:444/backend-api/f/conversation", "owned-post", "owned-post", None),
    ("POST", "https://chatgpt.com/backend-api/conversations", "owned-post", "owned-post", None),
    ("POST", "https://chatgpt.com/backend-api/f/conversation", "different-verified-post", "owned-post", None),
    ("POST", "https://chatgpt.com/backend-api/f/conversation", "owned-post", "late-prior-post", None),
    ("POST", "https://chatgpt.com/backend-api/f/conversation", "owned-post", "owned-post",
     "https://other.invalid/backend-api/f/conversation"),
])
def test_submit_status_ignores_other_method_host_route_capture_and_prior_request_ids(
        method, url, captured_id, response_id, response_url):
    from types import SimpleNamespace
    from upool.proxy.worker import _SubmitStatus
    async def scenario():
        driver = SimpleNamespace(_cdp_event_handlers={}, _upool_backend_failure=None)
        scope = SimpleNamespace(_closed=False, future=asyncio.get_running_loop().create_future())
        observer = _SubmitStatus(driver, scope)
        try:
            _submit_request(driver, method=method, url=url)
            scope.future.set_result(SimpleNamespace(uuid="validated-uuid", request_id=captured_id))
            _submit_response(driver, response_id, url=response_url or "https://chatgpt.com/backend-api/f/conversation")
            await asyncio.sleep(0)
            assert driver._upool_backend_failure is None
        finally:
            observer.close()
    asyncio.run(scenario())


def test_submit_redirect_to_get_and_late_closed_scope_cannot_poison_next_request():
    from types import SimpleNamespace
    from upool.proxy.worker import _SubmitStatus
    async def scenario():
        driver = SimpleNamespace(_cdp_event_handlers={}, _upool_backend_failure=None)
        scope = SimpleNamespace(_closed=False, future=asyncio.get_running_loop().create_future())
        observer = _SubmitStatus(driver, scope)
        _submit_request(driver)
        _submit_request(driver, method="GET")  # Same CDP ID after a method-changing redirect.
        scope.future.set_result(SimpleNamespace(uuid="validated-uuid", request_id="owned-post"))
        _submit_response(driver)
        await asyncio.sleep(0)
        assert driver._upool_backend_failure is None
        stale_dispatch = driver._cdp_event_handlers["Network.responseReceived"]
        observer.close()
        next_scope = SimpleNamespace(_closed=False, future=asyncio.get_running_loop().create_future())
        next_observer = _SubmitStatus(driver, next_scope)
        try:
            _submit_request(driver, "new-post")
            next_scope.future.set_result(SimpleNamespace(uuid="new-validated-uuid", request_id="new-post"))
            _submit_response(driver, "owned-post")
            stale_dispatch({"params": {"requestId": "owned-post", "response": {"status": 429}}})
            await asyncio.sleep(0)
            assert driver._upool_backend_failure is None
        finally:
            next_observer.close()
        assert driver._cdp_event_handlers == {}
    asyncio.run(scenario())


def test_owned_post_auth_failure_and_network_failure_preserve_safe_types(monkeypatch):
    from types import SimpleNamespace
    from upool.proxy.worker import _SubmitStatus
    module = _completion_types(monkeypatch)
    async def scenario():
        for status in (401, None):
            driver = SimpleNamespace(_cdp_event_handlers={}, _upool_backend_failure=None)
            scope = SimpleNamespace(_closed=False, future=asyncio.get_running_loop().create_future())
            observer = _SubmitStatus(driver, scope)
            try:
                _submit_request(driver)
                scope.future.set_result(SimpleNamespace(uuid="validated-uuid", request_id="owned-post"))
                if status:
                    _submit_response(driver, status=status)
                    assert isinstance(driver._upool_backend_failure, module.AuthExpiredError)
                else:
                    _submit_event(driver, "Network.loadingFailed", requestId="unrelated", errorText="PRIVATE")
                    assert driver._upool_backend_failure is None
                    _submit_event(driver, "Network.loadingFailed", requestId="owned-post", errorText="PRIVATE")
                    assert driver._upool_backend_failure.status == 503
                    assert "PRIVATE" not in str(driver._upool_backend_failure)
            finally:
                observer.close()
    asyncio.run(scenario())


@pytest.mark.parametrize("termination", ["success", "quota", "invalid", "cancel", "disconnect"])
def test_driver_owned_post_observer_closes_on_every_send_terminal_path(monkeypatch, termination):
    from types import SimpleNamespace
    from upool.proxy.worker import _compatible_driver_type
    _completion_types(monkeypatch)
    class BaseDriver:
        def __init__(self):
            self._completion = _WaitingDetector()
            self._cdp_event_handlers = {}
            self._identity_listener = SimpleNamespace(_active_scope=None)
            self.waiting = asyncio.Event()
            self.base_closed = False
        async def click_send(self):
            _submit_request(self)
            scope = self._identity_listener._active_scope
            scope.future.set_result(SimpleNamespace(uuid="verified-own-uuid", request_id="owned-post"))
            _submit_response(self, status={"quota": 429, "invalid": 400}.get(termination, 200))
        async def send_and_stream(self, text, **kwargs):
            scope = SimpleNamespace(_closed=False, future=asyncio.get_running_loop().create_future())
            self._identity_listener._active_scope = scope
            try:
                await self.click_send()
                if termination == "disconnect":
                    raise ConnectionError("PRIVATE CDP ERROR")
                if termination == "cancel":
                    self.waiting.set()
                    await asyncio.Event().wait()
                yield SimpleNamespace(delta="SUCCESS")
            finally:
                scope._closed = True
                self.base_closed = True
        async def close(self):
            pass
    async def scenario():
        driver = _compatible_driver_type(BaseDriver)()
        stream = driver.send_and_stream("synthetic prompt")
        if termination == "success":
            assert [chunk.delta async for chunk in stream] == ["SUCCESS"]
        elif termination in {"quota", "invalid"}:
            with pytest.raises(WorkerFailure) as error:
                await anext(stream)
            assert error.value.status == (429 if termination == "quota" else 400)
        elif termination == "disconnect":
            with pytest.raises(ConnectionError):
                await anext(stream)
        else:
            task = asyncio.create_task(anext(stream))
            await asyncio.wait_for(driver.waiting.wait(), 1)
            await driver.close()  # Disconnect/close removes handlers even while a send awaits.
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        await stream.aclose()
        assert driver._cdp_event_handlers == {} and driver._upool_submit_observer is None
        assert not driver._upool_request_active and driver.base_closed
        assert [task for task in asyncio.all_tasks() if task is not asyncio.current_task()] == []
    asyncio.run(scenario())


def test_worker_error_http_preserves_explicit_zero_retry_after():
    import io
    from types import SimpleNamespace
    from upool.proxy.worker import WorkerHandler
    headers = {}
    handler = SimpleNamespace(send_response=lambda status: None,
                              send_header=lambda name, value: headers.update({name: value}),
                              end_headers=lambda: None, wfile=io.BytesIO())
    WorkerHandler._json(handler, {"error": "rate limited"}, 429, 0)
    assert headers["Retry-After"] == "0"


def test_completion_preserves_submit_failure_observed_before_it_starts(monkeypatch):
    from types import SimpleNamespace
    from upool.proxy.worker import _AnchoredCompletion
    _completion_types(monkeypatch)
    failure = WorkerFailure("ChatGPT rejected the generation request.", 400)
    driver = SimpleNamespace(_upool_backend_failure=failure)
    async def scenario():
        completion = _AnchoredCompletion(driver, _WaitingDetector())
        with pytest.raises(WorkerFailure) as error:
            await anext(completion.stream_until_complete(initial_count=0, timeout=2,
                                                        turn_anchor=object()))
        assert error.value is failure
        assert [task for task in asyncio.all_tasks() if task is not asyncio.current_task()] == []
    asyncio.run(scenario())


def test_late_projection_failure_and_mid_request_refresh_cannot_replace_first_post_error(monkeypatch):
    driver = _projection_driver(monkeypatch)
    first = WorkerFailure("ChatGPT rejected the generation request.", 400)
    driver._upool_backend_failure = first
    driver._upool_request_active = True
    driver.status = 429
    async def scenario():
        with pytest.raises(WorkerFailure) as later:
            await driver._js_with_data_strict(_PROJECTION_FIXTURE, {"conv_id": "owned"})
        assert later.value is first and driver._upool_backend_failure is first
        await driver._refresh_token()
        assert driver._upool_backend_failure is first
        driver._upool_request_active = False
        await driver._refresh_token()
        assert driver._upool_backend_failure is None and driver._upool_projection_cache is None
    asyncio.run(scenario())


@pytest.mark.parametrize("state", [
    _fresh_state(pathname="/c/retained", previous=2),
    _fresh_state(previous=1),
    _fresh_state(origin="https://chatgpt.com.attacker.invalid"),
    _fresh_state(main=False, previous=None),
    _fresh_state(composer=False),
])
def test_retained_or_unverified_conversation_cannot_start_generation(monkeypatch, state):
    import queue
    from upool.proxy.worker import _compatible_driver_type
    _completion_types(monkeypatch)
    monkeypatch.setattr("upool.proxy.worker._FRESH_CHAT_TIMEOUT", 0.02)
    class BaseDriver:
        is_connected = True
        def __init__(self):
            self._completion = object()
        async def navigate_new_chat(self):
            pass  # Simulate a failed navigation that retains an old chat.
        async def _js_strict(self, expression, timeout=15):
            assert "location.pathname" in expression
            return json.dumps(state)
        async def send_and_stream(self, prompt, **kwargs):
            pytest.fail("No prompt may be sent to an unverified/retained conversation")
            yield
    async def scenario():
        worker = BrowserWorker({})
        worker.driver = _compatible_driver_type(BaseDriver)()
        worker._models = ["auto"]
        output = queue.Queue()
        await worker.complete({"model": "auto", "messages": [{"role": "user", "content": "private"}]}, output)
        kind, failure = output.get_nowait()
        assert kind == "error" and failure.status == 503
        assert str(failure) == "Could not open a fresh ChatGPT conversation."
        assert output.empty()
    asyncio.run(scenario())


def test_fresh_chat_guard_waits_for_empty_home_before_accepting(monkeypatch):
    from upool.proxy.worker import _compatible_driver_type
    monkeypatch.setattr("upool.proxy.worker._FRESH_CHAT_TIMEOUT", 0.5)
    class BaseDriver:
        def __init__(self):
            self._completion = object()
            self._current_conv_id = "prior-chat"
            self.snapshots = [_fresh_state(pathname="/c/prior", previous=2), _fresh_state()]
            self.navigations = 0
        async def navigate_new_chat(self):
            self.navigations += 1
        async def _js_strict(self, expression, timeout=15):
            return json.dumps(self.snapshots.pop(0))
    async def scenario():
        driver = _compatible_driver_type(BaseDriver)()
        driver._upool_modern_messages = True
        await driver.navigate_new_chat()
        assert driver.navigations == 1 and driver.snapshots == []
        assert driver._current_conv_id is None and driver._upool_modern_messages is False
    asyncio.run(scenario())


def test_fresh_chat_guard_preserves_cancellation():
    from upool.proxy.worker import _compatible_driver_type
    class BaseDriver:
        def __init__(self):
            self._completion = object()
        async def navigate_new_chat(self):
            pass
        async def _js_strict(self, expression, timeout=15):
            reading.set()
            await asyncio.Event().wait()
    async def scenario():
        driver = _compatible_driver_type(BaseDriver)()
        task = asyncio.create_task(driver.navigate_new_chat())
        await reading.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    reading = asyncio.Event()
    asyncio.run(scenario())
