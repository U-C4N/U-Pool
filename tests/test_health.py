from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from upool import health
from upool.models import APP_CLAUDE, APP_CODEX, Provider


class _Handler(BaseHTTPRequestHandler):
    """Answers with the status code named by the first path segment."""

    def do_GET(self):  # noqa: N802 - stdlib override
        head = self.path.strip("/").split("/")[0]
        code = int(head) if head.isdigit() else 200
        body = b"{}"
        self.send_response(code)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):
        pass


@pytest.fixture
def server():
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    host, port = httpd.server_address[:2]
    yield f"http://{host}:{port}"
    httpd.shutdown()
    httpd.server_close()


def claude_provider(base_url: str) -> Provider:
    return Provider(app=APP_CLAUDE, name="Probe", base_url=base_url, api_key="sk-test")


def test_reachable_endpoint_reports_latency(server):
    # The Claude adapter probes <base_url>/v1/models; the stub answers 200.
    result = health.check(claude_provider(server))
    assert result.status == health.STATUS_OK
    assert result.reachable
    assert result.http_status == 200
    assert result.latency_ms is not None and result.latency_ms >= 0
    assert "Reachable" in result.message


def test_rejected_key_is_reachable_but_flagged(server):
    result = health.check(claude_provider(f"{server}/401"))
    assert result.status == health.STATUS_AUTH
    assert result.reachable
    assert result.http_status == 401


def test_server_error_is_not_reachable(server):
    result = health.check(claude_provider(f"{server}/503"))
    assert result.status == health.STATUS_ERROR
    assert not result.reachable
    assert result.http_status == 503


def test_unreachable_host_reports_failure():
    # Port 9 (discard) refuses connections on loopback.
    result = health.check(claude_provider("http://127.0.0.1:9"), timeout=2.0)
    assert result.status == health.STATUS_UNREACHABLE
    assert not result.reachable
    assert result.latency_ms is None


def test_official_provider_is_skipped():
    provider = Provider(app=APP_CODEX, name="OpenAI Official", official=True)
    result = health.check(provider)
    assert result.status == health.STATUS_SKIPPED


def test_codex_probe_hits_the_models_endpoint(server):
    provider = Provider(app=APP_CODEX, name="Probe", base_url=f"{server}/v1", api_key="sk-test")
    assert health.check(provider).status == health.STATUS_OK


def test_check_many_returns_one_result_per_provider(server):
    providers = [claude_provider(server), claude_provider("http://127.0.0.1:9")]
    results = health.check_many(providers, timeout=2.0)
    assert len(results) == 2
    assert {r.provider_id for r in results} == {p.id for p in providers}
