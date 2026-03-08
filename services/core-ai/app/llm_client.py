import json
import time

import httpx
import structlog
from langsmith import traceable

from app.config import settings
from app.observability import record_llm_error, record_llm_timing

logger = structlog.get_logger("llm_client")
MAX_OUTPUT_TOKENS = 1024


@traceable(name="call_llm_text", run_type="llm")
async def call_llm_text(system_prompt: str, user_message: str, max_tokens: int) -> str | None:
    content, raw = await _call_llm(
        system_prompt, user_message, max_tokens, enforce_json=False, stream=False
    )
    if raw is not None:
        logger.info("llm_response_raw", raw=_truncate(json.dumps(raw, default=str), 600))
    return content


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

    # If generation is truncated, retry once with a larger token budget and tighter instruction.
    if _is_truncated(raw):
        retry_max_tokens = min(MAX_OUTPUT_TOKENS, max(max_tokens * 2, 128))
        retry_prompt = (
            f"{system_prompt} "
            "Return compact JSON only. Do not include reasoning or explanatory text."
        )
        retried_content, retried_raw = await _call_llm(
            retry_prompt,
            user_message,
            retry_max_tokens,
            enforce_json=True,
            stream=False,
        )
        if retried_raw is not None:
            logger.info("llm_response_retry_raw", raw=_truncate(json.dumps(retried_raw, default=str), 600))
        retried_payload = _parse_json_payload(retried_content)
        if retried_payload is not None:
            return retried_payload
        if retried_content:
            content = retried_content

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

    # Cap generation to avoid runaway outputs while honoring caller intent.
    effective_max_tokens = min(max_tokens, MAX_OUTPUT_TOKENS)

    payload = {
        "model": settings.llm_model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ],
        "temperature": 0,
        "max_tokens": effective_max_tokens,
    }
    if enforce_json:
        # OpenAI-compatible response_format plus Ollama-native format for stricter JSON.
        payload["response_format"] = {"type": "json_object"}
        payload["format"] = "json"
        payload["stream"] = False
    elif stream is not None:
        payload["stream"] = bool(stream)

    start = time.perf_counter()
    data = None
    last_exc: Exception | None = None
    timeout_seconds = settings.llm_timeout_seconds
    for attempt in range(2):
        try:
            async with httpx.AsyncClient(timeout=timeout_seconds) as client:
                response = await client.post(url, json=payload, headers=headers)
                response.raise_for_status()
                data = response.json()
                last_exc = None
                break
        except (httpx.ReadTimeout, httpx.ConnectTimeout) as exc:
            last_exc = exc
            timeout_seconds = max(timeout_seconds * 2, timeout_seconds + 5)
        except Exception as exc:
            last_exc = exc
            break
    if data is None:
        record_llm_error(settings.llm_model, last_exc.__class__.__name__ if last_exc else "UnknownError")
        logger.exception("llm_request_failed", error=str(last_exc or "unknown"))
        duration = time.perf_counter() - start
        record_llm_timing(settings.llm_model, duration, bool(stream))
        return None, None
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

    # Strip any chain-of-thought wrapped in <think>...</think>.
    if raw_content:
        raw_content = _strip_think_tags(raw_content)

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


def _strip_think_tags(text: str) -> str:
    start_tag = "<think>"
    end_tag = "</think>"
    if start_tag not in text:
        return text
    out = []
    i = 0
    while i < len(text):
        start = text.find(start_tag, i)
        if start == -1:
            out.append(text[i:])
            break
        out.append(text[i:start])
        end = text.find(end_tag, start + len(start_tag))
        if end == -1:
            break
        i = end + len(end_tag)
    return "".join(out).strip()


def _is_truncated(raw: dict | None) -> bool:
    if not isinstance(raw, dict):
        return False
    choices = raw.get("choices")
    if not isinstance(choices, list) or not choices:
        return False
    choice = choices[0]
    if not isinstance(choice, dict):
        return False
    return choice.get("finish_reason") == "length"
