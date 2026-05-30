from types import SimpleNamespace
from unittest.mock import Mock, patch

from memory.memory_mgr import (
    DisabledMemoryClient,
    DisabledMessageManager,
    DisabledRAGRetriever,
    MemoryManager,
)


def _settings(search_memory=True, search_rag=True):
    return SimpleNamespace(
        memory=SimpleNamespace(
            search_memory=search_memory,
            search_rag=search_rag,
            rag_retriever_config=None,
        ),
        dump_with_secrets=lambda **_: {},
    )


def test_missing_memory_client_degrades_without_import_failure():
    with patch("memory.memory_mgr.Memory", None), patch(
        "memory.memory_mgr.MessageManager", side_effect=RuntimeError("db down")
    ), patch("memory.memory_mgr.RAGRetriever", side_effect=RuntimeError("rag down")):
        manager = MemoryManager(_settings())

    assert isinstance(manager.memory_client, DisabledMemoryClient)
    assert isinstance(manager.message_manager, DisabledMessageManager)
    assert isinstance(manager.rag_retriever, DisabledRAGRetriever)
    assert manager.add_memory({"role": "user", "content": "hello"}, "u1", "a1") == []
    assert manager.get_messages(user_id="u1") == []
    assert manager.search_rag("query") == []


def test_memory_operations_return_safe_values_on_runtime_errors():
    manager = MemoryManager(_settings(search_rag=False))
    manager.memory_client = Mock()
    manager.memory_client.add.side_effect = RuntimeError("write failed")
    manager.memory_client.get_all.side_effect = RuntimeError("read failed")
    manager.memory_client.search.side_effect = RuntimeError("search failed")
    manager.memory_client.update.side_effect = RuntimeError("update failed")
    manager.memory_client.delete.side_effect = RuntimeError("delete failed")

    assert manager.add_memory({"role": "user", "content": "hello"}, "u1", "a1") == []
    assert manager.get_memory(user_id="u1") == []
    assert manager.search_memory("hello", user_id="u1") == []
    assert manager.update_memory("mem-1", {"content": "new"}) is None
    assert manager.delete_memory("mem-1") is False
    assert set(manager.get_degradation_status()) >= {
        "memory_write",
        "memory_read",
        "memory_search",
        "memory_update",
        "memory_delete",
    }


def test_message_operations_return_safe_values_on_runtime_errors():
    manager = MemoryManager(_settings(search_rag=False))
    manager.message_manager = Mock()
    manager.message_manager.add_message.side_effect = RuntimeError("write failed")
    manager.message_manager.get_messages.side_effect = RuntimeError("read failed")
    manager.message_manager.update_message.side_effect = RuntimeError("update failed")
    manager.message_manager.delete_message.side_effect = RuntimeError("delete failed")

    assert manager.add_message(trace_id="trace-1", content="hello") == "trace-1"
    assert manager.get_messages(trace_id="trace-1") == []
    assert manager.update_message("trace-1", content="new") is False
    assert manager.delete_message("trace-1") is False
    assert set(manager.get_degradation_status()) >= {
        "message_write",
        "message_read",
        "message_update",
        "message_delete",
    }


def test_unified_search_includes_scope_and_warning_on_failures():
    manager = MemoryManager(_settings())
    manager.search_memory = Mock(side_effect=RuntimeError("memory down"))
    manager.search_rag = Mock(return_value=[{"content": "rag result"}])

    result = manager.unified_search("query", user_id="user-1", agent_id="agent-1", run_id="run-1")

    assert result["memory_results"] == []
    assert result["rag_results"] == [{"content": "rag result"}]
    assert result["scope"] == {
        "user_id": "user-1",
        "agent_id": "agent-1",
        "run_id": "run-1",
    }
    assert result["warnings"] == [{"component": "memory_search", "reason": "RuntimeError"}]
