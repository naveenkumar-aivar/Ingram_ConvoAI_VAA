"""Thin wrapper around the AWS calls for KB retrieval and guardrail creation.

Behaviour is gated by MOCK_MODE (storage/AWS switch):
  - mock (MOCK_MODE=true)  : no AWS; returns realistic canned data.
  - real (MOCK_MODE=false) : boto3 calls to Bedrock Runtime (converse),
            Bedrock Agent Runtime (KB retrieve), and the Bedrock control
            plane (create guardrail).

Note: chat answers normally go through app.agent, which picks the provider via
LLM_PROVIDER. `converse()` here is only used by the agent's "mock" provider
path. Keeping AWS calls behind this class means the rest of the code is
identical across modes.
"""
from __future__ import annotations

from app.config import get_settings


class BedrockClient:
    def __init__(self) -> None:
        self.settings = get_settings()
        self.mock = self.settings.mock_mode
        self._runtime = None       # bedrock-runtime (Converse / InvokeModel)
        self._agent_runtime = None # bedrock-agent-runtime (KB retrieve)
        self._control = None       # bedrock (guardrails, create)
        if not self.mock:
            import boto3
            region = self.settings.aws_region
            self._runtime = boto3.client("bedrock-runtime", region_name=region)
            self._agent_runtime = boto3.client("bedrock-agent-runtime", region_name=region)
            self._control = boto3.client("bedrock", region_name=region)

    # ------------------------------------------------------------------ chat
    def converse(
        self,
        system_prompt: str,
        messages: list[dict],
        *,
        guardrail_id: str | None = None,
        guardrail_version: str | None = None,
        model_id: str | None = None,
    ) -> str:
        """Run a chat turn. `messages` is a list of {role, text}."""
        if self.mock:
            return _mock_answer(system_prompt, messages)

        model = model_id or self.settings.bedrock_model_id
        kwargs = {
            "modelId": model,
            "system": [{"text": system_prompt}] if system_prompt else [],
            "messages": [
                {"role": m["role"], "content": [{"text": m["text"]}]} for m in messages
            ],
            "inferenceConfig": {"maxTokens": 1024, "temperature": 0.3},
        }
        if guardrail_id:
            kwargs["guardrailConfig"] = {
                "guardrailIdentifier": guardrail_id,
                "guardrailVersion": guardrail_version or "DRAFT",
            }
        resp = self._runtime.converse(**kwargs)
        parts = resp["output"]["message"]["content"]
        return "".join(p.get("text", "") for p in parts).strip()

    # --------------------------------------------------------------- KB (RAG)
    def retrieve(self, kb_id: str | None, query: str, top_k: int = 3) -> list[str]:
        """Return the top relevant text chunks for a query."""
        if self.mock or not kb_id:
            return _mock_chunks(query)
        resp = self._agent_runtime.retrieve(
            knowledgeBaseId=kb_id,
            retrievalQuery={"text": query},
            retrievalConfiguration={"vectorSearchConfiguration": {"numberOfResults": top_k}},
        )
        return [r["content"]["text"] for r in resp.get("retrievalResults", [])]

    # ------------------------------------------------- guardrail (create ref)
    def create_guardrail(self, name: str, denied_topics: list[str],
                         blocked_phrases: list[str], pii: list[str]) -> dict:
        """Create a Bedrock guardrail; returns {id, version}. Mock returns a
        fake id so the pipeline is exercised end-to-end."""
        if self.mock:
            return {"id": f"mock-gr-{name}", "version": "DRAFT"}

        topic_policy = {
            "topicsConfig": [
                {"name": t[:100], "definition": t, "type": "DENY"} for t in denied_topics
            ]
        } if denied_topics else None
        word_policy = {
            "wordsConfig": [{"text": p} for p in blocked_phrases]
        } if blocked_phrases else None
        sensitive = {
            "piiEntitiesConfig": [{"type": p, "action": "BLOCK"} for p in pii]
        } if pii else None

        kwargs = {
            "name": name,
            "blockedInputMessaging": "I can't help with that request.",
            "blockedOutputsMessaging": "I can't provide that response.",
        }
        if topic_policy:
            kwargs["topicPolicyConfig"] = topic_policy
        if word_policy:
            kwargs["wordPolicyConfig"] = word_policy
        if sensitive:
            kwargs["sensitiveInformationPolicyConfig"] = sensitive

        resp = self._control.create_guardrail(**kwargs)
        return {"id": resp["guardrailId"], "version": resp.get("version", "DRAFT")}


# --------------------------------------------------------------------------
# Mock helpers — realistic enough to demo before AWS is wired.
# --------------------------------------------------------------------------
def _mock_chunks(query: str) -> list[str]:
    return [
        f"[KB] Reference material relevant to: {query}",
        "[KB] Policy excerpt describing the standard handling for this topic.",
    ]


def _mock_answer(system_prompt: str, messages: list[dict]) -> str:
    """Produce a plausible generic-vs-adapted contrast. The adapted (context
    pack) prompt is identifiable by its structured '## Role' header; the
    generic prompt is plain."""
    question = messages[-1]["text"] if messages else ""
    adapted = "## Role" in system_prompt
    if not adapted:
        return (
            f"Here's a general answer to \"{question}\". "
            "This is a plain, non-specialised response with no industry context."
        )
    # Pull the role line to flavour the mock adapted answer.
    role_line = ""
    for line in system_prompt.splitlines():
        if line.startswith("You are "):
            role_line = line
            break
    return (
        f"{role_line} Regarding \"{question}\": based on the loaded industry "
        "context and relevant knowledge-base material, here is a precise, "
        "policy-aware answer tailored to this industry, with escalation and "
        "safety rules applied."
    )


_client: BedrockClient | None = None


def get_bedrock() -> BedrockClient:
    global _client
    if _client is None:
        _client = BedrockClient()
    return _client
