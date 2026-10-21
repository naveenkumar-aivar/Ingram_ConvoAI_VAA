"""Structured JSON logging for the agent package (CloudWatch-friendly).

Used by the AgentCore runtime container, which does not ship the ``app`` layer.
Mirrors ``app/logging_config.py`` but sources settings from ``agent.config``.
"""
import json
import logging
import logging.config
import uuid
from datetime import datetime

from agent.config import get_settings


class MetricsJsonFormatter(logging.Formatter):
    """JSON formatter that includes all extra fields from the LogRecord."""

    def format(self, record):
        log_obj = {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if hasattr(record, "__dict__"):
            for key, value in record.__dict__.items():
                if key not in {
                    "name", "msg", "args", "created", "filename", "funcName", "levelname",
                    "levelno", "lineno", "module", "msecs", "message", "pathname", "process",
                    "processName", "relativeCreated", "thread", "threadName", "exc_info",
                    "exc_text", "stack_info", "getMessage",
                }:
                    if not key.startswith("_"):
                        log_obj[key] = value
        return json.dumps(log_obj)


def get_logging_config() -> dict:
    return {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {"json": {"()": MetricsJsonFormatter}},
        "handlers": {
            "console": {
                "class": "logging.StreamHandler",
                "formatter": "json",
                "stream": "ext://sys.stdout",
            }
        },
        "root": {"level": "INFO", "handlers": ["console"]},
    }


def setup_logging() -> None:
    # Ensure settings load (validates env) before configuring logging.
    get_settings()
    logging.config.dictConfig(get_logging_config())


def get_request_id() -> str:
    return str(uuid.uuid4())[:8]
