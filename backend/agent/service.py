"""Agent service facade — the single public surface of the agent package.

Every capability the API needs is exposed here as a plain function. This is the
one seam between the HTTP layer (``app/``) and the agent internals:

  * Locally, ``app/agent_client.py`` imports and calls these functions in-process.
  * In production, ``agent/runtime.py`` (the AgentCore entrypoint) dispatches
    ``invoke(action, payload)`` to these same functions inside the container.

Keeping all capabilities behind one facade means the AgentCore swap is a change
in ``app/agent_client.py`` only — route code and agent internals never move.

Cost note: this module adds no LLM calls of its own; it only routes to the
existing core logic, so it has zero token/cost overhead.
"""
from __future__ import annotations

from typing import Any, Iterator

from agent.models import ContextPack, CompareResponse


# --------------------------------------------------------------------- chat
def compare(
    question: str,
    pack_id: str | None,
    history: list[dict] | None = None,
    industry: str | None = None,
    session_id: str | None = None,
) -> CompareResponse:
    """Run the before/after comparison for one question."""
    from agent.core.compare import compare as _compare
    return _compare(question, pack_id, history, industry=industry, session_id=session_id)


def new_session() -> str:
    from agent.core.sessions import get_sessions
    return get_sessions().new_session()


def recent_sessions(limit: int = 5) -> list[dict]:
    from agent.core.sessions import get_sessions
    return get_sessions().recent(limit)


def session_history(session_id: str) -> dict:
    from agent.core.sessions import get_sessions
    return get_sessions().full_history(session_id)


def reset_session(session_id: str) -> None:
    from agent.core.sessions import get_sessions
    get_sessions().reset(session_id)


# --------------------------------------------------------- prompts / packs
def generic_system_prompt() -> str:
    from agent.core.conversational_agent import GENERIC_SYSTEM_PROMPT
    return GENERIC_SYSTEM_PROMPT


def system_prompt_for(pack: ContextPack | None) -> str:
    from agent.core.conversational_agent import system_prompt_for as _spf
    return _spf(pack)


def list_industries() -> list[dict]:
    from agent.aws.storage import get_store
    return get_store().list_industries()


def list_agent_names(industry: str) -> list[str]:
    from agent.aws.storage import get_store
    return get_store().list_agent_names(industry)


def list_versions(industry: str, agent_name: str = "") -> list:
    from agent.aws.storage import get_store
    return get_store().list_versions(industry, agent_name)


def get_pack(pack_id: str) -> ContextPack | None:
    from agent.aws.storage import get_store
    return get_store().get_pack(pack_id)


def name_exists(industry: str, name: str) -> bool:
    from agent.aws.storage import get_store
    return get_store().name_exists(industry, name)


def stages() -> list[str]:
    from agent.core.generator.pack_builder import STAGES
    return STAGES


# ---------------------------------------------- create flow (draft→approve)
def build_pack_agentic(inp: Any):
    """Run the agentic pack builder. ``inp`` is a BuildInput."""
    from agent.core.generator.generator_agent import build_pack_agentic as _b
    return _b(inp)


def make_build_input(**kwargs):
    from agent.core.generator.pack_builder import BuildInput
    return BuildInput(**kwargs)


def validate_pack(pack_json: str) -> str:
    from agent.core.generator.generator_tools import validate_pack as _v
    return _v(pack_json)


# drafts
def create_draft(pack, report, name, kb_file_names=None, user_inputs=None) -> str:
    from agent.core import drafts
    return drafts.create(pack, report, name, kb_file_names=kb_file_names, user_inputs=user_inputs)


def update_draft(draft_id: str, pack_dict: dict) -> dict | None:
    from agent.core import drafts
    return drafts.update(draft_id, pack_dict)


def discard_draft(draft_id: str) -> None:
    from agent.core import drafts
    drafts.discard(draft_id)


def approve_draft_stream(draft_id: str) -> Iterator[dict]:
    from agent.core import drafts
    return drafts.approve_stream(draft_id)
