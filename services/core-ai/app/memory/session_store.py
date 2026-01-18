import json
from typing import Any

from redis.asyncio import Redis


class SessionStore:
    def __init__(self, redis_url: str, ttl_seconds: int) -> None:
        self._redis_url = redis_url
        self._ttl_seconds = ttl_seconds
        self._redis: Redis | None = None

    async def connect(self) -> None:
        self._redis = Redis.from_url(self._redis_url, decode_responses=True)
        await self._redis.ping()

    async def close(self) -> None:
        if self._redis:
            await self._redis.close()

    def _pending_key(self, tenant_id: str, session_id: str) -> str:
        return f"pending_request:{tenant_id}:{session_id}"

    def _history_key(self, tenant_id: str, session_id: str) -> str:
        return f"chat_history:{tenant_id}:{session_id}"

    async def get_pending_request(self, tenant_id: str, session_id: str) -> dict[str, Any] | None:
        if not self._redis:
            return None
        payload = await self._redis.get(self._pending_key(tenant_id, session_id))
        if not payload:
            return None
        return json.loads(payload)

    async def set_pending_request(
        self, tenant_id: str, session_id: str, pending_request: dict[str, Any]
    ) -> None:
        if not self._redis:
            return
        key = self._pending_key(tenant_id, session_id)
        await self._redis.set(key, json.dumps(pending_request))
        await self._redis.expire(key, self._ttl_seconds)

    async def clear_pending_request(self, tenant_id: str, session_id: str) -> None:
        if not self._redis:
            return
        await self._redis.delete(self._pending_key(tenant_id, session_id))

    async def append_message(
        self, tenant_id: str, session_id: str, role: str, content: str
    ) -> None:
        if not self._redis:
            return
        key = self._history_key(tenant_id, session_id)
        item = json.dumps({"role": role, "content": content})
        await self._redis.rpush(key, item)
        await self._redis.expire(key, self._ttl_seconds)

    async def get_history(self, tenant_id: str, session_id: str) -> list[dict[str, Any]]:
        if not self._redis:
            return []
        key = self._history_key(tenant_id, session_id)
        items = await self._redis.lrange(key, 0, -1)
        return [json.loads(item) for item in items]
