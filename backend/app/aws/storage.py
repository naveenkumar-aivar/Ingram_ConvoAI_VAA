"""Context-pack storage (S3 only).

Reads/writes JSON objects in S3 under CONTEXT_PACK_PREFIX. The catalog
reflects exactly what's in the bucket — an empty bucket shows no industries.

Public API:
    list_industries() -> list[dict]
    list_versions(industry_id) -> list[PackVersionItem]
    get_pack(pack_id) -> ContextPack | None
    save_pack(pack) -> ContextPack
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

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

        # Real S3 is the ONLY source of truth. The catalog reflects exactly
        # what's in the bucket — an empty bucket shows no industries.
        import boto3
        self._s3 = boto3.client("s3", region_name=self.settings.aws_region)
        self._load_from_s3()

    def _pack_name(self, pack: ContextPack) -> str:
        """Derive the <name> folder from the pack_id ('<name>-vN')."""
        pid = pack.meta.pack_id
        return pid.rsplit("-v", 1)[0] if "-v" in pid else pid

    # -- real-mode S3 helpers -------------------------------------------------
    def _s3_key(self, pack_id: str) -> str:
        return f"{self.settings.context_pack_prefix}{pack_id}.json"

    def _load_from_s3(self) -> None:
        """Load packs from S3 by listing all context-pack/*.json files.

        The catalog reflects exactly what's in the bucket — an empty bucket
        shows no industries.
        """
        try:
            # List all objects under context-pack/ prefix
            paginator = self._s3.get_paginator("list_objects_v2")
            pages = paginator.paginate(
                Bucket=self.settings.s3_bucket,
                Prefix=self.settings.context_pack_prefix,
            )

            for page in pages:
                if "Contents" not in page:
                    continue

                for obj in page["Contents"]:
                    key = obj["Key"]
                    # Skip folders and latest.json pointers
                    if key.endswith("/") or key.endswith("/latest.json"):
                        continue
                    if not key.endswith(".json"):
                        continue

                    try:
                        body = self._s3.get_object(Bucket=self.settings.s3_bucket, Key=key)[
                            "Body"
                        ].read()
                        pack = ContextPack.model_validate_json(body)
                        self._packs[pack.meta.pack_id] = pack
                        self._industries[pack.meta.industry.lower()] = pack.meta.industry
                    except Exception as exc:  # noqa: BLE001
                        logger.warning(f"[storage] skipped malformed S3 pack {key}: {exc}")
                        continue

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

    def list_agent_names(self, industry_id: str) -> list[str]:
        """Return sorted list of unique agent names (pack names without version)
        for the given industry. Used for the "Agent Name" dropdown."""
        names = set()
        for pack in self._packs.values():
            if pack.meta.industry.lower() == industry_id.lower():
                # Extract the agent name from pack_id: "assist-v1" -> "assist"
                pack_id = pack.meta.pack_id
                if '-v' in pack_id:
                    name = pack_id[:pack_id.rfind('-v')]
                else:
                    name = pack_id
                if name:
                    names.add(name)
        return sorted(list(names))

    def list_versions(self, industry_id: str, agent_name: str = '') -> list[PackVersionItem]:
        """List versions for an industry, optionally filtered by agent_name.
        If agent_name is empty, returns all versions for the industry (legacy behavior)."""
        out: list[PackVersionItem] = []
        for pack in self._packs.values():
            if pack.meta.industry.lower() == industry_id.lower():
                # If agent_name is specified, filter to only that agent
                if agent_name:
                    pack_id = pack.meta.pack_id
                    name = pack_id[:pack_id.rfind('-v')] if '-v' in pack_id else pack_id
                    if name.lower() != agent_name.lower():
                        continue
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

    def persist_pack(self, pack: ContextPack) -> bool:
        """Persist the pack to S3 and return True ONLY if it was actually written.

        The in-memory catalog is always updated first (so the pack is visible in
        this process regardless of the persistence outcome). The returned bool
        reflects the real S3 write result: False when S3 rejected the write
        (e.g. expired token / missing bucket).

        Callers that need to surface a storage failure to the user (e.g. the
        approval SSE stream) should branch on this boolean.
        """
        # Update the in-memory catalog FIRST, so the new pack and its industry
        # are immediately visible to list_industries()/list_versions() in the
        # same process.
        self._packs[pack.meta.pack_id] = pack
        self._industries[pack.meta.industry.lower()] = pack.meta.industry

        try:
            # Write to S3: context-pack/<industry>/<pack_name>/<version>.json
            pack_name = self._pack_name(pack)
            key = (
                f"{self.settings.context_pack_prefix}"
                f"{pack.meta.industry.lower()}/{pack_name}/{pack.meta.version}.json"
            )
            body = json.dumps(pack.model_dump(), indent=2)
            self._s3.put_object(
                Bucket=self.settings.s3_bucket,
                Key=key,
                Body=body,
                ContentType="application/json",
            )

            # Also write latest.json pointer if this is the active version
            if pack.meta.status == "active":
                latest_key = (
                    f"{self.settings.context_pack_prefix}"
                    f"{pack.meta.industry.lower()}/{pack_name}/latest.json"
                )
                self._s3.put_object(
                    Bucket=self.settings.s3_bucket,
                    Key=latest_key,
                    Body=body,
                    ContentType="application/json",
                )

            logger.info(f"[storage] persisted pack {pack.meta.pack_id} to S3 ({key})")
            return True
        except Exception as exc:  # noqa: BLE001
            logger.error(f"[storage] S3 write failed for {pack.meta.pack_id}: {exc}")
            return False

    def save_pack(self, pack: ContextPack) -> ContextPack:
        """Persist the pack and return it (unchanged signature for callers that
        do not need the success flag). Use persist_pack() when you need to know
        whether the write actually succeeded."""
        self.persist_pack(pack)
        return pack


_store: _Store | None = None


def get_store() -> _Store:
    global _store
    if _store is None:
        _store = _Store()
    return _store
