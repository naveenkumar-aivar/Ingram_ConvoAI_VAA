"""Conversation sessions (chat memory using Amazon DynamoDB).

Stores per-session conversation history so the chatbot holds real multi-turn
dialogues. Each session stores TWO independent histories — one for the generic
("before") bot and one for the adapted ("after") bot — because they are
different conversations shown side by side.

Storage: Amazon DynamoDB (boto3), one item per session in a self-provisioned
table (`vaa-sessions`) with a TTL.

Public API:
    get_sessions() -> store
      store.new_session() -> str
      store.get_history(session_id, side) -> list[dict]   # [{"role","text"}]
      store.append_turn(session_id, side, user_text, assistant_text) -> None
      store.reset(session_id) -> None
      store.recent(limit=5) -> list[dict]                 # [{session_id,preview,turns}]
      store.full_history(session_id) -> dict              # {"generic":[...],"adapted":[...]}

This is deliberately simple and provider-agnostic. When the agent later moves
to AgentCore, this store is swapped for AgentCore Memory with no change to
callers.
"""
from __future__ import annotations

import json
import logging
import time
import uuid
from decimal import Decimal
from pathlib import Path

from agent.config import get_settings

logger = logging.getLogger(__name__)

# Trim stored history so prompts stay small (last N turns per side).
# Token optimization: Keep only last N turns to reduce input tokens
MAX_TURNS_PER_SIDE = 10  # Reduced from 20 for better token savings
# Each exchange is two entries (user + assistant).
_MAX_ENTRIES_PER_SIDE = MAX_TURNS_PER_SIDE * 2


def _plain(obj):
    """Recursively convert DynamoDB Decimals to int/float so results are JSON-safe."""
    if isinstance(obj, Decimal):
        return int(obj) if obj == obj.to_integral_value() else float(obj)
    if isinstance(obj, dict):
        return {k: _plain(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_plain(v) for v in obj]
    return obj


def _preview_and_turns(data: dict) -> tuple[str, int]:
    """Compute the sidebar preview (first user message on the richer side) and
    the user-turn count. Prefers the adapted side, falls back to generic."""
    adapted = data.get("adapted") or []
    generic = data.get("generic") or []
    turns = adapted if adapted else generic
    preview = next((t.get("text", "") for t in turns if t.get("role") == "user"), "")
    user_turns = sum(1 for t in turns if t.get("role") == "user")
    return preview[:60], user_turns


# ===========================================================================
# DynamoDB backend — one item per session.
# ===========================================================================
# Checked once per process: whether the table has been ensured to exist.
_table_ensured = False


class _DynamoSessionStore:
    def __init__(self) -> None:
        self.settings = get_settings()
        self._table_name = self.settings.sessions_table_name
        self._region = self.settings.aws_region
        # Lazily created boto3 resources (import boto3 lazily so mock mode never
        # requires it).
        self._resource = None
        self._table = None

    # -- clients / provisioning ---------------------------------------------
    def _get_table(self):
        if self._table is None:
            import boto3

            self._resource = boto3.resource("dynamodb", region_name=self._region)
            self._table = self._resource.Table(self._table_name)
        self._ensure_table()
        return self._table

    def _ensure_table(self) -> None:
        """Idempotently ensure the sessions table exists (create on first use,
        enable TTL). Mirrors bedrock_kb.ensure_kb_role: checked once per
        process via a module-global flag."""
        global _table_ensured
        if _table_ensured:
            return
        from botocore.exceptions import ClientError

        client = self._resource.meta.client
        try:
            client.describe_table(TableName=self._table_name)
            _table_ensured = True
            return
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") != "ResourceNotFoundException":
                raise

        # Table missing — create it (PAY_PER_REQUEST, single hash key).
        logger.info(f"[sessions] creating DynamoDB table {self._table_name}")
        client.create_table(
            TableName=self._table_name,
            BillingMode="PAY_PER_REQUEST",
            KeySchema=[{"AttributeName": "session_id", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "session_id", "AttributeType": "S"}],
        )
        client.get_waiter("table_exists").wait(TableName=self._table_name)

        # Enable TTL on the expires_at attribute (swallow "already enabled").
        try:
            client.update_time_to_live(
                TableName=self._table_name,
                TimeToLiveSpecification={"Enabled": True, "AttributeName": "expires_at"},
            )
        except ClientError as exc:  # noqa: BLE001 — fine if TTL is already on
            logger.warning(f"[sessions] update_time_to_live skipped: {exc}")

        _table_ensured = True

    # -- item helpers --------------------------------------------------------
    def _now(self) -> int:
        return int(time.time())

    def _expires_at(self) -> int:
        return self._now() + self.settings.session_ttl_days * 86400

    def _empty_item(self, session_id: str, pack_id: str = "", industry: str = "") -> dict:
        now = self._now()
        return {
            "session_id": session_id,
            "generic": [],
            "adapted": [],
            "updated_at": now,
            "preview": "",
            "user_turns": 0,
            "expires_at": self._expires_at(),
            "pack_id": pack_id,
            "industry": industry,
            "created_at": now,
            "total_input_tokens": 0,
            "total_output_tokens": 0,
        }

    def _get_item(self, session_id: str) -> dict | None:
        """Fetch a session item; returns None on miss and on any error (reads
        must never break chat)."""
        if not session_id:
            return None
        try:
            resp = self._get_table().get_item(Key={"session_id": session_id})
            return resp.get("Item")
        except Exception as exc:  # noqa: BLE001 — reads must never raise
            logger.warning(f"[sessions] get_item failed (non-fatal): {exc}")
            return None

    # -- public API ----------------------------------------------------------
    def new_session(self, pack_id: str = "", industry: str = "") -> str:
        sid = uuid.uuid4().hex
        try:
            self._get_table().put_item(Item=self._empty_item(sid, pack_id=pack_id, industry=industry))
        except Exception as exc:  # noqa: BLE001 — a failed persist must not break
            logger.warning(f"[sessions] new_session put failed (non-fatal): {exc}")
        return sid

    def get_history(self, session_id: str, side: str, max_turns: int | None = None) -> list[dict]:
        """Return prior turns for one side ('generic' or 'adapted').
        
        Token optimization: Trim to max_turns exchanges to keep input tokens under control.
        If max_turns is None, uses MAX_TURNS_PER_SIDE (default 10 turns = 20 entries).
        
        Args:
            session_id: session identifier
            side: "generic" or "adapted"
            max_turns: max exchanges (None = use MAX_TURNS_PER_SIDE)
        
        Returns:
            list[dict] with {role, text}, trimmed to max_turns exchanges
        """
        if max_turns is None:
            max_turns = MAX_TURNS_PER_SIDE
        
        item = self._get_item(session_id)
        if not item:
            return []
        
        hist = list(item.get(side) or [])
        max_entries = max_turns * 2  # Each turn is 2 entries (user + assistant)
        
        if len(hist) > max_entries:
            hist = hist[-max_entries:]
            logger.debug(f"[sessions] Trimmed history to last {max_turns} turns ({len(hist)} entries)")
        
        return hist

    def append_turn(self, session_id: str, side: str, user_text: str, assistant_text: str, input_tokens: int = 0, output_tokens: int = 0) -> None:
        """Add a turn to the conversation history with optional token tracking.
        
        Token optimization: Automatically trims history to MAX_TURNS_PER_SIDE exchanges
        (20 entries total: 10 user + 10 assistant) to keep input tokens under control.
        
        Args:
            session_id: session identifier
            side: "generic" or "adapted"
            user_text: user message
            assistant_text: assistant response
            input_tokens: input tokens used in this turn (optional)
            output_tokens: output tokens used in this turn (optional)
        """
        if not session_id:
            return
        item = self._get_item(session_id) or self._empty_item(session_id)
        
        # Ensure backward compatibility: add missing metadata fields if they don't exist
        if "pack_id" not in item:
            item["pack_id"] = ""
        if "industry" not in item:
            item["industry"] = ""
        if "created_at" not in item:
            item["created_at"] = self._now()
        if "total_input_tokens" not in item:
            item["total_input_tokens"] = 0
        if "total_output_tokens" not in item:
            item["total_output_tokens"] = 0
        
        hist = list(item.get(side) or [])
        now = self._now()
        hist.append({"role": "user", "text": user_text, "timestamp": now})
        hist.append({"role": "assistant", "text": assistant_text, "input_tokens": input_tokens, "output_tokens": output_tokens, "timestamp": now})
        # keep only the last MAX_TURNS_PER_SIDE exchanges (2 entries each)
        # Token optimization: Aggressive trimming to reduce input tokens in Bedrock calls
        if len(hist) > _MAX_ENTRIES_PER_SIDE:
            hist = hist[-_MAX_ENTRIES_PER_SIDE:]
            logger.debug(f"[sessions] Trimmed {side} history to last {MAX_TURNS_PER_SIDE} turns")
        item[side] = hist
        item["generic"] = list(item.get("generic") or [])
        item["adapted"] = list(item.get("adapted") or [])
        
        # Accumulate total input/output tokens
        item["total_input_tokens"] = int(item["total_input_tokens"]) + input_tokens
        item["total_output_tokens"] = int(item["total_output_tokens"]) + output_tokens
        
        preview, user_turns = _preview_and_turns(item)
        item["preview"] = preview
        item["user_turns"] = user_turns
        item["updated_at"] = now
        item["expires_at"] = self._expires_at()
        item["session_id"] = session_id
        try:
            self._get_table().put_item(Item=item)
            logger.debug(f"[sessions] Appended turn to {session_id} ({side}), history now {len(hist)} entries, cumulative tokens: {item['total_input_tokens']} input, {item['total_output_tokens']} output")
        except Exception as exc:  # noqa: BLE001 — a failed persist must not break
            logger.warning(f"[sessions] append_turn put failed (non-fatal): {exc}")

    def reset(self, session_id: str) -> None:
        if not session_id:
            return
        try:
            self._get_table().delete_item(Key={"session_id": session_id})
        except Exception as exc:  # noqa: BLE001 — a failed delete must not break
            logger.warning(f"[sessions] reset delete failed (non-fatal): {exc}")

    def recent(self, limit: int = 5) -> list[dict]:
        """Most-recently-updated sessions with a short preview and metadata. A full Scan is
        acceptable at demo scale; a GSI on (constant, updated_at) would be the
        production choice to avoid scanning the whole table."""
        try:
            resp = self._get_table().scan()
            items = resp.get("Items", [])
        except Exception as exc:  # noqa: BLE001 — reads must never raise
            logger.warning(f"[sessions] recent scan failed (non-fatal): {exc}")
            return []
        out: list[dict] = []
        for item in items:
            preview = item.get("preview") or ""
            # DynamoDB numbers come back as Decimal — cast counts to int.
            turns = int(item.get("user_turns") or 0)
            if not preview or turns <= 0:
                continue  # skip empty sessions
            out.append(
                {
                    "session_id": item.get("session_id"),
                    "preview": preview[:60],
                    "turns": turns,
                    "pack_id": item.get("pack_id", ""),
                    "industry": item.get("industry", ""),
                    "created_at": int(item.get("created_at") or 0),
                    "total_input_tokens": int(item.get("total_input_tokens") or 0),
                    "total_output_tokens": int(item.get("total_output_tokens") or 0),
                    "_updated_at": int(item.get("updated_at") or 0),
                }
            )
        out.sort(key=lambda d: d.pop("_updated_at"), reverse=True)
        return out[:limit]

    def full_history(self, session_id: str) -> dict:
        """Both sides' turns for resuming a conversation in the UI, plus the
        session metadata (industry/pack the session was created with, token totals)."""
        item = self._get_item(session_id) or {}
        return _plain(
            {
                "generic": list(item.get("generic") or []),
                "adapted": list(item.get("adapted") or []),
                "pack_id": item.get("pack_id", ""),
                "industry": item.get("industry", ""),
                "created_at": item.get("created_at", 0),
                "total_input_tokens": item.get("total_input_tokens", 0),
                "total_output_tokens": item.get("total_output_tokens", 0),
            }
        )


_store: _DynamoSessionStore | None = None


def get_sessions() -> _DynamoSessionStore:
    """Return the process-wide session store (DynamoDB backend)."""
    global _store
    if _store is None:
        _store = _DynamoSessionStore()
    return _store
