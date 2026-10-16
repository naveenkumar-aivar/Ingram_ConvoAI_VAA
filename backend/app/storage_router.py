"""
Storage Router - Route operations between local and S3 based on MOCK_MODE
Provides unified interface for storage operations across the application
Not wired yet - will be activated when switching from MOCK_MODE=true to false
"""

import os
import json
import logging
from typing import Optional, Dict, Any, List
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

# Import managers (will be None if not available)
try:
    from app.aws.s3_manager import get_s3_manager
    S3_AVAILABLE = True
except ImportError:
    S3_AVAILABLE = False
    logger.warning("S3Manager not available")

try:
    from app.session_manager import get_session_manager
    DYNAMODB_AVAILABLE = True
except ImportError:
    DYNAMODB_AVAILABLE = False
    logger.warning("SessionManager not available")


class StorageRouter:
    """
    Unified storage interface that routes to local or AWS based on MOCK_MODE
    
    MOCK_MODE=true  → local filesystem (s3-data/)
    MOCK_MODE=false → S3 + DynamoDB
    """
    
    def __init__(self):
        self.mock_mode = os.getenv("MOCK_MODE", "true").lower() == "true"
        self.local_s3_dir = os.getenv("LOCAL_S3_DIR", "../s3-data")
        
        if not self.mock_mode:
            if not S3_AVAILABLE or not DYNAMODB_AVAILABLE:
                logger.error("AWS managers not available but MOCK_MODE=false")
                raise RuntimeError("AWS dependencies not available")
            
            self.s3_manager = get_s3_manager()
            self.session_manager = get_session_manager()
        else:
            self.s3_manager = None
            self.session_manager = None
        
        logger.info(f"StorageRouter initialized - MOCK_MODE={self.mock_mode}")
    
    # ========== MODE INFO ==========
    
    def is_mock_mode(self) -> bool:
        """Check if running in mock mode"""
        return self.mock_mode
    
    def get_mode(self) -> str:
        """Get current storage mode"""
        return "mock" if self.mock_mode else "aws"
    
    # ========== INDUSTRY DOCS (Approved Details) ==========
    
    def save_industry_doc(
        self,
        industry: str,
        usecase: str,
        doc_type: str,
        data: Dict[str, Any],
        version: str = "v1"
    ) -> bool:
        """
        Save approved industry document
        Routes to: local or S3
        """
        if self.mock_mode:
            return self._local_save_industry_doc(industry, usecase, doc_type, data, version)
        else:
            return self.s3_manager.save_industry_doc(industry, usecase, doc_type, data, version)
    
    def load_industry_doc(
        self,
        industry: str,
        usecase: str,
        doc_type: str,
        version: str = "v1"
    ) -> Optional[Dict[str, Any]]:
        """
        Load approved industry document
        Routes to: local or S3
        """
        if self.mock_mode:
            return self._local_load_industry_doc(industry, usecase, doc_type, version)
        else:
            return self.s3_manager.load_industry_doc(industry, usecase, doc_type, version)
    
    def save_uploaded_file(
        self,
        industry: str,
        usecase: str,
        file_path: str,
        file_content: bytes
    ) -> bool:
        """Save uploaded data file"""
        if self.mock_mode:
            return self._local_save_uploaded_file(industry, usecase, file_path, file_content)
        else:
            return self.s3_manager.save_uploaded_file(industry, usecase, file_path, file_content)
    
    def load_uploaded_file(
        self,
        industry: str,
        usecase: str,
        filename: str
    ) -> Optional[bytes]:
        """Load uploaded data file"""
        if self.mock_mode:
            return self._local_load_uploaded_file(industry, usecase, filename)
        else:
            return self.s3_manager.load_uploaded_file(industry, usecase, filename)
    
    def list_industry_docs(
        self,
        industry: str,
        usecase: str,
        doc_type: str
    ) -> List[str]:
        """List versions of a document type"""
        if self.mock_mode:
            return self._local_list_industry_docs(industry, usecase, doc_type)
        else:
            return self.s3_manager.list_industry_docs(industry, usecase, doc_type)
    
    # ========== CONTEXT PACKS (Final Approved) ==========
    
    def save_context_pack(
        self,
        industry: str,
        usecase: str,
        pack_data: Dict[str, Any],
        version: str = "v1"
    ) -> bool:
        """Save final approved context pack.

        The industry + usecase are normalised to lowercase for the storage
        PATH/KEY only (folders in local mode, keys in S3), so casing stays
        consistent across all packs. The human-readable label is preserved
        inside pack_data['meta']['industry'], not in the path."""
        industry = (industry or "").strip().lower()
        usecase = (usecase or "").strip().lower()
        if self.mock_mode:
            return self._local_save_context_pack(industry, usecase, pack_data, version)
        else:
            return self.s3_manager.save_context_pack(industry, usecase, pack_data, version)
    
    def load_context_pack(
        self,
        industry: str,
        usecase: str,
        version: str = "v1"
    ) -> Optional[Dict[str, Any]]:
        """Load final approved context pack (path/key lowercased to match save)."""
        industry = (industry or "").strip().lower()
        usecase = (usecase or "").strip().lower()
        if self.mock_mode:
            return self._local_load_context_pack(industry, usecase, version)
        else:
            return self.s3_manager.load_context_pack(industry, usecase, version)
    
    def list_context_packs(self, industry: str = None) -> List[Dict[str, str]]:
        """List all context packs"""
        if self.mock_mode:
            return self._local_list_context_packs(industry)
        else:
            return self.s3_manager.list_context_packs(industry)
    
    # ========== SESSIONS ==========
    
    def create_session(
        self,
        user_id: str,
        industry: str,
        usecase: str,
        agent_name: str = None
    ) -> str:
        """Create new session"""
        if self.mock_mode:
            return self._local_create_session(user_id, industry, usecase, agent_name)
        else:
            return self.session_manager.create_session(user_id, industry, usecase, agent_name)
    
    def get_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        """Get session metadata"""
        if self.mock_mode:
            return self._local_get_session(session_id)
        else:
            return self.session_manager.get_session(session_id)
    
    def update_session(
        self,
        session_id: str,
        updates: Dict[str, Any]
    ) -> bool:
        """Update session"""
        if self.mock_mode:
            return self._local_update_session(session_id, updates)
        else:
            return self.session_manager.update_session(session_id, updates)
    
    def close_session(self, session_id: str) -> bool:
        """Close session"""
        if self.mock_mode:
            return self._local_close_session(session_id)
        else:
            return self.session_manager.close_session(session_id)
    
    def add_message(
        self,
        session_id: str,
        role: str,
        content: str,
        message_type: str = "text",
        metadata: Dict[str, Any] = None
    ) -> bool:
        """Add message to session"""
        if self.mock_mode:
            return self._local_add_message(session_id, role, content, message_type, metadata)
        else:
            return self.session_manager.add_message(session_id, role, content, message_type, metadata)
    
    def get_user_sessions(
        self,
        user_id: str,
        status: str = None
    ) -> List[Dict[str, Any]]:
        """Get user sessions"""
        if self.mock_mode:
            return self._local_get_user_sessions(user_id, status)
        else:
            return self.session_manager.get_user_sessions(user_id, status)
    
    # ========== LOGS ==========
    
    def save_log(self, log_type: str, log_data: Dict[str, Any]) -> bool:
        """Save log entry"""
        if self.mock_mode:
            return self._local_save_log(log_type, log_data)
        else:
            return self.s3_manager.save_log(log_type, log_data)
    
    # ========== LOCAL IMPLEMENTATIONS ==========
    
    def _local_save_industry_doc(
        self,
        industry: str,
        usecase: str,
        doc_type: str,
        data: Dict[str, Any],
        version: str = "v1"
    ) -> bool:
        """Local: Save industry doc"""
        try:
            industry = (industry or "").strip().lower()
            usecase = (usecase or "").strip().lower()
            path = f"{self.local_s3_dir}/industry-docs/{industry}/{usecase}/{doc_type}/{version}.json"
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, 'w') as f:
                json.dump(data, f, indent=2)
            logger.info(f"Local saved industry doc: {path}")
            return True
        except Exception as e:
            logger.error(f"Local error saving industry doc: {e}")
            return False
    
    def _local_load_industry_doc(
        self,
        industry: str,
        usecase: str,
        doc_type: str,
        version: str = "v1"
    ) -> Optional[Dict[str, Any]]:
        """Local: Load industry doc"""
        try:
            industry = (industry or "").strip().lower()
            usecase = (usecase or "").strip().lower()
            path = f"{self.local_s3_dir}/industry-docs/{industry}/{usecase}/{doc_type}/{version}.json"
            with open(path, 'r') as f:
                data = json.load(f)
            logger.info(f"Local loaded industry doc: {path}")
            return data
        except FileNotFoundError:
            logger.warning(f"Local industry doc not found: {path}")
            return None
        except Exception as e:
            logger.error(f"Local error loading industry doc: {e}")
            return None
    
    def _local_save_uploaded_file(
        self,
        industry: str,
        usecase: str,
        file_path: str,
        file_content: bytes
    ) -> bool:
        """Local: Save uploaded file"""
        try:
            industry = (industry or "").strip().lower()
            usecase = (usecase or "").strip().lower()
            filename = os.path.basename(file_path)
            path = f"{self.local_s3_dir}/industry-docs/{industry}/{usecase}/uploaded-data/{filename}"
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, 'wb') as f:
                f.write(file_content)
            logger.info(f"Local saved uploaded file: {path}")
            return True
        except Exception as e:
            logger.error(f"Local error saving uploaded file: {e}")
            return False
    
    def _local_load_uploaded_file(
        self,
        industry: str,
        usecase: str,
        filename: str
    ) -> Optional[bytes]:
        """Local: Load uploaded file"""
        try:
            industry = (industry or "").strip().lower()
            usecase = (usecase or "").strip().lower()
            path = f"{self.local_s3_dir}/industry-docs/{industry}/{usecase}/uploaded-data/{filename}"
            with open(path, 'rb') as f:
                content = f.read()
            logger.info(f"Local loaded uploaded file: {path}")
            return content
        except FileNotFoundError:
            logger.warning(f"Local uploaded file not found: {path}")
            return None
        except Exception as e:
            logger.error(f"Local error loading uploaded file: {e}")
            return None
    
    def _local_list_industry_docs(
        self,
        industry: str,
        usecase: str,
        doc_type: str
    ) -> List[str]:
        """Local: List industry docs"""
        try:
            industry = (industry or "").strip().lower()
            usecase = (usecase or "").strip().lower()
            path = f"{self.local_s3_dir}/industry-docs/{industry}/{usecase}/{doc_type}/"
            if os.path.exists(path):
                return [f for f in os.listdir(path) if f.endswith('.json')]
            return []
        except Exception as e:
            logger.error(f"Local error listing industry docs: {e}")
            return []
    
    def _local_save_context_pack(
        self,
        industry: str,
        usecase: str,
        pack_data: Dict[str, Any],
        version: str = "v1"
    ) -> bool:
        """Local: Save context pack"""
        try:
            path = f"{self.local_s3_dir}/context-pack/{industry}/{usecase}/{version}.json"
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, 'w') as f:
                json.dump(pack_data, f, indent=2)
            logger.info(f"Local saved context pack: {path}")
            return True
        except Exception as e:
            logger.error(f"Local error saving context pack: {e}")
            return False
    
    def _local_load_context_pack(
        self,
        industry: str,
        usecase: str,
        version: str = "v1"
    ) -> Optional[Dict[str, Any]]:
        """Local: Load context pack"""
        try:
            path = f"{self.local_s3_dir}/context-pack/{industry}/{usecase}/{version}.json"
            with open(path, 'r') as f:
                data = json.load(f)
            logger.info(f"Local loaded context pack: {path}")
            return data
        except FileNotFoundError:
            logger.warning(f"Local context pack not found: {path}")
            return None
        except Exception as e:
            logger.error(f"Local error loading context pack: {e}")
            return None
    
    def _local_list_context_packs(self, industry: str = None) -> List[Dict[str, str]]:
        """Local: List context packs"""
        try:
            base_path = f"{self.local_s3_dir}/context-pack/"
            packs = []
            industry = (industry or "").strip().lower() or None
            
            if not os.path.exists(base_path):
                return []
            
            for ind in os.listdir(base_path):
                if industry and ind.lower() != industry:
                    continue
                
                ind_path = os.path.join(base_path, ind)
                if os.path.isdir(ind_path):
                    for usecase in os.listdir(ind_path):
                        usecase_path = os.path.join(ind_path, usecase)
                        if os.path.isdir(usecase_path):
                            for version_file in os.listdir(usecase_path):
                                if version_file.endswith('.json'):
                                    packs.append({
                                        "industry": ind,
                                        "usecase": usecase,
                                        "version": version_file.replace('.json', '')
                                    })
            return packs
        except Exception as e:
            logger.error(f"Local error listing context packs: {e}")
            return []
    
    def _local_create_session(
        self,
        user_id: str,
        industry: str,
        usecase: str,
        agent_name: str = None
    ) -> str:
        """Local: Create session (memory only)"""
        import uuid
        session_id = str(uuid.uuid4())
        logger.info(f"Local created session: {session_id}")
        return session_id
    
    def _local_get_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        """Local: Get session (not persisted)"""
        logger.warning("Local mode: sessions not persisted")
        return None
    
    def _local_update_session(
        self,
        session_id: str,
        updates: Dict[str, Any]
    ) -> bool:
        """Local: Update session (memory only)"""
        logger.warning("Local mode: session updates not persisted")
        return True
    
    def _local_close_session(self, session_id: str) -> bool:
        """Local: Close session"""
        logger.info(f"Local closed session: {session_id}")
        return True
    
    def _local_add_message(
        self,
        session_id: str,
        role: str,
        content: str,
        message_type: str = "text",
        metadata: Dict[str, Any] = None
    ) -> bool:
        """Local: Add message (memory only)"""
        logger.warning("Local mode: messages not persisted")
        return True
    
    def _local_get_user_sessions(
        self,
        user_id: str,
        status: str = None
    ) -> List[Dict[str, Any]]:
        """Local: Get user sessions (none persisted)"""
        logger.warning("Local mode: sessions not persisted")
        return []
    
    def _local_save_log(self, log_type: str, log_data: Dict[str, Any]) -> bool:
        """Local: Save log"""
        try:
            from datetime import datetime
            date = datetime.utcnow().strftime("%Y-%m-%d")
            path = f"{self.local_s3_dir}/logs/{log_type}/{date}/"
            os.makedirs(path, exist_ok=True)
            
            timestamp = datetime.utcnow().isoformat().replace(':', '-')
            log_file = f"{path}{log_type}-{timestamp}.json"
            
            with open(log_file, 'w') as f:
                json.dump(log_data, f, indent=2, default=str)
            
            logger.info(f"Local saved log: {log_file}")
            return True
        except Exception as e:
            logger.error(f"Local error saving log: {e}")
            return False


# Singleton instance
_storage_router_instance = None

def get_storage_router() -> StorageRouter:
    """Get or create StorageRouter singleton"""
    global _storage_router_instance
    if _storage_router_instance is None:
        _storage_router_instance = StorageRouter()
    return _storage_router_instance
