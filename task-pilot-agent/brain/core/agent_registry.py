from __future__ import annotations

import fnmatch
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

import yaml


@dataclass(frozen=True)
class AgentToolSpec:
    name: str
    description: str = ""
    required: bool = False
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
class AgentConfig:
    id: str
    name: str
    description: str = ""
    version: str = "1"
    enabled: bool = True
    mode: str = "react"
    system_prompt: str = ""
    tools: List[AgentToolSpec] = field(default_factory=list)
    evals: List[AgentEvalCase] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)
    directory: Optional[Path] = None

    def tool_patterns(self) -> List[str]:
        return [tool.name for tool in self.tools if tool.name]

    def allows_tool(self, tool_name: str) -> bool:
        patterns = self.tool_patterns()
        if not patterns:
            return True
        matched = [tool for tool in self.tools if _matches_tool_pattern(tool.name, tool_name)]
        if not matched:
            return False
        for tool in matched:
            if _tool_policy_blocks(tool.policy):
                return False
        return True

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "version": self.version,
            "enabled": self.enabled,
            "mode": self.mode,
            "systemPrompt": self.system_prompt,
            "tools": [
                {
                    "name": tool.name,
                    "description": tool.description,
                    "required": tool.required,
                    "policy": tool.policy,
                }
                for tool in self.tools
            ],
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


def _matches_tool_pattern(pattern: str, tool_name: str) -> bool:
    if pattern in {"*", "all"}:
        return True
    return fnmatch.fnmatch(tool_name, pattern)


def _tool_policy_blocks(policy: Dict[str, Any]) -> bool:
    if not policy:
        return False
    if policy.get("enabled") is False:
        return True
    risk = str(policy.get("risk") or "").lower()
    if risk in {"high", "critical"} and not _high_risk_tools_enabled():
        return True
    return False


def _high_risk_tools_enabled() -> bool:
    value = os.getenv("APP_ALLOW_HIGH_RISK_TOOLS") or os.getenv("ALLOW_HIGH_RISK_TOOLS") or ""
    return value.strip().lower() in {"1", "true", "yes", "on"}


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

        self._agents = agents

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

    def _load_agent(self, agent_dir: Path, yaml_path: Path) -> AgentConfig:
        raw = yaml.safe_load(yaml_path.read_text(encoding="utf-8")) or {}
        if not isinstance(raw, dict):
            raise ValueError(f"Agent config must be a mapping: {yaml_path}")

        agent_id = str(raw.get("id") or agent_dir.name).strip()
        if not agent_id:
            raise ValueError(f"Agent id is required: {yaml_path}")

        system_prompt = str(raw.get("system_prompt") or "")
        prompt_file = raw.get("system_prompt_file")
        if prompt_file:
            system_prompt = self._read_prompt_file(agent_dir, str(prompt_file))

        tools = self._parse_tools(raw.get("tools") or [])
        evals = self._load_evals(agent_dir)
        return AgentConfig(
            id=agent_id,
            name=str(raw.get("name") or agent_id),
            description=str(raw.get("description") or ""),
            version=str(raw.get("version") or "1"),
            enabled=bool(raw.get("enabled", True)),
            mode=str(raw.get("mode") or "react"),
            system_prompt=system_prompt,
            tools=tools,
            evals=evals,
            metadata=raw.get("metadata") if isinstance(raw.get("metadata"), dict) else {},
            directory=agent_dir,
        )

    @staticmethod
    def _parse_tools(raw_tools: Any) -> List[AgentToolSpec]:
        if not isinstance(raw_tools, list):
            raise ValueError("Agent tools must be a list")

        tools: List[AgentToolSpec] = []
        for item in raw_tools:
            if isinstance(item, str):
                tools.append(AgentToolSpec(name=item))
                continue
            if not isinstance(item, dict):
                raise ValueError(f"Invalid tool spec: {item!r}")
            name = str(item.get("name") or "").strip()
            if not name:
                raise ValueError(f"Tool name is required: {item!r}")
            policy = item.get("policy") if isinstance(item.get("policy"), dict) else {}
            tools.append(
                AgentToolSpec(
                    name=name,
                    description=str(item.get("description") or ""),
                    required=bool(item.get("required", False)),
                    policy=policy,
                )
            )
        return tools

    @staticmethod
    def _read_prompt_file(agent_dir: Path, prompt_file: str) -> str:
        agent_root = agent_dir.resolve()
        prompt_path = (agent_dir / prompt_file).resolve()
        if not prompt_path.is_relative_to(agent_root):
            raise ValueError(f"Agent prompt file must stay inside its directory: {prompt_file}")
        return prompt_path.read_text(encoding="utf-8")

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
