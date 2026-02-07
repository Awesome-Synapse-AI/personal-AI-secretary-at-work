import time
import uuid
from typing import Callable

import structlog
from fastapi import Request, Response
from fastapi.responses import PlainTextResponse
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest
from starlette.middleware.base import BaseHTTPMiddleware
from structlog.contextvars import bind_contextvars, clear_contextvars

from app.config import settings

# Metrics collectors
REQUEST_COUNT = Counter(
    "core_ai_http_requests_total",
    "Total HTTP requests received",
    ["method", "path", "status"],
)
REQUEST_LATENCY = Histogram(
    "core_ai_http_request_duration_seconds",
    "HTTP request latency in seconds",
    ["method", "path", "status"],
    buckets=(0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10),
)
LLM_LATENCY = Histogram(
    "core_ai_llm_request_duration_seconds",
    "LLM request latency in seconds",
    ["model", "streaming"],
    buckets=(0.1, 0.25, 0.5, 1, 2, 5, 10, 20),
)
LLM_ERRORS = Counter(
    "core_ai_llm_request_errors_total",
    "LLM request errors",
    ["model", "reason"],
)

logger = structlog.get_logger("core-ai")


def path_template(request: Request) -> str:
    route = request.scope.get("route")
    if route and getattr(route, "path", None):
        return route.path  # type: ignore[return-value]
    return request.url.path


class RequestContextMiddleware(BaseHTTPMiddleware):
    """
    Attach a request id, tenant id, and timing to every request.
    Structured logs emitted here become the entry point for request traces.
    """

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        request_id = request.headers.get("x-request-id") or str(uuid.uuid4())
        tenant_id = request.headers.get("x-tenant-id", settings.default_tenant_id)
        bind_contextvars(
            request_id=request_id,
            tenant_id=tenant_id,
            path=path_template(request),
            method=request.method,
        )
        start = time.perf_counter()
        try:
            response = await call_next(request)
            status = response.status_code
        except Exception as exc:
            status = 500
            logger.exception("http_request_error", error=str(exc))
            raise
        finally:
            duration = time.perf_counter() - start
            REQUEST_COUNT.labels(request.method, path_template(request), str(status)).inc()
            REQUEST_LATENCY.labels(request.method, path_template(request), str(status)).observe(duration)
            logger.info(
                "http_request",
                status_code=status,
                duration_ms=round(duration * 1000, 2),
                user_agent=request.headers.get("user-agent", ""),
            )
            clear_contextvars()

        response.headers["X-Request-ID"] = request_id
        response.headers["X-Tenant-ID"] = tenant_id
        return response


def metrics_endpoint() -> Response:
    return PlainTextResponse(generate_latest(), media_type=CONTENT_TYPE_LATEST)


def record_llm_timing(model: str, duration: float, streaming: bool) -> None:
    LLM_LATENCY.labels(model, str(streaming).lower()).observe(duration)


def record_llm_error(model: str, reason: str) -> None:
    LLM_ERRORS.labels(model, reason).inc()
