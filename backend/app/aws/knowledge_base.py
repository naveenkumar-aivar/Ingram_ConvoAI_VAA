"""AWS Bedrock Knowledge Base integration only.

All retrieval comes from AWS Bedrock managed KBs.
No local Chroma fallback - AWS only.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)
