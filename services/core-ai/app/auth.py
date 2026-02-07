import time
from typing import Any

import httpx
import structlog
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from structlog.contextvars import bind_contextvars

from app.config import settings
from app.schemas.chat import UserContext

bearer_scheme = HTTPBearer(auto_error=False)

_jwks_cache: dict[str, Any] = {"keys": None, "expires_at": 0}
logger = structlog.get_logger("auth")


def _default_user() -> UserContext:
    return UserContext(
        sub="local-user",
        username="local-user",
        roles=["employee", "manager", "hr_approver", "it_approver", "admin_approver", "system_admin"],
        claims={"source": "auth_disabled"},
    )


async def _fetch_jwks() -> list[dict[str, Any]]:
    if _jwks_cache["keys"] and _jwks_cache["expires_at"] > time.time():
        return _jwks_cache["keys"]

    url = f"{settings.keycloak_realm_url}/protocol/openid-connect/certs"
    async with httpx.AsyncClient(timeout=5) as client:
        response = await client.get(url)
        response.raise_for_status()
        payload = response.json()

    keys = payload.get("keys", [])
    _jwks_cache["keys"] = keys
    _jwks_cache["expires_at"] = time.time() + 3600
    return keys


async def _decode_token(token: str) -> dict[str, Any]:
    headers = jwt.get_unverified_header(token)
    jwks = await _fetch_jwks()
    key = next((k for k in jwks if k.get("kid") == headers.get("kid")), None)
    if not key:
        raise HTTPException(status_code=401, detail="Token key not found")

    try:
        if settings.keycloak_client_id:
            return jwt.decode(
                token,
                key,
                algorithms=["RS256"],
                audience=settings.keycloak_client_id,
            )
        return jwt.decode(
            token, key, algorithms=["RS256"], options={"verify_aud": False}
        )
    except JWTError as exc:
        raise HTTPException(status_code=401, detail="Invalid token") from exc


def _user_from_claims(claims: dict[str, Any]) -> UserContext:
    roles = claims.get("realm_access", {}).get("roles", [])
    return UserContext(
        sub=claims.get("sub", ""),
        username=claims.get("preferred_username", ""),
        roles=roles,
        claims=claims,
    )


def require_roles(user: UserContext, allowed_roles: set[str], action: str) -> None:
    """
    Enforce role-based authorization for protected actions.

    If auth is disabled, the local user is seeded with broad roles so development
    flows continue to work while keeping the contract identical.
    """
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


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
) -> UserContext:
    if settings.auth_disabled:
        user = _default_user()
        bind_contextvars(user_id=user.sub, roles=user.roles)
        return user
    if not credentials:
        raise HTTPException(status_code=401, detail="Missing credentials")
    claims = await _decode_token(credentials.credentials)
    user = _user_from_claims(claims)
    bind_contextvars(user_id=user.sub, roles=user.roles)
    return user


async def get_user_from_token(token: str | None) -> UserContext:
    if settings.auth_disabled:
        user = _default_user()
        bind_contextvars(user_id=user.sub, roles=user.roles)
        return user
    if not token:
        raise HTTPException(status_code=401, detail="Missing credentials")
    claims = await _decode_token(token)
    user = _user_from_claims(claims)
    bind_contextvars(user_id=user.sub, roles=user.roles)
    return user
