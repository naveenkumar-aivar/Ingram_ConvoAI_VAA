"""FastAPI application — the thin HTTP layer the UI calls.

All real work lives in the modules below; this file only wires HTTP to them.

Context-pack creation (the agent) follows a draft → approve flow:
    POST   /api/context-packs/check-name   duplicate (industry+name) check
    POST   /api/context-packs/generate     build a DRAFT (reuse + LLM); not saved
    POST   /api/context-packs/{id}/approve commit the draft (versioned)
    DELETE /api/context-packs/{id}         discard a draft (reject)

Chat (before/after demo with memory):
    GET    /api/health
    GET    /api/industries
    GET    /api/agent-names?industry=       list agent names for an industry
    GET    /api/context-packs?industry=&agent_name=   list versions, optionally filtered by agent
    GET    /api/context-packs/{pack_id}
    GET    /api/context-packs/{pack_id}/system-prompt
    POST   /api/chat/session   DELETE /api/chat/session/{id}
    POST   /api/chat/compare
"""
from __future__ import annotations

import json

from fastapi import FastAPI, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from app.logging_config import setup_logging
from app.middleware import RequestIDMiddleware

from app import drafts
from app.agent import GENERIC_SYSTEM_PROMPT, system_prompt_for
from app.compare import compare
from app.config import get_settings
from app.models import (
    CompareRequest,
    CompareResponse,
    ContextPack,
    DraftResponse,
    GenerateRequest,
    IndustryItem,
    NameCheckResponse,
    PackVersionItem,
    UpdateDraftRequest,
)
from app.generator_tools import validate_pack
from app.pack_builder import STAGES, BuildInput
from app.generator_agent import build_pack_agentic
from app.aws.storage import get_store
from app.sessions import get_sessions

settings = get_settings()
setup_logging()

app = FastAPI(title="Vertical Adaptation Agent API", version="0.1.0")

# Request tracking middleware (adds X-Request-ID, logs to CloudWatch in AWS).
app.add_middleware(RequestIDMiddleware)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------- health/meta
@app.get("/api/health")
def health() -> dict:
    return {"status": "ok", "service": "Vertical Adaptation Agent (AWS-only)"}


@app.get("/api/industries", response_model=list[IndustryItem])
def industries() -> list[dict]:
    return get_store().list_industries()


@app.get("/api/context-packs/stages", response_model=list[str])
def stages() -> list[str]:
    return STAGES


# ---------------------------------------------------------------- read packs
@app.get("/api/context-packs", response_model=list[PackVersionItem])
def versions(industry: str, agent_name: str = '') -> list[PackVersionItem]:
    return get_store().list_versions(industry, agent_name)


@app.get("/api/agent-names")
def agent_names(industry: str) -> list[str]:
    """List unique agent names for a given industry."""
    return get_store().list_agent_names(industry)


@app.get("/api/context-packs/{pack_id}/system-prompt")
def pack_system_prompt(pack_id: str) -> dict:
    p = get_store().get_pack(pack_id)
    if not p:
        raise HTTPException(status_code=404, detail=f"No context pack '{pack_id}'")
    return {
        "pack_id": pack_id,
        "generic_system_prompt": GENERIC_SYSTEM_PROMPT,
        "adapted_system_prompt": system_prompt_for(p),
    }


@app.get("/api/context-packs/{pack_id}", response_model=ContextPack)
def pack(pack_id: str) -> ContextPack:
    p = get_store().get_pack(pack_id)
    if not p:
        raise HTTPException(status_code=404, detail=f"No context pack '{pack_id}'")
    return p


# ----------------------------------------------- create flow (draft→approve)
@app.post("/api/context-packs/upload-kb")
async def upload_kb(industry: str = Form(...), name: str = Form(...),
                    files: list[UploadFile] = []) -> dict:
    """Store uploaded knowledge-base files for (industry, name). The files are
    ingested into the vector store when the pack is approved. Returns the
    stored filenames to echo back into the generate request."""
    from app.aws.knowledge_base import save_upload
    if not industry.strip() or not name.strip():
        raise HTTPException(status_code=422, detail="industry and name are required.")
    saved: list[str] = []
    for f in files:
        data = await f.read()
        if data:
            saved.append(save_upload(industry, name, f.filename, data))
    return {"saved": saved}


@app.post("/api/context-packs/check-name", response_model=NameCheckResponse)
def check_name(req: GenerateRequest) -> NameCheckResponse:
    return NameCheckResponse(exists=get_store().name_exists(req.industry, req.name))


@app.post("/api/context-packs/generate", response_model=DraftResponse)
def generate_draft(req: GenerateRequest) -> DraftResponse:
    import logging
    logger = logging.getLogger(__name__)
    
    try:
        industry = req.industry.strip()
        name = req.name.strip()
        if not industry or not name:
            raise HTTPException(status_code=422, detail="industry and name are required.")
        if get_store().name_exists(industry, name):
            raise HTTPException(status_code=409, detail=f"'{name}' already exists for {industry}.")

        inp = BuildInput(
            industry=industry, use_case=req.use_case, name=name,
            persona_hint=req.persona_hint, instructions=req.instructions,
            guardrails=req.guardrails, handoff=req.handoff,
            kb_file_names=req.kb_file_names, dynamic=req.dynamic,
        )
        try:
            pack, report = build_pack_agentic(inp)
        except ValueError as exc:
            logger.error(f"BuildInput validation error: {exc}", exc_info=True)
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except Exception as exc:
            logger.error(f"Pack building failed: {exc}", exc_info=True)
            raise HTTPException(status_code=500, detail=f"Failed to build pack: {str(exc)}") from exc

        try:
            draft_id = drafts.create(
                pack,
                {"reused": report.reused, "generated": report.generated, "provider": report.provider},
                name, 
                kb_file_names=req.kb_file_names,
                user_inputs={
                    "industry": req.industry,
                    "use_case": req.use_case,
                    "persona_hint": req.persona_hint or "",
                    "instructions": req.instructions or "",
                    "guardrails": req.guardrails or "",
                    "handoff": req.handoff or "",
                }
            )
        except Exception as exc:
            logger.error(f"Draft creation failed: {exc}", exc_info=True)
            raise HTTPException(status_code=500, detail=f"Failed to create draft: {str(exc)}") from exc

        return DraftResponse(
            draft_id=draft_id, pack=pack,
            reused=report.reused, generated=report.generated, provider=report.provider,
            system_prompt=pack.to_system_prompt(),
        )
    except HTTPException:
        raise  # Re-raise HTTP exceptions
    except Exception as exc:
        logger.error(f"Unexpected error in generate_draft: {exc}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Unexpected error: {str(exc)}") from exc


@app.put("/api/context-packs/{draft_id}/draft")
def update_draft(draft_id: str, req: UpdateDraftRequest) -> dict:
    """Save edited pack content to a draft, re-validate it, and return the
    re-rendered preview. The submitted pack is stored BEFORE validation so the
    author's edits are never lost (Requirement 1.3/1.4/1.7)."""
    import logging
    logger = logging.getLogger(__name__)

    pack = req.pack  # already validated as a ContextPack by pydantic
    pack_dict = pack.model_dump()

    updated = drafts.update(draft_id, pack_dict)
    if updated is None:
        raise HTTPException(status_code=404, detail=f"No draft '{draft_id}'")

    try:
        gaps = validate_pack(json.dumps(pack_dict))
    except Exception as exc:  # noqa: BLE001
        logger.error(f"Re-validation failed for draft {draft_id}: {exc}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Re-validation failed: {str(exc)}") from exc

    return {
        "pack": pack.model_dump(),
        "system_prompt": pack.to_system_prompt(),
        "gaps": gaps,
    }


@app.post("/api/context-packs/{draft_id}/approve")
def approve_draft(draft_id: str) -> StreamingResponse:
    """Approve a draft, streaming step-by-step progress as Server-Sent Events
    (Requirement 4). Each event is a JSON object in an SSE `data:` frame."""
    def _event_stream():
        import logging
        logger = logging.getLogger(__name__)
        try:
            for event in drafts.approve_stream(draft_id):
                yield f"data: {json.dumps(event)}\n\n"
        except Exception as exc:  # noqa: BLE001 — stream must terminate cleanly
            logger.error(f"Error approving draft {draft_id}: {exc}", exc_info=True)
            yield f"data: {json.dumps({'step': 'done', 'status': 'failed', 'detail': str(exc)})}\n\n"

    return StreamingResponse(
        _event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.delete("/api/context-packs/{draft_id}")
def reject_draft(draft_id: str) -> dict:
    drafts.discard(draft_id)
    return {"status": "discarded", "draft_id": draft_id}


# ---------------------------------------------------------------- chat
@app.post("/api/chat/session")
def new_session() -> dict:
    return {"session_id": get_sessions().new_session()}


@app.get("/api/chat/sessions")
def recent_sessions() -> list[dict]:
    """Last 5 conversations (for the sidebar 'resume' list)."""
    return get_sessions().recent(5)


@app.get("/api/chat/session/{session_id}")
def session_history(session_id: str) -> dict:
    """Full history of a session, to restore the conversation in the UI."""
    return get_sessions().full_history(session_id)


@app.delete("/api/chat/session/{session_id}")
def reset_session(session_id: str) -> dict:
    get_sessions().reset(session_id)
    return {"status": "cleared", "session_id": session_id}


@app.post("/api/chat/compare", response_model=CompareResponse)
def chat_compare(req: CompareRequest) -> CompareResponse:
    history = [t.model_dump() for t in req.history]
    return compare(req.question, req.pack_id, history, industry=req.industry, session_id=req.session_id)
