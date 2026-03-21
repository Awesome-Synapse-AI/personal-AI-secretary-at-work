import structlog
from fastapi import HTTPException
from structlog.contextvars import bind_contextvars

from app.schemas.chat import UserContext

logger = structlog.get_logger("auth")


def _default_user() -> UserContext:
    return UserContext(
        sub="local-user",
        username="local-user",
        roles=["employee", "manager", "hr_approver", "it_approver", "admin_approver", "system_admin"],
        claims={"source": "local_auth"},
    )


def require_roles(user: UserContext, allowed_roles: set[str], action: str) -> None:
    if not allowed_roles:
        return
    user_roles = set(user.roles or [])
    if user_roles.intersection(allowed_roles):
        return
    logger.warning(
        "rbac_denied",
        action=action,
        required=list(allowed_roles),
        user_roles=list(user_roles),
        user=user.username,
    )
    raise HTTPException(
        status_code=403,
        detail=f"Insufficient role for {action}. Required: {', '.join(sorted(allowed_roles))}",
    )


async def get_current_user() -> UserContext:
    user = _default_user()
    bind_contextvars(user_id=user.sub, roles=user.roles)
    return user


async def get_user_from_token(_token: str | None) -> UserContext:
    user = _default_user()
    bind_contextvars(user_id=user.sub, roles=user.roles)
    return user
