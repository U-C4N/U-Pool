from __future__ import annotations

import copy
import json

import pytest

from upool.proxy.models import ProxyError
from upool.proxy.protocol import chat_completion_to_sse, chat_sse_to_responses, chat_to_response, parse_model, parse_tool_response, prepare_tool_prompt, repair_tool_prompt, responses_to_chat, sse_json, validate_request


def chat_payload(**extra):
    return {"model": "chatgpt/test", "messages": [{"role": "user", "content": "Hello"}], **extra}


def tool():
    return {"type": "function", "function": {"name": "read_file", "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"], "additionalProperties": False}}}


def chat_result(content="Hello", calls=None):
    message = {"role": "assistant", "content": content}
    if calls:
        message["tool_calls"] = calls
    return {"id": "chatcmpl-test", "model": "chatgpt/test", "created": 123, "choices": [{"index": 0, "message": message, "finish_reason": "tool_calls" if calls else "stop"}], "usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5}}


def events(chunks):
    return list(sse_json([*chunks, b"data: [DONE]\n\n"]))


def test_model_alias_parsing():
    assert parse_model("grok/grok-code-fast-1") == ("grok", "grok-code-fast-1")
    for model in (None, "gpt-4", "other/model", "grok/", "grok/a/../../secret"):
        with pytest.raises(ProxyError):
            parse_model(model)


@pytest.mark.parametrize("extra", [{"audio": {}}, {"modalities": ["text"]}, {"temperature": 0.5}, {"max_tokens": 50}, {"stream": "true"}, {"store": True}, {"tool_choice": []}, {"n": 2}, {"tools": [{"type": "web_search"}]}, {"response_format": {"type": "json_object"}}, {"stream_options": {"include_usage": True}}])
def test_unsupported_or_invalid_chat_fields_are_explicit(extra):
    with pytest.raises(ProxyError) as exc:
        validate_request("chat/completions", chat_payload(**extra))
    assert exc.value.status == 400


def test_validation_does_not_mutate_and_grok_controls_pass_through():
    payload = chat_payload(model="grok/code", temperature=0.3, max_tokens=80)
    original = copy.deepcopy(payload)
    validated = validate_request("chat/completions", payload)
    validated["messages"][0]["content"] = "changed"
    assert payload == original


@pytest.mark.parametrize("schema", [
    {"$ref": "#/$defs/Argument"},
    {"type": "unknown"},
    {"properties": {"path": False}},
    {"required": "path"},
    {"minItems": True},
    {"pattern": "["},
    {"anyOf": []},
])
def test_web_function_schemas_fail_before_starting_a_generation(schema):
    function = tool()
    function["function"]["parameters"] = schema
    with pytest.raises(ProxyError) as exc:
        validate_request("chat/completions", chat_payload(tools=[function]))
    assert exc.value.status == 400
    assert validate_request("chat/completions", chat_payload(model="grok/code", tools=[function]))


@pytest.mark.parametrize("content", [[{"type": "image_url", "image_url": {"url": "secret"}}], [{"type": [], "text": "x"}], 5])
def test_only_text_content_is_accepted(content):
    with pytest.raises(ProxyError):
        validate_request("chat/completions", chat_payload(messages=[{"role": "user", "content": content}]))


def test_responses_translation_preserves_full_tool_history():
    payload = {"model": "chatgpt/test", "instructions": "Read files", "input": [{"role": "user", "content": [{"type": "input_text", "text": "Read a.py"}]}, {"type": "function_call", "call_id": "call_a", "name": "read_file", "arguments": '{"path":"a.py"}'}, {"type": "function_call_output", "call_id": "call_a", "output": "code"}], "tools": [{"type": "function", **tool()["function"]}], "tool_choice": {"type": "function", "name": "read_file"}, "store": False}
    assert validate_request("responses", payload) == payload
    translated = responses_to_chat(payload)
    assert translated["messages"][0] == {"role": "system", "content": "Read files"}
    assert translated["messages"][-1] == {"role": "tool", "content": "code", "tool_call_id": "call_a"}
    assert translated["tools"] == [tool()]
    assert translated["tool_choice"]["function"]["name"] == "read_file"


@pytest.mark.parametrize("extra", [{"previous_response_id": "resp_last"}, {"store": True}, {"background": True}, {"include": ["reasoning.encrypted_content"]}, {"tools": [{"type": "computer_use"}]}])
def test_stateful_and_built_in_responses_are_rejected(extra):
    with pytest.raises(ProxyError):
        validate_request("responses", {"model": "chatgpt/test", "input": "hi", **extra})


def test_tool_output_requires_matching_input_call():
    with pytest.raises(ProxyError, match="match"):
        validate_request("responses", {"model": "chatgpt/test", "input": [{"type": "function_call_output", "call_id": "unknown", "output": "data"}]})


def test_json_response_translation_text_tools_and_usage():
    calls = [{"id": "call_a", "type": "function", "function": {"name": "read_file", "arguments": '{"path":"a"}'}}]
    result = chat_to_response(chat_result("Reading", calls), {"model": "chatgpt/test", "input": "hi"})
    assert result["status"] == "completed" and result["store"] is False
    assert result["output"][0]["content"][0]["text"] == "Reading"
    assert result["output"][1]["call_id"] == "call_a"
    assert result["usage"]["total_tokens"] == 5


def test_stream_lifecycle_and_split_utf8():
    raw = list(chat_completion_to_sse(chat_result("Merhaba 👋")))
    body = b"".join(raw)
    pieces = [body[index:index + 7] for index in range(0, len(body), 7)]
    converted = events(chat_sse_to_responses(pieces, {"model": "chatgpt/test", "input": "hi"}))
    types = [value["type"] for value in converted]
    assert types[:2] == ["response.created", "response.in_progress"]
    assert "response.output_item.added" in types
    assert "response.content_part.added" in types
    assert "response.output_text.delta" in types
    assert "response.output_text.done" in types
    assert types[-1] == "response.completed"
    assert converted[-1]["response"]["output"][0]["content"][0]["text"] == "Merhaba 👋"
    assert [value["sequence_number"] for value in converted] == list(range(len(converted)))
    assert converted[-1]["response"]["usage"]["total_tokens"] == 5


def test_stream_function_call_identity_and_arguments():
    calls = [{"id": "call_a", "type": "function", "function": {"name": "read_file", "arguments": '{"path":"a"}'}}]
    converted = events(chat_sse_to_responses(chat_completion_to_sse(chat_result(None, calls)), {"model": "chatgpt/test", "input": "hi"}))
    added = next(value for value in converted if value["type"] == "response.output_item.added")
    delta = next(value for value in converted if value["type"] == "response.function_call_arguments.delta")
    done = next(value for value in converted if value["type"] == "response.function_call_arguments.done")
    assert added["item"]["call_id"] == "call_a"
    assert delta["item_id"] == done["item_id"] == added["item"]["id"]
    assert delta["delta"] == done["arguments"] == '{"path":"a"}'
    assert converted[-1]["response"]["output"][0]["name"] == "read_file"


def test_truncated_stream_is_never_completed():
    frames = list(chat_completion_to_sse(chat_result()))[:-1]
    iterator = chat_sse_to_responses(frames, {"model": "chatgpt/test", "input": "hi"})
    observed = []
    with pytest.raises(ProxyError) as exc:
        while True:
            observed.extend(events([next(iterator)]))
    assert exc.value.code == "incomplete_stream"
    assert all(event["type"] != "response.completed" for event in observed)


def test_tool_prompt_and_parse_validated_output():
    payload = chat_payload(tools=[tool()], stream=True, stream_options={"include_usage": True}, tool_choice="required")
    worker = prepare_tool_prompt(payload)
    assert worker["stream"] is False
    assert "tools" not in worker and "stream_options" not in worker
    assert "read_file" in worker["messages"][0]["content"]
    chat = chat_result(json.dumps({"content": None, "tool_calls": [{"name": "read_file", "arguments": {"path": "a.py"}}]}))
    parsed = parse_tool_response(chat, payload)
    assert parsed["choices"][0]["finish_reason"] == "tool_calls"
    assert json.loads(parsed["choices"][0]["message"]["tool_calls"][0]["function"]["arguments"]) == {"path": "a.py"}


@pytest.mark.parametrize("value", [{"content": None, "tool_calls": [{"name": "unknown", "arguments": {"path": "a"}}]}, {"content": None, "tool_calls": [{"name": "read_file", "arguments": {}}]}, {"content": None, "tool_calls": [{"name": "read_file", "arguments": {"path": 1}}]}, {"content": "done", "tool_calls": []}, {"content": None, "tool_calls": [{"name": "read_file", "arguments": {"path": "a", "extra": 1}}]}])
def test_invalid_tool_output_is_repairable_exact_error_code(value):
    with pytest.raises(ProxyError) as exc:
        parse_tool_response(chat_result(json.dumps(value)), chat_payload(tools=[tool()], tool_choice="required"))
    assert exc.value.code == "invalid_tool_output"


@pytest.mark.parametrize("formatting", ["<escape>{}</escape>", " \n<escape>\n{}\n</escape>\t ",
                                       "<escape>```json\n{}\n```</escape>"])
def test_exact_escape_wrapped_tool_envelope_is_validated(formatting):
    envelope = json.dumps({"content": None, "tool_calls": [
        {"name": "read_file", "arguments": {"path": "main.py"}},
    ]})
    payload = chat_payload(tools=[tool()], tool_choice="required", parallel_tool_calls=False)
    parsed = parse_tool_response(chat_result(formatting.format(envelope)), payload)
    choice = parsed["choices"][0]
    assert choice["finish_reason"] == "tool_calls"
    assert choice["message"]["content"] is None
    assert choice["message"]["tool_calls"][0]["function"] == {
        "name": "read_file", "arguments": '{"path": "main.py"}',
    }


@pytest.mark.parametrize("formatting", [
    "prose <escape>{}</escape>", "<escape>{}</escape> prose", "<escape>{}",
    "{}</escape>", "<escape><escape>{}</escape></escape>",
    "<escape>{0}{0}</escape>", "<escape>{0}</escape><escape>{0}</escape>",
    "<escape>prose {}</escape>", "<escape attr='yes'>{}</escape>",
])
def test_escape_wrapper_does_not_allow_partial_nested_multiple_or_prose_output(formatting):
    envelope = json.dumps({"content": None, "tool_calls": [
        {"name": "read_file", "arguments": {"path": "main.py"}},
    ]})
    with pytest.raises(ProxyError) as exc:
        parse_tool_response(chat_result(formatting.format(envelope)), chat_payload(tools=[tool()]))
    assert exc.value.code == "invalid_tool_output"


@pytest.mark.parametrize(("value", "extras"), [
    ({"content": "done", "tool_calls": []}, {"tool_choice": "required"}),
    ({"content": None, "tool_calls": [{"name": "read_file", "arguments": {"path": "main.py"}}]},
     {"tool_choice": "none"}),
    ({"content": None, "tool_calls": [{"name": "read_file", "arguments": {"path": "main.py"}},
                                      {"name": "read_file", "arguments": {"path": "test_main.py"}}]},
     {"parallel_tool_calls": False}),
    ({"content": None, "tool_calls": [{"name": "read_file", "arguments": {"path": None}}]}, {}),
    ({"content": None, "tool_calls": [{"name": "read_file", "arguments": {}}]}, {}),
    ({"content": None, "tool_calls": [{"name": "unknown", "arguments": {"path": "main.py"}}]}, {}),
])
def test_escape_wrapped_output_still_requires_tool_choice_parallel_and_schema(value, extras):
    with pytest.raises(ProxyError) as exc:
        parse_tool_response(chat_result("<escape>" + json.dumps(value) + "</escape>"),
                            chat_payload(tools=[tool()], **extras))
    assert exc.value.code == "invalid_tool_output"


def test_tool_repair_keeps_original_request_isolated():
    worker = prepare_tool_prompt(chat_payload(tools=[tool()]))
    original = copy.deepcopy(worker)
    repaired = repair_tool_prompt(worker, "not-json")
    assert worker == original
    assert repaired["messages"][-2]["content"] == "not-json"
    assert len(repaired["messages"]) == len(worker["messages"]) + 2


def test_quota_usage_is_unknown_when_absent():
    chat = chat_result()
    del chat["usage"]
    assert chat_to_response(chat, {"model": "chatgpt/test", "input": "hi"})["usage"] is None
