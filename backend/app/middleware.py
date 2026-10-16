"""Request tracking middleware for AWS logging."""
import json
import logging
import time
from contextvars import ContextVar

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

request_id_var: ContextVar[str] = ContextVar("request_id", default="")
request_metrics_var: ContextVar[dict] = ContextVar("request_metrics", default={})

logger = logging.getLogger(__name__)


class RequestIDMiddleware(BaseHTTPMiddleware):
    """Attach a unique request ID to each request for tracing and collect metrics."""

    async def dispatch(self, request: Request, call_next):
        from app.logging_config import get_request_id

        req_id = request.headers.get("X-Request-ID", get_request_id())
        request_id_var.set(req_id)
        
        # Initialize metrics dict for this request
        metrics = {
            "request_id": req_id,
            "method": request.method,
            "path": request.url.path,
            "timestamp": time.time(),
        }
        request_metrics_var.set(metrics)

        start_time = time.time()
        response = await call_next(request)
        elapsed = time.time() - start_time
        elapsed_ms = int(elapsed * 1000)

        # Enrich metrics with response data
        metrics.update({
            "status": response.status_code,
            "duration_ms": elapsed_ms,
        })

        # Classify the request for structured logging
        is_compare_endpoint = "/api/chat/compare" in request.url.path
        is_generate_endpoint = "/api/context-packs/generate" in request.url.path
        
        # Log the request with context for CloudWatch
        log_level = "warning" if response.status_code >= 400 else "info"
        log_method = getattr(logger, log_level)
        
        log_method(
            f"{request.method} {request.url.path} {response.status_code}",
            extra=metrics,
        )
        
        # Additional metrics for performance-critical endpoints
        if is_compare_endpoint and elapsed_ms > 5000:
            logger.warning(
                f"Slow compare request: {elapsed_ms}ms",
                extra={**metrics, "slow_request": True},
            )
        
        if is_generate_endpoint and elapsed_ms > 10000:
            logger.warning(
                f"Slow generate request: {elapsed_ms}ms",
                extra={**metrics, "slow_request": True},
            )

        response.headers["X-Request-ID"] = req_id
        response.headers["X-Response-Time-MS"] = str(elapsed_ms)
        return response
