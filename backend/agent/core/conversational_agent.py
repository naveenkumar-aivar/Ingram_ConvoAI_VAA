"""Conversational Agent - AWS Bedrock KB Only.

Uses real AWS services for all retrieval and LLM invocation.
- KB: AWS Bedrock managed knowledge bases
- LLM: AWS Bedrock Converse or Strands Agent
- No local fallback, no Chroma, no mock mode
"""
from __future__ import annotations

import logging

from agent.config import get_settings
from agent.models import ContextPack
from agent.aws.bedrock_client import get_bedrock
from agent.aws.storage import get_store

logger = logging.getLogger(__name__)

# Generic system prompt for "before" chatbot (no industry context)
GENERIC_SYSTEM_PROMPT = """You are a helpful, professional assistant.

## Instructions
- Answer questions clearly and concisely
- Be respectful and courteous
- If you're unsure about something, say so
- Do not provide specialized industry advice unless explicitly asked

## Scope
You can help with general questions, common inquiries, and provide basic information on a wide range of topics."""


def _is_real_guardrail(gid: str | None) -> bool:
    """Check if guardrail ID is valid Bedrock format (alphanumeric, 8-50 chars)."""
    if not gid:
        return False
    return gid.isalnum() and 8 <= len(gid) <= 50


def system_prompt_for(pack: ContextPack | None, retrieved_terms: list[str] | None = None) -> str:
    """Build structured system prompt from pack.
    
    If pack is None, returns the generic system prompt (for "before" comparisons).
    Otherwise builds a structured prompt from the pack with guardrails and domain knowledge.
    Retrieved terms from KB are injected for context.
    """
    if pack is None:
        return GENERIC_SYSTEM_PROMPT
    return pack.to_system_prompt(retrieved_terms=retrieved_terms)


def _run_with_strands(system_prompt: str, user_text: str, pack: ContextPack | None,
                      history: list[dict] | None = None) -> tuple[str, dict]:
    """Bedrock path: build a Strands Agent on a BedrockModel and run it.

    Prior turns are seeded as the agent's starting messages for multi-turn conversation.
    Guardrails are attached if the ID is valid Bedrock format.
    
    Returns: (response_text, metrics dict)
    """
    from strands import Agent
    from strands.models import BedrockModel
    import time

    settings = get_settings()
    gr = pack.guardrails if pack else None

    model_kwargs = {"model_id": settings.bedrock_model_id, "region_name": settings.aws_region}
    
    # Log Bedrock call details
    logger.info(f"[bedrock-call] Preparing Bedrock model invocation")
    logger.info(f"  - Model ID: {settings.bedrock_model_id}")
    logger.info(f"  - User query: {user_text[:80]}...")
    logger.info(f"  - System prompt length: {len(system_prompt)} chars")
    logger.info(f"  - Pack: {pack.meta.pack_id if pack else 'N/A'}")
    
    # Attach guardrail if valid
    if gr and _is_real_guardrail(gr.guardrail_id):
        model_kwargs["guardrail_id"] = gr.guardrail_id
        model_kwargs["guardrail_version"] = gr.guardrail_version or "DRAFT"
        logger.info(f"  - Guardrail ID: {gr.guardrail_id} (version: {gr.guardrail_version})")
    else:
        logger.info(f"  - Guardrail: NONE (relying on system prompt enforcement)")

    # Seed prior conversation for multi-turn
    seeded = [
        {"role": ("assistant" if t.get("role") == "assistant" else "user"),
         "content": [{"text": t.get("text", "")}]}
        for t in (history or [])
    ]
    
    logger.info(f"  - History turns: {len(seeded)}")
    logger.info(f"  - System prompt sections: {len([l for l in system_prompt.split(chr(10)) if l.startswith('##')])}")

    # Run Bedrock agent and capture metrics
    model = BedrockModel(**model_kwargs)
    agent = Agent(
        model=model,
        system_prompt=system_prompt,
        messages=seeded or None,
    )
    logger.info(f"[bedrock-call] Agent executing with Strands...")
    
    start_time = time.time()
    result = agent(user_text)
    latency_ms = int((time.time() - start_time) * 1000)
    
    response_text = str(result)
    logger.info(f"[bedrock-call] Agent returned: {response_text[:100]}...")
    
    # Extract token usage from result metadata if available
    metrics = {
        "input_tokens": getattr(result, 'input_tokens', 0) if hasattr(result, 'input_tokens') else 0,
        "output_tokens": getattr(result, 'output_tokens', 0) if hasattr(result, 'output_tokens') else 0,
        "latency_ms": latency_ms
    }
    
    # Try to get usage from model response metadata
    if hasattr(result, '_metadata') and result._metadata:
        usage = result._metadata.get('usage', {})
        metrics['input_tokens'] = usage.get('input_tokens', metrics['input_tokens'])
        metrics['output_tokens'] = usage.get('output_tokens', metrics['output_tokens'])
    
    logger.info(f"[bedrock-call] Metrics: input={metrics['input_tokens']}, output={metrics['output_tokens']}, latency={metrics['latency_ms']}ms")
    
    return response_text, metrics


def answer(question: str, pack_id: str | None = None, history: list[dict] | None = None) -> tuple[str, dict]:
    """Answer a single question using AWS Bedrock KB and LLM.

    Args:
        question: the user's message.
        pack_id:  context pack ID (optional). If None, uses generic system prompt (for comparisons).
        history:  prior turns [{role, text}], optional.
    Returns:
        tuple: (assistant's reply text, metrics dict with input_tokens, output_tokens, latency_ms)
    """
    settings = get_settings()
    bedrock = get_bedrock()
    history = history or []

    # Load pack (optional for generic responses)
    pack: ContextPack | None = get_store().get_pack(pack_id) if pack_id else None
    
    logger.info("=" * 80)
    logger.info(f"[answer] Processing query: '{question[:80]}...'")
    logger.info(f"[answer] Pack: {pack.meta.pack_id if pack else 'NONE (generic response)'}")
    if pack:
        logger.info(f"[answer] KB ID: {pack.integrations.knowledge_base_id}")
        logger.info(f"[answer] DS ID: {pack.integrations.data_source_id}")
    logger.info("=" * 80)

    # 1. RAG: Retrieve from AWS Bedrock KB ONLY (if pack provided)
    retrieved: list[str] = []
    if pack and pack.integrations.knowledge_base_id:
        try:
            from agent.aws import bedrock_kb
            kb_id = pack.integrations.knowledge_base_id
            
            logger.info(f"[rag] Starting Bedrock KB retrieval")
            logger.info(f"[rag] KB ID: {kb_id}")
            logger.info(f"[rag] Using AWS Bedrock Knowledge Base (AWS Only)")
            
            retrieved = bedrock_kb.retrieve_kb(kb_id, question)
            
            logger.info(f"[rag] Retrieved {len(retrieved)} chunks from Bedrock KB")
            if retrieved:
                logger.info(f"[rag] Sample chunk: {retrieved[0][:100]}...")
            else:
                logger.warning(f"[rag] No chunks retrieved (KB may still be ingesting)")
        except Exception as exc:  # noqa: BLE001
            logger.error(f"[rag] AWS Bedrock KB retrieval failed: {exc}")
            logger.warning(f"[rag] Continuing without KB context")
    else:
        logger.info("[rag] Skipping KB retrieval (generic mode / no pack)")

    # 2. Build system prompt with retrieved content
    logger.info(f"\n[prompt] Building system prompt")
    logger.info(f"[prompt] Retrieved terms for injection: {len(retrieved)}")
    system_prompt = system_prompt_for(pack, retrieved_terms=retrieved)
    logger.info(f"[prompt] Final system prompt length: {len(system_prompt)} chars")
    logger.info(f"[prompt] Sections: {len([l for l in system_prompt.split(chr(10)) if l.startswith('##')])}")

    # 3. Invoke LLM via Bedrock with Strands
    logger.info(f"\n[llm] Invoking Bedrock via Strands")
    response_text, metrics = _run_with_strands(system_prompt, question, pack, history)
    return response_text, metrics
