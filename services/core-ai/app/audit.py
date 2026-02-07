import structlog
from sqlmodel import Session

from app.models import AuditLog

logger = structlog.get_logger("audit")


def record_audit_log(
    session: Session,
    actor_id: str,
    action: str,
    target_type: str,
    target_id: str | int | None,
    details: dict | None = None,
) -> None:
    entry = AuditLog(
        actor_id=actor_id,
        action=action,
        target_type=target_type,
        target_id=str(target_id) if target_id is not None else None,
        details=details or {},
    )
    session.add(entry)
    logger.info(
        "audit_event",
        action=action,
        target_type=target_type,
        target_id=target_id,
        actor_id=actor_id,
    )
