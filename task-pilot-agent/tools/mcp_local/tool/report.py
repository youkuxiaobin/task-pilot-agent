import os
from datetime import datetime
from typing import Optional, List, Literal, AsyncGenerator

from dotenv import load_dotenv
from jinja2 import Template
from utils.logger import get_logger

from tools.mcp_local.util.file_util import download_all_files, truncate_files, flatten_search_file
from tools.mcp_local.util.prompt_util import get_prompt
from tools.mcp_local.util.log_util import timer
from tools.mcp_local.model.context import LLMModelInfoFactory
from llm.manager import mgr as llm_mgr
from llm.types import LLMMessage, RoleType
from memory.memory_mgr import memory_manager

logger = get_logger(__name__)

load_dotenv()


@timer(key="enter")
async def report(
        task: str,
        request_id: str,
        model: str = "gpt-4.1",
        file_type: Literal["markdown", "html", "ppt"] = "markdown",
) -> AsyncGenerator:
    report_factory = {
        "ppt": ppt_report,
        "markdown": markdown_report,
        "html": html_report,
    }
    # get history messages
    history_messages = memory_manager.get_messages(trace_id=request_id)
    history_messages = "\n".join([f"{message.role}: {message.content}" for message in history_messages])
    model = os.getenv("REPORT_MODEL", "gpt-4.1")
    async for chunk in report_factory[file_type](task, history_messages, model):
        yield chunk


@timer(key="enter")
async def ppt_report(
        task: str,
        history_messages: str,
        file_names: Optional[List[str]] = tuple(),
        model: str = "gpt-4.1",
        temperature: float = None,
        top_p: float = 0.6,
       
) -> AsyncGenerator:
    files = await download_all_files(file_names)
    flat_files = []

    # 1. 首先解析 md html 文件，没有这部分文件则使用全部
    filtered_files = [f for f in files if f["file_name"].split(".")[-1] in ["md", "html"]
                      and not f["file_name"].endswith("_搜索结果.md")] or files
    for f in filtered_files:
        # 对于搜索文件有结构，需要重新解析
        if f["file_name"].endswith("_search_result.txt"):
            flat_files.extend(flatten_search_file(f))
        else:
            flat_files.append(f)

    truncate_flat_files = truncate_files(flat_files, max_tokens=int(LLMModelInfoFactory.get_context_length(model) * 0.8))
    prompt = Template(get_prompt("report")["ppt_prompt"]) \
        .render(task=task, files=truncate_flat_files, date=datetime.now().strftime("%Y-%m-%d"))

    async for chunk in llm_mgr.generate(messages=prompt, stream=True):
        yield chunk


@timer(key="enter")
async def markdown_report(
        task,
        history_messages: str,
        file_names: Optional[List[str]] = tuple(),
        model: str = "gpt-4.1",
        temperature: float = 0,
        top_p: float = 0.9,
        
) -> AsyncGenerator:
    try:
        #files = await download_all_files(file_names)
        flat_files = []
        #for f in files:
            # 对于搜索文件有结构，需要重新解析
        #    if f["file_name"].endswith("_search_result.txt"):
        #        flat_files.extend(flatten_search_file(f))
        #    else:
        #        flat_files.append(f)

        truncate_flat_files = truncate_files(flat_files, max_tokens=int(LLMModelInfoFactory.get_context_length(model) * 0.8))
        prompt = Template(get_prompt("report")["markdown_prompt"]) \
            .render(task=task, history_messages=history_messages, files=truncate_flat_files, current_time=datetime.now().strftime("%Y-%m-%d %H:%M:%S"))

        for chunk in llm_mgr.generate(messages=[LLMMessage(role=RoleType.USER.value, content=prompt)], stream=True):
            yield chunk

    except Exception as e:
        logger.error(f"markdown_report error: {e}")
        yield f"markdown_report error: {e}"


@timer(key="enter")
async def html_report(
        task,
        history_messages: str,
        file_names: Optional[List[str]] = tuple(),
        model: str = "gpt-4.1",
        temperature: float = 0,
        top_p: float = 0.9,
) -> AsyncGenerator:
    files = await download_all_files(file_names)
    key_files = []
    flat_files = []
    # 对于搜索文件有结构，需要重新解析
    for f in files:
        fpath = f["file_name"]
        fname = os.path.basename(fpath)
        if fname.split(".")[-1] in ["md", "txt", "csv"]:
            # CI 输出结果
            if "代码输出" in fname:
                key_files.append({"content": f["content"], "description": fname, "type": "txt", "link": fpath})
            # 搜索文件
            elif fname.endswith("_search_result.txt"):
                try:
                    flat_files.extend([{
                            "content": tf["content"],
                            "description": tf.get("title") or tf["content"][:20],
                            "type": "txt",
                            "link": tf.get("link"),
                        } for tf in flatten_search_file(f)
                    ])
                except Exception as e:
                    logger.warning(f"html_report parser file [{fpath}] error: {e}")
            # 其他文件
            else:
                flat_files.append({
                    "content": f["content"],
                    "description": fname,
                    "type": "txt",
                    "link": fpath
                })
    discount = int(LLMModelInfoFactory.get_context_length(model) * 0.8)
    key_files = truncate_files(key_files, max_tokens=discount)
    flat_files = truncate_files(flat_files, max_tokens=discount - sum([len(f["content"]) for f in key_files]))

    report_prompts = get_prompt("report")
    prompt = Template(report_prompts["html_task"]) \
        .render(task=task, key_files=key_files, files=flat_files, date=datetime.now().strftime('%Y年%m月%d日'))

    async for chunk in llm_mgr.generate(
            messages=[{"role": RoleType.SYSTEM.value, "content": report_prompts["html_prompt"]},
                      {"role": RoleType.USER.value, "content": prompt}],stream=True):
        yield chunk


if __name__ == "__main__":
    pass
