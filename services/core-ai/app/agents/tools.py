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

        base_url = _service_url(service)
        url = f"{base_url}{path}"
        headers = {}
        if settings.service_auth_token:
            headers["Authorization"] = f"Bearer {settings.service_auth_token}"
        response = await self._client.request(method, url, json=payload, headers=headers)
        response.raise_for_status()
        return response.json() if response.content else {"status": "ok"}


def _service_url(service: str) -> str:
    if service == "workspace":
        return settings.workspace_service_url
    if service == "leave":
        return settings.leave_service_url
    if service == "expense":
        return settings.expense_service_url
    if service == "ticket":
        return settings.ticket_service_url
    if service == "access":
        return settings.access_service_url
    raise ValueError(f"Unknown service: {service}")


tool_runner = ToolRunner()
