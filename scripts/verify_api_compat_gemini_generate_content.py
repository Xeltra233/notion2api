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
    request.app.state.compat_gemini_requests.append(
        {
            "model": req_body.model,
            "stream": bool(req_body.stream),
            "messages": [message.model_dump() for message in req_body.messages],
            "temperature": req_body.temperature,
            "top_p": req_body.top_p,
            "max_tokens": req_body.max_tokens,
        }
    )
    response.headers["X-Conversation-Id"] = "compat-gemini-conv"

    if req_body.stream:

        async def event_generator():
            yield 'data: {"choices": [{"index": 0, "delta": {"content": "compat "}}]}\n\n'
            if str(req_body.model or "").endswith("-search"):
                yield 'data: {"type": "search_metadata", "searches": {"queries": ["stream search please"], "sources": ["https://example.com/search-stream"]}}\n\n'
            yield 'data: {"choices": [{"index": 0, "delta": {"content": "gemini stream"}}]}\n\n'
            yield 'data: {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]}\n\n'
            yield "data: [DONE]\n\n"

        return StreamingResponse(event_generator(), media_type="text/event-stream")

    payload = ChatCompletionResponse(
        id="chatcmpl-gemini-compat",
        model="gemini-3.1pro",
        choices=[
            ChatMessageResponseChoice(
                index=0,
                message=ChatMessage(
                    role="assistant",
                    content="compat gemini reply https://example.com/search-result",
                ),
                finish_reason="stop",
            )
        ],
        usage={"prompt_tokens": 13, "completion_tokens": 9, "total_tokens": 22},
    )
    if str(req_body.model or "").endswith("-search"):
        payload.search_metadata = {
            "queries": ["search variant please"],
            "sources": ["https://example.com/search-result"],
        }
    return payload


def main() -> None:
    original_create = chat_module.create_chat_completion
    original_get_api_key = server_module.get_api_key
    app.state.compat_gemini_requests = []
    chat_module.create_chat_completion = _fake_create_chat_completion
    server_module.get_api_key = lambda: "test-server-key"

    try:
        with TestClient(app) as client:
            headers = {"Authorization": "Bearer test-server-key"}

            non_stream_response = client.post(
                "/v1beta/models/gemini-3.1-pro:generateContent",
                headers=headers,
                json={
                    "systemInstruction": {
                        "parts": [{"text": "be concise"}],
                    },
                    "contents": [
                        {
                            "role": "user",
                            "parts": [{"text": "hello from gemini compat"}],
                        }
                    ],
                    "generationConfig": {
                        "temperature": 0.4,
                        "topP": 0.7,
                        "maxOutputTokens": 256,
                    },
                },
            )

            stream_response = client.post(
                "/v1beta/models/gemini-3.1-pro:streamGenerateContent",
                headers=headers,
                json={
                    "contents": [
                        {
                            "role": "user",
                            "parts": [{"text": "stream please"}],
                        }
                    ]
                },
            )

            stream_search_response = client.post(
                "/v1beta/models/gemini-3.1-pro-search:streamGenerateContent",
                headers=headers,
                json={
                    "contents": [
                        {
                            "role": "user",
                            "parts": [{"text": "stream search please"}],
                        }
                    ]
                },
            )

            invalid_role_response = client.post(
                "/v1beta/models/gemini-3.1-pro:generateContent",
                headers=headers,
                json={
                    "contents": [
                        {
                            "role": "tool",
                            "parts": [{"text": "bad role"}],
                        }
                    ]
                },
            )

            invalid_safety_response = client.post(
                "/v1beta/models/gemini-3.1-pro:generateContent",
                headers=headers,
                json={
                    "contents": [
                        {
                            "role": "user",
                            "parts": [{"text": "hello"}],
                        }
                    ],
                    "safetySettings": [{"category": "HARM_CATEGORY_HATE_SPEECH"}],
                },
            )

            search_response = client.post(
                "/v1beta/models/gemini-3.1-pro-search:generateContent",
                headers=headers,
                json={
                    "contents": [
                        {
                            "role": "user",
                            "parts": [{"text": "search variant please"}],
                        }
                    ]
                },
            )

        non_stream_payload = non_stream_response.json()
        assert non_stream_response.status_code == 200, non_stream_response.text
        assert non_stream_payload["candidates"][0]["content"]["role"] == "model", (
            non_stream_payload
        )
        assert (
            non_stream_payload["candidates"][0]["content"]["parts"][0]["text"]
            == "compat gemini reply https://example.com/search-result"
        ), non_stream_payload
        assert non_stream_payload["candidates"][0]["finishReason"] == "STOP", (
            non_stream_payload
        )
        assert non_stream_payload["usageMetadata"] == {
            "promptTokenCount": 13,
            "candidatesTokenCount": 9,
            "totalTokenCount": 22,
        }, non_stream_payload

        stream_lines = [
            line for line in stream_response.text.splitlines() if line.strip()
        ]
        stream_payloads = [json.loads(line) for line in stream_lines]
        assert stream_response.status_code == 200, stream_response.text
        content_type = str(stream_response.headers.get("content-type") or "")
        assert "application/x-ndjson" in content_type, content_type
        assert (
            stream_payloads[0]["candidates"][0]["content"]["parts"][0]["text"]
            == "compat "
        ), stream_payloads
        assert (
            stream_payloads[-1]["candidates"][0]["content"]["parts"][0]["text"]
            == "compat gemini stream"
        ), stream_payloads
        assert stream_payloads[-1]["candidates"][0]["finishReason"] == "STOP", (
            stream_payloads
        )

        stream_search_lines = [
            line for line in stream_search_response.text.splitlines() if line.strip()
        ]
        stream_search_payloads = [json.loads(line) for line in stream_search_lines]
        assert stream_search_response.status_code == 200, stream_search_response.text
        assert (
            stream_search_payloads[0]["candidates"][0]["content"]["parts"][0]["text"]
            == "compat "
        ), stream_search_payloads
        assert stream_search_payloads[1]["candidates"][0]["groundingMetadata"] == {
            "webSearchQueries": ["stream search please"],
            "groundingChunks": [
                {
                    "web": {
                        "uri": "https://example.com/search-stream",
                        "title": "https://example.com/search-stream",
                    }
                }
            ],
        }, stream_search_payloads
        assert (
            stream_search_payloads[-1]["candidates"][0]["content"]["parts"][0]["text"]
            == "compat gemini stream"
        ), stream_search_payloads
        assert stream_search_payloads[-1]["candidates"][0]["groundingMetadata"] == {
            "webSearchQueries": ["stream search please"],
            "groundingChunks": [
                {
                    "web": {
                        "uri": "https://example.com/search-stream",
                        "title": "https://example.com/search-stream",
                    }
                }
            ],
        }, stream_search_payloads
        assert stream_search_payloads[-1]["candidates"][0]["finishReason"] == "STOP", (
            stream_search_payloads
        )

        invalid_role_payload = invalid_role_response.json()
        assert invalid_role_response.status_code == 400, invalid_role_response.text
        assert invalid_role_payload == {
            "error": {
                "code": 400,
                "message": "contents[0].role 'tool' is not supported.",
                "status": "INVALID_ARGUMENT",
            }
        }, invalid_role_payload

        invalid_safety_payload = invalid_safety_response.json()
        assert invalid_safety_response.status_code == 400, invalid_safety_response.text
        assert invalid_safety_payload == {
            "error": {
                "code": 400,
                "message": "Gemini safetySettings are not supported by this compatibility endpoint.",
                "status": "INVALID_ARGUMENT",
            }
        }, invalid_safety_payload

        search_payload = search_response.json()
        assert search_response.status_code == 200, search_response.text
        assert (
            search_payload["candidates"][0]["content"]["parts"][0]["text"]
            == "compat gemini reply https://example.com/search-result"
        ), search_payload
        assert search_payload["candidates"][0]["groundingMetadata"] == {
            "webSearchQueries": ["search variant please"],
            "groundingChunks": [
                {
                    "web": {
                        "uri": "https://example.com/search-result",
                        "title": "https://example.com/search-result",
                    }
                }
            ],
        }, search_payload
        assert search_payload["usageMetadata"] == {
            "promptTokenCount": 13,
            "candidatesTokenCount": 9,
            "totalTokenCount": 22,
        }, search_payload

        captured = app.state.compat_gemini_requests
        assert len(captured) == 4, captured
        first_request = captured[0]
        assert first_request["model"] == "gemini-3.1-pro", first_request
        assert first_request["messages"][0]["role"] == "system", first_request
        assert first_request["messages"][1]["role"] == "user", first_request
        assert first_request["temperature"] == 0.4, first_request
        assert first_request["top_p"] == 0.7, first_request
        assert first_request["max_tokens"] == 256, first_request
        assert captured[1]["stream"] is True, captured[1]
        assert captured[2]["model"] == "gemini-3.1-pro-search", captured[2]
        assert captured[2]["stream"] is True, captured[2]
        assert captured[3]["model"] == "gemini-3.1-pro-search", captured[3]

        output = {
            "gemini_search_generate_content": search_payload,
            "gemini_generate_content": non_stream_payload,
            "gemini_stream_chunks": stream_payloads,
            "gemini_stream_search_chunks": stream_search_payloads,
            "gemini_invalid_role": invalid_role_payload,
            "gemini_invalid_safety": invalid_safety_payload,
            "captured_requests": captured,
        }
        print(json.dumps(output, ensure_ascii=False))
    finally:
        chat_module.create_chat_completion = original_create
        server_module.get_api_key = original_get_api_key
        if hasattr(app.state, "compat_gemini_requests"):
            delattr(app.state, "compat_gemini_requests")


if __name__ == "__main__":
    main()
