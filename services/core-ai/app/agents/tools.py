from typing import Any

import httpx

from app.config import settings


class ToolRunner:
    def __init__(self) -> None:
        self._client = httpx.AsyncClient(timeout=10)

    async def close(self) -> None:
        await self._client.aclose()

    async def call(self, service: str, method: str, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        if not settings.tools_enabled:
            return {"status": "skipped", "service": service, "reason": "TOOLS_ENABLED=false"}

        url = f"{settings.domain_service_url}{path}"
        headers = {}
        if settings.service_auth_token:
            headers["Authorization"] = f"Bearer {settings.service_auth_token}"
        response = await self._client.request(method, url, json=payload, headers=headers)
        response.raise_for_status()
        return response.json() if response.content else {"status": "ok"}


tool_runner = ToolRunner()
