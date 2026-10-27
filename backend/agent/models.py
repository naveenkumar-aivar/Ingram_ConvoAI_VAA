"""Shared data shapes (the API contract).

These mirror the context-pack schema used across S3, the agent, and the UI.
Keeping them in one place means the frontend, backend, and a future AgentCore
deployment all agree on the same structure.
"""
from __future__ import annotations

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Context pack pieces
# ---------------------------------------------------------------------------
class OntologyEntry(BaseModel):
    term: str
    definition: str
    # When True, this term is always inlined in the system prompt (a "core"
    # term). When False, it lives only in the Knowledge Base and is retrieved
    # on demand — this is how long ontologies are kept out of the prompt.
    core: bool = False


class Persona(BaseModel):
    name: str
    role: str
    tone: str
    characteristics: list[str] = Field(default_factory=list)


class Scope(BaseModel):
    """Defines what the agent will and will not handle. The scope boundary is
    what makes a vertical agent refuse out-of-domain questions."""
    industry: str = ""
    in_scope: list[str] = Field(default_factory=list)      # topics it handles
    out_of_scope: list[str] = Field(default_factory=list)  # topics it refuses
    refusal_message: str = (
        "I'm a specialised assistant for this area, so I can't help with that. "
        "I can, however, help with questions in my domain."
    )


class EscalationRule(BaseModel):
    trigger: str
    escalate_to: str
    priority: str = "Medium"  # High | Medium | Low
    contact: str = ""         # optional team contact detail (phone/email/hours)


class Guardrails(BaseModel):
    # Hard refusal rules enforced via the system prompt (the behavioural layer).
    # e.g. "Never provide medication names, dosages, or diagnoses."
    safety_rules: list[str] = Field(default_factory=list)
    pii_blocks: list[str] = Field(default_factory=list)
    denied_topics: list[str] = Field(default_factory=list)
    blocked_phrases: list[str] = Field(default_factory=list)
    # Populated after the real Bedrock guardrail is created.
    guardrail_id: str | None = None
    guardrail_version: str | None = None


class Integrations(BaseModel):
    """References to the AWS resources backing this pack."""
    knowledge_base_id: str | None = None
    data_source_id: str | None = None  # Bedrock KB S3 data source (real path only)
    prompt_id: str | None = None
    prompt_version: str | None = None  # Bedrock Prompt version for caching
    guardrail_id: str | None = None
    # Token optimization: cached_prompt_hash stores the system prompt hash for reuse
    cached_prompt_hash: str | None = None
    cached_system_prompt: str | None = None  # Cached system prompt for token savings


class ContextPackMeta(BaseModel):
    industry: str
    version: str
    status: str = "active"  # active | archived | draft
    pack_id: str  # e.g. "banking-v2"


class ContextPack(BaseModel):
    meta: ContextPackMeta
    persona: Persona
    scope: Scope = Field(default_factory=Scope)
    domain_prompts: list[str] = Field(default_factory=list)
    ontology: list[OntologyEntry] = Field(default_factory=list)
    response_guidelines: list[str] = Field(default_factory=list)
    escalation_rules: list[EscalationRule] = Field(default_factory=list)
    guardrails: Guardrails = Field(default_factory=Guardrails)
    use_case: str = ""
    integrations: Integrations = Field(default_factory=Integrations)

    # Only this many ontology terms are ever inlined in the prompt. Core terms
    # are always inlined; the rest live in the KB and are retrieved on demand.
    MAX_INLINE_ONTOLOGY: int = 25

    def core_ontology(self) -> list["OntologyEntry"]:
        """Terms inlined into the prompt: explicit core terms first, then fill
        up to MAX_INLINE_ONTOLOGY. Everything else is left for KB retrieval."""
        core = [o for o in self.ontology if o.core]
        if len(core) >= self.MAX_INLINE_ONTOLOGY:
            return core[: self.MAX_INLINE_ONTOLOGY]
        rest = [o for o in self.ontology if not o.core]
        return (core + rest)[: self.MAX_INLINE_ONTOLOGY]

    def to_system_prompt(self, retrieved_terms: list[str] | None = None, compress: bool = True) -> str:
        """Compose a structured, scope-locked system prompt for a vertical
        conversational agent.

        `retrieved_terms` are extra glossary/knowledge snippets pulled from the
        Knowledge Base at runtime (this is how a LONG ontology reaches the model
        without bloating the base prompt).
        
        `compress` enables token optimization: removes extra whitespace and redundancy,
        consolidates rules, uses shorter variable names. Target: ~30-40% smaller prompt.
        """
        p = self.persona
        industry = self.scope.industry or self.meta.industry
        industry_lower = industry.lower()
        s: list[str] = []

        # 1. Identity & role (COMPRESSED)
        # Token optimization: Consolidate role, tone, and characteristics into one compact section
        s.append("## Role")
        role_line = f"{p.name}, {p.role} for {industry}."
        if p.tone:
            role_line += f" Tone: {p.tone}."
        s.append(role_line)
        if p.characteristics:
            s.append("Traits: " + ", ".join(p.characteristics) + ".")
        s.append("Multi-turn dialogue. Warm, conversational, never robotic.")

        # 1b. CONVERSATIONAL LAYER (comes FIRST - highest priority for non-domain)
        # This ensures basic human courtesy is always handled, never refused
        s.append("\n## Conversational Layer (Always In-Scope)")
        s.append("ALWAYS respond warmly to:")
        s.append("- Greetings: 'Hi', 'Hello', 'How are you?'")
        s.append("- Acknowledgments: 'Thank you', 'Thanks', 'Got it', 'Understood'")
        s.append("- Closings: 'Goodbye', 'Have a good day', 'Bye'")
        s.append("- Meta: 'Can you help?', 'What can you do?'")
        s.append("NEVER refuse these. Basic courtesy builds trust.")

        # 2. Domain Scope boundary (AFTER conversational) - COMPRESSED
        s.append("\n## Domain Scope")
        s.append(f"Primary focus: {industry} topics.")
        if self.scope.in_scope:
            s.append("In-Scope: " + "; ".join(self.scope.in_scope) + ".")
        if self.scope.out_of_scope:
            s.append("Out-of-Scope: " + "; ".join(self.scope.out_of_scope) + ".")
        s.append("Out-of-scope topics: acknowledge warmly, redirect conversationally.")

        # 2b. Safety & refusals (hard behavioural rules — highest priority) - COMPRESSED
        if self.guardrails.safety_rules:
            s.append("\n## Safety")
            for r in self.guardrails.safety_rules:
                s.append(f"- {r}")
        
        # Denied topics - COMPRESSED
        if self.guardrails.denied_topics:
            s.append("\n## Deny")
            for t in self.guardrails.denied_topics:
                s.append(f"- {t}")

        # 2d. CRITICAL: Request Classification (prioritizes knowledge over refusal)
        # Tell the model to classify requests and prioritize appropriately
        s.append("\n## Request Classification (Priority Order)")
        s.append("1. CONVERSATIONAL: Greetings, thanks, politeness → Always respond warmly")
        s.append("2. KNOWLEDGE: 'What is X?', 'Define X?' → Always answer (even if X seems sensitive)")
        s.append("3. DOMAIN: In-scope questions → Answer with domain expertise")
        s.append("4. HARMFUL: 'How to perform [harm]?', 'Steps for [illegal]?' → Refuse only here")
        s.append("5. OUT-OF-SCOPE: Unrelated topics → Acknowledge, redirect warmly")

        # 3. Domain glossary (core terms inline; long ones via retrieval) - COMPRESSED
        core = self.core_ontology()
        if core or retrieved_terms:
            s.append("\n## Terms")
            for o in core:
                s.append(f"- {o.term}: {o.definition}")
            if retrieved_terms:
                for t in retrieved_terms:
                    s.append(f"- {t}")

        # 4. Operating rules / policies - COMPRESSED
        if self.domain_prompts:
            s.append("\n## Rules")
            for r in self.domain_prompts:
                s.append(f"- {r}")

        # 5. Escalation / handoff - COMPRESSED
        if self.escalation_rules:
            s.append("\n## Escalate")
            for r in self.escalation_rules:
                s.append(f"- {r.trigger} → {r.escalate_to} ({r.priority})")

        # 6. Response guidelines - COMPRESSED
        if self.response_guidelines:
            s.append("\n## Response")
            for g in self.response_guidelines:
                s.append(f"- {g}")

        # 7. Structured examples (few-shot learning for better generation)
        # CRITICAL: Show all priority levels with examples
        s.append("\n## Examples (By Priority)")
        s.append("1-CONVERSATIONAL:")
        s.append("  Q: Thank you.\n  A: You're welcome! Happy to help. Is there anything else?")
        s.append("  Q: Goodbye.\n  A: Goodbye! Have a great day!")
        s.append("2-KNOWLEDGE:")
        s.append("  Q: What is [domain term]?\n  A: [Clear, accurate explanation of concept]")
        s.append("3-DOMAIN:")
        s.append("  Q: [In-scope question]\n  A: [Domain expertise answer]")
        s.append("4-HARMFUL:")
        s.append("  Q: How to [harmful action]?\n  A: I can't help with that. [Redirect to safe alternative]")
        s.append("5-OUT-OF-SCOPE:")
        s.append("  Q: [Unrelated topic]\n  A: I specialize in [domain], but [acknowledge]. Can I help with [domain]?")

        # 8. Formatting - COMPRESSED
        s.append("\n## Format")
        s.append("- Concise, conversational")
        s.append("- Markdown: paragraphs, lists, **bold**")
        s.append("- No HTML tables")

        return "\n".join(s)


# ---------------------------------------------------------------------------
# API request / response shapes
# ---------------------------------------------------------------------------
class IndustryItem(BaseModel):
    id: str
    label: str


class PackVersionItem(BaseModel):
    id: str
    version: str
    status: str


class GenerateRequest(BaseModel):
    """Input for the dynamic pack builder. industry, name and use_case are
    required; the rest are optional overrides the user can supply."""
    industry: str
    name: str
    use_case: str = ""
    persona_hint: str = ""
    instructions: str = ""   # newline-separated prompt overrides
    guardrails: str = ""     # newline-separated denied topics
    handoff: str = ""        # newline-separated "trigger -> team"
    kb_file_names: list[str] = Field(default_factory=list)
    dynamic: bool = True     # True = LLM generates; False = template/manual


class DraftResponse(BaseModel):
    """A generated-but-not-yet-committed pack, awaiting human approval."""
    draft_id: str
    pack: "ContextPack"
    reused: list[str] = Field(default_factory=list)
    generated: list[str] = Field(default_factory=list)
    provider: str = ""
    system_prompt: str = ""


class NameCheckResponse(BaseModel):
    exists: bool


class UpdateDraftRequest(BaseModel):
    """Body for PUT /api/context-packs/{draft_id}/draft — the full edited pack."""
    pack: "ContextPack"


class ChatTurn(BaseModel):
    role: str  # user | assistant
    text: str


class CompareRequest(BaseModel):
    question: str
    pack_id: str | None = None  # None => generic (no pack)
    industry: str | None = None  # the industry selected in the sidebar
    session_id: str | None = None  # conversation id for chat memory
    history: list[ChatTurn] = Field(default_factory=list)


class EvalMetric(BaseModel):
    metric: str
    generic: float
    adapted: float
    unit: str = "%"


class CompareResponse(BaseModel):
    question: str
    generic: str
    adapted: str
    evaluation: list[EvalMetric] = Field(default_factory=list)
    verdict: str = ""
    session_id: str = ""
    generic_duration_ms: int = 0  # Time to generate generic response (ms)
    adapted_duration_ms: int = 0  # Time to generate adapted response (ms)
    # Token usage metrics
    generic_input_tokens: int = 0
    generic_output_tokens: int = 0
    adapted_input_tokens: int = 0
    adapted_output_tokens: int = 0
    # Bedrock latency (from AWS)
    generic_latency_ms: int = 0
    adapted_latency_ms: int = 0
    # Session-level cumulative token tracking
    session_total_input_tokens: int = 0
    session_total_output_tokens: int = 0
    # Current turn token usage
    current_turn_input_tokens: int = 0
    current_turn_output_tokens: int = 0
