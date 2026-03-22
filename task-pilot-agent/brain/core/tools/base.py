from __future__ import annotations
from typing import Any, Dict
from dataclasses import dataclass


@dataclass
class BaseTool:
    name: str
    description: str

    def to_params(self) -> Dict[str, Any]:
        return {"type": "object", "properties": {}, "required": []}

    async def execute(self, input_obj: Dict[str, Any]) -> str | None:
        raise NotImplementedError

