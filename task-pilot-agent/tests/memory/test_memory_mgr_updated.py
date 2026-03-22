import pytest
from unittest.mock import Mock, patch, MagicMock
from typing import Dict, Any, List
import uuid

import sys
import os
sys.path.append(os.path.join(os.path.dirname(__file__), '../..'))

# Import the modules we need to mock
from config.config import AgentSettings

# Create a mock settings object
mock_settings = Mock()
mock_settings.memory = Mock()
mock_settings.memory.search_memory = True
mock_settings.memory.search_rag = True
mock_settings.vector_store = Mock()
mock_settings.vector_store.provider = "qdrant"
mock_settings.vector_store.config = Mock()
mock_settings.vector_store.config.model_dump.return_value = {"path": "/tmp/test"}
mock_settings.embedder = Mock()
mock_settings.embedder.provider = "openai"
mock_settings.embedder.config = Mock()
mock_settings.embedder.config.model_dump.return_value = {"model": "test-model"}

# Mock the AgentSettings class to return our mock settings
with patch('config.config.AgentSettings', return_value=mock_settings):
    with patch('memory.memory_mgr.AgentSettings', return_value=mock_settings):
        from memory.memory_mgr import MemoryManager


class TestMemoryManager:
    """Test suite for MemoryManager class"""
    
    @pytest.fixture
    def mock_settings(self):
        """Mock AgentSettings with memory configuration"""
        settings = Mock()
        settings.memory = Mock()
        settings.memory.search_memory = True
        settings.memory.search_rag = True
        settings.vector_store = Mock()
        settings.vector_store.provider = "qdrant"
        settings.vector_store.config = Mock()
        settings.vector_store.config.model_dump.return_value = {"path": "/tmp/test"}
        settings.embedder = Mock()
        settings.embedder.provider = "openai"
        settings.embedder.config = Mock()
        settings.embedder.config.model_dump.return_value = {"model": "test-model"}
        return settings
    
    @pytest.fixture
    def memory_manager(self, mock_settings):
        """MemoryManager instance with mocked dependencies"""
        with patch('memory.memory_mgr.Memory') as mock_memory_class:
            mock_memory = Mock()
            mock_memory_class.return_value = mock_memory
            
            with patch('memory.memory_mgr.PlanManager') as mock_plan_manager_class:
                mock_plan_manager = Mock()
                mock_plan_manager_class.return_value = mock_plan_manager
                
                with patch('memory.memory_mgr.RAGRetriever') as mock_rag_class:
                    mock_rag = Mock()
                    mock_rag_class.return_value = mock_rag
                    
                    manager = MemoryManager(mock_settings)
                    manager.memory_client = mock_memory
                    manager.plan_manager = mock_plan_manager
                    manager.rag_retriever = mock_rag
                    
                    yield manager
    
    def test_initialization(self, mock_settings):
        """Test MemoryManager initialization with settings"""
        with patch('memory.memory_mgr.Memory') as mock_memory_class:
            mock_memory = Mock()
            mock_memory_class.return_value = mock_memory
            
            with patch('memory.memory_mgr.PlanManager') as mock_plan_manager_class:
                mock_plan_manager = Mock()
                mock_plan_manager_class.return_value = mock_plan_manager
                
                with patch('memory.memory_mgr.RAGRetriever') as mock_rag_class:
                    mock_rag = Mock()
                    mock_rag_class.return_value = mock_rag
                    
                    manager = MemoryManager(mock_settings)
                    
                    assert manager.settings == mock_settings
                    assert manager.search_memory_enabled == mock_settings.memory.search_memory
                    assert manager.search_rag_enabled == mock_settings.memory.search_rag
                    assert manager.memory_client == mock_memory
                    assert manager.plan_manager == mock_plan_manager
                    assert manager.rag_retriever == mock_rag
    
    def test_initialization_default_settings(self):
        """Test MemoryManager initialization with default settings"""
        with patch('memory.memory_mgr.AgentSettings') as mock_settings_class:
            mock_settings = Mock()
            mock_settings.memory = Mock()
            mock_settings.memory.search_memory = True
            mock_settings.memory.search_rag = True
            mock_settings.vector_store = Mock()
            mock_settings.vector_store.provider = "qdrant"
            mock_settings.vector_store.config = Mock()
            mock_settings.vector_store.config.model_dump.return_value = {"path": "/tmp/test"}
            mock_settings.embedder = Mock()
            mock_settings.embedder.provider = "openai"
            mock_settings.embedder.config = Mock()
            mock_settings.embedder.config.model_dump.return_value = {"model": "test-model"}
            mock_settings_class.return_value = mock_settings
            
            with patch('memory.memory_mgr.Memory') as mock_memory_class:
                mock_memory = Mock()
                mock_memory_class.return_value = mock_memory
                
                with patch('memory.memory_mgr.PlanManager') as mock_plan_manager_class:
                    mock_plan_manager = Mock()
                    mock_plan_manager_class.return_value = mock_plan_manager
                    
                    with patch('memory.memory_mgr.RAGRetriever') as mock_rag_class:
                        mock_rag = Mock()
                        mock_rag_class.return_value = mock_rag
                        
                        manager = MemoryManager()
                        
                        assert manager.settings == mock_settings
    
    def test_add_memory(self, memory_manager):
        """Test adding messages to memory"""
        messages = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi there"}
        ]
        user_id = "test-user"
        agent_id = "test-agent"
        run_id = str(uuid.uuid4())
        
        # Mock memory client response
        mock_memory1 = Mock()
        mock_memory1.id = "mem-1"
        mock_memory2 = Mock()
        mock_memory2.id = "mem-2"
        
        memory_manager.memory_client.add.side_effect = [mock_memory1, mock_memory2]
        
        result = memory_manager.add_memory(messages, user_id, agent_id, run_id)
        
        assert result == ["mem-1", "mem-2"]
        assert memory_manager.memory_client.add.call_count == 2
        
        # Verify first call
        call1 = memory_manager.memory_client.add.call_args_list[0]
        assert call1[1]['content'] == "Hello"
        assert call1[1]['metadata']['user_id'] == user_id
        assert call1[1]['metadata']['agent_id'] == agent_id
        assert call1[1]['metadata']['run_id'] == run_id
        
        # Verify second call
        call2 = memory_manager.memory_client.add.call_args_list[1]
        assert call2[1]['content'] == "Hi there"
    
    def test_add_memory_with_missing_fields(self, memory_manager):
        """Test adding messages with missing role or content"""
        messages = [
            {"role": "user", "content": "Hello"},
            {"role": "", "content": "Empty role"},  # Empty role
            {"content": "No role"},  # Missing role
            {"role": "assistant", "content": ""},  # Empty content
        ]
        user_id = "test-user"
        agent_id = "test-agent"
        
        # Mock memory client response
        mock_memory = Mock()
        mock_memory.id = "mem-1"
        memory_manager.memory_client.add.return_value = mock_memory
        
        result = memory_manager.add_memory(messages, user_id, agent_id)
        
        assert len(result) == 4
        assert memory_manager.memory_client.add.call_count == 4
    
    def test_get_memory(self, memory_manager):
        """Test retrieving memories with filters"""
        user_id = "test-user"
        agent_id = "test-agent"
        run_id = "test-run"
        limit = 50
        
        mock_memories = [Mock(), Mock()]
        memory_manager.memory_client.get_all.return_value = mock_memories
        
        result = memory_manager.get_memory(user_id, agent_id, run_id, limit=limit)
        
        assert result == mock_memories
        memory_manager.memory_client.get_all.assert_called_once_with(
            filters={
                'user_id': user_id,
                'agent_id': agent_id,
                'run_id': run_id
            },
            limit=limit
        )
    
    def test_get_memory_no_filters(self, memory_manager):
        """Test retrieving memories without filters"""
        mock_memories = [Mock(), Mock()]
        memory_manager.memory_client.get_all.return_value = mock_memories
        
        result = memory_manager.get_memory()
        
        assert result == mock_memories
        memory_manager.memory_client.get_all.assert_called_once_with(
            filters={},
            limit=100
        )
    
    def test_get_memory_with_custom_filters(self, memory_manager):
        """Test retrieving memories with custom filters"""
        custom_filters = {"custom_key": "custom_value"}
        limit = 25
        
        mock_memories = [Mock()]
        memory_manager.memory_client.get_all.return_value = mock_memories
        
        result = memory_manager.get_memory(filters=custom_filters, limit=limit)
        
        assert result == mock_memories
        memory_manager.memory_client.get_all.assert_called_once_with(
            filters=custom_filters,
            limit=limit
        )
    
    def test_search_memory(self, memory_manager):
        """Test searching memories"""
        query = "test query"
        user_id = "test-user"
        agent_id = "test-agent"
        run_id = "test-run"
        limit = 5
        
        mock_results = [Mock(), Mock()]
        memory_manager.memory_client.search.return_value = mock_results
        
        result = memory_manager.search_memory(query, user_id, agent_id, run_id, limit)
        
        assert result == mock_results
        memory_manager.memory_client.search.assert_called_once_with(
            query=query,
            filters={
                'user_id': user_id,
                'agent_id': agent_id,
                'run_id': run_id
            },
            limit=limit
        )
    
    def test_search_memory_no_filters(self, memory_manager):
        """Test searching memories without filters"""
        query = "test query"
        mock_results = [Mock(), Mock()]
        memory_manager.memory_client.search.return_value = mock_results
        
        result = memory_manager.search_memory(query)
        
        assert result == mock_results
        memory_manager.memory_client.search.assert_called_once_with(
            query=query,
            filters={},
            limit=10
        )
    
    def test_update_memory(self, memory_manager):
        """Test updating a memory"""
        memory_id = "test-memory"
        update_data = {"content": "updated content", "metadata": {"key": "value"}}
        
        mock_updated_memory = Mock()
        memory_manager.memory_client.update.return_value = mock_updated_memory
        
        result = memory_manager.update_memory(memory_id, update_data)
        
        assert result == mock_updated_memory
        memory_manager.memory_client.update.assert_called_once_with(
            memory_id=memory_id,
            data=update_data
        )
    
    def test_delete_memory(self, memory_manager):
        """Test deleting a memory"""
        memory_id = "test-memory"
        memory_manager.memory_client.delete.return_value = True
        
        result = memory_manager.delete_memory(memory_id)
        
        assert result is True
        memory_manager.memory_client.delete.assert_called_once_with(memory_id=memory_id)
    
    def test_get_memory_type_with_mem0_types(self, memory_manager):
        """Test memory type mapping with mem0 types available"""
        with patch('memory.memory_mgr.HAS_MEM0_TYPES', True):
            with patch('memory.memory_mgr.MemoryType') as mock_memory_type:
                mock_memory_type.USER = "USER"
                mock_memory_type.SYSTEM = "SYSTEM"
                mock_memory_type.ASSISTANT = "ASSISTANT"
                mock_memory_type.TOOL = "TOOL"
                
                # Test different roles
                assert memory_manager._get_memory_type("user") == "USER"
                assert memory_manager._get_memory_type("system") == "SYSTEM"
                assert memory_manager._get_memory_type("assistant") == "ASSISTANT"
                assert memory_manager._get_memory_type("tool") == "TOOL"
                assert memory_manager._get_memory_type("unknown") == "USER"  # default
    
    def test_get_memory_type_without_mem0_types(self, memory_manager):
        """Test memory type mapping without mem0 types"""
        with patch('memory.memory_mgr.HAS_MEM0_TYPES', False):
            # Test different roles
            assert memory_manager._get_memory_type("user") == "USER"
            assert memory_manager._get_memory_type("system") == "SYSTEM"
            assert memory_manager._get_memory_type("assistant") == "ASSISTANT"
            assert memory_manager._get_memory_type("tool") == "TOOL"
            assert memory_manager._get_memory_type("unknown") == "UNKNOWN"
    
    def test_enable_memory_search(self, memory_manager):
        """Test enabling/disabling memory search"""
        memory_manager.enable_memory_search(False)
        assert memory_manager.search_memory_enabled is False
        
        memory_manager.enable_memory_search(True)
        assert memory_manager.search_memory_enabled is True
    
    def test_enable_rag_search(self, memory_manager):
        """Test enabling/disabling RAG search"""
        memory_manager.enable_rag_search(False)
        assert memory_manager.search_rag_enabled is False
        
        memory_manager.enable_rag_search(True)
        assert memory_manager.search_rag_enabled is True
    
    def test_get_search_config(self, memory_manager):
        """Test getting search configuration"""
        config = memory_manager.get_search_config()
        
        assert config == {
            'memory_enabled': True,
            'rag_enabled': True
        }
    
    def test_unified_search_both_enabled(self, memory_manager):
        """Test unified search with both memory and RAG enabled"""
        query = "test query"
        user_id = "test-user"
        
        # Mock memory search results
        mock_memory_results = [Mock(), Mock()]
        memory_manager.search_memory.return_value = mock_memory_results
        
        # Mock RAG search results
        mock_rag_results = [{"content": "rag result 1"}, {"content": "rag result 2"}]
        memory_manager.search_rag.return_value = mock_rag_results
        
        result = memory_manager.unified_search(query, user_id=user_id)
        
        assert 'memory_results' in result
        assert 'rag_results' in result
        assert len(result['memory_results']) == 2
        assert len(result['rag_results']) == 2
        
        memory_manager.search_memory.assert_called_once_with(
            query=query, user_id=user_id, agent_id=None, run_id=None, limit=10
        )
        memory_manager.search_rag.assert_called_once_with(query=query, limit=10)
    
    def test_unified_search_memory_disabled(self, memory_manager):
        """Test unified search with memory disabled"""
        memory_manager.search_memory_enabled = False
        query = "test query"
        
        # Mock RAG search results
        mock_rag_results = [{"content": "rag result"}]
        memory_manager.search_rag.return_value = mock_rag_results
        
        result = memory_manager.unified_search(query)
        
        assert result['memory_results'] == []
        assert result['rag_results'] == mock_rag_results
        memory_manager.search_memory.assert_not_called()
        memory_manager.search_rag.assert_called_once()
    
    def test_unified_search_rag_disabled(self, memory_manager):
        """Test unified search with RAG disabled"""
        memory_manager.search_rag_enabled = False
        query = "test query"
        
        # Mock memory search results
        mock_memory_results = [Mock(), Mock()]
        memory_manager.search_memory.return_value = mock_memory_results
        
        result = memory_manager.unified_search(query)
        
        assert len(result['memory_results']) == 2
        assert result['rag_results'] == []
        memory_manager.search_memory.assert_called_once()
        memory_manager.search_rag.assert_not_called()
    
    def test_unified_search_custom_limits(self, memory_manager):
        """Test unified search with custom limits"""
        query = "test query"
        
        # Mock results
        mock_memory_results = [Mock()]
        mock_rag_results = [{"content": "rag result"}]
        memory_manager.search_memory.return_value = mock_memory_results
        memory_manager.search_rag.return_value = mock_rag_results
        
        result = memory_manager.unified_search(
            query, 
            memory_limit=5, 
            rag_limit=3
        )
        
        memory_manager.search_memory.assert_called_once_with(
            query=query, user_id=None, agent_id=None, run_id=None, limit=5
        )
        memory_manager.search_rag.assert_called_once_with(query=query, limit=3)
    
    def test_unified_search_memory_error(self, memory_manager):
        """Test unified search when memory search fails"""
        query = "test query"
        
        # Mock memory search to raise exception
        memory_manager.search_memory.side_effect = Exception("Memory search failed")
        
        # Mock RAG search
        mock_rag_results = [{"content": "rag result"}]
        memory_manager.search_rag.return_value = mock_rag_results
        
        result = memory_manager.unified_search(query)
        
        assert result['memory_results'] == []
        assert result['rag_results'] == mock_rag_results
    
    def test_unified_search_rag_error(self, memory_manager):
        """Test unified search when RAG search fails"""
        query = "test query"
        
        # Mock memory search
        mock_memory_results = [Mock()]
        memory_manager.search_memory.return_value = mock_memory_results
        
        # Mock RAG search to raise exception
        memory_manager.search_rag.side_effect = Exception("RAG search failed")
        
        result = memory_manager.unified_search(query)
        
        assert len(result['memory_results']) == 1
        assert result['rag_results'] == []
    
    def test_format_memory_results_with_memory_objects(self, memory_manager):
        """Test formatting memory results with memory objects"""
        mock_memory1 = Mock()
        mock_memory1.id = "mem1"
        mock_memory1.content = "content1"
        mock_memory1.metadata = {"key": "value"}
        mock_memory1.memory_type = "USER"
        mock_memory1.score = 0.95
        
        mock_memory2 = Mock()
        mock_memory2.id = "mem2"
        mock_memory2.content = "content2"
        mock_memory2.metadata = {"key2": "value2"}
        mock_memory2.memory_type = "ASSISTANT"
        mock_memory2.score = 0.87
        
        memory_objects = [mock_memory1, mock_memory2]
        
        result = memory_manager._format_memory_results(memory_objects)
        
        assert len(result) == 2
        assert result[0]['id'] == "mem1"
        assert result[0]['content'] == "content1"
        assert result[0]['metadata'] == {"key": "value"}
        assert result[0]['type'] == "USER"
        assert result[0]['score'] == 0.95
        
        assert result[1]['id'] == "mem2"
        assert result[1]['content'] == "content2"
        assert result[1]['metadata'] == {"key2": "value2"}
        assert result[1]['type'] == "ASSISTANT"
        assert result[1]['score'] == 0.87
    
    def test_format_memory_results_with_dictionaries(self, memory_manager):
        """Test formatting memory results with dictionaries"""
        memory_dict1 = {
            'id': 'mem1',
            'content': 'content1',
            'metadata': {'key': 'value'},
            'memory_type': 'USER',
            'score': 0.95
        }
        
        memory_dict2 = {
            'id': 'mem2',
            'content': 'content2',
            'metadata': {'key2': 'value2'},
            'memory_type': 'ASSISTANT',
            'score': 0.87
        }
        
        memory_objects = [memory_dict1, memory_dict2]
        
        result = memory_manager._format_memory_results(memory_objects)
        
        assert len(result) == 2
        assert result[0]['id'] == "mem1"
        assert result[0]['content'] == "content1"
        assert result[0]['metadata'] == {"key": "value"}
        assert result[0]['type'] == "USER"
        assert result[0]['score'] == 0.95
        
        assert result[1]['id'] == "mem2"
        assert result[1]['content'] == "content2"
        assert result[1]['metadata'] == {"key2": "value2"}
        assert result[1]['type'] == "ASSISTANT"
        assert result[1]['score'] == 0.87
    
    def test_add_plan(self, memory_manager):
        """Test adding a plan"""
        steps = ["step1", "step2", "step3"]
        user_id = "test-user"
        agent_id = "test-agent"
        run_id = "test-run"
        
        mock_plan_id = "plan-123"
        memory_manager.plan_manager.add_plan.return_value = mock_plan_id
        
        result = memory_manager.add_plan(steps, user_id, agent_id, run_id)
        
        assert result == mock_plan_id
        memory_manager.plan_manager.add_plan.assert_called_once_with(
            steps, user_id, agent_id, run_id
        )
    
    def test_delete_plan(self, memory_manager):
        """Test deleting a plan"""
        plan_id = "plan-123"
        memory_manager.plan_manager.delete_plan.return_value = True
        
        result = memory_manager.delete_plan(plan_id)
        
        assert result is True
        memory_manager.plan_manager.delete_plan.assert_called_once_with(plan_id)
    
    def test_update_plan(self, memory_manager):
        """Test updating a plan"""
        plan_id = "plan-123"
        steps = ["updated step1", "updated step2"]
        user_id = "test-user"
        agent_id = "test-agent"
        run_id = "test-run"
        
        memory_manager.plan_manager.update_plan.return_value = True
        
        result = memory_manager.update_plan(plan_id, steps, user_id, agent_id, run_id)
        
        assert result is True
        memory_manager.plan_manager.update_plan.assert_called_once_with(
            plan_id, steps, user_id, agent_id, run_id
        )
    
    def test_get_plan(self, memory_manager):
        """Test retrieving a plan"""
        plan_id = "plan-123"
        user_id = "test-user"
        agent_id = "test-agent"
        run_id = "test-run"
        
        mock_plan_data = [{"step": "step1"}, {"step": "step2"}]
        memory_manager.plan_manager.get_plan.return_value = mock_plan_data
        
        result = memory_manager.get_plan(plan_id, user_id, agent_id, run_id)
        
        assert result == mock_plan_data
        memory_manager.plan_manager.get_plan.assert_called_once_with(
            plan_id, user_id, agent_id, run_id
        )
    
    def test_search_rag(self, memory_manager):
        """Test RAG search"""
        query = "test query"
        limit = 5
        
        mock_results = [{"content": "result1"}, {"content": "result2"}]
        memory_manager.rag_retriever.search_rag.return_value = mock_results
        
        result = memory_manager.search_rag(query, limit)
        
        assert result == mock_results
        memory_manager.rag_retriever.search_rag.assert_called_once_with(query, limit)
    
    def test_add_to_knowledge_base(self, memory_manager):
        """Test adding to knowledge base"""
        content = "test content"
        metadata = {"source": "test"}
        
        mock_doc_id = "doc-123"
        memory_manager.rag_retriever.add_to_knowledge_base.return_value = mock_doc_id
        
        result = memory_manager.add_to_knowledge_base(content, metadata)
        
        assert result == mock_doc_id
        memory_manager.rag_retriever.add_to_knowledge_base.assert_called_once_with(
            content, metadata
        )
    
    def test_delete_from_knowledge_base(self, memory_manager):
        """Test deleting from knowledge base"""
        document_id = "doc-123"
        memory_manager.rag_retriever.delete_from_knowledge_base.return_value = True
        
        result = memory_manager.delete_from_knowledge_base(document_id)
        
        assert result is True
        memory_manager.rag_retriever.delete_from_knowledge_base.assert_called_once_with(document_id)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
