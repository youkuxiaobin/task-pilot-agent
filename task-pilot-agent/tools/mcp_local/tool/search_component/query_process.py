import json
import os
import re
import time

from utils.logger import get_logger

from config.config import agentSettings
from tools.mcp_local.util.prompt_util import get_prompt
from tools.mcp_local.util.log_util import timer
from llm.manager import mgr as llm_mgr
from llm.types import LLMMessage, RoleType
from mcp.server.fastmcp import Context
from mcp.server.session import ServerSession

logger = get_logger(__name__)


@timer()
async def query_decompose(
    query: str,
    ctx: Context[ServerSession, None] = None,
    **kwargs,
):
    current_date = time.strftime("%Y-%m-%d", time.localtime())
    prompts = get_prompt("deepsearch")

    if ctx is not None:
        await ctx.info(f"query processquery: {query}")

    think_content = ""
    for chunk in llm_mgr.generate(
        messages=[
            LLMMessage(
                role=RoleType.USER.value,
                content=prompts["query_decompose_think_prompt"].format(
                    task=query,
                    retrieval_str="",
                ),
            )
        ],
        stream=True,
    ):
        if chunk:
            think_content += chunk

    if ctx is not None:
        await ctx.info("query extend...")

    lang = getattr(agentSettings, "lang", "ch").lower()
    prefix = ("思考总结: " if lang != "en" else "Thought summary: ")
    logger.debug("query decompose thinking finished: query_len=%s think_len=%s", len(query), len(think_content))
    messages = [
        LLMMessage(
            role=RoleType.SYSTEM.value,
            content=prompts["query_decompose_prompt"].format(
                current_date=current_date,
                max_queries=os.getenv("QUERY_DECOMPOSE_MAX_SIZE", 2),
            ),
        ),
        LLMMessage(role=RoleType.USER.value, content=f"{prefix}{think_content}"),
    ]

    extend_queries = ""
    for chunk in llm_mgr.generate(messages=messages, stream=True):
        if chunk:
            extend_queries += chunk

    queries = re.findall(r"^- (.+)$", extend_queries, re.MULTILINE)
    if ctx is not None:
        await ctx.info(f"query process queries: {json.dumps(queries, ensure_ascii=False)}")
    logger.info("query extend generated %s candidate query(s)", len(queries))
    return [match.strip().strip('"“”').strip() for match in queries]


if __name__ == "__main__":
    pass
