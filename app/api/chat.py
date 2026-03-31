import asyncio
import base64
from difflib import SequenceMatcher
import imghdr
import json
import mimetypes
import re
import secrets
import time
import uuid
from pathlib import Path
from typing import Any, Dict, Generator, Iterable, List, Tuple
from urllib.parse import urljoin

from fastapi import APIRouter, BackgroundTasks, Header, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse

from app.conversation import (
    build_lite_transcript,
    build_standard_transcript,
    compress_round_if_needed,
    compress_sliding_window_round,
    content_to_text,
)
from app.config import (
    get_media_public_base_url,
    get_media_storage_path,
    is_lite_mode,
)
from app.limiter import limiter
from app.logger import logger
from app.model_registry import (
    is_search_model,
    is_supported_model,
    list_available_models,
)
from app.notion_client import NotionUpstreamError
from app.usage import estimate_token_count
from app.schemas import (
    AnthropicMessagesRequest,
    ChatCompletionRequest,
    ChatCompletionResponse,
    ChatMessage,
    ChatMessageResponseChoice,
    GeminiGenerateContentRequest,
    MediaUploadRequest,
    MediaUploadResponse,
    ResponsesRequest,
)
from app.api.admin import _ensure_chat_access

router = APIRouter()
gemini_router = APIRouter()

ANTHROPIC_VERSION_HEADER = "2023-06-01"

RECALL_INTENT_KEYWORDS = [
    "之前",
    "上次",
    "以前",
    "你还记得",
    "我们之前",
    "earlier",
    "before",
    "recall",
    "remember",
    "之前说过",
    "历史记录",
    "找一下",
    "搜索记忆",
]

USER_CONTENT_TYPES = {"text", "image_url"}
SEARCH_TOOL_NAMES = {"search", "web_search", "browser.search", "web_search_preview"}
MAX_DATA_IMAGE_BYTES = 5 * 1024 * 1024
MAX_IMAGE_PARTS_PER_MESSAGE = 4
ALLOWED_DATA_IMAGE_MIME_PREFIXES = (
    "data:image/png;base64,",
    "data:image/jpeg;base64,",
    "data:image/jpg;base64,",
    "data:image/webp;base64,",
    "data:image/gif;base64,",
)
_MEDIA_EXTENSION_BY_MIME = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/webp": ".webp",
    "image/gif": ".gif",
}


def _resolve_media_storage_dir() -> Path:
    return get_media_storage_path()


def _guess_media_extension(mime_type: str) -> str:
    normalized = str(mime_type or "").strip().lower()
    if normalized in _MEDIA_EXTENSION_BY_MIME:
        return _MEDIA_EXTENSION_BY_MIME[normalized]
    guessed = mimetypes.guess_extension(normalized)
    return guessed or ".bin"


def _detect_image_mime_type(data: bytes, *, fallback: str = "") -> str:
    kind = imghdr.what(None, h=data)
    if kind == "jpeg":
        return "image/jpeg"
    if kind == "png":
        return "image/png"
    if kind == "webp":
        return "image/webp"
    if kind == "gif":
        return "image/gif"
    lowered_fallback = str(fallback or "").strip().lower()
    if lowered_fallback in _MEDIA_EXTENSION_BY_MIME:
        return lowered_fallback
    raise HTTPException(
        status_code=400, detail="Only png, jpeg, webp, and gif images are supported."
    )


def _parse_data_image_url(url: str, *, param: str) -> tuple[str, bytes]:
    lowered = url.lower()
    if not lowered.startswith(ALLOWED_DATA_IMAGE_MIME_PREFIXES):
        raise HTTPException(
            status_code=400,
            detail=(
                f"{param} only supports base64 data URIs for png, jpeg, jpg, webp, or gif images."
            ),
        )
    try:
        header, encoded = url.split(",", 1)
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail=f"{param} is not a valid base64 data URI.",
        ) from exc
    try:
        decoded = base64.b64decode(encoded, validate=True)
    except Exception as exc:
        raise HTTPException(
            status_code=400,
            detail=f"{param} contains invalid base64 image data.",
        ) from exc
    if not decoded:
        raise HTTPException(
            status_code=400,
            detail=f"{param} image payload cannot be empty.",
        )
    if len(decoded) > MAX_DATA_IMAGE_BYTES:
        raise HTTPException(
            status_code=400,
            detail=(
                f"{param} image payload is too large. Limit is {MAX_DATA_IMAGE_BYTES // (1024 * 1024)}MB after decoding."
            ),
        )
    declared_mime = header.split(";", 1)[0].split(":", 1)[-1].strip().lower()
    mime_type = _detect_image_mime_type(decoded, fallback=declared_mime)
    return mime_type, decoded


def _sanitize_media_filename(file_name: str | None, *, mime_type: str) -> str:
    candidate = Path(str(file_name or "upload")).name
    stem = (
        re.sub(r"[^A-Za-z0-9._-]+", "-", Path(candidate).stem).strip("-._") or "image"
    )
    extension = _guess_media_extension(mime_type)
    return f"{stem}{extension}"


def _build_media_public_url(request: Request, media_id: str) -> str:
    public_base = get_media_public_base_url()
    if public_base:
        normalized_base = public_base.rstrip("/") + "/"
        return urljoin(normalized_base, media_id)
    return str(request.url_for("get_media_file", media_id=media_id))


def _store_media_data(
    request: Request, *, data_url: str, file_name: str | None = None
) -> dict[str, Any]:
    mime_type, decoded = _parse_data_image_url(data_url, param="data_url")
    media_dir = _resolve_media_storage_dir()
    media_id = (
        f"{int(time.time())}-{secrets.token_hex(8)}{_guess_media_extension(mime_type)}"
    )
    media_path = media_dir / media_id
    media_path.write_bytes(decoded)
    safe_file_name = _sanitize_media_filename(file_name, mime_type=mime_type)
    return {
        "media_id": media_id,
        "file_name": safe_file_name,
        "mime_type": mime_type,
        "size_bytes": len(decoded),
        "path": media_path,
        "url": _build_media_public_url(request, media_id),
    }


def _wants_search_tools(tools: Any) -> bool:
    if not isinstance(tools, list):
        return False
    for tool in tools:
        if not isinstance(tool, dict):
            continue
        tool_type = str(tool.get("type", "") or "").strip().lower()
        if tool_type in SEARCH_TOOL_NAMES:
            return True
        if tool_type == "function":
            function_def = tool.get("function")
            if isinstance(function_def, dict):
                name = str(function_def.get("name", "") or "").strip().lower()
                if name in SEARCH_TOOL_NAMES:
                    return True
    return False


def _search_enabled(req_body: ChatCompletionRequest) -> bool:
    if is_search_model(req_body.model):
        return True
    if _wants_search_tools(req_body.tools):
        return True
    metadata = req_body.metadata or {}
    if isinstance(metadata, dict) and bool(
        metadata.get("web_search") or metadata.get("search")
    ):
        return True
    return False


def _looks_truncated(text: str) -> bool:
    stripped = str(text or "").rstrip()
    if not stripped:
        return False
    if stripped.endswith(
        ("...", "...)", "，", ",", ":", "：", "-", "(", "[", "{", " and", " or", " to")
    ):
        return True
    tail = stripped[-1]
    if tail.isalnum():
        lines = [line.strip() for line in stripped.splitlines() if line.strip()]
        if lines and len(lines[-1]) > 24:
            return True
    return False


def _build_retry_prompt(original_prompt: Any, partial_text: str) -> str:
    prompt_text = content_to_text(original_prompt).strip()
    partial = str(partial_text or "").strip()
    if not partial:
        return prompt_text
    return (
        f"{prompt_text}\n\n"
        "The previous answer may have been cut off. Continue from the last sentence without repeating the earlier completed content.\n\n"
        f"Partial answer:\n{partial}"
    )


def _validate_sampling_params(req_body: ChatCompletionRequest) -> None:
    if req_body.temperature is not None and not (0 <= req_body.temperature <= 2):
        raise HTTPException(
            status_code=400, detail="temperature must be between 0 and 2."
        )
    if req_body.top_p is not None and not (0 <= req_body.top_p <= 1):
        raise HTTPException(status_code=400, detail="top_p must be between 0 and 1.")
    if req_body.max_tokens is not None and req_body.max_tokens <= 0:
        raise HTTPException(
            status_code=400, detail="max_tokens must be greater than 0."
        )
    if req_body.presence_penalty is not None and not (
        -2 <= req_body.presence_penalty <= 2
    ):
        raise HTTPException(
            status_code=400,
            detail="presence_penalty must be between -2 and 2.",
        )
    if req_body.frequency_penalty is not None and not (
        -2 <= req_body.frequency_penalty <= 2
    ):
        raise HTTPException(
            status_code=400,
            detail="frequency_penalty must be between -2 and 2.",
        )


def _validate_tooling_params(req_body: ChatCompletionRequest) -> None:
    tools = req_body.tools if isinstance(req_body.tools, list) else []
    if not tools:
        return
    if _wants_search_tools(tools):
        return
    raise HTTPException(
        status_code=400,
        detail=(
            "This API only supports search-style tools for tool declarations. "
            "General tool calling is not implemented."
        ),
    )


def _max_attempts_for_request(
    req_body: ChatCompletionRequest, client_count: int
) -> int:
    base_limit = 4 if _search_enabled(req_body) else 3
    return min(base_limit, max(1, client_count))


def _validate_media_url(url: str, *, param: str) -> None:
    raw = str(url or "").strip()
    if not raw:
        raise HTTPException(status_code=400, detail=f"{param} cannot be empty.")
    if raw.startswith(("http://", "https://")):
        return
    if raw.startswith("data:image/"):
        _validate_data_image_url(raw, param=param)
        return
    raise HTTPException(
        status_code=400, detail=f"{param} must be an http(s) URL or data URI."
    )


def _validate_data_image_url(url: str, *, param: str) -> None:
    _parse_data_image_url(url, param=param)


def _normalize_message_content(content: Any, *, role: str, param_prefix: str) -> Any:
    if isinstance(content, str):
        if role in {"user", "system", "developer"} and not content.strip():
            raise HTTPException(
                status_code=400, detail=f"{param_prefix} cannot be empty."
            )
        return content

    if not isinstance(content, list):
        raise HTTPException(
            status_code=400, detail=f"{param_prefix} must be a string or content array."
        )

    if not content:
        raise HTTPException(
            status_code=400, detail=f"{param_prefix} cannot be an empty array."
        )

    normalized: List[Dict[str, Any]] = []
    has_visible_part = False
    image_part_count = 0

    for part_idx, part in enumerate(content):
        if hasattr(part, "model_dump"):
            part = part.model_dump()
        elif hasattr(part, "dict"):
            part = part.dict()
        if not isinstance(part, dict):
            raise HTTPException(
                status_code=400, detail=f"{param_prefix}.{part_idx} must be an object."
            )

        part_type = str(part.get("type", "") or "").strip().lower()
        if role == "user":
            if part_type not in USER_CONTENT_TYPES:
                raise HTTPException(
                    status_code=400,
                    detail=f"{param_prefix}.{part_idx}.type '{part_type}' is not supported.",
                )
        elif part_type != "text":
            raise HTTPException(
                status_code=400,
                detail=f"Only user messages support non-text content blocks. Invalid block at {param_prefix}.{part_idx}.",
            )

        if part_type == "text":
            text = str(part.get("text", "") or "")
            if not text.strip():
                raise HTTPException(
                    status_code=400,
                    detail=f"{param_prefix}.{part_idx}.text cannot be empty.",
                )
            normalized.append({"type": "text", "text": text})
            has_visible_part = True
            continue

        image_payload = part.get("image_url")
        if not isinstance(image_payload, dict):
            raise HTTPException(
                status_code=400,
                detail=f"{param_prefix}.{part_idx}.image_url must be an object.",
            )
        url = str(image_payload.get("url", "") or "").strip()
        _validate_media_url(url, param=f"{param_prefix}.{part_idx}.image_url.url")
        image_part_count += 1
        if image_part_count > MAX_IMAGE_PARTS_PER_MESSAGE:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"{param_prefix} supports at most {MAX_IMAGE_PARTS_PER_MESSAGE} image attachments per message."
                ),
            )
        normalized.append({"type": "image_url", "image_url": {"url": url}})
        has_visible_part = True

    if role in {"user", "system", "developer"} and not has_visible_part:
        raise HTTPException(
            status_code=400,
            detail=f"{param_prefix} must contain at least one non-empty content block.",
        )

    return normalized


def _normalize_request_messages(messages: List[ChatMessage]) -> List[ChatMessage]:
    normalized_messages: List[ChatMessage] = []
    for idx, msg in enumerate(messages):
        normalized_content = _normalize_message_content(
            msg.content,
            role=msg.role,
            param_prefix=f"messages[{idx}].content",
        )
        normalized_messages.append(
            ChatMessage(
                role=msg.role, content=normalized_content, thinking=msg.thinking
            )
        )
    return normalized_messages


def _build_messages_from_responses_input(
    req_body: ResponsesRequest,
) -> List[ChatMessage]:
    instructions = str(req_body.instructions or "").strip()
    normalized_messages: List[ChatMessage] = []
    if instructions:
        normalized_messages.append(ChatMessage(role="system", content=instructions))

    payload = req_body.input
    if isinstance(payload, str):
        normalized_messages.append(ChatMessage(role="user", content=payload))
        return _normalize_request_messages(normalized_messages)

    if isinstance(payload, list):
        if payload and all(
            isinstance(item, dict) and "type" in item for item in payload
        ):
            unsupported_types = sorted(
                {
                    str(item.get("type", "") or "").strip()
                    for item in payload
                    if isinstance(item, dict)
                    and str(item.get("type", "") or "").strip()
                    not in {"input_text", "text"}
                }
            )
            if unsupported_types:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        "responses.input item arrays currently only support text-like items; "
                        f"unsupported item types: {', '.join(unsupported_types)}"
                    ),
                )
            text_parts = [
                str(item.get("text", "") or "")
                for item in payload
                if isinstance(item, dict)
            ]
            text_content = "\n".join(part for part in text_parts if part)
            if not text_content.strip():
                raise HTTPException(
                    status_code=400,
                    detail="responses.input text item arrays cannot be empty.",
                )
            normalized_messages.append(ChatMessage(role="user", content=text_content))
            return _normalize_request_messages(normalized_messages)

        if payload and all(
            isinstance(item, dict) and "role" in item for item in payload
        ):
            for item in payload:
                normalized_messages.append(
                    ChatMessage(
                        role=str(item.get("role", "user")),
                        content=item.get("content", ""),
                        thinking=item.get("thinking"),
                    )
                )
            return _normalize_request_messages(normalized_messages)

        normalized_messages.append(ChatMessage(role="user", content=payload))
        return _normalize_request_messages(normalized_messages)

    raise HTTPException(
        status_code=400,
        detail="responses.input must be a string, text item array, content array, or messages array.",
    )


def _chat_request_from_responses(req_body: ResponsesRequest) -> ChatCompletionRequest:
    return ChatCompletionRequest(
        model=req_body.model,
        messages=_build_messages_from_responses_input(req_body),
        stream=req_body.stream,
        temperature=req_body.temperature,
        top_p=req_body.top_p,
        max_tokens=req_body.max_output_tokens,
        metadata=req_body.metadata,
        user=req_body.user,
        tools=req_body.tools,
        tool_choice=req_body.tool_choice,
        conversation_id=None,
    )


def _normalize_anthropic_content_blocks(
    content: Any, *, param_prefix: str
) -> List[Dict[str, Any]]:
    if isinstance(content, str):
        text = str(content)
        if not text.strip():
            raise HTTPException(
                status_code=400, detail=f"{param_prefix} cannot be empty."
            )
        return [{"type": "text", "text": text}]
    if not isinstance(content, list) or not content:
        raise HTTPException(
            status_code=400,
            detail=f"{param_prefix} must be a non-empty string or content block array.",
        )

    normalized: List[Dict[str, Any]] = []
    for idx, block in enumerate(content):
        if hasattr(block, "model_dump"):
            block = block.model_dump()
        elif hasattr(block, "dict"):
            block = block.dict()
        if not isinstance(block, dict):
            raise HTTPException(
                status_code=400, detail=f"{param_prefix}[{idx}] must be an object."
            )
        block_type = str(block.get("type", "") or "").strip().lower()
        if block_type == "text":
            text = str(block.get("text", "") or "")
            if not text.strip():
                raise HTTPException(
                    status_code=400,
                    detail=f"{param_prefix}[{idx}].text cannot be empty.",
                )
            normalized.append({"type": "text", "text": text})
            continue
        if block_type == "image":
            source = block.get("source")
            if not isinstance(source, dict):
                raise HTTPException(
                    status_code=400,
                    detail=f"{param_prefix}[{idx}].source must be an object.",
                )
            source_type = str(source.get("type", "") or "").strip().lower()
            if source_type == "url":
                url = str(source.get("url", "") or "").strip()
                _validate_media_url(url, param=f"{param_prefix}[{idx}].source.url")
                normalized.append({"type": "image_url", "image_url": {"url": url}})
                continue
            if source_type == "base64":
                media_type = (
                    str(source.get("media_type", "") or "image/png").strip().lower()
                )
                data = str(source.get("data", "") or "").strip()
                if not data:
                    raise HTTPException(
                        status_code=400,
                        detail=f"{param_prefix}[{idx}].source.data cannot be empty.",
                    )
                normalized.append(
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:{media_type};base64,{data}"},
                    }
                )
                continue
            raise HTTPException(
                status_code=400,
                detail=f"{param_prefix}[{idx}].source.type '{source_type}' is not supported.",
            )
        raise HTTPException(
            status_code=400,
            detail=f"{param_prefix}[{idx}].type '{block_type}' is not supported.",
        )
    return normalized


def _chat_request_from_anthropic(
    req_body: AnthropicMessagesRequest,
) -> ChatCompletionRequest:
    if not req_body.messages:
        raise HTTPException(
            status_code=400, detail="messages must contain at least one message."
        )

    normalized_messages: List[ChatMessage] = []
    if req_body.system is not None:
        system_blocks = _normalize_anthropic_content_blocks(
            req_body.system, param_prefix="system"
        )
        normalized_messages.append(ChatMessage(role="system", content=system_blocks))

    for idx, message in enumerate(req_body.messages):
        normalized_messages.append(
            ChatMessage(
                role=message.role,
                content=_normalize_anthropic_content_blocks(
                    message.content, param_prefix=f"messages[{idx}].content"
                ),
            )
        )

    return ChatCompletionRequest(
        model=req_body.model,
        messages=_normalize_request_messages(normalized_messages),
        stream=req_body.stream,
        temperature=req_body.temperature,
        top_p=req_body.top_p,
        max_tokens=req_body.max_tokens,
        metadata=req_body.metadata,
        conversation_id=None,
    )


def _normalize_gemini_parts(parts: Any, *, param_prefix: str) -> List[Dict[str, Any]]:
    if not isinstance(parts, list) or not parts:
        raise HTTPException(
            status_code=400, detail=f"{param_prefix} must be a non-empty parts array."
        )

    normalized: List[Dict[str, Any]] = []
    for idx, part in enumerate(parts):
        if hasattr(part, "model_dump"):
            part = part.model_dump()
        elif hasattr(part, "dict"):
            part = part.dict()
        if not isinstance(part, dict):
            raise HTTPException(
                status_code=400, detail=f"{param_prefix}[{idx}] must be an object."
            )
        if "text" in part:
            text = str(part.get("text", "") or "")
            if not text.strip():
                raise HTTPException(
                    status_code=400,
                    detail=f"{param_prefix}[{idx}].text cannot be empty.",
                )
            normalized.append({"type": "text", "text": text})
            continue
        inline_data = part.get("inline_data") or part.get("inlineData")
        if isinstance(inline_data, dict):
            mime_type = (
                str(
                    inline_data.get("mime_type")
                    or inline_data.get("mimeType")
                    or "image/png"
                )
                .strip()
                .lower()
            )
            data = str(inline_data.get("data", "") or "").strip()
            if not data:
                raise HTTPException(
                    status_code=400,
                    detail=f"{param_prefix}[{idx}].inline_data.data cannot be empty.",
                )
            normalized.append(
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:{mime_type};base64,{data}"},
                }
            )
            continue
        file_data = part.get("file_data") or part.get("fileData")
        if isinstance(file_data, dict):
            url = str(
                file_data.get("file_uri") or file_data.get("fileUri") or ""
            ).strip()
            if not url:
                raise HTTPException(
                    status_code=400,
                    detail=f"{param_prefix}[{idx}].file_data.file_uri cannot be empty.",
                )
            _validate_media_url(url, param=f"{param_prefix}[{idx}].file_data.file_uri")
            normalized.append({"type": "image_url", "image_url": {"url": url}})
            continue
        raise HTTPException(
            status_code=400,
            detail=f"{param_prefix}[{idx}] only supports text, inline_data, or file_data parts.",
        )
    return normalized


def _chat_request_from_gemini(
    model: str, req_body: GeminiGenerateContentRequest, *, stream: bool
) -> ChatCompletionRequest:
    if req_body.safetySettings:
        raise HTTPException(
            status_code=400,
            detail="Gemini safetySettings are not supported by this compatibility endpoint.",
        )

    generation_config = req_body.generationConfig or {}
    normalized_messages: List[ChatMessage] = []

    if req_body.systemInstruction is not None:
        normalized_messages.append(
            ChatMessage(
                role="system",
                content=_normalize_gemini_parts(
                    req_body.systemInstruction.parts,
                    param_prefix="systemInstruction.parts",
                ),
            )
        )

    for idx, content in enumerate(req_body.contents):
        role = str(content.role or "user").strip().lower() or "user"
        mapped_role = "assistant" if role == "model" else role
        if mapped_role not in {"user", "assistant"}:
            raise HTTPException(
                status_code=400,
                detail=f"contents[{idx}].role '{role}' is not supported.",
            )
        normalized_messages.append(
            ChatMessage(
                role=mapped_role,
                content=_normalize_gemini_parts(
                    content.parts, param_prefix=f"contents[{idx}].parts"
                ),
            )
        )

    return ChatCompletionRequest(
        model=model,
        messages=_normalize_request_messages(normalized_messages),
        stream=stream,
        temperature=generation_config.get("temperature"),
        top_p=generation_config.get("topP", generation_config.get("top_p")),
        max_tokens=generation_config.get(
            "maxOutputTokens", generation_config.get("max_output_tokens")
        ),
        conversation_id=None,
    )


def _build_responses_output_text(output_text: str) -> List[Dict[str, Any]]:
    return [{"type": "output_text", "text": output_text, "annotations": []}]


def _build_responses_output_payload(output_text: str) -> List[Dict[str, Any]]:
    return [
        {
            "id": f"msg_{uuid.uuid4().hex}",
            "type": "message",
            "status": "completed",
            "role": "assistant",
            "content": _build_responses_output_text(output_text),
        }
    ]


def _build_responses_response(
    result: ChatCompletionResponse,
    *,
    output_text: str,
) -> Dict[str, Any]:
    return {
        "id": result.id,
        "object": "response",
        "created_at": result.created,
        "status": "completed",
        "model": result.model,
        "output": _build_responses_output_payload(output_text),
        "output_text": output_text,
        "usage": result.usage,
        "metadata": None,
        "error": None,
        "incomplete_details": None,
    }


def _build_responses_stream_event(event_type: str, data: Dict[str, Any]) -> str:
    return f"event: {event_type}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _extract_response_text(result: ChatCompletionResponse) -> str:
    assistant_message = result.choices[0].message if result.choices else None
    if assistant_message is None:
        return ""
    return content_to_text(assistant_message.content)


def _normalize_search_metadata(search_metadata: Any) -> dict[str, list[str]]:
    metadata = search_metadata if isinstance(search_metadata, dict) else {}
    queries = [
        str(item).strip() for item in metadata.get("queries", []) if str(item).strip()
    ]
    sources = [
        str(item).strip() for item in metadata.get("sources", []) if str(item).strip()
    ]
    return {"queries": queries, "sources": sources}


def _build_anthropic_citations(
    search_metadata: Any, *, cited_text: str
) -> list[dict[str, Any]]:
    normalized = _normalize_search_metadata(search_metadata)
    citations = []
    for source in normalized["sources"]:
        citations.append(
            {
                "type": "web_search_result_location",
                "url": source,
                "title": source,
                "cited_text": cited_text,
            }
        )
    return citations


def _build_gemini_grounding_metadata(search_metadata: Any) -> dict[str, Any] | None:
    normalized = _normalize_search_metadata(search_metadata)
    if not normalized["queries"] and not normalized["sources"]:
        return None
    metadata: dict[str, Any] = {}
    if normalized["queries"]:
        metadata["webSearchQueries"] = normalized["queries"]
    if normalized["sources"]:
        metadata["groundingChunks"] = [
            {"web": {"uri": source, "title": source}}
            for source in normalized["sources"]
        ]
    return metadata


def _extract_stream_search_metadata(payload: dict[str, Any]) -> dict[str, Any] | None:
    if str(payload.get("type", "") or "") != "search_metadata":
        return None
    searches = payload.get("searches")
    if not isinstance(searches, dict):
        return None
    return searches


def _build_gemini_stream_chunk(
    *,
    model: str,
    text: str,
    finish_reason: str | None,
    grounding_metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    candidate: dict[str, Any] = {
        "content": {
            "role": "model",
            "parts": [{"text": text}],
        },
        "finishReason": finish_reason,
        "index": 0,
    }
    if grounding_metadata:
        candidate["groundingMetadata"] = grounding_metadata
    return {
        "candidates": [candidate],
        "modelVersion": model,
    }


def _build_anthropic_response(result: ChatCompletionResponse) -> Dict[str, Any]:
    output_text = _extract_response_text(result)
    usage = result.usage or {}
    text_block: dict[str, Any] = {"type": "text", "text": output_text}
    citations = _build_anthropic_citations(
        result.search_metadata, cited_text=output_text
    )
    if citations:
        text_block["citations"] = citations
    return {
        "id": result.id,
        "type": "message",
        "role": "assistant",
        "model": result.model,
        "content": [text_block],
        "stop_reason": "end_turn",
        "stop_sequence": None,
        "usage": {
            "input_tokens": int(usage.get("prompt_tokens") or 0),
            "output_tokens": int(usage.get("completion_tokens") or 0),
        },
    }


def _build_gemini_response(result: ChatCompletionResponse) -> Dict[str, Any]:
    output_text = _extract_response_text(result)
    usage = result.usage or {}
    candidate: dict[str, Any] = {
        "content": {"role": "model", "parts": [{"text": output_text}]},
        "finishReason": "STOP",
        "index": 0,
    }
    grounding_metadata = _build_gemini_grounding_metadata(result.search_metadata)
    if grounding_metadata:
        candidate["groundingMetadata"] = grounding_metadata
    return {
        "candidates": [candidate],
        "usageMetadata": {
            "promptTokenCount": int(usage.get("prompt_tokens") or 0),
            "candidatesTokenCount": int(usage.get("completion_tokens") or 0),
            "totalTokenCount": int(usage.get("total_tokens") or 0),
        },
        "modelVersion": result.model,
    }


def _build_anthropic_stream_event(event_type: str, data: Dict[str, Any]) -> str:
    return f"event: {event_type}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _build_anthropic_error_response(
    status_code: int, message: str, *, error_type: str = "invalid_request_error"
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={
            "type": "error",
            "error": {
                "type": error_type,
                "message": str(message),
            },
        },
    )


def _build_gemini_error_response(
    status_code: int, message: str, *, error_status: str = "INVALID_ARGUMENT"
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={
            "error": {
                "code": status_code,
                "message": str(message),
                "status": error_status,
            }
        },
    )


def _wrap_chat_stream_as_anthropic_stream(
    chat_stream: StreamingResponse,
    *,
    model: str,
) -> StreamingResponse:
    message_id = f"msg_{uuid.uuid4().hex}"

    async def event_generator():
        yield _build_anthropic_stream_event(
            "message_start",
            {
                "type": "message_start",
                "message": {
                    "id": message_id,
                    "type": "message",
                    "role": "assistant",
                    "model": model,
                    "content": [],
                    "stop_reason": None,
                    "stop_sequence": None,
                    "usage": {"input_tokens": 0, "output_tokens": 0},
                },
            },
        )
        yield _build_anthropic_stream_event(
            "content_block_start",
            {
                "type": "content_block_start",
                "index": 0,
                "content_block": {"type": "text", "text": ""},
            },
        )

        aggregate_text = ""
        async for chunk in chat_stream.body_iterator:
            text_chunk = (
                chunk.decode("utf-8") if isinstance(chunk, bytes) else str(chunk)
            )
            for frame in text_chunk.split("\n\n"):
                frame = frame.strip()
                if not frame.startswith("data:"):
                    continue
                payload_text = frame[5:].strip()
                if payload_text == "[DONE]":
                    continue
                try:
                    payload = json.loads(payload_text)
                except json.JSONDecodeError:
                    continue
                search_metadata = _extract_stream_search_metadata(payload)
                if search_metadata is not None:
                    citations = _build_anthropic_citations(
                        search_metadata,
                        cited_text=aggregate_text,
                    )
                    for citation in citations:
                        yield _build_anthropic_stream_event(
                            "content_block_delta",
                            {
                                "type": "content_block_delta",
                                "index": 0,
                                "delta": {
                                    "type": "citations_delta",
                                    "citation": citation,
                                },
                            },
                        )
                    continue
                choices = payload.get("choices") or []
                if not choices:
                    continue
                delta = choices[0].get("delta") or {}
                content_delta = str(delta.get("content", "") or "")
                if not content_delta:
                    continue
                aggregate_text += content_delta
                yield _build_anthropic_stream_event(
                    "content_block_delta",
                    {
                        "type": "content_block_delta",
                        "index": 0,
                        "delta": {"type": "text_delta", "text": content_delta},
                    },
                )

        yield _build_anthropic_stream_event(
            "content_block_stop",
            {"type": "content_block_stop", "index": 0},
        )
        yield _build_anthropic_stream_event(
            "message_delta",
            {
                "type": "message_delta",
                "delta": {"stop_reason": "end_turn", "stop_sequence": None},
                "usage": {"output_tokens": 0},
            },
        )
        yield _build_anthropic_stream_event(
            "message_stop",
            {"type": "message_stop"},
        )

    headers = dict(chat_stream.headers)
    headers["Cache-Control"] = "no-cache"
    headers["Connection"] = "keep-alive"
    headers["X-Accel-Buffering"] = "no"
    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers=headers,
    )


def _wrap_chat_stream_as_gemini_stream(
    chat_stream: StreamingResponse,
    *,
    model: str,
) -> StreamingResponse:
    async def event_generator():
        aggregate_text = ""
        grounding_metadata: dict[str, Any] | None = None
        async for chunk in chat_stream.body_iterator:
            text_chunk = (
                chunk.decode("utf-8") if isinstance(chunk, bytes) else str(chunk)
            )
            for frame in text_chunk.split("\n\n"):
                frame = frame.strip()
                if not frame.startswith("data:"):
                    continue
                payload_text = frame[5:].strip()
                if payload_text == "[DONE]":
                    continue
                try:
                    payload = json.loads(payload_text)
                except json.JSONDecodeError:
                    continue
                search_metadata = _extract_stream_search_metadata(payload)
                if search_metadata is not None:
                    grounding_metadata = _build_gemini_grounding_metadata(
                        search_metadata
                    )
                    if grounding_metadata:
                        yield (
                            json.dumps(
                                _build_gemini_stream_chunk(
                                    model=model,
                                    text="",
                                    finish_reason=None,
                                    grounding_metadata=grounding_metadata,
                                ),
                                ensure_ascii=False,
                            )
                            + "\n"
                        )
                    continue
                choices = payload.get("choices") or []
                if not choices:
                    continue
                choice = choices[0] or {}
                delta = choice.get("delta") or {}
                content_delta = str(delta.get("content", "") or "")
                finish_reason = choice.get("finish_reason")
                if content_delta:
                    aggregate_text += content_delta
                    yield (
                        json.dumps(
                            _build_gemini_stream_chunk(
                                model=model,
                                text=content_delta,
                                finish_reason=None,
                                grounding_metadata=grounding_metadata,
                            ),
                            ensure_ascii=False,
                        )
                        + "\n"
                    )
                elif finish_reason:
                    yield (
                        json.dumps(
                            _build_gemini_stream_chunk(
                                model=model,
                                text="",
                                finish_reason=str(finish_reason).upper(),
                                grounding_metadata=grounding_metadata,
                            ),
                            ensure_ascii=False,
                        )
                        + "\n"
                    )

    headers = dict(chat_stream.headers)
    headers.pop("content-type", None)
    headers["Cache-Control"] = "no-cache"
    headers["Connection"] = "keep-alive"
    headers["X-Accel-Buffering"] = "no"
    return StreamingResponse(
        event_generator(),
        media_type="application/x-ndjson",
        headers=headers,
    )


def _wrap_chat_stream_as_responses_stream(
    chat_stream: StreamingResponse,
    *,
    model: str,
) -> StreamingResponse:
    response_id = f"resp_{uuid.uuid4().hex}"

    async def event_generator():
        created_at = int(time.time())
        aggregate_text = ""
        yield _build_responses_stream_event(
            "response.created",
            {
                "type": "response.created",
                "response": {
                    "id": response_id,
                    "object": "response",
                    "created_at": created_at,
                    "status": "in_progress",
                    "model": model,
                    "output": [],
                    "output_text": "",
                },
            },
        )
        yield _build_responses_stream_event(
            "response.output_item.added",
            {
                "type": "response.output_item.added",
                "output_index": 0,
                "item": {
                    "id": f"msg_{uuid.uuid4().hex}",
                    "type": "message",
                    "status": "in_progress",
                    "role": "assistant",
                    "content": [],
                },
            },
        )

        async for chunk in chat_stream.body_iterator:
            text_chunk = (
                chunk.decode("utf-8") if isinstance(chunk, bytes) else str(chunk)
            )
            for frame in text_chunk.split("\n\n"):
                frame = frame.strip()
                if not frame.startswith("data:"):
                    continue
                payload_text = frame[5:].strip()
                if payload_text == "[DONE]":
                    continue
                try:
                    payload = json.loads(payload_text)
                except json.JSONDecodeError:
                    continue
                choices = payload.get("choices") or []
                if not choices:
                    continue
                delta = choices[0].get("delta") or {}
                content_delta = str(delta.get("content", "") or "")
                if content_delta:
                    aggregate_text += content_delta
                    yield _build_responses_stream_event(
                        "response.output_text.delta",
                        {
                            "type": "response.output_text.delta",
                            "delta": content_delta,
                            "output_index": 0,
                            "content_index": 0,
                        },
                    )

        yield _build_responses_stream_event(
            "response.output_text.done",
            {
                "type": "response.output_text.done",
                "text": aggregate_text,
                "output_index": 0,
                "content_index": 0,
            },
        )
        yield _build_responses_stream_event(
            "response.completed",
            {
                "type": "response.completed",
                "response": {
                    "id": response_id,
                    "object": "response",
                    "created_at": created_at,
                    "status": "completed",
                    "model": model,
                    "output": _build_responses_output_payload(aggregate_text),
                    "output_text": aggregate_text,
                },
            },
        )
        yield "data: [DONE]\n\n"

    headers = dict(chat_stream.headers)
    headers["Cache-Control"] = "no-cache"
    headers["Connection"] = "keep-alive"
    headers["X-Accel-Buffering"] = "no"
    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers=headers,
    )


def _build_stream_chunk(
    response_id: str,
    model: str,
    *,
    content: str = "",
    thinking: str = "",
    role: str = "",
    finish_reason=None,
) -> str:
    delta: Dict[str, Any] = {}
    if role:
        delta["role"] = role
    if content:
        delta["content"] = content
    if thinking:
        delta["reasoning_content"] = thinking

    payload = {
        "id": response_id,
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": model,
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}],
    }
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _build_local_ui_chunk(
    response_id: str,
    model: str,
    event_type: str,
    **payload_fields: Any,
) -> str:
    payload: Dict[str, Any] = {
        "id": response_id,
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": model,
        "choices": [{"index": 0, "delta": {}, "finish_reason": None}],
        "type": event_type,
    }
    payload.update(payload_fields)
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _format_search_results_md(search_data: dict[str, Any]) -> str:
    """将搜索数据格式化为 Markdown 引用块，以便标准客户端显示。"""
    lines = []
    queries = search_data.get("queries", [])
    if queries:
        lines.append(f"> 🔍 **已搜索:** {', '.join(queries)}")

    sources = search_data.get("sources", [])
    if sources:
        lines.append("> 🌐 **来源:**")
        for i, src in enumerate(sources[:5], 1):  # 最多显示5个来源，避免刷屏
            title = src.get("title") or src.get("url") or "未知来源"
            url = src.get("url")
            if url:
                lines.append(f"> {i}. [{title}]({url})")
            else:
                lines.append(f"> {i}. {title}")

    if lines:
        return "\n".join(lines) + "\n\n"
    return ""


def _extract_search_metadata_from_text(text: str) -> dict[str, list[Any]]:
    content = str(text or "")
    urls = re.findall(r"https?://[^\]\)\s]+", content)
    seen = set()
    sources = []
    for url in urls:
        clean_url = url.rstrip(".,;:")
        if clean_url in seen:
            continue
        seen.add(clean_url)
        sources.append({"title": clean_url, "url": clean_url})
    return {"queries": [], "sources": sources}


def _normalize_stream_item(item: Any) -> dict[str, Any]:
    if isinstance(item, str):
        return {"type": "content", "text": item}

    if isinstance(item, dict):
        item_type = str(item.get("type", "") or "").lower()
        if item_type == "content":
            return {"type": "content", "text": str(item.get("text", "") or "")}
        if item_type == "search":
            payload = item.get("data")
            return {
                "type": "search",
                "data": payload if isinstance(payload, dict) else {},
            }
        if item_type == "thinking":
            return {"type": "thinking", "text": str(item.get("text", "") or "")}
        if item_type == "final_content":
            return {
                "type": "final_content",
                "text": str(item.get("text", "") or ""),
                "source_type": str(item.get("source_type", "") or ""),
                "source_length": item.get("source_length"),
            }

    return {"type": "unknown"}


def _iter_stream_items(
    first_item: Any, stream_gen: Iterable[Any]
) -> Generator[Any, None, None]:
    if first_item is not None:
        yield first_item
    for item in stream_gen:
        yield item


def _compute_missing_suffix(current_text: str, final_text: str) -> str:
    if not final_text:
        return ""
    if not current_text:
        return final_text
    if final_text.startswith(current_text):
        return final_text[len(current_text) :]
    return ""


def _select_best_final_reply(
    streamed_text: str,
    final_text: str,
    final_source_type: str,
) -> tuple[str, str]:
    streamed = streamed_text or ""
    final = final_text or ""
    streamed_stripped = streamed.strip()
    final_stripped = final.strip()
    source = (final_source_type or "").strip().lower()

    if not final_stripped:
        return streamed, "streamed_only"
    if not streamed_stripped:
        return final, "final_only"
    if final.startswith(streamed):
        return final, "final_extends_streamed"
    if streamed.startswith(final):
        if source == "title" or len(final_stripped) <= max(
            32, int(len(streamed_stripped) * 0.35)
        ):
            return streamed, "streamed_beats_short_final"
        return final, "final_prefix_of_streamed"

    # Diverged content: usually prefer richer non-title final content.
    if source == "title" and len(final_stripped) < max(
        48, int(len(streamed_stripped) * 0.6)
    ):
        return streamed, "streamed_beats_title"
    if len(final_stripped) >= max(48, int(len(streamed_stripped) * 0.6)):
        return final, "final_diverged_preferred"
    return streamed, "streamed_diverged_preferred"


def _normalize_overlap_text(text: str) -> str:
    normalized = str(text or "").strip().lower()
    if not normalized:
        return ""
    normalized = re.sub(r"```.*?```", " ", normalized, flags=re.DOTALL)
    normalized = re.sub(r"\s+", "", normalized)
    return normalized


def _trim_redundant_thinking(
    thinking_text: str, final_reply: str
) -> tuple[str, str, float]:
    thinking = str(thinking_text or "").strip()
    final = str(final_reply or "").strip()
    if not thinking or not final:
        return thinking, "missing_text", 0.0

    normalized_thinking = _normalize_overlap_text(thinking)
    normalized_final = _normalize_overlap_text(final)
    if not normalized_thinking or not normalized_final:
        return thinking, "missing_normalized_text", 0.0

    overlap_ratio = SequenceMatcher(None, normalized_thinking, normalized_final).ratio()
    if normalized_thinking == normalized_final:
        return "", "identical", overlap_ratio

    if thinking.endswith(final):
        prefix = thinking[: -len(final)].rstrip()
        if len(_normalize_overlap_text(prefix)) >= 10:
            return prefix, "suffix_trimmed", overlap_ratio
        return "", "suffix_cleared", overlap_ratio

    if overlap_ratio >= 0.92 and (
        normalized_thinking in normalized_final
        or normalized_final in normalized_thinking
    ):
        return "", "high_overlap_cleared", overlap_ratio

    return thinking, "kept", overlap_ratio


def _build_thinking_replacement(
    streamed_content_text: str,
    thinking_text: str,
    final_reply: str,
    final_source_type: str,
) -> dict[str, Any] | None:
    source = str(final_source_type or "").strip().lower()

    # Relax constraint: Allow replacement for more source types to fix Sonnet thinking leakage
    # But still require minimal validation for non-inference sources
    if source not in ("agent-inference", "text", "markdown-chat", ""):
        # Only skip for clearly non-thinking source types
        return None

    normalized_final = _normalize_overlap_text(final_reply)
    normalized_streamed = _normalize_overlap_text(streamed_content_text)

    # Require at least some thinking content to process
    if not _normalize_overlap_text(thinking_text):
        return None

    # For non-agent-inference sources, be more conservative but still check for obvious duplication
    if source != "agent-inference":
        # Only process if there's clear overlap or thinking is redundant
        if not normalized_final:
            return None

        # Check for obvious duplication (thinking appears in final reply)
        if thinking_text.strip() in final_reply or final_reply in thinking_text:
            # Clear case of duplication - trim it
            replacement, decision, overlap_ratio = _trim_redundant_thinking(
                thinking_text, final_reply
            )
            if replacement != str(thinking_text or "").strip():
                logger.debug(
                    "Non-agent-inference thinking replacement applied",
                    extra={
                        "request_info": {
                            "event": "thinking_replacement_non_agent",
                            "source_type": source,
                            "overlap_ratio": round(overlap_ratio, 4),
                            "decision": f"{decision}_non_agent_inference",
                        }
                    },
                )
                return {
                    "thinking": replacement,
                    "decision": f"{decision}_non_agent_inference",
                    "overlap_ratio": round(overlap_ratio, 4),
                    "source_type": source,
                }
        return None

    # Original agent-inference logic continues
    if not normalized_final:
        return None

    # 只在几乎没有真实正文增量时做裁决，避免误伤复杂推理场景。
    if normalized_streamed and len(normalized_streamed) >= max(
        10, int(len(normalized_final) * 0.35)
    ):
        return None

    replacement, decision, overlap_ratio = _trim_redundant_thinking(
        thinking_text, final_reply
    )
    if replacement == str(thinking_text or "").strip():
        return None

    return {
        "thinking": replacement,
        "decision": decision,
        "overlap_ratio": round(overlap_ratio, 4),
        "source_type": source,
    }


def _contains_recall_intent(text: str) -> bool:
    lowered = text.lower()
    for keyword in RECALL_INTENT_KEYWORDS:
        if keyword.isascii():
            if keyword.lower() in lowered:
                return True
            continue
        if keyword in text:
            return True
    return False


def _extract_recall_query(text: str) -> str:
    cleaned = text
    for keyword in RECALL_INTENT_KEYWORDS:
        if keyword.isascii():
            cleaned = re.sub(
                rf"\b{re.escape(keyword)}\b", " ", cleaned, flags=re.IGNORECASE
            )
        else:
            cleaned = cleaned.replace(keyword, " ")
    cleaned = re.sub(r"[\s，。！？、,.!?;:：]+", " ", cleaned).strip()
    return cleaned or text.strip()


def _prepare_messages(
    req_body: ChatCompletionRequest,
) -> Tuple[Any, List[Tuple[str, Any, str]], str]:
    system_messages = []
    dialogue_messages = []

    for msg in req_body.messages:
        if msg.role in {"system", "developer"}:
            system_text = content_to_text(msg.content).strip()
            if system_text:
                system_messages.append(system_text)
            continue
        dialogue_messages.append((msg.role, msg.content, msg.thinking or ""))

    if not dialogue_messages:
        raise HTTPException(
            status_code=400,
            detail="The messages list must contain at least one user message.",
        )

    last_role, user_prompt, _ = dialogue_messages[-1]
    raw_user_prompt = content_to_text(user_prompt)
    history_messages = dialogue_messages[:-1]

    if last_role != "user":
        raise HTTPException(
            status_code=400, detail="The last message must be from role 'user'."
        )
    if not content_to_text(user_prompt).strip():
        raise HTTPException(
            status_code=400, detail="The last user message cannot be empty."
        )

    if system_messages:
        merged_system_prompt = "\n".join(system_messages)
        user_prompt = f"[System Instructions: {merged_system_prompt}]\n\n{content_to_text(user_prompt)}"

    return user_prompt, history_messages, raw_user_prompt


def _prepare_messages_lite(req_body: ChatCompletionRequest) -> Any:
    """Lite 模式：只提取最后一条 user 消息，支持 system 指令合并"""
    system_messages = []
    user_prompt: Any = ""

    for msg in req_body.messages:
        if msg.role in {"system", "developer"}:
            system_text = content_to_text(msg.content).strip()
            if system_text:
                system_messages.append(system_text)
        elif msg.role == "user":
            user_prompt = msg.content

    if not content_to_text(user_prompt).strip():
        raise HTTPException(
            status_code=400,
            detail="The messages list must contain at least one user message.",
        )

    if system_messages:
        user_prompt = f"[System Instructions: {' '.join(system_messages)}]\n\n{content_to_text(user_prompt)}"

    return user_prompt


def _create_lite_stream_generator(
    request: Request,
    req_body: ChatCompletionRequest,
    response_id: str,
    model_name: str,
    first_item: Any,
    stream_gen: Iterable[Any],
    account_id: str = "",
) -> Generator[str, None, None]:
    """Lite 模式流式生成器：只输出 content，忽略 thinking 和 search"""
    streamed_content_accumulator = ""
    authoritative_final_content = ""
    authoritative_final_source_type = ""
    assistant_started = False

    try:
        for raw_item in _iter_stream_items(first_item, stream_gen):
            item = _normalize_stream_item(raw_item)
            item_type = item.get("type")

            if item_type == "final_content":
                final_text = str(item.get("text", "") or "").strip()
                if final_text:
                    authoritative_final_content = final_text
                    authoritative_final_source_type = str(
                        item.get("source_type", "") or ""
                    )
                continue

            # Lite 模式忽略 thinking 和 search
            if item_type in ("thinking", "search"):
                continue

            if item_type != "content":
                continue

            chunk_text = item.get("text", "")
            if not chunk_text:
                continue

            streamed_content_accumulator += chunk_text
            if not assistant_started:
                assistant_started = True
                yield _build_stream_chunk(
                    response_id,
                    model_name,
                    role="assistant",
                    content=chunk_text,
                )
            else:
                yield _build_stream_chunk(response_id, model_name, content=chunk_text)
    except asyncio.CancelledError:
        logger.info(
            "Lite streaming cancelled by client",
            extra={"request_info": {"event": "lite_stream_cancelled"}},
        )
        raise
    except BaseException as exc:
        if _is_client_disconnect_error(exc):
            logger.info(
                "Lite streaming connection closed by client",
                extra={"request_info": {"event": "lite_stream_client_disconnected"}},
            )
            return
        logger.error(
            "Lite streaming interrupted",
            exc_info=True,
            extra={"request_info": {"event": "lite_stream_interrupted"}},
        )
        error_hint = "\n\n[上游连接中断，请稍后重试。]"
        streamed_content_accumulator += error_hint
        if not assistant_started:
            assistant_started = True
            yield _build_stream_chunk(
                response_id,
                model_name,
                role="assistant",
                content=error_hint,
            )
        else:
            yield _build_stream_chunk(response_id, model_name, content=error_hint)
    finally:
        # 选择最佳最终回复
        final_reply, _ = _select_best_final_reply(
            streamed_content_accumulator,
            authoritative_final_content,
            authoritative_final_source_type,
        )

        # 发送缺失的后缀（如果有）
        missing_suffix = _compute_missing_suffix(
            streamed_content_accumulator, final_reply
        )
        if missing_suffix:
            if not assistant_started:
                assistant_started = True
                yield _build_stream_chunk(
                    response_id,
                    model_name,
                    role="assistant",
                    content=missing_suffix,
                )
            else:
                yield _build_stream_chunk(
                    response_id, model_name, content=missing_suffix
                )
            streamed_content_accumulator += missing_suffix
        elif final_reply != streamed_content_accumulator:
            # 处理分叉内容（使用最终内容）
            if not streamed_content_accumulator and final_reply:
                if not assistant_started:
                    assistant_started = True
                    yield _build_stream_chunk(
                        response_id,
                        model_name,
                        role="assistant",
                        content=final_reply,
                    )
                else:
                    yield _build_stream_chunk(
                        response_id, model_name, content=final_reply
                    )
                streamed_content_accumulator = final_reply

        usage_payload = _build_usage_payload(
            req_body=req_body,
            output_text=final_reply or streamed_content_accumulator,
        )
        _record_usage_event(
            request,
            request_id=response_id,
            request_type="chat.completions",
            stream=True,
            model=model_name,
            usage=usage_payload,
            account_id=account_id,
        )
        yield _build_stream_chunk(response_id, model_name, finish_reason="stop")
        yield "data: [DONE]\n\n"


def _create_standard_stream_generator(
    request: Request,
    req_body: ChatCompletionRequest,
    response_id: str,
    model_name: str,
    first_item: Any,
    stream_gen: Iterable[Any],
    account_id: str = "",
) -> Generator[str, None, None]:
    """
    Standard 模式流式生成器：使用前端定义的 SSE 事件类型

    前端协议：
    - thinking_chunk: 流式思考片段
    - thinking_replace: 完整思考替换
    - search_metadata: 搜索结果
    - choices[0].delta.content: 正文内容
    """
    streamed_content_accumulator = ""
    streamed_thinking_accumulator = ""
    collected_search_sources = []
    collected_search_queries = []
    authoritative_final_content = ""
    authoritative_final_source_type = ""
    assistant_started = False

    try:
        for raw_item in _iter_stream_items(first_item, stream_gen):
            item = _normalize_stream_item(raw_item)
            item_type = item.get("type")

            if item_type == "final_content":
                final_text = str(item.get("text", "") or "").strip()
                if final_text:
                    authoritative_final_content = final_text
                    authoritative_final_source_type = str(
                        item.get("source_type", "") or ""
                    )
                continue

            # Standard 模式：处理 thinking（使用前端定义的 thinking_chunk 类型）
            if item_type == "thinking":
                thinking_text = item.get("text", "")
                if thinking_text:
                    streamed_thinking_accumulator += thinking_text
                    # 输出 thinking_chunk 事件
                    yield f"data: {json.dumps({'type': 'thinking_chunk', 'text': thinking_text}, ensure_ascii=False)}\n\n"
                continue

            # Standard 模式：处理 search（收集起来，最后输出）
            if item_type == "search":
                search_data = item.get("data", {})
                if isinstance(search_data, dict):
                    # 提取 queries 和 sources
                    queries = search_data.get("queries", [])
                    sources = search_data.get("sources", [])

                    if queries:
                        collected_search_queries.extend(queries)
                    if sources:
                        collected_search_sources.extend(sources)
                continue

            if item_type != "content":
                continue

            chunk_text = item.get("text", "")
            if not chunk_text:
                continue

            streamed_content_accumulator += chunk_text

            # 输出标准 OpenAI 格式的 delta
            if not assistant_started:
                assistant_started = True
                yield _build_stream_chunk(
                    response_id,
                    model_name,
                    role="assistant",
                    content=chunk_text,
                )
            else:
                yield _build_stream_chunk(response_id, model_name, content=chunk_text)
    except asyncio.CancelledError:
        logger.info(
            "Standard streaming cancelled by client",
            extra={"request_info": {"event": "standard_stream_cancelled"}},
        )
        raise
    except BaseException as exc:
        if _is_client_disconnect_error(exc):
            logger.info(
                "Standard streaming connection closed by client",
                extra={
                    "request_info": {"event": "standard_stream_client_disconnected"}
                },
            )
            return
        logger.error(
            "Standard streaming interrupted",
            exc_info=True,
            extra={"request_info": {"event": "standard_stream_interrupted"}},
        )
        error_hint = "\n\n[上游连接中断，请稍后重试。]"
        streamed_content_accumulator += error_hint
        if not assistant_started:
            assistant_started = True
            yield _build_stream_chunk(
                response_id,
                model_name,
                role="assistant",
                content=error_hint,
            )
        else:
            yield _build_stream_chunk(response_id, model_name, content=error_hint)
    finally:
        # 选择最佳最终回复
        final_reply, _ = _select_best_final_reply(
            streamed_content_accumulator,
            authoritative_final_content,
            authoritative_final_source_type,
        )

        # 发送缺失的后缀（如果有）
        missing_suffix = _compute_missing_suffix(
            streamed_content_accumulator, final_reply
        )
        if missing_suffix:
            if not assistant_started:
                assistant_started = True
                yield _build_stream_chunk(
                    response_id,
                    model_name,
                    role="assistant",
                    content=missing_suffix,
                )
            else:
                yield _build_stream_chunk(
                    response_id, model_name, content=missing_suffix
                )
            streamed_content_accumulator += missing_suffix
        elif final_reply != streamed_content_accumulator:
            # 处理分叉内容（使用最终内容）
            if not streamed_content_accumulator and final_reply:
                if not assistant_started:
                    assistant_started = True
                    yield _build_stream_chunk(
                        response_id,
                        model_name,
                        role="assistant",
                        content=final_reply,
                    )
                else:
                    yield _build_stream_chunk(
                        response_id, model_name, content=final_reply
                    )
                streamed_content_accumulator = final_reply

        # 输出搜索结果（使用前端定义的 search_metadata 类型）
        if collected_search_sources or collected_search_queries:
            search_metadata = {
                "type": "search_metadata",
                "searches": {
                    "queries": collected_search_queries,
                    "sources": collected_search_sources,
                },
            }
            yield f"data: {json.dumps(search_metadata, ensure_ascii=False)}\n\n"

        usage_payload = _build_usage_payload(
            req_body=req_body,
            output_text=final_reply or streamed_content_accumulator,
        )
        _record_usage_event(
            request,
            request_id=response_id,
            request_type="chat.completions",
            stream=True,
            model=model_name,
            usage=usage_payload,
            account_id=account_id,
        )
        yield _build_stream_chunk(response_id, model_name, finish_reason="stop")
        yield "data: [DONE]\n\n"


def _persist_round(
    manager,
    background_tasks: BackgroundTasks,
    conversation_id: str,
    user_prompt: str,
    assistant_reply: str,
    assistant_thinking: str = "",
) -> None:
    """
    持久化一轮对话并触发异步预压缩。

    预压缩逻辑：
    - 当 round >= WINDOW_ROUNDS//2 时，提前压缩滑出窗口的轮次
    - 使用 BackgroundTasks 确保不阻塞当前对话
    """
    round_index = manager.persist_round(
        conversation_id,
        user_prompt,
        assistant_reply,
        assistant_thinking=assistant_thinking,
    )

    # 异步预压缩：当窗口快满时提前压缩
    WINDOW_ROUNDS = 8  # 与 conversation.py 保持一致
    PRECOMPRESS_THRESHOLD = WINDOW_ROUNDS // 2  # 在第 4 轮时开始预压缩

    if round_index >= PRECOMPRESS_THRESHOLD:
        # 计算需要压缩的轮次（滑出窗口的轮次）
        round_to_compress = round_index - WINDOW_ROUNDS + 1
        if round_to_compress >= 0:
            background_tasks.add_task(
                compress_sliding_window_round,
                manager=manager,
                conversation_id=conversation_id,
                round_number=round_to_compress,
            )
            logger.info(
                "Triggered async pre-compression",
                extra={
                    "request_info": {
                        "event": "async_precompress_triggered",
                        "conversation_id": conversation_id,
                        "current_round": round_index,
                        "compress_round": round_to_compress,
                    }
                },
            )

    # 保留原有的压缩逻辑作为兜底
    background_tasks.add_task(
        compress_round_if_needed,
        manager=manager,
        conversation_id=conversation_id,
    )


def _persist_history_messages(
    manager, conversation_id: str, history_messages: List[Tuple[str, Any, str]]
) -> None:
    for role, content, thinking in history_messages:
        manager.add_message(conversation_id, role, content, thinking)


def _build_usage_payload(
    *,
    req_body: ChatCompletionRequest,
    output_text: str,
) -> dict[str, int]:
    prompt_text = "\n".join(
        content_to_text(message.content) for message in req_body.messages
    )
    prompt_tokens = estimate_token_count(prompt_text)
    completion_tokens = estimate_token_count(output_text)
    return {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": prompt_tokens + completion_tokens,
    }


def _record_usage_event(
    request: Request,
    *,
    request_id: str,
    request_type: str,
    stream: bool,
    model: str,
    usage: dict[str, int],
    account_id: str = "",
    conversation_id: str = "",
) -> None:
    usage_store = getattr(request.app.state, "usage_store", None)
    if usage_store is None:
        return
    try:
        usage_store.record_event(
            request_id=request_id,
            request_type=request_type,
            stream=stream,
            model=model,
            prompt_tokens=int(usage.get("prompt_tokens") or 0),
            completion_tokens=int(usage.get("completion_tokens") or 0),
            total_tokens=int(usage.get("total_tokens") or 0),
            account_id=account_id,
            conversation_id=conversation_id,
        )
    except Exception:
        logger.warning(
            "Failed to record usage event",
            exc_info=True,
            extra={
                "request_info": {
                    "event": "usage_record_failed",
                    "request_id": request_id,
                    "request_type": request_type,
                    "model": model,
                    "account_id": account_id,
                }
            },
        )


def _is_client_disconnect_error(exc: BaseException) -> bool:
    if isinstance(exc, asyncio.CancelledError):
        return True
    if isinstance(exc, (BrokenPipeError, ConnectionResetError)):
        return True
    if isinstance(exc, OSError):
        return exc.errno in {32, 54, 104, 10053, 10054}
    return False


async def _handle_lite_request(
    request: Request,
    req_body: ChatCompletionRequest,
    response: Response,
) -> JSONResponse | StreamingResponse:
    """处理 Lite 模式请求（无记忆，单轮问答）"""
    pool = request.app.state.account_pool

    # 提取用户问题
    user_prompt = _prepare_messages_lite(req_body)

    # 验证模型
    if not is_supported_model(req_body.model):
        available_models = list_available_models()
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported model '{req_body.model}'. Available models: {', '.join(available_models)}",
        )

    response_id = f"chatcmpl-{uuid.uuid4().hex}"
    max_retries = _max_attempts_for_request(req_body, len(pool.clients))

    for attempt in range(1, max_retries + 1):
        client = None
        try:
            client = pool.get_client()

            # 构建 Lite transcript（无历史记忆）
            search_enabled = _search_enabled(req_body)
            transcript = build_lite_transcript(
                user_prompt,
                req_body.model,
                search_enabled=search_enabled,
            )

            # 调用 Notion API（不使用 thread_id）
            stream_gen = client.stream_response(transcript, thread_id=None)
            first_item = next(stream_gen, None)

            if first_item is None:
                raise NotionUpstreamError(
                    "Notion upstream returned empty content.", retriable=True
                )

            # 流式响应
            if req_body.stream:
                stream_headers = {
                    "Cache-Control": "no-cache",
                    "Connection": "keep-alive",
                    "X-Accel-Buffering": "no",
                }
                return StreamingResponse(
                    _create_lite_stream_generator(
                        request,
                        req_body,
                        response_id,
                        req_body.model,
                        first_item,
                        stream_gen,
                        account_id=str(getattr(client, "account_id", "") or ""),
                    ),
                    media_type="text/event-stream",
                    headers=stream_headers,
                )

            # 非流式响应
            content_parts: list[str] = []
            authoritative_final_content = ""
            authoritative_final_source_type = ""

            for raw_item in _iter_stream_items(first_item, stream_gen):
                item = _normalize_stream_item(raw_item)
                item_type = item.get("type")

                if item_type == "final_content":
                    final_text = str(item.get("text", "") or "").strip()
                    if final_text:
                        authoritative_final_content = final_text
                        authoritative_final_source_type = str(
                            item.get("source_type", "") or ""
                        )
                    continue

                # Lite 模式忽略 thinking 和 search
                if item_type in ("thinking", "search"):
                    continue

                if item_type != "content":
                    continue

                chunk_text = item.get("text", "")
                if chunk_text:
                    content_parts.append(chunk_text)

            full_text, _ = _select_best_final_reply(
                "".join(content_parts),
                authoritative_final_content,
                authoritative_final_source_type,
            )

            if not full_text.strip():
                raise NotionUpstreamError(
                    "Notion upstream returned empty content.", retriable=True
                )

            response_text = (
                full_text if full_text.strip() else "[assistant_no_visible_content]"
            )
            usage_payload = _build_usage_payload(
                req_body=req_body,
                output_text=response_text,
            )

            if _looks_truncated(response_text) and attempt < max_retries:
                logger.warning(
                    "Lite mode response looks truncated, retrying",
                    extra={
                        "request_info": {
                            "event": "lite_truncation_retry",
                            "attempt": attempt,
                            "max_retries": max_retries,
                            "model": req_body.model,
                        }
                    },
                )
                user_prompt = _build_retry_prompt(user_prompt, response_text)
                continue

            _record_usage_event(
                request,
                request_id=response_id,
                request_type="chat.completions",
                stream=False,
                model=req_body.model,
                usage=usage_payload,
                account_id=str(getattr(client, "account_id", "") or ""),
            )
            return ChatCompletionResponse(
                id=response_id,
                model=req_body.model,
                choices=[
                    ChatMessageResponseChoice(
                        message=ChatMessage(role="assistant", content=response_text)
                    )
                ],
                usage=usage_payload,
            )

        except NotionUpstreamError as exc:
            if client is not None:
                pool.mark_upstream_error(client, exc.status_code, exc.response_excerpt)
            logger.warning(
                "Lite mode: Notion upstream failed",
                extra={
                    "request_info": {
                        "event": "lite_notion_upstream_failed",
                        "attempt": attempt,
                        "max_retries": max_retries,
                        "status_code": exc.status_code,
                        "retriable": exc.retriable,
                        "response_excerpt": exc.response_excerpt,
                    }
                },
            )
            if attempt == max_retries or not exc.retriable:
                raise HTTPException(status_code=503, detail=str(exc)) from exc
        except RuntimeError as exc:
            logger.error(
                "Lite mode: No available client in account pool",
                extra={
                    "request_info": {
                        "event": "lite_account_pool_unavailable",
                        "detail": str(exc),
                    }
                },
            )
            return JSONResponse(
                status_code=503,
                content={
                    "error": {
                        "message": str(exc),
                        "type": "rate_limit_error",
                        "code": "account_pool_cooling",
                    }
                },
            )
        except HTTPException:
            raise
        except Exception:
            if client is not None:
                pool.mark_failed(client)
            logger.error(
                "Lite mode: Unhandled error",
                exc_info=True,
                extra={
                    "request_info": {
                        "event": "lite_unhandled_exception",
                        "attempt": attempt,
                    }
                },
            )
            if attempt == max_retries:
                raise HTTPException(
                    status_code=500,
                    detail="Unexpected internal error while generating completion.",
                )

    raise HTTPException(
        status_code=503, detail="Service unavailable: all upstream retries exhausted."
    )


async def _handle_standard_request(
    request: Request,
    req_body: ChatCompletionRequest,
    response: Response,
) -> JSONResponse | StreamingResponse:
    """
    处理 Standard 模式请求（完整上下文，支持 thinking 和搜索）

    类似 Lite 模式，但：
    1. 发送完整 messages 历史
    2. 保留 thinking 输出
    3. 保留搜索结果输出
    """
    pool = request.app.state.account_pool

    # 验证模型
    if not is_supported_model(req_body.model):
        available_models = list_available_models()
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported model '{req_body.model}'. Available models: {', '.join(available_models)}",
        )

    response_id = f"chatcmpl-{uuid.uuid4().hex}"
    max_retries = _max_attempts_for_request(req_body, len(pool.clients))

    for attempt in range(1, max_retries + 1):
        client = None
        try:
            client = pool.get_client()

            # 构建 Standard transcript（完整上下文）
            # 从 client 提取账号信息
            account = {
                "user_id": client.user_id,
                "space_id": client.space_id,
            }
            messages = [msg.model_dump() for msg in req_body.messages]
            search_enabled = _search_enabled(req_body)
            transcript = build_standard_transcript(
                messages,
                req_body.model,
                account,
                search_enabled=search_enabled,
            )

            # 调用 Notion API（不使用 thread_id，让 Notion 自动处理）
            stream_gen = client.stream_response(transcript, thread_id=None)
            first_item = next(stream_gen, None)

            if first_item is None:
                raise NotionUpstreamError(
                    "Notion upstream returned empty content.", retriable=True
                )

            # 流式响应
            if req_body.stream:
                stream_headers = {
                    "Cache-Control": "no-cache",
                    "Connection": "keep-alive",
                    "X-Accel-Buffering": "no",
                }
                return StreamingResponse(
                    _create_standard_stream_generator(
                        request,
                        req_body,
                        response_id,
                        req_body.model,
                        first_item,
                        stream_gen,
                        account_id=str(getattr(client, "account_id", "") or ""),
                    ),
                    media_type="text/event-stream",
                    headers=stream_headers,
                )

            # 非流式响应
            content_parts: list[str] = []
            thinking_parts: list[str] = []
            search_results: list[dict] = []
            authoritative_final_content = ""
            authoritative_final_source_type = ""

            for raw_item in _iter_stream_items(first_item, stream_gen):
                item = _normalize_stream_item(raw_item)
                item_type = item.get("type")

                if item_type == "final_content":
                    final_text = str(item.get("text", "") or "").strip()
                    if final_text:
                        authoritative_final_content = final_text
                        authoritative_final_source_type = str(
                            item.get("source_type", "") or ""
                        )
                    continue

                # Standard 模式：处理 thinking
                if item_type == "thinking":
                    thinking_text = item.get("text", "")
                    if thinking_text:
                        thinking_parts.append(thinking_text)
                    continue

                # Standard 模式：处理 search
                if item_type == "search":
                    search_data = item.get("data", {})
                    if search_data:
                        search_results.append(search_data)
                    continue

                if item_type != "content":
                    continue

                chunk_text = item.get("text", "")
                if chunk_text:
                    content_parts.append(chunk_text)

            full_text, _ = _select_best_final_reply(
                "".join(content_parts),
                authoritative_final_content,
                authoritative_final_source_type,
            )

            if not full_text.strip():
                raise NotionUpstreamError(
                    "Notion upstream returned empty content.", retriable=True
                )

            response_text = (
                full_text if full_text.strip() else "[assistant_no_visible_content]"
            )
            usage_payload = _build_usage_payload(
                req_body=req_body,
                output_text=response_text,
            )

            if _looks_truncated(response_text) and attempt < max_retries:
                logger.warning(
                    "Standard mode response looks truncated, retrying",
                    extra={
                        "request_info": {
                            "event": "standard_truncation_retry",
                            "attempt": attempt,
                            "max_retries": max_retries,
                            "model": req_body.model,
                        }
                    },
                )
                messages = list(messages)
                messages.append(
                    {
                        "role": "user",
                        "content": _build_retry_prompt(raw_user_prompt, response_text),
                    }
                )
                continue

            # 构建响应
            response_message = ChatMessage(role="assistant", content=response_text)

            # 如果有 thinking，添加到扩展字段（前端会读取）
            if thinking_parts:
                response_message.thinking = "".join(thinking_parts)

            # 构建响应
            response_obj = ChatCompletionResponse(
                id=response_id,
                model=req_body.model,
                choices=[ChatMessageResponseChoice(message=response_message)],
            )

            response_obj.usage = usage_payload

            # 如果有搜索结果，添加到扩展字段（前端会读取）
            if search_results:
                # 提取 queries 和 sources
                all_queries = []
                all_sources = []
                for result in search_results:
                    if isinstance(result, dict):
                        all_queries.extend(result.get("queries", []))
                        all_sources.extend(result.get("sources", []))

                if all_queries or all_sources:
                    # 添加到自定义字段
                    response_obj.search_metadata = {
                        "queries": all_queries,
                        "sources": all_sources,
                    }
            elif is_search_model(req_body.model):
                fallback_search = _extract_search_metadata_from_text(response_text)
                if fallback_search["sources"]:
                    response_obj.search_metadata = fallback_search

            _record_usage_event(
                request,
                request_id=response_id,
                request_type="chat.completions",
                stream=False,
                model=req_body.model,
                usage=usage_payload,
                account_id=str(getattr(client, "account_id", "") or ""),
            )
            return response_obj

        except NotionUpstreamError as exc:
            if client is not None:
                pool.mark_upstream_error(client, exc.status_code, exc.response_excerpt)
            logger.warning(
                "Standard mode: Notion upstream failed",
                extra={
                    "request_info": {
                        "event": "standard_notion_upstream_failed",
                        "attempt": attempt,
                        "max_retries": max_retries,
                        "status_code": exc.status_code,
                        "retriable": exc.retriable,
                        "response_excerpt": exc.response_excerpt,
                    }
                },
            )
            if (
                attempt == max_retries
                and _search_enabled(req_body)
                and req_body.model.endswith("-search")
            ):
                fallback_model = req_body.model[: -len("-search")]
                logger.warning(
                    "Standard mode search model exhausted retries, falling back to base model",
                    extra={
                        "request_info": {
                            "event": "standard_search_fallback_to_base",
                            "attempt": attempt,
                            "search_model": req_body.model,
                            "base_model": fallback_model,
                        }
                    },
                )
                req_body.model = fallback_model
                continue
            if attempt == max_retries or not exc.retriable:
                raise HTTPException(status_code=503, detail=str(exc)) from exc
        except RuntimeError as exc:
            logger.error(
                "Standard mode: No available client in account pool",
                extra={
                    "request_info": {
                        "event": "standard_account_pool_unavailable",
                        "detail": str(exc),
                    }
                },
            )
            return JSONResponse(
                status_code=503,
                content={
                    "error": {
                        "message": str(exc),
                        "type": "rate_limit_error",
                        "code": "account_pool_cooling",
                    }
                },
            )
        except HTTPException:
            raise
        except Exception:
            if client is not None:
                pool.mark_failed(client)
            logger.error(
                "Standard mode: Unhandled error",
                exc_info=True,
                extra={
                    "request_info": {
                        "event": "standard_unhandled_exception",
                        "attempt": attempt,
                    }
                },
            )
            if attempt == max_retries:
                raise HTTPException(
                    status_code=500,
                    detail="Unexpected internal error while generating completion.",
                )

    raise HTTPException(
        status_code=503, detail="Service unavailable: all upstream retries exhausted."
    )


@router.post("/media/upload", tags=["chat"], response_model=MediaUploadResponse)
async def upload_media(
    request: Request,
    payload: MediaUploadRequest,
    x_chat_session: str | None = Header(default=None, alias="X-Chat-Session"),
):
    _ensure_chat_access(request, x_chat_session)
    saved = _store_media_data(
        request,
        data_url=str(payload.data_url or ""),
        file_name=payload.file_name,
    )
    return MediaUploadResponse(
        url=saved["url"],
        media_id=saved["media_id"],
        file_name=saved["file_name"],
        mime_type=saved["mime_type"],
        size_bytes=int(saved["size_bytes"]),
    )


@router.get("/media/{media_id}", tags=["chat"], name="get_media_file")
async def get_media_file(media_id: str):
    safe_name = Path(str(media_id or "")).name
    if safe_name != media_id or safe_name in {"", ".", ".."}:
        raise HTTPException(status_code=404, detail="Media not found.")
    media_path = _resolve_media_storage_dir() / safe_name
    if not media_path.exists() or not media_path.is_file():
        raise HTTPException(status_code=404, detail="Media not found.")
    mime_type, _ = mimetypes.guess_type(media_path.name)
    return FileResponse(media_path, media_type=mime_type or "application/octet-stream")


@router.post("/chat/completions", tags=["chat"])
async def create_chat_completion(
    request: Request,
    req_body: ChatCompletionRequest,
    background_tasks: BackgroundTasks,
    response: Response,
    x_chat_session: str | None = Header(default=None, alias="X-Chat-Session"),
):
    """
    创建聊天请求，严格兼容 OpenAI API。

    速率限制：
    - Lite 模式：30/分钟（适合单轮问答）
    - Standard 模式：25/分钟（完整上下文，支持 thinking 和搜索）
    - Heavy 模式：20/分钟（包含会话管理）
    """
    from app.config import is_standard_mode

    _ensure_chat_access(request, x_chat_session)
    req_body.messages = _normalize_request_messages(req_body.messages)
    _validate_sampling_params(req_body)
    _validate_tooling_params(req_body)

    # Lite 模式：单轮问答，无记忆
    if is_lite_mode():
        return await _handle_lite_request(request, req_body, response)

    # Standard 模式：完整上下文，支持 thinking 和搜索
    if is_standard_mode():
        return await _handle_standard_request(request, req_body, response)

    # Heavy 模式：完整会话管理
    pool = request.app.state.account_pool
    manager = request.app.state.conversation_manager

    user_prompt, history_messages, raw_user_prompt = _prepare_messages(req_body)
    recall_query = (
        _extract_recall_query(raw_user_prompt)
        if _contains_recall_intent(raw_user_prompt)
        else None
    )

    if not is_supported_model(req_body.model):
        available_models = list_available_models()
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported model '{req_body.model}'. Available models: {', '.join(available_models)}",
        )

    conversation_id = (
        req_body.conversation_id.strip() if req_body.conversation_id else ""
    )
    restore_history = False
    if not conversation_id:
        conversation_id = manager.new_conversation()
        restore_history = True
    elif not manager.conversation_exists(conversation_id):
        logger.warning(
            "Conversation id not found, creating a fresh conversation",
            extra={
                "request_info": {
                    "event": "conversation_id_not_found",
                    "provided_conversation_id": conversation_id,
                }
            },
        )
        conversation_id = manager.new_conversation()
        restore_history = True

    # 关键修复：总是持久化客户端发送的历史消息，避免上下文丢失
    # 即使 conversation_id 已存在，也需要同步客户端发送的完整历史
    if history_messages:
        # 检查是否需要持久化（避免重复）
        with manager._get_conn() as conn:
            existing_count = manager._count_messages(conn, conversation_id)
            history_count = len(history_messages)

            # 只有当客户端发送的历史消息多于数据库中的消息时才持久化
            # 这样可以：
            # 1. 避免重复持久化相同的历史
            # 2. 确保客户端发送的完整历史被保存
            # 3. 解决"滑动窗口缺失 AI 回复"的 bug
            if history_count > existing_count:
                _persist_history_messages(manager, conversation_id, history_messages)
                restored_user_count = sum(
                    1 for role, _, _ in history_messages if role == "user"
                )
                restored_assistant_count = sum(
                    1 for role, _, _ in history_messages if role == "assistant"
                )

                logger.info(
                    "Restored history into conversation",
                    extra={
                        "request_info": {
                            "event": "conversation_history_restored",
                            "conversation_id": conversation_id,
                            "restore_history_flag": restore_history,
                            "existing_count": existing_count,
                            "history_count": history_count,
                            "restored_total": len(history_messages),
                            "restored_user_count": restored_user_count,
                            "restored_assistant_count": restored_assistant_count,
                        }
                    },
                )

    response_id = f"chatcmpl-{uuid.uuid4().hex}"
    max_retries = _max_attempts_for_request(req_body, len(pool.clients))

    for attempt in range(1, max_retries + 1):
        client = None
        try:
            client = pool.get_client()
            transcript_payload = manager.get_transcript_payload(
                notion_client=client,
                conversation_id=conversation_id,
                new_prompt=user_prompt,
                model_name=req_body.model,
                recall_query=recall_query,
                search_enabled=_search_enabled(req_body),
            )
            transcript = transcript_payload["transcript"]
            memory_degraded = bool(transcript_payload.get("memory_degraded"))
            memory_headers = {"X-Memory-Status": "degraded"} if memory_degraded else {}

            # 获取或创建 thread_id 以保持对话上下文
            thread_id = manager.get_conversation_thread_id(conversation_id)

            stream_gen = client.stream_response(transcript, thread_id=thread_id)
            first_item = next(stream_gen, None)

            # 保存 thread_id（如果是新对话）
            if not thread_id and hasattr(client, "current_thread_id"):
                manager.set_conversation_thread_id(
                    conversation_id, client.current_thread_id
                )

            if first_item is None:
                raise NotionUpstreamError(
                    "Notion upstream returned empty content.", retriable=True
                )

            def openai_stream_generator() -> Generator[str, None, None]:
                streamed_content_accumulator = ""
                thinking_accumulator = ""
                authoritative_final_content = ""
                authoritative_final_source_type = ""
                assistant_started = False
                pending_search_md = ""
                client_type = request.headers.get("X-Client-Type", "").lower()
                recent_thinking_buffer: list[str] = []

                try:
                    for raw_item in _iter_stream_items(first_item, stream_gen):
                        item = _normalize_stream_item(raw_item)
                        item_type = item.get("type")

                        if item_type == "search":
                            search_data = item.get("data")
                            if isinstance(search_data, dict) and search_data:
                                pending_search_md += _format_search_results_md(
                                    search_data
                                )
                                if client_type == "web":
                                    yield _build_local_ui_chunk(
                                        response_id,
                                        req_body.model,
                                        "search_metadata",
                                        searches=search_data,
                                    )
                            continue

                        if item_type == "final_content":
                            final_text = str(item.get("text", "") or "").strip()
                            if final_text:
                                authoritative_final_content = final_text
                                authoritative_final_source_type = str(
                                    item.get("source_type", "") or ""
                                )
                            continue

                        if item_type == "thinking":
                            thinking_text = item.get("text", "")
                            if thinking_text:
                                thinking_accumulator += thinking_text
                                # Track recent thinking for overlap detection
                                recent_thinking_buffer.append(thinking_text)
                                # Keep buffer manageable (max 40 recent chunks)
                                if len(recent_thinking_buffer) > 40:
                                    recent_thinking_buffer.pop(0)

                                if not assistant_started:
                                    assistant_started = True
                                    yield _build_stream_chunk(
                                        response_id,
                                        req_body.model,
                                        role="assistant",
                                        thinking=thinking_text,
                                    )
                                else:
                                    yield _build_stream_chunk(
                                        response_id,
                                        req_body.model,
                                        thinking=thinking_text,
                                    )
                            continue

                        if item_type != "content":
                            continue

                        chunk_text = item.get("text", "")
                        if not chunk_text and not pending_search_md:
                            continue

                        # Check if content overlaps with recent thinking (prevents thinking leakage)
                        if recent_thinking_buffer and chunk_text.strip():
                            combined_recent_thinking = "".join(recent_thinking_buffer)
                            chunk_normalized = chunk_text.strip()

                            # Use normalized text without spaces for robust comparison
                            combined_norm = re.sub(r"\s+", "", combined_recent_thinking)
                            chunk_norm = re.sub(r"\s+", "", chunk_normalized)

                            # Check for significant overlap - skip duplicate content
                            # We only skip if a sufficiently long chunk matches to avoid swallowing short common characters.
                            if (
                                chunk_norm
                                and len(chunk_norm) > 3
                                and (
                                    chunk_norm in combined_norm
                                    or (
                                        len(chunk_norm) > 10
                                        and chunk_norm[:10] in combined_norm
                                    )
                                )
                            ):
                                # Skip this chunk as it's likely duplicated thinking content
                                logger.debug(
                                    "Skipping duplicate content chunk that overlaps with thinking",
                                    extra={
                                        "request_info": {
                                            "event": "content_overlap_with_thinking",
                                            "chunk_length": len(chunk_text),
                                            "overlap_detected": True,
                                        }
                                    },
                                )
                                continue

                        # 在第一个正文内容发出前，把积攒的搜索信息拼上去
                        if pending_search_md and client_type != "web":
                            chunk_text = pending_search_md + chunk_text

                        if pending_search_md:
                            pending_search_md = ""

                        streamed_content_accumulator += chunk_text
                        if not assistant_started:
                            assistant_started = True
                            yield _build_stream_chunk(
                                response_id,
                                req_body.model,
                                role="assistant",
                                content=chunk_text,
                            )
                        else:
                            yield _build_stream_chunk(
                                response_id, req_body.model, content=chunk_text
                            )
                except asyncio.CancelledError:
                    logger.info(
                        "Streaming response cancelled by downstream client",
                        extra={
                            "request_info": {
                                "event": "stream_cancelled_by_client",
                                "conversation_id": conversation_id,
                                "attempt": attempt,
                            }
                        },
                    )
                    raise
                except BaseException as exc:
                    if _is_client_disconnect_error(exc):
                        logger.info(
                            "Streaming connection closed by downstream client",
                            extra={
                                "request_info": {
                                    "event": "stream_client_disconnected",
                                    "conversation_id": conversation_id,
                                    "attempt": attempt,
                                }
                            },
                        )
                        return
                    if isinstance(exc, NotionUpstreamError) and client is not None:
                        pool.mark_upstream_error(
                            client, exc.status_code, exc.response_excerpt
                        )
                    log_method = (
                        logger.warning
                        if isinstance(exc, NotionUpstreamError)
                        else logger.error
                    )
                    log_method(
                        "Streaming response interrupted",
                        exc_info=True,
                        extra={
                            "request_info": {
                                "event": "stream_interrupted",
                                "conversation_id": conversation_id,
                                "attempt": attempt,
                                "is_upstream_error": isinstance(
                                    exc, NotionUpstreamError
                                ),
                            }
                        },
                    )
                    error_hint = "\n\n[上游连接中断，请稍后重试。]"
                    streamed_content_accumulator += error_hint
                    if not assistant_started:
                        assistant_started = True
                        yield _build_stream_chunk(
                            response_id,
                            req_body.model,
                            role="assistant",
                            content=error_hint,
                        )
                    else:
                        yield _build_stream_chunk(
                            response_id, req_body.model, content=error_hint
                        )
                finally:
                    final_reply, reply_decision = _select_best_final_reply(
                        streamed_content_accumulator,
                        authoritative_final_content,
                        authoritative_final_source_type,
                    )

                    missing_suffix = _compute_missing_suffix(
                        streamed_content_accumulator, final_reply
                    )
                    if missing_suffix:
                        suffix_to_emit = missing_suffix
                        if (
                            pending_search_md
                            and client_type != "web"
                            and not streamed_content_accumulator
                        ):
                            suffix_to_emit = pending_search_md + suffix_to_emit
                            pending_search_md = ""
                        if not assistant_started:
                            assistant_started = True
                            yield _build_stream_chunk(
                                response_id,
                                req_body.model,
                                role="assistant",
                                content=suffix_to_emit,
                            )
                        else:
                            yield _build_stream_chunk(
                                response_id, req_body.model, content=suffix_to_emit
                            )
                        streamed_content_accumulator += suffix_to_emit
                    elif final_reply != streamed_content_accumulator:
                        # Diverged bodies cannot be safely "patched" in plain OpenAI deltas.
                        # Web client supports replace event to keep rendered body aligned with persisted final reply.
                        if client_type == "web":
                            yield _build_local_ui_chunk(
                                response_id,
                                req_body.model,
                                "content_replace",
                                content=final_reply,
                                source_type=authoritative_final_source_type,
                                decision=reply_decision,
                            )
                            streamed_content_accumulator = final_reply
                        elif not streamed_content_accumulator and final_reply:
                            # Non-web fallback when nothing has been shown yet.
                            emit_text = final_reply
                            if pending_search_md and client_type != "web":
                                emit_text = pending_search_md + emit_text
                                pending_search_md = ""
                            if not assistant_started:
                                assistant_started = True
                                yield _build_stream_chunk(
                                    response_id,
                                    req_body.model,
                                    role="assistant",
                                    content=emit_text,
                                )
                            else:
                                yield _build_stream_chunk(
                                    response_id, req_body.model, content=emit_text
                                )
                            streamed_content_accumulator = final_reply

                    thinking_replacement = _build_thinking_replacement(
                        streamed_content_accumulator,
                        thinking_accumulator,
                        final_reply,
                        authoritative_final_source_type,
                    )
                    if client_type == "web" and thinking_replacement is not None:
                        yield _build_local_ui_chunk(
                            response_id,
                            req_body.model,
                            "thinking_replace",
                            thinking=thinking_replacement["thinking"],
                            decision=thinking_replacement["decision"],
                            overlap_ratio=thinking_replacement["overlap_ratio"],
                            source_type=thinking_replacement["source_type"],
                            reply_decision=reply_decision,
                        )

                    persisted_thinking = (
                        str(thinking_replacement["thinking"])
                        if thinking_replacement is not None
                        else thinking_accumulator
                    )
                    if final_reply.strip() or persisted_thinking.strip():
                        try:
                            _persist_round(
                                manager,
                                background_tasks,
                                conversation_id,
                                user_prompt,
                                final_reply,
                                persisted_thinking,
                            )
                        except Exception:
                            logger.error(
                                "Failed to persist conversation round",
                                exc_info=True,
                                extra={
                                    "request_info": {
                                        "event": "conversation_persist_failed",
                                        "conversation_id": conversation_id,
                                    }
                                },
                            )
                    usage_payload = _build_usage_payload(
                        req_body=req_body,
                        output_text=final_reply or streamed_content_accumulator,
                    )
                    _record_usage_event(
                        request,
                        request_id=response_id,
                        request_type="chat.completions",
                        stream=True,
                        model=req_body.model,
                        usage=usage_payload,
                        account_id=str(getattr(client, "account_id", "") or ""),
                        conversation_id=conversation_id,
                    )
                    yield _build_stream_chunk(
                        response_id, req_body.model, finish_reason="stop"
                    )
                    yield "data: [DONE]\n\n"

            if req_body.stream:
                stream_headers = {
                    "Cache-Control": "no-cache",
                    "Connection": "keep-alive",
                    "X-Accel-Buffering": "no",
                    "X-Conversation-Id": conversation_id,
                    **memory_headers,
                }
                return StreamingResponse(
                    openai_stream_generator(),
                    media_type="text/event-stream",
                    headers=stream_headers,
                )

            content_parts: list[str] = []
            thinking_parts: list[str] = []
            authoritative_final_content = ""
            authoritative_final_source_type = ""
            for raw_item in _iter_stream_items(first_item, stream_gen):
                item = _normalize_stream_item(raw_item)
                item_type = item.get("type")
                if item_type == "final_content":
                    final_text = str(item.get("text", "") or "").strip()
                    if final_text:
                        authoritative_final_content = final_text
                        authoritative_final_source_type = str(
                            item.get("source_type", "") or ""
                        )
                    continue
                if item_type == "thinking":
                    thinking_text = str(item.get("text", "") or "")
                    if thinking_text:
                        thinking_parts.append(thinking_text)
                    continue
                if item_type != "content":
                    continue
                chunk_text = item.get("text", "")
                if chunk_text:
                    content_parts.append(chunk_text)

            full_text, _ = _select_best_final_reply(
                "".join(content_parts),
                authoritative_final_content,
                authoritative_final_source_type,
            )
            merged_thinking = "".join(thinking_parts).strip()
            if not full_text.strip() and not merged_thinking:
                raise NotionUpstreamError(
                    "Notion upstream returned empty content.", retriable=True
                )

            _persist_round(
                manager,
                background_tasks,
                conversation_id,
                user_prompt,
                full_text,
                merged_thinking,
            )
            response.headers["X-Conversation-Id"] = conversation_id
            if memory_degraded:
                response.headers["X-Memory-Status"] = "degraded"

            response_text = (
                full_text if full_text.strip() else "[assistant_no_visible_content]"
            )
            usage_payload = _build_usage_payload(
                req_body=req_body,
                output_text=response_text,
            )

            if _looks_truncated(response_text) and attempt < max_retries:
                logger.warning(
                    "Heavy mode response looks truncated, retrying",
                    extra={
                        "request_info": {
                            "event": "heavy_truncation_retry",
                            "attempt": attempt,
                            "max_retries": max_retries,
                            "conversation_id": conversation_id,
                            "model": req_body.model,
                        }
                    },
                )
                user_prompt = _build_retry_prompt(user_prompt, response_text)
                continue

            _record_usage_event(
                request,
                request_id=response_id,
                request_type="chat.completions",
                stream=False,
                model=req_body.model,
                usage=usage_payload,
                account_id=str(getattr(client, "account_id", "") or ""),
                conversation_id=conversation_id,
            )
            return ChatCompletionResponse(
                id=response_id,
                model=req_body.model,
                choices=[
                    ChatMessageResponseChoice(
                        message=ChatMessage(role="assistant", content=response_text)
                    )
                ],
                usage=usage_payload,
            )
        except NotionUpstreamError as exc:
            if client is not None:
                pool.mark_upstream_error(client, exc.status_code, exc.response_excerpt)
            logger.warning(
                "Notion upstream failed",
                extra={
                    "request_info": {
                        "event": "notion_upstream_failed",
                        "attempt": attempt,
                        "max_retries": max_retries,
                        "conversation_id": conversation_id,
                        "status_code": exc.status_code,
                        "retriable": exc.retriable,
                        "response_excerpt": exc.response_excerpt,
                    }
                },
            )
            if attempt == max_retries or not exc.retriable:
                raise HTTPException(status_code=503, detail=str(exc)) from exc
        except RuntimeError as exc:
            logger.error(
                "No available client in account pool",
                extra={
                    "request_info": {
                        "event": "account_pool_unavailable",
                        "detail": str(exc),
                    }
                },
            )
            # 返回标准的 OpenAI 错误格式，让客户端（如 Cherry Studio）能直观显示报错
            return JSONResponse(
                status_code=503,
                content={
                    "error": {
                        "message": str(exc),
                        "type": "rate_limit_error",
                        "code": "account_pool_cooling",
                    }
                },
            )
        except HTTPException:
            raise
        except Exception:
            if client is not None:
                pool.mark_failed(client)
            logger.error(
                "Unhandled chat completion error",
                exc_info=True,
                extra={
                    "request_info": {
                        "event": "chat_completion_unhandled_exception",
                        "attempt": attempt,
                        "conversation_id": conversation_id,
                    }
                },
            )
            if attempt == max_retries:
                raise HTTPException(
                    status_code=500,
                    detail="Unexpected internal error while generating completion.",
                )


@router.post("/responses", tags=["chat"])
async def create_responses(
    request: Request,
    req_body: ResponsesRequest,
    background_tasks: BackgroundTasks,
    response: Response,
    x_chat_session: str | None = Header(default=None, alias="X-Chat-Session"),
):
    chat_req = _chat_request_from_responses(req_body)
    result = await create_chat_completion(
        request=request,
        req_body=chat_req,
        background_tasks=background_tasks,
        response=response,
        x_chat_session=x_chat_session,
    )

    if isinstance(result, StreamingResponse):
        response_id = (
            response.headers.get("X-Response-Id") or f"resp_{uuid.uuid4().hex}"
        )
        response.headers["X-Response-Id"] = response_id
        return _wrap_chat_stream_as_responses_stream(result, model=chat_req.model)

    if isinstance(result, ChatCompletionResponse):
        output_text = _extract_response_text(result)
        _record_usage_event(
            request,
            request_id=f"resp_{result.id}",
            request_type="responses",
            stream=False,
            model=result.model,
            usage=result.usage,
            conversation_id=response.headers.get("X-Conversation-Id") or "",
        )
        return _build_responses_response(result, output_text=output_text)

    return result

    raise HTTPException(
        status_code=503, detail="Service unavailable: all upstream retries exhausted."
    )


@router.post("/messages", tags=["chat"])
async def create_anthropic_messages(
    request: Request,
    req_body: AnthropicMessagesRequest,
    background_tasks: BackgroundTasks,
    response: Response,
    x_chat_session: str | None = Header(default=None, alias="X-Chat-Session"),
    anthropic_version: str | None = Header(default=None, alias="anthropic-version"),
):
    try:
        if anthropic_version is None:
            raise HTTPException(
                status_code=400,
                detail="anthropic-version header is required for Anthropic Messages compatibility.",
            )
        if anthropic_version != ANTHROPIC_VERSION_HEADER:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Unsupported anthropic-version '{anthropic_version}'. "
                    f"Expected '{ANTHROPIC_VERSION_HEADER}'."
                ),
            )
        chat_req = _chat_request_from_anthropic(req_body)
        result = await create_chat_completion(
            request=request,
            req_body=chat_req,
            background_tasks=background_tasks,
            response=response,
            x_chat_session=x_chat_session,
        )

        if isinstance(result, StreamingResponse):
            return _wrap_chat_stream_as_anthropic_stream(result, model=chat_req.model)

        if isinstance(result, ChatCompletionResponse):
            _record_usage_event(
                request,
                request_id=f"anthropic_{result.id}",
                request_type="anthropic.messages",
                stream=False,
                model=result.model,
                usage=result.usage,
                conversation_id=response.headers.get("X-Conversation-Id") or "",
            )
            return _build_anthropic_response(result)

        return result
    except HTTPException as exc:
        error_type = "invalid_request_error" if exc.status_code < 500 else "api_error"
        return _build_anthropic_error_response(
            exc.status_code, exc.detail, error_type=error_type
        )


@gemini_router.post("/models/{model}:generateContent", tags=["chat"])
async def generate_gemini_content(
    model: str,
    request: Request,
    req_body: GeminiGenerateContentRequest,
    background_tasks: BackgroundTasks,
    response: Response,
    x_chat_session: str | None = Header(default=None, alias="X-Chat-Session"),
):
    try:
        chat_req = _chat_request_from_gemini(model, req_body, stream=False)
        result = await create_chat_completion(
            request=request,
            req_body=chat_req,
            background_tasks=background_tasks,
            response=response,
            x_chat_session=x_chat_session,
        )

        if isinstance(result, ChatCompletionResponse):
            _record_usage_event(
                request,
                request_id=f"gemini_{result.id}",
                request_type="gemini.generateContent",
                stream=False,
                model=result.model,
                usage=result.usage,
                conversation_id=response.headers.get("X-Conversation-Id") or "",
            )
            return _build_gemini_response(result)

        return result
    except HTTPException as exc:
        error_status = "INVALID_ARGUMENT" if exc.status_code < 500 else "INTERNAL"
        return _build_gemini_error_response(
            exc.status_code, exc.detail, error_status=error_status
        )


@gemini_router.post("/models/{model}:streamGenerateContent", tags=["chat"])
async def stream_gemini_content(
    model: str,
    request: Request,
    req_body: GeminiGenerateContentRequest,
    background_tasks: BackgroundTasks,
    response: Response,
    x_chat_session: str | None = Header(default=None, alias="X-Chat-Session"),
):
    try:
        chat_req = _chat_request_from_gemini(model, req_body, stream=True)
        result = await create_chat_completion(
            request=request,
            req_body=chat_req,
            background_tasks=background_tasks,
            response=response,
            x_chat_session=x_chat_session,
        )

        if isinstance(result, StreamingResponse):
            return _wrap_chat_stream_as_gemini_stream(result, model=chat_req.model)

        if isinstance(result, ChatCompletionResponse):
            return JSONResponse(content=_build_gemini_response(result))

        return result
    except HTTPException as exc:
        error_status = "INVALID_ARGUMENT" if exc.status_code < 500 else "INTERNAL"
        return _build_gemini_error_response(
            exc.status_code, exc.detail, error_status=error_status
        )


@router.delete("/conversations/{conversation_id}", tags=["chat"])
async def delete_conversation(
    conversation_id: str,
    request: Request,
    x_chat_session: str | None = Header(default=None, alias="X-Chat-Session"),
):
    _ensure_chat_access(request, x_chat_session)
    manager = getattr(request.app.state, "conversation_manager", None)
    if manager is None:
        raise HTTPException(
            status_code=400,
            detail="Conversation management is only available in heavy mode.",
        )
    deleted = manager.delete_conversation(conversation_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Conversation not found.")
    return {"id": conversation_id, "deleted": True}
