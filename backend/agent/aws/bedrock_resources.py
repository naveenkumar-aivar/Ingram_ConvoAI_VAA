"""Bedrock Resources Management.

Creates REAL Amazon Bedrock resources that reflect an approved ContextPack:

  * a Bedrock Guardrail whose topic / word / sensitive-information policies are
    mapped directly from the pack's guardrails (denied_topics, blocked_phrases,
    pii_blocks) — the pack is the single source of truth, no separate pieces;
  * a Bedrock Prompt Management resource built from the pack's composed system
    prompt (pack.to_system_prompt()).

All guardrail content is derived EXCLUSIVELY from the ContextPack passed in.

The guardrail is created in DRAFT state and then a numbered, usable version is
produced via create_guardrail_version. The prompt is created and then versioned
via create_prompt_version.

Public surface (kept stable so imports do not break):
  * class BedrockResourceManager
  * get_bedrock_manager()
  * exceptions BedrockResourcesError / GuardrailCreationError / PromptTemplateError
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Any, Dict, Optional

import boto3
from botocore.exceptions import ClientError

from agent.config import get_settings

logger = logging.getLogger(__name__)


class BedrockResourcesError(Exception):
    """Base exception for Bedrock resource operations."""
    pass


class GuardrailCreationError(BedrockResourcesError):
    """Raised when guardrail creation fails."""
    pass


class PromptTemplateError(BedrockResourcesError):
    """Raised when prompt-management resource creation fails."""
    pass


# PII token (as used in ContextPack.guardrails.pii_blocks) -> Bedrock PII entity type.
# Comprehensive coverage: personal identifiers, financial, medical, authentication data.
_PII_MAP = {
    "SSN": "US_SOCIAL_SECURITY_NUMBER",
    "EMAIL": "EMAIL",
    "PHONE": "PHONE",
    "CREDIT_CARD": "CREDIT_DEBIT_CARD_NUMBER",
    "NAME": "NAME",
    "ADDRESS": "ADDRESS",
    "ACCOUNT_NUMBER": "CREDIT_DEBIT_CARD_NUMBER",  # Bank/financial account numbers
    "DRIVER_LICENSE": "US_DRIVER_LICENSE_NUMBER",  # Driver's license
    "PASSPORT": "PASSPORT_ID",  # Passport number
    "IP_ADDRESS": "IP_ADDRESS",  # IPv4/IPv6 addresses
    "MAC_ADDRESS": "MAC_ADDRESS",  # MAC addresses
}

# Bedrock topic names must match [0-9a-zA-Z-_ !?.]+ and be reasonably short.
_TOPIC_NAME_RE = re.compile(r"[^0-9a-zA-Z\-_ !?.]")

_DEFAULT_MESSAGING = "I can't help with that request."


def _topic_name(text: str, idx: int) -> str:
    """Derive a short, valid guardrail topic name from denied-topic text."""
    cleaned = _TOPIC_NAME_RE.sub(" ", (text or "").strip())
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if not cleaned:
        cleaned = f"topic-{idx + 1}"
    return cleaned[:100]


def _assistant_slug(pack: Any) -> str:
    """Derive a short assistant-name slug from pack.meta.pack_id.

    Pack ids look like '<name>-vN'; the trailing '-vN' is stripped to recover
    the assistant '<name>'. Falls back to 'assistant' when unavailable.
    """
    pid = ""
    try:
        pid = (pack.meta.pack_id or "").strip().lower()
    except Exception:  # noqa: BLE001 — defensive, pack_id should always exist
        pid = ""
    base = re.sub(r"-v\d+$", "", pid) if pid else ""
    base = re.sub(r"[^0-9a-z\-]", "-", base).strip("-")
    return base or "assistant"


def _safe_name(industry: str, assistant: str, timestamp: str, max_len: int) -> str:
    """Build a sanitized '<industry>-<assistant>-<timestamp>' name, truncated to
    `max_len` chars while preserving the timestamp suffix so names stay unique.

    The industry/assistant portion is trimmed as needed; the '-<timestamp>'
    suffix is always kept intact.
    """
    industry = re.sub(r"[^0-9a-z\-]", "-", (industry or "").strip().lower()).strip("-") or "general"
    assistant = re.sub(r"[^0-9a-z\-]", "-", (assistant or "").strip().lower()).strip("-") or "assistant"
    suffix = f"-{timestamp}"
    # Budget for the industry/assistant part (keep the timestamp suffix whole).
    head_budget = max(1, max_len - len(suffix))
    head = f"{industry}-{assistant}"[:head_budget].rstrip("-")
    name = f"{head}{suffix}"
    return name[:max_len]


class BedrockResourceManager:
    """Manages Bedrock guardrails and prompt-management resources."""

    def __init__(self, region: str):
        self.region = region
        # Control-plane clients.
        self.bedrock_client = boto3.client("bedrock", region_name=region)
        self.bedrock_agent = boto3.client("bedrock-agent", region_name=region)

    # ------------------------------------------------------------------
    # Guardrail
    # ------------------------------------------------------------------
    def create_guardrail_from_pack(self, pack: Any) -> Dict[str, str]:
        """Create a Bedrock guardrail from the ContextPack's guardrails.

        `pack` is a ContextPack (pydantic) instance. Guardrail content is taken
        ONLY from pack.meta + pack.guardrails. Returns {id, version, arn}.

        Raises GuardrailCreationError on AWS failure.
        """
        g = pack.guardrails
        industry = (pack.meta.industry or "general").strip().lower().replace(" ", "-") or "general"
        assistant = _assistant_slug(pack)
        timestamp = datetime.utcnow().strftime("%m%d%H%M%S")
        # Guardrail name must be <= 50 chars; description must be <= 200 chars.
        name = _safe_name(industry, assistant, timestamp, max_len=50)
        description = f"VAA guardrail for {industry}/{assistant}"[:200]

        denied_topics = [t for t in (g.denied_topics or []) if isinstance(t, str) and t.strip()]
        blocked_phrases = [p for p in (g.blocked_phrases or []) if isinstance(p, str) and p.strip()]
        pii_blocks = [p for p in (g.pii_blocks or []) if isinstance(p, str) and p.strip()]

        kwargs: Dict[str, Any] = {
            "name": name,
            "description": description,
            "blockedInputMessaging": _DEFAULT_MESSAGING,
            "blockedOutputsMessaging": _DEFAULT_MESSAGING,
        }

        # Topic policy — one entry per denied topic.
        if denied_topics:
            kwargs["topicPolicyConfig"] = {
                "topicsConfig": [
                    {
                        "name": _topic_name(topic, idx),
                        "definition": topic.strip(),
                        "type": "DENY",
                    }
                    for idx, topic in enumerate(denied_topics)
                ]
            }

        # Word policy — one entry per blocked phrase. Omit entirely if empty.
        if blocked_phrases:
            kwargs["wordPolicyConfig"] = {
                "wordsConfig": [{"text": phrase.strip()} for phrase in blocked_phrases]
            }

        # Sensitive-information policy — map known PII tokens, skip unknown.
        # Action ANONYMIZE: mask PII in place (e.g., "123-45-6789" → "***-**-6789")
        # This allows LLM to answer while protecting sensitive data.
        pii_entities = []
        for token in pii_blocks:
            mapped = _PII_MAP.get(token.strip().upper())
            if mapped:
                pii_entities.append({"type": mapped, "action": "ANONYMIZE"})
        if pii_entities:
            kwargs["sensitiveInformationPolicyConfig"] = {"piiEntitiesConfig": pii_entities}

        try:
            logger.info(f"[bedrock] Creating guardrail {name}")
            resp = self.bedrock_client.create_guardrail(**kwargs)
            guardrail_id = resp["guardrailId"]
            guardrail_arn = resp.get("guardrailArn", "")
        except ClientError as e:
            msg = e.response.get("Error", {}).get("Message", str(e))
            logger.error(f"[bedrock] Guardrail creation failed: {msg}")
            raise GuardrailCreationError(f"Guardrail creation failed: {msg}") from e

        # Produce a numbered, usable version from the DRAFT guardrail.
        version = "DRAFT"
        try:
            vresp = self.bedrock_client.create_guardrail_version(
                guardrailIdentifier=guardrail_id,
                description=f"Version for {name}",
            )
            version = str(vresp.get("version", "DRAFT"))
        except ClientError as e:
            msg = e.response.get("Error", {}).get("Message", str(e))
            logger.warning(f"[bedrock] create_guardrail_version failed, using DRAFT: {msg}")

        logger.info(f"[bedrock] Guardrail created: {guardrail_id} (v{version})")
        return {"id": guardrail_id, "version": version, "arn": guardrail_arn}

    # ------------------------------------------------------------------
    # Prompt Management
    # ------------------------------------------------------------------
    def create_prompt_from_pack(self, pack: Any) -> Dict[str, str]:
        """Create a Bedrock Prompt Management resource from the pack's composed
        system prompt. Returns {id, arn, version}.

        Raises PromptTemplateError on AWS failure.
        """
        settings = get_settings()
        industry = (pack.meta.industry or "general").strip().lower().replace(" ", "-") or "general"
        assistant = _assistant_slug(pack)
        timestamp = datetime.utcnow().strftime("%m%d%H%M%S")
        # Prompt name kept <= 100 chars; description must be <= 200 chars.
        name = _safe_name(industry, assistant, timestamp, max_len=100)
        description = f"VAA prompt for {industry}/{assistant}"[:200]

        system_prompt = pack.to_system_prompt()

        try:
            logger.info(f"[bedrock] Creating prompt {name}")
            resp = self.bedrock_agent.create_prompt(
                name=name,
                description=description,
                variants=[
                    {
                        "name": "default",
                        "templateType": "TEXT",
                        "templateConfiguration": {"text": {"text": system_prompt}},
                        "modelId": settings.bedrock_model_id,
                    }
                ],
            )
            prompt_id = resp["id"]
            prompt_arn = resp.get("arn", "")
        except ClientError as e:
            msg = e.response.get("Error", {}).get("Message", str(e))
            logger.error(f"[bedrock] Prompt creation failed: {msg}")
            raise PromptTemplateError(f"Prompt creation failed: {msg}") from e

        # Produce a numbered version; keep the draft prompt if versioning fails.
        version = "DRAFT"
        try:
            vresp = self.bedrock_agent.create_prompt_version(
                promptIdentifier=prompt_id,
                description=f"Version for {name}",
            )
            version = str(vresp.get("version", "DRAFT"))
            prompt_arn = vresp.get("arn", prompt_arn)
        except ClientError as e:
            msg = e.response.get("Error", {}).get("Message", str(e))
            logger.warning(f"[bedrock] create_prompt_version failed, using draft: {msg}")

        logger.info(f"[bedrock] Prompt created: {prompt_id} (v{version})")
        return {"id": prompt_id, "arn": prompt_arn, "version": version}

# Singleton instance.
_manager: Optional[BedrockResourceManager] = None


def get_bedrock_manager(region: Optional[str] = None) -> BedrockResourceManager:
    """Get or create the Bedrock resource manager singleton.

    Region defaults to agent.config.get_settings().aws_region.
    """
    global _manager
    if _manager is None:
        _manager = BedrockResourceManager(region or get_settings().aws_region)
    return _manager
