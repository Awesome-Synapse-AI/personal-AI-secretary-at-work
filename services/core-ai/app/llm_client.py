import json
import time

import httpx
import structlog
from langsmith import traceable

from app.config import settings
from app.observability import record_llm_error, record_llm_timing

logger = structlog.get_logger("llm_client")


@traceable(name="call_llm_json", run_type="llm")
async def call_llm_json(system_prompt: str, user_message: str, max_tokens: int) -> dict | None:
    # Request JSON-formatted output to improve parsing reliability with Ollama.
    content, raw = await _call_llm(
        system_prompt, user_message, max_tokens, enforce_json=True, stream=False
    )
    if raw is not None:
        logger.info("llm_response_raw", raw=_truncate(json.dumps(raw, default=str), 600))
    payload = _parse_json_payload(content)
    if payload is not None:
        return payload

    if not content:
        return None

    repair_prompt = (
        "You fix model outputs into strict JSON only. "
        "Return JSON only, no code fences or extra text."
    )
    repair_message = (
        "Convert the following into valid JSON that matches the requested schema:\n"
        f"{content}"
    )
    repaired, repaired_raw = await _call_llm(
        f"{system_prompt} {repair_prompt}",
        repair_message,
        max_tokens=max_tokens,
    )
    if repaired_raw is not None:
        logger.info("llm_response_repaired_raw", raw=_truncate(json.dumps(repaired_raw, default=str), 600))
    return _parse_json_payload(repaired)


def _parse_json_payload(content: str | None) -> dict | None:
    if not content:
        return None
    stripped = content.strip()
    payload = _load_json(stripped)
    if payload is not None:
        return payload
    extracted = _extract_json_object(stripped)
    return _load_json(extracted) if extracted else None


def _load_json(text: str | None) -> dict | None:
    if not text:
        return None
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def _extract_json_object(text: str) -> str | None:
    start = text.find("{")
    if start == -1:
        return None
    depth = 0
    in_string = False
    escape = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escape:
                escape = False
                continue
            if char == "\\":
                escape = True
                continue
            if char == "\"":
                in_string = False
            continue
        if char == "\"":
            in_string = True
            continue
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start : index + 1]
    return None


@traceable(name="llm_http_call", run_type="llm")
async def _call_llm(
    system_prompt: str,
    user_message: str,
    max_tokens: int,
    enforce_json: bool = False,
    stream: bool | None = None,
) -> tuple[str | None, dict | None]:
    url = f"{settings.llm_base_url.rstrip('/')}{settings.llm_chat_path}"
    headers: dict[str, str] = {}
    if settings.llm_api_key:
        headers["Authorization"] = f"Bearer {settings.llm_api_key}"

    payload = {
        "model": settings.llm_model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ],
        "temperature": 0,
        "max_tokens": max_tokens,
    }
    if enforce_json:
        # OpenAI-compatible response_format plus Ollama-native format for stricter JSON.
        payload["response_format"] = {"type": "json_object"}
        payload["format"] = "json"
        payload["stream"] = False
    elif stream is not None:
        payload["stream"] = bool(stream)

    start = time.perf_counter()
    try:
        async with httpx.AsyncClient(timeout=settings.llm_timeout_seconds) as client:
            response = await client.post(url, json=payload, headers=headers)
            response.raise_for_status()
            data = response.json()
    except Exception as exc:
        record_llm_error(settings.llm_model, exc.__class__.__name__)
        logger.exception("llm_request_failed", error=str(exc))
        return None, None
    finally:
        duration = time.perf_counter() - start
        record_llm_timing(settings.llm_model, duration, bool(stream))

    choice = data.get("choices", [{}])[0]
    message = choice.get("message", {}) or {}

    # Ollama can return content in either `content` or `reasoning`; TGI-style uses `text`.
    raw_content = (
        message.get("content")
        or message.get("reasoning")
        or choice.get("text", "")
    )

    if not raw_content:
        extracted = _extract_json_object(json.dumps(choice))
        raw_content = extracted or ""

    cleaned = raw_content.strip() if raw_content else None
    logger.info(
        "llm_request_succeeded",
        model=settings.llm_model,
        streaming=bool(stream),
        duration_ms=round((time.perf_counter() - start) * 1000, 2),
    )
    return cleaned, data


def _truncate(text: str, length: int) -> str:
    return text if len(text) <= length else f"{text[:length]}..."
