"""Tools for the agentic context-pack generator.

These are the actions the generator agent can CHOOSE to call while building a
pack — this is what makes it an agent (reason → act → observe → loop) rather
than a fixed pipeline. All tools are industry-agnostic and cheap/local:
reuse discovery (list/get existing pieces) and a self-validation check.

Each tool returns a short string the agent reasons about (keeping token cost
low). Decorated with Strands' @tool.
"""
from __future__ import annotations

import json

from strands import tool

from app.aws import pieces


@tool
def list_existing_pieces(industry: str, name: str) -> str:
    """List which reusable context-pack pieces already exist for this industry
    and assistant name, so you can REUSE them instead of regenerating.
    Returns a short JSON of which of persona/ontology/prompts/guardrails/handoff
    are available."""
    report = pieces.reuse_report(industry, name)
    return json.dumps(report)


@tool
def get_existing_piece(industry: str, name: str, kind: str) -> str:
    """Read an existing piece to build on it. `kind` is one of:
    persona, ontology, prompts, guardrails, handoff. Returns the piece JSON or
    'none' if it does not exist. Use this to reuse good existing content."""
    data = pieces.load_piece(industry, name, kind)
    return json.dumps(data) if data else "none"


@tool
def validate_pack(pack_json: str) -> str:
    """Self-check a draft pack before finishing. Returns 'OK' if complete, else
    a short list of gaps to fix (then regenerate those parts and re-validate
    once). Checks: persona, scope (in/out), domain prompts, safety rules,
    escalation rules, ontology."""
    try:
        p = json.loads(pack_json)
    except Exception:  # noqa: BLE001
        return "INVALID: pack_json is not valid JSON."

    gaps = []
    if not (p.get("persona") or {}).get("name"):
        gaps.append("persona.name missing")
    if not (p.get("persona") or {}).get("tone"):
        gaps.append("persona.tone missing")
    scope = p.get("scope") or {}
    if not scope.get("in_scope"):
        gaps.append("scope.in_scope empty")
    if not scope.get("out_of_scope"):
        gaps.append("scope.out_of_scope empty")
    if not p.get("domain_prompts"):
        gaps.append("domain_prompts empty")
    g = p.get("guardrails") or {}
    if not g.get("safety_rules"):
        gaps.append("guardrails.safety_rules empty (industry refusals missing)")
    if not p.get("escalation_rules"):
        gaps.append("escalation_rules empty")
    if not p.get("ontology"):
        gaps.append("ontology empty")

    return "OK" if not gaps else "GAPS: " + "; ".join(gaps)


@tool
def shape_guardrail_and_prompt(pack_json: str) -> str:
    """Confirm the pack's guardrail content maps cleanly onto the Bedrock
    guardrail policies (topic / word / sensitive-information). Call this ONCE
    before finishing to verify guardrails are complete.

    Checks guardrails.denied_topics, guardrails.blocked_phrases and
    guardrails.pii_blocks are each present, non-empty and well-formed (lists of
    strings/objects). Returns 'VALID: mapped topic=N word=M pii=K' when all
    three are present, else 'INVALID: <missing/malformed fields>'."""
    try:
        p = json.loads(pack_json)
    except Exception:  # noqa: BLE001
        return "INVALID: pack_json is not valid JSON."

    g = p.get("guardrails") or {}
    problems: list[str] = []

    def _well_formed_list(value) -> bool:
        if not isinstance(value, list) or not value:
            return False
        return all(isinstance(item, (str, dict)) for item in value)

    denied = g.get("denied_topics")
    blocked = g.get("blocked_phrases")
    pii = g.get("pii_blocks")

    if not _well_formed_list(denied):
        problems.append("guardrails.denied_topics missing or malformed")
    if not _well_formed_list(blocked):
        problems.append("guardrails.blocked_phrases missing or malformed")
    if not _well_formed_list(pii):
        problems.append("guardrails.pii_blocks missing or malformed")

    if problems:
        return "INVALID: " + "; ".join(problems)

    return f"VALID: mapped topic={len(denied)} word={len(blocked)} pii={len(pii)}"


# The tool list the agent is given.
# NOTE: web_search was removed — it was a no-op stub that returned placeholder
# text, so the agent wasted tokens calling it and often hit the max_tokens
# truncation (which triggered the deterministic fallback). The model already
# has strong industry knowledge; it grounds packs from that directly.
# NOTE: validate_pack and shape_guardrail_and_prompt were removed — they consumed
# significant tokens and triggered max_tokens truncation during generation. Pack
# validation now happens server-side ONLY when users edit the draft, not during
# initial AI generation. This allows the agent to focus solely on generating
# high-quality packs without token waste.
GENERATOR_TOOLS = [list_existing_pieces, get_existing_piece]
