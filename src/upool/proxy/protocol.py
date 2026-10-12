"""Stateless text/function compatibility across web and API providers."""

from __future__ import annotations

import copy
import itertools
import json
import math
import re
import time
import uuid
from collections.abc import Iterable, Iterator
from typing import Any

from .models import ProxyError

_COMMON = {"model", "stream", "tools", "tool_choice", "parallel_tool_calls", "store", "metadata", "temperature", "top_p"}
_CHAT = _COMMON | {"messages", "max_tokens", "max_completion_tokens", "stop", "presence_penalty", "frequency_penalty", "seed", "n", "stream_options", "response_format", "reasoning_effort"}
_RESPONSES = _COMMON | {"input", "instructions", "max_output_tokens", "previous_response_id", "background", "text", "reasoning", "include", "truncation"}
_WEB_UNSUPPORTED = {"temperature", "top_p", "max_tokens", "max_completion_tokens", "max_output_tokens", "stop", "presence_penalty", "frequency_penalty", "seed", "reasoning", "reasoning_effort"}
_NAME = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_SCHEMA_KEYS = {"type", "properties", "required", "additionalProperties", "items", "enum", "const", "description", "title", "default", "minimum", "maximum", "minLength", "maxLength", "minItems", "maxItems", "pattern", "anyOf", "oneOf", "allOf", "$schema"}
_SCHEMA_TYPES = {"object", "array", "string", "integer", "number", "boolean", "null"}


def _bad(message: str) -> None:
    raise ProxyError(message, 400, "invalid_request")


def _keys(value: dict, allowed: set[str], location: str) -> None:
    if set(value) - allowed:
        _bad(f"Unsupported {location} field: {sorted(set(value) - allowed)[0]}.")


def parse_model(model: str) -> tuple[str, str]:
    if not isinstance(model, str) or "/" not in model:
        _bad("Model must use the chatgpt/<model> or grok/<model> prefix.")
    provider, name = model.split("/", 1)
    if provider not in {"chatgpt", "grok"} or not name or not re.fullmatch(r"[A-Za-z0-9_.:-]+", name):
        _bad("Invalid provider or model name.")
    return provider, name


def _text(content: Any, *, response: bool = False, nullable: bool = False) -> str | None:
    if nullable and content is None:
        return None
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        _bad("Only text message content is supported.")
    pieces = []
    for part in content:
        if not isinstance(part, dict) or not isinstance(part.get("type"), str) or part.get("type") not in ({"input_text", "output_text"} if response else {"text"}):
            _bad("Only text content parts are supported.")
        _keys(part, {"type", "text", "annotations", "logprobs"} if response else {"type", "text"}, "content")
        if not isinstance(part.get("text"), str):
            _bad("Text content must be a string.")
        if part.get("annotations") or part.get("logprobs"):
            _bad("Annotated or logprob input is unsupported.")
        pieces.append(part["text"])
    return "\n".join(pieces)


def _function(function: Any, *, response: bool = False) -> None:
    if not isinstance(function, dict):
        _bad("Function definition must be an object.")
    _keys(function, {"name", "description", "parameters", "strict"} | ({"type"} if response else set()), "function")
    if not isinstance(function.get("name"), str) or not _NAME.fullmatch(function["name"]):
        _bad("Function name must contain 1-64 letters, digits, underscores or hyphens.")
    if "description" in function and not isinstance(function["description"], str):
        _bad("Function description must be text.")
    if "parameters" in function and not isinstance(function["parameters"], dict):
        _bad("Function parameters must be a JSON schema object.")
    if "strict" in function and type(function["strict"]) is not bool:
        _bad("Function strict must be a boolean.")


def _validate_web_schema(schema: Any, depth: int = 0) -> None:
    if depth > 32 or not isinstance(schema, dict):
        _bad("ChatGPT function schemas must contain objects with at most 32 nesting levels.")
    _keys(schema, _SCHEMA_KEYS, "ChatGPT function schema")
    kind = schema.get("type")
    if kind is not None:
        kinds = kind if isinstance(kind, list) else [kind]
        if not kinds or any(not isinstance(item, str) or item not in _SCHEMA_TYPES for item in kinds) or len(set(kinds)) != len(kinds):
            _bad("Invalid function schema type.")
    for name in ("description", "title", "$schema", "pattern"):
        if name in schema and not isinstance(schema[name], str):
            _bad(f"Function schema {name} must be text.")
    if "pattern" in schema:
        try:
            re.compile(schema["pattern"])
        except re.error:
            _bad("Invalid function schema pattern.")
    if "properties" in schema:
        properties = schema["properties"]
        if not isinstance(properties, dict):
            _bad("Function schema properties must be an object.")
        for child in properties.values():
            _validate_web_schema(child, depth + 1)
    if "required" in schema:
        required = schema["required"]
        if not isinstance(required, list) or any(not isinstance(item, str) for item in required) or len(set(required)) != len(required):
            _bad("Function schema required must contain unique property names.")
    if "items" in schema:
        _validate_web_schema(schema["items"], depth + 1)
    if "additionalProperties" in schema and type(schema["additionalProperties"]) is not bool:
        _validate_web_schema(schema["additionalProperties"], depth + 1)
    for name in ("minLength", "maxLength", "minItems", "maxItems"):
        if name in schema and (type(schema[name]) is not int or schema[name] < 0):
            _bad(f"Function schema {name} must be a nonnegative integer.")
    for name in ("minimum", "maximum"):
        if name in schema and (type(schema[name]) not in {int, float} or not math.isfinite(schema[name])):
            _bad(f"Function schema {name} must be a finite number.")
    if "enum" in schema and (not isinstance(schema["enum"], list) or not schema["enum"]):
        _bad("Function schema enum must be a nonempty array.")
    for name in ("anyOf", "oneOf", "allOf"):
        if name in schema:
            alternatives = schema[name]
            if not isinstance(alternatives, list) or not alternatives:
                _bad(f"Function schema {name} must be a nonempty array.")
            for child in alternatives:
                _validate_web_schema(child, depth + 1)


def validate_request(endpoint: str, payload: Any) -> dict:
    if endpoint not in {"chat/completions", "responses"} or not isinstance(payload, dict):
        _bad("Request must be a JSON object for a supported endpoint.")
    _keys(payload, _CHAT if endpoint == "chat/completions" else _RESPONSES, "request")
    provider, _ = parse_model(payload.get("model"))
    for field in ("stream", "store", "parallel_tool_calls", "background"):
        if field in payload and type(payload[field]) is not bool:
            _bad(f"{field} must be a boolean.")
    if payload.get("store") or payload.get("previous_response_id") is not None or payload.get("background"):
        _bad("Stored, background and previous_response_id requests are unsupported; send full history.")
    if provider == "chatgpt" and _WEB_UNSUPPORTED.intersection(payload):
        _bad(f"ChatGPT web sessions cannot honor {_WEB_UNSUPPORTED.intersection(payload).pop()}.")
    if "n" in payload and (type(payload["n"]) is not int or payload["n"] != 1):
        _bad("Only n=1 is supported.")
    if payload.get("response_format", {"type": "text"}) != {"type": "text"}:
        _bad("Only text response_format is supported.")
    if payload.get("text", {"format": {"type": "text"}}) != {"format": {"type": "text"}}:
        _bad("Only text.format.type=text is supported.")
    if payload.get("include", []) or payload.get("truncation", "disabled") != "disabled":
        _bad("Response includes and automatic truncation are unsupported.")
    if "stream_options" in payload:
        options = payload["stream_options"]
        if not isinstance(options, dict) or set(options) - {"include_usage"} or type(options.get("include_usage", False)) is not bool:
            _bad("Only stream_options.include_usage is supported.")
        if not payload.get("stream"):
            _bad("stream_options requires stream=true.")
    for field in ("max_tokens", "max_completion_tokens", "max_output_tokens"):
        if field in payload and (type(payload[field]) is not int or payload[field] < 1):
            _bad(f"{field} must be a positive integer.")
    for field, low, high in (("temperature", 0, 2), ("top_p", 0, 1), ("presence_penalty", -2, 2), ("frequency_penalty", -2, 2)):
        if field in payload and (type(payload[field]) not in {int, float} or not math.isfinite(payload[field]) or not low <= payload[field] <= high):
            _bad(f"{field} is outside its supported range.")
    if "seed" in payload and type(payload["seed"]) is not int:
        _bad("seed must be an integer.")
    if "stop" in payload and not (isinstance(payload["stop"], str) or isinstance(payload["stop"], list) and 1 <= len(payload["stop"]) <= 4 and all(isinstance(v, str) for v in payload["stop"])):
        _bad("stop must be text or up to four text strings.")
    if "metadata" in payload and (not isinstance(payload["metadata"], dict) or len(payload["metadata"]) > 16 or any(not isinstance(k, str) or not isinstance(v, str) or len(k) > 64 or len(v) > 512 for k, v in payload["metadata"].items())):
        _bad("metadata must contain up to 16 short string pairs.")
    tools = payload.get("tools", [])
    if not isinstance(tools, list):
        _bad("tools must be an array.")
    names = set()
    for tool in tools:
        if not isinstance(tool, dict) or tool.get("type") != "function":
            _bad("Only function tools are supported.")
        if endpoint == "chat/completions":
            _keys(tool, {"type", "function"}, "tool")
            function = tool.get("function")
        else:
            function = tool
        _function(function, response=endpoint == "responses")
        if provider == "chatgpt":
            _validate_web_schema(function.get("parameters", {}))
        if function["name"] in names:
            _bad("Function names must be unique.")
        names.add(function["name"])
    choice = payload.get("tool_choice", "auto")
    if isinstance(choice, dict):
        if endpoint == "responses":
            _keys(choice, {"type", "name"}, "tool_choice")
            name = choice.get("name")
        else:
            _keys(choice, {"type", "function"}, "tool_choice")
            fn = choice.get("function")
            if not isinstance(fn, dict) or set(fn) != {"name"}:
                _bad("Invalid tool_choice function.")
            name = fn["name"]
        if choice.get("type") != "function" or name not in names:
            _bad("tool_choice must reference a supplied function.")
    elif not isinstance(choice, str) or choice not in {"auto", "none", "required"} or choice == "required" and not tools:
        _bad("Invalid tool_choice.")
    if endpoint == "chat/completions":
        _validate_messages(payload.get("messages"))
    else:
        if "instructions" in payload and not isinstance(payload["instructions"], str):
            _bad("instructions must be text.")
        _response_messages(payload.get("input"))
    return copy.deepcopy(payload)


def _validate_messages(messages: Any) -> None:
    if not isinstance(messages, list) or not messages:
        _bad("messages must be a nonempty array.")
    pending: set[str] = set()
    for message in messages:
        if not isinstance(message, dict):
            _bad("Each message must be an object.")
        _keys(message, {"role", "content", "name", "tool_calls", "tool_call_id"}, "message")
        role = message.get("role")
        if not isinstance(role, str) or role not in {"system", "developer", "user", "assistant", "tool"}:
            _bad("Unsupported message role.")
        if "name" in message and (not isinstance(message["name"], str) or not _NAME.fullmatch(message["name"])):
            _bad("Invalid message name.")
        _text(message.get("content"), nullable=role == "assistant" and bool(message.get("tool_calls")))
        if role == "tool":
            call_id = message.get("tool_call_id")
            if not isinstance(call_id, str) or call_id not in pending:
                _bad("Tool result must match an earlier unresolved tool call.")
            pending.remove(call_id)
        elif "tool_call_id" in message:
            _bad("tool_call_id belongs only to tool messages.")
        calls = message.get("tool_calls", [])
        if not isinstance(calls, list) or calls and role != "assistant":
            _bad("tool_calls belongs only to assistant messages.")
        for call in calls:
            if not isinstance(call, dict):
                _bad("Invalid tool call.")
            _keys(call, {"id", "type", "function"}, "tool call")
            fn = call.get("function")
            if call.get("type") != "function" or not isinstance(call.get("id"), str) or not call["id"] or call["id"] in pending or not isinstance(fn, dict) or set(fn) != {"name", "arguments"} or not isinstance(fn["name"], str) or not _NAME.fullmatch(fn["name"]) or not isinstance(fn["arguments"], str):
                _bad("Invalid function call.")
            pending.add(call["id"])
    if pending:
        _bad("Full history must include a result for every tool call.")


def _response_messages(value: Any) -> list[dict]:
    if isinstance(value, str):
        return [{"role": "user", "content": value}]
    if not isinstance(value, list) or not value:
        _bad("input must be text or a nonempty array.")
    messages = []
    for item in value:
        if not isinstance(item, dict):
            _bad("Each input item must be an object.")
        kind = item.get("type", "message")
        if kind == "message":
            _keys(item, {"type", "role", "content", "id", "status"}, "input message")
            messages.append({"role": item.get("role"), "content": _text(item.get("content"), response=True)})
        elif kind == "function_call":
            _keys(item, {"type", "id", "call_id", "name", "arguments", "status"}, "input function call")
            call = {"id": item.get("call_id"), "type": "function", "function": {"name": item.get("name"), "arguments": item.get("arguments")}}
            if messages and messages[-1]["role"] == "assistant":
                messages[-1].setdefault("tool_calls", []).append(call)
            else:
                messages.append({"role": "assistant", "content": None, "tool_calls": [call]})
        elif kind == "function_call_output":
            _keys(item, {"type", "id", "call_id", "output", "status"}, "input function result")
            messages.append({"role": "tool", "content": _text(item.get("output"), response=True), "tool_call_id": item.get("call_id")})
        else:
            _bad("Only messages, function_call and function_call_output input items are supported.")
    _validate_messages(messages)
    return messages


def responses_to_chat(payload: dict) -> dict:
    messages = _response_messages(payload["input"])
    if payload.get("instructions"):
        messages.insert(0, {"role": "system", "content": payload["instructions"]})
    result = {key: copy.deepcopy(value) for key, value in payload.items() if key in {"model", "stream", "temperature", "top_p", "parallel_tool_calls", "metadata"}}
    result["messages"] = messages
    if "max_output_tokens" in payload:
        result["max_completion_tokens"] = payload["max_output_tokens"]
    if payload.get("tools"):
        result["tools"] = [{"type": "function", "function": {key: value for key, value in tool.items() if key != "type"}} for tool in payload["tools"]]
    if "tool_choice" in payload:
        choice = payload["tool_choice"]
        result["tool_choice"] = {"type": "function", "function": {"name": choice["name"]}} if isinstance(choice, dict) else choice
    return result


def _response_shell(payload: dict, *, response_id: str | None = None, created: int | None = None) -> dict:
    return {"id": response_id or "resp_" + uuid.uuid4().hex, "object": "response", "created_at": created or int(time.time()), "completed_at": None, "status": "in_progress", "error": None, "incomplete_details": None, "instructions": payload.get("instructions"), "model": payload["model"], "output": [], "parallel_tool_calls": payload.get("parallel_tool_calls", True), "previous_response_id": None, "store": False, "temperature": payload.get("temperature"), "top_p": payload.get("top_p"), "max_output_tokens": payload.get("max_output_tokens"), "text": {"format": {"type": "text"}}, "tools": payload.get("tools", []), "tool_choice": payload.get("tool_choice", "auto"), "truncation": "disabled", "usage": None, "metadata": payload.get("metadata", {})}


def _usage(usage: dict | None) -> dict | None:
    if not usage:
        return None
    return {"input_tokens": usage.get("prompt_tokens", 0), "input_tokens_details": {"cached_tokens": usage.get("prompt_tokens_details", {}).get("cached_tokens", 0)}, "output_tokens": usage.get("completion_tokens", 0), "output_tokens_details": {"reasoning_tokens": usage.get("completion_tokens_details", {}).get("reasoning_tokens", 0)}, "total_tokens": usage.get("total_tokens", usage.get("prompt_tokens", 0) + usage.get("completion_tokens", 0))}


def chat_to_response(chat: dict, payload: dict) -> dict:
    try:
        choice = chat["choices"][0]
        message = choice["message"]
    except (KeyError, IndexError, TypeError) as exc:
        raise ProxyError("Invalid upstream completion.") from exc
    result = _response_shell(payload, created=chat.get("created"))
    if message.get("content"):
        result["output"].append({"id": "msg_" + uuid.uuid4().hex, "type": "message", "status": "completed", "role": "assistant", "content": [{"type": "output_text", "text": message["content"], "annotations": [], "logprobs": []}]})
    for call in message.get("tool_calls", []):
        result["output"].append({"id": "fc_" + uuid.uuid4().hex, "type": "function_call", "status": "completed", "call_id": call["id"], "name": call["function"]["name"], "arguments": call["function"]["arguments"]})
    result["status"] = "incomplete" if choice.get("finish_reason") == "length" else "completed"
    if result["status"] == "incomplete":
        result["incomplete_details"] = {"reason": "max_output_tokens"}
    result["usage"] = _usage(chat.get("usage"))
    result["completed_at"] = int(time.time())
    return result


def sse_json(chunks: Iterable[bytes]) -> Iterator[dict]:
    buffer = b""
    done = False
    for chunk in chunks:
        buffer += chunk
        if len(buffer) > 4 * 1024 * 1024:
            raise ProxyError("Upstream stream frame exceeds its limit.")
        while True:
            match = re.search(br"\r?\n\r?\n", buffer)
            if match is None:
                break
            frame, buffer = buffer[:match.start()], buffer[match.end():]
            data = b"\n".join(line[5:].lstrip(b" ") for line in frame.splitlines() if line.startswith(b"data:"))
            if not data:
                continue
            if data == b"[DONE]":
                done = True
                return
            try:
                value = json.loads(data)
            except (ValueError, UnicodeError) as exc:
                raise ProxyError("Invalid upstream stream frame.") from exc
            if not isinstance(value, dict):
                raise ProxyError("The upstream stream returned an invalid event.")
            if "error" in value:
                error = value["error"]
                status = error.get("status", 502) if isinstance(error, dict) else 502
                status = status if type(status) is int and 400 <= status <= 599 else 502
                code = {401: "auth_expired", 403: "access_denied", 429: "rate_limit_exceeded"}.get(status, "upstream_error")
                failure = ProxyError("The upstream stream returned an error.", status, code)
                failure.retry_after = str(error.get("retry_after", "")) if isinstance(error, dict) else ""
                raise failure
            yield value
    if not done:
        raise ProxyError("The upstream stream ended before completion.", 502, "incomplete_stream")


def chat_sse_to_responses(chunks: Iterable[bytes], payload: dict) -> Iterator[bytes]:
    events = iter(sse_json(chunks))
    first = next(events, None)
    if first is None:
        raise ProxyError("The upstream returned an empty stream.")
    response = _response_shell(payload, created=first.get("created"))
    sequence = itertools.count()

    def event(kind: str, **values: Any) -> bytes:
        value = {"type": kind, "sequence_number": next(sequence), **values}
        return ("event: " + kind + "\ndata: " + json.dumps(value, ensure_ascii=False) + "\n\n").encode()

    yield event("response.created", response=copy.deepcopy(response))
    yield event("response.in_progress", response=copy.deepcopy(response))
    message = None
    text = ""
    calls: dict[int, dict] = {}
    finish = None
    for frame in itertools.chain([first], events):
        if frame.get("usage"):
            response["usage"] = _usage(frame["usage"])
        choices = frame.get("choices", [])
        if not choices:
            continue
        delta = choices[0].get("delta", {})
        if delta.get("refusal") or delta.get("audio"):
            raise ProxyError("Unsupported upstream response content.")
        fragment = delta.get("content")
        if fragment:
            if not isinstance(fragment, str):
                raise ProxyError("Invalid upstream text delta.")
            if message is None:
                message = {"id": "msg_" + uuid.uuid4().hex, "type": "message", "role": "assistant", "status": "in_progress", "content": []}
                yield event("response.output_item.added", output_index=0, item=copy.deepcopy(message))
                yield event("response.content_part.added", output_index=0, item_id=message["id"], content_index=0, part={"type": "output_text", "text": "", "annotations": [], "logprobs": []})
            text += fragment
            yield event("response.output_text.delta", output_index=0, item_id=message["id"], content_index=0, delta=fragment, logprobs=[])
        for call in delta.get("tool_calls", []):
            index = call.get("index", 0)
            if type(index) is not int or index < 0:
                raise ProxyError("Invalid tool call index.")
            aggregate = calls.setdefault(index, {"id": "", "name": "", "arguments": ""})
            aggregate["id"] += call.get("id", "")
            aggregate["name"] += call.get("function", {}).get("name", "")
            aggregate["arguments"] += call.get("function", {}).get("arguments", "")
        finish = choices[0].get("finish_reason") or finish
    if finish is None:
        raise ProxyError("The upstream stream has no completion marker.", 502, "incomplete_stream")
    if message is not None:
        part = {"type": "output_text", "text": text, "annotations": [], "logprobs": []}
        message["content"] = [part]
        message["status"] = "completed"
        yield event("response.output_text.done", output_index=0, item_id=message["id"], content_index=0, text=text, logprobs=[])
        yield event("response.content_part.done", output_index=0, item_id=message["id"], content_index=0, part=part)
        yield event("response.output_item.done", output_index=0, item=copy.deepcopy(message))
        response["output"].append(message)
    for call in (calls[index] for index in sorted(calls)):
        if not call["id"] or not call["name"]:
            raise ProxyError("Incomplete upstream function call.")
        item = {"id": "fc_" + uuid.uuid4().hex, "type": "function_call", "status": "in_progress", "call_id": call["id"], "name": call["name"], "arguments": ""}
        index = len(response["output"])
        yield event("response.output_item.added", output_index=index, item=copy.deepcopy(item))
        yield event("response.function_call_arguments.delta", output_index=index, item_id=item["id"], delta=call["arguments"])
        item["arguments"] = call["arguments"]
        item["status"] = "completed"
        yield event("response.function_call_arguments.done", output_index=index, item_id=item["id"], arguments=call["arguments"])
        yield event("response.output_item.done", output_index=index, item=copy.deepcopy(item))
        response["output"].append(item)
    response["status"] = "incomplete" if finish == "length" else "completed"
    if finish == "length":
        response["incomplete_details"] = {"reason": "max_output_tokens"}
    response["completed_at"] = int(time.time())
    yield event("response." + response["status"], response=response)


def chat_completion_sse(chat: dict) -> Iterator[bytes]:
    choice = chat["choices"][0]
    message = choice["message"]
    base = {"id": chat.get("id", "chatcmpl_" + uuid.uuid4().hex), "object": "chat.completion.chunk", "created": chat.get("created", int(time.time())), "model": chat["model"]}

    def frame(delta: dict, finish: str | None = None) -> bytes:
        return ("data: " + json.dumps({**base, "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}, ensure_ascii=False) + "\n\n").encode()

    yield frame({"role": "assistant"})
    if message.get("content"):
        yield frame({"content": message["content"]})
    for index, call in enumerate(message.get("tool_calls", [])):
        yield frame({"tool_calls": [{"index": index, **call}]})
    yield frame({}, choice.get("finish_reason", "stop"))
    if chat.get("usage"):
        yield ("data: " + json.dumps({**base, "choices": [], "usage": chat["usage"]}) + "\n\n").encode()
    yield b"data: [DONE]\n\n"


# Service callers use the longer name to make the direction of conversion clear.
chat_completion_to_sse = chat_completion_sse


def prepare_tool_prompt(payload: dict) -> dict:
    result = copy.deepcopy(payload)
    tools = result.pop("tools", [])
    choice = result.pop("tool_choice", "auto")
    parallel = result.pop("parallel_tool_calls", True)
    result["stream"] = False
    result.pop("stream_options", None)
    instruction = "Return only one JSON object with exactly content (string or null) and tool_calls (array). Each tool call has exactly name (one supplied function) and arguments (JSON object). Never execute tools yourself. When no function is needed, set tool_calls=[] and put your answer in content."
    instruction += "\nFunctions: " + json.dumps([tool["function"] for tool in tools], ensure_ascii=False)
    instruction += "\nTool choice: " + json.dumps(choice) + "; parallel_tool_calls=" + json.dumps(parallel)
    messages = []
    for message in result["messages"]:
        if message["role"] == "tool":
            messages.append({"role": "user", "content": "Function result for " + message["tool_call_id"] + ":\n" + (_text(message["content"]) or "")})
        elif message.get("tool_calls"):
            messages.append({"role": "assistant", "content": json.dumps({"content": message.get("content"), "tool_calls": [{"call_id": call["id"], **call["function"]} for call in message["tool_calls"]]}, ensure_ascii=False)})
        else:
            messages.append({"role": message["role"], "content": _text(message["content"])})
    messages.insert(0, {"role": "system", "content": instruction})
    result["messages"] = messages
    return result


def parse_tool_response(chat: dict, payload: dict) -> dict:
    try:
        raw = chat["choices"][0]["message"]["content"]
        text = raw.strip()
        # The web UI can wrap its complete structured answer in this literal
        # container. Remove one exact outer wrapper only; JSON decoding and all
        # envelope, choice and argument checks still apply to its full contents.
        if text.startswith("<escape>") and text.endswith("</escape>"):
            text = text[len("<escape>"):-len("</escape>")].strip()
        if text.startswith("```json\n") and text.endswith("\n```"):
            text = text[8:-4]
        value = json.loads(text)
        if not isinstance(value, dict) or set(value) != {"content", "tool_calls"} or value["content"] is not None and not isinstance(value["content"], str) or not isinstance(value["tool_calls"], list):
            raise ValueError("Invalid envelope")
        functions = {tool["function"]["name"]: tool["function"] for tool in payload.get("tools", [])}
        calls = []
        for call in value["tool_calls"]:
            if not isinstance(call, dict) or set(call) != {"name", "arguments"} or call["name"] not in functions or not isinstance(call["arguments"], dict):
                raise ValueError("Invalid function call")
            _check_schema(call["arguments"], functions[call["name"]].get("parameters", {}))
            calls.append({"id": "call_" + uuid.uuid4().hex, "type": "function", "function": {"name": call["name"], "arguments": json.dumps(call["arguments"], ensure_ascii=False, allow_nan=False)}})
        choice = payload.get("tool_choice", "auto")
        if choice == "none" and calls or choice == "required" and not calls or isinstance(choice, dict) and (not calls or any(call["function"]["name"] != choice["function"]["name"] for call in calls)):
            raise ValueError("Tool choice violated")
        if not payload.get("parallel_tool_calls", True) and len(calls) > 1:
            raise ValueError("Parallel calls disabled")
        result = copy.deepcopy(chat)
        result["choices"][0]["message"] = {"role": "assistant", "content": value["content"]}
        if calls:
            result["choices"][0]["message"]["tool_calls"] = calls
        result["choices"][0]["finish_reason"] = "tool_calls" if calls else "stop"
        return result
    except (ValueError, TypeError, KeyError, IndexError, AttributeError) as exc:
        raise ProxyError("ChatGPT returned invalid function output after validation.", 502, "invalid_tool_output") from exc


def _check_schema(value: Any, schema: dict) -> None:
    # Emulated calls cannot silently claim support for constraints we do not check.
    if set(schema) - _SCHEMA_KEYS:
        raise ValueError("Unsupported function schema constraint")
    kind = schema.get("type")
    matches = {"object": isinstance(value, dict), "array": isinstance(value, list), "string": isinstance(value, str), "integer": type(value) is int, "number": type(value) in {int, float} and math.isfinite(value), "boolean": type(value) is bool, "null": value is None}
    if kind is not None and not any(matches.get(option, False) for option in (kind if isinstance(kind, list) else [kind])):
        raise ValueError("Function argument type mismatch")
    if "enum" in schema and value not in schema["enum"] or "const" in schema and value != schema["const"]:
        raise ValueError("Function argument value mismatch")
    if isinstance(value, dict):
        if any(key not in value for key in schema.get("required", [])):
            raise ValueError("Missing function argument")
        properties = schema.get("properties", {})
        for key, item in value.items():
            if key in properties:
                _check_schema(item, properties[key])
            elif schema.get("additionalProperties") is False:
                raise ValueError("Unexpected function argument")
            elif isinstance(schema.get("additionalProperties"), dict):
                _check_schema(item, schema["additionalProperties"])
    if isinstance(value, list):
        if len(value) < schema.get("minItems", 0) or len(value) > schema.get("maxItems", float("inf")):
            raise ValueError("Function array length mismatch")
        for item in value:
            _check_schema(item, schema.get("items", {}))
    if isinstance(value, str):
        if len(value) < schema.get("minLength", 0) or len(value) > schema.get("maxLength", float("inf")) or "pattern" in schema and not re.search(schema["pattern"], value):
            raise ValueError("Function text constraint mismatch")
    if type(value) in {int, float} and (value < schema.get("minimum", -float("inf")) or value > schema.get("maximum", float("inf"))):
        raise ValueError("Function numeric constraint mismatch")
    for group in ("anyOf", "oneOf", "allOf"):
        if group in schema:
            successes = 0
            for alternative in schema[group]:
                try:
                    _check_schema(value, alternative)
                    successes += 1
                except ValueError:
                    pass
            if group == "anyOf" and not successes or group == "oneOf" and successes != 1 or group == "allOf" and successes != len(schema[group]):
                raise ValueError("Function union constraint mismatch")


def repair_tool_prompt(worker_payload: dict, malformed_text: str) -> dict:
    result = copy.deepcopy(worker_payload)
    result["messages"].extend([{ "role": "assistant", "content": malformed_text}, {"role": "user", "content": "Your previous output failed function schema validation. Return only the required JSON envelope with valid arguments and the selected tool choice. Do not include markdown or commentary."}])
    return result
