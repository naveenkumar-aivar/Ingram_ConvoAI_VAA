"""AWS Production Configuration - Bedrock KB Only.

All systems use real AWS services. No local fallback.
"""
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # AWS Configuration
    aws_region: str = "us-east-1"

    # Bedrock chat/answer model
    bedrock_model_id: str = "CONFIRM_IN_CONSOLE"

    # IAM role for Bedrock Knowledge Bases
    kb_role_name: str = "vaa-bedrock-kb-role"

    # Chat session store (DynamoDB)
    sessions_table_name: str = "vaa-sessions"
    session_ttl_days: int = 30

    # S3 Storage
    s3_bucket: str = "vertical-adaption-agent"
    context_pack_prefix: str = "context-pack/"
    industry_docs_prefix: str = "industry-docs/"

    # Server
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    cors_origins: str = "http://localhost:5173"

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
