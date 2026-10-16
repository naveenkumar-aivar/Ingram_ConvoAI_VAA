"""Context-pack storage.

Local mode (MOCK_MODE=true): the `s3-data` folder is the single source of
truth — industries are the subfolders of context-pack/, versions are the
<version>.json files. Nothing is seeded.

Real mode (MOCK_MODE=false): reads/writes JSON objects in S3 under
CONTEXT_PACK_PREFIX. The catalog reflects exactly what's in the bucket — an
empty bucket shows no industries (no seeds).

Public API (identical in both modes so callers never change):
    list_industries() -> list[dict]
    list_versions(industry_id) -> list[PackVersionItem]
    get_pack(pack_id) -> ContextPack | None
    save_pack(pack) -> ContextPack
    
New S3-backed API (not wired yet):
    save_industry_doc(industry, usecase, doc_type, data) -> bool
    load_industry_doc(industry, usecase, doc_type) -> dict | None
    save_context_pack(industry, usecase, pack_data) -> bool
    load_context_pack(industry, usecase) -> dict | None
    create_session(user_id, industry, usecase) -> str
    get_session(session_id) -> dict | None
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Optional, Dict, Any

from app.config import get_settings
from app.models import ContextPack, PackVersionItem

logger = logging.getLogger(__name__)


class _Store:
    def __init__(self) -> None:
        self.settings = get_settings()
        self._packs: dict[str, ContextPack] = {}
        # industry id -> display label, derived from the folder (so empty
        # industry folders still show up, with no versions).
        self._industries: dict[str, str] = {}
        self._s3 = None
        
        # New storage managers (not wired yet - activated via storage_router)
        self._storage_router = None

        if self.settings.mock_mode:
            # Local s3-data folder is the ONLY source of truth.
            self._load_from_local()
        else:
            import boto3
            self._s3 = boto3.client("s3", region_name=self.settings.aws_region)
            # Real S3 is the ONLY source of truth. The catalog reflects exactly
            # what's in the bucket — an empty bucket shows no industries.
            self._load_from_s3()
    
    def _get_storage_router(self):
        """Lazy load storage router (to avoid circular imports)"""
        if self._storage_router is None:
            try:
                from app.storage_router import get_storage_router
                self._storage_router = get_storage_router()
            except ImportError:
                logger.warning("StorageRouter not available")
        return self._storage_router

    # -- local-mode (s3-data folder) ------------------------------------------
    def _local_root(self) -> Path:
        # local_s3_dir is relative to the backend/ working dir.
        return (Path(__file__).resolve().parents[2] / self.settings.local_s3_dir).resolve()

    def _load_from_local(self) -> None:
        """Build the catalog from s3-data/context-pack/<industry>/<name>/vN.json.

        - Each subdirectory under context-pack/ is an industry (shown even if
          it has no packs yet).
        - Each <name> subfolder holds that assistant's versions; each
          <version>.json (except latest.json) is a context pack.
        The local folder is the ONLY source of truth; nothing is seeded."""
        root = self._local_root() / self.settings.context_pack_prefix
        if not root.exists():
            print(f"[storage] local context-pack folder not found at {root}")
            return

        for industry_dir in sorted(p for p in root.iterdir() if p.is_dir()):
            self._industries[industry_dir.name.lower()] = industry_dir.name.capitalize()
            for name_dir in sorted(p for p in industry_dir.iterdir() if p.is_dir()):
                for path in sorted(name_dir.glob("*.json")):
                    if path.stem == "latest":
                        continue
                    try:
                        pack = ContextPack.model_validate_json(path.read_text())
                        self._packs[pack.meta.pack_id] = pack
                        self._industries[pack.meta.industry.lower()] = pack.meta.industry
                    except Exception as exc:  # noqa: BLE001
                        print(f"[storage] skipped {path}: {exc}")

    def _pack_name(self, pack: ContextPack) -> str:
        """Derive the <name> folder from the pack_id ('<name>-vN')."""
        pid = pack.meta.pack_id
        return pid.rsplit("-v", 1)[0] if "-v" in pid else pid

    def _save_to_local(self, pack: ContextPack) -> None:
        root = (self._local_root() / self.settings.context_pack_prefix
                / pack.meta.industry.lower() / self._pack_name(pack))
        root.mkdir(parents=True, exist_ok=True)
        body = json.dumps(pack.model_dump(), indent=2)
        (root / f"{pack.meta.version}.json").write_text(body)
        if pack.meta.status == "active":
            (root / "latest.json").write_text(body)
        self._industries[pack.meta.industry.lower()] = pack.meta.industry

    # -- real-mode S3 helpers -------------------------------------------------
    def _s3_key(self, pack_id: str) -> str:
        return f"{self.settings.context_pack_prefix}{pack_id}.json"

    def _load_from_s3(self) -> None:
        """Load packs from S3 using the SAME nested layout as the writer:
        context-pack/<industry>/<usecase>/<version>.json.

        Uses the s3_manager (which already understands this layout) so the read
        and write paths never drift. Populates both the pack catalog and the
        industry list from whatever actually exists in the bucket."""
        try:
            router = self._get_storage_router()
            s3m = getattr(router, "s3_manager", None) if router else None
            if s3m is None:
                logger.warning("[storage] s3_manager unavailable; cannot load from S3")
                return

            objects = s3m.list_context_packs()  # [{industry, usecase, version, key, ...}]
            for obj in objects:
                key = obj["key"]
                # Skip the convenience 'latest.json' pointer; load real versions.
                if key.endswith("/latest.json"):
                    continue
                body = self._s3.get_object(Bucket=self.settings.s3_bucket, Key=key)["Body"].read()
                try:
                    pack = ContextPack.model_validate_json(body)
                except Exception as exc:  # noqa: BLE001
                    logger.warning(f"[storage] skipped malformed S3 pack {key}: {exc}")
                    continue
                self._packs[pack.meta.pack_id] = pack
                self._industries[pack.meta.industry.lower()] = pack.meta.industry

            logger.info(f"[storage] loaded {len(self._packs)} pack(s) from S3")
        except Exception as exc:  # noqa: BLE001 - startup resilience
            # Missing bucket/permissions should not crash the app.
            logger.warning(f"[storage] S3 load skipped: {exc}")

    # -- public API -----------------------------------------------------------
    def list_industries(self) -> list[dict]:
        """Industries to show in the UI. In both modes this is the in-memory
        catalog, which is populated from the source of truth at startup
        (local folders in mock mode, S3 in real mode) and kept up to date by
        save_pack(). So real mode reflects exactly what's in S3 — not the
        local demo industries."""
        return [{"id": iid, "label": label} for iid, label in sorted(self._industries.items())]

    def list_versions(self, industry_id: str) -> list[PackVersionItem]:
        out: list[PackVersionItem] = []
        for pack in self._packs.values():
            if pack.meta.industry.lower() == industry_id.lower():
                out.append(PackVersionItem(
                    id=pack.meta.pack_id, version=pack.meta.version, status=pack.meta.status,
                ))
        # active first, then newest-looking version string descending
        out.sort(key=lambda v: (v.status != "active", v.version), reverse=False)
        return out

    def get_pack(self, pack_id: str) -> ContextPack | None:
        return self._packs.get(pack_id)

    def name_exists(self, industry: str, name: str) -> bool:
        """A pack with this name already exists IN THIS INDUSTRY (duplicate
        check). The same name is allowed in a different industry."""
        name = (name or "").strip().lower()
        if not name:
            return False
        for pack in self._packs.values():
            if pack.meta.industry.lower() != industry.lower():
                continue
            pid = pack.meta.pack_id.lower()
            # pack ids look like "<name>-vN"; strip the version suffix.
            base = pid.rsplit("-v", 1)[0] if "-v" in pid else pid
            if base == name or pid == name:
                return True
        return False

    def save_pack(self, pack: ContextPack) -> ContextPack:
        # Update the in-memory catalog FIRST, so the new pack and its industry
        # are immediately visible to list_industries()/list_versions() in the
        # same process — regardless of which persistence path runs below.
        # (This is what makes a newly created industry show up in the chatbot
        # dropdown without a server restart.)
        self._packs[pack.meta.pack_id] = pack
        self._industries[pack.meta.industry.lower()] = pack.meta.industry

        # Persist. Route through storage_router for unified local/S3 handling.
        router = self._get_storage_router()
        if router:
            success = router.save_context_pack(
                industry=pack.meta.industry,
                usecase=self._pack_name(pack),
                pack_data=pack.model_dump(),
                version=pack.meta.version,
            )
            if not success:
                logger.warning(f"[storage] save_pack via router failed for {pack.meta.pack_id}")
        elif self.settings.mock_mode:
            # Fallback: write directly to the local s3-data folder.
            try:
                self._save_to_local(pack)
            except Exception as exc:  # noqa: BLE001
                logger.error(f"[storage] local save failed: {exc}")

        return pack
    
    # ========== NEW S3-BACKED API (Not wired yet) ==========
    # These methods use storage_router to route to local or S3
    
    def save_industry_doc(
        self,
        industry: str,
        usecase: str,
        doc_type: str,
        data: Dict[str, Any],
        version: str = "v1"
    ) -> bool:
        """Save approved industry document (persona, prompts, guardrails, handoff)"""
        router = self._get_storage_router()
        if router:
            return router.save_industry_doc(industry, usecase, doc_type, data, version)
        logger.error("StorageRouter not available")
        return False
    
    def load_industry_doc(
        self,
        industry: str,
        usecase: str,
        doc_type: str,
        version: str = "v1"
    ) -> Optional[Dict[str, Any]]:
        """Load approved industry document"""
        router = self._get_storage_router()
        if router:
            return router.load_industry_doc(industry, usecase, doc_type, version)
        logger.error("StorageRouter not available")
        return None
    
    def save_uploaded_file(
        self,
        industry: str,
        usecase: str,
        file_path: str,
        file_content: bytes
    ) -> bool:
        """Save user uploaded data file"""
        router = self._get_storage_router()
        if router:
            return router.save_uploaded_file(industry, usecase, file_path, file_content)
        logger.error("StorageRouter not available")
        return False
    
    def load_uploaded_file(
        self,
        industry: str,
        usecase: str,
        filename: str
    ) -> Optional[bytes]:
        """Load user uploaded data file"""
        router = self._get_storage_router()
        if router:
            return router.load_uploaded_file(industry, usecase, filename)
        logger.error("StorageRouter not available")
        return None
    
    def list_industry_docs(
        self,
        industry: str,
        usecase: str,
        doc_type: str
    ) -> list:
        """List versions of a specific document type"""
        router = self._get_storage_router()
        if router:
            return router.list_industry_docs(industry, usecase, doc_type)
        logger.error("StorageRouter not available")
        return []
    
    def save_context_pack_s3(
        self,
        industry: str,
        usecase: str,
        pack_data: Dict[str, Any],
        version: str = "v1"
    ) -> bool:
        """Save final approved context pack to S3"""
        router = self._get_storage_router()
        if router:
            return router.save_context_pack(industry, usecase, pack_data, version)
        logger.error("StorageRouter not available")
        return False
    
    def load_context_pack_s3(
        self,
        industry: str,
        usecase: str,
        version: str = "v1"
    ) -> Optional[Dict[str, Any]]:
        """Load final context pack from S3"""
        router = self._get_storage_router()
        if router:
            return router.load_context_pack(industry, usecase, version)
        logger.error("StorageRouter not available")
        return None
    
    def list_context_packs_s3(self, industry: str = None) -> list:
        """List context packs from S3"""
        router = self._get_storage_router()
        if router:
            return router.list_context_packs(industry)
        logger.error("StorageRouter not available")
        return []
    
    def create_session(
        self,
        user_id: str,
        industry: str,
        usecase: str,
        agent_name: str = None
    ) -> str:
        """Create new session"""
        router = self._get_storage_router()
        if router:
            return router.create_session(user_id, industry, usecase, agent_name)
        logger.error("StorageRouter not available")
        return ""
    
    def get_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        """Get session metadata"""
        router = self._get_storage_router()
        if router:
            return router.get_session(session_id)
        logger.error("StorageRouter not available")
        return None
    
    def update_session(
        self,
        session_id: str,
        updates: Dict[str, Any]
    ) -> bool:
        """Update session"""
        router = self._get_storage_router()
        if router:
            return router.update_session(session_id, updates)
        logger.error("StorageRouter not available")
        return False
    
    def add_session_message(
        self,
        session_id: str,
        role: str,
        content: str,
        message_type: str = "text",
        metadata: Dict[str, Any] = None
    ) -> bool:
        """Add message to session"""
        router = self._get_storage_router()
        if router:
            return router.add_message(session_id, role, content, message_type, metadata)
        logger.error("StorageRouter not available")
        return False
    
    def get_user_sessions(
        self,
        user_id: str,
        status: str = None
    ) -> list:
        """Get all sessions for user"""
        router = self._get_storage_router()
        if router:
            return router.get_user_sessions(user_id, status)
        logger.error("StorageRouter not available")
        return []
    
    def save_log(self, log_type: str, log_data: Dict[str, Any]) -> bool:
        """Save log entry"""
        router = self._get_storage_router()
        if router:
            return router.save_log(log_type, log_data)
        logger.error("StorageRouter not available")
        return False


_store: _Store | None = None


def get_store() -> _Store:
    global _store
    if _store is None:
        _store = _Store()
    return _store
