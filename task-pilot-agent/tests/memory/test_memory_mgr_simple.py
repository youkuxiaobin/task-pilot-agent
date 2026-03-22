import pytest
from unittest.mock import Mock, patch, MagicMock
from typing import Dict, Any, List
import uuid


def test_memory_manager_initialization():
    """Test basic MemoryManager initialization with mocked dependencies"""
    # First set up the path
    import sys
    import os
    sys.path.append(os.path.join(os.path.dirname(__file__), '../..'))
    
    # Mock all dependencies
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
    
    # Mock the Memory class
    mock_memory = Mock()
    
    # Mock PlanManager and RAGRetriever
    mock_plan_manager = Mock()
    mock_rag_retriever = Mock()
    
    with patch('memory.memory_mgr.Memory', return_value=mock_memory), \
         patch('memory.memory_mgr.PlanManager', return_value=mock_plan_manager), \
         patch('memory.memory_mgr.RAGRetriever', return_value=mock_rag_retriever):
        
        from memory.memory_mgr import MemoryManager
        
        # Create instance with mocked settings
        manager = MemoryManager(mock_settings)
        
        # Verify initialization
        assert manager.settings == mock_settings
        assert manager.search_memory_enabled == mock_settings.memory.search_memory
        assert manager.search_rag_enabled == mock_settings.memory.search_rag
        assert manager.memory_client == mock_memory
        assert manager.plan_manager == mock_plan_manager
        assert manager.rag_retriever == mock_rag_retriever


def test_memory_manager_default_settings():
    """Test MemoryManager initialization with default settings"""
    # First set up the path
    import sys
    import os
    sys.path.append(os.path.join(os.path.dirname(__file__), '../..'))
    
    # Mock the default AgentSettings
    mock_default_settings = Mock()
    mock_default_settings.memory = Mock()
    mock_default_settings.memory.search_memory = True
    mock_default_settings.memory.search_rag = True
    mock_default_settings.vector_store = Mock()
    mock_default_settings.vector_store.provider = "qdrant"
    mock_default_settings.vector_store.config = Mock()
    mock_default_settings.vector_store.config.model_dump.return_value = {"path": "/tmp/test"}
    mock_default_settings.embedder = Mock()
    mock_default_settings.embedder.provider = "openai"
    mock_default_settings.embedder.config = Mock()
    mock_default_settings.embedder.config.model_dump.return_value = {"model": "test-model"}
    
    # Mock dependencies
    mock_memory = Mock()
    mock_plan_manager = Mock()
    mock_rag_retriever = Mock()
    
    with patch('memory.memory_mgr.AgentSettings', return_value=mock_default_settings), \
         patch('memory.memory_mgr.Memory', return_value=mock_memory), \
         patch('memory.memory_mgr.PlanManager', return_value=mock_plan_manager), \
         patch('memory.memory_mgr.RAGRetriever', return_value=mock_rag_retriever):
        
        from memory.memory_mgr import MemoryManager
        
        # Create instance without providing settings
        manager = MemoryManager()
        
        # Should use default settings
        assert manager.settings == mock_default_settings


if __name__ == "__main__":
    test_memory_manager_initialization()
    test_memory_manager_default_settings()
    print("All tests passed!")