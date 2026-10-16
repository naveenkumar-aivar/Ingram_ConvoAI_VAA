"""Bedrock Resources Management

Handles creation and management of Bedrock Guardrails and Prompt Templates
from stored context pack pieces in S3.

Workflow:
  1. Load guardrails + prompts pieces from S3
  2. Create Bedrock guardrail (denied topics, blocked phrases, PII detection)
  3. Create Bedrock prompt template (system prompt)
  4. Update context pack with resource IDs
  5. Persist updated pack to S3
"""

import json
import logging
from datetime import datetime
from typing import Optional, Dict, Any

import boto3
from botocore.exceptions import ClientError, BotoCoreError

logger = logging.getLogger(__name__)


class BedrockResourcesError(Exception):
    """Base exception for Bedrock resource operations."""
    pass


class GuardrailCreationError(BedrockResourcesError):
    """Raised when guardrail creation fails."""
    pass


class PromptTemplateError(BedrockResourcesError):
    """Raised when prompt template creation fails."""
    pass


class BedrockResourceManager:
    """Manages Bedrock guardrails and prompt templates."""

    def __init__(self, region: str = "us-east-1"):
        """Initialize Bedrock client.

        Args:
            region: AWS region for Bedrock service.
        """
        self.region = region
        self.bedrock_client = boto3.client("bedrock", region_name=region)
        self.bedrock_runtime = boto3.client("bedrock-runtime", region_name=region)

    def create_guardrail(
        self,
        industry: str,
        usecase: str,
        guardrails_data: Dict[str, Any],
        description: Optional[str] = None,
    ) -> Dict[str, str]:
        """Create a Bedrock guardrail from guardrails piece data.

        Args:
            industry: Industry name (e.g., 'banking', 'healthcare')
            usecase: Use case name (e.g., 'support', 'assist')
            guardrails_data: Guardrails piece data with structure:
                {
                  "denied_topics": [{"name": "...", "definition": "..."}, ...],
                  "blocked_phrases": ["phrase1", "phrase2", ...],
                  "pii_detection": {
                    "email": true,
                    "phone": true,
                    "ssn": true,
                    "credit_card": true
                  }
                }
            description: Optional description for the guardrail.

        Returns:
            Dict with keys:
              - id: Guardrail ID
              - version: Guardrail version
              - arn: Guardrail ARN
              - created_at: Creation timestamp

        Raises:
            GuardrailCreationError: If guardrail creation fails.
        """
        try:
            # Build guardrail name (must be unique, include timestamp)
            timestamp = datetime.utcnow().strftime("%Y%m%d%H%M%S")
            guardrail_name = f"{industry}-{usecase}-guardrail-{timestamp}"

            # Default description if not provided
            if not description:
                description = f"Guardrail for {industry}/{usecase} context pack"

            # Build topic config (denied topics)
            topic_configs = []
            for topic in guardrails_data.get("denied_topics", []):
                topic_configs.append(
                    {
                        "name": topic.get("name", "unknown"),
                        "definition": topic.get(
                            "definition", "Topic to be denied"
                        ),
                        "examples": topic.get("examples", []),
                    }
                )

            # Build content policy (blocked phrases and PII)
            content_policy_config = {
                "filters": [],
                "contextualGroundingFilterEnabled": True,
            }

            # Add blocked phrases filter
            if guardrails_data.get("blocked_phrases"):
                content_policy_config["filters"].append(
                    {
                        "type": "PROFANITY",
                        "strength": "HIGH",
                        "regexFilter": {
                            "pattern": "|".join(
                                guardrails_data.get("blocked_phrases", [])
                            )
                        },
                    }
                )

            # Add PII filter
            pii_config = guardrails_data.get("pii_detection", {})
            if pii_config:
                pii_entities = []
                if pii_config.get("email"):
                    pii_entities.append("EMAIL")
                if pii_config.get("phone"):
                    pii_entities.append("PHONE")
                if pii_config.get("ssn"):
                    pii_entities.append("SSN")
                if pii_config.get("credit_card"):
                    pii_entities.append("CREDIT_CARD_NUMBER")

                if pii_entities:
                    content_policy_config["filters"].append(
                        {
                            "type": "PII",
                            "strength": "MEDIUM",
                            "piiEntities": pii_entities,
                        }
                    )

            # Create guardrail
            logger.info(
                f"[bedrock] Creating guardrail: {guardrail_name} for {industry}/{usecase}"
            )
            response = self.bedrock_client.create_guardrail(
                name=guardrail_name,
                description=description,
                topicPolicyConfig={"topicsConfig": topic_configs}
                if topic_configs
                else None,
                contentPolicyConfig=content_policy_config
                if content_policy_config["filters"]
                else None,
                tags={
                    "Industry": industry,
                    "UseCase": usecase,
                    "ManagedBy": "VerticalAdaptationAgent",
                },
            )

            guardrail_id = response["guardrailId"]
            guardrail_version = response["version"]
            guardrail_arn = response.get("guardrailArn", "")

            logger.info(
                f"[bedrock] Guardrail created: {guardrail_id} (v{guardrail_version})"
            )

            return {
                "id": guardrail_id,
                "version": guardrail_version,
                "arn": guardrail_arn,
                "name": guardrail_name,
                "created_at": datetime.utcnow().isoformat(),
            }

        except ClientError as e:
            error_code = e.response["Error"]["Code"]
            error_msg = e.response["Error"]["Message"]
            logger.error(
                f"[bedrock] Guardrail creation failed: {error_code} - {error_msg}"
            )
            raise GuardrailCreationError(
                f"Failed to create guardrail for {industry}/{usecase}: {error_msg}"
            ) from e
        except (BotoCoreError, Exception) as e:
            logger.error(f"[bedrock] Unexpected error creating guardrail: {e}")
            raise GuardrailCreationError(
                f"Unexpected error creating guardrail: {str(e)}"
            ) from e

    def create_prompt_template(
        self,
        industry: str,
        usecase: str,
        prompts_data: Dict[str, Any],
        model_id: str = "us.anthropic.claude-haiku-4-5-20251001-v1:0",
    ) -> Dict[str, str]:
        """Create a Bedrock prompt template from prompts piece data.

        Note: Bedrock doesn't have a dedicated "prompt template" service like some
        other platforms. Instead, we store the template in a custom way and reference
        it. This function creates a record that can be used when calling InvokeModel.

        Args:
            industry: Industry name
            usecase: Use case name
            prompts_data: Prompts piece data with structure:
                {
                  "system_prompt_lines": ["line1", "line2", ...],
                  "temperature": 0.7,  # optional
                  "max_tokens": 1024   # optional
                }
            model_id: Model ID to use with this template.

        Returns:
            Dict with keys:
              - id: Template ID (generated)
              - name: Template name
              - system_prompt: Full system prompt text
              - model_id: Model ID
              - temperature: Temperature setting
              - max_tokens: Max tokens setting
              - created_at: Creation timestamp
        """
        try:
            timestamp = datetime.utcnow().strftime("%Y%m%d%H%M%S")
            template_id = f"{industry}-{usecase}-prompt-{timestamp}"
            template_name = f"{industry}/{usecase} Prompt Template"

            # Build system prompt from lines
            system_prompt_lines = prompts_data.get("system_prompt_lines", [])
            system_prompt = "\n".join(system_prompt_lines) if system_prompt_lines else ""

            # Get optional parameters
            temperature = prompts_data.get("temperature", 0.7)
            max_tokens = prompts_data.get("max_tokens", 1024)

            logger.info(
                f"[bedrock] Created prompt template: {template_id} for {industry}/{usecase}"
            )

            return {
                "id": template_id,
                "name": template_name,
                "system_prompt": system_prompt,
                "model_id": model_id,
                "temperature": temperature,
                "max_tokens": max_tokens,
                "created_at": datetime.utcnow().isoformat(),
            }

        except Exception as e:
            logger.error(f"[bedrock] Error creating prompt template: {e}")
            raise PromptTemplateError(
                f"Failed to create prompt template for {industry}/{usecase}: {str(e)}"
            ) from e

    def sync_guardrail_to_pack(
        self,
        industry: str,
        usecase: str,
        pack_data: Dict[str, Any],
        guardrails_piece: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Create Bedrock guardrail and update pack with its ID.

        Args:
            industry: Industry name
            usecase: Use case name
            pack_data: Context pack data
            guardrails_piece: Guardrails piece from S3

        Returns:
            Updated pack_data with guardrail_id in integrations.

        Raises:
            GuardrailCreationError: If creation fails.
        """
        try:
            guardrail_response = self.create_guardrail(
                industry, usecase, guardrails_piece
            )

            # Update pack integrations
            if "integrations" not in pack_data:
                pack_data["integrations"] = {}

            pack_data["integrations"]["guardrail_id"] = guardrail_response["id"]
            pack_data["integrations"]["guardrail_version"] = guardrail_response[
                "version"
            ]
            pack_data["integrations"]["guardrail_arn"] = guardrail_response["arn"]
            pack_data["integrations"]["guardrail_created_at"] = guardrail_response[
                "created_at"
            ]

            logger.info(
                f"[bedrock] Pack updated with guardrail: {guardrail_response['id']}"
            )
            return pack_data

        except GuardrailCreationError:
            raise
        except Exception as e:
            logger.error(f"[bedrock] Error syncing guardrail to pack: {e}")
            raise

    def sync_prompt_to_pack(
        self,
        industry: str,
        usecase: str,
        pack_data: Dict[str, Any],
        prompts_piece: Dict[str, Any],
        model_id: str = "us.anthropic.claude-haiku-4-5-20251001-v1:0",
    ) -> Dict[str, Any]:
        """Create Bedrock prompt template and update pack with its ID.

        Args:
            industry: Industry name
            usecase: Use case name
            pack_data: Context pack data
            prompts_piece: Prompts piece from S3
            model_id: Model ID to associate with template

        Returns:
            Updated pack_data with prompt_template_id in integrations.

        Raises:
            PromptTemplateError: If creation fails.
        """
        try:
            prompt_response = self.create_prompt_template(
                industry, usecase, prompts_piece, model_id
            )

            # Update pack integrations
            if "integrations" not in pack_data:
                pack_data["integrations"] = {}

            pack_data["integrations"]["prompt_template_id"] = prompt_response["id"]
            pack_data["integrations"]["prompt_template_name"] = prompt_response[
                "name"
            ]
            pack_data["integrations"]["prompt_system_prompt"] = prompt_response[
                "system_prompt"
            ]
            pack_data["integrations"]["prompt_model_id"] = prompt_response[
                "model_id"
            ]
            pack_data["integrations"]["prompt_temperature"] = prompt_response[
                "temperature"
            ]
            pack_data["integrations"]["prompt_max_tokens"] = prompt_response[
                "max_tokens"
            ]
            pack_data["integrations"]["prompt_created_at"] = prompt_response[
                "created_at"
            ]

            logger.info(
                f"[bedrock] Pack updated with prompt template: {prompt_response['id']}"
            )
            return pack_data

        except PromptTemplateError:
            raise
        except Exception as e:
            logger.error(f"[bedrock] Error syncing prompt to pack: {e}")
            raise

    def sync_to_pack(
        self,
        industry: str,
        usecase: str,
        pack_data: Dict[str, Any],
        guardrails_piece: Optional[Dict[str, Any]] = None,
        prompts_piece: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Create Bedrock guardrail + prompt template and update pack.

        This is the main entry point. It orchestrates:
          1. Creating guardrail from guardrails_piece
          2. Creating prompt template from prompts_piece
          3. Updating pack with both IDs

        Args:
            industry: Industry name
            usecase: Use case name
            pack_data: Context pack data (will be modified in place)
            guardrails_piece: Guardrails piece data (if None, skipped)
            prompts_piece: Prompts piece data (if None, skipped)

        Returns:
            Updated pack_data with both resource IDs in integrations.

        Raises:
            GuardrailCreationError: If guardrail creation fails.
            PromptTemplateError: If prompt template creation fails.
        """
        logger.info(
            f"[bedrock] Syncing resources to pack {industry}/{usecase}"
        )

        try:
            # Create guardrail if piece provided
            if guardrails_piece:
                pack_data = self.sync_guardrail_to_pack(
                    industry, usecase, pack_data, guardrails_piece
                )

            # Create prompt template if piece provided
            if prompts_piece:
                pack_data = self.sync_prompt_to_pack(
                    industry, usecase, pack_data, prompts_piece
                )

            logger.info(
                f"[bedrock] Pack sync complete for {industry}/{usecase}"
            )
            return pack_data

        except (GuardrailCreationError, PromptTemplateError):
            raise
        except Exception as e:
            logger.error(f"[bedrock] Unexpected error during pack sync: {e}")
            raise BedrockResourcesError(
                f"Failed to sync resources to pack: {str(e)}"
            ) from e


# Singleton instance
_manager: Optional[BedrockResourceManager] = None


def get_bedrock_manager(region: str = "us-east-1") -> BedrockResourceManager:
    """Get or create the Bedrock resource manager singleton.

    Args:
        region: AWS region for Bedrock service.

    Returns:
        BedrockResourceManager instance.
    """
    global _manager
    if _manager is None:
        _manager = BedrockResourceManager(region)
    return _manager
