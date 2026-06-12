from __future__ import annotations

import base64
import os
import uuid
from pathlib import Path
from typing import Any, Dict, Optional

from openai import AsyncOpenAI

from config.config import agentSettings, reveal_secret
from tools.mcp_local.tool.filesystem import _resolve_path, _workspace_root


def _image_client() -> AsyncOpenAI:
    config = agentSettings.image_llm.config
    return AsyncOpenAI(
        api_key=reveal_secret(config.api_key),
        base_url=config.site_url,
    )


async def text_to_image(
    prompt: str,
    *,
    size: str = "1024x1024",
    output_path: Optional[str] = None,
    model: Optional[str] = None,
    work_dir: Optional[str] = None,
) -> Dict[str, Any]:
    if not str(prompt or "").strip():
        raise ValueError("prompt is required")

    resolved_model = model or os.getenv("TASKPILOT_IMAGE_GENERATION_MODEL") or agentSettings.image_llm.config.model
    client = _image_client()
    response = await client.images.generate(
        model=resolved_model,
        prompt=prompt,
        size=size,
        response_format="b64_json",
        n=1,
    )
    image = response.data[0] if response.data else None
    if image is None:
        raise RuntimeError("image generation returned no data")

    if getattr(image, "b64_json", None):
        if output_path:
            target = _resolve_path(output_path, work_dir=work_dir, require_workspace=True)
        else:
            root = _workspace_root(work_dir)
            target = root / "generated_images" / f"{uuid.uuid4().hex}.png"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(base64.b64decode(image.b64_json))
        return {
            "model": resolved_model,
            "prompt": prompt,
            "size": size,
            "path": str(target),
            "url": "",
        }

    url = str(getattr(image, "url", "") or "")
    if not url:
        raise RuntimeError("image generation returned neither b64_json nor url")
    return {
        "model": resolved_model,
        "prompt": prompt,
        "size": size,
        "path": "",
        "url": url,
    }
