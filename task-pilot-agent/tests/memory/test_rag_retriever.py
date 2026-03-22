import pytest
from unittest.mock import Mock, patch, MagicMock
from typing import Dict, Any, List
import uuid
import numpy as np

import sys
import os
sys.path.append(os.path.join(os.path.dirname(__file__), '../..'))

from memory.rag_retriever import RAGRetriever


class TestRAGRetriever:
    """Test suite for RAGRetriever class"""
    
    @pytest.fixture
    def mock_settings(self):
        """Mock AgentSettings with vector store configuration"""
        settings = Mock()
        settings.vector_store = Mock()
        settings.vector_store.config = Mock()
        settings.vector_store.config.path = "/tmp/test_qdrant"
        settings.vector_store.config.collection_name = "test_collection"
        settings.embedder = Mock()
        settings.embedder.config = Mock()
        settings.embedder.config.embedding_dims = 1024
        return settings
    
    @pytest.fixture
    def rag_retriever(self, mock_settings):
        """RAGRetriever instance with mocked dependencies"""
        with patch('memory.rag_retriever.QdrantClient') as mock_qdrant_class:
            mock_client = Mock()
            mock_qdrant_class.return_value = mock_client
            
            retriever = RAGRetriever(mock_settings)
            retriever.client = mock_client
            
            yield retriever
    
    def test_initialization_local_path(self, mock_settings):
        """Test RAGRetriever initialization with local path"""
        with patch('memory.rag_retriever.QdrantClient') as mock_qdrant_class:
            mock_client = Mock()
            mock_qdrant_class.return_value = mock_client
            
            retriever = RAGRetriever(mock_settings)
            
            assert retriever.settings == mock_settings
            assert retriever.client == mock_client
            assert retriever.collection_name == mock_settings.vector_store.config.collection_name
            mock_qdrant_class.assert_called_once_with(path=mock_settings.vector_store.config.path)
    
    def test_initialization_remote_url(self, mock_settings):
        """Test RAGRetriever initialization with remote URL"""
        mock_settings.vector_store.config.path = "http://localhost:6333"
        
        with patch('memory.rag_retriever.QdrantClient') as mock_qdrant_class:
            mock_client = Mock()
            mock_qdrant_class.return_value = mock_client
            
            retriever = RAGRetriever(mock_settings)
            
            assert retriever.settings == mock_settings
            assert retriever.client == mock_client
            mock_qdrant_class.assert_called_once_with(url=mock_settings.vector_store.config.path)
    
    def test_initialization_https_url(self, mock_settings):
        """Test RAGRetriever initialization with HTTPS URL"""
        mock_settings.vector_store.config.path = "https://qdrant.example.com"
        
        with patch('memory.rag_retriever.QdrantClient') as mock_qdrant_class:
            mock_client = Mock()
            mock_qdrant_class.return_value = mock_client
            
            retriever = RAGRetriever(mock_settings)
            
            assert retriever.settings == mock_settings
            assert retriever.client == mock_client
            mock_qdrant_class.assert_called_once_with(url=mock_settings.vector_store.config.path)
    
    def test_initialization_default_settings(self):
        """Test RAGRetriever initialization with default settings"""
        with patch('memory.rag_retriever.AgentSettings') as mock_settings_class:
            mock_settings = Mock()
            mock_settings.vector_store = Mock()
            mock_settings.vector_store.config = Mock()
            mock_settings.vector_store.config.path = "/tmp/test_qdrant"
            mock_settings.vector_store.config.collection_name = "test_collection"
            mock_settings.embedder = Mock()
            mock_settings.embedder.config = Mock()
            mock_settings.embedder.config.embedding_dims = 1024
            mock_settings_class.return_value = mock_settings
            
            with patch('memory.rag_retriever.QdrantClient') as mock_qdrant_class:
                mock_client = Mock()
                mock_qdrant_class.return_value = mock_client
                
                retriever = RAGRetriever()
                
                assert retriever.settings == mock_settings
    
    def test_generate_embedding(self, rag_retriever):
        """Test embedding generation"""
        text = "test text"
        
        with patch('memory.rag_retriever.np.random.rand') as mock_rand:
            mock_rand.return_value = np.array([0.1, 0.2, 0.3])
            
            result = rag_retriever._generate_embedding(text)
            
            assert len(result) == 1024  # embedding_dims
            assert isinstance(result, list)
            mock_rand.assert_called_once_with(1024)
    
    def test_search_rag_success(self, rag_retriever):
        """Test successful RAG search"""
        query = "test query"
        limit = 5
        
        # Mock search results
        mock_result1 = Mock()
        mock_result1.id = "doc1"
        mock_result1.score = 0.95
        mock_result1.payload = {"content": "content1", "metadata": {"source": "test"}}
        mock_result1.vector = [0.1, 0.2, 0.3]
        
        mock_result2 = Mock()
        mock_result2.id = "doc2"
        mock_result2.score = 0.87
        mock_result2.payload = {"content": "content2", "metadata": {"source": "test"}}
        mock_result2.vector = [0.4, 0.5, 0.6]
        
        rag_retriever.client.search.return_value = [mock_result1, mock_result2]
        
        with patch.object(rag_retriever, '_generate_embedding', return_value=[0.1, 0.2, 0.3]):
            result = rag_retriever.search_rag(query, limit)
            
            assert len(result) == 2
            assert result[0]['id'] == "doc1"
            assert result[0]['score'] == 0.95
            assert result[0]['content'] == "content1"
            assert result[0]['metadata'] == {"source": "test"}
            assert result[0]['vector'] == [0.1, 0.2, 0.3]
            
            assert result[1]['id'] == "doc2"
            assert result[1]['score'] == 0.87
            assert result[1]['content'] == "content2"
            
            rag_retriever.client.search.assert_called_once_with(
                collection_name=rag_retriever.collection_name,
                query_vector=[0.1, 0.2, 0.3],
                limit=limit
            )
    
    def test_search_rag_empty_results(self, rag_retriever):
        """Test RAG search with empty results"""
        query = "test query"
        
        rag_retriever.client.search.return_value = []
        
        with patch.object(rag_retriever, '_generate_embedding', return_value=[0.1, 0.2, 0.3]):
            result = rag_retriever.search_rag(query)
            
            assert result == []
            rag_retriever.client.search.assert_called_once()
    
    def test_search_rag_exception_handling(self, rag_retriever):
        """Test RAG search with exception handling"""
        query = "test query"
        
        rag_retriever.client.search.side_effect = Exception("Qdrant error")
        
        with patch.object(rag_retriever, '_generate_embedding', return_value=[0.1, 0.2, 0.3]):
            result = rag_retriever.search_rag(query)
            
            assert result == []
    
    def test_add_to_knowledge_base_success(self, rag_retriever):
        """Test successful addition to knowledge base"""
        content = "test content"
        metadata = {"source": "test", "type": "document"}
        
        with patch.object(rag_retriever, '_generate_embedding', return_value=[0.1, 0.2, 0.3]):
            with patch('memory.rag_retriever.uuid.uuid4', return_value=Mock(return_value="doc-123")):
                with patch('memory.rag_retriever.PointStruct') as mock_point_struct:
                    mock_point = Mock()
                    mock_point_struct.return_value = mock_point
                    
                    result = rag_retriever.add_to_knowledge_base(content, metadata)
                    
                    assert result == "doc-123"
                    rag_retriever.client.upsert.assert_called_once_with(
                        collection_name=rag_retriever.collection_name,
                        points=[mock_point]
                    )
    
    def test_add_to_knowledge_base_no_metadata(self, rag_retriever):
        """Test addition to knowledge base without metadata"""
        content = "test content"
        
        with patch.object(rag_retriever, '_generate_embedding', return_value=[0.1, 0.2, 0.3]):
            with patch('memory.rag_retriever.uuid.uuid4', return_value=Mock(return_value="doc-123")):
                with patch('memory.rag_retriever.PointStruct') as mock_point_struct:
                    mock_point = Mock()
                    mock_point_struct.return_value = mock_point
                    
                    result = rag_retriever.add_to_knowledge_base(content)
                    
                    assert result == "doc-123"
                    # Verify payload contains empty metadata
                    call_args = rag_retriever.client.upsert.call_args
                    points = call_args[1]['points']
                    assert points[0].payload['metadata'] == {}
    
    def test_add_to_knowledge_base_exception_handling(self, rag_retriever):
        """Test addition to knowledge base with exception handling"""
        content = "test content"
        
        rag_retriever.client.upsert.side_effect = Exception("Qdrant error")
        
        with patch.object(rag_retriever, '_generate_embedding', return_value=[0.1, 0.2, 0.3]):
            with patch('memory.rag_retriever.uuid.uuid4', return_value=Mock(return_value="doc-123")):
                with patch('memory.rag_retriever.PointStruct'):
                    with pytest.raises(Exception, match="Qdrant error"):
                        rag_retriever.add_to_knowledge_base(content)
    
    def test_delete_from_knowledge_base_success(self, rag_retriever):
        """Test successful deletion from knowledge base"""
        document_id = "doc-123"
        
        rag_retriever.client.delete.return_value = None
        
        result = rag_retriever.delete_from_knowledge_base(document_id)
        
        assert result is True
        rag_retriever.client.delete.assert_called_once_with(
            collection_name=rag_retriever.collection_name,
            points_selector=[document_id]
        )
    
    def test_delete_from_knowledge_base_exception_handling(self, rag_retriever):
        """Test deletion from knowledge base with exception handling"""
        document_id = "doc-123"
        
        rag_retriever.client.delete.side_effect = Exception("Qdrant error")
        
        result = rag_retriever.delete_from_knowledge_base(document_id)
        
        assert result is False
    
    def test_search_rag_with_different_limits(self, rag_retriever):
        """Test RAG search with different limit values"""
        query = "test query"
        
        rag_retriever.client.search.return_value = []
        
        with patch.object(rag_retriever, '_generate_embedding', return_value=[0.1, 0.2, 0.3]):
            # Test with limit=1
            rag_retriever.search_rag(query, limit=1)
            call1 = rag_retriever.client.search.call_args
            assert call1[1]['limit'] == 1
            
            # Test with limit=100
            rag_retriever.search_rag(query, limit=100)
            call2 = rag_retriever.client.search.call_args
            assert call2[1]['limit'] == 100
            
            # Test with default limit
            rag_retriever.search_rag(query)
            call3 = rag_retriever.client.search.call_args
            assert call3[1]['limit'] == 10
    
    def test_add_to_knowledge_base_payload_structure(self, rag_retriever):
        """Test that the payload structure is correct when adding to knowledge base"""
        content = "test content"
        metadata = {"source": "test"}
        
        with patch.object(rag_retriever, '_generate_embedding', return_value=[0.1, 0.2, 0.3]):
            with patch('memory.rag_retriever.uuid.uuid4', return_value=Mock(return_value="doc-123")):
                with patch('memory.rag_retriever.PointStruct') as mock_point_struct:
                    mock_point = Mock()
                    mock_point_struct.return_value = mock_point
                    
                    rag_retriever.add_to_knowledge_base(content, metadata)
                    
                    # Verify PointStruct was called with correct parameters
                    mock_point_struct.assert_called_once()
                    call_args = mock_point_struct.call_args
                    assert call_args[1]['id'] == "doc-123"
                    assert call_args[1]['vector'] == [0.1, 0.2, 0.3]
                    assert call_args[1]['payload']['content'] == content
                    assert call_args[1]['payload']['metadata'] == metadata


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
