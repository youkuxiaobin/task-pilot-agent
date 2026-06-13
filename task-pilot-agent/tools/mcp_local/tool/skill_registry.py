from __future__ import annotations

import json
import re
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from brain.core.agent_registry import AgentRegistry
from tools.mcp_local.tool.filesystem import _workspace_root


MAX_SKILL_INSTALL_CHARS = 20_000
MAX_SKILL_LOAD_CHARS = 12_000
SKILL_MANIFEST_NAME = ".taskpilot_skills.json"


def safe_skill_id(value: str, *, prefix: str = "skill") -> str:
    text = re.sub(r"[^a-zA-Z0-9_-]+", "-", str(value or "").strip().lower()).strip("-")
    return text or f"{prefix}-{uuid.uuid4().hex[:8]}"


def _now_ms() -> int:
    return int(time.time() * 1000)


def _skill_summary(text: str) -> str:
    for line in str(text or "").splitlines():
        clean = line.strip().lstrip("#").strip()
        if clean:
            return clean[:300]
    return ""


class TaskSkillRegistry:
    def __init__(self, work_dir: Optional[str] = None) -> None:
        self.root = _workspace_root(work_dir)
        self.skills_root = self.root / "skills"
        self.manifest_path = self.skills_root / SKILL_MANIFEST_NAME

    def _read_manifest(self) -> Dict[str, Any]:
        if not self.manifest_path.exists():
            return {"version": 1, "skills": {}}
        try:
            data = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            data = {}
        if not isinstance(data, dict):
            data = {}
        skills = data.get("skills")
        if not isinstance(skills, dict):
            skills = {}
        return {"version": int(data.get("version") or 1), "skills": skills}

    def _write_manifest(self, manifest: Dict[str, Any]) -> None:
        self.skills_root.mkdir(parents=True, exist_ok=True)
        self.manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=True, indent=2, sort_keys=True),
            encoding="utf-8",
        )

    def _skill_path(self, skill_id: str) -> Path:
        return self.skills_root / safe_skill_id(skill_id) / "SKILL.md"

    def _metadata_for_file(self, skill_file: Path, manifest: Dict[str, Any]) -> Dict[str, Any]:
        skill_id = skill_file.parent.name
        manifest_skills = manifest.get("skills") if isinstance(manifest.get("skills"), dict) else {}
        stored = manifest_skills.get(skill_id) if isinstance(manifest_skills, dict) else {}
        if not isinstance(stored, dict):
            stored = {}
        text = skill_file.read_text(encoding="utf-8", errors="replace")
        return {
            "id": skill_id,
            "type": "task_local_skill",
            "path": str(skill_file),
            "enabled": bool(stored.get("enabled", True)),
            "description": str(stored.get("description") or _skill_summary(text)),
            "contentChars": len(text),
            "maxLoadChars": MAX_SKILL_LOAD_CHARS,
            "agentIds": [str(item) for item in stored.get("agentIds", []) if str(item).strip()]
            if isinstance(stored.get("agentIds"), list)
            else [],
            "installedAt": stored.get("installedAt"),
            "updatedAt": stored.get("updatedAt"),
            "lastLoadedAt": stored.get("lastLoadedAt"),
            "loadCount": int(stored.get("loadCount") or 0),
        }

    def list_local_skills(
        self,
        *,
        query: str = "",
        limit: int = 20,
        include_disabled: bool = False,
        agent_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        needle = str(query or "").strip().lower()
        safe_limit = max(1, min(int(limit or 20), 100))
        if not self.skills_root.exists():
            return []

        manifest = self._read_manifest()
        items: List[Dict[str, Any]] = []
        for skill_file in sorted(self.skills_root.glob("*/SKILL.md")):
            if len(items) >= safe_limit:
                break
            item = self._metadata_for_file(skill_file, manifest)
            if not include_disabled and not item["enabled"]:
                continue
            if agent_id and item["agentIds"] and str(agent_id) not in item["agentIds"]:
                continue
            haystack = f"{item['id']} {item['description']}".lower()
            if needle and needle not in haystack:
                continue
            items.append(item)
        return items

    def install(
        self,
        skill_id: str,
        content: str,
        *,
        enabled: bool = True,
        agent_ids: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        if not str(content or "").strip():
            raise ValueError("content is required")
        if len(content) > MAX_SKILL_INSTALL_CHARS:
            raise ValueError(f"skill content must be at most {MAX_SKILL_INSTALL_CHARS} characters")

        safe_id = safe_skill_id(skill_id)
        target = self._skill_path(safe_id)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")

        manifest = self._read_manifest()
        skills = manifest.setdefault("skills", {})
        existing = skills.get(safe_id) if isinstance(skills.get(safe_id), dict) else {}
        now = _now_ms()
        skills[safe_id] = {
            **existing,
            "enabled": bool(enabled),
            "description": _skill_summary(content),
            "agentIds": [str(item) for item in (agent_ids or []) if str(item).strip()],
            "installedAt": existing.get("installedAt") or now,
            "updatedAt": now,
            "lastLoadedAt": existing.get("lastLoadedAt"),
            "loadCount": int(existing.get("loadCount") or 0),
        }
        self._write_manifest(manifest)
        return {
            "id": safe_id,
            "path": str(target),
            "installed": True,
            "enabled": bool(enabled),
            "scope": "task_workspace",
            "contentChars": len(content),
            "maxInstallChars": MAX_SKILL_INSTALL_CHARS,
        }

    def set_enabled(self, skill_id: str, enabled: bool) -> Dict[str, Any]:
        safe_id = safe_skill_id(skill_id)
        target = self._skill_path(safe_id)
        if not target.exists():
            raise KeyError(f"skill not found: {skill_id}")
        manifest = self._read_manifest()
        skills = manifest.setdefault("skills", {})
        existing = skills.get(safe_id) if isinstance(skills.get(safe_id), dict) else {}
        skills[safe_id] = {
            **existing,
            "enabled": bool(enabled),
            "updatedAt": _now_ms(),
        }
        self._write_manifest(manifest)
        return {"id": safe_id, "enabled": bool(enabled), "path": str(target)}

    def load(self, skill_id: str, *, include_disabled: bool = False) -> Dict[str, Any]:
        safe_id = safe_skill_id(skill_id)
        target = self._skill_path(safe_id)
        if not target.exists():
            raise KeyError(f"skill not found: {skill_id}")

        manifest = self._read_manifest()
        metadata = self._metadata_for_file(target, manifest)
        if not metadata["enabled"] and not include_disabled:
            raise PermissionError(f"skill is disabled: {skill_id}")

        content = target.read_text(encoding="utf-8", errors="replace")
        truncated = len(content) > MAX_SKILL_LOAD_CHARS
        if truncated:
            content = content[:MAX_SKILL_LOAD_CHARS]

        skills = manifest.setdefault("skills", {})
        stored = skills.get(safe_id) if isinstance(skills.get(safe_id), dict) else {}
        now = _now_ms()
        skills[safe_id] = {
            **stored,
            "enabled": metadata["enabled"],
            "description": metadata["description"],
            "lastLoadedAt": now,
            "loadCount": int(stored.get("loadCount") or 0) + 1,
        }
        self._write_manifest(manifest)

        return {
            **metadata,
            "lastLoadedAt": now,
            "loadCount": int(stored.get("loadCount") or 0) + 1,
            "content": content,
            "contentTruncated": truncated,
        }


def search_agent_skills(query: str = "", *, limit: int = 20, include_disabled: bool = False) -> List[Dict[str, Any]]:
    needle = str(query or "").strip().lower()
    safe_limit = max(1, min(int(limit or 20), 100))
    registry = AgentRegistry()
    items: List[Dict[str, Any]] = []
    for agent in registry.list_agents(include_disabled=include_disabled):
        haystack = f"{agent.id} {agent.name} {agent.description} {' '.join(agent.capabilities)}".lower()
        if needle and needle not in haystack:
            continue
        items.append(
            {
                "id": agent.id,
                "type": "agent",
                "name": agent.name,
                "description": agent.description,
                "capabilities": agent.capabilities,
                "enabled": bool(agent.enabled),
            }
        )
        if len(items) >= safe_limit:
            break
    return items
