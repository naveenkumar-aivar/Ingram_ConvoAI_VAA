"""LLM-as-judge evaluator.

Scores the generic ("before") and adapted ("after") answers for the same
question using an LLM with a scoring rubric. Uses the same provider as the
chat agent:
  - bedrock -> Strands + Amazon Bedrock
  - mock    -> deterministic placeholder (no LLM)

Returns a list of EvalMetric (metric, generic, adapted) plus a short verdict
sentence, so the UI can render a compact comparison.
"""
from __future__ import annotations

import json

from app.config import get_settings
from app.models import ContextPack, EvalMetric

# The dimensions the judge scores (0-100 each) for BOTH answers.
# "Industry scoped" measures how well the answer fits the SELECTED industry
# (the one chosen in the sidebar) — i.e. is it specific and relevant to that
# vertical, not generic or off-topic.
METRICS = ["Groundedness", "Compliance", "Persona & tone", "Scope adherence", "Industry scoped"]


def _resolve_industry(pack: ContextPack | None, industry: str | None) -> str:
    """Prefer the explicitly selected sidebar industry, then the pack's."""
    if industry:
        return industry
    if pack:
        return pack.scope.industry or pack.meta.industry
    return "the target industry"


def _rubric(question: str, generic: str, adapted: str, pack: ContextPack | None, industry: str) -> str:
    persona = f"{pack.persona.name}, a {pack.persona.role}" if pack else "a specialist assistant"
    return (
        "You are a strict evaluation judge for a vertical (industry-specific) "
        "conversational AI product. Score TWO answers to the SAME user question.\n\n"
        f"Selected industry: {industry}\n"
        f"Expected adapted persona: {persona}\n\n"
        f"User question:\n{question}\n\n"
        f"ANSWER A (generic assistant, no industry context):\n{generic}\n\n"
        f"ANSWER B (industry-adapted assistant):\n{adapted}\n\n"
        "Score each answer 0-100 on these dimensions:\n"
        "- Groundedness: factual, specific, non-hallucinated.\n"
        f"- Compliance: follows {industry} norms, cites relevant policy/regulation, adds disclaimers where needed.\n"
        "- Persona & tone: how closely the answer matches the expected persona and voice "
        "(first-person, on-brand, right emotional register) vs. a flat generic encyclopedia tone.\n"
        "- Scope adherence: stays within scope; refuses/redirects out-of-scope requests.\n"
        f"- Industry scoped: how specific and relevant the answer is to the {industry} industry "
        f"(uses {industry} terminology, context, and framing) vs. a generic answer that could apply anywhere.\n\n"
        "Return ONLY compact JSON of this exact shape (no prose):\n"
        '{"generic":{"Groundedness":int,"Compliance":int,"Persona & tone":int,"Scope adherence":int,"Industry scoped":int},'
        '"adapted":{"Groundedness":int,"Compliance":int,"Persona & tone":int,"Scope adherence":int,"Industry scoped":int},'
        '"verdict":"one short sentence on why the adapted answer is better or not"}'
    )


def evaluate(
    question: str,
    generic: str,
    adapted: str,
    pack: ContextPack | None,
    industry: str | None = None,
) -> dict:
    """Return {metrics: [EvalMetric...], verdict: str}.

    `industry` is the industry selected in the sidebar; it drives the
    "Industry scoped" metric.
    """
    settings = get_settings()
    provider = settings.llm_provider.lower()
    industry_name = _resolve_industry(pack, industry)

    if provider == "bedrock":
        try:
            data = _judge_with_bedrock(_rubric(question, generic, adapted, pack, industry_name))
            return _to_result(data)
        except Exception as exc:  # noqa: BLE001
            print(f"[evaluator] bedrock judge failed: {exc}")
            return _fallback(generic, adapted, pack)

    # mock
    return _fallback(generic, adapted, pack)


# --------------------------------------------------------------------------
# Judge backends
# --------------------------------------------------------------------------
def _judge_with_bedrock(prompt: str) -> dict:
    from strands import Agent
    from strands.models import BedrockModel
    settings = get_settings()
    model = BedrockModel(model_id=settings.bedrock_model_id, region_name=settings.aws_region)
    agent = Agent(model=model, system_prompt="You are a precise JSON-only evaluation judge.")
    text = str(agent(prompt))
    # Extract the JSON object from the response.
    start, end = text.find("{"), text.rfind("}")
    return json.loads(text[start : end + 1])


# --------------------------------------------------------------------------
def _to_result(data: dict) -> dict:
    g = data.get("generic", {})
    a = data.get("adapted", {})
    metrics = [
        EvalMetric(metric=m, generic=float(g.get(m, 0)), adapted=float(a.get(m, 0)))
        for m in METRICS
    ]
    return {"metrics": metrics, "verdict": data.get("verdict", "")}


def _fallback(generic: str, adapted: str, pack: ContextPack | None) -> dict:
    """Deterministic placeholder when no LLM judge is available."""
    if pack is None:
        metrics = [EvalMetric(metric=m, generic=50, adapted=50) for m in METRICS]
        return {"metrics": metrics, "verdict": "No context pack applied; answers are equivalent."}
    adapted_l = adapted.lower()
    hits = sum(t in adapted_l for t in ["policy", "regulation", "compliance", "escalate", "advisor"])
    metrics = [
        EvalMetric(metric="Groundedness", generic=40, adapted=85),
        EvalMetric(metric="Compliance", generic=10, adapted=min(100, hits * 30)),
        EvalMetric(metric="Persona & tone", generic=35, adapted=90),
        EvalMetric(metric="Scope adherence", generic=50, adapted=90),
        EvalMetric(metric="Industry scoped", generic=30, adapted=90),
    ]
    return {"metrics": metrics, "verdict": "Adapted answer is more on-brand and policy-aware."}
