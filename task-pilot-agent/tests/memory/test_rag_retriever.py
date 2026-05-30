import asyncio
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from memory.rag_retriever import QdrantClientWrapper, RAGRetriever


def _settings(provider: str = "qdrant"):
    rag_config = SimpleNamespace(
        provider=provider,
        config=SimpleNamespace(
            top_k=10,
            query_field="query_emb",
            query_result="content",
            url="",
            collection_name="",
        ),
    )
    return SimpleNamespace(
        memory=SimpleNamespace(rag_retriever_config=rag_config),
        vector_store=SimpleNamespace(
            config=SimpleNamespace(
                url="http://localhost:6333",
                collection_name="test_collection",
            )
        ),
        embedder=SimpleNamespace(config=SimpleNamespace(embedding_dims=4)),
    )


def _run(coro):
    return asyncio.run(coro)


def test_initializes_qdrant_wrapper_from_settings():
    settings = _settings()

    with patch("memory.rag_retriever.QDRANT_AVAILABLE", True), patch(
        "memory.rag_retriever.QdrantClient"
    ) as qdrant_client:
        retriever = RAGRetriever(settings)

    assert isinstance(retriever.vector_client, QdrantClientWrapper)
    qdrant_client.assert_called_once_with(url="http://localhost:6333")
    assert retriever.vector_client.collection_name == "test_collection"


def test_missing_rag_config_fails_fast():
    settings = _settings()
    settings.memory.rag_retriever_config = None

    with pytest.raises(ValueError, match="RAG retriever config not found"):
        RAGRetriever(settings)


def test_search_rag_returns_structured_results():
    settings = _settings()
    result_item = Mock()
    result_item.id = "doc-1"
    result_item.score = 0.9
    result_item.payload = {"content": "matched text", "metadata": {"source": "kb"}}
    result_item.vector = [0.1, 0.2]

    with patch("memory.rag_retriever.QDRANT_AVAILABLE", True), patch(
        "memory.rag_retriever.QdrantClient"
    ) as qdrant_client:
        qdrant_client.return_value.search.return_value = [result_item]
        retriever = RAGRetriever(settings)

        async def fake_embedding(_text):
            return [0.1, 0.2]

        retriever._generate_embedding = fake_embedding
        results = _run(retriever.search_rag("query", limit=3))

    assert results == [
        {
            "id": "doc-1",
            "score": 0.9,
            "content": "matched text",
            "metadata": {"source": "kb"},
            "vector": [0.1, 0.2],
        }
    ]
    qdrant_client.return_value.search.assert_called_once_with(
        collection_name="test_collection",
        query_vector=[0.1, 0.2],
        limit=3,
        with_payload=True,
        with_vectors=True,
    )


def test_search_rag_degrades_to_empty_list_on_error():
    settings = _settings()

    with patch("memory.rag_retriever.QDRANT_AVAILABLE", True), patch(
        "memory.rag_retriever.QdrantClient"
    ) as qdrant_client:
        qdrant_client.return_value.search.side_effect = RuntimeError("down")
        retriever = RAGRetriever(settings)

        async def fake_embedding(_text):
            return [0.1, 0.2]

        retriever._generate_embedding = fake_embedding
        assert _run(retriever.search_rag("query")) == []


def test_add_and_delete_knowledge_base_use_qdrant_when_available():
    settings = _settings()

    with patch("memory.rag_retriever.QDRANT_AVAILABLE", True), patch(
        "memory.rag_retriever.QdrantClient"
    ) as qdrant_client, patch("memory.rag_retriever.PointStruct") as point_struct, patch(
        "memory.rag_retriever.uuid.uuid4", return_value="doc-123"
    ):
        point_struct.return_value = "point"
        retriever = RAGRetriever(settings)

        async def fake_embedding(_text):
            return [0.1, 0.2]

        retriever._generate_embedding = fake_embedding
        document_id = _run(retriever.add_to_knowledge_base("content", {"source": "test"}))
        deleted = _run(retriever.delete_from_knowledge_base("doc-123"))

    assert document_id == "doc-123"
    assert deleted is True
    point_struct.assert_called_once_with(
        id="doc-123",
        vector=[0.1, 0.2],
        payload={"content": "content", "metadata": {"source": "test"}},
    )
    qdrant_client.return_value.upsert.assert_called_once_with(
        collection_name="test_collection",
        points=["point"],
    )
    qdrant_client.return_value.delete.assert_called_once_with(
        collection_name="test_collection",
        points_selector=["doc-123"],
    )
