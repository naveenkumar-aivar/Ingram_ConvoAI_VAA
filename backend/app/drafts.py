"""Draft storage for the Create-a-Context-Pack flow (S3 only).

A generated pack is NOT committed until a human approves it. Until then it
lives at s3-data/_drafts/<draft_id>.json in S3.

Approval is a streamed, step-by-step operation (approve_stream). The context-
pack version file is the SINGLE SOURCE OF TRUTH; nothing persona/prompts/
guardrails/handoff is written under industry-docs any more. Steps, in order:
  1. "s3"        : finalise meta + merge industry-common ontology + save pack.
  2. "guardrail" : create the real Bedrock guardrail from the pack; write its
                   id/version into pack.integrations + pack.guardrails.
  3. "kb"        : ingest uploaded KB files into the vector store (NON-FATAL).
  4. "prompt"    : create the real Bedrock Prompt Management resource; write its
                   id into pack.integrations.
Finally the pack is re-saved so the resource ids persist, the draft is
discarded, and a terminal "done" event carries the committed pack.

Guardrail and prompt steps create real Bedrock resources (guardrail/prompt
resources are always created). The KB step ingests files into Bedrock KB.
"""
from __future__ import annotations

import json
import logging
import uuid
from typing import Iterator

from app.config import get_settings
from app.models import ContextPack
from app.aws import pieces
from app.aws.storage import get_store
from app.aws.bedrock_resources import (
    get_bedrock_manager,
    BedrockResourcesError,
    GuardrailCreationError,
    PromptTemplateError,
)

logger = logging.getLogger(__name__)


def _s3_client():
    """Lazy get S3 client."""
    import boto3

    settings = get_settings()
    return boto3.client("s3", region_name=settings.aws_region)


def _draft_key(draft_id: str) -> str:
    """S3 key for draft storage."""
    return f"_drafts/{draft_id}.json"


def _get_draft(draft_id: str) -> dict | None:
    """Load draft from S3."""
    try:
        settings = get_settings()
        s3 = _s3_client()
        key = _draft_key(draft_id)
        response = s3.get_object(Bucket=settings.s3_bucket, Key=key)
        body = response["Body"].read()
        return json.loads(body)
    except Exception as exc:  # noqa: BLE001
        logger.debug(f"[drafts] get {draft_id} failed: {exc}")
        return None


def _save_draft(draft_id: str, draft_data: dict) -> bool:
    """Save draft to S3."""
    try:
        settings = get_settings()
        s3 = _s3_client()
        key = _draft_key(draft_id)
        body = json.dumps(draft_data, indent=2)
        s3.put_object(
            Bucket=settings.s3_bucket,
            Key=key,
            Body=body,
            ContentType="application/json",
        )
        logger.debug(f"[drafts] saved {key}")
        return True
    except Exception as exc:  # noqa: BLE001
        logger.error(f"[drafts] save {draft_id} failed: {exc}")
        return False


def _delete_draft(draft_id: str) -> None:
    """Delete draft from S3."""
    try:
        settings = get_settings()
        s3 = _s3_client()
        key = _draft_key(draft_id)
        s3.delete_object(Bucket=settings.s3_bucket, Key=key)
        logger.debug(f"[drafts] deleted {key}")
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"[drafts] delete {draft_id} failed: {exc}")


# ---------------------------------------------------------------------------
def create(
    pack: ContextPack,
    report: dict,
    name: str,
    kb_file_names: list[str] | None = None,
    user_inputs: dict | None = None,
) -> str:
    draft_id = uuid.uuid4().hex
    payload = {
        "draft_id": draft_id,
        "name": name,
        "pack": pack.model_dump(),
        "report": report,
        "kb_file_names": kb_file_names or [],
        "user_inputs": user_inputs or {},
    }
    _save_draft(draft_id, payload)
    return draft_id


def get(draft_id: str) -> dict | None:
    return _get_draft(draft_id)


def discard(draft_id: str) -> None:
    _delete_draft(draft_id)


def update(draft_id: str, pack_dict: dict) -> dict | None:
    """Overwrite the stored draft's "pack" with pack_dict (the edited pack) and
    return the updated draft record. Returns None if the draft does not exist."""
    draft = get(draft_id)
    if not draft:
        return None
    draft["pack"] = pack_dict
    _save_draft(draft_id, draft)
    return draft


# ---------------------------------------------------------------------------
def _next_version(industry: str, name: str) -> int:
    """v1 for a brand-new (industry, name), else highest existing + 1.

    Derives version from the pack ids already loaded from S3.
    (pack ids look like '<name>-vN').
    """
    s = get_settings()
    store = get_store()
    nums = []
    for pack in store._packs.values():  # loaded from S3 at startup / after saves
        if pack.meta.industry.lower() != industry.lower():
            continue
        pid = pack.meta.pack_id.lower()
        base, _, ver = pid.rpartition("-v")
        if base == name.lower() and ver.isdigit():
            nums.append(int(ver))
    return (max(nums) + 1) if nums else 1


def approve_stream(draft_id: str) -> Iterator[dict]:
    """Approve a draft, yielding progress events for each Approval_Step.

    Yields dict events. For each step it emits an "in_progress" event, then a
    terminal "succeeded"/"failed" event for that step. The final event has
    step "done" and either carries the committed pack (succeeded) or an error
    detail (failed).

    Step order (Requirement 4.2): s3 -> guardrail -> kb -> prompt.
    """
    draft = get(draft_id)
    if not draft:
        yield {"step": "done", "status": "failed", "detail": f"No draft '{draft_id}'"}
        return

    try:
        pack = ContextPack.model_validate(draft["pack"])
    except Exception as exc:  # noqa: BLE001
        logger.error(f"[drafts] Draft {draft_id} is not a valid pack: {exc}", exc_info=True)
        yield {"step": "done", "status": "failed", "detail": f"Invalid draft pack: {exc}"}
        return

    industry = pack.meta.industry
    name = (draft.get("name") or industry.lower()).strip()

    # ---- step 1: s3 (finalise meta + ontology merge + first save) ----------
    yield {"step": "s3", "status": "in_progress"}
    try:
        version = _next_version(industry, name)
        vstr = f"v{version}"
        pack.meta.version = vstr
        pack.meta.status = "active"
        pack.meta.pack_id = f"{name.lower()}-{vstr}"

        # industry-common ontology — append only missing terms (keeps history).
        pieces.merge_ontology(industry, [o.model_dump() for o in pack.ontology])

        # First save: the context-pack version file is the single source of truth.
        # persist_pack returns False if the real S3 write failed (e.g. expired
        # token) — surface that as a failed step rather than a false success.
        if not get_store().persist_pack(pack):
            raise RuntimeError("S3 write failed (check AWS credentials / bucket access)")
        logger.info(f"[drafts] Saved pack {pack.meta.pack_id} for {industry}/{name}")
        yield {"step": "s3", "status": "succeeded"}
    except Exception as exc:  # noqa: BLE001
        logger.error(f"[drafts] S3 step failed for {draft_id}: {exc}", exc_info=True)
        yield {"step": "s3", "status": "failed", "detail": str(exc)}
        yield {"step": "done", "status": "failed", "detail": f"S3 step failed: {exc}"}
        return

    had_failure = False
    failure_detail: str | None = None

    # ---- step 2: guardrail -------------------------------------------------
    yield {"step": "guardrail", "status": "in_progress"}
    try:
        gr = get_bedrock_manager().create_guardrail_from_pack(pack)
        pack.integrations.guardrail_id = gr["id"]
        pack.guardrails.guardrail_id = gr["id"]
        pack.guardrails.guardrail_version = gr["version"]
        yield {"step": "guardrail", "status": "succeeded"}
    except (GuardrailCreationError, BedrockResourcesError) as exc:
        had_failure = True
        failure_detail = f"guardrail step failed: {exc}"
        logger.warning(f"[drafts] {failure_detail}")
        yield {"step": "guardrail", "status": "failed", "detail": str(exc)}

    # ---- step 3: kb (NON-FATAL) --------------------------------------------
    yield {"step": "kb", "status": "in_progress"}
    kb_files = draft.get("kb_file_names") or []
    if not kb_files:
        yield {"step": "kb", "status": "succeeded", "detail": "no uploaded files"}
    else:
        # Real path: provision a managed Bedrock KB over the uploaded files in
        # S3 and ingest. Non-fatal: a KBError (or FAILED ingestion) must not
        # abort approval — we emit failed and continue, keeping any KB id set.
        try:
            from app.aws.bedrock_kb import create_managed_kb, ingest_kb, KBError
            res = create_managed_kb(industry, name)
            pack.integrations.knowledge_base_id = res["knowledge_base_id"]
            pack.integrations.data_source_id = res["data_source_id"]
            job_id = ingest_kb(res["knowledge_base_id"], res["data_source_id"])
            # ingest_kb returns the job_id (not the final status); ingestion runs
            # asynchronously in AWS and can take 5–10+ minutes. We emit success
            # once the job is queued (no long polling in the approval stream).
            yield {
                "step": "kb",
                "status": "succeeded",
                "detail": f"KB {res['knowledge_base_id']} created; ingestion job {job_id} queued (~5–10 min)",
            }
        except Exception as exc:  # noqa: BLE001 — KB failure must not block approval
            had_failure = True
            failure_detail = f"knowledge base step failed: {exc}"
            logger.warning(f"[drafts] {failure_detail}")
            yield {"step": "kb", "status": "failed", "detail": str(exc)}

    # ---- step 4: prompt ----------------------------------------------------
    yield {"step": "prompt", "status": "in_progress"}
    try:
        pr = get_bedrock_manager().create_prompt_from_pack(pack)
        pack.integrations.prompt_id = pr["id"]
        yield {"step": "prompt", "status": "succeeded"}
    except (PromptTemplateError, BedrockResourcesError) as exc:
        had_failure = True
        failure_detail = f"prompt step failed: {exc}"
        logger.warning(f"[drafts] {failure_detail}")
        yield {"step": "prompt", "status": "failed", "detail": str(exc)}

    # ---- finalise: re-save to persist resource ids, discard draft ----------
    try:
        # Re-persist so the resource ids written above reach S3. A False result
        # (real write rejected) is non-fatal but must be reported in the
        # terminal event rather than silently reported as success.
        if not get_store().persist_pack(pack):
            had_failure = True
            failure_detail = failure_detail or "failed to persist resource ids to S3"
            logger.warning(f"[drafts] final persist_pack failed for {pack.meta.pack_id}")
        discard(draft_id)
        logger.info(f"[drafts] Approved pack {pack.meta.pack_id} for {industry}/{name}")
    except Exception as exc:  # noqa: BLE001
        logger.error(f"[drafts] Final save failed for {draft_id}: {exc}", exc_info=True)
        yield {"step": "done", "status": "failed", "detail": f"Final save failed: {exc}"}
        return

    if had_failure:
        yield {
            "step": "done",
            "status": "failed",
            "detail": failure_detail or "one or more approval steps failed",
            "pack": pack.model_dump(),
        }
    else:
        yield {"step": "done", "status": "succeeded", "pack": pack.model_dump()}


def approve(draft_id: str) -> ContextPack | None:
    """Synchronous approval — drives approve_stream to completion and returns the
    committed pack (or None if the draft was missing). Kept for callers that do
    not need the streamed events."""
    committed: ContextPack | None = None
    missing = False
    for event in approve_stream(draft_id):
        if event.get("step") == "done":
            if event.get("status") == "succeeded" and event.get("pack"):
                committed = ContextPack.model_validate(event["pack"])
            elif event.get("pack"):
                # Failed step but pack persisted (Requirement 7.10 / 10.4).
                committed = ContextPack.model_validate(event["pack"])
            else:
                detail = event.get("detail") or ""
                if "No draft" in detail:
                    missing = True
    if missing:
        return None
    return committed
