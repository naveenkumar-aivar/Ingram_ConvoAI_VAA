"""Public agent interface - stable API for external consumers.

This module provides the public interface to the vertical adaptation agent.
It wraps the internal agent.answer() function with a clean, stable API that
can be called by:
- The FastAPI backend (compare.py)
- Other agents via AgentCore
- Lambda functions
- External services

This interface remains stable even if internal implementation changes.
"""
from __future__ import annotations

from typing import Any

from app.agent import answer as _internal_answer


def answer(
    question: str,
    pack_id: str | None = None,
    history: list[dict[str, Any]] | None = None,
) -> tuple[str, dict[str, Any]]:
    """Answer a question using the vertical adaptation agent.

    This is the PRIMARY PUBLIC API for the agent. All external consumers
    should use this function, not the internal agent.answer() directly.

    Args:
        question: The user's question/message.
        pack_id: Context pack ID for industry specialization.
                 - If provided: Uses adapted persona + KB + guardrails
                 - If None: Uses generic system prompt (for comparisons)
        history: Prior conversation turns for multi-turn dialogue.
                 Format: [{"role": "user"|"assistant", "text": "..."}]
                 Optional, defaults to empty (no history).

    Returns:
        tuple: (response_text, metrics)
        - response_text (str): The agent's reply
        - metrics (dict): Performance and usage metrics
            {
                "input_tokens": int,      # Tokens in prompt
                "output_tokens": int,     # Tokens in response
                "latency_ms": int         # Response time in milliseconds
            }

    Example:
        >>> text, metrics = answer("What is AML?", pack_id="banking-v1")
        >>> print(f"Response: {text}")
        >>> print(f"Used {metrics['output_tokens']} output tokens")

    Notes:
        - Requires AWS credentials configured
        - pack_id must reference an existing pack in S3
        - history is preserved across multi-turn conversations
        - Automatically retrieves relevant KB content if pack has knowledge_base_id
        - Applies guardrails if pack has guardrail_id
    """
    return _internal_answer(question=question, pack_id=pack_id, history=history)


# Lambda handler (for AgentCore deployment)
def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """AWS Lambda handler for AgentCore runtime.

    Event format:
        {
            "question": "User's question",
            "pack_id": "banking-v1" (optional),
            "history": [...] (optional)
        }

    Returns:
        {
            "statusCode": 200,
            "body": {
                "response": "Agent's answer",
                "metrics": {...}
            }
        }
    """
    try:
        question = event.get("question")
        if not question:
            return {
                "statusCode": 400,
                "body": {"error": "Missing required field: question"}
            }

        pack_id = event.get("pack_id")
        history = event.get("history", [])

        response_text, metrics = answer(
            question=question,
            pack_id=pack_id,
            history=history
        )

        return {
            "statusCode": 200,
            "body": {
                "response": response_text,
                "metrics": metrics
            }
        }

    except Exception as e:
        return {
            "statusCode": 500,
            "body": {"error": str(e)}
        }


__all__ = ["answer", "handler"]
