"""AWS Bedrock Managed Knowledge Base integration - Production Only.

All retrieval uses managed Bedrock KBs. No local fallback.

Public surface:
  ensure_kb_role() → role_arn
  create_managed_kb(industry, usecase) → {knowledge_base_id, data_source_id, status}
  ingest_kb(kb_id, ds_id) → job_id
  retrieve_kb(kb_id, query, top_k) → list[str] (never raises)
  delete_kb(kb_id) → None
  KBError
"""
from __future__ import annotations

import json
import logging
import re
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

import boto3
from botocore.exceptions import ClientError

from agent.config import get_settings

logger = logging.getLogger(__name__)


class KBError(Exception):
    """Raised when a required managed-KB provisioning step fails."""
    pass


# Cache the self-provisioned role ARN so repeated approvals don't re-check IAM.
_role_arn_cache: Optional[str] = None

# Cache the AWS account id (used as the S3 data-source bucketOwnerAccountId) so
# repeated managed-KB creations don't re-hit STS.
_account_id: Optional[str] = None

# KB names must match [0-9a-zA-Z][0-9a-zA-Z-_]{0,99} (<=100 chars).
_KB_NAME_RE = re.compile(r"[^0-9a-zA-Z\-_]")

_INLINE_POLICY_NAME = "vaa-kb-access"


# ---------------------------------------------------------------------------
# Clients
# ---------------------------------------------------------------------------
def _region() -> str:
    return get_settings().aws_region


def _agent_client():
    return boto3.client("bedrock-agent", region_name=_region())


def _runtime_client():
    return boto3.client("bedrock-agent-runtime", region_name=_region())


def _iam_client():
    # IAM is a global service; region is harmless.
    return boto3.client("iam", region_name=_region())


def _sts_client():
    return boto3.client("sts", region_name=_region())


def _get_account_id() -> str:
    """Return the current AWS account id, caching it in a module global so
    repeated managed-KB creations don't re-hit STS."""
    global _account_id
    if _account_id:
        return _account_id
    try:
        _account_id = _sts_client().get_caller_identity()["Account"]
    except ClientError as e:
        raise KBError(f"Could not resolve AWS account id: {e}") from e
    return _account_id


def _bucket() -> str:
    return get_settings().s3_bucket


def _inclusion_prefix(industry: str, usecase: str) -> str:
    """S3 key prefix where uploaded files live (matches s3_manager layout):
    industry-docs/<industry>/<usecase>/uploaded_data/  (both lowercased)."""
    s = get_settings()
    prefix = s.industry_docs_prefix.strip("/")  # "industry-docs"
    ind = (industry or "").strip().lower()
    uc = (usecase or "").strip().lower()
    return f"{prefix}/{ind}/{uc}/uploaded_data/"


def _sanitize_kb_name(name: str) -> str:
    """Coerce to the KB name pattern: first char alphanumeric, rest in
    [0-9a-zA-Z-_], max 100 chars."""
    cleaned = _KB_NAME_RE.sub("-", (name or "").strip())
    cleaned = cleaned.strip("-_") or "vaa-kb"
    if not cleaned[0].isalnum():
        cleaned = "k" + cleaned
    return cleaned[:100]


# ---------------------------------------------------------------------------
# A1. IAM role (self-provisioned, idempotent)
# ---------------------------------------------------------------------------
def ensure_kb_role() -> str:
    """Return the ARN of the Bedrock-KB execution role, creating it if absent.

    Idempotent: re-uses an existing role; creates it once with a trust policy
    for bedrock.amazonaws.com (confused-deputy protected via aws:SourceAccount /
    aws:SourceArn) and an inline policy granting S3 read on the bucket plus
    bedrock model invoke/rerank. Caches the ARN in a module global.
    """
    global _role_arn_cache
    if _role_arn_cache:
        return _role_arn_cache

    settings = get_settings()
    role_name = settings.kb_role_name
    region = settings.aws_region
    iam = _iam_client()

    try:
        account_id = _sts_client().get_caller_identity()["Account"]
    except ClientError as e:
        raise KBError(f"Could not resolve AWS account id: {e}") from e

    # 1. Re-use existing role if present.
    try:
        resp = iam.get_role(RoleName=role_name)
        _role_arn_cache = resp["Role"]["Arn"]
        logger.info(f"[bedrock-kb] Reusing KB role {role_name}")
        return _role_arn_cache
    except ClientError as e:
        if e.response.get("Error", {}).get("Code") != "NoSuchEntity":
            raise KBError(f"get_role failed for {role_name}: {e}") from e

    # 2. Create the role with a confused-deputy-protected trust policy.
    trust_policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Principal": {"Service": "bedrock.amazonaws.com"},
                "Action": "sts:AssumeRole",
                "Condition": {
                    "StringEquals": {"aws:SourceAccount": account_id},
                    "ArnLike": {
                        "aws:SourceArn": f"arn:aws:bedrock:{region}:{account_id}:knowledge-base/*"
                    },
                },
            }
        ],
    }
    try:
        resp = iam.create_role(
            RoleName=role_name,
            AssumeRolePolicyDocument=json.dumps(trust_policy),
            Description="Execution role for VAA managed Bedrock Knowledge Bases.",
        )
        role_arn = resp["Role"]["Arn"]
        logger.info(f"[bedrock-kb] Created KB role {role_name}")
    except ClientError as e:
        raise KBError(f"create_role failed for {role_name}: {e}") from e

    # 3. Attach inline access policy.
    bucket = _bucket()
    access_policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Sid": "S3List",
                "Effect": "Allow",
                "Action": ["s3:ListBucket"],
                "Resource": [f"arn:aws:s3:::{bucket}"],
            },
            {
                "Sid": "S3Get",
                "Effect": "Allow",
                "Action": ["s3:GetObject"],
                "Resource": [f"arn:aws:s3:::{bucket}/*"],
            },
            {
                "Sid": "BedrockModel",
                "Effect": "Allow",
                "Action": ["bedrock:InvokeModel", "bedrock:Rerank"],
                "Resource": ["*"],
            },
        ],
    }
    try:
        iam.put_role_policy(
            RoleName=role_name,
            PolicyName=_INLINE_POLICY_NAME,
            PolicyDocument=json.dumps(access_policy),
        )
    except ClientError as e:
        raise KBError(f"put_role_policy failed for {role_name}: {e}") from e

    # 4. Fresh roles need a moment to propagate before Bedrock can assume them.
    time.sleep(10)

    _role_arn_cache = role_arn
    return role_arn


# ---------------------------------------------------------------------------
# A2. Create a managed KB + its S3 data source
# ---------------------------------------------------------------------------
def create_managed_kb(industry: str, usecase: str) -> Dict[str, str]:
    """Create a managed Bedrock KB and an S3 data source pointing at the
    uploaded files for (industry, usecase). Returns
    {knowledge_base_id, data_source_id, status}. Raises KBError on failure."""
    role_arn = ensure_kb_role()
    agent = _agent_client()

    short_ts = datetime.utcnow().strftime("%m%d%H%M%S")
    kb_name = _sanitize_kb_name(f"vaa-{industry}-{usecase}-{short_ts}")
    description = f"Managed KB for {industry}/{usecase}."

    # Create the knowledge base (managed embeddings — no storageConfiguration).
    try:
        resp = agent.create_knowledge_base(
            name=kb_name,
            description=description,
            roleArn=role_arn,
            knowledgeBaseConfiguration={
                "type": "MANAGED",
                "managedKnowledgeBaseConfiguration": {"embeddingModelType": "MANAGED"},
            },
        )
        kb_id = resp["knowledgeBase"]["knowledgeBaseId"]
        logger.info(f"[bedrock-kb] Creating KB {kb_name} -> {kb_id}")
    except ClientError as e:
        raise KBError(f"create_knowledge_base failed: {e}") from e

    # Poll until the KB is ACTIVE (or FAILED / timeout ~60s).
    status = _poll_kb_active(agent, kb_id, timeout_s=60)
    if status == "FAILED":
        raise KBError(f"Knowledge base {kb_id} entered FAILED state")

    # Create the S3 data source over the uploaded_data/ prefix.
    #
    # A MANAGED Bedrock KB does NOT accept the plain {"type": "S3", ...} data
    # source — it requires the managed connector shape below. The S3 details go
    # under managedKnowledgeBaseConnectorConfiguration.connectorParameters as a
    # plain bucket name + owner account id (not a bucket ARN).
    bucket_name = _bucket()
    account_id = _get_account_id()
    inclusion_prefix = _inclusion_prefix(industry, usecase)
    try:
        ds_resp = agent.create_data_source(
            knowledgeBaseId=kb_id,
            name="s3-uploaded-data",
            dataSourceConfiguration={
                "type": "MANAGED_KNOWLEDGE_BASE_CONNECTOR",
                "managedKnowledgeBaseConnectorConfiguration": {
                    "connectorParameters": {
                        "type": "S3",
                        "version": "1",
                        "connectionConfiguration": {
                            "bucketName": bucket_name,
                            "bucketOwnerAccountId": account_id,
                        },
                        "filterConfiguration": {
                            "inclusionPrefixes": [inclusion_prefix],
                        },
                    },
                },
            },
        )
        ds_id = ds_resp["dataSource"]["dataSourceId"]
        logger.info(f"[bedrock-kb] Created data source {ds_id} (prefix {inclusion_prefix})")
    except ClientError as e:
        raise KBError(f"create_data_source failed for KB {kb_id}: {e}") from e

    # Managed-KB data sources are created ASYNChronously (CREATING -> AVAILABLE,
    # can take 2-5 min). Wait for AVAILABLE before any ingestion is started.
    ds_status = _poll_ds_available(agent, kb_id, ds_id, timeout_s=300)
    if ds_status == "FAILED":
        raise KBError(f"Data source {ds_id} entered FAILED state")
    if ds_status != "AVAILABLE":
        raise KBError(
            f"Data source {ds_id} not AVAILABLE after timeout (last status {ds_status})"
        )

    return {"knowledge_base_id": kb_id, "data_source_id": ds_id, "status": status}


def _poll_ds_available(agent, kb_id: str, ds_id: str, timeout_s: int = 300) -> str:
    """Poll get_data_source until status is AVAILABLE/FAILED or timeout.
    Returns the last observed status."""
    deadline = time.time() + timeout_s
    status = "CREATING"
    while time.time() < deadline:
        try:
            resp = agent.get_data_source(knowledgeBaseId=kb_id, dataSourceId=ds_id)
            status = resp["dataSource"]["status"]
        except ClientError as e:
            raise KBError(f"get_data_source failed for {ds_id}: {e}") from e
        if status in ("AVAILABLE", "FAILED"):
            return status
        time.sleep(10)
    return status


def _poll_kb_active(agent, kb_id: str, timeout_s: int = 60) -> str:
    """Poll get_knowledge_base until status is ACTIVE/FAILED or timeout.
    Returns the last observed status."""
    deadline = time.time() + timeout_s
    status = "CREATING"
    while time.time() < deadline:
        try:
            resp = agent.get_knowledge_base(knowledgeBaseId=kb_id)
            status = resp["knowledgeBase"]["status"]
        except ClientError as e:
            raise KBError(f"get_knowledge_base failed for {kb_id}: {e}") from e
        if status in ("ACTIVE", "FAILED"):
            return status
        time.sleep(3)
    return status


# ---------------------------------------------------------------------------
# A3. Ingestion
# ---------------------------------------------------------------------------
def ingest_kb(knowledge_base_id: str, data_source_id: str) -> str:
    """Start an ingestion job and return immediately with the job ID.
    The ingestion runs asynchronously in AWS; we do not poll for completion
    since that can take 5–10+ minutes. Returns the job_id so the caller can
    track it (or "STARTED" on success, "FAILED" on error)."""
    agent = _agent_client()
    try:
        resp = agent.start_ingestion_job(
            knowledgeBaseId=knowledge_base_id,
            dataSourceId=data_source_id,
        )
        job_id = resp["ingestionJob"]["ingestionJobId"]
        logger.info(f"[bedrock-kb] Started ingestion job {job_id} for KB {knowledge_base_id}")
        return job_id  # Return the job id so the caller knows it was started
    except ClientError as e:
        logger.error(f"[bedrock-kb] start_ingestion_job failed for KB {knowledge_base_id}: {e}")
        raise KBError(f"start_ingestion_job failed for KB {knowledge_base_id}: {e}") from e


# ---------------------------------------------------------------------------
# A4. Retrieval (never raises — must not break chat)
# ---------------------------------------------------------------------------
def retrieve_kb(knowledge_base_id: str, query: str, top_k: int = 4) -> List[str]:
    """Retrieve the top matching chunk texts for a query. Returns [] on any
    error so chat retrieval failures are always non-fatal."""
    if not knowledge_base_id or not (query or "").strip():
        return []
    try:
        runtime = _runtime_client()
        resp = runtime.retrieve(
            knowledgeBaseId=knowledge_base_id,
            retrievalQuery={"text": query},
            retrievalConfiguration={
                "managedSearchConfiguration": {}
            },
        )
        texts: List[str] = []
        for result in resp.get("retrievalResults", []):
            text = (result.get("content") or {}).get("text")
            if text:
                texts.append(text)
        # managedSearchConfiguration does not accept numberOfResults, so cap the
        # returned results here to preserve the top_k limit callers expect.
        if isinstance(top_k, int) and top_k > 0:
            texts = texts[:top_k]
        return texts
    except Exception as exc:  # noqa: BLE001 — retrieval must never raise
        logger.warning(f"[bedrock-kb] retrieve failed (non-fatal): {exc}")
        return []


# ---------------------------------------------------------------------------
# A5. Delete (best effort, for demo/test cleanup)
# ---------------------------------------------------------------------------
def delete_kb(knowledge_base_id: str) -> None:
    """Best-effort delete of a managed KB. Swallows all errors."""
    if not knowledge_base_id:
        return
    try:
        _agent_client().delete_knowledge_base(knowledgeBaseId=knowledge_base_id)
        logger.info(f"[bedrock-kb] Deleted KB {knowledge_base_id}")
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"[bedrock-kb] delete_knowledge_base failed (ignored): {exc}")
