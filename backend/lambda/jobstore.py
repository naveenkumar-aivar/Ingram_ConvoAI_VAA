"""DynamoDB-backed job store shared by the API and worker Lambdas.

A job tracks one asynchronous agent invocation:

    job_id      str   (partition key, uuid hex)
    status      str   PENDING | RUNNING | FINISHED | FAILED
    action      str   the agent action (e.g. "compare", "generate")
    payload     map   the agent payload (stored so the worker can run it)
    result      map   the agent result (set when FINISHED)
    error       str   error detail (set when FAILED)
    created_at  int   epoch seconds
    updated_at  int   epoch seconds
    expires_at  int   TTL epoch seconds (auto-cleanup)

The table name comes from JOBS_TABLE (default "vaa-jobs"). All timestamps are
epoch seconds so the DynamoDB TTL on `expires_at` can reap old jobs.
"""
from __future__ import annotations

import os
import time
import uuid
from decimal import Decimal
from typing import Any

import boto3

JOBS_TABLE = os.environ.get("JOBS_TABLE", "vaa-jobs")
JOB_TTL_SECONDS = int(os.environ.get("JOB_TTL_SECONDS", str(24 * 3600)))

_table = None


def _get_table():
    global _table
    if _table is None:
        _table = boto3.resource("dynamodb").Table(JOBS_TABLE)
    return _table


def _now() -> int:
    return int(time.time())


def _to_dynamo(obj: Any) -> Any:
    """Convert floats to Decimal (DynamoDB has no float) recursively."""
    if isinstance(obj, float):
        return Decimal(str(obj))
    if isinstance(obj, dict):
        return {k: _to_dynamo(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_to_dynamo(v) for v in obj]
    return obj


def _from_dynamo(obj: Any) -> Any:
    """Convert Decimal back to int/float for JSON serialization."""
    if isinstance(obj, Decimal):
        # Whole numbers → int, else float.
        return int(obj) if obj % 1 == 0 else float(obj)
    if isinstance(obj, dict):
        return {k: _from_dynamo(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_from_dynamo(v) for v in obj]
    return obj


def create_job(action: str, payload: dict) -> str:
    """Create a PENDING job and return its id."""
    job_id = uuid.uuid4().hex
    now = _now()
    item = {
        "job_id": job_id,
        "status": "PENDING",
        "action": action,
        "payload": _to_dynamo(payload or {}),
        "created_at": now,
        "updated_at": now,
        "expires_at": now + JOB_TTL_SECONDS,
    }
    _get_table().put_item(Item=item)
    return job_id


def get_job(job_id: str) -> dict | None:
    resp = _get_table().get_item(Key={"job_id": job_id})
    item = resp.get("Item")
    return _from_dynamo(item) if item else None


def mark_running(job_id: str) -> None:
    _get_table().update_item(
        Key={"job_id": job_id},
        UpdateExpression="SET #s = :s, updated_at = :u",
        ExpressionAttributeNames={"#s": "status"},
        ExpressionAttributeValues={":s": "RUNNING", ":u": _now()},
    )


def mark_finished(job_id: str, result: dict) -> None:
    _get_table().update_item(
        Key={"job_id": job_id},
        UpdateExpression="SET #s = :s, #r = :r, updated_at = :u",
        ExpressionAttributeNames={"#s": "status", "#r": "result"},
        ExpressionAttributeValues={
            ":s": "FINISHED",
            ":r": _to_dynamo(result or {}),
            ":u": _now(),
        },
    )


def mark_failed(job_id: str, error: str) -> None:
    _get_table().update_item(
        Key={"job_id": job_id},
        UpdateExpression="SET #s = :s, #e = :e, updated_at = :u",
        ExpressionAttributeNames={"#s": "status", "#e": "error"},
        ExpressionAttributeValues={":s": "FAILED", ":e": str(error)[:2000], ":u": _now()},
    )
