from typing import List, Dict, Any, Optional
import numpy as np
import asyncio
from abc import ABC, abstractmethod

# Import config
import sys
import os
sys.path.append(os.path.join(os.path.dirname(__file__), '..'))
from config.config import AgentSettings

# Qdrant imports
try:
    from qdrant_client import QdrantClient
    from qdrant_client.models import Filter, FieldCondition, MatchValue, SearchRequest
    QDRANT_AVAILABLE = True
except ImportError:
    QDRANT_AVAILABLE = False

# Milvus imports
try:
    from pymilvus import Collection, connections, utility
    MILVUS_AVAILABLE = True
except ImportError:
    MILVUS_AVAILABLE = False


class VectorDBClient(ABC):
    """抽象基类，定义向量数据库客户端的接口"""
    
    @abstractmethod
    async def search(self, query_embedding: List[float], top_k: int) -> List[Dict[str, Any]]:
        """搜索相似向量"""
        pass


class QdrantClientWrapper(VectorDBClient):
    """Qdrant 客户端包装器"""
    
    def __init__(self, config):
        self.client = QdrantClient(url=config.url)
        self.collection_name = config.collection_name
        self.retrieval_field = config.retrieval_field  # 通过embedding检索出来的文本内容字段
        self.query_field = config.query_field  # embedding向量存储字段
    
    async def search(self, query_embedding: List[float], top_k: int) -> List[Dict[str, Any]]:
        """使用 Qdrant 进行搜索"""
        try:
            search_results = self.client.search(
                collection_name=self.collection_name,
                query_vector=query_embedding,  # 使用 query_embedding 进行相似性搜索
                limit=top_k,
                with_payload=True,  # 确保返回 payload 数据
                with_vectors=True   # 确保返回向量数据
            )
            
            results = []
            for result in search_results:
                # 获取通过embedding检索出来的文本内容（retrieval_field）
                retrieved_content = result.payload.get(self.retrieval_field, '')
                results.append({
                    'id': str(result.id),
                    'score': result.score,
                    'content': retrieved_content,  # 返回 retrieval_field 的文本内容
                    'metadata': result.payload.get('metadata', {}),
                    'vector': result.vector
                })
            
            return results
            
        except Exception as e:
            print(f"Qdrant search error: {e}")
            return []


class MilvusClientWrapper(VectorDBClient):
    """Milvus 客户端包装器"""
    
    def __init__(self, config):
        self.address = config.address
        self.collection_name = config.collection
        self.retrieval_field = config.retrieval_field  # 通过embedding检索出来的文本内容字段
        self.query_field = config.query_field  # embedding向量存储字段
        self.dimension = config.dimension
        self.metric_type = config.metric_type
        self.timeout = config.timeout
        
        # 连接到 Milvus
        connections.connect("default", host=self.address.split(':')[0], port=int(self.address.split(':')[1]))
        
        # 获取集合
        self.collection = Collection(self.collection_name)
        self.collection.load()
    
    async def search(self, query_embedding: List[float], top_k: int) -> List[Dict[str, Any]]:
        """使用 Milvus 进行搜索"""
        try:
            # 在 Milvus 中搜索
            search_params = {
                "metric_type": self.metric_type,
                "params": {"nprobe": 10}
            }
            
            search_results = self.collection.search(
                data=[query_embedding],
                anns_field=self.query_field,  # 在 query_field 字段上进行相似性搜索
                param=search_params,
                limit=top_k,
                output_fields=[self.retrieval_field, "metadata"]  # 输出 retrieval_field 和 metadata
            )
            
            results = []
            for hits in search_results:
                for hit in hits:
                    # 获取通过embedding检索出来的文本内容（retrieval_field）
                    retrieved_content = hit.entity.get(self.retrieval_field, '')
                    results.append({
                        'id': str(hit.id),
                        'score': float(hit.score),
                        'content': retrieved_content,  # 返回 retrieval_field 的文本内容
                        'metadata': hit.entity.get('metadata', {}),
                        'vector': hit.vector
                    })
            
            return results
            
        except Exception as e:
            print(f"Milvus search error: {e}")
            return []


class RAGRetriever:
    """RAG retrieval functionality supporting both Qdrant and Milvus"""
    
    def __init__(self, settings: Optional[AgentSettings] = None):
        self.settings = settings or AgentSettings()
        self.vector_client = self._initialize_vector_client()
    
    def _initialize_vector_client(self) -> VectorDBClient:
        """根据配置初始化相应的向量数据库客户端"""
        rag_config = self.settings.memory.rag_retriever_config
        
        if not rag_config:
            raise ValueError("RAG retriever config not found in settings")
        
        provider = rag_config.provider.lower()
        config = rag_config.config
        
        if provider == "qdrant":
            if not QDRANT_AVAILABLE:
                raise ImportError("Qdrant client not available. Please install: pip install qdrant-client")
            
            # 使用 vector_store 配置中的 URL
            vector_config = self.settings.vector_store.config
            config.url = vector_config.url
            config.collection_name = vector_config.collection_name
            
            return QdrantClientWrapper(config)
            
        elif provider == "milvus":
            if not MILVUS_AVAILABLE:
                raise ImportError("Milvus client not available. Please install: pip install pymilvus")
            
            return MilvusClientWrapper(config)
            
        else:
            raise ValueError(f"Unsupported vector database provider: {provider}")
    
    async def search_rag(self, query: str, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        """
        搜索 RAG 知识库
        
        Args:
            query: 搜索查询
            limit: 最大返回结果数量，如果为 None 则使用配置中的 top_k
            
        Returns:
            包含检索结果的列表，每个结果包含 content（retrieval_field 字段的文本内容）
        """
        try:
            # 生成查询嵌入
            query_embedding = await self._generate_embedding(query)
            
            # 确定返回数量
            top_k = limit or self.settings.memory.rag_retriever_config.config.top_k
            
            # 使用相应的向量数据库客户端进行搜索
            results = await self.vector_client.search(query_embedding, top_k)
            
            return results
            
        except Exception as e:
            print(f"Error in RAG search: {e}")
            return []
    
    async def _generate_embedding(self, text: str) -> List[float]:
        """
        使用配置的嵌入模型生成文本嵌入
        
        Args:
            text: 要嵌入的文本
            
        Returns:
            嵌入向量
        """
        # TODO: 这里应该使用配置的嵌入模型
        # 目前返回随机向量作为占位符
        embedding_dim = self.settings.embedder.config.embedding_dims
        return np.random.rand(embedding_dim).tolist()


# 工厂函数
def create_rag_retriever(settings: Optional[AgentSettings] = None) -> RAGRetriever:
    """创建 RAG 检索器实例"""
    return RAGRetriever(settings)


# 单例实例（可选）
# rag_retriever = create_rag_retriever()