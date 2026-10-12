"""Explicit live-account SDK validation; never runs as part of pytest/build.

Connect accounts in the API tab first. The script requests its client key via
getpass and uses only a temporary coding workspace for function-call results.
"""

from __future__ import annotations

import argparse
import getpass
import json
import sys
import tempfile
from pathlib import Path
from urllib.parse import urlsplit


def read_file(workspace: Path, arguments: str) -> str:
    value = json.loads(arguments)
    if not isinstance(value, dict) or set(value) != {"path"} or not isinstance(value["path"], str):
        raise ValueError("Invalid read_file arguments")
    target = (workspace / value["path"]).resolve()
    if not target.is_relative_to(workspace.resolve()) or not target.is_file():
        raise ValueError("Function path escaped the temporary coding workspace")
    return target.read_text(encoding="utf-8")


def check_model(client, model: str, workspace: Path) -> None:
    prompt = [{"role": "user", "content": "Reply briefly with READY."}]
    answer = client.chat.completions.create(model=model, messages=prompt)
    assert answer.model == model and answer.choices[0].message.content
    with client.chat.completions.create(model=model, messages=prompt, stream=True) as stream:
        text = "".join(chunk.choices[0].delta.content or "" for chunk in stream if chunk.choices)
    assert text
    response = client.responses.create(model=model, input="Reply briefly with READY.", store=False)
    assert response.model == model and response.output_text
    with client.responses.create(model=model, input="Reply briefly with READY.", store=False, stream=True) as stream:
        events = list(stream)
    assert any(event.type == "response.output_text.delta" for event in events)
    assert events[-1].type == "response.completed"
    print(f"PASS {model}: normal and streaming Chat Completions/Responses", flush=True)

    function = {
        "name": "read_file", "description": "Read a relative path from the coding workspace.",
        "parameters": {"type": "object", "properties": {"path": {"type": "string"}},
                       "required": ["path"], "additionalProperties": False}, "strict": True,
    }
    history = [{"role": "user", "content": "Read main.py using read_file. Wait for its contents before reviewing."}]
    for round_index in range(2):
        if round_index:
            history.append({"role": "user", "content": "Now read test_main.py using read_file before suggesting the correction."})
        answer = client.chat.completions.create(
            model=model, messages=history, tools=[{"type": "function", "function": function}],
            tool_choice="required", parallel_tool_calls=False,
        )
        message = answer.choices[0].message
        assert message.tool_calls
        history.append({"role": "assistant", "content": message.content,
                        "tool_calls": [call.model_dump(exclude_none=True) for call in message.tool_calls]})
        for call in message.tool_calls:
            assert call.function.name == "read_file"
            history.append({"role": "tool", "tool_call_id": call.id,
                            "content": read_file(workspace, call.function.arguments)})
    history.append({"role": "user", "content": "Suggest the one-line correction. Do not call another function."})
    final = client.chat.completions.create(
        model=model, messages=history, tools=[{"type": "function", "function": function}], tool_choice="none",
    )
    assert final.choices[0].message.content and not final.choices[0].message.tool_calls

    items = [{"role": "user", "content": "Read main.py using read_file before reviewing."}]
    for round_index in range(2):
        if round_index:
            items.append({"role": "user", "content": "Read test_main.py using read_file before suggesting the correction."})
        with client.responses.create(
            model=model, input=items, tools=[{"type": "function", **function}],
            tool_choice="required", parallel_tool_calls=False, store=False, stream=True,
        ) as stream:
            events = list(stream)
        assert events[-1].type == "response.completed"
        response = events[-1].response
        calls = [item for item in response.output if item.type == "function_call"]
        assert calls
        items.extend(item.model_dump(exclude_none=True) for item in response.output)
        for call in calls:
            assert call.name == "read_file"
            items.append({"type": "function_call_output", "call_id": call.call_id,
                          "output": read_file(workspace, call.arguments)})
    items.append({"role": "user", "content": "Suggest the one-line correction. Do not call another function."})
    response = client.responses.create(
        model=model, input=items, tools=[{"type": "function", **function}], tool_choice="none", store=False,
    )
    assert response.output_text and all(item.type != "function_call" for item in response.output)
    print(f"PASS {model}: two-round coding tool histories in Chat Completions/streaming Responses", flush=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8318/v1")
    parser.add_argument("--model", action="append", required=True, help="exact pool model; repeat for both providers")
    args = parser.parse_args()
    url = urlsplit(args.base_url)
    if url.scheme != "http" or url.hostname not in {"127.0.0.1", "localhost"} or url.path.rstrip("/") != "/v1" or url.username or url.password or url.query or url.fragment:
        parser.error("Use the local U-Pool /v1 endpoint.")
    if any(not model.startswith(("chatgpt/", "grok/")) for model in args.model):
        parser.error("Use exact chatgpt/<model> or grok/<model> identifiers from the API panel.")
    try:
        from openai import OpenAI
    except ImportError:
        parser.error("Developer validation requires the OpenAI Python SDK (pip install openai).")
    key = getpass.getpass("U-Pool client API key: ")
    if not key:
        parser.error("A client key is required.")
    with OpenAI(api_key=key, base_url=args.base_url, max_retries=0, timeout=180) as client:
        available = {model.id for model in client.models.list()}
        with tempfile.TemporaryDirectory(prefix="upool-live-coding-") as directory:
            workspace = Path(directory)
            (workspace / "main.py").write_text("def add(a, b):\n    return a - b\n", encoding="utf-8")
            (workspace / "test_main.py").write_text("from main import add\n\ndef test_add():\n    assert add(2, 3) == 5\n", encoding="utf-8")
            for model in args.model:
                if model not in available:
                    parser.error(f"Model {model} is not available in the enabled pool.")
                try:
                    check_model(client, model, workspace)
                except Exception as error:
                    print(f"FAIL {model}: {type(error).__name__}. Check the API account status.", file=sys.stderr)
                    return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
