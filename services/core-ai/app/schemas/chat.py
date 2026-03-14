from typing import Any

from pydantic import BaseModel, Field


class UserContext(BaseModel):
    sub: str
    username: str
    roles: list[str] = Field(default_factory=list)
    claims: dict[str, Any] = Field(default_factory=dict)


class ChatRequest(BaseModel):
    message: str
    session_id: str | None = None
    tenant_id: str | None = None
    metadata: dict[str, Any] | None = None


class ChatResponse(BaseModel):
    session_id: str
    session_title: str | None = None
    message: str
    actions: list[dict[str, Any]] = Field(default_factory=list)
    pending_request: dict[str, Any] | None = None
    events: list[dict[str, Any]] = Field(default_factory=list)


class ChatSessionMeta(BaseModel):
    id: str
    title: str | None = None
    updated_at: int


class ChatSessionRenameRequest(BaseModel):
    title: str


class ChatMessagePayload(BaseModel):
    id: str
    role: str
    content: str
    created_at: int
    pending_request: dict[str, Any] | None = None
    actions: list[dict[str, Any]] | None = None
    events: list[dict[str, Any]] | None = None


class ChatSessionMessagesResponse(BaseModel):
    session_id: str
    title: str | None = None
    messages: list[ChatMessagePayload]
