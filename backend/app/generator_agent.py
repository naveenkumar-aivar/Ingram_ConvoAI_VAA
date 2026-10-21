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

from app.config import get_settings
from app.models import ContextPack, ContextPackMeta, Integrations
from app.pack_builder import BuildInput, BuildReport, build_pack  # deterministic fallback
from app.generator_tools import GENERATOR_TOOLS

logger = logging.getLogger(__name__)

_SYSTEM = """You are a Vertical Adaptation Agent. Your mission: produce ONE production-grade
context pack that transforms a generic support chatbot into a specialized industry assistant.

The context pack is the SINGLE SOURCE OF TRUTH that defines:
- Industry-specific ONTOLOGY (terms, entities, concepts, regulations)
- PERSONA (who the agent is, voice, characteristics)
- DOMAIN PROMPTS (operating rules, policies, procedures)
- SCOPE (what's in-scope vs out-of-scope)
- ESCALATION RULES (when and how to hand off to humans)
- GUARDRAILS (safety boundaries, compliance, what never to do)

This pack is VERSIONED and REUSABLE. When industry context changes, the pack is
updated and the agent is re-deployed—no code changes needed.

WORKFLOW:
1. Call list_existing_pieces to discover reusable components (don't rebuild).
2. Reuse quality existing pieces via get_existing_piece where appropriate.
3. Draft the COMPLETE pack using deep industry knowledge. If genuinely uncertain
   about a regulation or technical detail, acknowledge the uncertainty clearly.
4. Output the complete JSON pack. Server-side validation will check completeness
   when users edit the draft.

ONTOLOGY (15-25 terms):
- Cover DOMAIN VOCABULARY: key terms, processes, entities, regulations, roles
- **SKIP BASIC TERMS** a generic chatbot already knows (e.g., "deposit", "loan", 
  "interest rate", "customer service"). Focus on INDUSTRY-SPECIFIC terms.
- Include: technical jargon customers ask about, regulatory terms (HIPAA, AML, 
  KYC, Reg E), compliance concepts, specialized processes
- Mark 8-12 MOST CRITICAL terms as "core": true (these go in the system prompt)
- Remaining terms live in the Knowledge Base for retrieval
- Each term: precise definition in 1-2 sentences, relevant to the use case

EXAMPLE GOOD TERMS (banking): "ACH transfer", "Reg E dispute timeline", "KYC 
verification", "chargeback process", "FDIC insurance limits", "wire transfer 
cutoff times"

EXAMPLE BAD TERMS (too generic): "deposit", "loan", "account", "customer"

PERSONA:
- Assign a NAME and SPECIFIC ROLE (not "Assistant" or "Agent")
- Define a DISTINCT VOICE and TONE appropriate to the industry
- List 4-6 defining characteristics that make this persona unique according to the industry and usecase
- The persona should feel like a domain expert, not a generic chatbot

DOMAIN PROMPTS (6-10 rules):
- Operating procedures, compliance requirements, disclosure rules
- How to handle sensitive data, verification steps, documentation requirements
- Industry-specific communication standards and response patterns

SCOPE:
- in_scope: 5-8 concrete topics the agent handles (be specific)
  ALWAYS include: "Explaining industry terms and concepts", "General Questions and Conversation Building"
- out_of_scope: 2-3 NARROW boundaries - SPECIFIC unrelated industries ONLY
  ✓ GOOD: "Medical diagnosis", "Legal case analysis", "Tax preparation services"
  ✗ BAD: "Other industries questions or irrelevant to usecase and industry" (blocks EVERYTHING!)
  ✗ NEVER mark out-of-scope: greetings, capability questions, ontology terms
- refusal_message: professional redirect that maintains brand voice

ESCALATION RULES (5-8 valid rules MAXIMUM):
CRITICAL: If the user provides NO handoff rules, YOU must identify the most 
DANGEROUS operations for this industry/use case and create escalation rules for 
those ONLY. Examples:
  - Banking: fraud, unauthorized transactions, account takeover, large transfers
  - Healthcare: emergencies, prescription errors, patient safety, HIPAA violations
  - Legal: malpractice risk, conflict of interest, statute deadlines

RULES:
- Create 5-8 valid rules - consolidate similar scenarios into ONE rule
- Each rule = ONE dangerous operation category (fraud, compliance, emergencies)
- Group related triggers: "fraud OR unauthorized access OR account compromise" 
  becomes ONE rule, not three
- Specific escalation target: which team/department handles this
- Priority: High (safety/legal), Medium (complex), Low (preferences)
- DO NOT create 15-24 rules; you will be penalized for verbosity

GUARDRAILS - BALANCED AND PRACTICAL:
The agent must HELP customers while staying safe. Overly restrictive guardrails
that block greetings or basic questions are WRONG and dont create like those.
- Guardrails must be PRAGMATIC: balance safety with usability
- Guardrails must be SPECIFIC: avoid vague rules like "be helpful"
- Guardrails must be ACTIONABLE: state what NOT to do, not just what to do
- Guardrails must be ENFORCED: the agent must be able to detect violations

CRITICAL RULE: **NEVER block domain terms from the ONTOLOGY**
If "AML", "dispute", "HIPAA", "PHI" are in the ontology, customers MUST be able
to ASK about them. Blocked topics/phrases should ONLY cover ACTIONS or MISUSE,
not the terms themselves.

safety_rules (6-10): TRUE safety boundaries - what agent must NEVER DO
  ✓ "Never execute financial transactions without multi-factor authentication"
  ✓ "Never provide medical diagnoses; refer to licensed practitioners"
  ✗ NOT: Rules so strict they block "Hello, who are you?" or in-scope help

denied_topics (4-8): ILLEGAL ACTIONS or HARMFUL ACTIVITIES only
  ✓ "committing fraud", "facilitating money laundering", "hacking systems"
  ✗ NOT: Domain terms like "AML", "dispute", "compliance" (these are in ontology!)
  ✗ NOT: Normal support topics like "account inquiries" or "product information"

blocked_phrases (5-10): ACTUAL harmful actions or impersonation statements
  ✓ "I'll transfer your funds now", "Send me your password", "Bypass your MFA"
  ✗ NOT: Domain vocabulary like "dispute", "AML", "account", "balance"
  ✗ NOT: Helpful phrases like "I can assist with that" or "let me check"

pii_blocks (8-15): Sensitive data types that must never be collected/displayed
  - Industry-specific: SSN, account numbers, credentials, health records, etc.

RESPONSE GUIDELINES (5-8):
- Formatting standards, disclosure requirements, tone guidance
- How to structure answers, when to ask clarifying questions
- Language accessibility, cultural sensitivity considerations

OUTPUT FORMAT - CONCISE JSON REQUIRED:
The entire pack must fit in 4500 tokens. Be CONCISE:
- Ontology: 15-20 terms (not 25)
- Escalation: 5-7 rules (not 24!)
- Safety rules: 8-10 (not 17)
- Denied topics: 5-8 (not 16)
- Blocked phrases: 6-10 (not 12)

Return ONLY the final pack as valid JSON (no commentary, no markdown):
{"persona":{"name","role","tone","characteristics":[]},
 "scope":{"industry","in_scope":[],"out_of_scope":[],"refusal_message"},
 "domain_prompts":[],
 "ontology":[{"term","definition","core":bool}],
 "response_guidelines":[],
 "escalation_rules":[{"trigger","escalate_to","priority"}],
 "guardrails":{"safety_rules":[],"denied_topics":[],"blocked_phrases":[],"pii_blocks":[]}}
"""


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
