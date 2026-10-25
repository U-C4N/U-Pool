"""Own account selection so a client never receives two generations for one turn."""

from __future__ import annotations

import json
import itertools
import math
import re
import threading
import time
import uuid
from email.utils import parsedate_to_datetime
from typing import Any

from . import protocol
from .cookies import parse_cookies
from .gateway import GatewayServer
from .models import ProxyError, UpstreamResponse
from .runtime import ManagedEngines
from .store import AccountStore
from .telemetry import Telemetry


def _error(status: int) -> ProxyError:
    messages = {401: "The account needs a new login.", 403: "The account cannot access this model.",
                429: "The account is temporarily rate limited."}
    return ProxyError(messages.get(status, "The upstream request failed."), status,
                      {401: "auth_expired", 403: "access_denied", 429: "rate_limit_exceeded"}.get(status, "upstream_error"))


def _retry_after(headers: dict[str, str]) -> float:
    value = next((v for k, v in headers.items() if k.lower() == "retry-after"), "")
    try:
        seconds = float(value)
        return max(1, seconds) if math.isfinite(seconds) else 60
    except ValueError:
        try:
            return max(1, parsedate_to_datetime(value).timestamp() - time.time())
        except (ValueError, TypeError, OverflowError):
            return 60


def _json_response(response: UpstreamResponse) -> dict[str, Any]:
    try:
        body = response.body if isinstance(response.body, bytes) else b"".join(response.body)
        result = json.loads(body)
        if not isinstance(result, dict):
            raise ValueError
        return result
    except (ValueError, TypeError):
        raise ProxyError("The upstream returned an invalid response.") from None
    finally:
        _close(response)


def _close(response: UpstreamResponse) -> None:
    if response.close:
        try:
            response.close()
        except Exception:
            pass


def _public_frames(chunks: Any, model: str):
    buffer = b""
    terminal = False
    for block in chunks:
        buffer += block
        while match := re.search(br"\r?\n\r?\n", buffer):
            frame, buffer = buffer[:match.start()], buffer[match.end():]
            lines = frame.splitlines()
            raw = b"\n".join(line[5:].lstrip(b" ") for line in lines if line.startswith(b"data:"))
            if not raw or raw == b"[DONE]":
                if raw == b"[DONE]":
                    terminal = True
                yield frame + b"\n\n", None
                continue
            try:
                event = json.loads(raw)
            except (ValueError, UnicodeError) as exc:
                raise ProxyError("The upstream returned an invalid stream event.") from exc
            if not isinstance(event, dict):
                raise ProxyError("The upstream returned an invalid stream event.")
            error = event.get("error") or event.get("response", {}).get("error")
            if error:
                status = error.get("status", 502) if isinstance(error, dict) else 502
                status = status if type(status) is int and 400 <= status <= 599 else 502
                failure = _error(status)
                retry = error.get("retry_after") if isinstance(error, dict) else None
                failure.retry_after = str(retry) if retry is not None else ""
                raise failure
            if "model" in event:
                event["model"] = model
            if isinstance(event.get("response"), dict):
                event["response"]["model"] = model
            if event.get("type") in ("response.completed", "response.incomplete"):
                terminal = True
            prefix = b"\n".join(line for line in lines if not line.startswith(b"data:"))
            encoded = b"data: " + json.dumps(event, ensure_ascii=False).encode() + b"\n\n"
            yield (prefix + b"\n" if prefix else b"") + encoded, event
        if len(buffer) > 4 * 1024 * 1024:
            raise ProxyError("The upstream stream event exceeds its size limit.")
    if buffer.strip():
        raise ProxyError("The upstream stream ended in an incomplete event.", 502, "incomplete_stream")
    if not terminal:
        raise ProxyError("The upstream stream ended before completion.", 502, "incomplete_stream")


class ProxyService:
    def __init__(self, engines: Any = None, store: AccountStore | None = None) -> None:
        self.store = store or AccountStore()
        self.engines = engines if engines is not None else ManagedEngines()
        self.telemetry = Telemetry()
        self.gateway = GatewayServer(self)
        self._condition = threading.Condition(threading.RLock())
        self._busy: set[str] = set()
        self._next: dict[str, int] = {}
        self._worker: threading.Thread | None = None
        self._install_error = ""
        self._login: dict[str, str] = {}
        self._stopping = False
        self._shutdown = False

    def _record(self, *args: Any) -> None:
        try:
            self.telemetry.record(*args)
        except Exception:
            # A full usage disk must not turn a successful generation into a retry.
            pass

    def _reserve(self, account_id: str, *, browser: bool = False) -> dict[str, Any]:
        with self._condition:
            account = self.store.get(account_id)
            if self._shutdown:
                raise ProxyError("The application is shutting down.", 503, "api_stopping")
            if account_id in self._busy:
                raise ProxyError("Wait for the current account operation to finish.", 409, "account_busy")
            if browser and sum(a["provider"] == "chatgpt" and a["id"] in self._busy for a in self.store.accounts()) >= 2:
                raise ProxyError("Both managed ChatGPT browsers are busy.", 409, "browsers_busy")
            self._busy.add(account_id)
            return account

    @property
    def client_key(self) -> str:
        return self.store.key()

    def state(self) -> dict[str, Any]:
        data = self.store.data()
        accounts = self.store.public()
        with self._condition:
            for account in accounts:
                if account["id"] in self._busy and account["status"] != "verifying":
                    account["status"] = "busy"
        try:
            stats = self.telemetry.summary()
        except Exception:
            stats = {"requests": 0, "errors": 0, "avg_latency_ms": 0, "input_tokens": None, "output_tokens": None}
        return {"running": self.gateway.running, "port": data["port"],
                "endpoint": f"http://127.0.0.1:{data['port']}/v1", "has_key": bool(data.get("key")),
                "accounts": accounts, "runtimes": self.engines.snapshot(),
                "stats": stats, "busy": bool(self._worker and self._worker.is_alive()),
                "error": self._install_error, "supported": self.supported()}

    @staticmethod
    def supported() -> bool:
        import sys
        import struct
        return sys.platform == "win32" and struct.calcsize("P") == 8

    def install(self) -> dict[str, Any]:
        with self._condition:
            if self._worker and self._worker.is_alive():
                return self.state()
            self._install_error = ""
            def run() -> None:
                try:
                    self.engines.install()
                except Exception:
                    self._install_error = "Runtime installation failed. Check the runtime status and retry."
            self._worker = threading.Thread(target=run, name="upool-proxy-install", daemon=True)
            self._worker.start()
        return self.state()

    def start(self) -> dict[str, Any]:
        self.client_key
        with self._condition:
            self._stopping = False
        try:
            self.gateway.start(int(self.store.data()["port"]))
        except OSError:
            raise ProxyError("The configured API port is already in use or unavailable.", 409, "port_unavailable") from None
        return self.state()

    def stop(self) -> dict[str, Any]:
        with self._condition:
            self._stopping = True
            self._condition.notify_all()
        self.gateway.stop()
        self.engines.stop_all()
        return self.state()

    def shutdown(self) -> None:
        with self._condition:
            if self._shutdown:
                return
            self._shutdown = True
            self._stopping = True
            self._condition.notify_all()
        try:
            self.gateway.stop()
        finally:
            self.engines.stop_all()

    def set_port(self, port: int) -> dict[str, Any]:
        if self.gateway.running:
            raise ProxyError("Stop the API before changing its port.", 409, "api_running")
        self.store.set_port(port)
        return self.state()

    def _new_account(self, provider: str, name: str) -> dict[str, Any]:
        return {"id": uuid.uuid4().hex, "provider": provider, "name": str(name).strip()[:120] or provider.title(),
                "email": "", "user_id": "", "enabled": True, "status": "verifying", "models": [],
                "last_checked": 0, "cooldown_until": 0, "error": "", "cookies": []}

    def _adopt(self, account: dict[str, Any], facts: dict[str, Any]) -> str:
        identity = str(facts.get("user_id") or facts.get("email") or "")
        if not identity:
            raise ProxyError("The account identity could not be verified.", 401, "auth_expired")
        models = [str(m) for m in facts.get("models", []) if isinstance(m, str)]
        # Deduplication and target replacement share the scheduler lock so a
        # request cannot acquire the old profile between the check and the move.
        with self._condition:
            duplicate = next((a for a in self.store.accounts() if a["id"] != account["id"]
                              and a["provider"] == account["provider"]
                              and (a.get("user_id") or a.get("email")) == identity), None)
            if duplicate:
                if duplicate["id"] in self._busy:
                    raise ProxyError("This account is serving a request. Re-import after it finishes.", 409, "account_busy")
                self.engines.adopt_account(account["id"], duplicate["id"])
                self.store.delete(account["id"])
                account["id"] = duplicate["id"]
                account["enabled"] = duplicate.get("enabled", True)
            else:
                current = self.store.get(account["id"])
                account["enabled"] = current.get("enabled", True)
                account["name"] = current.get("name", account["name"])
            account.update(user_id=identity, email=str(facts.get("email") or ""), models=models,
                           status="ready", error="", cooldown_until=0, last_checked=time.time())
            if facts.get("name") and account["name"] in ("Chatgpt", "Grok"):
                account["name"] = str(facts["name"])[:120]
            self.store.put(account)
            return account["id"]

    def import_cookie(self, text: str, name: str = "") -> dict[str, Any]:
        cookies = parse_cookies(text)
        account = self._new_account("chatgpt", name)
        account["cookies"] = cookies
        source_id = account["id"]
        reserved = False
        try:
            with self._condition:
                self.store.put(account)
                self._reserve(source_id, browser=True)
                reserved = True
            facts = self.engines.verify_chatgpt(account, cookies)
            account_id = self._adopt(account, facts)
        except Exception as exc:
            if reserved:
                self.engines.stop_account(account["id"])
            status = exc.status if isinstance(exc, ProxyError) else 502
            message = {
                400: "The browser rejected the cookie export. Check the export format.",
                401: "ChatGPT did not accept this session. Open its managed profile and sign in, then refresh the account.",
                403: "ChatGPT blocked session verification. Open its managed profile and complete any browser verification, then refresh the account.",
                409: "Account verification is busy. Wait for the current operation to finish and retry.",
                429: "ChatGPT session verification is rate limited. Wait and refresh the account.",
            }.get(status, "The cookie session could not be verified because the browser or ChatGPT is unavailable. Retry or open its managed profile, then refresh the account.")
            self.store.update(account["id"], {"status": "reauth" if status == 401 else "error",
                                             "error": message})
            if isinstance(exc, ProxyError):
                raise ProxyError(message, exc.status, exc.code) from None
            raise ProxyError(message) from None
        finally:
            if reserved:
                self._release({"id": source_id})
        return {"account_id": account_id, "state": self.state()}

    def login_grok(self, name: str = "") -> dict[str, Any]:
        account = self._new_account("grok", name)
        self.store.put(account)
        try:
            login = self.engines.begin_grok(account)
            self._login[account["id"]] = str(login["state"])
            return {**login, "account_id": account["id"]}
        except Exception:
            self.store.update(account["id"], {"status": "error", "error": "OAuth login could not be started."})
            raise ProxyError("OAuth login could not be started. Check that the Grok runtime is installed.") from None

    def login_status(self, account_id: str, state: str) -> dict[str, Any]:
        if self._login.get(account_id) != state:
            raise ProxyError("Login session not found.", 404, "login_not_found")
        account = self._reserve(account_id)
        try:
            result = self.engines.poll_grok(account, state)
        except Exception:
            result = {"status": "error"}
        try:
            status = result.get("status", "pending")
            if status in ("ok", "success", "completed"):
                facts = result.get("facts", result)
                self._adopt(account, facts)
                self._login.pop(account_id, None)
                status = "ok"
            elif status in ("error", "expired", "cancelled"):
                self.store.update(account_id, {"status": "reauth", "error": "OAuth login was cancelled or expired."})
                self._login.pop(account_id, None)
                status = "error"
            else:
                status = "pending"
        finally:
            self._release({"id": account_id})
        return {"status": status, "state": self.state()}

    def cancel_login(self, account_id: str, state: str) -> dict[str, Any]:
        if self._login.get(account_id) != state:
            raise ProxyError("Login session not found.", 404, "login_not_found")
        account = self._reserve(account_id)
        try:
            self.engines.cancel_grok(account, state)
            self._login.pop(account_id, None)
            self.store.update(account_id, {"status": "reauth", "error": "Login cancelled."})
        finally:
            self._release({"id": account_id})
        return self.state()

    def update_account(self, account_id: str, patch: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(patch, dict) or set(patch) - {"name", "enabled"}:
            raise ProxyError("Only account name and enabled state can be edited.", 400, "invalid_account")
        clean: dict[str, Any] = {}
        if "name" in patch:
            if not isinstance(patch["name"], str) or not patch["name"].strip():
                raise ProxyError("Enter an account name.", 400, "invalid_account")
            clean["name"] = patch["name"].strip()[:120]
        if "enabled" in patch:
            if not isinstance(patch["enabled"], bool):
                raise ProxyError("Enabled must be a boolean.", 400, "invalid_account")
            clean["enabled"] = patch["enabled"]
        with self._condition:
            self.store.update(account_id, clean)
            self._condition.notify_all()
        return self.state()

    def refresh_account(self, account_id: str) -> dict[str, Any]:
        original = self.store.get(account_id)
        account = self._reserve(account_id, browser=original["provider"] == "chatgpt")
        try:
            if account["provider"] == "chatgpt":
                facts = self.engines.refresh_chatgpt(account)
                self._adopt(account, facts)
            else:
                models = self.engines.models(account)
                self.store.update(account_id, {"models": models, "status": "ready", "error": "", "last_checked": time.time(), "cooldown_until": 0})
        finally:
            self._release({"id": account_id})
        return self.state()

    def delete_account(self, account_id: str) -> dict[str, Any]:
        self._reserve(account_id)
        try:
            self.engines.delete_account_data(account_id)
            with self._condition:
                self.store.delete(account_id)
                self._login.pop(account_id, None)
        finally:
            self._release({"id": account_id})
        return self.state()

    def open_profile(self, account_id: str) -> Any:
        account = self._reserve(account_id, browser=True)
        try:
            return self.engines.open_profile(account)
        finally:
            self._release({"id": account_id})

    def list_models(self) -> list[dict[str, Any]]:
        names = sorted({f"{a['provider']}/{m}" for a in self.store.accounts()
                        if a.get("enabled") and a.get("status") in ("ready", "busy", "cooldown") for m in a.get("models", [])})
        return [{"id": name, "object": "model", "created": 0, "owned_by": "u-pool"} for name in names]

    def _acquire(self, provider: str, model: str, excluded: set[str], cancelled: threading.Event | None = None) -> dict[str, Any]:
        deadline = time.monotonic() + 60
        with self._condition:
            while True:
                self._check_cancelled(cancelled)
                if self._stopping:
                    raise ProxyError("The API is stopping.", 503, "api_stopping")
                candidates = []
                for account in self.store.accounts():
                    if account["provider"] != provider or not account.get("enabled") or account["id"] in excluded or model not in account.get("models", []):
                        continue
                    if account["status"] == "cooldown" and account.get("cooldown_until", 0) <= time.time():
                        account = self.store.update(account["id"], {"status": "ready", "cooldown_until": 0})
                    if account["status"] == "ready":
                        candidates.append(account)
                start = self._next.get(provider, 0)
                for offset in range(len(candidates)):
                    index = (start + offset) % len(candidates)
                    account = candidates[index]
                    browser_busy = sum(a["provider"] == "chatgpt" and a["id"] in self._busy for a in self.store.accounts())
                    if account["id"] not in self._busy and (provider != "chatgpt" or browser_busy < 2):
                        self._busy.add(account["id"])
                        self._next[provider] = index + 1
                        return account
                if not candidates:
                    raise ProxyError("No available account can serve this model. Check login, model access and cooldown status.", 503, "account_unavailable")
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise ProxyError("All eligible accounts are busy. Retry shortly.", 429, "pool_busy")
                self._condition.wait(min(remaining, 0.1 if cancelled is not None else 1))

    def _release(self, account: dict[str, Any]) -> None:
        with self._condition:
            self._busy.discard(account["id"])
            self._condition.notify_all()

    def _failed(self, account: dict[str, Any], status: int, headers: dict[str, str], model: str | None = None) -> None:
        patch = {"last_checked": time.time(), "error": str(_error(status))}
        if status == 401:
            patch["status"] = "reauth"
        elif status == 429:
            patch.update(status="cooldown", cooldown_until=time.time() + _retry_after(headers))
        elif status == 403:
            patch["models"] = [name for name in self.store.get(account["id"]).get("models", []) if name != model]
        self.store.update(account["id"], patch)

    @staticmethod
    def _check_cancelled(cancelled: threading.Event | None) -> None:
        if cancelled is not None and cancelled.is_set():
            raise ProxyError("The client cancelled the request.", 499, "request_cancelled")

    def _engine_complete(self, account: dict[str, Any], endpoint: str, payload: dict[str, Any], cancelled: threading.Event | None = None) -> UpstreamResponse:
        self._check_cancelled(cancelled)
        cancellable = getattr(self.engines, "complete_cancellable", None)
        if cancelled is not None and callable(cancellable):
            return cancellable(account, endpoint, payload, cancelled)
        return self.engines.complete(account, endpoint, payload)

    def _chatgpt(self, account: dict[str, Any], endpoint: str, payload: dict[str, Any], cancelled: threading.Event | None = None) -> UpstreamResponse:
        chat = protocol.responses_to_chat(payload) if endpoint == "responses" else dict(payload)
        if chat.get("tools"):
            worker = protocol.prepare_tool_prompt(chat)
            response = self._engine_complete(account, "chat/completions", worker, cancelled)
            if response.status >= 400:
                return response
            raw = _json_response(response)
            self._check_cancelled(cancelled)
            try:
                result = protocol.parse_tool_response(raw, chat)
            except ProxyError as exc:
                if exc.code != "invalid_tool_output":
                    raise
                text = raw.get("choices", [{}])[0].get("message", {}).get("content", "")
                retry = self._engine_complete(account, "chat/completions", protocol.repair_tool_prompt(worker, text), cancelled)
                if retry.status >= 400:
                    return retry
                result = protocol.parse_tool_response(_json_response(retry), chat)
            result["model"] = payload["model"]
            if endpoint == "responses":
                if payload.get("stream"):
                    chunks = protocol.chat_completion_sse(result)
                    return UpstreamResponse(200, {"Content-Type": "text/event-stream"}, protocol.chat_sse_to_responses(chunks, payload))
                result = protocol.chat_to_response(result, payload)
            elif payload.get("stream"):
                return UpstreamResponse(200, {"Content-Type": "text/event-stream"}, protocol.chat_completion_sse(result))
            return UpstreamResponse(200, {"Content-Type": "application/json"}, json.dumps(result).encode())
        # The CDP driver receives a JSON transcript whose content values must
        # all be strings, including multipart text and nullable tool-call turns.
        chat["messages"] = [{**message, "content": protocol._text(message.get("content"), nullable=message.get("role") == "assistant" and bool(message.get("tool_calls"))) or ""} for message in chat["messages"]]
        response = self._engine_complete(account, "chat/completions", chat, cancelled)
        if response.status >= 400:
            return response
        if endpoint == "responses":
            if payload.get("stream"):
                chunks = [response.body] if isinstance(response.body, bytes) else response.body
                return UpstreamResponse(200, {"Content-Type": "text/event-stream"}, protocol.chat_sse_to_responses(chunks, payload), response.close)
            return UpstreamResponse(200, {"Content-Type": "application/json"}, json.dumps(protocol.chat_to_response(_json_response(response), payload)).encode())
        return response

    def complete_cancellable(self, endpoint: str, payload: dict[str, Any], cancelled: threading.Event) -> UpstreamResponse:
        return self.complete(endpoint, payload, cancelled)

    def complete(self, endpoint: str, payload: dict[str, Any], cancelled: threading.Event | None = None) -> UpstreamResponse:
        self._check_cancelled(cancelled)
        payload = protocol.validate_request(endpoint, payload)
        provider, model = protocol.parse_model(payload["model"])
        wire = dict(payload, model=model)
        excluded: set[str] = set()
        last_error: ProxyError | None = None
        while True:
            try:
                account = self._acquire(provider, model, excluded, cancelled)
            except ProxyError:
                self._check_cancelled(cancelled)
                if last_error:
                    raise last_error
                raise
            started = time.monotonic()
            request_id = uuid.uuid4().hex
            failure_headers: dict[str, str] = {}
            response = None
            try:
                response = self._chatgpt(account, endpoint, wire, cancelled) if provider == "chatgpt" else self._engine_complete(account, endpoint, wire, cancelled)
                if response.status >= 400:
                    failure_headers = response.headers
                    raise _error(response.status)
                if not isinstance(response.body, bytes):
                    # Retry only while no public frame has escaped the scheduler.
                    frames = iter(_public_frames(response.body, payload["model"]))
                    first = next(frames, None)
                    if first is None:
                        raise ProxyError("The upstream returned an empty stream.")
                    response.body = itertools.chain([first], frames)
                self._check_cancelled(cancelled)
            except Exception as exc:
                error = exc if isinstance(exc, ProxyError) else ProxyError("The account runtime could not serve the request.")
                if cancelled is not None and cancelled.is_set():
                    error = ProxyError("The client cancelled the request.", 499, "request_cancelled")
                try:
                    if response is not None:
                        _close(response)
                    with self._condition:
                        if isinstance(exc, ProxyError) and error.status != 499:
                            if getattr(exc, "retry_after", ""):
                                failure_headers = {"retry-after": exc.retry_after}
                            self._failed(account, exc.status, failure_headers, model)
                finally:
                    self._release(account)
                self._record(request_id, account, payload["model"], error.status, (time.monotonic() - started) * 1000)
                if error.code == "invalid_tool_output" or error.status not in {401, 403, 429, 500, 502, 503, 504}:
                    raise error
                excluded.add(account["id"])
                last_error = error
                continue
            return self._track(response, account, payload, request_id, started, cancelled)

    def _track(self, response: UpstreamResponse, account: dict[str, Any], payload: dict[str, Any], request_id: str, started: float, cancelled: threading.Event | None = None) -> UpstreamResponse:
        finished = False
        finish_lock = threading.Lock()
        usage: dict[str, Any] = {}
        status = response.status
        exhausted = False
        def finish(cancel: bool = False) -> None:
            nonlocal finished, status
            with finish_lock:
                if finished:
                    return
                if cancel and not exhausted and status < 400:
                    status = 499
                finished = True
            try:
                _close(response)
            finally:
                self._release(account)
                self._record(request_id, account, payload["model"], status, (time.monotonic() - started) * 1000, usage)
        if isinstance(response.body, bytes):
            try:
                parsed = json.loads(response.body)
                parsed["model"] = payload["model"]
                usage.update(parsed.get("usage") or {})
                body = json.dumps(parsed).encode()
            except (ValueError, TypeError, AttributeError):
                body = response.body
            finish()
            return UpstreamResponse(response.status, response.headers, body)
        def stream():
            nonlocal status, exhausted
            try:
                for block, event in response.body:
                    self._check_cancelled(cancelled)
                    if event:
                        usage.update(event.get("usage") or event.get("response", {}).get("usage") or {})
                    terminal = bool(event and event.get("type") in {"response.completed", "response.incomplete"}) or any(line.startswith(b"data:") and line[5:].strip() == b"[DONE]" for line in block.splitlines())
                    if terminal:
                        # SDKs stop consuming at the terminal event. Release the
                        # account before publishing it, without waiting for EOF.
                        exhausted = True
                        finish()
                    yield block
                    if terminal:
                        return
                exhausted = True
            except GeneratorExit:
                if not exhausted:
                    status = 499
                raise
            except ProxyError as exc:
                status = exc.status
                if exc.status != 499:
                    with self._condition:
                        self._failed(account, exc.status, {}, protocol.parse_model(payload["model"])[1])
                raise
            except Exception:
                if cancelled is not None and cancelled.is_set():
                    status = 499
                    raise ProxyError("The client cancelled the request.", 499, "request_cancelled") from None
                status = 502
                raise ProxyError("The upstream stream was interrupted.") from None
            finally:
                finish(cancel=not exhausted)
        return UpstreamResponse(response.status, response.headers, stream(), lambda: finish(cancel=True))
