"""AgentCore Runtime entrypoint.

This is the file the container runs inside Amazon Bedrock AgentCore Runtime. It
exposes a single ``@app.entrypoint`` handler that dispatches on an ``action``
field to the agent service facade (``agent/service.py``). AgentCore serves it on
``POST /invocations`` and ``GET /ping`` (port 8080).

Request shape (JSON):
    {"action": "compare", "payload": {...}}

Supported actions mirror the service facade:
    compare, new_session, recent_sessions, session_history, reset_session,
    generate (agentic pack build), approve (streamed SSE), update_draft,
    discard_draft, validate_pack, list_industries, list_agent_names,
    list_versions, get_pack, name_exists, stages, system_prompt.

Cost note: the handler itself performs no LLM calls — it only routes to existing
logic. Streaming actions (``compare``, ``approve``) yield incrementally so the
caller sees progress without extra model invocations.
"""
from __future__ import annotations

import logging

from bedrock_agentcore import BedrockAgentCoreApp

from agent import service
from agent.logging_config import setup_logging

setup_logging()
logger = logging.getLogger(__name__)

app = BedrockAgentCoreApp()


def _as_dict(obj):
    """Best-effort convert pydantic models / dataclasses to plain JSON dicts."""
    if obj is None:
        return None
    if hasattr(obj, "model_dump"):
        return obj.model_dump()
    if isinstance(obj, list):
        return [_as_dict(x) for x in obj]
    return obj


@app.entrypoint
def handler(request: dict):
    """Single dispatch entrypoint. ``request`` is the invocation JSON.

    Returns a JSON-serializable result for buffered actions. For streamed
    actions it is a generator that yields SSE-friendly dict events.
    """
    action = (request or {}).get("action", "compare")
    payload = (request or {}).get("payload", {}) or {}
    logger.info(f"[runtime] action={action}")

    # -------------------------------------------------------------- health
    if action == "health":
        return {"status": "ok", "service": "Vertical Adaptation Agent (AgentCore)"}

    # -------------------------------------------------------------- chat
    if action == "compare":
        return _as_dict(
            service.compare(
                question=payload["question"],
                pack_id=payload.get("pack_id"),
                history=payload.get("history") or [],
                industry=payload.get("industry"),
                session_id=payload.get("session_id"),
            )
        )
    if action == "new_session":
        return service.new_session(
            pack_id=payload.get("pack_id") or "",
            industry=payload.get("industry") or "",
        )
    if action == "recent_sessions":
        return service.recent_sessions(payload.get("limit", 5))
    if action == "session_history":
        return service.session_history(payload["session_id"])
    if action == "reset_session":
        service.reset_session(payload["session_id"])
        return {"status": "cleared", "session_id": payload["session_id"]}

    # ----------------------------------------------------- packs / meta
    if action == "list_industries":
        return service.list_industries()
    if action == "list_agent_names":
        return service.list_agent_names(payload["industry"])
    if action == "list_versions":
        return _as_dict(service.list_versions(payload["industry"], payload.get("agent_name", "")))
    if action == "get_pack":
        return _as_dict(service.get_pack(payload["pack_id"]))
    if action == "name_exists":
        return {"exists": service.name_exists(payload["industry"], payload["name"])}
    if action == "upload_kb":
        return {"saved": service.save_kb_files(payload["industry"], payload["name"], payload.get("files") or [])}
    if action == "stages":
        return service.stages()
    if action == "system_prompt":
        pack = service.get_pack(payload["pack_id"])
        return {
            "pack_id": payload["pack_id"],
            "generic_system_prompt": service.generic_system_prompt(),
            "adapted_system_prompt": service.system_prompt_for(pack),
        }

    # ------------------------------------------- create flow (draft→approve)
    if action == "generate":
        # Mirror the FastAPI /generate route: build the pack AND persist a draft
        # so the client gets a draft_id to approve/edit later.
        industry = (payload.get("industry") or "").strip()
        name = (payload.get("name") or "").strip()
        if not industry or not name:
            raise ValueError("industry and name are required.")
        if service.name_exists(industry, name):
            raise ValueError(f"'{name}' already exists for {industry}.")

        inp = service.make_build_input(**payload)
        pack, report = service.build_pack_agentic(inp)
        draft_id = service.create_draft(
            pack,
            {"reused": report.reused, "generated": report.generated, "provider": report.provider},
            name,
            kb_file_names=payload.get("kb_file_names") or [],
            user_inputs={
                "industry": industry,
                "use_case": payload.get("use_case") or "",
                "persona_hint": payload.get("persona_hint") or "",
                "instructions": payload.get("instructions") or "",
                "guardrails": payload.get("guardrails") or "",
                "handoff": payload.get("handoff") or "",
            },
        )
        return {
            "draft_id": draft_id,
            "pack": _as_dict(pack),
            "reused": report.reused,
            "generated": report.generated,
            "provider": report.provider,
            "system_prompt": pack.to_system_prompt(),
        }
    if action == "validate_pack":
        return {"gaps": service.validate_pack(payload["pack_json"])}
    if action == "update_draft":
        import json as _json
        from agent.models import ContextPack
        pack_dict = payload["pack"]
        updated = service.update_draft(payload["draft_id"], pack_dict)
        if updated is None:
            raise ValueError(f"No draft '{payload['draft_id']}'")
        gaps = service.validate_pack(_json.dumps(pack_dict))
        pack = ContextPack.model_validate(pack_dict)
        return {
            "pack": pack.model_dump(),
            "system_prompt": pack.to_system_prompt(),
            "gaps": gaps,
        }
    if action == "discard_draft":
        service.discard_draft(payload["draft_id"])
        return {"status": "discarded", "draft_id": payload["draft_id"]}
    if action == "approve":
        # Streamed: yield each approval step as an event (used by the local
        # FastAPI SSE route and by AgentCore's streaming response).
        def _stream():
            for event in service.approve_draft_stream(payload["draft_id"]):
                yield event
        return _stream()
    if action == "approve_sync":
        # Buffered variant for the serverless path (API Gateway HTTP API does
        # not stream SSE cleanly): run the approval to completion and return all
        # events plus the terminal result.
        events = list(service.approve_draft_stream(payload["draft_id"]))
        done = next((e for e in reversed(events) if e.get("step") == "done"), {})
        return {"events": events, "done": done}

    raise ValueError(f"Unknown action: {action}")


if __name__ == "__main__":
    app.run()
