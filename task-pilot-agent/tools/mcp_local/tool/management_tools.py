from __future__ import annotations

import json
import os
import re
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

import aiohttp
import yaml

from brain.core.agent_registry import AgentRegistry, default_agents_dir
from config.config import agentSettings, reveal_secret
from tools.mcp_local.tool.filesystem import _resolve_path, _workspace_root
from tools.mcp_local.tool.skill_registry import TaskSkillRegistry, safe_skill_id, search_agent_skills


SENSITIVE_KEYWORDS = ("api_key", "password", "secret", "token", "cookie", "authorization")


def _enabled(name: str) -> bool:
    return (os.getenv(name) or "").strip().lower() in {"1", "true", "yes", "on"}


def _safe_identifier(value: str, *, prefix: str = "item") -> str:
    if prefix in {"skill", "agent", "mcp"}:
        return safe_skill_id(value, prefix=prefix)
    text = re.sub(r"[^a-zA-Z0-9_-]+", "-", str(value or "").strip().lower()).strip("-")
    return text or f"{prefix}-{uuid.uuid4().hex[:8]}"


def _mcp_manifest_key(url: str, tool_prefix: str) -> str:
    if str(tool_prefix or "").strip():
        return str(tool_prefix).strip()
    if "/9009/" in str(url) or str(url).rstrip("/").endswith(":9009/mcp"):
        return "local"
    return _safe_identifier(url, prefix="mcp")


def _redact(value: Any) -> Any:
    if isinstance(value, dict):
        result: Dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key).lower()
            if any(token in key_text for token in SENSITIVE_KEYWORDS):
                result[key] = "[REDACTED]"
            else:
                result[key] = _redact(item)
        return result
    if isinstance(value, list):
        return [_redact(item) for item in value]
    return value


def _settings_dict() -> Dict[str, Any]:
    return agentSettings.model_dump(mode="json")


def _config_file_path() -> Optional[Path]:
    raw = os.getenv("APP_CONFIG_FILE") or os.getenv("CONFIG_FILE")
    if raw:
        return Path(raw).expanduser().resolve()
    return None


def _set_nested_value(payload: Dict[str, Any], field_path: str, value: Any) -> None:
    keys = [item for item in str(field_path or "").split(".") if item]
    if not keys:
        raise ValueError("field_path is required")
    cursor: Any = payload
    for key in keys[:-1]:
        if not isinstance(cursor, dict):
            raise ValueError(f"cannot set nested field through non-object: {key}")
        cursor = cursor.setdefault(key, {})
    if not isinstance(cursor, dict):
        raise ValueError("target parent is not an object")
    cursor[keys[-1]] = value


def _agent_yaml_path(agent_id: str) -> Path:
    safe_id = _safe_identifier(agent_id, prefix="agent")
    return default_agents_dir() / safe_id / "agent.yaml"


async def config_read(section: Optional[str] = None, include_sensitive: bool = False) -> Dict[str, Any]:
    data = _settings_dict()
    if section:
        current: Any = data
        for key in str(section).split("."):
            if not isinstance(current, dict) or key not in current:
                raise KeyError(f"config section not found: {section}")
            current = current[key]
        data = current if isinstance(current, dict) else {"value": current}
    return {
        "section": section or "",
        "config": data if include_sensitive and _enabled("APP_ALLOW_CONFIG_SECRET_READ") else _redact(data),
    }


async def config_update(
    target: str,
    field_path: str,
    value: Any,
    *,
    agent_id: Optional[str] = None,
) -> Dict[str, Any]:
    if not _enabled("APP_ALLOW_CONFIG_WRITE_TOOLS"):
        raise PermissionError("config write tools are disabled")
    normalized_target = str(target or "").strip().lower()
    if normalized_target == "agent":
        if not agent_id:
            raise ValueError("agent_id is required for target=agent")
        yaml_path = _agent_yaml_path(agent_id)
    elif normalized_target == "app":
        yaml_path = _config_file_path()
        if yaml_path is None:
            raise ValueError("APP_CONFIG_FILE is required for target=app")
    else:
        raise ValueError("target must be agent or app")

    payload = yaml.safe_load(yaml_path.read_text(encoding="utf-8")) if yaml_path.exists() else {}
    if not isinstance(payload, dict):
        raise ValueError(f"config file is not a mapping: {yaml_path}")
    _set_nested_value(payload, field_path, value)
    yaml_path.parent.mkdir(parents=True, exist_ok=True)
    yaml_path.write_text(yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return {"path": str(yaml_path), "target": normalized_target, "fieldPath": field_path, "updated": True}


async def mcp_manager_list_servers() -> Dict[str, Any]:
    servers = []
    for item in agentSettings.mcp.mcp_market.mcp_servers:
        servers.append(
            {
                "url": item.url,
                "transport": item.transport,
                "toolPrefix": item.tool_prefix,
                "authorizationConfigured": bool(reveal_secret(item.authorization)),
            }
        )
    return {"servers": servers, "count": len(servers)}


async def mcp_manager_write_manifest(
    *,
    output_path: str = "mcp.json",
    work_dir: Optional[str] = None,
) -> Dict[str, Any]:
    target = _resolve_path(output_path, work_dir=work_dir, require_workspace=True)
    manifest = {
        "mcpServers": {
            _mcp_manifest_key(item.url, item.tool_prefix): {
                "url": item.url,
                "transport": item.transport,
                "authorizationConfigured": bool(reveal_secret(item.authorization)),
            }
            for item in agentSettings.mcp.mcp_market.mcp_servers
        }
    }
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"path": str(target), "serverCount": len(manifest["mcpServers"])}


async def mcp_manager_add_server(
    url: str,
    tool_prefix: str,
    *,
    transport: str = "streamable-http",
    authorization: str = "",
) -> Dict[str, Any]:
    if not _enabled("APP_ALLOW_CONFIG_WRITE_TOOLS"):
        raise PermissionError("MCP config write tools are disabled")
    config_path = _config_file_path()
    if config_path is None:
        raise ValueError("APP_CONFIG_FILE is required to update MCP servers")
    payload = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    if not isinstance(payload, dict):
        raise ValueError(f"config file is not a mapping: {config_path}")
    mcp = payload.setdefault("mcp", {})
    market = mcp.setdefault("mcp_market", {})
    servers = market.setdefault("mcp_servers", [])
    if not isinstance(servers, list):
        raise ValueError("mcp.mcp_market.mcp_servers must be a list")
    servers.append(
        {
            "url": url,
            "transport": transport,
            "authorization": authorization,
            "tool_prefix": _safe_identifier(tool_prefix, prefix="mcp"),
        }
    )
    config_path.write_text(yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return {"path": str(config_path), "added": True, "toolPrefix": tool_prefix}


async def send_message(
    channel: str,
    message: str,
    *,
    title: str = "",
    webhook_url: Optional[str] = None,
) -> Dict[str, Any]:
    if not str(message or "").strip():
        raise ValueError("message is required")
    normalized = str(channel or "local_task").strip()
    if normalized == "local_task":
        return {"channel": normalized, "sent": True, "title": title, "message": message}
    if normalized != "webhook":
        raise ValueError("unsupported channel")
    target_url = webhook_url or os.getenv("TASKPILOT_WEBHOOK_URL") or ""
    if not target_url:
        raise ValueError("webhook_url or TASKPILOT_WEBHOOK_URL is required")
    async with aiohttp.ClientSession() as session:
        async with session.post(target_url, json={"title": title, "message": message}) as response:
            text = await response.text()
            return {"channel": normalized, "sent": response.status < 400, "status": response.status, "response": text[:1000]}


async def search_skills(
    query: str = "",
    *,
    limit: int = 20,
    work_dir: Optional[str] = None,
    include_disabled: bool = False,
    agent_id: Optional[str] = None,
) -> Dict[str, Any]:
    safe_limit = max(1, min(int(limit or 20), 100))
    items: List[Dict[str, Any]] = search_agent_skills(
        query,
        limit=safe_limit,
        include_disabled=include_disabled,
    )
    remaining = max(safe_limit - len(items), 0)
    if remaining:
        items.extend(
            TaskSkillRegistry(work_dir).list_local_skills(
                query=query,
                limit=remaining,
                include_disabled=include_disabled,
                agent_id=agent_id,
            )
        )
    return {"items": items, "count": len(items)}


async def load_skill(
    skill_id: str,
    *,
    work_dir: Optional[str] = None,
    include_disabled: bool = False,
) -> Dict[str, Any]:
    safe_id = _safe_identifier(skill_id, prefix="skill")
    try:
        return TaskSkillRegistry(work_dir).load(safe_id, include_disabled=include_disabled)
    except KeyError:
        pass

    registry = AgentRegistry()
    agent = registry.get(safe_id)
    if agent is None:
        raise KeyError(f"skill not found: {skill_id}")
    return {
        "id": agent.id,
        "type": "agent",
        "name": agent.name,
        "description": agent.description,
        "systemPrompt": agent.system_prompt,
        "tools": [tool.name for tool in agent.tools],
    }


async def install_skill(
    skill_id: str,
    content: str,
    *,
    work_dir: Optional[str] = None,
    enabled: bool = True,
    agent_ids: Optional[List[str]] = None,
) -> Dict[str, Any]:
    return TaskSkillRegistry(work_dir).install(
        skill_id,
        content,
        enabled=enabled,
        agent_ids=agent_ids,
    )


async def set_skill_enabled(skill_id: str, enabled: bool, *, work_dir: Optional[str] = None) -> Dict[str, Any]:
    return TaskSkillRegistry(work_dir).set_enabled(skill_id, enabled)


async def create_subagent(
    agent_id: str,
    name: str,
    description: str,
    system_prompt: str,
    *,
    tools: Optional[List[str]] = None,
    enable_in_registry: bool = False,
    work_dir: Optional[str] = None,
) -> Dict[str, Any]:
    safe_id = _safe_identifier(agent_id, prefix="agent")
    spec = {
        "id": safe_id,
        "name": name or safe_id,
        "type": "react_worker",
        "description": description or "",
        "version": "1",
        "enabled": True,
        "mode": "react",
        "system_prompt_file": "system_prompt.md",
        "capabilities": ["react", "use_tools"],
        "tools": [{"name": item} for item in (tools or [])],
        "permissions": {
            "can_write_files": False,
            "can_run_shell": False,
            "can_access_network": True,
            "require_approval_for": [],
        },
        "output": {"format": "markdown"},
    }

    if enable_in_registry:
        if not _enabled("APP_ALLOW_AGENT_CONFIG_WRITE"):
            raise PermissionError("agent registry writes are disabled")
        target_dir = default_agents_dir() / safe_id
        runtime_available = True
    else:
        target_dir = _workspace_root(work_dir) / "subagents" / safe_id
        runtime_available = False

    target_dir.mkdir(parents=True, exist_ok=True)
    (target_dir / "agent.yaml").write_text(
        yaml.safe_dump(spec, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    (target_dir / "system_prompt.md").write_text(system_prompt or "", encoding="utf-8")
    return {
        "agentId": safe_id,
        "path": str(target_dir),
        "created": True,
        "runtimeAvailableAfterReload": runtime_available,
    }


def _format_memory_items(items: List[Any]) -> List[Dict[str, Any]]:
    formatted = []
    for item in items:
        if isinstance(item, dict):
            formatted.append(item)
        else:
            formatted.append(
                {
                    "id": str(getattr(item, "id", "") or ""),
                    "content": str(getattr(item, "content", "") or getattr(item, "memory", "") or ""),
                    "metadata": getattr(item, "metadata", {}) or {},
                    "score": getattr(item, "score", None),
                }
            )
    return formatted


async def memory_search(
    query: str,
    *,
    user_id: Optional[str] = None,
    agent_id: Optional[str] = None,
    run_id: Optional[str] = None,
    limit: int = 10,
) -> Dict[str, Any]:
    from memory.memory_mgr import memory_manager

    items = memory_manager.search_memory(query, user_id=user_id, agent_id=agent_id, run_id=run_id, limit=limit)
    return {"items": _format_memory_items(items), "count": len(items), "degraded": memory_manager.get_degradation_status()}


async def memory_add(
    content: str,
    *,
    user_id: str,
    agent_id: str,
    run_id: Optional[str] = None,
) -> Dict[str, Any]:
    from memory.memory_mgr import memory_manager

    ids = memory_manager.add_memory({"role": "user", "content": content}, user_id=user_id, agent_id=agent_id, run_id=run_id)
    return {"memoryIds": ids, "count": len(ids), "degraded": memory_manager.get_degradation_status()}


async def memory_delete(memory_id: str) -> Dict[str, Any]:
    from memory.memory_mgr import memory_manager

    return {"memoryId": memory_id, "deleted": memory_manager.delete_memory(memory_id)}
