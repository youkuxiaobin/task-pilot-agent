import asyncio
import importlib
import os
import shutil
import tempfile
import threading
from typing import List, Optional

import pandas as pd
import yaml
from jinja2 import Template
from utils.logger import get_logger
from smolagents import LiteLLMModel, FinalAnswerStep, PythonInterpreterTool, ChatMessageStreamDelta, OpenAIModel

from tools.mcp_local.tool.ci_agent import CIAgent
from tools.mcp_local.util.file_util import download_all_files_in_path, upload_file, upload_file_by_path
from tools.mcp_local.util.log_util import timer
from tools.mcp_local.util.prompt_util import get_prompt
import requests
from tools.mcp_local.model.code import ActionOutput, CodeOutput
from config.config import agentSettings

logger = get_logger(__name__)


def _log_exception_group(prefix: str, exc_group: BaseException) -> None:
    """Log ExceptionGroup/TaskGroup contents for easier debugging."""
    logger.error(f"{prefix}: {exc_group}")
    subs = getattr(exc_group, "exceptions", None)
    if not subs:
        return
    total = len(subs)
    for idx, exc in enumerate(subs, 1):
        logger.error(
            "%s sub-exception %d/%d: %s",
            prefix,
            idx,
            total,
            exc,
        )


def _build_error_output(message: str) -> ActionOutput:
    return ActionOutput(content=f"Code interpreter error: {message}", file_list=[])

@timer()
async def code_interpreter_agent(
    task: str,
    input_data: Optional[str] = None,
    file_names: Optional[List[str]] = None,
    max_file_abstract_size: int = 2000,
    max_tokens: int = 8000,
    request_id: str = "",
    stream: bool = True,
):
    work_dir = ""
    try:
        work_dir = tempfile.mkdtemp()
        output_dir = os.path.join(work_dir, "output")
        os.makedirs(output_dir, exist_ok=True)
        import_files = await download_all_files_in_path(file_names=file_names, work_dir=work_dir)

        #file extraction
        files = []
        if import_files:
            for import_file in import_files:
                file_name = import_file["file_name"]
                file_path = import_file["file_path"]
                if not file_name or not file_path:
                    continue

                # table
                if file_name.split(".")[-1] in ["xlsx", "xls", "csv"]:
                    pd.set_option("display.max_columns", None)
                    df = (
                        pd.read_csv(file_path)
                        if file_name.endswith(".csv")
                        else pd.read_excel(file_path)
                    )
                    files.append({"path": file_path, "extraction": f"{df.head(10)}"})
                # text
                elif file_name.split(".")[-1] in ["txt", "md", "html"]:
                    with open(file_path, "r") as rf:
                        files.append(
                            {
                                "path": file_path,
                                "extraction": "".join(rf.readlines())[ 
                                    :max_file_abstract_size
                                ],
                            }
                        )
                else:
                    files.append({"path": file_path, "extraction": ""})
        # 2. 构建 Prompt
        ci_prompt_template = get_prompt("code_interpreter")

        # 3. CodeAgent
        agent = create_ci_agent(
            prompt_templates=ci_prompt_template,
            max_tokens=max_tokens,
            return_full_result=True,
            output_dir=output_dir,
        )

        template_task = Template(ci_prompt_template["task_template"]).render(
            files=files, task=task, output_dir=output_dir, input_data=input_data,
        )

        if stream:
            error_output: Optional[ActionOutput] = None
            loop = asyncio.get_running_loop()
            queue: "asyncio.Queue[Optional[object]]" = asyncio.Queue()

            def _run_agent() -> None:
                try:
                    for step in agent.run(task=str(template_task), stream=True, max_steps=10):
                        asyncio.run_coroutine_threadsafe(queue.put(step), loop)
                except BaseException as exc:  # 保证线程内异常传回事件循环
                    asyncio.run_coroutine_threadsafe(queue.put(exc), loop)
                finally:
                    asyncio.run_coroutine_threadsafe(queue.put(None), loop)

            threading.Thread(target=_run_agent, daemon=True).start()

            try:
                while True:
                    step = await queue.get()
                    if step is None:
                        break
                    if isinstance(step, BaseException):
                        raise step

                    if isinstance(step, CodeOutput):
                        file_info = await upload_file(
                            content=step.code,
                            file_name=step.file_name,
                            file_type="py",
                            request_id=request_id,
                        )
                        step.file_list = [file_info]
                        yield step
                    elif isinstance(step, FinalAnswerStep):
                        file_list = []
                        file_path = get_new_file_by_path(output_dir=output_dir)
                        if file_path:
                            file_info = await upload_file_by_path(
                                file_path=file_path, request_id=request_id
                            )
                            if file_info:
                                file_list.append(file_info)
                        code_name = f"{task[:20]}_代码输出.md"
                        file_list.append(
                            await upload_file(
                                content=step.output,
                                file_name=code_name,
                                file_type="md",
                                request_id=request_id,
                            )
                        )

                        output = ActionOutput(content=step.output, file_list=file_list)
                        yield output
                    elif isinstance(step, ChatMessageStreamDelta):
                        pass
            except BaseException as exc:
                logger.error("code_interpreter_agent stream run failed: %s", exc)
                error_output = _build_error_output(str(exc))
            if error_output:
                yield error_output
                return
        else:
            error_output: Optional[ActionOutput] = None
            output: Optional[ActionOutput] = None
            try:
                output = await asyncio.to_thread(agent.run, task=task)
            except BaseException as exc:
                logger.error("code_interpreter_agent run failed: %s", exc)
                error_output = _build_error_output(str(exc))
            if error_output:
                yield error_output
                return
            if output is not None:
                yield output
    except BaseException as e:
        logger.error(f"code_interpreter_agent unexpected error: {e}")
        yield _build_error_output(str(e))
        return

    finally:
        if work_dir:
            shutil.rmtree(work_dir, ignore_errors=True)


def get_new_file_by_path(output_dir):
    temp_file = ""
    latest_time = 0
    for item in os.listdir(output_dir):
        if item.endswith(".xlsx") or item.endswith(".csv") or item.endswith(".xls"):
            item_path = os.path.join(output_dir, item)
            if os.path.isfile(item_path):
                # 获取文件的最后修改时间
                mod_time = os.path.getmtime(item_path)
                # 如果当前文件比之前记录的更新，则更新最新文件和时间为当前文件
                if mod_time > latest_time:
                    latest_time = mod_time
                    temp_file = item_path
    return temp_file


def create_ci_agent(
    prompt_templates=None,
    max_tokens: int = 16000,
    return_full_result: bool = True,
    output_dir: str = "",
) -> CIAgent:
    model = _build_agent_model(max_tokens=max_tokens)

    return CIAgent(
        model=model,
        prompt_templates=prompt_templates,
        tools=[PythonInterpreterTool()],
        return_full_result=return_full_result,
        additional_authorized_imports=[
            "pandas",
            "openpyxl",
            "numpy",
            "matplotlib",
            "seaborn",
            "json",
            "docx",  # python-docx 的导入名称是 docx
            "lxml",
            "pdfplumber",
            "PIL",
            "*",
        ],
        output_dir=output_dir,
    )


def _build_agent_model(max_tokens: int):
    """
    Build the model instance for the code interpreter agent based on configured provider.
    """
    llm_settings = agentSettings.llm
    provider = (getattr(llm_settings, "provider", "") or "").strip().lower()

    if provider == "openai":
        return _build_openai_model(llm_settings, max_tokens)

    return _build_litellm_model(llm_settings, max_tokens)


def _build_openai_model(llm_settings, max_tokens: int) -> OpenAIModel:
    llm_config = llm_settings.config
    context_length = getattr(llm_config, "context_length", None)
    effective_max_tokens = min(max_tokens, context_length) if context_length else max_tokens

    model_kwargs = {
        "model_id": llm_config.model,
        "max_tokens": effective_max_tokens,
        "temperature": getattr(llm_config, "temperature", 0.0),
    }

    api_base = getattr(llm_config, "site_url", None)
    if api_base:
        model_kwargs["api_base"] = api_base

    api_key = getattr(llm_config, "api_key", None)
    if api_key:
        model_kwargs["api_key"] = api_key

    return OpenAIModel(**model_kwargs)


def _build_litellm_model(llm_settings, max_tokens: int) -> LiteLLMModel:
    llm_config = llm_settings.config

    context_length = getattr(llm_config, "context_length", None)
    effective_max_tokens = min(max_tokens, context_length) if context_length else max_tokens

    provider = getattr(llm_settings, "provider", "") or ""
    provider = provider.strip().lower()

    model_id = llm_config.model
    if provider and "/" not in model_id:
        model_id = f"{provider}/{model_id}"

    model_kwargs = {
        "model_id": model_id,
        "max_tokens": effective_max_tokens,
        "temperature": getattr(llm_config, "temperature", 0.0),
    }

    api_base = getattr(llm_config, "site_url", None)
    if api_base:
        model_kwargs["api_base"] = api_base

    api_key = getattr(llm_config, "api_key", None)
    if api_key:
        model_kwargs["api_key"] = api_key

    if provider:
        model_kwargs["custom_llm_provider"] = provider

    return LiteLLMModel(**model_kwargs)


if __name__ == "__main__":
    pass 
