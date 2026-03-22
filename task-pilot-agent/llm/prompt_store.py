from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from llm.types import LLMMessage

LANGUAGE_ALIASES: Dict[str, set[str]] = {
    "ch": {"ch", "zh", "zh-cn", "zh_cn", "cn", "chinese"},
    "en": {"en", "en-us", "en_us", "english"},
}
DEFAULT_LANGUAGE = "ch"


class PromptStore:
    """
    Loads prompt templates from a YAML/JSON mapping and renders to messages.

    Schema options per key:
      key: "你好 {name}"
      key:
        template: "你好 {name}"
        role: "user"  # default "user"
      key:
        messages:
          - role: system
            content: "你是一个助手，语气简洁。"
          - role: user
            content: "请总结：{text}"
      key:
        ch: "..."
        en: "..."
    """

    def __init__(self, data: Dict[str, Any], language: str = DEFAULT_LANGUAGE) -> None:
        self.data = data or {}
        self.language = self._normalize_language(language)

    @classmethod
    def from_file(cls, path: str, language: str = DEFAULT_LANGUAGE) -> "PromptStore":
        if path.endswith(".json"):
            import json

            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return cls(data, language=language)
        # default YAML
        try:
            import yaml  # type: ignore
        except Exception as e:  # pragma: no cover
            raise RuntimeError(
                "PyYAML not installed. Install pyyaml or use a JSON prompt file."
            ) from e
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        if not isinstance(data, dict):
            raise ValueError("Prompt file root must be a mapping of keys -> templates")
            
        normalized_lang = cls._normalize_language(language)
        if normalized_lang and normalized_lang != DEFAULT_LANGUAGE:
            overlay_path = Path(path)
            overlay_file = overlay_path.with_name(
                f"{overlay_path.stem}_{normalized_lang}{overlay_path.suffix}"
            )
            if overlay_file.exists():
                with overlay_file.open("r", encoding="utf-8") as f:
                    overlay_data = yaml.safe_load(f) or {}
                if isinstance(overlay_data, dict):
                    data.update(overlay_data)
        return cls(data, language=language)

    def set_language(self, language: str) -> None:
        self.language = self._normalize_language(language)

    def has(self, key: str) -> bool:
        return key in self.data

    def get_prompt(self, key: str) -> Any:
        if key not in self.data:
            raise KeyError(f"Prompt key not found: {key}")
        return self._resolve_entry(self.data[key])

    def render_messages(self, key: str, variables: Dict[str, object]) -> List[LLMMessage]:
        if key not in self.data:
            raise KeyError(f"Prompt key not found: {key}")
        entry = self._resolve_entry(self.data[key])

        # Case 1: simple string
        if isinstance(entry, str):
            content = self._format(entry, variables)
            return [LLMMessage(role="user", content=content)]

        if not isinstance(entry, dict):
            raise ValueError(
                f"Unsupported prompt entry type for key '{key}': {type(entry)}"
            )

        # Case 2: single template + role
        if "template" in entry and "messages" not in entry:
            role = entry.get("role", "user")
            content = self._format(entry["template"], variables)
            return [LLMMessage(role=role, content=content)]

        # Case 3: multi-message conversation
        if "messages" in entry:
            msgs: List[LLMMessage] = []
            raw_msgs = entry["messages"] or []
            if not isinstance(raw_msgs, list):
                raise ValueError(f"'messages' for key '{key}' must be a list")
            for i, m in enumerate(raw_msgs):
                if not isinstance(m, dict) or "role" not in m or "content" not in m:
                    raise ValueError(
                        f"Invalid message at index {i} for key '{key}'"
                    )
                role = m["role"]
                content_tpl = m["content"]
                msgs.append(
                    LLMMessage(role=role, content=self._format(content_tpl, variables))
                )
            return msgs

        raise ValueError(f"Unrecognized prompt entry structure for key '{key}'")

    @staticmethod
    def _format(template: str, variables: Dict[str, object]) -> str:
        try:
            return template.format(**variables)
        except KeyError as e:
            missing = str(e).strip("'")
            raise KeyError(f"Missing variable '{missing}' for prompt key rendering")

    @staticmethod
    def _normalize_language(language: str) -> str:
        code = (language or DEFAULT_LANGUAGE).lower()
        for canonical, aliases in LANGUAGE_ALIASES.items():
            if code == canonical or code in aliases:
                return canonical
        return code

    def _resolve_entry(self, entry: Any) -> Any:
        if not isinstance(entry, dict):
            return entry

        lang_map: Dict[str, Any] = {}
        default_value = entry.get("default") if isinstance(entry, dict) else None
        for raw_key, value in entry.items():
            if not isinstance(raw_key, str):
                continue
            normalized = self._normalize_language(raw_key)
            if normalized in LANGUAGE_ALIASES:
                lang_map[normalized] = value

        if not lang_map:
            return entry

        preferred = self.language
        if preferred in lang_map:
            return lang_map[preferred]
        if default_value is not None:
            return default_value
        return next(iter(lang_map.values()))
