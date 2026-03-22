import json
import os
import time
from json_repair import repair_json

from config.config import agentSettings
from tools.mcp_local.util.prompt_util import get_prompt
from tools.mcp_local.util.log_util import timer
from llm.manager import mgr as llm_mgr
from llm.types import LLMMessage, RoleType


@timer()
async def search_reasoning(
    request_id: str,
    query: str,
    content: str,
    history_query_list: list = [],
):
    if not request_id or not query or not content:
        return {}

    model = os.getenv("SEARCH_REASONING_MODEL", "gpt-4.1")
    _ = model  # model kept for compatibility even if overridden elsewhere

    prompt = get_prompt("deepsearch")["reasoning_prompt"]
    lang = getattr(agentSettings, "lang", "ch").lower()
    if lang == "en":
        formatted_date = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
    else:
        formatted_date = time.strftime("%Y年%m月%d日 %H时%M分%S秒", time.localtime())

    prompt_content = prompt.format(
        query=query,
        sub_queries=history_query_list,
        content=content,
        date=formatted_date,
    )

    response_text = ""
    for chunk in llm_mgr.generate(
        messages=[LLMMessage(role=RoleType.USER.value, content=prompt_content)],
        stream=True,
    ):
        if chunk:
            response_text += chunk

    content_clean = json.loads(repair_json(response_text, ensure_ascii=False))
    return _parser(request_id, content_clean)


def _parser(request_id: str, reasoning: dict) -> dict:
    reasoning_dict = {
        "request_id": request_id,
        "rewrite_query": reasoning.get("rewrite_query", ""),
        "reason": reasoning.get("reason", ""),
    }
    if reasoning.get("is_answer", "") in [1, "1"]:
        reasoning_dict["is_verify"] = "1"
    else:
        reasoning_dict["is_verify"] = "0"
    return reasoning_dict


if __name__ == "__main__":
    pass
