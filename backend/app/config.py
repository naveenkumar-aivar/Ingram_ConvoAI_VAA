"""Central configuration, loaded from environment / .env.

Two independent switches control where things come from:

  MOCK_MODE      -> STORAGE only.
      True  = read/write context packs in the local `s3-data` folder.
      False = read/write real Amazon S3.

  LLM_PROVIDER   -> where chat answers (and the judge) come from.
      "mock"    = canned demo text, no LLM.
      "bedrock" = real LLM via Strands + Amazon Bedrock.

These are independent on purpose: MOCK_MODE controls storage (local folder vs
real S3) while LLM_PROVIDER controls the model (mock vs Bedrock).
"""
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Storage: True = local s3-data folder; False = real S3.
    mock_mode: bool = True

    # LLM provider for chat answers & the evaluator: "mock" | "bedrock".
    llm_provider: str = "mock"

    # AWS
    aws_region: str = "us-east-1"

    # Bedrock chat/answer model (confirm the exact id in the console before use).
    bedrock_model_id: str = "CONFIRM_IN_CONSOLE"

    # Storage locations (same layout for local folder and real S3).
    s3_bucket: str = "vertical-adaption-agent"
    context_pack_prefix: str = "context-pack/"
    industry_docs_prefix: str = "industry-docs/"

    # Local stand-in for S3, used when MOCK_MODE=true (relative to backend/).
    local_s3_dir: str = "../s3-data"
    # Uploaded knowledge-base files + local Chroma store (sibling of s3-data).
    kb_data_dir: str = "../kb-data"

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
