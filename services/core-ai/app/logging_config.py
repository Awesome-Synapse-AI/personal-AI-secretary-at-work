import logging
import sys

import structlog
from structlog.contextvars import clear_contextvars


def configure_logging(level: str) -> None:
    """
    Configure structured JSON logging for the service.

    We rely on contextvars so request-scoped metadata (request_id, tenant_id, user)
    automatically flows into every log line without manual plumbing.
    """
    logging.basicConfig(
        level=level,
        format="%(message)s",
        stream=sys.stdout,
        force=True,
    )

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.dict_tracebacks,
            structlog.processors.JSONRenderer(),
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.make_filtering_bound_logger(
            logging.getLevelName(level)
        ),
        cache_logger_on_first_use=True,
    )
    clear_contextvars()
