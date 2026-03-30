import json
import os
import sys
from pathlib import Path

from fastapi.responses import StreamingResponse
from fastapi.testclient import TestClient


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("ADMIN_PASSWORD", "test-admin-password")

import app.api.chat as chat_module
import app.server as server_module
from app.schemas import ChatCompletionResponse, ChatMessage, ChatMessageResponseChoice
from app.server import app


async def _fake_create_chat_completion(
    request,
    req_body,
    background_tasks,
    response,
    x_chat_session=None,
):
    request.app.state.compat_anthropic_requests.append(
        {
            "model": req_body.model,
            "stream": bool(req_body.stream),
            "messages": [message.model_dump() for message in req_body.messages],
            "temperature": req_body.temperature,
            "top_p": req_body.top_p,
            "max_tokens": req_body.max_tokens,
        }
    )
    response.headers["X-Conversation-Id"] = "compat-anthropic-conv"

    if req_body.stream:

        async def event_generator():
            yield 'data: {"choices": [{"index": 0, "delta": {"content": "compat "}}]}\n\n'
            if str(req_body.model or "").endswith("-search"):
                yield 'data: {"type": "search_metadata", "searches": {"queries": ["stream search please"], "sources": ["https://example.com/search-stream"]}}\n\n'
            yield 'data: {"choices": [{"index": 0, "delta": {"content": "anthropic stream"}}]}\n\n'
            yield 'data: {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]}\n\n'
            yield "data: [DONE]\n\n"

        return StreamingResponse(event_generator(), media_type="text/event-stream")

    payload = ChatCompletionResponse(
        id="chatcmpl-anthropic-compat",
        model="claude-sonnet4.6",
        choices=[
            ChatMessageResponseChoice(
                index=0,
                message=ChatMessage(
                    role="assistant",
                    content="compat anthropic reply https://example.com/search-result",
                ),
                finish_reason="stop",
            )
        ],
        usage={"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18},
    )
    if str(req_body.model or "").endswith("-search"):
        payload.search_metadata = {
            "queries": ["search variant please"],
            "sources": ["https://example.com/search-result"],
        }
    return payload


def _parse_sse_events(raw_text: str) -> list[dict[str, object]]:
    events: list[dict[str, object]] = []
    current_event = None
    current_data_lines: list[str] = []
    for line in raw_text.splitlines():
        if not line.strip():
            if current_event:
                payload = "\n".join(current_data_lines).strip()
                parsed = json.loads(payload) if payload else None
                events.append({"event": current_event, "data": parsed})
            current_event = None
            current_data_lines = []
            continue
        if line.startswith("event:"):
            current_event = line.split(":", 1)[1].strip()
            continue
        if line.startswith("data:"):
            current_data_lines.append(line.split(":", 1)[1].strip())
    if current_event:
        payload = "\n".join(current_data_lines).strip()
        parsed = json.loads(payload) if payload else None
        events.append({"event": current_event, "data": parsed})
    return events


def main() -> None:
    original_create = chat_module.create_chat_completion
    original_get_api_key = server_module.get_api_key
    app.state.compat_anthropic_requests = []
    chat_module.create_chat_completion = _fake_create_chat_completion
    server_module.get_api_key = lambda: "test-server-key"

    try:
        with TestClient(app) as client:
            bearer_auth_headers = {"Authorization": "Bearer test-server-key"}
            x_api_key_headers = {"x-api-key": "test-server-key"}
            headers = {
                **bearer_auth_headers,
                "anthropic-version": "2023-06-01",
            }
            x_api_key_request_headers = {
                **x_api_key_headers,
                "anthropic-version": "2023-06-01",
            }

            missing_version_response = client.post(
                "/v1/messages",
                headers=bearer_auth_headers,
                json={
                    "model": "claude-sonnet4.6",
                    "messages": [{"role": "user", "content": "missing version"}],
                },
            )

            invalid_version_response = client.post(
                "/v1/messages",
                headers={**bearer_auth_headers, "anthropic-version": "2024-01-01"},
                json={
                    "model": "claude-sonnet4.6",
                    "messages": [{"role": "user", "content": "bad version"}],
                },
            )

            non_stream_response = client.post(
                "/v1/messages",
                headers=headers,
                json={
                    "model": "claude-sonnet4.6",
                    "system": "be concise",
                    "messages": [
                        {"role": "user", "content": "hello from anthropic compat"}
                    ],
                    "temperature": 0.3,
                    "top_p": 0.8,
                    "max_tokens": 128,
                },
            )

            stream_response = client.post(
                "/v1/messages",
                headers=headers,
                json={
                    "model": "claude-sonnet4.6",
                    "messages": [{"role": "user", "content": "stream please"}],
                    "stream": True,
                },
            )

            stream_search_response = client.post(
                "/v1/messages",
                headers=headers,
                json={
                    "model": "claude-sonnet4.6-search",
                    "messages": [{"role": "user", "content": "stream search please"}],
                    "stream": True,
                },
            )

            invalid_response = client.post(
                "/v1/messages",
                headers=headers,
                json={"model": "claude-sonnet4.6", "messages": []},
            )

            x_api_key_response = client.post(
                "/v1/messages",
                headers=x_api_key_request_headers,
                json={
                    "model": "claude-sonnet4.6",
                    "messages": [{"role": "user", "content": "x-api-key auth works"}],
                },
            )

            search_response = client.post(
                "/v1/messages",
                headers=headers,
                json={
                    "model": "claude-sonnet4.6-search",
                    "messages": [{"role": "user", "content": "search variant please"}],
                },
            )

        missing_version_payload = missing_version_response.json()
        assert missing_version_response.status_code == 400, (
            missing_version_response.text
        )
        assert missing_version_payload == {
            "type": "error",
            "error": {
                "type": "invalid_request_error",
                "message": "anthropic-version header is required for Anthropic Messages compatibility.",
            },
        }, missing_version_payload

        invalid_version_payload = invalid_version_response.json()
        assert invalid_version_response.status_code == 400, (
            invalid_version_response.text
        )
        assert invalid_version_payload == {
            "type": "error",
            "error": {
                "type": "invalid_request_error",
                "message": "Unsupported anthropic-version '2024-01-01'. Expected '2023-06-01'.",
            },
        }, invalid_version_payload

        non_stream_payload = non_stream_response.json()
        assert non_stream_response.status_code == 200, non_stream_response.text
        assert non_stream_payload["type"] == "message", non_stream_payload
        assert non_stream_payload["role"] == "assistant", non_stream_payload
        assert (
            non_stream_payload["content"][0]["text"]
            == "compat anthropic reply https://example.com/search-result"
        ), non_stream_payload
        assert non_stream_payload["usage"] == {
            "input_tokens": 11,
            "output_tokens": 7,
        }, non_stream_payload

        events = _parse_sse_events(stream_response.text)
        event_names = [event["event"] for event in events]
        assert stream_response.status_code == 200, stream_response.text
        assert stream_response.headers["content-type"].startswith("text/event-stream")
        assert event_names[:2] == ["message_start", "content_block_start"], event_names
        assert "content_block_delta" in event_names, event_names
        assert event_names[-2:] == ["message_delta", "message_stop"], event_names
        delta_events = [
            event for event in events if event["event"] == "content_block_delta"
        ]
        delta_text = "".join(event["data"]["delta"]["text"] for event in delta_events)
        assert delta_text == "compat anthropic stream", delta_text

        stream_search_events = _parse_sse_events(stream_search_response.text)
        stream_search_event_names = [event["event"] for event in stream_search_events]
        assert stream_search_response.status_code == 200, stream_search_response.text
        stream_search_delta_events = [
            event
            for event in stream_search_events
            if event["event"] == "content_block_delta"
            and event["data"]["delta"]["type"] == "text_delta"
        ]
        stream_search_text = "".join(
            event["data"]["delta"]["text"] for event in stream_search_delta_events
        )
        assert stream_search_text == "compat anthropic stream", stream_search_text
        citation_deltas = [
            event["data"]["delta"]["citation"]
            for event in stream_search_events
            if event["event"] == "content_block_delta"
            and event["data"]["delta"]["type"] == "citations_delta"
        ]
        assert citation_deltas == [
            {
                "type": "web_search_result_location",
                "url": "https://example.com/search-stream",
                "title": "https://example.com/search-stream",
                "cited_text": "compat ",
            }
        ], citation_deltas

        invalid_payload = invalid_response.json()
        assert invalid_response.status_code == 400, invalid_response.text
        assert invalid_payload == {
            "type": "error",
            "error": {
                "type": "invalid_request_error",
                "message": "messages must contain at least one message.",
            },
        }, invalid_payload

        x_api_key_payload = x_api_key_response.json()
        assert x_api_key_response.status_code == 200, x_api_key_response.text
        assert (
            x_api_key_payload["content"][0]["text"]
            == "compat anthropic reply https://example.com/search-result"
        ), x_api_key_payload

        search_payload = search_response.json()
        assert search_response.status_code == 200, search_response.text
        assert (
            search_payload["content"][0]["text"]
            == "compat anthropic reply https://example.com/search-result"
        ), search_payload
        assert search_payload["content"][0]["citations"] == [
            {
                "type": "web_search_result_location",
                "url": "https://example.com/search-result",
                "title": "https://example.com/search-result",
                "cited_text": "compat anthropic reply https://example.com/search-result",
            }
        ], search_payload
        assert search_payload["usage"] == {"input_tokens": 11, "output_tokens": 7}, (
            search_payload
        )

        captured = app.state.compat_anthropic_requests
        assert len(captured) == 5, captured
        first_request = captured[0]
        assert first_request["messages"][0]["role"] == "system", first_request
        assert first_request["messages"][1]["role"] == "user", first_request
        assert first_request["temperature"] == 0.3, first_request
        assert first_request["top_p"] == 0.8, first_request
        assert first_request["max_tokens"] == 128, first_request
        assert captured[1]["stream"] is True, captured[1]
        assert captured[2]["model"] == "claude-sonnet4.6-search", captured[2]
        assert captured[2]["stream"] is True, captured[2]
        assert captured[3]["messages"][0]["role"] == "user", captured[3]
        assert captured[4]["model"] == "claude-sonnet4.6-search", captured[4]

        output = {
            "anthropic_search_messages": search_payload,
            "anthropic_x_api_key": x_api_key_payload,
            "anthropic_missing_version": missing_version_payload,
            "anthropic_invalid_version": invalid_version_payload,
            "anthropic_messages": non_stream_payload,
            "anthropic_stream_events": event_names,
            "anthropic_stream_text": delta_text,
            "anthropic_stream_search_events": stream_search_event_names,
            "anthropic_stream_search_citations": citation_deltas,
            "anthropic_invalid": invalid_payload,
            "captured_requests": captured,
        }
        print(json.dumps(output, ensure_ascii=False))
    finally:
        chat_module.create_chat_completion = original_create
        server_module.get_api_key = original_get_api_key
        if hasattr(app.state, "compat_anthropic_requests"):
            delattr(app.state, "compat_anthropic_requests")


if __name__ == "__main__":
    main()
