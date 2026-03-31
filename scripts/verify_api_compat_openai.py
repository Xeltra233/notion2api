import json
import os
import sys
from pathlib import Path

import asyncio

from fastapi import BackgroundTasks, Response
from fastapi.responses import StreamingResponse
from fastapi.testclient import TestClient
from starlette.requests import Request


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
    request.app.state.compat_openai_requests.append(
        {
            "model": req_body.model,
            "stream": bool(req_body.stream),
            "messages": [message.model_dump() for message in req_body.messages],
            "temperature": req_body.temperature,
            "top_p": req_body.top_p,
            "max_tokens": req_body.max_tokens,
            "metadata": req_body.metadata,
            "user": req_body.user,
            "conversation_id": req_body.conversation_id,
        }
    )
    response.headers["X-Conversation-Id"] = "compat-openai-conv"

    if req_body.stream:

        async def event_generator():
            yield 'data: {"id":"chatcmpl-openai-stream","object":"chat.completion.chunk","model":"gpt-5.4","choices":[{"index":0,"delta":{"role":"assistant"},"finish_reason":null}]}\n\n'
            yield 'data: {"id":"chatcmpl-openai-stream","object":"chat.completion.chunk","model":"gpt-5.4","choices":[{"index":0,"delta":{"content":"compat "},"finish_reason":null}]}\n\n'
            yield 'data: {"id":"chatcmpl-openai-stream","object":"chat.completion.chunk","model":"gpt-5.4","choices":[{"index":0,"delta":{"content":"openai stream"},"finish_reason":null}]}\n\n'
            yield 'data: {"id":"chatcmpl-openai-stream","object":"chat.completion.chunk","model":"gpt-5.4","choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}\n\n'
            yield "data: [DONE]\n\n"

        return StreamingResponse(event_generator(), media_type="text/event-stream")

    payload = ChatCompletionResponse(
        id="chatcmpl-openai-compat",
        model="gpt-5.4",
        choices=[
            ChatMessageResponseChoice(
                index=0,
                message=ChatMessage(
                    role="assistant",
                    content="compat openai reply https://example.com/search-result",
                ),
                finish_reason="stop",
            )
        ],
        usage={"prompt_tokens": 17, "completion_tokens": 8, "total_tokens": 25},
    )
    if str(req_body.model or "").endswith("-search"):
        payload.search_metadata = {
            "queries": ["search variant please"],
            "sources": ["https://example.com/search-result"],
        }
    return payload


def _parse_sse_frames(raw_text: str) -> list[str]:
    return [frame.strip() for frame in raw_text.split("\n\n") if frame.strip()]


def _parse_responses_events(raw_text: str) -> list[dict[str, object]]:
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
    app.state.compat_openai_requests = []
    chat_module.create_chat_completion = _fake_create_chat_completion
    server_module.get_api_key = lambda: "test-server-key"

    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        def run_async(coro):
            return loop.run_until_complete(coro)

        scope = {
            "type": "http",
            "app": app,
            "client": ("testclient", 50000),
            "server": ("testserver", 80),
            "scheme": "http",
            "root_path": "",
            "headers": [],
            "path_params": {},
            "query_string": b"",
        }

        def make_request(path: str, headers: dict[str, str] | None = None):
            encoded_headers = [
                (key.lower().encode("latin-1"), value.encode("latin-1"))
                for key, value in (headers or {}).items()
            ]
            request = Request({**scope, "path": path, "headers": encoded_headers})
            request._url = request.url.replace(path=path)
            return request

        async def collect_streaming_body(stream: StreamingResponse) -> str:
            parts: list[str] = []
            async for chunk in stream.body_iterator:
                parts.append(
                    chunk.decode("utf-8") if isinstance(chunk, bytes) else str(chunk)
                )
            return "".join(parts)

        auth_headers = {"Authorization": "Bearer test-server-key"}

        chat_response_obj = Response()
        chat_result = run_async(
            chat_module.create_chat_completion(
                request=make_request("/v1/chat/completions", auth_headers),
                req_body=chat_module.ChatCompletionRequest(
                    model="gpt-5.4",
                    messages=[
                        chat_module.ChatMessage(
                            role="user", content="hello from openai compat"
                        )
                    ],
                    temperature=0.2,
                    top_p=0.9,
                    max_tokens=64,
                    metadata={"source": "compat-test"},
                    user="openai-test-user",
                ),
                background_tasks=BackgroundTasks(),
                response=chat_response_obj,
                x_chat_session=None,
            )
        )

        chat_stream_response_obj = Response()
        chat_stream_result = run_async(
            chat_module.create_chat_completion(
                request=make_request("/v1/chat/completions", auth_headers),
                req_body=chat_module.ChatCompletionRequest(
                    model="gpt-5.4",
                    messages=[
                        chat_module.ChatMessage(role="user", content="stream please")
                    ],
                    stream=True,
                ),
                background_tasks=BackgroundTasks(),
                response=chat_stream_response_obj,
                x_chat_session=None,
            )
        )

        responses_response_obj = Response()
        responses_result = run_async(
            chat_module.create_responses(
                request=make_request("/v1/responses", auth_headers),
                req_body=chat_module.ResponsesRequest(
                    model="gpt-5.4",
                    input="hello from responses compat",
                    instructions="be concise",
                    temperature=0.6,
                    top_p=0.75,
                    max_output_tokens=96,
                ),
                background_tasks=BackgroundTasks(),
                response=responses_response_obj,
                x_chat_session=None,
            )
        )

        responses_stream_response_obj = Response()
        responses_stream_result = run_async(
            chat_module.create_responses(
                request=make_request("/v1/responses", auth_headers),
                req_body=chat_module.ResponsesRequest(
                    model="gpt-5.4",
                    input="stream responses please",
                    stream=True,
                ),
                background_tasks=BackgroundTasks(),
                response=responses_stream_response_obj,
                x_chat_session=None,
            )
        )

        search_response_obj = Response()
        search_result = run_async(
            chat_module.create_chat_completion(
                request=make_request("/v1/chat/completions", auth_headers),
                req_body=chat_module.ChatCompletionRequest(
                    model="gpt-5.4-search",
                    messages=[
                        chat_module.ChatMessage(
                            role="user", content="search variant please"
                        )
                    ],
                ),
                background_tasks=BackgroundTasks(),
                response=search_response_obj,
                x_chat_session=None,
            )
        )

        invalid_status = None
        invalid_payload = None
        try:
            invalid_req = chat_module.ChatCompletionRequest(
                model="gpt-5.4",
                messages=[chat_module.ChatMessage(role="user", content=[])],
            )
            invalid_req.messages = chat_module._normalize_request_messages(
                invalid_req.messages
            )
        except chat_module.HTTPException as exc:
            invalid_status = exc.status_code
            invalid_payload = {"detail": exc.detail}

        unsupported_tool_status = None
        unsupported_tool_payload = None
        try:
            unsupported_tool_req = chat_module.ChatCompletionRequest(
                model="gpt-5.4",
                messages=[
                    chat_module.ChatMessage(role="user", content="call a weather tool")
                ],
                tools=[
                    {
                        "type": "function",
                        "function": {
                            "name": "get_weather",
                            "description": "Fetch weather",
                            "parameters": {"type": "object", "properties": {}},
                        },
                    }
                ],
            )
            unsupported_tool_req.messages = chat_module._normalize_request_messages(
                unsupported_tool_req.messages
            )
            chat_module._validate_tooling_params(unsupported_tool_req)
        except chat_module.HTTPException as exc:
            unsupported_tool_status = exc.status_code
            unsupported_tool_payload = {"detail": exc.detail}

        unsupported_responses_status = None
        unsupported_responses_payload = None
        try:
            run_async(
                chat_module.create_responses(
                    request=make_request("/v1/responses", auth_headers),
                    req_body=chat_module.ResponsesRequest(
                        model="gpt-5.4",
                        input=[
                            {
                                "type": "input_image",
                                "image_url": "https://example.com/image.png",
                            }
                        ],
                    ),
                    background_tasks=BackgroundTasks(),
                    response=Response(),
                    x_chat_session=None,
                )
            )
        except chat_module.HTTPException as exc:
            unsupported_responses_status = exc.status_code
            unsupported_responses_payload = {"detail": exc.detail}

        chat_payload = chat_result.model_dump()
        assert chat_payload["object"] == "chat.completion", chat_payload
        assert (
            chat_payload["choices"][0]["message"]["content"]
            == "compat openai reply https://example.com/search-result"
        ), chat_payload
        assert chat_payload["usage"] == {
            "prompt_tokens": 17,
            "completion_tokens": 8,
            "total_tokens": 25,
        }, chat_payload
        assert (
            chat_response_obj.headers.get("X-Conversation-Id") == "compat-openai-conv"
        ), dict(chat_response_obj.headers)

        assert isinstance(chat_stream_result, StreamingResponse), type(
            chat_stream_result
        )
        chat_stream_text_raw = run_async(collect_streaming_body(chat_stream_result))
        chat_stream_frames = _parse_sse_frames(chat_stream_text_raw)
        assert str(chat_stream_result.media_type or "").startswith("text/event-stream")
        assert chat_stream_frames[-1] == "data: [DONE]", chat_stream_frames
        chat_stream_payloads = [
            json.loads(frame[5:].strip()) for frame in chat_stream_frames[:-1]
        ]
        assert chat_stream_payloads[0]["choices"][0]["delta"]["role"] == "assistant", (
            chat_stream_payloads
        )
        stream_text = "".join(
            payload["choices"][0].get("delta", {}).get("content", "")
            for payload in chat_stream_payloads
        )
        assert stream_text == "compat openai stream", stream_text
        assert chat_stream_payloads[-1]["choices"][0]["finish_reason"] == "stop", (
            chat_stream_payloads[-1]
        )

        responses_payload = responses_result
        assert responses_payload["object"] == "response", responses_payload
        assert responses_payload["status"] == "completed", responses_payload
        assert (
            responses_payload["output_text"]
            == "compat openai reply https://example.com/search-result"
        ), responses_payload
        assert responses_payload["output"][0]["role"] == "assistant", responses_payload
        assert responses_payload["usage"] == {
            "prompt_tokens": 17,
            "completion_tokens": 8,
            "total_tokens": 25,
        }, responses_payload

        assert isinstance(responses_stream_result, StreamingResponse), type(
            responses_stream_result
        )
        responses_stream_text_raw = run_async(
            collect_streaming_body(responses_stream_result)
        )
        responses_events = _parse_responses_events(responses_stream_text_raw)
        responses_event_names = [event["event"] for event in responses_events]
        assert str(responses_stream_result.media_type or "").startswith(
            "text/event-stream"
        )
        assert responses_event_names[:2] == [
            "response.created",
            "response.output_item.added",
        ], responses_event_names
        assert "response.output_text.delta" in responses_event_names, (
            responses_event_names
        )
        assert (
            responses_event_names[-2:] == ["response.completed", None]
            or responses_event_names[-1] == "response.completed"
        )
        response_delta_text = "".join(
            event["data"]["delta"]
            for event in responses_events
            if event["event"] == "response.output_text.delta"
        )
        assert response_delta_text == "compat openai stream", response_delta_text
        completed_event = next(
            event
            for event in responses_events
            if event["event"] == "response.completed"
        )
        assert (
            completed_event["data"]["response"]["output_text"] == "compat openai stream"
        ), completed_event

        search_payload = search_result.model_dump()
        assert (
            search_payload["choices"][0]["message"]["content"]
            == "compat openai reply https://example.com/search-result"
        ), search_payload
        assert search_payload["search_metadata"] == {
            "queries": ["search variant please"],
            "sources": ["https://example.com/search-result"],
        }, search_payload
        assert (
            search_response_obj.headers.get("X-Conversation-Id") == "compat-openai-conv"
        ), dict(search_response_obj.headers)

        assert invalid_status == 400, invalid_status
        assert invalid_payload == {
            "detail": "messages[0].content cannot be an empty array."
        }, invalid_payload
        assert unsupported_tool_status == 400, unsupported_tool_status
        assert unsupported_tool_payload == {
            "detail": "This API only supports search-style tools for tool declarations. General tool calling is not implemented."
        }, unsupported_tool_payload
        assert unsupported_responses_status == 400, unsupported_responses_status
        assert unsupported_responses_payload == {
            "detail": "responses.input item arrays currently only support text-like items; unsupported item types: input_image"
        }, unsupported_responses_payload

        captured = app.state.compat_openai_requests
        assert len(captured) == 5, captured
        first_request = captured[0]
        assert first_request["metadata"] == {"source": "compat-test"}, first_request
        assert first_request["user"] == "openai-test-user", first_request
        assert first_request["temperature"] == 0.2, first_request
        assert first_request["top_p"] == 0.9, first_request
        assert first_request["max_tokens"] == 64, first_request
        assert captured[1]["stream"] is True, captured[1]
        assert captured[2]["messages"][0]["role"] == "system", captured[2]
        assert captured[2]["messages"][1]["role"] == "user", captured[2]
        assert captured[2]["temperature"] == 0.6, captured[2]
        assert captured[2]["top_p"] == 0.75, captured[2]
        assert captured[2]["max_tokens"] == 96, captured[2]
        assert captured[3]["stream"] is True, captured[3]
        assert captured[4]["model"] == "gpt-5.4-search", captured[4]

        output = {
            "search_chat_completion": search_payload,
            "chat_completions": chat_payload,
            "chat_stream_text": stream_text,
            "responses": responses_payload,
            "responses_stream_events": responses_event_names,
            "responses_stream_text": response_delta_text,
            "invalid_chat_completion": invalid_payload,
            "unsupported_tool_calling": unsupported_tool_payload,
            "unsupported_responses_typed_item": unsupported_responses_payload,
            "captured_requests": captured,
        }
        print(json.dumps(output, ensure_ascii=False))
    finally:
        chat_module.create_chat_completion = original_create
        server_module.get_api_key = original_get_api_key
        try:
            asyncio.get_event_loop().close()
        except RuntimeError:
            pass
        asyncio.set_event_loop(None)
        if hasattr(app.state, "compat_openai_requests"):
            delattr(app.state, "compat_openai_requests")


if __name__ == "__main__":
    main()
