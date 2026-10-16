"""
Session Manager - Handle session lifecycle with DynamoDB
Stores session metadata, conversation state, and timestamps
Not wired yet - activated via storage_router when MOCK_MODE=false
"""

import boto3
import json
import logging
import os
import uuid
from typing import Optional, Dict, Any, List
from datetime import datetime, timedelta
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

class SessionManager:
    """Manages user sessions with DynamoDB backend"""
    
    def __init__(self):
        self.region = os.getenv("AWS_REGION", "us-east-1")
        self.table_name = os.getenv("DYNAMODB_TABLE_NAME", "vertical-adaption-sessions")
        self.ttl_days = int(os.getenv("DYNAMODB_SESSION_TTL_DAYS", "7"))
        
        self.dynamodb = boto3.resource("dynamodb", region_name=self.region)
        self.table = self.dynamodb.Table(self.table_name)
        self.client = boto3.client("dynamodb", region_name=self.region)
    
    # ========== SESSION LIFECYCLE ==========
    
    def create_session(
        self,
        user_id: str,
        industry: str,
        usecase: str,
        agent_name: str = None
    ) -> str:
        """
        Create new session
        Returns: session_id
        """
        try:
            session_id = str(uuid.uuid4())
            now = datetime.utcnow()
            ttl = int((now + timedelta(days=self.ttl_days)).timestamp())
            
            item = {
                "session_id": session_id,
                "user_id": user_id,
                "domain": f"{industry}#{usecase}",  # Sort key
                "industry": industry,
                "usecase": usecase,
                "agent_name": agent_name or "default",
                "status": "active",  # active, completed, archived
                "created_at": now.isoformat(),
                "last_accessed": now.isoformat(),
                "ttl": ttl,  # DynamoDB TTL
                "message_count": 0,
                "bedrock_model_id": os.getenv("BEDROCK_MODEL_ID", ""),
                "bedrock_guardrail_arn": None,
                "context_pack_version": "v1",
                "metadata": {}
            }
            
            self.table.put_item(Item=item)
            logger.info(f"Created session: {session_id} for user: {user_id}")
            return session_id
        except Exception as e:
            logger.error(f"Error creating session: {e}")
            raise
    
    def get_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        """Get session metadata"""
        try:
            response = self.table.get_item(Key={"session_id": session_id})
            if "Item" in response:
                logger.info(f"Retrieved session: {session_id}")
                return response["Item"]
            logger.warning(f"Session not found: {session_id}")
            return None
        except Exception as e:
            logger.error(f"Error retrieving session: {e}")
            return None
    
    def update_session(
        self,
        session_id: str,
        updates: Dict[str, Any]
    ) -> bool:
        """Update session metadata"""
        try:
            # Build update expression
            update_parts = []
            expr_values = {}
            
            for key, value in updates.items():
                if key not in ["session_id"]:  # Don't update PK
                    update_parts.append(f"{key} = :{key}")
                    expr_values[f":{key}"] = value
            
            # Always update last_accessed
            update_parts.append("last_accessed = :now")
            expr_values[":now"] = datetime.utcnow().isoformat()
            
            self.table.update_item(
                Key={"session_id": session_id},
                UpdateExpression="SET " + ", ".join(update_parts),
                ExpressionAttributeValues=expr_values
            )
            logger.info(f"Updated session: {session_id}")
            return True
        except Exception as e:
            logger.error(f"Error updating session: {e}")
            return False
    
    def close_session(self, session_id: str) -> bool:
        """Mark session as completed"""
        try:
            self.update_session(session_id, {"status": "completed"})
            logger.info(f"Closed session: {session_id}")
            return True
        except Exception as e:
            logger.error(f"Error closing session: {e}")
            return False
    
    def delete_session(self, session_id: str) -> bool:
        """Delete session (admin only)"""
        try:
            self.table.delete_item(Key={"session_id": session_id})
            logger.info(f"Deleted session: {session_id}")
            return True
        except Exception as e:
            logger.error(f"Error deleting session: {e}")
            return False
    
    # ========== CONVERSATION STATE ==========
    
    def add_message(
        self,
        session_id: str,
        role: str,  # user, agent, system
        content: str,
        message_type: str = "text",  # text, json, error
        metadata: Dict[str, Any] = None
    ) -> bool:
        """Add message to session conversation"""
        try:
            session = self.get_session(session_id)
            if not session:
                logger.error(f"Session not found: {session_id}")
                return False
            
            # Update message count
            new_count = session.get("message_count", 0) + 1
            
            message_entry = {
                "timestamp": datetime.utcnow().isoformat(),
                "role": role,
                "content": content,
                "type": message_type,
                "metadata": metadata or {}
            }
            
            # Update session
            self.update_session(session_id, {
                "message_count": new_count,
                "last_message_at": datetime.utcnow().isoformat()
            })
            
            logger.info(f"Added message to session {session_id}: {role}")
            return True
        except Exception as e:
            logger.error(f"Error adding message: {e}")
            return False
    
    # ========== CONTEXT PACK STATE ==========
    
    def set_context_pack_version(
        self,
        session_id: str,
        industry: str,
        usecase: str,
        version: str = "v1"
    ) -> bool:
        """Set current context pack version for session"""
        try:
            self.update_session(session_id, {
                "current_context_pack": f"{industry}/{usecase}",
                "context_pack_version": version
            })
            logger.info(f"Set context pack for session {session_id}: {industry}/{usecase}/{version}")
            return True
        except Exception as e:
            logger.error(f"Error setting context pack: {e}")
            return False
    
    def set_bedrock_guardrail(
        self,
        session_id: str,
        guardrail_arn: str
    ) -> bool:
        """Set Bedrock guardrail ARN for session"""
        try:
            self.update_session(session_id, {
                "bedrock_guardrail_arn": guardrail_arn
            })
            logger.info(f"Set guardrail for session {session_id}: {guardrail_arn}")
            return True
        except Exception as e:
            logger.error(f"Error setting guardrail: {e}")
            return False
    
    def store_session_metadata(
        self,
        session_id: str,
        key: str,
        value: Any
    ) -> bool:
        """Store custom metadata in session"""
        try:
            session = self.get_session(session_id)
            if not session:
                return False
            
            metadata = session.get("metadata", {})
            metadata[key] = value
            
            self.update_session(session_id, {"metadata": metadata})
            logger.info(f"Stored metadata for session {session_id}: {key}")
            return True
        except Exception as e:
            logger.error(f"Error storing metadata: {e}")
            return False
    
    def get_session_metadata(self, session_id: str, key: str = None) -> Optional[Any]:
        """Get session metadata"""
        try:
            session = self.get_session(session_id)
            if not session:
                return None
            
            metadata = session.get("metadata", {})
            if key:
                return metadata.get(key)
            return metadata
        except Exception as e:
            logger.error(f"Error retrieving metadata: {e}")
            return None
    
    # ========== QUERIES ==========
    
    def get_user_sessions(
        self,
        user_id: str,
        status: str = None
    ) -> List[Dict[str, Any]]:
        """Get all sessions for a user"""
        try:
            expr_values = {":uid": user_id}
            filter_expr = "user_id = :uid"
            
            if status:
                expr_values[":status"] = status
                filter_expr += " AND #s = :status"
            
            response = self.table.scan(
                FilterExpression=filter_expr,
                ExpressionAttributeNames={"#s": "status"} if status else {},
                ExpressionAttributeValues=expr_values
            )
            
            items = response.get("Items", [])
            logger.info(f"Retrieved {len(items)} sessions for user: {user_id}")
            return items
        except Exception as e:
            logger.error(f"Error retrieving user sessions: {e}")
            return []
    
    def get_sessions_by_industry(
        self,
        industry: str,
        usecase: str = None
    ) -> List[Dict[str, Any]]:
        """Get all sessions for an industry/usecase"""
        try:
            filter_expr = "industry = :ind"
            expr_values = {":ind": industry}
            
            if usecase:
                filter_expr += " AND usecase = :uc"
                expr_values[":uc"] = usecase
            
            response = self.table.scan(
                FilterExpression=filter_expr,
                ExpressionAttributeValues=expr_values
            )
            
            items = response.get("Items", [])
            logger.info(f"Retrieved {len(items)} sessions for {industry}/{usecase or 'all'}")
            return items
        except Exception as e:
            logger.error(f"Error retrieving industry sessions: {e}")
            return []
    
    def get_active_sessions(self) -> List[Dict[str, Any]]:
        """Get all active sessions"""
        try:
            response = self.table.scan(
                FilterExpression="attribute_exists(session_id) AND #s = :active",
                ExpressionAttributeNames={"#s": "status"},
                ExpressionAttributeValues={":active": "active"}
            )
            
            items = response.get("Items", [])
            logger.info(f"Retrieved {len(items)} active sessions")
            return items
        except Exception as e:
            logger.error(f"Error retrieving active sessions: {e}")
            return []
    
    # ========== TABLE MANAGEMENT ==========
    
    def create_table(self) -> bool:
        """Create DynamoDB table if it doesn't exist"""
        try:
            self.client.create_table(
                TableName=self.table_name,
                KeySchema=[
                    {"AttributeName": "session_id", "KeyType": "HASH"},
                ],
                AttributeDefinitions=[
                    {"AttributeName": "session_id", "AttributeType": "S"},
                    {"AttributeName": "user_id", "AttributeType": "S"},
                    {"AttributeName": "industry", "AttributeType": "S"},
                ],
                GlobalSecondaryIndexes=[
                    {
                        "IndexName": "user_id-index",
                        "KeySchema": [
                            {"AttributeName": "user_id", "KeyType": "HASH"},
                        ],
                        "Projection": {"ProjectionType": "ALL"},
                        "ProvisionedThroughput": {
                            "ReadCapacityUnits": 5,
                            "WriteCapacityUnits": 5
                        }
                    },
                    {
                        "IndexName": "industry-index",
                        "KeySchema": [
                            {"AttributeName": "industry", "KeyType": "HASH"},
                        ],
                        "Projection": {"ProjectionType": "ALL"},
                        "ProvisionedThroughput": {
                            "ReadCapacityUnits": 5,
                            "WriteCapacityUnits": 5
                        }
                    }
                ],
                BillingMode="PAY_PER_REQUEST",  # Auto-scaling
                TimeToLiveSpecification={
                    "AttributeName": "ttl",
                    "Enabled": True
                }
            )
            logger.info(f"Created table: {self.table_name}")
            return True
        except self.client.exceptions.ResourceInUseException:
            logger.info(f"Table already exists: {self.table_name}")
            return True
        except Exception as e:
            logger.error(f"Error creating table: {e}")
            return False
    
    def table_exists(self) -> bool:
        """Check if table exists"""
        try:
            self.client.describe_table(TableName=self.table_name)
            return True
        except self.client.exceptions.ResourceNotFoundException:
            return False
        except Exception as e:
            logger.error(f"Error checking table: {e}")
            return False


# Singleton instance
_session_manager_instance = None

def get_session_manager() -> SessionManager:
    """Get or create SessionManager singleton"""
    global _session_manager_instance
    if _session_manager_instance is None:
        _session_manager_instance = SessionManager()
    return _session_manager_instance
