"""Knowledge base — local Chroma vector store (swappable to Bedrock KB later).

Storage layout (identical keys in local s3-data and real S3):
  industry-docs/<industry>/<name>/uploaded_data/  — original uploaded files (source of truth)
  kb-data/_chroma/                                 — Chroma persistent store (chunked + embedded)

Flow:
  save_upload() -> stores raw file to industry-docs/<industry>/<name>/uploaded_data/
                   (local folder when MOCK_MODE=true, real S3 when false)
  ingest()      -> reads those raw files (local OR S3), chunks, embeds to Chroma
  retrieve()    -> query Chroma for top matching chunks

The raw-file store follows MOCK_MODE (local vs S3). The Chroma vector index is
kept local in both modes for now (kb-data/_chroma); it can be swapped for a
Bedrock Knowledge Base later without changing this interface.
"""
from __future__ import annotations

from pathlib import Path

from app.config import get_settings

_CHUNK_CHARS = 900
_OVERLAP = 150
_client = None


def _kb_root() -> Path:
    s = get_settings()
    return (Path(__file__).resolve().parents[2] / s.kb_data_dir).resolve()


def _s3_root() -> Path:
    s = get_settings()
    return (Path(__file__).resolve().parents[2] / s.local_s3_dir).resolve()


def _s3():
    """Return the s3_manager (via storage_router) or None if unavailable."""
    try:
        from app.storage_router import get_storage_router
        return getattr(get_storage_router(), "s3_manager", None)
    except Exception:  # noqa: BLE001
        return None


def uploaded_data_dir(industry: str, name: str) -> Path:
    """LOCAL raw-file store: s3-data/industry-docs/<industry>/<name>/uploaded_data/.

    Raw uploads live in an `uploaded_data/` subfolder so they stay separate from
    the generated source pieces (persona/prompts/handoff/guardrails/ontology)
    that sit directly under <name>/. The same relative key is used in real S3."""
    s = get_settings()
    d = _s3_root() / s.industry_docs_prefix / industry.lower() / name.lower() / "uploaded_data"
    d.mkdir(parents=True, exist_ok=True)
    return d


def save_upload(industry: str, name: str, filename: str, data: bytes) -> str:
    """Persist an uploaded file to industry-docs/<industry>/<name>/uploaded_data/.
    This is the source of truth for raw files.
    - MOCK_MODE=true  -> writes to the local s3-data/ folder.
    - MOCK_MODE=false -> writes to real S3 via s3_manager.
    Returns the stored filename (sanitised)."""
    safe = Path(filename).name
    if get_settings().mock_mode:
        (uploaded_data_dir(industry, name) / safe).write_bytes(data)
        return safe
    s3 = _s3()
    if s3 is None:
        raise RuntimeError("s3_manager unavailable; cannot upload file to S3")
    # usecase == assistant name; s3_manager builds the uploaded_data/ key.
    s3.save_uploaded_file(industry.lower(), name.lower(), safe, data)
    return safe


def _collection_name(industry: str, name: str) -> str:
    return f"{industry.lower()}__{name.lower()}".replace(" ", "-")


def _chroma():
    """Persistent Chroma client (stored under kb-data/_chroma)."""
    global _client
    if _client is None:
        import chromadb
        _client = chromadb.PersistentClient(path=str(_kb_root() / "_chroma"))
    return _client


def _read_text(path: Path) -> str:
    """Best-effort text extraction. .md/.txt/.json read directly; other types
    are read as UTF-8 (good enough for the demo). PDFs/docx would need a parser
    in production — flagged, not silently wrong."""
    try:
        return path.read_text(encoding="utf-8", errors="ignore")
    except Exception:  # noqa: BLE001
        return ""


def _decode(data: bytes) -> str:
    """Best-effort UTF-8 decode of raw file bytes (same leniency as _read_text)."""
    try:
        return data.decode("utf-8", errors="ignore")
    except Exception:  # noqa: BLE001
        return ""


def _load_source_texts(industry: str, name: str, file_names: list[str] | None) -> list[tuple[str, str]]:
    """Return [(filename, text)] for the raw uploaded files, reading from the
    local folder (MOCK_MODE=true) or real S3 (MOCK_MODE=false). If file_names is
    given, only those are read; otherwise all files in uploaded_data/ are used."""
    out: list[tuple[str, str]] = []

    if get_settings().mock_mode:
        folder = uploaded_data_dir(industry, name)
        if file_names:
            paths = [folder / fn for fn in file_names if (folder / fn).exists()]
        else:
            paths = [p for p in folder.iterdir() if p.is_file()] if folder.exists() else []
        for p in paths:
            out.append((p.name, _read_text(p)))
        return out

    # Real S3
    s3 = _s3()
    if s3 is None:
        return out
    names = file_names or s3.list_uploaded_files(industry.lower(), name.lower())
    for fn in names:
        data = s3.load_uploaded_file(industry.lower(), name.lower(), fn)
        if data is not None:
            out.append((fn, _decode(data)))
    return out


def _chunk(text: str) -> list[str]:
    text = text.strip()
    if not text:
        return []
    chunks, i = [], 0
    while i < len(text):
        chunks.append(text[i : i + _CHUNK_CHARS])
        i += _CHUNK_CHARS - _OVERLAP
    return chunks


# ---------------------------------------------------------------------------
def ingest(industry: str, name: str, file_names: list[str] | None = None) -> str:
    """Read raw files from industry-docs/<industry>/<name>/uploaded_data/ (local
    folder OR real S3 depending on MOCK_MODE), chunk them, and embed into a
    Chroma collection (stored in kb-data/_chroma). Returns the collection name
    (used as knowledge_base_id)."""
    coll_name = _collection_name(industry, name)
    client = _chroma()

    # Fresh collection each ingest (idempotent re-index).
    try:
        client.delete_collection(coll_name)
    except Exception:  # noqa: BLE001
        pass
    coll = client.get_or_create_collection(coll_name)

    docs, ids, metas = [], [], []
    for filename, text in _load_source_texts(industry, name, file_names):
        for idx, ch in enumerate(_chunk(text)):
            docs.append(ch)
            ids.append(f"{filename}-{idx}")
            metas.append({"source": filename})

    if docs:
        coll.add(documents=docs, ids=ids, metadatas=metas)
    return coll_name


def retrieve(industry: str, name: str, query: str, top_k: int = 3) -> list[str]:
    """Return the top matching chunks for a query (empty list if none)."""
    try:
        coll = _chroma().get_collection(_collection_name(industry, name))
    except Exception:  # noqa: BLE001
        return []
    if coll.count() == 0:
        return []
    res = coll.query(query_texts=[query], n_results=min(top_k, coll.count()))
    docs = res.get("documents") or [[]]
    return docs[0]
