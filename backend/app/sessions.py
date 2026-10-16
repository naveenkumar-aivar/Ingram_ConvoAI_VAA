"""Conversation sessions (chat memory).

Keeps per-session conversation history so the chatbot holds real multi-turn
dialogues. Each session stores TWO independent histories — one for the generic
("before") bot and one for the adapted ("after") bot — because they are
different conversations shown side by side.

Storage:
  - local  (MOCK_MODE=true):  JSON files under s3-data/sessions/<id>.json
  - real   (MOCK_MODE=false): same shape, written to S3 (future) — the API is
                              identical so callers never change.

This is deliberately simple and provider-agnostic (works with mock today and
Bedrock later). When the agent later moves to AgentCore, this file-based store
is swapped for AgentCore Memory with no change to callers.
"""
from __future__ import annotations

import json
import uuid
from pathlib import Path

from app.config import get_settings

# Trim stored history so prompts stay small (last N turns per side).
MAX_TURNS_PER_SIDE = 20


class _SessionStore:
    def __init__(self) -> None:
        self.settings = get_settings()
        # In-memory cache: {session_id: {"generic": [...], "adapted": [...]}}
        self._cache: dict[str, dict[str, list[dict]]] = {}

    # -- paths ---------------------------------------------------------------
    def _dir(self) -> Path:
        root = (Path(__file__).resolve().parents[1] / self.settings.local_s3_dir / "sessions").resolve()
        root.mkdir(parents=True, exist_ok=True)
        return root

    def _file(self, session_id: str) -> Path:
        # session_id is validated to be a safe token (see new_session / API).
        return self._dir() / f"{session_id}.json"

    # -- lifecycle -----------------------------------------------------------
    def new_session(self) -> str:
        sid = uuid.uuid4().hex
        self._cache[sid] = {"generic": [], "adapted": []}
        return sid

    def _load(self, session_id: str) -> dict[str, list[dict]]:
        if session_id in self._cache:
            return self._cache[session_id]
        data = {"generic": [], "adapted": []}
        f = self._file(session_id)
        if f.exists():
            try:
                data = json.loads(f.read_text())
            except Exception:  # noqa: BLE001
                pass
        self._cache[session_id] = data
        return data

    def _save(self, session_id: str, data: dict[str, list[dict]]) -> None:
        self._cache[session_id] = data
        try:
            self._file(session_id).write_text(json.dumps(data))
        except Exception as exc:  # noqa: BLE001
            print(f"[sessions] save skipped: {exc}")

    # -- public API ----------------------------------------------------------
    def get_history(self, session_id: str, side: str) -> list[dict]:
        """Return prior turns for one side ('generic' or 'adapted')."""
        if not session_id:
            return []
        return list(self._load(session_id).get(side, []))

    def append_turn(self, session_id: str, side: str, user_text: str, assistant_text: str) -> None:
        if not session_id:
            return
        data = self._load(session_id)
        hist = data.setdefault(side, [])
        hist.append({"role": "user", "text": user_text})
        hist.append({"role": "assistant", "text": assistant_text})
        # keep only the last MAX_TURNS_PER_SIDE exchanges (2 entries each)
        if len(hist) > MAX_TURNS_PER_SIDE * 2:
            data[side] = hist[-MAX_TURNS_PER_SIDE * 2 :]
        self._save(session_id, data)

    def reset(self, session_id: str) -> None:
        if not session_id:
            return
        self._cache.pop(session_id, None)
        f = self._file(session_id)
        if f.exists():
            try:
                f.unlink()
            except Exception:  # noqa: BLE001
                pass

    def recent(self, limit: int = 5) -> list[dict]:
        """Most-recently-updated sessions with a short preview (first user
        message) so the UI can offer 'resume where you left off'."""
        files = sorted(self._dir().glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
        out: list[dict] = []
        for f in files[:limit]:
            try:
                data = json.loads(f.read_text())
            except Exception:  # noqa: BLE001
                continue
            turns = data.get("adapted") or data.get("generic") or []
            preview = next((t["text"] for t in turns if t.get("role") == "user"), "")
            if not preview:
                continue  # skip empty sessions
            out.append({
                "session_id": f.stem,
                "preview": preview[:60],
                "turns": sum(1 for t in turns if t.get("role") == "user"),
            })
        return out

    def full_history(self, session_id: str) -> dict:
        """Both sides' turns for resuming a conversation in the UI."""
        return self._load(session_id)


_store: _SessionStore | None = None


def get_sessions() -> _SessionStore:
    global _store
    if _store is None:
        _store = _SessionStore()
    return _store
