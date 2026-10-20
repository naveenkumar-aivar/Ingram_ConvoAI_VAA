"""Reusable source-piece library (S3 only).

Only the industry-level ontology is written under industry-docs/:
    industry-docs/<industry>/ontology/<version>.json          ← COMMON per industry

Ontology is shared across all names in an industry (vocabulary is common);
when a new pack is approved, only MISSING terms are appended and a new ontology
version is written (history kept).

Per-name pieces (persona, prompts, handoff, guardrails) are NOT written here;
they are sourced from the latest context-pack version file for (industry, name)
via load_pack_pieces(). Uploaded data files are handled by knowledge_base.

The pack-building agent uses this module to DISCOVER and REUSE existing pieces
and generate only what's missing ("don't build the wheel again").
"""
from __future__ import annotations

import json
import logging

from app.config import get_settings

logger = logging.getLogger(__name__)

# Per-name pieces.
NAME_PIECE_KINDS = ["persona", "prompts", "handoff", "guardrails"]
# Industry-common pieces.
COMMON_KINDS = ["ontology"]


# ---------------------------------------------------------------------------
# Relative key paths (S3 only)
# ---------------------------------------------------------------------------
def _rel_dir(industry: str, name: str, kind: str) -> str:
    """Relative directory (under industry-docs/) for a piece kind.
    Common kinds (ontology) are industry-level; the rest are per-name."""
    industry = (industry or "").strip().lower()
    name = (name or "").strip().lower()
    if kind in COMMON_KINDS:
        return f"{industry}/{kind}"
    return f"{industry}/{name}/{kind}"


def _s3_client():
    """Lazy get S3 client."""
    import boto3
    settings = get_settings()
    return boto3.client("s3", region_name=settings.aws_region)


def _read_json(rel_dir: str, version: int) -> dict | None:
    """Read industry-docs/<rel_dir>/v<version>.json from S3."""
    try:
        settings = get_settings()
        s3 = _s3_client()
        key = f"{settings.industry_docs_prefix}{rel_dir}/v{version}.json"
        response = s3.get_object(Bucket=settings.s3_bucket, Key=key)
        body = response["Body"].read()
        return json.loads(body)
    except Exception as exc:  # noqa: BLE001
        logger.debug(f"[pieces] read {rel_dir}/v{version} failed: {exc}")
        return None


def _write_json(rel_dir: str, version: int, content: dict) -> None:
    """Write industry-docs/<rel_dir>/v<version>.json to S3."""
    try:
        settings = get_settings()
        s3 = _s3_client()
        key = f"{settings.industry_docs_prefix}{rel_dir}/v{version}.json"
        body = json.dumps(content, indent=2)
        s3.put_object(
            Bucket=settings.s3_bucket,
            Key=key,
            Body=body,
            ContentType="application/json",
        )
        logger.debug(f"[pieces] wrote {key}")
    except Exception as exc:  # noqa: BLE001
        logger.error(f"[pieces] write {rel_dir}/v{version} failed: {exc}")
        raise


def _latest_num(rel_dir: str) -> int:
    """Highest existing version number under industry-docs/<rel_dir>/ (0 if none)."""
    try:
        settings = get_settings()
        s3 = _s3_client()
        prefix = f"{settings.industry_docs_prefix}{rel_dir}/"

        paginator = s3.get_paginator("list_objects_v2")
        pages = paginator.paginate(Bucket=settings.s3_bucket, Prefix=prefix)

        nums = []
        for page in pages:
            if "Contents" not in page:
                continue
            for obj in page["Contents"]:
                key = obj["Key"]
                filename = key.split("/")[-1]
                if filename.startswith("v") and filename.endswith(".json"):
                    try:
                        num = int(filename[1:-5])  # strip "v" prefix and ".json" suffix
                        nums.append(num)
                    except ValueError:
                        pass

        return max(nums) if nums else 0
    except Exception as exc:  # noqa: BLE001
        logger.debug(f"[pieces] latest_num {rel_dir} failed: {exc}")
        return 0


def _list_industries() -> list[str]:
    """Immediate industry subdirectories under industry-docs/."""
    try:
        settings = get_settings()
        s3 = _s3_client()
        prefix = settings.industry_docs_prefix

        paginator = s3.get_paginator("list_objects_v2")
        pages = paginator.paginate(
            Bucket=settings.s3_bucket, Prefix=prefix, Delimiter="/"
        )

        industries = []
        for page in pages:
            if "CommonPrefixes" in page:
                for item in page["CommonPrefixes"]:
                    # CommonPrefixes look like "industry-docs/<industry>/"
                    prefix_path = item["Prefix"]
                    parts = prefix_path.rstrip("/").split("/")
                    if len(parts) >= 2:
                        industry = parts[-1]
                        if industry:
                            industries.append(industry)

        return sorted(list(set(industries)))
    except Exception as exc:  # noqa: BLE001
        logger.debug(f"[pieces] list_industries failed: {exc}")
        return []


# ---------------------------------------------------------------------------
# Versions / reads
# ---------------------------------------------------------------------------
def load_piece(industry: str, name: str, kind: str, version: int | None = None) -> dict | None:
    rel = _rel_dir(industry, name, kind)
    v = version or _latest_num(rel)
    if v == 0:
        return None
    return _read_json(rel, v)


def load_pack_pieces(industry: str, name: str) -> dict[str, dict | None]:
    """Load reusable per-name pieces from the LATEST context-pack version file
    for (industry, name) — the single source of truth.

    Returns a dict shaped like existing_pieces() expects:
        {"persona": {...}|None,
         "prompts": {"system_prompt_lines": [...]}|None,
         "handoff": {"rules": [...]}|None,
         "guardrails": {...}|None}

    Ontology is NOT included here — it is industry-level and comes from the
    shared ontology file via load_piece(industry, name, "ontology").

    If no context-pack exists for (industry, name), every value is None."""
    pack = _latest_pack(industry, name)
    if pack is None:
        return {"persona": None, "prompts": None, "handoff": None, "guardrails": None}

    persona = pack.get("persona")
    domain_prompts = pack.get("domain_prompts") or []
    escalation_rules = pack.get("escalation_rules") or []
    guardrails = pack.get("guardrails")

    return {
        "persona": persona if persona else None,
        "prompts": {"system_prompt_lines": domain_prompts} if domain_prompts else None,
        "handoff": {"rules": escalation_rules} if escalation_rules else None,
        "guardrails": guardrails if guardrails else None,
    }


def _latest_pack(industry: str, name: str) -> dict | None:
    """Return the latest context-pack version (as a plain dict) for
    (industry, name) from the store, or None if none exists.

    Strategy: ask the store for pack ids shaped "<name>-vN" and pick the
    highest N; fall back to scanning the loaded packs by industry + name."""
    try:
        from app.aws.storage import get_store
        store = get_store()
    except Exception:  # noqa: BLE001
        return None

    name_l = name.strip().lower()
    industry_l = industry.strip().lower()

    best_ver = 0
    best_pack = None
    for pack in getattr(store, "_packs", {}).values():
        try:
            if pack.meta.industry.strip().lower() != industry_l:
                continue
            pid = pack.meta.pack_id.strip().lower()
            base, _, ver = pid.rpartition("-v")
            if base == name_l and ver.isdigit() and int(ver) > best_ver:
                best_ver = int(ver)
                best_pack = pack
        except Exception:  # noqa: BLE001
            continue

    if best_pack is None:
        return None
    try:
        return best_pack.model_dump()
    except Exception:  # noqa: BLE001
        return None


def existing_pieces(industry: str, name: str) -> dict[str, dict | None]:
    """All reusable pieces relevant to (industry, name): per-name pieces sourced
    from the latest context-pack version file (the single source of truth) +
    the industry-common ontology from the shared ontology file."""
    out: dict[str, dict | None] = load_pack_pieces(industry, name)
    out["ontology"] = load_piece(industry, name, "ontology")  # industry-level common
    return out


def reuse_report(industry: str, name: str) -> dict[str, bool]:
    return {k: v is not None for k, v in existing_pieces(industry, name).items()}


def industries_with_pieces() -> list[str]:
    """Industries that have reusable source pieces under industry-docs/.
    (Every subdirectory here is an industry.)"""
    return _list_industries()


# ---------------------------------------------------------------------------
# Writes (only on draft approval)
# ---------------------------------------------------------------------------
def merge_ontology(industry: str, new_terms: list[dict]) -> int:
    """Append only MISSING terms to the industry-common ontology, keeping
    history as a new version. Terms are de-duplicated by lowercased term name
    and the result is sorted alphabetically. Returns the new version number.
    If nothing changes, returns the current version (no new file)."""
    rel = _rel_dir(industry, "", "ontology")
    cur_v = _latest_num(rel)
    existing_terms: list[dict] = []
    if cur_v:
        cur = _read_json(rel, cur_v)
        existing_terms = (cur or {}).get("terms", [])

    by_key = {t.get("term", "").strip().lower(): t for t in existing_terms if t.get("term")}
    added = False
    for t in new_terms:
        key = (t.get("term") or "").strip().lower()
        if key and key not in by_key:
            by_key[key] = t
            added = True

    if not added and cur_v:
        return cur_v  # nothing new → keep current version

    merged = sorted(by_key.values(), key=lambda t: t.get("term", "").lower())
    new_v = (cur_v + 1) if cur_v else 1
    _write_json(rel, new_v, {"terms": merged})
    return new_v
