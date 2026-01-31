import json
import logging

import httpx

from app.config import settings

logger = logging.getLogger(__name__)
if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setLevel(logging.INFO)
    logger.addHandler(handler)
logger.setLevel(logging.INFO)


async def call_llm_json(system_prompt: str, user_message: str, max_tokens: int) -> dict | None:
    # Request JSON-formatted output to improve parsing reliability with Ollama.
    content, raw = await _call_llm(
        system_prompt, user_message, max_tokens, enforce_json=True, stream=False
    )
    if raw is not None:
        logger.info("LLM raw response (truncated): %s", _truncate(json.dumps(raw, default=str), 600))
        print("LLM raw response (truncated):", _truncate(json.dumps(raw, default=str), 600), flush=True)
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
        logger.info(
            "LLM repaired raw response (truncated): %s",
            _truncate(json.dumps(repaired_raw, default=str), 600),
        )
        print(
            "LLM repaired raw response (truncated):",
            _truncate(json.dumps(repaired_raw, default=str), 600),
            flush=True,
        )
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

    try:
        async with httpx.AsyncClient(timeout=settings.llm_timeout_seconds) as client:
            response = await client.post(url, json=payload, headers=headers)
            response.raise_for_status()
            data = response.json()
    except Exception as exc:
        logger.exception("LLM request failed: %s", exc)
        return None, None

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

    return (raw_content.strip() if raw_content else None), data


def _truncate(text: str, length: int) -> str:
    return text if len(text) <= length else f"{text[:length]}..."
