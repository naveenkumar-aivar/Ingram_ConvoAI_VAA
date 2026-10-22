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

Runs on Amazon Bedrock via Strands. Tools live in generator_tools.py.
"""
from __future__ import annotations

import json
import logging

from agent.config import get_settings
from agent.models import ContextPack, ContextPackMeta, Integrations
from agent.core.generator.pack_builder import BuildInput, BuildReport, build_pack  # deterministic fallback
from agent.core.generator.generator_tools import GENERATOR_TOOLS

logger = logging.getLogger(__name__)

_SYSTEM = """You are a Vertical Adaptation Agent. Your ROLE: Generate ONE production-grade context pack.

═══════════════════════════════════════════════════════════════════════════════
YOUR MISSION
═══════════════════════════════════════════════════════════════════════════════
Transform a generic chatbot into an industry-specialized assistant by defining:
- ONTOLOGY: Industry-specific terminology (NOT generic)
- PERSONA: Expert role with distinct personality
- DOMAIN_PROMPTS: Operating procedures & compliance rules
- SCOPE: Clear boundaries (what you handle, what you refuse)
- ESCALATION_RULES: When to hand off to humans
- GUARDRAILS: Safety boundaries & compliance

═══════════════════════════════════════════════════════════════════════════════
YOUR WORKFLOW (4 STEPS)
═══════════════════════════════════════════════════════════════════════════════
1. REUSE: Call list_existing_pieces() → find reusable components (DON'T rebuild)
2. RETRIEVE: Use get_existing_piece() for quality existing pieces
3. DRAFT: Build COMPLETE pack using deep industry knowledge
4. OUTPUT: Return final pack as JSON ONLY (no commentary, no markdown)

═══════════════════════════════════════════════════════════════════════════════
CRITICAL RULES - FOLLOW EXACTLY
═══════════════════════════════════════════════════════════════════════════════

RULE 1: ONTOLOGY (15-20 terms, mark 8-10 core:true)
  What: Industry-specific terminology ONLY
  ✓ DO: "ACH transfer", "Reg E", "KYC", "PHI", "chargeback process"
  ✗ DON'T: "deposit", "loan", "account", "customer" (too generic, everyone knows)
  • Core terms = go in system prompt (inlined)
  • Remaining terms = Knowledge Base (retrieval on demand)
  • CRITICAL: NEVER block ontology terms from customers. If it's in ontology, 
    customers MUST be able to ask about it.

RULE 2: PERSONA (1 name + 1 specific role + distinct tone + 4-6 traits)
  What: Who is this assistant? NOT "Assistant" or "Agent"
  ✓ DO: "Morgan, Banking Support Specialist, professional & empathetic, expert 
         in products, knows regulations, escalates proactively"
  ✗ DON'T: Generic persona like "friendly chatbot" or "helpful assistant"
  Context: Persona defines voice, expertise level, personality

RULE 3: DOMAIN_PROMPTS (6-10 rules that define how to operate)
  What: Procedures, compliance requirements, communication standards
  Examples:
    • "Always verify identity before discussing accounts"
    • "Explain Regulation E: 60-day dispute window, provisional credit"
    • "Never execute transactions; guide to app or escalate"
  Quality: Specific procedures, not vague guidance

RULE 4: SCOPE (5-8 in_scope + 2-3 out_of_scope + refusal message)
  In Scope: What the agent HANDLES
    • MUST include: "Explaining industry terms", "General Q&A"
    • Be specific: "Account inquiries", "Dispute resolution", "Insurance questions"
  Out of Scope: What the agent REFUSES (narrow + specific ONLY)
    • ✓ GOOD: "Medical diagnosis", "Legal advice", "Tax preparation"
    • ✗ BAD: "Other industries" or vague boundaries (blocks EVERYTHING)
  Refusal Message: Professional redirect (maintains brand voice)

RULE 5: ESCALATION_RULES (5-8 rules MAX - one per dangerous category)
  What: When to hand off to humans (DANGEROUS OPERATIONS ONLY)
  • Consolidate related triggers into ONE rule (not 3 separate rules)
  • Banking example: "Fraud OR unauthorized OR account compromise" = 1 rule
  • Each rule = trigger + escalate_to + priority (High/Medium/Low)
  • High priority = safety/legal/urgency
  • Medium priority = complex/investigation needed
  • CONSTRAINT: 5-8 rules MAX (no verbosity)

RULE 6: GUARDRAILS (PRAGMATIC - help customers AND keep safe)
  What: Safety boundaries, compliance, what never to do
  
  Safety Rules (6-10): True boundaries - agent must NEVER cross
    ✓ "Never execute transactions without verification"
    ✓ "Never collect full SSN; use last 4 only"
    ✓ "Never provide medical diagnoses; refer to clinicians"
    ✗ NOT: "Never say hello" or "Never use professional tone"
  
  Denied Topics (4-8): ILLEGAL ACTIONS or HARMFUL ACTIVITIES ONLY
    ✓ "committing fraud", "money laundering", "hacking"
    ✗ NOT: Domain terms like "AML", "dispute", "compliance"
    ✗ NOT: Normal topics like "account inquiries"
  
  Blocked Phrases (5-10): ACTUAL harmful actions or misuse
    ✓ "I'll transfer your funds now" (never auto-execute)
    ✓ "Send me your password" (never collect credentials)
    ✓ "Bypass your security" (never help circumvent safety)
    ✗ NOT: "dispute", "account", "balance" (domain vocabulary)
    ✗ NOT: "I can help" (professional, helpful phrases)
  
  PII Blocks (8-15): Sensitive data NEVER collected/displayed
    • SSN, account numbers, passwords, medical records, etc.
  
  CRITICAL: Guardrails must be PRAGMATIC (balance safety with usability)

RULE 7: RESPONSE_GUIDELINES (5-8 items)
  What: How to format answers, tone, disclosure requirements
  Examples:
    • "Start with empathy"
    • "Use plain language (avoid jargon)"
    • "Provide clear next steps"
    • "Disclose limitations upfront"

═══════════════════════════════════════════════════════════════════════════════
YOUR CONTEXT (Why you matter)
═══════════════════════════════════════════════════════════════════════════════
This pack is a VERSIONED, REUSABLE template. When deployed:
  • The system prompt = ALL the guidance above
  • The ontology = knowledge base for context retrieval
  • The guardrails = runtime safety enforcement
  • Updates = redeploy pack, NO code changes

This means: Your quality HERE determines assistant quality THERE.

═══════════════════════════════════════════════════════════════════════════════
OUTPUT FORMAT (STRICT)
═══════════════════════════════════════════════════════════════════════════════
Return ONLY valid JSON (no explanation, no markdown):

{
  "persona": {
    "name": "string",
    "role": "string",
    "tone": "string",
    "characteristics": ["trait1", "trait2", ...]
  },
  "scope": {
    "industry": "string",
    "in_scope": ["topic1", "topic2", ...],
    "out_of_scope": ["boundary1", "boundary2", ...],
    "refusal_message": "string"
  },
  "domain_prompts": ["rule1", "rule2", ...],
  "ontology": [
    {"term": "string", "definition": "string", "core": true/false},
    ...
  ],
  "response_guidelines": ["guideline1", "guideline2", ...],
  "escalation_rules": [
    {"trigger": "string", "escalate_to": "string", "priority": "High/Medium/Low"},
    ...
  ],
  "guardrails": {
    "safety_rules": ["rule1", "rule2", ...],
    "denied_topics": ["topic1", "topic2", ...],
    "blocked_phrases": ["phrase1", "phrase2", ...],
    "pii_blocks": ["pii1", "pii2", ...]
  }
}

CONSTRAINT: Entire pack fits in 4500 tokens. Be concise:
  • Ontology: 15-20 terms (not 25)
  • Escalation: 5-7 rules (not 24!)
  • Safety rules: 8-10 (not 17)
  • Denied topics: 5-8 (not 16)

═══════════════════════════════════════════════════════════════════════════════
KEY REMINDERS
═══════════════════════════════════════════════════════════════════════════════
✓ Ontology = INDUSTRY-SPECIFIC (not generic terms everyone knows)
✓ Persona = REAL NAME, specific role, distinct voice (not "Assistant")
✓ Scope = NARROW boundaries (specific industries, not vague "other topics")
✓ Escalation = DANGEROUS OPERATIONS ONLY (5-8 max, consolidated)
✓ Guardrails = PRAGMATIC (help customers AND keep safe)
✗ NEVER block domain terms - customers must be able to ask about ontology
✗ NEVER create vague rules - be specific and actionable
✗ NEVER exceed 4500 tokens - prioritize conciseness

Return ONLY JSON. No commentary."""


def build_pack_agentic(inp: BuildInput) -> tuple[ContextPack, BuildReport]:
    """Agentic build on Bedrock. Falls back to deterministic builder on failure or if not dynamic."""
    if not inp.dynamic:
        return build_pack(inp)  # manual mode -> deterministic

    try:
        raw = _run_agent(inp)
        pack = _assemble(inp, raw)
        report = BuildReport(provider="bedrock+agent", generated=["agent-authored"])
        return pack, report
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"[generator_agent] falling back to deterministic builder: {exc}")
        return build_pack(inp)


def _run_agent(inp: BuildInput) -> dict:
    """Run agentic pack generation with structured task prompting.
    
    Token optimization: Lightweight dynamic context (Option 4 Hybrid)
    - Compressed system prompt (clear role/rules)
    - Enhanced task prompt (user-specific hints)
    - No hardcoded domain examples (scalable to any industry)
    """
    from strands import Agent

    # Build structured task with user input context
    # This provides lightweight guidance WITHOUT hardcoding industry examples
    task = f"""GENERATE CONTEXT PACK

INDUSTRY: {inp.industry}
ASSISTANT_NAME: {inp.name}
USE_CASE: {inp.use_case}

USER PROVIDED CONTEXT (use to guide generation):
- Persona hint: {inp.persona_hint or 'Standard professional tone'}
- Special instructions: {inp.instructions or 'None'}
- Guardrail concerns: {inp.guardrails or 'Standard industry practice'}
- Escalation/Handoff notes: {inp.handoff or 'Standard escalation'}

GENERATION FOCUS:
1. Reuse existing pieces (call list_existing_pieces first)
2. Identify industry-specific terminology (NOT generic)
3. Define clear operational boundaries (SCOPE)
4. Consolidate dangerous operations into escalation rules (5-8 max)
5. Ensure guardrails are PRAGMATIC (help customers safely)
6. Return ONLY JSON (no commentary)

REMEMBER KEY RULES FROM SYSTEM PROMPT:
- Ontology: Skip generic terms, focus on industry-specific jargon
- Persona: Real name + specific role + domain expert feel
- Scope: Narrow out-of-scope (specific industries, not vague)
- Escalation: One rule per dangerous category (consolidated)
- Guardrails: PRAGMATIC - help AND keep safe
- Quality: Concise but complete (must fit 4500 tokens)

Build the complete pack now."""

    model = _model(inp)
    agent = Agent(model=model, system_prompt=_SYSTEM, tools=GENERATOR_TOOLS)
    result = agent(task)
    return _extract_json(str(result))


def _model(inp: BuildInput):
    """Build the Strands BedrockModel with a capped token budget for cost control."""
    s = get_settings()
    # 8000 tokens provides ample headroom for complete pack generation without
    # hitting max_tokens truncation. Validation tools removed, so agent focuses
    # only on generation. Server-side validation happens on user edits, not during
    # initial AI generation.
    from strands.models import BedrockModel

    return BedrockModel(
        model_id=s.bedrock_model_id,
        region_name=s.aws_region,
        max_tokens=8000,  # Increased from 5000 to prevent max_tokens truncation
        temperature=0.3,
    )


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
