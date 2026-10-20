"""Structured logging for AWS CloudWatch and local development.

In AWS mode: JSON logs with request IDs, timestamps, and metadata → CloudWatch.
In local mode: human-readable console logs.

All logs include:
- request_id: unique trace ID for correlating requests
- timestamp: ISO 8601 timestamp
- metrics: timing data, response codes, evaluation scores
"""
import json
import logging
import logging.config
import uuid
from datetime import datetime

from app.config import get_settings


class MetricsJsonFormatter(logging.Formatter):
    """Custom JSON formatter that includes all extra fields from the LogRecord."""

    def format(self, record):
        # Build the base log object
        log_obj = {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        
        # Include all extra fields from the record (request_id, duration_ms, metrics, etc.)
        if hasattr(record, "__dict__"):
            for key, value in record.__dict__.items():
                # Skip standard LogRecord attributes
                if key not in {
                    "name", "msg", "args", "created", "filename", "funcName", "levelname",
                    "levelno", "lineno", "module", "msecs", "message", "pathname", "process",
                    "processName", "relativeCreated", "thread", "threadName", "exc_info",
                    "exc_text", "stack_info", "getMessage", "getMessage",
                }:
                    if not key.startswith("_"):
                        log_obj[key] = value
        
        return json.dumps(log_obj)


def get_logging_config():
    """Return logging config for AWS CloudWatch with JSON formatting."""
    s = get_settings()

    if s.aws_region:
        return {
            "version": 1,
            "disable_existing_loggers": False,
            "formatters": {
                "json": {
                    "()": MetricsJsonFormatter,
                }
            },
            "handlers": {
                "console": {
                    "class": "logging.StreamHandler",
                    "formatter": "json",
                    "stream": "ext://sys.stdout",
                }
            },
            "root": {"level": "INFO", "handlers": ["console"]},
            "loggers": {
                "uvicorn.access": {"level": "INFO", "handlers": ["console"], "propagate": False},
                "uvicorn.error": {"level": "ERROR", "handlers": ["console"], "propagate": False},
            },
        }
    else:
        # Fallback when AWS region not configured
        return {
            "version": 1,
            "disable_existing_loggers": False,
            "formatters": {
                "json": {
                    "()": MetricsJsonFormatter,
                }
            },
            "handlers": {
                "console": {
                    "class": "logging.StreamHandler",
                    "formatter": "json",
                    "stream": "ext://sys.stdout",
                }
            },
            "root": {"level": "INFO", "handlers": ["console"]},
            "loggers": {
                "uvicorn.access": {"level": "INFO", "handlers": ["console"], "propagate": False},
                "uvicorn.error": {"level": "ERROR", "handlers": ["console"], "propagate": False},
            },
        }


def setup_logging():
    """Configure logging based on environment."""
    config = get_logging_config()
    logging.config.dictConfig(config)


def get_request_id():
    """Generate a unique request ID for tracing."""
    return str(uuid.uuid4())[:8]
