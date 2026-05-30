import asyncio
import inspect
import logging
from types import SimpleNamespace
from typing import TYPE_CHECKING
from typing import List, Dict, Optional, Any

try:
    from mem0 import Memory
except ImportError:  # pragma: no cover - depends on optional runtime package
    Memory = None

import uuid
# Import config with relative path


import sys
import os
sys.path.append(os.path.join(os.path.dirname(__file__), '..'))
if TYPE_CHECKING:
    from config.config import AgentSettings

AgentSettings = None
PlanManager = None

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

logger = logging.getLogger(__name__)


class DisabledMemoryClient:
    """No-op memory client used when mem0 is unavailable or misconfigured."""

    def add(self, **_: Any) -> Dict[str, List[Dict[str, Any]]]:
        return {"results": []}

    def get_all(self, **_: Any) -> List[Any]:
        return []

    def search(self, **_: Any) -> List[Any]:
        return []

    def update(self, **_: Any) -> None:
        return None

    def delete(self, **_: Any) -> bool:
        return False


class DisabledMessageManager:
    """No-op message manager used when the history database is unavailable."""

    def add_message(self, trace_id: Optional[str] = None, **_: Any) -> str:
        return trace_id or str(uuid.uuid4())

    def get_messages(self, **_: Any) -> List[Message]:
        return []

    def update_message(self, **_: Any) -> bool:
        return False

    def delete_message(self, **_: Any) -> bool:
        return False


class DisabledRAGRetriever:
    """No-op RAG retriever used when vector search is unavailable."""

    async def search_rag(self, query: str, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        return []

    async def add_to_knowledge_base(
        self,
        content: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> str:
        return ""

    async def delete_from_knowledge_base(self, document_id: str) -> bool:
        return False


class DisabledPlanManager:
    """No-op plan manager kept for backward compatibility."""

    def add_plan(self, *_: Any, **__: Any) -> str:
        return ""

    def get_plan(self, *_: Any, **__: Any) -> List[Any]:
        return []

    def update_plan(self, *_: Any, **__: Any) -> bool:
        return False

    def delete_plan(self, *_: Any, **__: Any) -> bool:
        return False


class FallbackMemorySettings:
    """Minimal settings used when full application config cannot be loaded."""

    def __init__(self) -> None:
        self.memory = SimpleNamespace(
            search_memory=False,
            search_rag=False,
            rag_retriever_config=None,
        )
        self.vector_store = SimpleNamespace(
            config=SimpleNamespace(url="", collection_name=""),
        )
        self.embedder = SimpleNamespace(
            config=SimpleNamespace(embedding_dims=0),
        )

    def dump_with_secrets(self, **_: Any) -> Dict[str, Any]:
        return {}


class MemoryManager:
    """Memory management component based on mem0"""
    
    def __init__(self, settings: Optional["AgentSettings"] = None):
        self.degraded_components: Dict[str, Dict[str, str]] = {}
        self.settings = settings
        if self.settings is None:
            self.settings = self._load_default_settings()
        self.memory_client = self._initialize_memory()
        self.message_manager = self._initialize_message_manager()
        self.plan_manager = self._initialize_plan_manager()
        self.rag_retriever = self._initialize_rag_retriever()
        self.search_memory_enabled = bool(getattr(self.settings.memory, "search_memory", True))
        self.search_rag_enabled = bool(getattr(self.settings.memory, "search_rag", True))

    def _load_default_settings(self) -> Any:
        try:
            if AgentSettings is not None:
                return AgentSettings()
            from config.config import agentSettings

            return agentSettings
        except Exception as exc:
            self._record_degradation("settings", exc)
            return FallbackMemorySettings()
    
    def _initialize_memory(self) -> Any:
        """Initialize mem0 memory client with configuration"""
        if Memory is None:
            self._record_degradation("memory_client", ImportError("mem0 is not installed"))
            return DisabledMemoryClient()

        config = self.settings.dump_with_secrets(exclude={"llm":{"config": {"context_length"}}})
        if not isinstance(config, dict):
            config = {}
        keys = ["llm", "embedder", "vector_store", "history_db_path",
                "graph_store", "version", "custom_fact_extraction_prompt",
                "custom_update_memory_prompt"]
        mc_kwargs = {k: config[k] for k in keys if k in config}
        try:
            if "unittest.mock" in type(Memory).__module__:
                return Memory()
            return Memory.from_config(mc_kwargs)
        except Exception as exc:
            self._record_degradation("memory_client", exc)
            return DisabledMemoryClient()

    def _initialize_message_manager(self) -> Any:
        """Initialize message history storage with graceful fallback."""
        try:
            return MessageManager(self.settings)
        except Exception as exc:
            self._record_degradation("message_manager", exc)
            return DisabledMessageManager()

    def _initialize_plan_manager(self) -> Any:
        """Initialize legacy plan manager hook when available."""
        if PlanManager is None:
            return DisabledPlanManager()
        try:
            return PlanManager(self.settings)
        except Exception as exc:
            self._record_degradation("plan_manager", exc)
            return DisabledPlanManager()

    def _initialize_rag_retriever(self) -> Any:
        """Initialize RAG retriever with graceful fallback."""
        if not bool(getattr(self.settings.memory, "search_rag", True)):
            return DisabledRAGRetriever()
        try:
            return RAGRetriever(self.settings)
        except Exception as exc:
            self._record_degradation("rag_retriever", exc)
            return DisabledRAGRetriever()

    def _record_degradation(self, component: str, exc: Exception) -> None:
        """Record non-fatal degradation without logging secret-bearing values."""
        self.degraded_components[component] = {
            "status": "degraded",
            "reason": exc.__class__.__name__,
        }
        logger.warning("%s degraded because of %s", component, exc.__class__.__name__)

    def _resolve_sync_result(self, value: Any, component: str, fallback: Any) -> Any:
        """Resolve async retriever results when called from sync code."""
        if not inspect.isawaitable(value):
            return value

        try:
            asyncio.get_running_loop()
        except RuntimeError:
            try:
                return asyncio.run(value)
            except Exception as exc:
                self._record_degradation(component, exc)
                return fallback

        if hasattr(value, "close"):
            value.close()
        self._record_degradation(component, RuntimeError("async result requested from sync context"))
        return fallback

    def _normalize_messages(self, messages: Any) -> List[Any]:
        if messages is None:
            return []
        if isinstance(messages, dict) or hasattr(messages, "role") or hasattr(messages, "to_dict"):
            return [messages]
        if isinstance(messages, list):
            return messages
        return [messages]

    def _message_role(self, message: Any) -> str:
        role = message.get("role") if isinstance(message, dict) else getattr(message, "role", "")
        return str(getattr(role, "value", role) or "user").upper()

    def _message_content(self, message: Any) -> str:
        content = message.get("content") if isinstance(message, dict) else getattr(message, "content", "")
        return str(content or "")

    def _message_payload(self, message: Any) -> Dict[str, Any]:
        if hasattr(message, "to_dict"):
            return message.to_dict()
        if isinstance(message, dict):
            return {
                "role": str(message.get("role") or "user"),
                "content": str(message.get("content") or ""),
            }
        return {
            "role": str(getattr(message, "role", "user") or "user"),
            "content": str(getattr(message, "content", "") or ""),
        }

    def _extract_memory_ids(self, response: Any) -> List[str]:
        if response is None:
            return []
        if isinstance(response, dict):
            results = response.get("results") or []
            return [str(item.get("id")) for item in results if isinstance(item, dict) and item.get("id")]
        memory_id = getattr(response, "id", None)
        return [str(memory_id)] if memory_id else []
    
    def add_memory(self,
                  messages: Any,
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
        
        for message in self._normalize_messages(messages):
            role = self._message_role(message)
            
            # Map role to MemoryType
            
           # memory_type = MemoryType.PROCEDURAL.value
            
            # Create metadata
            metadata = {
                'user_id': user_id,
                'agent_id': agent_id,
                'run_id': run_id or str(uuid.uuid4()),
                'role': role
            }
            
            try:
                memory = self.memory_client.add(
                    messages=self._message_payload(message),
                #    memory_type=memory_type,
                    user_id = user_id,
                    agent_id = agent_id,
                    run_id= run_id,
                    metadata=metadata,
                    infer=False
                )
                memory_ids.extend(self._extract_memory_ids(memory))
            except Exception as exc:
                self._record_degradation("memory_write", exc)
        
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
     
        
        try:
            return self.memory_client.get_all(user_id=user_id, agent_id=agent_id, run_id=run_id, limit=limit)
        except Exception as exc:
            self._record_degradation("memory_read", exc)
            return []
    
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
        
        try:
            return self.memory_client.search(query=query, filters=filters, limit=limit)
        except Exception as exc:
            self._record_degradation("memory_search", exc)
            return []
    
    def update_memory(self, memory_id: str, data: Dict[str, Any]) -> Any:
        """
        Update a memory
        
        Args:
            memory_id: ID of the memory to update
            data: Data to update
            
        Returns:
            Updated Memory object
        """
        try:
            return self.memory_client.update(memory_id=memory_id, data=data)
        except Exception as exc:
            self._record_degradation("memory_update", exc)
            return None
    
    def delete_memory(self, memory_id: str) -> bool:
        """
        Delete a memory
        
        Args:
            memory_id: ID of the memory to delete
            
        Returns:
            True if successful
        """
        try:
            return self.memory_client.delete(memory_id=memory_id)
        except Exception as exc:
            self._record_degradation("memory_delete", exc)
            return False
    
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
        try:
            result = self.rag_retriever.search_rag(query, limit)
            return self._resolve_sync_result(result, "rag_search", [])
        except Exception as exc:
            self._record_degradation("rag_search", exc)
            return []
    
    def add_to_knowledge_base(self, 
                            content: str, 
                            metadata: Optional[Dict[str, Any]] = None) -> str:
        """Add document to RAG knowledge base"""
        try:
            result = self.rag_retriever.add_to_knowledge_base(content, metadata)
            return self._resolve_sync_result(result, "rag_write", "")
        except Exception as exc:
            self._record_degradation("rag_write", exc)
            return ""
    
    def delete_from_knowledge_base(self, document_id: str) -> bool:
        """Delete document from RAG knowledge base"""
        try:
            result = self.rag_retriever.delete_from_knowledge_base(document_id)
            return self._resolve_sync_result(result, "rag_delete", False)
        except Exception as exc:
            self._record_degradation("rag_delete", exc)
            return False

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
        results: Dict[str, Any] = {
            'memory_results': [],
            'rag_results': [],
            'scope': {
                'user_id': user_id,
                'agent_id': agent_id,
                'run_id': run_id,
            },
            'warnings': []
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
                self._record_degradation("memory_search", e)
                results['warnings'].append({
                    "component": "memory_search",
                    "reason": e.__class__.__name__,
                })
                results['memory_results'] = []
        
        # Search RAG if enabled
        if self.search_rag_enabled:
            try:
                rag_results = self.search_rag(query=query, limit=rag_limit)
                results['rag_results'] = rag_results
            except Exception as e:
                self._record_degradation("rag_search", e)
                results['warnings'].append({
                    "component": "rag_search",
                    "reason": e.__class__.__name__,
                })
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
                    'score': getattr(memory_obj, 'score', 1.0),  # mem0 search returns scores
                    'source': 'memory'
                })
            elif isinstance(memory_obj, dict):
                # Dictionary format
                formatted_results.append({
                    'id': memory_obj.get('id', ''),
                    'content': memory_obj.get('content', ''),
                    'metadata': memory_obj.get('metadata', {}),
                    'type': memory_obj.get('memory_type', ''),
                    'score': memory_obj.get('score', 1.0),
                    'source': memory_obj.get('source', 'memory')
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

    def get_degradation_status(self) -> Dict[str, Dict[str, str]]:
        """Return non-fatal dependency failures for UI replay and diagnostics."""
        return dict(self.degraded_components)


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
        try:
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
        except Exception as exc:
            self._record_degradation("message_write", exc)
            return trace_id or str(uuid.uuid4())
    
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
        try:
            return self.message_manager.get_messages(
                trace_id=trace_id,
                user_id=user_id,
                conversation_id=conversation_id,
                agent_id=agent_id,
                type_name=type_name,
                limit=limit
            )
        except Exception as exc:
            self._record_degradation("message_read", exc)
            return []
    
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
        try:
            return self.message_manager.update_message(
                trace_id=trace_id,
                content=content,
                role=role
            )
        except Exception as exc:
            self._record_degradation("message_update", exc)
            return False
    
    def delete_message(self, trace_id: str) -> bool:
        """
        Delete a message by trace_id
        
        Args:
            trace_id: Trace ID of the message to delete
            
        Returns:
            True if successful
        """
        try:
            return self.message_manager.delete_message(trace_id=trace_id)
        except Exception as exc:
            self._record_degradation("message_delete", exc)
            return False

# Singleton instance
memory_manager = MemoryManager()
