from typing import Any, TypedDict


class ChatState(TypedDict, total=False):
    session_id: str
    tenant_id: str
    message: str
    user: dict[str, Any]
    main_route: str
    sub_route: str
    sub_route_fields: dict[str, Any]
    domain: str
    doc_scope: str
    sensitivity: str
    pending_request: dict[str, Any] | None
    response: str
    actions: list[dict[str, Any]]
    events: list[dict[str, Any]]
    event_queue: Any
