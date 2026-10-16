"""Agentic context-pack generator (the core Vertical Adaptation Agent).

Unlike a fixed pipeline, this gives an LLM a GOAL + TOOLS and lets it decide
how to build the pack: check what's reusable, optionally research the industry,
draft the whole pack, self-validate, fix gaps, then return. That reason→act→
observe loop is what makes it an agent.

Cost-aware:
  - one agent run that returns the ENTIRE pack as a single JSON (not a call per
    piece), with a capped token budget and a one-pass validate/fix loop;
  - reuse-first (tools surface existing pieces so nothing is rebuilt);
  - concise-by-instruction (tight lists) so the pack fits the token budget;
  - on ANY failure it falls back to the deterministic pack_builder, so it never
    loops/burns tokens and never breaks the flow.

Runs on Amazon Bedrock (LLM_PROVIDER=bedrock). Strands code lives here; the
tools live in generator_tools.py.
"""
from __future__ import annotations

import json

from app.config import get_settings
from app.models import ContextPack, ContextPackMeta, Integrations
from app.pack_builder import BuildInput, BuildReport, build_pack  # deterministic fallback
from app.generator_tools import GENERATOR_TOOLS

_SYSTEM = """You are a Vertical Adaptation Agent. Your job: produce ONE production-grade
context pack that adapts a generic chatbot to a specific industry + use case.

Work like an agent, not a script:
1. Call list_existing_pieces to see what can be REUSED (don't rebuild the wheel).
2. Reuse good existing pieces via get_existing_piece where sensible.
3. Draft the FULL pack from your own industry knowledge. If unsure about a
   specific regulation or term, state the uncertainty rather than inventing it.
4. Optionally call validate_pack ONCE on your draft; if it returns GAPS, fix
   only those, then stop. Do not loop.

Be cost-efficient and CONCISE: minimise tool calls (3-4 total is plenty), and
keep lists tight — aim for 5-8 items per list, not 15+. A focused pack is
better than an exhaustive one, and long packs get truncated.

Persona must be vivid and specific. Guardrails must include strict safety_rules
describing what the assistant must NEVER do in this industry and how to refuse.
Escalation rules must be concrete (trigger -> team, priority).

When done, output ONLY the final pack as a single JSON object (no prose) with
exactly these keys:
{"persona":{"name","role","tone","characteristics":[...]},
 "scope":{"industry","in_scope":[...],"out_of_scope":[...],"refusal_message"},
 "domain_prompts":[...],
 "ontology":[{"term","definition","core":bool}],
 "response_guidelines":[...],
 "escalation_rules":[{"trigger","escalate_to","priority"}],
 "guardrails":{"safety_rules":[...],"denied_topics":[...],"blocked_phrases":[...],"pii_blocks":[...]}}
"""


def build_pack_agentic(inp: BuildInput) -> tuple[ContextPack, BuildReport]:
    """Agentic build with graceful fallback to the deterministic builder."""
    settings = get_settings()
    provider = settings.llm_provider.lower()
    if provider != "bedrock" or not inp.dynamic:
        return build_pack(inp)  # mock / manual -> deterministic

    try:
        raw = _run_agent(inp)
        pack = _assemble(inp, raw)
        report = BuildReport(provider=f"{provider}+agent", generated=["agent-authored"])
        return pack, report
    except Exception as exc:  # noqa: BLE001
        print(f"[generator_agent] falling back to deterministic builder: {exc}")
        return build_pack(inp)


def _run_agent(inp: BuildInput) -> dict:
    from strands import Agent

    task = (
        f"Industry: {inp.industry}\n"
        f"Assistant name: {inp.name}\n"
        f"Use case: {inp.use_case}\n"
        f"Persona hint: {inp.persona_hint or 'none'}\n"
        f"Extra instructions: {inp.instructions or 'none'}\n"
        f"Guardrail notes: {inp.guardrails or 'none'}\n"
        f"Handoff notes: {inp.handoff or 'none'}\n"
        "Build the context pack now."
    )

    model = _model(inp)
    agent = Agent(model=model, system_prompt=_SYSTEM, tools=GENERATOR_TOOLS)
    result = agent(task)
    return _extract_json(str(result))


def _model(inp: BuildInput):
    """Build the Strands model for the active provider, with a capped token
    budget for cost control."""
    s = get_settings()
    # 4000 tokens comfortably fits a full pack JSON; the concise system prompt
    # keeps the pack tight so this budget is rarely hit (avoids the truncation
    # that forced the deterministic fallback before).
    from strands.models import BedrockModel
    return BedrockModel(model_id=s.bedrock_model_id, region_name=s.aws_region,
                        max_tokens=4000, temperature=0.3)


def _extract_json(text: str) -> dict:
    t = text.strip()
    if t.startswith("```"):
        t = t.strip("`")
        if t.lower().startswith("json"):
            t = t[4:]
    a, b = t.find("{"), t.rfind("}")
    if a == -1 or b == -1:
        raise ValueError("agent returned no JSON pack")
    return json.loads(t[a : b + 1])


def _assemble(inp: BuildInput, raw: dict) -> ContextPack:
    """Turn the agent's JSON into a validated ContextPack (draft meta)."""
    name = (inp.name or inp.industry.lower()).strip()
    pack = ContextPack(
        meta=ContextPackMeta(industry=inp.industry.strip() or "General",
                             version="draft", status="draft", pack_id=f"{name}-draft"),
        persona=raw.get("persona") or {},
        scope=raw.get("scope") or {},
        domain_prompts=raw.get("domain_prompts") or [],
        ontology=raw.get("ontology") or [],
        response_guidelines=raw.get("response_guidelines") or [],
        escalation_rules=raw.get("escalation_rules") or [],
        guardrails=raw.get("guardrails") or {},
        use_case=inp.use_case.strip() or f"Support assistant for {inp.industry}.",
        integrations=Integrations(),
    )
    if not pack.persona.name or not pack.domain_prompts:
        raise ValueError("agent pack failed validation")
    return pack
