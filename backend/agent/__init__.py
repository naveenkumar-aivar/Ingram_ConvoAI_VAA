"""Vertical Adaptation Agent — the dockerized agent package.

This package holds ALL domain/agent logic: the Strands conversational agent,
the pack-generator agent and its tools, the LLM/Bedrock wrappers, the evaluator,
the compare orchestration, drafts, sessions, and the AWS integration layer.

It is deployed as a container image (ECR) and run inside Amazon Bedrock
AgentCore Runtime. The FastAPI layer (``app/``) calls into this package through
``app/agent_client.py`` so the HTTP layer stays deployment-agnostic.
"""
