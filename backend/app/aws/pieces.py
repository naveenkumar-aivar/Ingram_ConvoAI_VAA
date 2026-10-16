"""Reusable source-piece library (name-level layout).

Layout (identical in local s3-data and real S3) — everything lives under the
industry-docs/ prefix so source pieces never collide with context-pack/,
sessions/, logs/, etc.:
    industry-docs/<industry>/ontology/<version>.json          ← COMMON per industry
    industry-docs/<industry>/<name>/persona/<version>.json    ← per name
    industry-docs/<industry>/<name>/prompts/<version>.json
    industry-docs/<industry>/<name>/handoff/<version>.json
    industry-docs/<industry>/<name>/guardrails/<version>.json

Ontology is shared across all names in an industry (vocabulary is common);
when a new pack is approved, only MISSING terms are appended and a new ontology
version is written (history kept). Persona/prompts/handoff/guardrails are
per-name so different assistants in the same industry can differ.

Storage backend is chosen by MOCK_MODE:
  - MOCK_MODE=true  -> local filesystem under s3-data/industry-docs/
  - MOCK_MODE=false -> real S3 under <bucket>/industry-docs/ (via s3_manager)
Both use the SAME relative key paths, so S3 mirrors the local structure.

The pack-building agent uses this module to DISCOVER and REUSE existing pieces
and generate only what's missing ("don't build the wheel again").
"""
from __future__ import annotations

import json
from pathlib import Path

from app.config import get_settings

# Per-name pieces.
NAME_PIECE_KINDS = ["persona", "prompts", "handoff", "guardrails"]
# Industry-common pieces.
COMMON_KINDS = ["ontology"]


# ---------------------------------------------------------------------------
# Relative key paths (shared by both backends)
# ---------------------------------------------------------------------------
def _rel_dir(industry: str, name: str, kind: str) -> str:
    """Relative directory (under industry-docs/) for a piece kind.
    Common kinds (ontology) are industry-level; the rest are per-name."""
    if kind in COMMON_KINDS:
        return f"{industry.lower()}/{kind}"
    return f"{industry.lower()}/{name.lower()}/{kind}"


# ---------------------------------------------------------------------------
# Backend primitives — dispatch to local filesystem or S3 by MOCK_MODE
# ---------------------------------------------------------------------------
def _local_root() -> Path:
    s = get_settings()
    prefix = s.industry_docs_prefix.strip("/")
    return (Path(__file__).resolve().parents[2] / s.local_s3_dir / prefix).resolve()


def _s3():
    """Return the s3_manager (via storage_router) or None if unavailable."""
    try:
        from app.storage_router import get_storage_router
        return getattr(get_storage_router(), "s3_manager", None)
    except Exception:  # noqa: BLE001
        return None


def _read_json(rel_dir: str, version: int) -> dict | None:
    """Read industry-docs/<rel_dir>/v<version>.json."""
    if get_settings().mock_mode:
        f = _local_root() / rel_dir / f"v{version}.json"
        if not f.exists():
            return None
        try:
            return json.loads(f.read_text())
        except Exception:  # noqa: BLE001
            return None
    s3 = _s3()
    if s3 is None:
        return None
    return s3.get_industry_json(f"{rel_dir}/v{version}.json")


def _write_json(rel_dir: str, version: int, content: dict) -> None:
    """Write industry-docs/<rel_dir>/v<version>.json."""
    if get_settings().mock_mode:
        d = _local_root() / rel_dir
        d.mkdir(parents=True, exist_ok=True)
        (d / f"v{version}.json").write_text(json.dumps(content, indent=2))
        return
    s3 = _s3()
    if s3 is None:
        raise RuntimeError("s3_manager unavailable; cannot write piece to S3")
    s3.put_industry_json(f"{rel_dir}/v{version}.json", content)


def _latest_num(rel_dir: str) -> int:
    """Highest existing version number under industry-docs/<rel_dir>/ (0 if none)."""
    names: list[str] = []
    if get_settings().mock_mode:
        d = _local_root() / rel_dir
        if d.exists():
            names = [p.name for p in d.glob("v*.json")]
    else:
        s3 = _s3()
        if s3 is not None:
            names = s3.list_industry_dir(rel_dir)

    nums = []
    for n in names:
        stem = n[:-5] if n.endswith(".json") else n  # strip .json
        if stem.startswith("v"):
            try:
                nums.append(int(stem[1:]))
            except ValueError:
                pass
    return max(nums) if nums else 0


def _list_industries() -> list[str]:
    """Immediate industry subdirectories under industry-docs/."""
    if get_settings().mock_mode:
        root = _local_root()
        return sorted(d.name for d in (root.iterdir() if root.exists() else []) if d.is_dir())
    s3 = _s3()
    if s3 is None:
        return []
    return sorted(s3.list_industry_subdirs(""))


# ---------------------------------------------------------------------------
# Versions / reads
# ---------------------------------------------------------------------------
def load_piece(industry: str, name: str, kind: str, version: int | None = None) -> dict | None:
    rel = _rel_dir(industry, name, kind)
    v = version or _latest_num(rel)
    if v == 0:
        return None
    return _read_json(rel, v)


def existing_pieces(industry: str, name: str) -> dict[str, dict | None]:
    """All reusable pieces relevant to (industry, name): per-name pieces +
    the industry-common ontology."""
    out: dict[str, dict | None] = {k: load_piece(industry, name, k) for k in NAME_PIECE_KINDS}
    out["ontology"] = load_piece(industry, name, "ontology")  # common
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
def save_name_piece(industry: str, name: str, kind: str, content: dict, version: int) -> None:
    _write_json(_rel_dir(industry, name, kind), version, content)


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
