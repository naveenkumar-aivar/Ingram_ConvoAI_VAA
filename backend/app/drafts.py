"""Draft storage for the Create-a-Context-Pack flow.

A generated pack is NOT committed until a human approves it. Until then it
lives at s3-data/_drafts/<draft_id>.json.

Approval (the only time real folders are written):
  1. version = v1 for a new (industry, name), else max+1 for that name.
  2. create the guardrail resource.
  3. write per-name source pieces: industry-docs/<industry>/<name>/<kind>/vN.json
  4. merge generated ontology terms into the industry-common ontology
     (append only missing terms, keep history).
  5. ingest uploaded KB files into Chroma (per industry+name).
  6. write the assembled pack: context-pack/<industry>/<name>/vN.json + latest.json
"""
from __future__ import annotations

import json
import uuid
from pathlib import Path

from app.config import get_settings
from app.models import ContextPack
from app.aws import pieces
from app.aws.storage import get_store
from app.aws.bedrock_resources import get_bedrock_manager, BedrockResourcesError


def _drafts_dir() -> Path:
    s = get_settings()
    d = (Path(__file__).resolve().parents[1] / s.local_s3_dir / "_drafts").resolve()
    d.mkdir(parents=True, exist_ok=True)
    return d


def _file(draft_id: str) -> Path:
    return _drafts_dir() / f"{draft_id}.json"


# ---------------------------------------------------------------------------
def create(pack: ContextPack, report: dict, name: str, kb_file_names: list[str] | None = None, 
           user_inputs: dict | None = None) -> str:
    draft_id = uuid.uuid4().hex
    payload = {
        "draft_id": draft_id,
        "name": name,
        "pack": pack.model_dump(),
        "report": report,
        "kb_file_names": kb_file_names or [],
        "user_inputs": user_inputs or {}
    }
    _file(draft_id).write_text(json.dumps(payload, indent=2))
    return draft_id


def get(draft_id: str) -> dict | None:
    f = _file(draft_id)
    if not f.exists():
        return None
    try:
        return json.loads(f.read_text())
    except Exception:  # noqa: BLE001
        return None


def discard(draft_id: str) -> None:
    f = _file(draft_id)
    if f.exists():
        f.unlink()


# ---------------------------------------------------------------------------
def _next_version(industry: str, name: str) -> int:
    """v1 for a brand-new (industry, name), else highest existing + 1.

    Works in both modes:
      - MOCK_MODE=true  -> scan the local context-pack/<industry>/<name>/ folder.
      - MOCK_MODE=false -> derive from the pack ids already loaded from S3
        (pack ids look like '<name>-vN').
    """
    s = get_settings()

    if not s.mock_mode:
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

    root = (Path(__file__).resolve().parents[1] / s.local_s3_dir
            / s.context_pack_prefix / industry.lower() / name.lower()).resolve()
    if not root.exists():
        return 1
    nums = []
    for p in root.glob("v*.json"):
        try:
            nums.append(int(p.stem.lstrip("v")))
        except ValueError:
            pass
    return (max(nums) + 1) if nums else 1


def approve(draft_id: str) -> ContextPack | None:
    import logging
    logger = logging.getLogger(__name__)
    
    try:
        draft = get(draft_id)
        if not draft:
            return None

        pack = ContextPack.model_validate(draft["pack"])
        industry = pack.meta.industry
        name = (draft.get("name") or industry.lower()).strip()
        version = _next_version(industry, name)
        vstr = f"v{version}"

        # finalise meta — pack_id = "<name>-vN"
        pack.meta.version = vstr
        pack.meta.status = "active"
        pack.meta.pack_id = f"{name.lower()}-{vstr}"

        # NOTE: Bedrock guardrail + prompt-management creation are intentionally
        # NOT done here yet. For now approval only PERSISTS everything to S3
        # (the source of truth). The guardrail text (denied topics, blocked
        # phrases, pii blocks) and prompts are still saved as JSON, so a real
        # Bedrock guardrail / prompt can be created from them later and its id
        # written back into pack.integrations.

        # --- STORAGE (the critical part) ------------------------------------
        # per-name source pieces (reusable next time)
        try:
            pieces.save_name_piece(industry, name, "persona", pack.persona.model_dump(), version)
            pieces.save_name_piece(industry, name, "prompts", {"system_prompt_lines": pack.domain_prompts}, version)
            pieces.save_name_piece(industry, name, "handoff", {"rules": [r.model_dump() for r in pack.escalation_rules]}, version)
            pieces.save_name_piece(industry, name, "guardrails", pack.guardrails.model_dump(), version)
        except Exception as e:
            logger.error(f"[drafts] Pieces save failed: {e}", exc_info=True)
            raise

        # industry-common ontology — append only missing terms (keeps history)
        try:
            pieces.merge_ontology(industry, [o.model_dump() for o in pack.ontology])
        except Exception as e:
            logger.error(f"[drafts] Ontology merge failed: {e}", exc_info=True)
            raise

        # ingest uploaded KB files into Chroma (per industry+name). Non-fatal:
        # a KB problem must not block storing the pack.
        kb_files = draft.get("kb_file_names") or []
        if kb_files:
            try:
                from app.aws.knowledge_base import ingest
                collection = ingest(industry, name, kb_files)
                pack.integrations.knowledge_base_id = collection
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"[drafts] KB ingest skipped: {exc}")

        # Create Bedrock resources (guardrails + prompt template) from stored pieces.
        # Non-fatal: Bedrock resource creation should not block pack approval.
        try:
            bedrock_mgr = get_bedrock_manager()
            
            # Load guardrails and prompts pieces from storage
            guardrails_piece = pieces.load_piece(industry, name, "guardrails", version)
            prompts_piece = pieces.load_piece(industry, name, "prompts", version)
            
            # Sync to Bedrock and update pack with resource IDs
            pack_dict = pack.model_dump()
            pack_dict = bedrock_mgr.sync_to_pack(
                industry=industry,
                usecase=name,
                pack_data=pack_dict,
                guardrails_piece=guardrails_piece,
                prompts_piece=prompts_piece,
            )
            # Reconstruct pack from updated dict
            pack = ContextPack.model_validate(pack_dict)
            logger.info(f"[drafts] Bedrock resources created for {industry}/{name}")
        except BedrockResourcesError as e:
            logger.warning(f"[drafts] Bedrock resource creation failed (non-fatal): {e}")
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[drafts] Unexpected error creating Bedrock resources: {e}")

        # assembled context pack (storage writes name-level file + latest.json)
        get_store().save_pack(pack)
        logger.info(f"[drafts] Approved pack {pack.meta.pack_id} for {industry}/{name}")

        discard(draft_id)
        return pack
    
    except Exception as e:
        logger.error(f"[drafts] Approval failed for {draft_id}: {e}", exc_info=True)
        raise
