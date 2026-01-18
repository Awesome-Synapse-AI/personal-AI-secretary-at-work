import json

import httpx

from app.config import settings


async def call_llm_json(system_prompt: str, user_message: str, max_tokens: int) -> dict | None:
    content = await _call_llm(system_prompt, user_message, max_tokens)
    if not content:
        return None
    try:
        payload = json.loads(content)
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


async def _call_llm(system_prompt: str, user_message: str, max_tokens: int) -> str | None:
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

    try:
        async with httpx.AsyncClient(timeout=settings.llm_timeout_seconds) as client:
            response = await client.post(url, json=payload, headers=headers)
            response.raise_for_status()
            data = response.json()
    except Exception:
        return None

    choice = data.get("choices", [{}])[0]
    content = choice.get("message", {}).get("content") or choice.get("text", "")
    return content.strip() if content else None
