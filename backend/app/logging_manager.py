"""
Logging Manager - Handle CloudWatch and local logging
Routes logs to CloudWatch (AWS) or local files (MOCK_MODE)
Not wired yet - activated via integrations when needed
"""

import os
import json
import logging
import sys
from typing import Dict, Any, Optional
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()


class LoggingManager:
    """Unified logging interface for CloudWatch and local logs"""
    
    def __init__(self):
        self.mock_mode = os.getenv("MOCK_MODE", "true").lower() == "true"
        self.local_logs_dir = os.getenv("LOCAL_S3_DIR", "../s3-data") + "/logs"
        self.log_level = os.getenv("LOG_LEVEL", "INFO").upper()
        self.region = os.getenv("AWS_REGION", "us-east-1")
        
        # CloudWatch setup (only if not in mock mode)
        self.cloudwatch_client = None
        self.log_group = os.getenv("CLOUDWATCH_LOG_GROUP", "/aws/lambda/vertical-agent")
        self.log_stream = None
        
        if not self.mock_mode:
            try:
                import boto3
                self.cloudwatch_client = boto3.client("logs", region_name=self.region)
                self._setup_cloudwatch()
            except Exception as e:
                logging.warning(f"CloudWatch setup failed: {e}")
        
        # Local logger setup
        self._setup_local_logging()
        self.logger = logging.getLogger(__name__)
    
    # ========== CLOUDWATCH SETUP ==========
    
    def _setup_cloudwatch(self) -> None:
        """Initialize CloudWatch log group and stream"""
        try:
            # Create log group if it doesn't exist
            try:
                self.cloudwatch_client.create_log_group(logGroupName=self.log_group)
                logging.info(f"Created CloudWatch log group: {self.log_group}")
            except self.cloudwatch_client.exceptions.ResourceAlreadyExistsException:
                pass  # Group already exists
            
            # Set retention policy (7 days)
            self.cloudwatch_client.put_retention_policy(
                logGroupName=self.log_group,
                retentionInDays=7
            )
            
            # Create log stream
            self.log_stream = f"vertical-agent-{datetime.utcnow().strftime('%Y%m%d-%H%M%S')}"
            try:
                self.cloudwatch_client.create_log_stream(
                    logGroupName=self.log_group,
                    logStreamName=self.log_stream
                )
                logging.info(f"Created CloudWatch log stream: {self.log_stream}")
            except self.cloudwatch_client.exceptions.ResourceAlreadyExistsException:
                pass
        except Exception as e:
            logging.error(f"CloudWatch setup error: {e}")
    
    # ========== LOCAL LOGGING SETUP ==========
    
    def _setup_local_logging(self) -> None:
        """Setup local file logging"""
        try:
            # Create logs directory
            os.makedirs(self.local_logs_dir, exist_ok=True)
            
            # Create logger
            logger = logging.getLogger("vertical_agent")
            logger.setLevel(getattr(logging, self.log_level))
            
            # Console handler
            console_handler = logging.StreamHandler(sys.stdout)
            console_handler.setLevel(getattr(logging, self.log_level))
            
            # File handler
            log_file = os.path.join(
                self.local_logs_dir,
                f"agent-{datetime.utcnow().strftime('%Y%m%d')}.log"
            )
            file_handler = logging.FileHandler(log_file)
            file_handler.setLevel(getattr(logging, self.log_level))
            
            # Formatter
            formatter = logging.Formatter(
                '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
            )
            console_handler.setFormatter(formatter)
            file_handler.setFormatter(formatter)
            
            # Add handlers
            if not logger.handlers:
                logger.addHandler(console_handler)
                logger.addHandler(file_handler)
        except Exception as e:
            logging.warning(f"Local logging setup failed: {e}")
    
    # ========== LOG OPERATIONS ==========
    
    def log_bedrock_call(
        self,
        model_id: str,
        input_tokens: int,
        output_tokens: int,
        latency_ms: float,
        status: str = "success",
        error: Optional[str] = None
    ) -> bool:
        """Log Bedrock API call"""
        try:
            log_data = {
                "timestamp": datetime.utcnow().isoformat(),
                "type": "bedrock_call",
                "model_id": model_id,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "latency_ms": latency_ms,
                "status": status,
                "error": error
            }
            
            if self.mock_mode:
                return self._log_local("bedrock-calls", log_data)
            else:
                return self._log_cloudwatch(log_data)
        except Exception as e:
            logging.error(f"Error logging Bedrock call: {e}")
            return False
    
    def log_agent_execution(
        self,
        session_id: str,
        agent_name: str,
        action: str,
        status: str = "success",
        details: Optional[Dict[str, Any]] = None
    ) -> bool:
        """Log agent execution event"""
        try:
            log_data = {
                "timestamp": datetime.utcnow().isoformat(),
                "type": "agent_execution",
                "session_id": session_id,
                "agent_name": agent_name,
                "action": action,
                "status": status,
                "details": details or {}
            }
            
            if self.mock_mode:
                return self._log_local("agent-execution", log_data)
            else:
                return self._log_cloudwatch(log_data)
        except Exception as e:
            logging.error(f"Error logging agent execution: {e}")
            return False
    
    def log_error(
        self,
        error_type: str,
        error_message: str,
        stack_trace: Optional[str] = None,
        context: Optional[Dict[str, Any]] = None
    ) -> bool:
        """Log error event"""
        try:
            log_data = {
                "timestamp": datetime.utcnow().isoformat(),
                "type": "error",
                "error_type": error_type,
                "error_message": error_message,
                "stack_trace": stack_trace,
                "context": context or {}
            }
            
            if self.mock_mode:
                return self._log_local("errors", log_data)
            else:
                return self._log_cloudwatch(log_data)
        except Exception as e:
            logging.error(f"Error logging error event: {e}")
            return False
    
    def log_guardrail_block(
        self,
        guardrail_arn: str,
        input_text: str,
        block_reason: str,
        session_id: Optional[str] = None
    ) -> bool:
        """Log Bedrock guardrail block"""
        try:
            log_data = {
                "timestamp": datetime.utcnow().isoformat(),
                "type": "guardrail_block",
                "guardrail_arn": guardrail_arn,
                "input_preview": input_text[:200],  # First 200 chars
                "block_reason": block_reason,
                "session_id": session_id
            }
            
            if self.mock_mode:
                return self._log_local("guardrails", log_data)
            else:
                return self._log_cloudwatch(log_data)
        except Exception as e:
            logging.error(f"Error logging guardrail block: {e}")
            return False
    
    def log_context_pack_loaded(
        self,
        industry: str,
        usecase: str,
        version: str,
        session_id: Optional[str] = None
    ) -> bool:
        """Log context pack loaded"""
        try:
            log_data = {
                "timestamp": datetime.utcnow().isoformat(),
                "type": "context_pack_loaded",
                "industry": industry,
                "usecase": usecase,
                "version": version,
                "session_id": session_id
            }
            
            if self.mock_mode:
                return self._log_local("agent-execution", log_data)
            else:
                return self._log_cloudwatch(log_data)
        except Exception as e:
            logging.error(f"Error logging context pack load: {e}")
            return False
    
    def log_session_created(
        self,
        session_id: str,
        user_id: str,
        industry: str,
        usecase: str
    ) -> bool:
        """Log session creation"""
        try:
            log_data = {
                "timestamp": datetime.utcnow().isoformat(),
                "type": "session_created",
                "session_id": session_id,
                "user_id": user_id,
                "industry": industry,
                "usecase": usecase
            }
            
            if self.mock_mode:
                return self._log_local("agent-execution", log_data)
            else:
                return self._log_cloudwatch(log_data)
        except Exception as e:
            logging.error(f"Error logging session creation: {e}")
            return False
    
    # ========== INTERNAL LOGGING ==========
    
    def _log_local(self, log_type: str, log_data: Dict[str, Any]) -> bool:
        """Write log to local file"""
        try:
            date = datetime.utcnow().strftime("%Y-%m-%d")
            log_dir = os.path.join(self.local_logs_dir, log_type, date)
            os.makedirs(log_dir, exist_ok=True)
            
            timestamp = datetime.utcnow().isoformat().replace(':', '-')
            log_file = os.path.join(log_dir, f"{log_type}-{timestamp}.json")
            
            with open(log_file, 'w') as f:
                json.dump(log_data, f, indent=2, default=str)
            
            logging.debug(f"Logged to: {log_file}")
            return True
        except Exception as e:
            logging.error(f"Error writing local log: {e}")
            return False
    
    def _log_cloudwatch(self, log_data: Dict[str, Any]) -> bool:
        """Send log to CloudWatch"""
        try:
            if not self.cloudwatch_client or not self.log_stream:
                logging.warning("CloudWatch not available")
                # Fallback to local
                return self._log_local("cloudwatch-fallback", log_data)
            
            log_message = json.dumps(log_data, default=str)
            
            self.cloudwatch_client.put_log_events(
                logGroupName=self.log_group,
                logStreamName=self.log_stream,
                logEvents=[
                    {
                        "timestamp": int(datetime.utcnow().timestamp() * 1000),
                        "message": log_message
                    }
                ]
            )
            
            logging.debug(f"Sent to CloudWatch: {self.log_group}/{self.log_stream}")
            return True
        except Exception as e:
            logging.error(f"Error sending to CloudWatch: {e}")
            # Fallback to local
            return self._log_local("cloudwatch-fallback", log_data)
    
    # ========== UTILITY ==========
    
    def get_mode(self) -> str:
        """Get current logging mode"""
        return "mock" if self.mock_mode else "cloudwatch"
    
    def get_logger(self, name: str) -> logging.Logger:
        """Get a named logger"""
        return logging.getLogger(name)


# Singleton instance
_logging_manager_instance = None

def get_logging_manager() -> LoggingManager:
    """Get or create LoggingManager singleton"""
    global _logging_manager_instance
    if _logging_manager_instance is None:
        _logging_manager_instance = LoggingManager()
    return _logging_manager_instance
