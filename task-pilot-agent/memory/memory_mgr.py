from typing import List, Dict, Optional, Any
from mem0 import Memory
from mem0.configs.base import MemoryConfig
from mem0.vector_stores.configs import VectorStoreConfig
import uuid
import json
from dataclasses import dataclass, field
from enum import Enum
from llm.types import LLMMessage
# Import config with relative path


import sys
import os
sys.path.append(os.path.join(os.path.dirname(__file__), '..'))
from config.config import agentSettings, AgentSettings

# Import plan and RAG components
# Import message manager
from .message_manager import MessageManager, Message
from .rag_retriever import RAGRetriever

# Try to import mem0 types, fallback to string if not available
try:
    from mem0.memory.types import MemoryType
    HAS_MEM0_TYPES = True
except ImportError:
    HAS_MEM0_TYPES = False
    MemoryType = str


class MemoryManager:
    """Memory management component based on mem0"""
    
    def __init__(self, settings: Optional[AgentSettings] = None):
        self.settings = settings or agentSettings
  
        self.memory_client = self._initialize_memory()
        self.message_manager = MessageManager(self.settings)
        #self.rag_retriever = RAGRetriever(self.settings)
        self.search_memory_enabled = self.settings.memory.search_memory
        self.search_rag_enabled = self.settings.memory.search_rag
    
    def _initialize_memory(self) -> Memory:
        """Initialize mem0 memory client with configuration"""
        config = self.settings.dump_with_secrets(exclude={"llm":{"config": {"context_length"}}})
        keys = ["llm", "embedder", "vector_store", "history_db_path",
                "graph_store", "version", "custom_fact_extraction_prompt",
                "custom_update_memory_prompt"]
        mc_kwargs = {k: config[k] for k in keys if k in config}
        
        return Memory.from_config(mc_kwargs)
    
    def add_memory(self, 
                  messages: List[LLMMessage], 
                  user_id: str, 
                  agent_id: str, 
                  run_id: Optional[str] = None) -> List[str]:
        """
        Add messages to memory
        
        Args:
            messages: List of message dictionaries with 'role' and 'content'
            user_id: User identifier
            agent_id: Agent identifier
            run_id: Session/run identifier
            
        Returns:
            List of memory IDs
        """
        memory_ids = []
        
        for message in messages:
            role = message.role.upper()
            content = message.content
            
            # Map role to MemoryType
            
           # memory_type = MemoryType.PROCEDURAL.value
            
            # Create metadata
            metadata = {
                'user_id': user_id,
                'agent_id': agent_id,
                'run_id': run_id or str(uuid.uuid4()),
                'role': role
            }
            
            # Add to memory
            memory = self.memory_client.add(
                messages=message.to_dict(),
            #    memory_type=memory_type,
                user_id = user_id,
                agent_id = agent_id,
                run_id= run_id,
                metadata=metadata,
                infer=False
            )
            
            for result in memory["results"]:
                memory_ids.append(result["id"])
        
        return memory_ids
    
    def get_memory(self, 
                  user_id: Optional[str] = None,
                  agent_id: Optional[str] = None,
                  run_id: Optional[str] = None,
                  filters: Optional[Dict[str, Any]] = None,
                  limit: int = 100) -> List[Any]:
        """
        Retrieve memories with filters
        
        Args:
            user_id: Filter by user ID
            agent_id: Filter by agent ID
            run_id: Filter by run ID
            filters: Additional filters
            limit: Maximum number of memories to return
            
        Returns:
            List of Memory objects
        """
     
        
        return self.memory_client.get_all(user_id=user_id, agent_id=agent_id, run_id=run_id, limit=limit)
    
    def search_memory(self, 
                     query: str, 
                     user_id: Optional[str] = None,
                     agent_id: Optional[str] = None,
                     run_id: Optional[str] = None,
                     limit: int = 10) -> List[Any]:
        """
        Search memories by query
        
        Args:
            query: Search query
            user_id: Filter by user ID
            agent_id: Filter by agent ID
            run_id: Filter by run ID
            limit: Maximum number of results
            
        Returns:
            List of relevant Memory objects
        """
        filters = {}
        if user_id:
            filters['user_id'] = user_id
        if agent_id:
            filters['agent_id'] = agent_id
        if run_id:
            filters['run_id'] = run_id
        
        return self.memory_client.search(query=query, filters=filters, limit=limit)
    
    def update_memory(self, memory_id: str, data: Dict[str, Any]) -> Any:
        """
        Update a memory
        
        Args:
            memory_id: ID of the memory to update
            data: Data to update
            
        Returns:
            Updated Memory object
        """
        return self.memory_client.update(memory_id=memory_id, data=data)
    
    def delete_memory(self, memory_id: str) -> bool:
        """
        Delete a memory
        
        Args:
            memory_id: ID of the memory to delete
            
        Returns:
            True if successful
        """
        return self.memory_client.delete(memory_id=memory_id)
    
    def _get_memory_type(self, role: str):
        """Map role string to MemoryType enum or string"""
        if HAS_MEM0_TYPES:
            role_mapping = {
                'USER': MemoryType.USER,
                'SYSTEM': MemoryType.SYSTEM,
                'ASSISTANT': MemoryType.ASSISTANT,
                'TOOL': MemoryType.TOOL
            }
            return role_mapping.get(role.upper(), MemoryType.USER)
        else:
            # Fallback to string representation
            return role.upper()


    # RAG retrieval methods
    def search_rag(self, query: str, limit: int = 10) -> List[Dict[str, Any]]:
        """Search RAG knowledge base with query"""
        return self.rag_retriever.search_rag(query, limit)
    
    def add_to_knowledge_base(self, 
                            content: str, 
                            metadata: Optional[Dict[str, Any]] = None) -> str:
        """Add document to RAG knowledge base"""
        return self.rag_retriever.add_to_knowledge_base(content, metadata)
    
    def delete_from_knowledge_base(self, document_id: str) -> bool:
        """Delete document from RAG knowledge base"""
        return self.rag_retriever.delete_from_knowledge_base(document_id)

    # Unified search method
    def unified_search(self, 
                      query: str, 
                      user_id: Optional[str] = None,
                      agent_id: Optional[str] = None,
                      run_id: Optional[str] = None,
                      memory_limit: int = 10,
                      rag_limit: int = 10) -> Dict[str, List[Dict[str, Any]]]:
        """
        Unified search across memory and RAG knowledge base
        
        Returns:
            Dictionary with memory_results and rag_results
        """
        results = {
            'memory_results': [],
            'rag_results': []
        }
        
        # Search memory if enabled
        if self.search_memory_enabled:
            try:
                memory_results = self.search_memory(
                    query=query,
                    user_id=user_id,
                    agent_id=agent_id,
                    run_id=run_id,
                    limit=memory_limit
                )
                results['memory_results'] = self._format_memory_results(memory_results)
            except Exception as e:
                print(f"Error searching memory: {e}")
                results['memory_results'] = []
        
        # Search RAG if enabled
        if self.search_rag_enabled:
            try:
                rag_results = self.search_rag(query=query, limit=rag_limit)
                results['rag_results'] = rag_results
            except Exception as e:
                print(f"Error searching RAG: {e}")
                results['rag_results'] = []
        
        return results
    
    def _format_memory_results(self, memory_objects: List[Any]) -> List[Dict[str, Any]]:
        """
        Format memory objects for consistent response format
        
        Args:
            memory_objects: List of memory objects from mem0
            
        Returns:
            Formatted memory results
        """
        formatted_results = []
        
        for memory_obj in memory_objects:
            # Handle both mem0 Memory objects and dictionaries
            if hasattr(memory_obj, 'content'):
                # mem0 Memory object
                formatted_results.append({
                    'id': getattr(memory_obj, 'id', ''),
                    'content': getattr(memory_obj, 'content', ''),
                    'metadata': getattr(memory_obj, 'metadata', {}),
                    'type': getattr(memory_obj, 'memory_type', ''),
                    'score': getattr(memory_obj, 'score', 1.0)  # mem0 search returns scores
                })
            elif isinstance(memory_obj, dict):
                # Dictionary format
                formatted_results.append({
                    'id': memory_obj.get('id', ''),
                    'content': memory_obj.get('content', ''),
                    'metadata': memory_obj.get('metadata', {}),
                    'type': memory_obj.get('memory_type', ''),
                    'score': memory_obj.get('score', 1.0)
                })
        
        return formatted_results
    
    def enable_memory_search(self, enabled: bool):
        """Enable or disable memory search in unified search"""
        self.search_memory_enabled = enabled
    
    def enable_rag_search(self, enabled: bool):
        """Enable or disable RAG search in unified search"""
        self.search_rag_enabled = enabled
    
    def get_search_config(self) -> Dict[str, bool]:
        """Get current search configuration"""
        return {
            'memory_enabled': getattr(self, 'search_memory_enabled', True),
            'rag_enabled': getattr(self, 'search_rag_enabled', True)
        }


    # Message management methods
    def add_message(self, 
                   user_id: Optional[str] = None,
                   conversation_id: Optional[str] = None,
                   agent_id: Optional[str] = None,
                   role: str = "user",
                   content: str = "",
                   type_name : str = "",
                   trace_id: Optional[str] = None,
                   tool_name: Optional[str] = None) -> str:
        """
        Add a new message to database
        
        Args:
            user_id: User identifier
            conversation_id: Conversation identifier
            agent_id: Agent identifier
            role: Message role (user, assistant, system, etc.)
            content: Message content
            trace_id: Trace identifier, auto-generated if not provided
            
        Returns:
            Trace ID
        """
        return self.message_manager.add_message(
            user_id=user_id,
            conversation_id=conversation_id,
            agent_id=agent_id,
            role=role,
            content=content,
            type_name=type_name,
            trace_id=trace_id,
            tool_name=tool_name
        )
    
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
            List of messages with metadata
        """
        return self.message_manager.get_messages(
            trace_id=trace_id,
            user_id=user_id,
            conversation_id=conversation_id,
            agent_id=agent_id,
            type_name=type_name,
            limit=limit
        )
    
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
        return self.message_manager.update_message(
            trace_id=trace_id,
            content=content,
            role=role
        )
    
    def delete_message(self, trace_id: str) -> bool:
        """
        Delete a message by trace_id
        
        Args:
            trace_id: Trace ID of the message to delete
            
        Returns:
            True if successful
        """
        return self.message_manager.delete_message(trace_id=trace_id)

# Singleton instance
memory_manager = MemoryManager()
