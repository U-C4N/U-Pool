from __future__ import annotations

import http.client
import json
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import pytest

from upool.proxy.gateway import GatewayServer, MAX_BODY_BYTES
from upool.proxy.models import ProxyError, UpstreamResponse


class FakeService:
    client_key = "test-client-key"

    def __init__(self):
        self.requests = []
        self.closed = threading.Event()
        self.response = UpstreamResponse(200, {"Content-Type": "application/json"}, b'{"choices":[]}', self.closed.set)

    def list_models(self):
        return [{"id": "chatgpt/test", "object": "model", "owned_by": "chatgpt"}]

    def complete(self, endpoint, payload):
        self.requests.append((endpoint, payload))
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture
def gateway():
    service = FakeService()
    server = GatewayServer(service)
    port = free_port()
    assert server.start(port) == f"http://127.0.0.1:{port}/v1"
    yield service, server, port
    server.stop()


def request(port, method="POST", path="/v1/chat/completions", payload=None, headers=None):
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
    actual_headers = {"Authorization": "Bearer test-client-key", "Content-Type": "application/json"}
    actual_headers.update(headers or {})
    body = json.dumps(payload or {"model": "chatgpt/test", "messages": [{"role": "user", "content": "Hello"}]}).encode() if method == "POST" else None
    connection.request(method, path, body, actual_headers)
    response = connection.getresponse()
    result = response.status, dict(response.getheaders()), response.read()
    connection.close()
    return result


def test_authenticated_models_and_completion(gateway):
    service, server, port = gateway
    status, _, body = request(port, "GET", "/v1/models")
    assert status == 200
    assert json.loads(body)["data"][0]["id"] == "chatgpt/test"
    assert server.running
    status, headers, body = request(port)
    assert status == 200
    assert headers["Content-Length"] == str(len(body))
    assert service.requests[0][0] == "chat/completions"
    assert service.closed.wait(1)


@pytest.mark.parametrize("path", ["/v1/models", "/v1/chat/completions", "/v1/responses"])
def test_every_endpoint_requires_key(gateway, path):
    service, _, port = gateway
    status, _, body = request(port, "GET" if path.endswith("models") else "POST", path, headers={"Authorization": "Bearer incorrect"})
    assert status == 401
    assert json.loads(body)["error"]["code"] == "invalid_api_key"
    assert not service.requests


@pytest.mark.parametrize("path", ["/v1/models", "/v1/chat/completions", "/v1/responses"])
def test_repeated_early_auth_failures_deliver_complete_json(gateway, path):
    service, _, port = gateway
    for _ in range(50):
        status, headers, body = request(port, "GET" if path.endswith("models") else "POST", path,
                                        headers={"Authorization": "Bearer incorrect"})
        assert status == 401
        assert len(body) == int(headers["Content-Length"])
        assert json.loads(body)["error"]["code"] == "invalid_api_key"
    assert not service.requests


def test_auth_body_discard_has_elapsed_time_bound(gateway):
    service, _, port = gateway
    client = socket.create_connection(("127.0.0.1", port), timeout=2)
    started = time.monotonic()
    try:
        client.sendall(f"POST /v1/responses HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nAuthorization: Bearer incorrect\r\nContent-Length: 100000\r\nContent-Type: application/json\r\n\r\n{{".encode())
        response = http.client.HTTPResponse(client)
        response.begin()
        assert response.status == 401
        assert json.loads(response.read())["error"]["code"] == "invalid_api_key"
        assert time.monotonic() - started < 1
    finally:
        client.close()
    assert not service.requests


def test_domain_host_is_rejected(gateway):
    _, _, port = gateway
    assert request(port, headers={"Host": "external.example"})[0] == 403


def test_no_port_fallback():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        sock.listen()
        gateway = GatewayServer(FakeService())
        with pytest.raises(ProxyError, match="unavailable"):
            gateway.start(sock.getsockname()[1])
        assert not gateway.running


def test_bounded_body_rejected_before_read(gateway):
    service, _, port = gateway
    status, _, body = request(port, headers={"Content-Length": str(MAX_BODY_BYTES + 1)})
    assert status == 413
    assert json.loads(body)["error"]["code"] == "body_too_large"
    assert not service.requests


def test_unknown_fields_rejected(gateway):
    service, _, port = gateway
    status, _, _ = request(port, payload={"model": "chatgpt/test", "messages": [{"role": "user", "content": "hi"}], "audio": {}})
    assert status == 400
    assert not service.requests


def test_error_does_not_expose_internal_exception(gateway):
    service, _, port = gateway
    service.response = RuntimeError("secret-cookie-value")
    status, _, body = request(port)
    assert status == 502
    assert b"secret-cookie-value" not in body


def test_sse_is_forwarded_and_close_is_called(gateway):
    service, _, port = gateway
    frames = [b'data: {"choices":[]}\n\n', b"data: [DONE]\n\n"]
    service.response = UpstreamResponse(200, {"Content-Type": "text/event-stream", "Authorization": "hidden", "Set-Cookie": "secret"}, iter(frames), service.closed.set)
    status, headers, body = request(port, payload={"model": "chatgpt/test", "messages": [{"role": "user", "content": "hi"}], "stream": True})
    assert status == 200
    assert body == b"".join(frames)
    assert {key.lower(): value for key, value in headers.items()}["content-type"] == "text/event-stream"
    assert "Set-Cookie" not in headers and "Authorization" not in headers
    assert service.closed.wait(1)


def test_failed_stream_is_not_replayed(gateway):
    service, _, port = gateway

    def chunks():
        yield b'data: {"choices":[]}\n\n'
        raise ProxyError("Stream stopped.", 502, "incomplete_stream")

    service.response = UpstreamResponse(200, {"Content-Type": "text/event-stream"}, chunks(), service.closed.set)
    status, _, body = request(port, payload={"model": "chatgpt/test", "messages": [{"role": "user", "content": "hi"}], "stream": True})
    assert status == 200
    assert b"incomplete_stream" in body
    assert len(service.requests) == 1
    assert service.closed.wait(1)


def test_first_chunk_error_keeps_json_error_status(gateway):
    service, _, port = gateway

    def chunks():
        raise ProxyError("Quota unavailable.", 429, "rate_limit")
        yield b""

    service.response = UpstreamResponse(200, {"Content-Type": "text/event-stream"}, chunks(), service.closed.set)
    status, _, body = request(port)
    assert status == 429
    assert json.loads(body)["error"]["code"] == "rate_limit"
    assert service.closed.wait(1)


def test_stop_closes_active_upstream(gateway):
    service, server, port = gateway
    first_sent = threading.Event()

    def chunks():
        yield b"data: first\n\n"
        first_sent.set()
        service.closed.wait(3)
        yield b"data: last\n\n"

    service.response = UpstreamResponse(200, {"Content-Type": "text/event-stream"}, chunks(), service.closed.set)
    client = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
    client.request("POST", "/v1/chat/completions", json.dumps({"model": "chatgpt/test", "messages": [{"role": "user", "content": "hi"}], "stream": True}), {"Authorization": "Bearer test-client-key", "Content-Type": "application/json"})
    response = client.getresponse()
    assert response.status == 200
    assert first_sent.wait(1)
    server.stop()
    assert service.closed.wait(1)
    assert not server.running
    client.close()


def test_disconnect_cancels_worker_waiting_for_next_token(gateway):
    service, _, port = gateway
    close_count = []

    def close():
        close_count.append(True)
        service.closed.set()

    def chunks():
        yield b"data: first\n\n"
        service.closed.wait(3)
        yield b"data: last\n\n"

    service.response = UpstreamResponse(200, {"Content-Type": "text/event-stream"}, chunks(), close)
    client = socket.create_connection(("127.0.0.1", port), timeout=3)
    payload = json.dumps({"model": "chatgpt/test", "messages": [{"role": "user", "content": "hi"}], "stream": True}).encode()
    headers = f"POST /v1/chat/completions HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nAuthorization: Bearer test-client-key\r\nContent-Type: application/json\r\nContent-Length: {len(payload)}\r\n\r\n".encode()
    client.sendall(headers + payload)
    assert client.recv(1024).startswith(b"HTTP/1.1 200")
    client.shutdown(socket.SHUT_RDWR)
    client.close()
    assert service.closed.wait(1)
    assert close_count == [True]


def test_disconnect_cancels_initial_wait_before_headers(gateway):
    service, _, port = gateway
    entered = threading.Event()
    cancelled = threading.Event()
    def complete(endpoint, payload, signal):
        entered.set()
        assert signal.wait(2)
        cancelled.set()
        raise ProxyError("Cancelled", 499, "request_cancelled")
    service.complete_cancellable = complete
    client = socket.create_connection(("127.0.0.1", port), timeout=3)
    body = json.dumps({"model": "chatgpt/test", "messages": [{"role": "user", "content": "hi"}], "stream": True}).encode()
    headers = f"POST /v1/chat/completions HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nAuthorization: Bearer test-client-key\r\nContent-Type: application/json\r\nContent-Length: {len(body)}\r\n\r\n".encode()
    client.sendall(headers + body)
    assert entered.wait(1)
    client.shutdown(socket.SHUT_RDWR)
    client.close()
    assert cancelled.wait(1)


def test_stop_cancels_initial_wait_before_headers(gateway):
    service, server, port = gateway
    entered = threading.Event()
    cancelled = threading.Event()
    def complete(endpoint, payload, signal):
        entered.set()
        assert signal.wait(2)
        cancelled.set()
        raise ProxyError("Cancelled", 499, "request_cancelled")
    service.complete_cancellable = complete
    client = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
    client.request("POST", "/v1/chat/completions", json.dumps({"model": "chatgpt/test", "messages": [{"role": "user", "content": "hi"}]}), {"Authorization": "Bearer test-client-key", "Content-Type": "application/json"})
    assert entered.wait(1)
    server.stop()
    assert cancelled.wait(1)
    client.close()


def test_initial_disconnect_aborts_real_runtime_header_wait(monkeypatch):
    from upool.proxy.runtime import ManagedEngines
    from upool.proxy.service import ProxyService

    entered = threading.Event()
    upstream_disconnected = threading.Event()
    recorded = threading.Event()
    statuses = []

    class WaitingWorker(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_POST(self):
            self.rfile.read(int(self.headers["Content-Length"]))
            entered.set()
            self.connection.settimeout(3)
            try:
                if self.connection.recv(1) == b"":
                    upstream_disconnected.set()
            except OSError:
                upstream_disconnected.set()

    worker = ThreadingHTTPServer(("127.0.0.1", 0), WaitingWorker)
    worker.daemon_threads = True
    thread = threading.Thread(target=worker.serve_forever, kwargs={"poll_interval": 0.1}, daemon=True)
    thread.start()
    engines = ManagedEngines(SimpleNamespace(snapshot=lambda: []))
    instance = SimpleNamespace(port=worker.server_port, key="private-worker-key", management_key="private-management-key", active=0)
    monkeypatch.setattr(engines, "_start", lambda account: instance)
    service = ProxyService(engines)
    account = service._new_account("grok", "One")
    account.update(id="one", status="ready", models=["test"])
    service.store.put(account)
    original_record = service.telemetry.record

    def record(*args):
        original_record(*args)
        statuses.append(args[3])
        recorded.set()

    monkeypatch.setattr(service.telemetry, "record", record)
    port = free_port()
    service.gateway.start(port)
    client = socket.create_connection(("127.0.0.1", port), timeout=3)
    try:
        body = json.dumps({"model": "grok/test", "messages": [{"role": "user", "content": "hi"}], "stream": True}).encode()
        headers = f"POST /v1/chat/completions HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nAuthorization: Bearer {service.client_key}\r\nContent-Type: application/json\r\nContent-Length: {len(body)}\r\n\r\n".encode()
        client.sendall(headers + body)
        assert entered.wait(1)
        client.shutdown(socket.SHUT_RDWR)
        client.close()
        assert upstream_disconnected.wait(1)
        assert recorded.wait(1)
        assert statuses == [499]
        assert not service._busy and instance.active == 0
    finally:
        client.close()
        service.shutdown()
        worker.shutdown()
        worker.server_close()
        thread.join(2)
