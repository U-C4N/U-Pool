"""Private Windows ChatGPT worker built with the pinned Web2API CDP driver.

This entry point is packaged separately; its websocket/file-lock dependencies
never become dependencies of U-Pool's gateway. All HTTP routes require a
random per-process Bearer key, and upstream logs are disabled deliberately.
"""

from __future__ import annotations

import argparse
import asyncio
import concurrent.futures
import hmac
import json
import logging
import math
import os
import queue
import re
import select
import socket
import subprocess
import threading
import tempfile
import time
import unicodedata
import urllib.request
import uuid
from email.utils import parsedate_to_datetime
from urllib.parse import urlsplit
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


class WorkerFailure(Exception):
    def __init__(self, message: str, status: int = 502, retry_after: int | None = None) -> None:
        super().__init__(message)
        self.status, self.retry_after = status, retry_after


_TURN_POLL_INTERVAL = 3.0
_PROJECTION_TIMEOUT = 5.0
_FRESH_CHAT_TIMEOUT = 6.0
_SESSION_TIMEOUT = 20.0
_SESSION_PROBE = r"""(async () => {
  if (location.origin !== 'https://chatgpt.com' || document.readyState === 'loading')
    return JSON.stringify({status: 503});
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 4000);
  try {
    const r = await fetch('/api/auth/session', {
      credentials: 'include', cache: 'no-store', signal: controller.signal
    });
    const result = {status: r.status, retry_after: r.headers.get('retry-after')};
    if (r.ok) {
      const s = await r.json();
      if (!s || typeof s !== 'object' || Array.isArray(s))
        return JSON.stringify({status: 502});
      result.session = {token: s.accessToken || '', user: s.user?.name || '',
                        email: s.user?.email || '', user_id: s.user?.id || ''};
    }
    return JSON.stringify(result);
  } catch (_) {
    return JSON.stringify({status: 503});
  } finally {
    clearTimeout(timer);
  }
})()"""


async def _read_session(driver) -> dict:
    """Distinguish a signed-out session from a page/network failure."""
    deadline = time.monotonic() + _SESSION_TIMEOUT
    signed_out = 0
    failure = WorkerFailure("ChatGPT session verification is temporarily unavailable. Retry or open the managed profile.", 503)
    while time.monotonic() < deadline:
        try:
            raw = await asyncio.wait_for(
                driver._js_strict(_SESSION_PROBE, timeout=5),
                timeout=min(5, max(0.001, deadline - time.monotonic())))
            result = json.loads(raw) if isinstance(raw, str) else raw
        except Exception:
            result = None
        status = result.get("status") if isinstance(result, dict) else None
        session = result.get("session") if isinstance(result, dict) else None
        if status == 403:
            raise WorkerFailure("ChatGPT blocked session verification. Open the managed profile and complete any browser verification.", 403)
        if status == 429:
            raise WorkerFailure("ChatGPT session verification is rate limited. Retry later.", 429,
                                _worker_retry_after(result.get("retry_after")))
        if (type(status) is int and 200 <= status < 300 and isinstance(session, dict)
                and isinstance(session.get("token"), str) and session["token"]):
            return session
        if status == 401 or (status == 200 and isinstance(session, dict) and not session.get("token")):
            signed_out += 1
            failure = WorkerFailure("ChatGPT did not accept this session. Open the managed profile and sign in.", 401)
            if signed_out >= 3:
                raise failure
        else:
            signed_out = 0
            failure = WorkerFailure("ChatGPT session verification is temporarily unavailable. Retry or open the managed profile.", 503)
        await asyncio.sleep(min(0.5, max(0, deadline - time.monotonic())))
    raise failure


_FRESH_CHAT_SNAPSHOT = r"""(() => {
  const main = document.querySelector('main, [role="main"]');
  const previous = main ? main.querySelectorAll(
    '[data-turn-key], [data-message-author-role], [data-conversation-role], ' +
    '[data-user-message-bubble], [data-chatgpt-agent-turn-start]'
  ).length : null;
  const composer = [...document.querySelectorAll(
    'div[role="textbox"]#prompt-textarea, div[role="textbox"].ProseMirror, textarea#prompt-textarea'
  )].some(el => {
    const r = el.getBoundingClientRect(), style = getComputedStyle(el);
    return !el.disabled && (el.isContentEditable || el.tagName === 'TEXTAREA') &&
      r.width > 0 && r.height > 0 && style.display !== 'none' && style.visibility !== 'hidden';
  });
  return JSON.stringify({origin: location.origin, pathname: location.pathname,
    main: !!main, previous, composer});
})()"""


def _worker_retry_after(value: str | None) -> int:
    """Decode only the provider's delay header, never its response body."""
    try:
        seconds = float(value)
    except (ValueError, TypeError):
        try:
            seconds = parsedate_to_datetime(value).timestamp() - time.time()
        except (ValueError, TypeError, OverflowError):
            return 60
    return math.ceil(max(0, seconds)) if math.isfinite(seconds) else 60


def _projection_query(expression: str) -> str:
    """Keep the pinned graph projection; preserve HTTP status/delay safely."""
    status = "if (!r.ok) return JSON.stringify({__status: r.status});"
    headers = "headers: {'Authorization': 'Bearer ' + __D.token}"
    if expression.count(status) != 1 or expression.count(headers) != 1:
        raise WorkerFailure("The ChatGPT backend projection is incompatible.", 503)
    return expression.replace(status,
        "if (!r.ok) return JSON.stringify({__status: r.status, "
        "__retry_after: r.headers.get('Retry-After')});").replace(headers,
        "signal: AbortSignal.timeout(4500), " + headers)


def _backend_http_failure(status, retry_after=None):
    if status == 429:
        return WorkerFailure("ChatGPT usage limit reached.", 429, _worker_retry_after(retry_after))
    if status in (400, 413, 422):
        return WorkerFailure("ChatGPT rejected the generation request.", 400)
    if status == 403:
        return WorkerFailure("The ChatGPT account cannot access this request.", 403)
    if isinstance(status, (int, float)) and status >= 400 and status not in (401, 404):
        return WorkerFailure("ChatGPT backend is temporarily unavailable.", 503)
    return None


def _latch_backend_failure(driver, failure):
    if driver._upool_backend_failure is None:
        driver._upool_backend_failure = failure


def _send_endpoint(url) -> bool:
    if not isinstance(url, str):
        return False
    try:
        parts = urlsplit(url)
        return (parts.scheme == "https" and parts.hostname == "chatgpt.com" and
                parts.port in (None, 443) and parts.username is None and parts.password is None and
                not parts.fragment and parts.path in {"/backend-api/conversation", "/backend-api/f/conversation"})
    except ValueError:
        return False


class _SubmitStatus:
    """Observe status only for the exact POST ID verified by our capture scope."""
    def __init__(self, driver, scope):
        self.driver, self.scope = driver, scope
        self.closed = False
        self.candidates = set()
        self.failures = {}
        self.handlers = {}
        for method, callback in {"Network.requestWillBeSent": self._request,
                                 "Network.responseReceived": self._response,
                                 "Network.loadingFailed": self._failed}.items():
            previous = driver._cdp_event_handlers.get(method)
            def dispatch(event, previous=previous, callback=callback):
                if previous is not None:
                    previous(event)
                if not self.closed:
                    callback(event.get("params", {}))
            self.handlers[method] = (previous, dispatch)
            driver._cdp_event_handlers[method] = dispatch
        if scope.future is not None:
            scope.future.add_done_callback(self._resolved)

    def _request(self, params):
        request = params.get("request", {})
        request_id = params.get("requestId")
        if request.get("method") != "POST" or not _send_endpoint(request.get("url")):
            self.candidates.discard(request_id)
            self.failures.pop(request_id, None)
            return
        if (not getattr(self.scope, "_closed", True) and request.get("method") == "POST" and
                _send_endpoint(request.get("url")) and isinstance(request_id, str) and
                len(self.candidates) < 8):
            self.candidates.add(request_id)

    def _response(self, params):
        request_id, response = params.get("requestId"), params.get("response", {})
        if request_id not in self.candidates or not _send_endpoint(response.get("url")):
            return
        status = response.get("status")
        # Select only this header; never copy/log header dictionaries or bodies.
        delay = next((value for name, value in response.get("headers", {}).items()
                      if isinstance(name, str) and name.lower() == "retry-after"), None)
        if status == 401:
            from chatgpt_web2api.cdp_driver import AuthExpiredError
            failure = AuthExpiredError()
        elif status == 404:
            failure = WorkerFailure("ChatGPT generation endpoint is unavailable.", 503)
        else:
            failure = _backend_http_failure(status, delay)
        if failure is not None:
            self.failures[request_id] = failure
            self._resolved()

    def _failed(self, params):
        request_id = params.get("requestId")
        if request_id in self.candidates:
            self.failures.setdefault(request_id, WorkerFailure("ChatGPT generation connection failed.", 503))
            self._resolved()

    def _resolved(self, _future=None):
        future = self.scope.future
        if self.closed or future is None or not future.done() or future.cancelled():
            return
        result = future.result()
        # IdentityListener validated the full submitted text hash, scope and
        # UUID. A same-route POST candidate alone is never sufficient.
        if result.uuid and result.request_id in self.candidates:
            failure = self.failures.get(result.request_id)
            if failure is not None:
                _latch_backend_failure(self.driver, failure)

    def close(self):
        if self.closed:
            return
        self.closed = True
        if self.scope.future is not None:
            self.scope.future.remove_done_callback(self._resolved)
        for method, (previous, handler) in self.handlers.items():
            if self.driver._cdp_event_handlers.get(method) is handler:
                if previous is None:
                    self.driver._cdp_event_handlers.pop(method, None)
                else:
                    self.driver._cdp_event_handlers[method] = previous
        self.candidates.clear()
        self.failures.clear()
_ROLE_QUERY = re.compile(
    r'''document\.querySelectorAll\(\s*'\[data-message-author-role="(assistant|user)"\]'\s*\)''')
_COMPLETION_SNAPSHOT = "return JSON.stringify({text: text, md_text: mdText, html_len:"
_MESSAGE_QUERY_HELPER = r"""
  let __upoolModernMessageQuery = false;
  const __upoolMessageNodes = role => {
    const old = [...document.querySelectorAll('[data-message-author-role="' + role + '"]')];
    // User role labels are not present in all modern layouts. The bubble is
    // the actual user message, unlike the shared turn's screen-reader label.
    const modern = role === 'user'
      ? [...document.querySelectorAll('[data-user-message-bubble]')] : [];
    for (const label of document.querySelectorAll('[data-conversation-role="' + role + '"]')) {
      // The modern role node is a screen-reader label. Its enclosing turn can
      // contain BOTH participants, so only use the matching content unit.
      const unit = label.closest('[data-content-search-unit-key]');
      if (!unit) continue;
      const selector = role === 'assistant'
        ? '[data-chatgpt-selection-message-id]' : '[data-user-message-bubble]';
      const nodes = [...unit.querySelectorAll(selector)].filter(node =>
        node.closest('[data-content-search-unit-key]') === unit);
      if (nodes.length > 1) throw new Error('Ambiguous ChatGPT message structure');
      if (nodes.length === 1) modern.push(nodes[0]);
    }
    const unique = [...new Set(modern)];
    if (role === 'assistant' && unique.length) __upoolModernMessageQuery = true;
    const legacy = old.filter(node => !unique.some(current =>
      current === node || current.contains(node) || node.contains(current)));
    return [...unique, ...legacy].sort((left, right) =>
      left.compareDocumentPosition(right) & 2 ? 1 : -1);
  };
"""


def _message_query(expression: str) -> str:
    """Adapt only the pinned driver's message queries to current web markup."""
    expression, changes = _ROLE_QUERY.subn(
        lambda match: "__upoolMessageNodes('" + match.group(1) + "')", expression)
    if not changes:
        return expression
    # Scoped to the actual assistant message, never the whole turn/page. The
    # screen-reader role label and action-control siblings are excluded above.
    expression = expression.replace("last.querySelector('.markdown')",
                                    "last.querySelector('.markdown, [data-markdown-text-style]')")
    expression = expression.replace(
        _COMPLETION_SNAPSHOT,
        "return JSON.stringify({upool_modern_message: __upoolModernMessageQuery, "
        "text: text, md_text: mdText, html_len:")
    return "(function(){" + _MESSAGE_QUERY_HELPER + "return (" + expression + ");})()"


class _AnchoredCompletion:
    """Keep DOM streaming, with terminal backend reads for virtualized answers.

    A backend ``matched`` result already requires a terminal assistant text
    descended from this request's captured/verified user-turn anchor. It does
    not depend on a rendered DOM node or stale copy/feedback affordances.
    """

    def __init__(self, driver, detector) -> None:
        self.driver, self.detector = driver, detector
        self.last_dom_text = ""
        self.had_non_text_content = False
        self.driver._upool_modern_messages = False

    async def stream_until_complete(self, *, initial_count: int, timeout: float,
                                    turn_anchor, budgets=None, model=None):
        from chatgpt_web2api.cdp_driver import GenerationStuckError, StreamChunk

        self.last_dom_text = ""
        self.had_non_text_content = False
        self.driver._upool_modern_messages = False
        self.driver._upool_backend_failure = getattr(self.driver, "_upool_backend_failure", None)
        stream = self.detector.stream_until_complete(
            initial_count=initial_count, timeout=timeout, turn_anchor=turn_anchor,
            budgets=budgets, model=model)
        pending = asyncio.create_task(anext(stream))
        started = time.monotonic()
        deadline = started + timeout
        if budgets is not None:
            deadline = min(deadline, started + budgets.hard_timeout_seconds)
        last_probe = float("-inf")
        conversation_id = ""

        async def terminal_text():
            nonlocal conversation_id
            if self.driver._upool_backend_failure is not None:
                raise self.driver._upool_backend_failure
            if not conversation_id:
                conversation_id = await self.driver._get_live_conversation_id_best_effort()
            if not conversation_id:
                return None
            result = await self.driver._fetch_text_for_turn(conversation_id, turn_anchor)
            if result.status == "matched" and isinstance(result.text, str) and result.text:
                if not result.text.startswith(self.last_dom_text):
                    raise WorkerFailure("ChatGPT changed an already streamed answer. Start a new request.")
                return result.text
            # Ambiguous, stale, non-text and failed reads never become success.
            return None

        async def poll_terminal():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            try:
                return await asyncio.wait_for(terminal_text(), timeout=min(5.0, remaining))
            except asyncio.TimeoutError:
                return None

        try:
            while True:
                if self.driver._upool_backend_failure is not None:
                    raise self.driver._upool_backend_failure
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise GenerationStuckError("phase_1_appear" if not self.last_dom_text else
                                               "phase_2_stream", timeout)
                done, _ = await asyncio.wait(
                    {pending}, timeout=min(0.25, remaining, max(0.001, remaining / 2)))
                # The pinned detector can swallow a typed read failure and then
                # yield or finish during this await. Check again before output.
                if self.driver._upool_backend_failure is not None:
                    raise self.driver._upool_backend_failure
                if done:
                    try:
                        chunk = pending.result()
                    except StopAsyncIteration:
                        self.had_non_text_content = self.detector.had_non_text_content
                        return
                    except GenerationStuckError:
                        final = await poll_terminal()
                        if final is None:
                            raise
                        suffix = final[len(self.last_dom_text):]
                        self.last_dom_text = final
                        if suffix:
                            yield StreamChunk(delta=suffix)
                        return
                    if not self.driver._upool_modern_messages:
                        if chunk.delta:
                            self.last_dom_text += chunk.delta
                        yield chunk
                    # Modern MarkdownRoot renders formatted DOM text, which
                    # loses Markdown syntax/code fences. Buffer those deltas
                    # until the anchored backend supplies the canonical answer.
                    pending = asyncio.create_task(anext(stream))
                if time.monotonic() - last_probe >= _TURN_POLL_INTERVAL:
                    last_probe = time.monotonic()
                    final = await poll_terminal()
                    if final is not None:
                        suffix = final[len(self.last_dom_text):]
                        self.last_dom_text = final
                        if suffix:
                            yield StreamChunk(delta=suffix)
                        return
        finally:
            if not pending.done():
                pending.cancel()
            try:
                await pending
            except (asyncio.CancelledError, StopAsyncIteration, Exception):
                pass
            await stream.aclose()


def _compatible_driver_type(base):
    """Keep compatibility changes local to each worker-owned CDP driver."""
    class CompatibleDriver(base):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self._upool_modern_messages = False
            self._upool_backend_failure = None
            self._upool_projection_cache = None
            self._upool_projection_lock = asyncio.Lock()
            self._upool_submit_observer = None
            self._upool_request_active = False
            self._completion = _AnchoredCompletion(self, self._completion)

        def _reset_backend_observation(self):
            self._upool_backend_failure = None
            self._upool_projection_cache = None

        async def _refresh_token(self):
            self._last_refresh_attempt_at = time.time()
            session = await _read_session(self)
            self._access_token = session["token"]
            self._user_name = session.get("user", "")
            self._token_fetched_at = time.time()
            if not self._upool_request_active:
                self._reset_backend_observation()

        async def click_send(self):
            scope = getattr(getattr(self, "_identity_listener", None), "_active_scope", None)
            if self._upool_request_active and scope is not None and not scope._closed:
                if self._upool_submit_observer is not None:
                    self._upool_submit_observer.close()
                self._upool_submit_observer = _SubmitStatus(self, scope)
            await super().click_send()
            if self._upool_backend_failure is not None:
                raise self._upool_backend_failure

        async def send_and_stream(self, text, timeout=120, *, budgets=None, model=None):
            if self._upool_request_active:
                raise WorkerFailure("The ChatGPT account is already generating.", 503)
            self._reset_backend_observation()
            self._upool_request_active = True
            stream = super().send_and_stream(text, timeout=timeout, budgets=budgets, model=model)
            try:
                async for chunk in stream:
                    if self._upool_backend_failure is not None:
                        raise self._upool_backend_failure
                    yield chunk
                if self._upool_backend_failure is not None:
                    raise self._upool_backend_failure
            finally:
                if self._upool_submit_observer is not None:
                    self._upool_submit_observer.close()
                    self._upool_submit_observer = None
                self._upool_request_active = False
                await stream.aclose()

        async def close(self):
            if self._upool_submit_observer is not None:
                self._upool_submit_observer.close()
                self._upool_submit_observer = None
            await super().close()

        async def _js_with_data_strict(self, expression: str, data: dict, timeout: float = 15):
            from chatgpt_web2api.backend_projection import CONVERSATION_PROJECTION_JS

            if expression != CONVERSATION_PROJECTION_JS:
                return await super()._js_with_data_strict(expression, data, timeout=timeout)
            if self._upool_backend_failure is not None:
                raise self._upool_backend_failure
            conversation_id = data.get("conv_id")
            async with self._upool_projection_lock:
                # Text polling, the pinned end-turn detector and final reconcile
                # observe one shared graph. Never double-fetch to inspect status.
                cached = self._upool_projection_cache
                if (cached is not None and cached[0] == conversation_id and
                        time.monotonic() - cached[1] < _TURN_POLL_INTERVAL):
                    result = cached[2]
                    if isinstance(result, WorkerFailure):
                        _latch_backend_failure(self, result)
                        raise self._upool_backend_failure
                    return result
                bound = min(timeout, _PROJECTION_TIMEOUT)
                raw = await asyncio.wait_for(super()._js_with_data_strict(
                    _projection_query(expression), data, timeout=bound), timeout=bound)
                if self._upool_backend_failure is not None:
                    raise self._upool_backend_failure
                failure = None
                if isinstance(raw, str) and raw.startswith('{"__status"'):
                    try:
                        state = json.loads(raw)
                    except (ValueError, TypeError):
                        state = {}
                    status = state.get("__status")
                    failure = _backend_http_failure(status, state.get("__retry_after"))
                    # The pinned auth breaker and transient-404 handling retain
                    # their exact semantics. Response bodies are never read here.
                self._upool_projection_cache = (conversation_id, time.monotonic(), failure or raw)
                if failure is not None:
                    _latch_backend_failure(self, failure)
                    raise self._upool_backend_failure
                return raw

        async def _js_strict(self, expression: str, timeout: float = 15):
            result = await super()._js_strict(_message_query(expression), timeout=timeout)
            if _COMPLETION_SNAPSHOT in expression and isinstance(result, str):
                try:
                    state = json.loads(result)
                    if isinstance(state, dict):
                        self._upool_modern_messages = state.get("upool_modern_message") is True
                except (ValueError, TypeError):
                    pass
            return result

        async def navigate_new_chat(self, gizmo_id=None):
            # Workers always replay a complete transcript into a fresh chat.
            # A composer on a retained /c page is not proof of isolation.
            if gizmo_id is not None:
                raise WorkerFailure("Could not open a fresh ChatGPT conversation.", 503)
            self._reset_backend_observation()
            await super().navigate_new_chat()
            deadline = time.monotonic() + _FRESH_CHAT_TIMEOUT
            while time.monotonic() < deadline:
                try:
                    remaining = deadline - time.monotonic()
                    raw = await self._js_strict(_FRESH_CHAT_SNAPSHOT, timeout=min(1.5, remaining))
                    state = json.loads(raw) if isinstance(raw, str) else raw
                    if (isinstance(state, dict) and state.get("origin") == "https://chatgpt.com" and
                            state.get("pathname") == "/" and state.get("main") is True and
                            state.get("previous") == 0 and state.get("composer") is True):
                        self._current_conv_id = None
                        self._upool_modern_messages = False
                        return
                except Exception:
                    pass
                await asyncio.sleep(min(0.1, max(0, deadline - time.monotonic())))
            raise WorkerFailure("Could not open a fresh ChatGPT conversation.", 503)

    return CompatibleDriver


_MODEL_PICKER_TIMEOUT = 6.0
_MODEL_PICKER_SNAPSHOT = r"""(() => {
  const visible = el => {
    const r = el.getBoundingClientRect(), s = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && s.display !== 'none' &&
      s.visibility !== 'hidden' && r.bottom > 0 && r.right > 0 &&
      r.top < innerHeight && r.left < innerWidth;
  };
  const geometry = el => {
    const r = el.getBoundingClientRect();
    return {x: (Math.max(0, r.left) + Math.min(innerWidth, r.right)) / 2,
            y: (Math.max(0, r.top) + Math.min(innerHeight, r.bottom)) / 2};
  };
  let trigger = null;
  for (const selector of [
    'button[data-codex-intelligence-trigger="true"][aria-haspopup="menu"]',
    'button[data-composer-navigation-target="reasoning"][aria-haspopup="menu"]',
    'button#model-selector-btn',
    'button[aria-label*="model" i][aria-haspopup="menu"]',
    'button[data-testid*="model" i][aria-haspopup="menu"]'
  ]) {
    const matches = [...document.querySelectorAll(selector)].filter(visible);
    if (matches.length > 1) break;
    if (matches.length === 1) { trigger = geometry(matches[0]); break; }
  }
  const menus = [...document.querySelectorAll('[role="menu"], [role="listbox"]')].filter(visible);
  const elements = [...new Set(menus.flatMap(menu =>
    [...menu.querySelectorAll('[role="menuitemradio"], [role="menuitem"], [role="option"]')]
  ))].filter(visible);
  const items = elements.map(el => {
    const row = el.querySelector('[data-menu-row-content="true"]');
    const primary = row?.querySelector('span');
    const labels = [el.getAttribute('aria-label'), primary?.textContent];
    if (!primary) labels.push(...[...el.querySelectorAll('span, [data-model-label]')]
      .filter(node => !node.querySelector('span, [data-model-label]'))
      .map(node => node.textContent));
    if (!labels.some(Boolean)) labels.push(el.textContent);
    return {...geometry(el), labels: labels.filter(Boolean).map(text => text.trim()),
      slugs: ['data-slug', 'data-model-slug', 'data-model'].map(name => el.getAttribute(name)).filter(Boolean),
      disabled: el.getAttribute('aria-disabled') === 'true' || el.hasAttribute('disabled'),
      checked: el.getAttribute('aria-checked') === 'true' ||
        el.getAttribute('aria-selected') === 'true' ||
        el.getAttribute('data-model-selected') === 'true' ||
        el.getAttribute('data-state') === 'checked'};
  });
  const composer = [...document.querySelectorAll(
    'div[role="textbox"]#prompt-textarea, div[role="textbox"].ProseMirror, textarea#prompt-textarea'
  )].some(visible);
  return JSON.stringify({origin: location.origin, composer, trigger,
    menu_open: menus.length > 0, items});
})()"""


def _model_label(value: str) -> str:
    """Canonicalize punctuation, while preserving complete model variants."""
    return ''.join(character for character in unicodedata.normalize("NFKC", value).casefold()
                   if character.isalnum())


def conversation_prompt(messages: list[dict]) -> str:
    """Replay complete context in a new web conversation, without sharing IDs."""
    if not messages or any(not isinstance(message, dict) or message.get("role") not in
                           {"system", "developer", "user", "assistant", "tool"} for message in messages):
        raise WorkerFailure("A complete text conversation is required.", 400)
    if any(not isinstance(message.get("content", ""), str) for message in messages):
        raise WorkerFailure("Only text conversation content is supported.", 400)
    return ("Continue the conversation represented by the JSON transcript below. "
            "Follow its system and developer instructions and answer the final user turn. "
            "Return only the next assistant response, without transcript wrappers.\n\n" +
            json.dumps(messages, ensure_ascii=False))


def cdp_cookies(cookies: list[dict]) -> list[dict]:
    allowed = {"name", "value", "domain", "path", "secure", "httpOnly", "sameSite", "expires", "url"}
    result = []
    for cookie in cookies:
        domain = str(cookie.get("domain", "chatgpt.com")).lower().lstrip(".")
        if domain != "chatgpt.com" and not domain.endswith(".chatgpt.com"):
            raise WorkerFailure("Cookie domain must belong to chatgpt.com.", 400)
        item = {name: value for name, value in cookie.items() if name in allowed}
        item.setdefault("domain", ".chatgpt.com")
        item.setdefault("path", "/")
        if item.get("expires", -1) <= 0:
            item.pop("expires", None)
        if not item.get("name") or not isinstance(item.get("value"), str):
            raise WorkerFailure("Invalid cookie record.", 400)
        if item.get("url") and not str(item["url"]).startswith("https://chatgpt.com/"):
            raise WorkerFailure("Invalid cookie origin.", 400)
        result.append(item)
    if not result:
        raise WorkerFailure("No ChatGPT cookies were supplied.", 400)
    return result


class BrowserWorker:
    def __init__(self, config: dict) -> None:
        self.config = config
        self.driver = None
        self.chrome = None
        self.lock = asyncio.Lock()
        self._models: list[str] = []
        self._model_titles: dict[str, str] = {}

    def _new_driver(self, target_id: str | None = None):
        from chatgpt_web2api import tab_registry
        from chatgpt_web2api.cdp_driver import CDPDriver
        tab_registry.REGISTRY_DIR = Path(self.config["state"])
        driver = _compatible_driver_type(CDPDriver)(
            cdp_port=self.config["cdp_port"], tab_mode="owned" if target_id else "adopt",
            instance_id=self.config["account_id"])
        if target_id:
            driver._target_id = target_id
            driver._owns_target = True
        return driver

    async def _ensure(self) -> None:
        if self.chrome is not None and self.chrome.poll() is not None:
            raise WorkerFailure("The managed browser exited. Restart this account's runtime.", 503)
        if self.driver is None:
            self.driver = self._new_driver()
        if not self.driver.is_connected:
            try:
                await self.driver.connect()
            except (Exception, asyncio.CancelledError) as error:
                # CDP connect opens its websocket before refreshing auth. A
                # failed refresh must not leave a seemingly connected driver
                # that subsequent requests would reuse without authenticating.
                failed, self.driver = self.driver, None
                try:
                    await asyncio.wait_for(failed.close(), timeout=2)
                except (Exception, asyncio.CancelledError):
                    pass
                if isinstance(error, asyncio.CancelledError):
                    raise
                if isinstance(error, WorkerFailure):
                    raise error from None
                raise WorkerFailure("The managed browser could not connect to ChatGPT. Retry or open the managed profile.", 503) from None

    async def inject(self, cookies: list[dict]) -> dict:
        async with self.lock:
            from websockets import connect
            validated = cdp_cookies(cookies)
            if self.driver:
                previous, self.driver = self.driver, None
                try:
                    await asyncio.wait_for(previous.close(), timeout=2)
                except Exception:
                    pass
            port = self.config["cdp_port"]
            def discover():
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version", timeout=3) as response:
                    return json.load(response)
            browser = await asyncio.to_thread(discover)
            endpoint = browser.get("webSocketDebuggerUrl") if isinstance(browser, dict) else None
            if not isinstance(endpoint, str) or not endpoint:
                raise WorkerFailure("The managed browser is unavailable.", 503)
            # A recently closed page can remain briefly in /json/list. Write
            # cookies through the browser endpoint, then own a NEW known page;
            # never pick the first page and send cookies to its dying socket.
            async with connect(endpoint, max_size=8 * 1024 * 1024) as ws:
                async def command(identifier: int, method: str, params: dict):
                    await ws.send(json.dumps({"id": identifier, "method": method, "params": params}))
                    deadline = time.monotonic() + 15
                    while True:
                        remaining = deadline - time.monotonic()
                        if remaining <= 0:
                            raise WorkerFailure("The managed browser did not complete the cookie import.", 503)
                        result = json.loads(await asyncio.wait_for(ws.recv(), timeout=remaining))
                        if result.get("id") == identifier:
                            if "error" in result:
                                raise WorkerFailure(
                                    "Chrome rejected the cookie import." if method == "Storage.setCookies" else
                                    "Could not open the managed ChatGPT profile.",
                                    400 if method == "Storage.setCookies" else 503)
                            return result.get("result", {})
                await command(1, "Storage.setCookies", {"cookies": validated})
                created = await command(2, "Target.createTarget", {"url": "https://chatgpt.com/?model=auto"})
                target_id = created.get("targetId") if isinstance(created, dict) else None
                if not isinstance(target_id, str) or not target_id:
                    raise WorkerFailure("Could not open the managed ChatGPT profile.", 503)
                self.driver = self._new_driver(target_id)
            return await self._facts()

    async def _facts(self) -> dict:
        await self._ensure()
        session = await _read_session(self.driver)
        facts = {"email": session.get("email", ""), "name": session.get("user", ""),
                 "user_id": session.get("user_id", "")}
        if not facts.get("user_id") and not facts.get("email"):
            raise WorkerFailure("The ChatGPT session did not identify its account. Retry verification.", 502)
        facts["models"] = await self._catalog()
        return facts

    async def facts(self) -> dict:
        async with self.lock:
            return await self._facts()

    async def _catalog(self) -> list[str]:
        await self._ensure()
        models = await self.driver.get_models()
        records = [model for model in models if isinstance(model, dict) and
                   isinstance(model.get("slug"), str) and model["slug"]]
        self._models = list(dict.fromkeys(model["slug"] for model in records))
        self._model_titles = {model["slug"]: model["title"] for model in records
                              if isinstance(model.get("title"), str) and model["title"].strip()}
        if not self._models:
            raise WorkerFailure("ChatGPT did not return the account's available models.", 503)
        # This explicit alias asks ChatGPT to choose its current/default model.
        # It is never substituted for a failed concrete model selection.
        if "auto" not in self._models:
            self._models.append("auto")
        return self._models

    async def models(self) -> dict:
        async with self.lock:
            return {"object": "list", "data": [{"id": model, "object": "model"}
                                               for model in await self._catalog()]}

    async def _picker_snapshot(self) -> dict:
        raw = await self.driver._js_strict(_MODEL_PICKER_SNAPSHOT, timeout=5)
        state = json.loads(raw) if isinstance(raw, str) else raw
        if not isinstance(state, dict):
            raise WorkerFailure("ChatGPT model picker is unavailable.", 503)
        return state

    async def _wait_picker(self, *, menu_open: bool = False) -> dict | None:
        deadline = time.monotonic() + _MODEL_PICKER_TIMEOUT
        while time.monotonic() < deadline:
            state = await self._picker_snapshot()
            if (state.get("origin") == "https://chatgpt.com" and state.get("composer") and
                    state.get("trigger") and (not menu_open or state.get("menu_open"))):
                return state
            await asyncio.sleep(0.1)
        return None

    def _matching_model_item(self, state: dict, slug: str) -> dict | None:
        items = [item for item in state.get("items", []) if isinstance(item, dict) and
                 not item.get("disabled")]
        exact = [item for item in items if slug in item.get("slugs", [])]
        if exact:
            return exact[0] if len(exact) == 1 else None
        title = self._model_titles.get(slug, slug)
        # A shared display title cannot identify one of two distinct backend slugs.
        if any(other != slug and _model_label(other_title) == _model_label(title)
               for other, other_title in self._model_titles.items()):
            return None
        expected = {_model_label(slug), _model_label(title)} - {""}
        matches = [item for item in items if not item.get("slugs") and any(
            isinstance(label, str) and _model_label(label) in expected
            for label in item.get("labels", []))]
        return matches[0] if len(matches) == 1 else None

    async def _click_picker_element(self, element: dict) -> None:
        if not all(isinstance(element.get(name), (int, float)) and
                   math.isfinite(element[name]) and element[name] >= 0 for name in ("x", "y")):
            raise WorkerFailure("ChatGPT model picker is unavailable.", 503)
        coordinates = {"x": element["x"], "y": element["y"]}
        # React/Radix ignores synthetic element.click() on the current picker.
        await self.driver._cdp("Input.dispatchMouseEvent", {"type": "mouseMoved", **coordinates})
        for event in ("mousePressed", "mouseReleased"):
            await self.driver._cdp("Input.dispatchMouseEvent", {
                "type": event, "button": "left", "clickCount": 1, **coordinates})

    async def _dismiss_picker(self) -> None:
        for event in ("keyDown", "keyUp"):
            await self.driver._cdp("Input.dispatchKeyEvent", {
                "type": event, "key": "Escape", "code": "Escape", "windowsVirtualKeyCode": 27})

    async def _select_model(self, slug: str) -> bool:
        """Select and verify the exact model; never silently use the active one."""
        try:
            state = await self._wait_picker()
            if state is None:
                return False
            if not state.get("menu_open"):
                await self._click_picker_element(state["trigger"])
                state = await self._wait_picker(menu_open=True)
            if state is None:
                return False
            item = self._matching_model_item(state, slug)
            if item is None:
                return False
            if not item.get("checked"):
                await self._click_picker_element(item)
                # Selection normally closes the menu. Reopen it to observe the
                # checked item; the driver cache and picker title are not proof.
                deadline = time.monotonic() + _MODEL_PICKER_TIMEOUT
                while time.monotonic() < deadline:
                    state = await self._picker_snapshot()
                    if not state.get("menu_open"):
                        if not state.get("trigger"):
                            return False
                        await self._click_picker_element(state["trigger"])
                        state = await self._wait_picker(menu_open=True)
                        if state is None:
                            return False
                    selected = self._matching_model_item(state, slug)
                    if selected is not None and selected.get("checked"):
                        break
                    await asyncio.sleep(0.1)
                else:
                    return False
            return True
        except Exception:
            return False
        finally:
            try:
                await self._dismiss_picker()
            except Exception:
                pass

    async def reveal(self) -> dict:
        # Raw browser CDP remains usable even when authentication has expired.
        from websockets import connect
        def discover():
            with urllib.request.urlopen(f"http://127.0.0.1:{self.config['cdp_port']}/json/version", timeout=3) as response:
                browser = json.load(response)
            with urllib.request.urlopen(f"http://127.0.0.1:{self.config['cdp_port']}/json/list", timeout=3) as response:
                pages = json.load(response)
            target = next((page["id"] for page in pages if page.get("type") == "page"), None)
            return browser, target
        browser, target = await asyncio.to_thread(discover)
        if target is None:
            raise WorkerFailure("The managed browser has no page target.")
        async with connect(browser["webSocketDebuggerUrl"]) as ws:
            await ws.send(json.dumps({"id": 1, "method": "Browser.getWindowForTarget", "params": {"targetId": target}}))
            while True:
                item = json.loads(await ws.recv())
                if item.get("id") == 1:
                    window = item.get("result", {}).get("windowId")
                    break
            if window is None:
                raise WorkerFailure("Could not locate the managed browser window.")
            await ws.send(json.dumps({"id": 2, "method": "Browser.setWindowBounds",
                                      "params": {"windowId": window, "bounds": {"windowState": "normal"}}}))
            while True:
                result = json.loads(await ws.recv())
                if result.get("id") == 2:
                    if "error" in result:
                        raise WorkerFailure("Could not reveal the managed browser window.")
                    return {"status": "ok"}

    async def complete(self, payload: dict, output: queue.Queue) -> None:
        from chatgpt_web2api.cdp_driver import AuthExpiredError, RateLimitError
        async def put(item):
            while output.full():
                await asyncio.sleep(0.02)
            output.put_nowait(item)
        try:
            async with self.lock:
                prompt = conversation_prompt(payload.get("messages", []))
                await self._ensure()
                model = payload.get("model", "auto")
                if not self._models:
                    await self._catalog()
                if model not in self._models:
                    raise WorkerFailure("The selected model is not available to this account.", 400)
                # This navigation is mandatory for each request, including tool repair.
                await self.driver.navigate_new_chat()
                if model != "auto" and not await self._select_model(model):
                    raise WorkerFailure("ChatGPT could not select the requested model.", 503)
                stream = self.driver.send_and_stream(prompt, timeout=150, model=model)
                try:
                    # Read first output before committing HTTP success, preserving failover.
                    first = await anext(stream)
                    await put(("ready", None))
                    await put(("chunk", first))
                    async for chunk in stream:
                        await put(("chunk", chunk))
                    await put(("done", None))
                except asyncio.CancelledError:
                    try:
                        await asyncio.wait_for(self.driver._js("document.querySelector('[data-testid=stop-button]')?.click()"), timeout=2)
                    except Exception:
                        pass
                    raise
                finally:
                    # Release CDP capture scopes before another request can navigate.
                    await stream.aclose()
        except asyncio.CancelledError:
            raise
        except AuthExpiredError:
            await put(("error", WorkerFailure("ChatGPT authentication has expired.", 401)))
        except RateLimitError as error:
            await put(("error", WorkerFailure("ChatGPT usage limit reached.", 429, error.retry_after)))
        except WorkerFailure as error:
            await put(("error", error))
        except Exception:
            await put(("error", WorkerFailure("ChatGPT generation failed. Check the managed profile and retry.")))


class WorkerServer(ThreadingHTTPServer):
    daemon_threads = True
    def __init__(self, config: dict, worker: BrowserWorker, loop: asyncio.AbstractEventLoop, chrome=None):
        self.config, self.worker, self.loop = config, worker, loop
        self.chrome = chrome
        super().__init__(("127.0.0.1", config["port"]), WorkerHandler)


class WorkerHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    def log_message(self, format: str, *args) -> None:
        pass

    def _authorized(self) -> bool:
        expected = "Bearer " + self.server.config["api_key"]
        return hmac.compare_digest(self.headers.get("Authorization", ""), expected)

    def _json(self, data: dict, status: int = 200, retry_after: int | None = None) -> None:
        raw = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        if retry_after is not None:
            self.send_header("Retry-After", str(retry_after))
        self.end_headers()
        self.wfile.write(raw)

    def _failure(self, error: WorkerFailure) -> None:
        self._json({"error": {"message": str(error), "type": "upstream_error"}}, error.status, error.retry_after)

    def _payload(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0 or length > 8 * 1024 * 1024:
            raise WorkerFailure("Invalid request body length.", 400)
        data = json.loads(self.rfile.read(length))
        if not isinstance(data, dict):
            raise WorkerFailure("Expected a JSON object.", 400)
        return data

    def _call(self, awaitable):
        future = asyncio.run_coroutine_threadsafe(awaitable, self.server.loop)
        try:
            return future.result(timeout=90)
        except concurrent.futures.TimeoutError:
            future.cancel()
            raise WorkerFailure("The managed browser operation timed out.", 504) from None

    def do_GET(self) -> None:
        if not self._authorized():
            self._json({"error": {"message": "Invalid worker key"}}, 401)
            return
        try:
            if self.path == "/health":
                healthy = self.server.chrome is None or self.server.chrome.poll() is None
                self._json({"status": "ready" if healthy else "broken"}, 200 if healthy else 503)
            elif self.path == "/v1/models":
                self._json(self._call(self.server.worker.models()))
            else:
                self._json({"error": {"message": "Unknown route"}}, 404)
        except WorkerFailure as error:
            self._failure(error)
        except Exception:
            self._failure(WorkerFailure("Managed browser operation failed."))

    def do_POST(self) -> None:
        if not self._authorized():
            self._json({"error": {"message": "Invalid worker key"}}, 401)
            return
        try:
            payload = self._payload()
            if self.path == "/internal/verify":
                self._json(self._call(self.server.worker.inject(payload.get("cookies", []))))
            elif self.path == "/internal/facts":
                self._json(self._call(self.server.worker.facts()))
            elif self.path == "/internal/reveal":
                self._json(self._call(self.server.worker.reveal()))
            elif self.path == "/v1/chat/completions":
                self._complete(payload)
            else:
                self._json({"error": {"message": "Unknown route"}}, 404)
        except WorkerFailure as error:
            self._failure(error)
        except (ValueError, TypeError):
            self._failure(WorkerFailure("Invalid JSON request.", 400))
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception:
            self._failure(WorkerFailure("Managed browser operation failed."))

    def _complete(self, payload: dict) -> None:
        output = queue.Queue(maxsize=256)
        task = asyncio.run_coroutine_threadsafe(self.server.worker.complete(payload, output), self.server.loop)
        monitoring_done = threading.Event()
        def watch_eof():
            while not monitoring_done.wait(0.1):
                try:
                    readable, _, _ = select.select([self.connection], [], [], 0)
                    if not readable or self.connection.recv(1, socket.MSG_PEEK):
                        continue
                except (OSError, ValueError):
                    if monitoring_done.is_set():
                        return
                task.cancel()
                try:
                    output.put_nowait(("error", WorkerFailure("The client cancelled the request.", 499)))
                except queue.Full:
                    pass
                return
        threading.Thread(target=watch_eof, name="upool-worker-client-eof", daemon=True).start()
        identifier, created = "chatcmpl-" + uuid.uuid4().hex, int(time.time())
        def event(delta: dict, finish=None):
            return {"id": identifier, "object": "chat.completion.chunk", "created": created,
                    "model": payload.get("model"), "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}
        def send(data):
            self.wfile.write(b"data: " + json.dumps(data).encode() + b"\n\n")
            self.wfile.flush()
        try:
            kind, value = output.get(timeout=180)
            if kind == "error":
                self._failure(value)
                return
            streaming = bool(payload.get("stream"))
            if streaming:
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Connection", "close")
                self.end_headers()
                self.close_connection = True
                send(event({"role": "assistant"}))
            parts = []
            while True:
                kind, value = output.get(timeout=180)
                if kind == "error":
                    if streaming:
                        send({"error": {"message": str(value), "type": "upstream_error", "status": value.status,
                                        "code": "rate_limited" if value.status == 429 else "authentication_required" if value.status == 401 else "upstream_error",
                                        "retry_after": value.retry_after}})
                    else:
                        self._failure(value)
                    return
                if kind == "done":
                    break
                if value.delta:
                    if streaming:
                        send(event({"content": value.delta}))
                    else:
                        parts.append(value.delta)
            if streaming:
                send(event({}, "stop"))
                self.wfile.write(b"data: [DONE]\n\n")
                self.wfile.flush()
            else:
                self._json({"id": identifier, "object": "chat.completion", "created": created,
                            "model": payload.get("model"), "choices": [{"index": 0, "message":
                            {"role": "assistant", "content": "".join(parts)}, "finish_reason": "stop"}]})
        finally:
            monitoring_done.set()
            task.cancel()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config")
    parser.add_argument("--version", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.version:
        print("U-Pool ChatGPT Worker 1.0.0; Web2API 497527dceabfa3f95961e23c291e618c5570f1ac")
        return 0
    if args.self_test:
        # Validate dynamic imports in the compiled artifact without a browser/account.
        with tempfile.TemporaryDirectory(prefix="upool-worker-smoke-") as state:
            os.environ["USERPROFILE"] = state
            os.environ["HOME"] = state
            from chatgpt_web2api.cdp_driver import CDPDriver
            CDPDriver(tab_mode="adopt", instance_id="packaging-smoke")
        print("Worker CDP dependencies imported successfully")
        return 0
    if not args.config:
        parser.error("--config is required")
    config = json.loads(Path(args.config).read_text(encoding="utf-8"))
    # Upstream diagnostics and file locks default to Path.home() at import.
    # The worker is isolated, so its HOME is always the managed account state.
    os.environ["USERPROFILE"] = config["state"]
    os.environ["HOME"] = config["state"]
    os.environ.pop("W2A_DIAGNOSE", None)
    logging.disable(logging.CRITICAL)  # Upstream messages can contain user/session data.
    profile = Path(config["profile"])
    profile.mkdir(parents=True, exist_ok=True)
    Path(config["state"]).mkdir(parents=True, exist_ok=True)
    chrome = subprocess.Popen([config["chrome"], "--remote-debugging-address=127.0.0.1",
                               "--remote-debugging-port=" + str(config["cdp_port"]),
                               "--user-data-dir=" + str(profile), "--start-minimized", "--no-first-run",
                               "--no-default-browser-check", "about:blank"],
                              stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(150):
            if chrome.poll() is not None:
                raise RuntimeError("Managed Chrome exited")
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{config['cdp_port']}/json/version", timeout=0.2):
                    break
            except OSError:
                time.sleep(0.1)
        else:
            raise RuntimeError("Managed Chrome did not start")
        loop = asyncio.new_event_loop()
        thread = threading.Thread(target=loop.run_forever, daemon=True)
        thread.start()
        worker = BrowserWorker(config)
        worker.chrome = chrome
        server = WorkerServer(config, worker, loop, chrome)
        try:
            server.serve_forever()
        finally:
            server.server_close()
            loop.call_soon_threadsafe(loop.stop)
            thread.join(timeout=3)
    finally:
        chrome.terminate()
        try:
            chrome.wait(timeout=5)
        except subprocess.TimeoutExpired:
            chrome.kill()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
