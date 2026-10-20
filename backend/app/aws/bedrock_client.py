"""Bedrock Runtime wrapper for chat and guardrail operations.

Provides a thin interface to AWS Bedrock for:
- Converse API (chat with guardrails)
- Agent Runtime Retrieve (KB vector search)
- Control Plane operations (create guardrails)
"""
from __future__ import annotations

import logging

from app.config import get_settings

logger = logging.getLogger(__name__)


class BedrockClient:
    def __init__(self) -> None:
        self.settings = get_settings()
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
        """Run a chat turn using Bedrock Converse API.

        Args:
            system_prompt: System context/instructions
            messages: List of {role, text} dicts (role: "user" or "assistant")
            guardrail_id: Optional Bedrock guardrail to apply
            guardrail_version: Guardrail version (defaults to "DRAFT")
            model_id: Model ID (defaults to config.bedrock_model_id)

        Returns:
            Assistant's text response
        """
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
        """Retrieve top relevant text chunks from Bedrock Knowledge Base.

        Args:
            kb_id: Knowledge Base ID
            query: Search query
            top_k: Number of results to return

        Returns:
            List of text chunks
        """
        if not kb_id:
            logger.warning("[bedrock] retrieve called with no KB ID")
            return []

        resp = self._agent_runtime.retrieve(
            knowledgeBaseId=kb_id,
            retrievalQuery={"text": query},
            retrievalConfiguration={"vectorSearchConfiguration": {"numberOfResults": top_k}},
        )
        return [r["content"]["text"] for r in resp.get("retrievalResults", [])]

    # ------------------------------------------------- guardrail (create ref)
    def create_guardrail(
        self,
        name: str,
        denied_topics: list[str],
        blocked_phrases: list[str],
        pii: list[str],
    ) -> dict:
        """Create a Bedrock guardrail.

        Args:
            name: Guardrail name
            denied_topics: Topics to deny
            blocked_phrases: Phrases to block
            pii: PII entity types to block (e.g., ["EMAIL", "PHONE"])

        Returns:
            dict with keys: id, version
        """
        topic_policy = (
            {
                "topicsConfig": [
                    {"name": t[:100], "definition": t, "type": "DENY"} for t in denied_topics
                ]
            }
            if denied_topics
            else None
        )
        word_policy = {"wordsConfig": [{"text": p} for p in blocked_phrases]} if blocked_phrases else None
        sensitive = (
            {"piiEntitiesConfig": [{"type": p, "action": "BLOCK"} for p in pii]}
            if pii
            else None
        )

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


_client: BedrockClient | None = None


def get_bedrock() -> BedrockClient:
    global _client
    if _client is None:
        _client = BedrockClient()
    return _client
