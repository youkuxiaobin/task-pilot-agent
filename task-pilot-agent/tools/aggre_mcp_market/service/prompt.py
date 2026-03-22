from __future__ import annotations

from typing import Iterable

from tools.aggre_mcp_market.models import ToolInfo


def assemble_prompt(tools: Iterable[ToolInfo]) -> str:
    """Assemble a concise prompt snippet for LLM tool selection.

    Format example:
    - tool full name
      - description
      - params: JSON schema properties summary
    """
    lines = []
    lines.append("Available Tools:")
    for t in tools:
        lines.append(f"- {t.full_name}")
        if t.description:
            lines.append(f"  desc: {t.description}")
        # Attempt to summarize parameters from JSON schema
        props = []
        if t.input_schema and isinstance(t.input_schema, dict):
            schema = t.input_schema
            properties = schema.get("properties") or {}
            required = set(schema.get("required") or [])
            for name, spec in properties.items():
                typ = spec.get("type") if isinstance(spec, dict) else None
                req = "required" if name in required else "optional"
                props.append(f"{name}:{typ or 'any'}({req})")
        if props:
            lines.append("  params: " + ", ".join(props))
        # Output schema hint if available
        if getattr(t, "output_schema", None) and isinstance(t.output_schema, dict):
            out_props = []
            properties = t.output_schema.get("properties") or {}
            required = set(t.output_schema.get("required") or [])
            for name, spec in properties.items():
                typ = spec.get("type") if isinstance(spec, dict) else None
                req = "required" if name in required else "optional"
                out_props.append(f"{name}:{typ or 'any'}({req})")
            if out_props:
                lines.append("  returns: " + ", ".join(out_props))
        # Transport hint for debugging
        lines.append(f"  via: {t.protocol} @ {t.server_url}")
    return "\n".join(lines)
