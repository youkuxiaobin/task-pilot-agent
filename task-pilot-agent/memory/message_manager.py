from typing import List, Dict, Optional, Any
from sqlalchemy import create_engine, Column, String, Text, BigInteger, DateTime, func
from sqlalchemy.sql import text as sql_text
from sqlalchemy.dialects.mysql import LONGTEXT
from sqlalchemy.orm import declarative_base
from sqlalchemy.orm import sessionmaker
import uuid
import time
from datetime import datetime

# Import config
import sys
import os
sys.path.append(os.path.join(os.path.dirname(__file__), '..'))
from config.config import AgentSettings, agentSettings

Base = declarative_base()

class Message(Base):
    """Database model for messages"""
    __tablename__ = 'meta_agent_message'
    
    id = Column(BigInteger, primary_key=True)
    trace_id = Column(String(64), nullable=False, index=True)
    user_id = Column(String(64), nullable=True, index=True)
    conversation_id = Column(String(64), nullable=True, index=True)
    agent_id = Column(String(64), nullable=True, index=True)
    type_name = Column(String(64), nullable=True, index=True)
    role = Column(String(32), nullable=False)
    content = Column(LONGTEXT, nullable=False)
    tool_name = Column(String(64), nullable=True, index=True)
    create_time = Column(BigInteger, nullable=False, default=lambda: int(time.time()))
    update_time = Column(BigInteger, nullable=False, default=lambda: int(time.time()))

class MessageManager:
    """Message management with database storage"""
    
    def __init__(self, settings: Optional[AgentSettings] = None):
        self.settings = settings or agentSettings
        self.engine = self._create_engine()
        self.Session = sessionmaker(bind=self.engine)
        self._create_tables()
    
    def _create_engine(self):
        """Create database engine from settings"""
        db_cfg = getattr(self.settings, 'db', agentSettings.db)
        pool_pre_ping = getattr(db_cfg, 'pool_pre_ping', True)
        pool_recycle = getattr(db_cfg, 'pool_recycle', 1800)
        return create_engine(
            db_cfg.dsn,
            pool_pre_ping=pool_pre_ping,
            pool_recycle=pool_recycle,
        )
    
    def _create_tables(self):
        """Create database tables if they don't exist"""
        Base.metadata.create_all(self.engine)
        # Try to ensure content column can store very large payloads
        try:
            with self.engine.connect() as conn:
                conn.execute(sql_text("""
                    ALTER TABLE meta_agent_message
                    MODIFY COLUMN content LONGTEXT NOT NULL
                """))
                conn.commit()
        except Exception:
            # Ignore if not MySQL or already correct type
            pass
    
    def _generate_trace_id(self) -> str:
        """Generate unique trace ID"""
        return str(uuid.uuid4())
    
    def add_message(self, 
                    user_id: Optional[str] = None,
                    conversation_id: Optional[str] = None,
                    agent_id: Optional[str] = None,
                    role: str = "user",
                    content: str = "",
                    type_name: str = "",
                    tool_name: Optional[str] = None,
                    trace_id: Optional[str] = None) -> str:
        """
        Add a new message to database
        
        Args:
            user_id: User identifier
            conversation_id: Conversation identifier
            agent_id: Agent identifier
            role: Message role (user, assistant, system, etc.)
            tool_name: Tool name used (optional)
            content: Message content
            trace_id: Trace identifier, auto-generated if not provided
            
        Returns:
            Trace ID
        """
        if trace_id is None:
            trace_id = self._generate_trace_id()
        
        current_time = int(time.time())
        message = Message(
            trace_id=trace_id,
            user_id=user_id,
            conversation_id=conversation_id,
            agent_id=agent_id,
            role=role,
            content=content,
            type_name = type_name,
            tool_name=tool_name,
            create_time=current_time,
            update_time=current_time
        )
        
        session = self.Session()
        
        try:
            session.add(message)
            session.commit()
            return trace_id
            
        except Exception as e:
            session.rollback()
            raise e
        finally:
            session.close()
    
    def get_messages(self, 
                    trace_id: Optional[str] = None,
                    user_id: Optional[str] = None,
                    conversation_id: Optional[str] = None,
                    agent_id: Optional[str] = None,
                    type_name: Optional[str] = None,
                    limit: int = 100) -> List[Message]:
        """
        Retrieve messages with optional filters
        
        Args:
            trace_id: Filter by trace ID
            user_id: Filter by user ID
            conversation_id: Filter by conversation ID
            agent_id: Filter by agent ID
            limit: Maximum number of results
            
        Returns:
            List of Message objects
        """
        session = self.Session()
        
        try:
            query = session.query(Message)
            
            if trace_id:
                query = query.filter(Message.trace_id == trace_id)
            if user_id:
                query = query.filter(Message.user_id == user_id)
            if conversation_id:
                query = query.filter(Message.conversation_id == conversation_id)
            if agent_id:
                query = query.filter(Message.agent_id == agent_id)
            if type_name:
                query = query.filter(Message.type_name == type_name)
            
            
            results = query.order_by(Message.id.asc()).limit(limit).all()
            
            return results
            
        finally:
            session.close()
    
    def update_message(self, 
                      trace_id: str,
                      content: Optional[str] = None,
                      role: Optional[str] = None) -> bool:
        """
        Update a message by trace_id
        
        Args:
            trace_id: Trace ID of the message to update
            content: New content (optional)
            role: New role (optional)
            
        Returns:
            True if successful
        """
        session = self.Session()
        
        try:
            message = session.query(Message).filter(Message.trace_id == trace_id).first()
            
            if not message:
                return False
            
            if content is not None:
                message.content = content
            if role is not None:
                message.role = role
            
            message.update_time = int(time.time())
            session.commit()
            return True
            
        except Exception as e:
            session.rollback()
            raise e
        finally:
            session.close()
    
    def delete_message(self, trace_id: str) -> bool:
        """
        Delete a message by trace_id
        
        Args:
            trace_id: Trace ID of the message to delete
            
        Returns:
            True if successful
        """
        session = self.Session()
        
        try:
            deleted_count = session.query(Message).filter(Message.trace_id == trace_id).delete()
            session.commit()
            return deleted_count > 0
            
        except Exception as e:
            session.rollback()
            raise e
        finally:
            session.close()

# Singleton instance
#message_manager = MessageManager()
