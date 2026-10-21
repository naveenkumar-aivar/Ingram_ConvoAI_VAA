"""
S3 Manager - Handle S3 operations for uploaded files, industry-docs pieces,
and context packs.
"""

import boto3
import json
import logging
import os
from typing import Optional, Dict, Any, List
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

class S3Manager:
    """Manages S3 operations for Vertical Adaptation Agent"""
    
    def __init__(self):
        self.region = os.getenv("AWS_REGION", "us-east-1")
        self.bucket = os.getenv("S3_BUCKET", "vertical-adaption-agent")
        self.s3_client = boto3.client("s3", region_name=self.region)
        self.s3_resource = boto3.resource("s3", region_name=self.region)
        
        # Prefixes
        self.industry_docs_prefix = os.getenv("INDUSTRY_DOCS_PREFIX", "industry-docs/")
        self.context_pack_prefix = os.getenv("CONTEXT_PACK_PREFIX", "context-pack/")
    
    # ========== UPLOADED FILES ==========
    
    def save_uploaded_file(
        self,
        industry: str,
        usecase: str,
        file_path: str,
        file_content: bytes
    ) -> bool:
        """
        Save uploaded data file to S3
        Path: industry-docs/{industry}/{usecase}/uploaded_data/{filename}
        (industry/usecase lowercased for the key to avoid duplicate prefixes).
        """
        try:
            industry = (industry or "").strip().lower()
            usecase = (usecase or "").strip().lower()
            filename = os.path.basename(file_path)
            key = f"{self.industry_docs_prefix}{industry}/{usecase}/uploaded_data/{filename}"
            self.s3_client.put_object(
                Bucket=self.bucket,
                Key=key,
                Body=file_content
            )
            logger.info(f"Saved uploaded file: {key}")
            return True
        except Exception as e:
            logger.error(f"Error saving uploaded file: {e}")
            return False
    
    def load_uploaded_file(
        self,
        industry: str,
        usecase: str,
        filename: str
    ) -> Optional[bytes]:
        """Load uploaded file from S3 (key lowercased to match save)."""
        try:
            industry = (industry or "").strip().lower()
            usecase = (usecase or "").strip().lower()
            key = f"{self.industry_docs_prefix}{industry}/{usecase}/uploaded_data/{filename}"
            response = self.s3_client.get_object(Bucket=self.bucket, Key=key)
            data = response['Body'].read()
            logger.info(f"Loaded uploaded file: {key}")
            return data
        except self.s3_client.exceptions.NoSuchKey:
            logger.warning(f"Uploaded file not found: {key}")
            return None
        except Exception as e:
            logger.error(f"Error loading uploaded file: {e}")
            return None

    def list_uploaded_files(self, industry: str, usecase: str) -> List[str]:
        """List filenames stored under
        industry-docs/{industry}/{usecase}/uploaded_data/."""
        industry = (industry or "").strip().lower()
        usecase = (usecase or "").strip().lower()
        prefix = f"{self.industry_docs_prefix}{industry}/{usecase}/uploaded_data/"
        try:
            response = self.s3_client.list_objects_v2(Bucket=self.bucket, Prefix=prefix)
            names: List[str] = []
            for obj in response.get("Contents", []):
                tail = obj["Key"][len(prefix):]
                if tail and "/" not in tail:
                    names.append(tail)
            return names
        except Exception as e:
            logger.error(f"Error listing uploaded files: {e}")
            return []

    # ----- generic industry-docs helpers (arbitrary relative path) -----
    # These let callers store/read pieces whose key shape doesn't fit the
    # {industry}/{usecase}/{doc_type} pattern — notably the industry-common
    # ontology at {industry}/ontology/vN.json. The relative path is always
    # placed under the industry-docs/ prefix so the S3 layout mirrors local.

    def put_industry_json(self, rel_path: str, data: Dict[str, Any]) -> bool:
        """Write a JSON doc at industry-docs/<rel_path>. rel_path example:
        'healthcare/ontology/v2.json' or 'healthcare/assist/persona/v1.json'."""
        try:
            key = f"{self.industry_docs_prefix}{rel_path.lstrip('/')}"
            self.s3_client.put_object(
                Bucket=self.bucket,
                Key=key,
                Body=json.dumps(data, indent=2),
                ContentType="application/json",
            )
            logger.info(f"Saved industry json: {key}")
            return True
        except Exception as e:
            logger.error(f"Error saving industry json {rel_path}: {e}")
            return False

    def get_industry_json(self, rel_path: str) -> Optional[Dict[str, Any]]:
        """Read a JSON doc at industry-docs/<rel_path>. Returns None if absent."""
        key = f"{self.industry_docs_prefix}{rel_path.lstrip('/')}"
        try:
            response = self.s3_client.get_object(Bucket=self.bucket, Key=key)
            return json.loads(response['Body'].read())
        except self.s3_client.exceptions.NoSuchKey:
            return None
        except Exception as e:
            logger.error(f"Error loading industry json {rel_path}: {e}")
            return None

    def list_industry_dir(self, rel_dir: str) -> List[str]:
        """List the filenames (last path segment) directly under
        industry-docs/<rel_dir>/. Used to find version files like v1.json."""
        prefix = f"{self.industry_docs_prefix}{rel_dir.strip('/')}/"
        try:
            response = self.s3_client.list_objects_v2(Bucket=self.bucket, Prefix=prefix)
            names: List[str] = []
            for obj in response.get('Contents', []):
                tail = obj['Key'][len(prefix):]
                # Only direct children (no deeper nesting).
                if tail and "/" not in tail:
                    names.append(tail)
            return names
        except Exception as e:
            logger.error(f"Error listing industry dir {rel_dir}: {e}")
            return []

    def list_industry_subdirs(self, rel_dir: str = "") -> List[str]:
        """List immediate subdirectory names under industry-docs/<rel_dir>/.
        Used to discover industries that have reusable pieces."""
        prefix = f"{self.industry_docs_prefix}{rel_dir.strip('/')}"
        if prefix and not prefix.endswith("/"):
            prefix += "/"
        try:
            response = self.s3_client.list_objects_v2(
                Bucket=self.bucket, Prefix=prefix, Delimiter="/"
            )
            subdirs = []
            for cp in response.get("CommonPrefixes", []):
                name = cp["Prefix"][len(prefix):].strip("/")
                if name:
                    subdirs.append(name)
            return subdirs
        except Exception as e:
            logger.error(f"Error listing industry subdirs {rel_dir}: {e}")
            return []
    
    # ========== CONTEXT PACKS (Final Approved) ==========
    
    def save_context_pack(
        self,
        industry: str,
        usecase: str,
        pack_data: Dict[str, Any],
        version: str = "v1"
    ) -> bool:
        """
        Save final approved context pack to S3
        Path: context-pack/{industry}/{usecase}/{version}.json
        (industry/usecase lowercased for the key to avoid duplicate prefixes).
        """
        try:
            industry = (industry or "").strip().lower()
            usecase = (usecase or "").strip().lower()
            key = f"{self.context_pack_prefix}{industry}/{usecase}/{version}.json"
            self.s3_client.put_object(
                Bucket=self.bucket,
                Key=key,
                Body=json.dumps(pack_data, indent=2),
                ContentType="application/json",
                Metadata={
                    "created_at": datetime.utcnow().isoformat(),
                    "industry": industry,
                    "usecase": usecase
                }
            )
            logger.info(f"Saved context pack: {key}")
            return True
        except Exception as e:
            logger.error(f"Error saving context pack: {e}")
            return False
    
    def load_context_pack(
        self,
        industry: str,
        usecase: str,
        version: str = "v1"
    ) -> Optional[Dict[str, Any]]:
        """Load context pack from S3 (key lowercased to match save)."""
        try:
            industry = (industry or "").strip().lower()
            usecase = (usecase or "").strip().lower()
            key = f"{self.context_pack_prefix}{industry}/{usecase}/{version}.json"
            response = self.s3_client.get_object(Bucket=self.bucket, Key=key)
            data = json.loads(response['Body'].read())
            logger.info(f"Loaded context pack: {key}")
            return data
        except self.s3_client.exceptions.NoSuchKey:
            logger.warning(f"Context pack not found: {key}")
            return None
        except Exception as e:
            logger.error(f"Error loading context pack: {e}")
            return None
    
    def list_context_packs(self, industry: str = None) -> List[Dict[str, str]]:
        """List all context packs, optionally filtered by industry"""
        try:
            prefix = self.context_pack_prefix
            if industry:
                prefix = f"{self.context_pack_prefix}{(industry or '').strip().lower()}/"
            
            response = self.s3_client.list_objects_v2(
                Bucket=self.bucket,
                Prefix=prefix
            )
            
            if 'Contents' not in response:
                return []
            
            packs = []
            for obj in response['Contents']:
                parts = obj['Key'].split('/')
                if len(parts) >= 4 and parts[-1].endswith('.json'):
                    packs.append({
                        "industry": parts[1],
                        "usecase": parts[2],
                        "version": parts[3].replace('.json', ''),
                        "key": obj['Key'],
                        "size": obj['Size'],
                        "last_modified": obj['LastModified'].isoformat()
                    })
            
            return packs
        except Exception as e:
            logger.error(f"Error listing context packs: {e}")
            return []
    
# Singleton instance
_s3_manager_instance = None

def get_s3_manager() -> S3Manager:
    """Get or create S3Manager singleton"""
    global _s3_manager_instance
    if _s3_manager_instance is None:
        _s3_manager_instance = S3Manager()
    return _s3_manager_instance
