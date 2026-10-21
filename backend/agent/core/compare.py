"""Before/after comparison for the chatbot demo, with conversation memory.

For one question it:
  1. loads prior history for each side from the session store,
  2. runs the agent twice (generic + adapted) with that history (multi-turn),
  3. appends the new turn to each side's history,
  4. tracks timing for each response to measure time-to-adapt,
  5. evaluates both responses and populates evaluation metrics.
"""
from __future__ import annotations

import logging
import time

from agent.core.conversational_agent import answer
from agent.core.evaluator import evaluate
from agent.models import CompareResponse
from agent.core.sessions import get_sessions
from agent.aws.storage import get_store

logger = logging.getLogger(__name__)


def compare(
    question: str,
    pack_id: str | None,
    history: list[dict] | None = None,
    industry: str | None = None,
    session_id: str | None = None,
) -> CompareResponse:
    sessions = get_sessions()
    store = get_store()

    # Prior conversation per side (empty if no session / new chat).
    generic_hist = sessions.get_history(session_id, "generic") if session_id else (history or [])
    adapted_hist = sessions.get_history(session_id, "adapted") if session_id else (history or [])

    # Time the generic response
    start_generic = time.time()
    generic_text, generic_metrics = answer(question, pack_id=None, history=generic_hist)
    generic_duration_ms = int((time.time() - start_generic) * 1000)

    # Time the adapted response
    start_adapted = time.time()
    if pack_id:
        adapted_text, adapted_metrics = answer(question, pack_id=pack_id, history=adapted_hist)
        adapted_duration_ms = int((time.time() - start_adapted) * 1000)
    else:
        adapted_text = generic_text
        adapted_metrics = generic_metrics
        adapted_duration_ms = 0

    # Persist the new turn so the next question remembers this one.
    if session_id:
        sessions.append_turn(session_id, "generic", question, generic_text)
        if pack_id:
            sessions.append_turn(session_id, "adapted", question, adapted_text)

    # Evaluate both responses (if a pack is applied).
    evaluation = []
    verdict = ""
    if pack_id:
        pack = store.get_pack(pack_id)
        eval_result = evaluate(question, generic_text, adapted_text, pack, industry=industry)
        evaluation = eval_result.get("metrics", [])
        verdict = eval_result.get("verdict", "")

    # Log timing and evaluation metrics for CloudWatch correlation
    log_extra = {
        "question_hash": hash(question) % 10000,
        "generic_duration_ms": generic_duration_ms,
        "adapted_duration_ms": adapted_duration_ms,
        "pack_id": pack_id,
        "industry": industry,
        "num_metrics": len(evaluation),
    }
    
    # Include evaluation scores if available
    if evaluation:
        for metric in evaluation:
            log_extra[f"eval_{metric.metric.lower().replace(' & ', '_').replace(' ', '_')}"] = {
                "generic": metric.generic,
                "adapted": metric.adapted,
            }
        log_extra["verdict"] = verdict
    
    logger.info(
        f"Compare: {question[:50]}...",
        extra=log_extra,
    )

    return CompareResponse(
        question=question,
        generic=generic_text,
        adapted=adapted_text,
        evaluation=evaluation,
        verdict=verdict,
        session_id=session_id or "",
        generic_duration_ms=generic_duration_ms,
        adapted_duration_ms=adapted_duration_ms,
        generic_input_tokens=generic_metrics.get('input_tokens', 0),
        generic_output_tokens=generic_metrics.get('output_tokens', 0),
        adapted_input_tokens=adapted_metrics.get('input_tokens', 0),
        adapted_output_tokens=adapted_metrics.get('output_tokens', 0),
        generic_latency_ms=generic_metrics.get('latency_ms', 0),
        adapted_latency_ms=adapted_metrics.get('latency_ms', 0),
    )
