"""Single Lambda — front door AND async worker in one function.

One deployment package, one Lambda. The handler detects how it was invoked:

  * API Gateway event (has an HTTP method/path) → act as the API:
      POST /api/jobs {action, payload}
        - create a PENDING job in DynamoDB
        - self-invoke this SAME function asynchronously (InvocationType='Event')
          with {"__worker__": true, "job_id": ...}
        - return {job_id} immediately (well under the API Gateway timeout)
      GET  /api/jobs/{job_id}
        - read the job and return {status, result|error}; the browser polls this.
      fast read actions (health, industries, …) → invoke AgentCore synchronously.

  * Worker event (has "__worker__": true) → act as the worker:
      load the job, mark RUNNING, call AgentCore (the long call, up to the
      function timeout), write FINISHED/FAILED.

Self-invocation is why one function can play both roles: the API path returns
fast, and the async copy runs the long AgentCore call on its own clock. The
function's timeout must be high (e.g. 900s) to cover the worker path; the API
path still returns in well under API Gateway's limit.
"""
from __future__ import annotations

import json
import logging
import os

import boto3

import jobstore

logger = logging.getLogger()
logger.setLevel(logging.INFO)

_RUNTIME_ARN = os.environ.get("AGENT_RUNTIME_ARN", "")
# The function's own name, so it can self-invoke for the worker role.
_SELF_NAME = os.environ.get("SELF_FUNCTION_NAME") or os.environ.get("AWS_LAMBDA_FUNCTION_NAME", "")

# Actions that must run asynchronously (can exceed the API Gateway timeout).
_ASYNC_ACTIONS = {"compare", "generate", "approve_sync"}

_agentcore = None
_lambda = None

_CORS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Headers": "Content-Type",
    "Access-Control-Allow-Methods": "GET,POST,OPTIONS,DELETE,PUT",
}


def _agentcore_client():
    global _agentcore
    if _agentcore is None:
        _agentcore = boto3.client("bedrock-agentcore")
    return _agentcore


def _lambda_client():
    global _lambda
    if _lambda is None:
        _lambda = boto3.client("lambda")
    return _lambda


def _resp(status: int, body: dict) -> dict:
    return {
        "statusCode": status,
        "headers": {"Content-Type": "application/json", **_CORS},
        "body": json.dumps(body),
    }


def _invoke_agentcore(action: str, payload: dict) -> dict:
    body = json.dumps({"action": action, "payload": payload}).encode("utf-8")
    resp = _agentcore_client().invoke_agent_runtime(
        agentRuntimeArn=_RUNTIME_ARN,
        contentType="application/json",
        accept="application/json",
        payload=body,
    )
    data = resp.get("response")
    if hasattr(data, "read"):
        data = data.read()
    if isinstance(data, (bytes, bytearray)):
        data = data.decode("utf-8")
    try:
        return json.loads(data or "{}")
    except json.JSONDecodeError:
        return {"raw": data}


# --------------------------------------------------------------------------
# Worker role — long AgentCore call, writes the job result.
# --------------------------------------------------------------------------
def _run_worker(job_id: str) -> dict:
    job = jobstore.get_job(job_id)
    if not job:
        logger.error(f"worker: job {job_id} not found")
        return {"ok": False, "detail": "job not found"}
    jobstore.mark_running(job_id)
    try:
        result = _invoke_agentcore(job["action"], job.get("payload") or {})
        jobstore.mark_finished(job_id, result)
        logger.info(f"job {job_id} FINISHED")
        return {"ok": True, "job_id": job_id}
    except Exception as exc:  # noqa: BLE001
        logger.exception(f"job {job_id} FAILED")
        jobstore.mark_failed(job_id, str(exc))
        return {"ok": False, "job_id": job_id, "error": str(exc)}


# --------------------------------------------------------------------------
# API role — request parsing (HTTP API v2 and REST v1 proxy events)
# --------------------------------------------------------------------------
def _method(event: dict) -> str:
    return (
        event.get("requestContext", {}).get("http", {}).get("method")
        or event.get("httpMethod")
        or "GET"
    ).upper()


def _path(event: dict) -> str:
    return (event.get("rawPath") or event.get("path") or "").rstrip("/")


def _path_params(event: dict) -> dict:
    return event.get("pathParameters") or {}


def _body(event: dict) -> dict:
    raw = event.get("body") or "{}"
    if event.get("isBase64Encoded"):
        import base64
        raw = base64.b64decode(raw).decode("utf-8")
    try:
        return json.loads(raw) if isinstance(raw, str) else (raw or {})
    except json.JSONDecodeError:
        return {}


def _map_sync_action(method: str, path: str, qs: dict, body: dict) -> tuple[str, dict] | None:
    """Return (action, payload) for a FAST/synchronous agent call, or None."""
    if path.endswith("/api/health") and method == "GET":
        return "health", {}
    if path.endswith("/api/industries") and method == "GET":
        return "list_industries", {}
    if path.endswith("/api/agent-names") and method == "GET":
        return "list_agent_names", {"industry": qs.get("industry", "")}
    if path.endswith("/api/context-packs/stages") and method == "GET":
        return "stages", {}
    if path.endswith("/api/context-packs") and method == "GET":
        return "list_versions", {"industry": qs.get("industry", ""), "agent_name": qs.get("agent_name", "")}
    if path.endswith("/system-prompt") and method == "GET":
        return "system_prompt", {"pack_id": path.split("/")[-2]}
    if path.endswith("/api/chat/session") and method == "POST":
        # Same contract as the local FastAPI route: optional pack_id/industry
        # (query string or JSON body) stamp the session with its own industry.
        return "new_session", {
            "pack_id": qs.get("pack_id") or body.get("pack_id") or "",
            "industry": qs.get("industry") or body.get("industry") or "",
        }
    if path.endswith("/api/chat/sessions") and method == "GET":
        return "recent_sessions", {}
    if "/api/chat/session/" in path and method == "GET":
        return "session_history", {"session_id": path.split("/")[-1]}
    if "/api/chat/session/" in path and method == "DELETE":
        return "reset_session", {"session_id": path.split("/")[-1]}
    if path.endswith("/api/context-packs/check-name") and method == "POST":
        return "name_exists", {"industry": body.get("industry", ""), "name": body.get("name", "")}
    if path.endswith("/draft") and method == "PUT":
        return "update_draft", {"draft_id": path.split("/")[-2], "pack": body.get("pack")}
    if method == "DELETE" and "/api/context-packs/" in path and path.count("/") == 3:
        return "discard_draft", {"draft_id": path.split("/")[-1]}
    if method == "GET" and "/api/context-packs/" in path and path.count("/") == 3:
        return "get_pack", {"pack_id": path.split("/")[-1]}
    return None


# API Gateway caps the request at 10 MB and Lambda's synchronous payload at 6 MB;
# base64 inflates by ~33%, so keep decoded uploads comfortably under that.
_MAX_UPLOAD_BYTES = 4 * 1024 * 1024


def _parse_multipart(event: dict) -> tuple[dict, list[dict]]:
    """Parse a multipart/form-data API Gateway event into (fields, files).

    files -> [{"filename", "content_b64"}]. Uses only the stdlib so the Lambda
    package needs no extra dependencies.
    """
    import base64
    from email import policy
    from email.parser import BytesParser

    headers = {k.lower(): v for k, v in (event.get("headers") or {}).items()}
    ctype = headers.get("content-type", "")
    raw = event.get("body") or ""
    data = base64.b64decode(raw) if event.get("isBase64Encoded") else raw.encode("utf-8")
    if len(data) > _MAX_UPLOAD_BYTES:
        raise ValueError("TOO_LARGE")
    msg = BytesParser(policy=policy.default).parsebytes(
        b"Content-Type: " + ctype.encode("utf-8") + b"\r\nMIME-Version: 1.0\r\n\r\n" + data
    )
    fields: dict = {}
    files: list[dict] = []
    for part in msg.iter_parts():
        name = part.get_param("name", header="content-disposition")
        filename = part.get_filename()
        payload = part.get_payload(decode=True) or b""
        if filename is not None:
            files.append({"filename": filename, "content_b64": base64.b64encode(payload).decode("ascii")})
        elif name:
            fields[name] = payload.decode("utf-8", "replace")
    return fields, files


def _handle_upload_kb(event: dict) -> dict:
    """POST /api/context-packs/upload-kb (multipart) -> {"saved": [filenames]}.

    Same contract as the local FastAPI route. Parsed here (the body is binary,
    not JSON) and stored by the agent's ``upload_kb`` action, whose role already
    has S3 access.
    """
    try:
        fields, files = _parse_multipart(event)
    except ValueError as exc:
        if str(exc) == "TOO_LARGE":
            return _resp(413, {"detail": f"Upload exceeds {_MAX_UPLOAD_BYTES // (1024 * 1024)} MB limit."})
        return _resp(400, {"detail": f"Invalid multipart body: {exc}"})
    except Exception as exc:  # noqa: BLE001
        logger.exception("multipart parse failed")
        return _resp(400, {"detail": f"Invalid multipart body: {exc}"})
    industry = (fields.get("industry") or "").strip()
    name = (fields.get("name") or "").strip()
    if not industry or not name:
        return _resp(422, {"detail": "industry and name are required."})
    try:
        result = _invoke_agentcore("upload_kb", {"industry": industry, "name": name, "files": files})
    except Exception as exc:  # noqa: BLE001
        logger.exception("upload_kb AgentCore invocation failed")
        return _resp(502, {"detail": f"Agent runtime error: {exc}"})
    return _resp(200, result)


def _handle_presigned_url(event: dict, qs: dict) -> dict:
    """GET /api/context-packs/upload-kb/presigned -> {url, expires_in}.

    Generates a presigned S3 PUT URL for direct file upload (bypasses Lambda 4 MB limit).
    Query params: industry, name, filename, expires_in (optional, default 3600).
    """
    industry = (qs.get("industry") or "").strip()
    name = (qs.get("name") or "").strip()
    filename = (qs.get("filename") or "").strip()
    expires_in = int(qs.get("expires_in") or 3600)
    
    if not industry or not name or not filename:
        return _resp(422, {"detail": "industry, name, and filename are required."})
    
    expires_in = max(1, min(expires_in, 604800))  # Cap: 1 second to 7 days
    
    try:
        result = _invoke_agentcore("presigned_upload_url", {
            "industry": industry,
            "name": name,
            "filename": filename,
            "expires_in": expires_in,
        })
        return _resp(200, result)
    except Exception as exc:  # noqa: BLE001
        logger.exception("presigned_upload_url AgentCore invocation failed")
        return _resp(502, {"detail": f"Agent runtime error: {exc}"})


def _handle_api(event: dict) -> dict:
    method = _method(event)
    path = _path(event)
    qs = event.get("queryStringParameters") or {}

    if method == "OPTIONS":
        return _resp(200, {"ok": True})
    if not _RUNTIME_ARN:
        return _resp(500, {"detail": "AGENT_RUNTIME_ARN not configured"})

    # Multipart upload: must run BEFORE _body(), which assumes a UTF-8 JSON body.
    if path.endswith("/api/context-packs/upload-kb") and method == "POST":
        return _handle_upload_kb(event)

    # Presigned URL generation: GET before multipart (handles query string only)
    if path.endswith("/api/context-packs/upload-kb/presigned") and method == "GET":
        return _handle_presigned_url(event, qs)

    body = _body(event)

    # GET /api/jobs/{job_id} : poll
    if "/api/jobs/" in path and method == "GET":
        job_id = _path_params(event).get("job_id") or path.split("/")[-1]
        job = jobstore.get_job(job_id)
        if not job:
            return _resp(404, {"detail": f"No job '{job_id}'"})
        out = {"job_id": job_id, "status": job.get("status")}
        if job.get("status") == "FINISHED":
            out["result"] = job.get("result")
        elif job.get("status") == "FAILED":
            out["error"] = job.get("error")
        return _resp(200, out)

    # POST /api/jobs : create async job + self-invoke worker
    if path.endswith("/api/jobs") and method == "POST":
        action = body.get("action")
        payload = body.get("payload") or {}
        if action not in _ASYNC_ACTIONS:
            return _resp(400, {"detail": f"action must be one of {sorted(_ASYNC_ACTIONS)}"})
        job_id = jobstore.create_job(action, payload)
        try:
            _lambda_client().invoke(
                FunctionName=_SELF_NAME,
                InvocationType="Event",  # fire-and-forget self-invoke → worker role
                Payload=json.dumps({"__worker__": True, "job_id": job_id}).encode("utf-8"),
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("Failed to trigger worker")
            jobstore.mark_failed(job_id, f"worker trigger failed: {exc}")
            return _resp(502, {"job_id": job_id, "status": "FAILED", "error": str(exc)})
        return _resp(202, {"job_id": job_id, "status": "PENDING"})

    # synchronous (fast) actions
    mapped = _map_sync_action(method, path, qs, body)
    if mapped is None:
        return _resp(404, {"detail": f"No route for {method} {path}"})
    action, payload = mapped
    try:
        result = _invoke_agentcore(action, payload)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Sync AgentCore invocation failed")
        return _resp(502, {"detail": f"Agent runtime error: {exc}"})
    return _resp(200, result)


# --------------------------------------------------------------------------
# Entry point — route to worker or API role based on the event shape.
# --------------------------------------------------------------------------
def lambda_handler(event: dict, context) -> dict:
    if isinstance(event, dict) and event.get("__worker__"):
        return _run_worker(event.get("job_id"))
    return _handle_api(event)
