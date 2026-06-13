from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

import yaml

from brain.core.tool_policy import (
    matches_any_tool_pattern as _shared_matches_any_tool_pattern,
    matches_tool_pattern as _shared_matches_tool_pattern,
    tool_name_variants as _shared_tool_name_variants,
)

SUPPORTED_AGENT_TYPES = {
    "supervisor",
    "react_worker",
    "summary_worker",
    "review_worker",
}


@dataclass(frozen=True)
class AgentToolSpec:
    name: str
    description: str = ""
    alias: str = ""
    purpose: str = ""
    when_to_use: str = ""
    required: bool = False
    timeout_seconds: Optional[int] = None
    input_schema: Dict[str, Any] = field(default_factory=dict)
    output_schema: Dict[str, Any] = field(default_factory=dict)
    policy: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AgentEvalCase:
    id: str
    name: str
    input: str
    expected: str = ""
    tags: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AgentSelection:
    supervisor_id: str
    agent_id: str
    reason: str
    score: int = 0
    matched_terms: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "supervisorId": self.supervisor_id,
            "agentId": self.agent_id,
            "reason": self.reason,
            "score": self.score,
            "matchedTerms": self.matched_terms,
        }


@dataclass(frozen=True)
class AgentConfig:
    id: str
    name: str
    type: str = "react_worker"
    description: str = ""
    version: str = "1"
    enabled: bool = True
    mode: str = "react"
    system_prompt: str = ""
    model: Dict[str, Any] = field(default_factory=dict)
    capabilities: List[str] = field(default_factory=list)
    tools: List[AgentToolSpec] = field(default_factory=list)
    denied_tools: List[str] = field(default_factory=list)
    handoffs: Dict[str, Any] = field(default_factory=dict)
    memory: Dict[str, Any] = field(default_factory=dict)
    permissions: Dict[str, Any] = field(default_factory=dict)
    output: Dict[str, Any] = field(default_factory=dict)
    evals: List[AgentEvalCase] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)
    directory: Optional[Path] = None

    def tool_patterns(self) -> List[str]:
        return [tool.name for tool in self.tools if tool.name]

    def allows_tool(self, tool_name: str, approved_tools: Optional[List[str]] = None) -> bool:
        return not self.tool_block_reason(tool_name, approved_tools=approved_tools)

    def tool_block_reason(self, tool_name: str, approved_tools: Optional[List[str]] = None) -> str:
        if any(_matches_tool_pattern(pattern, tool_name) for pattern in self.denied_tools):
            return "denied_tools"
        permission_reason = _tool_permission_block_reason(self.permissions, tool_name)
        if permission_reason:
            return permission_reason
        patterns = self.tool_patterns()
        if not patterns:
            return ""
        matched = [tool for tool in self.tools if _matches_tool_pattern(tool.name, tool_name)]
        if not matched:
            return "not_in_allowed_tools"
        for tool in matched:
            approval_reason = _tool_approval_block_reason(
                self.permissions,
                tool.policy,
                tool_name,
                approved_tools or [],
            )
            if approval_reason:
                return approval_reason
            policy_reason = _tool_policy_block_reason(tool.policy)
            if policy_reason:
                if policy_reason == "high_risk_requires_enable" and _matches_any_tool_pattern(tool_name, approved_tools or []):
                    continue
                return policy_reason
        return ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "type": self.type,
            "description": self.description,
            "version": self.version,
            "enabled": self.enabled,
            "mode": self.mode,
            "systemPrompt": self.system_prompt,
            "model": self.model,
            "capabilities": self.capabilities,
            "tools": [
                {
                    "name": tool.name,
                    "description": tool.description,
                    "allowed": self.allows_tool(tool.name),
                    "blockReason": self.tool_block_reason(tool.name),
                    "alias": tool.alias,
                    "purpose": tool.purpose,
                    "whenToUse": tool.when_to_use,
                    "required": tool.required,
                    "timeoutSeconds": tool.timeout_seconds,
                    "inputSchema": tool.input_schema,
                    "outputSchema": tool.output_schema,
                    "policy": tool.policy,
                }
                for tool in self.tools
            ],
            "deniedTools": self.denied_tools,
            "handoffs": self.handoffs,
            "memory": self.memory,
            "permissions": self.permissions,
            "output": self.output,
            "evals": [
                {
                    "id": item.id,
                    "name": item.name,
                    "input": item.input,
                    "expected": item.expected,
                    "tags": item.tags,
                    "metadata": item.metadata,
                }
                for item in self.evals
            ],
            "metadata": self.metadata,
            "directory": str(self.directory) if self.directory else None,
        }

    def to_runtime_snapshot(self, approved_tools: Optional[List[str]] = None) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "type": self.type,
            "description": self.description,
            "version": self.version,
            "mode": self.mode,
            "capabilities": list(self.capabilities),
            "tools": [
                {
                    "name": tool.name,
                    "description": tool.description,
                    "allowed": self.allows_tool(tool.name, approved_tools=approved_tools),
                    "blockReason": self.tool_block_reason(tool.name, approved_tools=approved_tools),
                    "policy": dict(tool.policy),
                }
                for tool in self.tools
            ],
            "handoffs": dict(self.handoffs),
            "output": dict(self.output),
        }


def _matches_tool_pattern(pattern: str, tool_name: str) -> bool:
    return _shared_matches_tool_pattern(tool_name, pattern)


def _tool_name_variants(value: str) -> List[str]:
    return _shared_tool_name_variants(value)


def _tool_policy_block_reason(policy: Dict[str, Any]) -> str:
    if not policy:
        return ""
    if policy.get("enabled") is False:
        return "tool_disabled"
    risk = str(policy.get("risk") or "").lower()
    if risk in {"high", "critical"} and not _high_risk_tools_enabled():
        return "high_risk_requires_enable"
    return ""


def _tool_approval_block_reason(
    permissions: Dict[str, Any],
    policy: Dict[str, Any],
    tool_name: str,
    approved_tools: List[str],
) -> str:
    approval_items = permissions.get("require_approval_for") if isinstance(permissions, dict) else None
    if not approval_items:
        return ""
    if isinstance(approval_items, str):
        required = {approval_items}
    elif isinstance(approval_items, list):
        required = {str(item) for item in approval_items}
    else:
        return ""

    risk = str((policy or {}).get("risk") or "").lower()
    requires_high_risk_approval = "high_risk_tools" in required and risk in {"high", "critical"}
    requires_explicit_approval = bool((policy or {}).get("requires_explicit_enable"))
    if (requires_high_risk_approval or requires_explicit_approval) and not _matches_any_tool_pattern(
        tool_name,
        approved_tools,
    ):
        return "high_risk_requires_approval"
    return ""


def _tool_permission_block_reason(permissions: Dict[str, Any], tool_name: str) -> str:
    if not permissions:
        return ""
    if permissions.get("can_run_shell") is False and _matches_any_tool_pattern(
        tool_name,
        ["*shell*", "*terminal*", "*command*"],
    ):
        return "permission_can_run_shell"
    if permissions.get("can_access_network") is False and _matches_any_tool_pattern(
        tool_name,
        ["*search*", "*browser*", "*web*", "*http*", "*weather*"],
    ):
        return "permission_can_access_network"
    if permissions.get("can_write_files") is False and _matches_any_tool_pattern(
        tool_name,
        [
            "*file_write*",
            "*write_file*",
            "*file_copy*",
            "*file_move*",
            "*file_delete*",
            "*directory_create*",
            "*artifact_write*",
            "*report*",
        ],
    ):
        return "permission_can_write_files"
    return ""


def _matches_any_tool_pattern(tool_name: str, patterns: List[str]) -> bool:
    return _shared_matches_any_tool_pattern(tool_name, patterns)


def _high_risk_tools_enabled() -> bool:
    value = os.getenv("APP_ALLOW_HIGH_RISK_TOOLS") or os.getenv("ALLOW_HIGH_RISK_TOOLS") or ""
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _optional_int(value: Any) -> Optional[int]:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        raise ValueError(f"Expected integer value, got: {value!r}") from None


def _optional_mapping(mapping: Dict[str, Any], *keys: str) -> Dict[str, Any]:
    for key in keys:
        if key not in mapping:
            continue
        value = mapping.get(key)
        if value in (None, ""):
            return {}
        if not isinstance(value, dict):
            raise ValueError(f"Expected mapping for {key}, got: {value!r}")
        return value
    return {}


def default_agents_dir() -> Path:
    explicit = os.getenv("APP_AGENT_CONFIG_DIR") or os.getenv("AGENT_CONFIG_DIR")
    if explicit:
        return Path(explicit).expanduser().resolve()

    config_file = os.getenv("APP_CONFIG_FILE")
    if config_file:
        return (Path(config_file).expanduser().resolve().parent / "agents").resolve()

    return (Path.cwd() / "config" / "agents").resolve()


class AgentRegistry:
    def __init__(self, root_dir: Optional[Path | str] = None) -> None:
        self.root_dir = Path(root_dir).expanduser().resolve() if root_dir else default_agents_dir()
        self._agents: Dict[str, AgentConfig] = {}
        self.reload()

    def reload(self) -> None:
        agents: Dict[str, AgentConfig] = {}
        if self.root_dir.exists():
            for agent_dir in sorted(path for path in self.root_dir.iterdir() if path.is_dir()):
                yaml_path = agent_dir / "agent.yaml"
                if not yaml_path.exists():
                    continue
                agent = self._load_agent(agent_dir, yaml_path)
                if agent.id in agents:
                    raise ValueError(f"Duplicate agent id: {agent.id}")
                agents[agent.id] = agent

        self._validate_handoffs(agents)
        self._agents = agents

    def diagnostics(self) -> Dict[str, Any]:
        return self.diagnostics_for(self.root_dir)

    @classmethod
    def diagnostics_for(cls, root_dir: Path | str) -> Dict[str, Any]:
        root = Path(root_dir).expanduser().resolve()
        diagnostics_by_id: Dict[str, Dict[str, Any]] = {}
        items: List[Dict[str, Any]] = []
        agents: Dict[str, AgentConfig] = {}

        if not root.exists():
            return {
                "rootDir": str(root),
                "status": "missing",
                "validCount": 0,
                "invalidCount": 0,
                "items": [],
            }

        loader = cls.__new__(cls)
        loader.root_dir = root
        loader._agents = {}

        for agent_dir in sorted(path for path in root.iterdir() if path.is_dir()):
            yaml_path = agent_dir / "agent.yaml"
            if not yaml_path.exists():
                continue
            agent_id = cls._best_effort_agent_id(agent_dir, yaml_path)
            item = {
                "directory": str(agent_dir),
                "agentId": agent_id,
                "status": "valid",
                "error": "",
                "errors": [],
            }
            try:
                agent = loader._load_agent(agent_dir, yaml_path)
                agent_id = agent.id
                item["agentId"] = agent_id
                if agent_id in agents:
                    raise ValueError(f"Duplicate agent id: {agent_id}")
                agents[agent_id] = agent
            except Exception as exc:
                item["status"] = "invalid"
                item["error"] = str(exc)
                item["errors"] = [str(exc)]
            diagnostics_by_id[agent_id] = item
            items.append(item)

        for agent_id, errors in cls._handoff_errors(agents).items():
            item = diagnostics_by_id.get(agent_id)
            if not item:
                continue
            item["status"] = "invalid"
            item["errors"] = list(item.get("errors") or []) + errors
            item["error"] = "; ".join(item["errors"])

        invalid_count = sum(1 for item in items if item["status"] == "invalid")
        return {
            "rootDir": str(root),
            "status": "invalid" if invalid_count else "ok",
            "validCount": len(items) - invalid_count,
            "invalidCount": invalid_count,
            "items": items,
        }

    def list_agents(self, *, include_disabled: bool = False) -> List[AgentConfig]:
        agents = list(self._agents.values())
        if not include_disabled:
            agents = [agent for agent in agents if agent.enabled]
        return sorted(agents, key=lambda agent: agent.id)

    def get(self, agent_id: Optional[str]) -> Optional[AgentConfig]:
        if not agent_id:
            return None
        agent = self._agents.get(agent_id)
        if agent and agent.enabled:
            return agent
        return None

    def filter_tool_names(self, agent_id: Optional[str], tool_names: Iterable[str]) -> List[str]:
        agent = self.get(agent_id)
        if agent is None:
            return list(tool_names)
        return [tool_name for tool_name in tool_names if agent.allows_tool(tool_name)]

    def list_evals(self, agent_id: Optional[str] = None) -> List[AgentEvalCase]:
        if agent_id:
            agent = self.get(agent_id)
            return list(agent.evals) if agent else []
        evals: List[AgentEvalCase] = []
        for agent in self.list_agents():
            evals.extend(agent.evals)
        return evals

    def select_agent_for_task(self, supervisor_id: str, task_text: str) -> Optional[AgentSelection]:
        supervisor = self.get(supervisor_id)
        if supervisor is None:
            return None
        candidates = self._selectable_agents_for(supervisor)
        if not candidates:
            return None

        scored: List[tuple[int, int, AgentConfig, List[str]]] = []
        for index, candidate in enumerate(candidates):
            score, matched_terms = _score_agent_for_task(candidate, task_text)
            scored.append((score, index, candidate, matched_terms))

        score, _index, selected, matched_terms = max(scored, key=lambda item: (item[0], -item[1]))
        if score > 0:
            reason = f"matched task terms: {', '.join(matched_terms[:5])}"
        else:
            reason = "fallback to first allowed handoff agent"
        return AgentSelection(
            supervisor_id=supervisor.id,
            agent_id=selected.id,
            reason=reason,
            score=score,
            matched_terms=matched_terms[:10],
        )

    def _selectable_agents_for(self, supervisor: AgentConfig) -> List[AgentConfig]:
        allowed = supervisor.handoffs.get("allowed") if isinstance(supervisor.handoffs, dict) else []
        if not isinstance(allowed, list):
            return []
        candidates: List[AgentConfig] = []
        for target in allowed:
            agent = self.get(str(target).strip())
            if agent and agent.id != supervisor.id and agent.type != "supervisor":
                candidates.append(agent)
        return candidates

    def _load_agent(self, agent_dir: Path, yaml_path: Path) -> AgentConfig:
        raw = yaml.safe_load(yaml_path.read_text(encoding="utf-8")) or {}
        if not isinstance(raw, dict):
            raise ValueError(f"Agent config must be a mapping: {yaml_path}")

        agent_id = str(raw.get("id") or agent_dir.name).strip()
        if not agent_id:
            raise ValueError(f"Agent id is required: {yaml_path}")
        if agent_id != agent_dir.name:
            raise ValueError(f"Agent id must match directory name: {yaml_path}")
        agent_type = str(raw.get("type") or raw.get("agent_type") or "react_worker")
        if agent_type not in SUPPORTED_AGENT_TYPES:
            raise ValueError(f"Unsupported agent type `{agent_type}`: {yaml_path}")

        system_prompt = str(raw.get("system_prompt") or "")
        prompt_file = raw.get("system_prompt_file")
        if prompt_file:
            system_prompt = self._read_prompt_file(agent_dir, str(prompt_file))

        tools = self._parse_tools(raw.get("tools") or [])
        evals = self._load_evals(agent_dir)
        return AgentConfig(
            id=agent_id,
            name=str(raw.get("name") or agent_id),
            type=agent_type,
            description=str(raw.get("description") or ""),
            version=str(raw.get("version") or "1"),
            enabled=bool(raw.get("enabled", True)),
            mode=str(raw.get("mode") or "react"),
            system_prompt=system_prompt,
            model=raw.get("model") if isinstance(raw.get("model"), dict) else {},
            capabilities=(
                [str(item) for item in raw.get("capabilities", [])]
                if isinstance(raw.get("capabilities"), list)
                else []
            ),
            tools=tools,
            denied_tools=self._parse_denied_tools(
                raw.get("tools") or [],
                raw.get("denied_tools", raw.get("deniedTools")),
            ),
            handoffs=raw.get("handoffs") if isinstance(raw.get("handoffs"), dict) else {},
            memory=raw.get("memory") if isinstance(raw.get("memory"), dict) else {},
            permissions=raw.get("permissions") if isinstance(raw.get("permissions"), dict) else {},
            output=raw.get("output") if isinstance(raw.get("output"), dict) else {},
            evals=evals,
            metadata=raw.get("metadata") if isinstance(raw.get("metadata"), dict) else {},
            directory=agent_dir,
        )

    @staticmethod
    def _parse_tools(raw_tools: Any) -> List[AgentToolSpec]:
        if isinstance(raw_tools, dict):
            raw_tools = raw_tools.get("allowed") or []
        if not isinstance(raw_tools, list):
            raise ValueError("Agent tools must be a list")

        tools: List[AgentToolSpec] = []
        for item in raw_tools:
            if isinstance(item, str):
                tools.append(AgentToolSpec(name=item))
                continue
            if not isinstance(item, dict):
                raise ValueError(f"Invalid tool spec: {item!r}")
            name = str(item.get("name") or item.get("id") or "").strip()
            if not name:
                raise ValueError(f"Tool name is required: {item!r}")
            policy = item.get("policy") if isinstance(item.get("policy"), dict) else {}
            risk_level = item.get("risk_level")
            if risk_level and "risk" not in policy:
                policy = {**policy, "risk": str(risk_level)}
            tools.append(
                AgentToolSpec(
                    name=name,
                    description=str(item.get("description") or ""),
                    alias=str(item.get("alias") or ""),
                    purpose=str(item.get("purpose") or ""),
                    when_to_use=str(item.get("when_to_use") or ""),
                    required=bool(item.get("required", False)),
                    timeout_seconds=_optional_int(item.get("timeout_seconds")),
                    input_schema=_optional_mapping(item, "input_schema", "inputSchema"),
                    output_schema=_optional_mapping(item, "output_schema", "outputSchema"),
                    policy=policy,
                )
            )
        return tools

    @staticmethod
    def _parse_denied_tools(raw_tools: Any, raw_top_level_denied: Any = None) -> List[str]:
        if isinstance(raw_tools, dict):
            raw_denied = raw_tools.get("denied") or []
        else:
            raw_denied = []
        denied_items: List[Any] = []
        for value in (raw_denied, raw_top_level_denied):
            if value in (None, ""):
                continue
            if isinstance(value, str):
                denied_items.append(value)
                continue
            if not isinstance(value, list):
                raise ValueError("Agent denied tools must be a list")
            denied_items.extend(value)
        denied: List[str] = []
        for item in denied_items:
            if isinstance(item, str):
                value = item.strip()
            elif isinstance(item, dict):
                value = str(item.get("name") or item.get("id") or "").strip()
            else:
                raise ValueError(f"Invalid denied tool spec: {item!r}")
            if value:
                denied.append(value)
        return denied

    @staticmethod
    def _read_prompt_file(agent_dir: Path, prompt_file: str) -> str:
        agent_root = agent_dir.resolve()
        prompt_path = (agent_dir / prompt_file).resolve()
        if not prompt_path.is_relative_to(agent_root):
            raise ValueError(f"Agent prompt file must stay inside its directory: {prompt_file}")
        return prompt_path.read_text(encoding="utf-8")

    @staticmethod
    def _validate_handoffs(agents: Dict[str, AgentConfig]) -> None:
        errors_by_agent = AgentRegistry._handoff_errors(agents)
        for agent_id, errors in errors_by_agent.items():
            if errors:
                raise ValueError(errors[0])

    @staticmethod
    def _handoff_errors(agents: Dict[str, AgentConfig]) -> Dict[str, List[str]]:
        errors: Dict[str, List[str]] = {}
        for agent in agents.values():
            allowed = agent.handoffs.get("allowed") if isinstance(agent.handoffs, dict) else None
            if not allowed:
                continue
            if not isinstance(allowed, list):
                errors.setdefault(agent.id, []).append(f"Agent handoffs.allowed must be a list: {agent.id}")
                continue
            for target in allowed:
                target_id = str(target).strip()
                if target_id and target_id not in agents:
                    errors.setdefault(agent.id, []).append(
                        f"Agent handoff target not found: {agent.id} -> {target_id}"
                    )
        return errors

    @staticmethod
    def _best_effort_agent_id(agent_dir: Path, yaml_path: Path) -> str:
        try:
            raw = yaml.safe_load(yaml_path.read_text(encoding="utf-8")) or {}
            if isinstance(raw, dict) and raw.get("id"):
                return str(raw.get("id")).strip() or agent_dir.name
        except Exception:
            pass
        return agent_dir.name

    @classmethod
    def _load_evals(cls, agent_dir: Path) -> List[AgentEvalCase]:
        evals_path = agent_dir / "evals.yaml"
        if not evals_path.exists():
            return []
        raw = yaml.safe_load(evals_path.read_text(encoding="utf-8")) or {}
        if isinstance(raw, dict):
            raw_cases = raw.get("cases") or []
        elif isinstance(raw, list):
            raw_cases = raw
        else:
            raise ValueError(f"Agent evals must be a mapping or list: {evals_path}")
        if not isinstance(raw_cases, list):
            raise ValueError(f"Agent eval cases must be a list: {evals_path}")

        cases: List[AgentEvalCase] = []
        for index, item in enumerate(raw_cases, start=1):
            if not isinstance(item, dict):
                raise ValueError(f"Invalid eval case in {evals_path}: {item!r}")
            case_id = str(item.get("id") or f"case_{index}").strip()
            input_text = str(item.get("input") or "").strip()
            if not input_text:
                raise ValueError(f"Eval case input is required: {evals_path}:{case_id}")
            raw_tags = item.get("tags") or []
            tags = [str(tag) for tag in raw_tags] if isinstance(raw_tags, list) else [str(raw_tags)]
            metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
            cases.append(
                AgentEvalCase(
                    id=case_id,
                    name=str(item.get("name") or case_id),
                    input=input_text,
                    expected=str(item.get("expected") or ""),
                    tags=tags,
                    metadata=metadata,
                )
            )
        return cases


TOKEN_RE = re.compile(r"[A-Za-z0-9_:-]{2,}|[\u4e00-\u9fff]{2,}")


def _score_agent_for_task(agent: AgentConfig, task_text: str) -> tuple[int, List[str]]:
    normalized_task = task_text.lower()
    haystack = _agent_search_text(agent).lower()
    terms = _task_terms(normalized_task)
    matched: List[str] = []
    score = 0

    for term in terms:
        if term in haystack:
            matched.append(term)
            score += 2

    for keyword in _agent_keywords(agent):
        if keyword in normalized_task and keyword not in matched:
            matched.append(keyword)
            score += 3

    return score, matched


def _task_terms(task_text: str) -> List[str]:
    seen: set[str] = set()
    terms: List[str] = []
    for match in TOKEN_RE.findall(task_text):
        value = match.strip().lower()
        if len(value) < 2 or value in seen:
            continue
        seen.add(value)
        terms.append(value)
    return terms


def _agent_search_text(agent: AgentConfig) -> str:
    parts: List[str] = [
        agent.id,
        agent.name,
        agent.description,
        agent.type,
        " ".join(agent.capabilities),
    ]
    for tool in agent.tools:
        parts.extend([tool.name, tool.description, tool.alias, tool.purpose, tool.when_to_use])
    for value in agent.metadata.values():
        parts.extend(_metadata_text_values(value))
    return " ".join(part for part in parts if part)


def _agent_keywords(agent: AgentConfig) -> List[str]:
    raw = [agent.id, agent.name, agent.description, *agent.capabilities]
    for tool in agent.tools:
        raw.extend([tool.name, tool.description, tool.alias, tool.purpose, tool.when_to_use])
    for value in agent.metadata.values():
        raw.extend(_metadata_text_values(value))

    keywords: List[str] = []
    seen: set[str] = set()
    for item in raw:
        for token in TOKEN_RE.findall(str(item).lower()):
            if len(token) < 2 or token in seen:
                continue
            seen.add(token)
            keywords.append(token)
    return keywords


def _metadata_text_values(value: Any) -> List[str]:
    if isinstance(value, (str, int, float, bool)):
        return [str(value)]
    if isinstance(value, list):
        items: List[str] = []
        for item in value:
            items.extend(_metadata_text_values(item))
        return items
    if isinstance(value, dict):
        items: List[str] = []
        for item in value.values():
            items.extend(_metadata_text_values(item))
        return items
    return []
