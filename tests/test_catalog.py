from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from upool import adapters, catalog, paths
from upool.api import Api
from upool.models import Provider, UPoolError


@pytest.fixture
def endpoint_server():
    requests = []
    replies = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            requests.append((self.path, dict(self.headers)))
            status, payload, headers = replies.pop(0)
            self.send_response(status)
            for key, value in headers.items():
                self.send_header(key, value)
            self.end_headers()
            self.wfile.write(payload if isinstance(payload, bytes) else json.dumps(payload).encode())

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}", replies, requests
    server.shutdown()
    server.server_close()
    thread.join()


def test_complete_catalog_preserves_ids_and_does_not_filter_standard_models(endpoint_server):
    url, replies, requests = endpoint_server
    replies.append((200, {"data": [
        {"id": "glm-5.3-abliterated", "context_length": 1048576, "abliterated": True},
        {"id": "minimax-m3.1-flash", "abliterated": False, "display_name": "MiniMax"},
        {"id": "glm-5.3-abliterated", "context_length": 1048576},
    ]}, {}))
    result = catalog.fetch(Provider(base_url=url, api_key="test-key"))
    assert result["models"] == [
        {"id": "glm-5.3-abliterated", "name": "glm-5.3-abliterated", "context_length": 1048576},
        {"id": "minimax-m3.1-flash", "name": "MiniMax", "context_length": None},
    ]
    assert requests[0][0] == "/v1/models"
    assert requests[0][1]["Authorization"] == "Bearer test-key"


def test_reads_all_anthropic_pages(endpoint_server):
    url, replies, requests = endpoint_server
    replies.extend([
        (200, {"data": [{"id": "one"}], "has_more": True, "last_id": "one"}, {}),
        (200, {"data": [{"id": "two"}], "has_more": False}, {}),
    ])
    result = catalog.fetch(Provider(base_url=url, api_key="test-key", auth_style="api_key"))
    assert [m["id"] for m in result["models"]] == ["one", "two"]
    assert requests[1][0] == "/v1/models?after_id=one"
    assert requests[0][1]["X-Api-Key"] == "test-key"


@pytest.mark.parametrize("payload", [b"not json", {"error": "bad"}, {"data": [{}]}, {"data": [None]}])
def test_bad_catalogues_are_errors(endpoint_server, payload):
    url, replies, _ = endpoint_server
    replies.append((200, payload, {}))
    with pytest.raises(UPoolError):
        catalog.fetch(Provider(base_url=url))


def test_empty_catalogue_is_not_replaced_with_static_defaults(endpoint_server):
    url, replies, _ = endpoint_server
    replies.append((200, {"data": []}, {}))
    assert catalog.fetch(Provider(base_url=url)) == {"models": []}


@pytest.mark.parametrize("status, message", [(401, "key rejected"), (403, "Access denied"), (404, "manually"), (429, "rate limiting"), (503, "HTTP 503")])
def test_errors_are_actionable_and_never_echo_credentials(endpoint_server, status, message):
    url, replies, _ = endpoint_server
    replies.append((status, {"error": "test-secret-key"}, {}))
    with pytest.raises(UPoolError, match=message) as error:
        catalog.fetch(Provider(base_url=url, api_key="test-secret-key"))
    assert "test-secret-key" not in str(error.value)


def test_redirects_do_not_forward_the_key(endpoint_server):
    url, replies, requests = endpoint_server
    replies.append((302, {}, {"Location": f"{url}/elsewhere"}))
    with pytest.raises(UPoolError, match="HTTP 302"):
        catalog.fetch(Provider(base_url=url, api_key="test-key"))
    assert len(requests) == 1


def test_broken_pagination_never_returns_a_partial_catalogue(endpoint_server):
    url, replies, _ = endpoint_server
    replies.extend([(200, {"data": [{"id": "one"}], "has_more": True, "last_id": "one"}, {})] * 2)
    with pytest.raises(UPoolError, match="cursor"):
        catalog.fetch(Provider(base_url=url))


@pytest.mark.parametrize("app, fields", [
    ("claude", {"base_url": "https://api.refuseless.com"}),
    ("claude_desktop", {"base_url": "https://api.refuseless.com"}),
    ("codex", {"base_url": "https://api.refuseless.com/v1"}),
    ("hermes", {"base_url": "https://api.refuseless.com/v1", "transport": "chat_completions"}),
    ("opencode", {"base_url": "https://api.refuseless.com/v1", "npm": "@ai-sdk/openai-compatible"}),
])
def test_refuseless_protocols_share_one_authenticated_catalogue(app, fields):
    provider = Provider(app=app, api_key="test-key", **fields)
    url, headers = adapters.get(app).health_target(provider)
    assert url == "https://api.refuseless.com/v1/models"
    assert headers["authorization"] == "Bearer test-key"


def test_unsaved_draft_catalogue_does_not_save_or_activate(endpoint_server):
    url, replies, _ = endpoint_server
    api = Api()
    before = api.list_providers("claude")
    live = paths.home() / ".claude" / "settings.json"
    assert not live.exists()
    replies.append((200, {"data": [{"id": "glm-5.3-abliterated"}]}, {}))
    result = api.list_provider_models({"app": "claude", "base_url": url, "api_key": "test-key"})
    assert result["ok"]
    assert result["data"]["models"][0]["id"] == "glm-5.3-abliterated"
    assert api.list_providers("claude") == before
    assert not live.exists()


def test_no_endpoint_is_a_readable_bridge_error():
    result = Api().list_provider_models({"app": "claude", "official": True})
    assert not result["ok"]
    assert "endpoint" in result["error"]
