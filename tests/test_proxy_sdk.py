"""Optional developer acceptance using the real SDK and isolated fake engines.

Install OpenAI's SDK only in a development environment; the desktop application's
gateway deliberately has no dependency on it or on live account credentials.
"""

from __future__ import annotations

import json
import re
import socket

import pytest

openai = pytest.importorskip("openai")

from upool.proxy import protocol
from upool.proxy.models import UpstreamResponse
from upool.proxy.service import ProxyService


class SDKEngines:
    def __init__(self):
        self.calls = []

    def snapshot(self):
        return []

    def stop_all(self):
        pass

    def complete(self, account, endpoint, payload):
        self.calls.append((account["id"], endpoint, payload))
        chat = protocol.responses_to_chat(payload) if endpoint == "responses" else payload
        messages = chat["messages"]
        emulated = any("Return only one JSON object with exactly content" in (message.get("content") or "") for message in messages)
        has_result = any(message.get("role") == "tool" or "Function result for " in (message.get("content") or "") for message in messages)
        tools = chat.get("tools", [])
        tool_name = tools[0]["function"]["name"] if tools else "read_file"
        if emulated:
            match = re.search(r'"name": "([^\"]+)"', messages[0]["content"])
            tool_name = match.group(1) if match else tool_name
        calls = []
        content = "File contents received." if has_result else "Hello from the isolated provider."
        if (tools or emulated) and not has_result:
            calls = [{"id": "call_read", "type": "function", "function": {"name": tool_name, "arguments": '{"path":"main.py"}'}}]
            content = None
        if emulated:
            content = json.dumps({"content": content, "tool_calls": [{"name": call["function"]["name"], "arguments": json.loads(call["function"]["arguments"])} for call in calls]})
            calls = []
        message = {"role": "assistant", "content": content}
        if calls:
            message["tool_calls"] = calls
        result = {"id": "chatcmpl_sdk", "object": "chat.completion", "created": 123,
                  "model": payload["model"], "choices": [{"index": 0, "message": message, "finish_reason": "tool_calls" if calls else "stop"}],
                  "usage": {"prompt_tokens": 4, "completion_tokens": 3, "total_tokens": 7}}
        if endpoint == "responses":
            body = protocol.chat_sse_to_responses(protocol.chat_completion_sse(result), payload) if payload.get("stream") else json.dumps(protocol.chat_to_response(result, payload)).encode()
        else:
            body = protocol.chat_completion_sse(result) if payload.get("stream") else json.dumps(result).encode()
        return UpstreamResponse(200, {"Content-Type": "text/event-stream" if payload.get("stream") else "application/json"}, body)


@pytest.fixture
def sdk_client():
    engines = SDKEngines()
    service = ProxyService(engines)
    for provider in ("grok", "chatgpt"):
        account = service._new_account(provider, provider)
        account.update(id=provider, status="ready", models=["test"])
        service.store.put(account)
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    service.set_port(port)
    service.start()
    client = openai.OpenAI(base_url=f"http://127.0.0.1:{port}/v1", api_key=service.client_key, max_retries=0, timeout=5)
    yield client, service, engines
    client.close()
    service.shutdown()


@pytest.mark.parametrize("provider", ["grok", "chatgpt"])
def test_sdk_models_and_normal_chat(sdk_client, provider):
    client, service, engines = sdk_client
    assert {model.id for model in client.models.list()} == {"grok/test", "chatgpt/test"}
    response = client.chat.completions.create(model=f"{provider}/test", messages=[{"role": "user", "content": "hi"}])
    assert response.model == f"{provider}/test"
    assert response.choices[0].message.content == "Hello from the isolated provider."
    assert response.usage.total_tokens == 7
    assert engines.calls[0][2]["model"] == "test"
    assert not service._busy


@pytest.mark.parametrize("provider", ["grok", "chatgpt"])
def test_sdk_stream_chat(sdk_client, provider):
    client, service, _ = sdk_client
    with client.chat.completions.create(model=f"{provider}/test", messages=[{"role": "user", "content": "hi"}], stream=True, stream_options={"include_usage": True}) as stream:
        chunks = list(stream)
    assert all(chunk.model == f"{provider}/test" for chunk in chunks)
    assert "".join(chunk.choices[0].delta.content or "" for chunk in chunks if chunk.choices) == "Hello from the isolated provider."
    assert chunks[-1].usage.total_tokens == 7
    assert not service._busy


@pytest.mark.parametrize("provider", ["grok", "chatgpt"])
def test_sdk_normal_responses(sdk_client, provider):
    client, service, _ = sdk_client
    response = client.responses.create(model=f"{provider}/test", input="hi", store=False)
    assert response.model == f"{provider}/test"
    assert response.output_text == "Hello from the isolated provider."
    assert response.status == "completed" and response.store is False
    assert response.usage.input_tokens == 4 and response.usage.output_tokens == 3
    assert not service._busy


@pytest.mark.parametrize("provider", ["grok", "chatgpt"])
def test_sdk_stream_responses(sdk_client, provider):
    client, service, _ = sdk_client
    with client.responses.create(model=f"{provider}/test", input="hi", stream=True, store=False) as stream:
        events = list(stream)
    assert events[0].type == "response.created"
    assert events[-1].type == "response.completed"
    assert events[-1].response.model == f"{provider}/test"
    assert "".join(event.delta for event in events if event.type == "response.output_text.delta") == "Hello from the isolated provider."
    assert events[-1].response.usage.total_tokens == 7
    assert not service._busy


@pytest.mark.parametrize("provider", ["grok", "chatgpt"])
@pytest.mark.parametrize("stream", [False, True])
def test_sdk_chat_function_result_loop(sdk_client, provider, stream):
    client, service, _ = sdk_client
    tools = [{"type": "function", "function": {"name": "read_file", "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}}]
    messages = [{"role": "user", "content": "Read main.py"}]
    response = client.chat.completions.create(model=f"{provider}/test", messages=messages, tools=tools, stream=stream)
    if stream:
        with response:
            calls = [call for chunk in response for choice in chunk.choices for call in (choice.delta.tool_calls or [])]
        assert len(calls) == 1
        call = {"id": calls[0].id, "type": "function", "function": {"name": calls[0].function.name, "arguments": calls[0].function.arguments}}
        assistant = {"role": "assistant", "content": None, "tool_calls": [call]}
    else:
        assistant = response.choices[0].message.model_dump(exclude_none=True)
        call = assistant["tool_calls"][0]
        assistant["content"] = None
    assert call["function"]["name"] == "read_file"
    assert json.loads(call["function"]["arguments"]) == {"path": "main.py"}
    messages += [assistant, {"role": "tool", "tool_call_id": call["id"], "content": "print('hello')"}]
    final = client.chat.completions.create(model=f"{provider}/test", messages=messages, tools=tools)
    assert final.choices[0].message.content == "File contents received."
    assert final.choices[0].finish_reason == "stop"
    assert not service._busy


@pytest.mark.parametrize("provider", ["grok", "chatgpt"])
@pytest.mark.parametrize("stream", [False, True])
def test_sdk_responses_function_result_loop(sdk_client, provider, stream):
    client, service, _ = sdk_client
    tools = [{"type": "function", "name": "read_file", "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}]
    history = [{"role": "user", "content": "Read main.py"}]
    response = client.responses.create(model=f"{provider}/test", input=history, tools=tools, store=False, stream=stream)
    if stream:
        with response:
            events = list(response)
        response = events[-1].response
    call = next(item for item in response.output if item.type == "function_call")
    assert call.name == "read_file" and json.loads(call.arguments) == {"path": "main.py"}
    history += [item.model_dump(exclude_none=True) for item in response.output]
    history.append({"type": "function_call_output", "call_id": call.call_id, "output": "print('hello')"})
    final = client.responses.create(model=f"{provider}/test", input=history, tools=tools, store=False)
    assert final.output_text == "File contents received."
    assert final.status == "completed"
    assert not service._busy
