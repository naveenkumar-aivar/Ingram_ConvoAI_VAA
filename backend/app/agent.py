"""The conversational agent.

Intentionally free of any web/FastAPI code. Its single public entry point
`answer(...)` is what the FastAPI layer calls today and what a future AgentCore
deployment would host — migrating means wrapping this function, not rewriting.

Behaviour:
  - No pack  -> generic chatbot (minimal system prompt, no KB, no guardrail).
  - With pack -> adapted chatbot (pack's scope-locked system prompt + KB
                 retrieval + the pack's guardrail in Bedrock mode).

The answer is produced by the provider set in LLM_PROVIDER:
  - "mock"    -> canned text from the mock Bedrock client (no LLM).
  - "bedrock" -> real LLM via Strands + Amazon Bedrock (applies the guardrail).
Storage (MOCK_MODE) is independent of this choice.
"""
from __future__ import annotations

from app.config import get_settings
from app.models import ContextPack
from app.aws.bedrock_client import get_bedrock
from app.aws.storage import get_store

# The GENERIC ("before") chatbot uses this single, minimal, industry-neutral
# prompt — the same for every industry. Only the ADAPTED ("after") chatbot
# swaps in the context pack's system prompt. This is what the SOW's
# before/after comparison demonstrates: one base chatbot, adapted by config.
GENERIC_SYSTEM_PROMPT = (
    "You are a helpful general-purpose assistant chatbot built for conversational AI . "
    "Answer the user's question as clearly and accurately as you can."
)


def _is_real_guardrail(gid: str | None) -> bool:
    """A real Bedrock guardrail id is a short alphanumeric token. Our local /
    hand-authored packs use readable slugs ('banking-...', 'mock-gr-...') that
    Bedrock would reject — skip those."""
    if not gid:
        return False
    if gid.startswith("mock-gr-") or "-" in gid and not gid[:8].isalnum():
        return False
    # Heuristic: real ids are lowercase alphanumeric, no spaces, len ~ 10-30.
    return gid.isalnum() and len(gid) >= 8


def system_prompt_for(pack: ContextPack | None, retrieved_terms: list[str] | None = None) -> str:
    """The system prompt used for a turn: generic if no pack, else the pack's
    structured, scope-locked prompt."""
    if pack is None:
        return GENERIC_SYSTEM_PROMPT
    return pack.to_system_prompt(retrieved_terms=retrieved_terms)


def _run_with_strands(system_prompt: str, user_text: str, pack: ContextPack | None,
                      history: list[dict] | None = None) -> str:
    """Bedrock path: build a Strands Agent on a BedrockModel and run it.

    Prior turns are seeded as the agent's starting messages so the
    conversation is multi-turn (chat memory)."""
    from strands import Agent
    from strands.models import BedrockModel

    settings = get_settings()
    gr = pack.guardrails if pack else None

    model_kwargs = {"model_id": settings.bedrock_model_id, "region_name": settings.aws_region}
    # Only attach a guardrail if it's a REAL Bedrock guardrail id. Hand-authored
    # packs and mock mode use placeholder slugs (e.g. "banking-...-v1",
    # "mock-gr-...") which Bedrock rejects. Behaviour is still enforced by the
    # system prompt's Safety & refusals section.
    if gr and _is_real_guardrail(gr.guardrail_id):
        model_kwargs["guardrail_id"] = gr.guardrail_id
        model_kwargs["guardrail_version"] = gr.guardrail_version or "DRAFT"

    # Seed prior conversation into Strands' message format.
    seeded = [
        {"role": ("assistant" if t.get("role") == "assistant" else "user"),
         "content": [{"text": t.get("text", "")}]}
        for t in (history or [])
    ]

    model = BedrockModel(**model_kwargs)
    agent = Agent(
        model=model,
        system_prompt=system_prompt or "You are a helpful assistant.",
        messages=seeded or None,
    )
    result = agent(user_text)
    return str(result)


def answer(question: str, pack_id: str | None = None, history: list[dict] | None = None) -> str:
    """Answer a single question, optionally adapted by a context pack.

    Args:
        question: the user's message.
        pack_id:  context pack to apply, or None for the generic chatbot.
        history:  prior turns [{role, text}], optional.
    Returns:
        the assistant's reply text.
    """
    settings = get_settings()
    bedrock = get_bedrock()
    history = history or []

    pack: ContextPack | None = get_store().get_pack(pack_id) if pack_id else None

    # 1. RAG: retrieve from the pack's knowledge base (the organisation's own
    #    documents) when one was ingested. Grounds the adapted answer.
    retrieved: list[str] = []
    if pack is not None and pack.integrations.knowledge_base_id:
        try:
            from app.aws.knowledge_base import retrieve as kb_retrieve
            name = pack.meta.pack_id.rsplit("-v", 1)[0]
            retrieved = kb_retrieve(pack.meta.industry, name, question)
        except Exception as exc:  # noqa: BLE001
            print(f"[agent] KB retrieve skipped: {exc}")

    # 2. Build the system prompt: generic (minimal) when no pack, else the
    #    pack's structured scope-locked prompt with retrieved terms injected.
    system_prompt = system_prompt_for(pack, retrieved_terms=retrieved)

    # 3. Generate the answer via the configured provider.
    provider = settings.llm_provider.lower()
    if provider == "bedrock":
        return _run_with_strands(system_prompt, question, pack, history)
    # default: mock (canned demo text, no LLM)
    messages = history + [{"role": "user", "text": question}]
    return bedrock.converse(system_prompt, messages)
